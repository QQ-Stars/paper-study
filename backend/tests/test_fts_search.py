from __future__ import annotations

from contextlib import closing
from datetime import datetime, timezone
import hashlib
import os
import unittest

if (
    os.name == "nt"
    and hasattr(os, "add_dll_directory")
    and os.environ.get("P3_SQLITE_DLL_DIR")
):
    _SQLITE_DLL_HANDLE = os.add_dll_directory(os.environ["P3_SQLITE_DLL_DIR"])

import sqlite3


NOW = datetime(2026, 8, 13, 14, 0, tzinfo=timezone.utc)


class FtsSearchTests(unittest.IsolatedAsyncioTestCase):
    async def test_one_and_two_code_point_queries_fail_with_typed_422_error(
        self,
    ) -> None:
        from backend.app.domain import SearchQueryTooShortError
        from backend.app.domain.context import SearchRequest

        for query in ("a", "ab", "机", "机器", "  ab  "):
            with self.subTest(query=query):
                with self.assertRaises(SearchQueryTooShortError) as caught:
                    SearchRequest(query=query, mode="lexical")
                self.assertEqual("SEARCH_QUERY_TOO_SHORT", caught.exception.code)
                self.assertEqual(422, caught.exception.http_status)

    async def test_external_content_triggers_rollback_rebuild_and_integrity_stay_consistent(
        self,
    ) -> None:
        from backend.app.domain.context import ChunkingSpec
        from backend.tests.support.p3_database import p3_context_fixture

        markdown = (
            "[page 1]\n# Trigger Insert Heading\n\n"
            "The trigger insert sentinel is indexed from a committed chunk.\n"
        )
        async with p3_context_fixture(
            prefix="study-app-p3-fts-consistency-",
            source_id="src_fts_consistency",
            markdown=markdown,
            spec=ChunkingSpec(target_tokens=64, hard_cap_tokens=64),
            now=NOW,
            pdf_sha256="c" * 64,
            options_hash="d" * 64,
        ) as fixture:
            with closing(sqlite3.connect(fixture.database_path)) as connection:
                rowid, chunk_id = connection.execute(
                    "SELECT rowid,id FROM document_chunks "
                    "WHERE source_document_id='src_fts_consistency' "
                    "AND content LIKE '%trigger insert sentinel%'"
                ).fetchone()

                inserted = connection.execute(
                    "SELECT count(*) FROM document_chunks_fts "
                    "WHERE document_chunks_fts MATCH ?",
                    ('"trigger insert sentinel"',),
                ).fetchone()[0]
                self.assertEqual(1, inserted)

                connection.execute(
                    "UPDATE document_chunks SET heading_path=?,content=? WHERE id=?",
                    (
                        '["Trigger Updated Heading"]',
                        "The committed trigger update sentinel replaces the old text.",
                        chunk_id,
                    ),
                )
                connection.commit()
                updated = connection.execute(
                    "SELECT count(*) FROM document_chunks_fts "
                    "WHERE document_chunks_fts MATCH ?",
                    ('"trigger update sentinel"',),
                ).fetchone()[0]
                removed_old = connection.execute(
                    "SELECT count(*) FROM document_chunks_fts "
                    "WHERE document_chunks_fts MATCH ?",
                    ('"trigger insert sentinel"',),
                ).fetchone()[0]
                self.assertEqual((1, 0), (updated, removed_old))

                connection.execute("BEGIN")
                connection.execute(
                    "UPDATE document_chunks SET content=? WHERE id=?",
                    ("A rollback leak sentinel must never survive.", chunk_id),
                )
                self.assertEqual(
                    1,
                    connection.execute(
                        "SELECT count(*) FROM document_chunks_fts "
                        "WHERE document_chunks_fts MATCH ?",
                        ('"rollback leak sentinel"',),
                    ).fetchone()[0],
                )
                connection.rollback()
                self.assertEqual(
                    0,
                    connection.execute(
                        "SELECT count(*) FROM document_chunks_fts "
                        "WHERE document_chunks_fts MATCH ?",
                        ('"rollback leak sentinel"',),
                    ).fetchone()[0],
                )
                self.assertEqual(
                    1,
                    connection.execute(
                        "SELECT count(*) FROM document_chunks_fts "
                        "WHERE document_chunks_fts MATCH ?",
                        ('"trigger update sentinel"',),
                    ).fetchone()[0],
                )

                connection.execute(
                    "INSERT INTO document_chunks_fts(document_chunks_fts) VALUES('rebuild')"
                )
                connection.commit()
                total, joined = connection.execute(
                    "SELECT (SELECT count(*) FROM document_chunks),count(*) "
                    "FROM document_chunks c JOIN document_chunks_fts f ON f.rowid=c.rowid"
                ).fetchone()
                self.assertEqual(total, joined)
                self.assertEqual(
                    chunk_id,
                    connection.execute(
                        "SELECT c.id FROM document_chunks c "
                        "JOIN document_chunks_fts f ON f.rowid=c.rowid WHERE c.rowid=?",
                        (rowid,),
                    ).fetchone()[0],
                )
                connection.execute(
                    "INSERT INTO document_chunks_fts(document_chunks_fts) "
                    "VALUES('integrity-check')"
                )

                connection.execute("DELETE FROM document_chunks WHERE id=?", (chunk_id,))
                connection.commit()
                self.assertEqual(
                    0,
                    connection.execute(
                        "SELECT count(*) FROM document_chunks_fts "
                        "WHERE document_chunks_fts MATCH ?",
                        ('"trigger update sentinel"',),
                    ).fetchone()[0],
                )
                self.assertEqual(
                    0,
                    connection.execute(
                        "SELECT count(*) FROM document_chunks_fts WHERE rowid=?", (rowid,)
                    ).fetchone()[0],
                )

    async def test_literal_queries_heading_matches_filters_and_bm25_ties_are_stable(
        self,
    ) -> None:
        from backend.app.application.document_search import DocumentSearch
        from backend.app.domain import SourceDocument
        from backend.app.domain.context import ChunkingSpec, SearchRequest
        from backend.app.repositories.document_search import (
            SqlAlchemyDocumentSearchRepository,
        )
        from backend.tests.support.p3_database import p3_context_fixture

        markdown = (
            "[page 1]\n# Shared Heading\n\n"
            "A stable tie sentinel appears exactly once.\n\n"
            "# Quoted Material\n\n"
            'Literal "quoted" punctuation target appears here.\n\n'
            "# Ordinary Section\n\nOrdinary body text without the heading needle.\n"
        )
        spec = ChunkingSpec(target_tokens=32, hard_cap_tokens=32)
        async with p3_context_fixture(
            prefix="study-app-p3-fts-hardening-",
            source_id="src_tie_b",
            markdown=markdown,
            spec=spec,
            now=NOW,
            pdf_sha256="e" * 64,
            options_hash="f" * 64,
        ) as fixture:
            with closing(sqlite3.connect(fixture.database_path)) as connection:
                connection.execute(
                    "INSERT INTO papers(id,source,title) "
                    "VALUES('paper-search-2','test','Second Search Paper')"
                )
                connection.commit()
            source_sha = hashlib.sha256(markdown.encode("utf-8")).hexdigest()
            async with fixture.unit_of_work_factory() as work:
                await work.sources.add(
                    SourceDocument(
                        id="src_tie_a",
                        paper_id="paper-search-2",
                        mode="native",
                        status="ready",
                        provider="local",
                        model="pymupdf4llm-pymupdf",
                        pdf_sha256="1" * 64,
                        options_hash="2" * 64,
                        processing_version="native-v1",
                        created_at=NOW,
                        updated_at=NOW,
                        markdown=markdown,
                        content_sha256=source_sha,
                        page_count=1,
                    )
                )
                await work.commit()
            await fixture.builder.materialize_chunks("src_tie_a", spec, now=NOW)

            with closing(sqlite3.connect(fixture.database_path)) as connection:
                heading_only_chunk = connection.execute(
                    "SELECT id FROM document_chunks WHERE source_document_id='src_tie_b' "
                    "AND content LIKE '%Ordinary body text%'"
                ).fetchone()[0]
                connection.execute(
                    "UPDATE document_chunks SET heading_path=? WHERE id=?",
                    ('["Exclusive heading needle"]', heading_only_chunk),
                )
                connection.commit()

            search = DocumentSearch(
                SqlAlchemyDocumentSearchRepository(fixture.session_factory)
            )
            tied = await search.search(
                SearchRequest(
                    query="stable tie sentinel",
                    mode="lexical",
                    paper_ids=("paper-search-2", "paper-1"),
                    limit=10,
                )
            )
            reversed_filter = await search.search(
                SearchRequest(
                    query="stable tie sentinel",
                    mode="lexical",
                    paper_ids=("paper-1", "paper-search-2"),
                    limit=10,
                )
            )
            filtered = await search.search(
                SearchRequest(
                    query="stable tie sentinel",
                    mode="lexical",
                    paper_ids=("paper-search-2",),
                    limit=10,
                )
            )
            heading = await search.search(
                SearchRequest(query="exclusive heading needle", mode="lexical", limit=10)
            )
            quoted = await search.search(
                SearchRequest(query='literal "quoted" punctuation', limit=10)
            )
            hostile = await search.search(
                SearchRequest(
                    query='stable" OR document_chunks_fts MATCH "*" --',
                    limit=10,
                )
            )

            self.assertEqual(
                ["paper-1", "paper-search-2"],
                [item.paper_id for item in tied.items],
            )
            self.assertEqual(
                [item.chunk_id for item in tied.items],
                [item.chunk_id for item in reversed_filter.items],
            )
            self.assertEqual(tied.items[0].lexical_score, tied.items[1].lexical_score)
            self.assertEqual(["paper-search-2"], [item.paper_id for item in filtered.items])
            self.assertEqual(heading_only_chunk, heading.items[0].chunk_id)
            self.assertNotIn("exclusive heading needle", heading.items[0].excerpt.casefold())
            self.assertEqual(2, len(quoted.items))
            self.assertTrue(all('"quoted" punctuation' in item.excerpt for item in quoted.items))
            self.assertEqual((), hostile.items)

    async def test_lexical_search_matches_substrings_with_provenance_stale_filter_and_zero_writes(
        self,
    ) -> None:
        from backend.app.application.document_search import DocumentSearch
        from backend.app.domain import SourceDocument
        from backend.app.domain.context import ChunkingSpec, SearchRequest
        from backend.app.infrastructure.database_backup import inspect_database
        from backend.app.repositories.document_search import (
            SqlAlchemyDocumentSearchRepository,
        )
        from backend.tests.support.p3_database import p3_context_fixture

        markdown = (
            "[page 1]\n# Introduction\n\n"
            "A robust multimodal evaluation protocol appears here.\n\n"
            "[page 2]\n# 方法\n\n这是一个机器学习模型，用于可靠评估。\n"
        )
        async with p3_context_fixture(
            prefix="study-app-p3-fts-search-",
            source_id="src_fts_ready",
            markdown=markdown,
            spec=ChunkingSpec(target_tokens=8, hard_cap_tokens=8),
            now=NOW,
            pdf_sha256="a" * 64,
            options_hash="b" * 64,
        ) as fixture:
            with closing(sqlite3.connect(fixture.database_path)) as connection:
                ready_rows = connection.execute(
                    "SELECT id,sequence,heading_path,page_start,page_end,content "
                    "FROM document_chunks WHERE source_document_id='src_fts_ready' "
                    "ORDER BY sequence"
                ).fetchall()
                chinese_row = next(row for row in ready_rows if "机器学习" in row[5])
            stale_markdown = "[page 9]\n# Stale\n\n机器学习 stale poison.\n"
            async with fixture.unit_of_work_factory() as work:
                await work.sources.add(
                    SourceDocument(
                        id="src_fts_stale",
                        paper_id="paper-1",
                        mode="native",
                        status="ready",
                        provider="local",
                        model="pymupdf4llm-pymupdf",
                        pdf_sha256="a" * 64,
                        options_hash="e" * 64,
                        processing_version="native-v1",
                        created_at=NOW,
                        updated_at=NOW,
                        markdown=stale_markdown,
                        content_sha256=hashlib.sha256(
                            stale_markdown.encode("utf-8")
                        ).hexdigest(),
                        page_count=9,
                    )
                )
                await work.commit()
            await fixture.builder.materialize_chunks(
                "src_fts_stale",
                ChunkingSpec(target_tokens=8, hard_cap_tokens=8),
                now=NOW,
            )
            with closing(sqlite3.connect(fixture.database_path)) as connection:
                connection.execute(
                    "UPDATE document_sources SET status='stale',stale_at=? "
                    "WHERE id='src_fts_stale'",
                    (NOW.isoformat(),),
                )
                connection.execute(
                    "UPDATE document_chunks SET status='stale',stale_at=? "
                    "WHERE source_document_id='src_fts_stale'",
                    (NOW.isoformat(),),
                )
                connection.commit()

            search = DocumentSearch(
                SqlAlchemyDocumentSearchRepository(fixture.session_factory)
            )
            before = inspect_database(fixture.database_path)

            english = await search.search(
                SearchRequest(
                    query="multimodal evalu",
                    mode="lexical",
                    paper_ids=("paper-1",),
                    limit=10,
                )
            )
            chinese = await search.search(
                SearchRequest(query="机器学习", mode="lexical", limit=10)
            )

            after = inspect_database(fixture.database_path)
            self.assertEqual(1, len(english.items))
            self.assertIn("multimodal evaluation", english.items[0].excerpt)
            self.assertEqual("paper-1", english.items[0].paper_id)
            self.assertEqual("src_fts_ready", english.items[0].source_document_id)
            self.assertEqual(("Introduction",), english.items[0].heading_path)
            self.assertEqual((1, 1), (english.items[0].page_start, english.items[0].page_end))
            self.assertIsNotNone(english.items[0].lexical_score)
            self.assertIsNone(english.items[0].semantic_score)
            self.assertEqual(1, len(chinese.items))
            self.assertEqual("src_fts_ready", chinese.items[0].source_document_id)
            self.assertEqual(chinese_row[0], chinese.items[0].chunk_id)
            self.assertIn("机器学习", chinese.items[0].excerpt)
            self.assertEqual(("方法",), chinese.items[0].heading_path)
            self.assertEqual((2, 2), (chinese.items[0].page_start, chinese.items[0].page_end))
            self.assertNotIn("stale poison", chinese.items[0].excerpt)
            self.assertEqual(before.logical_sha256, after.logical_sha256)
            self.assertEqual(before.table_sha256, after.table_sha256)
            self.assertEqual(before.content_sha256, after.content_sha256)


if __name__ == "__main__":
    unittest.main()
