from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
import unittest


class OcrProviderGateTests(unittest.TestCase):
    def test_disabled_explicit_ocr_fails_before_registry_or_request_side_effects(self) -> None:
        from backend.app.providers.ocr.registry import compose_ocr_gate

        counters = {
            "registry": 0,
            "provider": 0,
            "credential": 0,
            "transport": 0,
            "network": 0,
            "pdf_reader": 0,
            "source_write": 0,
            "job_write": 0,
            "checkpoint_write": 0,
        }

        def registry_factory():
            counters["registry"] += 1
            raise AssertionError("disabled OCR constructed a registry")

        gate = compose_ocr_gate(enabled=False, registry_factory=registry_factory)

        with self.assertRaises(Exception) as caught:
            gate.select(source_mode="ocr", provider_id=None, model=None, options=None)
            for name in counters:
                if name != "registry":
                    counters[name] += 1

        self.assertEqual("OCR_DISABLED", getattr(caught.exception, "code", None))
        self.assertEqual(409, getattr(caught.exception, "http_status", None))
        self.assertEqual({name: 0 for name in counters}, counters)


class FakeOcrProviderTests(unittest.IsolatedAsyncioTestCase):
    async def test_extract_batch_returns_configured_pages_fails_by_page_and_records_calls(self) -> None:
        from backend.app.application.ports.ocr_provider import OcrRequest
        from backend.app.providers.ocr.fake import FakeOcrProvider

        failure = RuntimeError("synthetic page failure")
        provider = FakeOcrProvider(
            pages={1: "# page one\n", 3: "# page three\n"},
            failures={2: failure},
        )

        successful = OcrRequest(
            source_id="source-1",
            paper_id="paper-1",
            pdf_bytes=b"%PDF synthetic",
            pdf_sha256=hashlib.sha256(b"%PDF synthetic").hexdigest(),
            media_type="application/pdf",
            model="fake-ocr-v1",
            options={"pageBatchSize": 1, "maxConcurrency": 1},
            page_numbers=(1, 3),
            total_pages=3,
        )
        result = await provider.extract_batch(successful)

        self.assertEqual("fake", result.provider)
        self.assertEqual("fake-ocr-v1", result.model)
        self.assertEqual("fake-ocr-v1", result.processing_version)
        self.assertEqual([1, 3], [page.page_number for page in result.pages])
        self.assertEqual(
            [hashlib.sha256(text.encode("utf-8")).hexdigest() for text in ("# page one\n", "# page three\n")],
            [page.content_sha256 for page in result.pages],
        )

        failing = OcrRequest(
            source_id="source-1",
            paper_id="paper-1",
            pdf_bytes=b"%PDF synthetic",
            pdf_sha256=hashlib.sha256(b"%PDF synthetic").hexdigest(),
            media_type="application/pdf",
            model="fake-ocr-v1",
            options={},
            page_numbers=(2,),
            total_pages=3,
        )
        with self.assertRaises(RuntimeError) as caught:
            await provider.extract_batch(failing)

        self.assertIs(failure, caught.exception)
        self.assertEqual([successful, failing], provider.calls)


class RetryAfterTests(unittest.TestCase):
    def test_normalizes_seconds_date_missing_invalid_negative_and_overlong_values(self) -> None:
        from backend.app.providers.ocr.retry_after import normalize_retry_after

        now = datetime(2026, 8, 9, 8, 0, tzinfo=timezone.utc)
        cases = (
            ("120", 120),
            (format_datetime(now + timedelta(seconds=61), usegmt=True), 61),
            (None, None),
            ("not-a-delay", None),
            ("-5", 0),
            ("901", 900),
            (format_datetime(now + timedelta(seconds=1800), usegmt=True), 900),
        )

        for raw, expected in cases:
            with self.subTest(raw=raw):
                self.assertEqual(expected, normalize_retry_after(raw, now=now))


class ProductionOcrRegistryTests(unittest.TestCase):
    def test_production_excludes_fake_rejects_unknown_and_gates_deepseek_before_side_effects(self) -> None:
        from backend.app.providers.ocr.registry import create_production_ocr_registry

        registry = create_production_ocr_registry()
        self.assertNotIn("fake", registry.provider_ids)

        for provider_id in ("fake", "unknown-provider"):
            with self.subTest(provider_id=provider_id):
                with self.assertRaises(Exception) as caught:
                    registry.resolve(provider_id)
                self.assertEqual("OCR_PROVIDER_UNKNOWN", getattr(caught.exception, "code", None))
                self.assertEqual(422, getattr(caught.exception, "http_status", None))

        counters = {
            "provider": 0,
            "credential": 0,
            "transport": 0,
            "network": 0,
            "pdf_reader": 0,
            "source_write": 0,
            "job_write": 0,
            "checkpoint_write": 0,
        }
        with self.assertRaises(Exception) as caught:
            registry.resolve("deepseek")
            for name in counters:
                counters[name] += 1

        self.assertEqual("OCR_PROVIDER_CONTRACT_UNVERIFIED", getattr(caught.exception, "code", None))
        self.assertEqual(503, getattr(caught.exception, "http_status", None))
        self.assertEqual({name: 0 for name in counters}, counters)


