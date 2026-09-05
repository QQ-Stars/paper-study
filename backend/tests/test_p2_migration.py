from __future__ import annotations

from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory

from backend.app.domain.processing import (
    LegacyImportedJobSpecV1,
    decode_job_spec_v1,
    encode_job_spec_v1,
    hash_job_spec,
)
from backend.app.infrastructure.database_backup import inspect_database
from backend.tests.support.p1_database import (
    ALEMBIC_CONFIG_PATH,
    P1_TABLES,
    REPOSITORY_ROOT,
    legacy_count_hashes,
    run_alembic,
    temporary_legacy_database,
    temporary_restore_database,
)


P2_TABLES = (
    "paper_artifact_heads",
    "processing_job_events",
    "ocr_page_checkpoints",
)
P1_JOB_COLUMNS = (
    "id", "paper_id", "job_type", "source_mode", "status", "progress_json",
    "attempt", "max_attempts", "idempotency_key", "error_code", "error_message",
    "created_at", "started_at", "finished_at", "cancelled_at",
)
P1_EXPECTED_COLUMNS = {
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
    "processing_jobs": P1_JOB_COLUMNS,
    "document_chunks": (
        "id", "source_document_id", "sequence", "heading_path", "page_start", "page_end",
        "content", "content_sha256", "token_count",
    ),
    "obsidian_exports": (
        "id", "paper_id", "artifact_id", "target_path", "source_hash", "exported_hash",
        "status", "exported_at", "error_message",
    ),
}
P2_ADDED_COLUMNS = {
    "document_sources": ("source_key", "ready_at", "stale_at"),
    "generated_artifacts": ("artifact_key", "ready_at", "stale_at"),
    "processing_jobs": (
        "source_document_id", "artifact_id", "spec_json", "available_at", "lease_owner",
        "lease_token", "lease_expires_at", "heartbeat_at", "cancel_requested_at",
        "result_json", "updated_at", "retry_of_job_id", "retry_sequence",
    ),
}
REQUIRED_P2_INDEXES = {
    "ux_document_sources_source_key",
    "ux_generated_artifacts_artifact_key",
    "ix_processing_jobs_claim",
    "ix_processing_jobs_lease_expires",
    "ix_processing_jobs_source",
    "ix_processing_jobs_artifact",
    "ix_processing_jobs_retry_parent",
    "ux_processing_jobs_active_retry",
    "ix_paper_artifact_heads_artifact",
    "ix_processing_job_events_job_sequence",
    "ix_ocr_page_checkpoints_source_status_page",
}


def _canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _legacy_spec(row: sqlite3.Row) -> str:
    return _canonical(
        {
            "arguments": {"legacyImported": True},
            "jobType": row["job_type"],
            "paperId": row["paper_id"],
            "schemaVersion": 1,
            "sourceMode": row["source_mode"],
            "target": {
                "artifactId": None,
                "sourceDocumentId": None,
            },
        }
    )


def _p1_job_fingerprint(connection: sqlite3.Connection) -> tuple[int, str]:
    columns = ",".join(f'"{name}"' for name in P1_JOB_COLUMNS)
    rows = connection.execute(
        f"SELECT {columns} FROM processing_jobs ORDER BY id"
    ).fetchall()
    payload = _canonical([list(row) for row in rows]).encode("utf-8")
    return len(rows), hashlib.sha256(payload).hexdigest()


def _assert_legacy_fingerprints_unchanged(
    test: unittest.TestCase,
    before: dict[str, tuple[int, str]],
    connection: sqlite3.Connection,
) -> None:
    after = legacy_count_hashes(connection)
    test.assertEqual(before, {name: after[name] for name in before})


