"""Add the empty P1 domain and data foundation tables."""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20260807_01"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


LOWER_SHA = "length({0}) = 64 AND {0} NOT GLOB '*[^0-9a-f]*'"


def upgrade() -> None:
    op.create_table(
        "document_sources",
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("paper_id", sa.Text(), nullable=False),
        sa.Column("mode", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("model", sa.Text(), nullable=False),
        sa.Column("pdf_sha256", sa.Text(), nullable=False),
        sa.Column("options_hash", sa.Text(), nullable=False),
        sa.Column("content_sha256", sa.Text(), nullable=True),
        sa.Column("markdown", sa.Text(), nullable=True),
        sa.Column("page_count", sa.Integer(), nullable=True),
        sa.Column("processing_version", sa.Text(), nullable=False),
        sa.Column("error_code", sa.Text(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.Text(), nullable=False, server_default=sa.text("(datetime('now'))")),
        sa.Column("updated_at", sa.Text(), nullable=False, server_default=sa.text("(datetime('now'))")),
        sa.PrimaryKeyConstraint("id", name="pk_document_sources"),
        sa.ForeignKeyConstraint(["paper_id"], ["papers.id"], ondelete="CASCADE", name="fk_document_sources_paper"),
        sa.UniqueConstraint(
            "paper_id", "pdf_sha256", "mode", "provider", "model", "options_hash", "processing_version",
            name="uq_document_sources_cache",
        ),
        sa.CheckConstraint("mode IN ('native','ocr')", name="ck_document_sources_mode"),
        sa.CheckConstraint(
            "status IN ('queued','running','ready','failed','stale','cancelled')",
            name="ck_document_sources_status",
        ),
        sa.CheckConstraint("length(trim(provider)) > 0", name="ck_document_sources_provider_nonblank"),
        sa.CheckConstraint("length(trim(model)) > 0", name="ck_document_sources_model_nonblank"),
        sa.CheckConstraint("length(trim(processing_version)) > 0", name="ck_document_sources_version_nonblank"),
        sa.CheckConstraint(LOWER_SHA.format("pdf_sha256"), name="ck_document_sources_pdf_sha"),
        sa.CheckConstraint(LOWER_SHA.format("options_hash"), name="ck_document_sources_options_sha"),
        sa.CheckConstraint(
            f"content_sha256 IS NULL OR ({LOWER_SHA.format('content_sha256')})",
            name="ck_document_sources_content_sha",
        ),
        sa.CheckConstraint("page_count IS NULL OR page_count >= 0", name="ck_document_sources_page_count"),
        sa.CheckConstraint(
            "status <> 'ready' OR (markdown IS NOT NULL AND length(trim(markdown)) > 0 "
            "AND content_sha256 IS NOT NULL)",
            name="ck_document_sources_ready_payload",
        ),
        sa.CheckConstraint(
            "status <> 'failed' OR (error_code IS NOT NULL AND length(trim(error_code)) > 0)",
            name="ck_document_sources_failed_error",
        ),
    )
    op.create_index(
        "ix_document_sources_paper_status",
        "document_sources",
        ["paper_id", "status", "updated_at"],
    )

    op.create_table(
        "generated_artifacts",
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("paper_id", sa.Text(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("source_document_id", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("content", sa.Text(), nullable=True),
        sa.Column("content_sha256", sa.Text(), nullable=True),
        sa.Column("generator_provider", sa.Text(), nullable=False),
        sa.Column("generator_model", sa.Text(), nullable=False),
        sa.Column("prompt_version", sa.Text(), nullable=False),
        sa.Column("error_code", sa.Text(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.Text(), nullable=False, server_default=sa.text("(datetime('now'))")),
        sa.Column("updated_at", sa.Text(), nullable=False, server_default=sa.text("(datetime('now'))")),
        sa.PrimaryKeyConstraint("id", name="pk_generated_artifacts"),
        sa.ForeignKeyConstraint(["paper_id"], ["papers.id"], ondelete="CASCADE", name="fk_generated_artifacts_paper"),
        sa.ForeignKeyConstraint(
            ["source_document_id"], ["document_sources.id"], ondelete="CASCADE",
            name="fk_generated_artifacts_source",
        ),
        sa.UniqueConstraint(
            "source_document_id", "kind", "generator_provider", "generator_model", "prompt_version",
            name="uq_generated_artifacts_version",
        ),
        sa.CheckConstraint("length(trim(kind)) > 0", name="ck_generated_artifacts_kind_nonblank"),
        sa.CheckConstraint(
            "status IN ('queued','running','ready','failed','stale','cancelled')",
            name="ck_generated_artifacts_status",
        ),
        sa.CheckConstraint("length(trim(generator_provider)) > 0", name="ck_generated_artifacts_provider_nonblank"),
        sa.CheckConstraint("length(trim(generator_model)) > 0", name="ck_generated_artifacts_model_nonblank"),
        sa.CheckConstraint("length(trim(prompt_version)) > 0", name="ck_generated_artifacts_prompt_nonblank"),
        sa.CheckConstraint(
            f"content_sha256 IS NULL OR ({LOWER_SHA.format('content_sha256')})",
            name="ck_generated_artifacts_content_sha",
        ),
        sa.CheckConstraint(
            "status <> 'ready' OR (content IS NOT NULL AND length(trim(content)) > 0 "
            "AND content_sha256 IS NOT NULL)",
            name="ck_generated_artifacts_ready_payload",
        ),
        sa.CheckConstraint(
            "status <> 'failed' OR (error_code IS NOT NULL AND length(trim(error_code)) > 0)",
            name="ck_generated_artifacts_failed_error",
        ),
    )
    op.create_index(
        "ix_generated_artifacts_paper_kind_status",
        "generated_artifacts",
        ["paper_id", "kind", "status", "updated_at"],
    )
    op.create_index("ix_generated_artifacts_source", "generated_artifacts", ["source_document_id"])

    op.create_table(
        "processing_jobs",
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("paper_id", sa.Text(), nullable=True),
        sa.Column("job_type", sa.Text(), nullable=False),
        sa.Column("source_mode", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("progress_json", sa.Text(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("attempt", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column("idempotency_key", sa.Text(), nullable=False),
        sa.Column("error_code", sa.Text(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.Text(), nullable=False, server_default=sa.text("(datetime('now'))")),
        sa.Column("started_at", sa.Text(), nullable=True),
        sa.Column("finished_at", sa.Text(), nullable=True),
        sa.Column("cancelled_at", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_processing_jobs"),
        sa.ForeignKeyConstraint(["paper_id"], ["papers.id"], ondelete="CASCADE", name="fk_processing_jobs_paper"),
        sa.UniqueConstraint("idempotency_key", name="uq_processing_jobs_idempotency_key"),
        sa.CheckConstraint(
            "job_type IN ('source_materialize','ocr','explain','translate','embed','obsidian_export','obsidian_sync')",
            name="ck_processing_jobs_type",
        ),
        sa.CheckConstraint("source_mode IS NULL OR source_mode IN ('native','ocr')", name="ck_processing_jobs_source_mode"),
        sa.CheckConstraint(
            "status IN ('queued','running','succeeded','failed','cancelled')",
            name="ck_processing_jobs_status",
        ),
        sa.CheckConstraint(
            "((job_type IN ('source_materialize','ocr','explain','translate','embed') "
            "AND paper_id IS NOT NULL AND source_mode IS NOT NULL) "
            "OR (job_type = 'obsidian_export' AND paper_id IS NOT NULL) OR job_type = 'obsidian_sync')",
            name="ck_processing_jobs_scope",
        ),
        sa.CheckConstraint(
            "job_type <> 'source_materialize' OR source_mode = 'native'",
            name="ck_processing_jobs_native_pairing",
        ),
        sa.CheckConstraint("job_type <> 'ocr' OR source_mode = 'ocr'", name="ck_processing_jobs_ocr_pairing"),
        sa.CheckConstraint("attempt >= 0", name="ck_processing_jobs_attempt"),
        sa.CheckConstraint("max_attempts >= 1", name="ck_processing_jobs_max_attempts"),
        sa.CheckConstraint("length(trim(idempotency_key)) > 0", name="ck_processing_jobs_key_nonblank"),
    )
    op.create_index("ix_processing_jobs_status_created", "processing_jobs", ["status", "created_at"])
    op.create_index("ix_processing_jobs_paper_created", "processing_jobs", ["paper_id", "created_at"])

    op.create_table(
        "document_chunks",
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("source_document_id", sa.Text(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("heading_path", sa.Text(), nullable=True),
        sa.Column("page_start", sa.Integer(), nullable=True),
        sa.Column("page_end", sa.Integer(), nullable=True),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("content_sha256", sa.Text(), nullable=False),
        sa.Column("token_count", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_document_chunks"),
        sa.ForeignKeyConstraint(
            ["source_document_id"], ["document_sources.id"], ondelete="CASCADE",
            name="fk_document_chunks_source",
        ),
        sa.UniqueConstraint("source_document_id", "sequence", name="uq_document_chunks_source_sequence"),
        sa.CheckConstraint("sequence >= 0", name="ck_document_chunks_sequence"),
        sa.CheckConstraint("token_count >= 0", name="ck_document_chunks_token_count"),
        sa.CheckConstraint("page_start IS NULL OR page_start >= 0", name="ck_document_chunks_page_start"),
        sa.CheckConstraint("page_end IS NULL OR page_end >= 0", name="ck_document_chunks_page_end"),
        sa.CheckConstraint(
            "page_start IS NULL OR page_end IS NULL OR page_end >= page_start",
            name="ck_document_chunks_page_order",
        ),
        sa.CheckConstraint(LOWER_SHA.format("content_sha256"), name="ck_document_chunks_content_sha"),
    )
    op.create_index("ix_document_chunks_source", "document_chunks", ["source_document_id"])

    op.create_table(
        "obsidian_exports",
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("paper_id", sa.Text(), nullable=False),
        sa.Column("artifact_id", sa.Text(), nullable=True),
        sa.Column("target_path", sa.Text(), nullable=False),
        sa.Column("source_hash", sa.Text(), nullable=True),
        sa.Column("exported_hash", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("exported_at", sa.Text(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_obsidian_exports"),
        sa.ForeignKeyConstraint(["paper_id"], ["papers.id"], ondelete="CASCADE", name="fk_obsidian_exports_paper"),
        sa.ForeignKeyConstraint(
            ["artifact_id"], ["generated_artifacts.id"], ondelete="SET NULL",
            name="fk_obsidian_exports_artifact",
        ),
        sa.UniqueConstraint("target_path", name="uq_obsidian_exports_target_path"),
        sa.CheckConstraint("length(trim(target_path)) > 0", name="ck_obsidian_exports_target_nonblank"),
        sa.CheckConstraint("length(trim(status)) > 0", name="ck_obsidian_exports_status_nonblank"),
        sa.CheckConstraint(
            f"source_hash IS NULL OR ({LOWER_SHA.format('source_hash')})",
            name="ck_obsidian_exports_source_sha",
        ),
        sa.CheckConstraint(
            f"exported_hash IS NULL OR ({LOWER_SHA.format('exported_hash')})",
            name="ck_obsidian_exports_exported_sha",
        ),
    )
    op.create_index("ix_obsidian_exports_paper_status", "obsidian_exports", ["paper_id", "status"])
    op.create_index("ix_obsidian_exports_artifact", "obsidian_exports", ["artifact_id"])


def downgrade() -> None:
    connection = op.get_bind()
    drop_order = (
        "obsidian_exports",
        "document_chunks",
        "processing_jobs",
        "generated_artifacts",
        "document_sources",
    )
    nonempty = [
        table_name
        for table_name in drop_order
        if connection.execute(sa.text(f'SELECT count(*) FROM "{table_name}"')).scalar_one() != 0
    ]
    if nonempty:
        names = ", ".join(nonempty)
        raise RuntimeError(
            "P1_DOWNGRADE_NONEMPTY: P1 tables contain data: "
            f"{names}. Use runtime rollback and verified backup recovery."
        )
    for table_name in drop_order:
        op.drop_table(table_name)