class EnabledOcrSelectionTests(unittest.TestCase):
    def test_runtime_settings_resolver_controls_enablement_identity_and_defaults(self) -> None:
        from backend.app.providers.ocr.fake import FakeOcrProvider
        from backend.app.providers.ocr.registry import (
            compose_ocr_gate,
            create_test_ocr_registry,
        )

        runtime = {
            "ocrEnabled": False,
            "ocrProvider": "fake",
            "ocrModel": "saved-ocr-model",
            "ocrPageBatchSize": 4,
            "ocrMaxConcurrency": 2,
        }
        fake = FakeOcrProvider(pages={1: "# page one\n"})
        gate = compose_ocr_gate(
            enabled=False,
            registry_factory=lambda: create_test_ocr_registry({"fake": fake}),
            settings_resolver=lambda: runtime,
        )

        with self.assertRaises(Exception) as caught:
            gate.select(
                source_mode="ocr",
                provider_id=None,
                model=None,
                options=None,
            )
        self.assertEqual("OCR_DISABLED", getattr(caught.exception, "code", None))

        runtime["ocrEnabled"] = True
        selection = gate.select(
            source_mode="ocr",
            provider_id=None,
            model=None,
            options=None,
        )
        self.assertEqual("fake", selection.provider_id)
        self.assertEqual("saved-ocr-model", selection.model)
        self.assertEqual({"pageBatchSize": 4, "maxConcurrency": 2}, dict(selection.options))

        runtime["ocrPageBatchSize"] = 3
        runtime["ocrMaxConcurrency"] = 1
        selection = gate.select(
            source_mode="ocr",
            provider_id=None,
            model=None,
            options={"pageBatchSize": 2},
        )
        self.assertEqual({"pageBatchSize": 2, "maxConcurrency": 1}, dict(selection.options))

    def test_enabled_test_override_selects_fake_with_canonical_options(self) -> None:
        from backend.app.providers.ocr.fake import FakeOcrProvider
        from backend.app.providers.ocr.registry import (
            compose_ocr_gate,
            create_test_ocr_registry,
        )

        fake = FakeOcrProvider(pages={1: "# page one\n"})
        gate = compose_ocr_gate(
            enabled=True,
            registry_factory=lambda: create_test_ocr_registry({"fake": fake}),
        )

        selection = gate.select(
            source_mode="ocr",
            provider_id="fake",
            model="fake-ocr-v1",
            options={},
        )

        self.assertIs(fake, selection.provider)
        self.assertEqual("fake", selection.provider_id)
        self.assertEqual("fake-ocr-v1", selection.model)
        self.assertEqual({"pageBatchSize": 1, "maxConcurrency": 1}, dict(selection.options))
        self.assertEqual(1, selection.page_batch_size)
        self.assertEqual(1, selection.max_concurrency)

    def test_native_ocr_fields_and_enabled_ocr_invalid_options_are_422(self) -> None:
        from backend.app.providers.ocr.fake import FakeOcrProvider
        from backend.app.providers.ocr.registry import compose_ocr_gate, create_test_ocr_registry

        gate = compose_ocr_gate(
            enabled=True,
            registry_factory=lambda: create_test_ocr_registry(
                {"fake": FakeOcrProvider(pages={1: "# page one\n"})}
            ),
        )
        invalid = (
            ("native", "fake", None, None),
            ("native", None, "fake-ocr-v1", None),
            ("native", None, None, {}),
            ("ocr", None, "fake-ocr-v1", {}),
            ("ocr", "fake", "", {}),
            ("ocr", "fake", "fake-ocr-v1", {"pageBatchSize": 0}),
            ("ocr", "fake", "fake-ocr-v1", {"unexpected": True}),
        )

        for source_mode, provider_id, model, options in invalid:
            with self.subTest(source_mode=source_mode, provider_id=provider_id, options=options):
                with self.assertRaises(Exception) as caught:
                    gate.select(
                        source_mode=source_mode,
                        provider_id=provider_id,
                        model=model,
                        options=options,
                    )
                self.assertEqual("OCR_REQUEST_INVALID", getattr(caught.exception, "code", None))
                self.assertEqual(422, getattr(caught.exception, "http_status", None))


if __name__ == "__main__":
    unittest.main()
