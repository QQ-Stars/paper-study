from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Mapping

from sqlalchemy import and_, or_, select, text, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.domain import (
    GeneratedArtifact,
    JobLeaseLostError,
    PersistenceConflictError,
)
from backend.app.domain.context import chunk_key_for
from backend.app.domain.processing import (
    EnqueueResult,
    JobSpecValidationError,
    NewProcessingJob,
    build_artifact_key,
    decode_job_spec_v1,
)
from backend.app.repositories.models import (
    ArtifactTranslationCheckpointModel,
    DocumentChunkModel,
    GeneratedArtifactModel,
    PaperModel,
    PaperArtifactHeadModel,
    ProcessingJobModel,
    SourceDocumentModel,
)
from backend.app.repositories.processing_jobs import SqlAlchemyProcessingJobRepository, _timestamp
from backend.app.repositories.sqlalchemy import SqlAlchemyGeneratedArtifactRepository as _P1ArtifactRepository


class SqlAlchemyGeneratedArtifactRepository(_P1ArtifactRepository):
    """P2 artifact enqueue validates source ownership before any artifact write."""

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session)
        self._jobs = SqlAlchemyProcessingJobRepository(session)

    async def list_page(
        self,
        *,
        paper_id: str,
        kind: str | None,
        limit: int,
        cursor: tuple[str, str] | None,
    ) -> tuple[tuple[GeneratedArtifact, ...], tuple[str, str] | None]:
        statement = select(GeneratedArtifactModel).where(
            GeneratedArtifactModel.paper_id == paper_id
        )
        if kind is not None:
            statement = statement.where(GeneratedArtifactModel.kind == kind)
        if cursor is not None:
            created_at, identifier = cursor
            statement = statement.where(
                or_(
                    GeneratedArtifactModel.created_at < created_at,
                    and_(
                        GeneratedArtifactModel.created_at == created_at,
                        GeneratedArtifactModel.id < identifier,
                    ),
                )
            )
        rows = (
            await self._session.execute(
                statement.order_by(
                    GeneratedArtifactModel.created_at.desc(),
                    GeneratedArtifactModel.id.desc(),
                ).limit(limit + 1)
            )
        ).scalars().all()
        visible = rows[:limit]
        collected: list[GeneratedArtifact] = []
        for row in visible:
            artifact = await self.get(row.id)
            if artifact is not None:
                collected.append(artifact)
        next_cursor = (
            (visible[-1].created_at, visible[-1].id)
            if len(rows) > limit and visible
            else None
        )
        return tuple(collected), next_cursor

    async def enqueue_with_job(
        self,
        artifact: GeneratedArtifact,
        job: NewProcessingJob,
        *,
        spec_json: str,
        spec_sha256: str,
        kind_specific_options: Mapping[str, object] | None = None,
        expected_pdf_sha256: str | None = None,
        pdf_path: Path | None = None,
        expected_source_provider: str | None = None,
        expected_source_model: str | None = None,
    ) -> EnqueueResult:
        spec = decode_job_spec_v1(spec_json, expected_row={
            "job_type": job.spec.job_type,
            "paper_id": job.spec.paper_id,
            "source_mode": job.spec.source_mode,
            "source_document_id": job.spec.source_document_id,
            "artifact_id": job.spec.artifact_id,
        })
        if (
            spec != job.spec
            or spec.job_type not in {"explain", "translate"}
            or spec.artifact_id != artifact.id
            or spec.source_document_id != artifact.source_document_id
            or spec.paper_id != artifact.paper_id
        ):
            raise JobSpecValidationError("artifact enqueue spec does not bind the target")
        if (expected_pdf_sha256 is None) != (pdf_path is None):
            raise ValueError("artifact enqueue PDF identity must be complete")
        if expected_pdf_sha256 is not None and (
            not isinstance(expected_pdf_sha256, str)
            or len(expected_pdf_sha256) != 64
        ):
            raise ValueError("artifact enqueue PDF SHA-256 is invalid")
        if pdf_path is not None:
            if not self._session.in_transaction():
                await self._session.execute(text("BEGIN IMMEDIATE"))
        source = await self._session.get(SourceDocumentModel, artifact.source_document_id)
        paper = await self._session.get(PaperModel, artifact.paper_id)
        if (
            source is None
            or source.paper_id != artifact.paper_id
            or source.mode != spec.source_mode
            or source.status != "ready"
            or source.content_sha256 is None
            or (
                expected_source_provider is not None
                and source.provider != expected_source_provider
            )
            or (
                expected_source_model is not None
                and source.model != expected_source_model
            )
            or (
                expected_pdf_sha256 is not None
                and source.pdf_sha256 != expected_pdf_sha256
            )
        ):
            raise JobSpecValidationError("artifact enqueue requires a ready source for the same paper")
        if pdf_path is not None:
            await _assert_artifact_enqueue_pdf_identity(
                paper=paper,
                expected_path=pdf_path,
                expected_sha256=expected_pdf_sha256,
            )
        if (
            expected_source_provider is not None
            and source.provider != expected_source_provider
        ) or (
            expected_source_model is not None
            and source.model != expected_source_model
        ):
            raise JobSpecValidationError("artifact enqueue source identity changed")
        artifact_key = build_artifact_key(
            kind=artifact.kind.value,
            source_document_id=artifact.source_document_id,
            source_content_sha256=source.content_sha256,
            generator_provider=artifact.generator_provider,
            generator_model=artifact.generator_model,
            prompt_version=artifact.prompt_version,
            kind_specific_options=(
                dict(kind_specific_options)
                if kind_specific_options is not None
                else {"profile": getattr(spec, "profile", "")}
            ),
        )
        statement = sqlite_insert(GeneratedArtifactModel).values(
            id=artifact.id,
            paper_id=artifact.paper_id,
            kind=artifact.kind.value,
            source_document_id=artifact.source_document_id,
            status=artifact.status.value,
            content=artifact.content,
            content_sha256=artifact.content_sha256,
            generator_provider=artifact.generator_provider,
            generator_model=artifact.generator_model,
            prompt_version=artifact.prompt_version,
            error_code=artifact.error_code,
            error_message=artifact.error_message,
            created_at=_timestamp(artifact.created_at),
            updated_at=_timestamp(artifact.updated_at),
            artifact_key=artifact_key,
            ready_at=_timestamp(artifact.updated_at) if artifact.status.value == "ready" else None,
            stale_at=_timestamp(artifact.updated_at) if artifact.status.value == "stale" else None,
        ).on_conflict_do_nothing(
            index_elements=[GeneratedArtifactModel.artifact_key],
            index_where=GeneratedArtifactModel.artifact_key.is_not(None),
        )
        result = await self._session.execute(statement)
        if result.rowcount != 1:
            winner = (
                await self._session.execute(
                    select(GeneratedArtifactModel).where(GeneratedArtifactModel.artifact_key == artifact_key)
                )
            ).scalar_one_or_none()
            if winner is None or winner.id != artifact.id:
                raise JobSpecValidationError("artifact enqueue target id must be stable")
        result = await self._jobs.insert_with_spec(
            job,
            spec_json=spec_json,
            spec_sha256=spec_sha256,
        )
        if pdf_path is not None:
            await _assert_artifact_enqueue_pdf_identity(
                paper=paper,
                expected_path=pdf_path,
                expected_sha256=expected_pdf_sha256,
            )
        return result

    async def find_by_artifact_key(self, artifact_key: str) -> GeneratedArtifact | None:
        if not isinstance(artifact_key, str) or not artifact_key.strip():
            raise ValueError("artifact_key must be nonblank")
        row = (
            await self._session.execute(
                select(GeneratedArtifactModel).where(
                    GeneratedArtifactModel.artifact_key == artifact_key
                )
            )
        ).scalar_one_or_none()
        return await self.get(row.id) if row is not None else None

    async def publish_head(
        self,
        *,
        paper_id: str,
        kind: str,
        artifact_id: str,
        expected_artifact_id: str | None,
        updated_at,
    ) -> bool:
        artifact = await self._session.get(GeneratedArtifactModel, artifact_id)
        if (
            artifact is None
            or artifact.paper_id != paper_id
            or artifact.kind != kind
            or artifact.status != "ready"
        ):
            return False
        statement = sqlite_insert(PaperArtifactHeadModel).values(
            paper_id=paper_id,
            kind=kind,
            artifact_id=artifact_id,
            updated_at=_timestamp(updated_at),
        )
        if expected_artifact_id is None:
            statement = statement.on_conflict_do_nothing(
                index_elements=[PaperArtifactHeadModel.paper_id, PaperArtifactHeadModel.kind]
            )
        else:
            statement = statement.on_conflict_do_update(
                index_elements=[PaperArtifactHeadModel.paper_id, PaperArtifactHeadModel.kind],
                set_={"artifact_id": artifact_id, "updated_at": _timestamp(updated_at)},
                where=PaperArtifactHeadModel.artifact_id == expected_artifact_id,
            )
        result = await self._session.execute(statement)
        return result.rowcount == 1

    async def get_head_artifact_id(self, *, paper_id: str, kind: str) -> str | None:
        return (
            await self._session.execute(
                select(PaperArtifactHeadModel.artifact_id).where(
                    PaperArtifactHeadModel.paper_id == paper_id,
                    PaperArtifactHeadModel.kind == kind,
                )
            )
        ).scalar_one_or_none()


    async def publish_explainer(
        self,
        *,
        lease,
        source_document_id: str,
        expected_source_mode: str,
        expected_source_pdf_sha256: str,
        expected_source_content_sha256: str,
        expected_source_processing_version: str,
        artifact_id: str,
        expected_head_artifact_id: str | None,
        content: str,
        content_sha256: str,
        updated_at,
    ) -> GeneratedArtifact:
        """Publish the explainer, head, legacy projection and job as one CAS."""

        updated_at_text = _timestamp(updated_at)
        await self._session.execute(text("BEGIN IMMEDIATE"))
        job = await self._session.get(ProcessingJobModel, lease.job.id)
        if job is None:
            raise JobLeaseLostError(operation="publish_explainer_job_missing")
        stored = self._jobs._stored_from_row(job)
        if (
            stored != lease.spec
            or job.job_type != "explain"
            or job.status != "running"
            or job.lease_owner != lease.worker_id
            or job.lease_token != lease.token
            or job.lease_expires_at is None
            or job.lease_expires_at <= updated_at_text
            or job.cancel_requested_at is not None
            or job.paper_id != stored.value.paper_id
            or job.source_document_id != source_document_id
            or job.artifact_id != artifact_id
        ):
            raise JobLeaseLostError(operation="publish_explainer_lease_lost")

        source = await self._session.get(SourceDocumentModel, source_document_id)
        if (
            source is None
            or source.status != "ready"
            or source.paper_id != stored.value.paper_id
            or source.mode != expected_source_mode
            or source.mode != stored.value.source_mode
            or source.pdf_sha256 != expected_source_pdf_sha256
            or source.content_sha256 != expected_source_content_sha256
            or source.processing_version != expected_source_processing_version
            or source.markdown is None
            or hashlib.sha256(source.markdown.encode("utf-8")).hexdigest()
            != expected_source_content_sha256
        ):
            raise PersistenceConflictError(operation="publish_explainer_source_changed")
        artifact = await self._session.get(GeneratedArtifactModel, artifact_id)
        if (
            artifact is None
            or artifact.status != "running"
            or artifact.paper_id != stored.value.paper_id
            or artifact.kind != "explainer"
            or artifact.source_document_id != source_document_id
            or artifact.generator_provider != stored.value.provider
            or artifact.generator_model != stored.value.model
            or artifact.prompt_version != stored.value.prompt_version
        ):
            raise PersistenceConflictError(operation="publish_explainer_artifact_changed")
        current_head_artifact_id = await self.get_head_artifact_id(
            paper_id=job.paper_id,
            kind="explainer",
        )
        if current_head_artifact_id != expected_head_artifact_id:
            raise PersistenceConflictError(operation="publish_explainer_head_changed")

        new_artifact = await self._session.execute(
            update(GeneratedArtifactModel)
            .where(
                GeneratedArtifactModel.id == artifact_id,
                GeneratedArtifactModel.status == "running",
            )
            .values(
                status="ready",
                content=content,
                content_sha256=content_sha256,
                error_code=None,
                error_message=None,
                ready_at=updated_at_text,
                stale_at=None,
                updated_at=updated_at_text,
            )
        )
        if new_artifact.rowcount != 1:
            raise PersistenceConflictError(operation="publish_explainer_artifact")
        if expected_head_artifact_id is not None:
            old_artifact = await self._session.execute(
                update(GeneratedArtifactModel)
                .where(
                    GeneratedArtifactModel.id == expected_head_artifact_id,
                    GeneratedArtifactModel.status == "ready",
                )
                .values(
                    status="stale",
                    stale_at=updated_at_text,
                    updated_at=updated_at_text,
                )
            )
            if old_artifact.rowcount != 1:
                raise PersistenceConflictError(operation="publish_explainer_old_head")
        if not await self.publish_head(
            paper_id=job.paper_id,
            kind="explainer",
            artifact_id=artifact_id,
            expected_artifact_id=expected_head_artifact_id,
            updated_at=updated_at,
        ):
            raise PersistenceConflictError(operation="publish_explainer_head")
        await self.write_legacy_explainer(job.paper_id, content, updated_at)
        result_json = json.dumps(
            {"artifactId": artifact_id, "contentSha256": content_sha256},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        completed = await self._session.execute(
            update(ProcessingJobModel)
            .where(
                ProcessingJobModel.id == job.id,
                ProcessingJobModel.status == "running",
                ProcessingJobModel.lease_owner == lease.worker_id,
                ProcessingJobModel.lease_token == lease.token,
                ProcessingJobModel.lease_expires_at > updated_at_text,
                ProcessingJobModel.cancel_requested_at.is_(None),
            )
            .values(
                status="succeeded",
                result_json=result_json,
                finished_at=updated_at_text,
                lease_owner=None,
                lease_token=None,
                lease_expires_at=None,
                heartbeat_at=None,
                updated_at=updated_at_text,
            )
        )
        if completed.rowcount != 1:
            raise JobLeaseLostError(operation="publish_explainer_lease_lost")
        await self._jobs._append_event(job.id, "succeeded", {}, None, updated_at_text)
        await self._session.refresh(artifact)
        published = await self.get(artifact_id)
        if published is None:
            raise PersistenceConflictError(operation="publish_explainer_artifact_missing")
        return published

    async def publish_translation(
        self,
        *,
        lease,
        source_document_id: str,
        expected_source_mode: str,
        expected_source_pdf_sha256: str,
        expected_source_content_sha256: str,
        expected_source_processing_version: str,
        expected_chunking_version: str,
        expected_chunk_identities: tuple[tuple[str, int, str, str, int, int], ...],
        artifact_id: str,
        expected_head_artifact_id: str | None,
        content: str,
        content_sha256: str,
        updated_at,
    ) -> GeneratedArtifact:
        """Publish translation, head, legacy row, and job after one identity fence."""

        updated_at_text = _timestamp(updated_at)
        await self._session.execute(text("BEGIN IMMEDIATE"))
        job = await self._session.get(ProcessingJobModel, lease.job.id)
        if job is None:
            raise JobLeaseLostError(operation="publish_translation_job_missing")
        stored = self._jobs._stored_from_row(job)
        if (
            stored != lease.spec
            or job.job_type != "translate"
            or job.status != "running"
            or job.lease_owner != lease.worker_id
            or job.lease_token != lease.token
            or job.lease_expires_at is None
            or job.lease_expires_at <= updated_at_text
            or job.cancel_requested_at is not None
            or job.paper_id != stored.value.paper_id
            or job.source_document_id != source_document_id
            or job.artifact_id != artifact_id
        ):
            raise JobLeaseLostError(operation="publish_translation_lease_lost")
        source = await self._session.get(SourceDocumentModel, source_document_id)
        if (
            source is None
            or source.status != "ready"
            or source.paper_id != stored.value.paper_id
            or source.mode != expected_source_mode
            or source.mode != stored.value.source_mode
            or source.pdf_sha256 != expected_source_pdf_sha256
            or source.content_sha256 != expected_source_content_sha256
            or source.processing_version != expected_source_processing_version
            or source.markdown is None
            or hashlib.sha256(source.markdown.encode("utf-8")).hexdigest()
            != expected_source_content_sha256
        ):
            raise PersistenceConflictError(operation="publish_translation_source_changed")
        artifact = await self._session.get(GeneratedArtifactModel, artifact_id)
        if (
            artifact is None
            or artifact.status != "running"
            or artifact.paper_id != stored.value.paper_id
            or artifact.kind != "translation"
            or artifact.source_document_id != source_document_id
        ):
            raise PersistenceConflictError(operation="publish_translation_artifact_changed")
        chunks = (
            await self._session.execute(
                select(DocumentChunkModel)
                .where(
                    DocumentChunkModel.source_document_id == source_document_id,
                    DocumentChunkModel.status == "ready",
                )
                .order_by(DocumentChunkModel.sequence.asc())
            )
        ).scalars().all()
        actual_chunk_identities = _verified_persisted_chunk_identities(
            chunks,
            source_document_id=source_document_id,
            source_markdown=source.markdown,
            source_content_sha256=expected_source_content_sha256,
            chunking_version=expected_chunking_version,
        )
        if actual_chunk_identities is None or actual_chunk_identities != expected_chunk_identities:
            raise PersistenceConflictError(operation="publish_translation_chunks_changed")
        checkpoints = (
            await self._session.execute(
                select(ArtifactTranslationCheckpointModel)
                .where(ArtifactTranslationCheckpointModel.artifact_id == artifact_id)
                .order_by(ArtifactTranslationCheckpointModel.sequence.asc())
            )
        ).scalars().all()
        assembled = "".join(
            checkpoint.translated_markdown or "" for checkpoint in checkpoints
        )
        if (
            len(checkpoints) != len(chunks)
            or any(
                checkpoint.sequence != sequence
                or checkpoint.chunk_id != chunks[sequence].id
                or checkpoint.source_content_sha256 != expected_source_content_sha256
                or checkpoint.provider != artifact.generator_provider
                or checkpoint.model != artifact.generator_model
                or checkpoint.prompt_version != artifact.prompt_version
                or checkpoint.status != "succeeded"
                or checkpoint.translated_markdown is None
                or checkpoint.content_sha256
                != hashlib.sha256(
                    checkpoint.translated_markdown.encode("utf-8")
                ).hexdigest()
                for sequence, checkpoint in enumerate(checkpoints)
            )
            or assembled != content
            or hashlib.sha256(assembled.encode("utf-8")).hexdigest() != content_sha256
            or not assembled.strip()
        ):
            raise PersistenceConflictError(operation="publish_translation_checkpoints_changed")
        current_head_artifact_id = await self.get_head_artifact_id(
            paper_id=job.paper_id,
            kind="translation",
        )
        if current_head_artifact_id != expected_head_artifact_id:
            raise PersistenceConflictError(operation="publish_translation_head_changed")
        new_artifact = await self._session.execute(
            update(GeneratedArtifactModel)
            .where(
                GeneratedArtifactModel.id == artifact_id,
                GeneratedArtifactModel.status == "running",
            )
            .values(
                status="ready",
                content=content,
                content_sha256=content_sha256,
                error_code=None,
                error_message=None,
                ready_at=updated_at_text,
                stale_at=None,
                updated_at=updated_at_text,
            )
        )
        if new_artifact.rowcount != 1:
            raise PersistenceConflictError(operation="publish_translation_artifact")
        if expected_head_artifact_id is not None:
            old_artifact = await self._session.execute(
                update(GeneratedArtifactModel)
                .where(
                    GeneratedArtifactModel.id == expected_head_artifact_id,
                    GeneratedArtifactModel.status == "ready",
                )
                .values(
                    status="stale",
                    stale_at=updated_at_text,
                    updated_at=updated_at_text,
                )
            )
            if old_artifact.rowcount != 1:
                raise PersistenceConflictError(operation="publish_translation_old_head")
        if not await self.publish_head(
            paper_id=job.paper_id,
            kind="translation",
            artifact_id=artifact_id,
            expected_artifact_id=expected_head_artifact_id,
            updated_at=updated_at,
        ):
            raise PersistenceConflictError(operation="publish_translation_head")
        await self.write_legacy_translation(job.paper_id, content, updated_at)
        result_json = json.dumps(
            {"artifactId": artifact_id, "contentSha256": content_sha256},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        completed = await self._session.execute(
            update(ProcessingJobModel)
            .where(
                ProcessingJobModel.id == job.id,
                ProcessingJobModel.status == "running",
                ProcessingJobModel.lease_owner == lease.worker_id,
                ProcessingJobModel.lease_token == lease.token,
                ProcessingJobModel.lease_expires_at > updated_at_text,
                ProcessingJobModel.cancel_requested_at.is_(None),
            )
            .values(
                status="succeeded",
                result_json=result_json,
                finished_at=updated_at_text,
                lease_owner=None,
                lease_token=None,
                lease_expires_at=None,
                heartbeat_at=None,
                updated_at=updated_at_text,
            )
        )
        if completed.rowcount != 1:
            raise JobLeaseLostError(operation="publish_translation_lease_lost")
        await self._jobs._append_event(job.id, "succeeded", {}, None, updated_at_text)
        await self._session.refresh(artifact)
        published = await self.get(artifact_id)
        if published is None:
            raise PersistenceConflictError(operation="publish_translation_artifact_missing")
        return published

    async def publish_structured(
        self,
        *,
        lease,
        source_document_id: str,
        expected_source_mode: str,
        expected_source_pdf_sha256: str,
        expected_source_content_sha256: str,
        expected_source_processing_version: str,
        expected_chunking_version: str,
        expected_selected_chunk_identities: tuple[
            tuple[str, int, str, str, int, int], ...
        ],
        artifact_id: str,
        kind: str,
        expected_head_artifact_id: str | None,
        content: str,
        content_sha256: str,
        projection: Mapping[str, object],
        updated_at,
    ) -> GeneratedArtifact:
        """Publish one ContextPlan-backed structured artifact as one CAS."""

        if kind not in {"classification", "metadata", "summary", "explainer"}:
            raise PersistenceConflictError(operation="publish_structured_kind")
        updated_at_text = _timestamp(updated_at)
        await self._session.execute(text("BEGIN IMMEDIATE"))
        job = await self._session.get(ProcessingJobModel, lease.job.id)
        if job is None:
            raise JobLeaseLostError(operation="publish_structured_job_missing")
        stored = self._jobs._stored_from_row(job)
        if (
            stored != lease.spec
            or job.job_type != "explain"
            or job.status != "running"
            or job.lease_owner != lease.worker_id
            or job.lease_token != lease.token
            or job.lease_expires_at is None
            or job.lease_expires_at <= updated_at_text
            or job.cancel_requested_at is not None
            or job.paper_id != stored.value.paper_id
            or job.source_document_id != source_document_id
            or job.artifact_id != artifact_id
        ):
            raise JobLeaseLostError(operation="publish_structured_lease_lost")
        source = await self._session.get(SourceDocumentModel, source_document_id)
        if (
            source is None
            or source.status != "ready"
            or source.paper_id != stored.value.paper_id
            or source.mode != expected_source_mode
            or source.mode != stored.value.source_mode
            or source.pdf_sha256 != expected_source_pdf_sha256
            or source.content_sha256 != expected_source_content_sha256
            or source.processing_version != expected_source_processing_version
            or source.markdown is None
            or hashlib.sha256(source.markdown.encode("utf-8")).hexdigest()
            != expected_source_content_sha256
        ):
            raise PersistenceConflictError(operation="publish_structured_source_changed")
        artifact = await self._session.get(GeneratedArtifactModel, artifact_id)
        if (
            artifact is None
            or artifact.status != "running"
            or artifact.paper_id != stored.value.paper_id
            or artifact.kind != kind
            or artifact.source_document_id != source_document_id
            or artifact.generator_provider != stored.value.provider
            or artifact.generator_model != stored.value.model
            or artifact.prompt_version != stored.value.prompt_version
        ):
            raise PersistenceConflictError(operation="publish_structured_artifact_changed")
        chunks = (
            await self._session.execute(
                select(DocumentChunkModel)
                .where(
                    DocumentChunkModel.source_document_id == source_document_id,
                    DocumentChunkModel.status == "ready",
                )
                .order_by(DocumentChunkModel.sequence.asc())
            )
        ).scalars().all()
        actual_chunk_identities = _verified_persisted_chunk_identities(
            chunks,
            source_document_id=source_document_id,
            source_markdown=source.markdown,
            source_content_sha256=expected_source_content_sha256,
            chunking_version=expected_chunking_version,
        )
        if actual_chunk_identities is None:
            raise PersistenceConflictError(operation="publish_structured_chunks_changed")
        identity_by_id = {
            identity[0]: identity for identity in actual_chunk_identities
        }
        if (
            not chunks
            or not expected_selected_chunk_identities
            or len({identity[0] for identity in expected_selected_chunk_identities})
            != len(expected_selected_chunk_identities)
            or any(
                identity_by_id.get(identity[0]) != identity
                for identity in expected_selected_chunk_identities
            )
        ):
            raise PersistenceConflictError(operation="publish_structured_chunks_changed")
        current_head_artifact_id = await self.get_head_artifact_id(
            paper_id=job.paper_id,
            kind=kind,
        )
        if current_head_artifact_id != expected_head_artifact_id:
            raise PersistenceConflictError(operation="publish_structured_head_changed")
        new_artifact = await self._session.execute(
            update(GeneratedArtifactModel)
            .where(
                GeneratedArtifactModel.id == artifact_id,
                GeneratedArtifactModel.status == "running",
            )
            .values(
                status="ready",
                content=content,
                content_sha256=content_sha256,
                error_code=None,
                error_message=None,
                ready_at=updated_at_text,
                stale_at=None,
                updated_at=updated_at_text,
            )
        )
        if new_artifact.rowcount != 1:
            raise PersistenceConflictError(operation="publish_structured_artifact")
        if expected_head_artifact_id is not None:
            old_artifact = await self._session.execute(
                update(GeneratedArtifactModel)
                .where(
                    GeneratedArtifactModel.id == expected_head_artifact_id,
                    GeneratedArtifactModel.status == "ready",
                )
                .values(
                    status="stale",
                    stale_at=updated_at_text,
                    updated_at=updated_at_text,
                )
            )
            if old_artifact.rowcount != 1:
                raise PersistenceConflictError(operation="publish_structured_old_head")
        if not await self.publish_head(
            paper_id=job.paper_id,
            kind=kind,
            artifact_id=artifact_id,
            expected_artifact_id=expected_head_artifact_id,
            updated_at=updated_at,
        ):
            raise PersistenceConflictError(operation="publish_structured_head")
        paper_row = await self._session.get(PaperModel, job.paper_id)
        if paper_row is None:
            raise PersistenceConflictError(operation="publish_structured_paper_missing")
        if kind == "classification":
            if set(projection) != {
                "type",
                "topic",
                "task",
                "models",
                "datasets",
                "tags",
                "relevance",
            }:
                raise PersistenceConflictError(operation="publish_classification_projection")
            projection_values: dict[str, object] = {
                "type": projection["type"],
                "topic": projection["topic"],
                "task": projection["task"],
                "models": json.dumps(
                    projection["models"],
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
                "datasets": json.dumps(
                    projection["datasets"],
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
                "tags": json.dumps(
                    projection["tags"],
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
                "relevance": projection["relevance"],
            }
        elif kind == "metadata":
            if set(projection) != {
                "title",
                "title_zh",
                "authors",
                "venue",
                "year",
                "abstract",
                "arxiv_id",
                "doi",
            }:
                raise PersistenceConflictError(operation="publish_metadata_projection")
            projection_values = {
                "title": projection["title"],
                "title_zh": projection["title_zh"] or paper_row.title_zh,
                "authors": (
                    json.dumps(
                        projection["authors"],
                        ensure_ascii=False,
                        separators=(",", ":"),
                    )
                    if projection["authors"]
                    else paper_row.authors
                ),
                "venue": projection["venue"] or paper_row.venue,
                "year": projection["year"] or paper_row.year,
                "abstract": projection["abstract"] or paper_row.abstract,
                "arxiv_id": paper_row.arxiv_id or projection["arxiv_id"],
                "doi": paper_row.doi or projection["doi"],
            }
        elif kind == "summary":
            if set(projection) != {"tldr", "contribution"}:
                raise PersistenceConflictError(operation="publish_summary_projection")
            projection_values = {
                "tldr": projection["tldr"],
                "contribution": projection["contribution"],
            }
        elif kind == "explainer":
            if set(projection) != {"explainer"} or projection["explainer"] != content:
                raise PersistenceConflictError(operation="publish_explainer_projection")
            projection_values = {"explainer": content}
        else:
            raise PersistenceConflictError(operation="publish_structured_projection")
        projection_values["updated_at"] = updated_at_text
        paper_update = await self._session.execute(
            update(PaperModel)
            .where(PaperModel.id == job.paper_id)
            .values(**projection_values)
        )
        if paper_update.rowcount != 1:
            raise PersistenceConflictError(operation="publish_classification_projection")
        result_json = json.dumps(
            {"artifactId": artifact_id, "contentSha256": content_sha256},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        completed = await self._session.execute(
            update(ProcessingJobModel)
            .where(
                ProcessingJobModel.id == job.id,
                ProcessingJobModel.status == "running",
                ProcessingJobModel.lease_owner == lease.worker_id,
                ProcessingJobModel.lease_token == lease.token,
                ProcessingJobModel.lease_expires_at > updated_at_text,
                ProcessingJobModel.cancel_requested_at.is_(None),
            )
            .values(
                status="succeeded",
                result_json=result_json,
                finished_at=updated_at_text,
                lease_owner=None,
                lease_token=None,
                lease_expires_at=None,
                heartbeat_at=None,
                updated_at=updated_at_text,
            )
        )
        if completed.rowcount != 1:
            raise JobLeaseLostError(operation="publish_structured_lease_lost")
        await self._jobs._append_event(job.id, "succeeded", {}, None, updated_at_text)
        await self._session.refresh(artifact)
        published = await self.get(artifact_id)
        if published is None:
            raise PersistenceConflictError(operation="publish_structured_artifact_missing")
        return published


async def _assert_artifact_enqueue_pdf_identity(
    *,
    paper: PaperModel | None,
    expected_path: Path,
    expected_sha256: str | None,
) -> None:
    if (
        paper is None
        or not paper.pdf_path
        or expected_sha256 is None
    ):
        raise PersistenceConflictError(operation="artifact_enqueue_pdf_identity")
    try:
        persisted_path = Path(paper.pdf_path).resolve(strict=True)
        verified_path = expected_path.resolve(strict=True)
    except OSError as error:
        raise PersistenceConflictError(operation="artifact_enqueue_pdf_missing") from error
    if persisted_path != verified_path:
        raise PersistenceConflictError(operation="artifact_enqueue_pdf_path")
    try:
        actual_sha256 = hashlib.sha256(verified_path.read_bytes()).hexdigest()
    except OSError as error:
        raise PersistenceConflictError(operation="artifact_enqueue_pdf_missing") from error
    if actual_sha256 != expected_sha256:
        raise PersistenceConflictError(operation="artifact_enqueue_pdf_changed")


def _verified_persisted_chunk_identities(
    chunks: list[DocumentChunkModel],
    *,
    source_document_id: str,
    source_markdown: str,
    source_content_sha256: str,
    chunking_version: str,
) -> tuple[tuple[str, int, str, str, int, int], ...] | None:
    if not chunks:
        return None
    identities: list[tuple[str, int, str, str, int, int]] = []
    expected_offset = 0
    joined: list[str] = []
    for sequence, chunk in enumerate(chunks):
        content_sha256 = hashlib.sha256(chunk.content.encode("utf-8")).hexdigest()
        char_start = chunk.char_start
        char_end = chunk.char_end
        if (
            chunk.source_document_id != source_document_id
            or chunk.sequence != sequence
            or chunk.source_content_sha256 != source_content_sha256
            or chunk.chunking_version != chunking_version
            or chunk.content_sha256 != content_sha256
            or char_start != expected_offset
            or char_end != char_start + len(chunk.content)
        ):
            return None
        chunk_key = chunk_key_for(
            source_document_id=source_document_id,
            source_content_sha256=source_content_sha256,
            chunking_version=chunking_version,
            sequence=sequence,
            char_start=char_start,
            char_end=char_end,
            content_sha256=content_sha256,
        )
        if chunk.chunk_key != chunk_key:
            return None
        identities.append(
            (
                chunk.id,
                sequence,
                content_sha256,
                chunk_key,
                char_start,
                char_end,
            )
        )
        joined.append(chunk.content)
        expected_offset = char_end
    if "".join(joined) != source_markdown:
        return None
    return tuple(identities)
