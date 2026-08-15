from __future__ import annotations

import hashlib
import json
import os
import stat as stat_module
import subprocess
import sys
import tempfile
import unittest
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.parse import unquote, urlsplit
from unittest import mock


_SQLITE_DLL_HANDLE = None
if os.name == "nt" and os.environ.get("P3_SQLITE_DLL_DIR"):
    _SQLITE_DLL_HANDLE = os.add_dll_directory(os.environ["P3_SQLITE_DLL_DIR"])

import sqlite3

import backend.app.infrastructure.database_backup as database_backup_module
from backend.app.infrastructure.database_backup import (
    DatabaseBackupError,
    create_verified_backup,
    restore_backup_for_validation,
    verify_backup,
)
from backend.app.domain.processing import (
    LegacyImportedJobSpecV1,
    ObsidianSyncJobSpecV1,
    encode_job_spec_v1,
)
from backend.tests.fixtures.bound_root_platform import (
    DeterministicBoundRootPlatform,
)
from backend.tests.support.p1_database import run_alembic, temporary_legacy_database


class _InvocationPathTripwire:
    def __init__(self, test_case: unittest.TestCase, owned_root: Path) -> None:
        self._test_case = test_case
        self._owned_root = owned_root.resolve()
        self._workspace_live = (
            Path(__file__).resolve().parents[2] / "data" / "app.db"
        ).resolve()
        self._original_db_path = os.environ.get("DB_PATH")
        self._patchers: list[mock._patch] = []
        self._real_path_open = Path.open
        self._real_os_open = os.open
        self._real_sqlite_connect = sqlite3.connect
        self._real_open_readonly = database_backup_module._open_readonly
        self.path_open_adapter: Callable[[Path, str, Any], Any] | None = None
        self.observed_paths: list[Path] = []
        self.hostile_open_count = 0
        self.violations: list[Path] = []

    def __enter__(self) -> "_InvocationPathTripwire":
        os.environ["DB_PATH"] = str(self._workspace_live)
        self._patchers = [
            mock.patch.object(Path, "open", autospec=True, side_effect=self._path_open),
            mock.patch.object(
                database_backup_module.os,
                "open",
                autospec=True,
                side_effect=self._os_open,
            ),
            mock.patch.object(
                database_backup_module.sqlite3,
                "connect",
                autospec=True,
                side_effect=self._sqlite_connect,
            ),
            mock.patch.object(
                database_backup_module,
                "_open_readonly",
                autospec=True,
                side_effect=self._open_readonly,
            ),
        ]
        for patcher in self._patchers:
            patcher.start()
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        for patcher in reversed(self._patchers):
            patcher.stop()
        if self._original_db_path is None:
            os.environ.pop("DB_PATH", None)
        else:
            os.environ["DB_PATH"] = self._original_db_path
        self._test_case.assertEqual(self.violations, [])
        self._test_case.assertGreater(len(self.observed_paths), 0)
        self._test_case.assertEqual(self.hostile_open_count, 0)

    def _resolved_path(self, value: object, *, dir_fd: int | None = None) -> Path | None:
        if isinstance(value, int):
            return None
        raw = os.fspath(value)
        if isinstance(raw, bytes):
            raw = os.fsdecode(raw)
        if raw == ":memory:":
            return None
        if raw.startswith("file:"):
            parsed = urlsplit(raw)
            raw = unquote(parsed.path)
            if os.name == "nt" and len(raw) >= 3 and raw[0] == "/" and raw[2] == ":":
                raw = raw[1:]
        candidate = Path(raw)
        if not candidate.is_absolute() and dir_fd is not None and os.name == "posix":
            descriptor_path = Path(f"/proc/self/fd/{dir_fd}")
            try:
                candidate = descriptor_path.resolve(strict=True) / candidate
            except OSError:
                return None
        return candidate.resolve(strict=False)

    def _observe(self, value: object, *, dir_fd: int | None = None) -> None:
        candidate = self._resolved_path(value, dir_fd=dir_fd)
        if candidate is None:
            return
        hostile = candidate == self._workspace_live or (
            candidate.parent == self._workspace_live.parent
            and candidate.name.startswith(f"{self._workspace_live.name}-")
        )
        if hostile:
            self.hostile_open_count += 1
        try:
            candidate.relative_to(self._owned_root)
        except ValueError:
            self.violations.append(candidate)
            raise AssertionError(f"tripwire rejected filesystem access outside test root: {candidate.name}")
        self.observed_paths.append(candidate)

    def _path_open(self, path: Path, mode: str = "r", *args: object, **kwargs: object) -> Any:
        self._observe(path)
        handle = self._real_path_open(path, mode, *args, **kwargs)
        if self.path_open_adapter is not None:
            return self.path_open_adapter(path, mode, handle)
        return handle

    def _os_open(
        self,
        path: object,
        flags: int,
        mode: int = 0o777,
        *,
        dir_fd: int | None = None,
    ) -> int:
        self._observe(path, dir_fd=dir_fd)
        return self._real_os_open(path, flags, mode, dir_fd=dir_fd)

    def _sqlite_connect(self, database: object, *args: object, **kwargs: object) -> sqlite3.Connection:
        self._observe(database)
        return self._real_sqlite_connect(database, *args, **kwargs)

    def _open_readonly(self, database_path: Path) -> sqlite3.Connection:
        self._observe(database_path)
        return self._real_open_readonly(database_path)


class _ReplacingManifestHandle:
    def __init__(self, handle: Any, path: Path, sentinel: bytes) -> None:
        self._handle = handle
        self._path = path
        self._sentinel = sentinel
        self.fsync_failed = False
        self.replacement_created = False

    def __enter__(self) -> "_ReplacingManifestHandle":
        self._handle.__enter__()
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> object:
        result = self._handle.__exit__(exc_type, exc, traceback)
        if self.fsync_failed:
            self._path.unlink()
            self._path.write_bytes(self._sentinel)
            self.replacement_created = True
        return result

    def __getattr__(self, name: str) -> Any:
        return getattr(self._handle, name)


def _append_bytes_with_delete_sharing(path: Path, payload: bytes) -> None:
    if os.name != "nt":
        with path.open("ab") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        return

    import ctypes
    import msvcrt
    from ctypes import wintypes

    generic_write = 0x40000000
    share_read = 0x00000001
    share_write = 0x00000002
    share_delete = 0x00000004
    open_existing = 3
    normal_attributes = 0x00000080
    invalid_handle_value = ctypes.c_void_p(-1).value
    create_file = ctypes.WinDLL("kernel32", use_last_error=True).CreateFileW
    create_file.argtypes = (
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    )
    create_file.restype = wintypes.HANDLE
    raw_handle = create_file(
        str(path),
        generic_write,
        share_read | share_write | share_delete,
        None,
        open_existing,
        normal_attributes,
        None,
    )
    if raw_handle == invalid_handle_value:
        raise OSError(ctypes.get_last_error(), "CreateFileW failed")
    descriptor: int | None = None
    try:
        descriptor = msvcrt.open_osfhandle(
            int(raw_handle),
            os.O_WRONLY | getattr(os, "O_BINARY", 0),
        )
        raw_handle = None
        with os.fdopen(descriptor, "wb", buffering=0) as handle:
            descriptor = None
            handle.seek(0, os.SEEK_END)
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if raw_handle is not None:
            database_backup_module._close_windows_handle(int(raw_handle))


def _create_minimal_database(path: Path) -> None:
    connection = sqlite3.connect(path)
    try:
        connection.execute("CREATE TABLE papers(id TEXT PRIMARY KEY)")
        connection.execute("INSERT INTO papers(id) VALUES('paper-1')")
        connection.commit()
    finally:
        connection.close()


def _file_evidence(path: Path) -> tuple[bytes, tuple[int, int, int, int, int, int]]:
    payload = path.read_bytes()
    metadata = path.stat()
    return payload, (
        int(metadata.st_size),
        int(metadata.st_mtime_ns),
        int(metadata.st_ctime_ns),
        int(metadata.st_mode),
        int(metadata.st_dev),
        int(metadata.st_ino),
    )


def _insert_nonempty_p1_core_rows(connection: sqlite3.Connection) -> None:
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute(
        "INSERT INTO document_sources("
        "id,paper_id,mode,status,provider,model,pdf_sha256,options_hash,"
        "content_sha256,markdown,page_count,processing_version,error_code,error_message,"
        "created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            "source-core", "paper-1", "native", "ready", "local", "native-v1",
            "a" * 64, "b" * 64, "c" * 64, "# source core", 3, "p1-v1",
            None, "core-source-no-secret", "2026-08-07T00:00:00Z",
            "2026-08-07T00:01:00Z",
        ),
    )
    connection.execute(
        "INSERT INTO generated_artifacts("
        "id,paper_id,kind,source_document_id,status,content,content_sha256,"
        "generator_provider,generator_model,prompt_version,error_code,error_message,"
        "created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            "artifact-core", "paper-1", "explainer", "source-core", "ready",
            "# artifact core", "d" * 64, "provider-v1", "model-v1", "prompt-v1",
            None, "core-artifact-no-secret", "2026-08-07T00:02:00Z",
            "2026-08-07T00:03:00Z",
        ),
    )
    connection.execute(
        "INSERT INTO processing_jobs("
        "id,paper_id,job_type,source_mode,status,progress_json,attempt,max_attempts,"
        "idempotency_key,error_code,error_message,created_at,started_at,finished_at,"
        "cancelled_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            "job-core", "paper-1", "source_materialize", "native", "queued",
            '{"phase":"p1"}', 0, 3, "core-job-key", None,
            "core-job-no-secret", "2026-08-07T00:04:00Z", None, None, None,
        ),
    )
    connection.commit()


def _insert_p2_auxiliary_rows(connection: sqlite3.Connection) -> None:
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute(
        "INSERT INTO paper_artifact_heads(paper_id,kind,artifact_id,updated_at) "
        "VALUES('paper-1','explainer','artifact-core','2026-08-07T00:05:00Z')"
    )
    connection.execute(
        "INSERT INTO processing_job_events(job_id,sequence,event_type,progress_json,created_at) "
        "VALUES('job-core',1,'enqueued','{}','2026-08-07T00:06:00Z')"
    )
    connection.execute(
        "INSERT INTO ocr_page_checkpoints("
        "source_document_id,page_number,status,markdown,content_sha256,attempt,created_at,updated_at) "
        "VALUES('source-core',1,'succeeded','page core',?,0,?,?)",
        ("e" * 64, "2026-08-07T00:07:00Z", "2026-08-07T00:08:00Z"),
    )
    connection.execute(
        "UPDATE processing_jobs SET lease_token=?, result_json=? WHERE id='job-core'",
        ("lease-token-must-not-leak", '{"result":"p2"}'),
    )
    connection.commit()


def _insert_p3_search_rows(connection: sqlite3.Connection) -> None:
    """Seed one valid P3 chunk, embedding, and translation checkpoint.

    The rows deliberately use the public additive schema rather than a mock so
    the backup seam exercises FTS synchronization and the foreign-key graph.
    """

    connection.execute("PRAGMA foreign_keys=ON")
    timestamp = "2026-08-07T00:09:00Z"
    chunk_content = "P3 searchable source chunk."
    chunk_sha256 = hashlib.sha256(chunk_content.encode("utf-8")).hexdigest()
    vector = b"\x00\x00\x80?"
    vector_sha256 = hashlib.sha256(vector).hexdigest()
    translated_markdown = "P3 \u53ef\u68c0\u7d22\u6e90\u6587\u5206\u5757\u3002"
    translated_sha256 = hashlib.sha256(
        translated_markdown.encode("utf-8")
    ).hexdigest()
    connection.execute(
        "INSERT INTO document_chunks("
        "id,source_document_id,sequence,heading_path,page_start,page_end,content,"
        "content_sha256,token_count,status,content_kind,chunk_key,chunking_version,"
        "source_content_sha256,char_start,char_end,created_at,updated_at,stale_at) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            "chunk-p3",
            "source-core",
            0,
            "Methods",
            1,
            1,
            chunk_content,
            chunk_sha256,
            5,
            "ready",
            "text",
            "f" * 64,
            "p3-test-v1",
            "c" * 64,
            0,
            len(chunk_content),
            timestamp,
            timestamp,
            None,
        ),
    )
    connection.execute(
        "INSERT INTO document_chunk_embeddings("
        "id,chunk_id,source_document_id,provider,model,embedding_version,dimensions,"
        "vector,vector_sha256,chunk_content_sha256,status,error_code,error_message,"
        "created_at,updated_at,stale_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            "embedding-p3",
            "chunk-p3",
            "source-core",
            "fake-embedding",
            "fake-embedding-model",
            "p3-test-v1",
            1,
            vector,
            vector_sha256,
            chunk_sha256,
            "ready",
            None,
            None,
            timestamp,
            timestamp,
            None,
        ),
    )
    connection.execute(
        "INSERT INTO artifact_translation_checkpoints("
        "artifact_id,chunk_id,sequence,source_content_sha256,provider,model,prompt_version,"
        "status,translated_markdown,content_sha256,attempt,error_code,error_message,"
        "created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            "artifact-core",
            "chunk-p3",
            0,
            "c" * 64,
            "fake-translation",
            "fake-translation-model",
            "p3-test-v1",
            "succeeded",
            translated_markdown,
            translated_sha256,
            1,
            None,
            None,
            timestamp,
            timestamp,
        ),
    )
    connection.commit()


def _rebind_manifest_file_evidence(backup_path: Path, manifest_path: Path) -> None:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["backupSizeBytes"] = backup_path.stat().st_size
    manifest["backupSha256"] = hashlib.sha256(backup_path.read_bytes()).hexdigest()
    payload = {key: value for key, value in manifest.items() if key != "manifestSha256"}
    manifest["manifestSha256"] = hashlib.sha256(
        database_backup_module._canonical_json_bytes(payload)
    ).hexdigest()
    manifest_path.write_bytes(database_backup_module._canonical_json_bytes(manifest))


