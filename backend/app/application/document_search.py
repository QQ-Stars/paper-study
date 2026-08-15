from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import math
from pathlib import Path
import struct
from uuid import uuid4

from backend.app.domain import (
    CredentialKind,
    DomainError,
    EmbeddingRequestFailedError,
    EmbeddingProfileUnavailableError,
    EmbeddingResponseInvalidError,
    MissingPdfError,
    StaleSourceError,
    is_frozen_native_source_identity,
)
from backend.app.domain.context import (
    ChunkingSpec,
    ContextRequest,
    EmbeddingProfile,
    EmbeddingRequest,
    SearchCoverage,
    SearchHit,
    SearchMode,
    SearchRequest,
    reciprocal_rank_fusion,
)
from backend.app.domain.processing import (
    EmbedJobSpecV1,
    JobResult,
    JobSpecValidationError,
    NewProcessingJob,
    build_index_job_key,
    encode_job_spec_v1,
    hash_job_spec,
)


@dataclass(frozen=True, slots=True)
class SearchResultPage:
    items: tuple[SearchHit, ...]
    coverage: SearchCoverage

    def __post_init__(self) -> None:
        object.__setattr__(self, "items", tuple(self.items))


@dataclass(frozen=True, slots=True)
class IndexResult:
    total_chunks: int
    embedded_chunks: int
    reused_chunks: int
    failed_embeddings: int = 0

    def to_job_result(self) -> dict[str, object]:
        return {
            "totalChunks": self.total_chunks,
            "embeddedChunks": self.embedded_chunks,
            "reusedChunks": self.reused_chunks,
            "failedEmbeddings": self.failed_embeddings,
        }


@dataclass(frozen=True, slots=True)
class IndexStatus:
    """Read-only coverage for one source and one frozen embedding profile."""

    total_chunks: int
    ready_chunks: int
    embedded_chunks: int
    stale_chunks: int
    failed_embeddings: int
    provider: str | None = None
    model: str | None = None
    version: str | None = None
    coverage: str = "empty"

    def __post_init__(self) -> None:
        for name in (
            "total_chunks",
            "ready_chunks",
            "embedded_chunks",
            "stale_chunks",
            "failed_embeddings",
        ):
            value = getattr(self, name)
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise ValueError(f"{name} must be a nonnegative integer")
        if self.ready_chunks > self.total_chunks:
            raise ValueError("ready_chunks cannot exceed total_chunks")
        if self.embedded_chunks > self.ready_chunks:
            raise ValueError("embedded_chunks cannot exceed ready_chunks")
        if self.coverage not in {"empty", "partial", "complete"}:
            raise ValueError("coverage is invalid")

    def to_api_dict(self) -> dict[str, object]:
        return {
            "totalChunks": self.total_chunks,
            "readyChunks": self.ready_chunks,
            "embeddedChunks": self.embedded_chunks,
            "staleChunks": self.stale_chunks,
            "failedEmbeddings": self.failed_embeddings,
            "provider": self.provider,
            "model": self.model,
            "version": self.version,
            "coverage": self.coverage,
        }


