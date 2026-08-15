from __future__ import annotations

from contextlib import closing
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest import mock

from alembic.config import Config
from alembic.script import ScriptDirectory

from backend.tests.support.p1_database import (
    ALEMBIC_CONFIG_PATH,
    P1_TABLES,
    REPOSITORY_ROOT,
    legacy_count_hashes,
    run_alembic,
    temporary_legacy_database,
)


EXPECTED_COLUMNS = {
    "document_sources": (
        "id", "paper_id", "mode", "status", "provider", "model", "pdf_sha256",
        "options_hash", "content_sha256", "markdown", "page_count", "processing_version",
        "error_code", "error_message", "created_at", "updated_at",
    ),
    "generated_artifacts": (
        "id", "paper_id", "kind", "source_document_id", "status", "content",
        "content_sha256", "generator_provider", "generator_model", "prompt_version",
        "error_code", "error_message", "created_at", "updated_at",
    ),
    "processing_jobs": (
        "id", "paper_id", "job_type", "source_mode", "status", "progress_json", "attempt",
        "max_attempts", "idempotency_key", "error_code", "error_message", "created_at",
        "started_at", "finished_at", "cancelled_at",
    ),
    "document_chunks": (
        "id", "source_document_id", "sequence", "heading_path", "page_start", "page_end",
        "content", "content_sha256", "token_count",
    ),
    "obsidian_exports": (
        "id", "paper_id", "artifact_id", "target_path", "source_hash", "exported_hash",
        "status", "exported_at", "error_message",
    ),
}
SHA_A = "a" * 64
SHA_B = "b" * 64


def _table_names(connection: sqlite3.Connection) -> set[str]:
    return {
        str(row[0])
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        )
    }


def _health(connection: sqlite3.Connection) -> None:
    assert connection.execute("PRAGMA quick_check").fetchone()[0] == "ok"
    assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert connection.execute("PRAGMA foreign_key_check").fetchall() == []


def _restore_validation_path(test: unittest.TestCase) -> Path:
    raw_database = os.environ.get("DB_PATH")
    raw_root = os.environ.get("MIGRATION_RESTORE_ROOT")
    test.assertIsNotNone(raw_database, "DB_PATH is required")
    test.assertIsNotNone(raw_root, "MIGRATION_RESTORE_ROOT is required")
    database_path = Path(raw_database).resolve(strict=True)
    restore_root = Path(raw_root).resolve(strict=True)
    test.assertTrue(database_path.is_file())
    test.assertTrue(restore_root.is_dir())
    test.assertNotEqual((REPOSITORY_ROOT / "data" / "app.db").resolve(), database_path)
    test.assertTrue(database_path.parent.name.startswith("restore-validation-"))
    test.assertTrue(database_path.is_relative_to(restore_root), "restore path escaped restore root")
    return database_path


class P1RestoredCopyValidationTests(unittest.TestCase):
    def test_db_path_is_bound_restore_at_exact_p1_revision(self) -> None:
        database_path = _restore_validation_path(self)
        with closing(
            sqlite3.connect(database_path.as_uri() + "?mode=ro", uri=True)
        ) as connection:
            connection.execute("PRAGMA query_only=ON")
            versions = connection.execute("SELECT version_num FROM alembic_version").fetchall()
        self.assertEqual([("20260807_01",)], versions)

    def test_p1_schema_health_and_required_objects_are_read_only(self) -> None:
        database_path = _restore_validation_path(self)
        sidecars = tuple(Path(f"{database_path}{suffix}") for suffix in ("-wal", "-shm", "-journal"))
        before = (database_path.read_bytes(), database_path.stat().st_size, database_path.stat().st_mtime_ns)
        before_sidecars = {path: path.read_bytes() if path.exists() else None for path in sidecars}
        with closing(
            sqlite3.connect(database_path.as_uri() + "?mode=ro", uri=True)
        ) as connection:
            connection.execute("PRAGMA query_only=ON")
            self.assertTrue(set(P1_TABLES).issubset(_table_names(connection)))
            for table_name, expected in EXPECTED_COLUMNS.items():
                observed = tuple(
                    str(row[1]) for row in connection.execute(f'PRAGMA table_xinfo("{table_name}")')
                )
                self.assertEqual(expected, observed)
            _health(connection)
        after = (database_path.read_bytes(), database_path.stat().st_size, database_path.stat().st_mtime_ns)
        self.assertEqual(before, after)
        self.assertEqual(
            before_sidecars,
            {path: path.read_bytes() if path.exists() else None for path in sidecars},
        )


