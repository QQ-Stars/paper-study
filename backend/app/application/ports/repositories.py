from __future__ import annotations

from datetime import datetime
from typing import Protocol

from backend.app.domain import (
    ArtifactKind,
    ArtifactVersionIdentity,
    GeneratedArtifact,
    Paper,
    ProcessingJob,
    SourceCacheIdentity,
    SourceDocument,
    VaultProjection,
)
from backend.app.domain.context import (
    ChunkSet,
    EmbeddingProfile,
    SearchCoverage,
    SearchHit,
    SearchRequest,
)
from backend.app.domain.entities import DocumentChunk


class PaperRepository(Protocol):
    async def get(self, paper_id: str) -> Paper | None: ...
    async def exists(self, paper_id: str) -> bool: ...
    async def list_legacy(self) -> list[dict[str, object]]: ...
    async def get_legacy(self, paper_id: str) -> dict[str, object] | None: ...
    async def citation_graph_records(
        self,
    ) -> tuple[list[dict[str, object]], list[dict[str, object]]]: ...
    async def add_legacy(self, values: dict[str, object]) -> None: ...
    async def update_legacy(
        self, paper_id: str, fields: dict[str, object], *, updated_at: str
    ) -> int: ...
    async def set_status(self, paper_id: str, status: object, *, updated_at: str) -> None: ...
    async def ensure_review_plan(
        self,
        paper_id: str,
        *,
        started_at: str,
        next_due_at: str,
        updated_at: str,
    ) -> None: ...
    async def get_review_plan(self, paper_id: str) -> dict[str, object] | None: ...
    async def complete_review_step(
        self,
        paper_id: str,
        *,
        completed_steps: int,
        current_step: int,
        next_due_at: str,
        completed_at: str | None,
        updated_at: str,
    ) -> dict[str, object] | None: ...
    async def list_review_items(self) -> list[dict[str, object]]: ...
    async def get_note(self, paper_id: str) -> str | None: ...
    async def set_note(self, paper_id: str, content: str, *, updated_at: str) -> None: ...
    async def list_missing_title_translations(self) -> list[dict[str, object]]: ...
    async def list_missing_explainers(self) -> list[dict[str, object]]: ...
    async def set_favorite(
        self, paper_id: str, favorite: bool, *, created_at: str
    ) -> None: ...
    async def delete_legacy(self, paper_id: str) -> int: ...


class SourceDocumentRepository(Protocol):
    async def get(self, identifier: str) -> SourceDocument | None: ...
    async def find_by_cache_identity(self, identity: SourceCacheIdentity) -> SourceDocument | None: ...
    async def add(self, document: SourceDocument) -> None: ...
    async def publish_ready(
        self, identifier: str, expected_status: str, markdown: str,
        content_sha256: str, page_count: int, updated_at: datetime,
    ) -> bool: ...
    async def publish_failed(
        self, identifier: str, expected_status: str, error_code: str,
        error_message: str | None, updated_at: datetime,
    ) -> bool: ...
    async def publish_stale(
        self, identifier: str, expected_status: str, error_code: str, updated_at: datetime,
    ) -> bool: ...
    async def stale_for_pdf_change(
        self, paper_id: str, current_pdf_sha256: str, *, now: datetime,
    ) -> object: ...
    async def stale_for_active_source(
        self, source_document_id: str, *, now: datetime,
    ) -> object: ...


class GeneratedArtifactRepository(Protocol):
    async def get(self, identifier: str) -> GeneratedArtifact | None: ...
    async def find_by_version_identity(
        self, identity: ArtifactVersionIdentity,
    ) -> GeneratedArtifact | None: ...
    async def find_ready_for_paper(
        self, paper_id: str, kind: ArtifactKind,
    ) -> GeneratedArtifact | None: ...
    async def add(self, artifact: GeneratedArtifact) -> None: ...
    async def publish_ready(
        self, identifier: str, expected_status: str, content: str,
        content_sha256: str, updated_at: datetime,
    ) -> bool: ...
    async def publish_failed(
        self, identifier: str, expected_status: str, error_code: str,
        error_message: str | None, updated_at: datetime,
    ) -> bool: ...
    async def write_legacy_explainer(
        self, paper_id: str, content: str, updated_at: datetime,
    ) -> None: ...
    async def write_legacy_translation(
        self, paper_id: str, content: str, updated_at: datetime,
    ) -> None: ...
    async def read_legacy(self, paper_id: str, kind: ArtifactKind) -> str | None: ...


class ProcessingJobRepository(Protocol):
    async def get(self, identifier: str) -> ProcessingJob | None: ...
    async def add(self, job: ProcessingJob) -> None: ...


class VaultProjectionRepository(Protocol):
    async def get(self, identifier: str) -> VaultProjection | None: ...
    async def find_by_target_path(self, path: str) -> VaultProjection | None: ...
    async def add(self, projection: VaultProjection) -> None: ...


class DocumentChunkCommandRepository(Protocol):
    async def insert_set(self, chunk_set: ChunkSet) -> tuple[DocumentChunk, ...]: ...
    async def stale_other_versions(
        self,
        source_document_id: str,
        active_chunking_version: str,
        *,
        now: datetime,
    ) -> int: ...


class DocumentChunkQueryRepository(Protocol):
    async def list_for_source(
        self,
        source_document_id: str,
        *,
        status: str | None = None,
        chunking_version: str | None = None,
    ) -> tuple[DocumentChunk, ...]: ...


class DocumentSearchQueryRepository(Protocol):
    async def lexical(self, request: SearchRequest) -> tuple[SearchHit, ...]: ...
    async def semantic_candidates(
        self,
        request: SearchRequest,
        profile: EmbeddingProfile,
    ) -> tuple[SearchHit, ...]: ...
    async def coverage(
        self,
        source_document_id: str,
        profile: EmbeddingProfile | None = None,
    ) -> SearchCoverage: ...


class TranslationCheckpointRepository(Protocol):
    async def get(self, artifact_id: str, chunk_id: str) -> object | None: ...
    async def list_succeeded(self, artifact_id: str) -> tuple[object, ...]: ...
    async def save_success(self, checkpoint: object) -> bool: ...
    async def save_failure(self, checkpoint: object) -> bool: ...
