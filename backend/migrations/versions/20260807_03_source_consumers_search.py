"""Add the P3 source-consumer, chunk-search, and embedding schema.

The revision is deliberately additive.  It refuses to touch a database unless
the complete P2 contract is present and the connected SQLite build proves the
FTS5 trigram features used by the public search seam.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import stat

from alembic import context, op
import sqlalchemy as sa


revision: str = "20260807_03"
down_revision: str | None = "20260807_02"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


P2_REQUIRED_COLUMNS: dict[str, set[str]] = {
    "document_sources": {
        "id", "paper_id", "mode", "status", "provider", "model", "pdf_sha256",
        "options_hash", "content_sha256", "markdown", "page_count", "processing_version",
        "error_code", "error_message", "created_at", "updated_at", "source_key",
        "ready_at", "stale_at",
    },
    "generated_artifacts": {
        "id", "paper_id", "kind", "source_document_id", "status", "content",
        "content_sha256", "generator_provider", "generator_model", "prompt_version",
        "error_code", "error_message", "created_at", "updated_at", "artifact_key",
        "ready_at", "stale_at",
    },
    "processing_jobs": {
        "id", "paper_id", "job_type", "source_mode", "status", "progress_json", "attempt",
        "max_attempts", "idempotency_key", "error_code", "error_message", "created_at",
        "started_at", "finished_at", "cancelled_at", "source_document_id", "artifact_id",
        "spec_json", "available_at", "lease_owner", "lease_token", "lease_expires_at",
        "heartbeat_at", "cancel_requested_at", "result_json", "updated_at", "retry_of_job_id",
        "retry_sequence",
    },
    "document_chunks": {
        "id", "source_document_id", "sequence", "heading_path", "page_start", "page_end",
        "content", "content_sha256", "token_count",
    },
    "obsidian_exports": {
        "id", "paper_id", "artifact_id", "target_path", "source_hash", "exported_hash",
        "status", "exported_at", "error_message",
    },
    "paper_artifact_heads": {"paper_id", "kind", "artifact_id", "updated_at"},
    "processing_job_events": {
        "id", "job_id", "sequence", "event_type", "progress_json", "error_code", "created_at",
    },
    "ocr_page_checkpoints": {
        "source_document_id", "page_number", "status", "markdown", "content_sha256",
        "provider_page_id", "attempt", "error_code", "error_message", "created_at", "updated_at",
    },
}

P3_CHUNK_COLUMNS = (
    "status",
    "content_kind",
    "chunk_key",
    "chunking_version",
    "source_content_sha256",
    "char_start",
    "char_end",
    "created_at",
    "updated_at",
    "stale_at",
)

_SHA = "length({0})=64 AND {0} NOT GLOB '*[^0-9a-f]*'"
_UTC = "{0} GLOB '????-??-??T??:??:??*Z'"
_FTS_TOKENIZER = "trigram case_sensitive 0 remove_diacritics 1"
_FALLBACK_FTS_TOKENIZER = "trigram case_sensitive 0"


def upgrade() -> None:
    connection = op.get_bind()
    _preflight_p2_schema(connection)
    tokenizer = _probe_fts5_trigram(connection)

    # SQLite cannot add a NOT NULL column to a populated table without a
    # default.  Columns are therefore added nullable, backfilled determinis-
    # tically, and the three FTS triggers enforce the write-time contract.
    for column in (
        sa.Column("status", sa.Text(), nullable=True),
        sa.Column("content_kind", sa.Text(), nullable=True),
        sa.Column("chunk_key", sa.Text(), nullable=True),
        sa.Column("chunking_version", sa.Text(), nullable=True),
        sa.Column("source_content_sha256", sa.Text(), nullable=True),
        sa.Column("char_start", sa.Integer(), nullable=True),
        sa.Column("char_end", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.Text(), nullable=True),
        sa.Column("updated_at", sa.Text(), nullable=True),
        sa.Column("stale_at", sa.Text(), nullable=True),
    ):
        op.add_column("document_chunks", column)
    _backfill_chunks(connection)

    op.create_index(
        "ux_document_chunks_chunk_key",
        "document_chunks",
        ["chunk_key"],
        unique=True,
        sqlite_where=sa.text("chunk_key IS NOT NULL"),
    )
    op.create_index(
        "ix_document_chunks_source_status_sequence",
        "document_chunks",
        ["source_document_id", "status", "sequence"],
    )

    _create_embedding_table()
    _create_checkpoint_table()
    _create_fts(connection, tokenizer)
    _validate_fts_coverage(connection)


def downgrade() -> None:
    connection = op.get_bind()
    state = _p3_state(connection)
    allow_data_loss = context.get_x_argument(as_dictionary=True).get("allow_p3_data_loss")
    if state and allow_data_loss != "true":
        raise RuntimeError(
            "P3_DOWNGRADE_BLOCKED_NONEMPTY: P3 operational state exists: "
            + ", ".join(state)
        )
    if state:
        _require_isolated_database(connection)

    # Remove dependent objects before dropping the additive columns.
    for trigger in (
        "document_chunks_fts_au",
        "document_chunks_fts_ad",
        "document_chunks_fts_ai",
    ):
        op.execute(f"DROP TRIGGER IF EXISTS {trigger}")
    op.execute("DROP TABLE IF EXISTS document_chunks_fts")
    op.drop_table("artifact_translation_checkpoints")
    op.drop_table("document_chunk_embeddings")
    op.drop_index("ix_document_chunks_source_status_sequence", table_name="document_chunks")
    op.drop_index("ux_document_chunks_chunk_key", table_name="document_chunks")
    for column in reversed(P3_CHUNK_COLUMNS):
        op.drop_column("document_chunks", column)


def _preflight_p2_schema(connection: sa.Connection) -> None:
    problems: list[str] = []
    versions = [
        str(row[0])
        for row in connection.exec_driver_sql(
            "SELECT version_num FROM alembic_version ORDER BY version_num"
        ).fetchall()
    ]
    if versions != ["20260807_02"]:
        problems.append(f"expected head 20260807_02, observed {versions!r}")
    inspector = sa.inspect(connection)
    tables = set(inspector.get_table_names())
    for table, required in P2_REQUIRED_COLUMNS.items():
        if table not in tables:
            problems.append(f"missing table {table}")
            continue
        observed = {str(column["name"]) for column in inspector.get_columns(table)}
        missing = sorted(required - observed)
        if missing:
            problems.append(f"{table} missing columns {','.join(missing)}")
    if problems:
        raise RuntimeError("P3_BASE_SCHEMA_MISSING: " + "; ".join(problems))


def _probe_fts5_trigram(connection: sa.Connection) -> str:
    # The probe lives in TEMP and is dropped on both success and failure, so a
    # capability failure cannot leave persistent sqlite_master changes behind.
    errors: list[Exception] = []
    for tokenizer in (_FTS_TOKENIZER, _FALLBACK_FTS_TOKENIZER):
        try:
            connection.exec_driver_sql(
                "CREATE VIRTUAL TABLE temp.p3_fts_probe USING fts5("
                f"body, tokenize='{tokenizer}')"
            )
            connection.exec_driver_sql(
                "INSERT INTO temp.p3_fts_probe(body) VALUES (?)",
                ("A multimodal model for 机器学习",),
            )
            english = connection.exec_driver_sql(
                "SELECT count(*) FROM temp.p3_fts_probe WHERE p3_fts_probe MATCH ?",
                ("timodal",),
            ).scalar_one()
            chinese = connection.exec_driver_sql(
                "SELECT count(*) FROM temp.p3_fts_probe WHERE p3_fts_probe MATCH ?",
                ("机器学习",),
            ).scalar_one()
            if english != 1 or chinese != 1:
                raise RuntimeError("sentinel query did not match")
            return tokenizer
        except Exception as error:
            errors.append(error)
        finally:
            try:
                connection.exec_driver_sql("DROP TABLE IF EXISTS temp.p3_fts_probe")
            except Exception:
                pass
    raise RuntimeError(
        "FTS5_TRIGRAM_UNAVAILABLE: SQLite must support FTS5 trigram "
        "with case_sensitive=0"
    ) from errors[-1]


def _backfill_chunks(connection: sa.Connection) -> None:
    rows = connection.exec_driver_sql(
        "SELECT c.rowid,c.id,c.source_document_id,c.sequence,c.content,c.content_sha256,"
        "s.status AS source_status,s.content_sha256 AS source_sha,s.updated_at "
        "FROM document_chunks c JOIN document_sources s ON s.id=c.source_document_id "
        "ORDER BY c.source_document_id,c.sequence,c.rowid"
    ).mappings().all()
    offsets: dict[str, int] = {}
    for row in rows:
        source_id = str(row["source_document_id"])
        start = offsets.get(source_id, 0)
        content = str(row["content"])
        end = start + len(content)
        source_sha = str(row["source_sha"] or row["content_sha256"])
        status = "ready" if str(row["source_status"]) == "ready" else "stale"
        version = "legacy-p1-v1"
        material = "\0".join(
            (
                "chunk:v1",
                source_id,
                source_sha,
                version,
                str(row["sequence"]),
                str(start),
                str(end),
                str(row["content_sha256"]),
            )
        )
        chunk_key = hashlib.sha256(material.encode("utf-8")).hexdigest()
        timestamp = str(row["updated_at"] or "1970-01-01T00:00:00Z")
        connection.exec_driver_sql(
            "UPDATE document_chunks SET status=?,content_kind=?,chunk_key=?,"
            "chunking_version=?,source_content_sha256=?,char_start=?,char_end=?,"
            "created_at=?,updated_at=?,stale_at=? WHERE rowid=?",
            (
                status,
                "text",
                chunk_key,
                version,
                source_sha,
                start,
                end,
                timestamp,
                timestamp,
                timestamp if status == "stale" else None,
                row["rowid"],
            ),
        )
        offsets[source_id] = end


def _create_embedding_table() -> None:
    op.create_table(
        "document_chunk_embeddings",
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("chunk_id", sa.Text(), nullable=False),
        sa.Column("source_document_id", sa.Text(), nullable=False),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("model", sa.Text(), nullable=False),
        sa.Column("embedding_version", sa.Text(), nullable=False),
        sa.Column("dimensions", sa.Integer(), nullable=False),
        sa.Column("vector", sa.LargeBinary(), nullable=True),
        sa.Column("vector_sha256", sa.Text(), nullable=True),
        sa.Column("chunk_content_sha256", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("error_code", sa.Text(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.Text(), nullable=False),
        sa.Column("stale_at", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_document_chunk_embeddings"),
        sa.ForeignKeyConstraint(
            ["chunk_id"], ["document_chunks.id"], ondelete="CASCADE",
            name="fk_embeddings_chunk",
        ),
        sa.ForeignKeyConstraint(
            ["source_document_id"], ["document_sources.id"], ondelete="CASCADE",
            name="fk_embeddings_source",
        ),
        sa.UniqueConstraint(
            "chunk_id", "provider", "model", "embedding_version",
            name="uq_embedding_identity",
        ),
        sa.CheckConstraint("length(trim(provider)) > 0", name="ck_embeddings_provider"),
        sa.CheckConstraint("length(trim(model)) > 0", name="ck_embeddings_model"),
        sa.CheckConstraint("length(trim(embedding_version)) > 0", name="ck_embeddings_version"),
        sa.CheckConstraint("dimensions > 0", name="ck_embeddings_dimensions"),
        sa.CheckConstraint(_SHA.format("chunk_content_sha256"), name="ck_embeddings_chunk_sha"),
        sa.CheckConstraint(
            f"vector_sha256 IS NULL OR ({_SHA.format('vector_sha256')})",
            name="ck_embeddings_vector_sha",
        ),
        sa.CheckConstraint(
            "status IN ('ready','failed','stale')", name="ck_embeddings_status"
        ),
        sa.CheckConstraint(
            "status <> 'ready' OR (vector IS NOT NULL AND vector_sha256 IS NOT NULL "
            "AND length(vector)=dimensions*4 AND error_code IS NULL)",
            name="ck_embeddings_ready_payload",
        ),
        sa.CheckConstraint(
            "status <> 'failed' OR (vector IS NULL AND error_code IS NOT NULL "
            "AND length(trim(error_code)) > 0)",
            name="ck_embeddings_failed_payload",
        ),
        sa.CheckConstraint(
            "status <> 'stale' OR stale_at IS NOT NULL", name="ck_embeddings_stale_at"
        ),
        sa.CheckConstraint(_UTC.format("created_at"), name="ck_embeddings_created_utc"),
        sa.CheckConstraint(_UTC.format("updated_at"), name="ck_embeddings_updated_utc"),
    )
    op.create_index(
        "ix_embeddings_source_profile",
        "document_chunk_embeddings",
        ["source_document_id", "status", "provider", "model", "embedding_version"],
    )
    op.create_index("ix_embeddings_chunk", "document_chunk_embeddings", ["chunk_id"])


def _create_checkpoint_table() -> None:
    op.create_table(
        "artifact_translation_checkpoints",
        sa.Column("artifact_id", sa.Text(), nullable=False),
        sa.Column("chunk_id", sa.Text(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("source_content_sha256", sa.Text(), nullable=False),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("model", sa.Text(), nullable=False),
        sa.Column("prompt_version", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("translated_markdown", sa.Text(), nullable=True),
        sa.Column("content_sha256", sa.Text(), nullable=True),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("error_code", sa.Text(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("artifact_id", "sequence", name="pk_translation_checkpoints"),
        sa.ForeignKeyConstraint(
            ["artifact_id"], ["generated_artifacts.id"], ondelete="CASCADE",
            name="fk_checkpoints_artifact",
        ),
        sa.ForeignKeyConstraint(
            ["chunk_id"], ["document_chunks.id"], ondelete="CASCADE",
            name="fk_checkpoints_chunk",
        ),
        sa.UniqueConstraint("artifact_id", "chunk_id", name="uq_checkpoint_chunk"),
        sa.CheckConstraint("sequence >= 0", name="ck_checkpoint_sequence"),
        sa.CheckConstraint(_SHA.format("source_content_sha256"), name="ck_checkpoint_source_sha"),
        sa.CheckConstraint("length(trim(provider)) > 0", name="ck_checkpoint_provider"),
        sa.CheckConstraint("length(trim(model)) > 0", name="ck_checkpoint_model"),
        sa.CheckConstraint("length(trim(prompt_version)) > 0", name="ck_checkpoint_prompt"),
        sa.CheckConstraint(
            "status IN ('queued','running','succeeded','failed')", name="ck_checkpoint_status"
        ),
        sa.CheckConstraint("attempt >= 0", name="ck_checkpoint_attempt"),
        sa.CheckConstraint(
            f"content_sha256 IS NULL OR ({_SHA.format('content_sha256')})",
            name="ck_checkpoint_content_sha",
        ),
        sa.CheckConstraint(
            "status <> 'succeeded' OR (translated_markdown IS NOT NULL "
            "AND length(trim(translated_markdown)) > 0 AND content_sha256 IS NOT NULL)",
            name="ck_checkpoint_succeeded_payload",
        ),
        sa.CheckConstraint(
            "status <> 'failed' OR (error_code IS NOT NULL AND length(trim(error_code)) > 0)",
            name="ck_checkpoint_failed_payload",
        ),
        sa.CheckConstraint(_UTC.format("created_at"), name="ck_checkpoint_created_utc"),
        sa.CheckConstraint(_UTC.format("updated_at"), name="ck_checkpoint_updated_utc"),
    )
    op.create_index(
        "ix_checkpoints_artifact_status_sequence",
        "artifact_translation_checkpoints",
        ["artifact_id", "status", "sequence"],
    )


def _chunk_guard_predicate(alias: str) -> str:
    return (
        f"{alias}.status IS NULL OR {alias}.status NOT IN ('ready','stale') "
        f"OR {alias}.content_kind IS NULL OR {alias}.content_kind NOT IN ('text','verbatim','structured') "
        f"OR {alias}.chunk_key IS NULL OR length(trim({alias}.chunk_key)) = 0 "
        f"OR {alias}.chunking_version IS NULL OR length(trim({alias}.chunking_version)) = 0 "
        f"OR {alias}.source_content_sha256 IS NULL OR NOT ({_SHA.format(alias + '.source_content_sha256')}) "
        f"OR {alias}.char_start IS NULL OR {alias}.char_start < 0 "
        f"OR {alias}.char_end IS NULL OR {alias}.char_end < {alias}.char_start "
        f"OR {alias}.created_at IS NULL OR NOT ({_UTC.format(alias + '.created_at')}) "
        f"OR {alias}.updated_at IS NULL OR NOT ({_UTC.format(alias + '.updated_at')}) "
        f"OR ({alias}.status = 'stale' AND {alias}.stale_at IS NULL) "
        f"OR ({alias}.status = 'ready' AND {alias}.stale_at IS NOT NULL)"
    )


def _create_fts(connection: sa.Connection, tokenizer: str) -> None:
    op.execute(
        "CREATE VIRTUAL TABLE document_chunks_fts USING fts5("
        "heading_path, content, content='document_chunks', content_rowid='rowid', "
        f"tokenize='{tokenizer}')"
    )
    predicate_new = _chunk_guard_predicate("NEW")
    connection.exec_driver_sql(
        "CREATE TRIGGER document_chunks_fts_ai AFTER INSERT ON document_chunks "
        "BEGIN "
        f"SELECT CASE WHEN {predicate_new} THEN RAISE(ABORT,'CHUNK_METADATA_INVALID') END; "
        "INSERT INTO document_chunks_fts(rowid,heading_path,content) "
        "VALUES(NEW.rowid,COALESCE(NEW.heading_path,''),NEW.content); END"
    )
    connection.exec_driver_sql(
        "CREATE TRIGGER document_chunks_fts_ad AFTER DELETE ON document_chunks "
        "BEGIN "
        "INSERT INTO document_chunks_fts(document_chunks_fts,rowid,heading_path,content) "
        "VALUES('delete',OLD.rowid,COALESCE(OLD.heading_path,''),OLD.content); END"
    )
    connection.exec_driver_sql(
        "CREATE TRIGGER document_chunks_fts_au AFTER UPDATE ON document_chunks "
        "BEGIN "
        f"SELECT CASE WHEN {_chunk_guard_predicate('NEW')} THEN RAISE(ABORT,'CHUNK_METADATA_INVALID') END; "
        "INSERT INTO document_chunks_fts(document_chunks_fts,rowid,heading_path,content) "
        "VALUES('delete',OLD.rowid,COALESCE(OLD.heading_path,''),OLD.content); "
        "INSERT INTO document_chunks_fts(rowid,heading_path,content) "
        "VALUES(NEW.rowid,COALESCE(NEW.heading_path,''),NEW.content); END"
    )
    connection.exec_driver_sql(
        "INSERT INTO document_chunks_fts(document_chunks_fts) VALUES('rebuild')"
    )


def _validate_fts_coverage(connection: sa.Connection) -> None:
    invalid = connection.exec_driver_sql(
        "SELECT count(*) FROM document_chunks WHERE status IS NULL OR content_kind IS NULL "
        "OR chunk_key IS NULL OR chunking_version IS NULL OR source_content_sha256 IS NULL "
        "OR char_start IS NULL OR char_end IS NULL OR created_at IS NULL OR updated_at IS NULL"
    ).scalar_one()
    if invalid:
        raise RuntimeError("CHUNK_COVERAGE_INVALID: backfill left incomplete metadata")
    total = connection.exec_driver_sql("SELECT count(*) FROM document_chunks").scalar_one()
    joined = connection.exec_driver_sql(
        "SELECT count(*) FROM document_chunks c JOIN document_chunks_fts f ON f.rowid=c.rowid"
    ).scalar_one()
    if total != joined:
        raise RuntimeError(
            f"FTS5_COVERAGE_INVALID: document_chunks={total}, indexed={joined}"
        )
    integrity = connection.exec_driver_sql(
        "INSERT INTO document_chunks_fts(document_chunks_fts) VALUES('integrity-check')"
    )
    del integrity


def _p3_state(connection: sa.Connection) -> list[str]:
    state: list[str] = []
    for table in ("document_chunk_embeddings", "artifact_translation_checkpoints"):
        if connection.exec_driver_sql(f'SELECT count(*) FROM "{table}"').scalar_one():
            state.append(table)
    if connection.exec_driver_sql("SELECT count(*) FROM document_chunks").scalar_one():
        state.append("document_chunks")
    return state


def _require_isolated_database(connection: sa.Connection) -> None:
    raw_database = os.environ.get("DB_PATH")
    raw_restore_root = os.environ.get("MIGRATION_RESTORE_ROOT")
    main_rows = connection.exec_driver_sql("PRAGMA database_list").fetchall()
    main_paths = [Path(str(row[2])) for row in main_rows if str(row[1]) == "main"]
    if len(main_paths) != 1 or not raw_database or not raw_restore_root:
        _raise_isolation_error()
    database = Path(raw_database).expanduser().absolute()
    root = Path(raw_restore_root).expanduser().absolute()
    parent = database.parent
    if any(_is_link_or_reparse(path) for path in (root, parent, database)):
        _raise_isolation_error()
    try:
        database = database.resolve(strict=True)
        root = root.resolve(strict=True)
        connected = main_paths[0].expanduser().absolute().resolve(strict=True)
    except OSError:
        _raise_isolation_error()
    if (
        database != connected
        or database.name != "app.db"
        or not parent.name.startswith("restore-validation-")
        or parent.name == "restore-validation-"
        or not root.is_dir()
        or parent.parent != root
        or not database.is_file()
    ):
        _raise_isolation_error()


def _is_link_or_reparse(path: Path) -> bool:
    try:
        metadata = path.lstat()
    except OSError:
        return True
    return path.is_symlink() or bool(
        getattr(metadata, "st_file_attributes", 0)
        & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    )


def _raise_isolation_error() -> None:
    raise RuntimeError(
        "P3_DOWNGRADE_BLOCKED_NONEMPTY: data-loss opt-in requires a bound "
        "restore-validation copy"
    )
