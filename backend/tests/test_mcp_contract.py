from __future__ import annotations

import asyncio
from contextlib import contextmanager
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from agent import config, mcp_server


FIXTURE_ROOT = Path(__file__).resolve().parent / "fixtures" / "mcp"
TOOL_NAMES = {
    "get_explainer",
    "get_paper",
    "get_translation",
    "library_overview",
    "list_categories",
    "list_due_reviews",
    "related_papers",
    "search_papers",
    "semantic_search",
}


PAPER_COLUMNS = """
    id, title, title_zh, venue, year, type, topic, relevance, citations, tldr,
    abstract, explainer, pdf_path, authors, doi, arxiv_id, url, task,
    models, datasets, contribution, tags, s2_fields, created_at, updated_at
"""


def create_mcp_fixture_database(path: Path) -> None:
    connection = sqlite3.connect(path)
    try:
        connection.executescript(
            """
            CREATE TABLE papers (
                id TEXT PRIMARY KEY, title TEXT NOT NULL, title_zh TEXT, venue TEXT,
                year TEXT, type TEXT, topic TEXT, relevance REAL, citations INTEGER,
                tldr TEXT, abstract TEXT, explainer TEXT, pdf_path TEXT, authors TEXT,
                doi TEXT, arxiv_id TEXT, url TEXT, task TEXT, models TEXT,
                datasets TEXT, contribution TEXT, tags TEXT, s2_fields TEXT,
                created_at TEXT, updated_at TEXT
            );
            CREATE TABLE notes (paper_id TEXT PRIMARY KEY, content TEXT);
            CREATE TABLE progress (paper_id TEXT PRIMARY KEY, status TEXT, updated_at TEXT);
            CREATE TABLE favorites (paper_id TEXT PRIMARY KEY, created_at TEXT);
            CREATE TABLE translations (paper_id TEXT PRIMARY KEY, content TEXT, updated_at TEXT);
            CREATE TABLE paper_vectors (paper_id TEXT PRIMARY KEY, dim INTEGER, vector BLOB);
            CREATE TABLE paper_reviews (
                paper_id TEXT PRIMARY KEY, started_at TEXT NOT NULL,
                current_step INTEGER NOT NULL, completed_steps INTEGER NOT NULL,
                next_due_at TEXT NOT NULL, completed_at TEXT, updated_at TEXT NOT NULL
            );
            CREATE TABLE document_sources (
                id TEXT PRIMARY KEY, paper_id TEXT NOT NULL, mode TEXT NOT NULL,
                status TEXT NOT NULL, provider TEXT NOT NULL, model TEXT NOT NULL,
                pdf_sha256 TEXT NOT NULL, options_hash TEXT NOT NULL,
                content_sha256 TEXT, markdown TEXT, page_count INTEGER,
                processing_version TEXT NOT NULL, error_code TEXT, error_message TEXT,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                source_key TEXT, ready_at TEXT, stale_at TEXT
            );
            CREATE TABLE generated_artifacts (
                id TEXT PRIMARY KEY, paper_id TEXT NOT NULL, kind TEXT NOT NULL,
                source_document_id TEXT NOT NULL, status TEXT NOT NULL, content TEXT,
                content_sha256 TEXT, generator_provider TEXT NOT NULL,
                generator_model TEXT NOT NULL, prompt_version TEXT NOT NULL,
                error_code TEXT, error_message TEXT, created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL, artifact_key TEXT, ready_at TEXT, stale_at TEXT
            );
            CREATE TABLE paper_artifact_heads (
                paper_id TEXT NOT NULL, kind TEXT NOT NULL, artifact_id TEXT NOT NULL,
                updated_at TEXT NOT NULL, PRIMARY KEY (paper_id, kind)
            );
            """
        )
        rows = (
            (
                "p1", "Reviewable Paper", "可复习论文", "ACL", "2026", "评测",
                "hallucination", 0.95, 12, "short summary", "abstract one",
                "LEGACY-EXPLAINER-尾", None, '["Alice"]', None, "2601.00001",
                "https://example.test/p1", "task-a", '["m1"]', '["d1"]',
                "contribution", '["tag"]', '["cs.CL"]',
                "2026-07-01T00:00:00Z", "2026-07-02T00:00:00Z",
            ),
            (
                "p2", "Empty Paper", None, "NeurIPS", "2025", "方法", "robustness",
                0.5, 3, "", "abstract two", "", None, "[]", None, None, None,
                "task-b", "[]", "[]", "", "[]", "[]",
                "2026-06-01T00:00:00Z", "2026-06-02T00:00:00Z",
            ),
        )
        connection.executemany(
            f"INSERT INTO papers({PAPER_COLUMNS}) VALUES ({','.join(['?'] * 25)})",
            rows,
        )
        connection.execute("INSERT INTO notes VALUES('p1','笔记')")
        connection.execute("INSERT INTO progress VALUES('p1','学习中','2026-07-02')")
        connection.execute("INSERT INTO favorites VALUES('p1','2026-07-01')")
        connection.execute(
            "INSERT INTO translations VALUES('p1','LEGACY-TRANSLATION-尾','2026-07-02')"
        )
        connection.execute(
            "INSERT INTO paper_vectors VALUES('p1',2,?)",
            (sqlite3.Binary(b"\x00" * 8),),
        )
        connection.execute(
            "INSERT INTO paper_reviews VALUES"
            "('p1','2026-07-01',2,1,'2026-07-03',NULL,'2026-07-02')"
        )
        connection.commit()
    finally:
        connection.close()


