from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True, slots=True)
class ExtractedSource:
    markdown: str
    content_sha256: str
    page_count: int
    provider: str
    model: str
    processing_version: str


class SourceExtractor(Protocol):
    provider: str
    model: str
    processing_version: str

    def extract(self, pdf_path: Path) -> ExtractedSource: ...