class DocumentSearch:
    """Read-only query facade; indexing and provider work are separate commands."""

    def __init__(
        self,
        repository,
        *,
        context_builder=None,
        embedding_provider=None,
        index_embedding_profile: EmbeddingProfile | None = None,
        query_embedding_profile: EmbeddingProfile | None = None,
        query_embedding_provider=None,
        clock=None,
        embedding_batch_size: int = 32,
        job_id_factory=None,
    ) -> None:
        if (
            not isinstance(embedding_batch_size, int)
            or isinstance(embedding_batch_size, bool)
            or embedding_batch_size < 1
        ):
            raise ValueError("embedding_batch_size must be positive")
        self._repository = repository
        self._context_builder = context_builder
        self._embedding_provider = embedding_provider
        self._index_embedding_profile = index_embedding_profile
        self._query_embedding_profile = query_embedding_profile
        self._query_embedding_provider = query_embedding_provider
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._embedding_batch_size = embedding_batch_size
        self._job_id_factory = job_id_factory or (lambda: f"job_{uuid4().hex}")

    async def status(
        self,
        source_document_id: str,
        *,
        paper_id: str | None = None,
    ) -> IndexStatus:
        """Return persisted index coverage without repairing or materializing data."""

        if not isinstance(source_document_id, str) or not source_document_id.strip():
            raise ValueError("source_document_id must be nonblank")
        if paper_id is not None and (
            not isinstance(paper_id, str) or not paper_id.strip()
        ):
            raise ValueError("paper_id must be nonblank")
        profile = self._index_embedding_profile
        raw = await self._repository.index_status(
            source_document_id,
            paper_id=paper_id,
            profile=profile,
        )
        if isinstance(raw, IndexStatus):
            return raw
        if not isinstance(raw, dict):
            raise ValueError("index status repository returned an invalid value")
        return IndexStatus(
            total_chunks=int(raw.get("total_chunks", 0)),
            ready_chunks=int(raw.get("ready_chunks", 0)),
            embedded_chunks=int(raw.get("embedded_chunks", 0)),
            stale_chunks=int(raw.get("stale_chunks", 0)),
            failed_embeddings=int(raw.get("failed_embeddings", 0)),
            provider=raw.get("provider"),
            model=raw.get("model"),
            version=raw.get("version", raw.get("embedding_version")),
            coverage=str(raw.get("coverage", "empty")),
        )

    async def enqueue_index(
        self,
        *,
        paper_id: str,
        source_mode: str,
        source_document_id: str,
        include_embeddings: bool,
        profile: EmbeddingProfile | None,
    ):
        if not isinstance(include_embeddings, bool):
            raise ValueError("include_embeddings must be boolean")
        if include_embeddings:
            if not isinstance(profile, EmbeddingProfile):
                raise EmbeddingProfileUnavailableError()
            provider = profile.provider
            model = profile.model
            version = profile.embedding_version
            dimensions = profile.dimensions
            options = dict(profile.options)
        else:
            if profile is not None:
                raise ValueError("disabled embeddings cannot carry a profile")
            provider = model = version = "none"
            dimensions = None
            options = {}
        identity = await self._repository.index_source_identity(
            paper_id=paper_id,
            source_mode=source_mode,
            source_document_id=source_document_id,
        )
        if source_mode == "native" and not is_frozen_native_source_identity(
            mode=source_mode,
            provider=identity["source_provider"],
            model=identity["source_model"],
        ):
            raise StaleSourceError(paper_id=paper_id)
        pdf_path = Path(identity["pdf_path"])
        try:
            pdf_sha256 = hashlib.sha256(pdf_path.read_bytes()).hexdigest()
        except OSError:
            raise MissingPdfError(paper_id=paper_id) from None
        if pdf_sha256 != identity["pdf_sha256"]:
            raise StaleSourceError(paper_id=paper_id)
        chunking_version = "markdown-coverage-v1"
        spec = EmbedJobSpecV1(
            paper_id=paper_id,
            source_document_id=source_document_id,
            include_embeddings=include_embeddings,
            provider=provider,
            model=model,
            embedding_version=version,
            dimensions=dimensions,
            chunking_version=chunking_version,
            options=options,
            source_mode=source_mode,
        )
        spec_json = encode_job_spec_v1(spec)
        job = NewProcessingJob(
            id=self._job_id_factory(),
            spec=spec,
            idempotency_key=build_index_job_key(
                source_document_id=source_document_id,
                source_content_sha256=identity["source_content_sha256"],
                chunking_version=chunking_version,
                embedding_provider=provider,
                embedding_model=model,
                embedding_version=version,
                include_embeddings=include_embeddings,
                embedding_options=options,
            ),
            created_at=self._clock(),
            max_attempts=3,
        )
        return await self._repository.enqueue_index_job(
            job=job,
            spec_json=spec_json,
            spec_sha256=hash_job_spec(spec_json),
            expected_source_content_sha256=identity["source_content_sha256"],
            expected_pdf_sha256=pdf_sha256,
            pdf_path=pdf_path,
            expected_source_provider=identity["source_provider"],
            expected_source_model=identity["source_model"],
        )

    async def index(
        self,
        lease,
        source_document_id: str,
        profile: EmbeddingProfile,
    ) -> IndexResult:
        if self._context_builder is None or self._embedding_provider is None:
            raise ValueError("embedding index is not configured")
        if not isinstance(profile, EmbeddingProfile):
            raise ValueError("profile must be EmbeddingProfile")
        if self._embedding_provider.provider_id != profile.provider:
            raise ValueError("embedding provider identity mismatch")
        plan = await self._context_builder.build(
            source_document_id,
            ContextRequest(
                source_document_id=source_document_id,
                consumer="embedding",
                budget_tokens=8192,
            ),
        )
        chunks = tuple(chunk for batch in plan.batches for chunk in batch.chunks)
        if tuple(chunk.id for chunk in chunks) != plan.all_chunk_ids:
            raise ValueError("embedding context plan does not cover all chunks in order")
        content_sha_by_id = {chunk.id: chunk.content_sha256 for chunk in chunks}
        ready_ids = await self._repository.exact_ready_chunk_ids(
            source_document_id=source_document_id,
            profile=profile,
            chunk_content_sha256=content_sha_by_id,
        )
        missing = tuple(chunk for chunk in chunks if chunk.id not in ready_ids)
        completed = len(ready_ids)
        for start in range(0, len(missing), self._embedding_batch_size):
            batch_chunks = missing[start : start + self._embedding_batch_size]
            now = self._clock().astimezone(timezone.utc)
            now_text = now.isoformat().replace("+00:00", "Z")
            await self._repository.assert_active_index_lease(
                lease,
                source_document_id=source_document_id,
                now_text=now_text,
            )
            request = EmbeddingRequest(
                profile=profile,
                texts=tuple(chunk.content for chunk in batch_chunks),
                chunk_ids=tuple(chunk.id for chunk in batch_chunks),
            )
            try:
                response = await self._embedding_provider.embed(request)
                if response.profile != profile or response.chunk_ids != request.chunk_ids:
                    raise EmbeddingResponseInvalidError()
                if len(response.vectors) != len(batch_chunks):
                    raise EmbeddingResponseInvalidError()
                packed = tuple(_normalized_float32(vector) for vector in response.vectors)
                if any(len(vector) != profile.dimensions * 4 for vector in packed):
                    raise EmbeddingResponseInvalidError()
            except DomainError as error:
                if not isinstance(
                    error,
                    (EmbeddingRequestFailedError, EmbeddingResponseInvalidError),
                ):
                    raise
                failed_now = self._clock().astimezone(timezone.utc)
                await self._repository.save_failed_batch(
                    lease=lease,
                    source_document_id=source_document_id,
                    source_content_sha256=plan.source_content_sha256,
                    chunking_version=plan.chunking_version,
                    profile=profile,
                    chunks=batch_chunks,
                    error_code=error.code,
                    now_text=failed_now.isoformat().replace("+00:00", "Z"),
                )
                raise
            except Exception:
                error = EmbeddingRequestFailedError(retryable=True)
                failed_now = self._clock().astimezone(timezone.utc)
                await self._repository.save_failed_batch(
                    lease=lease,
                    source_document_id=source_document_id,
                    source_content_sha256=plan.source_content_sha256,
                    chunking_version=plan.chunking_version,
                    profile=profile,
                    chunks=batch_chunks,
                    error_code=error.code,
                    now_text=failed_now.isoformat().replace("+00:00", "Z"),
                )
                raise error from None
            ready_now = self._clock().astimezone(timezone.utc)
            await self._repository.save_ready_batch(
                lease=lease,
                source_document_id=source_document_id,
                source_content_sha256=plan.source_content_sha256,
                chunking_version=plan.chunking_version,
                profile=profile,
                chunks=batch_chunks,
                vectors=packed,
                now_text=ready_now.isoformat().replace("+00:00", "Z"),
            )
            completed += len(batch_chunks)
            progress_now = self._clock().astimezone(timezone.utc)
            await self._repository.report_index_progress(
                lease,
                completed=completed,
                total=len(chunks),
                now=progress_now,
            )
        verification = await self._context_builder.build(
            source_document_id,
            ContextRequest(
                source_document_id=source_document_id,
                consumer="embedding",
                budget_tokens=8192,
                chunking_version=plan.chunking_version,
            ),
        )
        if (
            verification.source_content_sha256 != plan.source_content_sha256
            or verification.all_chunk_ids != plan.all_chunk_ids
        ):
            raise ValueError("embedding source identity changed during indexing")
        await self._repository.activate_embedding_profile(
            lease=lease,
            source_document_id=source_document_id,
            source_content_sha256=plan.source_content_sha256,
            chunking_version=plan.chunking_version,
            profile=profile,
            chunk_content_sha256=content_sha_by_id,
            now_text=self._clock()
            .astimezone(timezone.utc)
            .isoformat()
            .replace("+00:00", "Z"),
        )
        return IndexResult(
            total_chunks=len(chunks),
            embedded_chunks=completed,
            reused_chunks=len(ready_ids),
        )

    async def search(self, request: SearchRequest) -> SearchResultPage:
        if not isinstance(request, SearchRequest):
            raise ValueError("request must be SearchRequest")
        if request.mode is SearchMode.LEXICAL:
            items = await self._repository.lexical(request)
            coverage = await self._repository.coverage_for_request(request)
            return SearchResultPage(items=items, coverage=coverage)
        profile = self._query_embedding_profile
        provider = self._query_embedding_provider
        if profile is None or provider is None:
            raise EmbeddingProfileUnavailableError()
        if getattr(provider, "provider_id", None) != profile.provider:
            raise EmbeddingProfileUnavailableError()
        query_id = "query-vector"
        try:
            response = await provider.embed(
                EmbeddingRequest(
                    profile=profile,
                    texts=(request.query,),
                    chunk_ids=(query_id,),
                )
            )
            if (
                response.profile != profile
                or response.chunk_ids != (query_id,)
                or len(response.vectors) != 1
            ):
                raise EmbeddingResponseInvalidError()
            query_vector = _normalized_float32(response.vectors[0])
            if len(query_vector) != profile.dimensions * 4:
                raise EmbeddingResponseInvalidError()
        except (EmbeddingRequestFailedError, EmbeddingResponseInvalidError):
            raise
        except Exception:
            raise EmbeddingRequestFailedError(retryable=True) from None
        semantic = await self._repository.semantic(
            request,
            profile=profile,
            query_vector=struct.unpack(
                "<" + "f" * profile.dimensions,
                query_vector,
            ),
        )
        coverage = await self._repository.coverage_for_request(
            request,
            profile=profile,
        )
        if request.mode is SearchMode.SEMANTIC:
            return SearchResultPage(items=semantic, coverage=coverage)
        lexical = await self._repository.lexical(request)
        items = _hybrid_hits(lexical, semantic, limit=request.limit)
        return SearchResultPage(items=items, coverage=coverage)


