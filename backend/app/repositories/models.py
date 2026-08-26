from __future__ import annotations

from sqlalchemy import Integer, LargeBinary, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class PaperModel(Base):
    __tablename__ = "papers"
    id: Mapped[str] = mapped_column(Text, primary_key=True)
    arxiv_id: Mapped[str | None] = mapped_column(Text)
    doi: Mapped[str | None] = mapped_column(Text)
    title: Mapped[str] = mapped_column(Text)
    title_zh: Mapped[str | None] = mapped_column(Text)
    authors: Mapped[str | None] = mapped_column(Text)
    abstract: Mapped[str | None] = mapped_column(Text)
    venue: Mapped[str | None] = mapped_column(Text)
    year: Mapped[str | None] = mapped_column(Text)
    tldr: Mapped[str | None] = mapped_column(Text)
    type: Mapped[str | None] = mapped_column(Text)
    topic: Mapped[str | None] = mapped_column(Text)
    task: Mapped[str | None] = mapped_column(Text)
    models: Mapped[str | None] = mapped_column(Text)
    datasets: Mapped[str | None] = mapped_column(Text)
    contribution: Mapped[str | None] = mapped_column(Text)
    tags: Mapped[str | None] = mapped_column(Text)
    relevance: Mapped[float | None] = mapped_column()
    pdf_path: Mapped[str | None] = mapped_column(Text)
    explainer: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[str | None] = mapped_column(Text)


class TranslationModel(Base):
    __tablename__ = "translations"
    paper_id: Mapped[str] = mapped_column(Text, primary_key=True)
    content: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[str | None] = mapped_column(Text)


class PaperVectorModel(Base):
    __tablename__ = "paper_vectors"
    paper_id: Mapped[str] = mapped_column(Text, primary_key=True)
    dim: Mapped[int | None] = mapped_column(Integer)
    vector: Mapped[bytes | None] = mapped_column(LargeBinary)


class SourceDocumentModel(Base):
    __tablename__ = "document_sources"
    id: Mapped[str] = mapped_column(Text, primary_key=True)
    paper_id: Mapped[str] = mapped_column(Text)
    mode: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text)
    provider: Mapped[str] = mapped_column(Text)
    model: Mapped[str] = mapped_column(Text)
    pdf_sha256: Mapped[str] = mapped_column(String(64))
    options_hash: Mapped[str] = mapped_column(String(64))
    content_sha256: Mapped[str | None] = mapped_column(String(64))
    markdown: Mapped[str | None] = mapped_column(Text)
    page_count: Mapped[int | None] = mapped_column(Integer)
    processing_version: Mapped[str] = mapped_column(Text)
    error_code: Mapped[str | None] = mapped_column(Text)
    error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[str] = mapped_column(Text)
    source_key: Mapped[str | None] = mapped_column(Text)
    ready_at: Mapped[str | None] = mapped_column(Text)
    stale_at: Mapped[str | None] = mapped_column(Text)


class GeneratedArtifactModel(Base):
    __tablename__ = "generated_artifacts"
    id: Mapped[str] = mapped_column(Text, primary_key=True)
    paper_id: Mapped[str] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(Text)
    source_document_id: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text)
    content: Mapped[str | None] = mapped_column(Text)
    content_sha256: Mapped[str | None] = mapped_column(String(64))
    generator_provider: Mapped[str] = mapped_column(Text)
    generator_model: Mapped[str] = mapped_column(Text)
    prompt_version: Mapped[str] = mapped_column(Text)
    error_code: Mapped[str | None] = mapped_column(Text)
    error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[str] = mapped_column(Text)
    artifact_key: Mapped[str | None] = mapped_column(Text)
    ready_at: Mapped[str | None] = mapped_column(Text)
    stale_at: Mapped[str | None] = mapped_column(Text)


