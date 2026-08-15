from __future__ import annotations

import base64
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
from typing import Any, Mapping, Sequence

from backend.app.api.compat.database_identity import (
    DatabaseIdentityError,
    PlatformFileIdentity,
    canonical_json_bytes,
    exclusive_write_bytes,
    load_database_evidence_identity_manifest,
    read_platform_file_identity,
)
from backend.app.domain.processing import JobSpecValidationError, decode_job_spec_v1


INVENTORY_SCHEMA_VERSION = 1
BEHAVIOR_CONTRACT_VERSION = "p4-schema-inventory-v1"
PROCESSING_JOB_COLUMNS = (
    "id",
    "paper_id",
    "job_type",
    "source_mode",
    "status",
    "progress_json",
    "attempt",
    "max_attempts",
    "idempotency_key",
    "error_code",
    "error_message",
    "created_at",
    "started_at",
    "finished_at",
    "cancelled_at",
    "source_document_id",
    "artifact_id",
    "spec_json",
    "available_at",
    "lease_owner",
    "lease_token",
    "lease_expires_at",
    "heartbeat_at",
    "cancel_requested_at",
    "result_json",
    "updated_at",
    "retry_of_job_id",
    "retry_sequence",
)
_LEGACY_TABLES = (
    "papers",
    "progress",
    "paper_reviews",
    "notes",
    "favorites",
    "translations",
    "paper_vectors",
    "cite_edges",
    "ingest_jobs",
    "job_candidates",
    "job_schedules",
    "schema_migrations",
)
_P1_TABLES = (
    "document_sources",
    "generated_artifacts",
    "processing_jobs",
    "document_chunks",
    "obsidian_exports",
)
_P2_TABLES = (
    "paper_artifact_heads",
    "processing_job_events",
    "ocr_page_checkpoints",
)
_P3_TABLES = (
    "document_chunk_embeddings",
    "artifact_translation_checkpoints",
)
_FINGERPRINT_TABLES = (*_LEGACY_TABLES, *_P1_TABLES, *_P2_TABLES, *_P3_TABLES, "alembic_version")
_FTS_TABLE = "document_chunks_fts"
_FTS_SHADOW_TABLES = (
    "document_chunks_fts_config",
    "document_chunks_fts_data",
    "document_chunks_fts_docsize",
    "document_chunks_fts_idx",
)
_EXPECTED_TABLE_SQL_SHA256 = {
    "alembic_version": "c85bfd889cff304bc45779b35249633db07627c6e27322efb0b63bdc0ac77ca4",
    "artifact_translation_checkpoints": "5e5c808e3ebe1f1f9d8888e586078b2f972a6ec38414e9e2bf340140e1e00d8b",
    "cite_edges": "c5deb99e8a027d609207a891f8f3cf7f5e5382a20a759648cbf789a5ab922cd8",
    "document_chunk_embeddings": "59bb8c53a94baa36bdd9ee4d705762f6af97397efaf7db71a4458dcdc675d95d",
    "document_chunks": "3d199c1abe7d317e62097907b21cd036129f2fa36d114505538b82691fa5893b",
    "document_chunks_fts": "779f525345212c9347bfa74ee57638535e90b1a5fa9c1b36f7a97cebf4589474",
    "document_chunks_fts_config": "e0e7aa6519a893b8e26fca710114e424888ea41defc8aaa82d5b5b0922515225",
    "document_chunks_fts_data": "03d87945bea1e4937b9674d6352dd4bb6465b768bd5e407146b4effe402773c8",
    "document_chunks_fts_docsize": "d9a9cd25e82d057638786ef97d41c884208e5d9d8ea98133c628557cb350973a",
    "document_chunks_fts_idx": "7d4827aee2639ac360315faec95272d96e92bf7ea228af84dff71f5383c80bf5",
    "document_sources": "9d7a8e7b7a34aa3832550bb63fcd24a0ecdb61f654d49771aa13209e8ecef504",
    "favorites": "d88492728154604c53fd0944f569dec47c0da5d54487b9adc365f9ab8b29df44",
    "generated_artifacts": "ec98a7051d0f72cf026de32fd17d99902e5c5f2428b4c29ac136a2cb52fe6319",
    "ingest_jobs": "ce6489477ee60fb38fdad9480e6a8e25fc9662ed291e239e824e1fc793862871",
    "job_candidates": "6d9b5b062b556b88a003e47b2b87110b9ac62fd631a3a4423d8b55ee1bd80901",
    "job_schedules": "d7edd059b37f6aea41247e358cf5e150a8b987f57032e61ccb40a336487c09fd",
    "notes": "0523fa5c13af763704d4b1670f2dc0eeb9f53a59983bd91f79888e43ddcfb7b6",
    "obsidian_exports": "aa5b9185b86f27557de06216afba9e8ae56dd1ed2b59012b7780f80ce775b053",
    "ocr_page_checkpoints": "7de11a1ac068fec29f8ce508500be3a073129f080555ae6e6525f1de6057bfaf",
    "paper_artifact_heads": "9851ad86c4fabf5e023619027078b08706894d991bf3da084cd63bb96498666f",
    "paper_reviews": "b0c38cc7fc4fd883c7fd4012a2cf951b47d7052c081519bb49b609b8609728b4",
    "paper_vectors": "563f616e1d472e0d6331e2a89a30c962f690c91554e3a2d70eecef0c8ef6a8d4",
    "papers": "a473a6db51abebd3a6d597d565c1900633ee4c6d41838b3eee932c4e9eb18aac",
    "processing_job_events": "d3deca4b69e3abbbe699ac71bc78f36c271dcb59cf68e6e6e1bdc39d43420039",
    "processing_jobs": "0a68798c633d79995eb6274058717cb3308a6528a26e52302b1c64481db43428",
    "progress": "f5d3efb116434b555473f47fa19830355998a141c9f5f79f731540620307d356",
    "schema_migrations": "dce17389c2e3dd4f6cacdd25322e247b2bff5650968862d952c94b75dd23a104",
    "translations": "d0e14e9886010fd585785813f314cd8db0318b89b235364601c6fb6ff1b0904b",
}
_EXPECTED_TRIGGER_SQL_SHA256 = {
    "document_chunks_fts_ad": "5413206258bc5f1093b1c8ce6feb933eedb95687419218ff2b8517179c1412e6",
    "document_chunks_fts_ai": "2af48a2c2b8ca8921ad62c9ef0de64bc8fcaf3dd04c1878b8773b448631b4fff",
    "document_chunks_fts_au": "849f80306c6109b0b3730e2f32c2d458b0cdc45864a2280956a75f42b74b9330",
    "processing_jobs_spec_guard_insert": "499a50aaca8952b838ccea76c2b6db8714f7a9b8c018e2b21bf55eefe7b1b935",
    "processing_jobs_spec_guard_update": "eedfd7ec71a936078358508dee5758c7a8a4af9b702c4fd75228b59ef71f8a38",
}
_INVENTORY_FIELDS = (
    "schemaVersion",
    "inventoryKind",
    "behaviorContractVersion",
    "databaseIdentity",
    "alembic",
    "tables",
    "processingJobs",
    "processingJobSpecs",
    "triggers",
    "fts",
)
_DATABASE_IDENTITY_FIELDS = (
    "databaseLineageId",
    "subjectDatabaseId",
    "subjectKind",
    "resolvedPathHash",
    "platformFileIdentity",
    "parentBackupId",
    "parentManifestSha256",
    "parentSubjectDatabaseId",
    "parentIdentityManifestFileSha256",
    "originReceiptFileSha256",
)
_TABLE_FINGERPRINT_FIELDS = {
    "count",
    "columns",
    "primaryKeyColumns",
    "primaryKeySetSha256",
    "rowSha256",
    "normalizedSqlSha256",
}
_PROCESSING_JOB_FIELDS = {
    "count",
    "sha256",
    "strictDecodeCount",
    "strictDecodeErrorCount",
}
_PROCESSING_JOB_SPEC_FIELDS = {"count", "sha256", "projection"}
_TRIGGER_FIELDS = {"normalizedSqlSha256", "behaviorContractVersion"}
_FTS_FIELDS = {
    "virtualTableSqlSha256",
    "tokenizer",
    "logicalCount",
    "logicalSha256",
    "externalContentRowidJoin",
    "shadowTables",
}
_FTS_SHADOW_FIELDS = {"count", "rowSha256", "normalizedSqlSha256"}
_SHA256_PATTERN = re.compile(r"[0-9a-f]{64}\Z")
_SUBJECT_KIND_PATTERN = re.compile(r"[a-z0-9_-]+\Z")


