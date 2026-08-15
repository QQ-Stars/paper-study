from __future__ import annotations

from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3
import struct
import unicodedata
from typing import Any, Mapping, Sequence

from backend.app.api.compat.database_identity import canonical_json_bytes
from backend.app.api.compat.database_identity import exclusive_write_bytes
from backend.app.api.compat.schema_inventory import (
    PROCESSING_JOB_COLUMNS,
    _EXPECTED_TABLE_SQL_SHA256,
    _EXPECTED_TRIGGER_SQL_SHA256,
    _capture_fts,
    _sql_sha256,
    _validate_fts,
)
from backend.app.api.compat.schema_inventory import SchemaInventoryError
from backend.app.domain.processing import JobSpecValidationError, decode_job_spec_v1


APPLICATION_TABLES = (
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
    "document_sources",
    "generated_artifacts",
    "processing_jobs",
    "document_chunks",
    "obsidian_exports",
    "paper_artifact_heads",
    "processing_job_events",
    "ocr_page_checkpoints",
    "document_chunk_embeddings",
    "artifact_translation_checkpoints",
)
LEGACY_TABLES = APPLICATION_TABLES[:12]
_REPORT_FIELDS = (
    "schemaVersion",
    "fingerprintKind",
    "alembic",
    "integrity",
    "tables",
    "legacyColumnHashes",
    "processingJobSpecs",
    "triggers",
    "fts",
    "canonicalDataSha256",
)


class DataFingerprintError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(f"{code}: {message}")


def encode_scalar_v1(value: Any) -> dict[str, str]:
    if value is None:
        return {"type": "null"}
    if isinstance(value, bool):
        raise _unsupported(value)
    if isinstance(value, int):
        return {"type": "integer", "value": str(value)}
    if isinstance(value, float):
        return {"type": "real", "value": struct.pack(">d", value).hex()}
    if isinstance(value, str):
        return {"type": "text", "value": unicodedata.normalize("NFC", value)}
    if isinstance(value, (bytes, bytearray, memoryview)):
        return {"type": "blob", "value": bytes(value).hex()}
    raise _unsupported(value)


