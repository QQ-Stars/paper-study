from __future__ import annotations

import hashlib
from pathlib import Path
import re
from typing import Callable, Sequence

from backend.app.application.ports.source_extractor import ExtractedSource
from backend.app.canonical_text import normalize_canonical_text
from backend.app.domain import (
    NATIVE_SOURCE_MODEL,
    NATIVE_SOURCE_PROVIDER,
    NativeExtractionFailedError,
    NativeTextEmptyError,
)


MarkdownExtractor = Callable[..., str]
PlainExtractor = Callable[[Path], tuple[Sequence[str] | str, int]]
PageCounter = Callable[[Path], int]


class NativeExtractor:
    provider = NATIVE_SOURCE_PROVIDER
    model = NATIVE_SOURCE_MODEL
    processing_version = "native-v1"

    def __init__(
        self,
        markdown_extractor: MarkdownExtractor | None = None,
        plain_extractor: PlainExtractor | None = None,
        page_counter: PageCounter | None = None,
    ) -> None:
        self._markdown_extractor = markdown_extractor or _extract_markdown
        self._plain_extractor = plain_extractor or _extract_plain_pages
        self._page_counter = page_counter or _page_count

    def extract(self, pdf_path: Path) -> ExtractedSource:
        resolved_path = Path(pdf_path)
        if not resolved_path.is_absolute() or not resolved_path.is_file():
            raise NativeExtractionFailedError()

        markdown: str | None = None
        page_count: int | None = None
        try:
            candidate = self._markdown_extractor(resolved_path, show_progress=False)
            if _non_whitespace_length(candidate) >= 200:
                markdown = candidate
                page_count = self._page_counter(resolved_path)
        except Exception:
            markdown = None

        if markdown is None:
            try:
                pages, page_count = self._plain_extractor(resolved_path)
                markdown = pages if isinstance(pages, str) else "\n\n".join(pages)
            except Exception as error:
                raise NativeExtractionFailedError() from error

        normalized = normalize_canonical_text(markdown)
        if not normalized.strip():
            raise NativeTextEmptyError()
        if not isinstance(page_count, int) or isinstance(page_count, bool) or page_count < 0:
            raise NativeExtractionFailedError()
        content_sha256 = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
        return ExtractedSource(
            markdown=normalized,
            content_sha256=content_sha256,
            page_count=page_count,
            provider=self.provider,
            model=self.model,
            processing_version=self.processing_version,
        )

def _non_whitespace_length(value: object) -> int:
    return len(re.sub(r"\s+", "", value)) if isinstance(value, str) else 0


def _extract_markdown(pdf_path: Path, *, show_progress: bool) -> str:
    import pymupdf4llm

    return pymupdf4llm.to_markdown(str(pdf_path), show_progress=show_progress)


def _extract_plain_pages(pdf_path: Path) -> tuple[list[str], int]:
    import fitz

    document = fitz.open(str(pdf_path))
    try:
        return ([document[index].get_text("text") for index in range(document.page_count)], document.page_count)
    finally:
        document.close()


def _page_count(pdf_path: Path) -> int:
    import fitz

    document = fitz.open(str(pdf_path))
    try:
        return int(document.page_count)
    finally:
        document.close()