class ProcessingJobModel(Base):
    __tablename__ = "processing_jobs"
    id: Mapped[str] = mapped_column(Text, primary_key=True)
    paper_id: Mapped[str | None] = mapped_column(Text)
    job_type: Mapped[str] = mapped_column(Text)
    source_mode: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text)
    progress_json: Mapped[str] = mapped_column(Text)
    attempt: Mapped[int] = mapped_column(Integer)
    max_attempts: Mapped[int] = mapped_column(Integer)
    idempotency_key: Mapped[str] = mapped_column(Text)
    error_code: Mapped[str | None] = mapped_column(Text)
    error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(Text)
    started_at: Mapped[str | None] = mapped_column(Text)
    finished_at: Mapped[str | None] = mapped_column(Text)
    cancelled_at: Mapped[str | None] = mapped_column(Text)
    source_document_id: Mapped[str | None] = mapped_column(Text)
    artifact_id: Mapped[str | None] = mapped_column(Text)
    spec_json: Mapped[str] = mapped_column(Text, nullable=False)
    available_at: Mapped[str] = mapped_column(Text, nullable=False)
    lease_owner: Mapped[str | None] = mapped_column(Text)
    lease_token: Mapped[str | None] = mapped_column(Text)
    lease_expires_at: Mapped[str | None] = mapped_column(Text)
    heartbeat_at: Mapped[str | None] = mapped_column(Text)
    cancel_requested_at: Mapped[str | None] = mapped_column(Text)
    result_json: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[str] = mapped_column(Text, nullable=False)
    retry_of_job_id: Mapped[str | None] = mapped_column(Text)
    retry_sequence: Mapped[int] = mapped_column(Integer)


class PaperArtifactHeadModel(Base):
    __tablename__ = "paper_artifact_heads"
    paper_id: Mapped[str] = mapped_column(Text, primary_key=True)
    kind: Mapped[str] = mapped_column(Text, primary_key=True)
    artifact_id: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[str] = mapped_column(Text)


class ProcessingJobEventModel(Base):
    __tablename__ = "processing_job_events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[str] = mapped_column(Text)
    sequence: Mapped[int] = mapped_column(Integer)
    event_type: Mapped[str] = mapped_column(Text)
    progress_json: Mapped[str] = mapped_column(Text)
    error_code: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(Text)


class OcrPageCheckpointModel(Base):
    __tablename__ = "ocr_page_checkpoints"
    source_document_id: Mapped[str] = mapped_column(Text, primary_key=True)
    page_number: Mapped[int] = mapped_column(Integer, primary_key=True)
    status: Mapped[str] = mapped_column(Text)
    markdown: Mapped[str | None] = mapped_column(Text)
    content_sha256: Mapped[str | None] = mapped_column(String(64))
    provider_page_id: Mapped[str | None] = mapped_column(Text)
    attempt: Mapped[int] = mapped_column(Integer)
    error_code: Mapped[str | None] = mapped_column(Text)
    error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[str] = mapped_column(Text)


class ArtifactTranslationCheckpointModel(Base):
    __tablename__ = "artifact_translation_checkpoints"
    artifact_id: Mapped[str] = mapped_column(Text, primary_key=True)
    chunk_id: Mapped[str] = mapped_column(Text)
    sequence: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_content_sha256: Mapped[str] = mapped_column(String(64))
    provider: Mapped[str] = mapped_column(Text)
    model: Mapped[str] = mapped_column(Text)
    prompt_version: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text)
    translated_markdown: Mapped[str | None] = mapped_column(Text)
    content_sha256: Mapped[str | None] = mapped_column(String(64))
    attempt: Mapped[int] = mapped_column(Integer)
    error_code: Mapped[str | None] = mapped_column(Text)
    error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[str] = mapped_column(Text)


class DocumentChunkModel(Base):
    __tablename__ = "document_chunks"
    id: Mapped[str] = mapped_column(Text, primary_key=True)
    source_document_id: Mapped[str] = mapped_column(Text)
    sequence: Mapped[int] = mapped_column(Integer)
    heading_path: Mapped[str | None] = mapped_column(Text)
    page_start: Mapped[int | None] = mapped_column(Integer)
    page_end: Mapped[int | None] = mapped_column(Integer)
    content: Mapped[str] = mapped_column(Text)
    content_sha256: Mapped[str] = mapped_column(String(64))
    token_count: Mapped[int] = mapped_column(Integer)
    status: Mapped[str | None] = mapped_column(Text)
    content_kind: Mapped[str | None] = mapped_column(Text)
    chunk_key: Mapped[str | None] = mapped_column(Text)
    chunking_version: Mapped[str | None] = mapped_column(Text)
    source_content_sha256: Mapped[str | None] = mapped_column(String(64))
    char_start: Mapped[int | None] = mapped_column(Integer)
    char_end: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[str | None] = mapped_column(Text)
    stale_at: Mapped[str | None] = mapped_column(Text)


