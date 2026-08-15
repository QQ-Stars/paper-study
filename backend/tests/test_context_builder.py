from __future__ import annotations

import dataclasses
from datetime import datetime, timezone
import hashlib
import importlib
import inspect
import math
import os
import subprocess
import sys
import unittest


_SQLITE_DLL_HANDLE = None
if os.name == "nt" and os.environ.get("P3_SQLITE_DLL_DIR"):
    _SQLITE_DLL_HANDLE = os.add_dll_directory(os.environ["P3_SQLITE_DLL_DIR"])


SHA_A = "a" * 64
SHA_B = "b" * 64
SHA_ABC = hashlib.sha256(b"abc").hexdigest()
SHA_DEF = hashlib.sha256(b"def").hexdigest()
NOW = datetime(2026, 8, 9, 12, 0, tzinfo=timezone.utc)


class ContextDomainTests(unittest.TestCase):
    def test_context_domain_uses_stdlib_and_exposes_public_contract(self) -> None:
        before = set(sys.modules)
        context = importlib.import_module("backend.app.domain.context")
        imported = set(sys.modules) - before
        for forbidden in ("sqlalchemy", "fastapi", "numpy", "httpx", "openai"):
            self.assertFalse(
                any(name == forbidden or name.startswith(forbidden + ".") for name in imported),
                forbidden,
            )
        for name in (
            "ChunkingSpec", "ChunkSet", "ContextRequest", "ContextBatch", "ContextPlan",
            "EmbeddingProfile", "EmbeddingRequest", "EmbeddingBatch", "SearchRequest",
            "SearchHit", "SearchCoverage", "SearchMode",
        ):
            self.assertTrue(hasattr(context, name), name)

    def test_chunking_spec_and_chunk_set_validate_coverage_and_hashes(self) -> None:
        from backend.app.domain.context import ChunkSet, ChunkingSpec
        from backend.app.domain.entities import DocumentChunk

        spec = ChunkingSpec()
        self.assertEqual("markdown-coverage-v1", spec.chunking_version)
        self.assertEqual(1600, spec.target_tokens)
        self.assertEqual(8192, spec.atomic_hard_cap_tokens)
        first = DocumentChunk(
            id="chunk_1", source_document_id="src_1", sequence=0,
            heading_path='["Intro"]', page_start=1, page_end=1,
            content="abc", content_sha256=SHA_ABC, token_count=1,
        )
        second = dataclasses.replace(
            first, id="chunk_2", sequence=1, content="def", content_sha256=SHA_DEF,
            page_start=1, page_end=2,
        )
        chunk_set = ChunkSet(
            source_document_id="src_1", source_content_sha256=SHA_A,
            chunks=(first, second), source_markdown="abcdef", spec=spec,
        )
        self.assertEqual(("chunk_1", "chunk_2"), chunk_set.chunk_ids)
        self.assertEqual("abcdef", "".join(item.content for item in chunk_set.chunks))
        with self.assertRaises(ValueError):
            ChunkSet(
                source_document_id="src_1", source_content_sha256=SHA_A,
                chunks=(first, dataclasses.replace(second, sequence=2)),
                source_markdown="abcdef", spec=spec,
            )
        with self.assertRaises(ValueError):
            ChunkSet(
                source_document_id="src_1", source_content_sha256=SHA_A,
                chunks=(first, second), source_markdown="abcXdef", spec=spec,
            )
        with self.assertRaises(ValueError):
            ChunkSet(
                source_document_id="src_1", source_content_sha256=SHA_A,
                chunks=(first, dataclasses.replace(second, content_sha256=SHA_A)),
                source_markdown="abcdef", spec=spec,
            )

    def test_context_request_batch_plan_and_artifact_kinds_are_strict(self) -> None:
        from backend.app.domain.context import (
            ContextBatch, ContextPlan, ContextRequest, ChunkingSpec,
        )
        from backend.app.domain.entities import ArtifactKind, DocumentChunk

        chunk = DocumentChunk(
            id="chunk_1", source_document_id="src_1", sequence=0,
            heading_path='["Methods"]', page_start=None, page_end=None,
            content="one", content_sha256=SHA_A, token_count=1,
        )
        request = ContextRequest(
            source_document_id="src_1", consumer=ArtifactKind.TRANSLATION,
            budget_tokens=1600,
        )
        self.assertEqual("translation", request.consumer.value)
        batch = ContextBatch(sequence=0, chunk_ids=("chunk_1",), chunks=(chunk,), token_count=1)
        plan = ContextPlan(
            source_document_id="src_1", source_content_sha256=SHA_A,
            chunking_version=ChunkingSpec().chunking_version,
            all_chunk_ids=("chunk_1",), eligible_chunk_ids=("chunk_1",),
            selected_chunk_ids=("chunk_1",), batches=(batch,),
        )
        self.assertEqual(("chunk_1",), plan.selected_chunk_ids)
        self.assertEqual("translation", plan.request_consumer)
        self.assertEqual(
            hashlib.sha256(b"one").hexdigest(),
            plan.covered_content_sha256,
        )
        with self.assertRaises(ValueError):
            ContextRequest(source_document_id="src_1", consumer="not-a-kind")
        with self.assertRaises(ValueError):
            ContextBatch(sequence=0, chunk_ids=("chunk_1",), chunks=(), token_count=1)

    def test_embedding_context_policy_does_not_extend_p1_artifact_kind(self) -> None:
        from backend.app.domain.context import ContextRequest
        from backend.app.domain.entities import ArtifactKind

        request = ContextRequest(
            source_document_id="src_1",
            consumer="embedding",
        )

        self.assertEqual("embedding", request.consumer)
        self.assertNotIn("embedding", {kind.value for kind in ArtifactKind})
        self.assertEqual(7, len(ArtifactKind))

    def test_embedding_and_search_values_reject_invalid_vectors_queries_and_limits(self) -> None:
        from backend.app.domain.context import (
            EmbeddingBatch, EmbeddingProfile, EmbeddingRequest, SearchCoverage,
            SearchHit, SearchMode, SearchRequest,
        )
        from backend.app.domain import SearchQueryTooShortError

        profile = EmbeddingProfile(
            provider="local", model="static-v1", embedding_version="v1", dimensions=2,
        )
        request = EmbeddingRequest(
            profile=profile, texts=("first", "second"),
            chunk_ids=("chunk_1", "chunk_2"),
        )
        batch = EmbeddingBatch(
            profile=profile, vectors=((0.6, 0.8), (1.0, 0.0)),
            chunk_ids=request.chunk_ids,
        )
        self.assertEqual(2, len(batch.vectors))
        self.assertEqual(2, batch.profile.dimensions)
        with self.assertRaises(ValueError):
            EmbeddingBatch(profile=profile, vectors=((math.nan, 0.0),), chunk_ids=("chunk_1",))
        with self.assertRaises(ValueError):
            EmbeddingRequest(profile=profile, texts=("only",), chunk_ids=("a", "b"))
        with self.assertRaises(ValueError):
            EmbeddingProfile(provider="", model="m", embedding_version="v", dimensions=2)

        search = SearchRequest(query="机器学习模型", mode=SearchMode.LEXICAL, limit=20)
        self.assertEqual("lexical", search.mode.value)
        hit = SearchHit(
            paper_id="paper-1", source_document_id="src_1", chunk_id="chunk_1",
            sequence=0, heading_path=("Methods",), page_start=1, page_end=1,
            excerpt="机器学习模型", score=0.5, lexical_score=1.0, semantic_score=None,
        )
        coverage = SearchCoverage(ready_chunks=1, embedded_chunks=0, stale_chunks=0, failed_embeddings=0)
        self.assertEqual("paper-1", hit.paper_id)
        self.assertEqual(1, coverage.ready_chunks)
        for query in ("", "a", "ab", "  x "):
            with self.subTest(query=query), self.assertRaises(SearchQueryTooShortError):
                SearchRequest(query=query, mode="lexical")
        with self.assertRaises(ValueError):
            SearchRequest(query="valid query", mode="invalid")
        with self.assertRaises(ValueError):
            SearchRequest(query="valid query", mode="lexical", limit=0)

    def test_identity_builders_are_sensitive_and_cross_process_stable(self) -> None:
        from backend.app.domain.processing import (
            build_artifact_key, build_index_job_key, hash_canonical_json,
        )

        base = dict(
            source_document_id="src_1", source_content_sha256=SHA_A,
            embedding_provider="local", embedding_model="embed-v1",
            embedding_version="v1", chunking_version="markdown-coverage-v1",
            include_embeddings=True, embedding_options={"batchSize": 8},
        )
        key = build_index_job_key(**base)
        self.assertEqual(64, len(key))
        self.assertNotEqual(
            key,
            build_index_job_key(
                **{
                    **base,
                    "include_embeddings": False,
                    "embedding_provider": "none",
                    "embedding_model": "none",
                    "embedding_version": "none",
                }
            ),
        )
        self.assertNotEqual(key, build_index_job_key(**{**base, "chunking_version": "other"}))
        self.assertNotEqual(
            key, build_index_job_key(**{**base, "embedding_options": {"batchSize": 16}})
        )
        artifact_keys = {
            build_artifact_key(
                kind=kind, source_document_id="src_1", source_content_sha256=SHA_A,
                generator_provider="llm", generator_model="m", prompt_version="p1",
                kind_specific_options={"kind": kind},
            )
            for kind in ("explainer", "translation", "summary", "classification", "metadata")
        }
        self.assertEqual(5, len(artifact_keys))
        source = (
            "from backend.app.domain.processing import build_index_job_key; "
            "print(build_index_job_key(source_document_id='src_1',source_content_sha256='" + SHA_A
            + "',embedding_provider='local',embedding_model='embed-v1',embedding_version='v1',"
            "chunking_version='markdown-coverage-v1',include_embeddings=True,embedding_options={'batchSize':8}))"
        )
        child = subprocess.run([sys.executable, "-c", source], capture_output=True, text=True, check=True)
        self.assertEqual(key, child.stdout.strip())

    def test_rrf_golden_and_plan_coverage_hash_are_deterministic(self) -> None:
        from backend.app.domain.context import reciprocal_rank_fusion

        fused = reciprocal_rank_fusion(
            lexical=("chunk-a", "chunk-b", "chunk-c"),
            semantic=("chunk-c", "chunk-a", "chunk-d"),
        )
        self.assertEqual(("chunk-a", "chunk-c", "chunk-b", "chunk-d"), tuple(item[0] for item in fused))
        self.assertEqual(fused, reciprocal_rank_fusion(
            lexical=("chunk-a", "chunk-b", "chunk-c"),
            semantic=("chunk-c", "chunk-a", "chunk-d"),
        ))

    def test_embedding_and_repository_ports_are_protocol_only_public_seams(self) -> None:
        embedding = importlib.import_module(
            "backend.app.application.ports.embedding_provider"
        )
        repositories = importlib.import_module(
            "backend.app.application.ports.repositories"
        )
        self.assertTrue(getattr(embedding.EmbeddingProvider, "_is_protocol", False))
        self.assertEqual(
            {"embed", "provider_id"},
            {
                name
                for name, value in inspect.getmembers(embedding.EmbeddingProvider)
                if (inspect.isfunction(value) or isinstance(value, property))
                and not name.startswith("__")
            },
        )
        for name, methods in {
            "DocumentChunkCommandRepository": {
                "insert_set", "stale_other_versions",
            },
            "DocumentChunkQueryRepository": {"list_for_source"},
            "DocumentSearchQueryRepository": {
                "lexical", "semantic_candidates", "coverage",
            },
            "TranslationCheckpointRepository": {
                "get", "list_succeeded", "save_success", "save_failure",
            },
        }.items():
            protocol = getattr(repositories, name)
            self.assertTrue(getattr(protocol, "_is_protocol", False), name)
            observed = {
                method
                for method, value in inspect.getmembers(protocol)
                if inspect.isfunction(value) and not method.startswith("__")
            }
            self.assertEqual(methods, observed, name)


