"""Pure domain values shared by P3 chunking, artifact, and search seams.

This module intentionally imports only the Python standard library and the
existing P1 domain entities.  Persistence/provider adapters belong outside the
module so query construction cannot accidentally acquire write or network
side-effects.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import hashlib
import json
import math
import re
from typing import Mapping, Sequence

from backend.app.domain.entities import ArtifactKind, DocumentChunk
from backend.app.domain.errors import SearchQueryTooShortError


_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_CHUNK_KEY = re.compile(r"[0-9a-f]{64}\Z")


def _nonblank(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be nonblank text")
    return value


def _sha(value: object, field_name: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise ValueError(f"{field_name} must be a lowercase SHA-256 digest")
    return value


def _positive_int(value: object, field_name: str, minimum: int = 1, maximum: int | None = None) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < minimum:
        raise ValueError(f"{field_name} must be an integer >= {minimum}")
    if maximum is not None and value > maximum:
        raise ValueError(f"{field_name} must be <= {maximum}")
    return value


def _canonical_json(value: Mapping[str, object]) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError) as error:
        raise ValueError("value must be JSON-safe") from error


def _content_sha(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


class SearchMode(str, Enum):
    LEXICAL = "lexical"
    SEMANTIC = "semantic"
    HYBRID = "hybrid"


@dataclass(frozen=True, slots=True)
class ChunkingSpec:
    chunking_version: str = "markdown-coverage-v1"
    tokenizer_version: str = "unicode-word-v1"
    target_tokens: int = 1600
    hard_cap_tokens: int = 1600
    atomic_hard_cap_tokens: int = 8192
    overlap_tokens: int = 0

    def __post_init__(self) -> None:
        _nonblank(self.chunking_version, "chunking_version")
        _nonblank(self.tokenizer_version, "tokenizer_version")
        _positive_int(self.target_tokens, "target_tokens")
        _positive_int(self.hard_cap_tokens, "hard_cap_tokens")
        _positive_int(self.atomic_hard_cap_tokens, "atomic_hard_cap_tokens")
        if self.target_tokens > self.hard_cap_tokens:
            raise ValueError("target_tokens cannot exceed hard_cap_tokens")
        if self.atomic_hard_cap_tokens < self.hard_cap_tokens:
            raise ValueError("atomic_hard_cap_tokens cannot be below hard_cap_tokens")
        if not isinstance(self.overlap_tokens, int) or isinstance(self.overlap_tokens, bool) or self.overlap_tokens < 0:
            raise ValueError("overlap_tokens must be nonnegative")
        if self.overlap_tokens:
            raise ValueError("P3 coverage chunking requires zero overlap")


def chunk_key_for(
    *,
    source_document_id: str,
    source_content_sha256: str,
    chunking_version: str,
    sequence: int,
    char_start: int,
    char_end: int,
    content_sha256: str,
) -> str:
    """Return the stable P3 chunk identity defined by the migration plan."""

    source_document_id = _nonblank(source_document_id, "source_document_id")
    source_content_sha256 = _sha(source_content_sha256, "source_content_sha256")
    chunking_version = _nonblank(chunking_version, "chunking_version")
    content_sha256 = _sha(content_sha256, "content_sha256")
    if not isinstance(sequence, int) or isinstance(sequence, bool) or sequence < 0:
        raise ValueError("sequence must be nonnegative")
    if not isinstance(char_start, int) or isinstance(char_start, bool) or char_start < 0:
        raise ValueError("char_start must be nonnegative")
    if not isinstance(char_end, int) or isinstance(char_end, bool) or char_end < char_start:
        raise ValueError("char_end must be >= char_start")
    material = "\0".join(
        (
            "chunk:v1", source_document_id, source_content_sha256, chunking_version,
            str(sequence), str(char_start), str(char_end), content_sha256,
        )
    ).encode("utf-8")
    return hashlib.sha256(material).hexdigest()


@dataclass(frozen=True, slots=True)
class ChunkSet:
    source_document_id: str
    source_content_sha256: str
    chunks: tuple[DocumentChunk, ...]
    source_markdown: str
    spec: ChunkingSpec = field(default_factory=ChunkingSpec)

    def __post_init__(self) -> None:
        _nonblank(self.source_document_id, "source_document_id")
        _sha(self.source_content_sha256, "source_content_sha256")
        if not isinstance(self.source_markdown, str):
            raise ValueError("source_markdown must be text")
        chunks = tuple(self.chunks)
        object.__setattr__(self, "chunks", chunks)
        expected_offset = 0
        for sequence, chunk in enumerate(chunks):
            if not isinstance(chunk, DocumentChunk):
                raise ValueError("chunks must contain DocumentChunk values")
            if chunk.source_document_id != self.source_document_id:
                raise ValueError("chunk source_document_id mismatch")
            if chunk.sequence != sequence:
                raise ValueError("chunk sequences must be contiguous from zero")
            if _content_sha(chunk.content) != chunk.content_sha256:
                raise ValueError("chunk content_sha256 does not match content")
            start = chunk.char_start if chunk.char_start is not None else expected_offset
            end = chunk.char_end if chunk.char_end is not None else start + len(chunk.content)
            if start != expected_offset or end != start + len(chunk.content):
                raise ValueError("chunk offsets contain a gap or overlap")
            if chunk.chunk_key is not None and chunk.chunking_version is not None:
                expected_key = chunk_key_for(
                    source_document_id=self.source_document_id,
                    source_content_sha256=self.source_content_sha256,
                    chunking_version=chunk.chunking_version,
                    sequence=sequence,
                    char_start=start,
                    char_end=end,
                    content_sha256=chunk.content_sha256,
                )
                if chunk.chunk_key != expected_key:
                    raise ValueError("chunk_key is not deterministic for the chunk")
            expected_offset = end
        if "".join(chunk.content for chunk in chunks) != self.source_markdown:
            raise ValueError("chunks do not cover source markdown byte-for-byte")

    @property
    def chunk_ids(self) -> tuple[str, ...]:
        return tuple(chunk.id for chunk in self.chunks)

    @property
    def total_tokens(self) -> int:
        return sum(chunk.token_count for chunk in self.chunks)

    @property
    def covered_content_sha256(self) -> str:
        return _content_sha(self.source_markdown)


@dataclass(frozen=True, slots=True)
class ContextRequest:
    source_document_id: str
    consumer: ArtifactKind | str
    budget_tokens: int | None = None
    chunking_version: str = "markdown-coverage-v1"

    def __post_init__(self) -> None:
        _nonblank(self.source_document_id, "source_document_id")
        if self.consumer == "embedding":
            object.__setattr__(self, "consumer", "embedding")
        else:
            try:
                object.__setattr__(self, "consumer", ArtifactKind(self.consumer))
            except (TypeError, ValueError) as error:
                raise ValueError(
                    "consumer must be a P1 ArtifactKind or embedding"
                ) from error
        if self.budget_tokens is not None:
            _positive_int(self.budget_tokens, "budget_tokens")
        _nonblank(self.chunking_version, "chunking_version")


@dataclass(frozen=True, slots=True)
class ContextBatch:
    sequence: int
    chunk_ids: tuple[str, ...]
    chunks: tuple[DocumentChunk, ...]
    token_count: int
    level: str = "leaf"
    covered_ranges: tuple[tuple[int, int], ...] = ()
    group_sequence: int | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.sequence, int) or isinstance(self.sequence, bool) or self.sequence < 0:
            raise ValueError("batch sequence must be nonnegative")
        ids = tuple(self.chunk_ids)
        chunks = tuple(self.chunks)
        object.__setattr__(self, "chunk_ids", ids)
        object.__setattr__(self, "chunks", chunks)
        if len(ids) != len(chunks) or not ids:
            raise ValueError("batch chunk ids and chunks must be nonempty and aligned")
        if ids != tuple(chunk.id for chunk in chunks):
            raise ValueError("batch chunk ids do not match chunks")
        if not isinstance(self.token_count, int) or isinstance(self.token_count, bool) or self.token_count < 0:
            raise ValueError("batch token_count must be nonnegative")
        if self.token_count != sum(chunk.token_count for chunk in chunks):
            raise ValueError("batch token_count does not match chunks")
        _nonblank(self.level, "level")
        if self.group_sequence is not None and (
            not isinstance(self.group_sequence, int)
            or isinstance(self.group_sequence, bool)
            or self.group_sequence < 0
        ):
            raise ValueError("batch group_sequence must be nonnegative")
        ranges = tuple(self.covered_ranges)
        for start, end in ranges:
            if not isinstance(start, int) or not isinstance(end, int) or start < 0 or end <= start:
                raise ValueError("covered range is invalid")
        object.__setattr__(self, "covered_ranges", ranges)


@dataclass(frozen=True, slots=True)
class ContextPlan:
    source_document_id: str
    source_content_sha256: str
    chunking_version: str
    all_chunk_ids: tuple[str, ...]
    eligible_chunk_ids: tuple[str, ...]
    selected_chunk_ids: tuple[str, ...]
    batches: tuple[ContextBatch, ...]
    consumer: ArtifactKind | str = ArtifactKind.TRANSLATION
    excluded_reasons: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        _nonblank(self.source_document_id, "source_document_id")
        _sha(self.source_content_sha256, "source_content_sha256")
        _nonblank(self.chunking_version, "chunking_version")
        if self.consumer == "embedding":
            object.__setattr__(self, "consumer", "embedding")
        else:
            try:
                object.__setattr__(self, "consumer", ArtifactKind(self.consumer))
            except (TypeError, ValueError) as error:
                raise ValueError(
                    "consumer must be a P1 ArtifactKind or embedding"
                ) from error
        all_ids = tuple(self.all_chunk_ids)
        eligible = tuple(self.eligible_chunk_ids)
        selected = tuple(self.selected_chunk_ids)
        batches = tuple(self.batches)
        object.__setattr__(self, "all_chunk_ids", all_ids)
        object.__setattr__(self, "eligible_chunk_ids", eligible)
        object.__setattr__(self, "selected_chunk_ids", selected)
        object.__setattr__(self, "batches", batches)
        if len(set(all_ids)) != len(all_ids) or len(set(eligible)) != len(eligible) or len(set(selected)) != len(selected):
            raise ValueError("plan ids must not repeat")
        if not set(eligible).issubset(all_ids) or not set(selected).issubset(eligible):
            raise ValueError("plan coverage sets are not nested")
        batch_ids = tuple(item for batch in batches for item in batch.chunk_ids)
        if set(batch_ids) != set(selected) or len(batch_ids) != len(selected):
            raise ValueError("batches do not cover selected chunks exactly once")
        reasons = tuple(self.excluded_reasons)
        for item in reasons:
            if not isinstance(item, tuple) or len(item) != 2 or not all(isinstance(value, str) for value in item):
                raise ValueError("excluded_reasons must contain (range, reason) text pairs")
        object.__setattr__(self, "excluded_reasons", reasons)

    @property
    def request_consumer(self) -> str:
        return self.consumer if isinstance(self.consumer, str) else self.consumer.value

    @property
    def total_chunks(self) -> int:
        return len(self.all_chunk_ids)

    @property
    def total_tokens(self) -> int:
        return sum(batch.token_count for batch in self.batches)

    @property
    def covered_content_sha256(self) -> str:
        return _content_sha(
            "".join(
                chunk.content
                for batch in self.batches
                for chunk in batch.chunks
            )
        )

    @property
    def coverage_hash(self) -> str:
        payload = {
            "all": self.all_chunk_ids,
            "eligible": self.eligible_chunk_ids,
            "selected": self.selected_chunk_ids,
            "consumer": self.request_consumer,
            "chunkingVersion": self.chunking_version,
            "coveredContentSha256": self.covered_content_sha256,
        }
        return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class EmbeddingProfile:
    provider: str
    model: str
    embedding_version: str
    dimensions: int
    options: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for name in ("provider", "model", "embedding_version"):
            _nonblank(getattr(self, name), name)
        _positive_int(self.dimensions, "dimensions")
        normalized = json.loads(_canonical_json(dict(self.options)))
        object.__setattr__(self, "options", normalized)


@dataclass(frozen=True, slots=True)
class EmbeddingRequest:
    profile: EmbeddingProfile
    texts: tuple[str, ...]
    chunk_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        texts = tuple(self.texts)
        ids = tuple(self.chunk_ids)
        object.__setattr__(self, "texts", texts)
        object.__setattr__(self, "chunk_ids", ids)
        if not texts or len(texts) != len(ids):
            raise ValueError("embedding texts and chunk_ids must be nonempty and aligned")
        if any(not isinstance(text, str) or not text for text in texts):
            raise ValueError("embedding texts must be nonempty text")
        if any(not isinstance(identifier, str) or not identifier.strip() for identifier in ids):
            raise ValueError("embedding chunk ids must be nonblank")


@dataclass(frozen=True, slots=True)
class EmbeddingBatch:
    profile: EmbeddingProfile
    vectors: tuple[tuple[float, ...], ...]
    chunk_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        vectors = tuple(tuple(vector) for vector in self.vectors)
        ids = tuple(self.chunk_ids)
        object.__setattr__(self, "vectors", vectors)
        object.__setattr__(self, "chunk_ids", ids)
        if len(vectors) != len(ids) or not vectors:
            raise ValueError("embedding vectors and chunk_ids must be nonempty and aligned")
        for vector in vectors:
            if len(vector) != self.profile.dimensions:
                raise ValueError("embedding vector dimensions do not match profile")
            if not vector or all(value == 0 for value in vector):
                raise ValueError("embedding vector must not be zero")
            if any(not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(float(value)) for value in vector):
                raise ValueError("embedding vectors must contain finite numbers")


@dataclass(frozen=True, slots=True)
class SearchRequest:
    query: str
    mode: SearchMode | str = SearchMode.LEXICAL
    paper_ids: tuple[str, ...] = ()
    limit: int = 20

    def __post_init__(self) -> None:
        if not isinstance(self.query, str):
            raise ValueError("query must be text")
        normalized = self.query.strip()
        if len(tuple(normalized)) < 3:
            raise SearchQueryTooShortError()
        object.__setattr__(self, "query", normalized)
        try:
            object.__setattr__(self, "mode", SearchMode(self.mode))
        except (TypeError, ValueError) as error:
            raise ValueError("mode is invalid") from error
        ids = tuple(self.paper_ids)
        if any(not isinstance(identifier, str) or not identifier.strip() for identifier in ids):
            raise ValueError("paper_ids must contain nonblank text")
        object.__setattr__(self, "paper_ids", ids)
        _positive_int(self.limit, "limit", minimum=1, maximum=50)


@dataclass(frozen=True, slots=True)
class SearchHit:
    paper_id: str
    source_document_id: str
    chunk_id: str
    sequence: int
    heading_path: tuple[str, ...]
    page_start: int | None
    page_end: int | None
    excerpt: str
    score: float
    lexical_score: float | None = None
    semantic_score: float | None = None

    def __post_init__(self) -> None:
        for name in ("paper_id", "source_document_id", "chunk_id", "excerpt"):
            _nonblank(getattr(self, name), name)
        if not isinstance(self.sequence, int) or isinstance(self.sequence, bool) or self.sequence < 0:
            raise ValueError("sequence must be nonnegative")
        path = tuple(self.heading_path)
        if any(not isinstance(item, str) for item in path):
            raise ValueError("heading_path must contain text")
        object.__setattr__(self, "heading_path", path)
        if self.page_start is not None and (not isinstance(self.page_start, int) or self.page_start < 0):
            raise ValueError("page_start is invalid")
        if self.page_end is not None and (not isinstance(self.page_end, int) or self.page_end < 0):
            raise ValueError("page_end is invalid")
        if self.page_start is not None and self.page_end is not None and self.page_end < self.page_start:
            raise ValueError("page_end cannot precede page_start")
        if not math.isfinite(float(self.score)):
            raise ValueError("score must be finite")
        for value in (self.lexical_score, self.semantic_score):
            if value is not None and not math.isfinite(float(value)):
                raise ValueError("scores must be finite")


@dataclass(frozen=True, slots=True)
class SearchCoverage:
    ready_chunks: int
    embedded_chunks: int
    stale_chunks: int
    failed_embeddings: int

    def __post_init__(self) -> None:
        for name in ("ready_chunks", "embedded_chunks", "stale_chunks", "failed_embeddings"):
            _positive_int(getattr(self, name), name, minimum=0)


def reciprocal_rank_fusion(
    lexical: Sequence[str], semantic: Sequence[str], *, k: int = 60,
) -> tuple[tuple[str, float], ...]:
    """Fuse two already-ranked id sequences using deterministic RRF."""

    _positive_int(k, "k")
    scores: dict[str, float] = {}
    first_seen: dict[str, int] = {}
    order = 0
    for ranked in (tuple(lexical), tuple(semantic)):
        for rank, identifier in enumerate(ranked, start=1):
            identifier = _nonblank(identifier, "ranked identifier")
            if identifier not in first_seen:
                first_seen[identifier] = order
                order += 1
            scores[identifier] = scores.get(identifier, 0.0) + 1.0 / (k + rank)
    return tuple(
        (identifier, scores[identifier])
        for identifier in sorted(scores, key=lambda item: (-scores[item], first_seen[item], item))
    )


__all__ = [
    "ChunkSet", "ChunkingSpec", "ContextBatch", "ContextPlan", "ContextRequest",
    "EmbeddingBatch", "EmbeddingProfile", "EmbeddingRequest", "SearchCoverage",
    "SearchHit", "SearchMode", "SearchRequest", "chunk_key_for", "reciprocal_rank_fusion",
]
