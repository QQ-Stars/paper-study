import sqlite3
import tempfile
import unittest
from pathlib import Path

from agent import config, db, mcp_server


PAPER_COLUMNS = """
    id, title, venue, year, type, topic, relevance, citations, tldr,
    abstract, explainer, pdf_path, authors, doi, arxiv_id, url, task,
    models, datasets, contribution, tags, s2_fields
"""


class McpServerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "app.db"
        self.old_db_path = config.DB_PATH
        config.DB_PATH = str(self.db_path)
        self._create_db()

    def tearDown(self):
        config.DB_PATH = self.old_db_path
        self.tmp.cleanup()

    def _create_db(self):
        con = sqlite3.connect(self.db_path)
        con.executescript(
            """
            CREATE TABLE papers (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                venue TEXT,
                year TEXT,
                type TEXT,
                topic TEXT,
                relevance REAL,
                citations INTEGER,
                tldr TEXT,
                abstract TEXT,
                explainer TEXT,
                pdf_path TEXT,
                authors TEXT,
                doi TEXT,
                arxiv_id TEXT,
                url TEXT,
                task TEXT,
                models TEXT,
                datasets TEXT,
                contribution TEXT,
                tags TEXT,
                s2_fields TEXT,
                created_at TEXT DEFAULT '2026-07-01',
                updated_at TEXT DEFAULT '2026-07-01'
            );
            CREATE TABLE notes (paper_id TEXT PRIMARY KEY, content TEXT);
            CREATE TABLE progress (paper_id TEXT PRIMARY KEY, status TEXT, updated_at TEXT);
            CREATE TABLE favorites (paper_id TEXT PRIMARY KEY, created_at TEXT);
            CREATE TABLE translations (paper_id TEXT PRIMARY KEY, content TEXT, updated_at TEXT);
            CREATE TABLE paper_vectors (paper_id TEXT PRIMARY KEY, dim INTEGER, vector BLOB);
            CREATE TABLE paper_reviews (
                paper_id TEXT PRIMARY KEY,
                started_at TEXT NOT NULL,
                current_step INTEGER NOT NULL DEFAULT 1,
                completed_steps INTEGER NOT NULL DEFAULT 0,
                next_due_at TEXT NOT NULL,
                completed_at TEXT,
                updated_at TEXT NOT NULL
            );
            """
        )
        long_explainer = "E" * 40
        long_translation = "T" * 35
        con.execute(
            f"INSERT INTO papers({PAPER_COLUMNS}) VALUES ({','.join(['?'] * 22)})",
            (
                "p1",
                "Reviewable Paper",
                "ACL",
                "2026",
                "评测",
                "hallucination",
                0.95,
                12,
                "short summary",
                "abstract",
                long_explainer,
                None,
                '["Alice"]',
                None,
                "2601.00001",
                "https://example.test/p1",
                "task",
                '["m1"]',
                '["d1"]',
                "contribution",
                '["tag"]',
                '["cs.CL"]',
            ),
        )
        con.execute(
            "INSERT INTO translations(paper_id, content, updated_at) VALUES('p1', ?, '2026-07-01')",
            (long_translation,),
        )
        con.execute(
            """
            INSERT INTO paper_reviews(
                paper_id, started_at, current_step, completed_steps, next_due_at, completed_at, updated_at
            ) VALUES('p1', '2026-07-01', 2, 1, '2026-07-02', NULL, '2026-07-01')
            """
        )
        con.execute("INSERT INTO progress(paper_id, status, updated_at) VALUES('p1', '学习中', '2026-07-01')")
        for i in range(60):
            pid = f"bulk-{i:02d}"
            con.execute(
                f"INSERT INTO papers({PAPER_COLUMNS}) VALUES ({','.join(['?'] * 22)})",
                (
                    pid,
                    f"Bulk Paper {i:02d}",
                    "ACL",
                    "2025",
                    "评测",
                    "bulk",
                    0.5,
                    i,
                    "",
                    "",
                    "",
                    None,
                    "[]",
                    None,
                    None,
                    None,
                    "",
                    "[]",
                    "[]",
                    "",
                    "[]",
                    "[]",
                ),
            )
        con.commit()
        con.close()

    def test_readonly_connection_rejects_writes(self):
        con = db.connect_readonly()
        try:
            with self.assertRaises(sqlite3.OperationalError):
                con.execute("INSERT INTO papers(id, title) VALUES('write-test', 'Write Test')")
        finally:
            con.close()

    def test_search_papers_has_consistent_shape_and_caps_limit(self):
        result = mcp_server.search_papers(query="", sort="citations", limit=999)

        self.assertTrue(result["ok"])
        self.assertLessEqual(result["count"], 50)
        self.assertLessEqual(len(result["results"]), 50)

    def test_get_paper_missing_uses_consistent_error_shape(self):
        result = mcp_server.get_paper("missing")

        self.assertFalse(result["ok"])
        self.assertIn("未找到论文", result["error"])

    def test_get_explainer_returns_chunk_metadata(self):
        result = mcp_server.get_explainer("p1", max_chars=10)

        self.assertTrue(result["ok"])
        self.assertEqual(result["content"], "E" * 10)
        self.assertEqual(result["total_chars"], 40)
        self.assertEqual(result["offset"], 0)
        self.assertEqual(result["next_offset"], 10)
        self.assertTrue(result["truncated"])

    def test_get_translation_returns_chunk_metadata_with_offset(self):
        result = mcp_server.get_translation("p1", offset=30, max_chars=10)

        self.assertTrue(result["ok"])
        self.assertEqual(result["content"], "T" * 5)
        self.assertEqual(result["total_chars"], 35)
        self.assertEqual(result["offset"], 30)
        self.assertIsNone(result["next_offset"])
        self.assertFalse(result["truncated"])

    def test_list_due_reviews_exposes_readonly_review_queue(self):
        result = mcp_server.list_due_reviews(today="2026-07-02")

        self.assertTrue(result["ok"])
        self.assertEqual(result["today"], "2026-07-02")
        self.assertEqual(result["count"], 1)
        self.assertEqual(result["results"][0]["id"], "p1")
        self.assertEqual(result["results"][0]["review_state"], "dueToday")
        self.assertEqual(result["results"][0]["current_step"], 2)
        self.assertEqual(result["results"][0]["completed_steps"], 1)


if __name__ == "__main__":
    unittest.main()