class DeterministicChunkingTests(unittest.TestCase):
    def test_plain_single_line_falls_back_to_token_boundaries_at_hard_cap(self) -> None:
        from backend.app.application.context_builder import chunk_markdown
        from backend.app.domain.context import ChunkingSpec

        markdown = " ".join(f"token{index}" for index in range(1601))
        result = chunk_markdown(
            source_document_id="src_plain_hard_cap",
            source_content_sha256=hashlib.sha256(markdown.encode("utf-8")).hexdigest(),
            markdown=markdown,
            spec=ChunkingSpec(),
        )

        self.assertEqual(2, len(result.chunks))
        self.assertEqual(markdown, "".join(chunk.content for chunk in result.chunks))
        self.assertEqual((0, 1), tuple(chunk.sequence for chunk in result.chunks))
        self.assertTrue(all(chunk.content_kind == "text" for chunk in result.chunks))
        self.assertTrue(all(chunk.token_count <= 1600 for chunk in result.chunks))
        self.assertEqual(result.chunks[0].char_end, result.chunks[1].char_start)

    def test_markdown_chunker_preserves_structures_offsets_and_stable_ids(self) -> None:
        from backend.app.application.context_builder import chunk_markdown
        from backend.app.domain.context import ChunkingSpec

        markdown = (
            "# Intro\n\n"
            "这是一个机器学习模型，用于 robust evaluation。\n\n"
            "```python\n"
            "print('fenced sentinel')\n"
            "```\n\n"
            "| name | score |\n| --- | --- |\n| model | $x^2$ |\n\n"
            "$$\\alpha + \\beta$$\n\n"
            "## Conclusion\n\n后部结论 sentinel-tail。"
        )
        source_sha = hashlib.sha256(markdown.encode("utf-8")).hexdigest()
        result = chunk_markdown(
            source_document_id="src_1",
            source_content_sha256=source_sha,
            markdown=markdown,
            spec=ChunkingSpec(target_tokens=12, hard_cap_tokens=12),
        )
        self.assertEqual(markdown, "".join(chunk.content for chunk in result.chunks))
        self.assertEqual(tuple(range(len(result.chunks))), tuple(chunk.sequence for chunk in result.chunks))
        self.assertEqual(0, result.chunks[0].char_start)
        for previous, current in zip(result.chunks, result.chunks[1:]):
            self.assertEqual(previous.char_end, current.char_start)
            self.assertEqual(current.char_start, markdown.index(current.content, previous.char_end))
        self.assertTrue(any(chunk.content_kind == "verbatim" and "fenced sentinel" in chunk.content for chunk in result.chunks))
        self.assertTrue(any(chunk.content_kind == "structured" and "| model |" in chunk.content for chunk in result.chunks))
        self.assertTrue(any(chunk.content_kind == "verbatim" and "\\alpha" in chunk.content for chunk in result.chunks))
        self.assertTrue(any("Conclusion" in chunk.heading_path and "sentinel-tail" in chunk.content for chunk in result.chunks))
        self.assertTrue(all(chunk.content_sha256 == hashlib.sha256(chunk.content.encode("utf-8")).hexdigest() for chunk in result.chunks))
        self.assertTrue(all(chunk.chunk_key and chunk.id == "chunk_" + chunk.chunk_key[:32] for chunk in result.chunks))
        self.assertTrue(
            all(
                chunk.token_count <= 12
                or chunk.content_kind in {"verbatim", "structured"}
                for chunk in result.chunks
            )
        )

        child = subprocess.run(
            [
                sys.executable,
                "-c",
                "from backend.app.application.context_builder import chunk_markdown; "
                "from backend.app.domain.context import ChunkingSpec; import hashlib; "
                "m='" + markdown.replace("\\", "\\\\").replace("'", "\\'").replace("\n", "\\n") + "'; "
                "s=hashlib.sha256(m.encode()).hexdigest(); "
                "print(','.join(c.id for c in chunk_markdown(source_document_id='src_1',source_content_sha256=s,markdown=m,spec=ChunkingSpec(target_tokens=12,hard_cap_tokens=12)).chunks))",
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        self.assertEqual(",".join(chunk.id for chunk in result.chunks), child.stdout.strip())

    def test_display_math_closes_on_standalone_and_single_line_delimiters(self) -> None:
        from backend.app.application.context_builder import chunk_markdown
        from backend.app.domain.context import ChunkingSpec

        markdown = (
            "$$\n"
            "x + y\n"
            "$$\n\n"
            "plain after block sentinel.\n\n"
            "$$z^2$$\n\n"
            "tail sentinel.\n"
        )
        result = chunk_markdown(
            source_document_id="src_display_math",
            source_content_sha256=hashlib.sha256(markdown.encode("utf-8")).hexdigest(),
            markdown=markdown,
            spec=ChunkingSpec(target_tokens=8, hard_cap_tokens=8),
        )

        self.assertEqual(markdown, "".join(chunk.content for chunk in result.chunks))
        block = next(chunk for chunk in result.chunks if "x + y" in chunk.content)
        inline_block = next(chunk for chunk in result.chunks if "$$z^2$$" in chunk.content)
        self.assertEqual("verbatim", block.content_kind)
        self.assertEqual("verbatim", inline_block.content_kind)
        self.assertNotIn("plain after block sentinel", block.content)
        self.assertNotIn("tail sentinel", inline_block.content)

    def test_display_math_with_same_line_tail_remains_translation_eligible(self) -> None:
        from backend.app.application.context_builder import chunk_markdown
        from backend.app.domain.context import ChunkingSpec

        markdown = (
            "$$x + y$$ first tail sentinel.\n\n"
            "\\[z^2\\] second tail sentinel.\n"
        )
        result = chunk_markdown(
            source_document_id="src_display_math_tail",
            source_content_sha256=hashlib.sha256(markdown.encode("utf-8")).hexdigest(),
            markdown=markdown,
            spec=ChunkingSpec(target_tokens=8, hard_cap_tokens=8),
        )

        self.assertEqual(markdown, "".join(chunk.content for chunk in result.chunks))
        for sentinel in ("first tail sentinel", "second tail sentinel"):
            chunk = next(chunk for chunk in result.chunks if sentinel in chunk.content)
            self.assertEqual("structured", chunk.content_kind)

    def test_atomic_block_over_hard_cap_fails_closed(self) -> None:
        from backend.app.application.context_builder import chunk_markdown
        from backend.app.domain.context import ChunkingSpec

        markdown = "```\n" + ("token " * 8200) + "\n```"
        with self.assertRaises(ValueError) as raised:
            chunk_markdown(
                source_document_id="src_1",
                source_content_sha256=hashlib.sha256(markdown.encode()).hexdigest(),
                markdown=markdown,
                spec=ChunkingSpec(),
            )
        self.assertIn("CHUNK_ATOMIC_BLOCK_TOO_LARGE", str(raised.exception))


class ChunkMaterializationTests(unittest.IsolatedAsyncioTestCase):
    async def test_ready_source_materializes_once_and_reuses_exact_winner(self) -> None:
        from backend.app.application.context_builder import ContextBuilder
        from backend.app.domain import SourceDocument
        from backend.app.domain.context import ChunkingSpec
        from backend.app.repositories.unit_of_work import SqlAlchemyUnitOfWork
        from backend.tests.support.p3_database import p3_database_fixture

        markdown = "# Intro\n\n机器学习 source。\n\n## Conclusion\n\ntail sentinel。"
        content_sha = hashlib.sha256(markdown.encode("utf-8")).hexdigest()
        async with p3_database_fixture(prefix="study-app-p3-chunks-") as fixture:
            factory = lambda: SqlAlchemyUnitOfWork(fixture.session_factory)
            async with factory() as work:
                await work.sources.add(
                    SourceDocument(
                        id="src_1", paper_id="paper-1", mode="native", status="ready",
                        provider="local", model="pymupdf4llm-pymupdf",
                        pdf_sha256=SHA_A, options_hash=SHA_B,
                        processing_version="native-v1", created_at=NOW, updated_at=NOW,
                        markdown=markdown, content_sha256=content_sha, page_count=1,
                    )
                )
                await work.commit()

            builder = ContextBuilder(factory)
            first = await builder.materialize_chunks(
                "src_1", ChunkingSpec(target_tokens=8, hard_cap_tokens=8), now=NOW,
            )
            second = await builder.materialize_chunks(
                "src_1", ChunkingSpec(target_tokens=8, hard_cap_tokens=8), now=NOW,
            )
            self.assertEqual(first, second)
            self.assertEqual(markdown, "".join(chunk.content for chunk in first.chunks))
            async with factory() as work:
                persisted = await work.chunks.list_for_source("src_1", status="ready")
            self.assertEqual(first.chunks, persisted)

    async def test_nonready_or_hash_mismatched_source_fails_without_rows(self) -> None:
        from backend.app.application.context_builder import ContextBuilder
        from backend.app.domain import SourceDocument
        from backend.app.domain.context import ChunkingSpec
        from backend.app.repositories.unit_of_work import SqlAlchemyUnitOfWork
        from backend.tests.support.p3_database import p3_database_fixture

        async with p3_database_fixture(prefix="study-app-p3-chunks-invalid-") as fixture:
            factory = lambda: SqlAlchemyUnitOfWork(fixture.session_factory)
            async with factory() as work:
                await work.sources.add(
                    SourceDocument(
                        id="src_pending", paper_id="paper-1", mode="native", status="queued",
                        provider="local", model="pymupdf4llm-pymupdf",
                        pdf_sha256=SHA_A, options_hash=SHA_B,
                        processing_version="native-v1", created_at=NOW, updated_at=NOW,
                    )
                )
                await work.commit()
            with self.assertRaises(Exception):
                await ContextBuilder(factory).materialize_chunks(
                    "src_pending", ChunkingSpec(), now=NOW,
                )
            async with factory() as work:
                self.assertEqual((), await work.chunks.list_for_source("src_pending"))


class ContextPlanCoverageTests(unittest.IsolatedAsyncioTestCase):
    async def test_translation_covers_every_ready_chunk_once_without_writes(self) -> None:
        from backend.app.domain.context import ChunkingSpec, ContextRequest
        from backend.app.infrastructure.database_backup import inspect_database
        from backend.tests.support.p3_database import p3_context_fixture

        markdown = (
            "# Abstract\n\nfirst context sentinel.\n\n"
            "## Methods\n\nsecond context sentinel.\n\n"
            "## Conclusion\n\ntail context sentinel.\n"
        )
        spec = ChunkingSpec(target_tokens=5, hard_cap_tokens=5)
        async with p3_context_fixture(
            prefix="study-app-p3-context-translation-",
            source_id="src_translation",
            markdown=markdown,
            spec=spec,
            now=NOW,
            pdf_sha256=SHA_A,
            options_hash=SHA_B,
        ) as fixture:
            builder = fixture.builder
            materialized = fixture.chunk_set
            before = inspect_database(fixture.database_path)
            plan = await builder.build(
                "src_translation",
                ContextRequest(
                    source_document_id="src_translation",
                    consumer="translation",
                    chunking_version=spec.chunking_version,
                ),
            )
            after = inspect_database(fixture.database_path)

            self.assertEqual(materialized.chunk_ids, plan.all_chunk_ids)
            self.assertEqual(plan.all_chunk_ids, plan.eligible_chunk_ids)
            self.assertEqual(plan.all_chunk_ids, plan.selected_chunk_ids)
            self.assertEqual(
                tuple((chunk_id,) for chunk_id in materialized.chunk_ids),
                tuple(batch.chunk_ids for batch in plan.batches),
            )
            self.assertEqual("translation", plan.request_consumer)
            self.assertEqual(materialized.source_content_sha256, plan.covered_content_sha256)
            self.assertEqual(before.logical_sha256, after.logical_sha256)
            self.assertEqual(before.table_counts, after.table_counts)
            self.assertEqual(before.table_sha256, after.table_sha256)
            self.assertEqual(before.content_counts, after.content_counts)
            self.assertEqual(before.content_sha256, after.content_sha256)

    async def test_embedding_covers_all_ready_chunks_with_bounded_batches(self) -> None:
        from backend.app.domain.context import ChunkingSpec, ContextRequest
        from backend.app.infrastructure.database_backup import inspect_database
        from backend.tests.support.p3_database import p3_context_fixture

        markdown = "".join(
            f"## Section {index}\n\nembedding sentinel {index}.\n\n"
            for index in range(12)
        )
        spec = ChunkingSpec(target_tokens=5, hard_cap_tokens=5)
        async with p3_context_fixture(
            prefix="study-app-p3-context-embedding-",
            source_id="src_embedding",
            markdown=markdown,
            spec=spec,
            now=NOW,
            pdf_sha256=SHA_A,
            options_hash=SHA_B,
        ) as fixture:
            before = inspect_database(fixture.database_path)
            plan = await fixture.builder.build(
                "src_embedding",
                ContextRequest(
                    source_document_id="src_embedding",
                    consumer="embedding",
                    budget_tokens=12,
                    chunking_version=spec.chunking_version,
                ),
            )
            after = inspect_database(fixture.database_path)

            self.assertEqual(fixture.chunk_set.chunk_ids, plan.all_chunk_ids)
            self.assertEqual(plan.all_chunk_ids, plan.eligible_chunk_ids)
            self.assertEqual(plan.all_chunk_ids, plan.selected_chunk_ids)
            self.assertEqual(
                plan.selected_chunk_ids,
                tuple(chunk_id for batch in plan.batches for chunk_id in batch.chunk_ids),
            )
            self.assertTrue(all(batch.token_count <= 12 for batch in plan.batches))
            self.assertEqual("embedding", plan.request_consumer)
            self.assertEqual(before.logical_sha256, after.logical_sha256)
            self.assertEqual(before.table_sha256, after.table_sha256)
            self.assertEqual(before.content_sha256, after.content_sha256)

    async def test_summary_maps_every_body_chunk_and_excludes_reference_or_acknowledgement_sections(self) -> None:
        import json

        from backend.app.domain.context import ChunkingSpec, ContextRequest
        from backend.app.infrastructure.database_backup import inspect_database
        from backend.tests.support.p3_database import p3_context_fixture

        markdown = (
            "# Abstract\n\nfront summary sentinel.\n\n"
            "# Methods\n\nmethod summary sentinel.\n\n"
            "# References\n\nreferences poison sentinel.\n\n"
            "# Conclusion\n\ntail summary sentinel.\n\n"
            "# 致谢\n\nacknowledgement poison sentinel.\n"
        )
        spec = ChunkingSpec(target_tokens=6, hard_cap_tokens=6)
        async with p3_context_fixture(
            prefix="study-app-p3-context-summary-",
            source_id="src_summary",
            markdown=markdown,
            spec=spec,
            now=NOW,
            pdf_sha256=SHA_A,
            options_hash=SHA_B,
        ) as fixture:
            blocked_headings = {"references", "致谢"}
            expected_chunks = tuple(
                chunk
                for chunk in fixture.chunk_set.chunks
                if not blocked_headings.intersection(
                    heading.casefold()
                    for heading in json.loads(chunk.heading_path or "[]")
                )
            )
            excluded_chunks = tuple(
                chunk
                for chunk in fixture.chunk_set.chunks
                if chunk not in expected_chunks
            )
            before = inspect_database(fixture.database_path)
            plan = await fixture.builder.build(
                "src_summary",
                ContextRequest(
                    source_document_id="src_summary",
                    consumer="summary",
                    chunking_version=spec.chunking_version,
                ),
            )
            after = inspect_database(fixture.database_path)

            self.assertEqual(fixture.chunk_set.chunk_ids, plan.all_chunk_ids)
            self.assertEqual(tuple(chunk.id for chunk in expected_chunks), plan.eligible_chunk_ids)
            self.assertEqual(plan.eligible_chunk_ids, plan.selected_chunk_ids)
            self.assertEqual(
                tuple((chunk.id,) for chunk in expected_chunks),
                tuple(batch.chunk_ids for batch in plan.batches),
            )
            self.assertTrue(all(batch.level == "map" for batch in plan.batches))
            selected_content = "".join(
                chunk.content for batch in plan.batches for chunk in batch.chunks
            )
            self.assertIn("tail summary sentinel", selected_content)
            self.assertNotIn("poison sentinel", selected_content)
            self.assertTrue(
                all(any(chunk.id in span for span, _ in plan.excluded_reasons) for chunk in excluded_chunks)
            )
            self.assertEqual("summary", plan.request_consumer)
            self.assertEqual(before.logical_sha256, after.logical_sha256)
            self.assertEqual(before.table_sha256, after.table_sha256)
            self.assertEqual(before.content_sha256, after.content_sha256)

    async def test_explainer_covers_priority_sections_and_complete_long_section_ranges(self) -> None:
        import json

        from backend.app.domain.context import ChunkingSpec, ContextRequest
        from backend.app.infrastructure.database_backup import inspect_database
        from backend.tests.support.p3_database import p3_context_fixture

        markdown = (
            "# Abstract\n\nfront explain sentinel.\n\n"
            "# Related Work\n\nirrelevant poison sentinel.\n\n"
            "# Methods\n\n"
            + "".join(f"method child sentinel {index}.\n\n" for index in range(8))
            + "# References\n\nreferences poison sentinel.\n\n"
            "# Discussion\n\ntail discussion sentinel.\n\n"
            "# 6 Conclusion\n\ntail conclusion sentinel.\n"
        )
        spec = ChunkingSpec(target_tokens=6, hard_cap_tokens=6)
        async with p3_context_fixture(
            prefix="study-app-p3-context-explainer-",
            source_id="src_explainer",
            markdown=markdown,
            spec=spec,
            now=NOW,
            pdf_sha256=SHA_A,
            options_hash=SHA_B,
        ) as fixture:
            priority_headings = {"abstract", "methods", "discussion", "6 conclusion"}
            expected_chunks = tuple(
                chunk
                for chunk in fixture.chunk_set.chunks
                if priority_headings.intersection(
                    heading.casefold()
                    for heading in json.loads(chunk.heading_path or "[]")
                )
            )
            method_chunks = tuple(
                chunk
                for chunk in fixture.chunk_set.chunks
                if "methods" in {
                    heading.casefold()
                    for heading in json.loads(chunk.heading_path or "[]")
                }
            )
            before = inspect_database(fixture.database_path)
            plan = await fixture.builder.build(
                "src_explainer",
                ContextRequest(
                    source_document_id="src_explainer",
                    consumer="explainer",
                    chunking_version=spec.chunking_version,
                ),
            )
            after = inspect_database(fixture.database_path)

            self.assertEqual(tuple(chunk.id for chunk in expected_chunks), plan.eligible_chunk_ids)
            self.assertEqual(plan.eligible_chunk_ids, plan.selected_chunk_ids)
            self.assertEqual(
                plan.selected_chunk_ids,
                tuple(chunk_id for batch in plan.batches for chunk_id in batch.chunk_ids),
            )
            self.assertTrue(all(batch.level == "section" for batch in plan.batches))
            selected_content = "".join(
                chunk.content for batch in plan.batches for chunk in batch.chunks
            )
            self.assertIn("tail discussion sentinel", selected_content)
            self.assertIn("tail conclusion sentinel", selected_content)
            self.assertNotIn("poison sentinel", selected_content)
            method_batch = next(
                batch
                for batch in plan.batches
                if any("method child sentinel" in chunk.content for chunk in batch.chunks)
            )
            self.assertEqual(tuple(chunk.id for chunk in method_chunks), method_batch.chunk_ids)
            self.assertEqual(
                tuple((chunk.char_start, chunk.char_end) for chunk in method_chunks),
                method_batch.covered_ranges,
            )
            self.assertEqual("explainer", plan.request_consumer)
            self.assertEqual(before.logical_sha256, after.logical_sha256)
            self.assertEqual(before.table_sha256, after.table_sha256)
            self.assertEqual(before.content_sha256, after.content_sha256)

    async def test_explainer_splits_oversized_sections_into_bounded_map_batches(self) -> None:
        import json

        from backend.app.domain.context import ChunkingSpec, ContextRequest
        from backend.tests.support.p3_database import p3_context_fixture

        markdown = (
            "# Methods\n\n"
            + "".join(
                f"bounded methods sentinel {index} with enough independent context.\n\n"
                for index in range(220)
            )
            + "# Conclusion\n\nbounded conclusion tail sentinel.\n"
        )
        spec = ChunkingSpec(target_tokens=12, hard_cap_tokens=12)
        async with p3_context_fixture(
            prefix="study-app-p3-context-explainer-bounded-",
            source_id="src_explainer_bounded",
            markdown=markdown,
            spec=spec,
            now=NOW,
            pdf_sha256=SHA_A,
            options_hash=SHA_B,
        ) as fixture:
            plan = await fixture.builder.build(
                "src_explainer_bounded",
                ContextRequest(
                    source_document_id="src_explainer_bounded",
                    consumer="explainer",
                    chunking_version=spec.chunking_version,
                ),
            )

        method_chunks = tuple(
            chunk
            for chunk in fixture.chunk_set.chunks
            if "methods" in {
                heading.casefold()
                for heading in json.loads(chunk.heading_path or "[]")
            }
        )
        method_ids = {chunk.id for chunk in method_chunks}
        method_batches = tuple(
            batch
            for batch in plan.batches
            if any(chunk.id in method_ids for chunk in batch.chunks)
        )
        self.assertGreater(len(method_batches), 1)
        self.assertTrue(all(batch.token_count <= 1600 for batch in method_batches))
        self.assertEqual(
            tuple(chunk.id for chunk in method_chunks),
            tuple(chunk.id for batch in method_batches for chunk in batch.chunks),
        )
        self.assertIn(
            "bounded conclusion tail sentinel",
            "".join(chunk.content for batch in plan.batches for chunk in batch.chunks),
        )

    async def test_classification_selects_bounded_semantic_categories_and_tail_conclusion(self) -> None:
        from backend.app.domain.context import ChunkingSpec, ContextRequest
        from backend.app.infrastructure.database_backup import inspect_database
        from backend.tests.support.p3_database import p3_context_fixture

        markdown = (
            "# classification title sentinel\n\n"
            "title details must not replace the first-chunk fallback.\n\n"
            "# Abstract\n\nclassification abstract sentinel.\n\n"
            "# Introduction\n\nintroduction poison sentinel.\n\n"
            "# Methods Overview\n\nclassification method sentinel.\n\n"
            "# Experiments\n\n"
            + "".join(
                f"## Experiment {index}\n\nclassification experiment poison {index}.\n\n"
                for index in range(30)
            )
            + "# Conclusion\n\ntail classification sentinel.\n\n"
            "# References\n\nclassification references poison.\n"
        )
        spec = ChunkingSpec(target_tokens=6, hard_cap_tokens=6)
        async with p3_context_fixture(
            prefix="study-app-p3-context-classification-",
            source_id="src_classification",
            markdown=markdown,
            spec=spec,
            now=NOW,
            pdf_sha256=SHA_A,
            options_hash=SHA_B,
        ) as fixture:
            before = inspect_database(fixture.database_path)
            plan = await fixture.builder.build(
                "src_classification",
                ContextRequest(
                    source_document_id="src_classification",
                    consumer="classification",
                    chunking_version=spec.chunking_version,
                ),
            )
            after = inspect_database(fixture.database_path)

            selected_content = "".join(
                chunk.content for batch in plan.batches for chunk in batch.chunks
            )
            self.assertIn("classification title sentinel", selected_content)
            self.assertIn("classification abstract sentinel", selected_content)
            self.assertIn("classification method sentinel", selected_content)
            self.assertIn("tail classification sentinel", selected_content)
            self.assertNotIn("poison", selected_content)
            self.assertLessEqual(plan.total_tokens, 3200)
            self.assertEqual(
                plan.selected_chunk_ids,
                tuple(chunk_id for batch in plan.batches for chunk_id in batch.chunk_ids),
            )
            self.assertTrue(plan.excluded_reasons)
            self.assertEqual("classification", plan.request_consumer)
            self.assertEqual(before.logical_sha256, after.logical_sha256)
            self.assertEqual(before.table_sha256, after.table_sha256)
            self.assertEqual(before.content_sha256, after.content_sha256)

    async def test_classification_uses_the_frozen_per_category_budget_split(self) -> None:
        """The 3200-token total must not move capacity from Conclusion to front matter."""

        from backend.app.domain.context import ChunkingSpec, ContextRequest
        from backend.tests.support.p3_database import p3_context_fixture

        front_words = " ".join(f"frontword{index}" for index in range(700))
        conclusion_words = " ".join(
            f"conclusionword{index}" for index in range(700)
        )
        markdown = (
            f"front-budget-sentinel {front_words}.\n\n"
            "# Conclusion\n\n"
            f"conclusion-budget-sentinel {conclusion_words}.\n"
        )
        spec = ChunkingSpec(target_tokens=800, hard_cap_tokens=800)
        async with p3_context_fixture(
            prefix="study-app-p3-context-classification-budget-split-",
            source_id="src_classification_budget_split",
            markdown=markdown,
            spec=spec,
            now=NOW,
            pdf_sha256=SHA_A,
            options_hash=SHA_B,
        ) as fixture:
            plan = await fixture.builder.build(
                fixture.chunk_set.source_document_id,
                ContextRequest(
                    source_document_id=fixture.chunk_set.source_document_id,
                    consumer="classification",
                    chunking_version=spec.chunking_version,
                ),
            )

        selected_content = "".join(
            chunk.content for batch in plan.batches for chunk in batch.chunks
        )
        self.assertNotIn("front-budget-sentinel", selected_content)
        self.assertIn(
            "conclusion-budget-sentinel",
            selected_content,
        )
        reasons = dict(plan.excluded_reasons)
        self.assertIn("classification front budget exceeded", reasons.values())
        self.assertNotIn("classification conclusion budget exceeded", reasons.values())

    async def test_metadata_uses_only_first_page_or_first_chunk_fallback_with_fixed_budget(self) -> None:
        from backend.app.domain.context import ChunkingSpec, ContextRequest
        from backend.app.infrastructure.database_backup import inspect_database
        from backend.tests.support.p3_database import p3_context_fixture

        spec = ChunkingSpec(target_tokens=8, hard_cap_tokens=8)
        paged_markdown = (
            "[page 1]\n"
            "# metadata title sentinel\n\n"
            "authors and venue sentinel.\n\n"
            "[page 2]\n"
            "# Methods\n\nmetadata methods poison.\n\n"
            "# References\n\nmetadata references poison.\n"
        )
        async with p3_context_fixture(
            prefix="study-app-p3-context-metadata-paged-",
            source_id="src_metadata_paged",
            markdown=paged_markdown,
            spec=spec,
            now=NOW,
            pdf_sha256=SHA_A,
            options_hash=SHA_B,
        ) as fixture:
            expected_ids = tuple(
                chunk.id for chunk in fixture.chunk_set.chunks if chunk.page_start == 1
            )
            before = inspect_database(fixture.database_path)
            plan = await fixture.builder.build(
                "src_metadata_paged",
                ContextRequest(
                    source_document_id="src_metadata_paged",
                    consumer="metadata",
                    chunking_version=spec.chunking_version,
                ),
            )
            after = inspect_database(fixture.database_path)

            selected_content = "".join(
                chunk.content for batch in plan.batches for chunk in batch.chunks
            )
            self.assertEqual(expected_ids, plan.eligible_chunk_ids)
            self.assertEqual(expected_ids, plan.selected_chunk_ids)
            self.assertIn("metadata title sentinel", selected_content)
            self.assertIn("authors and venue sentinel", selected_content)
            self.assertNotIn("poison", selected_content)
            self.assertLessEqual(plan.total_tokens, 1600)
            self.assertEqual(before.logical_sha256, after.logical_sha256)
            self.assertEqual(before.table_sha256, after.table_sha256)
            self.assertEqual(before.content_sha256, after.content_sha256)

        fallback_markdown = (
            "# fallback metadata title sentinel\n\n"
            "fallback second chunk poison.\n\n"
            "# Methods\n\nfallback methods poison.\n"
        )
        async with p3_context_fixture(
            prefix="study-app-p3-context-metadata-fallback-",
            source_id="src_metadata_fallback",
            markdown=fallback_markdown,
            spec=spec,
            now=NOW,
            pdf_sha256=SHA_A,
            options_hash=SHA_B,
        ) as fixture:
            plan = await fixture.builder.build(
                "src_metadata_fallback",
                ContextRequest(
                    source_document_id="src_metadata_fallback",
                    consumer="metadata",
                    chunking_version=spec.chunking_version,
                ),
            )

            self.assertEqual((fixture.chunk_set.chunks[0].id,), plan.eligible_chunk_ids)
            self.assertEqual(plan.eligible_chunk_ids, plan.selected_chunk_ids)
            self.assertEqual((fixture.chunk_set.chunks[0].id,), plan.batches[0].chunk_ids)
            self.assertEqual("metadata", plan.request_consumer)
            self.assertTrue(plan.excluded_reasons)

    async def test_explainer_recognizes_english_aliases_and_chinese_priority_headings(self) -> None:
        from backend.app.domain.context import ChunkingSpec, ContextRequest
        from backend.tests.support.p3_database import p3_context_fixture

        markdown = (
            "# 摘要\n\n中文摘要 sentinel.\n\n"
            "# Background\n\nbackground alias sentinel.\n\n"
            "# 方法\n\n中文方法 sentinel.\n\n"
            "# Evaluation\n\nevaluation alias sentinel.\n\n"
            "# 讨论\n\n中文讨论 sentinel.\n\n"
            "# 结论\n\n中文结论 sentinel.\n\n"
            "# 参考文献\n\n中文参考 poison.\n"
        )
        spec = ChunkingSpec(target_tokens=6, hard_cap_tokens=6)
        async with p3_context_fixture(
            prefix="study-app-p3-context-explainer-aliases-",
            source_id="src_explainer_aliases",
            markdown=markdown,
            spec=spec,
            now=NOW,
            pdf_sha256=SHA_A,
            options_hash=SHA_B,
        ) as fixture:
            plan = await fixture.builder.build(
                "src_explainer_aliases",
                ContextRequest(
                    source_document_id="src_explainer_aliases",
                    consumer="explainer",
                    chunking_version=spec.chunking_version,
                ),
            )

            selected_content = "".join(
                chunk.content for batch in plan.batches for chunk in batch.chunks
            )
            for sentinel in (
                "中文摘要 sentinel",
                "background alias sentinel",
                "中文方法 sentinel",
                "evaluation alias sentinel",
                "中文讨论 sentinel",
                "中文结论 sentinel",
            ):
                self.assertIn(sentinel, selected_content)
            self.assertNotIn("中文参考 poison", selected_content)

    async def test_build_fails_closed_for_missing_stale_mixed_version_or_gapped_chunks(self) -> None:
        from sqlalchemy import update

        from backend.app.domain import DomainError
        from backend.app.domain.context import ChunkingSpec, ContextRequest
        from backend.app.infrastructure.database_backup import inspect_database
        from backend.app.repositories.models import DocumentChunkModel, SourceDocumentModel
        from backend.tests.support.p3_database import p3_context_fixture

        markdown = (
            "# Abstract\n\nfail closed first sentinel.\n\n"
            "# Methods\n\nfail closed second sentinel.\n\n"
            "# Conclusion\n\nfail closed tail sentinel.\n"
        )
        spec = ChunkingSpec(target_tokens=5, hard_cap_tokens=5)
        async with p3_context_fixture(
            prefix="study-app-p3-context-invalid-",
            source_id="src_invalid_context",
            markdown=markdown,
            spec=spec,
            now=NOW,
            pdf_sha256=SHA_A,
            options_hash=SHA_B,
        ) as fixture:
            request = ContextRequest(
                source_document_id="src_invalid_context",
                consumer="translation",
                chunking_version=spec.chunking_version,
            )

            async def mutate(statement) -> None:
                async with fixture.session_factory() as session:
                    await session.execute(statement)
                    await session.commit()

            async def assert_failure(error_code: str) -> None:
                before = inspect_database(fixture.database_path)
                with self.assertRaises(DomainError) as raised:
                    await fixture.builder.build("src_invalid_context", request)
                self.assertEqual(error_code, raised.exception.code)
                after = inspect_database(fixture.database_path)
                self.assertEqual(before.logical_sha256, after.logical_sha256)
                self.assertEqual(before.table_sha256, after.table_sha256)
                self.assertEqual(before.content_sha256, after.content_sha256)

            await mutate(
                update(DocumentChunkModel)
                .where(DocumentChunkModel.source_document_id == "src_invalid_context")
                .values(status="stale", stale_at=NOW.isoformat())
            )
            await assert_failure("SOURCE_CHUNKS_NOT_READY")
            await mutate(
                update(DocumentChunkModel)
                .where(DocumentChunkModel.source_document_id == "src_invalid_context")
                .values(status="ready", stale_at=None)
            )

            await mutate(
                update(SourceDocumentModel)
                .where(SourceDocumentModel.id == "src_invalid_context")
                .values(status="stale", stale_at=NOW.isoformat())
            )
            await assert_failure("SOURCE_NOT_READY")
            await mutate(
                update(SourceDocumentModel)
                .where(SourceDocumentModel.id == "src_invalid_context")
                .values(status="ready", stale_at=None)
            )

            changed_chunk = fixture.chunk_set.chunks[1]
            await mutate(
                update(DocumentChunkModel)
                .where(DocumentChunkModel.id == changed_chunk.id)
                .values(chunking_version="other-version")
            )
            await assert_failure("CHUNKING_VERSION_MISMATCH")
            await mutate(
                update(DocumentChunkModel)
                .where(DocumentChunkModel.id == changed_chunk.id)
                .values(chunking_version=spec.chunking_version)
            )

            await mutate(
                update(DocumentChunkModel)
                .where(DocumentChunkModel.id == changed_chunk.id)
                .values(char_start=changed_chunk.char_start + 1)
            )
            await assert_failure("CONTEXT_COVERAGE_INVALID")


if __name__ == "__main__":
    unittest.main()
