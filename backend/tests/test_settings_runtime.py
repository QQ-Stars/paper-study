from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from backend.app.application.settings import SettingsService
from backend.app.application.settings import _openai_compat_probe_transport
from backend.app.domain import Credential, CredentialKind, CredentialStatus, EmbeddingProfile


class _CredentialService:
    async def status(self, kind: CredentialKind) -> CredentialStatus:
        return CredentialStatus(
            kind=kind,
            has_key=False,
            key_tail=None,
            environment_managed=False,
        )

    async def update(self, _kind: CredentialKind, _value: str) -> CredentialStatus:
        return await self.status(_kind)

    async def clear(self, kind: CredentialKind) -> CredentialStatus:
        return await self.status(kind)


class SettingsRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_default_llm_timeout_view_matches_runtime_sdk_default(self) -> None:
        with tempfile.TemporaryDirectory(prefix="study-app-settings-default-timeout-") as temp:
            root = Path(temp)
            service = SettingsService(
                settings_path=root / "settings.json",
                root=root,
                credential_service=_CredentialService(),
                environment_snapshot={},
            )

            view = await service.view()
            runtime = service.llm_runtime_settings()

            self.assertEqual(0, view["llmTimeout"])
            self.assertIsNone(runtime.timeout_seconds)

    async def test_llm_runtime_settings_are_resolved_by_the_settings_seam(self) -> None:
        with tempfile.TemporaryDirectory(prefix="study-app-settings-llm-runtime-") as temp:
            root = Path(temp)
            service = SettingsService(
                settings_path=root / "settings.json",
                root=root,
                credential_service=_CredentialService(),
                environment_snapshot={
                    "LLM_PROVIDER": "openai",
                    "LLM_BASE_URL": "https://environment.example/v1",
                    "LLM_MODEL": "environment-model",
                    "LLM_TIMEOUT": "9000",
                },
            )

            environment_runtime = service.llm_runtime_settings()
            self.assertEqual("openai", environment_runtime.provider)
            self.assertEqual("https://environment.example/v1", environment_runtime.base_url)
            self.assertEqual("environment-model", environment_runtime.model)
            self.assertEqual(9.0, environment_runtime.timeout_seconds)

            await service.update(
                {
                    "provider": "qwen",
                    "baseUrl": "https://saved.example/v1",
                    "model": "saved-model",
                    "llmTimeout": 2200,
                }
            )
            saved_runtime = service.llm_runtime_settings()
            self.assertEqual("qwen", saved_runtime.provider)
            self.assertEqual("https://saved.example/v1", saved_runtime.base_url)
            self.assertEqual("saved-model", saved_runtime.model)
            self.assertEqual(2.2, saved_runtime.timeout_seconds)

    async def test_legacy_ocr_api_base_alias_is_visible_and_used_by_profile(self) -> None:
        with tempfile.TemporaryDirectory(prefix="study-app-settings-ocr-alias-") as temp:
            root = Path(temp)
            settings_path = root / "settings.json"
            settings_path.write_text(
                json.dumps(
                    {
                        "ocrProvider": "deepseek",
                        "ocrApiBase": "https://legacy-ocr.example/v1",
                        "ocrModel": "fixture-ocr-model",
                    }
                ),
                encoding="utf-8",
            )
            service = SettingsService(
                settings_path=settings_path,
                root=root,
                credential_service=_CredentialService(),
                environment_snapshot={"OCR_BASE_URL": "https://environment.example/v1"},
            )

            view = await service.view()
            profile = await service.profile(CredentialKind.OCR)

            self.assertEqual("https://legacy-ocr.example/v1", view["ocrBaseUrl"])
            self.assertEqual("https://legacy-ocr.example/v1", profile.base_url)

    async def test_ocr_runtime_settings_use_saved_values_over_environment(self) -> None:
        with tempfile.TemporaryDirectory(prefix="study-app-settings-ocr-") as temp:
            root = Path(temp)
            service = SettingsService(
                settings_path=root / "settings.json",
                root=root,
                credential_service=_CredentialService(),
                environment_snapshot={
                    "OCR_ENABLED": "1",
                    "OCR_PROVIDER": "environment-provider",
                    "OCR_MODEL": "environment-model",
                    "OCR_PAGE_BATCH_SIZE": "1",
                    "OCR_MAX_CONCURRENCY": "1",
                },
            )
            await service.update(
                {
                    "ocrEnabled": False,
                    "ocrProvider": "saved-provider",
                    "ocrModel": "saved-model",
                    "ocrPageBatchSize": 4,
                    "ocrMaxConcurrency": 2,
                }
            )
            runtime = service.ocr_runtime_settings()
            self.assertEqual(
                {
                    "ocrEnabled": False,
                    "ocrProvider": "saved-provider",
                    "ocrModel": "saved-model",
                    "ocrPageBatchSize": 4,
                    "ocrMaxConcurrency": 2,
                },
                runtime,
            )

    async def test_embedding_profile_resolves_saved_local_identity_and_fails_closed_for_api(self) -> None:
        with tempfile.TemporaryDirectory(prefix="study-app-settings-embedding-") as temp:
            root = Path(temp)
            service = SettingsService(
                settings_path=root / "settings.json",
                root=root,
                credential_service=_CredentialService(),
                environment_snapshot={
                    "EMBED_PROVIDER": "local",
                    "EMBED_API_MODEL": "environment-model",
                },
            )
            baseline = EmbeddingProfile(
                provider="model2vec",
                model="startup-model",
                embedding_version="model2vec-0.8.2",
                dimensions=256,
            )

            await service.update(
                {"embedProvider": "local", "embedApiModel": "saved-model"}
            )
            resolved = await service.embedding_profile(baseline)
            self.assertIsNotNone(resolved)
            self.assertEqual("model2vec", resolved.provider)
            self.assertEqual("saved-model", resolved.model)
            self.assertEqual(256, resolved.dimensions)

            await service.update({"embedProvider": "api"})
            self.assertIsNone(await service.embedding_profile(baseline))

    async def test_llm_probe_uses_saved_timeout_and_zero_uses_sdk_default(self) -> None:
        class _Response:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self):
                return b'{"choices":[{"message":{"content":"ok"}}]}'

        with mock.patch(
            "backend.app.application.settings.urllib.request.urlopen",
            return_value=_Response(),
        ) as urlopen:
            transport = _openai_compat_probe_transport(
                "https://example.test/v1",
                "fixture-model",
                timeout_seconds=7.5,
            )
            self.assertTrue(
                await transport(Credential(CredentialKind.LLM, "fixture-key"), "ping")
            )
            self.assertEqual(7.5, urlopen.call_args.kwargs["timeout"])

        with mock.patch(
            "backend.app.application.settings.urllib.request.urlopen",
            return_value=_Response(),
        ) as urlopen:
            transport = _openai_compat_probe_transport(
                "https://example.test/v1",
                "fixture-model",
                timeout_seconds=None,
            )
            self.assertTrue(
                await transport(Credential(CredentialKind.LLM, "fixture-key"), "ping")
            )
            self.assertNotIn("timeout", urlopen.call_args.kwargs)

    async def test_zero_llm_timeout_is_a_saved_sdk_default(self) -> None:
        with tempfile.TemporaryDirectory(prefix="study-app-settings-runtime-") as temp:
            root = Path(temp)
            settings_path = root / "settings.json"
            service = SettingsService(
                settings_path=settings_path,
                root=root,
                credential_service=_CredentialService(),
                environment_snapshot={"LLM_TIMEOUT": "90000"},
            )

            await service.update({"llmTimeout": 0})
            view = await service.view()

            self.assertEqual(0, view["llmTimeout"])
            self.assertEqual(0, json.loads(settings_path.read_text())["llmTimeout"])

    async def test_saved_artifact_directories_are_created_before_settings_commit(self) -> None:
        with tempfile.TemporaryDirectory(prefix="study-app-settings-directories-") as temp:
            root = Path(temp)
            settings_path = root / "settings.json"
            service = SettingsService(
                settings_path=settings_path,
                root=root,
                credential_service=_CredentialService(),
            )

            await service.update(
                {
                    "explainerDir": "artifacts/explainers",
                    "translationDir": "artifacts/translations",
                    "ocrMarkdownDir": "artifacts/ocr",
                }
            )

            self.assertTrue((root / "artifacts/explainers").is_dir())
            self.assertTrue((root / "artifacts/translations").is_dir())
            self.assertTrue((root / "artifacts/ocr").is_dir())

    async def test_artifact_directories_follow_saved_then_environment_then_default_priority(self) -> None:
        with tempfile.TemporaryDirectory(prefix="study-app-settings-dir-priority-") as temp:
            root = Path(temp)
            service = SettingsService(
                settings_path=root / "settings.json",
                root=root,
                credential_service=_CredentialService(),
                environment_snapshot={
                    "PDF_DIR": "env/pdfs",
                    "EXPLAINER_DIR": "env/explainers",
                    "TRANSLATION_DIR": "env/translations",
                    "OCR_MARKDOWN_DIR": "env/ocr",
                },
            )

            environment_view = await service.view()
            self.assertEqual("env/pdfs", environment_view["pdfDir"])
            self.assertEqual("env/explainers", environment_view["explainerDir"])
            self.assertEqual("env/translations", environment_view["translationDir"])
            self.assertEqual("env/ocr", environment_view["ocrMarkdownDir"])

            await service.update({"pdfDir": "saved/pdfs", "ocrMarkdownDir": "saved/ocr"})
            saved_view = await service.view()
            self.assertEqual("saved/pdfs", saved_view["pdfDir"])
            self.assertEqual("env/explainers", saved_view["explainerDir"])
            self.assertEqual("env/translations", saved_view["translationDir"])
            self.assertEqual("saved/ocr", saved_view["ocrMarkdownDir"])

    async def test_legacy_agent_directories_use_environment_when_settings_are_absent(self) -> None:
        with tempfile.TemporaryDirectory(prefix="study-app-agent-dir-priority-") as temp:
            root = Path(temp)
            environment = {
                **os.environ,
                "PAPER_STUDY_SETTINGS_PATH": str(root / "missing-settings.json"),
                "PDF_DIR": str(root / "env-pdfs"),
                "EXPLAINER_DIR": str(root / "env-explainers"),
                "TRANSLATION_DIR": str(root / "env-translations"),
                "OCR_MARKDOWN_DIR": str(root / "env-ocr"),
            }
            command = (
                "import json; from agent import config; "
                "print(json.dumps({"
                "'pdf': str(config.PDF_DIR), "
                "'explainer': str(config.EXPLAINER_DIR), "
                "'translation': str(config.TRANSLATION_DIR), "
                "'ocr': str(config.OCR_MARKDOWN_DIR)"
                "}))"
            )
            completed = subprocess.run(
                [sys.executable, "-B", "-c", command],
                cwd=Path(__file__).resolve().parents[2],
                env=environment,
                check=True,
                capture_output=True,
                text=True,
            )
            resolved = json.loads(completed.stdout)
            self.assertEqual(
                {
                    "pdf": str(root / "env-pdfs"),
                    "explainer": str(root / "env-explainers"),
                    "translation": str(root / "env-translations"),
                    "ocr": str(root / "env-ocr"),
                },
                resolved,
            )


if __name__ == "__main__":
    unittest.main()