@contextmanager
def mcp_fixture_database():
    with tempfile.TemporaryDirectory(prefix="study-app-p6-mcp-") as directory:
        database_path = Path(directory) / "app.db"
        create_mcp_fixture_database(database_path)
        previous = config.DB_PATH
        config.DB_PATH = str(database_path)
        try:
            yield database_path
        finally:
            config.DB_PATH = previous


def capture_legacy_results() -> dict[str, object]:
    observed_k: list[int] = []

    def semantic_rank(query: str, k: int, **_kwargs: object):
        observed_k.append(k)
        return [] if query == "none" else [{"id": "p1", "score": 0.875}]

    with patch.object(mcp_server.embed, "rank", side_effect=semantic_rank):
        semantic_normal = mcp_server.semantic_search("研究问题", k=15)
        semantic_empty = mcp_server.semantic_search("none", k=0)
    with patch.object(
        mcp_server.embed,
        "rank",
        return_value=[{"id": "p2", "score": 0.625}],
    ):
        related_normal = mcp_server.related_papers("p1", k=999)

    if observed_k != [15, 1]:
        raise AssertionError(f"semantic k clamp changed: {observed_k}")
    return {
        "get_explainer.missing": mcp_server.get_explainer("missing"),
        "get_explainer.normal": mcp_server.get_explainer("p1", max_chars=9),
        "get_paper.missing": mcp_server.get_paper("missing"),
        "get_paper.normal": mcp_server.get_paper("p1"),
        "get_translation.missing": mcp_server.get_translation("missing"),
        "get_translation.normal": mcp_server.get_translation(
            "p1", offset=7, max_chars=8
        ),
        "library_overview.normal": mcp_server.library_overview(),
        "list_categories.normal": mcp_server.list_categories(),
        "list_due_reviews.empty": mcp_server.list_due_reviews(today="2020-01-01"),
        "list_due_reviews.normal": mcp_server.list_due_reviews(
            today="2026-07-03", limit=0
        ),
        "related_papers.missing": mcp_server.related_papers("missing"),
        "related_papers.normal": related_normal,
        "search_papers.empty": mcp_server.search_papers(query="not-present"),
        "search_papers.normal": mcp_server.search_papers(query="可复习论文"),
        "search_papers.limit_high": mcp_server.search_papers(limit=999),
        "semantic_search.empty": semantic_empty,
        "semantic_search.normal": semantic_normal,
    }


