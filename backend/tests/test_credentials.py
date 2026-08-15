from __future__ import annotations

import asyncio
import contextlib
import hashlib
import io
import json
from pathlib import Path
import tempfile
import threading
import traceback
import unittest
from unittest import mock

from backend.app.application.credentials import CredentialService
from backend.app.domain import (
    CredentialBackendError,
    CredentialKind,
    CredentialUpdateIndeterminateError,
)
from backend.app.providers.credentials import (
    CompositeCredentialStore,
    EnvironmentCredentialStore,
    KeyringCredentialStore,
    LegacySettingsCredentialStore,
    SafeCredentialProbe,
)


KIND_CASES = (
    (CredentialKind.LLM, "LLM_API_KEY", "apiKey", "credential:llm"),
    (CredentialKind.OCR, "OCR_API_KEY", "ocrApiKey", "credential:ocr"),
    (CredentialKind.EMBEDDING, "EMBED_API_KEY", "embedApiKey", "credential:embedding"),
    (CredentialKind.SEMANTIC_SCHOLAR, "S2_API_KEY", "s2ApiKey", "credential:semantic_scholar"),
)


class FakeKeyringAdapter:
    def __init__(self) -> None:
        self.values: dict[tuple[str, str], str] = {}
        self.calls: list[tuple[str, str, str | None]] = []
        self.fail_get = False
        self.fail_set = False
        self.fail_delete = False

    def get_password(self, service: str, username: str) -> str | None:
        self.calls.append(("get", service, username))
        if self.fail_get:
            raise RuntimeError("locked backend TOP-SECRET")
        return self.values.get((service, username))

    def set_password(self, service: str, username: str, value: str) -> None:
        self.calls.append(("set", service, username))
        if self.fail_set:
            raise RuntimeError("set failed TOP-SECRET")
        self.values[(service, username)] = value

    def delete_password(self, service: str, username: str) -> None:
        self.calls.append(("delete", service, username))
        if self.fail_delete:
            raise RuntimeError("delete failed TOP-SECRET")
        self.values.pop((service, username), None)


class BlockingKeyringAdapter(FakeKeyringAdapter):
    def __init__(self) -> None:
        super().__init__()
        self.blocked_operation: str | None = None
        self.started = threading.Event()
        self.release = threading.Event()
        self.finished = threading.Event()

    def block_next(self, operation: str) -> None:
        self.blocked_operation = operation
        self.started.clear()
        self.release.clear()
        self.finished.clear()

    def _block(self, operation: str) -> None:
        if self.blocked_operation != operation:
            return
        self.blocked_operation = None
        self.started.set()
        if not self.release.wait(timeout=5):
            raise RuntimeError("timed out waiting to release blocked keyring mutation")

    def set_password(self, service: str, username: str, value: str) -> None:
        self._block("set")
        super().set_password(service, username, value)
        self.finished.set()

    def delete_password(self, service: str, username: str) -> None:
        self._block("delete")
        super().delete_password(service, username)
        self.finished.set()


class BlockingLegacySettingsStore(LegacySettingsCredentialStore):
    def __init__(self, settings_path: Path) -> None:
        super().__init__(settings_path)
        self.blocked_operation: str | None = None
        self.started = threading.Event()
        self.release = threading.Event()
        self.finished = threading.Event()

    def block_next(self, operation: str) -> None:
        self.blocked_operation = operation
        self.started.clear()
        self.release.clear()
        self.finished.clear()

    def _mutate_sync(self, kind: CredentialKind, value: str | None) -> None:
        operation = "delete" if value is None else "set"
        if self.blocked_operation == operation:
            self.blocked_operation = None
            self.started.set()
            if not self.release.wait(timeout=5):
                raise RuntimeError("timed out waiting to release blocked legacy mutation")
        super()._mutate_sync(kind, value)
        self.finished.set()


