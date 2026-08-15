"""Add the P2 processing queue and OCR persistence contract."""

from __future__ import annotations

from collections.abc import Sequence
import hashlib
import json
import os
from pathlib import Path
import stat

from alembic import context, op
import sqlalchemy as sa

from backend.app.domain.processing import (
    LegacyImportedJobSpecV1,
    decode_job_spec_v1,
    encode_job_spec_v1,
    hash_job_spec,
)


revision: str = "20260807_02"
down_revision: str | None = "20260807_01"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


P1_REQUIRED_COLUMNS = {
    "document_sources": {
        "id", "paper_id", "mode", "status", "provider", "model", "pdf_sha256",
        "options_hash", "content_sha256", "markdown", "page_count", "processing_version",
        "error_code", "error_message", "created_at", "updated_at",
    },
    "generated_artifacts": {
        "id", "paper_id", "kind", "source_document_id", "status", "content",
        "content_sha256", "generator_provider", "generator_model", "prompt_version",
        "error_code", "error_message", "created_at", "updated_at",
    },
    "processing_jobs": {
        "id", "paper_id", "job_type", "source_mode", "status", "progress_json", "attempt",
        "max_attempts", "idempotency_key", "error_code", "error_message", "created_at",
        "started_at", "finished_at", "cancelled_at",
    },
    "document_chunks": {
        "id", "source_document_id", "sequence", "heading_path", "page_start", "page_end",
        "content", "content_sha256", "token_count",
    },
    "obsidian_exports": {
        "id", "paper_id", "artifact_id", "target_path", "source_hash", "exported_hash",
        "status", "exported_at", "error_message",
    },
}


