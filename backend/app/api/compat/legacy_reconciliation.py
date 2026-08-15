from __future__ import annotations

from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3
import unicodedata
from typing import Any, Mapping, Sequence

from backend.app.api.compat.database_identity import (
    canonical_json_bytes,
    exclusive_write_bytes,
    load_database_evidence_identity_manifest,
    verify_database_evidence_identity_subject,
)
from backend.app.api.compat.data_fingerprint import encode_row_v1


CLASSIFICATIONS = (
    "proven_migrated",
    "legacy_only_unprovable",
    "mismatch",
)


class LegacyReconciliationError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(f"{code}: {message}")


def reconcile_legacy_database(
    database: str | Path,
    database_identity_manifest: str | Path,
) -> dict[str, Any]:
    database_path = Path(database).expanduser().resolve(strict=True)
    identity = load_database_evidence_identity_manifest(database_identity_manifest)
    verify_database_evidence_identity_subject(database=database_path, identity=identity)
    sidecars = tuple(Path(f"{database_path}{suffix}") for suffix in ("-wal", "-shm", "-journal"))
    before = _file_state(database_path, sidecars)
    uri = database_path.as_uri() + "?mode=ro&immutable=1"
    try:
        with closing(sqlite3.connect(uri, uri=True)) as connection:
            connection.execute("PRAGMA query_only=ON")
            connection.execute("BEGIN")
            try:
                ledger = _capture(connection, identity)
                if connection.total_changes != 0:
                    raise LegacyReconciliationError(
                        "LEGACY_RECONCILIATION_WRITE_ATTEMPT",
                        "Reconciliation attempted to change SQLite state.",
                    )
            finally:
                connection.rollback()
    except LegacyReconciliationError:
        raise
    except sqlite3.Error as error:
        raise LegacyReconciliationError(
            "LEGACY_RECONCILIATION_READ_FAILED",
            "Legacy reconciliation could not be captured read-only.",
        ) from error
    if before != _file_state(database_path, sidecars):
        raise LegacyReconciliationError(
            "LEGACY_RECONCILIATION_READ_SIDE_EFFECT",
            "Read-only reconciliation changed database bytes or sidecars.",
        )
    validate_reconciliation_ledger(ledger)
    return ledger


def capture_legacy_reconciliation(
    *,
    database: str | Path,
    database_identity_manifest: str | Path,
    output: str | Path,
) -> dict[str, Any]:
    ledger = reconcile_legacy_database(database, database_identity_manifest)
    try:
        exclusive_write_bytes(
            Path(output).expanduser().resolve(strict=False),
            canonical_json_bytes(ledger),
        )
    except Exception as error:
        raise LegacyReconciliationError(
            "LEGACY_RECONCILIATION_OUTPUT_FAILED",
            "The reconciliation ledger could not be exclusively created.",
        ) from error
    return ledger


def assert_reconciliation_gate(ledger: Mapping[str, Any]) -> bool:
    validate_reconciliation_ledger(ledger)
    if ledger["classificationCounts"]["mismatch"] != 0:
        raise LegacyReconciliationError(
            "LEGACY_RECONCILIATION_MISMATCH",
            "Legacy reconciliation contains mismatched provenance evidence.",
        )
    return True


def validate_reconciliation_ledger(ledger: Mapping[str, Any]) -> None:
    items = ledger.get("items")
    counts = ledger.get("classificationCounts")
    if (
        ledger.get("schemaVersion") != 1
        or ledger.get("ledgerKind") != "legacy-reconciliation-v1"
        or not isinstance(items, list)
        or not isinstance(counts, dict)
        or tuple(counts) != CLASSIFICATIONS
        or ledger.get("itemCount") != len(items)
        or ledger.get("itemCount") != sum(counts.values())
        or any(counts[name] != sum(item.get("classification") == name for item in items) for name in CLASSIFICATIONS)
        or len({(item.get("paperId"), item.get("kind")) for item in items}) != len(items)
    ):
        raise _invalid()
    for item in items:
        if not _valid_item(item):
            raise _invalid()
    expected_input = {
        kind: _set_hash(
            (item["paperId"], item["kind"])
            for item in items
            if item["kind"] == kind
        )
        for kind in ("explainer", "translation")
    }
    expected_classifications = {
        classification: _set_hash(
            (item["paperId"], item["kind"])
            for item in items
            if item["classification"] == classification
        )
        for classification in CLASSIFICATIONS
    }
    if (
        ledger.get("inputSetHashes") != expected_input
        or ledger.get("classificationSetHashes") != expected_classifications
        or ledger.get("legacyAggregateSha256")
        != _aggregate(items, ("paperId", "kind", "legacyContentSha256"))
        or ledger.get("artifactAggregateSha256")
        != _aggregate(items, ("paperId", "kind", "artifactId", "artifactContentSha256"))
        or ledger.get("provenanceAggregateSha256")
        != _aggregate(
            items,
            (
                "paperId",
                "kind",
                "classification",
                "sourceDocumentId",
                "sourceContentSha256",
            ),
        )
    ):
        raise _invalid()
    preservation = ledger.get("preservation")
    if not isinstance(preservation, dict) or set(preservation) != {"notes", "paper_vectors"}:
        raise _invalid()
    for entry in preservation.values():
        if not _valid_preservation(entry):
            raise _invalid()


