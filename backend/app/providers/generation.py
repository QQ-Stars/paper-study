from __future__ import annotations

from collections.abc import Callable

from backend.app.application.ports.artifact_generator import (
    GeneratorIdentity,
    PaperMetadata,
)


class LegacyGenerationProvider:
    def __init__(
        self,
        explainer: Callable[[PaperMetadata, str], str] | None = None,
        translator: Callable[[PaperMetadata, str], str] | None = None,
    ) -> None:
        self._explainer = explainer or _generate_explainer
        self._translator = translator or _generate_translation

    def identity(self, kind: str, profile: str = "standard") -> GeneratorIdentity:
        if kind == "explainer":
            if profile not in {"standard", "deep"}:
                raise ValueError("unsupported explainer profile")
            prompt_version = "explainer-v1" if profile == "standard" else "explainer-deep-v1"
            return GeneratorIdentity("legacy-llm", "configured-llm", prompt_version)
        if kind == "translation":
            if profile != "standard":
                raise ValueError("translation does not support profiles")
            return GeneratorIdentity("legacy-llm", "configured-llm", "translation-v1")
        raise ValueError("unsupported artifact kind")

    def generate(
        self,
        kind: str,
        paper: PaperMetadata,
        source_markdown: str,
        profile: str = "standard",
    ) -> str:
        if kind == "explainer":
            if profile not in {"standard", "deep"}:
                raise ValueError("unsupported explainer profile")
            return self._explainer(paper, source_markdown)
        if kind == "translation":
            if profile != "standard":
                raise ValueError("translation does not support profiles")
            return self._translator(paper, source_markdown)
        raise ValueError("unsupported artifact kind")


def _generate_explainer(paper: PaperMetadata, source_markdown: str) -> str:
    from agent import llm

    row = {
        "id": paper.id,
        "title": paper.title,
        "authors": list(paper.authors),
        "abstract": paper.abstract,
    }
    return llm.generate_explainer(row, source_markdown)


def _generate_translation(_paper: PaperMetadata, source_markdown: str) -> str:
    from agent import llm

    return llm.translate_md(source_markdown)
