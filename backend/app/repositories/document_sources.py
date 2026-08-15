from __future__ import annotations

from sqlalchemy import and_, delete, or_, select, text, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.domain import PersistenceConflictError, SourceDocument
from backend.app.application.source_freshness import StaleResult
from backend.app.domain.processing import (
    EnqueueResult,
    JobSpecValidationError,
    NewProcessingJob,
    build_source_key,
    decode_job_spec_v1,
)
from backend.app.repositories.models import (
    DocumentChunkModel,
    GeneratedArtifactModel,
    PaperArtifactHeadModel,
    ProcessingJobModel,
    SourceDocumentModel,
)
from backend.app.repositories.processing_jobs import SqlAlchemyProcessingJobRepository, _timestamp
from backend.app.repositories.sqlalchemy import SqlAlchemySourceDocumentRepository as _P1SourceRepository


class SqlAlchemySourceDocumentRepository(_P1SourceRepository):
    """P2 source enqueue keeps target and job creation in the caller's one UoW."""

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session)
        self._jobs = SqlAlchemyProcessingJobRepository(session)

    async def stale_for_pdf_change(
        self,
        paper_id: str,
        current_pdf_sha256: str,
        *,
        now,
    ) -> StaleResult:
        """Cascade stale state for every ready source whose PDF identity drifted."""

        now_text = _timestamp(now)
        if not self._session.in_transaction():
            await self._session.execute(text("BEGIN IMMEDIATE"))
        source_ids = tuple(
            (
                await self._session.execute(
                    select(SourceDocumentModel.id)
                    .where(
                        SourceDocumentModel.paper_id == paper_id,
                        SourceDocumentModel.status == "ready",
                        SourceDocumentModel.pdf_sha256 != current_pdf_sha256,
                    )
                    .order_by(SourceDocumentModel.id)
                )
            ).scalars().all()
        )
        return await self._cascade_stale_sources(
            source_ids,
            now_text=now_text,
            source_error_code="SOURCE_PDF_CHANGED",
        )

    async def stale_for_active_source(
        self,
        source_document_id: str,
        *,
        now,
    ) -> StaleResult:
        """Stale older ready identities for the active source's paper and mode."""

        now_text = _timestamp(now)
        if not self._session.in_transaction():
            await self._session.execute(text("BEGIN IMMEDIATE"))
        active = await self._session.get(SourceDocumentModel, source_document_id)
        if active is None or active.status != "ready":
            raise ValueError("SOURCE_NOT_READY: activation requires a ready source")
        source_ids = tuple(
            (
                await self._session.execute(
                    select(SourceDocumentModel.id)
                    .where(
                        SourceDocumentModel.paper_id == active.paper_id,
                        SourceDocumentModel.mode == active.mode,
                        SourceDocumentModel.status == "ready",
                        SourceDocumentModel.id != active.id,
                        or_(
                            SourceDocumentModel.provider != active.provider,
                            SourceDocumentModel.model != active.model,
                            SourceDocumentModel.options_hash != active.options_hash,
                            SourceDocumentModel.processing_version
                            != active.processing_version,
                        ),
                    )
                    .order_by(SourceDocumentModel.id)
                )
            ).scalars().all()
        )
        return await self._cascade_stale_sources(
            source_ids,
            now_text=now_text,
            source_error_code="SOURCE_SUPERSEDED",
        )

    async def _cascade_stale_sources(
        self,
        source_ids: tuple[str, ...],
        *,
        now_text: str,
        source_error_code: str,
    ) -> StaleResult:
        if not source_ids:
            return StaleResult()

        artifact_ids = tuple(
            (
                await self._session.execute(
                    select(GeneratedArtifactModel.id)
                    .where(
                        GeneratedArtifactModel.source_document_id.in_(source_ids),
                        GeneratedArtifactModel.status == "ready",
                    )
                    .order_by(GeneratedArtifactModel.id)
                )
            ).scalars().all()
        )
        chunk_ids = tuple(
            (
                await self._session.execute(
                    select(DocumentChunkModel.id)
                    .where(
                        DocumentChunkModel.source_document_id.in_(source_ids),
                        DocumentChunkModel.status == "ready",
                    )
                    .order_by(DocumentChunkModel.source_document_id, DocumentChunkModel.sequence)
                )
            ).scalars().all()
        )
        source_parameters = {
            f"source_{index}": identifier
            for index, identifier in enumerate(source_ids)
        }
        embedding_ids = tuple(
            str(value)
            for value in (
                await self._session.execute(
                    text(
                        "SELECT id FROM document_chunk_embeddings "
                        "WHERE source_document_id IN ("
                        + ",".join(f":{name}" for name in source_parameters)
                        + ") AND status IN ('ready','failed') ORDER BY id"
                    ),
                    source_parameters,
                )
            ).scalars().all()
        )
        head_rows = (
            await self._session.execute(
                select(
                    PaperArtifactHeadModel.paper_id,
                    PaperArtifactHeadModel.kind,
                    PaperArtifactHeadModel.artifact_id,
                )
                .where(PaperArtifactHeadModel.artifact_id.in_(artifact_ids))
                .order_by(PaperArtifactHeadModel.paper_id, PaperArtifactHeadModel.kind)
            )
        ).all()
        queued_jobs = tuple(
            (
                await self._session.execute(
                    select(ProcessingJobModel)
                    .where(
                        ProcessingJobModel.source_document_id.in_(source_ids),
                        ProcessingJobModel.status == "queued",
                    )
                    .order_by(ProcessingJobModel.id)
                )
            ).scalars().all()
        )
        running_jobs = tuple(
            (
                await self._session.execute(
                    select(ProcessingJobModel)
                    .where(
                        ProcessingJobModel.source_document_id.in_(source_ids),
                        ProcessingJobModel.status == "running",
                    )
                    .order_by(ProcessingJobModel.id)
                )
            ).scalars().all()
        )

        source_update = await self._session.execute(
            update(SourceDocumentModel)
            .where(
                SourceDocumentModel.id.in_(source_ids),
                SourceDocumentModel.status == "ready",
            )
            .values(
                status="stale",
                error_code=source_error_code,
                error_message=None,
                stale_at=now_text,
                updated_at=now_text,
            )
        )
        if source_update.rowcount != len(source_ids):
            raise PersistenceConflictError(operation="cascade_stale_sources")
        if artifact_ids:
            await self._session.execute(
                update(GeneratedArtifactModel)
                .where(
                    GeneratedArtifactModel.id.in_(artifact_ids),
                    GeneratedArtifactModel.status == "ready",
                )
                .values(status="stale", stale_at=now_text, updated_at=now_text)
            )
        if chunk_ids:
            await self._session.execute(
                update(DocumentChunkModel)
                .where(
                    DocumentChunkModel.id.in_(chunk_ids),
                    DocumentChunkModel.status == "ready",
                )
                .values(status="stale", stale_at=now_text, updated_at=now_text)
            )
        if embedding_ids:
            await self._session.execute(
                text(
                    "UPDATE document_chunk_embeddings SET status='stale',"
                    "stale_at=:now,updated_at=:now WHERE id IN ("
                    + ",".join(f":embedding_{index}" for index in range(len(embedding_ids)))
                    + ") AND status IN ('ready','failed')"
                ),
                {
                    "now": now_text,
                    **{
                        f"embedding_{index}": identifier
                        for index, identifier in enumerate(embedding_ids)
                    },
                },
            )
        if head_rows:
            await self._session.execute(
                delete(PaperArtifactHeadModel).where(
                    PaperArtifactHeadModel.artifact_id.in_(artifact_ids)
                )
            )

        queued_artifact_ids = tuple(
            row.artifact_id for row in queued_jobs if row.artifact_id is not None
        )
        if queued_artifact_ids:
            await self._session.execute(
                update(GeneratedArtifactModel)
                .where(
                    GeneratedArtifactModel.id.in_(queued_artifact_ids),
                    GeneratedArtifactModel.status == "queued",
                )
                .values(status="cancelled", updated_at=now_text)
            )
        queued_job_ids = tuple(row.id for row in queued_jobs)
        if queued_job_ids:
            await self._session.execute(
                update(ProcessingJobModel)
                .where(
                    ProcessingJobModel.id.in_(queued_job_ids),
                    ProcessingJobModel.status == "queued",
                )
                .values(
                    status="cancelled",
                    finished_at=now_text,
                    cancelled_at=now_text,
                    updated_at=now_text,
                )
            )
            for job_id in queued_job_ids:
                await self._jobs._append_event(job_id, "cancelled", {}, None, now_text)
        running_job_ids = tuple(row.id for row in running_jobs)
        if running_job_ids:
            await self._session.execute(
                update(ProcessingJobModel)
                .where(
                    ProcessingJobModel.id.in_(running_job_ids),
                    ProcessingJobModel.status == "running",
                    ProcessingJobModel.cancel_requested_at.is_(None),
                )
                .values(cancel_requested_at=now_text, updated_at=now_text)
            )
            for job_id in running_job_ids:
                await self._jobs._append_event(job_id, "cancel_requested", {}, None, now_text)

        return StaleResult(
            source_ids=source_ids,
            artifact_ids=artifact_ids,
            chunk_ids=chunk_ids,
            embedding_ids=embedding_ids,
            removed_head_keys=tuple(
                f"{row.paper_id}:{row.kind}" for row in head_rows
            ),
            cancelled_job_ids=queued_job_ids,
            cancel_requested_job_ids=running_job_ids,
        )

    async def list_page(
        self,
        *,
        paper_id: str,
        limit: int,
        cursor: tuple[str, str] | None,
    ) -> tuple[tuple[SourceDocument, ...], tuple[str, str] | None]:
        statement = select(SourceDocumentModel).where(
            SourceDocumentModel.paper_id == paper_id
        )
        if cursor is not None:
            created_at, identifier = cursor
            statement = statement.where(
                or_(
                    SourceDocumentModel.created_at < created_at,
                    and_(
                        SourceDocumentModel.created_at == created_at,
                        SourceDocumentModel.id < identifier,
                    ),
                )
            )
        rows = (
            await self._session.execute(
                statement.order_by(
                    SourceDocumentModel.created_at.desc(),
                    SourceDocumentModel.id.desc(),
                ).limit(limit + 1)
            )
        ).scalars().all()
        visible = rows[:limit]
        collected: list[SourceDocument] = []
        for row in visible:
            document = await self.get(row.id)
            if document is not None:
                collected.append(document)
        items = tuple(collected)
        next_cursor = (
            (visible[-1].created_at, visible[-1].id)
            if len(rows) > limit and visible
            else None
        )
        return items, next_cursor

    async def enqueue_with_job(
        self,
        document: SourceDocument,
        job: NewProcessingJob,
        *,
        spec_json: str,
        spec_sha256: str,
    ) -> tuple[SourceDocument, EnqueueResult]:
        # Validate the raw boundary before the target INSERT is even issued.
        spec = decode_job_spec_v1(spec_json, expected_row={
            "job_type": job.spec.job_type,
            "paper_id": job.spec.paper_id,
            "source_mode": job.spec.source_mode,
            "source_document_id": job.spec.source_document_id,
            "artifact_id": job.spec.artifact_id,
        })
        if spec != job.spec or spec.source_document_id != document.id:
            raise JobSpecValidationError("source enqueue spec does not bind the target")
        source_key = build_source_key(
            paper_id=document.paper_id,
            mode=document.mode.value,
            provider=document.provider,
            model=document.model,
            pdf_sha256=document.pdf_sha256,
            options_hash=document.options_hash,
            processing_version=document.processing_version,
        )
        statement = sqlite_insert(SourceDocumentModel).values(
            id=document.id,
            paper_id=document.paper_id,
            mode=document.mode.value,
            status=document.status.value,
            provider=document.provider,
            model=document.model,
            pdf_sha256=document.pdf_sha256,
            options_hash=document.options_hash,
            content_sha256=document.content_sha256,
            markdown=document.markdown,
            page_count=document.page_count,
            processing_version=document.processing_version,
            error_code=document.error_code,
            error_message=document.error_message,
            created_at=_timestamp(document.created_at),
            updated_at=_timestamp(document.updated_at),
            source_key=source_key,
            ready_at=_timestamp(document.updated_at) if document.status.value == "ready" else None,
            stale_at=_timestamp(document.updated_at) if document.status.value == "stale" else None,
        ).on_conflict_do_nothing(
            index_elements=[SourceDocumentModel.source_key],
            index_where=SourceDocumentModel.source_key.is_not(None),
        )
        result = await self._session.execute(statement)
        winner = document
        if result.rowcount != 1:
            row = (
                await self._session.execute(
                    select(SourceDocumentModel).where(SourceDocumentModel.source_key == source_key)
                )
            ).scalar_one_or_none()
            if row is None:
                raise JobSpecValidationError("source enqueue conflict has no winner")
            winner = await self.get(row.id)
            if winner is None:
                raise JobSpecValidationError("source enqueue conflict has no readable winner")
            if winner.id != document.id:
                raise JobSpecValidationError("source enqueue target id must be stable")
        job_result = await self._jobs.insert_with_spec(
            job, spec_json=spec_json, spec_sha256=spec_sha256,
        )
        return winner, job_result