def upgrade() -> None:
    connection = op.get_bind()
    _preflight_p1_schema(connection)

    op.add_column("document_sources", sa.Column("source_key", sa.Text(), nullable=True))
    op.add_column("document_sources", sa.Column("ready_at", sa.Text(), nullable=True))
    op.add_column("document_sources", sa.Column("stale_at", sa.Text(), nullable=True))
    op.add_column("generated_artifacts", sa.Column("artifact_key", sa.Text(), nullable=True))
    op.add_column("generated_artifacts", sa.Column("ready_at", sa.Text(), nullable=True))
    op.add_column("generated_artifacts", sa.Column("stale_at", sa.Text(), nullable=True))
    op.execute(
        "ALTER TABLE processing_jobs ADD COLUMN source_document_id TEXT "
        "REFERENCES document_sources(id) ON DELETE CASCADE"
    )
    op.execute(
        "ALTER TABLE processing_jobs ADD COLUMN artifact_id TEXT "
        "REFERENCES generated_artifacts(id) ON DELETE CASCADE"
    )
    op.add_column(
        "processing_jobs",
        sa.Column("spec_json", sa.Text(), nullable=False, server_default=sa.text("'{}'")),
    )
    op.add_column("processing_jobs", sa.Column("available_at", sa.Text(), nullable=True))
    op.add_column("processing_jobs", sa.Column("lease_owner", sa.Text(), nullable=True))
    op.add_column("processing_jobs", sa.Column("lease_token", sa.Text(), nullable=True))
    op.add_column("processing_jobs", sa.Column("lease_expires_at", sa.Text(), nullable=True))
    op.add_column("processing_jobs", sa.Column("heartbeat_at", sa.Text(), nullable=True))
    op.add_column("processing_jobs", sa.Column("cancel_requested_at", sa.Text(), nullable=True))
    op.add_column("processing_jobs", sa.Column("result_json", sa.Text(), nullable=True))
    op.add_column("processing_jobs", sa.Column("updated_at", sa.Text(), nullable=True))
    op.execute(
        "ALTER TABLE processing_jobs ADD COLUMN retry_of_job_id TEXT "
        "REFERENCES processing_jobs(id)"
    )
    op.add_column(
        "processing_jobs",
        sa.Column("retry_sequence", sa.Integer(), nullable=False, server_default=sa.text("0")),
    )

    _backfill_cache_columns(connection)

    rows = connection.execute(
        sa.text(
            "SELECT id,paper_id,job_type,source_mode,created_at "
            "FROM processing_jobs ORDER BY id"
        )
    ).mappings().all()
    before_count = len(rows)
    before_spec_hashes: dict[str, str] = {}
    for row in rows:
        spec_json = encode_job_spec_v1(
            LegacyImportedJobSpecV1(
                job_type=str(row["job_type"]),
                paper_id=row["paper_id"],
                source_mode=row["source_mode"],
            )
        )
        decode_job_spec_v1(
            spec_json,
            expected_row={
                "job_type": row["job_type"],
                "paper_id": row["paper_id"],
                "source_mode": row["source_mode"],
                "source_document_id": None,
                "artifact_id": None,
            },
        )
        before_spec_hashes[str(row["id"])] = hash_job_spec(spec_json)
        connection.execute(
            sa.text(
                "UPDATE processing_jobs SET spec_json=:spec_json, "
                "available_at=created_at, updated_at=created_at WHERE id=:id"
            ),
            {"id": row["id"], "spec_json": spec_json},
        )
    persisted = connection.execute(
        sa.text(
            "SELECT id,paper_id,job_type,source_mode,spec_json "
            "FROM processing_jobs ORDER BY id"
        )
    ).mappings().all()
    if len(persisted) != before_count:
        raise RuntimeError("P2_SPEC_BACKFILL_INVALID: processing job count changed")
    for row in persisted:
        decode_job_spec_v1(
            str(row["spec_json"]),
            expected_row={
                "job_type": row["job_type"],
                "paper_id": row["paper_id"],
                "source_mode": row["source_mode"],
                "source_document_id": None,
                "artifact_id": None,
            },
        )
        if hash_job_spec(str(row["spec_json"])) != before_spec_hashes[str(row["id"])]:
            raise RuntimeError("P2_SPEC_BACKFILL_INVALID: processing job spec hash changed")
    _create_spec_guards(connection)
    op.create_index(
        "ux_document_sources_source_key",
        "document_sources",
        ["source_key"],
        unique=True,
        sqlite_where=sa.text("source_key IS NOT NULL"),
    )
    op.create_index(
        "ux_generated_artifacts_artifact_key",
        "generated_artifacts",
        ["artifact_key"],
        unique=True,
        sqlite_where=sa.text("artifact_key IS NOT NULL"),
    )
    op.create_index(
        "ux_generated_artifacts_head_relation",
        "generated_artifacts",
        ["paper_id", "kind", "id"],
        unique=True,
    )
    op.create_index(
        "ix_processing_jobs_claim",
        "processing_jobs",
        ["status", "available_at", "created_at"],
    )
    op.create_index("ix_processing_jobs_lease_expires", "processing_jobs", ["lease_expires_at"])
    op.create_index("ix_processing_jobs_source", "processing_jobs", ["source_document_id"])
    op.create_index("ix_processing_jobs_artifact", "processing_jobs", ["artifact_id"])
    op.create_index("ix_processing_jobs_retry_parent", "processing_jobs", ["retry_of_job_id"])
    op.create_index(
        "ux_processing_jobs_active_retry",
        "processing_jobs",
        ["retry_of_job_id"],
        unique=True,
        sqlite_where=sa.text(
            "retry_of_job_id IS NOT NULL AND status IN ('queued','running')"
        ),
    )

    utc_check = "{0} GLOB '????-??-??T??:??:??*Z'"
    op.create_table(
        "paper_artifact_heads",
        sa.Column("paper_id", sa.Text(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("artifact_id", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("paper_id", "kind", name="pk_paper_artifact_heads"),
        sa.ForeignKeyConstraint(
            ["paper_id"], ["papers.id"], ondelete="CASCADE", name="fk_heads_paper"
        ),
        sa.ForeignKeyConstraint(
            ["paper_id", "kind", "artifact_id"],
            ["generated_artifacts.paper_id", "generated_artifacts.kind", "generated_artifacts.id"],
            ondelete="CASCADE",
            name="fk_heads_artifact_relation",
        ),
        sa.CheckConstraint("length(trim(kind)) > 0", name="ck_heads_kind_nonblank"),
        sa.CheckConstraint(utc_check.format("updated_at"), name="ck_heads_updated_utc"),
    )
    op.create_index(
        "ix_paper_artifact_heads_artifact", "paper_artifact_heads", ["artifact_id"]
    )

    op.create_table(
        "processing_job_events",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("job_id", sa.Text(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.Text(), nullable=False),
        sa.Column("progress_json", sa.Text(), nullable=False),
        sa.Column("error_code", sa.Text(), nullable=True),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_processing_job_events"),
        sa.ForeignKeyConstraint(
            ["job_id"], ["processing_jobs.id"], ondelete="CASCADE", name="fk_events_job"
        ),
        sa.UniqueConstraint("job_id", "sequence", name="uq_events_job_sequence"),
        sa.CheckConstraint("sequence > 0", name="ck_events_sequence"),
        sa.CheckConstraint(
            "event_type IN ('enqueued','claimed','progress','retry_scheduled',"
            "'cancel_requested','cancelled','succeeded','failed','lease_recovered')",
            name="ck_events_type",
        ),
        sa.CheckConstraint("json_valid(progress_json)=1", name="ck_events_progress_json"),
        sa.CheckConstraint(utc_check.format("created_at"), name="ck_events_created_utc"),
        sqlite_autoincrement=True,
    )
    op.create_index(
        "ix_processing_job_events_job_sequence",
        "processing_job_events",
        ["job_id", "sequence"],
    )

    lower_sha = "length({0})=64 AND {0} NOT GLOB '*[^0-9a-f]*'"
    op.create_table(
        "ocr_page_checkpoints",
        sa.Column("source_document_id", sa.Text(), nullable=False),
        sa.Column("page_number", sa.Integer(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("markdown", sa.Text(), nullable=True),
        sa.Column("content_sha256", sa.Text(), nullable=True),
        sa.Column("provider_page_id", sa.Text(), nullable=True),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("error_code", sa.Text(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint(
            "source_document_id", "page_number", name="pk_ocr_page_checkpoints"
        ),
        sa.ForeignKeyConstraint(
            ["source_document_id"], ["document_sources.id"], ondelete="CASCADE",
            name="fk_ocr_checkpoints_source",
        ),
        sa.CheckConstraint("page_number > 0", name="ck_ocr_checkpoints_page"),
        sa.CheckConstraint(
            "status IN ('queued','running','succeeded','failed')",
            name="ck_ocr_checkpoints_status",
        ),
        sa.CheckConstraint(
            f"content_sha256 IS NULL OR ({lower_sha.format('content_sha256')})",
            name="ck_ocr_checkpoints_sha",
        ),
        sa.CheckConstraint("attempt >= 0", name="ck_ocr_checkpoints_attempt"),
        sa.CheckConstraint(
            "status <> 'succeeded' OR (markdown IS NOT NULL AND length(trim(markdown)) > 0 "
            "AND content_sha256 IS NOT NULL)",
            name="ck_ocr_checkpoints_succeeded_payload",
        ),
        sa.CheckConstraint(
            "status <> 'failed' OR (error_code IS NOT NULL AND length(trim(error_code)) > 0)",
            name="ck_ocr_checkpoints_failed_error",
        ),
        sa.CheckConstraint(utc_check.format("created_at"), name="ck_ocr_checkpoints_created_utc"),
        sa.CheckConstraint(utc_check.format("updated_at"), name="ck_ocr_checkpoints_updated_utc"),
    )
    op.create_index(
        "ix_ocr_page_checkpoints_source_status_page",
        "ocr_page_checkpoints",
        ["source_document_id", "status", "page_number"],
    )


def downgrade() -> None:
    connection = op.get_bind()
    state = _p2_state(connection)
    allow_data_loss = context.get_x_argument(as_dictionary=True).get("allow_p2_data_loss")
    if state and allow_data_loss != "true":
        raise RuntimeError(
            "P2_DOWNGRADE_BLOCKED_NONEMPTY: P2 operational state exists: "
            + ", ".join(state)
        )
    if state:
        _require_isolated_database(connection)
    before = _p1_core_fingerprints(connection)
    op.execute("DROP TRIGGER IF EXISTS processing_jobs_spec_guard_update")
    op.execute("DROP TRIGGER IF EXISTS processing_jobs_spec_guard_insert")
    op.drop_table("ocr_page_checkpoints")
    op.drop_table("processing_job_events")
    op.drop_table("paper_artifact_heads")
    for index_name, table_name in (
        ("ux_processing_jobs_active_retry", "processing_jobs"),
        ("ix_processing_jobs_retry_parent", "processing_jobs"),
        ("ix_processing_jobs_artifact", "processing_jobs"),
        ("ix_processing_jobs_source", "processing_jobs"),
        ("ix_processing_jobs_lease_expires", "processing_jobs"),
        ("ix_processing_jobs_claim", "processing_jobs"),
        ("ux_generated_artifacts_head_relation", "generated_artifacts"),
        ("ux_generated_artifacts_artifact_key", "generated_artifacts"),
        ("ux_document_sources_source_key", "document_sources"),
    ):
        op.drop_index(index_name, table_name=table_name)
    for column_name in reversed((
        "source_document_id", "artifact_id", "spec_json", "available_at", "lease_owner",
        "lease_token", "lease_expires_at", "heartbeat_at", "cancel_requested_at",
        "result_json", "updated_at", "retry_of_job_id", "retry_sequence",
    )):
        op.drop_column("processing_jobs", column_name)
    for column_name in reversed(("artifact_key", "ready_at", "stale_at")):
        op.drop_column("generated_artifacts", column_name)
    for column_name in reversed(("source_key", "ready_at", "stale_at")):
        op.drop_column("document_sources", column_name)
    if _p1_core_fingerprints(connection) != before:
        raise RuntimeError("P2_DOWNGRADE_P1_DATA_CHANGED: P1 core fingerprints changed")


def _backfill_cache_columns(connection: sa.Connection) -> None:
    for row in connection.execute(sa.text(
        "SELECT id,paper_id,mode,provider,model,pdf_sha256,options_hash,processing_version,status,updated_at "
        "FROM document_sources ORDER BY id"
    )).mappings():
        material = "\0".join((
            "source:v1", str(row["paper_id"]), str(row["mode"]), str(row["provider"]),
            str(row["model"]), str(row["pdf_sha256"]), str(row["options_hash"]),
            str(row["processing_version"]),
        ))
        connection.execute(sa.text(
            "UPDATE document_sources SET source_key=:key, "
            "ready_at=CASE WHEN status='ready' THEN updated_at END, "
            "stale_at=CASE WHEN status='stale' THEN updated_at END WHERE id=:id"
        ), {"id": row["id"], "key": hashlib.sha256(material.encode("utf-8")).hexdigest()})
    empty_options_hash = hashlib.sha256(b"{}").hexdigest()
    for row in connection.execute(sa.text(
        "SELECT a.id,a.kind,a.source_document_id,a.generator_provider,a.generator_model,"
        "a.prompt_version,a.status,a.updated_at,s.content_sha256 "
        "FROM generated_artifacts a JOIN document_sources s ON s.id=a.source_document_id ORDER BY a.id"
    )).mappings():
        material = "\0".join((
            "artifact:v1", str(row["kind"]), str(row["source_document_id"]),
            str(row["content_sha256"] or ""), str(row["generator_provider"]),
            str(row["generator_model"]), str(row["prompt_version"]), empty_options_hash,
        ))
        connection.execute(sa.text(
            "UPDATE generated_artifacts SET artifact_key=:key, "
            "ready_at=CASE WHEN status='ready' THEN updated_at END, "
            "stale_at=CASE WHEN status='stale' THEN updated_at END WHERE id=:id"
        ), {"id": row["id"], "key": hashlib.sha256(material.encode("utf-8")).hexdigest()})


def _p2_state(connection: sa.Connection) -> list[str]:
    state: list[str] = []
    for table_name in ("paper_artifact_heads", "processing_job_events", "ocr_page_checkpoints"):
        if connection.execute(sa.text(f'SELECT count(*) FROM "{table_name}"')).scalar_one():
            state.append(table_name)
    if connection.execute(sa.text("SELECT count(*) FROM processing_jobs")).scalar_one():
        state.append("processing_jobs")
    if connection.execute(sa.text(
        "SELECT count(*) FROM document_sources WHERE source_key IS NOT NULL OR ready_at IS NOT NULL OR stale_at IS NOT NULL"
    )).scalar_one():
        state.append("document_sources")
    if connection.execute(sa.text(
        "SELECT count(*) FROM generated_artifacts WHERE artifact_key IS NOT NULL OR ready_at IS NOT NULL OR stale_at IS NOT NULL"
    )).scalar_one():
        state.append("generated_artifacts")
    return state


def _require_isolated_database(connection: sa.Connection) -> None:
    raw = connection.exec_driver_sql("PRAGMA database_list").fetchall()
    main_paths = [Path(str(row[2])) for row in raw if str(row[1]) == "main"]
    raw_database = os.environ.get("DB_PATH")
    raw_restore_root = os.environ.get("MIGRATION_RESTORE_ROOT")
    if len(main_paths) != 1 or not raw_database or not raw_restore_root:
        _raise_nonisolated_downgrade()

    database_lexical = Path(raw_database).expanduser().absolute()
    connected_lexical = main_paths[0].expanduser().absolute()
    restore_root_lexical = Path(raw_restore_root).expanduser().absolute()
    validation_directory_lexical = database_lexical.parent
    if any(
        _is_link_or_reparse(path)
        for path in (
            restore_root_lexical,
            validation_directory_lexical,
            database_lexical,
        )
    ):
        _raise_nonisolated_downgrade()

    try:
        database_path = database_lexical.resolve(strict=True)
        connected_path = connected_lexical.resolve(strict=True)
        restore_root = restore_root_lexical.resolve(strict=True)
    except OSError:
        _raise_nonisolated_downgrade()

    validation_directory = database_path.parent
    validation_name = validation_directory.name
    if (
        database_path != connected_path
        or not database_path.is_file()
        or not restore_root.is_dir()
        or database_path.name != "app.db"
        or not validation_name.startswith("restore-validation-")
        or validation_name == "restore-validation-"
        or validation_directory.parent != restore_root
    ):
        _raise_nonisolated_downgrade()


def _is_link_or_reparse(path: Path) -> bool:
    try:
        metadata = path.lstat()
    except OSError:
        return True
    return path.is_symlink() or bool(
        getattr(metadata, "st_file_attributes", 0)
        & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    )


def _raise_nonisolated_downgrade() -> None:
    raise RuntimeError(
        "P2_DOWNGRADE_BLOCKED_NONEMPTY: data-loss opt-in requires a bound "
        "restore-validation copy"
    )


def _p1_core_fingerprints(connection: sa.Connection) -> dict[str, tuple[int, str]]:
    result: dict[str, tuple[int, str]] = {}
    for table_name, columns in P1_REQUIRED_COLUMNS.items():
        ordered = sorted(columns)
        projection = ",".join(f'"{column}"' for column in ordered)
        rows = connection.exec_driver_sql(
            f'SELECT {projection} FROM "{table_name}" ORDER BY {projection}'
        ).fetchall()
        payload = json.dumps(
            [[{"type": type(value).__name__, "value": value.hex() if isinstance(value, bytes) else value}
              for value in row] for row in rows],
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        result[table_name] = (len(rows), hashlib.sha256(payload).hexdigest())
    return result


def _preflight_p1_schema(connection: sa.Connection) -> None:
    inspector = sa.inspect(connection)
    table_names = set(inspector.get_table_names())
    problems: list[str] = []
    for table_name, required_columns in P1_REQUIRED_COLUMNS.items():
        if table_name not in table_names:
            problems.append(f"missing table {table_name}")
            continue
        observed = {str(column["name"]) for column in inspector.get_columns(table_name)}
        missing = sorted(required_columns - observed)
        if missing:
            problems.append(f"{table_name} missing columns {','.join(missing)}")
    if problems:
        raise RuntimeError("P2_BASE_SCHEMA_MISSING: " + "; ".join(problems))


def _create_spec_guards(connection: sa.Connection) -> None:
    predicate = """
        length(CAST(NEW.spec_json AS BLOB)) NOT BETWEEN 2 AND 4194304
        OR json_valid(NEW.spec_json) <> 1
        OR json_type(NEW.spec_json, '$') <> 'object'
        OR json_type(NEW.spec_json, '$.arguments') <> 'object'
        OR json_type(NEW.spec_json, '$.target') <> 'object'
        OR json_type(NEW.spec_json, '$.schemaVersion') <> 'integer'
        OR json_extract(NEW.spec_json, '$.schemaVersion') <> 1
        OR (SELECT count(*) FROM json_each(NEW.spec_json)) <> 6
        OR EXISTS (
            SELECT 1 FROM json_each(NEW.spec_json)
            WHERE key NOT IN ('arguments','jobType','paperId','schemaVersion','sourceMode','target')
        )
        OR (SELECT count(*) FROM json_each(NEW.spec_json, '$.target')) <> 2
        OR EXISTS (
            SELECT 1 FROM json_each(NEW.spec_json, '$.target')
            WHERE key NOT IN ('artifactId','sourceDocumentId')
        )
        OR NOT (json_extract(NEW.spec_json, '$.jobType') IS NEW.job_type)
        OR NOT (json_extract(NEW.spec_json, '$.paperId') IS NEW.paper_id)
        OR NOT (json_extract(NEW.spec_json, '$.sourceMode') IS NEW.source_mode)
        OR NOT (json_extract(NEW.spec_json, '$.target.sourceDocumentId') IS NEW.source_document_id)
        OR NOT (json_extract(NEW.spec_json, '$.target.artifactId') IS NEW.artifact_id)
        OR EXISTS (
            SELECT 1 FROM json_tree(NEW.spec_json)
            WHERE key IS NOT NULL AND lower(replace(replace(key, '_', ''), '-', '')) IN (
                'apikey','authorization','cookie','credential','credentials','headers','markdown',
                'pdf','prompt','rawrequest','rawresponse','leasetoken'
            )
        )
        OR NEW.spec_json <> (
            '{"arguments":' || json_extract(NEW.spec_json, '$.arguments') ||
            ',"jobType":' || json_quote(NEW.job_type) ||
            ',"paperId":' || CASE WHEN NEW.paper_id IS NULL THEN 'null' ELSE json_quote(NEW.paper_id) END ||
            ',"schemaVersion":1,"sourceMode":' ||
                CASE WHEN NEW.source_mode IS NULL THEN 'null' ELSE json_quote(NEW.source_mode) END ||
            ',"target":{"artifactId":' ||
                CASE WHEN NEW.artifact_id IS NULL THEN 'null' ELSE json_quote(NEW.artifact_id) END ||
            ',"sourceDocumentId":' ||
                CASE WHEN NEW.source_document_id IS NULL THEN 'null' ELSE json_quote(NEW.source_document_id) END ||
            '}}'
        )
    """
    for action in ("INSERT", "UPDATE"):
        connection.exec_driver_sql(
            f"""
            CREATE TRIGGER processing_jobs_spec_guard_{action.lower()}
            BEFORE {action} ON processing_jobs
            FOR EACH ROW WHEN {predicate}
            BEGIN
                SELECT RAISE(ABORT, 'JOB_SPEC_INVALID');
            END
            """
        )
