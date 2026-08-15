from __future__ import annotations

from contextlib import closing
from datetime import datetime, timezone
import hashlib
import math
import os
import struct
import unittest
from dataclasses import dataclass
from types import SimpleNamespace

if (
    os.name == "nt"
    and hasattr(os, "add_dll_directory")
    and os.environ.get("P3_SQLITE_DLL_DIR")
):
    _SQLITE_DLL_HANDLE = os.add_dll_directory(os.environ["P3_SQLITE_DLL_DIR"])

import sqlite3
from unittest import mock


NOW = datetime(2026, 8, 13, 18, 0, tzinfo=timezone.utc)


class SemanticSearchTests(unittest.IsolatedAsyncioTestCase):
    async def test_semantic_query_provider_failure_is_typed_and_redacted_at_http_boundary(
        self,
    ) -> None:
        from backend.app.api.app import create_app
        from backend.app.application.document_search import DocumentSearch
        from backend.app.domain.context import EmbeddingProfile
        from backend.app.repositories.document_search import (
            SqlAlchemyDocumentSearchRepository,
        )
        from backend.tests.support.p3_database import p3_database_fixture
        from fastapi.testclient import TestClient

        secret = "query-provider-secret-must-not-escape"
        profile = EmbeddingProfile(
            provider="query-failure-provider",
            model="query-failure-model",
            embedding_version="query-failure-v1",
            dimensions=2,
        )

        class FailingProvider:
            provider_id = profile.provider

            async def embed(self, _request):
                raise RuntimeError(f"raw query transport detail: {secret}")

        async with p3_database_fixture(
            prefix="study-app-p3-semantic-query-provider-failure-"
        ) as fixture:
            class Services:
                schema_revision = "20260807_03"
                document_search = DocumentSearch(
                    SqlAlchemyDocumentSearchRepository(fixture.session_factory),
                    query_embedding_profile=profile,
                    query_embedding_provider=FailingProvider(),
                )

                async def dispose(self) -> None:
                    await fixture.session_factory.kw["bind"].dispose()

            client_context = TestClient(
                create_app(
                    Services(),
                    fixture.session_factory,
                    required_schema_revision="20260807_03",
                ),
                raise_server_exceptions=False,
            )
            client = client_context.__enter__()
            try:
                response = client.post(
                    "/api/v2/search/chunks",
                    json={"query": "semantic tracer", "mode": "semantic"},
                )
            finally:
                client_context.__exit__(None, None, None)

        self.assertEqual(400, response.status_code, response.text)
        self.assertEqual("EMBEDDING_REQUEST_FAILED", response.json()["error"]["code"])
        self.assertNotIn(secret, response.text)

    async def test_cross_source_embedding_provenance_is_excluded_from_results_and_coverage(
        self,
    ) -> None:
        from backend.app.domain import SourceDocument
        from backend.app.domain.context import (
            ChunkingSpec,
            EmbeddingProfile,
            SearchRequest,
        )
        from backend.app.repositories.document_search import (
            SqlAlchemyDocumentSearchRepository,
        )
        from backend.tests.support.p3_database import p3_context_fixture

        profile = EmbeddingProfile(
            provider="provenance-provider",
            model="provenance-model",
            embedding_version="provenance-v1",
            dimensions=2,
        )
        async with p3_context_fixture(
            prefix="study-app-p3-semantic-provenance-",
            source_id="src_provenance_owner",
            markdown="shared provenance needle.\n",
            spec=ChunkingSpec(target_tokens=8, hard_cap_tokens=8),
            now=NOW,
            pdf_sha256="a" * 64,
            options_hash="b" * 64,
        ) as fixture:
            chunk = fixture.chunk_set.chunks[0]
            spoof_source_id = "src_provenance_spoof"
            async with fixture.unit_of_work_factory() as work:
                await work.sources.add(
                    SourceDocument(
                        id=spoof_source_id,
                        paper_id="paper-1",
                        mode="ocr",
                        status="ready",
                        provider="fake-ocr",
                        model="fake-ocr-model",
                        pdf_sha256="c" * 64,
                        options_hash="d" * 64,
                        processing_version="ocr-v1",
                        created_at=NOW,
                        updated_at=NOW,
                        markdown="spoof source markdown.\n",
                        content_sha256=hashlib.sha256(
                            b"spoof source markdown.\n"
                        ).hexdigest(),
                        page_count=1,
                    )
                )
                await work.commit()
            spoof_chunk_set = await fixture.builder.materialize_chunks(
                spoof_source_id,
                ChunkingSpec(target_tokens=8, hard_cap_tokens=8),
                now=NOW,
            )
            spoof_chunk = spoof_chunk_set.chunks[0]
            packed = struct.pack("<2f", 1.0, 0.0)
            timestamp = NOW.isoformat().replace("+00:00", "Z")
            with closing(sqlite3.connect(fixture.database_path)) as connection:
                connection.execute(
                    "INSERT INTO document_chunk_embeddings("
                    "id,chunk_id,source_document_id,provider,model,embedding_version,"
                    "dimensions,vector,vector_sha256,chunk_content_sha256,status,"
                    "error_code,error_message,created_at,updated_at,stale_at) VALUES("
                    "?,?,?,?,?,?,?,?,?,?,'ready',NULL,NULL,?,?,NULL)",
                    (
                        "embedding_cross_source_spoof",
                        chunk.id,
                        spoof_source_id,
                        profile.provider,
                        profile.model,
                        profile.embedding_version,
                        profile.dimensions,
                        packed,
                        hashlib.sha256(packed).hexdigest(),
                        chunk.content_sha256,
                        timestamp,
                        timestamp,
                    ),
                )
                connection.execute(
                    "INSERT INTO document_chunk_embeddings("
                    "id,chunk_id,source_document_id,provider,model,embedding_version,"
                    "dimensions,vector,vector_sha256,chunk_content_sha256,status,"
                    "error_code,error_message,created_at,updated_at,stale_at) VALUES("
                    "?,?,?,?,?,?,?,?,?,?,'ready',NULL,NULL,?,?,NULL)",
                    (
                        "embedding_cross_source_owner",
                        spoof_chunk.id,
                        fixture.chunk_set.source_document_id,
                        profile.provider,
                        profile.model,
                        profile.embedding_version,
                        profile.dimensions,
                        packed,
                        hashlib.sha256(packed).hexdigest(),
                        spoof_chunk.content_sha256,
                        timestamp,
                        timestamp,
                    ),
                )
                connection.commit()

            repository = SqlAlchemyDocumentSearchRepository(fixture.session_factory)
            request = SearchRequest(
                query="shared provenance needle",
                mode="semantic",
                paper_ids=("paper-1",),
                limit=10,
            )

            hits = await repository.semantic(
                request,
                profile=profile,
                query_vector=(1.0, 0.0),
            )
            coverage = await repository.coverage_for_request(
                request,
                profile=profile,
            )
            status = await repository.index_status(
                fixture.chunk_set.source_document_id,
                paper_id="paper-1",
                profile=profile,
            )

            self.assertEqual((), hits)
            self.assertEqual(0, coverage.embedded_chunks)
            self.assertEqual(0, status["embedded_chunks"])

    async def test_semantic_and_hybrid_use_exact_ready_profile_once_and_never_write(
        self,
    ) -> None:
        from backend.app.application.document_search import DocumentSearch
        from backend.app.domain.context import (
            ChunkingSpec,
            EmbeddingBatch,
            EmbeddingProfile,
            EmbeddingRequest,
            SearchRequest,
        )
        from backend.app.infrastructure.database_backup import inspect_database
        from backend.app.repositories.document_search import (
            SqlAlchemyDocumentSearchRepository,
        )
        from backend.tests.support.p3_database import p3_context_fixture

        markdown = (
            "shared lexical needle alpha.\n\n"
            "shared lexical needle bravo.\n\n"
            "shared lexical needle gamma.\n\n"
            "coverage failed chunk.\n\n"
            "coverage stale chunk.\n"
        )
        profile = EmbeddingProfile(
            provider="fixed-query",
            model="fixed-vector-v1",
            embedding_version="fixture-semantic-v1",
            dimensions=2,
        )
        async with p3_context_fixture(
            prefix="study-app-p3-semantic-search-",
            source_id="src_semantic_search",
            markdown=markdown,
            spec=ChunkingSpec(target_tokens=5, hard_cap_tokens=5),
            now=NOW,
            pdf_sha256="b" * 64,
            options_hash="c" * 64,
        ) as fixture:
            chunks = fixture.chunk_set.chunks
            ranked_chunks = tuple(
                chunk for chunk in chunks if "shared lexical needle" in chunk.content
            )
            failed_chunk = next(
                chunk for chunk in chunks if "coverage failed chunk" in chunk.content
            )
            stale_chunk = next(
                chunk for chunk in chunks if "coverage stale chunk" in chunk.content
            )
            self.assertEqual(3, len(ranked_chunks))

            exact_vectors = (
                (0.0, 1.0),
                (math.sqrt(0.5), math.sqrt(0.5)),
                (1.0, 0.0),
            )
            timestamp = NOW.isoformat().replace("+00:00", "Z")
            with closing(sqlite3.connect(fixture.database_path)) as connection:
                for index, (chunk, vector) in enumerate(
                    zip(ranked_chunks, exact_vectors, strict=True)
                ):
                    packed = struct.pack("<2f", *vector)
                    connection.execute(
                        "INSERT INTO document_chunk_embeddings("
                        "id,chunk_id,source_document_id,provider,model,embedding_version,"
                        "dimensions,vector,vector_sha256,chunk_content_sha256,status,"
                        "error_code,error_message,created_at,updated_at,stale_at) VALUES("
                        "?,?,?,?,?,?,?,?,?,?,'ready',NULL,NULL,?,?,NULL)",
                        (
                            f"embedding_semantic_ready_{index}",
                            chunk.id,
                            chunk.source_document_id,
                            profile.provider,
                            profile.model,
                            profile.embedding_version,
                            profile.dimensions,
                            packed,
                            hashlib.sha256(packed).hexdigest(),
                            chunk.content_sha256,
                            timestamp,
                            timestamp,
                        ),
                    )
                connection.execute(
                    "INSERT INTO document_chunk_embeddings("
                    "id,chunk_id,source_document_id,provider,model,embedding_version,"
                    "dimensions,vector,vector_sha256,chunk_content_sha256,status,"
                    "error_code,error_message,created_at,updated_at,stale_at) VALUES("
                    "?,?,?,?,?,?,2,NULL,NULL,?,'failed','FIXTURE_FAILURE',NULL,?,?,NULL)",
                    (
                        "embedding_semantic_failed",
                        failed_chunk.id,
                        failed_chunk.source_document_id,
                        profile.provider,
                        profile.model,
                        profile.embedding_version,
                        failed_chunk.content_sha256,
                        timestamp,
                        timestamp,
                    ),
                )
                stale_vector = struct.pack("<2f", 1.0, 0.0)
                connection.execute(
                    "INSERT INTO document_chunk_embeddings("
                    "id,chunk_id,source_document_id,provider,model,embedding_version,"
                    "dimensions,vector,vector_sha256,chunk_content_sha256,status,"
                    "error_code,error_message,created_at,updated_at,stale_at) VALUES("
                    "?,?,?,?,?,?,?,?,?,?,'stale',NULL,NULL,?,?,?)",
                    (
                        "embedding_semantic_stale",
                        stale_chunk.id,
                        stale_chunk.source_document_id,
                        profile.provider,
                        profile.model,
                        profile.embedding_version,
                        profile.dimensions,
                        stale_vector,
                        hashlib.sha256(stale_vector).hexdigest(),
                        stale_chunk.content_sha256,
                        timestamp,
                        timestamp,
                        timestamp,
                    ),
                )
                wrong_vector = struct.pack("<2f", 1.0, 0.0)
                connection.execute(
                    "INSERT INTO document_chunk_embeddings("
                    "id,chunk_id,source_document_id,provider,model,embedding_version,"
                    "dimensions,vector,vector_sha256,chunk_content_sha256,status,"
                    "error_code,error_message,created_at,updated_at,stale_at) VALUES("
                    "?,?,?,?,?,?,?,?,?,?,'ready',NULL,NULL,?,?,NULL)",
                    (
                        "embedding_wrong_profile",
                        failed_chunk.id,
                        failed_chunk.source_document_id,
                        profile.provider,
                        "wrong-model",
                        profile.embedding_version,
                        profile.dimensions,
                        wrong_vector,
                        hashlib.sha256(wrong_vector).hexdigest(),
                        failed_chunk.content_sha256,
                        timestamp,
                        timestamp,
                    ),
                )
                connection.commit()

            class QueryProvider:
                provider_id = profile.provider

                def __init__(self) -> None:
                    self.calls: list[EmbeddingRequest] = []

                async def embed(self, request: EmbeddingRequest) -> EmbeddingBatch:
                    self.calls.append(request)
                    return EmbeddingBatch(
                        profile=request.profile,
                        chunk_ids=request.chunk_ids,
                        vectors=((1.0, 0.0),),
                    )

            provider = QueryProvider()
            search = DocumentSearch(
                SqlAlchemyDocumentSearchRepository(fixture.session_factory),
                query_embedding_profile=profile,
                query_embedding_provider=provider,
            )
            before = inspect_database(fixture.database_path)

            lexical = await search.search(
                SearchRequest(
                    query="shared lexical needle",
                    mode="lexical",
                    paper_ids=("paper-1",),
                    limit=10,
                )
            )
            semantic = await search.search(
                SearchRequest(
                    query="shared lexical needle",
                    mode="semantic",
                    paper_ids=("paper-1",),
                    limit=10,
                )
            )
            hybrid = await search.search(
                SearchRequest(
                    query="shared lexical needle",
                    mode="hybrid",
                    paper_ids=("paper-1",),
                    limit=10,
                )
            )

            after = inspect_database(fixture.database_path)
            self.assertEqual(
                [chunk.id for chunk in ranked_chunks],
                [item.chunk_id for item in lexical.items],
            )
            self.assertEqual(2, len(provider.calls))
            self.assertTrue(
                all(
                    call.profile == profile
                    and call.texts == ("shared lexical needle",)
                    and len(call.chunk_ids) == 1
                    for call in provider.calls
                )
            )
            self.assertEqual(
                [chunk.id for chunk in reversed(ranked_chunks)],
                [item.chunk_id for item in semantic.items],
            )
            for item, expected in zip(
                semantic.items,
                (1.0, math.sqrt(0.5), 0.0),
                strict=True,
            ):
                self.assertAlmostEqual(expected, item.score, places=6)
            self.assertTrue(all(item.lexical_score is None for item in semantic.items))
            for item, expected in zip(
                semantic.items,
                (1.0, math.sqrt(0.5), 0.0),
                strict=True,
            ):
                self.assertAlmostEqual(expected, item.semantic_score, places=6)
            self.assertEqual(
                (len(chunks), 3, 1, 1),
                (
                    semantic.coverage.ready_chunks,
                    semantic.coverage.embedded_chunks,
                    semantic.coverage.stale_chunks,
                    semantic.coverage.failed_embeddings,
                ),
            )
            self.assertEqual(
                [ranked_chunks[0].id, ranked_chunks[2].id, ranked_chunks[1].id],
                [item.chunk_id for item in hybrid.items],
            )
            expected_rrf = (
                1 / 61 + 1 / 63,
                1 / 63 + 1 / 61,
                1 / 62 + 1 / 62,
            )
            for item, expected in zip(hybrid.items, expected_rrf, strict=True):
                self.assertAlmostEqual(expected, item.score)
                self.assertIsNotNone(item.lexical_score)
                self.assertIsNotNone(item.semantic_score)
            self.assertEqual(before.logical_sha256, after.logical_sha256)
            self.assertEqual(before.table_sha256, after.table_sha256)
            self.assertEqual(before.content_sha256, after.content_sha256)