def capture_application_results(database_path: Path) -> dict[str, object]:
    from backend.app.api.mcp import ApplicationMcpAdapter

    observed_k: list[int] = []

    def rank(query: str, k: int, **kwargs: object):
        observed_k.append(k)
        if kwargs.get("exclude") == "p1":
            return [{"id": "p2", "score": 0.625}]
        return [] if query == "none" else [{"id": "p1", "score": 0.875}]

    adapter = ApplicationMcpAdapter(
        database_path,
        artifact_read_mode="prefer_new",
        ranker=rank,
        has_pdf=lambda _row: False,
    )
    results = {
        "get_explainer.missing": adapter.get_explainer("missing"),
        "get_explainer.normal": adapter.get_explainer("p1", max_chars=9),
        "get_paper.missing": adapter.get_paper("missing"),
        "get_paper.normal": adapter.get_paper("p1"),
        "get_translation.missing": adapter.get_translation("missing"),
        "get_translation.normal": adapter.get_translation(
            "p1", offset=7, max_chars=8
        ),
        "library_overview.normal": adapter.library_overview(),
        "list_categories.normal": adapter.list_categories(),
        "list_due_reviews.empty": adapter.list_due_reviews(today="2020-01-01"),
        "list_due_reviews.normal": adapter.list_due_reviews(
            today="2026-07-03", limit=0
        ),
        "related_papers.missing": adapter.related_papers("missing"),
        "related_papers.normal": adapter.related_papers("p1", k=999),
        "search_papers.empty": adapter.search_papers(query="not-present"),
        "search_papers.normal": adapter.search_papers(query="可复习论文"),
        "search_papers.limit_high": adapter.search_papers(limit=999),
        "semantic_search.empty": adapter.semantic_search("none", k=0),
        "semantic_search.normal": adapter.semantic_search("研究问题", k=15),
    }
    if observed_k != [50, 1, 15]:
        raise AssertionError(f"application k clamp changed: {observed_k}")
    return results


