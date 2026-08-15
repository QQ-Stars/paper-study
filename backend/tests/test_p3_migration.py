from __future__ import annotations

from contextlib import closing
import os
from pathlib import Path
import stat
from types import SimpleNamespace
import unittest
from unittest import mock


_SQLITE_DLL_HANDLE = None
if os.name == "nt" and os.environ.get("P3_SQLITE_DLL_DIR"):
    _SQLITE_DLL_HANDLE = os.add_dll_directory(os.environ["P3_SQLITE_DLL_DIR"])

import sqlite3

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory

from backend.tests.support.p1_database import (
    ALEMBIC_CONFIG_PATH,
    REPOSITORY_ROOT,
    run_alembic,
    temporary_legacy_database,
    temporary_restore_database,
)


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
P3_TABLES = (
    "document_chunk_embeddings",
    "artifact_translation_checkpoints",
    "document_chunks_fts",
)
P3_INDEXES = {
    "ux_document_chunks_chunk_key",
    "ix_document_chunks_source_status_sequence",
    "ix_embeddings_source_profile",
    "ix_embeddings_chunk",
    "ix_checkpoints_artifact_status_sequence",
}
P3_TRIGGERS = {
    "document_chunks_fts_ai",
    "document_chunks_fts_ad",
    "document_chunks_fts_au",
}


def _is_reparse_point(path: Path) -> bool:
    attributes = getattr(path.lstat(), "st_file_attributes", 0)
    return path.is_symlink() or bool(
        attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    )


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


def _file_state(path: Path) -> tuple[bytes, int, int, int]:
    metadata = path.stat()
    return (
        path.read_bytes(),
        metadata.st_size,
        metadata.st_mtime_ns,
        getattr(metadata, "st_file_attributes", 0),
    )


def _run_p3_downgrade(
    database_path: Path,
    *,
    allow_data_loss: str | None = None,
    restore_root: Path | None = None,
) -> None:
    previous_database = os.environ.get("DB_PATH")
    previous_restore_root = os.environ.get("MIGRATION_RESTORE_ROOT")
    os.environ["DB_PATH"] = str(database_path.resolve(strict=True))
    if restore_root is None:
        os.environ.pop("MIGRATION_RESTORE_ROOT", None)
    else:
        os.environ["MIGRATION_RESTORE_ROOT"] = str(restore_root.resolve(strict=True))
    try:
        configuration = Config(str(ALEMBIC_CONFIG_PATH))
        if allow_data_loss is not None:
            configuration.cmd_opts = SimpleNamespace(
                x=[f"allow_p3_data_loss={allow_data_loss}"]
            )
        command.downgrade(configuration, "20260807_02")
    finally:
        if previous_database is None:
            os.environ.pop("DB_PATH", None)
        else:
            os.environ["DB_PATH"] = previous_database
        if previous_restore_root is None:
            os.environ.pop("MIGRATION_RESTORE_ROOT", None)
        else:
            os.environ["MIGRATION_RESTORE_ROOT"] = previous_restore_root


def _insert_p2_chunk_sentinel(database_path: Path) -> None:
    with closing(sqlite3.connect(database_path)) as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute(
            "INSERT INTO document_sources("
            "id,paper_id,mode,status,provider,model,pdf_sha256,options_hash,"
            "content_sha256,markdown,page_count,processing_version,created_at,updated_at,"
            "source_key,ready_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "source-p3-migration",
                "paper-1",
                "native",
                "ready",
                "local",
                "native-v1",
                "a" * 64,
                "b" * 64,
                "c" * 64,
                "Tail sentinel: P3 migration",
                1,
                "native-v1",
                "2026-08-07T00:00:00Z",
                "2026-08-07T00:00:01Z",
                "source-key-p3-migration",
                "2026-08-07T00:00:01Z",
            ),
        )
        connection.execute(
            "INSERT INTO document_chunks("
            "id,source_document_id,sequence,heading_path,page_start,page_end,content,"
            "content_sha256,token_count) VALUES(?,?,?,?,?,?,?,?,?)",
            (
                "chunk-p3-migration",
                "source-p3-migration",
                0,
                '["Migration"]',
                1,
                1,
                "Tail sentinel: P3 migration",
                "d" * 64,
                5,
            ),
        )
        connection.commit()