class SchemaInventoryError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(f"{code}: {message}")


def capture_inventory(
    *,
    database: str | os.PathLike[str],
    database_identity_manifest: str | os.PathLike[str],
    output: str | os.PathLike[str],
) -> dict[str, Any]:
    database_path = _exact_existing_file(database, "database")
    identity = load_database_evidence_identity_manifest(database_identity_manifest)
    if (
        identity.database_path != database_path
        or identity.platform_file_identity != read_platform_file_identity(database_path)
    ):
        raise SchemaInventoryError(
            "INVENTORY_DATABASE_IDENTITY_MISMATCH",
            "The database does not match its typed evidence identity.",
        )
    sidecars = tuple(Path(f"{database_path}{suffix}") for suffix in ("-wal", "-shm", "-journal"))
    before = _file_state(database_path, sidecars)
    uri = database_path.as_uri() + "?mode=ro&immutable=1"
    try:
        with closing(sqlite3.connect(uri, uri=True)) as connection:
            connection.execute("PRAGMA query_only=ON")
            connection.execute("BEGIN")
            document = _capture(connection, identity)
            connection.rollback()
    except SchemaInventoryError:
        raise
    except sqlite3.Error as error:
        raise SchemaInventoryError(
            "INVENTORY_DATABASE_READ_FAILED",
            "The fixed schema inventory could not be captured read-only.",
        ) from error
    after = _file_state(database_path, sidecars)
    if before != after:
        raise SchemaInventoryError(
            "INVENTORY_READ_SIDE_EFFECT",
            "Read-only inventory capture changed database bytes or sidecars.",
        )
    output_path = _exact_output(output)
    exclusive_write_bytes(output_path, canonical_json_bytes(document))
    return document


