from __future__ import annotations

from types import ModuleType, SimpleNamespace
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

from backend.app.application.ports.translation_provider import TranslationRequest
from backend.app.application.ports.structured_artifact_provider import (
    StructuredArtifactInput,
    StructuredArtifactRequest,
)
from backend.app.domain import (
    Credential,
    CredentialKind,
    TranslationProviderRequestError,
    WorkerConfigurationError,
)
from backend.app.providers.legacy_p3 import (
    LegacyChunkTranslationProvider,
    LegacyStructuredArtifactProvider,
    legacy_p3_provider_factories,
)


class LegacyP3ProviderTests(unittest.IsolatedAsyncioTestCase):
    async def test_translation_uses_request_scoped_effective_credential(self) -> None:
        observed: list[dict[str, object]] = []
        fake_llm = ModuleType("agent.llm")
        fake_llm.config = SimpleNamespace(PROVIDER="deepseek", MODEL="deepseek-v4-flash")
        fake_llm.TRANSLATE_SYSTEM = "strict translation"

        class Completions:
            def create(self, **request):
                observed.append(request)
                return SimpleNamespace(
                    choices=[SimpleNamespace(message=SimpleNamespace(content="严格的中文翻译结果。"))]
                )

        fake_llm.client = lambda **options: (
            observed.append({"clientOptions": options})
            or SimpleNamespace(chat=SimpleNamespace(completions=Completions()))
        )
        current = ["first-effective-key"]

        async def resolve(kind: CredentialKind) -> Credential | None:
            self.assertIs(CredentialKind.LLM, kind)
            return Credential(CredentialKind.LLM, current[0])

        provider = LegacyChunkTranslationProvider(
            "deepseek",
            "deepseek-v4-flash",
            credential_resolver=resolve,
        )
        request = TranslationRequest(
            artifact_id="artifact-1",
            source_document_id="source-1",
            source_content_sha256="a" * 64,
            chunk_id="chunk-1",
            sequence=0,
            markdown="Translate this source chunk.",
            content_kind="text",
        )

        with mock.patch.dict(sys.modules, _agent_modules(fake_llm)):
            await provider.translate(request)
            current[0] = "second-effective-key"
            await provider.translate(request)

        client_options = [
            item["clientOptions"] for item in observed if "clientOptions" in item
        ]
        self.assertEqual(
            [{"api_key": "first-effective-key"}, {"api_key": "second-effective-key"}],
            client_options,
        )

    async def test_structured_factory_refreshes_settings_without_global_environment_switch(
        self,
    ) -> None:
        observed: list[dict[str, object]] = []
        fake_llm = ModuleType("agent.llm")
        fake_llm.config = SimpleNamespace(PROVIDER="stale", MODEL="stale-model")

        class Completions:
            def create(self, **request):
                observed.append(request)
                return SimpleNamespace(
                    choices=[SimpleNamespace(message=SimpleNamespace(content='{"ok":true}'))]
                )

        fake_llm.client = lambda **options: (
            observed.append({"clientOptions": options})
            or SimpleNamespace(chat=SimpleNamespace(completions=Completions()))
        )
        with tempfile.TemporaryDirectory(prefix="study-app-p3-settings-") as temporary:
            settings_path = Path(temporary) / "settings.json"
            settings_path.write_text(
                json.dumps(
                    {
                        "provider": "deepseek",
                        "baseUrl": "https://first.example/v1",
                        "model": "first-model",
                        "llmTimeout": 1100,
                    }
                ),
                encoding="utf-8",
            )
            before_environment = dict(__import__("os").environ)
            _, structured_factory = legacy_p3_provider_factories(
                {"PAPER_STUDY_SETTINGS_PATH": str(settings_path)},
                credential_resolver=lambda _kind: _credential("first-key"),
            )
            provider = structured_factory()
            request = StructuredArtifactRequest(
                artifact_id="artifact-2",
                kind="summary",
                paper_id="paper-1",
                paper_title="Paper",
                paper_authors=(),
                prompt_version="summary-v1",
                stage="reduce",
                inputs=(
                    StructuredArtifactInput(
                        content="typed child",
                        covered_ranges=((0, 1),),
                    ),
                ),
            )
            with mock.patch.dict(sys.modules, _agent_modules(fake_llm)):
                await provider.generate(request)
                settings_path.write_text(
                    json.dumps(
                        {
                            "provider": "qwen",
                            "baseUrl": "https://second.example/v1",
                            "model": "second-model",
                            "llmTimeout": 2200,
                        }
                    ),
                    encoding="utf-8",
                )
                await provider.generate(request)
            self.assertEqual(before_environment, dict(__import__("os").environ))

        self.assertEqual(
            [
                {"api_key": "first-key", "base_url": "https://first.example/v1", "timeout": 1.1},
                {"api_key": "first-key", "base_url": "https://second.example/v1", "timeout": 2.2},
            ],
            [item["clientOptions"] for item in observed if "clientOptions" in item],
        )

    async def test_provider_identity_is_resolved_for_each_task(self) -> None:
        current = ["deepseek", "deepseek-v4-flash"]
        provider = LegacyChunkTranslationProvider(lambda: tuple(current))
        self.assertEqual("deepseek", provider.provider_id)
        self.assertEqual("deepseek-v4-flash", provider.model_id)
        current[:] = ["qwen", "qwen-plus"]
        self.assertEqual("qwen", provider.provider_id)
        self.assertEqual("qwen-plus", provider.model_id)

    async def test_translation_exhaustion_never_returns_source_as_success(self) -> None:
        transport_calls: list[str] = []
        fake_llm = ModuleType("agent.llm")
        fake_llm.config = SimpleNamespace(
            PROVIDER="deepseek",
            MODEL="deepseek-v4-flash",
        )
        fake_llm.TRANSLATE_SYSTEM = "strict translation"
        fake_llm.translate_md = lambda markdown: markdown

        class Completions:
            def create(self, **_request):
                transport_calls.append("create")
                raise TimeoutError("secret transport failure")

        fake_llm.client = lambda: SimpleNamespace(
            chat=SimpleNamespace(completions=Completions())
        )
        provider = LegacyChunkTranslationProvider(
            "deepseek",
            "deepseek-v4-flash",
        )
        request = TranslationRequest(
            artifact_id="artifact-1",
            source_document_id="source-1",
            source_content_sha256="a" * 64,
            chunk_id="chunk-1",
            sequence=0,
            markdown="This source sentence must never be checkpointed after failure.",
            content_kind="text",
        )

        with mock.patch.dict(sys.modules, _agent_modules(fake_llm)):
            with self.assertRaises(TranslationProviderRequestError) as caught:
                await provider.translate(request)

        self.assertTrue(caught.exception.retryable)
        self.assertEqual(3, len(transport_calls))

    async def test_declared_identity_mismatch_fails_before_transport(self) -> None:
        transport_calls: list[dict[str, object]] = []
        fake_llm = ModuleType("agent.llm")
        fake_llm.config = SimpleNamespace(PROVIDER="qwen", MODEL="qwen-plus")
        fake_llm.TRANSLATE_SYSTEM = "strict translation"

        class Completions:
            def create(self, **request):
                transport_calls.append(request)
                content = (
                    '{"coveredRanges":[[0,1]],"tldr":"x","contribution":"y"}'
                    if "response_format" in request
                    else "这是严格的中文翻译结果。"
                )
                return SimpleNamespace(
                    choices=[SimpleNamespace(message=SimpleNamespace(content=content))]
                )

        fake_llm.client = lambda: SimpleNamespace(
            chat=SimpleNamespace(completions=Completions())
        )
        translation = LegacyChunkTranslationProvider(
            "deepseek",
            "deepseek-v4-flash",
        )
        structured = LegacyStructuredArtifactProvider(
            "deepseek",
            "deepseek-v4-flash",
        )

        with mock.patch.dict(sys.modules, _agent_modules(fake_llm)):
            with self.assertRaises(TranslationProviderRequestError) as translation_error:
                await translation.translate(
                    TranslationRequest(
                        artifact_id="artifact-1",
                        source_document_id="source-1",
                        source_content_sha256="a" * 64,
                        chunk_id="chunk-1",
                        sequence=0,
                        markdown="Translate this source chunk.",
                        content_kind="text",
                    )
                )
            with self.assertRaises(WorkerConfigurationError):
                await structured.generate(
                    StructuredArtifactRequest(
                        artifact_id="artifact-2",
                        kind="summary",
                        paper_id="paper-1",
                        paper_title="Paper",
                        paper_authors=(),
                        prompt_version="summary-v1",
                        stage="reduce",
                        inputs=(
                            StructuredArtifactInput(
                                content="typed child",
                                covered_ranges=((0, 1),),
                            ),
                        ),
                    )
                )

        self.assertFalse(translation_error.exception.retryable)
        self.assertEqual([], transport_calls)


def _agent_modules(fake_llm: ModuleType) -> dict[str, ModuleType]:
    fake_agent = ModuleType("agent")
    fake_agent.llm = fake_llm
    return {"agent": fake_agent, "agent.llm": fake_llm}


async def _credential(value: str) -> Credential:
    return Credential(CredentialKind.LLM, value)


if __name__ == "__main__":
    unittest.main()
