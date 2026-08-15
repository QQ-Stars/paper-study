from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
from typing import Any

from sqlalchemy import select, text

from backend.app.domain import (
    JobLeaseLostError,
    TranslationCheckpointConflictError,
)
from backend.app.repositories.models import (
    ArtifactTranslationCheckpointModel,
    DocumentChunkModel,
    GeneratedArtifactModel,
    ProcessingJobModel,
)


@dataclass(frozen=True, slots=True)
class TranslationCheckpoint:
    artifact_id: str
    chunk_id: str
    sequence: int
    source_content_sha256: str
    provider: str
    model: str
    prompt_version: str
    status: str
    translated_markdown: str | None
    content_sha256: str | None
    attempt: int
    error_code: str | None
    error_message: str | None


class SqlAlchemyTranslationCheckpointRepository:
    """Persist each translated chunk in its own lease-fenced transaction."""

    def __init__(self, session_factory: Any, *, clock: Callable[[], datetime]) -> None:
        self._session_factory = session_factory
        self._clock = clock

    async def read(self, artifact_id: str, sequence: int) -> TranslationCheckpoint | None:
        _nonblank(artifact_id, "artifact_id")
        _sequence(sequence)
        async with self._session_factory() as session:
            row = await session.get(
                ArtifactTranslationCheckpointModel,
                {"artifact_id": artifact_id, "sequence": sequence},
            )
            return _checkpoint(row)

    async def get(self, artifact_id: str, chunk_id: str) -> TranslationCheckpoint | None:
        _nonblank(artifact_id, "artifact_id")
        _nonblank(chunk_id, "chunk_id")
        async with self._session_factory() as session:
            row = (
                await session.execute(
                    select(ArtifactTranslationCheckpointModel).where(
                        ArtifactTranslationCheckpointModel.artifact_id == artifact_id,
                        ArtifactTranslationCheckpointModel.chunk_id == chunk_id,
                    )
                )
            ).scalar_one_or_none()
            return _checkpoint(row)

    async def list_succeeded(self, artifact_id: str) -> tuple[TranslationCheckpoint, ...]:
        _nonblank(artifact_id, "artifact_id")
        async with self._session_factory() as session:
            rows = (
                await session.execute(
                    select(ArtifactTranslationCheckpointModel)
                    .where(
                        ArtifactTranslationCheckpointModel.artifact_id == artifact_id,
                        ArtifactTranslationCheckpointModel.status == "succeeded",
                    )
                    .order_by(ArtifactTranslationCheckpointModel.sequence.asc())
                )
            ).scalars().all()
            return tuple(_checkpoint(row) for row in rows if row is not None)

    async def save_success(
        self,
        *,
        lease: object,
        artifact_id: str,
        chunk_id: str,
        sequence: int,
        source_content_sha256: str,
        provider: str,
        model: str,
        prompt_version: str,
        translated_markdown: str,
    ) -> bool:
        for name, value in (
            ("artifact_id", artifact_id),
            ("chunk_id", chunk_id),
            ("source_content_sha256", source_content_sha256),
            ("provider", provider),
            ("model", model),
            ("prompt_version", prompt_version),
        ):
            _nonblank(value, name)
        _sequence(sequence)
        if not isinstance(translated_markdown, str) or not translated_markdown.strip():
            raise ValueError("translated_markdown must be nonblank")
        now_text = _timestamp(self._clock())
        content_sha256 = hashlib.sha256(translated_markdown.encode("utf-8")).hexdigest()
        async with self._session_factory() as session:
            await session.execute(text("BEGIN IMMEDIATE"))
            job = (
                await session.execute(
                    select(ProcessingJobModel).where(
                        ProcessingJobModel.id == lease.job.id,
                        ProcessingJobModel.job_type == "translate",
                        ProcessingJobModel.artifact_id == artifact_id,
                        ProcessingJobModel.status == "running",
                        ProcessingJobModel.lease_owner == lease.worker_id,
                        ProcessingJobModel.lease_token == lease.token,
                        ProcessingJobModel.lease_expires_at > now_text,
                        ProcessingJobModel.cancel_requested_at.is_(None),
                    )
                )
            ).scalar_one_or_none()
            if job is None:
                raise JobLeaseLostError(operation="translation_checkpoint_lease_lost")
            artifact = await session.get(GeneratedArtifactModel, artifact_id)
            chunk = await session.get(DocumentChunkModel, chunk_id)
            if (
                artifact is None
                or artifact.status != "running"
                or artifact.kind != "translation"
                or artifact.source_document_id != job.source_document_id
                or artifact.generator_provider != provider
                or artifact.generator_model != model
                or artifact.prompt_version != prompt_version
                or chunk is None
                or chunk.source_document_id != job.source_document_id
                or chunk.sequence != sequence
                or chunk.status != "ready"
                or chunk.source_content_sha256 != source_content_sha256
            ):
                raise TranslationCheckpointConflictError(
                    operation="translation_checkpoint_identity"
                )
            existing = await session.get(
                ArtifactTranslationCheckpointModel,
                {"artifact_id": artifact_id, "sequence": sequence},
            )
            if existing is not None and not _same_checkpoint_identity(
                existing,
                chunk_id=chunk_id,
                source_content_sha256=source_content_sha256,
                provider=provider,
                model=model,
                prompt_version=prompt_version,
            ):
                raise TranslationCheckpointConflictError(
                    operation="translation_checkpoint_identity"
                )
            if existing is not None and existing.status == "succeeded":
                same = (
                    existing.chunk_id == chunk_id
                    and existing.source_content_sha256 == source_content_sha256
                    and existing.provider == provider
                    and existing.model == model
                    and existing.prompt_version == prompt_version
                    and existing.translated_markdown == translated_markdown
                    and existing.content_sha256 == content_sha256
                )
                if not same:
                    raise TranslationCheckpointConflictError(
                        operation="translation_checkpoint_content_conflict"
                    )
                return False

            attempt = int(getattr(lease.job, "attempt", 0))
            if existing is None:
                session.add(
                    ArtifactTranslationCheckpointModel(
                        artifact_id=artifact_id,
                        chunk_id=chunk_id,
                        sequence=sequence,
                        source_content_sha256=source_content_sha256,
                        provider=provider,
                        model=model,
                        prompt_version=prompt_version,
                        status="succeeded",
                        translated_markdown=translated_markdown,
                        content_sha256=content_sha256,
                        attempt=attempt,
                        error_code=None,
                        error_message=None,
                        created_at=now_text,
                        updated_at=now_text,
                    )
                )
            else:
                existing.chunk_id = chunk_id
                existing.source_content_sha256 = source_content_sha256
                existing.provider = provider
                existing.model = model
                existing.prompt_version = prompt_version
                existing.status = "succeeded"
                existing.translated_markdown = translated_markdown
                existing.content_sha256 = content_sha256
                existing.attempt = attempt
                existing.error_code = None
                existing.error_message = None
                existing.updated_at = now_text
            await session.commit()
            return True

    async def save_failure(
        self,
        *,
        lease: object,
        artifact_id: str,
        chunk_id: str,
        sequence: int,
        source_content_sha256: str,
        provider: str,
        model: str,
        prompt_version: str,
        error_code: str,
    ) -> bool:
        for name, value in (
            ("artifact_id", artifact_id),
            ("chunk_id", chunk_id),
            ("source_content_sha256", source_content_sha256),
            ("provider", provider),
            ("model", model),
            ("prompt_version", prompt_version),
            ("error_code", error_code),
        ):
            _nonblank(value, name)
        _sequence(sequence)
        now_text = _timestamp(self._clock())
        async with self._session_factory() as session:
            await session.execute(text("BEGIN IMMEDIATE"))
            job = (
                await session.execute(
                    select(ProcessingJobModel).where(
                        ProcessingJobModel.id == lease.job.id,
                        ProcessingJobModel.job_type == "translate",
                        ProcessingJobModel.artifact_id == artifact_id,
                        ProcessingJobModel.status == "running",
                        ProcessingJobModel.lease_owner == lease.worker_id,
                        ProcessingJobModel.lease_token == lease.token,
                        ProcessingJobModel.lease_expires_at > now_text,
                        ProcessingJobModel.cancel_requested_at.is_(None),
                    )
                )
            ).scalar_one_or_none()
            if job is None:
                raise JobLeaseLostError(operation="translation_checkpoint_lease_lost")
            artifact = await session.get(GeneratedArtifactModel, artifact_id)
            chunk = await session.get(DocumentChunkModel, chunk_id)
            if (
                artifact is None
                or artifact.status != "running"
                or artifact.kind != "translation"
                or artifact.source_document_id != job.source_document_id
                or artifact.generator_provider != provider
                or artifact.generator_model != model
                or artifact.prompt_version != prompt_version
                or chunk is None
                or chunk.source_document_id != job.source_document_id
                or chunk.sequence != sequence
                or chunk.status != "ready"
                or chunk.source_content_sha256 != source_content_sha256
            ):
                raise TranslationCheckpointConflictError(
                    operation="translation_checkpoint_identity"
                )
            existing = await session.get(
                ArtifactTranslationCheckpointModel,
                {"artifact_id": artifact_id, "sequence": sequence},
            )
            if existing is not None and not _same_checkpoint_identity(
                existing,
                chunk_id=chunk_id,
                source_content_sha256=source_content_sha256,
                provider=provider,
                model=model,
                prompt_version=prompt_version,
            ):
                raise TranslationCheckpointConflictError(
                    operation="translation_checkpoint_identity"
                )
            attempt = int(getattr(lease.job, "attempt", 0))
            if existing is None:
                session.add(
                    ArtifactTranslationCheckpointModel(
                        artifact_id=artifact_id,
                        chunk_id=chunk_id,
                        sequence=sequence,
                        source_content_sha256=source_content_sha256,
                        provider=provider,
                        model=model,
                        prompt_version=prompt_version,
                        status="failed",
                        translated_markdown=None,
                        content_sha256=None,
                        attempt=attempt,
                        error_code=error_code,
                        error_message=None,
                        created_at=now_text,
                        updated_at=now_text,
                    )
                )
            elif existing.status != "succeeded":
                existing.status = "failed"
                existing.translated_markdown = None
                existing.content_sha256 = None
                existing.attempt = attempt
                existing.error_code = error_code
                existing.error_message = None
                existing.updated_at = now_text
            else:
                return False
            await session.commit()
            return True