def encode_row_v1(row: Sequence[Any]) -> bytes:
    return json.dumps(
        {"version": 1, "cells": [encode_scalar_v1(value) for value in row]},
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")


def fingerprint_database(database: str | Path) -> dict[str, Any]:
    database_path = Path(database).expanduser().resolve(strict=True)
    uri = database_path.as_uri() + "?mode=ro&immutable=1"
    try:
        with closing(sqlite3.connect(uri, uri=True)) as connection:
            connection.execute("PRAGMA query_only=ON")
            connection.execute("BEGIN")
            try:
                report = _capture_fingerprint(connection)
            finally:
                connection.rollback()
    except DataFingerprintError:
        raise
    except (OSError, sqlite3.Error) as error:
        raise DataFingerprintError(
            "FINGERPRINT_DATABASE_READ_FAILED",
            "The canonical data fingerprint could not be captured read-only.",
        ) from error
    validate_fingerprint_document(report)
    return report


def capture_fingerprint(
    *,
    database: str | Path,
    output: str | Path,
) -> dict[str, Any]:
    report = fingerprint_database(database)
    output_path = Path(output).expanduser().resolve(strict=False)
    try:
        exclusive_write_bytes(output_path, canonical_json_bytes(report))
    except Exception as error:
        raise DataFingerprintError(
            "FINGERPRINT_OUTPUT_FAILED",
            "The fingerprint output could not be exclusively created.",
        ) from error
    return report


def load_fingerprint_document(value: str | Path) -> dict[str, Any]:
    try:
        path = Path(value).expanduser().resolve(strict=True)
        payload = path.read_bytes()
        document = json.loads(payload.decode("utf-8"), object_pairs_hook=_reject_duplicates)
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as error:
        raise DataFingerprintError(
            "FINGERPRINT_DOCUMENT_INVALID",
            "The fingerprint input is not strict canonical JSON.",
        ) from error
    if (
        not isinstance(document, dict)
        or canonical_json_bytes(document) != payload
    ):
        raise _invalid_document()
    validate_fingerprint_document(document)
    return document


def validate_fingerprint_document(document: Mapping[str, Any]) -> None:
    if (
        not isinstance(document, Mapping)
        or tuple(document) != _REPORT_FIELDS
        or document.get("schemaVersion") != 1
        or document.get("fingerprintKind") != "p6-canonical-data-v1"
        or document.get("alembic") != {"revision": "20260807_03", "count": 1}
        or document.get("integrity")
        != {"quickCheck": "ok", "foreignKeyViolationCount": 0}
    ):
        raise _invalid_document()
    tables = document.get("tables")
    if not isinstance(tables, dict) or tuple(tables) != tuple(sorted(APPLICATION_TABLES)):
        raise _invalid_document()
    required_table_fields = {
        "count",
        "columns",
        "primaryKeyColumns",
        "primaryKeySetSha256",
        "rowSha256",
        "normalizedSqlSha256",
    }
    for name, value in tables.items():
        if (
            not isinstance(value, dict)
            or set(value) != required_table_fields
            or not _nonnegative_int(value.get("count"))
            or not _sha256(value.get("primaryKeySetSha256"))
            or not _sha256(value.get("rowSha256"))
            or value.get("normalizedSqlSha256") != _EXPECTED_TABLE_SQL_SHA256[name]
            or not isinstance(value.get("columns"), list)
            or not isinstance(value.get("primaryKeyColumns"), list)
        ):
            raise _invalid_document()
    specs = document.get("processingJobSpecs")
    if (
        not isinstance(specs, dict)
        or set(specs)
        != {"count", "sha256", "projection", "strictDecodeCount", "strictDecodeErrorCount"}
        or specs.get("count") != tables["processing_jobs"]["count"]
        or specs.get("projection") != ["id", "spec_json"]
        or specs.get("strictDecodeCount") != specs.get("count")
        or specs.get("strictDecodeErrorCount") != 0
        or not _sha256(specs.get("sha256"))
    ):
        raise _invalid_document()
    legacy = document.get("legacyColumnHashes")
    if not isinstance(legacy, dict) or set(legacy) != {"explainer", "translation"}:
        raise _invalid_document()
    for value in legacy.values():
        if (
            not isinstance(value, dict)
            or set(value) != {"count", "sha256"}
            or not _nonnegative_int(value.get("count"))
            or not _sha256(value.get("sha256"))
        ):
            raise _invalid_document()
    triggers = document.get("triggers")
    if not isinstance(triggers, dict) or set(triggers) != set(_EXPECTED_TRIGGER_SQL_SHA256):
        raise _invalid_document()
    for name, value in triggers.items():
        if value != {"normalizedSqlSha256": _EXPECTED_TRIGGER_SQL_SHA256[name]}:
            raise _invalid_document()
    try:
        _validate_fts(document.get("fts"), tables)
    except SchemaInventoryError as error:
        raise _invalid_document() from error
    expected_sha = hashlib.sha256(
        canonical_json_bytes({key: document[key] for key in _REPORT_FIELDS[:-1]})
    ).hexdigest()
    if document.get("canonicalDataSha256") != expected_sha:
        raise _invalid_document()


def compare_fingerprints(
    before: Mapping[str, Any],
    after: Mapping[str, Any],
    *,
    mode: str,
    delta_ledger: Mapping[str, Any] | None = None,
) -> bool:
    validate_fingerprint_document(before)
    validate_fingerprint_document(after)
    if mode == "strict-readonly":
        if delta_ledger is not None:
            raise DataFingerprintError(
                "FINGERPRINT_COMPARE_ARGUMENT_INVALID",
                "Strict comparison does not accept a delta ledger.",
            )
        if before != after:
            raise DataFingerprintError(
                "FINGERPRINT_MISMATCH",
                "The canonical data fingerprint changed.",
            )
        return True
    if mode != "explained-write" or delta_ledger is None:
        raise DataFingerprintError(
            "FINGERPRINT_COMPARE_ARGUMENT_INVALID",
            "The comparison mode or delta ledger is invalid.",
        )
    _compare_explained_write(before, after, delta_ledger)
    return True


def compare_backup_logical_evidence(
    before: Mapping[str, Any],
    after: Mapping[str, Any],
) -> bool:
    before_sha = before.get("logicalSha256") if isinstance(before, Mapping) else None
    after_sha = after.get("logicalSha256") if isinstance(after, Mapping) else None
    if not _sha256(before_sha) or not _sha256(after_sha):
        raise DataFingerprintError(
            "BACKUP_LOGICAL_EVIDENCE_INVALID",
            "Cutover equality requires P0 backup-compatible logicalSha256 evidence.",
        )
    if before_sha != after_sha:
        raise DataFingerprintError(
            "BACKUP_LOGICAL_MISMATCH",
            "The backup-compatible logical database fingerprints differ.",
        )
    return True


def _compare_explained_write(
    before: Mapping[str, Any],
    after: Mapping[str, Any],
    ledger: Mapping[str, Any],
) -> None:
    if set(ledger) != {"schemaVersion", "entries"} or ledger.get("schemaVersion") != 1:
        raise _invalid_delta()
    entries = ledger.get("entries")
    if not isinstance(entries, list) or not entries:
        raise _invalid_delta()
    for key in ("schemaVersion", "fingerprintKind", "alembic", "integrity", "triggers"):
        if before[key] != after[key]:
            raise _mismatch()
    before_tables = before["tables"]
    after_tables = after["tables"]
    for name in LEGACY_TABLES:
        if before_tables[name] != after_tables[name]:
            raise _mismatch()
    if before["legacyColumnHashes"] != after["legacyColumnHashes"]:
        raise _mismatch()
    changed = {
        name
        for name in APPLICATION_TABLES
        if before_tables[name] != after_tables[name]
    }
    entry_tables = {entry.get("table") for entry in entries if isinstance(entry, Mapping)}
    if (
        not changed
        or entry_tables != changed
        or any(name in LEGACY_TABLES for name in changed)
    ):
        raise _invalid_delta()
    seen_keys: set[tuple[str, str]] = set()
    count_deltas = {name: 0 for name in changed}
    for entry in entries:
        if not isinstance(entry, Mapping):
            raise _invalid_delta()
        table = str(entry.get("table"))
        _validate_delta_entry(
            entry,
            before_tables[table],
            after_tables[table],
        )
        count_deltas[table] += int(entry["countDelta"])
        for primary_key in entry["primaryKeys"]:
            key = (table, primary_key)
            if key in seen_keys:
                raise _invalid_delta()
            seen_keys.add(key)
    if any(
        count_deltas[name] != after_tables[name]["count"] - before_tables[name]["count"]
        for name in changed
    ):
        raise _invalid_delta()
    if "processing_jobs" not in changed and before["processingJobSpecs"] != after["processingJobSpecs"]:
        raise _mismatch()
    if "document_chunks" not in changed and before["fts"] != after["fts"]:
        raise _mismatch()


def _validate_delta_entry(
    entry: Mapping[str, Any],
    before: Mapping[str, Any],
    after: Mapping[str, Any],
) -> None:
    fields = {
        "table",
        "operation",
        "primaryKeys",
        "countDelta",
        "beforePrimaryKeySetSha256",
        "afterPrimaryKeySetSha256",
        "beforeRowSha256",
        "afterRowSha256",
        "jobId",
        "sourceDocumentId",
        "artifactId",
        "evidenceSha256",
    }
    primary_keys = entry.get("primaryKeys")
    if (
        set(entry) != fields
        or entry.get("operation") not in {"insert", "update", "delete"}
        or not isinstance(primary_keys, list)
        or not primary_keys
        or len(primary_keys) != len(set(primary_keys))
        or not all(isinstance(value, str) and value for value in primary_keys)
        or not all(
            isinstance(entry.get(name), str) and entry.get(name)
            for name in ("jobId", "sourceDocumentId", "artifactId")
        )
        or entry.get("countDelta")
        != {"insert": 1, "update": 0, "delete": -1}[entry["operation"]]
        or entry.get("beforePrimaryKeySetSha256") != before["primaryKeySetSha256"]
        or entry.get("afterPrimaryKeySetSha256") != after["primaryKeySetSha256"]
        or entry.get("beforeRowSha256") != before["rowSha256"]
        or entry.get("afterRowSha256") != after["rowSha256"]
    ):
        raise _invalid_delta()
    unsigned = {key: value for key, value in entry.items() if key != "evidenceSha256"}
    if entry.get("evidenceSha256") != hashlib.sha256(canonical_json_bytes(unsigned)).hexdigest():
        raise _invalid_delta()


def _invalid_delta() -> DataFingerprintError:
    return DataFingerprintError(
        "FINGERPRINT_DELTA_LEDGER_INVALID",
        "The explained-write delta ledger does not exactly explain the changes.",
    )


def _mismatch() -> DataFingerprintError:
    return DataFingerprintError(
        "FINGERPRINT_MISMATCH",
        "The canonical data fingerprint contains an unexplained change.",
    )


def _capture_fingerprint(connection: sqlite3.Connection) -> dict[str, Any]:
    quick_rows = connection.execute("PRAGMA quick_check").fetchall()
    foreign_keys = connection.execute("PRAGMA foreign_key_check").fetchall()
    if quick_rows != [("ok",)] or foreign_keys:
        raise DataFingerprintError(
            "FINGERPRINT_INTEGRITY_FAILED",
            "SQLite integrity or foreign-key verification failed.",
        )
    schema_rows = connection.execute(
        "SELECT type,name,sql FROM sqlite_schema "
        "WHERE type IN ('table','trigger') AND name NOT LIKE 'sqlite_%' "
        "ORDER BY type,name"
    ).fetchall()
    tables = {
        str(name): str(sql)
        for type_, name, sql in schema_rows
        if type_ == "table"
    }
    missing = set((*APPLICATION_TABLES, "alembic_version", "document_chunks_fts")) - set(tables)
    if missing:
        raise DataFingerprintError(
            "REQUIRED_TABLE_MISSING",
            "A required application table is missing.",
        )
    revision_rows = connection.execute(
        "SELECT version_num FROM alembic_version ORDER BY version_num"
    ).fetchall()
    if revision_rows != [("20260807_03",)]:
        raise DataFingerprintError(
            "FINGERPRINT_REVISION_INVALID",
            "The fingerprint requires exactly one P3 Alembic head.",
        )
    table_fingerprints = {
        name: _fingerprint_table(connection, name, tables[name])
        for name in sorted(APPLICATION_TABLES)
    }
    trigger_sql = {
        str(name): str(sql)
        for type_, name, sql in schema_rows
        if type_ == "trigger"
    }
    if set(trigger_sql) != set(_EXPECTED_TRIGGER_SQL_SHA256):
        raise DataFingerprintError(
            "FINGERPRINT_TRIGGER_INVENTORY_INVALID",
            "The exact five-trigger inventory changed.",
        )
    triggers: dict[str, dict[str, str]] = {}
    for name in sorted(_EXPECTED_TRIGGER_SQL_SHA256):
        actual = _sql_sha256(trigger_sql[name])
        if actual != _EXPECTED_TRIGGER_SQL_SHA256[name]:
            raise DataFingerprintError(
                "FINGERPRINT_TRIGGER_SQL_INVALID",
                "A required trigger definition changed.",
            )
        triggers[name] = {"normalizedSqlSha256": actual}
    unsigned: dict[str, Any] = {
        "schemaVersion": 1,
        "fingerprintKind": "p6-canonical-data-v1",
        "alembic": {"revision": "20260807_03", "count": 1},
        "integrity": {"quickCheck": "ok", "foreignKeyViolationCount": 0},
        "tables": table_fingerprints,
        "legacyColumnHashes": {
            "explainer": _column_stream(
                connection,
                "SELECT id,explainer FROM papers ORDER BY id",
            ),
            "translation": _column_stream(
                connection,
                "SELECT paper_id,content FROM translations ORDER BY paper_id",
            ),
        },
        "processingJobSpecs": _processing_job_specs(connection),
        "triggers": triggers,
        "fts": _capture_fts(connection, tables["document_chunks_fts"]),
    }
    return {
        **unsigned,
        "canonicalDataSha256": hashlib.sha256(canonical_json_bytes(unsigned)).hexdigest(),
    }


def _fingerprint_table(
    connection: sqlite3.Connection,
    name: str,
    schema_sql: str,
) -> dict[str, Any]:
    metadata = connection.execute(f"PRAGMA table_info({_quote(name)})").fetchall()
    columns = tuple(str(row[1]) for row in metadata)
    if name == "processing_jobs" and columns != PROCESSING_JOB_COLUMNS:
        raise DataFingerprintError(
            "FINGERPRINT_PROCESSING_COLUMNS_INVALID",
            "The processing_jobs ordered column contract changed.",
        )
    rows = connection.execute(
        f"SELECT {','.join(_quote(column) for column in columns)} FROM {_quote(name)}"
    ).fetchall()
    encoded_rows = sorted(encode_row_v1(row) for row in rows)
    primary_columns = tuple(
        str(row[1])
        for row in sorted(metadata, key=lambda item: int(item[5]))
        if int(row[5]) > 0
    )
    primary_indexes = tuple(columns.index(column) for column in primary_columns)
    primary_rows = (
        sorted(
            encode_row_v1(tuple(row[index] for index in primary_indexes))
            for row in rows
        )
        if primary_indexes
        else encoded_rows
    )
    actual_sql_sha = _sql_sha256(schema_sql)
    if actual_sql_sha != _EXPECTED_TABLE_SQL_SHA256[name]:
        raise DataFingerprintError(
            "FINGERPRINT_TABLE_SQL_INVALID",
            f"The normalized schema SQL changed for {name}.",
        )
    return {
        "count": len(rows),
        "columns": list(columns),
        "primaryKeyColumns": list(primary_columns),
        "primaryKeySetSha256": _sequence_sha256(primary_rows),
        "rowSha256": _sequence_sha256(encoded_rows),
        "normalizedSqlSha256": actual_sql_sha,
    }


def _processing_job_specs(connection: sqlite3.Connection) -> dict[str, Any]:
    rows = connection.execute(
        "SELECT " + ",".join(_quote(column) for column in PROCESSING_JOB_COLUMNS)
        + " FROM processing_jobs ORDER BY id"
    ).fetchall()
    encoded: list[bytes] = []
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
            raise DataFingerprintError(
                "FINGERPRINT_JOB_SPEC_INVALID",
                "A processing job contains a non-canonical or mismatched spec.",
            ) from error
        encoded.append(encode_row_v1((mapped["id"], mapped["spec_json"])))
    return {
        "count": len(rows),
        "sha256": _sequence_sha256(encoded),
        "projection": ["id", "spec_json"],
        "strictDecodeCount": len(rows),
        "strictDecodeErrorCount": 0,
    }


def _column_stream(connection: sqlite3.Connection, statement: str) -> dict[str, Any]:
    rows = connection.execute(statement).fetchall()
    return {"count": len(rows), "sha256": _sequence_sha256([encode_row_v1(row) for row in rows])}


def _sequence_sha256(rows: Sequence[bytes]) -> str:
    hasher = hashlib.sha256()
    for row in rows:
        hasher.update(len(row).to_bytes(8, "big"))
        hasher.update(row)
    return hasher.hexdigest()


def _quote(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _nonnegative_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _sha256(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _invalid_document() -> DataFingerprintError:
    return DataFingerprintError(
        "FINGERPRINT_DOCUMENT_INVALID",
        "The canonical data fingerprint document is invalid.",
    )


def _reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate key")
        result[key] = value
    return result


def _unsupported(value: Any) -> DataFingerprintError:
    return DataFingerprintError(
        "FINGERPRINT_TYPE_UNSUPPORTED",
        f"SQLite returned unsupported value type {type(value).__name__!r}.",
    )


__all__ = [
    "APPLICATION_TABLES",
    "DataFingerprintError",
    "compare_backup_logical_evidence",
    "compare_fingerprints",
    "capture_fingerprint",
    "encode_row_v1",
    "encode_scalar_v1",
    "fingerprint_database",
    "load_fingerprint_document",
    "validate_fingerprint_document",
]
