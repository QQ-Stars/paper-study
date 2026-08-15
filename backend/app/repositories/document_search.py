from __future__ import annotations

import json
import hashlib
import math
from pathlib import Path
import re
from typing import Any

import numpy as np
from sqlalchemy import select, text, update

from backend.app.domain import (
    JobLeaseLostError,
    MissingPdfError,
    PersistenceConflictError,
    SourceModeMismatchError,
    SourceNotFoundError,
    SourceNotReadyError,
    StaleSourceError,
)
from backend.app.domain.processing import EnqueueResult, JobProgress, NewProcessingJob
from backend.app.domain.context import (
    EmbeddingProfile,
    SearchCoverage,
    SearchHit,
    SearchRequest,
)
from backend.app.repositories.models import (
    DocumentChunkEmbeddingModel,
    DocumentChunkModel,
    PaperModel,
    ProcessingJobModel,
    SourceDocumentModel,
)
from backend.app.repositories.processing_jobs import SqlAlchemyProcessingJobRepository


_CONTROL_CHARACTERS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


class SqlAlchemyDocumentSearchRepository:
    """Read-only P3 search repository over external-content FTS rows."""

    def __init__(self, session_factory: Any) -> None:
        self._session_factory = session_factory

    async def index_status(
        self,
        source_document_id: str,
        *,
        paper_id: str | None = None,
        profile: EmbeddingProfile | None = None,
    ) -> dict[str, object]:
        """Read persisted chunk/embedding coverage for one source."""

        if not isinstance(source_document_id, str) or not source_document_id.strip():
            raise ValueError("source_document_id must be nonblank")
        if paper_id is not None and (
            not isinstance(paper_id, str) or not paper_id.strip()
        ):
            raise ValueError("paper_id must be nonblank")
        source_filter = "c.source_document_id=:source_document_id"
        parameters: dict[str, object] = {
            "source_document_id": source_document_id,
        }
        if paper_id is not None:
            source_filter += " AND s.paper_id=:paper_id"
            parameters["paper_id"] = paper_id
        async with self._session_factory() as session:
            counts = (
                await session.execute(
                    text(
                        "SELECT "
                        "count(*) AS total_chunks, "
                        "sum(CASE WHEN c.status='ready' AND s.status='ready' "
                        "THEN 1 ELSE 0 END) AS ready_chunks, "
                        "sum(CASE WHEN c.status='stale' OR s.status='stale' "
                        "THEN 1 ELSE 0 END) AS stale_chunks "
                        "FROM document_chunks c "
                        "JOIN document_sources s ON s.id=c.source_document_id "
                        "WHERE " + source_filter
                    ),
                    parameters,
                )
            ).mappings().one()
            embedded = failed = embedding_stale = 0
            if profile is not None:
                embedding_counts = (
                    await session.execute(
                        text(
                            "SELECT "
                            "sum(CASE WHEN e.status='ready' AND c.status='ready' "
                            "AND s.status='ready' THEN 1 ELSE 0 END) AS embedded_chunks, "
                            "sum(CASE WHEN e.status='failed' AND c.status='ready' "
                            "AND s.status='ready' THEN 1 ELSE 0 END) AS failed_embeddings, "
                            "sum(CASE WHEN e.status='stale' THEN 1 ELSE 0 END) AS stale_chunks "
                            "FROM document_chunk_embeddings e "
                            "JOIN document_chunks c ON c.id=e.chunk_id "
                            "AND c.source_document_id=e.source_document_id "
                            "JOIN document_sources s ON s.id=e.source_document_id "
                            "WHERE e.source_document_id=:source_document_id "
                            "AND e.provider=:provider AND e.model=:model "
                            "AND e.embedding_version=:version AND e.dimensions=:dimensions"
                            + (" AND s.paper_id=:paper_id" if paper_id is not None else "")
                        ),
                        {
                            **parameters,
                            "provider": profile.provider,
                            "model": profile.model,
                            "version": profile.embedding_version,
                            "dimensions": profile.dimensions,
                        },
                    )
                ).mappings().one()
                embedded = int(embedding_counts["embedded_chunks"] or 0)
                failed = int(embedding_counts["failed_embeddings"] or 0)
                embedding_stale = int(embedding_counts["stale_chunks"] or 0)
        total = int(counts["total_chunks"] or 0)
        ready = int(counts["ready_chunks"] or 0)
        stale = int(counts["stale_chunks"] or 0) + embedding_stale
        coverage = "empty" if ready == 0 else ("complete" if embedded == ready else "partial")
        return {
            "total_chunks": total,
            "ready_chunks": ready,
            "embedded_chunks": embedded,
            "stale_chunks": stale,
            "failed_embeddings": failed,
            "provider": profile.provider if profile is not None else None,
            "model": profile.model if profile is not None else None,
            "version": profile.embedding_version if profile is not None else None,
            "coverage": coverage,
        }

    async def lexical(self, request: SearchRequest) -> tuple[SearchHit, ...]:
        if not isinstance(request, SearchRequest):
            raise ValueError("request must be SearchRequest")
        paper_filter = ""
        parameters: dict[str, object] = {
            "match_query": _literal_fts_query(request.query),
            "limit": request.limit,
        }
        if request.paper_ids:
            names = []
            for index, paper_id in enumerate(request.paper_ids):
                name = f"paper_{index}"
                names.append(f":{name}")
                parameters[name] = paper_id
            paper_filter = " AND s.paper_id IN (" + ",".join(names) + ")"
        statement = text(
            "WITH matches AS ("
            "SELECT rowid,bm25(document_chunks_fts) AS rank "
            "FROM document_chunks_fts WHERE document_chunks_fts MATCH :match_query"
            ") "
            "SELECT s.paper_id,c.source_document_id,c.id AS chunk_id,c.sequence,"
            "c.heading_path,c.page_start,c.page_end,c.content,m.rank "
            "FROM matches m "
            "JOIN document_chunks c ON c.rowid=m.rowid "
            "JOIN document_sources s ON s.id=c.source_document_id "
            "WHERE c.status='ready' AND s.status='ready'"
            + paper_filter
            + " ORDER BY m.rank ASC,s.paper_id ASC,c.source_document_id ASC,"
            "c.sequence ASC,c.id ASC LIMIT :limit"
        )
        async with self._session_factory() as session:
            rows = (await session.execute(statement, parameters)).mappings().all()
        return tuple(
            SearchHit(
                paper_id=str(row["paper_id"]),
                source_document_id=str(row["source_document_id"]),
                chunk_id=str(row["chunk_id"]),
                sequence=int(row["sequence"]),
                heading_path=_decode_heading_path(row["heading_path"]),
                page_start=row["page_start"],
                page_end=row["page_end"],
                excerpt=_safe_excerpt(str(row["content"]), request.query),
                score=-float(row["rank"]),
                lexical_score=-float(row["rank"]),
            )
            for row in rows
        )

    async def enqueue_index_job(
        self,
        *,
        job: NewProcessingJob,
        spec_json: str,
        spec_sha256: str,
        expected_source_content_sha256: str,
        expected_pdf_sha256: str,
        pdf_path: Path,
        expected_source_provider: str,
        expected_source_model: str,
    ) -> EnqueueResult:
        async with self._session_factory() as session:
            await session.execute(text("BEGIN IMMEDIATE"))
            await _assert_index_enqueue_identity(
                session,
                job=job,
                expected_source_content_sha256=expected_source_content_sha256,
                expected_pdf_sha256=expected_pdf_sha256,
                pdf_path=pdf_path,
                expected_source_provider=expected_source_provider,
                expected_source_model=expected_source_model,
            )
            result = await SqlAlchemyProcessingJobRepository(session).insert_with_spec(
                job,
                spec_json=spec_json,
                spec_sha256=spec_sha256,
            )
            await _assert_index_enqueue_identity(
                session,
                job=job,
                expected_source_content_sha256=expected_source_content_sha256,
                expected_pdf_sha256=expected_pdf_sha256,
                pdf_path=pdf_path,
                expected_source_provider=expected_source_provider,
                expected_source_model=expected_source_model,
            )
            await session.commit()
            return result

    async def index_source_identity(
        self,
        *,
        paper_id: str,
        source_mode: str,
        source_document_id: str,
    ) -> dict[str, str]:
        async with self._session_factory() as session:
            source = await session.get(SourceDocumentModel, source_document_id)
            paper = await session.get(PaperModel, paper_id)
            if source is None or paper is None or source.paper_id != paper_id:
                raise SourceNotFoundError(paper_id=paper_id)
            if source.mode != source_mode:
                raise SourceModeMismatchError(
                    paper_id=paper_id,
                    source_mode=source_mode,
                )
            if source.status == "stale":
                raise StaleSourceError(paper_id=paper_id)
            if (
                source.status != "ready"
                or source.markdown is None
                or source.content_sha256 is None
            ):
                raise SourceNotReadyError(paper_id=paper_id)
            if not paper.pdf_path:
                raise MissingPdfError(paper_id=paper_id)
            return {
                "source_content_sha256": source.content_sha256,
                "pdf_sha256": source.pdf_sha256,
                "pdf_path": paper.pdf_path,
                "source_provider": source.provider,
                "source_model": source.model,
            }

    async def coverage_for_request(
        self,
        request: SearchRequest,
        *,
        profile: EmbeddingProfile | None = None,
    ) -> SearchCoverage:
        paper_filter = ""
        parameters: dict[str, object] = {}
        if request.paper_ids:
            names = []
            for index, paper_id in enumerate(request.paper_ids):
                name = f"paper_{index}"
                names.append(f":{name}")
                parameters[name] = paper_id
            paper_filter = " WHERE s.paper_id IN (" + ",".join(names) + ")"
        statement = text(
            "SELECT "
            "sum(CASE WHEN c.status='ready' AND s.status='ready' THEN 1 ELSE 0 END) "
            "AS ready_chunks,"
            "sum(CASE WHEN c.status='stale' OR s.status='stale' THEN 1 ELSE 0 END) "
            "AS stale_chunks "
            "FROM document_chunks c JOIN document_sources s ON s.id=c.source_document_id"
            + paper_filter
        )
        async with self._session_factory() as session:
            row = (await session.execute(statement, parameters)).mappings().one()
            embedded_chunks = 0
            failed_embeddings = 0
            embedding_stale_chunks = 0
            if profile is not None:
                embedding_filter = ""
                embedding_parameters: dict[str, object] = {
                    "provider": profile.provider,
                    "model": profile.model,
                    "version": profile.embedding_version,
                    "dimensions": profile.dimensions,
                }
                if request.paper_ids:
                    names = []
                    for index, paper_id in enumerate(request.paper_ids):
                        name = f"embedding_paper_{index}"
                        names.append(f":{name}")
                        embedding_parameters[name] = paper_id
                    embedding_filter = " AND s.paper_id IN (" + ",".join(names) + ")"
                counts = (
                    await session.execute(
                        text(
                            "SELECT "
                            "sum(CASE WHEN e.status='ready' AND c.status='ready' "
                            "AND s.status='ready' THEN 1 ELSE 0 END) AS ready_count,"
                            "sum(CASE WHEN e.status='failed' AND c.status='ready' "
                            "AND s.status='ready' THEN 1 ELSE 0 END) AS failed_count,"
                            "sum(CASE WHEN e.status='stale' THEN 1 ELSE 0 END) AS stale_count "
                            "FROM document_chunk_embeddings e "
                            "JOIN document_chunks c ON c.id=e.chunk_id "
                            "AND c.source_document_id=e.source_document_id "
                            "JOIN document_sources s ON s.id=e.source_document_id "
                            "WHERE e.provider=:provider AND e.model=:model "
                            "AND e.embedding_version=:version "
                            "AND e.dimensions=:dimensions"
                            + embedding_filter
                        ),
                        embedding_parameters,
                    )
                ).mappings().one()
                embedded_chunks = int(counts["ready_count"] or 0)
                failed_embeddings = int(counts["failed_count"] or 0)
                embedding_stale_chunks = int(counts["stale_count"] or 0)
        return SearchCoverage(
            ready_chunks=int(row["ready_chunks"] or 0),
            embedded_chunks=embedded_chunks,
            stale_chunks=int(row["stale_chunks"] or 0) + embedding_stale_chunks,
            failed_embeddings=failed_embeddings,
        )

    async def semantic(
        self,
        request: SearchRequest,
        *,
        profile: EmbeddingProfile,
        query_vector: tuple[float, ...],
    ) -> tuple[SearchHit, ...]:
        query = np.asarray(query_vector, dtype="<f4")
        if query.shape != (profile.dimensions,) or not np.isfinite(query).all():
            raise ValueError("semantic query vector is invalid")
        query_norm = float(np.linalg.norm(query))
        if not math.isfinite(query_norm) or query_norm <= 0:
            raise ValueError("semantic query vector is invalid")
        query = query / query_norm
        paper_filter = ""
        parameters: dict[str, object] = {
            "provider": profile.provider,
            "model": profile.model,
            "version": profile.embedding_version,
            "dimensions": profile.dimensions,
        }
        if request.paper_ids:
            names = []
            for index, paper_id in enumerate(request.paper_ids):
                name = f"semantic_paper_{index}"
                names.append(f":{name}")
                parameters[name] = paper_id
            paper_filter = " AND s.paper_id IN (" + ",".join(names) + ")"
        statement = text(
            "SELECT s.paper_id,c.source_document_id,c.id AS chunk_id,c.sequence,"
            "c.heading_path,c.page_start,c.page_end,c.content,c.content_sha256,"
            "e.vector,e.vector_sha256,e.chunk_content_sha256 "
            "FROM document_chunk_embeddings e "
            "JOIN document_chunks c ON c.id=e.chunk_id "
            "AND c.source_document_id=e.source_document_id "
            "JOIN document_sources s ON s.id=e.source_document_id "
            "WHERE e.provider=:provider AND e.model=:model "
            "AND e.embedding_version=:version AND e.dimensions=:dimensions "
            "AND e.status='ready' AND c.status='ready' AND s.status='ready'"
            + paper_filter
        )
        async with self._session_factory() as session:
            rows = (await session.execute(statement, parameters)).mappings().all()
        scored: list[tuple[float, Any]] = []
        for row in rows:
            vector = bytes(row["vector"] or b"")
            if (
                len(vector) != profile.dimensions * 4
                or row["vector_sha256"] != hashlib.sha256(vector).hexdigest()
                or row["chunk_content_sha256"] != row["content_sha256"]
            ):
                raise PersistenceConflictError(operation="semantic_embedding_identity")
            values = np.frombuffer(vector, dtype="<f4")
            if values.shape != (profile.dimensions,) or not np.isfinite(values).all():
                raise PersistenceConflictError(operation="semantic_embedding_vector")
            norm = float(np.linalg.norm(values))
            if not math.isfinite(norm) or norm <= 0:
                raise PersistenceConflictError(operation="semantic_embedding_vector")
            score = float(np.dot(values / norm, query))
            if not math.isfinite(score):
                raise PersistenceConflictError(operation="semantic_embedding_vector")
            scored.append((score, row))
        scored.sort(
            key=lambda item: (
                -item[0],
                str(item[1]["paper_id"]),
                str(item[1]["source_document_id"]),
                int(item[1]["sequence"]),
                str(item[1]["chunk_id"]),
            )
        )
        return tuple(
            SearchHit(
                paper_id=str(row["paper_id"]),
                source_document_id=str(row["source_document_id"]),
                chunk_id=str(row["chunk_id"]),
                sequence=int(row["sequence"]),
                heading_path=_decode_heading_path(row["heading_path"]),
                page_start=row["page_start"],
                page_end=row["page_end"],
                excerpt=_safe_excerpt(str(row["content"]), request.query),
                score=score,
                lexical_score=None,
                semantic_score=score,
            )
            for score, row in scored[: request.limit]
        )

    async def exact_ready_chunk_ids(
        self,
        *,
        source_document_id: str,
        profile: EmbeddingProfile,
        chunk_content_sha256: dict[str, str],
    ) -> frozenset[str]:
        statement = select(DocumentChunkEmbeddingModel).where(
            DocumentChunkEmbeddingModel.source_document_id == source_document_id,
            DocumentChunkEmbeddingModel.provider == profile.provider,
            DocumentChunkEmbeddingModel.model == profile.model,
            DocumentChunkEmbeddingModel.embedding_version == profile.embedding_version,
            DocumentChunkEmbeddingModel.status == "ready",
        )
        async with self._session_factory() as session:
            rows = (await session.execute(statement)).scalars().all()
        ready: set[str] = set()
        for row in rows:
            expected_chunk_sha = chunk_content_sha256.get(row.chunk_id)
            vector = bytes(row.vector) if row.vector is not None else None
            if (
                expected_chunk_sha is None
                or row.chunk_content_sha256 != expected_chunk_sha
                or row.dimensions != profile.dimensions
                or vector is None
                or len(vector) != profile.dimensions * 4
                or row.vector_sha256 != hashlib.sha256(vector).hexdigest()
            ):
                raise PersistenceConflictError(operation="embedding_ready_identity")
            ready.add(row.chunk_id)
        return frozenset(ready)

    async def validate_index_context(
        self,
        *,
        lease: object,
        source_document_id: str,
        source_content_sha256: str,
        chunking_version: str,
        chunk_content_sha256: dict[str, str],
        now_text: str,
    ) -> None:
        async with self._session_factory() as session:
            job = await session.get(
                ProcessingJobModel,
                getattr(getattr(lease, "job", None), "id", None),
            )
            if not _active_index_job(
                job,
                lease,
                source_document_id=source_document_id,
                now_text=now_text,
            ):
                raise JobLeaseLostError(operation="embedding_index_lease_lost")
            source = await session.get(SourceDocumentModel, source_document_id)
            if (
                source is None
                or source.status != "ready"
                or source.content_sha256 != source_content_sha256
            ):
                raise PersistenceConflictError(operation="embedding_source_identity")
            rows = (
                await session.execute(
                    select(DocumentChunkModel)
                    .where(
                        DocumentChunkModel.source_document_id == source_document_id,
                        DocumentChunkModel.status == "ready",
                    )
                    .order_by(DocumentChunkModel.sequence, DocumentChunkModel.id)
                )
            ).scalars().all()
            if (
                tuple(row.id for row in rows) != tuple(chunk_content_sha256)
                or any(
                    row.source_content_sha256 != source_content_sha256
                    or row.chunking_version != chunking_version
                    or row.content_sha256 != chunk_content_sha256[row.id]
                    for row in rows
                )
            ):
                raise PersistenceConflictError(operation="embedding_chunk_identity")
            indexed = (
                await session.execute(
                    text(
                        "SELECT count(*) FROM document_chunks c "
                        "JOIN document_chunks_fts f ON f.rowid=c.rowid "
                        "WHERE c.source_document_id=:source_document_id "
                        "AND c.status='ready'"
                    ),
                    {"source_document_id": source_document_id},
                )
            ).scalar_one()
            if int(indexed) != len(rows):
                raise PersistenceConflictError(operation="embedding_fts_coverage")

    async def assert_active_index_lease(
        self,
        lease: object,
        *,
        source_document_id: str,
        now_text: str,
    ) -> None:
        job = getattr(lease, "job", None)
        async with self._session_factory() as session:
            row = await session.get(ProcessingJobModel, getattr(job, "id", None))
            active = _active_index_job(
                row,
                lease,
                source_document_id=source_document_id,
                now_text=now_text,
            )
        if not active:
            raise JobLeaseLostError(operation="embedding_index_lease_lost")

    async def save_ready_batch(
        self,
        *,
        lease: object,
        source_document_id: str,
        source_content_sha256: str,
        chunking_version: str,
        profile: EmbeddingProfile,
        chunks: tuple[object, ...],
        vectors: tuple[bytes, ...],
        now_text: str,
    ) -> None:
        if len(chunks) != len(vectors) or not chunks:
            raise ValueError("embedding persistence batch must be nonempty and aligned")
        async with self._session_factory() as session:
            await session.execute(text("BEGIN IMMEDIATE"))
            job = await session.get(
                ProcessingJobModel,
                getattr(getattr(lease, "job", None), "id", None),
            )
            if not _active_index_job(
                job,
                lease,
                source_document_id=source_document_id,
                now_text=now_text,
            ):
                raise JobLeaseLostError(operation="embedding_index_lease_lost")
            source = await session.get(SourceDocumentModel, source_document_id)
            if (
                source is None
                or source.status != "ready"
                or source.content_sha256 != source_content_sha256
            ):
                raise PersistenceConflictError(operation="embedding_source_identity")
            for chunk, vector in zip(chunks, vectors, strict=True):
                chunk_id = str(getattr(chunk, "id", ""))
                persisted_chunk = await session.get(DocumentChunkModel, chunk_id)
                if (
                    persisted_chunk is None
                    or persisted_chunk.source_document_id != source_document_id
                    or persisted_chunk.status != "ready"
                    or persisted_chunk.source_content_sha256 != source_content_sha256
                    or persisted_chunk.chunking_version != chunking_version
                    or persisted_chunk.content_sha256 != getattr(chunk, "content_sha256", None)
                ):
                    raise PersistenceConflictError(operation="embedding_chunk_identity")
                existing = (
                    await session.execute(
                        select(DocumentChunkEmbeddingModel).where(
                            DocumentChunkEmbeddingModel.chunk_id == chunk_id,
                            DocumentChunkEmbeddingModel.provider == profile.provider,
                            DocumentChunkEmbeddingModel.model == profile.model,
                            DocumentChunkEmbeddingModel.embedding_version
                            == profile.embedding_version,
                        )
                    )
                ).scalar_one_or_none()
                vector_sha256 = hashlib.sha256(vector).hexdigest()
                if existing is not None and existing.status == "ready":
                    if (
                        existing.source_document_id != source_document_id
                        or existing.dimensions != profile.dimensions
                        or existing.chunk_content_sha256 != persisted_chunk.content_sha256
                        or bytes(existing.vector or b"") != vector
                        or existing.vector_sha256 != vector_sha256
                    ):
                        raise PersistenceConflictError(operation="embedding_ready_immutable")
                    continue
                if existing is not None and existing.status == "stale":
                    raise PersistenceConflictError(operation="embedding_stale_identity")
                values = {
                    "source_document_id": source_document_id,
                    "dimensions": profile.dimensions,
                    "vector": vector,
                    "vector_sha256": vector_sha256,
                    "chunk_content_sha256": persisted_chunk.content_sha256,
                    "status": "ready",
                    "error_code": None,
                    "error_message": None,
                    "updated_at": now_text,
                    "stale_at": None,
                }
                if existing is None:
                    session.add(
                        DocumentChunkEmbeddingModel(
                            id=_embedding_id(chunk_id, profile),
                            chunk_id=chunk_id,
                            provider=profile.provider,
                            model=profile.model,
                            embedding_version=profile.embedding_version,
                            created_at=now_text,
                            **values,
                        )
                    )
                else:
                    for name, value in values.items():
                        setattr(existing, name, value)
            await session.commit()

    async def save_failed_batch(
        self,
        *,
        lease: object,
        source_document_id: str,
        source_content_sha256: str,
        chunking_version: str,
        profile: EmbeddingProfile,
        chunks: tuple[object, ...],
        error_code: str,
        now_text: str,
    ) -> None:
        """Checkpoint one failed provider batch behind the current embed-job lease."""

        if not chunks:
            raise ValueError("embedding failure batch must be nonempty")
        if not isinstance(error_code, str) or not error_code.strip():
            raise ValueError("embedding failure error_code must be nonblank")
        async with self._session_factory() as session:
            await session.execute(text("BEGIN IMMEDIATE"))
            job = await session.get(
                ProcessingJobModel,
                getattr(getattr(lease, "job", None), "id", None),
            )
            if not _active_index_job(
                job,
                lease,
                source_document_id=source_document_id,
                now_text=now_text,
            ):
                raise JobLeaseLostError(operation="embedding_index_lease_lost")
            source = await session.get(SourceDocumentModel, source_document_id)
            if (
                source is None
                or source.status != "ready"
                or source.content_sha256 != source_content_sha256
            ):
                raise PersistenceConflictError(operation="embedding_source_identity")
            for chunk in chunks:
                chunk_id = str(getattr(chunk, "id", ""))
                persisted_chunk = await session.get(DocumentChunkModel, chunk_id)
                if (
                    persisted_chunk is None
                    or persisted_chunk.source_document_id != source_document_id
                    or persisted_chunk.status != "ready"
                    or persisted_chunk.source_content_sha256 != source_content_sha256
                    or persisted_chunk.chunking_version != chunking_version
                    or persisted_chunk.content_sha256 != getattr(chunk, "content_sha256", None)
                ):
                    raise PersistenceConflictError(operation="embedding_chunk_identity")
                existing = (
                    await session.execute(
                        select(DocumentChunkEmbeddingModel).where(
                            DocumentChunkEmbeddingModel.chunk_id == chunk_id,
                            DocumentChunkEmbeddingModel.provider == profile.provider,
                            DocumentChunkEmbeddingModel.model == profile.model,
                            DocumentChunkEmbeddingModel.embedding_version
                            == profile.embedding_version,
                        )
                    )
                ).scalar_one_or_none()
                if existing is not None and existing.status == "ready":
                    if (
                        existing.source_document_id != source_document_id
                        or existing.dimensions != profile.dimensions
                        or existing.chunk_content_sha256 != persisted_chunk.content_sha256
                        or existing.vector is None
                        or existing.vector_sha256
                        != hashlib.sha256(bytes(existing.vector)).hexdigest()
                    ):
                        raise PersistenceConflictError(operation="embedding_ready_immutable")
                    continue
                if existing is not None and existing.status == "stale":
                    raise PersistenceConflictError(operation="embedding_stale_identity")
                values = {
                    "source_document_id": source_document_id,
                    "dimensions": profile.dimensions,
                    "vector": None,
                    "vector_sha256": None,
                    "chunk_content_sha256": persisted_chunk.content_sha256,
                    "status": "failed",
                    "error_code": error_code,
                    "error_message": None,
                    "updated_at": now_text,
                    "stale_at": None,
                }
                if existing is None:
                    session.add(
                        DocumentChunkEmbeddingModel(
                            id=_embedding_id(chunk_id, profile),
                            chunk_id=chunk_id,
                            provider=profile.provider,
                            model=profile.model,
                            embedding_version=profile.embedding_version,
                            created_at=now_text,
                            **values,
                        )
                    )
                else:
                    for name, value in values.items():
                        setattr(existing, name, value)
            await session.commit()

    async def report_index_progress(
        self,
        lease: object,
        *,
        completed: int,
        total: int,
        now: object,
    ) -> None:
        async with self._session_factory() as session:
            await SqlAlchemyProcessingJobRepository(session).report_progress(
                lease,
                JobProgress(
                    {
                        "phase": "embedding",
                        "completed": completed,
                        "total": total,
                    }
                ),
                now=now,
            )
            await session.commit()

    async def activate_embedding_profile(
        self,
        *,
        lease: object,
        source_document_id: str,
        source_content_sha256: str,
        chunking_version: str,
        profile: EmbeddingProfile,
        chunk_content_sha256: dict[str, str],
        now_text: str,
    ) -> None:
        async with self._session_factory() as session:
            await session.execute(text("BEGIN IMMEDIATE"))
            job = await session.get(
                ProcessingJobModel,
                getattr(getattr(lease, "job", None), "id", None),
            )
            if not _active_index_job(
                job,
                lease,
                source_document_id=source_document_id,
                now_text=now_text,
            ):
                raise JobLeaseLostError(operation="embedding_index_lease_lost")
            source = await session.get(SourceDocumentModel, source_document_id)
            if (
                source is None
                or source.status != "ready"
                or source.content_sha256 != source_content_sha256
            ):
                raise PersistenceConflictError(operation="embedding_source_identity")
            for chunk_id, expected_sha256 in chunk_content_sha256.items():
                chunk = await session.get(DocumentChunkModel, chunk_id)
                embedding = (
                    await session.execute(
                        select(DocumentChunkEmbeddingModel).where(
                            DocumentChunkEmbeddingModel.chunk_id == chunk_id,
                            DocumentChunkEmbeddingModel.provider == profile.provider,
                            DocumentChunkEmbeddingModel.model == profile.model,
                            DocumentChunkEmbeddingModel.embedding_version
                            == profile.embedding_version,
                        )
                    )
                ).scalar_one_or_none()
                vector = bytes(embedding.vector or b"") if embedding is not None else b""
                if (
                    chunk is None
                    or chunk.source_document_id != source_document_id
                    or chunk.status != "ready"
                    or chunk.source_content_sha256 != source_content_sha256
                    or chunk.chunking_version != chunking_version
                    or chunk.content_sha256 != expected_sha256
                    or embedding is None
                    or embedding.source_document_id != source_document_id
                    or embedding.status != "ready"
                    or embedding.dimensions != profile.dimensions
                    or embedding.chunk_content_sha256 != expected_sha256
                    or len(vector) != profile.dimensions * 4
                    or embedding.vector_sha256 != hashlib.sha256(vector).hexdigest()
                ):
                    raise PersistenceConflictError(
                        operation="embedding_profile_incomplete"
                    )
            await session.commit()


