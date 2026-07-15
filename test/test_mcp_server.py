import asyncio
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent import config, db, mcp_server


PAPER_COLUMNS = """
    id, title, title_zh, venue, year, type, topic, relevance, citations, tldr,
    abstract, explainer, pdf_path, authors, doi, arxiv_id, url, task,
    models, datasets, contribution, tags, s2_fields
"""


def _normalize_nullable_scalar_schema(schema):
    normalized = dict(schema)
    schema_type = normalized.get("type")
    if isinstance(schema_type, list):
        non_null_types = [item for item in schema_type if item != "null"]
        if len(schema_type) == 2 and len(non_null_types) == 1 and "null" in schema_type:
            normalized["type"] = non_null_types[0]
        return normalized

    variants = normalized.pop("anyOf", None)
    if variants is None:
        return normalized
    non_null_variants = [item for item in variants if item.get("type") != "null"]
    null_variants = [item for item in variants if item.get("type") == "null"]
    if len(variants) != 2 or len(non_null_variants) != 1 or len(null_variants) != 1:
        normalized["anyOf"] = variants
        return normalized
    return {**non_null_variants[0], **normalized}


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
                title_zh TEXT,
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
            f"INSERT INTO papers({PAPER_COLUMNS}) VALUES ({','.join(['?'] * 23)})",
            (
                "p1",
                "Reviewable Paper",
                "可复习论文",
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
                f"INSERT INTO papers({PAPER_COLUMNS}) VALUES ({','.join(['?'] * 23)})",
                (
                    pid,
                    f"Bulk Paper {i:02d}",
                    None,
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

    def test_search_papers_limit_is_clamped_at_both_boundaries(self):
        for boundary, requested, expected in (("below", 0, 1), ("above", 999, 50)):
            with self.subTest(boundary=boundary):
                result = mcp_server.search_papers(query="", sort="citations", limit=requested)
                self.assertTrue(result["ok"])
                self.assertEqual(result["count"], expected)
                self.assertEqual(len(result["results"]), expected)

    def test_search_papers_matches_and_returns_chinese_title(self):
        result = mcp_server.search_papers(query="可复习论文")

        self.assertTrue(result["ok"])
        self.assertEqual(result["count"], 1)
        self.assertEqual(result["results"][0]["id"], "p1")
        self.assertEqual(result["results"][0]["title_zh"], "可复习论文")

    def test_get_paper_returns_chinese_title(self):
        result = mcp_server.get_paper("p1")

        self.assertTrue(result["ok"])
        self.assertEqual(result["title_zh"], "可复习论文")

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

    def test_long_text_max_chars_is_clamped_at_both_boundaries(self):
        con = sqlite3.connect(self.db_path)
        con.execute("UPDATE papers SET explainer=? WHERE id='p1'", ("E" * 20_001,))
        con.execute("UPDATE translations SET content=? WHERE paper_id='p1'", ("T" * 20_001,))
        con.commit()
        con.close()

        for name, getter, marker in (
            ("explainer", mcp_server.get_explainer, "E"),
            ("translation", mcp_server.get_translation, "T"),
        ):
            with self.subTest(name=name, boundary="below"):
                result = getter("p1", max_chars=0)
                self.assertEqual(result["content"], marker)
                self.assertEqual(result["next_offset"], 1)
            with self.subTest(name=name, boundary="above"):
                result = getter("p1", max_chars=99_999)
                self.assertEqual(len(result["content"]), 20_000)
                self.assertEqual(result["next_offset"], 20_000)

    def test_semantic_search_k_is_clamped_at_both_boundaries(self):
        observed_k = []

        def rank_without_embeddings(_text, k, **_kwargs):
            observed_k.append(k)
            return []

        with patch.object(mcp_server.embed, "rank", side_effect=rank_without_embeddings):
            mcp_server.semantic_search("query", k=0)
            mcp_server.semantic_search("query", k=999)

        self.assertEqual(observed_k, [1, 50])

    def test_related_papers_k_is_clamped_at_both_boundaries(self):
        observed_k = []

        def rank_without_embeddings(_text, k, **_kwargs):
            observed_k.append(k)
            return []

        with patch.object(mcp_server.embed, "rank", side_effect=rank_without_embeddings):
            mcp_server.related_papers("p1", k=0)
            mcp_server.related_papers("p1", k=999)

        self.assertEqual(observed_k, [1, 50])

    def test_list_due_reviews_limit_is_clamped_at_both_boundaries(self):
        con = sqlite3.connect(self.db_path)
        for i in range(60):
            con.execute(
                """
                INSERT INTO paper_reviews(
                    paper_id, started_at, current_step, completed_steps,
                    next_due_at, completed_at, updated_at
                ) VALUES(?, '2026-07-01', 1, 0, '2026-07-03', NULL, '2026-07-01')
                """,
                (f"bulk-{i:02d}",),
            )
        con.commit()
        con.close()

        below = mcp_server.list_due_reviews(today="2026-07-03", include_upcoming=True, limit=0)
        above = mcp_server.list_due_reviews(today="2026-07-03", include_upcoming=True, limit=999)

        self.assertEqual(below["count"], 1)
        self.assertEqual(len(below["results"]), 1)
        self.assertEqual(above["count"], 50)
        self.assertEqual(len(above["results"]), 50)

    def test_list_due_reviews_exposes_readonly_review_queue(self):
        result = mcp_server.list_due_reviews(today="2026-07-02")

        self.assertTrue(result["ok"])
        self.assertEqual(result["today"], "2026-07-02")
        self.assertEqual(result["count"], 1)
        self.assertEqual(result["results"][0]["id"], "p1")
        self.assertEqual(result["results"][0]["title_zh"], "可复习论文")
        self.assertEqual(result["results"][0]["review_state"], "dueToday")
        self.assertEqual(result["results"][0]["current_step"], 2)
        self.assertEqual(result["results"][0]["completed_steps"], 1)

    def test_fastmcp_publishes_complete_tool_descriptions(self):
        published_tools = {
            tool.name: tool
            for tool in asyncio.run(mcp_server.mcp.list_tools())
        }
        tools = {name: tool.description or "" for name, tool in published_tools.items()}
        expected_names = {
            "library_overview",
            "list_categories",
            "search_papers",
            "semantic_search",
            "get_paper",
            "get_explainer",
            "get_translation",
            "related_papers",
            "list_due_reviews",
        }
        self.assertEqual(set(tools), expected_names)
        for name, description in tools.items():
            with self.subTest(name=name):
                self.assertTrue(description.strip())

        search = tools["search_papers"]
        for token in (
            "query",
            "type",
            "topic",
            "venue",
            "year_from",
            "year_to",
            "min_relevance",
            "has_explainer",
            "only_favorites",
            "sort",
            "relevance|year|citations|recent",
            "limit",
            "1-50",
            "get_paper",
        ):
            with self.subTest(search_token=token):
                self.assertIn(token, search)

    def test_fastmcp_publishes_exact_input_schemas(self):
        tools = {
            tool.name: tool
            for tool in asyncio.run(mcp_server.mcp.list_tools())
        }
        expected = {
            "library_overview": ({}, set(), {}),
            "list_categories": ({}, set(), {}),
            "search_papers": (
                {
                    "query": "string",
                    "type": "string",
                    "topic": "string",
                    "venue": "string",
                    "year_from": "integer",
                    "year_to": "integer",
                    "min_relevance": "number",
                    "has_explainer": "boolean",
                    "only_favorites": "boolean",
                    "sort": "string",
                    "limit": "integer",
                },
                set(),
                {
                    "query": "",
                    "type": "",
                    "topic": "",
                    "venue": "",
                    "year_from": 0,
                    "year_to": 0,
                    "min_relevance": 0.0,
                    "has_explainer": False,
                    "only_favorites": False,
                    "sort": "relevance",
                    "limit": 20,
                },
            ),
            "semantic_search": ({"query": "string", "k": "integer"}, {"query"}, {"k": 15}),
            "related_papers": ({"id": "string", "k": "integer"}, {"id"}, {"k": 8}),
            "get_paper": ({"id": "string"}, {"id"}, {}),
            "get_explainer": (
                {"id": "string", "offset": "integer", "max_chars": "integer"},
                {"id"},
                {"offset": 0, "max_chars": 12_000},
            ),
            "get_translation": (
                {"id": "string", "offset": "integer", "max_chars": "integer"},
                {"id"},
                {"offset": 0, "max_chars": 12_000},
            ),
            "list_due_reviews": (
                {"today": "string", "include_upcoming": "boolean", "limit": "integer"},
                set(),
                {"today": "", "include_upcoming": False, "limit": 20},
            ),
        }

        self.assertEqual(set(tools), set(expected))
        for name, (expected_types, expected_required, expected_defaults) in expected.items():
            with self.subTest(name=name):
                schema = tools[name].inputSchema
                properties = schema.get("properties", {})
                self.assertEqual(set(properties), set(expected_types))
                self.assertEqual(set(schema.get("required", [])), expected_required)
                normalized = {
                    prop: _normalize_nullable_scalar_schema(prop_schema)
                    for prop, prop_schema in properties.items()
                }
                self.assertEqual(
                    {prop: prop_schema.get("type") for prop, prop_schema in normalized.items()},
                    expected_types,
                )
                actual_defaults = {
                    prop: prop_schema["default"]
                    for prop, prop_schema in normalized.items()
                    if "default" in prop_schema
                }
                self.assertEqual(actual_defaults, expected_defaults)

    def test_fastmcp_publishes_response_control_fields(self):
        published_tools = {
            tool.name: tool
            for tool in asyncio.run(mcp_server.mcp.list_tools())
        }
        tools = {name: tool.description or "" for name, tool in published_tools.items()}
        expected_tokens = {
            "semantic_search": (
                "query",
                "k",
                "15",
                "1-50",
                "score",
                "indexed",
                "total",
                "note",
            ),
            "related_papers": (
                "id",
                "k",
                "8",
                "1-50",
                "seed",
                "score",
                "ok: false",
            ),
            "get_paper": (
                "id",
                "has_explainer",
                "has_translation",
                "has_pdf",
                "ok: false",
            ),
            "get_explainer": (
                "id",
                "offset",
                "max_chars",
                "12000",
                "1-20000",
                "next_offset",
                "total_chars",
                "truncated",
                "ok: false",
            ),
            "get_translation": (
                "id",
                "offset",
                "max_chars",
                "12000",
                "1-20000",
                "next_offset",
                "total_chars",
                "truncated",
                "ok: false",
            ),
            "list_due_reviews": (
                "today",
                "include_upcoming",
                "limit",
                "20",
                "1-50",
                "只读",
            ),
            "list_categories": ("types", "topics", "tasks"),
            "library_overview": ("total", "indexed_vectors", "review_due", "review_open"),
        }
        for name, tokens in expected_tokens.items():
            for token in tokens:
                with self.subTest(name=name, token=token):
                    self.assertIn(token, tools[name])


if __name__ == "__main__":
    unittest.main()