def compare_inventory(
    before: Mapping[str, Any] | str | os.PathLike[str],
    after: Mapping[str, Any] | str | os.PathLike[str],
) -> bool:
    before_document = _load_inventory(before)
    after_document = _load_inventory(after)
    _validate_inventory_document(before_document)
    _validate_inventory_document(after_document)
    if before_document != after_document:
        raise SchemaInventoryError(
            "INVENTORY_MISMATCH",
            "The fixed schema or row-level inventory changed.",
        )
    return True


def _capture(connection: sqlite3.Connection, identity: Any) -> dict[str, Any]:
    schema_rows = connection.execute(
        "SELECT type,name,sql FROM sqlite_schema "
        "WHERE type IN ('table','trigger') AND name NOT LIKE 'sqlite_%' "
        "ORDER BY type,name"
    ).fetchall()
    tables = {str(name): str(sql) for type_, name, sql in schema_rows if type_ == "table"}
    expected_tables = set((*_FINGERPRINT_TABLES, _FTS_TABLE, *_FTS_SHADOW_TABLES))
    if set(tables) != expected_tables:
        raise SchemaInventoryError(
            "INVENTORY_TABLE_SET_INVALID",
            "The database table set does not match the frozen P4/P5 contract.",
        )
    for name, expected_hash in _EXPECTED_TABLE_SQL_SHA256.items():
        if _sql_sha256(tables[name]) != expected_hash:
            raise SchemaInventoryError(
                "INVENTORY_TABLE_SQL_INVALID",
                f"The normalized schema SQL changed for {name}.",
            )
    trigger_sql = {
        str(name): str(sql) for type_, name, sql in schema_rows if type_ == "trigger"
    }
    if set(trigger_sql) != set(_EXPECTED_TRIGGER_SQL_SHA256):
        raise SchemaInventoryError(
            "INVENTORY_TRIGGER_SET_INVALID",
            "The exact five-trigger contract is missing or has extra objects.",
        )
    trigger_inventory: dict[str, Any] = {}
    for name in sorted(_EXPECTED_TRIGGER_SQL_SHA256):
        actual_hash = _sql_sha256(trigger_sql[name])
        if actual_hash != _EXPECTED_TRIGGER_SQL_SHA256[name]:
            raise SchemaInventoryError(
                "INVENTORY_TRIGGER_SQL_INVALID",
                f"The normalized trigger SQL changed for {name}.",
            )
        trigger_inventory[name] = {
            "normalizedSqlSha256": actual_hash,
            "behaviorContractVersion": BEHAVIOR_CONTRACT_VERSION,
        }
    revision_rows = connection.execute(
        "SELECT version_num FROM alembic_version ORDER BY version_num"
    ).fetchall()
    if revision_rows != [("20260807_03",)]:
        raise SchemaInventoryError(
            "INVENTORY_REVISION_INVALID",
            "The inventory requires exactly one 20260807_03 Alembic revision.",
        )
    table_inventory: dict[str, Any] = {}
    for name in sorted(_FINGERPRINT_TABLES):
        table_inventory[name] = _fingerprint_table(connection, name, tables[name])
    processing = _processing_job_fingerprints(connection)
    fts = _capture_fts(connection, tables[_FTS_TABLE])
    document = {
        "schemaVersion": INVENTORY_SCHEMA_VERSION,
        "inventoryKind": "p4-p5-fixed-schema",
        "behaviorContractVersion": BEHAVIOR_CONTRACT_VERSION,
        "databaseIdentity": {
            "databaseLineageId": identity.database_lineage_id,
            "subjectDatabaseId": identity.subject_database_id,
            "subjectKind": identity.subject_kind,
            "resolvedPathHash": identity.resolved_path_hash,
            "platformFileIdentity": identity.platform_file_identity.to_dict(),
            "parentBackupId": identity.parent_backup_id,
            "parentManifestSha256": identity.parent_manifest_sha256,
            "parentSubjectDatabaseId": identity.parent_subject_database_id,
            "parentIdentityManifestFileSha256": identity.parent_identity_manifest_file_sha256,
            "originReceiptFileSha256": identity.origin_receipt_file_sha256,
        },
        "alembic": {"revision": "20260807_03", "count": 1},
        "tables": table_inventory,
        "processingJobs": processing[0],
        "processingJobSpecs": processing[1],
        "triggers": trigger_inventory,
        "fts": fts,
    }
    _validate_inventory_document(document)
    return document


