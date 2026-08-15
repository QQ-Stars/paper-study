from __future__ import annotations

import copy
from contextlib import closing
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
import unittest

from backend.tests.support.p4_identity import p4_identity_fixture


EXPECTED_PROCESSING_JOB_COLUMNS = (
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
EXPECTED_TRIGGERS = {
    "processing_jobs_spec_guard_insert",
    "processing_jobs_spec_guard_update",
    "document_chunks_fts_ai",
    "document_chunks_fts_ad",
    "document_chunks_fts_au",
}


def _identity(fixture: object, output: Path, *, database: Path | None = None) -> Path:
    from backend.app.api.compat.database_identity import DatabaseEvidenceIdentityService

    DatabaseEvidenceIdentityService().create_live_database_identity(
        database=fixture.database_path if database is None else database,
        p0_origin_receipt=fixture.receipt_path,
        expected_p0_origin_receipt_sha256=fixture.receipt_file_sha256,
        origin_backup=fixture.origin_backup_path,
        origin_manifest=fixture.origin_manifest_path,
        output=output,
    )
    return output


class SchemaInventoryTests(unittest.TestCase):
    def _assert_spec_guard_behavior(self, database: Path) -> None:
        from backend.app.domain.processing import (
            LegacyImportedJobSpecV1,
            encode_job_spec_v1,
        )

        native_spec = encode_job_spec_v1(
            LegacyImportedJobSpecV1("explain", "paper-1", "native")
        )
        ocr_spec = encode_job_spec_v1(
            LegacyImportedJobSpecV1("explain", "paper-1", "ocr")
        )
        mismatched_spec = encode_job_spec_v1(
            LegacyImportedJobSpecV1("explain", "paper-2", "native")
        )
        insert_sql = (
            "INSERT INTO processing_jobs("
            "id,paper_id,job_type,source_mode,status,progress_json,attempt,"
            "max_attempts,idempotency_key,spec_json) VALUES(?,?,?,?,?,?,?,?,?,?)"
        )
        with closing(sqlite3.connect(database)) as connection:
            connection.execute(
                insert_sql,
                (
                    "inventory-oracle-valid",
                    "paper-1",
                    "explain",
                    "native",
                    "queued",
                    "{}",
                    0,
                    2,
                    "inventory-oracle-valid",
                    native_spec,
                ),
            )
            connection.commit()
            with self.assertRaisesRegex(sqlite3.IntegrityError, "JOB_SPEC_INVALID"):
                connection.execute(
                    insert_sql,
                    (
                        "inventory-oracle-invalid",
                        "paper-1",
                        "explain",
                        "native",
                        "queued",
                        "{}",
                        0,
                        2,
                        "inventory-oracle-invalid",
                        mismatched_spec,
                    ),
                )
            connection.rollback()
            connection.execute(
                "UPDATE processing_jobs SET source_mode='ocr',spec_json=? WHERE id=?",
                (ocr_spec, "inventory-oracle-valid"),
            )
            connection.commit()
            expected_row = connection.execute(
                "SELECT source_mode,spec_json FROM processing_jobs WHERE id=?",
                ("inventory-oracle-valid",),
            ).fetchone()
            with self.assertRaisesRegex(sqlite3.IntegrityError, "JOB_SPEC_INVALID"):
                connection.execute(
                    "UPDATE processing_jobs SET source_mode='native' WHERE id=?",
                    ("inventory-oracle-valid",),
                )
            connection.rollback()
            self.assertEqual(
                expected_row,
                connection.execute(
                    "SELECT source_mode,spec_json FROM processing_jobs WHERE id=?",
                    ("inventory-oracle-valid",),
                ).fetchone(),
            )
            noncanonical = json.dumps(json.loads(ocr_spec), indent=2, sort_keys=True)
            with self.assertRaisesRegex(sqlite3.IntegrityError, "JOB_SPEC_INVALID"):
                connection.execute(
                    "UPDATE processing_jobs SET spec_json=? WHERE id=?",
                    (noncanonical, "inventory-oracle-valid"),
                )
            connection.rollback()

    def _assert_fts_trigger_behavior(self, database: Path) -> None:
        inserted = "inventoryoracleinsertalpha"
        updated = "inventoryoracleupdatebeta"
        with closing(sqlite3.connect(database)) as connection:
            source = connection.execute(
                "SELECT id FROM document_sources ORDER BY id LIMIT 1"
            ).fetchone()
            if source is None:
                source_markdown = "inventory oracle source"
                connection.execute(
                    "INSERT INTO document_sources("
                    "id,paper_id,mode,status,provider,model,pdf_sha256,options_hash,"
                    "content_sha256,markdown,page_count,processing_version,created_at,updated_at) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        "inventory-oracle-source",
                        "paper-1",
                        "native",
                        "ready",
                        "inventory-oracle",
                        "inventory-oracle",
                        "b" * 64,
                        "c" * 64,
                        hashlib.sha256(source_markdown.encode()).hexdigest(),
                        source_markdown,
                        1,
                        "inventory-oracle-v1",
                        "2026-08-14T00:00:00Z",
                        "2026-08-14T00:00:00Z",
                    ),
                )
                connection.commit()
                source = ("inventory-oracle-source",)
            sequence = int(
                connection.execute(
                    "SELECT COALESCE(max(sequence),-1)+100 FROM document_chunks "
                    "WHERE source_document_id=?",
                    (source[0],),
                ).fetchone()[0]
            )
            cursor = connection.execute(
                "INSERT INTO document_chunks("
                "id,source_document_id,sequence,heading_path,page_start,page_end,content,"
                "content_sha256,token_count,status,content_kind,chunk_key,chunking_version,"
                "source_content_sha256,char_start,char_end,created_at,updated_at,stale_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    "inventory-oracle-chunk",
                    source[0],
                    sequence,
                    "Oracle",
                    1,
                    1,
                    inserted,
                    hashlib.sha256(inserted.encode()).hexdigest(),
                    4,
                    "ready",
                    "text",
                    "inventory-oracle-chunk-key",
                    "inventory-oracle-v1",
                    "a" * 64,
                    0,
                    len(inserted),
                    "2026-08-14T00:00:00Z",
                    "2026-08-14T00:00:00Z",
                    None,
                ),
            )
            rowid = int(cursor.lastrowid)
            connection.commit()

            def matches(term: str) -> set[int]:
                return {
                    int(row[0])
                    for row in connection.execute(
                        "SELECT rowid FROM document_chunks_fts "
                        "WHERE document_chunks_fts MATCH ?",
                        (term,),
                    ).fetchall()
                }

            self.assertIn(rowid, matches(inserted))
            connection.execute(
                "UPDATE document_chunks SET content=?,content_sha256=?,char_end=?,updated_at=? "
                "WHERE rowid=?",
                (
                    updated,
                    hashlib.sha256(updated.encode()).hexdigest(),
                    len(updated),
                    "2026-08-14T00:00:01Z",
                    rowid,
                ),
            )
            connection.commit()
            self.assertNotIn(rowid, matches(inserted))
            self.assertIn(rowid, matches(updated))
            connection.execute("DELETE FROM document_chunks WHERE rowid=?", (rowid,))
            connection.commit()
            self.assertNotIn(rowid, matches(updated))

    def test_cli_capture_and_compare_require_exact_paths(self) -> None:
        from backend.app.cli.schema_inventory import run

        with p4_identity_fixture() as fixture:
            identity = _identity(fixture, fixture.root / "cli-identity.json")
            first = fixture.root / "cli-before.json"
            second = fixture.root / "cli-after.json"
            captured = run(
                [
                    "capture",
                    "--database",
                    str(fixture.database_path),
                    "--database-identity-manifest",
                    str(identity),
                    "--output",
                    str(first),
                ]
            )
            self.assertTrue(captured["ok"])
            run(
                [
                    "capture",
                    "--database",
                    str(fixture.database_path),
                    "--database-identity-manifest",
                    str(identity),
                    "--output",
                    str(second),
                ]
            )
            compared = run(["compare", "--before", str(first), "--after", str(second)])
            self.assertTrue(compared["ok"])

    def test_inventory_requires_exact_p4_p5_object_set(self) -> None:
        from backend.app.api.compat.schema_inventory import capture_inventory

        with p4_identity_fixture() as fixture:
            identity = _identity(fixture, fixture.root / "identity.json")
            output = fixture.root / "inventory.json"
            inventory = capture_inventory(
                database=fixture.database_path,
                database_identity_manifest=identity,
                output=output,
            )
            self.assertEqual(1, inventory["schemaVersion"])
            self.assertEqual("20260807_03", inventory["alembic"]["revision"])
            self.assertEqual(
                EXPECTED_PROCESSING_JOB_COLUMNS,
                tuple(inventory["tables"]["processing_jobs"]["columns"]),
            )
            self.assertEqual(EXPECTED_TRIGGERS, set(inventory["triggers"]))
            self.assertEqual(
                inventory["tables"]["processing_jobs"]["count"],
                inventory["processingJobs"]["count"],
            )
            self.assertEqual(
                inventory["processingJobs"]["count"],
                inventory["processingJobSpecs"]["count"],
            )
            self.assertEqual(0, inventory["processingJobs"]["strictDecodeErrorCount"])
            self.assertTrue(inventory["fts"]["externalContentRowidJoin"])
            self.assertTrue(output.is_file())
            self.assertEqual(output.read_bytes(), json.dumps(inventory, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))

    def test_strict_compare_rejects_missing_or_changed_legacy_aux_spec_or_fts_object(
        self,
    ) -> None:
        from backend.app.api.compat.schema_inventory import (
            SchemaInventoryError,
            capture_inventory,
            compare_inventory,
        )

        with p4_identity_fixture() as fixture:
            before_identity = _identity(fixture, fixture.root / "before-identity.json")
            before = capture_inventory(
                database=fixture.database_path,
                database_identity_manifest=before_identity,
                output=fixture.root / "before.json",
            )
            capture_rejections = {
                "missing_spec_guard": "DROP TRIGGER processing_jobs_spec_guard_insert",
                "extra_lookalike": (
                    "CREATE TRIGGER processing_jobs_spec_guard_lookalike "
                    "AFTER INSERT ON papers BEGIN SELECT 1; END"
                ),
                "fts_join": "DELETE FROM document_chunks_fts_data",
            }
            for name, mutation in capture_rejections.items():
                with self.subTest(mutation=name):
                    database = fixture.root / f"{name}.db"
                    shutil.copyfile(fixture.database_path, database)
                    with closing(sqlite3.connect(database)) as connection:
                        connection.execute(mutation)
                        connection.commit()
                    identity = _identity(
                        fixture,
                        fixture.root / f"{name}-identity.json",
                        database=database,
                    )
                    with self.assertRaises(SchemaInventoryError):
                        capture_inventory(
                            database=database,
                            database_identity_manifest=identity,
                            output=fixture.root / f"{name}-inventory.json",
                        )
            unchanged_identity = _identity(fixture, fixture.root / "unchanged-identity.json")
            unchanged = capture_inventory(
                database=fixture.database_path,
                database_identity_manifest=unchanged_identity,
                output=fixture.root / "unchanged.json",
            )
            self.assertTrue(compare_inventory(before, unchanged))

            with closing(sqlite3.connect(fixture.database_path)) as connection:
                connection.execute(
                    "UPDATE papers SET title='inventory mutation' "
                    "WHERE rowid=(SELECT min(rowid) FROM papers)"
                )
                connection.commit()
            changed_identity = _identity(
                fixture,
                fixture.root / "legacy-row-identity.json",
            )
            changed = capture_inventory(
                database=fixture.database_path,
                database_identity_manifest=changed_identity,
                output=fixture.root / "legacy-row-inventory.json",
            )
            with self.assertRaises(SchemaInventoryError):
                compare_inventory(before, changed)

    def test_compare_rejects_forged_or_minimal_inventory_evidence(self) -> None:
        from backend.app.api.compat.schema_inventory import (
            SchemaInventoryError,
            capture_inventory,
            compare_inventory,
        )

        with p4_identity_fixture() as fixture:
            identity = _identity(fixture, fixture.root / "strict-identity.json")
            valid = capture_inventory(
                database=fixture.database_path,
                database_identity_manifest=identity,
                output=fixture.root / "strict-inventory.json",
            )

            def remove_database_identity(document: dict[str, object]) -> None:
                document.pop("databaseIdentity")

            def add_unknown_top_level(document: dict[str, object]) -> None:
                document["forged"] = True

            def replace_identity_hash(document: dict[str, object]) -> None:
                document["databaseIdentity"]["subjectDatabaseId"] = "0" * 64  # type: ignore[index]

            def minimize_table_fingerprint(document: dict[str, object]) -> None:
                tables = document["tables"]  # type: ignore[assignment]
                tables["papers"] = {  # type: ignore[index]
                    "normalizedSqlSha256": tables["papers"]["normalizedSqlSha256"]  # type: ignore[index]
                }

            def remove_processing_hash(document: dict[str, object]) -> None:
                document["processingJobs"].pop("sha256")  # type: ignore[union-attr]

            def remove_fts_fingerprint(document: dict[str, object]) -> None:
                document["fts"].pop("logicalSha256")  # type: ignore[union-attr]

            def remove_fts_shadow_table(document: dict[str, object]) -> None:
                document["fts"]["shadowTables"].pop(  # type: ignore[index]
                    "document_chunks_fts_docsize"
                )

            mutations = {
                "missing_database_identity": remove_database_identity,
                "unknown_top_level": add_unknown_top_level,
                "forged_subject_identity": replace_identity_hash,
                "minimal_table_fingerprint": minimize_table_fingerprint,
                "missing_processing_hash": remove_processing_hash,
                "missing_fts_fingerprint": remove_fts_fingerprint,
                "missing_fts_shadow_table": remove_fts_shadow_table,
            }
            for name, mutate in mutations.items():
                with self.subTest(mutation=name):
                    forged = copy.deepcopy(valid)
                    mutate(forged)
                    with self.assertRaises(SchemaInventoryError):
                        compare_inventory(forged, copy.deepcopy(forged))

    def test_fixed_trigger_behavior_contract_rejects_spec_and_fts_mutations(self) -> None:
        with p4_identity_fixture() as fixture:
            spec_pristine = fixture.root / "spec-pristine.db"
            shutil.copyfile(fixture.database_path, spec_pristine)
            self._assert_spec_guard_behavior(spec_pristine)
            for trigger, action in (
                ("processing_jobs_spec_guard_insert", "INSERT"),
                ("processing_jobs_spec_guard_update", "UPDATE"),
            ):
                with self.subTest(trigger=trigger):
                    database = fixture.root / f"{trigger}.db"
                    shutil.copyfile(fixture.database_path, database)
                    with closing(sqlite3.connect(database)) as connection:
                        connection.executescript(
                            f"DROP TRIGGER {trigger};"
                            f"CREATE TRIGGER {trigger} BEFORE {action} ON processing_jobs "
                            "BEGIN SELECT 1; END;"
                        )
                        connection.commit()
                    with self.assertRaises(AssertionError):
                        self._assert_spec_guard_behavior(database)

            fts_pristine = fixture.root / "fts-pristine.db"
            shutil.copyfile(fixture.database_path, fts_pristine)
            self._assert_fts_trigger_behavior(fts_pristine)
            for trigger, action in (
                ("document_chunks_fts_ai", "INSERT"),
                ("document_chunks_fts_ad", "DELETE"),
                ("document_chunks_fts_au", "UPDATE"),
            ):
                with self.subTest(trigger=trigger):
                    database = fixture.root / f"{trigger}.db"
                    shutil.copyfile(fixture.database_path, database)
                    with closing(sqlite3.connect(database)) as connection:
                        connection.executescript(
                            f"DROP TRIGGER {trigger};"
                            f"CREATE TRIGGER {trigger} AFTER {action} ON document_chunks "
                            "BEGIN SELECT 1; END;"
                        )
                        connection.commit()
                    with self.assertRaises(AssertionError):
                        self._assert_fts_trigger_behavior(database)


if __name__ == "__main__":
    unittest.main()