class EmbeddingJobHandler:
    """Reconstruct and execute one index command from its immutable lease spec."""

    def __init__(
        self,
        repository,
        *,
        context_builder,
        credential_store,
        provider_factory,
        clock=None,
    ) -> None:
        self._repository = repository
        self._context_builder = context_builder
        self._credential_store = credential_store
        self._provider_factory = provider_factory
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    async def __call__(self, lease) -> JobResult:
        spec = getattr(getattr(lease, "spec", None), "value", None)
        if not isinstance(spec, EmbedJobSpecV1) or spec.include_embeddings is None:
            raise JobSpecValidationError("embed lease requires a frozen P3 index spec")
        now = self._clock().astimezone(timezone.utc)
        await self._context_builder.materialize_chunks(
            spec.source_document_id,
            ChunkingSpec(chunking_version=spec.chunking_version),
            now=now,
        )
        plan = await self._context_builder.build(
            spec.source_document_id,
            ContextRequest(
                source_document_id=spec.source_document_id,
                consumer="embedding",
                budget_tokens=8192,
                chunking_version=spec.chunking_version,
            ),
        )
        chunks = tuple(chunk for batch in plan.batches for chunk in batch.chunks)
        if tuple(chunk.id for chunk in chunks) != plan.all_chunk_ids:
            raise JobSpecValidationError("embed context does not cover all chunks")
        await self._repository.validate_index_context(
            lease=lease,
            source_document_id=spec.source_document_id,
            source_content_sha256=plan.source_content_sha256,
            chunking_version=plan.chunking_version,
            chunk_content_sha256={chunk.id: chunk.content_sha256 for chunk in chunks},
            now_text=now.isoformat().replace("+00:00", "Z"),
        )
        if not spec.include_embeddings:
            return JobResult(
                IndexResult(
                    total_chunks=len(chunks),
                    embedded_chunks=0,
                    reused_chunks=0,
                ).to_job_result()
            )

        profile = EmbeddingProfile(
            provider=spec.provider or "",
            model=spec.model or "",
            embedding_version=spec.embedding_version or "",
            dimensions=spec.dimensions or 0,
            options=dict(spec.options or {}),
        )
        credential = await self._credential_store.get(CredentialKind.EMBEDDING)
        provider = self._provider_factory(profile, credential)
        batch_size = _embedding_batch_size(profile.options)
        search = DocumentSearch(
            self._repository,
            context_builder=self._context_builder,
            embedding_provider=provider,
            clock=self._clock,
            embedding_batch_size=batch_size,
        )
        result = await search.index(lease, spec.source_document_id, profile)
        return JobResult(result.to_job_result())