def _literal_fts_query(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _active_index_job(
    row: ProcessingJobModel | None,
    lease: object,
    *,
    source_document_id: str,
    now_text: str,
) -> bool:
    return bool(
        row is not None
        and row.job_type == "embed"
        and row.source_document_id == source_document_id
        and row.status == "running"
        and row.lease_owner == getattr(lease, "worker_id", None)
        and row.lease_token == getattr(lease, "token", None)
        and row.lease_expires_at is not None
        and row.lease_expires_at > now_text
        and row.cancel_requested_at is None
    )


def _embedding_id(chunk_id: str, profile: EmbeddingProfile) -> str:
    material = "\0".join(
        (chunk_id, profile.provider, profile.model, profile.embedding_version)
    ).encode("utf-8")
    return "embedding_" + hashlib.sha256(material).hexdigest()[:32]


async def _assert_index_enqueue_identity(
    session,
    *,
    job: NewProcessingJob,
    expected_source_content_sha256: str,
    expected_pdf_sha256: str,
    pdf_path: Path,
    expected_source_provider: str,
    expected_source_model: str,
) -> None:
    source = await session.get(SourceDocumentModel, job.spec.source_document_id)
    paper = await session.get(PaperModel, job.spec.paper_id)
    if (
        source is None
        or paper is None
        or source.paper_id != job.spec.paper_id
        or source.mode != job.spec.source_mode
        or source.status != "ready"
        or source.content_sha256 != expected_source_content_sha256
        or source.pdf_sha256 != expected_pdf_sha256
        or source.provider != expected_source_provider
        or source.model != expected_source_model
        or not paper.pdf_path
        or Path(paper.pdf_path).resolve() != pdf_path.resolve()
    ):
        raise PersistenceConflictError(operation="enqueue_index_source_identity")
    try:
        actual_pdf_sha256 = hashlib.sha256(pdf_path.read_bytes()).hexdigest()
    except OSError:
        raise PersistenceConflictError(operation="enqueue_index_pdf_missing") from None
    if actual_pdf_sha256 != expected_pdf_sha256:
        raise PersistenceConflictError(operation="enqueue_index_pdf_changed")


def _decode_heading_path(value: object) -> tuple[str, ...]:
    if value is None:
        return ()
    decoded = json.loads(str(value))
    if not isinstance(decoded, list) or not all(isinstance(item, str) for item in decoded):
        raise ValueError("SEARCH_INDEX_INVALID: heading path is not a string array")
    return tuple(decoded)


def _safe_excerpt(content: str, query: str, *, radius: int = 96) -> str:
    lowered = content.casefold()
    index = lowered.find(query.casefold())
    if index < 0:
        index = 0
    start = max(0, index - radius)
    end = min(len(content), index + len(query) + radius)
    excerpt = content[start:end]
    excerpt = _CONTROL_CHARACTERS.sub("�", excerpt).strip()
    return excerpt or "[indexed content]"


__all__ = ["SqlAlchemyDocumentSearchRepository"]