def _capture(connection: sqlite3.Connection, identity: Any) -> dict[str, Any]:
    revision = connection.execute(
        "SELECT version_num FROM alembic_version ORDER BY version_num"
    ).fetchall()
    if revision != [("20260807_03",)]:
        raise LegacyReconciliationError(
            "LEGACY_RECONCILIATION_REVISION_INVALID",
            "Reconciliation requires the P3 Alembic head.",
        )
    legacy_rows: list[tuple[str, str, str]] = []
    legacy_rows.extend(
        (str(paper_id), "explainer", str(content))
        for paper_id, content in connection.execute(
            "SELECT id,explainer FROM papers WHERE explainer IS NOT NULL AND length(explainer)>0"
        )
    )
    legacy_rows.extend(
        (str(paper_id), "translation", str(content))
        for paper_id, content in connection.execute(
            "SELECT paper_id,content FROM translations WHERE content IS NOT NULL AND length(content)>0"
        )
    )
    items = [
        _classify(connection, paper_id, kind, content)
        for paper_id, kind, content in sorted(legacy_rows, key=lambda row: (row[1], row[0]))
    ]
    counts = {
        classification: sum(item["classification"] == classification for item in items)
        for classification in CLASSIFICATIONS
    }
    return {
        "schemaVersion": 1,
        "ledgerKind": "legacy-reconciliation-v1",
        "databaseLineageId": identity.database_lineage_id,
        "subjectDatabaseId": identity.subject_database_id,
        "subjectKind": identity.subject_kind,
        "parentSubjectDatabaseId": identity.parent_subject_database_id,
        "parentIdentityManifestFileSha256": identity.parent_identity_manifest_file_sha256,
        "originReceiptFileSha256": identity.origin_receipt_file_sha256,
        "alembicRevision": "20260807_03",
        "itemCount": len(items),
        "classificationCounts": counts,
        "inputSetHashes": {
            kind: _set_hash(
                (item["paperId"], item["kind"])
                for item in items
                if item["kind"] == kind
            )
            for kind in ("explainer", "translation")
        },
        "classificationSetHashes": {
            classification: _set_hash(
                (item["paperId"], item["kind"])
                for item in items
                if item["classification"] == classification
            )
            for classification in CLASSIFICATIONS
        },
        "legacyAggregateSha256": _aggregate(
            items,
            ("paperId", "kind", "legacyContentSha256"),
        ),
        "artifactAggregateSha256": _aggregate(
            items,
            ("paperId", "kind", "artifactId", "artifactContentSha256"),
        ),
        "provenanceAggregateSha256": _aggregate(
            items,
            (
                "paperId",
                "kind",
                "classification",
                "sourceDocumentId",
                "sourceContentSha256",
            ),
        ),
        "preservation": {
            "notes": _preservation(connection, "notes"),
            "paper_vectors": _preservation(connection, "paper_vectors"),
        },
        "items": items,
    }


def _classify(
    connection: sqlite3.Connection,
    paper_id: str,
    kind: str,
    legacy_content: str,
) -> dict[str, Any]:
    legacy_sha = _content_sha(legacy_content)
    candidates = connection.execute(
        "SELECT id,source_document_id,status,content,content_sha256 "
        "FROM generated_artifacts WHERE paper_id=? AND kind=? ORDER BY id",
        (paper_id, kind),
    ).fetchall()
    exact = []
    for row in candidates:
        artifact_id, source_id, status, content, stored_sha = row
        actual_sha = _content_sha(content) if isinstance(content, str) else None
        if status == "ready" and actual_sha == legacy_sha and stored_sha == actual_sha:
            exact.append((str(artifact_id), source_id, str(stored_sha)))
    classification = "legacy_only_unprovable" if not candidates else "mismatch"
    artifact_id = artifact_sha = source_id = source_sha = None
    if len(exact) == 1:
        artifact_id, source_id, artifact_sha = exact[0]
        source = connection.execute(
            "SELECT paper_id,status,content_sha256 FROM document_sources WHERE id=?",
            (source_id,),
        ).fetchone()
        if (
            source is not None
            and source[0] == paper_id
            and source[1] == "ready"
            and _is_sha256(source[2])
        ):
            classification = "proven_migrated"
            source_id = str(source_id)
            source_sha = str(source[2])
        else:
            source_id = source_sha = None
    if classification != "proven_migrated":
        artifact_id = artifact_sha = source_id = source_sha = None
    return {
        "paperId": paper_id,
        "kind": kind,
        "classification": classification,
        "legacyContentSha256": legacy_sha,
        "artifactId": artifact_id,
        "artifactContentSha256": artifact_sha,
        "sourceDocumentId": source_id,
        "sourceContentSha256": source_sha,
    }