class IndexStatusTests(unittest.IsolatedAsyncioTestCase):
    async def test_index_status_reports_exact_profile_and_chunk_coverage(self) -> None:
        from backend.app.application.document_search import DocumentSearch
        from backend.app.domain.context import ChunkingSpec, EmbeddingProfile
        from backend.app.repositories.document_search import (
            SqlAlchemyDocumentSearchRepository,
        )
        from backend.tests.support.p3_database import p3_context_fixture

        profile = EmbeddingProfile(
            provider="status-provider",
            model="status-model",
            embedding_version="status-v1",
            dimensions=2,
        )
        markdown = (
            "# Status\n\n"
            "ready status chunk.\n\n"
            "failed status chunk.\n\n"
            "stale status chunk.\n"
        )
        async with p3_context_fixture(
            prefix="study-app-p3-index-status-",
            source_id="src_index_status",
            markdown=markdown,
            spec=ChunkingSpec(target_tokens=4, hard_cap_tokens=4),
            now=NOW,
            pdf_sha256="d" * 64,
            options_hash="e" * 64,
        ) as fixture:
            import hashlib
            import struct
            from contextlib import closing

            chunks = fixture.chunk_set.chunks
            self.assertGreaterEqual(len(chunks), 3)
            timestamp = NOW.isoformat().replace("+00:00", "Z")
            ready_vector = struct.pack("<2f", 1.0, 0.0)
            stale_vector = struct.pack("<2f", 0.0, 1.0)
            with closing(sqlite3.connect(fixture.database_path)) as connection:
                connection.execute(
                    "INSERT INTO document_chunk_embeddings("
                    "id,chunk_id,source_document_id,provider,model,embedding_version,"
                    "dimensions,vector,vector_sha256,chunk_content_sha256,status,"
                    "error_code,error_message,created_at,updated_at,stale_at) VALUES("
                    "?,?,?,?,?,?,?,?,?,?,'ready',NULL,NULL,?,?,NULL)",
                    (
                        "status-ready",
                        chunks[0].id,
                        chunks[0].source_document_id,
                        profile.provider,
                        profile.model,
                        profile.embedding_version,
                        profile.dimensions,
                        ready_vector,
                        hashlib.sha256(ready_vector).hexdigest(),
                        chunks[0].content_sha256,
                        timestamp,
                        timestamp,
                    ),
                )
                connection.execute(
                    "INSERT INTO document_chunk_embeddings("
                    "id,chunk_id,source_document_id,provider,model,embedding_version,"
                    "dimensions,vector,vector_sha256,chunk_content_sha256,status,"
                    "error_code,error_message,created_at,updated_at,stale_at) VALUES("
                    "?,?,?,?,?,?,?,NULL,NULL,?,'failed',?,?,?, ?,NULL)",
                    (
                        "status-failed",
                        chunks[1].id,
                        chunks[1].source_document_id,
                        profile.provider,
                        profile.model,
                        profile.embedding_version,
                        profile.dimensions,
                        chunks[1].content_sha256,
                        "STATUS_PROVIDER_FAILED",
                        "fixture failure",
                        timestamp,
                        timestamp,
                    ),
                )
                connection.execute(
                    "INSERT INTO document_chunk_embeddings("
                    "id,chunk_id,source_document_id,provider,model,embedding_version,"
                    "dimensions,vector,vector_sha256,chunk_content_sha256,status,"
                    "error_code,error_message,created_at,updated_at,stale_at) VALUES("
                    "?,?,?,?,?,?,?,?,?,?,'stale',NULL,NULL,?,?,?)",
                    (
                        "status-stale",
                        chunks[2].id,
                        chunks[2].source_document_id,
                        profile.provider,
                        profile.model,
                        profile.embedding_version,
                        profile.dimensions,
                        stale_vector,
                        hashlib.sha256(stale_vector).hexdigest(),
                        chunks[2].content_sha256,
                        timestamp,
                        timestamp,
                        timestamp,
                    ),
                )
                connection.commit()

            search = DocumentSearch(
                SqlAlchemyDocumentSearchRepository(fixture.session_factory),
                index_embedding_profile=profile,
            )
            result = await search.status(
                fixture.chunk_set.source_document_id,
                paper_id="paper-1",
            )
            self.assertEqual(len(chunks), result.total_chunks)
            self.assertEqual(len(chunks), result.ready_chunks)
            self.assertEqual(1, result.embedded_chunks)
            self.assertEqual(1, result.stale_chunks)
            self.assertEqual(1, result.failed_embeddings)
            self.assertEqual(profile.provider, result.provider)
            self.assertEqual(profile.model, result.model)
            self.assertEqual(profile.embedding_version, result.version)
            self.assertEqual("partial", result.coverage)