def _insert_p1_job_matrix(connection: sqlite3.Connection) -> None:
    rows = (
        ("source_materialize", "paper-1", "native", "queued"),
        ("ocr", "paper-1", "ocr", "running"),
        ("explain", "paper-1", "native", "succeeded"),
        ("translate", "paper-1", "native", "failed"),
        ("embed", "paper-1", "native", "cancelled"),
        ("obsidian_export", "paper-1", None, "queued"),
        ("obsidian_sync", None, None, "succeeded"),
    )
    for index, (job_type, paper_id, source_mode, status) in enumerate(rows):
        connection.execute(
            "INSERT INTO processing_jobs("
            "id,paper_id,job_type,source_mode,status,progress_json,attempt,max_attempts,"
            "idempotency_key,error_code) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (
                f"legacy-{index}", paper_id, job_type, source_mode, status,
                _canonical({"sentinel": index}), index, 9, f"legacy-key-{index}",
                "LEGACY_FAILURE" if status == "failed" else None,
            ),
        )
    connection.commit()


def _insert_p1_core_projection_sentinels(connection: sqlite3.Connection) -> None:
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute(
        "INSERT INTO document_sources("
        "id,paper_id,mode,status,provider,model,pdf_sha256,options_hash,"
        "content_sha256,markdown,page_count,processing_version,error_code,error_message,"
        "created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            "source-core", "paper-1", "native", "ready", "local", "native-v1",
            "a" * 64, "b" * 64, "c" * 64, "# source", 2, "p1-v1", None,
            "source-sentinel", "2026-08-07T00:00:00Z", "2026-08-07T00:01:00Z",
        ),
    )
    connection.execute(
        "INSERT INTO generated_artifacts("
        "id,paper_id,kind,source_document_id,status,content,content_sha256,"
        "generator_provider,generator_model,prompt_version,error_code,error_message,"
        "created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            "artifact-core", "paper-1", "explainer", "source-core", "ready",
            "# artifact", "d" * 64, "provider", "model", "prompt-v1", None,
            "artifact-sentinel", "2026-08-07T00:02:00Z", "2026-08-07T00:03:00Z",
        ),
    )
    connection.execute(
        "INSERT INTO processing_jobs("
        "id,paper_id,job_type,source_mode,status,progress_json,attempt,max_attempts,"
        "idempotency_key,error_code,error_message,created_at,started_at,finished_at,"
        "cancelled_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            "job-core", "paper-1", "source_materialize", "native", "queued",
            '{"phase":"p1"}', 0, 3, "job-core-key", None, "job-sentinel",
            "2026-08-07T00:04:00Z", None, None, None,
        ),
    )
    connection.commit()


def _core_projection_fingerprints(database_path: Path) -> dict[str, tuple[int, str]]:
    fingerprint = inspect_database(database_path)
    keys = (
        "p1CoreDocumentSources",
        "p1CoreGeneratedArtifacts",
        "p1CoreProcessingJobs",
    )
    return {
        key: (fingerprint.content_counts[key], fingerprint.content_sha256[key])
        for key in keys
    }


def _table_names(connection: sqlite3.Connection) -> set[str]:
    return {
        str(row[0])
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        )
    }


def _columns(connection: sqlite3.Connection, table_name: str) -> tuple[str, ...]:
    return tuple(
        str(row[1]) for row in connection.execute(f'PRAGMA table_xinfo("{table_name}")')
    )


def _health(connection: sqlite3.Connection) -> None:
    assert connection.execute("PRAGMA quick_check").fetchone()[0] == "ok"
    assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert connection.execute("PRAGMA foreign_key_check").fetchall() == []