def _preservation(connection: sqlite3.Connection, table: str) -> dict[str, Any]:
    columns = [str(row[1]) for row in connection.execute(f'PRAGMA table_info("{table}")')]
    rows = connection.execute(
        f'SELECT {",".join(chr(34) + column + chr(34) for column in columns)} FROM "{table}" ORDER BY paper_id'
    ).fetchall()
    primary_keys = [str(row[0]) for row in rows]
    item_rows = [
        {"paperId": str(row[0]), "rowSha256": hashlib.sha256(encode_row_v1(row)).hexdigest()}
        for row in rows
    ]
    return {
        "count": len(rows),
        "primaryKeys": primary_keys,
        "primaryKeySetSha256": _set_hash((value,) for value in primary_keys),
        "rowSha256": _sequence_hash(
            bytes.fromhex(item["rowSha256"]) for item in item_rows
        ),
        "items": item_rows,
    }


def _valid_item(item: Any) -> bool:
    if not isinstance(item, dict) or set(item) != {
        "paperId", "kind", "classification", "legacyContentSha256",
        "artifactId", "artifactContentSha256", "sourceDocumentId", "sourceContentSha256",
    }:
        return False
    if (
        not isinstance(item["paperId"], str)
        or item["kind"] not in {"explainer", "translation"}
        or item["classification"] not in CLASSIFICATIONS
        or not _is_sha256(item["legacyContentSha256"])
    ):
        return False
    relation = tuple(
        item[name]
        for name in ("artifactId", "artifactContentSha256", "sourceDocumentId", "sourceContentSha256")
    )
    if item["classification"] == "proven_migrated":
        return (
            isinstance(relation[0], str)
            and bool(relation[0])
            and _is_sha256(relation[1])
            and isinstance(relation[2], str)
            and bool(relation[2])
            and _is_sha256(relation[3])
            and relation[1] == item["legacyContentSha256"]
        )
    return relation == (None, None, None, None)


def _valid_preservation(entry: Any) -> bool:
    if not isinstance(entry, dict) or set(entry) != {
        "count", "primaryKeys", "primaryKeySetSha256", "rowSha256", "items"
    }:
        return False
    keys = entry["primaryKeys"]
    items = entry["items"]
    if not (
        isinstance(entry["count"], int)
        and entry["count"] >= 0
        and isinstance(keys, list)
        and len(keys) == entry["count"]
        and isinstance(items, list)
        and len(items) == entry["count"]
        and _is_sha256(entry["primaryKeySetSha256"])
        and _is_sha256(entry["rowSha256"])
        and all(set(item) == {"paperId", "rowSha256"} and _is_sha256(item["rowSha256"]) for item in items)
    ):
        return False
    return (
        len(keys) == len(set(keys))
        and [item["paperId"] for item in items] == keys
        and entry["primaryKeySetSha256"] == _set_hash((value,) for value in keys)
        and entry["rowSha256"]
        == _sequence_hash(bytes.fromhex(item["rowSha256"]) for item in items)
    )


def _content_sha(value: str) -> str:
    return hashlib.sha256(unicodedata.normalize("NFC", value).encode("utf-8")).hexdigest()


def _aggregate(items: Sequence[Mapping[str, Any]], fields: Sequence[str]) -> str:
    rows = [canonical_json_bytes({field: item[field] for field in fields}) for item in items]
    return _sequence_hash(rows)


def _set_hash(values: Any) -> str:
    rows = sorted(canonical_json_bytes({"key": list(value)}) for value in values)
    return _sequence_hash(rows)


def _sequence_hash(values: Any) -> str:
    hasher = hashlib.sha256()
    for value in values:
        hasher.update(len(value).to_bytes(8, "big"))
        hasher.update(value)
    return hasher.hexdigest()


def _is_sha256(value: Any) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(c in "0123456789abcdef" for c in value)


def _file_state(database: Path, sidecars: Sequence[Path]) -> tuple[Any, ...]:
    return (
        database.read_bytes(),
        database.stat().st_mtime_ns,
        tuple((path.exists(), path.read_bytes() if path.exists() else None) for path in sidecars),
    )


def _invalid() -> LegacyReconciliationError:
    return LegacyReconciliationError(
        "LEGACY_RECONCILIATION_INVALID",
        "The legacy reconciliation ledger is incomplete or inconsistent.",
    )


__all__ = [
    "LegacyReconciliationError",
    "assert_reconciliation_gate",
    "capture_legacy_reconciliation",
    "reconcile_legacy_database",
    "validate_reconciliation_ledger",
]