class P3MigrationTests(unittest.TestCase):
    """Public migration seam for the final P3 additive schema."""

    def test_missing_p2_base_schema_fails_before_persistent_ddl(self) -> None:
        with temporary_legacy_database() as database_path:
            run_alembic(database_path, "20260807_02")
            with closing(sqlite3.connect(database_path)) as connection:
                connection.execute("DROP TABLE ocr_page_checkpoints")
                connection.commit()
                before = connection.execute(
                    "SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name"
                ).fetchall()

            with self.assertRaises(Exception) as raised:
                run_alembic(database_path, "20260807_03")

            self.assertIn("P3_BASE_SCHEMA_MISSING", str(raised.exception))
            with closing(sqlite3.connect(database_path)) as connection:
                after = connection.execute(
                    "SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name"
                ).fetchall()
                self.assertEqual(before, after)

    def test_fts_capability_failure_leaves_no_persistent_ddl(self) -> None:
        original_connect = sqlite3.connect

        def unavailable_fts_connect(*args: object, **kwargs: object) -> sqlite3.Connection:
            connection = original_connect(*args, **kwargs)

            def authorizer(
                action: int,
                _arg1: str | None,
                _arg2: str | None,
                database: str | None,
                _source: str | None,
            ) -> int:
                if action == sqlite3.SQLITE_CREATE_VTABLE and database == "temp":
                    return sqlite3.SQLITE_DENY
                return sqlite3.SQLITE_OK

            connection.set_authorizer(authorizer)
            return connection

        with temporary_legacy_database() as database_path:
            run_alembic(database_path, "20260807_02")
            with closing(sqlite3.connect(database_path)) as connection:
                before = connection.execute(
                    "SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name"
                ).fetchall()

            with mock.patch.object(sqlite3.dbapi2, "connect", unavailable_fts_connect):
                with self.assertRaises(Exception) as raised:
                    run_alembic(database_path, "20260807_03")

            self.assertIn("FTS5_TRIGRAM_UNAVAILABLE", str(raised.exception))
            with closing(sqlite3.connect(database_path)) as connection:
                after = connection.execute(
                    "SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name"
                ).fetchall()
                self.assertEqual(before, after)

    def test_nonempty_p3_downgrade_is_blocked_without_mutation(self) -> None:
        with temporary_legacy_database() as database_path:
            run_alembic(database_path, "20260807_02")
            _insert_p2_chunk_sentinel(database_path)
            run_alembic(database_path, "20260807_03")
            with closing(sqlite3.connect(database_path)) as connection:
                before_schema = connection.execute(
                    "SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name"
                ).fetchall()
                before_chunk = connection.execute(
                    "SELECT * FROM document_chunks WHERE id='chunk-p3-migration'"
                ).fetchone()

            with self.assertRaises(Exception) as raised:
                _run_p3_downgrade(database_path)

            self.assertIn("P3_DOWNGRADE_BLOCKED_NONEMPTY", str(raised.exception))
            with closing(sqlite3.connect(database_path)) as connection:
                after_schema = connection.execute(
                    "SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name"
                ).fetchall()
                after_chunk = connection.execute(
                    "SELECT * FROM document_chunks WHERE id='chunk-p3-migration'"
                ).fetchone()
                self.assertEqual(before_schema, after_schema)
                self.assertEqual(before_chunk, after_chunk)
                self.assertEqual(
                    [("20260807_03",)],
                    connection.execute(
                        "SELECT version_num FROM alembic_version"
                    ).fetchall(),
                )

    def test_isolated_data_loss_opt_in_preserves_p2_and_allows_reupgrade(self) -> None:
        with temporary_restore_database() as (database_path, restore_root):
            run_alembic(database_path, "20260807_02")
            _insert_p2_chunk_sentinel(database_path)
            with closing(sqlite3.connect(database_path)) as connection:
                p2_columns = tuple(
                    str(row[1])
                    for row in connection.execute("PRAGMA table_xinfo(document_chunks)")
                )
                p2_chunk = connection.execute(
                    "SELECT * FROM document_chunks WHERE id='chunk-p3-migration'"
                ).fetchone()

            run_alembic(database_path, "20260807_03")
            _run_p3_downgrade(
                database_path,
                allow_data_loss="true",
                restore_root=restore_root,
            )

            with closing(sqlite3.connect(database_path)) as connection:
                self.assertEqual(
                    p2_columns,
                    tuple(
                        str(row[1])
                        for row in connection.execute(
                            "PRAGMA table_xinfo(document_chunks)"
                        )
                    ),
                )
                self.assertEqual(
                    p2_chunk,
                    connection.execute(
                        "SELECT * FROM document_chunks WHERE id='chunk-p3-migration'"
                    ).fetchone(),
                )
                self.assertTrue(
                    set(P3_TABLES).isdisjoint(
                        {
                            str(row[0])
                            for row in connection.execute(
                                "SELECT name FROM sqlite_master WHERE type='table'"
                            )
                        }
                    )
                )
                self.assertEqual(
                    [("20260807_02",)],
                    connection.execute(
                        "SELECT version_num FROM alembic_version"
                    ).fetchall(),
                )

            run_alembic(database_path, "20260807_03")
            with closing(sqlite3.connect(database_path)) as connection:
                self.assertTrue(
                    set(P3_TABLES).issubset(
                        {
                            str(row[0])
                            for row in connection.execute(
                                "SELECT name FROM sqlite_master WHERE type='table'"
                            )
                        }
                    )
                )
                self.assertEqual(
                    "Tail sentinel: P3 migration",
                    connection.execute(
                        "SELECT content FROM document_chunks "
                        "WHERE id='chunk-p3-migration'"
                    ).fetchone()[0],
                )
                self.assertEqual(
                    [("20260807_03",)],
                    connection.execute(
                        "SELECT version_num FROM alembic_version"
                    ).fetchall(),
                )

    def test_embedding_and_checkpoint_constraints_are_hard(self) -> None:
        with temporary_legacy_database() as database_path:
            run_alembic(database_path, "20260807_02")
            _insert_p2_chunk_sentinel(database_path)
            run_alembic(database_path, "20260807_03")
            sha_a = "a" * 64
            sha_b = "b" * 64
            now = "2026-08-07T00:00:00Z"
            with closing(sqlite3.connect(database_path)) as connection:
                connection.execute("PRAGMA foreign_keys=ON")
                connection.execute(
                    "INSERT INTO generated_artifacts("
                    "id,paper_id,kind,source_document_id,status,generator_provider,generator_model,"
                    "prompt_version,artifact_key,created_at,updated_at) VALUES("
                    "'artifact-p3-migration','paper-1','translation','source-p3-migration',"
                    "'queued','provider','model','prompt-v1','artifact-key-p3-migration',?,?)",
                    (now, now),
                )
                connection.execute(
                    "INSERT INTO document_chunk_embeddings("
                    "id,chunk_id,source_document_id,provider,model,embedding_version,"
                    "dimensions,vector,vector_sha256,chunk_content_sha256,status,error_code,"
                    "error_message,created_at,updated_at,stale_at) VALUES("
                    "'embedding-valid','chunk-p3-migration','source-p3-migration','provider',"
                    "'model','v1',1,?,?,?,'ready',NULL,NULL,?,?,NULL)",
                    (b"\x00\x00\x80?", sha_a, "d" * 64, now, now),
                )
                connection.execute(
                    "INSERT INTO artifact_translation_checkpoints("
                    "artifact_id,chunk_id,sequence,source_content_sha256,provider,model,prompt_version,"
                    "status,translated_markdown,content_sha256,attempt,error_code,error_message,"
                    "created_at,updated_at) VALUES("
                    "'artifact-p3-migration','chunk-p3-migration',0,?,'provider','model','prompt-v1',"
                    "'succeeded','translated tail sentinel',?,1,NULL,NULL,?,?)",
                    (sha_b, sha_a, now, now),
                )

                invalid_embeddings = (
                    (
                        "embedding-missing-chunk",
                        "missing-chunk",
                        "source-p3-migration",
                        1,
                        b"\x00\x00\x80?",
                        sha_a,
                        "d" * 64,
                        "ready",
                        None,
                        None,
                        None,
                    ),
                    (
                        "embedding-ready-null-vector",
                        "chunk-p3-migration",
                        "source-p3-migration",
                        1,
                        None,
                        None,
                        "d" * 64,
                        "ready",
                        None,
                        None,
                        None,
                    ),
                    (
                        "embedding-failed-with-vector",
                        "chunk-p3-migration",
                        "source-p3-migration",
                        1,
                        b"\x00\x00\x80?",
                        sha_a,
                        "d" * 64,
                        "failed",
                        "EMBEDDING_REQUEST_FAILED",
                        None,
                        None,
                    ),
                    (
                        "embedding-stale-no-time",
                        "chunk-p3-migration",
                        "source-p3-migration",
                        1,
                        None,
                        None,
                        "d" * 64,
                        "stale",
                        None,
                        None,
                        None,
                    ),
                )
                for values in invalid_embeddings:
                    with self.subTest(embedding=values[0]), self.assertRaises(sqlite3.IntegrityError):
                        connection.execute(
                            "INSERT INTO document_chunk_embeddings("
                            "id,chunk_id,source_document_id,provider,model,embedding_version,dimensions,"
                            "vector,vector_sha256,chunk_content_sha256,status,error_code,error_message,"
                            "created_at,updated_at,stale_at) VALUES("
                            ":id,:chunk_id,:source_id,:provider,'model','v1',:dimensions,"
                            ":vector,:vector_sha,:chunk_sha,:status,:error_code,:error_message,"
                            ":created_at,:updated_at,:stale_at)",
                            {
                                "id": values[0],
                                "chunk_id": values[1],
                                "source_id": values[2],
                                "provider": values[0],
                                "dimensions": values[3],
                                "vector": values[4],
                                "vector_sha": values[5],
                                "chunk_sha": values[6],
                                "status": values[7],
                                "error_code": values[8],
                                "error_message": values[9],
                                "created_at": now,
                                "updated_at": now,
                                "stale_at": values[10],
                            },
                        )

                invalid_checkpoints = (
                    ("checkpoint-missing-chunk", "missing-chunk", 1, "succeeded", "translated", sha_a, 1, None),
                    ("checkpoint-negative-sequence", "chunk-p3-migration", -1, "succeeded", "translated", sha_a, 1, None),
                    ("checkpoint-succeeded-no-body", "chunk-p3-migration", 1, "succeeded", None, sha_a, 1, None),
                    ("checkpoint-failed-no-code", "chunk-p3-migration", 1, "failed", None, None, 1, None),
                    ("checkpoint-negative-attempt", "chunk-p3-migration", 1, "queued", None, None, -1, None),
                )
                for artifact_id, *_rest in invalid_checkpoints:
                    connection.execute(
                        "INSERT INTO generated_artifacts("
                        "id,paper_id,kind,source_document_id,status,generator_provider,generator_model,"
                        "prompt_version,artifact_key,created_at,updated_at) VALUES("
                        "?,'paper-1','translation','source-p3-migration','queued',?,?,? ,?,?,?)",
                        (
                            artifact_id,
                            f"provider-{artifact_id}",
                            f"model-{artifact_id}",
                            f"prompt-{artifact_id}",
                            f"artifact-key-{artifact_id}",
                            now,
                            now,
                        ),
                    )
                for values in invalid_checkpoints:
                    with self.subTest(checkpoint=values[0]), self.assertRaises(sqlite3.IntegrityError):
                        connection.execute(
                            "INSERT INTO artifact_translation_checkpoints("
                            "artifact_id,chunk_id,sequence,source_content_sha256,provider,model,prompt_version,"
                            "status,translated_markdown,content_sha256,attempt,error_code,error_message,"
                            "created_at,updated_at) VALUES("
                            ":artifact_id,:chunk_id,:sequence,:source_sha,:provider,:model,:prompt,"
                            ":status,:translated_markdown,:content_sha,:attempt,:error_code,:error_message,"
                            ":created_at,:updated_at)",
                            {
                                "artifact_id": values[0],
                                "chunk_id": values[1],
                                "sequence": values[2],
                                "source_sha": sha_b,
                                "provider": f"provider-{values[0]}",
                                "model": f"model-{values[0]}",
                                "prompt": f"prompt-{values[0]}",
                                "status": values[3],
                                "translated_markdown": values[4],
                                "content_sha": values[5],
                                "attempt": values[6],
                                "error_code": values[7],
                                "error_message": None,
                                "created_at": now,
                                "updated_at": now,
                            },
                        )

    def test_revision_chain_and_additive_chunk_columns(self) -> None:
        configuration = Config(str(ALEMBIC_CONFIG_PATH))
        scripts = ScriptDirectory.from_config(configuration)
        self.assertEqual(["20260807_03"], scripts.get_heads())
        self.assertEqual("20260807_02", scripts.get_revision("20260807_03").down_revision)

        with temporary_legacy_database() as database_path:
            run_alembic(database_path, "20260807_02")
            with closing(sqlite3.connect(database_path)) as connection:
                before = tuple(
                    str(row[1])
                    for row in connection.execute("PRAGMA table_xinfo(document_chunks)")
                )

            run_alembic(database_path, "20260807_03")

            with closing(sqlite3.connect(database_path)) as connection:
                after = tuple(
                    str(row[1])
                    for row in connection.execute("PRAGMA table_xinfo(document_chunks)")
                )
                self.assertEqual(
                    before
                    + (
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
                    ),
                    after,
                )
                self.assertEqual(
                    [("20260807_03",)],
                    connection.execute(
                        "SELECT version_num FROM alembic_version"
                    ).fetchall(),
                )

    def test_p3_search_and_consumer_objects_are_exactly_present(self) -> None:
        with temporary_legacy_database() as database_path:
            run_alembic(database_path, "20260807_03")
            with closing(sqlite3.connect(database_path)) as connection:
                tables = {
                    str(row[0])
                    for row in connection.execute(
                        "SELECT name FROM sqlite_master WHERE type IN ('table','view')"
                    )
                }
                self.assertTrue(
                    {
                        "document_chunk_embeddings",
                        "artifact_translation_checkpoints",
                        "document_chunks_fts",
                    }.issubset(tables)
                )
                indexes = {
                    str(row[0])
                    for row in connection.execute(
                        "SELECT name FROM sqlite_master WHERE type='index'"
                    )
                }
                self.assertTrue(
                    {
                        "ux_document_chunks_chunk_key",
                        "ix_document_chunks_source_status_sequence",
                        "ix_embeddings_source_profile",
                        "ix_embeddings_chunk",
                        "ix_checkpoints_artifact_status_sequence",
                    }.issubset(indexes)
                )
                triggers = {
                    str(row[0])
                    for row in connection.execute(
                        "SELECT name FROM sqlite_master WHERE type='trigger'"
                    )
                }
                self.assertTrue(
                    {
                        "document_chunks_fts_ai",
                        "document_chunks_fts_ad",
                        "document_chunks_fts_au",
                    }.issubset(triggers)
                )


