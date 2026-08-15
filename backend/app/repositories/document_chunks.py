from __future__ import annotations

from datetime import datetime, timezone
import json

from sqlalchemy import select, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.domain import DocumentChunk
from backend.app.domain.context import ChunkSet
from backend.app.repositories.models import DocumentChunkModel


def _timestamp(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _heading_path(value: str | None) -> str | None:
    if value is None:
        return None
    decoded = json.loads(value)
    if not isinstance(decoded, list) or any(not isinstance(item, str) for item in decoded):
        raise ValueError("heading_path must be a canonical JSON string array")
    return json.dumps(decoded, ensure_ascii=False, separators=(",", ":"))


class SqlAlchemyDocumentChunkRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_for_source(
        self,
        source_document_id: str,
        *,
        status: str | None = None,
        chunking_version: str | None = None,
    ) -> tuple[DocumentChunk, ...]:
        statement = select(DocumentChunkModel).where(
            DocumentChunkModel.source_document_id == source_document_id
        )
        if status is not None:
            statement = statement.where(DocumentChunkModel.status == status)
        if chunking_version is not None:
            statement = statement.where(DocumentChunkModel.chunking_version == chunking_version)
        rows = (
            await self._session.execute(statement.order_by(DocumentChunkModel.sequence))
        ).scalars().all()
        return tuple(_chunk(row) for row in rows)

    async def insert_set(self, chunk_set: ChunkSet) -> tuple[DocumentChunk, ...]:
        for chunk in chunk_set.chunks:
            statement = sqlite_insert(DocumentChunkModel).values(
                id=chunk.id,
                source_document_id=chunk.source_document_id,
                sequence=chunk.sequence,
                heading_path=_heading_path(chunk.heading_path),
                page_start=chunk.page_start,
                page_end=chunk.page_end,
                content=chunk.content,
                content_sha256=chunk.content_sha256,
                token_count=chunk.token_count,
                status=chunk.status,
                content_kind=chunk.content_kind,
                chunk_key=chunk.chunk_key,
                chunking_version=chunk.chunking_version,
                source_content_sha256=chunk.source_content_sha256,
                char_start=chunk.char_start,
                char_end=chunk.char_end,
                created_at=_timestamp(chunk.created_at),
                updated_at=_timestamp(chunk.updated_at),
                stale_at=_timestamp(chunk.stale_at),
            ).on_conflict_do_nothing(
                index_elements=[DocumentChunkModel.chunk_key],
                index_where=DocumentChunkModel.chunk_key.is_not(None),
            )
            await self._session.execute(statement)
        persisted = await self.list_for_source(
            chunk_set.source_document_id,
            status="ready",
            chunking_version=chunk_set.spec.chunking_version,
        )
        if not _same_chunk_set_identity(persisted, chunk_set.chunks):
            raise ValueError("CHUNK_COVERAGE_INVALID: persisted chunk winner differs")
        return persisted

    async def stale_other_versions(
        self,
        source_document_id: str,
        active_chunking_version: str,
        *,
        now: datetime,
    ) -> int:
        result = await self._session.execute(
            update(DocumentChunkModel)
            .where(
                DocumentChunkModel.source_document_id == source_document_id,
                DocumentChunkModel.status == "ready",
                DocumentChunkModel.chunking_version != active_chunking_version,
            )
            .values(status="stale", stale_at=_timestamp(now), updated_at=_timestamp(now))
        )
        return int(result.rowcount or 0)


def _parse_timestamp(value: str | None) -> datetime | None:
    if value is None:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def _same_chunk_set_identity(
    persisted: tuple[DocumentChunk, ...],
    generated: tuple[DocumentChunk, ...],
) -> bool:
    """Timestamps are materialization observations, not chunk identity."""

    return len(persisted) == len(generated) and all(
        (
            saved.id,
            saved.source_document_id,
            saved.sequence,
            saved.heading_path,
            saved.page_start,
            saved.page_end,
            saved.content,
            saved.content_sha256,
            saved.token_count,
            saved.status,
            saved.content_kind,
            saved.chunk_key,
            saved.chunking_version,
            saved.source_content_sha256,
            saved.char_start,
            saved.char_end,
            saved.stale_at,
        )
        == (
            expected.id,
            expected.source_document_id,
            expected.sequence,
            expected.heading_path,
            expected.page_start,
            expected.page_end,
            expected.content,
            expected.content_sha256,
            expected.token_count,
            expected.status,
            expected.content_kind,
            expected.chunk_key,
            expected.chunking_version,
            expected.source_content_sha256,
            expected.char_start,
            expected.char_end,
            expected.stale_at,
        )
        for saved, expected in zip(persisted, generated, strict=True)
    )


def _chunk(row: DocumentChunkModel) -> DocumentChunk:
    return DocumentChunk(
        id=row.id,
        source_document_id=row.source_document_id,
        sequence=row.sequence,
        heading_path=row.heading_path,
        page_start=row.page_start,
        page_end=row.page_end,
        content=row.content,
        content_sha256=row.content_sha256,
        token_count=row.token_count,
        status=row.status,
        content_kind=row.content_kind,
        chunk_key=row.chunk_key,
        chunking_version=row.chunking_version,
        source_content_sha256=row.source_content_sha256,
        char_start=row.char_start,
        char_end=row.char_end,
        created_at=_parse_timestamp(row.created_at),
        updated_at=_parse_timestamp(row.updated_at),
        stale_at=_parse_timestamp(row.stale_at),
    )


__all__ = ["SqlAlchemyDocumentChunkRepository"]
