from __future__ import annotations

from types import TracebackType
from typing import Protocol

from backend.app.application.ports.repositories import (
    GeneratedArtifactRepository,
    PaperRepository,
    ProcessingJobRepository,
    SourceDocumentRepository,
    VaultProjectionRepository,
)


class UnitOfWork(Protocol):
    papers: PaperRepository
    sources: SourceDocumentRepository
    artifacts: GeneratedArtifactRepository
    jobs: ProcessingJobRepository
    projections: VaultProjectionRepository
    reproductions: object

    async def __aenter__(self) -> "UnitOfWork": ...
    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None: ...
    async def commit(self) -> None: ...
    async def rollback(self) -> None: ...