def _fingerprint_table(
    connection: sqlite3.Connection,
    name: str,
    schema_sql: str,
) -> dict[str, Any]:
    metadata = connection.execute(f"PRAGMA table_info({_quote(name)})").fetchall()
    columns = tuple(str(row[1]) for row in metadata)
    if name == "processing_jobs":
        if columns != PROCESSING_JOB_COLUMNS or int(metadata[17][3]) != 1:
            raise SchemaInventoryError(
                "INVENTORY_PROCESSING_COLUMNS_INVALID",
                "processing_jobs must retain the fixed 28-column non-null spec_json contract.",
            )
    projection = ",".join(_quote(column) for column in columns)
    rows = connection.execute(f"SELECT {projection} FROM {_quote(name)}").fetchall()
    encoded_rows = sorted(_row_bytes(row) for row in rows)
    primary_columns = tuple(
        str(row[1]) for row in sorted(metadata, key=lambda item: int(item[5])) if int(row[5]) > 0
    )
    if primary_columns:
        primary_indexes = tuple(columns.index(column) for column in primary_columns)
        primary_rows = sorted(
            _row_bytes(tuple(row[index] for index in primary_indexes)) for row in rows
        )
    else:
        primary_rows = encoded_rows
    return {
        "count": len(rows),
        "columns": list(columns),
        "primaryKeyColumns": list(primary_columns),
        "primaryKeySetSha256": _sequence_sha256(primary_rows),
        "rowSha256": _sequence_sha256(encoded_rows),
        "normalizedSqlSha256": _sql_sha256(schema_sql),
    }