class DocumentChunkEmbeddingModel(Base):
    __tablename__ = "document_chunk_embeddings"
    id: Mapped[str] = mapped_column(Text, primary_key=True)
    chunk_id: Mapped[str] = mapped_column(Text)
    source_document_id: Mapped[str] = mapped_column(Text)
    provider: Mapped[str] = mapped_column(Text)
    model: Mapped[str] = mapped_column(Text)
    embedding_version: Mapped[str] = mapped_column(Text)
    dimensions: Mapped[int] = mapped_column(Integer)
    vector: Mapped[bytes | None] = mapped_column(LargeBinary)
    vector_sha256: Mapped[str | None] = mapped_column(String(64))
    chunk_content_sha256: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(Text)
    error_code: Mapped[str | None] = mapped_column(Text)
    error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[str] = mapped_column(Text)
    stale_at: Mapped[str | None] = mapped_column(Text)


class VaultProjectionModel(Base):
    __tablename__ = "obsidian_exports"
    id: Mapped[str] = mapped_column(Text, primary_key=True)
    paper_id: Mapped[str] = mapped_column(Text)
    artifact_id: Mapped[str | None] = mapped_column(Text)
    target_path: Mapped[str] = mapped_column(Text)
    source_hash: Mapped[str | None] = mapped_column(String(64))
    exported_hash: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(Text)
    exported_at: Mapped[str | None] = mapped_column(Text)
    error_message: Mapped[str | None] = mapped_column(Text)


class ReproductionProjectModel(Base):
    __tablename__ = "reproduction_projects"
    id: Mapped[str] = mapped_column(Text, primary_key=True)
    paper_id: Mapped[str | None] = mapped_column(Text)
    paper_title: Mapped[str] = mapped_column(Text)
    name: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text)
    tags_json: Mapped[str] = mapped_column(Text)
    revision: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[str] = mapped_column(Text)


class ReproductionDocumentModel(Base):
    __tablename__ = "reproduction_documents"
    id: Mapped[str] = mapped_column(Text, primary_key=True)
    project_id: Mapped[str] = mapped_column(Text)
    content: Mapped[str] = mapped_column(Text)
    revision: Mapped[int] = mapped_column(Integer)
    save_status: Mapped[str] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[str] = mapped_column(Text)


class ExperimentRunModel(Base):
    __tablename__ = "experiment_runs"
    id: Mapped[str] = mapped_column(Text, primary_key=True)
    project_id: Mapped[str] = mapped_column(Text)
    name: Mapped[str | None] = mapped_column(Text)
    environment: Mapped[str | None] = mapped_column(Text)
    command: Mapped[str | None] = mapped_column(Text)
    parameters_json: Mapped[str] = mapped_column(Text)
    data_version: Mapped[str | None] = mapped_column(Text)
    code_revision: Mapped[str | None] = mapped_column(Text)
    seed: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(Text)
    metrics_json: Mapped[str] = mapped_column(Text)
    result_summary: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[str | None] = mapped_column(Text)
    finished_at: Mapped[str | None] = mapped_column(Text)
    runtime_versions: Mapped[str | None] = mapped_column(Text)
    dataset: Mapped[str | None] = mapped_column(Text)
    preprocessing: Mapped[str | None] = mapped_column(Text)
    repository_url: Mapped[str | None] = mapped_column(Text)
    config: Mapped[str | None] = mapped_column(Text)
    issues: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[str] = mapped_column(Text)


class ReproductionArtifactModel(Base):
    __tablename__ = "reproduction_artifacts"
    id: Mapped[str] = mapped_column(Text, primary_key=True)
    project_id: Mapped[str] = mapped_column(Text)
    run_id: Mapped[str | None] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(Text)
    filename: Mapped[str] = mapped_column(Text)
    storage_key: Mapped[str] = mapped_column(Text)
    mime_type: Mapped[str] = mapped_column(Text)
    size_bytes: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[str] = mapped_column(Text)


class ReproductionNoteModel(Base):
    __tablename__ = "reproduction_notes"
    id: Mapped[str] = mapped_column(Text, primary_key=True)
    project_id: Mapped[str] = mapped_column(Text)
    content: Mapped[str] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[str] = mapped_column(Text)


class ReproductionResultModel(Base):
    __tablename__ = "reproduction_results"
    id: Mapped[str] = mapped_column(Text, primary_key=True)
    project_id: Mapped[str] = mapped_column(Text)
    metric_name: Mapped[str] = mapped_column(Text)
    paper_value: Mapped[str | None] = mapped_column(Text)
    reproduction_value: Mapped[str | None] = mapped_column(Text)
    difference: Mapped[str | None] = mapped_column(Text)
    difference_percent: Mapped[str | None] = mapped_column(Text)
    dataset_settings: Mapped[str | None] = mapped_column(Text)
    source: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text)
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[str] = mapped_column(Text)