def _run_downgrade(
    database_path: Path,
    revision: str,
    *,
    allow_data_loss: str | None = None,
    restore_root: Path | None = None,
) -> None:
    previous = os.environ.get("DB_PATH")
    previous_restore_root = os.environ.get("MIGRATION_RESTORE_ROOT")
    os.environ["DB_PATH"] = str(database_path.resolve(strict=True))
    if restore_root is None:
        os.environ.pop("MIGRATION_RESTORE_ROOT", None)
    else:
        os.environ["MIGRATION_RESTORE_ROOT"] = str(
            restore_root.resolve(strict=True)
        )
    try:
        configuration = Config(str(ALEMBIC_CONFIG_PATH))
        if allow_data_loss is not None:
            configuration.cmd_opts = SimpleNamespace(x=[f"allow_p2_data_loss={allow_data_loss}"])
        command.downgrade(configuration, revision)
    finally:
        if previous is None:
            os.environ.pop("DB_PATH", None)
        else:
            os.environ["DB_PATH"] = previous
        if previous_restore_root is None:
            os.environ.pop("MIGRATION_RESTORE_ROOT", None)
        else:
            os.environ["MIGRATION_RESTORE_ROOT"] = previous_restore_root


def _is_reparse_point(path: Path) -> bool:
    attributes = getattr(path.lstat(), "st_file_attributes", 0)
    return bool(attributes & getattr(__import__("stat"), "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))


def _restore_validation_path(test: unittest.TestCase) -> Path:
    raw_database = os.environ.get("DB_PATH")
    raw_root = os.environ.get("MIGRATION_RESTORE_ROOT")
    test.assertIsNotNone(raw_database, "DB_PATH is required")
    test.assertIsNotNone(raw_root, "MIGRATION_RESTORE_ROOT is required")
    database_lexical = Path(str(raw_database)).absolute()
    root_lexical = Path(str(raw_root)).absolute()
    test.assertFalse(_is_reparse_point(root_lexical), "restore root must not be a reparse point")
    for candidate in (database_lexical.parent, database_lexical):
        test.assertFalse(_is_reparse_point(candidate), "restore path must not contain a reparse point")
    database_path = database_lexical.resolve(strict=True)
    restore_root = root_lexical.resolve(strict=True)
    test.assertTrue(database_path.is_file())
    test.assertTrue(restore_root.is_dir())
    test.assertNotEqual((REPOSITORY_ROOT / "data" / "app.db").resolve(), database_path)
    test.assertTrue(database_path.parent.name.startswith("restore-validation-"))
    test.assertTrue(database_path.is_relative_to(restore_root), "restore path escaped restore root")
    return database_path


class P2MigrationTests(unittest.TestCase):
    def test_upgrade_backfills_versioned_canonical_job_specs_and_installs_guards(self) -> None:
        with temporary_legacy_database() as database_path:
            run_alembic(database_path, "20260807_01")
            with closing(sqlite3.connect(database_path)) as connection:
                connection.row_factory = sqlite3.Row
                connection.execute("PRAGMA foreign_keys=ON")
                _insert_p1_job_matrix(connection)
                before_jobs = _p1_job_fingerprint(connection)
                before_legacy = legacy_count_hashes(connection)

            run_alembic(database_path, "20260807_02")

            with closing(sqlite3.connect(database_path)) as connection:
                connection.row_factory = sqlite3.Row
                connection.execute("PRAGMA foreign_keys=ON")
                jobs = connection.execute(
                    "SELECT * FROM processing_jobs ORDER BY id"
                ).fetchall()
                self.assertEqual(7, len(jobs))
                self.assertEqual(before_jobs, _p1_job_fingerprint(connection))
                _assert_legacy_fingerprints_unchanged(self, before_legacy, connection)
                for row in jobs:
                    self.assertEqual(_legacy_spec(row), row["spec_json"])
                    decoded = decode_job_spec_v1(
                        row["spec_json"],
                        expected_row={
                            "job_type": row["job_type"],
                            "paper_id": row["paper_id"],
                            "source_mode": row["source_mode"],
                            "source_document_id": row["source_document_id"],
                            "artifact_id": row["artifact_id"],
                        },
                    )
                    self.assertIsInstance(decoded, LegacyImportedJobSpecV1)
                    self.assertEqual(row["spec_json"], encode_job_spec_v1(decoded))
                    self.assertEqual(
                        hashlib.sha256(row["spec_json"].encode("utf-8")).hexdigest(),
                        hash_job_spec(row["spec_json"]),
                    )
                    self.assertEqual("JOB_SPEC_UNRECOVERABLE", decoded.dispatch_error_code)
                    spec = json.loads(row["spec_json"])
                    self.assertEqual(1, spec["schemaVersion"])
                    self.assertEqual(row["job_type"], spec["jobType"])
                    self.assertEqual(row["paper_id"], spec["paperId"])
                    self.assertEqual(row["source_mode"], spec["sourceMode"])
                    self.assertEqual({"legacyImported": True}, spec["arguments"])
                    self.assertEqual(
                        {"artifactId": None, "sourceDocumentId": None}, spec["target"]
                    )
                    self.assertEqual(row["created_at"], row["available_at"])
                    self.assertEqual(row["created_at"], row["updated_at"])

                trigger_names = {
                    row[0]
                    for row in connection.execute(
                        "SELECT name FROM sqlite_master WHERE type='trigger'"
                    )
                }
                self.assertTrue(
                    {
                        "processing_jobs_spec_guard_insert",
                        "processing_jobs_spec_guard_update",
                    }.issubset(trigger_names)
                )

                original_spec = jobs[0]["spec_json"]
                connection.execute(
                    "UPDATE processing_jobs SET progress_json=? WHERE id=?",
                    (_canonical({"changed": True}), jobs[0]["id"]),
                )
                self.assertEqual(
                    original_spec,
                    connection.execute(
                        "SELECT spec_json FROM processing_jobs WHERE id=?", (jobs[0]["id"],)
                    ).fetchone()[0],
                )

                base = json.loads(original_spec)
                invalid_specs = (
                    "{}",
                    json.dumps(base, ensure_ascii=False, indent=2, sort_keys=True),
                    _canonical({**base, "schemaVersion": 2}),
                    _canonical({**base, "unknown": True}),
                    _canonical({**base, "arguments": {"apiKey": "secret"}}),
                    _canonical({**base, "paperId": "paper-2"}),
                    " " * (4 * 1024 * 1024 + 1),
                )
                for index, invalid in enumerate(invalid_specs):
                    with self.subTest(invalid=index), self.assertRaises(
                        (sqlite3.IntegrityError, sqlite3.OperationalError)
                    ):
                        connection.execute(
                            "UPDATE processing_jobs SET spec_json=? WHERE id=?",
                            (invalid, jobs[0]["id"]),
                        )
                    self.assertEqual(
                        original_spec,
                        connection.execute(
                            "SELECT spec_json FROM processing_jobs WHERE id=?",
                            (jobs[0]["id"],),
                        ).fetchone()[0],
                    )
                with self.assertRaises(sqlite3.IntegrityError):
                    connection.execute(
                        "INSERT INTO processing_jobs("
                        "id,paper_id,job_type,source_mode,status,progress_json,attempt,max_attempts,idempotency_key"
                        ") VALUES('missing-spec','paper-1','source_materialize','native','queued','{}',0,1,'missing-spec')"
                    )

    def test_cache_head_event_and_checkpoint_constraints_are_hard(self) -> None:
        sha_a = "a" * 64
        sha_b = "b" * 64
        with temporary_legacy_database() as database_path:
            run_alembic(database_path, "20260807_02")
            with closing(sqlite3.connect(database_path)) as connection:
                connection.execute("PRAGMA foreign_keys=ON")
                connection.execute(
                    "INSERT INTO document_sources("
                    "id,paper_id,mode,status,provider,model,pdf_sha256,options_hash,content_sha256,"
                    "markdown,page_count,processing_version,source_key,ready_at) "
                    "VALUES('src-1','paper-1','native','ready','local','pymupdf4llm-pymupdf',"
                    "?,?,?,'source',1,'v1','source-key','2026-08-07T00:00:00Z')",
                    (sha_a, sha_b, sha_a),
                )
                connection.execute(
                    "INSERT INTO generated_artifacts("
                    "id,paper_id,kind,source_document_id,status,content,content_sha256,"
                    "generator_provider,generator_model,prompt_version,artifact_key,ready_at) "
                    "VALUES('art-1','paper-1','explainer','src-1','ready','artifact',?,"
                    "'provider','model','prompt-v1','artifact-key','2026-08-07T00:00:00Z')",
                    (sha_b,),
                )
                with self.assertRaises(sqlite3.IntegrityError):
                    connection.execute(
                        "INSERT INTO document_sources("
                        "id,paper_id,mode,status,provider,model,pdf_sha256,options_hash,processing_version,source_key) "
                        "VALUES('src-dup','paper-1','native','queued','local','other',?,?,'v2','source-key')",
                        (sha_b, sha_a),
                    )
                with self.assertRaises(sqlite3.IntegrityError):
                    connection.execute(
                        "INSERT INTO generated_artifacts("
                        "id,paper_id,kind,source_document_id,status,generator_provider,generator_model,"
                        "prompt_version,artifact_key) VALUES('art-dup','paper-1','future','src-1','queued',"
                        "'provider','model','prompt-v2','artifact-key')"
                    )

                connection.execute(
                    "INSERT INTO paper_artifact_heads(paper_id,kind,artifact_id,updated_at) "
                    "VALUES('paper-1','explainer','art-1','2026-08-07T00:00:00Z')"
                )
                for values in (
                    ("paper-1", "explainer", "art-1", "2026-08-07T00:00:00Z"),
                    ("paper-2", "explainer", "art-1", "2026-08-07T00:00:00Z"),
                    ("paper-1", "wrong-kind", "art-1", "2026-08-07T00:00:00Z"),
                    ("paper-1", "explainer", "missing", "2026-08-07T00:00:00Z"),
                    ("paper-1", " ", "art-1", "not-utc"),
                ):
                    with self.subTest(head=values), self.assertRaises(sqlite3.IntegrityError):
                        connection.execute(
                            "INSERT INTO paper_artifact_heads(paper_id,kind,artifact_id,updated_at) VALUES(?,?,?,?)",
                            values,
                        )

                spec = _canonical(
                    {
                        "arguments": {"legacyImported": True}, "jobType": "source_materialize",
                        "paperId": "paper-1", "schemaVersion": 1, "sourceMode": "native",
                        "target": {"artifactId": None, "sourceDocumentId": None},
                    }
                )
                connection.execute(
                    "INSERT INTO processing_jobs("
                    "id,paper_id,job_type,source_mode,status,progress_json,attempt,max_attempts,"
                    "idempotency_key,spec_json,available_at,updated_at) "
                    "VALUES('job-1','paper-1','source_materialize','native','queued','{}',0,2,'job-key',?,?,?)",
                    (spec, "2026-08-07T00:00:00Z", "2026-08-07T00:00:00Z"),
                )
                connection.execute(
                    "INSERT INTO processing_job_events(job_id,sequence,event_type,progress_json,created_at) "
                    "VALUES('job-1',1,'enqueued','{}','2026-08-07T00:00:00Z')"
                )
                for statement in (
                    "INSERT INTO processing_job_events(job_id,sequence,event_type,progress_json,created_at) VALUES('job-1',1,'progress','{}','2026-08-07T00:00:00Z')",
                    "INSERT INTO processing_job_events(job_id,sequence,event_type,progress_json,created_at) VALUES('job-1',0,'progress','{}','2026-08-07T00:00:00Z')",
                    "INSERT INTO processing_job_events(job_id,sequence,event_type,progress_json,created_at) VALUES('job-1',2,'unknown','{}','2026-08-07T00:00:00Z')",
                    "INSERT INTO processing_job_events(job_id,sequence,event_type,progress_json,created_at) VALUES('job-1',2,'progress','{}','not-utc')",
                ):
                    with self.assertRaises(sqlite3.IntegrityError):
                        connection.execute(statement)

                connection.execute(
                    "INSERT INTO ocr_page_checkpoints("
                    "source_document_id,page_number,status,markdown,content_sha256,attempt,created_at,updated_at) "
                    "VALUES('src-1',1,'succeeded','page',?,0,'2026-08-07T00:00:00Z','2026-08-07T00:00:00Z')",
                    (sha_a,),
                )
                invalid_checkpoints = (
                    (0, "queued", None, None, 0, None),
                    (2, "succeeded", None, sha_a, 0, None),
                    (2, "succeeded", "page", None, 0, None),
                    (2, "failed", None, None, 0, None),
                    (2, "queued", None, None, -1, None),
                    (2, "unknown", None, None, 0, None),
                )
                for index, values in enumerate(invalid_checkpoints):
                    with self.subTest(checkpoint=values), self.assertRaises(sqlite3.IntegrityError):
                        connection.execute(
                            "INSERT INTO ocr_page_checkpoints("
                            "source_document_id,page_number,status,markdown,content_sha256,attempt,error_code,created_at,updated_at) "
                            "VALUES('src-1',?,?,?,?,?,?,'2026-08-07T00:00:00Z','2026-08-07T00:00:00Z')",
                            values,
                        )

                connection.execute("DELETE FROM generated_artifacts WHERE id='art-1'")
                self.assertEqual(
                    0, connection.execute("SELECT count(*) FROM paper_artifact_heads").fetchone()[0]
                )
                connection.execute("DELETE FROM papers WHERE id='paper-1'")
                self.assertEqual(
                    0, connection.execute("SELECT count(*) FROM processing_job_events").fetchone()[0]
                )
                self.assertEqual(
                    0, connection.execute("SELECT count(*) FROM ocr_page_checkpoints").fetchone()[0]
                )

    def test_guarded_downgrade_requires_exact_opt_in_and_preserves_p1(self) -> None:
        with temporary_legacy_database() as database_path:
            with closing(sqlite3.connect(database_path)) as connection:
                before_legacy = legacy_count_hashes(connection)
            run_alembic(database_path, "20260807_02")
            _run_downgrade(database_path, "20260807_01")
            with closing(sqlite3.connect(database_path)) as connection:
                self.assertEqual(before_legacy, legacy_count_hashes(connection))
                self.assertTrue(set(P2_TABLES).isdisjoint(_table_names(connection)))
                for table_name in P1_TABLES:
                    self.assertEqual(P1_EXPECTED_COLUMNS[table_name], _columns(connection, table_name))
                self.assertEqual(
                    [("20260807_01",)],
                    connection.execute("SELECT version_num FROM alembic_version").fetchall(),
                )
                _health(connection)

        with temporary_restore_database() as (database_path, restore_root):
            run_alembic(database_path, "20260807_01")
            with closing(sqlite3.connect(database_path)) as connection:
                _insert_p1_job_matrix(connection)
                before_jobs = _p1_job_fingerprint(connection)
            run_alembic(database_path, "20260807_02")
            with closing(sqlite3.connect(database_path)) as connection:
                before_schema = {
                    name: _columns(connection, name) for name in (*P1_TABLES, *P2_TABLES)
                }
                before_specs = connection.execute(
                    "SELECT id,spec_json FROM processing_jobs ORDER BY id"
                ).fetchall()
            for opt_in in (None, "TRUE", "1"):
                with self.subTest(opt_in=opt_in):
                    with self.assertRaises(Exception) as raised:
                        _run_downgrade(
                            database_path, "20260807_01", allow_data_loss=opt_in
                        )
                    self.assertIn("P2_DOWNGRADE_BLOCKED_NONEMPTY", str(raised.exception))
                    with closing(sqlite3.connect(database_path)) as connection:
                        self.assertEqual(before_jobs, _p1_job_fingerprint(connection))
                        self.assertEqual(before_specs, connection.execute(
                            "SELECT id,spec_json FROM processing_jobs ORDER BY id"
                        ).fetchall())
                        self.assertEqual(
                            before_schema,
                            {name: _columns(connection, name) for name in (*P1_TABLES, *P2_TABLES)},
                        )

            _run_downgrade(
                database_path,
                "20260807_01",
                allow_data_loss="true",
                restore_root=restore_root,
            )
            with closing(sqlite3.connect(database_path)) as connection:
                self.assertEqual(before_jobs, _p1_job_fingerprint(connection))
                self.assertTrue(set(P2_TABLES).isdisjoint(_table_names(connection)))
                for table_name in P1_TABLES:
                    self.assertEqual(P1_EXPECTED_COLUMNS[table_name], _columns(connection, table_name))
                self.assertEqual([], connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='trigger' AND name LIKE 'processing_jobs_spec_guard_%'"
                ).fetchall())

    def test_data_loss_opt_in_rejects_custom_live_path_without_bound_restore_root(
        self,
    ) -> None:
        with temporary_legacy_database() as database_path:
            run_alembic(database_path, "20260807_01")
            with closing(sqlite3.connect(database_path)) as connection:
                _insert_p1_job_matrix(connection)
                before_jobs = _p1_job_fingerprint(connection)
            run_alembic(database_path, "20260807_02")
            with closing(sqlite3.connect(database_path)) as connection:
                before_schema = {
                    name: _columns(connection, name)
                    for name in (*P1_TABLES, *P2_TABLES)
                }

            with mock.patch.dict(os.environ):
                os.environ.pop("MIGRATION_RESTORE_ROOT", None)
                with self.assertRaises(Exception) as raised:
                    _run_downgrade(
                        database_path,
                        "20260807_01",
                        allow_data_loss="true",
                    )

            self.assertIn("P2_DOWNGRADE_BLOCKED_NONEMPTY", str(raised.exception))
            with closing(sqlite3.connect(database_path)) as connection:
                self.assertEqual(before_jobs, _p1_job_fingerprint(connection))
                self.assertEqual(
                    before_schema,
                    {
                        name: _columns(connection, name)
                        for name in (*P1_TABLES, *P2_TABLES)
                    },
                )

    def test_restored_copy_gate_requires_explicit_environment(self) -> None:
        with mock.patch.dict(os.environ):
            os.environ.pop("DB_PATH", None)
            os.environ.pop("MIGRATION_RESTORE_ROOT", None)
            case = P2RestoredCopyValidationTests(
                "test_db_path_is_bound_restore_at_exact_p2_revision"
            )
            result = unittest.TestResult()
            case.run(result)
        self.assertEqual([], result.skipped)
        self.assertEqual(1, len(result.failures))
        self.assertIn("DB_PATH is required", result.failures[0][1])


class P2CoreProjectionMigrationTests(unittest.TestCase):
    def test_nonempty_p1_core_fingerprints_survive_p2_upgrade_downgrade_reupgrade(
        self,
    ) -> None:
        with temporary_restore_database() as (database_path, restore_root):
            self.assertNotEqual(
                (REPOSITORY_ROOT / "data" / "app.db").resolve(),
                database_path.resolve(),
            )
            run_alembic(database_path, "20260807_01")
            with closing(sqlite3.connect(database_path)) as connection:
                _insert_p1_core_projection_sentinels(connection)
            initial = _core_projection_fingerprints(database_path)
            self.assertEqual({1}, {count for count, _digest in initial.values()})

            run_alembic(database_path, "20260807_02")
            upgraded = _core_projection_fingerprints(database_path)
            self.assertEqual(initial, upgraded)
            with closing(sqlite3.connect(database_path)) as connection:
                self.assertIsNotNone(connection.execute(
                    "SELECT source_key FROM document_sources WHERE id='source-core'"
                ).fetchone()[0])
                self.assertIsNotNone(connection.execute(
                    "SELECT artifact_key FROM generated_artifacts WHERE id='artifact-core'"
                ).fetchone()[0])
                self.assertIsNotNone(connection.execute(
                    "SELECT spec_json FROM processing_jobs WHERE id='job-core'"
                ).fetchone()[0])

            with self.assertRaises(Exception) as blocked:
                _run_downgrade(database_path, "20260807_01")
            self.assertIn("P2_DOWNGRADE_BLOCKED_NONEMPTY", str(blocked.exception))
            self.assertEqual(initial, _core_projection_fingerprints(database_path))

            _run_downgrade(
                database_path,
                "20260807_01",
                allow_data_loss="true",
                restore_root=restore_root,
            )
            self.assertEqual(initial, _core_projection_fingerprints(database_path))
            with closing(sqlite3.connect(database_path)) as connection:
                self.assertEqual(P1_EXPECTED_COLUMNS["document_sources"], _columns(
                    connection, "document_sources"
                ))
                self.assertEqual(P1_EXPECTED_COLUMNS["generated_artifacts"], _columns(
                    connection, "generated_artifacts"
                ))
                self.assertEqual(P1_EXPECTED_COLUMNS["processing_jobs"], _columns(
                    connection, "processing_jobs"
                ))

            run_alembic(database_path, "20260807_02")
            self.assertEqual(initial, _core_projection_fingerprints(database_path))


class P2RestoredCopyValidationTests(unittest.TestCase):
    def test_db_path_is_bound_restore_at_exact_p2_revision(self) -> None:
        database_path = _restore_validation_path(self)
        with closing(sqlite3.connect(database_path.as_uri() + "?mode=ro", uri=True)) as connection:
            connection.execute("PRAGMA query_only=ON")
            versions = connection.execute("SELECT version_num FROM alembic_version").fetchall()
        self.assertEqual([("20260807_02",)], versions)

    def test_p2_schema_health_and_required_objects_are_read_only(self) -> None:
        database_path = _restore_validation_path(self)
        sidecars = tuple(Path(f"{database_path}{suffix}") for suffix in ("-wal", "-shm", "-journal"))
        before = (
            database_path.read_bytes(), database_path.stat().st_size, database_path.stat().st_mtime_ns
        )
        before_sidecars = {path: path.read_bytes() if path.exists() else None for path in sidecars}
        with closing(sqlite3.connect(database_path.as_uri() + "?mode=ro", uri=True)) as connection:
            connection.execute("PRAGMA query_only=ON")
            self.assertTrue(set((*P1_TABLES, *P2_TABLES)).issubset(_table_names(connection)))
            for table_name in P1_TABLES:
                expected = P1_EXPECTED_COLUMNS[table_name] + P2_ADDED_COLUMNS.get(table_name, ())
                self.assertEqual(expected, _columns(connection, table_name))
            triggers = {
                str(row[0]) for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='trigger'"
                )
            }
            self.assertTrue({
                "processing_jobs_spec_guard_insert", "processing_jobs_spec_guard_update"
            }.issubset(triggers))
            _health(connection)
        after = (
            database_path.read_bytes(), database_path.stat().st_size, database_path.stat().st_mtime_ns
        )
        self.assertEqual(before, after)
        self.assertEqual(
            before_sidecars,
            {path: path.read_bytes() if path.exists() else None for path in sidecars},
        )


def load_tests(loader, standard_tests, _pattern):
    """Keep restore-only validators out of unbound full-suite discovery."""

    suite = unittest.TestSuite()
    for case in (
        P2MigrationTests,
        P2CoreProjectionMigrationTests,
    ):
        suite.addTests(loader.loadTestsFromTestCase(case))
    return suite


if __name__ == "__main__":
    unittest.main()
