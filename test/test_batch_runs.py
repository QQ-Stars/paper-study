import io
import json
import sqlite3
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from agent import db, explain, ocrmd


class BatchRunPersistenceTest(unittest.TestCase):
    def test_recorded_batch_run_is_retrievable_as_latest_for_its_kind(self):
        connection = sqlite3.connect(":memory:")
        connection.row_factory = sqlite3.Row
        self.addCleanup(connection.close)

        db.record_batch_run(
            connection,
            "ocr",
            total=99,
            done=1,
            failed=98,
            skipped=0,
            detail={"note": "older"},
        )
        db.record_batch_run(
            connection,
            "ocr",
            total=3,
            done=2,
            failed=1,
            skipped=4,
            detail={"failed": ["paper-2"], "note": "可重试"},
        )
        db.record_batch_run(
            connection,
            "explain",
            total=7,
            done=7,
            failed=0,
            skipped=0,
            detail={"note": "newer, but another kind"},
        )

        latest = db.last_batch_run(connection, "ocr")

        self.assertIsNotNone(latest)
        self.assertEqual(
            {
                "kind": latest["kind"],
                "total": latest["total"],
                "done": latest["done"],
                "failed": latest["failed"],
                "skipped": latest["skipped"],
                "detail": latest["detail"],
            },
            {
                "kind": "ocr",
                "total": 3,
                "done": 2,
                "failed": 1,
                "skipped": 4,
                "detail": {"failed": ["paper-2"], "note": "可重试"},
            },
        )
        self.assertIsInstance(latest["id"], int)
        self.assertTrue(latest["finished_at"])

    def test_empty_ocr_batch_preserves_summary_and_records_the_run(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            database_path = Path(temporary_directory) / "app.db"
            connection = sqlite3.connect(database_path)
            connection.execute(
                """CREATE TABLE papers (
                    id TEXT PRIMARY KEY,
                    title TEXT,
                    created_at TEXT,
                    explainer TEXT,
                    pdf_path TEXT)"""
            )
            connection.commit()
            connection.close()

            with mock.patch.object(db.config, "DB_PATH", database_path):
                with (
                    redirect_stdout(io.StringIO()) as stdout,
                    redirect_stderr(io.StringIO()),
                ):
                    result = ocrmd.ocr_batch()

                expected = {
                    "ok": True,
                    "total": 0,
                    "done": 0,
                    "failed": [],
                    "skipped_no_pdf": [],
                }
                self.assertEqual(result, expected)
                self.assertEqual(json.loads(stdout.getvalue()), expected)

                connection = db.connect()
                latest = db.last_batch_run(connection, "ocr")
                connection.close()
                self.assertEqual(
                    {
                        "total": latest["total"],
                        "done": latest["done"],
                        "failed": latest["failed"],
                        "skipped": latest["skipped"],
                        "detail": latest["detail"],
                    },
                    {
                        "total": 0,
                        "done": 0,
                        "failed": 0,
                        "skipped": 0,
                        "detail": expected,
                    },
                )

    def test_empty_explain_batch_preserves_summary_and_records_the_run(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            database_path = Path(temporary_directory) / "app.db"
            connection = sqlite3.connect(database_path)
            connection.execute(
                """CREATE TABLE papers (
                    id TEXT PRIMARY KEY,
                    title TEXT,
                    created_at TEXT,
                    explainer TEXT,
                    pdf_path TEXT)"""
            )
            connection.commit()
            connection.close()

            with mock.patch.object(db.config, "DB_PATH", database_path):
                with (
                    redirect_stdout(io.StringIO()) as stdout,
                    redirect_stderr(io.StringIO()),
                ):
                    result = explain.explain_batch()

                expected = {
                    "ok": True,
                    "total": 0,
                    "done": 0,
                    "failed": [],
                    "skipped_no_pdf": [],
                }
                self.assertEqual(result, expected)
                self.assertEqual(json.loads(stdout.getvalue()), expected)

                connection = db.connect()
                latest = db.last_batch_run(connection, "explain")
                connection.close()
                self.assertEqual(
                    {
                        "total": latest["total"],
                        "done": latest["done"],
                        "failed": latest["failed"],
                        "skipped": latest["skipped"],
                        "detail": latest["detail"],
                    },
                    {
                        "total": 0,
                        "done": 0,
                        "failed": 0,
                        "skipped": 0,
                        "detail": expected,
                    },
                )


if __name__ == "__main__":
    unittest.main()
