from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class PaperMetadata:
    id: str
    title: str
    authors: tuple[str, ...]
    abstract: str | None


@dataclass(frozen=True, slots=True)
class GeneratorIdentity:
    provider: str
    model: str
    prompt_version: str

    def __post_init__(self) -> None:
        for value in (self.provider, self.model, self.prompt_version):
            if not isinstance(value, str) or not value.strip():
                raise ValueError("generator identity values must be nonblank")


class ArtifactGenerator(Protocol):
    def identity(self, kind: str) -> GeneratorIdentity: ...
    def generate(self, kind: str, paper: PaperMetadata, source_markdown: str) -> str: ...