def _processing_job_fingerprints(
    connection: sqlite3.Connection,
) -> tuple[dict[str, Any], dict[str, Any]]:
    projection = ",".join(_quote(column) for column in PROCESSING_JOB_COLUMNS)
    rows = connection.execute(
        f"SELECT {projection} FROM processing_jobs ORDER BY id"
    ).fetchall()
    spec_rows: list[bytes] = []
    all_rows: list[bytes] = []
    for row in rows:
        mapped = dict(zip(PROCESSING_JOB_COLUMNS, row, strict=True))
        try:
            decode_job_spec_v1(
                mapped["spec_json"],
                expected_row={
                    "job_type": mapped["job_type"],
                    "paper_id": mapped["paper_id"],
                    "source_mode": mapped["source_mode"],
                    "source_document_id": mapped["source_document_id"],
                    "artifact_id": mapped["artifact_id"],
                },
            )
        except JobSpecValidationError as error:
            raise SchemaInventoryError(
                "INVENTORY_JOB_SPEC_INVALID",
                f"Processing job {mapped['id']!r} has a non-canonical or mismatched spec.",
            ) from error
        all_rows.append(_row_bytes(row))
        spec_rows.append(_row_bytes((mapped["id"], mapped["spec_json"])))
    count = len(rows)
    return (
        {
            "count": count,
            "sha256": _sequence_sha256(all_rows),
            "strictDecodeCount": count,
            "strictDecodeErrorCount": 0,
        },
        {
            "count": count,
            "sha256": _sequence_sha256(spec_rows),
            "projection": ["id", "spec_json"],
        },
    )


def _capture_fts(connection: sqlite3.Connection, schema_sql: str) -> dict[str, Any]:
    normalized = _normalize_sql(schema_sql)
    tokenizer = "trigram case_sensitive 0 remove_diacritics 1"
    if tokenizer not in normalized:
        raise SchemaInventoryError(
            "INVENTORY_FTS_TOKENIZER_INVALID",
            "The fixed trigram tokenizer contract changed.",
        )
    source_rows = connection.execute(
        "SELECT rowid,COALESCE(heading_path,''),content FROM document_chunks ORDER BY rowid"
    ).fetchall()
    joined_rows = connection.execute(
        "SELECT c.rowid,COALESCE(f.heading_path,''),f.content "
        "FROM document_chunks c JOIN document_chunks_fts f ON f.rowid=c.rowid "
        "ORDER BY c.rowid"
    ).fetchall()
    source_hash = _sequence_sha256([_row_bytes(row) for row in source_rows])
    joined_hash = _sequence_sha256([_row_bytes(row) for row in joined_rows])
    if len(source_rows) != len(joined_rows) or source_hash != joined_hash:
        raise SchemaInventoryError(
            "INVENTORY_FTS_JOIN_INVALID",
            "FTS external-content rowid coverage or logical content changed.",
        )
    shadow: dict[str, Any] = {}
    for table in _FTS_SHADOW_TABLES:
        metadata = connection.execute(f"PRAGMA table_info({_quote(table)})").fetchall()
        columns = tuple(str(row[1]) for row in metadata)
        rows = connection.execute(
            f"SELECT {','.join(_quote(column) for column in columns)} FROM {_quote(table)}"
        ).fetchall()
        shadow[table] = {
            "count": len(rows),
            "rowSha256": _sequence_sha256(sorted(_row_bytes(row) for row in rows)),
            "normalizedSqlSha256": _EXPECTED_TABLE_SQL_SHA256[table],
        }
    if shadow["document_chunks_fts_data"]["count"] == 0:
        raise SchemaInventoryError(
            "INVENTORY_FTS_STORAGE_INVALID",
            "The FTS data shadow table is empty or corrupt.",
        )
    if shadow["document_chunks_fts_docsize"]["count"] != len(source_rows):
        raise SchemaInventoryError(
            "INVENTORY_FTS_STORAGE_INVALID",
            "The FTS docsize coverage does not match document_chunks.",
        )
    return {
        "virtualTableSqlSha256": _sql_sha256(schema_sql),
        "tokenizer": tokenizer,
        "logicalCount": len(source_rows),
        "logicalSha256": source_hash,
        "externalContentRowidJoin": True,
        "shadowTables": shadow,
    }


