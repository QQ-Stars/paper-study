from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from os import PathLike
from pathlib import Path
import re
from typing import TypeVar


_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_EnumValue = TypeVar("_EnumValue", bound=Enum)


def _enum_value(enum_type: type[_EnumValue], value: _EnumValue | str, field: str) -> _EnumValue:
    try:
        return value if isinstance(value, enum_type) else enum_type(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{field} is invalid") from error


def _nonblank(value: str, field: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a nonblank string")


def _paper_id(value: str) -> None:
    if not isinstance(value, str) or value == "":
        raise ValueError("paper_id must be a nonempty string")


def _sha256(value: str | None, field: str, *, required: bool = False) -> None:
    if value is None:
        if required:
            raise ValueError(f"{field} is required")
        return
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise ValueError(f"{field} must be a lowercase SHA-256 digest")


def _utc(value: datetime | None, field: str) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field} must be timezone-aware")
    return value.astimezone(timezone.utc)


def _nonnegative(value: int | None, field: str) -> None:
    if value is not None and (not isinstance(value, int) or isinstance(value, bool) or value < 0):
        raise ValueError(f"{field} must be nonnegative")


class SourceMode(str, Enum):
    NATIVE = "native"
    OCR = "ocr"


NATIVE_SOURCE_PROVIDER = "local"
NATIVE_SOURCE_MODEL = "pymupdf4llm-pymupdf"


def is_frozen_native_source_identity(
    *,
    mode: SourceMode | str,
    provider: str,
    model: str,
) -> bool:
    try:
        source_mode = mode if isinstance(mode, SourceMode) else SourceMode(mode)
    except (TypeError, ValueError):
        return False
    return bool(
        source_mode is SourceMode.NATIVE
        and provider == NATIVE_SOURCE_PROVIDER
        and model == NATIVE_SOURCE_MODEL
    )


def has_frozen_native_source_identity(source: object) -> bool:
    return is_frozen_native_source_identity(
        mode=getattr(source, "mode", ""),
        provider=getattr(source, "provider", ""),
        model=getattr(source, "model", ""),
    )


class SourceDocumentStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    READY = "ready"
    FAILED = "failed"
    STALE = "stale"
    CANCELLED = "cancelled"


class ArtifactKind(str, Enum):
    EXPLAINER = "explainer"
    TRANSLATION = "translation"
    SUMMARY = "summary"
    OUTLINE = "outline"
    STUDY_CARD = "study_card"
    CLASSIFICATION = "classification"
    METADATA = "metadata"


class ProcessingJobType(str, Enum):
    SOURCE_MATERIALIZE = "source_materialize"
    OCR = "ocr"
    EXPLAIN = "explain"
    TRANSLATE = "translate"
    EMBED = "embed"
    OBSIDIAN_EXPORT = "obsidian_export"
    OBSIDIAN_SYNC = "obsidian_sync"


class ProcessingJobStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class CredentialKind(str, Enum):
    LLM = "llm"
    OCR = "ocr"
    EMBEDDING = "embedding"
    SEMANTIC_SCHOLAR = "semantic_scholar"


@dataclass(frozen=True, slots=True)
class SourceCacheIdentity:
    paper_id: str
    pdf_sha256: str
    mode: SourceMode | str
    provider: str
    model: str
    options_hash: str
    processing_version: str

    def __post_init__(self) -> None:
        _paper_id(self.paper_id)
        _sha256(self.pdf_sha256, "pdf_sha256", required=True)
        _sha256(self.options_hash, "options_hash", required=True)
        object.__setattr__(self, "mode", _enum_value(SourceMode, self.mode, "mode"))
        for field in ("provider", "model", "processing_version"):
            _nonblank(getattr(self, field), field)

    @classmethod
    def from_document(cls, document: "SourceDocument") -> "SourceCacheIdentity":
        return cls(
            paper_id=document.paper_id,
            pdf_sha256=document.pdf_sha256,
            mode=document.mode,
            provider=document.provider,
            model=document.model,
            options_hash=document.options_hash,
            processing_version=document.processing_version,
        )


@dataclass(frozen=True, slots=True)
class ArtifactVersionIdentity:
    source_document_id: str
    kind: ArtifactKind | str
    generator_provider: str
    generator_model: str
    prompt_version: str

    def __post_init__(self) -> None:
        _nonblank(self.source_document_id, "source_document_id")
        object.__setattr__(self, "kind", _enum_value(ArtifactKind, self.kind, "kind"))
        for field in ("generator_provider", "generator_model", "prompt_version"):
            _nonblank(getattr(self, field), field)

    @classmethod
    def from_artifact(cls, artifact: "GeneratedArtifact") -> "ArtifactVersionIdentity":
        return cls(
            source_document_id=artifact.source_document_id,
            kind=artifact.kind,
            generator_provider=artifact.generator_provider,
            generator_model=artifact.generator_model,
            prompt_version=artifact.prompt_version,
        )


@dataclass(frozen=True, slots=True)
class Paper:
    id: str
    title: str = ""
    authors: tuple[str, ...] = ()
    abstract: str | None = None
    pdf_path: Path | None = None

    def __post_init__(self) -> None:
        _paper_id(self.id)
        if self.pdf_path is not None:
            object.__setattr__(self, "pdf_path", Path(self.pdf_path))


@dataclass(frozen=True, slots=True)
class SourceDocument:
    id: str
    paper_id: str
    mode: SourceMode | str
    status: SourceDocumentStatus | str
    provider: str
    model: str
    pdf_sha256: str
    options_hash: str
    processing_version: str
    created_at: datetime
    updated_at: datetime
    content_sha256: str | None = None
    markdown: str | None = None
    page_count: int | None = None
    error_code: str | None = None
    error_message: str | None = None

    def __post_init__(self) -> None:
        _nonblank(self.id, "id")
        _paper_id(self.paper_id)
        object.__setattr__(self, "mode", _enum_value(SourceMode, self.mode, "mode"))
        object.__setattr__(
            self,
            "status",
            _enum_value(SourceDocumentStatus, self.status, "status"),
        )
        for field, value in (
            ("provider", self.provider),
            ("model", self.model),
            ("processing_version", self.processing_version),
        ):
            _nonblank(value, field)
        _sha256(self.pdf_sha256, "pdf_sha256", required=True)
        _sha256(self.options_hash, "options_hash", required=True)
        _sha256(self.content_sha256, "content_sha256")
        _nonnegative(self.page_count, "page_count")
        object.__setattr__(self, "created_at", _utc(self.created_at, "created_at"))
        object.__setattr__(self, "updated_at", _utc(self.updated_at, "updated_at"))
        if self.status is SourceDocumentStatus.READY:
            _nonblank(self.markdown or "", "markdown")
            _sha256(self.content_sha256, "content_sha256", required=True)
        if self.status is SourceDocumentStatus.FAILED:
            _nonblank(self.error_code or "", "error_code")


@dataclass(frozen=True, slots=True)
class GeneratedArtifact:
    id: str
    paper_id: str
    kind: ArtifactKind | str
    source_document_id: str
    status: SourceDocumentStatus | str
    generator_provider: str
    generator_model: str
    prompt_version: str
    created_at: datetime
    updated_at: datetime
    content: str | None = None
    content_sha256: str | None = None
    error_code: str | None = None
    error_message: str | None = None

    def __post_init__(self) -> None:
        _nonblank(self.id, "id")
        _paper_id(self.paper_id)
        _nonblank(self.source_document_id, "source_document_id")
        object.__setattr__(self, "kind", _enum_value(ArtifactKind, self.kind, "kind"))
        object.__setattr__(
            self,
            "status",
            _enum_value(SourceDocumentStatus, self.status, "status"),
        )
        for field, value in (
            ("generator_provider", self.generator_provider),
            ("generator_model", self.generator_model),
            ("prompt_version", self.prompt_version),
        ):
            _nonblank(value, field)
        _sha256(self.content_sha256, "content_sha256")
        object.__setattr__(self, "created_at", _utc(self.created_at, "created_at"))
        object.__setattr__(self, "updated_at", _utc(self.updated_at, "updated_at"))
        if self.status is SourceDocumentStatus.READY:
            _nonblank(self.content or "", "content")
            _sha256(self.content_sha256, "content_sha256", required=True)
        if self.status is SourceDocumentStatus.FAILED:
            _nonblank(self.error_code or "", "error_code")


@dataclass(frozen=True, slots=True)
class ProcessingJob:
    id: str
    job_type: ProcessingJobType | str
    status: ProcessingJobStatus | str
    idempotency_key: str
    created_at: datetime
    paper_id: str | None = None
    source_mode: SourceMode | str | None = None
    progress_json: str = "{}"
    attempt: int = 0
    max_attempts: int = 1
    error_code: str | None = None
    error_message: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    cancelled_at: datetime | None = None

    def __post_init__(self) -> None:
        _nonblank(self.id, "id")
        _nonblank(self.idempotency_key, "idempotency_key")
        _nonblank(self.progress_json, "progress_json")
        object.__setattr__(
            self,
            "job_type",
            _enum_value(ProcessingJobType, self.job_type, "job_type"),
        )
        object.__setattr__(
            self,
            "status",
            _enum_value(ProcessingJobStatus, self.status, "status"),
        )
        if self.source_mode is not None:
            object.__setattr__(
                self,
                "source_mode",
                _enum_value(SourceMode, self.source_mode, "source_mode"),
            )
        _nonnegative(self.attempt, "attempt")
        if not isinstance(self.max_attempts, int) or isinstance(self.max_attempts, bool) or self.max_attempts < 1:
            raise ValueError("max_attempts must be at least one")
        object.__setattr__(self, "created_at", _utc(self.created_at, "created_at"))
        for field in ("started_at", "finished_at", "cancelled_at"):
            object.__setattr__(self, field, _utc(getattr(self, field), field))

        source_jobs = {
            ProcessingJobType.SOURCE_MATERIALIZE,
            ProcessingJobType.OCR,
            ProcessingJobType.EXPLAIN,
            ProcessingJobType.TRANSLATE,
            ProcessingJobType.EMBED,
        }
        if self.job_type in source_jobs:
            if self.paper_id is None or self.source_mode is None:
                raise ValueError("paper_id and source_mode are required for this job type")
            _paper_id(self.paper_id)
        elif self.job_type is ProcessingJobType.OBSIDIAN_EXPORT:
            if self.paper_id is None:
                raise ValueError("paper_id is required for obsidian_export")
            _paper_id(self.paper_id)
        elif self.paper_id is not None:
            _paper_id(self.paper_id)

        if self.job_type is ProcessingJobType.SOURCE_MATERIALIZE and self.source_mode is not SourceMode.NATIVE:
            raise ValueError("source_materialize requires native source mode")
        if self.job_type is ProcessingJobType.OCR and self.source_mode is not SourceMode.OCR:
            raise ValueError("ocr requires OCR source mode")


@dataclass(frozen=True, slots=True)
class DocumentChunk:
    id: str
    source_document_id: str
    sequence: int
    heading_path: str | None
    page_start: int | None
    page_end: int | None
    content: str
    content_sha256: str
    token_count: int
    # P3 additive coverage metadata.  These remain optional for P1 legacy
    # callers; ChunkSet/migration seams validate them when present.
    status: str | None = None
    content_kind: str | None = None
    chunk_key: str | None = None
    chunking_version: str | None = None
    source_content_sha256: str | None = None
    char_start: int | None = None
    char_end: int | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    stale_at: datetime | None = None

    def __post_init__(self) -> None:
        _nonblank(self.id, "id")
        _nonblank(self.source_document_id, "source_document_id")
        _nonnegative(self.sequence, "sequence")
        _nonnegative(self.page_start, "page_start")
        _nonnegative(self.page_end, "page_end")
        _nonnegative(self.token_count, "token_count")
        if self.page_start is not None and self.page_end is not None and self.page_end < self.page_start:
            raise ValueError("page_end cannot precede page_start")
        _sha256(self.content_sha256, "content_sha256", required=True)
        if self.status is not None and self.status not in {"ready", "stale"}:
            raise ValueError("status is invalid")
        if self.content_kind is not None and self.content_kind not in {"text", "verbatim", "structured"}:
            raise ValueError("content_kind is invalid")
        if self.chunk_key is not None:
            _nonblank(self.chunk_key, "chunk_key")
        if self.chunking_version is not None:
            _nonblank(self.chunking_version, "chunking_version")
        if self.source_content_sha256 is not None:
            _sha256(self.source_content_sha256, "source_content_sha256")
        _nonnegative(self.char_start, "char_start")
        _nonnegative(self.char_end, "char_end")
        if self.char_start is not None and self.char_end is not None and self.char_end < self.char_start:
            raise ValueError("char_end cannot precede char_start")
        for field in ("created_at", "updated_at", "stale_at"):
            object.__setattr__(self, field, _utc(getattr(self, field), field))


@dataclass(frozen=True, slots=True)
class VaultProjection:
    id: str
    paper_id: str
    artifact_id: str | None
    target_path: str
    source_hash: str | None
    exported_hash: str | None
    status: str
    exported_at: datetime | None = None
    error_message: str | None = None

    def __post_init__(self) -> None:
        _nonblank(self.id, "id")
        _paper_id(self.paper_id)
        _nonblank(self.target_path, "target_path")
        _nonblank(self.status, "status")
        if self.artifact_id is not None:
            _nonblank(self.artifact_id, "artifact_id")
        _sha256(self.source_hash, "source_hash")
        _sha256(self.exported_hash, "exported_hash")
        object.__setattr__(self, "exported_at", _utc(self.exported_at, "exported_at"))


@dataclass(frozen=True, slots=True)
class ProviderProfile:
    provider: str
    model: str
    base_url: str | None = None

    def __post_init__(self) -> None:
        _nonblank(self.provider, "provider")
        _nonblank(self.model, "model")


@dataclass(frozen=True, slots=True)
class ProviderRuntimeConfig:
    """Request-time non-secret provider settings resolved by the application seam."""

    provider: str
    model: str
    base_url: str | None = None
    timeout_seconds: float | None = None

    def __post_init__(self) -> None:
        _nonblank(self.provider, "provider")
        _nonblank(self.model, "model")
        if self.base_url is not None:
            _nonblank(self.base_url, "base_url")
        if self.timeout_seconds is not None and self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive when provided")


@dataclass(frozen=True, slots=True, repr=False)
class Credential:
    kind: CredentialKind | str
    value: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "kind", _enum_value(CredentialKind, self.kind, "kind"))
        _nonblank(self.value, "value")

    def __repr__(self) -> str:
        return f"Credential(kind={self.kind.value!r}, value=<redacted>)"

    __str__ = __repr__


@dataclass(frozen=True, slots=True)
class CredentialStatus:
    kind: CredentialKind | str
    has_key: bool
    key_tail: str | None
    environment_managed: bool

    def __post_init__(self) -> None:
        object.__setattr__(self, "kind", _enum_value(CredentialKind, self.kind, "kind"))
        if self.key_tail is not None and not self.key_tail.startswith("****"):
            raise ValueError("key_tail must be redacted")

    def to_dict(self) -> dict[str, object]:
        return {
            "kind": self.kind.value,
            "hasKey": self.has_key,
            "keyTail": self.key_tail,
            "environmentManaged": self.environment_managed,
        }