class DatabaseBackupTests(unittest.TestCase):
    def test_manifest_records_p3_search_fingerprints(self) -> None:
        """P3 manifests bind logical chunk/search state, never FTS shadows."""

        with temporary_legacy_database() as source_path:
            run_alembic(source_path, "20260807_01")
            with closing(sqlite3.connect(source_path)) as connection:
                _insert_nonempty_p1_core_rows(connection)
            run_alembic(source_path, "20260807_02")
            with closing(sqlite3.connect(source_path)) as connection:
                _insert_p2_auxiliary_rows(connection)
            run_alembic(source_path, "20260807_03")
            with closing(sqlite3.connect(source_path)) as connection:
                _insert_p3_search_rows(connection)

            result = create_verified_backup(
                source_path, source_path.parent / "backups", label="p3-search"
            )
            database = result.manifest.database
            expected_content = {
                "documentChunks",
                "chunkEmbeddings",
                "translationCheckpoints",
                "documentChunksFtsCoverage",
                "documentChunksFtsIntegrity",
            }
            self.assertTrue(expected_content.issubset(database.content_counts))
            self.assertTrue(expected_content.issubset(database.content_sha256))
            for key in expected_content:
                self.assertEqual(1, database.content_counts[key])
                self.assertRegex(database.content_sha256[key], r"^[0-9a-f]{64}$")
            self.assertFalse(
                any(
                    table_name == "document_chunks_fts"
                    or table_name.startswith("document_chunks_fts_")
                    for table_name in database.table_counts
                )
            )
            self.assertTrue(verify_backup(result.backup_path, result.manifest_path).valid)

    def test_verify_detects_p3_search_tampering(self) -> None:
        """P3 content and logical FTS corruption are classified fail-closed."""

        with temporary_legacy_database() as source_path:
            run_alembic(source_path, "20260807_01")
            with closing(sqlite3.connect(source_path)) as connection:
                _insert_nonempty_p1_core_rows(connection)
            run_alembic(source_path, "20260807_02")
            with closing(sqlite3.connect(source_path)) as connection:
                _insert_p2_auxiliary_rows(connection)
            run_alembic(source_path, "20260807_03")
            with closing(sqlite3.connect(source_path)) as connection:
                _insert_p3_search_rows(connection)
            result = create_verified_backup(
                source_path, source_path.parent / "backups", label="p3-tamper"
            )
            pristine_backup = result.backup_path.read_bytes()
            pristine_manifest = result.manifest_path.read_bytes()
            cases = (
                (
                    "UPDATE document_chunks SET content='tampered chunk' "
                    "WHERE id='chunk-p3'",
                    "BACKUP_LOGICAL_MISMATCH",
                ),
                (
                    "UPDATE document_chunk_embeddings SET vector=X'00000040' "
                    "WHERE id='embedding-p3'",
                    "BACKUP_LOGICAL_MISMATCH",
                ),
                (
                    "UPDATE artifact_translation_checkpoints "
                    "SET translated_markdown='tampered checkpoint' "
                    "WHERE artifact_id='artifact-core' AND sequence=0",
                    "BACKUP_LOGICAL_MISMATCH",
                ),
                (
                    "DROP TRIGGER document_chunks_fts_ai",
                    "BACKUP_FTS_SCHEMA_INVALID",
                ),
                (
                    "DROP TRIGGER document_chunks_fts_ai; "
                    "CREATE TRIGGER document_chunks_fts_ai AFTER INSERT ON document_chunks "
                    "BEGIN SELECT 1; END",
                    "BACKUP_FTS_SCHEMA_INVALID",
                ),
                (
                    "DELETE FROM document_chunks_fts_data",
                    "BACKUP_FTS_INTEGRITY_INVALID",
                ),
            )
            for statement, expected_code in cases:
                with self.subTest(statement=statement):
                    result.backup_path.write_bytes(pristine_backup)
                    result.manifest_path.write_bytes(pristine_manifest)
                    with closing(sqlite3.connect(result.backup_path)) as connection:
                        connection.executescript(statement)
                        connection.commit()
                    _rebind_manifest_file_evidence(
                        result.backup_path, result.manifest_path
                    )
                    with self.assertRaises(DatabaseBackupError) as raised:
                        verify_backup(result.backup_path, result.manifest_path)
                    self.assertEqual(expected_code, raised.exception.code)

    def test_manifest_records_and_verifies_canonical_processing_job_specs(self) -> None:
        """P2 manifests bind canonical job requests and both schema guards."""

        with temporary_legacy_database() as source_path:
            run_alembic(source_path, "20260807_01")
            rows = (
                ("job-source", "paper-1", "source_materialize", "native"),
                ("job-ocr", "paper-1", "ocr", "ocr"),
                ("job-explain", "paper-1", "explain", "native"),
                ("job-translate", "paper-1", "translate", "native"),
                ("job-embed", "paper-1", "embed", "native"),
                ("job-export", "paper-1", "obsidian_export", None),
                ("job-sync", None, "obsidian_sync", None),
            )
            with closing(sqlite3.connect(source_path)) as connection:
                for job_id, paper_id, job_type, source_mode in rows:
                    connection.execute(
                        "INSERT INTO processing_jobs("
                        "id,paper_id,job_type,source_mode,status,progress_json,attempt,"
                        "max_attempts,idempotency_key) VALUES(?,?,?,?,?,?,?,?,?)",
                        (
                            job_id,
                            paper_id,
                            job_type,
                            source_mode,
                            "queued",
                            "{}",
                            0,
                            2,
                            f"key-{job_id}",
                        ),
                    )
                connection.commit()
            run_alembic(source_path, "20260807_02")

            expected_trigger_hashes = {
                "processing_jobs_spec_guard_insert":
                    "499a50aaca8952b838ccea76c2b6db8714f7a9b8c018e2b21bf55eefe7b1b935",
                "processing_jobs_spec_guard_update":
                    "eedfd7ec71a936078358508dee5758c7a8a4af9b702c4fd75228b59ef71f8a38",
            }
            result = create_verified_backup(
                source_path,
                source_path.parent / "backups",
                label="p2-specs",
            )
            database = result.manifest.database

            self.assertEqual(7, database.table_counts["processing_jobs"])
            self.assertEqual(7, database.content_counts["processingJobs"])
            self.assertEqual(7, database.content_counts["processingJobSpecs"])
            self.assertEqual(
                expected_trigger_hashes,
                {
                    "processing_jobs_spec_guard_insert": database.content_sha256[
                        "processingJobsSpecGuardInsert"
                    ],
                    "processing_jobs_spec_guard_update": database.content_sha256[
                        "processingJobsSpecGuardUpdate"
                    ],
                },
            )
            self.assertTrue(verify_backup(result.backup_path, result.manifest_path).valid)
            manifest_text = result.manifest_path.read_text(encoding="utf-8")
            pristine_backup_bytes = result.backup_path.read_bytes()
            pristine_manifest_bytes = result.manifest_path.read_bytes()
            for _job_id, paper_id, job_type, source_mode in rows:
                spec = encode_job_spec_v1(
                    LegacyImportedJobSpecV1(
                        job_type=job_type,
                        paper_id=paper_id,
                        source_mode=source_mode,
                    )
                )
                self.assertNotIn(spec, manifest_text)

            with closing(sqlite3.connect(result.backup_path)) as connection:
                connection.execute(
                    "CREATE TRIGGER processing_jobs_spec_guard_lookalike "
                    "BEFORE DELETE ON processing_jobs BEGIN SELECT 1; END"
                )
                connection.commit()
            _rebind_manifest_file_evidence(result.backup_path, result.manifest_path)
            with self.assertRaises(DatabaseBackupError) as extra_guard:
                verify_backup(result.backup_path, result.manifest_path)
            self.assertEqual(
                "BACKUP_SCHEMA_INVENTORY_INVALID", extra_guard.exception.code
            )
            result.backup_path.write_bytes(pristine_backup_bytes)
            result.manifest_path.write_bytes(pristine_manifest_bytes)

            with closing(sqlite3.connect(result.backup_path)) as connection:
                original_specs = dict(
                    connection.execute(
                        "SELECT id,spec_json FROM processing_jobs ORDER BY id"
                    ).fetchall()
                )
                trigger_sql = [
                    str(row[0])
                    for row in connection.execute(
                        "SELECT sql FROM sqlite_schema WHERE type='trigger' "
                        "AND name LIKE 'processing_jobs_spec_guard_%' ORDER BY name"
                    )
                ]

            def mutate_spec_and_rebind_manifest(job_id: str, replacement: str) -> None:
                with closing(sqlite3.connect(result.backup_path)) as connection:
                    connection.execute("DROP TRIGGER processing_jobs_spec_guard_insert")
                    connection.execute("DROP TRIGGER processing_jobs_spec_guard_update")
                    connection.execute(
                        "UPDATE processing_jobs SET spec_json=? WHERE id=?",
                        (replacement, job_id),
                    )
                    for statement in trigger_sql:
                        connection.execute(statement)
                    connection.commit()
                manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
                manifest["backupSizeBytes"] = result.backup_path.stat().st_size
                manifest["backupSha256"] = hashlib.sha256(
                    result.backup_path.read_bytes()
                ).hexdigest()
                payload = {key: value for key, value in manifest.items() if key != "manifestSha256"}
                manifest["manifestSha256"] = hashlib.sha256(
                    database_backup_module._canonical_json_bytes(payload)
                ).hexdigest()
                result.manifest_path.write_bytes(
                    database_backup_module._canonical_json_bytes(manifest)
                )

            byte_tamper = encode_job_spec_v1(ObsidianSyncJobSpecV1())
            source_payload = json.loads(original_specs["job-source"])
            tamper_cases = (
                ("job-sync", byte_tamper, "BACKUP_LOGICAL_MISMATCH"),
                (
                    "job-source",
                    json.dumps(source_payload, ensure_ascii=False, indent=2, sort_keys=True),
                    "JOB_SPEC_INVALID",
                ),
                (
                    "job-source",
                    json.dumps(
                        {**source_payload, "schemaVersion": 2},
                        ensure_ascii=False,
                        separators=(",", ":"),
                        sort_keys=True,
                    ),
                    "JOB_SPEC_INVALID",
                ),
                (
                    "job-source",
                    json.dumps(
                        {**source_payload, "arguments": {"apiKey": "not-logged"}},
                        ensure_ascii=False,
                        separators=(",", ":"),
                        sort_keys=True,
                    ),
                    "JOB_SPEC_INVALID",
                ),
                (
                    "job-source",
                    json.dumps(
                        {**source_payload, "paperId": "paper-2"},
                        ensure_ascii=False,
                        separators=(",", ":"),
                        sort_keys=True,
                    ),
                    "JOB_SPEC_INVALID",
                ),
            )
            for job_id, replacement, expected_code in tamper_cases:
                with self.subTest(expected_code=expected_code, job_id=job_id):
                    with closing(sqlite3.connect(result.backup_path)) as connection:
                        connection.execute("DROP TRIGGER processing_jobs_spec_guard_insert")
                        connection.execute("DROP TRIGGER processing_jobs_spec_guard_update")
                        connection.execute(
                            "UPDATE processing_jobs SET spec_json=? WHERE id=?",
                            (original_specs[job_id], job_id),
                        )
                        for statement in trigger_sql:
                            connection.execute(statement)
                        connection.commit()
                    mutate_spec_and_rebind_manifest(job_id, replacement)
                    with self.assertRaises(DatabaseBackupError) as raised:
                        verify_backup(result.backup_path, result.manifest_path)
                    self.assertEqual(expected_code, raised.exception.code)
                    self.assertIn(job_id, str(raised.exception)) if expected_code == "JOB_SPEC_INVALID" else None
                    self.assertNotIn(replacement, str(raised.exception))

    def test_manifest_records_p2_content_fingerprints(self) -> None:
        with temporary_legacy_database() as source_path:
            run_alembic(source_path, "20260807_01")
            with closing(sqlite3.connect(source_path)) as connection:
                _insert_nonempty_p1_core_rows(connection)
            run_alembic(source_path, "20260807_02")
            with closing(sqlite3.connect(source_path)) as connection:
                _insert_p2_auxiliary_rows(connection)

            result = create_verified_backup(
                source_path, source_path.parent / "backups", label="p2-content"
            )
            database = result.manifest.database
            expected_content = {
                "documentSources",
                "generatedArtifacts",
                "processingJobs",
                "processingJobSpecs",
                "p1CoreDocumentSources",
                "p1CoreGeneratedArtifacts",
                "p1CoreProcessingJobs",
            }
            self.assertTrue(expected_content.issubset(database.content_counts))
            self.assertTrue(expected_content.issubset(database.content_sha256))
            self.assertEqual(1, database.content_counts["documentSources"])
            self.assertEqual(1, database.content_counts["generatedArtifacts"])
            self.assertEqual(1, database.content_counts["processingJobs"])
            self.assertEqual(1, database.content_counts["processingJobSpecs"])
            self.assertNotIn("documentChunksFtsCoverage", database.content_counts)
            self.assertNotIn("documentChunksFtsIntegrity", database.content_counts)
            for table_name, core_key in (
                ("document_sources", "p1CoreDocumentSources"),
                ("generated_artifacts", "p1CoreGeneratedArtifacts"),
                ("processing_jobs", "p1CoreProcessingJobs"),
            ):
                self.assertEqual(
                    database.table_counts[table_name], database.content_counts[core_key]
                )
            for table_name in (
                "paper_artifact_heads",
                "processing_job_events",
                "ocr_page_checkpoints",
            ):
                self.assertEqual(1, database.table_counts[table_name])
            manifest_text = result.manifest_path.read_text(encoding="utf-8")
            self.assertNotIn("lease-token-must-not-leak", manifest_text)
            self.assertNotIn("core-job-no-secret", manifest_text)

    def test_verify_detects_p2_content_tampering(self) -> None:
        with temporary_legacy_database() as source_path:
            run_alembic(source_path, "20260807_01")
            with closing(sqlite3.connect(source_path)) as connection:
                _insert_nonempty_p1_core_rows(connection)
            run_alembic(source_path, "20260807_02")
            with closing(sqlite3.connect(source_path)) as connection:
                _insert_p2_auxiliary_rows(connection)
            result = create_verified_backup(
                source_path, source_path.parent / "backups", label="p2-tamper"
            )

            statements = (
                "UPDATE document_sources SET processing_version='tampered-core' "
                "WHERE id='source-core'",
                "UPDATE processing_job_events SET event_type='progress' "
                "WHERE job_id='job-core' AND sequence=1",
            )
            original_backup_bytes = result.backup_path.read_bytes()
            original_manifest_bytes = result.manifest_path.read_bytes()
            for statement in statements:
                with self.subTest(statement=statement):
                    result.backup_path.write_bytes(original_backup_bytes)
                    result.manifest_path.write_bytes(original_manifest_bytes)
                    with closing(sqlite3.connect(result.backup_path)) as connection:
                        connection.execute(statement)
                        connection.commit()
                    _rebind_manifest_file_evidence(
                        result.backup_path, result.manifest_path
                    )
                    with self.assertRaises(DatabaseBackupError) as raised:
                        verify_backup(result.backup_path, result.manifest_path)
                    self.assertEqual("BACKUP_LOGICAL_MISMATCH", raised.exception.code)

    def test_create_preserves_replacement_when_manifest_write_fails_before_identity_capture(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory(prefix="study-app-manifest-owner-") as temp_dir:
            root = Path(temp_dir)
            source_path = root / "app.db"
            _create_minimal_database(source_path)

            sentinel = b"replacement manifest must survive"
            manifest_proxy: _ReplacingManifestHandle | None = None
            real_fsync = os.fsync
            real_fdopen = os.fdopen

            with _InvocationPathTripwire(self, root) as tripwire:
                def wrap_manifest(descriptor: int, mode: str, *args: object, **kwargs: object) -> Any:
                    nonlocal manifest_proxy
                    handle = real_fdopen(descriptor, mode, *args, **kwargs)
                    path = tripwire.observed_paths[-1]
                    if mode == "w+b" and path.name.startswith("manifest-"):
                        manifest_proxy = _ReplacingManifestHandle(handle, path, sentinel)
                        return manifest_proxy
                    return handle

                def fail_manifest_fsync(descriptor: int) -> None:
                    if manifest_proxy is not None and descriptor == manifest_proxy.fileno():
                        manifest_proxy.fsync_failed = True
                        raise OSError("injected manifest durability failure")
                    real_fsync(descriptor)

                with (
                    mock.patch.object(
                        database_backup_module.os,
                        "fdopen",
                        autospec=True,
                        side_effect=wrap_manifest,
                    ),
                    mock.patch.object(
                        database_backup_module.os,
                        "fsync",
                        autospec=True,
                        side_effect=fail_manifest_fsync,
                    ),
                ):
                    with self.assertRaises(DatabaseBackupError) as raised:
                        create_verified_backup(
                            source_path,
                            root / "backups",
                            label="manifest-owner",
                        )

            self.assertEqual(raised.exception.code, "BACKUP_MANIFEST_WRITE_FAILED")
            self.assertEqual(str(raised.exception), "Could not write backup manifest.")
            self.assertNotIn("injected manifest durability failure", str(raised.exception))
            self.assertIsNotNone(manifest_proxy)
            assert manifest_proxy is not None
            self.assertTrue(manifest_proxy.replacement_created)
            self.assertTrue(manifest_proxy._path.is_file())
            self.assertEqual(manifest_proxy._path.read_bytes(), sentinel)

    def test_bound_root_windows_contract_runs_without_platform_skip(self) -> None:
        with tempfile.TemporaryDirectory(prefix="study-app-windows-platform-") as temp_dir:
            root = Path(temp_dir)
            source_path = root / "app.db"
            _create_minimal_database(source_path)
            restore_root = root / "restore-checks"
            restore_root.mkdir(mode=0o700)
            platform = DeterministicBoundRootPlatform("windows")

            with _InvocationPathTripwire(self, root):
                result = create_verified_backup(
                    source_path,
                    root / "backups",
                    label="windows-platform",
                )
                report = restore_backup_for_validation(
                    result.backup_path,
                    result.manifest_path,
                    restore_root,
                    bound_root_platform=platform,
                )

            self.assertTrue(report.valid)
            self.assertTrue(platform.root_swap_attempted)
            self.assertFalse(platform.root_swap_succeeded)
            self.assertTrue(platform.destination_swap_attempted)
            self.assertFalse(platform.destination_swap_succeeded)
            self.assertEqual(platform.mkdirat_calls, 1)
            self.assertEqual(platform.openat_nofollow_calls, 1)
            self.assertIsNotNone(platform.hostile_sentinel)
            assert platform.hostile_sentinel is not None
            self.assertEqual(
                platform.hostile_sentinel.read_bytes(),
                b"windows hostile target\n",
            )
            self.assertIsNotNone(report.restored_path)
            assert report.restored_path is not None
            self.assertTrue(report.restored_path.is_file())

    def test_bound_root_posix_contract_runs_without_platform_skip(self) -> None:
        with tempfile.TemporaryDirectory(prefix="study-app-posix-platform-") as temp_dir:
            root = Path(temp_dir)
            source_path = root / "app.db"
            _create_minimal_database(source_path)
            restore_root = root / "restore-checks"
            restore_root.mkdir(mode=0o700)
            platform = DeterministicBoundRootPlatform("posix")

            with _InvocationPathTripwire(self, root):
                result = create_verified_backup(
                    source_path,
                    root / "backups",
                    label="posix-platform",
                )
                with self.assertRaises(DatabaseBackupError) as raised:
                    restore_backup_for_validation(
                        result.backup_path,
                        result.manifest_path,
                        restore_root,
                        bound_root_platform=platform,
                    )

            self.assertEqual(
                raised.exception.code,
                "RESTORE_OUTPUT_DIRECTORY_CHANGED",
            )
            self.assertTrue(platform.root_swap_attempted)
            self.assertTrue(platform.root_swap_succeeded)
            self.assertEqual(platform.mkdirat_calls, 1)
            self.assertEqual(platform.openat_nofollow_calls, 1)
            self.assertEqual(platform.validation_parent, platform.detached_root)
            self.assertIsNotNone(platform.fingerprint_path)
            assert platform.fingerprint_path is not None
            assert platform.detached_root is not None
            self.assertIn(platform.detached_root, platform.fingerprint_path.parents)
            self.assertIsNotNone(platform.hostile_sentinel)
            assert platform.hostile_sentinel is not None
            self.assertEqual(
                platform.hostile_sentinel.read_bytes(),
                b"hostile replacement root\n",
            )
            assert platform.replacement_root is not None
            self.assertEqual(
                list(platform.replacement_root.iterdir()),
                [platform.hostile_sentinel],
            )

    @unittest.skipUnless(os.name == "nt", "Windows bound output-root handle")
    def test_restore_holds_windows_output_root_handle_until_child_is_bound(self) -> None:
        with tempfile.TemporaryDirectory(prefix="study-app-windows-bound-root-") as temp_dir:
            root = Path(temp_dir)
            source_path = root / "app.db"
            connection = sqlite3.connect(source_path)
            try:
                connection.execute("CREATE TABLE papers(id TEXT PRIMARY KEY)")
                connection.execute("INSERT INTO papers(id) VALUES('paper-1')")
                connection.commit()
            finally:
                connection.close()

            restore_root = root / "restore-checks"
            restore_root.mkdir(mode=0o700)
            detached_root = root / "detached-restore-checks"
            junction_target = root / "junction-target"
            junction_target.mkdir(mode=0o700)
            sentinel_path = junction_target / "sentinel.bin"
            sentinel_path.write_bytes(b"junction target sentinel")
            replacement_attempted = False
            replacement_succeeded = False
            destination_replacement_attempted = False
            destination_replacement_succeeded = False

            with _InvocationPathTripwire(self, root):
                result = create_verified_backup(
                    source_path,
                    root / "backups",
                    label="windows-bound-root",
                )
                real_read_identity = database_backup_module._read_directory_identity
                real_assert_no_sidecars = database_backup_module._assert_database_has_no_sidecars

                def read_then_try_replace(path: Path) -> object:
                    nonlocal replacement_attempted, replacement_succeeded
                    identity = real_read_identity(path)
                    if path == restore_root and not replacement_attempted:
                        replacement_attempted = True
                        try:
                            restore_root.rename(detached_root)
                        except OSError:
                            return identity
                        completed = subprocess.run(
                            ["cmd", "/c", "mklink", "/J", str(restore_root), str(junction_target)],
                            capture_output=True,
                            text=True,
                            check=False,
                        )
                        self.assertEqual(completed.returncode, 0, completed.stderr or completed.stdout)
                        replacement_succeeded = True
                    return identity

                def try_replace_destination(database_path: Path) -> None:
                    nonlocal destination_replacement_attempted, destination_replacement_succeeded
                    if (
                        database_path.name == "app.db"
                        and database_path.parent.name.startswith("restore-validation-")
                        and not destination_replacement_attempted
                    ):
                        destination_replacement_attempted = True
                        try:
                            database_path.unlink()
                            database_path.write_bytes(b"replacement destination")
                        except OSError:
                            pass
                        else:
                            destination_replacement_succeeded = True
                    real_assert_no_sidecars(database_path)

                try:
                    with (
                        mock.patch(
                            "backend.app.infrastructure.database_backup._read_directory_identity",
                            side_effect=read_then_try_replace,
                        ),
                        mock.patch(
                            "backend.app.infrastructure.database_backup._assert_database_has_no_sidecars",
                            side_effect=try_replace_destination,
                        ),
                    ):
                        restore_backup_for_validation(
                            result.backup_path,
                            result.manifest_path,
                            restore_root,
                        )
                finally:
                    if replacement_succeeded and restore_root.exists():
                        os.rmdir(restore_root)

            self.assertTrue(replacement_attempted)
            self.assertFalse(replacement_succeeded)
            self.assertTrue(destination_replacement_attempted)
            self.assertFalse(destination_replacement_succeeded)
            self.assertEqual(sentinel_path.read_bytes(), b"junction target sentinel")
            self.assertEqual([sentinel_path], list(junction_target.iterdir()))

    def test_restore_creates_validation_directory_through_bound_posix_dirfd(self) -> None:
        with tempfile.TemporaryDirectory(prefix="study-app-posix-bound-root-") as temp_dir:
            root = Path(temp_dir)
            restore_root = root / "restore-checks"
            restore_root.mkdir(mode=0o700)
            platform = DeterministicBoundRootPlatform("posix")
            bound_root = platform.open_restore_root(restore_root)
            try:
                child = bound_root.create_validation_directory("restore-validation-")
                self.assertEqual(child.path.parent, platform.detached_root)
                self.assertEqual(platform.mkdirat_calls, 1)
                with self.assertRaises(DatabaseBackupError) as raised:
                    bound_root.close()
            finally:
                bound_root._abort()

            self.assertEqual(raised.exception.code, "RESTORE_OUTPUT_DIRECTORY_CHANGED")
            self.assertTrue(platform.root_swap_attempted)
            self.assertTrue(platform.root_swap_succeeded)
            self.assertIsNotNone(platform.hostile_sentinel)
            assert platform.hostile_sentinel is not None
            self.assertEqual(
                platform.hostile_sentinel.read_bytes(),
                b"hostile replacement root\n",
            )
            assert platform.replacement_root is not None
            self.assertEqual(
                list(platform.replacement_root.iterdir()),
                [platform.hostile_sentinel],
            )

    @unittest.skipUnless(os.name == "nt", "Windows atomic bound child creation")
    def test_restore_windows_child_creation_has_no_unbound_path_interval(self) -> None:
        with tempfile.TemporaryDirectory(prefix="study-app-windows-bound-child-") as temp_dir:
            root = Path(temp_dir)
            source_path = root / "app.db"
            _create_minimal_database(source_path)

            restore_root = root / "restore-checks"
            legacy_interval_observed = False
            replacement_attempted = False
            replacement_succeeded = False
            real_mkdir = Path.mkdir
            real_create_bound_directory = (
                database_backup_module._create_windows_bound_directory
            )
            junction_target = root / "junction-target"
            junction_target.mkdir(mode=0o700)
            sentinel_path = junction_target / "sentinel.bin"
            sentinel_path.write_bytes(b"junction target sentinel")

            def mkdir_then_replace(
                path: Path,
                *args: object,
                **kwargs: object,
            ) -> None:
                nonlocal legacy_interval_observed, replacement_succeeded
                real_mkdir(path, *args, **kwargs)
                if path.name.startswith("restore-validation-"):
                    legacy_interval_observed = True
                    path.rmdir()
                    real_mkdir(path, mode=0o700)
                    (path / "competitor-sentinel.bin").write_bytes(b"competitor")
                    replacement_succeeded = True

            def create_then_try_replace(
                parent_handle: int,
                name: str,
            ) -> tuple[int, object]:
                nonlocal replacement_attempted, replacement_succeeded
                raw_handle, identity = real_create_bound_directory(parent_handle, name)
                validation_path = restore_root / name
                replacement_attempted = True
                try:
                    validation_path.rmdir()
                except OSError:
                    return raw_handle, identity
                completed = subprocess.run(
                    ["cmd", "/c", "mklink", "/J", str(validation_path), str(junction_target)],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                self.assertEqual(completed.returncode, 0, completed.stderr or completed.stdout)
                replacement_succeeded = True
                return raw_handle, identity

            with _InvocationPathTripwire(self, root):
                result = create_verified_backup(
                    source_path,
                    root / "backups",
                    label="windows-bound-child",
                )
                with (
                    mock.patch.object(
                        Path,
                        "mkdir",
                        autospec=True,
                        side_effect=mkdir_then_replace,
                    ),
                    mock.patch(
                        "backend.app.infrastructure.database_backup."
                        "_create_windows_bound_directory",
                        side_effect=create_then_try_replace,
                    ),
                ):
                    report = restore_backup_for_validation(
                        result.backup_path,
                        result.manifest_path,
                        restore_root,
                    )

            self.assertFalse(legacy_interval_observed)
            self.assertTrue(replacement_attempted)
            self.assertFalse(replacement_succeeded)
            self.assertEqual(sentinel_path.read_bytes(), b"junction target sentinel")
            self.assertEqual([sentinel_path], list(junction_target.iterdir()))
            self.assertIsNotNone(report.restored_path)
            assert report.restored_path is not None
            self.assertTrue(report.restored_path.is_file())

    def test_restore_posix_rejects_nonprivate_root_before_child_creation(self) -> None:
        with tempfile.TemporaryDirectory(prefix="study-app-posix-private-root-") as temp_dir:
            root = Path(temp_dir)
            source_path = root / "app.db"
            _create_minimal_database(source_path)

            restore_root = root / "restore-checks"
            restore_root.mkdir(mode=0o700)
            platform = DeterministicBoundRootPlatform(
                "posix",
                root_is_private=False,
            )

            with _InvocationPathTripwire(self, root):
                result = create_verified_backup(
                    source_path,
                    root / "backups",
                    label="posix-private-root",
                )
                with self.assertRaises(DatabaseBackupError) as raised:
                    restore_backup_for_validation(
                        result.backup_path,
                        result.manifest_path,
                        restore_root,
                        bound_root_platform=platform,
                    )

            self.assertEqual(
                raised.exception.code,
                "RESTORE_OUTPUT_DIRECTORY_PRIVATE_REQUIRED",
            )
            self.assertEqual(platform.mkdirat_calls, 0)
            self.assertEqual(list(restore_root.iterdir()), [])

    @unittest.skipUnless(os.name == "nt", "Windows bound child cleanup handle")
    def test_restore_abort_deletes_validation_directory_through_bound_handle(self) -> None:
        with tempfile.TemporaryDirectory(prefix="study-app-windows-bound-abort-") as temp_dir:
            root = Path(temp_dir)
            source_path = root / "app.db"
            _create_minimal_database(source_path)

            restore_root = root / "restore-checks"
            replacement_attempted = False
            replacement_succeeded = False
            abort_started = False
            validation_path: Path | None = None

            with _InvocationPathTripwire(self, root):
                result = create_verified_backup(
                    source_path,
                    root / "backups",
                    label="windows-bound-abort",
                )
                real_fingerprint = database_backup_module._fingerprint_bound_restore
                real_mark_directory_for_deletion = (
                    database_backup_module._mark_windows_directory_handle_for_deletion
                )

                def fail_after_destination_copy(
                    destination: object,
                    manifest: object,
                ) -> object:
                    nonlocal abort_started, validation_path
                    database_path = destination.report_path
                    if (
                        database_path.name == "app.db"
                        and database_path.parent.name.startswith("restore-validation-")
                    ):
                        validation_path = database_path.parent
                        abort_started = True
                        raise DatabaseBackupError(
                            "RESTORE_INJECTED_FAILURE",
                            "Injected restore failure after destination copy.",
                        )
                    return real_fingerprint(destination, manifest)

                def mark_directory_after_replacement_attempt(
                    raw_handle: int,
                    *,
                    expected_identity: object,
                    strict: bool,
                ) -> bool:
                    nonlocal replacement_attempted, replacement_succeeded
                    if abort_started and not replacement_attempted:
                        assert validation_path is not None
                        replacement_attempted = True
                        try:
                            validation_path.rmdir()
                            validation_path.mkdir(mode=0o700)
                        except OSError:
                            pass
                        else:
                            replacement_succeeded = True
                    return real_mark_directory_for_deletion(
                        raw_handle,
                        expected_identity=expected_identity,
                        strict=strict,
                    )

                with (
                    mock.patch(
                        "backend.app.infrastructure.database_backup._fingerprint_bound_restore",
                        side_effect=fail_after_destination_copy,
                    ),
                    mock.patch(
                        "backend.app.infrastructure.database_backup."
                        "_mark_windows_directory_handle_for_deletion",
                        side_effect=mark_directory_after_replacement_attempt,
                    ),
                ):
                    with self.assertRaises(DatabaseBackupError) as raised:
                        restore_backup_for_validation(
                            result.backup_path,
                            result.manifest_path,
                            restore_root,
                        )

            self.assertEqual(raised.exception.code, "RESTORE_INJECTED_FAILURE")
            self.assertIsNotNone(validation_path)
            self.assertTrue(replacement_attempted)
            self.assertFalse(replacement_succeeded)
            assert validation_path is not None
            self.assertFalse(validation_path.exists())

    def test_backup_captures_committed_wal_rows_and_restores_them(self) -> None:
        with tempfile.TemporaryDirectory(prefix="study-app-backup-") as temp_dir:
            root = Path(temp_dir)
            source_path = root / "app.db"
            backup_dir = root / "backups"
            restore_root = root / "restored"

            source = sqlite3.connect(source_path)
            try:
                source.execute("PRAGMA foreign_keys = ON")
                self.assertEqual(source.execute("PRAGMA journal_mode = WAL").fetchone()[0], "wal")
                source.execute("PRAGMA wal_autocheckpoint = 0")
                source.executescript(
                    """
                    CREATE TABLE papers (
                        id TEXT PRIMARY KEY,
                        title TEXT NOT NULL,
                        explainer TEXT
                    );
                    CREATE TABLE translations (
                        paper_id TEXT PRIMARY KEY REFERENCES papers(id) ON DELETE CASCADE,
                        content TEXT NOT NULL
                    );
                    CREATE TABLE notes (
                        paper_id TEXT PRIMARY KEY REFERENCES papers(id) ON DELETE CASCADE,
                        content TEXT NOT NULL
                    );
                    CREATE TABLE paper_vectors (
                        paper_id TEXT PRIMARY KEY REFERENCES papers(id) ON DELETE CASCADE,
                        dim INTEGER NOT NULL,
                        vector BLOB NOT NULL
                    );
                    """
                )
                source.executemany(
                    "INSERT INTO papers(id, title, explainer) VALUES(?, ?, ?)",
                    [
                        ("paper-1", "First Paper", "# First explainer\n"),
                        ("paper-2", "Second Paper", None),
                    ],
                )
                source.execute(
                    "INSERT INTO translations(paper_id, content) VALUES(?, ?)",
                    ("paper-1", "# 第一篇翻译\n"),
                )
                source.execute(
                    "INSERT INTO notes(paper_id, content) VALUES(?, ?)",
                    ("paper-2", "A private note"),
                )
                source.execute(
                    "INSERT INTO paper_vectors(paper_id, dim, vector) VALUES(?, ?, ?)",
                    ("paper-1", 3, b"\x01\x02\x03"),
                )
                source.commit()

                wal_path = source_path.with_name(f"{source_path.name}-wal")
                self.assertTrue(wal_path.is_file())
                self.assertGreater(wal_path.stat().st_size, 0)

                result = create_verified_backup(source_path, backup_dir, label="pre-p0")
            finally:
                source.close()

            self.assertTrue(result.backup_path.is_file())
            self.assertTrue(result.manifest_path.is_file())
            self.assertFalse(
                result.backup_path.with_name(f"{result.backup_path.name}-wal").exists()
            )
            self.assertFalse(
                result.backup_path.with_name(f"{result.backup_path.name}-shm").exists()
            )
            self.assertFalse(
                any(
                    path.name.endswith((".tmp", ".tmp-wal", ".tmp-shm"))
                    for path in backup_dir.iterdir()
                )
            )
            self.assertEqual(result.manifest.source_journal_mode, "wal")
            self.assertEqual(result.manifest.database.table_counts["papers"], 2)
            self.assertEqual(result.manifest.database.table_counts["translations"], 1)
            self.assertEqual(result.manifest.database.table_counts["notes"], 1)
            self.assertEqual(result.manifest.database.table_counts["paper_vectors"], 1)
            self.assertEqual(result.manifest.database.quick_check, "ok")
            self.assertEqual(result.manifest.database.foreign_key_violations, 0)

            manifest_json = json.loads(result.manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(manifest_json["formatVersion"], 2)
            self.assertEqual(manifest_json["label"], "pre-p0")

            restored = restore_backup_for_validation(
                result.backup_path,
                result.manifest_path,
                restore_root,
            )
            self.assertTrue(restored.valid)
            self.assertEqual(restored.logical_sha256, result.manifest.database.logical_sha256)
            self.assertIsNotNone(restored.restored_path)
            restored_path = restored.restored_path
            assert restored_path is not None
            self.assertFalse(
                restored_path.with_name(f"{restored_path.name}-wal").exists()
            )
            self.assertFalse(
                restored_path.with_name(f"{restored_path.name}-shm").exists()
            )

            restored_db = sqlite3.connect(restored_path.as_uri() + "?mode=ro", uri=True)
            try:
                self.assertEqual(restored_db.execute("PRAGMA quick_check").fetchone()[0], "ok")
                self.assertEqual(
                    restored_db.execute(
                        "SELECT id, title, explainer FROM papers ORDER BY id"
                    ).fetchall(),
                    [
                        ("paper-1", "First Paper", "# First explainer\n"),
                        ("paper-2", "Second Paper", None),
                    ],
                )
                self.assertEqual(
                    restored_db.execute(
                        "SELECT paper_id, content FROM translations ORDER BY paper_id"
                    ).fetchall(),
                    [("paper-1", "# 第一篇翻译\n")],
                )
                self.assertEqual(
                    restored_db.execute(
                        "SELECT paper_id, content FROM notes ORDER BY paper_id"
                    ).fetchall(),
                    [("paper-2", "A private note")],
                )
                self.assertEqual(
                    restored_db.execute(
                        "SELECT paper_id, dim, vector FROM paper_vectors ORDER BY paper_id"
                    ).fetchall(),
                    [("paper-1", 3, b"\x01\x02\x03")],
                )
            finally:
                restored_db.close()

    def test_manifest_records_migration_and_critical_content_fingerprints(self) -> None:
        with tempfile.TemporaryDirectory(prefix="study-app-manifest-") as temp_dir:
            root = Path(temp_dir)
            source_path = root / "app.db"
            connection = sqlite3.connect(source_path)
            try:
                connection.executescript(
                    """
                    PRAGMA foreign_keys = ON;
                    CREATE TABLE papers (
                        id TEXT PRIMARY KEY,
                        title TEXT NOT NULL,
                        explainer TEXT
                    );
                    CREATE TABLE translations (
                        paper_id TEXT PRIMARY KEY REFERENCES papers(id),
                        content TEXT NOT NULL
                    );
                    CREATE TABLE notes (
                        paper_id TEXT PRIMARY KEY REFERENCES papers(id),
                        content TEXT NOT NULL
                    );
                    CREATE TABLE paper_vectors (
                        paper_id TEXT PRIMARY KEY REFERENCES papers(id),
                        dim INTEGER NOT NULL,
                        vector BLOB NOT NULL
                    );
                    CREATE TABLE schema_migrations (
                        version INTEGER PRIMARY KEY
                    );
                    CREATE TABLE alembic_version (
                        version_num TEXT PRIMARY KEY
                    );
                    INSERT INTO papers(id, title, explainer)
                    VALUES
                        ('paper-1', 'First Paper', '# Explanation'),
                        ('paper-2', 'Second Paper', NULL);
                    INSERT INTO translations(paper_id, content)
                    VALUES ('paper-1', '# Translation');
                    INSERT INTO notes(paper_id, content)
                    VALUES ('paper-2', 'Remember this');
                    INSERT INTO paper_vectors(paper_id, dim, vector)
                    VALUES ('paper-1', 2, X'0102');
                    INSERT INTO schema_migrations(version) VALUES (1);
                    INSERT INTO alembic_version(version_num) VALUES ('0001-test');
                    """
                )
                connection.commit()
            finally:
                connection.close()

            result = create_verified_backup(source_path, root / "backups", label="manifest")
            database = result.manifest.database

            self.assertEqual(database.integrity_check, "ok")
            self.assertEqual(database.legacy_schema_migrations, (1,))
            self.assertEqual(database.alembic_version, "0001-test")
            self.assertEqual(
                database.content_counts,
                {
                    "paperIds": 2,
                    "explainers": 1,
                    "translations": 1,
                    "notes": 1,
                    "paperVectors": 1,
                },
            )
            self.assertEqual(set(database.content_sha256), set(database.content_counts))
            for value in database.content_sha256.values():
                self.assertRegex(value, r"^[0-9a-f]{64}$")

    def test_backup_rejects_multiple_alembic_heads_without_publishing_files(self) -> None:
        with tempfile.TemporaryDirectory(prefix="study-app-alembic-heads-") as temp_dir:
            root = Path(temp_dir)
            source_path = root / "app.db"
            backup_dir = root / "backups"
            connection = sqlite3.connect(source_path)
            try:
                connection.executescript(
                    """
                    CREATE TABLE papers(id TEXT PRIMARY KEY);
                    CREATE TABLE alembic_version(version_num TEXT PRIMARY KEY);
                    INSERT INTO papers(id) VALUES ('paper-1');
                    INSERT INTO alembic_version(version_num)
                    VALUES ('head-a'), ('head-b');
                    """
                )
                connection.commit()
            finally:
                connection.close()

            with self.assertRaises(DatabaseBackupError) as raised:
                create_verified_backup(source_path, backup_dir, label="ambiguous")

            self.assertEqual(raised.exception.code, "BACKUP_ALEMBIC_STATE_AMBIGUOUS")
            self.assertEqual(list(backup_dir.iterdir()), [])

    def test_cleanup_failure_never_masks_the_original_classified_error(self) -> None:
        with tempfile.TemporaryDirectory(prefix="study-app-cleanup-error-") as temp_dir:
            root = Path(temp_dir)
            source_path = root / "app.db"
            backup_dir = root / "backups"
            connection = sqlite3.connect(source_path)
            try:
                connection.executescript(
                    """
                    CREATE TABLE papers(id TEXT PRIMARY KEY);
                    CREATE TABLE alembic_version(version_num TEXT PRIMARY KEY);
                    INSERT INTO papers(id) VALUES ('paper-1');
                    INSERT INTO alembic_version(version_num)
                    VALUES ('head-a'), ('head-b');
                    """
                )
                connection.commit()
            finally:
                connection.close()

            cleanup_attempted = False

            def fail_cleanup(*args: object, **kwargs: object) -> object:
                nonlocal cleanup_attempted
                del args, kwargs
                cleanup_attempted = True
                raise RuntimeError("generated cleanup helper failed")

            with mock.patch(
                "backend.app.infrastructure.database_backup._unlink_owned_file",
                side_effect=fail_cleanup,
            ):
                with self.assertRaises(DatabaseBackupError) as raised:
                    create_verified_backup(source_path, backup_dir, label="cleanup")

            self.assertTrue(cleanup_attempted)
            self.assertEqual(
                raised.exception.code,
                "BACKUP_ALEMBIC_STATE_AMBIGUOUS",
            )

    def test_cli_creates_verifies_and_restores_a_backup(self) -> None:
        with tempfile.TemporaryDirectory(prefix="study-app-backup-cli-") as temp_dir:
            root = Path(temp_dir)
            source_path = root / "app.db"
            backup_dir = root / "backups"
            restore_root = root / "restore"
            connection = sqlite3.connect(source_path)
            try:
                connection.execute("CREATE TABLE papers(id TEXT PRIMARY KEY, title TEXT NOT NULL)")
                connection.execute(
                    "INSERT INTO papers(id, title) VALUES(?, ?)",
                    ("paper-1", "CLI Paper"),
                )
                connection.commit()
            finally:
                connection.close()

            created = self._run_cli(
                "create",
                "--database",
                str(source_path),
                "--output-directory",
                str(backup_dir),
                "--label",
                "cli-test",
            )
            created_payload = json.loads(created.stdout)
            self.assertTrue(created_payload["ok"])
            self.assertEqual(created_payload["operation"], "create")
            backup_path = Path(created_payload["backupPath"])
            manifest_path = Path(created_payload["manifestPath"])

            verified = self._run_cli(
                "verify",
                "--backup",
                str(backup_path),
                "--manifest",
                str(manifest_path),
            )
            verified_payload = json.loads(verified.stdout)
            self.assertEqual(
                verified_payload,
                {
                    "ok": True,
                    "operation": "verify",
                    "formatVersion": 2,
                    "backupId": created_payload["backupId"],
                    "backupSha256": created_payload["backupSha256"],
                    "manifestSha256": created_payload["manifestSha256"],
                    "manifestFileSha256": created_payload["manifestFileSha256"],
                    "logicalSha256": created_payload["logicalSha256"],
                    "tableCounts": {"papers": 1},
                    "tableSha256": created_payload["tableSha256"],
                    "contentCounts": created_payload["contentCounts"],
                    "contentSha256": created_payload["contentSha256"],
                },
            )

            restored = self._run_cli(
                "restore-check",
                "--backup",
                str(backup_path),
                "--manifest",
                str(manifest_path),
                "--output-directory",
                str(restore_root),
            )
            restored_payload = json.loads(restored.stdout)
            self.assertTrue(restored_payload["ok"])
            self.assertEqual(restored_payload["operation"], "restoreCheck")
            for audit_field in (
                "formatVersion",
                "backupId",
                "backupSha256",
                "manifestSha256",
                "manifestFileSha256",
                "logicalSha256",
                "tableCounts",
                "tableSha256",
                "contentCounts",
                "contentSha256",
            ):
                self.assertEqual(restored_payload[audit_field], created_payload[audit_field])
            restored_path = Path(restored_payload["restoredPath"])
            self.assertEqual(restored_path.name, "app.db")
            self.assertEqual(restored_path.parent.parent, restore_root.resolve())

            restored_db = sqlite3.connect(restored_path.as_uri() + "?mode=ro", uri=True)
            try:
                self.assertEqual(
                    restored_db.execute("SELECT id, title FROM papers").fetchall(),
                    [("paper-1", "CLI Paper")],
                )
            finally:
                restored_db.close()

    def test_restore_validation_never_targets_live_database_or_sidecars(self) -> None:
        with tempfile.TemporaryDirectory(prefix="study-app-restore-guard-") as temp_dir:
            root = Path(temp_dir)
            source_path = root / "app.db"
            connection = sqlite3.connect(source_path)
            try:
                connection.execute("CREATE TABLE papers(id TEXT PRIMARY KEY)")
                connection.execute("INSERT INTO papers(id) VALUES('paper-1')")
                connection.commit()
            finally:
                connection.close()

            result = create_verified_backup(source_path, root / "backups", label="guard")
            original_source = source_path.read_bytes()
            forbidden_paths = {
                source_path,
                source_path.with_name(f"{source_path.name}-wal"),
                source_path.with_name(f"{source_path.name}-shm"),
            }

            restored = restore_backup_for_validation(
                result.backup_path,
                result.manifest_path,
                source_path.parent,
            )

            self.assertIsNotNone(restored.restored_path)
            restored_path = restored.restored_path
            assert restored_path is not None
            self.assertNotIn(restored_path, forbidden_paths)
            self.assertEqual(restored_path.name, "app.db")
            self.assertEqual(restored_path.parent.parent, source_path.parent)
            self.assertEqual(source_path.read_bytes(), original_source)
            self.assertFalse(source_path.with_name(f"{source_path.name}-wal").exists())
            self.assertFalse(source_path.with_name(f"{source_path.name}-shm").exists())

    def test_restore_rejects_sidecar_like_output_directory_before_creating_it(self) -> None:
        with tempfile.TemporaryDirectory(prefix="study-app-restore-output-") as temp_dir:
            root = Path(temp_dir)
            source_path = root / "app.db"
            connection = sqlite3.connect(source_path)
            try:
                connection.execute("CREATE TABLE papers(id TEXT PRIMARY KEY)")
                connection.execute("INSERT INTO papers(id) VALUES('paper-1')")
                connection.commit()
            finally:
                connection.close()

            result = create_verified_backup(source_path, root / "backups", label="guard")
            sidecar_like_output = root / "another-live.db-wal"

            with self.assertRaises(DatabaseBackupError) as raised:
                restore_backup_for_validation(
                    result.backup_path,
                    result.manifest_path,
                    sidecar_like_output,
                )

            self.assertEqual(raised.exception.code, "RESTORE_OUTPUT_DIRECTORY_INVALID")
            self.assertFalse(sidecar_like_output.exists())

    def test_restore_rejects_every_database_shaped_output_directory(self) -> None:
        with tempfile.TemporaryDirectory(prefix="study-app-restore-shapes-") as temp_dir:
            root = Path(temp_dir)
            source_path = root / "app.db"
            connection = sqlite3.connect(source_path)
            try:
                connection.execute("CREATE TABLE papers(id TEXT PRIMARY KEY)")
                connection.execute("INSERT INTO papers(id) VALUES('paper-1')")
                connection.commit()
            finally:
                connection.close()

            result = create_verified_backup(source_path, root / "backups", label="shapes")
            forbidden_names = (
                "restore.db",
                "restore.db3",
                "restore.sqlite",
                "restore.sqlite3",
                "restore-wal",
                "restore-shm",
                "restore-journal",
            )
            for forbidden_name in forbidden_names:
                with self.subTest(forbidden_name=forbidden_name):
                    forbidden_path = root / forbidden_name
                    with self.assertRaises(DatabaseBackupError) as raised:
                        restore_backup_for_validation(
                            result.backup_path,
                            result.manifest_path,
                            forbidden_path,
                        )
                    self.assertEqual(
                        raised.exception.code,
                        "RESTORE_OUTPUT_DIRECTORY_INVALID",
                    )
                    self.assertFalse(forbidden_path.exists())

            existing_file = root / "restore-target.txt"
            existing_bytes = b"user-owned output target"
            existing_file.write_bytes(existing_bytes)
            with self.assertRaises(DatabaseBackupError) as raised:
                restore_backup_for_validation(
                    result.backup_path,
                    result.manifest_path,
                    existing_file,
                )
            self.assertEqual(raised.exception.code, "RESTORE_OUTPUT_DIRECTORY_INVALID")
            self.assertEqual(existing_file.read_bytes(), existing_bytes)

    def test_restore_collision_never_cleans_files_it_does_not_own(self) -> None:
        with tempfile.TemporaryDirectory(prefix="study-app-restore-collision-") as temp_dir:
            root = Path(temp_dir)
            source_path = root / "app.db"
            connection = sqlite3.connect(source_path)
            try:
                connection.execute("CREATE TABLE papers(id TEXT PRIMARY KEY)")
                connection.execute("INSERT INTO papers(id) VALUES('paper-1')")
                connection.commit()
            finally:
                connection.close()

            result = create_verified_backup(source_path, root / "backups", label="collision")
            validation_id = "a" * 32
            collision_directory = (
                root / "restore-checks" / f"restore-validation-{validation_id}"
            )
            collision_directory.mkdir(parents=True)
            existing_files = {
                collision_directory / "app.db": b"owned database",
                collision_directory / "app.db-wal": b"owned wal",
                collision_directory / "app.db-shm": b"owned shm",
                collision_directory / f".app.db.{validation_id}.tmp": b"owned temp",
            }
            for path, content in existing_files.items():
                path.write_bytes(content)

            with mock.patch(
                "backend.app.infrastructure.database_backup.uuid.uuid4",
                return_value=mock.Mock(hex=validation_id),
            ):
                with self.assertRaises(DatabaseBackupError) as raised:
                    restore_backup_for_validation(
                        result.backup_path,
                        result.manifest_path,
                        root / "restore-checks",
                    )

            self.assertEqual(raised.exception.code, "RESTORE_VALIDATION_FAILED")
            self.assertEqual(
                {path: path.read_bytes() for path in existing_files},
                existing_files,
            )

    def test_restore_failure_preserves_destination_replaced_after_publication(self) -> None:
        with tempfile.TemporaryDirectory(prefix="study-app-restore-replaced-") as temp_dir:
            root = Path(temp_dir)
            source_path = root / "app.db"
            connection = sqlite3.connect(source_path)
            try:
                connection.execute("CREATE TABLE papers(id TEXT PRIMARY KEY)")
                connection.execute("INSERT INTO papers(id) VALUES('paper-1')")
                connection.commit()
            finally:
                connection.close()

            result = create_verified_backup(source_path, root / "backups", label="restore")
            competing_bytes = b"replacement restore owned by another process"
            replaced_path: Path | None = None
            replacement_blocked = False
            replacement_succeeded = False
            real_assert_no_sidecars = (
                database_backup_module._assert_database_has_no_sidecars
            )

            def replace_before_final_check(database_path: Path) -> None:
                nonlocal replaced_path, replacement_blocked, replacement_succeeded
                if (
                    database_path.name == "app.db"
                    and database_path.parent.name.startswith("restore-validation-")
                ):
                    replaced_path = database_path
                    try:
                        database_path.unlink()
                        database_path.write_bytes(competing_bytes)
                    except OSError:
                        replacement_blocked = True
                    else:
                        replacement_succeeded = True
                    raise DatabaseBackupError(
                        "INJECTED_RESTORE_FAILURE",
                        "Injected restore verification failure.",
                    )
                real_assert_no_sidecars(database_path)

            with mock.patch(
                "backend.app.infrastructure.database_backup._assert_database_has_no_sidecars",
                side_effect=replace_before_final_check,
            ):
                with self.assertRaises(DatabaseBackupError) as raised:
                    restore_backup_for_validation(
                        result.backup_path,
                        result.manifest_path,
                        root / "restore-checks",
                    )

            self.assertEqual(raised.exception.code, "INJECTED_RESTORE_FAILURE")
            self.assertIsNotNone(replaced_path)
            assert replaced_path is not None
            if replacement_succeeded:
                self.assertEqual(replaced_path.read_bytes(), competing_bytes)
            else:
                self.assertTrue(replacement_blocked)
                self.assertFalse(replaced_path.exists())

    def test_verify_rejects_manifest_metadata_tampering(self) -> None:
        with tempfile.TemporaryDirectory(prefix="study-app-manifest-tamper-") as temp_dir:
            root = Path(temp_dir)
            source_path = root / "app.db"
            connection = sqlite3.connect(source_path)
            try:
                connection.execute("CREATE TABLE papers(id TEXT PRIMARY KEY)")
                connection.execute("INSERT INTO papers(id) VALUES('paper-1')")
                connection.commit()
            finally:
                connection.close()

            result = create_verified_backup(source_path, root / "backups", label="tamper")
            manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
            manifest["sourceDatabase"] = str(root / "different.db")
            result.manifest_path.write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
                newline="\n",
            )

            with self.assertRaises(DatabaseBackupError) as raised:
                verify_backup(result.backup_path, result.manifest_path)
            self.assertEqual(raised.exception.code, "BACKUP_MANIFEST_MISMATCH")

    def test_verify_rejects_rehashed_logical_fingerprint_tampering(self) -> None:
        with tempfile.TemporaryDirectory(prefix="study-app-logical-tamper-") as temp_dir:
            root = Path(temp_dir)
            source_path = root / "app.db"
            connection = sqlite3.connect(source_path)
            try:
                connection.execute("CREATE TABLE papers(id TEXT PRIMARY KEY)")
                connection.execute("INSERT INTO papers(id) VALUES('paper-1')")
                connection.commit()
            finally:
                connection.close()

            result = create_verified_backup(source_path, root / "backups", label="logical")
            manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
            manifest["database"]["tableCounts"]["papers"] = 999
            manifest_payload = {
                key: value
                for key, value in manifest.items()
                if key != "manifestSha256"
            }
            canonical_payload = json.dumps(
                manifest_payload,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
            manifest["manifestSha256"] = hashlib.sha256(canonical_payload).hexdigest()
            result.manifest_path.write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
                newline="\n",
            )

            with self.assertRaises(DatabaseBackupError) as raised:
                verify_backup(result.backup_path, result.manifest_path)

            self.assertEqual(raised.exception.code, "BACKUP_LOGICAL_MISMATCH")

    def test_verify_rejects_in_place_mutation_after_fingerprint(self) -> None:
        with tempfile.TemporaryDirectory(prefix="study-app-backup-in-place-") as temp_dir:
            root = Path(temp_dir)
            source_path = root / "app.db"
            connection = sqlite3.connect(source_path)
            try:
                connection.execute("CREATE TABLE papers(id TEXT PRIMARY KEY)")
                connection.execute("INSERT INTO papers(id) VALUES('paper-1')")
                connection.commit()
            finally:
                connection.close()

            result = create_verified_backup(source_path, root / "backups", label="in-place")
            real_fingerprint = database_backup_module._fingerprint_database

            def fingerprint_then_mutate(database_path: Path, **kwargs: object) -> object:
                fingerprint = real_fingerprint(database_path, **kwargs)
                with database_path.open("ab") as handle:
                    handle.write(b"\x00")
                return fingerprint

            with mock.patch(
                "backend.app.infrastructure.database_backup._fingerprint_database",
                side_effect=fingerprint_then_mutate,
            ):
                with self.assertRaises(DatabaseBackupError) as raised:
                    verify_backup(result.backup_path, result.manifest_path)

            self.assertEqual(raised.exception.code, "BACKUP_FILE_CHANGED_DURING_VERIFY")

    def test_create_rejects_in_place_mutation_after_public_verification(self) -> None:
        with tempfile.TemporaryDirectory(prefix="study-app-create-post-verify-") as temp_dir:
            root = Path(temp_dir)
            source_path = root / "app.db"
            backup_dir = root / "backups"
            connection = sqlite3.connect(source_path)
            try:
                connection.execute("CREATE TABLE papers(id TEXT PRIMARY KEY, title TEXT NOT NULL)")
                connection.execute(
                    "INSERT INTO papers(id, title) VALUES(?, ?)",
                    ("paper-1", "Original"),
                )
                connection.commit()
            finally:
                connection.close()

            real_verify = database_backup_module.verify_backup
            mutation_observed = False

            def verify_then_mutate(
                backup_database: object,
                manifest_file: object,
            ) -> object:
                nonlocal mutation_observed
                report = real_verify(backup_database, manifest_file)
                published_path = Path(backup_database)
                before = published_path.stat()
                payload = bytearray(published_path.read_bytes())
                payload[-1] ^= 0x01
                published_path.write_bytes(payload)
                after = published_path.stat()
                self.assertEqual((after.st_dev, after.st_ino), (before.st_dev, before.st_ino))
                mutation_observed = True
                return report

            with mock.patch(
                "backend.app.infrastructure.database_backup.verify_backup",
                side_effect=verify_then_mutate,
            ):
                with self.assertRaises(DatabaseBackupError) as raised:
                    create_verified_backup(source_path, backup_dir, label="post-verify")

            self.assertTrue(mutation_observed)
            self.assertEqual(raised.exception.code, "BACKUP_FILE_CHANGED_AFTER_VERIFY")
            self.assertEqual(list(backup_dir.iterdir()), [])

    def test_create_rejects_in_place_manifest_mutation_after_verification(self) -> None:
        with tempfile.TemporaryDirectory(prefix="study-app-create-manifest-race-") as temp_dir:
            root = Path(temp_dir)
            source_path = root / "app.db"
            backup_dir = root / "backups"
            connection = sqlite3.connect(source_path)
            try:
                connection.execute("CREATE TABLE papers(id TEXT PRIMARY KEY)")
                connection.execute("INSERT INTO papers(id) VALUES('paper-1')")
                connection.commit()
            finally:
                connection.close()

            real_verify = database_backup_module.verify_backup
            mutation_observed = False

            def verify_then_mutate_manifest(
                backup_database: object,
                manifest_file: object,
            ) -> object:
                nonlocal mutation_observed
                report = real_verify(backup_database, manifest_file)
                published_manifest = Path(manifest_file)
                before = published_manifest.stat()
                payload = published_manifest.read_text(encoding="utf-8") + " "
                published_manifest.write_text(payload, encoding="utf-8", newline="\n")
                after = published_manifest.stat()
                self.assertEqual((after.st_dev, after.st_ino), (before.st_dev, before.st_ino))
                mutation_observed = True
                return report

            with mock.patch(
                "backend.app.infrastructure.database_backup.verify_backup",
                side_effect=verify_then_mutate_manifest,
            ):
                with self.assertRaises(DatabaseBackupError) as raised:
                    create_verified_backup(source_path, backup_dir, label="manifest-race")

            self.assertTrue(mutation_observed)
            self.assertEqual(raised.exception.code, "BACKUP_MANIFEST_CHANGED_AFTER_VERIFY")
            self.assertEqual(list(backup_dir.iterdir()), [])

    def test_restore_rejects_manifest_source_path_with_nul(self) -> None:
        with tempfile.TemporaryDirectory(prefix="study-app-manifest-nul-") as temp_dir:
            root = Path(temp_dir)
            source_path = root / "app.db"
            connection = sqlite3.connect(source_path)
            try:
                connection.execute("CREATE TABLE papers(id TEXT PRIMARY KEY)")
                connection.execute("INSERT INTO papers(id) VALUES('paper-1')")
                connection.commit()
            finally:
                connection.close()

            result = create_verified_backup(source_path, root / "backups", label="nul")
            manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
            manifest["sourceDatabase"] = "C:/study-app/invalid\u0000source/app.db"
            manifest_payload = {
                key: value
                for key, value in manifest.items()
                if key != "manifestSha256"
            }
            manifest["manifestSha256"] = hashlib.sha256(
                json.dumps(
                    manifest_payload,
                    ensure_ascii=False,
                    separators=(",", ":"),
                    sort_keys=True,
                ).encode("utf-8")
            ).hexdigest()
            result.manifest_path.write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
                newline="\n",
            )

            with self.assertRaises(DatabaseBackupError) as raised:
                restore_backup_for_validation(
                    result.backup_path,
                    result.manifest_path,
                    root / "restore-checks",
                )

            self.assertEqual(raised.exception.code, "BACKUP_MANIFEST_INVALID")
            self.assertFalse((root / "restore-checks").exists())

    def test_restore_rejects_in_place_mutation_after_destination_fingerprint(self) -> None:
        with tempfile.TemporaryDirectory(prefix="study-app-restore-post-fingerprint-") as temp_dir:
            root = Path(temp_dir)
            source_path = root / "app.db"
            restore_root = root / "restore-checks"
            connection = sqlite3.connect(source_path)
            try:
                connection.execute("CREATE TABLE papers(id TEXT PRIMARY KEY, title TEXT NOT NULL)")
                connection.execute(
                    "INSERT INTO papers(id, title) VALUES(?, ?)",
                    ("paper-1", "Original"),
                )
                connection.commit()
            finally:
                connection.close()

            result = create_verified_backup(source_path, root / "backups", label="restore-race")
            real_fingerprint = database_backup_module._fingerprint_bound_restore
            mutation_observed = False

            def fingerprint_then_mutate(destination: object, manifest: object) -> object:
                nonlocal mutation_observed
                fingerprint = real_fingerprint(destination, manifest)
                database_path = destination.report_path
                if database_path.parent.name.startswith("restore-validation-"):
                    before = database_path.stat()
                    _append_bytes_with_delete_sharing(database_path, b"\x00")
                    after = database_path.stat()
                    self.assertEqual((after.st_dev, after.st_ino), (before.st_dev, before.st_ino))
                    mutation_observed = True
                return fingerprint

            with mock.patch(
                "backend.app.infrastructure.database_backup._fingerprint_bound_restore",
                side_effect=fingerprint_then_mutate,
            ):
                with self.assertRaises(DatabaseBackupError) as raised:
                    restore_backup_for_validation(
                        result.backup_path,
                        result.manifest_path,
                        restore_root,
                    )

            self.assertTrue(mutation_observed)
            self.assertEqual(raised.exception.code, "RESTORE_FILE_CHANGED_DURING_VALIDATION")
            self.assertEqual(list(restore_root.iterdir()), [])

    def test_verify_rejects_backup_sidecars_without_deleting_them(self) -> None:
        with tempfile.TemporaryDirectory(prefix="study-app-backup-sidecar-") as temp_dir:
            root = Path(temp_dir)
            source_path = root / "app.db"
            connection = sqlite3.connect(source_path)
            try:
                connection.execute("CREATE TABLE papers(id TEXT PRIMARY KEY)")
                connection.execute("INSERT INTO papers(id) VALUES('paper-1')")
                connection.commit()
            finally:
                connection.close()

            result = create_verified_backup(source_path, root / "backups", label="sidecar")
            backup_wal = result.backup_path.with_name(f"{result.backup_path.name}-wal")
            backup_wal.write_bytes(b"unowned sidecar marker")

            with self.assertRaises(DatabaseBackupError) as raised:
                verify_backup(result.backup_path, result.manifest_path)

            self.assertEqual(raised.exception.code, "BACKUP_SIDECAR_PRESENT")
            self.assertEqual(backup_wal.read_bytes(), b"unowned sidecar marker")

    def test_verify_rejects_rollback_journal_without_deleting_it(self) -> None:
        with tempfile.TemporaryDirectory(prefix="study-app-backup-journal-") as temp_dir:
            root = Path(temp_dir)
            source_path = root / "app.db"
            connection = sqlite3.connect(source_path)
            try:
                connection.execute("CREATE TABLE papers(id TEXT PRIMARY KEY)")
                connection.execute("INSERT INTO papers(id) VALUES('paper-1')")
                connection.commit()
            finally:
                connection.close()

            result = create_verified_backup(source_path, root / "backups", label="journal")
            backup_journal = result.backup_path.with_name(
                f"{result.backup_path.name}-journal"
            )
            marker = b"unowned rollback journal"
            backup_journal.write_bytes(marker)

            with self.assertRaises(DatabaseBackupError) as raised:
                verify_backup(result.backup_path, result.manifest_path)

            self.assertEqual(raised.exception.code, "BACKUP_SIDECAR_PRESENT")
            self.assertEqual(backup_journal.read_bytes(), marker)

    def test_verify_classifies_sidecar_inspection_failure(self) -> None:
        with tempfile.TemporaryDirectory(prefix="study-app-sidecar-inspect-") as temp_dir:
            root = Path(temp_dir)
            source_path = root / "app.db"
            connection = sqlite3.connect(source_path)
            try:
                connection.execute("CREATE TABLE papers(id TEXT PRIMARY KEY)")
                connection.execute("INSERT INTO papers(id) VALUES('paper-1')")
                connection.commit()
            finally:
                connection.close()

            result = create_verified_backup(source_path, root / "backups", label="inspect")
            real_exists = Path.exists

            def guarded_exists(path: Path) -> bool:
                if path.name == f"{result.backup_path.name}-wal":
                    raise PermissionError("sidecar inspection denied")
                return real_exists(path)

            with mock.patch.object(Path, "exists", guarded_exists):
                with self.assertRaises(DatabaseBackupError) as raised:
                    verify_backup(result.backup_path, result.manifest_path)

            self.assertEqual(
                raised.exception.code,
                "BACKUP_SIDECAR_INSPECTION_FAILED",
            )

    def test_backup_target_collision_preserves_existing_file_and_sidecars(self) -> None:
        with tempfile.TemporaryDirectory(prefix="study-app-backup-collision-") as temp_dir:
            root = Path(temp_dir)
            source_path = root / "app.db"
            connection = sqlite3.connect(source_path)
            try:
                connection.execute("CREATE TABLE papers(id TEXT PRIMARY KEY)")
                connection.execute("INSERT INTO papers(id) VALUES('paper-1')")
                connection.commit()
            finally:
                connection.close()

            backup_dir = root / "backups"
            backup_dir.mkdir()
            backup_id = "b" * 32
            fixed_now = datetime(2026, 8, 7, tzinfo=timezone.utc)
            backup_path = (
                backup_dir
                / "app-collision-20260807T000000Z-bbbbbbbbbbbb.sqlite3"
            )
            existing_files = {
                backup_path: b"owned backup",
                backup_path.with_name(f"{backup_path.name}-wal"): b"owned wal",
                backup_path.with_name(f"{backup_path.name}-shm"): b"owned shm",
            }
            for path, content in existing_files.items():
                path.write_bytes(content)

            with (
                mock.patch(
                    "backend.app.infrastructure.database_backup.uuid.uuid4",
                    return_value=mock.Mock(hex=backup_id),
                ),
                mock.patch(
                    "backend.app.infrastructure.database_backup.datetime"
                ) as datetime_mock,
            ):
                datetime_mock.now.return_value = fixed_now
                with self.assertRaises(DatabaseBackupError) as raised:
                    create_verified_backup(source_path, backup_dir, label="collision")

            self.assertEqual(raised.exception.code, "BACKUP_TARGET_EXISTS")
            self.assertEqual(
                {path: path.read_bytes() for path in existing_files},
                existing_files,
            )

    def test_backup_publish_never_overwrites_a_racing_target(self) -> None:
        with tempfile.TemporaryDirectory(prefix="study-app-backup-race-") as temp_dir:
            root = Path(temp_dir)
            source_path = root / "app.db"
            connection = sqlite3.connect(source_path)
            try:
                connection.execute("CREATE TABLE papers(id TEXT PRIMARY KEY)")
                connection.execute("INSERT INTO papers(id) VALUES('paper-1')")
                connection.commit()
            finally:
                connection.close()

            backup_dir = root / "backups"
            backup_id = "c" * 32
            fixed_now = datetime(2026, 8, 7, tzinfo=timezone.utc)
            backup_path = (
                backup_dir
                / "app-race-20260807T000000Z-cccccccccccc.sqlite3"
            )
            competing_bytes = b"competing backup"
            real_replace = os.replace
            real_link = os.link

            def race_replace(source: object, target: object) -> None:
                target_path = Path(target)
                if target_path == backup_path:
                    target_path.write_bytes(competing_bytes)
                real_replace(source, target)

            def race_link(source: object, target: object) -> None:
                target_path = Path(target)
                if target_path == backup_path:
                    target_path.write_bytes(competing_bytes)
                real_link(source, target)

            with (
                mock.patch(
                    "backend.app.infrastructure.database_backup.uuid.uuid4",
                    return_value=mock.Mock(hex=backup_id),
                ),
                mock.patch(
                    "backend.app.infrastructure.database_backup.datetime"
                ) as datetime_mock,
                mock.patch(
                    "backend.app.infrastructure.database_backup.os.replace",
                    side_effect=race_replace,
                ),
                mock.patch(
                    "backend.app.infrastructure.database_backup.os.link",
                    side_effect=race_link,
                ),
            ):
                datetime_mock.now.return_value = fixed_now
                with self.assertRaises(DatabaseBackupError) as raised:
                    create_verified_backup(source_path, backup_dir, label="race")

            self.assertEqual(raised.exception.code, "BACKUP_TARGET_EXISTS")
            self.assertEqual(backup_path.read_bytes(), competing_bytes)

    @unittest.skipUnless(os.name == "nt", "Windows no-replace rename fallback")
    def test_backup_uses_atomic_windows_rename_when_hard_links_are_unavailable(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory(prefix="study-app-backup-rename-") as temp_dir:
            root = Path(temp_dir)
            source_path = root / "app.db"
            connection = sqlite3.connect(source_path)
            try:
                connection.execute("CREATE TABLE papers(id TEXT PRIMARY KEY)")
                connection.execute("INSERT INTO papers(id) VALUES('paper-1')")
                connection.commit()
            finally:
                connection.close()

            unsupported = OSError(1, "hard links unavailable on this volume")
            with mock.patch(
                "backend.app.infrastructure.database_backup.os.link",
                side_effect=unsupported,
            ):
                result = create_verified_backup(
                    source_path,
                    root / "backups",
                    label="rename-fallback",
                )

            self.assertTrue(result.backup_path.is_file())
            self.assertTrue(result.manifest_path.is_file())
            self.assertTrue(
                verify_backup(result.backup_path, result.manifest_path).valid
            )

    @unittest.skipUnless(os.name == "nt", "Windows no-replace rename fallback")
    def test_windows_rename_fallback_never_overwrites_a_racing_target(self) -> None:
        with tempfile.TemporaryDirectory(prefix="study-app-rename-race-") as temp_dir:
            root = Path(temp_dir)
            source_path = root / "app.db"
            connection = sqlite3.connect(source_path)
            try:
                connection.execute("CREATE TABLE papers(id TEXT PRIMARY KEY)")
                connection.execute("INSERT INTO papers(id) VALUES('paper-1')")
                connection.commit()
            finally:
                connection.close()

            backup_dir = root / "backups"
            backup_id = "f" * 32
            fixed_now = datetime(2026, 8, 7, tzinfo=timezone.utc)
            backup_path = (
                backup_dir
                / "app-rename-race-20260807T000000Z-ffffffffffff.sqlite3"
            )
            competing_bytes = b"competing Windows rename target"
            real_rename = database_backup_module._rename_windows_handle_no_replace

            def racing_rename(handle: object, target: object) -> None:
                target_path = Path(target)
                if target_path == backup_path:
                    target_path.write_bytes(competing_bytes)
                real_rename(handle, target_path)

            with (
                mock.patch(
                    "backend.app.infrastructure.database_backup.uuid.uuid4",
                    return_value=mock.Mock(hex=backup_id),
                ),
                mock.patch(
                    "backend.app.infrastructure.database_backup.datetime"
                ) as datetime_mock,
                mock.patch(
                    "backend.app.infrastructure.database_backup.os.link",
                    side_effect=OSError(1, "hard links unavailable"),
                ),
                mock.patch(
                    "backend.app.infrastructure.database_backup."
                    "_rename_windows_handle_no_replace",
                    side_effect=racing_rename,
                ),
            ):
                datetime_mock.now.return_value = fixed_now
                with self.assertRaises(DatabaseBackupError) as raised:
                    create_verified_backup(
                        source_path,
                        backup_dir,
                        label="rename-race",
                    )

            self.assertEqual(raised.exception.code, "BACKUP_TARGET_EXISTS")
            self.assertEqual(backup_path.read_bytes(), competing_bytes)

    def test_backup_failure_never_deletes_target_replaced_after_publication(self) -> None:
        with tempfile.TemporaryDirectory(prefix="study-app-backup-replaced-") as temp_dir:
            root = Path(temp_dir)
            source_path = root / "app.db"
            connection = sqlite3.connect(source_path)
            try:
                connection.execute("CREATE TABLE papers(id TEXT PRIMARY KEY)")
                connection.execute("INSERT INTO papers(id) VALUES('paper-1')")
                connection.commit()
            finally:
                connection.close()

            competing_bytes = b"replacement owned by another process"
            replaced_path: Path | None = None

            def replace_then_fail(
                backup_database: object,
                manifest_file: object,
            ) -> None:
                nonlocal replaced_path
                del manifest_file
                replaced_path = Path(backup_database)
                replaced_path.unlink()
                replaced_path.write_bytes(competing_bytes)
                raise DatabaseBackupError(
                    "INJECTED_VERIFY_FAILURE",
                    "Injected verification failure.",
                )

            with mock.patch(
                "backend.app.infrastructure.database_backup.verify_backup",
                side_effect=replace_then_fail,
            ):
                with self.assertRaises(DatabaseBackupError) as raised:
                    create_verified_backup(
                        source_path,
                        root / "backups",
                        label="replaced",
                    )

            self.assertEqual(raised.exception.code, "INJECTED_VERIFY_FAILURE")
            self.assertIsNotNone(replaced_path)
            assert replaced_path is not None
            self.assertEqual(replaced_path.read_bytes(), competing_bytes)

    def test_external_manifest_temp_collision_is_ignored_and_never_deleted(self) -> None:
        with tempfile.TemporaryDirectory(prefix="study-app-manifest-race-") as temp_dir:
            root = Path(temp_dir)
            source_path = root / "app.db"
            connection = sqlite3.connect(source_path)
            try:
                connection.execute("CREATE TABLE papers(id TEXT PRIMARY KEY)")
                connection.execute("INSERT INTO papers(id) VALUES('paper-1')")
                connection.commit()
            finally:
                connection.close()

            backup_dir = root / "backups"
            backup_dir.mkdir()
            backup_id = "d" * 32
            manifest_temp_id = "e" * 32
            fixed_now = datetime(2026, 8, 7, tzinfo=timezone.utc)
            backup_name = "app-manifest-race-20260807T000000Z-dddddddddddd.sqlite3"
            manifest_name = f"{backup_name}.manifest.json"
            manifest_temp_path = (
                backup_dir / f".{manifest_name}.{manifest_temp_id}.tmp"
            )
            competing_bytes = b"competing manifest temp"
            manifest_temp_path.write_bytes(competing_bytes)

            uuid_values = [
                mock.Mock(hex=backup_id),
                mock.Mock(hex=manifest_temp_id),
            ]
            with (
                mock.patch(
                    "backend.app.infrastructure.database_backup.uuid.uuid4",
                    side_effect=uuid_values,
                ),
                mock.patch(
                    "backend.app.infrastructure.database_backup.datetime"
                ) as datetime_mock,
            ):
                datetime_mock.now.return_value = fixed_now
                result = create_verified_backup(
                    source_path,
                    backup_dir,
                    label="manifest-race",
                )

            self.assertTrue(result.verification.valid)
            self.assertEqual(manifest_temp_path.read_bytes(), competing_bytes)

    def test_format_v1_manifest_without_sqlite_sequence_remains_verifiable(self) -> None:
        with tempfile.TemporaryDirectory(prefix="study-app-format-v1-") as temp_dir:
            root = Path(temp_dir)
            source_path = root / "app.db"
            connection = sqlite3.connect(source_path)
            try:
                connection.execute(
                    "CREATE TABLE papers(id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT)"
                )
                connection.execute("INSERT INTO papers(title) VALUES('Legacy')")
                connection.commit()
            finally:
                connection.close()

            result = create_verified_backup(source_path, root / "backups", label="v1")
            legacy_fingerprint = database_backup_module._fingerprint_database(
                result.backup_path,
                format_version=1,
            )
            manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
            manifest["formatVersion"] = 1
            manifest["database"] = legacy_fingerprint.to_dict()
            manifest_payload = {
                key: value
                for key, value in manifest.items()
                if key != "manifestSha256"
            }
            manifest["manifestSha256"] = hashlib.sha256(
                json.dumps(
                    manifest_payload,
                    ensure_ascii=False,
                    separators=(",", ":"),
                    sort_keys=True,
                ).encode("utf-8")
            ).hexdigest()
            result.manifest_path.write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
                newline="\n",
            )

            verification = verify_backup(result.backup_path, result.manifest_path)

            self.assertTrue(verification.valid)
            self.assertEqual(verification.format_version, 1)
            self.assertNotIn("sqlite_sequence", verification.table_counts)

    def test_verify_rejects_unknown_manifest_fields_for_v1_and_v2(self) -> None:
        with tempfile.TemporaryDirectory(prefix="study-app-manifest-schema-") as temp_dir:
            root = Path(temp_dir)
            source_path = root / "app.db"
            connection = sqlite3.connect(source_path)
            try:
                connection.execute("CREATE TABLE papers(id TEXT PRIMARY KEY, title TEXT NOT NULL)")
                connection.execute(
                    "INSERT INTO papers(id, title) VALUES(?, ?)",
                    ("paper-1", "Schema"),
                )
                connection.commit()
            finally:
                connection.close()

            result = create_verified_backup(source_path, root / "backups", label="schema")
            original = json.loads(result.manifest_path.read_text(encoding="utf-8"))
            v1_database = database_backup_module._fingerprint_database(
                result.backup_path,
                format_version=1,
            ).to_dict()

            for format_version in (1, 2):
                for field_location in ("top-level", "database"):
                    with self.subTest(
                        format_version=format_version,
                        field_location=field_location,
                    ):
                        manifest = json.loads(json.dumps(original))
                        manifest["formatVersion"] = format_version
                        if format_version == 1:
                            manifest["database"] = v1_database
                        if field_location == "top-level":
                            manifest["unexpectedTopLevel"] = "must be rejected"
                        else:
                            manifest["database"]["unexpectedDatabaseField"] = "must be rejected"

                        integrity_payload = json.loads(json.dumps(manifest))
                        integrity_payload.pop("manifestSha256", None)
                        integrity_payload.pop("unexpectedTopLevel", None)
                        integrity_payload["database"].pop("unexpectedDatabaseField", None)
                        canonical_payload = json.dumps(
                            integrity_payload,
                            ensure_ascii=False,
                            separators=(",", ":"),
                            sort_keys=True,
                        ).encode("utf-8")
                        manifest["manifestSha256"] = hashlib.sha256(
                            canonical_payload
                        ).hexdigest()
                        result.manifest_path.write_text(
                            json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True)
                            + "\n",
                            encoding="utf-8",
                            newline="\n",
                        )

                        with self.assertRaises(DatabaseBackupError) as raised:
                            verify_backup(result.backup_path, result.manifest_path)

                        self.assertEqual(raised.exception.code, "BACKUP_MANIFEST_INVALID")

    def test_restore_rejects_path_like_backup_id_even_with_recomputed_hash(self) -> None:
        with tempfile.TemporaryDirectory(prefix="study-app-manifest-id-") as temp_dir:
            root = Path(temp_dir)
            source_path = root / "app.db"
            connection = sqlite3.connect(source_path)
            try:
                connection.execute("CREATE TABLE papers(id TEXT PRIMARY KEY)")
                connection.execute("INSERT INTO papers(id) VALUES('paper-1')")
                connection.commit()
            finally:
                connection.close()

            result = create_verified_backup(source_path, root / "backups", label="id-guard")
            manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
            manifest["backupId"] = "..\\..\\escaped-backup"
            manifest_payload = {
                key: value
                for key, value in manifest.items()
                if key != "manifestSha256"
            }
            canonical_payload = json.dumps(
                manifest_payload,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
            manifest["manifestSha256"] = hashlib.sha256(canonical_payload).hexdigest()
            result.manifest_path.write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
                newline="\n",
            )
            restore_root = root / "restore-checks"

            with self.assertRaises(DatabaseBackupError) as raised:
                restore_backup_for_validation(
                    result.backup_path,
                    result.manifest_path,
                    restore_root,
                )

            self.assertEqual(raised.exception.code, "BACKUP_MANIFEST_INVALID")
            self.assertFalse(restore_root.exists())
            self.assertEqual(
                {path.name for path in root.iterdir()},
                {"app.db", "backups"},
            )

    def test_verify_rejects_modified_backup_before_restore_validation(self) -> None:
        with tempfile.TemporaryDirectory(prefix="study-app-backup-tamper-") as temp_dir:
            root = Path(temp_dir)
            source_path = root / "app.db"
            connection = sqlite3.connect(source_path)
            try:
                connection.execute(
                    "CREATE TABLE papers(id TEXT PRIMARY KEY, title TEXT NOT NULL)"
                )
                connection.execute(
                    "INSERT INTO papers(id, title) VALUES('paper-1', 'Original')"
                )
                connection.commit()
            finally:
                connection.close()

            result = create_verified_backup(source_path, root / "backups", label="tamper-db")
            tampered = sqlite3.connect(result.backup_path)
            try:
                tampered.execute("UPDATE papers SET title = 'Modified' WHERE id = 'paper-1'")
                tampered.commit()
            finally:
                tampered.close()

            with self.assertRaises(DatabaseBackupError) as raised:
                restore_backup_for_validation(
                    result.backup_path,
                    result.manifest_path,
                    root / "restore-checks",
                )
            self.assertEqual(raised.exception.code, "BACKUP_FILE_MISMATCH")
            self.assertFalse((root / "restore-checks").exists())

    def test_publish_binds_ownership_before_a_target_can_be_replaced(self) -> None:
        with tempfile.TemporaryDirectory(prefix="study-app-publish-identity-") as temp_dir:
            root = Path(temp_dir)
            source_path = root / "app.db"
            connection = sqlite3.connect(source_path)
            try:
                connection.execute("CREATE TABLE papers(id TEXT PRIMARY KEY)")
                connection.execute("INSERT INTO papers(id) VALUES('paper-1')")
                connection.commit()
            finally:
                connection.close()

            backup_dir = root / "backups"
            backup_id = "1" * 32
            fixed_now = datetime(2026, 8, 8, tzinfo=timezone.utc)
            backup_path = (
                backup_dir
                / "app-publish-identity-20260808T000000Z-111111111111.sqlite3"
            )
            competing_bytes = b"replacement published by another process"
            real_link = os.link

            def replace_after_link(source: object, target: object) -> None:
                real_link(source, target)
                target_path = Path(target)
                if target_path == backup_path:
                    target_path.unlink()
                    target_path.write_bytes(competing_bytes)

            with (
                mock.patch(
                    "backend.app.infrastructure.database_backup.uuid.uuid4",
                    return_value=mock.Mock(hex=backup_id),
                ),
                mock.patch(
                    "backend.app.infrastructure.database_backup.datetime"
                ) as datetime_mock,
                mock.patch(
                    "backend.app.infrastructure.database_backup.os.link",
                    side_effect=replace_after_link,
                ),
            ):
                datetime_mock.now.return_value = fixed_now
                with self.assertRaises(DatabaseBackupError) as raised:
                    create_verified_backup(
                        source_path,
                        backup_dir,
                        label="publish-identity",
                    )

            self.assertEqual(
                raised.exception.code,
                "BACKUP_PUBLISH_OWNERSHIP_CHANGED",
            )
            self.assertEqual(backup_path.read_bytes(), competing_bytes)

    @unittest.skipUnless(os.name == "nt", "Windows exFAT rename identity semantics")
    def test_backup_publish_accepts_verified_exfat_cross_directory_rename(self) -> None:
        workspace_root = Path(__file__).resolve().parents[2]
        with tempfile.TemporaryDirectory(
            prefix="study-app-exfat-publish-",
            dir=workspace_root,
        ) as temp_dir:
            root = Path(temp_dir)
            source_path = root / "app.db"
            backup_dir = root / "backups"
            _create_minimal_database(source_path)

            backup_id = "2" * 32
            fixed_now = datetime(2026, 8, 9, tzinfo=timezone.utc)
            backup_path = (
                backup_dir
                / "app-exfat-20260809T000000Z-222222222222.sqlite3"
            )
            with (
                mock.patch(
                    "backend.app.infrastructure.database_backup.uuid.uuid4",
                    return_value=mock.Mock(hex=backup_id),
                ),
                mock.patch(
                    "backend.app.infrastructure.database_backup.datetime"
                ) as datetime_mock,
                mock.patch(
                    "backend.app.infrastructure.database_backup.os.link",
                    side_effect=OSError(1, "hard links unsupported"),
                ),
            ):
                datetime_mock.now.return_value = fixed_now
                result = create_verified_backup(
                    source_path,
                    backup_dir,
                    label="exfat",
                )

            self.assertEqual(result.backup_path, backup_path)
            self.assertTrue(result.verification.valid)
            self.assertEqual(verify_backup(result.backup_path, result.manifest_path).valid, True)

    @unittest.skipUnless(os.name == "nt", "Windows lexical rename destination")
    def test_windows_rename_does_not_follow_final_component_resolution(self) -> None:
        with tempfile.TemporaryDirectory(prefix="study-app-lexical-rename-") as temp_dir:
            root = Path(temp_dir)
            source_path = root / "app.db"
            backup_dir = root / "backups"
            _create_minimal_database(source_path)
            backup_id = "4" * 32
            fixed_now = datetime(2026, 8, 9, tzinfo=timezone.utc)
            backup_path = (
                backup_dir
                / "app-lexical-rename-20260809T000000Z-444444444444.sqlite3"
            )
            external_target = root / "outside.sqlite3"
            real_resolve = Path.resolve
            real_rename = database_backup_module._rename_windows_handle_no_replace

            def redirect_final_component(path: Path, strict: bool = False) -> Path:
                if path == backup_path:
                    return external_target
                return real_resolve(path, strict=strict)

            def redirect_during_rename(handle: object, target: Path) -> None:
                with mock.patch.object(
                    Path,
                    "resolve",
                    autospec=True,
                    side_effect=redirect_final_component,
                ):
                    real_rename(handle, target)

            with (
                mock.patch(
                    "backend.app.infrastructure.database_backup.uuid.uuid4",
                    return_value=mock.Mock(hex=backup_id),
                ),
                mock.patch(
                    "backend.app.infrastructure.database_backup.datetime"
                ) as datetime_mock,
                mock.patch(
                    "backend.app.infrastructure.database_backup.os.link",
                    side_effect=OSError(1, "hard links unsupported"),
                ),
                mock.patch(
                    "backend.app.infrastructure.database_backup."
                    "_rename_windows_handle_no_replace",
                    side_effect=redirect_during_rename,
                ),
            ):
                datetime_mock.now.return_value = fixed_now
                result = create_verified_backup(
                    source_path,
                    backup_dir,
                    label="lexical-rename",
                )

            self.assertEqual(result.backup_path, backup_path)
            self.assertTrue(backup_path.is_file())
            self.assertFalse(external_target.exists())

    @unittest.skipUnless(os.name == "nt", "Windows final-component reparse race")
    def test_windows_rename_rejects_a_final_component_symlink(self) -> None:
        with tempfile.TemporaryDirectory(prefix="study-app-reparse-rename-") as temp_dir:
            root = Path(temp_dir)
            source_path = root / "app.db"
            backup_dir = root / "backups"
            _create_minimal_database(source_path)
            backup_id = "5" * 32
            fixed_now = datetime(2026, 8, 9, tzinfo=timezone.utc)
            backup_path = (
                backup_dir
                / "app-reparse-rename-20260809T000000Z-555555555555.sqlite3"
            )
            external_target = root / "outside.sqlite3"
            backup_dir.mkdir()
            try:
                os.symlink(external_target, backup_path)
            except OSError as error:
                self.skipTest(f"symlink creation unavailable: {error}")

            with (
                mock.patch(
                    "backend.app.infrastructure.database_backup.uuid.uuid4",
                    return_value=mock.Mock(hex=backup_id),
                ),
                mock.patch(
                    "backend.app.infrastructure.database_backup.datetime"
                ) as datetime_mock,
                mock.patch(
                    "backend.app.infrastructure.database_backup.os.link",
                    side_effect=OSError(1, "hard links unsupported"),
                ),
            ):
                datetime_mock.now.return_value = fixed_now
                with self.assertRaises(DatabaseBackupError) as raised:
                    create_verified_backup(
                        source_path,
                        backup_dir,
                        label="reparse-rename",
                    )

            self.assertEqual(raised.exception.code, "BACKUP_TARGET_EXISTS")
            self.assertTrue(backup_path.is_symlink())
            self.assertFalse(external_target.exists())

    @unittest.skipUnless(os.name == "nt", "Windows handle-bound rename race")
    def test_windows_bound_rename_blocks_an_identical_post_rename_replacement(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory(prefix="study-app-bound-rename-") as temp_dir:
            root = Path(temp_dir)
            source_path = root / "app.db"
            backup_dir = root / "backups"
            _create_minimal_database(source_path)

            backup_id = "3" * 32
            fixed_now = datetime(2026, 8, 9, tzinfo=timezone.utc)
            backup_path = (
                backup_dir
                / "app-bound-rename-20260809T000000Z-333333333333.sqlite3"
            )
            real_reopen = database_backup_module._reopen_windows_bound_file
            replacement_attempted = False
            replacement_blocked = False
            replacement_created = False

            def try_identical_replacement(
                handle: object,
                *,
                share_delete: bool,
            ) -> object:
                nonlocal replacement_attempted, replacement_blocked, replacement_created
                reopened = real_reopen(handle, share_delete=share_delete)
                if share_delete or not backup_path.exists():
                    return reopened
                replacement_attempted = True
                payload = backup_path.read_bytes()
                metadata = backup_path.stat()
                try:
                    backup_path.unlink()
                except PermissionError:
                    replacement_blocked = True
                    return reopened
                backup_path.write_bytes(payload)
                os.utime(
                    backup_path,
                    ns=(int(metadata.st_atime_ns), int(metadata.st_mtime_ns)),
                )
                replacement_created = True
                return reopened

            with (
                mock.patch(
                    "backend.app.infrastructure.database_backup.uuid.uuid4",
                    return_value=mock.Mock(hex=backup_id),
                ),
                mock.patch(
                    "backend.app.infrastructure.database_backup.datetime"
                ) as datetime_mock,
                mock.patch(
                    "backend.app.infrastructure.database_backup.os.link",
                    side_effect=OSError(1, "hard links unsupported"),
                ),
                mock.patch(
                    "backend.app.infrastructure.database_backup."
                    "_reopen_windows_bound_file",
                    side_effect=try_identical_replacement,
                ),
            ):
                datetime_mock.now.return_value = fixed_now
                result = create_verified_backup(
                    source_path,
                    backup_dir,
                    label="bound-rename",
                )

            self.assertTrue(result.verification.valid)
            self.assertTrue(replacement_attempted)
            self.assertTrue(replacement_blocked)
            self.assertFalse(replacement_created)

    @unittest.skipUnless(os.name == "nt", "Windows handle-bound delete semantics")
    def test_cleanup_does_not_delete_a_target_replaced_after_identity_check(self) -> None:
        with tempfile.TemporaryDirectory(prefix="study-app-cleanup-identity-") as temp_dir:
            root = Path(temp_dir)
            source_path = root / "app.db"
            connection = sqlite3.connect(source_path)
            try:
                connection.execute("CREATE TABLE papers(id TEXT PRIMARY KEY)")
                connection.execute("INSERT INTO papers(id) VALUES('paper-1')")
                connection.commit()
            finally:
                connection.close()

            competing_bytes = b"replacement after identity check"
            published_path: Path | None = None
            replacement_enabled = False
            replacement_done = False
            real_read_identity = database_backup_module._read_file_identity

            def fail_after_publication(
                backup_database: object,
                manifest_file: object,
            ) -> None:
                nonlocal published_path, replacement_enabled
                del manifest_file
                published_path = Path(backup_database)
                replacement_enabled = True
                raise DatabaseBackupError(
                    "INJECTED_VERIFY_FAILURE",
                    "Injected verification failure.",
                )

            def read_then_replace(path: Path) -> object:
                nonlocal replacement_done
                identity = real_read_identity(path)
                if (
                    replacement_enabled
                    and not replacement_done
                    and published_path is not None
                    and path == published_path
                ):
                    os.unlink(path)
                    path.write_bytes(competing_bytes)
                    replacement_done = True
                return identity

            with (
                mock.patch(
                    "backend.app.infrastructure.database_backup.verify_backup",
                    side_effect=fail_after_publication,
                ),
                mock.patch(
                    "backend.app.infrastructure.database_backup._read_file_identity",
                    side_effect=read_then_replace,
                ),
            ):
                with self.assertRaises(DatabaseBackupError) as raised:
                    create_verified_backup(
                        source_path,
                        root / "backups",
                        label="cleanup-identity",
                    )

            self.assertEqual(raised.exception.code, "INJECTED_VERIFY_FAILURE")
            self.assertTrue(replacement_done)
            self.assertIsNotNone(published_path)
            assert published_path is not None
            self.assertEqual(published_path.read_bytes(), competing_bytes)

    def test_posix_private_namespace_cleanup_removes_owned_paths(self) -> None:
        with tempfile.TemporaryDirectory(prefix="study-app-posix-cleanup-") as temp_dir:
            root = Path(temp_dir)
            generated_directory = root / "generated-stage"
            generated_file = generated_directory / "generated.tmp"
            generated_directory.mkdir()
            generated_file.write_bytes(b"owned")
            file_identity = database_backup_module._read_file_identity(generated_file)
            directory_identity = database_backup_module._read_directory_identity(
                generated_directory
            )
            parent_identity = database_backup_module._read_directory_identity(root)

            with (
                mock.patch.object(database_backup_module.os, "name", "posix"),
                mock.patch(
                    "backend.app.infrastructure.database_backup._is_private_posix_directory",
                    create=True,
                    return_value=True,
                ),
            ):
                self.assertTrue(
                    database_backup_module._unlink_owned_file(
                        generated_file,
                        strict=True,
                        expected_identity=file_identity,
                        owned_directory=generated_directory,
                        expected_directory_identity=directory_identity,
                    )
                )
                self.assertFalse(generated_file.exists())
                self.assertTrue(
                    database_backup_module._remove_owned_staging_directory(
                        generated_directory,
                        strict=True,
                        expected_identity=directory_identity,
                        owned_parent_directory=root,
                        expected_parent_identity=parent_identity,
                    )
                )
                self.assertFalse(generated_directory.exists())

    def test_create_wires_posix_private_namespace_ownership_through_public_seam(self) -> None:
        with tempfile.TemporaryDirectory(prefix="study-app-posix-create-") as temp_dir:
            root = Path(temp_dir)
            source_path = root / "app.db"
            backup_dir = root / "backups"
            connection = sqlite3.connect(source_path)
            try:
                connection.execute("CREATE TABLE papers(id TEXT PRIMARY KEY)")
                connection.execute("INSERT INTO papers(id) VALUES('paper-1')")
                connection.commit()
            finally:
                connection.close()

            concrete_path_type = type(source_path)
            with (
                mock.patch.object(database_backup_module.os, "name", "posix"),
                mock.patch(
                    "backend.app.infrastructure.database_backup.Path",
                    side_effect=concrete_path_type,
                ),
                mock.patch(
                    "backend.app.infrastructure.database_backup._is_private_posix_directory",
                    return_value=True,
                ),
                mock.patch(
                    "backend.app.infrastructure.database_backup._assert_owner_only_directory_mode",
                ) as permission_check,
            ):
                result = create_verified_backup(
                    source_path,
                    backup_dir,
                    label="posix-private",
                )

            permission_check.assert_called_once()
            self.assertTrue(result.verification.valid)
            self.assertEqual(
                sorted(path.name for path in backup_dir.iterdir()),
                sorted((result.backup_path.name, result.manifest_path.name)),
            )

    def test_posix_private_file_cleanup_preserves_a_racing_replacement(self) -> None:
        with tempfile.TemporaryDirectory(prefix="study-app-posix-file-race-") as temp_dir:
            root = Path(temp_dir)
            owned_directory = root / "owned-stage"
            owned_directory.mkdir()
            generated_file = owned_directory / "generated.tmp"
            generated_file.write_bytes(b"invocation-owned")
            file_identity = database_backup_module._read_file_identity(generated_file)
            directory_identity = database_backup_module._read_directory_identity(
                owned_directory
            )
            competing_bytes = b"competing replacement"
            replacement_created = False
            real_read_identity = database_backup_module._read_file_identity

            def read_then_replace(path: Path) -> object:
                nonlocal replacement_created
                identity = real_read_identity(path)
                if path == generated_file and not replacement_created:
                    path.unlink()
                    path.write_bytes(competing_bytes)
                    replacement_created = True
                return identity

            with (
                mock.patch.object(database_backup_module.os, "name", "posix"),
                mock.patch(
                    "backend.app.infrastructure.database_backup._is_private_posix_directory",
                    return_value=True,
                ),
                mock.patch(
                    "backend.app.infrastructure.database_backup._read_file_identity",
                    side_effect=read_then_replace,
                ),
            ):
                removed = database_backup_module._unlink_owned_file(
                    generated_file,
                    strict=False,
                    expected_identity=file_identity,
                    owned_directory=owned_directory,
                    expected_directory_identity=directory_identity,
                )

            self.assertTrue(replacement_created)
            self.assertFalse(removed)
            self.assertEqual(generated_file.read_bytes(), competing_bytes)

    def test_posix_private_directory_cleanup_preserves_a_racing_replacement(self) -> None:
        with tempfile.TemporaryDirectory(prefix="study-app-posix-directory-race-") as temp_dir:
            root = Path(temp_dir)
            generated_directory = root / "generated-stage"
            generated_directory.mkdir()
            directory_identity = database_backup_module._read_directory_identity(
                generated_directory
            )
            parent_identity = database_backup_module._read_directory_identity(root)
            replacement_created = False
            real_read_identity = database_backup_module._read_directory_identity

            def read_then_replace(path: Path) -> object:
                nonlocal replacement_created
                identity = real_read_identity(path)
                if path == generated_directory and not replacement_created:
                    path.rmdir()
                    path.mkdir()
                    replacement_created = True
                return identity

            with (
                mock.patch.object(database_backup_module.os, "name", "posix"),
                mock.patch(
                    "backend.app.infrastructure.database_backup._is_private_posix_directory",
                    return_value=True,
                ),
                mock.patch(
                    "backend.app.infrastructure.database_backup._read_directory_identity",
                    side_effect=read_then_replace,
                ),
            ):
                removed = database_backup_module._remove_owned_staging_directory(
                    generated_directory,
                    strict=False,
                    expected_identity=directory_identity,
                    owned_parent_directory=root,
                    expected_parent_identity=parent_identity,
                )

            self.assertTrue(replacement_created)
            self.assertFalse(removed)
            self.assertTrue(generated_directory.is_dir())

    def test_posix_cleanup_without_private_parent_ownership_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory(prefix="study-app-posix-unbound-") as temp_dir:
            root = Path(temp_dir)
            generated_file = root / "unbound.tmp"
            generated_directory = root / "unbound-stage"
            generated_file.write_bytes(b"not privately bound")
            generated_directory.mkdir()
            file_identity = database_backup_module._read_file_identity(generated_file)
            directory_identity = database_backup_module._read_directory_identity(
                generated_directory
            )

            with mock.patch.object(database_backup_module.os, "name", "posix"):
                self.assertFalse(
                    database_backup_module._unlink_owned_file(
                        generated_file,
                        strict=False,
                        expected_identity=file_identity,
                    )
                )
                self.assertFalse(
                    database_backup_module._remove_owned_staging_directory(
                        generated_directory,
                        strict=False,
                        expected_identity=directory_identity,
                    )
                )

            self.assertEqual(generated_file.read_bytes(), b"not privately bound")
            self.assertTrue(generated_directory.is_dir())

    def test_sqlite_backup_is_built_inside_a_private_staging_directory(self) -> None:
        with tempfile.TemporaryDirectory(prefix="study-app-private-stage-") as temp_dir:
            root = Path(temp_dir)
            source_path = root / "app.db"
            connection = sqlite3.connect(source_path)
            try:
                connection.execute("CREATE TABLE papers(id TEXT PRIMARY KEY)")
                connection.execute("INSERT INTO papers(id) VALUES('paper-1')")
                connection.commit()
            finally:
                connection.close()

            backup_dir = root / "backups"
            destination_paths: list[Path] = []
            real_connect = sqlite3.connect

            def record_destination(database: object, *args: object, **kwargs: object):
                if isinstance(database, Path):
                    destination_paths.append(database.resolve())
                return real_connect(database, *args, **kwargs)

            with mock.patch(
                "backend.app.infrastructure.database_backup.sqlite3.connect",
                side_effect=record_destination,
            ):
                create_verified_backup(source_path, backup_dir, label="private-stage")

            self.assertEqual(len(destination_paths), 1)
            staging_directory = destination_paths[0].parent
            self.assertEqual(staging_directory.parent, backup_dir.resolve())
            self.assertTrue(staging_directory.name.startswith(".backup-stage-"))
            self.assertFalse(staging_directory.exists())

    def test_exclusive_publication_files_request_owner_only_mode(self) -> None:
        with tempfile.TemporaryDirectory(prefix="study-app-owner-only-file-") as temp_dir:
            output_path = Path(temp_dir) / "evidence.json"
            with mock.patch.object(os, "open", wraps=os.open) as exclusive_open:
                with database_backup_module._OwnedExclusiveFile(output_path) as owned_file:
                    owned_file.write(b"sensitive evidence")
                    owned_file.flush_and_sync()

            matching_calls = [
                call
                for call in exclusive_open.call_args_list
                if Path(call.args[0]) == output_path
            ]
            self.assertEqual(len(matching_calls), 1)
            self.assertEqual(matching_calls[0].args[2], 0o600)

    def test_publication_directory_mode_rejects_group_or_world_access(self) -> None:
        database_backup_module._assert_owner_only_directory_mode(
            stat_module.S_IFDIR | 0o700
        )
        for unsafe_mode in (0o750, 0o705, 0o777):
            with self.subTest(mode=oct(unsafe_mode)):
                with self.assertRaises(DatabaseBackupError) as raised:
                    database_backup_module._assert_owner_only_directory_mode(
                        stat_module.S_IFDIR | unsafe_mode
                    )
                self.assertEqual(
                    raised.exception.code,
                    "BACKUP_OUTPUT_DIRECTORY_PERMISSIONS",
                )

    def test_noninteger_legacy_migration_is_classified_and_leaves_no_staging_data(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory(prefix="study-app-invalid-migration-") as temp_dir:
            root = Path(temp_dir)
            source_path = root / "app.db"
            backup_dir = root / "backups"
            connection = sqlite3.connect(source_path)
            try:
                connection.execute("CREATE TABLE papers(id TEXT PRIMARY KEY)")
                connection.execute("CREATE TABLE schema_migrations(version TEXT NOT NULL)")
                connection.execute("INSERT INTO papers(id) VALUES('paper-1')")
                connection.execute(
                    "INSERT INTO schema_migrations(version) VALUES('not-an-integer')"
                )
                connection.commit()
            finally:
                connection.close()

            with self.assertRaises(DatabaseBackupError) as raised:
                create_verified_backup(
                    source_path,
                    backup_dir,
                    label="invalid-migration",
                )

            self.assertEqual(
                raised.exception.code,
                "BACKUP_SCHEMA_MIGRATION_INVALID",
            )
            self.assertEqual(list(backup_dir.rglob("*")), [])

    def test_sqlite_sequence_is_part_of_the_versioned_logical_fingerprint(self) -> None:
        with tempfile.TemporaryDirectory(prefix="study-app-sqlite-sequence-") as temp_dir:
            root = Path(temp_dir)
            source_path = root / "app.db"
            connection = sqlite3.connect(source_path)
            try:
                connection.execute(
                    "CREATE TABLE papers(id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT)"
                )
                connection.execute("INSERT INTO papers(title) VALUES('First')")
                connection.commit()
            finally:
                connection.close()

            result = create_verified_backup(
                source_path,
                root / "backups",
                label="sqlite-sequence",
            )
            manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(manifest["formatVersion"], 2)
            self.assertEqual(manifest["database"]["tableCounts"]["sqlite_sequence"], 1)

            tampered = sqlite3.connect(result.backup_path)
            try:
                tampered.execute(
                    "UPDATE sqlite_sequence SET seq = 999 WHERE name = 'papers'"
                )
                tampered.commit()
            finally:
                tampered.close()

            manifest["backupSizeBytes"] = result.backup_path.stat().st_size
            manifest["backupSha256"] = hashlib.sha256(
                result.backup_path.read_bytes()
            ).hexdigest()
            manifest_payload = {
                key: value
                for key, value in manifest.items()
                if key != "manifestSha256"
            }
            manifest["manifestSha256"] = hashlib.sha256(
                json.dumps(
                    manifest_payload,
                    ensure_ascii=False,
                    separators=(",", ":"),
                    sort_keys=True,
                ).encode("utf-8")
            ).hexdigest()
            result.manifest_path.write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
                newline="\n",
            )

            with self.assertRaises(DatabaseBackupError) as raised:
                verify_backup(result.backup_path, result.manifest_path)
            self.assertEqual(raised.exception.code, "BACKUP_LOGICAL_MISMATCH")

    def test_create_does_not_change_source_database_bytes_or_metadata(self) -> None:
        with tempfile.TemporaryDirectory(prefix="study-app-source-unchanged-") as temp_dir:
            root = Path(temp_dir)
            source_path = root / "app.db"
            connection = sqlite3.connect(source_path)
            try:
                connection.execute("CREATE TABLE papers(id TEXT PRIMARY KEY)")
                connection.execute("INSERT INTO papers(id) VALUES('paper-1')")
                connection.commit()
            finally:
                connection.close()

            before_bytes = source_path.read_bytes()
            before_stat = source_path.stat()
            create_verified_backup(source_path, root / "backups", label="read-only-source")
            after_stat = source_path.stat()

            self.assertEqual(source_path.read_bytes(), before_bytes)
            self.assertEqual(after_stat.st_size, before_stat.st_size)
            self.assertEqual(after_stat.st_mtime_ns, before_stat.st_mtime_ns)

    def test_unexpected_create_failure_is_classified_and_cleans_owned_outputs(self) -> None:
        with tempfile.TemporaryDirectory(prefix="study-app-unexpected-create-") as temp_dir:
            root = Path(temp_dir)
            source_path = root / "app.db"
            connection = sqlite3.connect(source_path)
            try:
                connection.execute("CREATE TABLE papers(id TEXT PRIMARY KEY)")
                connection.execute("INSERT INTO papers(id) VALUES('paper-1')")
                connection.commit()
            finally:
                connection.close()

            def fail_manifest(*args: object, **kwargs: object) -> object:
                del args, kwargs
                raise RuntimeError("unexpected manifest failure")

            with mock.patch(
                "backend.app.infrastructure.database_backup._write_manifest",
                side_effect=fail_manifest,
            ):
                with self.assertRaises(DatabaseBackupError) as raised:
                    create_verified_backup(source_path, root / "backups", label="unexpected")

            self.assertEqual(raised.exception.code, "BACKUP_CREATE_FAILED")
            self.assertEqual(list((root / "backups").rglob("*")), [])

    def test_unexpected_restore_failure_is_classified_and_cleans_owned_outputs(self) -> None:
        with tempfile.TemporaryDirectory(prefix="study-app-unexpected-restore-") as temp_dir:
            root = Path(temp_dir)
            source_path = root / "app.db"
            connection = sqlite3.connect(source_path)
            try:
                connection.execute("CREATE TABLE papers(id TEXT PRIMARY KEY)")
                connection.execute("INSERT INTO papers(id) VALUES('paper-1')")
                connection.commit()
            finally:
                connection.close()

            result = create_verified_backup(source_path, root / "backups", label="unexpected")
            real_fingerprint = database_backup_module._fingerprint_bound_restore

            def fail_destination(destination: object, manifest: object) -> object:
                database_path = destination.report_path
                if database_path.name == "app.db" and database_path.parent.name.startswith(
                    "restore-validation-"
                ):
                    raise RuntimeError("unexpected restore fingerprint failure")
                return real_fingerprint(destination, manifest)

            with mock.patch(
                "backend.app.infrastructure.database_backup._fingerprint_bound_restore",
                side_effect=fail_destination,
            ):
                with self.assertRaises(DatabaseBackupError) as raised:
                    restore_backup_for_validation(
                        result.backup_path,
                        result.manifest_path,
                        root / "restore-checks",
                    )

            self.assertEqual(raised.exception.code, "RESTORE_VALIDATION_FAILED")
            restore_root = root / "restore-checks"
            self.assertTrue(restore_root.is_dir())
            self.assertEqual(
                list(restore_root.glob("restore-validation-*")),
                [],
            )

    @unittest.skipUnless(os.name == "nt", "handle-bound ownership race")
    def test_staging_directory_replacement_is_never_removed(self) -> None:
        with tempfile.TemporaryDirectory(prefix="study-app-stage-race-") as temp_dir:
            root = Path(temp_dir)
            source_path = root / "app.db"
            connection = sqlite3.connect(source_path)
            try:
                connection.execute("CREATE TABLE papers(id TEXT PRIMARY KEY)")
                connection.execute("INSERT INTO papers(id) VALUES('paper-1')")
                connection.commit()
            finally:
                connection.close()

            backup_id = "a" * 32
            replaced_directory: Path | None = None

            def fail_after_replacing_stage(
                backup_database: object,
                manifest_file: object,
            ) -> None:
                del manifest_file
                nonlocal replaced_directory
                backup_path = Path(backup_database)
                stage = backup_path.parent / f".backup-stage-{backup_id}"
                stage.rmdir()
                stage.mkdir(mode=0o700)
                replaced_directory = stage
                raise DatabaseBackupError("INJECTED_VERIFY_FAILURE", "injected")

            with (
                mock.patch(
                    "backend.app.infrastructure.database_backup.uuid.uuid4",
                    return_value=mock.Mock(hex=backup_id),
                ),
                mock.patch(
                    "backend.app.infrastructure.database_backup.verify_backup",
                    side_effect=fail_after_replacing_stage,
                ),
            ):
                with self.assertRaises(DatabaseBackupError) as raised:
                    create_verified_backup(source_path, root / "backups", label="stage-race")

            self.assertEqual(raised.exception.code, "INJECTED_VERIFY_FAILURE")
            self.assertIsNotNone(replaced_directory)
            assert replaced_directory is not None
            self.assertTrue(replaced_directory.exists())

    @unittest.skipUnless(os.name == "nt", "Windows handle-bound directory delete")
    def test_staging_cleanup_preserves_directory_replaced_after_identity_check(self) -> None:
        with tempfile.TemporaryDirectory(prefix="study-app-stage-delete-race-") as temp_dir:
            root = Path(temp_dir)
            source_path = root / "app.db"
            backup_dir = root / "backups"
            connection = sqlite3.connect(source_path)
            try:
                connection.execute("CREATE TABLE papers(id TEXT PRIMARY KEY)")
                connection.execute("INSERT INTO papers(id) VALUES('paper-1')")
                connection.commit()
            finally:
                connection.close()

            backup_id = "b" * 32
            staging_directory = backup_dir / f".backup-stage-{backup_id}"
            directory_reads = 0
            replacement_created = False
            real_read_identity = database_backup_module._read_directory_identity

            def read_then_replace(path: Path) -> object:
                nonlocal directory_reads, replacement_created
                identity = real_read_identity(path)
                if path == staging_directory:
                    directory_reads += 1
                    if directory_reads == 2:
                        path.rmdir()
                        path.mkdir(mode=0o700)
                        replacement_created = True
                return identity

            with (
                mock.patch(
                    "backend.app.infrastructure.database_backup.uuid.uuid4",
                    return_value=mock.Mock(hex=backup_id),
                ),
                mock.patch(
                    "backend.app.infrastructure.database_backup._read_directory_identity",
                    side_effect=read_then_replace,
                ),
            ):
                with self.assertRaises(DatabaseBackupError) as raised:
                    create_verified_backup(source_path, backup_dir, label="stage-delete-race")

            self.assertTrue(replacement_created)
            self.assertEqual(raised.exception.code, "BACKUP_STAGING_OWNERSHIP_CHANGED")
            self.assertTrue(staging_directory.is_dir())

    @unittest.skipUnless(os.name == "nt", "handle-bound sidecar ownership race")
    def test_sidecar_cleanup_never_deletes_a_replacement(self) -> None:
        with tempfile.TemporaryDirectory(prefix="study-app-sidecar-race-") as temp_dir:
            owned_directory = Path(temp_dir) / "owned-stage"
            owned_directory.mkdir(mode=0o700)
            database_path = owned_directory / "backup.sqlite3"
            database_path.write_bytes(b"database")
            sidecar_path = database_path.with_name(f"{database_path.name}-wal")
            sidecar_path.write_bytes(b"invocation-owned sidecar")
            competing_bytes = b"replacement sidecar"
            replaced = False
            real_read_identity = database_backup_module._read_file_identity

            def read_then_replace(path: Path) -> object:
                nonlocal replaced
                identity = real_read_identity(path)
                if path == sidecar_path and not replaced:
                    path.unlink()
                    path.write_bytes(competing_bytes)
                    replaced = True
                return identity

            with mock.patch(
                "backend.app.infrastructure.database_backup._read_file_identity",
                side_effect=read_then_replace,
            ):
                database_backup_module._remove_database_sidecars(
                    database_path,
                    owned_directory=owned_directory,
                    strict=False,
                )

            self.assertTrue(replaced)
            self.assertEqual(sidecar_path.read_bytes(), competing_bytes)

    @unittest.skipUnless(hasattr(os, "symlink"), "symlink support")
    def test_restore_rejects_symlinked_output_root(self) -> None:
        with tempfile.TemporaryDirectory(prefix="study-app-restore-symlink-") as temp_dir:
            root = Path(temp_dir)
            source_path = root / "app.db"
            connection = sqlite3.connect(source_path)
            try:
                connection.execute("CREATE TABLE papers(id TEXT PRIMARY KEY)")
                connection.execute("INSERT INTO papers(id) VALUES('paper-1')")
                connection.commit()
            finally:
                connection.close()

            result = create_verified_backup(source_path, root / "backups", label="symlink")
            real_root = root / "real-restore"
            real_root.mkdir()
            linked_root = root / "linked-restore"
            try:
                os.symlink(real_root, linked_root, target_is_directory=True)
            except (OSError, NotImplementedError):
                self.skipTest("symlink creation is not permitted")

            with self.assertRaises(DatabaseBackupError) as raised:
                restore_backup_for_validation(
                    result.backup_path,
                    result.manifest_path,
                    linked_root,
                )

            self.assertEqual(raised.exception.code, "RESTORE_OUTPUT_DIRECTORY_INVALID")
            self.assertEqual(list(real_root.iterdir()), [])

    def test_restore_rechecks_output_root_for_reparse_before_first_write(self) -> None:
        with tempfile.TemporaryDirectory(prefix="study-app-restore-reparse-race-") as temp_dir:
            root = Path(temp_dir)
            source_path = root / "app.db"
            restore_root = root / "restore-checks"
            connection = sqlite3.connect(source_path)
            try:
                connection.execute("CREATE TABLE papers(id TEXT PRIMARY KEY)")
                connection.execute("INSERT INTO papers(id) VALUES('paper-1')")
                connection.commit()
            finally:
                connection.close()

            result = create_verified_backup(source_path, root / "backups", label="root-race")
            reparse_checks = 0

            def becomes_reparse_during_verification(path: Path) -> bool:
                nonlocal reparse_checks
                self.assertEqual(path, restore_root)
                reparse_checks += 1
                return reparse_checks >= 2

            with mock.patch(
                "backend.app.infrastructure.database_backup._path_contains_reparse_point",
                side_effect=becomes_reparse_during_verification,
            ):
                with self.assertRaises(DatabaseBackupError) as raised:
                    restore_backup_for_validation(
                        result.backup_path,
                        result.manifest_path,
                        restore_root,
                    )

            self.assertEqual(raised.exception.code, "RESTORE_OUTPUT_DIRECTORY_INVALID")
            self.assertGreaterEqual(reparse_checks, 2)
            self.assertFalse(restore_root.exists())

    def test_directory_identity_rejects_windows_reparse_attribute(self) -> None:
        with tempfile.TemporaryDirectory(prefix="study-app-reparse-attribute-") as temp_dir:
            directory = Path(temp_dir) / "generated"
            directory.mkdir()
            real_lstat = Path.lstat
            real_metadata = real_lstat(directory)
            reparse_metadata = mock.Mock(
                st_mode=stat_module.S_IFDIR,
                st_dev=real_metadata.st_dev,
                st_ino=real_metadata.st_ino,
                st_size=real_metadata.st_size,
                st_mtime_ns=real_metadata.st_mtime_ns,
                st_ctime_ns=real_metadata.st_ctime_ns,
                st_file_attributes=0x400,
            )

            def fake_lstat(path: Path) -> object:
                if path == directory:
                    return reparse_metadata
                return real_lstat(path)

            with mock.patch.object(
                Path,
                "lstat",
                autospec=True,
                side_effect=fake_lstat,
            ):
                with self.assertRaises(DatabaseBackupError) as raised:
                    database_backup_module._read_directory_identity(directory)

            self.assertEqual(raised.exception.code, "BACKUP_DIRECTORY_IDENTITY_INVALID")

    def test_origin_receipt_seals_canonical_lineage_and_verifies_expected_file_hash(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory(prefix="study-app-origin-receipt-") as temp_dir:
            root = Path(temp_dir)
            source_path = root / "app.db"
            _create_minimal_database(source_path)

            backup = create_verified_backup(source_path, root / "backups", label="origin")
            receipt_path = root / "p0-origin-receipt-v1.json"
            sealed = database_backup_module.seal_origin_receipt(
                backup.backup_path,
                backup.manifest_path,
                receipt_path,
            )
            payload = receipt_path.read_bytes()
            document = json.loads(payload.decode("utf-8"))

            expected_fields = [
                "schemaVersion",
                "manifestKind",
                "backupId",
                "backupPath",
                "backupSha256",
                "manifestPath",
                "manifestSha256",
                "logicalSha256",
                "databaseLineageId",
                "receiptPath",
                "createdAt",
                "receiptSha256",
            ]
            self.assertEqual(list(document), expected_fields)
            self.assertFalse(payload.startswith(b"\xef\xbb\xbf"))
            self.assertFalse(payload.endswith(b"\n"))
            self.assertEqual(document["schemaVersion"], 1)
            self.assertEqual(document["manifestKind"], "p0-origin")
            self.assertEqual(
                document["manifestSha256"],
                backup.verification.manifest_file_sha256,
            )
            lineage_payload = (
                '{"version":1,"originBackupId":"'
                + backup.verification.backup_id
                + '","originManifestSha256":"'
                + backup.verification.manifest_file_sha256
                + '","originLogicalSha256":"'
                + backup.verification.logical_sha256
                + '"}'
            ).encode("utf-8")
            self.assertEqual(
                document["databaseLineageId"],
                hashlib.sha256(lineage_payload).hexdigest(),
            )
            receipt_unsigned = payload.rsplit(b',"receiptSha256":', 1)[0] + b"}"
            self.assertEqual(
                document["receiptSha256"],
                hashlib.sha256(receipt_unsigned).hexdigest(),
            )
            self.assertEqual(
                sealed.origin_receipt_file_sha256,
                hashlib.sha256(payload).hexdigest(),
            )

            verified = database_backup_module.verify_origin_receipt(
                receipt_path,
                sealed.origin_receipt_file_sha256,
            )
            self.assertTrue(verified.valid)
            self.assertEqual(verified, sealed)

    def test_origin_receipt_cli_seals_fixed_path_and_verifies_out_of_band_hash(
        self,
    ) -> None:
        from backend.app.cli import database_backup as database_backup_cli

        with tempfile.TemporaryDirectory(prefix="study-app-origin-receipt-cli-") as temp_dir:
            root = Path(temp_dir)
            source_path = root / "app.db"
            _create_minimal_database(source_path)

            backup = create_verified_backup(source_path, root / "backups", label="origin-cli")
            receipt_path = root / "p0-origin-receipt-v1.json"
            with mock.patch.object(
                database_backup_cli,
                "ORIGIN_RECEIPT_PATH",
                receipt_path,
            ):
                sealed = database_backup_cli.run(
                    [
                        "seal-origin",
                        "--backup",
                        str(backup.backup_path),
                        "--manifest",
                        str(backup.manifest_path),
                    ]
                )
                verified = database_backup_cli.run(
                    [
                        "verify-origin-receipt",
                        "--receipt",
                        str(receipt_path),
                        "--expected-receipt-file-sha256",
                        sealed["originReceiptFileSha256"],
                    ]
                )

            self.assertEqual(sealed["operation"], "sealOrigin")
            self.assertEqual(sealed["receiptPath"], str(receipt_path))
            self.assertEqual(verified["operation"], "verifyOriginReceipt")
            self.assertEqual(verified["databaseLineageId"], sealed["databaseLineageId"])
            self.assertEqual(
                verified["originReceiptFileSha256"],
                sealed["originReceiptFileSha256"],
            )

    def test_origin_receipt_seal_never_overwrites_existing_fixed_receipt(self) -> None:
        with tempfile.TemporaryDirectory(prefix="study-app-origin-receipt-collision-") as temp_dir:
            root = Path(temp_dir)
            source_path = root / "app.db"
            _create_minimal_database(source_path)

            backup = create_verified_backup(source_path, root / "backups", label="origin-exists")
            receipt_path = root / "p0-origin-receipt-v1.json"
            sentinel = b"existing origin receipt sentinel"
            receipt_path.write_bytes(sentinel)

            with self.assertRaises(DatabaseBackupError) as raised:
                database_backup_module.seal_origin_receipt(
                    backup.backup_path,
                    backup.manifest_path,
                    receipt_path,
                )

            self.assertEqual(raised.exception.code, "ORIGIN_RECEIPT_EXISTS")
            self.assertEqual(receipt_path.read_bytes(), sentinel)

    def test_origin_receipt_verify_rejects_strict_schema_and_noncanonical_json(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory(prefix="study-app-origin-receipt-strict-") as temp_dir:
            root = Path(temp_dir)
            source_path = root / "app.db"
            _create_minimal_database(source_path)

            backup = create_verified_backup(source_path, root / "backups", label="origin-strict")
            receipt_path = root / "p0-origin-receipt-v1.json"
            sealed = database_backup_module.seal_origin_receipt(
                backup.backup_path,
                backup.manifest_path,
                receipt_path,
            )
            canonical = receipt_path.read_bytes()
            original = json.loads(canonical.decode("utf-8"))

            invalid_payloads: dict[str, bytes] = {}
            for name, field, value in (
                ("boolean schema", "schemaVersion", True),
                ("empty string", "backupPath", ""),
                ("nul string", "backupId", "origin\x00id"),
                ("uppercase hash", "backupSha256", "A" * 64),
            ):
                mutated = dict(original)
                mutated[field] = value
                unsigned = {
                    key: mutated[key]
                    for key in list(mutated)[:-1]
                }
                mutated["receiptSha256"] = hashlib.sha256(
                    json.dumps(
                        unsigned,
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ).encode("utf-8")
                ).hexdigest()
                invalid_payloads[name] = json.dumps(
                    mutated,
                    ensure_ascii=False,
                    separators=(",", ":"),
                ).encode("utf-8")

            invalid_payloads["duplicate key"] = canonical.replace(
                b'{"schemaVersion":1,',
                b'{"schemaVersion":1,"schemaVersion":1,',
                1,
            )
            invalid_payloads["noncanonical whitespace"] = json.dumps(
                original,
                ensure_ascii=False,
                indent=2,
            ).encode("utf-8")

            for name, payload in invalid_payloads.items():
                with self.subTest(name=name):
                    receipt_path.write_bytes(payload)
                    with self.assertRaises(DatabaseBackupError) as raised:
                        database_backup_module.verify_origin_receipt(
                            receipt_path,
                            hashlib.sha256(payload).hexdigest(),
                        )
                    self.assertEqual(raised.exception.code, "ORIGIN_RECEIPT_INVALID")

            self.assertEqual(
                sealed.origin_receipt_file_sha256,
                hashlib.sha256(canonical).hexdigest(),
            )

    def test_origin_receipt_verify_requires_out_of_band_sha_and_internal_hash(self) -> None:
        with tempfile.TemporaryDirectory(prefix="study-app-origin-receipt-tamper-") as temp_dir:
            root = Path(temp_dir)
            source_path = root / "app.db"
            _create_minimal_database(source_path)

            backup = create_verified_backup(source_path, root / "backups", label="origin-tamper")
            receipt_path = root / "p0-origin-receipt-v1.json"
            sealed = database_backup_module.seal_origin_receipt(
                backup.backup_path,
                backup.manifest_path,
                receipt_path,
            )

            with self.assertRaises(DatabaseBackupError) as wrong_file_hash:
                database_backup_module.verify_origin_receipt(
                    receipt_path,
                    "0" * 64,
                )
            self.assertEqual(
                wrong_file_hash.exception.code,
                "ORIGIN_RECEIPT_FILE_SHA_MISMATCH",
            )

            document = json.loads(receipt_path.read_text(encoding="utf-8"))
            document["receiptSha256"] = "0" * 64
            tampered = json.dumps(
                document,
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")
            receipt_path.write_bytes(tampered)
            with self.assertRaises(DatabaseBackupError) as wrong_internal_hash:
                database_backup_module.verify_origin_receipt(
                    receipt_path,
                    hashlib.sha256(tampered).hexdigest(),
                )
            self.assertEqual(
                wrong_internal_hash.exception.code,
                "ORIGIN_RECEIPT_INVALID",
            )
            self.assertNotEqual(
                hashlib.sha256(tampered).hexdigest(),
                sealed.origin_receipt_file_sha256,
            )

    def test_verify_origin_receipt_rejects_receipt_backup_or_manifest_drift(self) -> None:
        with tempfile.TemporaryDirectory(prefix="study-app-origin-receipt-drift-") as temp_dir:
            root = Path(temp_dir)
            first_source = root / "first.db"
            second_source = root / "second.db"
            _create_minimal_database(first_source)
            _create_minimal_database(second_source)
            connection = sqlite3.connect(second_source)
            try:
                connection.execute("UPDATE papers SET id = 'paper-2'")
                connection.commit()
            finally:
                connection.close()

            first = create_verified_backup(first_source, root / "first-backups", label="origin")
            second = create_verified_backup(second_source, root / "second-backups", label="origin")
            receipt_path = root / "p0-origin-receipt-v1.json"
            sealed = database_backup_module.seal_origin_receipt(
                first.backup_path,
                first.manifest_path,
                receipt_path,
            )
            original_receipt = receipt_path.read_bytes()
            original_backup = first.backup_path.read_bytes()
            original_manifest = first.manifest_path.read_bytes()

            swapped = json.loads(original_receipt.decode("utf-8"))
            swapped["backupPath"] = str(second.backup_path)
            swapped["manifestPath"] = str(second.manifest_path)
            unsigned = {key: swapped[key] for key in list(swapped)[:-1]}
            swapped["receiptSha256"] = hashlib.sha256(
                json.dumps(
                    unsigned,
                    ensure_ascii=False,
                    separators=(",", ":"),
                ).encode("utf-8")
            ).hexdigest()
            swapped_payload = json.dumps(
                swapped,
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")
            receipt_path.write_bytes(swapped_payload)
            second_before = (
                _file_evidence(second.backup_path),
                _file_evidence(second.manifest_path),
            )
            with self.assertRaises(DatabaseBackupError) as swapped_error:
                database_backup_module.verify_origin_receipt(
                    receipt_path,
                    hashlib.sha256(swapped_payload).hexdigest(),
                )
            self.assertEqual(
                swapped_error.exception.code,
                "ORIGIN_RECEIPT_REFERENCE_INVALID",
            )
            second_after = (
                _file_evidence(second.backup_path),
                _file_evidence(second.manifest_path),
            )
            self.assertEqual(second_after, second_before)

            receipt_path.write_bytes(original_receipt)
            first.backup_path.write_bytes(original_backup + b"\x00")
            backup_before = _file_evidence(first.backup_path)
            with self.assertRaises(DatabaseBackupError) as backup_error:
                database_backup_module.verify_origin_receipt(
                    receipt_path,
                    sealed.origin_receipt_file_sha256,
                )
            self.assertEqual(
                backup_error.exception.code,
                "ORIGIN_RECEIPT_REFERENCE_INVALID",
            )
            self.assertEqual(_file_evidence(first.backup_path), backup_before)

            first.backup_path.write_bytes(original_backup)
            first.manifest_path.write_bytes(original_manifest + b" ")
            manifest_before = _file_evidence(first.manifest_path)
            with self.assertRaises(DatabaseBackupError) as manifest_error:
                database_backup_module.verify_origin_receipt(
                    receipt_path,
                    sealed.origin_receipt_file_sha256,
                )
            self.assertEqual(
                manifest_error.exception.code,
                "ORIGIN_RECEIPT_REFERENCE_INVALID",
            )
            self.assertEqual(_file_evidence(first.manifest_path), manifest_before)

    def test_inspect_reports_existing_fingerprint_without_writing_database(self) -> None:
        from backend.app.infrastructure.database_backup import inspect_database

        with tempfile.TemporaryDirectory(prefix="study-app-inspect-") as temp_dir:
            database_path = Path(temp_dir) / "app.db"
            with closing(sqlite3.connect(database_path)) as connection:
                connection.execute("CREATE TABLE papers(id TEXT PRIMARY KEY, explainer TEXT)")
                connection.execute("CREATE TABLE schema_migrations(version INTEGER PRIMARY KEY)")
                connection.execute("INSERT INTO papers VALUES('paper-1','# content')")
                connection.execute("INSERT INTO schema_migrations VALUES(7)")
                connection.commit()
            before = _file_evidence(database_path)
            sidecars_before = {
                path.name: path.read_bytes()
                for path in database_path.parent.iterdir()
                if path.name.startswith(f"{database_path.name}-")
            }
            report = inspect_database(database_path)
            self.assertEqual("ok", report.quick_check)
            self.assertEqual("ok", report.integrity_check)
            self.assertEqual(0, report.foreign_key_violations)
            self.assertEqual(1, report.table_counts["papers"])
            self.assertEqual((7,), report.legacy_schema_migrations)
            self.assertIsNone(report.alembic_version)
            self.assertEqual(64, len(report.schema_sha256))
            self.assertEqual(64, len(report.logical_sha256))
            self.assertGreater(report.page_size, 0)
            self.assertGreater(report.page_count, 0)
            self.assertEqual(before, _file_evidence(database_path))
            self.assertEqual(
                sidecars_before,
                {
                    path.name: path.read_bytes()
                    for path in database_path.parent.iterdir()
                    if path.name.startswith(f"{database_path.name}-")
                },
            )

    def test_inspect_cli_emits_json_and_preserves_file_metadata(self) -> None:
        from contextlib import redirect_stderr, redirect_stdout
        import io
        from backend.app.cli import database_backup as cli

        with tempfile.TemporaryDirectory(prefix="study-app-inspect-cli-") as temp_dir:
            database_path = Path(temp_dir) / "app.db"
            with closing(sqlite3.connect(database_path)) as connection:
                connection.execute("CREATE TABLE papers(id TEXT PRIMARY KEY)")
                connection.execute("INSERT INTO papers VALUES('paper-1')")
                connection.commit()
            before = _file_evidence(database_path)
            stdout, stderr = io.StringIO(), io.StringIO()
            with redirect_stdout(stdout), redirect_stderr(stderr):
                exit_code = cli.main(["inspect", "--database", str(database_path)])
            self.assertEqual(0, exit_code)
            self.assertEqual("", stderr.getvalue())
            payload = json.loads(stdout.getvalue())
            self.assertTrue(payload["ok"])
            self.assertEqual("inspect", payload["operation"])
            for field in (
                "quickCheck", "integrityCheck", "foreignKeyViolations",
                "schemaSha256", "logicalSha256", "tableCounts", "tableSha256",
                "contentCounts", "contentSha256", "legacySchemaMigrations",
                "alembicVersion", "pageSize", "pageCount", "freelistCount",
                "schemaVersion", "userVersion", "applicationId",
            ):
                self.assertIn(field, payload)
            self.assertEqual(before, _file_evidence(database_path))

    def _run_cli(self, *arguments: str) -> subprocess.CompletedProcess[str]:
        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "backend.app.cli.database_backup",
                *arguments,
            ],
            cwd=Path(__file__).resolve().parents[2],
            capture_output=True,
            text=True,
            encoding="utf-8",
            env={
                **os.environ,
                "PYTHONUTF8": "1",
                "PYTHONIOENCODING": "utf-8",
            },
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stderr, "")
        return completed


if __name__ == "__main__":
    unittest.main()