class P1MigrationTests(unittest.TestCase):
    def test_restored_copy_gate_requires_explicit_environment(self) -> None:
        with mock.patch.dict(os.environ):
            os.environ.pop("DB_PATH", None)
            os.environ.pop("MIGRATION_RESTORE_ROOT", None)
            case = P1RestoredCopyValidationTests(
                "test_db_path_is_bound_restore_at_exact_p1_revision"
            )
            result = unittest.TestResult()
            case.run(result)
        self.assertEqual([], result.skipped)
        self.assertEqual(1, len(result.failures))
        self.assertIn("DB_PATH is required", result.failures[0][1])

    def test_revision_has_single_exact_head_and_upgrade_is_additive(self) -> None:
        configuration = Config(str(ALEMBIC_CONFIG_PATH))
        scripts = ScriptDirectory.from_config(configuration)
        self.assertEqual(["20260807_03"], scripts.get_heads())
        self.assertIsNone(scripts.get_revision("20260807_01").down_revision)

        with temporary_legacy_database() as database_path:
            with closing(sqlite3.connect(database_path)) as connection:
                before_tables = _table_names(connection)
                before_legacy = legacy_count_hashes(connection)
            run_alembic(database_path, "20260807_01")
            with closing(sqlite3.connect(database_path)) as connection:
                connection.execute("PRAGMA foreign_keys=ON")
                self.assertEqual(
                    {*P1_TABLES, "alembic_version"},
                    _table_names(connection) - before_tables,
                )
                self.assertEqual(before_legacy, legacy_count_hashes(connection))
                self.assertEqual(
                    [("20260807_01",)],
                    connection.execute("SELECT version_num FROM alembic_version").fetchall(),
                )
                for table_name in P1_TABLES:
                    self.assertEqual(0, connection.execute(f'SELECT count(*) FROM "{table_name}"').fetchone()[0])
                _health(connection)

    def test_required_columns_foreign_keys_constraints_and_indexes_exist(self) -> None:
        with temporary_legacy_database() as database_path:
            run_alembic(database_path, "20260807_01")
            with closing(sqlite3.connect(database_path)) as connection:
                for table_name, expected in EXPECTED_COLUMNS.items():
                    columns = tuple(
                        str(row[1]) for row in connection.execute(f'PRAGMA table_xinfo("{table_name}")')
                    )
                    self.assertEqual(expected, columns)
                foreign_keys = {
                    table_name: {
                        (str(row[3]), str(row[2]), str(row[6]).upper())
                        for row in connection.execute(f'PRAGMA foreign_key_list("{table_name}")')
                    }
                    for table_name in P1_TABLES
                }
                self.assertIn(("paper_id", "papers", "CASCADE"), foreign_keys["document_sources"])
                self.assertIn(("paper_id", "papers", "CASCADE"), foreign_keys["generated_artifacts"])
                self.assertIn(("source_document_id", "document_sources", "CASCADE"), foreign_keys["generated_artifacts"])
                self.assertIn(("paper_id", "papers", "CASCADE"), foreign_keys["processing_jobs"])
                self.assertIn(("source_document_id", "document_sources", "CASCADE"), foreign_keys["document_chunks"])
                self.assertIn(("paper_id", "papers", "CASCADE"), foreign_keys["obsidian_exports"])
                self.assertIn(("artifact_id", "generated_artifacts", "SET NULL"), foreign_keys["obsidian_exports"])
                index_names = {
                    str(row[0])
                    for row in connection.execute(
                        "SELECT name FROM sqlite_master WHERE type='index'"
                    )
                }
                self.assertTrue(
                    {
                        "ix_document_sources_paper_status",
                        "ix_generated_artifacts_paper_kind_status",
                        "ix_generated_artifacts_source",
                        "ix_processing_jobs_status_created",
                        "ix_processing_jobs_paper_created",
                        "ix_document_chunks_source",
                        "ix_obsidian_exports_paper_status",
                        "ix_obsidian_exports_artifact",
                    }.issubset(index_names)
                )

    def test_job_scope_and_domain_row_checks_reject_invalid_values(self) -> None:
        with temporary_legacy_database() as database_path:
            run_alembic(database_path, "20260807_01")
            with closing(sqlite3.connect(database_path)) as connection:
                connection.execute("PRAGMA foreign_keys=ON")
                valid_jobs = (
                    ("source_materialize", "paper-1", "native"),
                    ("ocr", "paper-1", "ocr"),
                    ("explain", "paper-1", "native"),
                    ("translate", "paper-1", "native"),
                    ("embed", "paper-1", "native"),
                    ("obsidian_export", "paper-1", None),
                    ("obsidian_sync", None, None),
                )
                for index, (job_type, paper_id, mode) in enumerate(valid_jobs):
                    connection.execute(
                        "INSERT INTO processing_jobs(id,paper_id,job_type,source_mode,status,progress_json,attempt,max_attempts,idempotency_key) "
                        "VALUES(?,?,?,?,?,?,?,?,?)",
                        (f"job_{index}", paper_id, job_type, mode, "queued", "{}", 0, 1, f"key-{index}"),
                    )
                rejected = (
                    ("source_materialize", "paper-1", "ocr", "queued", 0, 1),
                    ("ocr", "paper-1", "native", "queued", 0, 1),
                    ("explain", None, "native", "queued", 0, 1),
                    ("obsidian_export", None, None, "queued", 0, 1),
                    ("obsidian_sync", None, None, "ready", 0, 1),
                    ("obsidian_sync", None, None, "queued", -1, 1),
                    ("obsidian_sync", None, None, "queued", 0, 0),
                )
                for index, values in enumerate(rejected):
                    with self.subTest(values=values), self.assertRaises(sqlite3.IntegrityError):
                        connection.execute(
                            "INSERT INTO processing_jobs(id,paper_id,job_type,source_mode,status,progress_json,attempt,max_attempts,idempotency_key) "
                            "VALUES(?,?,?,?,?,?,?,?,?)",
                            (f"bad_{index}", *values[:3], values[3], "{}", values[4], values[5], f"bad-key-{index}"),
                        )

    def test_source_artifact_chunk_and_export_constraints_are_hard(self) -> None:
        with temporary_legacy_database() as database_path:
            run_alembic(database_path, "20260807_01")
            with closing(sqlite3.connect(database_path)) as connection:
                connection.execute("PRAGMA foreign_keys=ON")
                source_values = (
                    "src_1", "paper-1", "native", "queued", "local", "model",
                    SHA_A, SHA_B, None, None, 0, "v1", None,
                )
                connection.execute(
                    "INSERT INTO document_sources(id,paper_id,mode,status,provider,model,pdf_sha256,options_hash,content_sha256,markdown,page_count,processing_version,error_code) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    source_values,
                )
                invalid_sources = (
                    {"id": "bad_mode", "mode": "auto"},
                    {"id": "bad_status", "status": "succeeded"},
                    {"id": "bad_hash", "pdf_sha256": SHA_A.upper()},
                    {"id": "bad_page", "page_count": -1},
                    {"id": "bad_ready_null", "status": "ready", "markdown": None, "content_sha256": SHA_A},
                    {"id": "bad_failed_null", "status": "failed", "error_code": None},
                )
                source_columns = (
                    "id", "paper_id", "mode", "status", "provider", "model", "pdf_sha256",
                    "options_hash", "content_sha256", "markdown", "page_count", "processing_version",
                    "error_code",
                )
                base_source = dict(zip(source_columns, source_values))
                for index, overrides in enumerate(invalid_sources):
                    values = {
                        **base_source,
                        "pdf_sha256": "cdef01"[index] * 64,
                        **overrides,
                    }
                    with self.subTest(source=overrides), self.assertRaises(sqlite3.IntegrityError):
                        connection.execute(
                            f"INSERT INTO document_sources({','.join(source_columns)}) VALUES({','.join('?' for _ in source_columns)})",
                            tuple(values[column] for column in source_columns),
                        )
                with self.assertRaises(sqlite3.IntegrityError):
                    connection.execute(
                        "INSERT INTO document_sources(id,paper_id,mode,status,provider,model,pdf_sha256,options_hash,page_count,processing_version) "
                        "VALUES('src_duplicate','paper-1','native','queued','local','model',?,?,0,'v1')",
                        (SHA_A, SHA_B),
                    )

                connection.execute(
                    "INSERT INTO generated_artifacts(id,paper_id,kind,source_document_id,status,generator_provider,generator_model,prompt_version) "
                    "VALUES('art_compat','paper-1','future_kind','src_1','queued','provider','model','p1')"
                )
                invalid_artifacts = (
                    ("art_blank", "", "queued", None, None, None),
                    ("art_status", "explainer", "succeeded", None, None, None),
                    ("art_ready_null", "explainer", "ready", None, SHA_A, None),
                    ("art_failed_null", "explainer", "failed", None, None, None),
                    ("art_hash", "explainer", "queued", None, SHA_A.upper(), None),
                )
                for values in invalid_artifacts:
                    with self.subTest(artifact=values), self.assertRaises(sqlite3.IntegrityError):
                        connection.execute(
                            "INSERT INTO generated_artifacts(id,paper_id,kind,source_document_id,status,content,content_sha256,generator_provider,generator_model,prompt_version,error_code) "
                            "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                            (values[0], "paper-1", values[1], "src_1", values[2], values[3], values[4], "provider", "model", "p1", values[5]),
                        )
                with self.assertRaises(sqlite3.IntegrityError):
                    connection.execute(
                        "INSERT INTO generated_artifacts(id,paper_id,kind,source_document_id,status,generator_provider,generator_model,prompt_version) "
                        "VALUES('art_duplicate','paper-1','future_kind','src_1','queued','provider','model','p1')"
                    )

                connection.execute(
                    "INSERT INTO document_chunks(id,source_document_id,sequence,content,content_sha256,token_count) "
                    "VALUES('chk_1','src_1',0,'content',?,0)",
                    (SHA_A,),
                )
                for row in (
                    ("chk_seq", -1, SHA_A, 0),
                    ("chk_hash", 1, SHA_A.upper(), 0),
                    ("chk_token", 1, SHA_A, -1),
                    ("chk_duplicate", 0, SHA_A, 0),
                ):
                    with self.subTest(chunk=row), self.assertRaises(sqlite3.IntegrityError):
                        connection.execute(
                            "INSERT INTO document_chunks(id,source_document_id,sequence,content,content_sha256,token_count) VALUES(?,'src_1',?,'content',?,?)",
                            row,
                        )

                connection.execute(
                    "INSERT INTO obsidian_exports(id,paper_id,target_path,source_hash,status) VALUES('exp_1','paper-1','one.md',?,'pending')",
                    (SHA_A,),
                )
                for row in (
                    ("exp_hash", "two.md", SHA_A.upper()),
                    ("exp_duplicate", "one.md", SHA_A),
                ):
                    with self.subTest(export=row), self.assertRaises(sqlite3.IntegrityError):
                        connection.execute(
                            "INSERT INTO obsidian_exports(id,paper_id,target_path,source_hash,status) VALUES(?,'paper-1',?,?,'pending')",
                            row,
                        )

    def test_delete_actions_change_only_database_ledger(self) -> None:
        with temporary_legacy_database() as database_path, tempfile.TemporaryDirectory(
            prefix="study-app-vault-fixture-"
        ) as vault_dir:
            run_alembic(database_path, "20260807_01")
            external = Path(vault_dir) / "paper.md"
            external.write_bytes(b"external fixture")
            with closing(sqlite3.connect(database_path)) as connection:
                connection.execute("PRAGMA foreign_keys=ON")
                _insert_ready_source_and_artifact(connection)
                connection.execute(
                    "INSERT INTO obsidian_exports(id,paper_id,artifact_id,target_path,status) VALUES(?,?,?,?,?)",
                    ("exp_1", "paper-1", "art_1", "paper-1.md", "ready"),
                )
                connection.execute("DELETE FROM generated_artifacts WHERE id='art_1'")
                self.assertEqual(
                    (None,),
                    connection.execute("SELECT artifact_id FROM obsidian_exports WHERE id='exp_1'").fetchone(),
                )
                connection.execute("DELETE FROM papers WHERE id='paper-1'")
                self.assertEqual(0, connection.execute("SELECT count(*) FROM obsidian_exports").fetchone()[0])
            self.assertEqual(b"external fixture", external.read_bytes())

    def test_guarded_downgrade_and_empty_reupgrade_preserve_legacy_rows(self) -> None:
        with temporary_legacy_database() as database_path:
            with closing(sqlite3.connect(database_path)) as connection:
                before = legacy_count_hashes(connection)
            run_alembic(database_path, "20260807_01")
            with closing(sqlite3.connect(database_path)) as connection:
                connection.execute("PRAGMA foreign_keys=ON")
                connection.execute(
                    "INSERT INTO processing_jobs(id,job_type,status,progress_json,attempt,max_attempts,idempotency_key) "
                    "VALUES('job_guard','obsidian_sync','queued','{}',0,1,'guard')"
                )
                connection.commit()
            with self.assertRaises(Exception) as raised:
                run_alembic(database_path, "base")
            self.assertIn("P1_DOWNGRADE_NONEMPTY", str(raised.exception))
            with closing(sqlite3.connect(database_path)) as connection:
                connection.execute("DELETE FROM processing_jobs")
                connection.commit()
            run_alembic(database_path, "base")
            with closing(sqlite3.connect(database_path)) as connection:
                self.assertTrue(set(P1_TABLES).isdisjoint(_table_names(connection)))
                self.assertEqual(before, legacy_count_hashes(connection))
                _health(connection)
            run_alembic(database_path, "20260807_01")
            with closing(sqlite3.connect(database_path)) as connection:
                self.assertTrue(set(P1_TABLES).issubset(_table_names(connection)))
                self.assertEqual([("20260807_01",)], connection.execute("SELECT version_num FROM alembic_version").fetchall())
                self.assertEqual(before, legacy_count_hashes(connection))
                _health(connection)


def _insert_ready_source_and_artifact(connection: sqlite3.Connection) -> None:
    connection.execute(
        "INSERT INTO document_sources(id,paper_id,mode,status,provider,model,pdf_sha256,options_hash,content_sha256,markdown,page_count,processing_version) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
        ("src_1", "paper-1", "native", "ready", "local", "model", SHA_A, SHA_B, SHA_A, "source", 1, "v1"),
    )
    connection.execute(
        "INSERT INTO generated_artifacts(id,paper_id,kind,source_document_id,status,content,content_sha256,generator_provider,generator_model,prompt_version) "
        "VALUES(?,?,?,?,?,?,?,?,?,?)",
        ("art_1", "paper-1", "explainer", "src_1", "ready", "artifact", SHA_A, "provider", "model", "prompt-v1"),
    )


def load_tests(loader, standard_tests, _pattern):
    """Keep restore-only validators out of unbound full-suite discovery."""

    suite = unittest.TestSuite()
    suite.addTests(loader.loadTestsFromTestCase(P1MigrationTests))
    return suite


if __name__ == "__main__":
    unittest.main()