def _validate_inventory_document(document: Mapping[str, Any]) -> None:
    if (
        tuple(document) != _INVENTORY_FIELDS
        or document.get("schemaVersion") != INVENTORY_SCHEMA_VERSION
        or document.get("inventoryKind") != "p4-p5-fixed-schema"
        or document.get("behaviorContractVersion") != BEHAVIOR_CONTRACT_VERSION
        or document.get("alembic") != {"revision": "20260807_03", "count": 1}
    ):
        raise SchemaInventoryError(
            "INVENTORY_DOCUMENT_INVALID",
            "The inventory header or revision contract is invalid.",
        )
    _validate_database_identity(document.get("databaseIdentity"))
    tables = document.get("tables")
    if not isinstance(tables, dict) or set(tables) != set(_FINGERPRINT_TABLES):
        raise SchemaInventoryError(
            "INVENTORY_DOCUMENT_INVALID",
            "The inventory document table set is invalid.",
        )
    for name, entry in tables.items():
        if not isinstance(entry, dict) or set(entry) != _TABLE_FINGERPRINT_FIELDS:
            raise SchemaInventoryError(
                "INVENTORY_DOCUMENT_INVALID",
                f"The inventory table fingerprint is incomplete for {name}.",
            )
        columns = entry["columns"]
        primary_key_columns = entry["primaryKeyColumns"]
        if (
            not _is_nonnegative_int(entry["count"])
            or not _is_unique_string_list(columns, allow_empty=False)
            or not _is_unique_string_list(primary_key_columns, allow_empty=True)
            or not set(primary_key_columns).issubset(columns)
            or not _is_sha256(entry["primaryKeySetSha256"])
            or not _is_sha256(entry["rowSha256"])
            or entry["normalizedSqlSha256"] != _EXPECTED_TABLE_SQL_SHA256[name]
        ):
            raise SchemaInventoryError(
                "INVENTORY_DOCUMENT_INVALID",
                f"The inventory table fingerprint is invalid for {name}.",
            )
        if name == "processing_jobs" and (
            tuple(columns) != PROCESSING_JOB_COLUMNS
            or primary_key_columns != ["id"]
        ):
            raise SchemaInventoryError(
                "INVENTORY_DOCUMENT_INVALID",
                "The processing_jobs ordered column or primary-key contract is invalid.",
            )
    triggers = document.get("triggers")
    if not isinstance(triggers, dict) or set(triggers) != set(_EXPECTED_TRIGGER_SQL_SHA256):
        raise SchemaInventoryError(
            "INVENTORY_DOCUMENT_INVALID",
            "The inventory document trigger set is invalid.",
        )
    for name, entry in triggers.items():
        if (
            not isinstance(entry, dict)
            or set(entry) != _TRIGGER_FIELDS
            or entry.get("normalizedSqlSha256") != _EXPECTED_TRIGGER_SQL_SHA256[name]
            or entry.get("behaviorContractVersion") != BEHAVIOR_CONTRACT_VERSION
        ):
            raise SchemaInventoryError(
                "INVENTORY_DOCUMENT_INVALID",
                f"The trigger contract is invalid for {name}.",
            )
    jobs = document.get("processingJobs")
    specs = document.get("processingJobSpecs")
    if (
        not isinstance(jobs, dict)
        or not isinstance(specs, dict)
        or set(jobs) != _PROCESSING_JOB_FIELDS
        or set(specs) != _PROCESSING_JOB_SPEC_FIELDS
        or not _is_nonnegative_int(jobs.get("count"))
        or not _is_sha256(jobs.get("sha256"))
        or not _is_nonnegative_int(specs.get("count"))
        or not _is_sha256(specs.get("sha256"))
        or jobs.get("count") != specs.get("count")
        or jobs.get("count") != tables["processing_jobs"]["count"]
        or jobs.get("strictDecodeCount") != jobs.get("count")
        or jobs.get("strictDecodeErrorCount") != 0
        or specs.get("projection") != ["id", "spec_json"]
    ):
        raise SchemaInventoryError(
            "INVENTORY_DOCUMENT_INVALID",
            "The processing job/spec projection contract is invalid.",
        )
    _validate_fts(document.get("fts"), tables)


