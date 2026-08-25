from __future__ import annotations

import asyncio
from types import TracebackType

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.app.repositories.sqlalchemy import (
    SqlAlchemyPaperRepository,
    SqlAlchemyVaultProjectionRepository,
)
from backend.app.repositories.document_sources import SqlAlchemySourceDocumentRepository
from backend.app.repositories.document_chunks import SqlAlchemyDocumentChunkRepository
from backend.app.repositories.generated_artifacts import SqlAlchemyGeneratedArtifactRepository
from backend.app.repositories.processing_jobs import SqlAlchemyProcessingJobRepository
from backend.app.repositories.reproductions import SqlAlchemyReproductionRepository


class SqlAlchemyUnitOfWork:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory
        self._session: AsyncSession | None = None

    async def __aenter__(self) -> "SqlAlchemyUnitOfWork":
        if self._session is not None:
            raise RuntimeError("UnitOfWork is already active")
        self._session = self._session_factory()
        self.papers = SqlAlchemyPaperRepository(self._session)
        self.sources = SqlAlchemySourceDocumentRepository(self._session)
        self.chunks = SqlAlchemyDocumentChunkRepository(self._session)
        self.artifacts = SqlAlchemyGeneratedArtifactRepository(self._session)
        self.jobs = SqlAlchemyProcessingJobRepository(self._session)
        self.projections = SqlAlchemyVaultProjectionRepository(self._session)
        self.reproductions = SqlAlchemyReproductionRepository(self._session)
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        session = self._require_session()
        try:
            await _finish_despite_cancellation(session.rollback())
        finally:
            try:
                await _finish_despite_cancellation(session.close())
            finally:
                self._session = None

    async def commit(self) -> None:
        await self._require_session().commit()

    async def rollback(self) -> None:
        await self._require_session().rollback()

    def _require_session(self) -> AsyncSession:
        if self._session is None:
            raise RuntimeError("UnitOfWork is not active")
        return self._session


async def _finish_despite_cancellation(awaitable: object) -> None:
    task = asyncio.ensure_future(awaitable)
    try:
        await asyncio.shield(task)
    except asyncio.CancelledError:
        await task
        raise
