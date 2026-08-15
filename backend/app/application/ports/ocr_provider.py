from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Protocol, Sequence

from backend.app.domain.processing import JsonValue


@dataclass(frozen=True, slots=True)
class OcrRequest:
    source_id: str
    paper_id: str
    pdf_bytes: bytes
    pdf_sha256: str
    media_type: str
    model: str
    options: Mapping[str, JsonValue]
    page_numbers: Sequence[int]
    total_pages: int

    def __post_init__(self) -> None:
        if not isinstance(self.source_id, str) or not self.source_id.strip():
            raise ValueError("source_id must be nonblank")
        if not isinstance(self.paper_id, str) or not self.paper_id.strip():
            raise ValueError("paper_id must be nonblank")
        if not isinstance(self.pdf_bytes, bytes) or not self.pdf_bytes:
            raise ValueError("pdf_bytes must be nonempty bytes")
        if not isinstance(self.pdf_sha256, str) or len(self.pdf_sha256) != 64:
            raise ValueError("pdf_sha256 must be a SHA-256 digest")
        if not isinstance(self.media_type, str) or not self.media_type.strip():
            raise ValueError("media_type must be nonblank")
        if not isinstance(self.model, str) or not self.model.strip():
            raise ValueError("model must be nonblank")
        pages = tuple(self.page_numbers)
        if not pages or any(not isinstance(page, int) or isinstance(page, bool) or page < 1 for page in pages):
            raise ValueError("page_numbers must be positive integers")
        if len(set(pages)) != len(pages) or tuple(sorted(pages)) != pages:
            raise ValueError("page_numbers must be unique and ascending")
        if not isinstance(self.total_pages, int) or isinstance(self.total_pages, bool) or self.total_pages < pages[-1]:
            raise ValueError("total_pages must cover requested pages")
        object.__setattr__(self, "page_numbers", pages)


@dataclass(frozen=True, slots=True)
class OcrPageResult:
    page_number: int
    markdown: str
    content_sha256: str
    provider_page_id: str | None


@dataclass(frozen=True, slots=True)
class OcrResult:
    pages: Sequence[OcrPageResult]
    provider: str
    model: str
    processing_version: str
    provider_request_id: str | None


class OcrProvider(Protocol):
    provider_id: str

    async def extract_batch(self, request: OcrRequest) -> OcrResult: ...
