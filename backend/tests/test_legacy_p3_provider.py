from __future__ import annotations

from types import ModuleType, SimpleNamespace
import sys
import unittest
from unittest import mock

from backend.app.application.ports.translation_provider import TranslationRequest
from backend.app.application.ports.structured_artifact_provider import (
    StructuredArtifactInput,
    StructuredArtifactRequest,
)
from backend.app.domain import TranslationProviderRequestError, WorkerConfigurationError
from backend.app.providers.legacy_p3 import (
    LegacyChunkTranslationProvider,
    LegacyStructuredArtifactProvider,
)


class LegacyP3ProviderTests(unittest.IsolatedAsyncioTestCase):
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


if __name__ == "__main__":
    unittest.main()