def _validate_database_identity(value: Any) -> None:
    if not isinstance(value, dict) or tuple(value) != _DATABASE_IDENTITY_FIELDS:
        raise SchemaInventoryError(
            "INVENTORY_DOCUMENT_INVALID",
            "The inventory database identity schema is invalid.",
        )
    parent_subject = value["parentSubjectDatabaseId"]
    parent_manifest = value["parentIdentityManifestFileSha256"]
    if (
        not _is_sha256(value["databaseLineageId"])
        or not _is_sha256(value["subjectDatabaseId"])
        or not isinstance(value["subjectKind"], str)
        or _SUBJECT_KIND_PATTERN.fullmatch(value["subjectKind"]) is None
        or not _is_sha256(value["resolvedPathHash"])
        or not isinstance(value["parentBackupId"], str)
        or not value["parentBackupId"]
        or "\x00" in value["parentBackupId"]
        or not _is_sha256(value["parentManifestSha256"])
        or (parent_subject is not None and not _is_sha256(parent_subject))
        or (parent_manifest is not None and not _is_sha256(parent_manifest))
        or (parent_subject is None) != (parent_manifest is None)
        or not _is_sha256(value["originReceiptFileSha256"])
    ):
        raise SchemaInventoryError(
            "INVENTORY_DOCUMENT_INVALID",
            "The inventory database identity fields are invalid.",
        )
    try:
        platform_identity = PlatformFileIdentity.from_dict(value["platformFileIdentity"])
    except (DatabaseIdentityError, TypeError, KeyError) as error:
        raise SchemaInventoryError(
            "INVENTORY_DOCUMENT_INVALID",
            "The inventory platform file identity is invalid.",
        ) from error
    subject_document = {
        "version": 1,
        "databaseLineageId": value["databaseLineageId"],
        "subjectKind": value["subjectKind"],
        "resolvedPathHash": value["resolvedPathHash"],
        "platformFileIdentity": platform_identity.to_dict(),
        "parentBackupId": value["parentBackupId"],
        "parentManifestSha256": value["parentManifestSha256"],
    }
    expected_subject = hashlib.sha256(canonical_json_bytes(subject_document)).hexdigest()
    if value["subjectDatabaseId"] != expected_subject:
        raise SchemaInventoryError(
            "INVENTORY_DOCUMENT_INVALID",
            "The inventory subject database identity cannot be reproduced.",
        )


def _validate_fts(value: Any, tables: Mapping[str, Any]) -> None:
    if not isinstance(value, dict) or set(value) != _FTS_FIELDS:
        raise SchemaInventoryError(
            "INVENTORY_DOCUMENT_INVALID",
            "The inventory FTS fingerprint schema is invalid.",
        )
    logical_count = value["logicalCount"]
    shadow_tables = value["shadowTables"]
    if (
        value["virtualTableSqlSha256"] != _EXPECTED_TABLE_SQL_SHA256[_FTS_TABLE]
        or value["tokenizer"] != "trigram case_sensitive 0 remove_diacritics 1"
        or not _is_nonnegative_int(logical_count)
        or logical_count != tables["document_chunks"]["count"]
        or not _is_sha256(value["logicalSha256"])
        or value["externalContentRowidJoin"] is not True
        or not isinstance(shadow_tables, dict)
        or set(shadow_tables) != set(_FTS_SHADOW_TABLES)
    ):
        raise SchemaInventoryError(
            "INVENTORY_DOCUMENT_INVALID",
            "The inventory FTS fingerprint is invalid.",
        )
    for name, entry in shadow_tables.items():
        if (
            not isinstance(entry, dict)
            or set(entry) != _FTS_SHADOW_FIELDS
            or not _is_nonnegative_int(entry["count"])
            or not _is_sha256(entry["rowSha256"])
            or entry["normalizedSqlSha256"] != _EXPECTED_TABLE_SQL_SHA256[name]
        ):
            raise SchemaInventoryError(
                "INVENTORY_DOCUMENT_INVALID",
                f"The inventory FTS shadow fingerprint is invalid for {name}.",
            )
    if (
        shadow_tables["document_chunks_fts_data"]["count"] == 0
        or shadow_tables["document_chunks_fts_docsize"]["count"] != logical_count
    ):
        raise SchemaInventoryError(
            "INVENTORY_DOCUMENT_INVALID",
            "The inventory FTS shadow coverage is invalid.",
        )