def _embedding_batch_size(options: object) -> int:
    value = dict(options).get("batchSize", 32)
    if (
        not isinstance(value, int)
        or isinstance(value, bool)
        or not 1 <= value <= 256
    ):
        raise JobSpecValidationError("embedding batchSize must be from one to 256")
    return value


def _hybrid_hits(
    lexical: tuple[SearchHit, ...],
    semantic: tuple[SearchHit, ...],
    *,
    limit: int,
) -> tuple[SearchHit, ...]:
    lexical_by_id = {item.chunk_id: item for item in lexical}
    semantic_by_id = {item.chunk_id: item for item in semantic}
    fused = reciprocal_rank_fusion(
        tuple(lexical_by_id),
        tuple(semantic_by_id),
        k=60,
    )
    result: list[SearchHit] = []
    for chunk_id, score in fused[:limit]:
        lexical_hit = lexical_by_id.get(chunk_id)
        semantic_hit = semantic_by_id.get(chunk_id)
        template = lexical_hit or semantic_hit
        if template is None:
            raise ValueError("hybrid search rank references an unknown chunk")
        result.append(
            SearchHit(
                paper_id=template.paper_id,
                source_document_id=template.source_document_id,
                chunk_id=template.chunk_id,
                sequence=template.sequence,
                heading_path=template.heading_path,
                page_start=template.page_start,
                page_end=template.page_end,
                excerpt=template.excerpt,
                score=score,
                lexical_score=(
                    lexical_hit.lexical_score if lexical_hit is not None else None
                ),
                semantic_score=(
                    semantic_hit.semantic_score if semantic_hit is not None else None
                ),
            )
        )
    return tuple(result)


def _normalized_float32(vector: tuple[float, ...]) -> bytes:
    try:
        values = tuple(float(value) for value in vector)
        norm = math.sqrt(math.fsum(value * value for value in values))
        if not math.isfinite(norm) or norm <= 0:
            raise ValueError("embedding vector cannot be normalized")
        normalized = tuple(value / norm for value in values)
        packed = struct.pack(f"<{len(normalized)}f", *normalized)
        if any(
            not math.isfinite(value)
            for value in struct.unpack(f"<{len(normalized)}f", packed)
        ):
            raise ValueError("embedding vector is not finite float32")
        return packed
    except (TypeError, ValueError, OverflowError, struct.error) as error:
        raise EmbeddingResponseInvalidError() from error


__all__ = [
    "DocumentSearch",
    "EmbeddingJobHandler",
    "IndexResult",
    "IndexStatus",
    "SearchResultPage",
]