class CredentialFixture(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory(prefix="study-app-credentials-")
        self.settings_path = Path(self._temp.name) / "settings.json"
        self.settings = {
            "apiKey": "legacy-llm-secret",
            "ocrApiKey": "legacy-ocr-secret",
            "embedApiKey": "legacy-embed-secret",
            "s2ApiKey": "legacy-s2-secret",
            "provider": "openai",
            "unknown": {"preserve": True},
        }
        self.settings_path.write_text(
            json.dumps(self.settings, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        self.adapter = FakeKeyringAdapter()

    async def asyncTearDown(self) -> None:
        self._temp.cleanup()

    def stores(self, environment: dict[str, str] | None = None):
        env = EnvironmentCredentialStore(environment or {})
        keyring = KeyringCredentialStore(self.adapter)
        legacy = LegacySettingsCredentialStore(self.settings_path)
        composite = CompositeCredentialStore(env, keyring, legacy)
        return env, keyring, legacy, composite


class CredentialPriorityTests(CredentialFixture):
    async def test_four_exact_priority_mappings_and_missing_state(self) -> None:
        for kind, env_name, legacy_field, username in KIND_CASES:
            with self.subTest(kind=kind):
                self.adapter.values[("study-app", username)] = f"keyring-{kind.value}"
                _env, _keyring, _legacy, store = self.stores(
                    {env_name: f" env-{kind.value} "}
                )
                credential = await store.get(kind)
                self.assertEqual(f" env-{kind.value} ", credential.value)

                _env, _keyring, _legacy, store = self.stores()
                credential = await store.get(kind)
                self.assertEqual(f"keyring-{kind.value}", credential.value)

                self.adapter.values.pop(("study-app", username), None)
                _env, _keyring, _legacy, store = self.stores()
                credential = await store.get(kind)
                self.assertEqual(self.settings[legacy_field], credential.value)

        empty_path = Path(self._temp.name) / "empty.json"
        empty_path.write_text("{}", encoding="utf-8")
        store = CompositeCredentialStore(
            EnvironmentCredentialStore({}),
            KeyringCredentialStore(FakeKeyringAdapter()),
            LegacySettingsCredentialStore(empty_path),
        )
        for kind, *_rest in KIND_CASES:
            self.assertIsNone(await store.get(kind))


class KeyringCredentialStoreTests(CredentialFixture):
    async def test_fixed_service_usernames_and_sanitized_failures(self) -> None:
        _env, keyring, _legacy, _store = self.stores()
        for kind, _env_name, _field, username in KIND_CASES:
            await keyring.set(kind, f"sentinel-{kind.value}")
            self.assertEqual(f"sentinel-{kind.value}", (await keyring.get(kind)).value)
            await keyring.delete(kind)
            self.assertIn(("set", "study-app", username), self.adapter.calls)

        for operation in ("get", "set", "delete"):
            setattr(self.adapter, f"fail_{operation}", True)
            with self.assertRaises(CredentialBackendError) as raised:
                if operation == "get":
                    await keyring.get(CredentialKind.LLM)
                elif operation == "set":
                    await keyring.set(CredentialKind.LLM, "TOP-SECRET")
                else:
                    await keyring.delete(CredentialKind.LLM)
            self.assertNotIn("TOP-SECRET", str(raised.exception))
            rendered = "".join(
                traceback.format_exception(
                    type(raised.exception),
                    raised.exception,
                    raised.exception.__traceback__,
                )
            )
            self.assertNotIn("TOP-SECRET", rendered)
            setattr(self.adapter, f"fail_{operation}", False)


class LegacyCredentialMigrationTests(CredentialFixture):
    async def test_imports_all_fields_verifies_readback_and_preserves_file(self) -> None:
        before = self.settings_path.read_bytes()
        _env, _keyring, _legacy, store = self.stores()
        for kind, _env_name, field, username in KIND_CASES:
            credential = await store.get(kind)
            self.assertEqual(self.settings[field], credential.value)
            self.assertEqual(
                self.settings[field], self.adapter.values[("study-app", username)]
            )
        self.assertEqual(before, self.settings_path.read_bytes())

        failing = FakeKeyringAdapter()
        failing.fail_set = True
        failed_store = CompositeCredentialStore(
            EnvironmentCredentialStore({}),
            KeyringCredentialStore(failing),
            LegacySettingsCredentialStore(self.settings_path),
        )
        with self.assertRaises(CredentialBackendError):
            await failed_store.get(CredentialKind.LLM)
        self.assertEqual(before, self.settings_path.read_bytes())

    async def test_obsidian_fields_survive_credential_mutation(self) -> None:
        obsidian = {
            "obsidianEnabled": True,
            "obsidianVaultPath": "C:/TestVault",
            "obsidianRootFolder": "Research",
            "obsidianPdfMode": "copy",
            "obsidianExportSource": True,
            "obsidianExportExplainer": False,
            "obsidianExportTranslation": True,
            "obsidianAutoExport": False,
        }
        document = json.loads(self.settings_path.read_text(encoding="utf-8"))
        document.update(obsidian)
        self.settings_path.write_text(json.dumps(document), encoding="utf-8")
        legacy = LegacySettingsCredentialStore(self.settings_path)

        for index, (kind, *_rest) in enumerate(KIND_CASES):
            await legacy.set(kind, f"replacement-{index}")
            self.assertEqual(
                obsidian,
                {
                    key: json.loads(
                        self.settings_path.read_text(encoding="utf-8")
                    )[key]
                    for key in obsidian
                },
            )
            await legacy.delete(kind)
            persisted = json.loads(self.settings_path.read_text(encoding="utf-8"))
            self.assertEqual(obsidian, {key: persisted[key] for key in obsidian})

class CredentialMutationTests(CredentialFixture):
    async def test_blank_update_nonblank_update_clear_and_environment_authority(self) -> None:
        _env, _keyring, _legacy, store = self.stores()
        self.adapter.calls.clear()
        status = await store.update(CredentialKind.LLM, "   ")
        self.assertTrue(status.has_key)
        self.assertFalse(any(call[0] in {"set", "delete"} for call in self.adapter.calls))

        await store.update(CredentialKind.LLM, "new-llm-secret")
        updated = json.loads(self.settings_path.read_text(encoding="utf-8"))
        self.assertEqual("new-llm-secret", updated["apiKey"])
        self.assertEqual(self.settings["ocrApiKey"], updated["ocrApiKey"])
        self.assertEqual(self.settings["unknown"], updated["unknown"])
        await store.clear(CredentialKind.LLM)
        cleared = json.loads(self.settings_path.read_text(encoding="utf-8"))
        self.assertNotIn("apiKey", cleared)
        self.assertNotIn(("study-app", "credential:llm"), self.adapter.values)

        _env, _keyring, _legacy, environment_store = self.stores(
            {"LLM_API_KEY": "environment-secret"}
        )
        await environment_store.update(CredentialKind.LLM, "lower-tier")
        await environment_store.clear(CredentialKind.LLM)
        credential = await environment_store.get(CredentialKind.LLM)
        self.assertEqual("environment-secret", credential.value)

    async def test_second_tier_failure_compensates_or_reports_indeterminate(self) -> None:
        _env, _keyring, legacy, store = self.stores()
        await store.get(CredentialKind.LLM)
        previous = self.adapter.values[("study-app", "credential:llm")]

        async def fail_legacy(_kind, _value):
            raise RuntimeError("legacy write leaked TOP-SECRET")

        legacy.set = fail_legacy
        with self.assertRaises(CredentialBackendError) as raised:
            await store.update(CredentialKind.LLM, "replacement-secret")
        rendered = "".join(
            traceback.format_exception(
                type(raised.exception),
                raised.exception,
                raised.exception.__traceback__,
            )
        )
        self.assertNotIn("TOP-SECRET", rendered)
        self.assertEqual(previous, self.adapter.values[("study-app", "credential:llm")])

        original_set = self.adapter.set_password
        set_calls = 0

        def fail_compensation(service: str, username: str, value: str) -> None:
            nonlocal set_calls
            set_calls += 1
            if set_calls == 2:
                raise RuntimeError("compensation unavailable TOP-SECRET")
            original_set(service, username, value)

        self.adapter.set_password = fail_compensation
        with self.assertRaises(CredentialUpdateIndeterminateError) as raised:
            await store.update(CredentialKind.LLM, "another-secret")
        self.assertNotIn("TOP-SECRET", str(raised.exception))
        rendered = "".join(
            traceback.format_exception(
                type(raised.exception),
                raised.exception,
                raised.exception.__traceback__,
            )
        )
        self.assertNotIn("TOP-SECRET", rendered)

    async def test_update_cancellation_waits_for_keyring_and_restores_previous_value(self) -> None:
        adapter = BlockingKeyringAdapter()
        store = CompositeCredentialStore(
            EnvironmentCredentialStore({}),
            KeyringCredentialStore(adapter),
            LegacySettingsCredentialStore(self.settings_path),
        )
        await store.get(CredentialKind.LLM)
        previous = self.settings["apiKey"]
        adapter.block_next("set")

        update = asyncio.create_task(
            store.update(CredentialKind.LLM, "replacement-secret")
        )
        self.assertTrue(await asyncio.to_thread(adapter.started.wait, 2))
        update.cancel()
        asyncio.get_running_loop().call_later(0.05, adapter.release.set)
        with self.assertRaises(asyncio.CancelledError):
            await update
        self.assertTrue(await asyncio.to_thread(adapter.finished.wait, 2))

        self.assertEqual(
            previous,
            adapter.values[("study-app", "credential:llm")],
        )
        persisted = json.loads(self.settings_path.read_text(encoding="utf-8"))
        self.assertEqual(previous, persisted["apiKey"])

    async def test_clear_cancellation_waits_for_keyring_and_restores_previous_value(self) -> None:
        adapter = BlockingKeyringAdapter()
        store = CompositeCredentialStore(
            EnvironmentCredentialStore({}),
            KeyringCredentialStore(adapter),
            LegacySettingsCredentialStore(self.settings_path),
        )
        await store.get(CredentialKind.LLM)
        previous = self.settings["apiKey"]
        adapter.block_next("delete")

        clear = asyncio.create_task(store.clear(CredentialKind.LLM))
        self.assertTrue(await asyncio.to_thread(adapter.started.wait, 2))
        clear.cancel()
        asyncio.get_running_loop().call_later(0.05, adapter.release.set)
        with self.assertRaises(asyncio.CancelledError):
            await clear
        self.assertTrue(await asyncio.to_thread(adapter.finished.wait, 2))

        self.assertEqual(
            previous,
            adapter.values[("study-app", "credential:llm")],
        )
        persisted = json.loads(self.settings_path.read_text(encoding="utf-8"))
        self.assertEqual(previous, persisted["apiKey"])

    async def test_update_cancellation_during_legacy_write_restores_both_tiers(self) -> None:
        adapter = FakeKeyringAdapter()
        legacy = BlockingLegacySettingsStore(self.settings_path)
        store = CompositeCredentialStore(
            EnvironmentCredentialStore({}),
            KeyringCredentialStore(adapter),
            legacy,
        )
        await store.get(CredentialKind.LLM)
        previous = self.settings["apiKey"]
        legacy.block_next("set")

        update = asyncio.create_task(
            store.update(CredentialKind.LLM, "replacement-secret")
        )
        self.assertTrue(await asyncio.to_thread(legacy.started.wait, 2))
        update.cancel()
        asyncio.get_running_loop().call_later(0.05, legacy.release.set)
        with self.assertRaises(asyncio.CancelledError):
            await update
        self.assertTrue(await asyncio.to_thread(legacy.finished.wait, 2))

        self.assertEqual(
            previous,
            adapter.values[("study-app", "credential:llm")],
        )
        persisted = json.loads(self.settings_path.read_text(encoding="utf-8"))
        self.assertEqual(previous, persisted["apiKey"])

    async def test_clear_cancellation_during_legacy_delete_restores_both_tiers(self) -> None:
        adapter = FakeKeyringAdapter()
        legacy = BlockingLegacySettingsStore(self.settings_path)
        store = CompositeCredentialStore(
            EnvironmentCredentialStore({}),
            KeyringCredentialStore(adapter),
            legacy,
        )
        await store.get(CredentialKind.LLM)
        previous = self.settings["apiKey"]
        legacy.block_next("delete")

        clear = asyncio.create_task(store.clear(CredentialKind.LLM))
        self.assertTrue(await asyncio.to_thread(legacy.started.wait, 2))
        clear.cancel()
        asyncio.get_running_loop().call_later(0.05, legacy.release.set)
        with self.assertRaises(asyncio.CancelledError):
            await clear
        self.assertTrue(await asyncio.to_thread(legacy.finished.wait, 2))

        self.assertEqual(
            previous,
            adapter.values[("study-app", "credential:llm")],
        )
        persisted = json.loads(self.settings_path.read_text(encoding="utf-8"))
        self.assertEqual(previous, persisted["apiKey"])


class CredentialStatusTests(CredentialFixture):
    async def test_status_shape_and_tail_rules_for_all_kinds(self) -> None:
        for kind, env_name, _field, _username in KIND_CASES:
            for value, tail in (("short", "****"), ("long-secret-value", "****alue")):
                _env, _keyring, _legacy, store = self.stores({env_name: value})
                status = await store.status(kind)
                self.assertEqual(
                    {
                        "kind": kind.value,
                        "hasKey": True,
                        "keyTail": tail,
                        "environmentManaged": True,
                    },
                    status.to_dict(),
                )


class CredentialRedactionTests(CredentialFixture):
    async def test_sentinel_secrets_never_reach_text_or_status_outputs(self) -> None:
        sentinel = "SENTINEL-CREDENTIAL-TOP-SECRET"
        _env, _keyring, _legacy, store = self.stores({"LLM_API_KEY": sentinel})
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            credential = await store.get(CredentialKind.LLM)
            status = await store.status(CredentialKind.LLM)
        rendered = "\n".join(
            (repr(credential), str(credential), json.dumps(status.to_dict()), stdout.getvalue(), stderr.getvalue())
        )
        self.assertNotIn(sentinel, rendered)


class CredentialProbeTests(CredentialFixture):
    async def test_fixed_fixtures_and_zero_user_content_probe_contracts(self) -> None:
        llm_calls: list[tuple[str, str]] = []
        ocr_calls: list[bytes] = []
        probe = SafeCredentialProbe(
            llm_transport=lambda credential, prompt: llm_calls.append((credential.kind.value, prompt)) or True,
            verified_ocr_transport=lambda _credential, image: ocr_calls.append(image) or True,
            enable_verified_ocr=False,
        )
        _env, _keyring, _legacy, store = self.stores(
            {
                "LLM_API_KEY": "llm-secret",
                "OCR_API_KEY": "ocr-secret",
                "EMBED_API_KEY": "embed-secret",
                "S2_API_KEY": "s2-secret",
            }
        )
        service = CredentialService(store, probe)
        llm = await service.test_connection(CredentialKind.LLM)
        self.assertTrue(llm.ok)
        self.assertEqual(
            [("llm", "Return exactly STUDY_APP_CREDENTIAL_OK.\n")], llm_calls
        )
        ocr = await service.test_connection(CredentialKind.OCR)
        self.assertEqual("OCR_PROVIDER_CONTRACT_UNVERIFIED", ocr.code)
        self.assertEqual([], ocr_calls)
        for kind in (CredentialKind.EMBEDDING, CredentialKind.SEMANTIC_SCHOLAR):
            result = await service.test_connection(kind)
            self.assertEqual("CREDENTIAL_PROBE_UNSUPPORTED", result.code)

        verified = SafeCredentialProbe(
            verified_ocr_transport=lambda _credential, image: ocr_calls.append(image) or True,
            enable_verified_ocr=True,
        )
        verified_service = CredentialService(store, verified)
        result = await verified_service.test_connection(CredentialKind.OCR)
        self.assertTrue(result.ok)
        self.assertEqual(68, len(ocr_calls[-1]))
        self.assertEqual(
            "431ced6916a2a21a156e38701afe55bbd7f88969fbbfc56d7fe099d47f265460",
            hashlib.sha256(ocr_calls[-1]).hexdigest(),
        )


class CredentialConcurrencyTests(CredentialFixture):
    async def test_cross_kind_updates_serialize_and_leave_no_partial_file(self) -> None:
        _env, _keyring, _legacy, store = self.stores()
        await asyncio.gather(
            *(store.update(kind, f"concurrent-{kind.value}-secret") for kind, *_rest in KIND_CASES)
        )
        observed = json.loads(self.settings_path.read_text(encoding="utf-8"))
        for kind, _env_name, field, _username in KIND_CASES:
            self.assertEqual(f"concurrent-{kind.value}-secret", observed[field])
        self.assertEqual(self.settings["unknown"], observed["unknown"])
        self.assertEqual([], list(self.settings_path.parent.glob("*.tmp")))

    async def test_expected_sha_conflict_preserves_external_bytes_and_cleans_temp(self) -> None:
        _env, _keyring, legacy, _store = self.stores()
        external = b'{"externally":"changed"}\n'
        real_fsync = __import__("os").fsync
        changed = False

        def race(handle: int) -> None:
            nonlocal changed
            real_fsync(handle)
            if not changed:
                changed = True
                self.settings_path.write_bytes(external)

        with mock.patch(
            "backend.app.providers.credentials.legacy_settings.os.fsync",
            side_effect=race,
        ):
            with self.assertRaises(CredentialBackendError):
                await legacy.set(CredentialKind.LLM, "must-not-overwrite")
        self.assertEqual(external, self.settings_path.read_bytes())
        self.assertEqual([], list(self.settings_path.parent.glob("*.tmp")))


if __name__ == "__main__":
    unittest.main()
