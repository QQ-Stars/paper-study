import io
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock
from urllib.parse import unquote

from agent import db, enrich


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


class Response:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload
        self.headers = {}

    def raise_for_status(self):
        if self.status_code >= 400:
            request = mock.Mock()
            response = mock.Mock(status_code=self.status_code)
            raise enrich.httpx.HTTPStatusError(
                f"HTTP {self.status_code}", request=request, response=response
            )

    def json(self):
        return self._payload


class MetadataEnrichmentTest(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tempdir.name) / "enrich.db"
        connection = sqlite3.connect(self.db_path)
        connection.executescript(
            """
            CREATE TABLE papers (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                arxiv_id TEXT,
                doi TEXT,
                year TEXT,
                venue TEXT
            );
            INSERT INTO papers(id, title, arxiv_id, doi, year, venue)
            VALUES(
                'paper-1',
                'A Paper Needing Metadata',
                '2301.00001',
                '10.1000/example',
                NULL,
                'ICLR'
            );
            """
        )
        connection.commit()
        connection.close()

    def tearDown(self):
        self.tempdir.cleanup()

    def test_run_prefers_arxiv_and_persists_only_missing_metadata_and_authors(self):
        calls = []

        def get(url, **kwargs):
            calls.append((url, kwargs))
            return Response(
                200,
                {
                    "paperId": "S2-1",
                    "year": 2024,
                    "venue": "CVPR",
                    "authors": [{"name": "Alice"}, {"name": "Bob"}],
                },
            )

        stdout = io.StringIO()
        stderr = io.StringIO()
        with (
            mock.patch.object(db.config, "DB_PATH", str(self.db_path)),
            mock.patch.object(enrich.httpx, "get", side_effect=get),
            redirect_stdout(stdout),
            redirect_stderr(stderr),
        ):
            result = enrich.run(limit=1)

        self.assertEqual(
            result,
            {"ok": True, "total": 1, "done": 1, "failed": [], "skipped": []},
        )
        self.assertEqual(json.loads(stdout.getvalue()), result)
        self.assertIn("ITEM::1::1::done::paper-1", stderr.getvalue())
        self.assertEqual(len(calls), 1)
        self.assertTrue(unquote(calls[0][0]).endswith("/ARXIV:2301.00001"))

        connection = sqlite3.connect(self.db_path)
        paper = connection.execute(
            "SELECT year, venue FROM papers WHERE id='paper-1'"
        ).fetchone()
        author_row = connection.execute(
            "SELECT authors, updated_at FROM paper_authors WHERE paper_id='paper-1'"
        ).fetchone()
        connection.close()

        self.assertEqual(paper, ("2024", "ICLR"))
        self.assertEqual(json.loads(author_row[0]), ["Alice", "Bob"])
        self.assertTrue(author_row[1])

    def test_run_tries_doi_after_arxiv_then_falls_back_to_title_search(self):
        connection = sqlite3.connect(self.db_path)
        connection.execute("DELETE FROM papers")
        connection.executemany(
            "INSERT INTO papers(id, title, arxiv_id, doi, year, venue) VALUES(?, ?, ?, ?, ?, ?)",
            [
                (
                    "paper-both",
                    "Identifier Paper",
                    "2401.01234",
                    "10.5555/identifier",
                    None,
                    None,
                ),
                ("paper-title", "Fallback Paper", None, None, None, None),
            ],
        )
        connection.commit()
        connection.close()

        calls = []

        def get(url, **kwargs):
            decoded = unquote(url)
            calls.append((decoded, kwargs.get("params") or {}))
            if decoded.endswith("/ARXIV:2401.01234"):
                return Response(404, {})
            if decoded.endswith("/DOI:10.5555/identifier"):
                return Response(
                    200,
                    {
                        "title": "Identifier Paper",
                        "year": 2023,
                        "venue": "NeurIPS",
                        "authors": [{"name": "Ida"}],
                    },
                )
            return Response(
                200,
                {
                    "data": [
                        {
                            "title": "Fallback Paper",
                            "year": 2022,
                            "venue": "ACL",
                            "authors": [{"name": "Terry"}],
                        }
                    ]
                },
            )

        with (
            mock.patch.object(db.config, "DB_PATH", str(self.db_path)),
            mock.patch.object(enrich.httpx, "get", side_effect=get),
            redirect_stdout(io.StringIO()),
            redirect_stderr(io.StringIO()),
        ):
            result = enrich.run(limit=2)

        self.assertEqual(result["done"], 2)
        self.assertTrue(calls[0][0].endswith("/ARXIV:2401.01234"))
        self.assertTrue(calls[1][0].endswith("/DOI:10.5555/identifier"))
        self.assertTrue(calls[2][0].endswith("/paper/search"))
        self.assertEqual(calls[2][1]["query"], "Fallback Paper")

        connection = sqlite3.connect(self.db_path)
        papers = connection.execute(
            "SELECT id, year, venue FROM papers ORDER BY id"
        ).fetchall()
        authors = connection.execute(
            "SELECT paper_id, authors FROM paper_authors ORDER BY paper_id"
        ).fetchall()
        connection.close()
        self.assertEqual(
            papers,
            [
                ("paper-both", "2023", "NeurIPS"),
                ("paper-title", "2022", "ACL"),
            ],
        )
        self.assertEqual(
            [(paper_id, json.loads(value)) for paper_id, value in authors],
            [("paper-both", ["Ida"]), ("paper-title", ["Terry"])],
        )

    def test_title_fallback_uses_the_exact_normalized_match_not_the_first_hit(self):
        connection = sqlite3.connect(self.db_path)
        connection.execute(
            "UPDATE papers SET arxiv_id=NULL, doi=NULL WHERE id='paper-1'"
        )
        connection.commit()
        connection.close()

        response = Response(
            200,
            {
                "data": [
                    {
                        "title": "A Different Paper",
                        "year": 1999,
                        "venue": "Wrong Venue",
                        "authors": [{"name": "Wrong Author"}],
                    },
                    {
                        "title": "A Paper: Needing Metadata",
                        "year": 2025,
                        "venue": "ICML",
                        "authors": [{"name": "Correct Author"}],
                    },
                ]
            },
        )
        with (
            mock.patch.object(db.config, "DB_PATH", str(self.db_path)),
            mock.patch.object(enrich.httpx, "get", return_value=response),
            redirect_stdout(io.StringIO()),
            redirect_stderr(io.StringIO()),
        ):
            enrich.run(limit=1)

        connection = sqlite3.connect(self.db_path)
        year = connection.execute(
            "SELECT year FROM papers WHERE id='paper-1'"
        ).fetchone()[0]
        authors = json.loads(
            connection.execute(
                "SELECT authors FROM paper_authors WHERE paper_id='paper-1'"
            ).fetchone()[0]
        )
        connection.close()
        self.assertEqual(year, "2025")
        self.assertEqual(authors, ["Correct Author"])

    def test_run_retries_a_semantic_scholar_rate_limit(self):
        responses = [
            Response(429, {}),
            Response(
                200,
                {
                    "title": "A Paper Needing Metadata",
                    "year": 2024,
                    "venue": "ICLR",
                    "authors": [{"name": "Alice"}],
                },
            ),
        ]
        with (
            mock.patch.object(db.config, "DB_PATH", str(self.db_path)),
            mock.patch.object(enrich.httpx, "get", side_effect=responses) as get,
            mock.patch.object(enrich.time, "sleep") as sleep,
            redirect_stdout(io.StringIO()),
            redirect_stderr(io.StringIO()),
        ):
            result = enrich.run(limit=1)

        self.assertEqual(result["done"], 1)
        self.assertEqual(get.call_count, 2)
        sleep.assert_called_once_with(2.0)

    def test_run_skips_a_match_that_has_no_writable_metadata_or_authors(self):
        connection = sqlite3.connect(self.db_path)
        connection.execute("UPDATE papers SET year='2024' WHERE id='paper-1'")
        connection.commit()
        connection.close()

        response = Response(
            200,
            {
                "title": "A Paper Needing Metadata",
                "year": 2024,
                "venue": "ICLR",
                "authors": [],
            },
        )
        stderr = io.StringIO()
        with (
            mock.patch.object(db.config, "DB_PATH", str(self.db_path)),
            mock.patch.object(enrich.httpx, "get", return_value=response),
            redirect_stdout(io.StringIO()),
            redirect_stderr(stderr),
        ):
            result = enrich.run(limit=1)

        self.assertEqual(
            result,
            {
                "ok": True,
                "total": 1,
                "done": 0,
                "failed": [],
                "skipped": ["paper-1"],
            },
        )
        self.assertIn("ITEM::1::1::skip::paper-1::无可写元数据", stderr.getvalue())
        connection = sqlite3.connect(self.db_path)
        author_row = connection.execute(
            "SELECT 1 FROM paper_authors WHERE paper_id='paper-1'"
        ).fetchone()
        connection.close()
        self.assertIsNone(author_row)

    def test_enrich_cli_emits_json_when_nothing_is_pending(self):
        connection = sqlite3.connect(self.db_path)
        connection.executescript(
            """
            UPDATE papers SET year='2024' WHERE id='paper-1';
            CREATE TABLE paper_authors (
                paper_id TEXT PRIMARY KEY REFERENCES papers(id) ON DELETE CASCADE,
                authors TEXT NOT NULL DEFAULT '[]',
                updated_at TEXT DEFAULT (datetime('now'))
            );
            INSERT INTO paper_authors(paper_id, authors)
            VALUES('paper-1', '["Alice"]');
            """
        )
        connection.commit()
        connection.close()

        completed = subprocess.run(
            [sys.executable, "-m", "agent", "enrich", "--limit", "1"],
            cwd=REPOSITORY_ROOT,
            env={
                **os.environ,
                "DB_PATH": str(self.db_path),
                "PYTHONIOENCODING": "utf-8",
            },
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(
            json.loads(completed.stdout),
            {"ok": True, "total": 0, "done": 0, "failed": [], "skipped": []},
        )


if __name__ == "__main__":
    unittest.main()
