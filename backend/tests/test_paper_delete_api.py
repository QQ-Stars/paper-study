"""Regression tests for POST /api/delete and the FTS runtime repair.

Covers the HTTP 500 root cause (FTS5 trigram tokenizer stored by a newer
SQLite runtime than the one serving requests) and the delete contract:
structured 400/404 errors, cascade cleanup, reproduction SET NULL, and
best-effort local PDF cleanup.
"""

from __future__ import annotations

from contextlib import closing
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient

from backend.app.api.app import create_app
from backend.app.api.dependencies import ApiDependencies
from backend.app.application.library_queries import LibraryQueries
from backend.app.application.paper_library import PaperLibrary
from backend.app.config import DatabaseSettings
from backend.app.infrastructure import fts_runtime
from backend.app.infrastructure.database import create_async_session_factory
from backend.app.providers.pdf_files import PdfFiles
from backend.app.repositories.unit_of_work import SqlAlchemyUnitOfWork
from backend.app.runtime import ApiSettings
from backend.tests.support.p1_database import create_legacy_database, run_alembic


REVISION = "20260826_01"
NOW = "2026-08-27T09:00:00Z"
SHA_A = "a" * 64
SHA_B = "b" * 64


def _seed_p3_and_reproduction(database_path: Path) -> None:
    with closing(sqlite3.connect(database_path)) as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute(
            "INSERT INTO document_sources("
            "id,paper_id,mode,status,provider,model,pdf_sha256,options_hash,"
            "content_sha256,markdown,page_count,processing_version,error_code,"
            "error_message,created_at,updated_at,source_key,ready_at,stale_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "src-del-1", "paper-1", "native", "ready", "local",
                "pymupdf4llm-pymupdf", SHA_A, SHA_B, SHA_A, "# markdown", 1,
                "v1", None, None, NOW, NOW, SHA_B, NOW, None,
            ),
        )
        connection.execute(
            "INSERT INTO document_chunks("
            "id,source_document_id,sequence,heading_path,page_start,page_end,"
            "content,content_sha256,token_count,status,content_kind,chunk_key,"
            "chunking_version,source_content_sha256,char_start,char_end,"
            "created_at,updated_at,stale_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "chunk-del-1", "src-del-1", 1, None, 1, 1, "deterministic chunk",
                SHA_A, 3, "ready", "text", "chunk-del-1", "markdown-coverage-v1",
                SHA_A, 0, 19, NOW, NOW, None,
            ),
        )
        connection.execute(
            "INSERT INTO reproduction_projects(id,paper_id,paper_title,name) "
            "VALUES('rp-del-1','paper-1','Seed One','delete repro')"
        )
        connection.execute(
            "INSERT INTO reproduction_notes(id,project_id,content) "
            "VALUES('rn-del-1','rp-del-1','keep me')"
        )
        connection.commit()


class _DeleteFixture:
    def __init__(self) -> None:
        self._temp = tempfile.TemporaryDirectory(prefix="study-app-delete-api-")
        root = Path(self._temp.name)
        self.root = root
        self.database_path = root / "database" / "app.db"
        create_legacy_database(self.database_path)
        run_alembic(self.database_path, REVISION)
        self.pdf_root = root / "pdfs"
        self.pdf_root.mkdir()
        (self.pdf_root / "paper-1.pdf").write_bytes(b"%PDF-1.4 fixture")
        _seed_p3_and_reproduction(self.database_path)
        session_factory = create_async_session_factory(
            DatabaseSettings(self.database_path)
        )
        pdf_files = PdfFiles(root=root, default_directory=self.pdf_root)
        work_factory = lambda: SqlAlchemyUnitOfWork(session_factory)
        container = SimpleNamespace(
            schema_revision=REVISION,
            session_factory=session_factory,
            legacy=SimpleNamespace(
                paper_library=PaperLibrary(work_factory, pdf_files=pdf_files),
                library_queries=LibraryQueries(work_factory, pdf_files=pdf_files),
            ),
        )

        async def dispose() -> None:
            await session_factory.kw["bind"].dispose()

        container.dispose = dispose
        self.client = TestClient(
            create_app(
                ApiSettings.for_tests(),
                ApiDependencies(container, session_factory),
                required_schema_revision=REVISION,
            ),
            raise_server_exceptions=False,
        )

    def close(self) -> None:
        self.client.close()
        self._temp.cleanup()


class PaperDeleteApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = _DeleteFixture()

    def tearDown(self) -> None:
        self.fixture.close()

    def _count(self, sql: str, params: tuple = ()) -> int:
        with closing(sqlite3.connect(self.fixture.database_path)) as connection:
            return int(connection.execute(sql, params).fetchone()[0])

    def test_delete_success_cascades_cleans_file_and_sets_null(self) -> None:
        with self.fixture.client as client:
            response = client.post("/api/delete", json={"id": "paper-1"})
        self.assertEqual(200, response.status_code)
        self.assertEqual({"ok": True}, response.json())
        database = self.fixture.database_path
        self.assertEqual(0, self._count("SELECT count(*) FROM papers WHERE id='paper-1'"))
        self.assertEqual(0, self._count("SELECT count(*) FROM progress WHERE paper_id='paper-1'"))
        self.assertEqual(0, self._count("SELECT count(*) FROM document_sources WHERE paper_id='paper-1'"))
        self.assertEqual(0, self._count("SELECT count(*) FROM document_chunks WHERE id='chunk-del-1'"))
        self.assertEqual(0, self._count("SELECT count(*) FROM cite_edges WHERE src_id='paper-1' OR dst_id='paper-1'"))
        with closing(sqlite3.connect(database)) as connection:
            project = connection.execute(
                "SELECT paper_id FROM reproduction_projects WHERE id='rp-del-1'"
            ).fetchone()
            notes = connection.execute(
                "SELECT count(*) FROM reproduction_notes WHERE id='rn-del-1'"
            ).fetchone()[0]
        self.assertEqual((None,), tuple(project))
        self.assertEqual(1, notes)
        self.assertFalse((self.fixture.pdf_root / "paper-1.pdf").exists())

    def test_delete_missing_paper_returns_structured_404(self) -> None:
        with self.fixture.client as client:
            response = client.post("/api/delete", json={"id": "no-such-paper"})
        self.assertEqual(404, response.status_code)
        body = response.json()
        self.assertFalse(body["ok"])
        self.assertEqual("PAPER_NOT_FOUND", body["code"])

    def test_repeated_delete_is_404_not_500(self) -> None:
        with self.fixture.client as client:
            first = client.post("/api/delete", json={"id": "paper-1"})
            second = client.post("/api/delete", json={"id": "paper-1"})
        self.assertEqual(200, first.status_code)
        self.assertEqual(404, second.status_code)
        self.assertEqual("PAPER_NOT_FOUND", second.json()["code"])

    def test_delete_without_id_returns_400(self) -> None:
        with self.fixture.client as client:
            response = client.post("/api/delete", json={})
        self.assertEqual(400, response.status_code)
        self.assertFalse(response.json()["ok"])

    def test_repair_is_noop_when_tokenizer_supported(self) -> None:
        self.assertFalse(fts_runtime.ensure_fts_runtime(self.fixture.database_path))

    def test_repaired_runtime_allows_delete(self) -> None:
        database = self.fixture.database_path
        with closing(sqlite3.connect(database)) as connection:
            stored = fts_runtime.stored_tokenizer(connection)
            broken = next(
                (
                    candidate
                    for candidate in (
                        "trigram case_sensitive 0 remove_diacritics 1",
                        "tokenizer_that_does_not_exist",
                    )
                    if not fts_runtime._probe(connection, candidate)
                ),
                None,
            )
        self.assertIsNotNone(broken)
        if broken != stored:
            with closing(sqlite3.connect(database)) as connection:
                current = connection.execute(
                    "SELECT sql FROM sqlite_master WHERE type='table' "
                    "AND name='document_chunks_fts'"
                ).fetchone()[0]
                connection.execute("PRAGMA writable_schema=ON")
                connection.execute(
                    "UPDATE sqlite_master SET sql=? WHERE type='table' "
                    "AND name='document_chunks_fts'",
                    (current.replace(stored, broken),),
                )
                connection.execute("PRAGMA writable_schema=OFF")
                connection.commit()

        # Red: the mismatched tokenizer aborts cascade deletes.
        with closing(sqlite3.connect(database)) as connection:
            connection.execute("PRAGMA foreign_keys=ON")
            with self.assertRaises(sqlite3.OperationalError):
                connection.execute("DELETE FROM papers WHERE id='paper-1'")

        self.assertTrue(fts_runtime.ensure_fts_runtime(database))

        with self.fixture.client as client:
            response = client.post("/api/delete", json={"id": "paper-1"})
        self.assertEqual(200, response.status_code)
        self.assertEqual({"ok": True}, response.json())
        self.assertEqual(0, self._count("SELECT count(*) FROM papers WHERE id='paper-1'"))
        with closing(sqlite3.connect(database)) as connection:
            repaired_sql = connection.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' "
                "AND name='document_chunks_fts'"
            ).fetchone()[0]
            triggers = {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='trigger' "
                    "AND name LIKE 'document_chunks_fts_%'"
                ).fetchall()
            }
        repaired_tokenizer = fts_runtime._TOKENIZE_OPTION.search(repaired_sql)
        self.assertIn(
            repaired_tokenizer.group(1) if repaired_tokenizer else "",
            fts_runtime.SANCTIONED_TOKENIZERS,
        )
        self.assertEqual(set(fts_runtime._TRIGGER_NAMES), triggers)


if __name__ == "__main__":
    unittest.main()