class McpContractTests(unittest.TestCase):
    def test_exact_nine_tool_schemas_match_snapshot(self) -> None:
        tools = asyncio.run(mcp_server.mcp.list_tools())
        actual = [
            {
                "description": tool.description,
                "inputSchema": tool.inputSchema,
                "name": tool.name,
            }
            for tool in tools
        ]

        self.assertEqual(9, len(actual))
        self.assertEqual(TOOL_NAMES, {item["name"] for item in actual})
        expected = json.loads(
            (FIXTURE_ROOT / "tool_schemas.json").read_text(encoding="utf-8")
        )
        self.assertEqual(expected, actual)

    def test_legacy_results_cover_normal_empty_unicode_and_errors(self) -> None:
        with mcp_fixture_database():
            actual = capture_legacy_results()

        expected = json.loads(
            (FIXTURE_ROOT / "results" / "legacy_results.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(expected, actual)

    def test_get_paper_source_document_contract_decision_is_explicit(self) -> None:
        decision = json.loads(
            (FIXTURE_ROOT / "source_document_contract.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(
            {
                "application": {
                    "allowedField": "sourceDocument",
                    "allowedModes": ["native", "ocr"],
                    "errorKeys": ["code", "message"],
                    "markdownAllowed": False,
                    "modeViewKeys": ["currentId", "error", "status", "updatedAt"],
                    "optional": True,
                },
                "legacyAndShadowAdditionalFields": [],
                "schemaVersion": 1,
                "toolCount": 9,
            },
            decision,
        )

    def test_application_tools_prefer_ready_new_rows_and_fallback_to_legacy_fields(
        self,
    ) -> None:
        from backend.app.api.mcp import ApplicationMcpAdapter

        with mcp_fixture_database() as database_path:
            connection = sqlite3.connect(database_path)
            try:
                source_values = (
                    "src-ready",
                    "p1",
                    "native",
                    "ready",
                    "local",
                    "pymupdf4llm-pymupdf",
                    "a" * 64,
                    "b" * 64,
                    "c" * 64,
                    "# source",
                    1,
                    "v1",
                    None,
                    None,
                    "2026-08-01T00:00:00Z",
                    "2026-08-01T00:00:00Z",
                    "source-key",
                    "2026-08-01T00:00:00Z",
                    None,
                )
                connection.execute(
                    "INSERT INTO document_sources VALUES"
                    "(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    source_values,
                )
                connection.execute(
                    "INSERT INTO document_sources VALUES"
                    "(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        "src-failed", "p1", "ocr", "failed", "ocr-provider",
                        "ocr-model", "d" * 64, "e" * 64, None, None, None, "v1",
                        "OCR_SECRET_DETAIL", "must-not-leak", "2026-08-02T00:00:00Z",
                        "2026-08-02T00:00:00Z", "source-failed", None, None,
                    ),
                )

                def artifact(
                    identifier: str,
                    kind: str,
                    status: str,
                    content: str,
                    updated_at: str,
                    *,
                    source_id: str = "src-ready",
                    stale_at: str | None = None,
                    provider: str = "provider-a",
                ) -> tuple[object, ...]:
                    return (
                        identifier, "p1", kind, source_id, status, content,
                        "f" * 64 if status == "ready" else None, provider, "model",
                        "prompt-v1", None, None, "2026-08-01T00:00:00Z",
                        updated_at, f"key-{identifier}", updated_at if status == "ready" else None,
                        stale_at,
                    )

                connection.executemany(
                    "INSERT INTO generated_artifacts VALUES"
                    "(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    [
                        artifact("art-exp-old", "explainer", "ready", "NEW-OLD", "2026-08-02T00:00:00Z"),
                        artifact("art-exp-z", "explainer", "ready", "NEW-EXPLAINER-TAIL", "2026-08-03T00:00:00Z", provider="provider-b"),
                        artifact("art-exp-stale", "explainer", "ready", "STALE-MUST-NOT-WIN", "2026-08-04T00:00:00Z", stale_at="2026-08-04T00:00:01Z"),
                        artifact("art-exp-failed", "explainer", "failed", "FAILED-MUST-NOT-WIN", "2026-08-05T00:00:00Z"),
                        artifact("art-exp-source-failed", "explainer", "ready", "SOURCE-FAILED-MUST-NOT-WIN", "2026-08-06T00:00:00Z", source_id="src-failed"),
                        artifact("art-tr", "translation", "ready", "NEW-TRANSLATION-TAIL", "2026-08-03T00:00:00Z"),
                    ],
                )
                connection.commit()
            finally:
                connection.close()

            prefer_new = ApplicationMcpAdapter(
                database_path,
                artifact_read_mode="prefer_new",
                ranker=lambda *_args, **_kwargs: [],
                has_pdf=lambda _row: False,
            )
            legacy = ApplicationMcpAdapter(
                database_path,
                artifact_read_mode="legacy",
                ranker=lambda *_args, **_kwargs: [],
                has_pdf=lambda _row: False,
            )
            self.assertEqual(
                "NEW-EXPLAINER-TAIL",
                prefer_new.get_explainer("p1")["content"],
            )
            self.assertEqual(
                "NEW-TRANSLATION-TAIL",
                prefer_new.get_translation("p1")["content"],
            )
            self.assertEqual(
                "LEGACY-EXPLAINER-尾", legacy.get_explainer("p1")["content"]
            )

            connection = sqlite3.connect(database_path)
            try:
                connection.execute(
                    "DELETE FROM generated_artifacts WHERE status='ready' AND stale_at IS NULL AND source_document_id='src-ready'"
                )
                connection.commit()
            finally:
                connection.close()
            self.assertEqual(
                "LEGACY-EXPLAINER-尾", prefer_new.get_explainer("p1")["content"]
            )
            self.assertEqual(
                "LEGACY-TRANSLATION-尾", prefer_new.get_translation("p1")["content"]
            )

            connection = sqlite3.connect(database_path)
            try:
                connection.execute("DROP TABLE generated_artifacts")
                connection.commit()
            finally:
                connection.close()
            failed = prefer_new.get_explainer("p1")
            self.assertFalse(failed["ok"])
            self.assertEqual("MCP_APPLICATION_READ_FAILED", failed["code"])
            self.assertNotIn("LEGACY-EXPLAINER", json.dumps(failed))

    def test_application_mode_matches_all_legacy_goldens(self) -> None:
        with mcp_fixture_database() as database_path:
            actual = capture_application_results(database_path)

        expected = json.loads(
            (FIXTURE_ROOT / "results" / "legacy_results.json").read_text(
                encoding="utf-8"
            )
        )
        expected["get_paper.normal"]["sourceDocument"] = {
            "native": None,
            "ocr": None,
        }
        self.assertEqual(expected, actual)

    def test_explainer_translation_pagination_uses_selected_content(self) -> None:
        from backend.app.api.mcp import ApplicationMcpAdapter

        with mcp_fixture_database() as database_path:
            connection = sqlite3.connect(database_path)
            try:
                connection.execute(
                    "INSERT INTO document_sources VALUES"
                    "(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        "src", "p1", "native", "ready", "local", "model",
                        "a" * 64, "b" * 64, "c" * 64, "# source", 1, "v1",
                        None, None, "2026-08-01", "2026-08-01", "key",
                        "2026-08-01", None,
                    ),
                )
                for identifier, kind, content in (
                    ("exp", "explainer", "0123456789-NEW-EXPLAINER-TAIL"),
                    ("tr", "translation", "abcdefghij-NEW-TRANSLATION-TAIL"),
                ):
                    connection.execute(
                        "INSERT INTO generated_artifacts VALUES"
                        "(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (
                            identifier, "p1", kind, "src", "ready", content,
                            "d" * 64, "provider", "model", "prompt", None,
                            None, "2026-08-01", "2026-08-01", f"key-{kind}",
                            "2026-08-01", None,
                        ),
                    )
                connection.commit()
            finally:
                connection.close()

            adapter = ApplicationMcpAdapter(
                database_path,
                artifact_read_mode="prefer_new",
                ranker=lambda *_args, **_kwargs: [],
                has_pdf=lambda _row: False,
            )
            explainer = adapter.get_explainer("p1", offset=11, max_chars=50)
            translation = adapter.get_translation("p1", offset=11, max_chars=50)
            self.assertEqual("NEW-EXPLAINER-TAIL", explainer["content"])
            self.assertEqual("NEW-TRANSLATION-TAIL", translation["content"])
            self.assertEqual(29, explainer["total_chars"])
            self.assertEqual(31, translation["total_chars"])

    def test_get_paper_application_source_document_view_is_mode_specific_and_safe(
        self,
    ) -> None:
        from backend.app.api.mcp import ApplicationMcpAdapter

        statuses = ("queued", "running", "ready", "failed", "stale", "cancelled")
        with mcp_fixture_database() as database_path:
            adapter = ApplicationMcpAdapter(
                database_path,
                artifact_read_mode="prefer_new",
                ranker=lambda *_args, **_kwargs: [],
                has_pdf=lambda _row: False,
            )
            for sequence, status in enumerate(statuses):
                updated_at = f"2026-08-{sequence + 1:02d}T00:00:00Z"
                connection = sqlite3.connect(database_path)
                try:
                    connection.execute(
                        "INSERT INTO document_sources VALUES"
                        "(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (
                            f"native-{status}", "p1", "native", status,
                            "provider-secret", "model-secret", "a" * 64,
                            "b" * 64, None, "# markdown must not leak", None,
                            "v1", "SOURCE_SAFE_CODE" if status == "failed" else None,
                            "credential=top-secret traceback" if status == "failed" else None,
                            updated_at, updated_at, f"key-{status}",
                            updated_at if status == "ready" else None,
                            updated_at if status == "stale" else None,
                        ),
                    )
                    connection.commit()
                finally:
                    connection.close()

                result = adapter.get_paper("p1")
                view = result["sourceDocument"]["native"]
                self.assertEqual(f"native-{status}", view["currentId"])
                self.assertEqual(status, view["status"])
                self.assertEqual(updated_at, view["updatedAt"])
                expected_error = (
                    {"code": "SOURCE_SAFE_CODE", "message": "Processing failed."}
                    if status == "failed"
                    else None
                )
                self.assertEqual(expected_error, view["error"])
                serialized = json.dumps(view)
                self.assertNotIn("top-secret", serialized)
                self.assertNotIn("markdown", serialized)
                self.assertNotIn("provider-secret", serialized)

            connection = sqlite3.connect(database_path)
            try:
                connection.execute(
                    "INSERT INTO document_sources VALUES"
                    "(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        "ocr-current", "p1", "ocr", "ready", "provider", "model",
                        "c" * 64, "d" * 64, "e" * 64, "# hidden", 1, "v1",
                        None, None, "2026-09-01", "2026-09-01", "ocr-key",
                        "2026-09-01", None,
                    ),
                )
                connection.commit()
            finally:
                connection.close()
            source = adapter.get_paper("p1")["sourceDocument"]
            self.assertEqual("native-cancelled", source["native"]["currentId"])
            self.assertEqual("ocr-current", source["ocr"]["currentId"])


if __name__ == "__main__":
    unittest.main()