def _is_nonnegative_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _is_sha256(value: Any) -> bool:
    return isinstance(value, str) and _SHA256_PATTERN.fullmatch(value) is not None


def _is_unique_string_list(value: Any, *, allow_empty: bool) -> bool:
    return (
        isinstance(value, list)
        and (allow_empty or bool(value))
        and all(isinstance(item, str) and item and "\x00" not in item for item in value)
        and len(value) == len(set(value))
    )


def _load_inventory(value: Mapping[str, Any] | str | os.PathLike[str]) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    path = _exact_existing_file(value, "inventory")
    payload = path.read_bytes()
    try:
        document = json.loads(payload.decode("utf-8"), object_pairs_hook=_reject_duplicates)
    except (UnicodeError, ValueError, json.JSONDecodeError) as error:
        raise SchemaInventoryError(
            "INVENTORY_DOCUMENT_INVALID",
            "The inventory is not valid strict canonical JSON.",
        ) from error
    if not isinstance(document, dict) or canonical_json_bytes(document) != payload:
        raise SchemaInventoryError(
            "INVENTORY_DOCUMENT_INVALID",
            "The inventory must use canonical JSON bytes.",
        )
    return document


def _reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate key")
        result[key] = value
    return result


def _row_bytes(row: Sequence[Any]) -> bytes:
    return canonical_json_bytes({"cells": [_cell(value) for value in row]})


def _cell(value: Any) -> dict[str, Any]:
    if value is None:
        return {"type": "null"}
    if isinstance(value, bytes):
        return {"type": "blob", "base64": base64.b64encode(value).decode("ascii")}
    if isinstance(value, int):
        return {"type": "integer", "value": str(value)}
    if isinstance(value, float):
        return {"type": "real", "value": format(value, ".17g")}
    if isinstance(value, str):
        return {"type": "text", "value": value}
    raise SchemaInventoryError(
        "INVENTORY_CELL_TYPE_INVALID",
        f"Unsupported SQLite value type: {type(value).__name__}",
    )


def _sequence_sha256(rows: Sequence[bytes]) -> str:
    digest = hashlib.sha256()
    for row in rows:
        digest.update(len(row).to_bytes(8, "big"))
        digest.update(row)
    return digest.hexdigest()


def _normalize_sql(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip())


def _sql_sha256(value: str) -> str:
    return hashlib.sha256(_normalize_sql(value).encode("utf-8")).hexdigest()


def _quote(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _exact_existing_file(value: str | os.PathLike[str], description: str) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        raise SchemaInventoryError(
            "INVENTORY_PATH_INVALID",
            f"The {description} path must be exact and absolute.",
        )
    resolved = path.resolve()
    if not resolved.is_file():
        raise SchemaInventoryError(
            "INVENTORY_PATH_INVALID",
            f"The {description} path does not name a file.",
        )
    return resolved


def _exact_output(value: str | os.PathLike[str]) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        raise SchemaInventoryError(
            "INVENTORY_PATH_INVALID",
            "The inventory output path must be exact and absolute.",
        )
    return path.resolve(strict=False)


def _file_state(database: Path, sidecars: Sequence[Path]) -> tuple[Any, ...]:
    metadata = database.stat()
    return (
        database.read_bytes(),
        metadata.st_mtime_ns,
        tuple(
            (path.name, path.read_bytes(), path.stat().st_mtime_ns) if path.exists() else (path.name, None, None)
            for path in sidecars
        ),
    )