def _checkpoint(row: ArtifactTranslationCheckpointModel | None) -> TranslationCheckpoint | None:
    if row is None:
        return None
    return TranslationCheckpoint(
        artifact_id=row.artifact_id,
        chunk_id=row.chunk_id,
        sequence=row.sequence,
        source_content_sha256=row.source_content_sha256,
        provider=row.provider,
        model=row.model,
        prompt_version=row.prompt_version,
        status=row.status,
        translated_markdown=row.translated_markdown,
        content_sha256=row.content_sha256,
        attempt=row.attempt,
        error_code=row.error_code,
        error_message=row.error_message,
    )


def _same_checkpoint_identity(
    row: ArtifactTranslationCheckpointModel,
    *,
    chunk_id: str,
    source_content_sha256: str,
    provider: str,
    model: str,
    prompt_version: str,
) -> bool:
    return (
        row.chunk_id == chunk_id
        and row.source_content_sha256 == source_content_sha256
        and row.provider == provider
        and row.model == model
        and row.prompt_version == prompt_version
    )


def _nonblank(value: str, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be nonblank")
    return value


def _sequence(value: int) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError("sequence must be nonnegative")
    return value


def _timestamp(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("clock must return a timezone-aware datetime")
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds").replace(
        "+00:00", "Z"
    )


__all__ = ["SqlAlchemyTranslationCheckpointRepository", "TranslationCheckpoint"]