class P3OperationalDocumentationTests(unittest.TestCase):
    def test_p3_plan_uses_flat_inspect_fingerprint_shape(self) -> None:
        plan = (
            Path(__file__).resolve().parents[2]
            / "docs"
            / "superpowers"
            / "plans"
            / "2026-08-07-p3-source-consumers-search.md"
        ).read_text(encoding="utf-8")

        self.assertNotIn(".database.", plan)
        for literal in (
            "$env:PYTHONIOENCODING = 'utf-8'",
            "[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)",
            "$Fingerprint.tableCounts",
            "$Fingerprint.tableSha256",
            "$Fingerprint.contentCounts",
            "$Fingerprint.contentSha256",
            "$p3Before.alembicVersion",
            "$p3LiveBefore.alembicVersion",
            "$p3LiveAfter.quickCheck",
            "$p3LiveAfter.integrityCheck",
            "$p3LiveAfter.foreignKeyViolations",
        ):
            with self.subTest(literal=literal):
                self.assertIn(literal, plan)

    def test_p3_runbook_contains_fixed_migration_search_and_rollback_contract(self) -> None:
        runbook = (
            Path(__file__).resolve().parents[2] / "docs" / "DATABASE.md"
        ).read_text(encoding="utf-8")

        required_literals = (
            "## 13. P3 source consumers、search 与回滚门禁",
            "20260807_02 → 20260807_03",
            "trigram case_sensitive 0 remove_diacritics 1",
            "documentChunks",
            "chunkEmbeddings",
            "translationCheckpoints",
            "documentChunksFtsCoverage",
            "documentChunksFtsIntegrity",
            "INSERT INTO document_chunks_fts(document_chunks_fts,rank) VALUES('integrity-check',1)",
            "INSERT INTO document_chunks_fts(document_chunks_fts) VALUES('rebuild')",
            "query path never re-embeds",
            "source stale cascade",
            "停止新 enqueue → 停止 worker claim → 等待/取消 running jobs → 停止 API writer",
            "pre-p3-source-consumers-search",
            "upgrade 20260807_03",
            "downgrade 20260807_02",
            "-x allow_p3_data_loss=true downgrade 20260807_02",
            "P3_DOWNGRADE_BLOCKED_NONEMPTY",
            "API_BACKEND_MODE=legacy",
            "DOCUMENT_PIPELINE_MODE=legacy",
            "GENERATION_PIPELINE_MODE=legacy",
            "ARTIFACT_READ_MODE=legacy",
            "ARTIFACT_WRITE_MODE=legacy",
            "OCR_ENABLED=0",
        )
        for literal in required_literals:
            with self.subTest(literal=literal):
                self.assertIn(literal, runbook)

        legacy_tables = (
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
        for table in legacy_tables:
            with self.subTest(legacy_table=table):
                self.assertIn(f"`{table}` tableCounts/tableSha256", runbook)

        stable_content = (
            "paperIds",
            "explainers",
            "translations",
            "notes",
            "paperVectors",
            "documentSources",
            "generatedArtifacts",
            "processingJobs",
        )
        for key in stable_content:
            with self.subTest(stable_content=key):
                self.assertIn(f"`{key}` contentCounts/contentSha256", runbook)

        self.assertIn("map-presence guard", runbook)
        self.assertIn("before/after equality guard", runbook)
        self.assertIn("only on restore-validation-*/app.db", runbook)
        self.assertIn("不得在 Live 使用 allow_p3_data_loss=true", runbook)


class P3RestoredCopyValidationTests(unittest.TestCase):
    def test_db_path_is_bound_restore_at_exact_p3_revision(self) -> None:
        database_path = _restore_validation_path(self)
        with closing(sqlite3.connect(database_path.as_uri() + "?mode=ro", uri=True)) as connection:
            connection.execute("PRAGMA query_only=ON")
            versions = connection.execute("SELECT version_num FROM alembic_version").fetchall()
        self.assertEqual([("20260807_03",)], versions)

    def test_p3_schema_fts_health_and_required_objects_are_read_only(self) -> None:
        database_path = _restore_validation_path(self)
        sidecars = tuple(Path(f"{database_path}{suffix}") for suffix in ("-wal", "-shm", "-journal"))
        before = _file_state(database_path)
        before_sidecars = {
            path: _file_state(path) if path.exists() else None for path in sidecars
        }
        with closing(sqlite3.connect(database_path.as_uri() + "?mode=ro", uri=True)) as connection:
            connection.execute("PRAGMA query_only=ON")
            tables = {
                str(row[0]) for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type IN ('table','view')"
                )
            }
            self.assertTrue(set(P3_TABLES).issubset(tables))
            chunk_columns = tuple(
                str(row[1]) for row in connection.execute(
                    "PRAGMA table_xinfo(document_chunks)"
                )
            )
            self.assertEqual(P3_CHUNK_COLUMNS, chunk_columns[-len(P3_CHUNK_COLUMNS):])
            indexes = {
                str(row[0]) for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='index'"
                )
            }
            triggers = {
                str(row[0]) for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='trigger'"
                )
            }
            self.assertTrue(P3_INDEXES.issubset(indexes))
            self.assertEqual(P3_TRIGGERS, triggers & P3_TRIGGERS)
            fts_sql = str(connection.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name='document_chunks_fts'"
            ).fetchone()[0])
            self.assertIn("content='document_chunks'", fts_sql)
            self.assertIn("content_rowid='rowid'", fts_sql)
            self.assertIn("trigram case_sensitive 0 remove_diacritics 1", fts_sql)
            total = connection.execute("SELECT count(*) FROM document_chunks").fetchone()[0]
            indexed = connection.execute(
                "SELECT count(*) FROM document_chunks c "
                "JOIN document_chunks_fts f ON f.rowid=c.rowid"
            ).fetchone()[0]
            self.assertEqual(total, indexed)
            self.assertEqual("ok", connection.execute("PRAGMA quick_check").fetchone()[0])
            self.assertEqual("ok", connection.execute("PRAGMA integrity_check").fetchone()[0])
            self.assertEqual([], connection.execute("PRAGMA foreign_key_check").fetchall())
        self.assertEqual(before, _file_state(database_path))
        self.assertEqual(
            before_sidecars,
            {path: _file_state(path) if path.exists() else None for path in sidecars},
        )


def load_tests(loader, standard_tests, _pattern):
    """Keep restore-only validators out of unbound full-suite discovery."""

    suite = unittest.TestSuite()
    for case in (
        P3MigrationTests,
        P3OperationalDocumentationTests,
    ):
        suite.addTests(loader.loadTestsFromTestCase(case))
    return suite


if __name__ == "__main__":
    unittest.main()