class DocumentConsumerHttpTests(unittest.IsolatedAsyncioTestCase):
    async def test_p3_explainer_api_service_forwards_deep_profile_to_context_artifact_service(
        self,
    ) -> None:
        from backend.app.api.routes.document_processing import ProcessingApiService
        from backend.app.application.document_artifacts import DocumentArtifactEnqueueResult
        from backend.app.domain import GeneratedArtifact
        from backend.app.domain.processing import ExplainJobSpecV1, NewProcessingJob

        now = NOW

        class _DocumentArtifacts:
            def __init__(self) -> None:
                self.requests: list[dict[str, object]] = []

            async def enqueue(
                self,
                paper_id,
                source_document_id,
                source_mode,
                kind,
                *,
                profile,
                now,
            ):
                self.requests.append(
                    {
                        "paper_id": paper_id,
                        "source_document_id": source_document_id,
                        "source_mode": source_mode,
                        "kind": kind,
                        "profile": profile,
                    }
                )
                artifact = GeneratedArtifact(
                    id="artifact-deep-explainer",
                    paper_id=paper_id,
                    kind=kind,
                    source_document_id=source_document_id,
                    status="queued",
                    generator_provider="fixture-provider",
                    generator_model="fixture-model",
                    prompt_version="explainer-context-deep-v1",
                    created_at=now,
                    updated_at=now,
                )
                job = NewProcessingJob(
                    id="job-deep-explainer",
                    spec=ExplainJobSpecV1(
                        paper_id=paper_id,
                        source_document_id=source_document_id,
                        artifact_id=artifact.id,
                        profile=profile,
                        provider=artifact.generator_provider,
                        model=artifact.generator_model,
                        prompt_version=artifact.prompt_version,
                        source_mode=source_mode,
                    ),
                    idempotency_key="deep-explainer-key",
                    created_at=now,
                )
                return DocumentArtifactEnqueueResult(artifact, job, False)

        class _Jobs:
            async def get(self, job_id):
                return SimpleNamespace(
                    id=job_id,
                    paper_id="paper-1",
                    job_type=SimpleNamespace(value="explain"),
                    source_mode=SimpleNamespace(value="native"),
                    status=SimpleNamespace(value="queued"),
                )

        class _Work:
            def __init__(self) -> None:
                self.jobs = _Jobs()

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_args):
                return None

        document_artifacts = _DocumentArtifacts()
        service = ProcessingApiService(
            lambda: _Work(),
            native_provider=object(),
            ocr_gate=object(),
            artifact_generator=None,
            clock=lambda: now,
            cursor_secret=b"p3-deep-profile-forwarding-secret",
            document_artifacts=document_artifacts,
        )

        await service.enqueue_explainer(
            "paper-1",
            source_mode="native",
            source_document_id="source-1",
            profile="deep",
        )

        self.assertEqual(
            [
                {
                    "paper_id": "paper-1",
                    "source_document_id": "source-1",
                    "source_mode": "native",
                    "kind": "explainer",
                    "profile": "deep",
                }
            ],
            document_artifacts.requests,
        )

    async def test_p3_unknown_artifact_kind_returns_frozen_typed_error(self) -> None:
        from backend.app.api.app import create_app
        from fastapi.testclient import TestClient

        class _Services:
            schema_revision = "20260807_03"

            class _Artifacts:
                async def enqueue(self, *_args, **_kwargs):
                    raise AssertionError("unknown artifact kind reached the application service")

            document_artifacts = _Artifacts()

            async def dispose(self) -> None:
                return None

        client_context = TestClient(
            create_app(_Services(), None, required_schema_revision="20260807_03")
        )
        client = client_context.__enter__()
        try:
            response = client.post(
                "/api/v2/papers/paper-1/artifacts/outline",
                json={"sourceMode": "native", "sourceDocumentId": "source-1"},
            )

            self.assertEqual(422, response.status_code, response.text)
            self.assertEqual(
                {
                    "error": {
                        "code": "ARTIFACT_KIND_UNSUPPORTED",
                        "message": "The requested artifact kind is unsupported.",
                        "details": {"artifact_kind": "outline"},
                    }
                },
                response.json(),
            )
        finally:
            client_context.__exit__(None, None, None)

    """Task 10 HTTP matrix tracer at the application/router seam."""

    async def test_p3_artifact_routes_preserve_typed_source_identity_errors(self) -> None:
        """Real P3 services must not collapse source failures into INVALID_REQUEST."""
        from backend.app.api.app import create_app
        from backend.app.bootstrap import RolloutSettings, bootstrap
        from backend.app.config import DatabaseSettings
        from backend.app.domain.context import ChunkingSpec
        from backend.tests.support.p3_database import p3_context_fixture
        from fastapi.testclient import TestClient

        pdf_bytes = b"p3 typed artifact source pdf"
        pdf_sha = hashlib.sha256(pdf_bytes).hexdigest()

        class NativeProvider:
            provider = "local"
            model = "pymupdf4llm-pymupdf"
            processing_version = "native-v1"

        class LegacyGenerator:
            def identity(self, _kind: str, profile: str = "standard"):
                return SimpleNamespace(
                    provider="legacy-provider",
                    model="legacy-model",
                    prompt_version=f"legacy-{profile}-v1",
                )

        class TranslationProvider:
            provider_id = "fixture-translation"
            model_id = "fixture-translation-model"
            prompt_version = "translation-v1"

        class StructuredProvider:
            provider_id = "fixture-structured"
            model_id = "fixture-structured-model"

        async with p3_context_fixture(
            prefix="study-app-p3-http-typed-source-errors-",
            source_id="src_http_typed_source_errors",
            markdown="# Abstract\n\ntyped source identity.\n",
            spec=ChunkingSpec(target_tokens=8, hard_cap_tokens=8),
            now=NOW,
            pdf_sha256=pdf_sha,
            options_hash="d" * 64,
        ) as fixture:
            pdf_path = fixture.database_path.parent / "paper.pdf"
            pdf_path.write_bytes(pdf_bytes)
            with closing(sqlite3.connect(fixture.database_path)) as connection:
                connection.execute(
                    "UPDATE papers SET pdf_path=? WHERE id='paper-1'",
                    (str(pdf_path),),
                )
                connection.commit()

            container = bootstrap(
                RolloutSettings(
                    document_pipeline_mode="p1",
                    generation_pipeline_mode="p1",
                    artifact_read_mode="prefer_new",
                    artifact_write_mode="dual",
                    processing_cursor_secret="p3-typed-source-errors-at-least-32-bytes",
                ),
                DatabaseSettings(fixture.database_path),
                required_schema_revision="20260807_03",
                native_provider_factory=NativeProvider,
                generation_provider_factory=LegacyGenerator,
                translation_provider_factory=TranslationProvider,
                structured_provider_factory=StructuredProvider,
            )
            client_context = TestClient(
                create_app(
                    container,
                    container.session_factory,
                    required_schema_revision="20260807_03",
                )
            )
            client = client_context.__enter__()
            try:
                def enqueue(kind: str, source_id: str, mode: str = "native"):
                    body = {"sourceMode": mode, "sourceDocumentId": source_id}
                    if kind == "explainer":
                        body["profile"] = "standard"
                    return client.post(
                        f"/api/v2/papers/paper-1/artifacts/{kind}",
                        json=body,
                    )

                def enqueue_index(source_id: str, mode: str = "native"):
                    return client.post(
                        "/api/v2/papers/paper-1/index",
                        json={
                            "sourceMode": mode,
                            "sourceDocumentId": source_id,
                            "includeEmbeddings": False,
                        },
                    )

                for kind in ("explainer", "summary"):
                    missing = enqueue(kind, "src_missing")
                    self.assertEqual(404, missing.status_code, missing.text)
                    self.assertEqual("SOURCE_NOT_FOUND", missing.json()["error"]["code"])

                    mismatch = enqueue(kind, "src_http_typed_source_errors", "ocr")
                    self.assertEqual(422, mismatch.status_code, mismatch.text)
                    self.assertEqual(
                        "SOURCE_MODE_MISMATCH", mismatch.json()["error"]["code"]
                    )

                missing_index = enqueue_index("src_missing")
                self.assertEqual(404, missing_index.status_code, missing_index.text)
                self.assertEqual(
                    "SOURCE_NOT_FOUND", missing_index.json()["error"]["code"]
                )
                mismatch_index = enqueue_index(
                    "src_http_typed_source_errors", "ocr"
                )
                self.assertEqual(422, mismatch_index.status_code, mismatch_index.text)
                self.assertEqual(
                    "SOURCE_MODE_MISMATCH", mismatch_index.json()["error"]["code"]
                )
                missing_profile = client.post(
                    "/api/v2/papers/paper-1/index",
                    json={
                        "sourceMode": "native",
                        "sourceDocumentId": "src_http_typed_source_errors",
                        "includeEmbeddings": True,
                    },
                )
                self.assertEqual(409, missing_profile.status_code, missing_profile.text)
                self.assertEqual(
                    "EMBEDDING_PROFILE_UNAVAILABLE",
                    missing_profile.json()["error"]["code"],
                )

                with closing(sqlite3.connect(fixture.database_path)) as connection:
                    connection.execute(
                        "UPDATE document_sources SET status='running' "
                        "WHERE id='src_http_typed_source_errors'"
                    )
                    connection.commit()
                not_ready = enqueue("summary", "src_http_typed_source_errors")
                self.assertEqual(409, not_ready.status_code, not_ready.text)
                self.assertEqual("SOURCE_NOT_READY", not_ready.json()["error"]["code"])
                not_ready_index = enqueue_index("src_http_typed_source_errors")
                self.assertEqual(409, not_ready_index.status_code, not_ready_index.text)
                self.assertEqual(
                    "SOURCE_NOT_READY", not_ready_index.json()["error"]["code"]
                )

                with closing(sqlite3.connect(fixture.database_path)) as connection:
                    connection.execute(
                        "UPDATE document_sources SET status='ready' "
                        "WHERE id='src_http_typed_source_errors'"
                    )
                    connection.commit()
                pdf_path.write_bytes(b"changed p3 typed artifact source pdf")
                stale = enqueue("summary", "src_http_typed_source_errors")
                self.assertEqual(409, stale.status_code, stale.text)
                self.assertEqual("SOURCE_STALE", stale.json()["error"]["code"])
                stale_index = enqueue_index("src_http_typed_source_errors")
                self.assertEqual(409, stale_index.status_code, stale_index.text)
                self.assertEqual("SOURCE_STALE", stale_index.json()["error"]["code"])

                with closing(sqlite3.connect(fixture.database_path)) as connection:
                    connection.execute(
                        "UPDATE papers SET pdf_path=? WHERE id='paper-1'",
                        (str(fixture.database_path.parent / "missing.pdf"),),
                    )
                    connection.commit()
                missing_pdf = enqueue("summary", "src_http_typed_source_errors")
                self.assertEqual(404, missing_pdf.status_code, missing_pdf.text)
                self.assertEqual("PDF_NOT_FOUND", missing_pdf.json()["error"]["code"])
                missing_pdf_index = enqueue_index("src_http_typed_source_errors")
                self.assertEqual(404, missing_pdf_index.status_code, missing_pdf_index.text)
                self.assertEqual(
                    "PDF_NOT_FOUND", missing_pdf_index.json()["error"]["code"]
                )
            finally:
                client_context.__exit__(None, None, None)

    async def test_p3_bootstrap_explainer_enqueue_uses_context_artifact_service(self) -> None:
        """The final-stage HTTP explainer command must not use whole-document P2 generation."""
        from backend.app.api.app import create_app
        from backend.app.bootstrap import RolloutSettings, bootstrap
        from backend.app.config import DatabaseSettings
        from backend.tests.support.p3_database import p3_context_fixture
        from backend.app.domain.context import ChunkingSpec
        from fastapi.testclient import TestClient

        now = NOW
        pdf_bytes = b"p3 bootstrap explainer pdf"
        pdf_sha = hashlib.sha256(pdf_bytes).hexdigest()

        class NativeProvider:
            provider = "local"
            model = "pymupdf4llm-pymupdf"
            processing_version = "native-v1"

        class LegacyGenerator:
            def identity(self, _kind: str, profile: str = "standard"):
                return SimpleNamespace(
                    provider="legacy-provider",
                    model="legacy-model",
                    prompt_version=f"legacy-{profile}-v1",
                )

            def generate(self, *_args, **_kwargs):
                raise AssertionError("P3 explainer must not call the legacy generator")

        class TranslationProvider:
            provider_id = "fixture-translation"
            model_id = "fixture-translation-model"
            prompt_version = "translation-v1"

        class StructuredProvider:
            provider_id = "fixture-structured"
            model_id = "fixture-structured-model"

            async def generate(self, _request):
                raise AssertionError("HTTP enqueue must not execute the structured provider")

        async with p3_context_fixture(
            prefix="study-app-p3-http-explainer-bootstrap-",
            source_id="src_http_explainer_bootstrap",
            markdown="# Abstract\n\nbootstrap explainer source.\n",
            spec=ChunkingSpec(target_tokens=8, hard_cap_tokens=8),
            now=now,
            pdf_sha256=pdf_sha,
            options_hash="b" * 64,
        ) as fixture:
            pdf_path = fixture.database_path.parent / "paper.pdf"
            pdf_path.write_bytes(pdf_bytes)
            with closing(sqlite3.connect(fixture.database_path)) as connection:
                connection.execute(
                    "UPDATE papers SET pdf_path=? WHERE id='paper-1'",
                    (str(pdf_path),),
                )
                connection.commit()

            container = bootstrap(
                RolloutSettings(
                    document_pipeline_mode="p1",
                    generation_pipeline_mode="p1",
                    artifact_read_mode="prefer_new",
                    artifact_write_mode="dual",
                    processing_cursor_secret="p3-http-explainer-secret-at-least-32-bytes",
                ),
                DatabaseSettings(fixture.database_path),
                required_schema_revision="20260807_03",
                native_provider_factory=NativeProvider,
                generation_provider_factory=LegacyGenerator,
                translation_provider_factory=TranslationProvider,
                structured_provider_factory=StructuredProvider,
            )
            client_context = TestClient(
                create_app(
                    container,
                    container.session_factory,
                    required_schema_revision="20260807_03",
                )
            )
            client = client_context.__enter__()
            try:
                legacy = container.artifact_generator
                self.assertIsNotNone(legacy)
                with mock.patch.object(
                    legacy,
                    "enqueue_explainer",
                    new=mock.AsyncMock(
                        side_effect=AssertionError(
                            "P3 explainer route called the legacy whole-document generator"
                        )
                    ),
                ) as legacy_enqueue:
                    response = client.post(
                        "/api/v2/papers/paper-1/artifacts/explainer",
                        json={
                            "sourceMode": "native",
                            "sourceDocumentId": "src_http_explainer_bootstrap",
                            "profile": "standard",
                        },
                    )
                self.assertEqual(202, response.status_code, response.text)
                payload = response.json()
                self.assertEqual("explainer", payload["artifact"]["kind"])
                self.assertEqual("explain", payload["job"]["jobType"])
                legacy_enqueue.assert_not_awaited()
            finally:
                client_context.__exit__(None, None, None)

    async def test_p3_commands_and_search_matrix_are_strict_and_enqueue_only(self) -> None:
        from backend.app.api.app import create_app
        from backend.app.domain.context import (
            SearchCoverage,
            SearchHit,
            SearchMode,
        )
        from backend.app.application.document_search import SearchResultPage
        from backend.app.application.document_artifacts import DocumentArtifactEnqueueResult
        from backend.app.domain import GeneratedArtifact
        from backend.app.api.routes.document_processing import SourceModeMismatchError
        from backend.app.domain.processing import (
            EmbedJobSpecV1,
            ExplainJobSpecV1,
            NewProcessingJob,
            TranslateJobSpecV1,
        )
        from fastapi.testclient import TestClient

        now = NOW

        @dataclass(frozen=True)
        class _ProviderCounters:
            artifact_calls: int = 0
            embedding_calls: int = 0

        class _Application:
            schema_revision = "20260807_03"

            def __init__(self) -> None:
                self.counters = _ProviderCounters()
                self.artifact_requests: list[tuple[str, str, str, str]] = []
                self.index_requests: list[dict[str, object]] = []
                self.search_requests: list[object] = []

                class _ArtifactService:
                    def __init__(self, owner: "_Application") -> None:
                        self.owner = owner

                    async def enqueue(self, paper_id, source_document_id, source_mode, kind, *, now):
                        if source_mode != "native":
                            raise SourceModeMismatchError(source_mode=source_mode)
                        self.owner.artifact_requests.append(
                            (paper_id, source_document_id, source_mode, kind)
                        )
                        artifact = GeneratedArtifact(
                            id=f"artifact-{kind}",
                            paper_id=paper_id,
                            kind=kind,
                            source_document_id=source_document_id,
                            status="queued",
                            generator_provider="fixture-provider",
                            generator_model="fixture-model",
                            prompt_version=f"{kind}-v1",
                            created_at=now,
                            updated_at=now,
                        )
                        if kind == "translation":
                            spec = TranslateJobSpecV1(
                                paper_id=paper_id,
                                source_document_id=source_document_id,
                                artifact_id=artifact.id,
                                source_mode=source_mode,
                            )
                        else:
                            spec = ExplainJobSpecV1(
                                paper_id=paper_id,
                                source_document_id=source_document_id,
                                artifact_id=artifact.id,
                                profile="standard",
                                provider="fixture-provider",
                                model="fixture-model",
                                prompt_version=f"{kind}-v1",
                                source_mode=source_mode,
                            )
                        job = NewProcessingJob(
                            id=f"job-{kind}",
                            spec=spec,
                            idempotency_key=f"key-{kind}",
                            created_at=now,
                            max_attempts=3,
                        )
                        return DocumentArtifactEnqueueResult(artifact, job, False)

                class _SearchService:
                    def __init__(self, owner: "_Application") -> None:
                        self.owner = owner

                    async def enqueue_index(self, **request):
                        self.owner.index_requests.append(dict(request))
                        spec = EmbedJobSpecV1(
                            paper_id=request["paper_id"],
                            source_document_id=request["source_document_id"],
                            include_embeddings=request["include_embeddings"],
                            provider="fixture-provider" if request["include_embeddings"] else "none",
                            model="fixture-model" if request["include_embeddings"] else "none",
                            embedding_version="fixture-v1" if request["include_embeddings"] else "none",
                            dimensions=2 if request["include_embeddings"] else None,
                            chunking_version="markdown-coverage-v1",
                            options={},
                            source_mode=request["source_mode"],
                        )
                        # The route only exposes the durable job DTO; this
                        # fixture deliberately keeps provider execution out.
                        job = NewProcessingJob(
                            id="job-index",
                            spec=spec,
                            idempotency_key="key-index",
                            created_at=now,
                            max_attempts=3,
                        )
                        return SimpleNamespace(job=job, deduplicated=False)

                    async def status(self, source_document_id: str, *, paper_id: str | None = None):
                        self.owner.search_requests.append(
                            ("status", source_document_id, paper_id)
                        )
                        return {
                            "total_chunks": 2,
                            "ready_chunks": 2,
                            "embedded_chunks": 1,
                            "stale_chunks": 0,
                            "failed_embeddings": 1,
                            "provider": "fixture-provider",
                            "model": "fixture-model",
                            "version": "fixture-v1",
                            "coverage": "partial",
                        }

                    async def search(self, request):
                        self.owner.search_requests.append(request)
                        mode = SearchMode(request.mode)
                        return SearchResultPage(
                            items=(
                                SearchHit(
                                    paper_id="paper-1",
                                    source_document_id="source-1",
                                    chunk_id="chunk-1",
                                    sequence=0,
                                    heading_path=("Methods", "Evaluation"),
                                    page_start=5,
                                    page_end=6,
                                    excerpt="evaluation protocol",
                                    score=0.8,
                                    lexical_score=(1.2 if mode is not SearchMode.SEMANTIC else None),
                                    semantic_score=(0.7 if mode is not SearchMode.LEXICAL else None),
                                ),
                            ),
                            coverage=SearchCoverage(2, 1, 0, 1),
                        )

                self.document_artifacts = _ArtifactService(self)
                self.document_search = _SearchService(self)

            async def dispose(self) -> None:
                return None

        application = _Application()
        client_context = TestClient(
            create_app(application, None, required_schema_revision="20260807_03")
        )
        client = client_context.__enter__()
        try:
            body = {"sourceMode": "native", "sourceDocumentId": "source-1"}
            for kind in ("translation", "classification", "metadata", "summary"):
                response = client.post(f"/api/v2/papers/paper-1/artifacts/{kind}", json=body)
                self.assertEqual(202, response.status_code, response.text)
                payload = response.json()
                self.assertEqual({"artifact", "job", "deduplicated"}, set(payload))
                self.assertEqual(kind, payload["artifact"]["kind"])
                self.assertEqual("queued", payload["job"]["status"])

            for include_embeddings in (False, True):
                response = client.post(
                    "/api/v2/papers/paper-1/index",
                    json={**body, "includeEmbeddings": include_embeddings},
                )
                self.assertEqual(202, response.status_code, response.text)
                self.assertEqual("embed", response.json()["job"]["jobType"])

            status_response = client.get(
                "/api/v2/papers/paper-1/index-status",
                params={"sourceDocumentId": "source-1"},
            )
            self.assertEqual(200, status_response.status_code, status_response.text)
            self.assertEqual(
                {
                    "totalChunks": 2,
                    "readyChunks": 2,
                    "embeddedChunks": 1,
                    "staleChunks": 0,
                    "failedEmbeddings": 1,
                    "provider": "fixture-provider",
                    "model": "fixture-model",
                    "version": "fixture-v1",
                    "coverage": "partial",
                },
                status_response.json(),
            )

            search_body = {"query": "evaluation protocol", "limit": 20}
            for mode in ("lexical", "semantic", "hybrid"):
                response = client.post(
                    "/api/v2/search/chunks",
                    json={**search_body, "mode": mode, "paperIds": ["paper-1"]},
                )
                self.assertEqual(200, response.status_code, response.text)
                item = response.json()["items"][0]
                self.assertEqual(
                    {
                        "paperId", "sourceDocumentId", "chunkId", "sequence",
                        "headingPath", "pageStart", "pageEnd", "excerpt", "score",
                        "lexicalScore", "semanticScore",
                    },
                    set(item),
                )
                self.assertEqual(["Methods", "Evaluation"], item["headingPath"])

            invalid_requests = (
                {"sourceMode": "native"},
                {"source_mode": "native", "sourceDocumentId": "source-1"},
                {"sourceMode": "native", "sourceDocumentId": "source-1", "extra": True},
            )
            for invalid in invalid_requests:
                response = client.post(
                    "/api/v2/papers/paper-1/artifacts/summary", json=invalid
                )
                self.assertEqual(422, response.status_code, response.text)

            mismatch = client.post(
                "/api/v2/papers/paper-1/artifacts/summary",
                json={"sourceMode": "ocr", "sourceDocumentId": "source-1"},
            )
            self.assertEqual(422, mismatch.status_code, mismatch.text)
            self.assertEqual("SOURCE_MODE_MISMATCH", mismatch.json()["error"]["code"])
            self.assertEqual(4, len(application.artifact_requests))
            self.assertEqual(2, len(application.index_requests))
            self.assertEqual(0, application.counters.artifact_calls)
            self.assertEqual(0, application.counters.embedding_calls)
        finally:
            client_context.__exit__(None, None, None)

    async def test_p3_api_maps_profile_and_source_readiness_errors_to_frozen_codes(self) -> None:
        from backend.app.api.app import create_app
        from backend.app.domain import EmbeddingProfileUnavailableError
        from backend.app.api.routes.document_processing import (
            SourceNotReadyError,
            SourceStaleError,
        )
        from fastapi.testclient import TestClient

        class Services:
            schema_revision = "20260807_03"
            embedding_profile = None

            class Artifacts:
                async def enqueue(self, *_args, **_kwargs):
                    raise SourceNotReadyError(paper_id="paper-1")

            class Search:
                async def enqueue_index(self, **_kwargs):
                    raise EmbeddingProfileUnavailableError()

                async def status(self, *_args, **_kwargs):
                    raise SourceStaleError(paper_id="paper-1")

            document_artifacts = Artifacts()
            document_search = Search()

            async def dispose(self):
                return None

        client_context = TestClient(
            create_app(Services(), None, required_schema_revision="20260807_03")
        )
        client = client_context.__enter__()
        try:
            not_ready = client.post(
                "/api/v2/papers/paper-1/artifacts/summary",
                json={"sourceMode": "native", "sourceDocumentId": "source-1"},
            )
            self.assertEqual(409, not_ready.status_code)
            self.assertEqual("SOURCE_NOT_READY", not_ready.json()["error"]["code"])

            profile = client.post(
                "/api/v2/papers/paper-1/index",
                json={
                    "sourceMode": "native",
                    "sourceDocumentId": "source-1",
                    "includeEmbeddings": True,
                },
            )
            self.assertEqual(409, profile.status_code)
            self.assertEqual(
                "EMBEDDING_PROFILE_UNAVAILABLE",
                profile.json()["error"]["code"],
            )

            stale = client.get(
                "/api/v2/papers/paper-1/index-status",
                params={"sourceDocumentId": "source-1"},
            )
            self.assertEqual(409, stale.status_code)
            self.assertEqual("SOURCE_STALE", stale.json()["error"]["code"])
        finally:
            client_context.__exit__(None, None, None)


if __name__ == "__main__":
    unittest.main()
