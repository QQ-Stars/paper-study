from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class TranslationRequest:
    artifact_id: str
    source_document_id: str
    source_content_sha256: str
    chunk_id: str
    sequence: int
    markdown: str
    content_kind: str
    target_language: str = "zh-CN"

    def __post_init__(self) -> None:
        for name in (
            "artifact_id",
            "source_document_id",
            "source_content_sha256",
            "chunk_id",
            "content_kind",
            "target_language",
        ):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must be nonblank")
        if not isinstance(self.sequence, int) or isinstance(self.sequence, bool) or self.sequence < 0:
            raise ValueError("sequence must be nonnegative")
        if not isinstance(self.markdown, str):
            raise ValueError("markdown must be text")
        if self.content_kind not in {"text", "structured", "verbatim"}:
            raise ValueError("content_kind is invalid")


class TranslationProvider(Protocol):
    provider_id: str
    model_id: str
    prompt_version: str

    async def translate(self, request: TranslationRequest) -> str: ...


__all__ = ["TranslationProvider", "TranslationRequest"]
