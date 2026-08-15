from __future__ import annotations

import asyncio
from contextlib import closing
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
import os
import struct
import threading
import unittest
from unittest import mock

if (
    os.name == "nt"
    and hasattr(os, "add_dll_directory")
    and os.environ.get("P3_SQLITE_DLL_DIR")
):
    _SQLITE_DLL_HANDLE = os.add_dll_directory(os.environ["P3_SQLITE_DLL_DIR"])

import sqlite3


NOW = datetime(2026, 8, 13, 16, 0, tzinfo=timezone.utc)


class ChunkEmbeddingTests(unittest.IsolatedAsyncioTestCase):
    async def test_provider_return_rechecks_current_lease_time_before_vector_write(
        self,
    ) -> None:
        from backend.app.application.document_search import DocumentSearch
        from backend.app.domain import JobLeaseLostError
        from backend.app.domain.context import (
            ChunkingSpec,
            EmbeddingBatch,
            EmbeddingProfile,
            EmbeddingRequest,
        )
        from backend.app.repositories.document_search import (
            SqlAlchemyDocumentSearchRepository,
        )
        from backend.tests.support.p3_database import p3_context_fixture

        profile = EmbeddingProfile(
            provider="fixture-embedding",
            model="lease-time-model",
            embedding_version="lease-time-v1",
            dimensions=3,
        )
        now = [NOW]
        pdf_bytes = b"embedding lease time pdf"
        async with p3_context_fixture(
            prefix="study-app-p3-embedding-lease-time-",
            source_id="src_embedding_lease_time",
            markdown="# Lease time\n\nProvider return sentinel.\n",
            spec=ChunkingSpec(target_tokens=8, hard_cap_tokens=8),
            now=NOW,
            pdf_sha256=hashlib.sha256(pdf_bytes).hexdigest(),
            options_hash="9" * 64,
        ) as fixture:
            pdf_path = fixture.database_path.parent / "lease-time.pdf"
            pdf_path.write_bytes(pdf_bytes)
            with closing(sqlite3.connect(fixture.database_path)) as connection:
                connection.execute(
                    "UPDATE papers SET pdf_path=? WHERE id='paper-1'",
                    (str(pdf_path),),
                )
                connection.commit()

            class Provider:
                provider_id = profile.provider

                async def embed(self, request: EmbeddingRequest) -> EmbeddingBatch:
                    now[0] = NOW + timedelta(seconds=2)
                    return EmbeddingBatch(
                        profile=request.profile,
                        chunk_ids=request.chunk_ids,
                        vectors=tuple((1.0, 2.0, 2.0) for _ in request.chunk_ids),
                    )

            repository = SqlAlchemyDocumentSearchRepository(fixture.session_factory)
            search = DocumentSearch(
                repository,
                context_builder=fixture.builder,
                embedding_provider=Provider(),
                clock=lambda: now[0],
            )
            await search.enqueue_index(
                paper_id="paper-1",
                source_mode="native",
                source_document_id=fixture.chunk_set.source_document_id,
                include_embeddings=True,
                profile=profile,
            )
            async with fixture.unit_of_work_factory() as work:
                lease = await work.jobs.claim_next(
                    worker_id="embedding-lease-time",
                    now=NOW,
                    lease_seconds=1,
                )
                await work.commit()
            self.assertIsNotNone(lease)
            assert lease is not None

            with self.assertRaises(JobLeaseLostError):
                await search.index(
                    lease,
                    fixture.chunk_set.source_document_id,
                    profile,
                )

            with closing(sqlite3.connect(fixture.database_path)) as connection:
                count = connection.execute(
                    "SELECT count(*) FROM document_chunk_embeddings"
                ).fetchone()[0]
            self.assertEqual(0, count)

    async def test_index_enqueue_rejects_mismatched_native_provider_identity(self) -> None:
        from backend.app.application.document_search import DocumentSearch
        from backend.app.domain import StaleSourceError
        from backend.app.domain.context import ChunkingSpec
        from backend.app.repositories.document_search import (
            SqlAlchemyDocumentSearchRepository,
        )
        from backend.tests.support.p3_database import p3_context_fixture

        pdf_bytes = b"p3 index native identity pdf"
        source_id = "src_index_native_identity"
        async with p3_context_fixture(
            prefix="study-app-p3-index-native-identity-",
            source_id=source_id,
            markdown="# Index\n\nnative identity sentinel.\n",
            spec=ChunkingSpec(target_tokens=8, hard_cap_tokens=8),
            now=NOW,
            pdf_sha256=hashlib.sha256(pdf_bytes).hexdigest(),
            options_hash="c" * 64,
        ) as fixture:
            pdf_path = fixture.database_path.parent / "native-identity.pdf"
            pdf_path.write_bytes(pdf_bytes)
            with closing(sqlite3.connect(fixture.database_path)) as connection:
                connection.execute(
                    "UPDATE papers SET pdf_path=? WHERE id='paper-1'",
                    (str(pdf_path),),
                )
                connection.execute(
                    "UPDATE document_sources SET provider='wrong-provider',"
                    "model='wrong-model' WHERE id=?",
                    (source_id,),
                )
                connection.commit()

            search = DocumentSearch(
                SqlAlchemyDocumentSearchRepository(fixture.session_factory),
                context_builder=fixture.builder,
                clock=lambda: NOW,
                job_id_factory=lambda: "job_index_native_identity",
            )
            with self.assertRaises(StaleSourceError) as raised:
                await search.enqueue_index(
                    paper_id="paper-1",
                    source_mode="native",
                    source_document_id=source_id,
                    include_embeddings=False,
                    profile=None,
                )
            self.assertEqual("SOURCE_STALE", raised.exception.code)
            with closing(sqlite3.connect(fixture.database_path)) as connection:
                self.assertEqual(
                    0,
                    connection.execute(
                        "SELECT count(*) FROM processing_jobs"
                    ).fetchone()[0],
                )

    async def test_embed_job_handler_materializes_missing_chunks_for_lexical_only_index(
        self,
    ) -> None:
        from backend.app.application.context_builder import ContextBuilder
        from backend.app.application.document_search import (
            DocumentSearch,
            EmbeddingJobHandler,
        )
        from backend.app.domain import SourceDocument
        from backend.app.repositories.document_search import (
            SqlAlchemyDocumentSearchRepository,
        )
        from backend.app.repositories.unit_of_work import SqlAlchemyUnitOfWork
        from backend.tests.support.p3_database import p3_database_fixture

        source_id = "src_embedding_missing_chunks"
        markdown = (
            "[page 1]\n# Missing chunks\n\n"
            "Lexical materialization body.\n\n"
            "LEXICAL_MATERIALIZATION_TAIL_SENTINEL\n"
        )
        pdf_bytes = b"p3 lexical materialization pdf"
        pdf_sha256 = hashlib.sha256(pdf_bytes).hexdigest()
        async with p3_database_fixture(
            prefix="study-app-p3-embedding-missing-chunks-"
        ) as fixture:
            pdf_path = fixture.database_path.parent / "missing-chunks.pdf"
            pdf_path.write_bytes(pdf_bytes)
            factory = lambda: SqlAlchemyUnitOfWork(fixture.session_factory)
            async with factory() as work:
                await work.sources.add(
                    SourceDocument(
                        id=source_id,
                        paper_id="paper-1",
                        mode="native",
                        status="ready",
                        provider="local",
                        model="pymupdf4llm-pymupdf",
                        pdf_sha256=pdf_sha256,
                        options_hash="c" * 64,
                        processing_version="native-v1",
                        created_at=NOW,
                        updated_at=NOW,
                        markdown=markdown,
                        content_sha256=hashlib.sha256(
                            markdown.encode("utf-8")
                        ).hexdigest(),
                        page_count=1,
                    )
                )
                await work.commit()
            with closing(sqlite3.connect(fixture.database_path)) as connection:
                connection.execute(
                    "UPDATE papers SET pdf_path=? WHERE id='paper-1'",
                    (str(pdf_path),),
                )
                connection.commit()
                self.assertEqual(
                    0,
                    connection.execute(
                        "SELECT count(*) FROM document_chunks WHERE source_document_id=?",
                        (source_id,),
                    ).fetchone()[0],
                )

            builder = ContextBuilder(factory)
            repository = SqlAlchemyDocumentSearchRepository(fixture.session_factory)
            command = DocumentSearch(
                repository,
                context_builder=builder,
                clock=lambda: NOW,
                job_id_factory=lambda: "job_embedding_missing_chunks",
            )
            enqueue = await command.enqueue_index(
                paper_id="paper-1",
                source_mode="native",
                source_document_id=source_id,
                include_embeddings=False,
                profile=None,
            )
            async with factory() as work:
                lease = await work.jobs.claim_next(
                    worker_id="embedding-missing-chunks-worker",
                    now=NOW,
                    lease_seconds=3600,
                )
                await work.commit()
            self.assertIsNotNone(lease)
            assert lease is not None
            self.assertEqual(enqueue.job.id, lease.job.id)

            class ForbiddenCredentialStore:
                async def get(self, _kind):
                    raise AssertionError("lexical-only index must not read credentials")

            def forbidden_provider_factory(_profile, _credential):
                raise AssertionError("lexical-only index must not construct a provider")

            result = await EmbeddingJobHandler(
                repository,
                context_builder=builder,
                credential_store=ForbiddenCredentialStore(),
                provider_factory=forbidden_provider_factory,
                clock=lambda: NOW,
            )(lease)

            self.assertGreater(result.value["totalChunks"], 0)
            self.assertEqual(0, result.value["embeddedChunks"])
            with closing(sqlite3.connect(fixture.database_path)) as connection:
                chunks = connection.execute(
                    "SELECT sequence,content FROM document_chunks "
                    "WHERE source_document_id=? AND status='ready' ORDER BY sequence",
                    (source_id,),
                ).fetchall()
                fts_count = connection.execute(
                    "SELECT count(*) FROM document_chunks_fts "
                    "WHERE document_chunks_fts MATCH ?",
                    ('"MATERIALIZATION"',),
                ).fetchone()[0]
            self.assertEqual(markdown, "".join(row[1] for row in chunks))
            self.assertEqual(list(range(len(chunks))), [row[0] for row in chunks])
            self.assertIn("LEXICAL_MATERIALIZATION_TAIL_SENTINEL", chunks[-1][1])
            self.assertGreater(fts_count, 0)

    async def test_cancel_and_source_stale_during_provider_call_stop_before_next_batch(
        self,
    ) -> None:
        from backend.app.application.document_search import DocumentSearch
        from backend.app.application.source_freshness import SourceFreshnessService
        from backend.app.domain import JobLeaseLostError
        from backend.app.domain.context import (
            ChunkingSpec,
            EmbeddingBatch,
            EmbeddingProfile,
            EmbeddingRequest,
        )
        from backend.app.repositories.document_search import (
            SqlAlchemyDocumentSearchRepository,
        )
        from backend.tests.support.p3_database import p3_context_fixture

        profile = EmbeddingProfile(
            provider="fake-embedding",
            model="fence-model-v1",
            embedding_version="fixture-fence-v1",
            dimensions=3,
        )
        for case in ("cancel", "source_stale"):
            with self.subTest(case=case):
                pdf_bytes = f"p3 embedding {case} pdf".encode("ascii")
                async with p3_context_fixture(
                    prefix=f"study-app-p3-embedding-{case}-",
                    source_id=f"src_embedding_{case}",
                    markdown=(
                        "[page 1]\n# Fence coverage\n\n"
                        "Fence alpha chunk.\n\n"
                        "Fence beta chunk.\n\n"
                        "Fence gamma chunk.\n\n"
                        "Fence tail sentinel.\n"
                    ),
                    spec=ChunkingSpec(target_tokens=4, hard_cap_tokens=4),
                    now=NOW,
                    pdf_sha256=hashlib.sha256(pdf_bytes).hexdigest(),
                    options_hash="a" * 64,
                ) as fixture:
                    pdf_path = fixture.database_path.parent / f"embedding-{case}.pdf"
                    pdf_path.write_bytes(pdf_bytes)
                    with closing(sqlite3.connect(fixture.database_path)) as connection:
                        connection.execute(
                            "UPDATE papers SET pdf_path=? WHERE id='paper-1'",
                            (str(pdf_path),),
                        )
                        connection.commit()

                    repository = SqlAlchemyDocumentSearchRepository(
                        fixture.session_factory
                    )
                    command = DocumentSearch(
                        repository,
                        context_builder=fixture.builder,
                        clock=lambda: NOW,
                        job_id_factory=lambda: f"job_embedding_{case}",
                    )
                    enqueue = await command.enqueue_index(
                        paper_id="paper-1",
                        source_mode="native",
                        source_document_id=fixture.chunk_set.source_document_id,
                        include_embeddings=True,
                        profile=profile,
                    )
                    async with fixture.unit_of_work_factory() as work:
                        lease = await work.jobs.claim_next(
                            worker_id=f"embedding-{case}-worker",
                            now=NOW,
                            lease_seconds=3600,
                        )
                        await work.commit()
                    self.assertIsNotNone(lease)
                    assert lease is not None

                    async def race_action() -> None:
                        if case == "cancel":
                            async with fixture.unit_of_work_factory() as work:
                                await work.jobs.cancel(enqueue.job.id, now=NOW)
                                await work.commit()
                        else:
                            await SourceFreshnessService(
                                fixture.unit_of_work_factory
                            ).reconcile_pdf(
                                "paper-1",
                                "f" * 64,
                                now=NOW,
                            )

                    class RacingProvider:
                        provider_id = profile.provider

                        def __init__(self) -> None:
                            self.calls: list[tuple[str, ...]] = []

                        async def embed(
                            self, request: EmbeddingRequest
                        ) -> EmbeddingBatch:
                            self.calls.append(request.chunk_ids)
                            if len(self.calls) == 2:
                                await race_action()
                            return EmbeddingBatch(
                                profile=request.profile,
                                chunk_ids=request.chunk_ids,
                                vectors=tuple(
                                    (1.0, 1.0, 1.0) for _ in request.chunk_ids
                                ),
                            )

                    provider = RacingProvider()
                    search = DocumentSearch(
                        repository,
                        context_builder=fixture.builder,
                        embedding_provider=provider,
                        clock=lambda: NOW,
                        embedding_batch_size=1,
                    )
                    with self.assertRaises(JobLeaseLostError):
                        await search.index(
                            lease,
                            fixture.chunk_set.source_document_id,
                            profile,
                        )
                    self.assertEqual(2, len(provider.calls))
                    with closing(sqlite3.connect(fixture.database_path)) as connection:
                        embedding_states = connection.execute(
                            "SELECT status FROM document_chunk_embeddings "
                            "WHERE source_document_id=? AND provider=? AND model=? "
                            "AND embedding_version=? ORDER BY id",
                            (
                                fixture.chunk_set.source_document_id,
                                profile.provider,
                                profile.model,
                                profile.embedding_version,
                            ),
                        ).fetchall()
                        job_state = connection.execute(
                            "SELECT status,cancel_requested_at FROM processing_jobs "
                            "WHERE id=?",
                            (enqueue.job.id,),
                        ).fetchone()
                    self.assertEqual(1, len(embedding_states))
                    if case == "cancel":
                        self.assertEqual([("ready",)], embedding_states)
                        self.assertEqual("running", job_state[0])
                        self.assertIsNotNone(job_state[1])
                    else:
                        self.assertEqual([("stale",)], embedding_states)
                        self.assertEqual("running", job_state[0])
                        self.assertIsNotNone(job_state[1])

    async def test_embed_job_handler_keeps_lexical_only_external_free_and_uses_frozen_profile(
        self,
    ) -> None:
        from backend.app.application.document_search import (
            DocumentSearch,
            EmbeddingJobHandler,
        )
        from backend.app.domain import Credential, CredentialKind
        from backend.app.domain.context import (
            ChunkingSpec,
            EmbeddingBatch,
            EmbeddingProfile,
            EmbeddingRequest,
        )
        from backend.app.repositories.document_search import (
            SqlAlchemyDocumentSearchRepository,
        )
        from backend.tests.support.p3_database import p3_context_fixture

        pdf_bytes = b"p3 lexical and embedding composition pdf"
        pdf_sha256 = hashlib.sha256(pdf_bytes).hexdigest()
        profile = EmbeddingProfile(
            provider="openai-compatible",
            model="frozen-composition-model",
            embedding_version="openai-compatible-v1",
            dimensions=3,
            options={"batchSize": 2},
        )
        async with p3_context_fixture(
            prefix="study-app-p3-embedding-composition-",
            source_id="src_embedding_composition",
            markdown=(
                "[page 1]\n# Composition\n\n"
                "Composition alpha chunk.\n\n"
                "Composition beta chunk.\n\n"
                "Composition tail sentinel.\n"
            ),
            spec=ChunkingSpec(target_tokens=4, hard_cap_tokens=4),
            now=NOW,
            pdf_sha256=pdf_sha256,
            options_hash="9" * 64,
        ) as fixture:
            pdf_path = fixture.database_path.parent / "embedding-composition.pdf"
            pdf_path.write_bytes(pdf_bytes)
            with closing(sqlite3.connect(fixture.database_path)) as connection:
                connection.execute(
                    "UPDATE papers SET pdf_path=? WHERE id='paper-1'",
                    (str(pdf_path),),
                )
                connection.commit()

            repository = SqlAlchemyDocumentSearchRepository(fixture.session_factory)
            job_ids = iter(("job_lexical_composition", "job_embed_composition"))
            command = DocumentSearch(
                repository,
                context_builder=fixture.builder,
                clock=lambda: NOW,
                job_id_factory=lambda: next(job_ids),
            )
            lexical_enqueue = await command.enqueue_index(
                paper_id="paper-1",
                source_mode="native",
                source_document_id=fixture.chunk_set.source_document_id,
                include_embeddings=False,
                profile=None,
            )
            async with fixture.unit_of_work_factory() as work:
                lexical_lease = await work.jobs.claim_next(
                    worker_id="embedding-composition-worker",
                    now=NOW,
                    lease_seconds=3600,
                )
                await work.commit()
            self.assertIsNotNone(lexical_lease)
            assert lexical_lease is not None
            self.assertEqual(lexical_enqueue.job.id, lexical_lease.job.id)

            calls = {"credential": [], "factory": [], "provider": []}

            class CredentialStoreSpy:
                async def get(self, kind):
                    calls["credential"].append(kind)
                    return Credential(CredentialKind.EMBEDDING, "composition-secret")

            class RecordingProvider:
                provider_id = profile.provider

                async def embed(self, request: EmbeddingRequest) -> EmbeddingBatch:
                    calls["provider"].append(request)
                    return EmbeddingBatch(
                        profile=request.profile,
                        chunk_ids=request.chunk_ids,
                        vectors=tuple((2.0, 1.0, 2.0) for _ in request.chunk_ids),
                    )

            provider = RecordingProvider()

            def provider_factory(frozen_profile, credential):
                calls["factory"].append((frozen_profile, credential))
                return provider

            handler = EmbeddingJobHandler(
                repository,
                context_builder=fixture.builder,
                credential_store=CredentialStoreSpy(),
                provider_factory=provider_factory,
                clock=lambda: NOW,
            )
            lexical_result = await handler(lexical_lease)
            self.assertEqual([], calls["credential"])
            self.assertEqual([], calls["factory"])
            self.assertEqual([], calls["provider"])
            self.assertEqual(0, lexical_result.value["embeddedChunks"])
            self.assertEqual(
                len(fixture.chunk_set.chunks),
                lexical_result.value["totalChunks"],
            )
            async with fixture.unit_of_work_factory() as work:
                await work.jobs.complete(lexical_lease, lexical_result, now=NOW)
                await work.commit()

            embed_enqueue = await command.enqueue_index(
                paper_id="paper-1",
                source_mode="native",
                source_document_id=fixture.chunk_set.source_document_id,
                include_embeddings=True,
                profile=profile,
            )
            async with fixture.unit_of_work_factory() as work:
                embed_lease = await work.jobs.claim_next(
                    worker_id="embedding-composition-worker",
                    now=NOW,
                    lease_seconds=3600,
                )
                await work.commit()
            self.assertIsNotNone(embed_lease)
            assert embed_lease is not None
            self.assertEqual(embed_enqueue.job.id, embed_lease.job.id)

            embed_result = await handler(embed_lease)
            self.assertEqual([CredentialKind.EMBEDDING], calls["credential"])
            self.assertEqual(1, len(calls["factory"]))
            frozen_profile, resolved_credential = calls["factory"][0]
            self.assertEqual(profile, frozen_profile)
            self.assertEqual(CredentialKind.EMBEDDING, resolved_credential.kind)
            self.assertTrue(calls["provider"])
            self.assertTrue(
                all(request.profile == profile for request in calls["provider"])
            )
            self.assertEqual(
                len(fixture.chunk_set.chunks),
                embed_result.value["embeddedChunks"],
            )

    async def test_complete_new_profile_preserves_prior_valid_profile_for_switchback(
        self,
    ) -> None:
        from backend.app.application.document_search import DocumentSearch
        from backend.app.domain.context import (
            ChunkingSpec,
            EmbeddingBatch,
            EmbeddingProfile,
            EmbeddingRequest,
        )
        from backend.app.domain.processing import JobResult
        from backend.app.repositories.document_search import (
            SqlAlchemyDocumentSearchRepository,
        )
        from backend.tests.support.p3_database import p3_context_fixture

        pdf_bytes = b"p3 embedding profile activation pdf"
        pdf_sha256 = hashlib.sha256(pdf_bytes).hexdigest()
        old_profile = EmbeddingProfile(
            provider="fake-embedding",
            model="profile-model-v1",
            embedding_version="fixture-profile-v1",
            dimensions=3,
        )
        new_profile = EmbeddingProfile(
            provider="fake-embedding",
            model="profile-model-v2",
            embedding_version="fixture-profile-v2",
            dimensions=3,
        )
        async with p3_context_fixture(
            prefix="study-app-p3-embedding-profile-",
            source_id="src_embedding_profile",
            markdown=(
                "[page 1]\n# Profile activation\n\n"
                "First profile chunk.\n\n"
                "Second profile chunk.\n\n"
                "Tail profile sentinel.\n"
            ),
            spec=ChunkingSpec(target_tokens=4, hard_cap_tokens=4),
            now=NOW,
            pdf_sha256=pdf_sha256,
            options_hash="8" * 64,
        ) as fixture:
            pdf_path = fixture.database_path.parent / "embedding-profile.pdf"
            pdf_path.write_bytes(pdf_bytes)
            with closing(sqlite3.connect(fixture.database_path)) as connection:
                connection.execute(
                    "UPDATE papers SET pdf_path=? WHERE id='paper-1'",
                    (str(pdf_path),),
                )
                connection.commit()

            class FixedProvider:
                def __init__(self, profile: EmbeddingProfile, vector: tuple[float, ...]) -> None:
                    self.profile = profile
                    self.provider_id = profile.provider
                    self.vector = vector

                async def embed(self, request: EmbeddingRequest) -> EmbeddingBatch:
                    return EmbeddingBatch(
                        profile=request.profile,
                        chunk_ids=request.chunk_ids,
                        vectors=tuple(self.vector for _ in request.chunk_ids),
                    )

            repository = SqlAlchemyDocumentSearchRepository(fixture.session_factory)
            old_search = DocumentSearch(
                repository,
                context_builder=fixture.builder,
                embedding_provider=FixedProvider(old_profile, (1.0, 0.0, 0.0)),
                clock=lambda: NOW,
                embedding_batch_size=2,
                job_id_factory=lambda: "job_embedding_profile_old",
            )
            old_enqueue = await old_search.enqueue_index(
                paper_id="paper-1",
                source_mode="native",
                source_document_id=fixture.chunk_set.source_document_id,
                include_embeddings=True,
                profile=old_profile,
            )
            async with fixture.unit_of_work_factory() as work:
                old_lease = await work.jobs.claim_next(
                    worker_id="embedding-profile-old",
                    now=NOW,
                    lease_seconds=3600,
                )
                await work.commit()
            self.assertIsNotNone(old_lease)
            assert old_lease is not None
            old_result = await old_search.index(
                old_lease,
                fixture.chunk_set.source_document_id,
                old_profile,
            )
            async with fixture.unit_of_work_factory() as work:
                await work.jobs.complete(
                    old_lease,
                    JobResult(old_result.to_job_result()),
                    now=NOW,
                )
                await work.commit()
            self.assertEqual(old_enqueue.job.id, old_lease.job.id)

            with closing(sqlite3.connect(fixture.database_path)) as connection:
                old_before = connection.execute(
                    "SELECT chunk_id,vector,vector_sha256,status,stale_at "
                    "FROM document_chunk_embeddings WHERE source_document_id=? "
                    "AND provider=? AND model=? AND embedding_version=? ORDER BY chunk_id",
                    (
                        fixture.chunk_set.source_document_id,
                        old_profile.provider,
                        old_profile.model,
                        old_profile.embedding_version,
                    ),
                ).fetchall()
            self.assertEqual(len(fixture.chunk_set.chunks), len(old_before))
            self.assertTrue(all(row[3:] == ("ready", None) for row in old_before))

            new_search = DocumentSearch(
                repository,
                context_builder=fixture.builder,
                embedding_provider=FixedProvider(new_profile, (0.0, 1.0, 0.0)),
                clock=lambda: NOW,
                embedding_batch_size=2,
                job_id_factory=lambda: "job_embedding_profile_new",
            )
            new_enqueue = await new_search.enqueue_index(
                paper_id="paper-1",
                source_mode="native",
                source_document_id=fixture.chunk_set.source_document_id,
                include_embeddings=True,
                profile=new_profile,
            )
            async with fixture.unit_of_work_factory() as work:
                new_lease = await work.jobs.claim_next(
                    worker_id="embedding-profile-new",
                    now=NOW,
                    lease_seconds=3600,
                )
                await work.commit()
            self.assertIsNotNone(new_lease)
            assert new_lease is not None
            new_result = await new_search.index(
                new_lease,
                fixture.chunk_set.source_document_id,
                new_profile,
            )
            async with fixture.unit_of_work_factory() as work:
                await work.jobs.complete(
                    new_lease,
                    JobResult(new_result.to_job_result()),
                    now=NOW,
                )
                await work.commit()
            self.assertEqual(new_enqueue.job.id, new_lease.job.id)

            switchback = await old_search.enqueue_index(
                paper_id="paper-1",
                source_mode="native",
                source_document_id=fixture.chunk_set.source_document_id,
                include_embeddings=True,
                profile=old_profile,
            )
            self.assertTrue(switchback.deduplicated)
            self.assertEqual(old_enqueue.job.id, switchback.job.id)

            with closing(sqlite3.connect(fixture.database_path)) as connection:
                old_after = connection.execute(
                    "SELECT chunk_id,vector,vector_sha256,status,stale_at "
                    "FROM document_chunk_embeddings WHERE source_document_id=? "
                    "AND provider=? AND model=? AND embedding_version=? ORDER BY chunk_id",
                    (
                        fixture.chunk_set.source_document_id,
                        old_profile.provider,
                        old_profile.model,
                        old_profile.embedding_version,
                    ),
                ).fetchall()
                new_rows = connection.execute(
                    "SELECT status FROM document_chunk_embeddings "
                    "WHERE source_document_id=? AND provider=? AND model=? "
                    "AND embedding_version=?",
                    (
                        fixture.chunk_set.source_document_id,
                        new_profile.provider,
                        new_profile.model,
                        new_profile.embedding_version,
                    ),
                ).fetchall()
            self.assertEqual(
                tuple((row[0], row[1], row[2]) for row in old_before),
                tuple((row[0], row[1], row[2]) for row in old_after),
            )
            self.assertTrue(all(row[3:] == ("ready", None) for row in old_after))
            self.assertEqual(
                [("ready",)] * len(fixture.chunk_set.chunks),
                new_rows,
            )

    async def test_embed_worker_persists_failed_batch_and_retries_only_missing_or_failed_chunks(
        self,
    ) -> None:
        """A provider failure is durable per chunk and resumes through the queue."""
        from sqlalchemy import event

        from backend.app.application.document_search import (
            DocumentSearch,
            EmbeddingJobHandler,
        )
        from backend.app.domain import Credential, CredentialKind
        from backend.app.domain.context import (
            ChunkingSpec,
            EmbeddingBatch,
            EmbeddingProfile,
            EmbeddingRequest,
        )
        from backend.app.repositories.document_search import (
            SqlAlchemyDocumentSearchRepository,
        )
        from backend.app.repositories.unit_of_work import SqlAlchemyUnitOfWork
        from backend.app.workers.processing_worker import ProcessingWorker
        from backend.tests.support.p3_database import p3_context_fixture

        markdown = (
            "[page 1]\n# Durable embedding resume\n\n"
            "Alpha durable embedding chunk.\n\n"
            "Beta durable embedding chunk.\n\n"
            "Gamma durable embedding chunk.\n\n"
            "Delta durable embedding chunk.\n\n"
            "EMBEDDING_FAILED_RESUME_TAIL_SENTINEL\n"
        )
        pdf_bytes = b"p3 durable embedding failure resume pdf"
        profile = EmbeddingProfile(
            provider="fixture-embedding",
            model="fixture-embedding-model",
            embedding_version="fixture-embedding-v1",
            dimensions=3,
            options={"batchSize": 2},
        )
        now = [NOW]
        async with p3_context_fixture(
            prefix="study-app-p3-embedding-worker-failed-resume-",
            source_id="src_embedding_worker_failed_resume",
            markdown=markdown,
            spec=ChunkingSpec(),
            now=NOW,
            pdf_sha256=hashlib.sha256(pdf_bytes).hexdigest(),
            options_hash="a" * 64,
        ) as fixture:
            pdf_path = fixture.database_path.parent / "embedding-worker.pdf"
            pdf_path.write_bytes(pdf_bytes)
            with closing(sqlite3.connect(fixture.database_path)) as connection:
                connection.execute(
                    "UPDATE papers SET pdf_path=? WHERE id='paper-1'",
                    (str(pdf_path),),
                )
                connection.commit()

            repository = SqlAlchemyDocumentSearchRepository(fixture.session_factory)
            enqueue = await DocumentSearch(
                repository,
                context_builder=fixture.builder,
                clock=lambda: now[0],
                job_id_factory=lambda: "job_embedding_worker_failed_resume",
            ).enqueue_index(
                paper_id="paper-1",
                source_mode="native",
                source_document_id=fixture.chunk_set.source_document_id,
                include_embeddings=True,
                profile=profile,
            )

            active_transactions: set[int] = set()
            engine = fixture.session_factory.kw["bind"]

            def transaction_started(connection) -> None:
                active_transactions.add(id(connection))

            def transaction_finished(connection) -> None:
                active_transactions.discard(id(connection))

            event.listen(engine.sync_engine, "begin", transaction_started)
            event.listen(engine.sync_engine, "commit", transaction_finished)
            event.listen(engine.sync_engine, "rollback", transaction_finished)

            class FailSecondBatchOnceProvider:
                provider_id = profile.provider

                def __init__(self) -> None:
                    self.calls: list[tuple[str, ...]] = []
                    self.failed = False

                async def embed(self, request: EmbeddingRequest) -> EmbeddingBatch:
                    if active_transactions:
                        raise AssertionError(
                            "embedding provider was called during a database transaction"
                        )
                    self.calls.append(request.chunk_ids)
                    if len(self.calls) == 2 and not self.failed:
                        self.failed = True
                        raise TimeoutError("fixture transport timeout")
                    return EmbeddingBatch(
                        profile=request.profile,
                        chunk_ids=request.chunk_ids,
                        vectors=tuple((1.0, 2.0, 2.0) for _ in request.chunk_ids),
                    )

            provider = FailSecondBatchOnceProvider()

            class CredentialStore:
                async def get(self, kind):
                    if kind is not CredentialKind.EMBEDDING:
                        raise AssertionError("only the embedding credential is allowed")
                    return Credential(CredentialKind.EMBEDDING, "fixture-credential")

            handler = EmbeddingJobHandler(
                repository,
                context_builder=fixture.builder,
                credential_store=CredentialStore(),
                provider_factory=lambda frozen_profile, _credential: (
                    provider if frozen_profile == profile else None
                ),
                clock=lambda: now[0],
            )
            worker = ProcessingWorker(
                lambda: SqlAlchemyUnitOfWork(fixture.session_factory),
                handlers={"embed": handler},
                worker_id="embedding-worker-failed-resume",
                clock=lambda: now[0],
                lease_seconds=60,
            )
            try:
                self.assertTrue(await worker.run_once())
            finally:
                event.remove(engine.sync_engine, "begin", transaction_started)
                event.remove(engine.sync_engine, "commit", transaction_finished)
                event.remove(engine.sync_engine, "rollback", transaction_finished)

            chunk_ids = fixture.chunk_set.chunk_ids
            with closing(sqlite3.connect(fixture.database_path)) as connection:
                job_row = connection.execute(
                    "SELECT status,attempt,error_code FROM processing_jobs WHERE id=?",
                    (enqueue.job.id,),
                ).fetchone()
                failed_rows = connection.execute(
                    "SELECT e.chunk_id,e.status,e.vector,e.vector_sha256,e.error_code "
                    "FROM document_chunk_embeddings e JOIN document_chunks c ON c.id=e.chunk_id "
                    "WHERE e.source_document_id=? AND e.provider=? AND e.model=? "
                    "AND e.embedding_version=? ORDER BY c.sequence",
                    (
                        fixture.chunk_set.source_document_id,
                        profile.provider,
                        profile.model,
                        profile.embedding_version,
                    ),
                ).fetchall()
            self.assertEqual(("queued", 1, "EMBEDDING_REQUEST_FAILED"), job_row)
            self.assertEqual(
                [
                    (chunk_ids[0], "ready", None),
                    (chunk_ids[1], "ready", None),
                    *[
                        (chunk_id, "failed", "EMBEDDING_REQUEST_FAILED")
                        for chunk_id in chunk_ids[2:4]
                    ],
                ],
                [
                    (chunk_id, status, error_code)
                    for chunk_id, status, _vector, _vector_sha, error_code in failed_rows
                ],
            )
            self.assertTrue(all(row[2] is not None for row in failed_rows[:2]))
            self.assertTrue(
                all(row[2] is None and row[3] is None for row in failed_rows[2:])
            )

            now[0] = NOW + timedelta(seconds=5)
            self.assertTrue(await worker.run_once())
            self.assertEqual(chunk_ids[2:4], provider.calls[1])
            self.assertEqual(chunk_ids[2:4], provider.calls[2])
            retried_ids = tuple(chunk_id for call in provider.calls[2:] for chunk_id in call)
            self.assertNotIn(chunk_ids[0], retried_ids)
            self.assertNotIn(chunk_ids[1], retried_ids)

            with closing(sqlite3.connect(fixture.database_path)) as connection:
                job_row = connection.execute(
                    "SELECT status,attempt,error_code FROM processing_jobs WHERE id=?",
                    (enqueue.job.id,),
                ).fetchone()
                rows = connection.execute(
                    "SELECT e.chunk_id,e.status,e.vector,e.error_code "
                    "FROM document_chunk_embeddings e JOIN document_chunks c ON c.id=e.chunk_id "
                    "WHERE e.source_document_id=? AND e.provider=? AND e.model=? "
                    "AND e.embedding_version=? ORDER BY c.sequence",
                    (
                        fixture.chunk_set.source_document_id,
                        profile.provider,
                        profile.model,
                        profile.embedding_version,
                    ),
                ).fetchall()
            self.assertEqual(
                ("succeeded", 2, "EMBEDDING_REQUEST_FAILED"), job_row
            )
            self.assertEqual(chunk_ids, tuple(row[0] for row in rows))
            self.assertTrue(all(row[1] == "ready" and row[2] is not None and row[3] is None for row in rows))

    async def test_transient_batch_failure_retries_only_unfinished_chunks(
        self,
    ) -> None:
        from backend.app.application.document_search import DocumentSearch
        from backend.app.domain import EmbeddingRequestFailedError
        from backend.app.domain.context import (
            ChunkingSpec,
            EmbeddingBatch,
            EmbeddingProfile,
            EmbeddingRequest,
        )
        from backend.app.domain.processing import (
            EmbedJobSpecV1,
            JobFailure,
            JobResult,
            NewProcessingJob,
            build_index_job_key,
            encode_job_spec_v1,
            hash_job_spec,
        )
        from backend.app.repositories.document_search import (
            SqlAlchemyDocumentSearchRepository,
        )
        from backend.tests.support.p3_database import p3_context_fixture

        markdown = (
            "[page 1]\n# Retry coverage\n\n"
            "Alpha retry chunk.\n\n"
            "Beta retry chunk.\n\n"
            "Gamma retry chunk.\n\n"
            "Delta retry chunk.\n\n"
            "Tail retry sentinel.\n"
        )
        profile = EmbeddingProfile(
            provider="fake-embedding",
            model="fake-retry-model-v1",
            embedding_version="fixture-retry-v1",
            dimensions=3,
            options={"batchSize": 2},
        )
        async with p3_context_fixture(
            prefix="study-app-p3-embedding-retry-",
            source_id="src_embedding_retry",
            markdown=markdown,
            spec=ChunkingSpec(target_tokens=4, hard_cap_tokens=4),
            now=NOW,
            pdf_sha256="6" * 64,
            options_hash="7" * 64,
        ) as fixture:
            chunk_ids = fixture.chunk_set.chunk_ids
            self.assertGreater(len(chunk_ids), 2)
            spec = EmbedJobSpecV1(
                paper_id="paper-1",
                source_document_id=fixture.chunk_set.source_document_id,
                include_embeddings=True,
                provider=profile.provider,
                model=profile.model,
                embedding_version=profile.embedding_version,
                dimensions=profile.dimensions,
                chunking_version=fixture.chunk_set.spec.chunking_version,
                options=dict(profile.options),
            )
            raw_spec = encode_job_spec_v1(spec)
            job = NewProcessingJob(
                id="job_embedding_retry",
                spec=spec,
                idempotency_key=build_index_job_key(
                    source_document_id=spec.source_document_id,
                    source_content_sha256=fixture.chunk_set.source_content_sha256,
                    chunking_version=spec.chunking_version,
                    embedding_provider=spec.provider,
                    embedding_model=spec.model,
                    embedding_version=spec.embedding_version,
                    include_embeddings=True,
                    embedding_options=dict(spec.options),
                ),
                created_at=NOW,
                max_attempts=3,
            )
            async with fixture.unit_of_work_factory() as work:
                await work.jobs.insert_with_spec(
                    job,
                    spec_json=raw_spec,
                    spec_sha256=hash_job_spec(raw_spec),
                )
                await work.commit()
            async with fixture.unit_of_work_factory() as work:
                first_lease = await work.jobs.claim_next(
                    worker_id="embedding-worker-first",
                    now=NOW,
                    lease_seconds=3600,
                )
                await work.commit()
            self.assertIsNotNone(first_lease)
            assert first_lease is not None

            class FailSecondBatchOnceProvider:
                provider_id = profile.provider

                def __init__(self) -> None:
                    self.calls: list[tuple[str, ...]] = []
                    self.failed = False

                async def embed(self, request: EmbeddingRequest) -> EmbeddingBatch:
                    self.calls.append(request.chunk_ids)
                    if len(self.calls) == 2 and not self.failed:
                        self.failed = True
                        raise EmbeddingRequestFailedError(retryable=True)
                    return EmbeddingBatch(
                        profile=request.profile,
                        chunk_ids=request.chunk_ids,
                        vectors=tuple((1.0, 2.0, 2.0) for _ in request.chunk_ids),
                    )

            provider = FailSecondBatchOnceProvider()
            search = DocumentSearch(
                SqlAlchemyDocumentSearchRepository(fixture.session_factory),
                context_builder=fixture.builder,
                embedding_provider=provider,
                clock=lambda: NOW,
                embedding_batch_size=2,
            )
            with self.assertRaises(EmbeddingRequestFailedError):
                await search.index(first_lease, spec.source_document_id, profile)

            with closing(sqlite3.connect(fixture.database_path)) as connection:
                ready_after_failure = tuple(
                    row[0]
                    for row in connection.execute(
                        "SELECT e.chunk_id FROM document_chunk_embeddings e "
                        "JOIN document_chunks c ON c.id=e.chunk_id "
                        "WHERE e.source_document_id=? AND e.provider=? AND e.model=? "
                        "AND e.embedding_version=? AND e.status='ready' "
                        "ORDER BY c.sequence",
                        (
                            spec.source_document_id,
                            profile.provider,
                            profile.model,
                            profile.embedding_version,
                        ),
                    )
                )
                progress_after_failure = json.loads(
                    connection.execute(
                        "SELECT progress_json FROM processing_jobs WHERE id=?",
                        (job.id,),
                    ).fetchone()[0]
                )
            self.assertEqual(chunk_ids[:2], ready_after_failure)
            self.assertEqual(
                {
                    "phase": "embedding",
                    "completed": 2,
                    "total": len(chunk_ids),
                },
                progress_after_failure,
            )

            failed_at = NOW + timedelta(seconds=1)
            async with fixture.unit_of_work_factory() as work:
                await work.jobs.fail(
                    first_lease,
                    JobFailure(code="EMBEDDING_REQUEST_FAILED", retryable=True),
                    now=failed_at,
                )
                await work.commit()
            async with fixture.unit_of_work_factory() as work:
                retry_lease = await work.jobs.claim_next(
                    worker_id="embedding-worker-retry",
                    now=failed_at + timedelta(seconds=5),
                    lease_seconds=3600,
                )
                await work.commit()
            self.assertIsNotNone(retry_lease)
            assert retry_lease is not None
            self.assertEqual(2, retry_lease.job.attempt)

            result = await search.index(retry_lease, spec.source_document_id, profile)
            self.assertEqual(chunk_ids[2:4], provider.calls[1])
            self.assertEqual(chunk_ids[2:4], provider.calls[2])
            self.assertNotIn(chunk_ids[0], tuple(id_ for call in provider.calls[2:] for id_ in call))
            self.assertNotIn(chunk_ids[1], tuple(id_ for call in provider.calls[2:] for id_ in call))
            self.assertEqual(len(chunk_ids), result.embedded_chunks)
            self.assertEqual(2, result.reused_chunks)

            async with fixture.unit_of_work_factory() as work:
                await work.jobs.complete(
                    retry_lease,
                    JobResult(result.to_job_result()),
                    now=failed_at + timedelta(seconds=6),
                )
                await work.commit()
            with closing(sqlite3.connect(fixture.database_path)) as connection:
                status, ready_count = connection.execute(
                    "SELECT j.status,count(e.id) FROM processing_jobs j "
                    "JOIN document_chunk_embeddings e ON e.source_document_id=j.source_document_id "
                    "AND e.provider=? AND e.model=? AND e.embedding_version=? "
                    "AND e.status='ready' WHERE j.id=? GROUP BY j.status",
                    (
                        profile.provider,
                        profile.model,
                        profile.embedding_version,
                        job.id,
                    ),
                ).fetchone()
            self.assertEqual(("succeeded", len(chunk_ids)), (status, ready_count))

    async def test_index_enqueue_freezes_profile_deduplicates_and_separates_identity(
        self,
    ) -> None:
        from backend.app.application.document_search import DocumentSearch
        from backend.app.domain.context import ChunkingSpec, EmbeddingProfile
        from backend.app.domain.processing import (
            EmbedJobSpecV1,
            build_index_job_key,
            decode_job_spec_v1,
        )
        from backend.app.repositories.document_search import (
            SqlAlchemyDocumentSearchRepository,
        )
        from backend.tests.support.p3_database import p3_context_fixture

        pdf_bytes = b"p3 embedding enqueue pdf"
        pdf_sha256 = hashlib.sha256(pdf_bytes).hexdigest()
        profile = EmbeddingProfile(
            provider="openai-compatible",
            model="embed-model-v1",
            embedding_version="openai-compatible-v1",
            dimensions=3,
            options={"batchSize": 2},
        )
        changed_profile = EmbeddingProfile(
            provider="openai-compatible",
            model="embed-model-v2",
            embedding_version="openai-compatible-v2",
            dimensions=3,
            options={"batchSize": 4},
        )
        async with p3_context_fixture(
            prefix="study-app-p3-embedding-enqueue-",
            source_id="src_embedding_enqueue",
            markdown="# Index\n\nEmbedding enqueue sentinel.\n",
            spec=ChunkingSpec(target_tokens=8, hard_cap_tokens=8),
            now=NOW,
            pdf_sha256=pdf_sha256,
            options_hash="5" * 64,
        ) as fixture:
            pdf_path = fixture.database_path.parent / "embedding-enqueue.pdf"
            pdf_path.write_bytes(pdf_bytes)
            with closing(sqlite3.connect(fixture.database_path)) as connection:
                connection.execute(
                    "UPDATE papers SET pdf_path=? WHERE id='paper-1'",
                    (str(pdf_path),),
                )
                connection.commit()

            job_ids = iter(
                (
                    "job_index_one",
                    "job_index_duplicate",
                    "job_index_changed",
                    "job_index_lexical",
                )
            )
            search = DocumentSearch(
                SqlAlchemyDocumentSearchRepository(fixture.session_factory),
                context_builder=fixture.builder,
                clock=lambda: NOW,
                job_id_factory=lambda: next(job_ids),
            )

            first = await search.enqueue_index(
                paper_id="paper-1",
                source_mode="native",
                source_document_id="src_embedding_enqueue",
                include_embeddings=True,
                profile=profile,
            )
            duplicate = await search.enqueue_index(
                paper_id="paper-1",
                source_mode="native",
                source_document_id="src_embedding_enqueue",
                include_embeddings=True,
                profile=profile,
            )
            changed = await search.enqueue_index(
                paper_id="paper-1",
                source_mode="native",
                source_document_id="src_embedding_enqueue",
                include_embeddings=True,
                profile=changed_profile,
            )
            lexical = await search.enqueue_index(
                paper_id="paper-1",
                source_mode="native",
                source_document_id="src_embedding_enqueue",
                include_embeddings=False,
                profile=None,
            )

            self.assertFalse(first.deduplicated)
            self.assertTrue(duplicate.deduplicated)
            self.assertEqual(first.job.id, duplicate.job.id)
            self.assertNotEqual(first.job.id, changed.job.id)
            self.assertNotEqual(first.job.id, lexical.job.id)
            with closing(sqlite3.connect(fixture.database_path)) as connection:
                rows = connection.execute(
                    "SELECT id,idempotency_key,spec_json FROM processing_jobs "
                    "WHERE job_type='embed' ORDER BY id"
                ).fetchall()
            self.assertEqual(3, len(rows))
            decoded = {row[0]: decode_job_spec_v1(row[2]) for row in rows}
            frozen = decoded[first.job.id]
            self.assertIsInstance(frozen, EmbedJobSpecV1)
            self.assertTrue(frozen.include_embeddings)
            self.assertEqual(profile.provider, frozen.provider)
            self.assertEqual(profile.model, frozen.model)
            self.assertEqual(profile.embedding_version, frozen.embedding_version)
            self.assertEqual(profile.dimensions, frozen.dimensions)
            self.assertEqual(dict(profile.options), dict(frozen.options))
            self.assertEqual(
                build_index_job_key(
                    source_document_id="src_embedding_enqueue",
                    source_content_sha256=fixture.chunk_set.source_content_sha256,
                    chunking_version=fixture.chunk_set.spec.chunking_version,
                    embedding_provider=profile.provider,
                    embedding_model=profile.model,
                    embedding_version=profile.embedding_version,
                    include_embeddings=True,
                    embedding_options=dict(profile.options),
                ),
                next(row[1] for row in rows if row[0] == first.job.id),
            )
            disabled = decoded[lexical.job.id]
            self.assertFalse(disabled.include_embeddings)
            self.assertEqual(
                ("none", "none", "none", None, {}),
                (
                    disabled.provider,
                    disabled.model,
                    disabled.embedding_version,
                    disabled.dimensions,
                    dict(disabled.options),
                ),
            )

    async def test_index_enqueue_rechecks_paper_path_inside_atomic_write(self) -> None:
        from backend.app.application.document_search import DocumentSearch
        from backend.app.domain import PersistenceConflictError
        from backend.app.domain.context import ChunkingSpec
        from backend.app.repositories import document_search as search_repository_module
        from backend.app.repositories.document_search import (
            SqlAlchemyDocumentSearchRepository,
        )
        from backend.tests.support.p3_database import p3_context_fixture

        original_pdf = b"%PDF-1.7\noriginal index enqueue identity\n"
        replacement_pdf = b"%PDF-1.7\nreplacement index enqueue identity\n"
        source_id = "src_index_enqueue_path_fence"
        async with p3_context_fixture(
            prefix="study-app-p3-index-enqueue-path-fence-",
            source_id=source_id,
            markdown="# Index\n\nIndex enqueue path fence sentinel.\n",
            spec=ChunkingSpec(target_tokens=8, hard_cap_tokens=8),
            now=NOW,
            pdf_sha256=hashlib.sha256(original_pdf).hexdigest(),
            options_hash="6" * 64,
        ) as fixture:
            original_path = fixture.database_path.parent / "original-index.pdf"
            replacement_path = fixture.database_path.parent / "replacement-index.pdf"
            original_path.write_bytes(original_pdf)
            replacement_path.write_bytes(replacement_pdf)
            with closing(sqlite3.connect(fixture.database_path)) as connection:
                connection.execute(
                    "UPDATE papers SET pdf_path=? WHERE id='paper-1'",
                    (str(original_path),),
                )
                connection.commit()

            writer_started = threading.Event()
            writer_attempting_update = threading.Event()
            writer_finished = threading.Event()
            writer_task = None
            original_insert = (
                search_repository_module.SqlAlchemyProcessingJobRepository.insert_with_spec
            )

            def swap_path() -> None:
                with closing(sqlite3.connect(fixture.database_path, timeout=5)) as connection:
                    connection.execute("PRAGMA busy_timeout=5000")
                    writer_started.set()
                    writer_attempting_update.set()
                    connection.execute(
                        "UPDATE papers SET pdf_path=? WHERE id='paper-1'",
                        (str(replacement_path),),
                    )
                    connection.commit()
                writer_finished.set()

            async def swap_path_before_job_write(repository, *args, **kwargs):
                nonlocal writer_task
                writer_task = asyncio.create_task(asyncio.to_thread(swap_path))
                self.assertTrue(
                    await asyncio.to_thread(writer_started.wait, 1),
                    "competing writer did not begin",
                )
                self.assertTrue(
                    await asyncio.to_thread(writer_attempting_update.wait, 1),
                    "competing writer did not attempt its update",
                )
                result = await original_insert(repository, *args, **kwargs)
                self.assertFalse(
                    writer_finished.is_set(),
                    "path update interleaved with the identity-fenced job write",
                )
                return result

            search = DocumentSearch(
                SqlAlchemyDocumentSearchRepository(fixture.session_factory),
                context_builder=fixture.builder,
                clock=lambda: NOW,
                job_id_factory=lambda: "job_index_enqueue_path_fence",
            )
            with mock.patch.object(
                search_repository_module.SqlAlchemyProcessingJobRepository,
                "insert_with_spec",
                new=swap_path_before_job_write,
            ):
                enqueue = await search.enqueue_index(
                    paper_id="paper-1",
                    source_mode="native",
                    source_document_id=source_id,
                    include_embeddings=False,
                    profile=None,
                )
            self.assertIsNotNone(writer_task)
            assert writer_task is not None
            await writer_task
            self.assertTrue(writer_finished.is_set())
            with closing(sqlite3.connect(fixture.database_path)) as connection:
                self.assertEqual(
                    1,
                    connection.execute(
                        "SELECT count(*) FROM processing_jobs WHERE id=?",
                        ("job_index_enqueue_path_fence",),
                    ).fetchone()[0],
                )
                self.assertEqual(
                    str(replacement_path),
                    connection.execute(
                        "SELECT pdf_path FROM papers WHERE id='paper-1'"
                    ).fetchone()[0],
                )
            self.assertEqual("job_index_enqueue_path_fence", enqueue.job.id)

    async def test_embedding_providers_normalize_raw_json_loader_and_encoder_failures(
        self,
    ) -> None:
        from backend.app.domain import (
            Credential,
            EmbeddingRequestFailedError,
            EmbeddingResponseInvalidError,
        )
        from backend.app.domain.context import EmbeddingProfile, EmbeddingRequest
        from backend.app.providers.embeddings.model2vec import Model2VecEmbeddingProvider
        from backend.app.providers.embeddings.openai_compatible import (
            OpenAiCompatibleEmbeddingProvider,
        )

        remote_profile = EmbeddingProfile(
            provider="openai-compatible",
            model="raw-json-fixture",
            embedding_version="openai-compatible-v1",
            dimensions=2,
        )
        remote_request = EmbeddingRequest(
            profile=remote_profile,
            texts=("fixture input",),
            chunk_ids=("chunk-raw-json",),
        )

        class InvalidJsonResponse:
            status_code = 200
            headers: dict[str, str] = {}

            def json(self):
                raise RuntimeError("provider raw parse detail must not escape")

        class Transport:
            async def post(self, *_args, **_kwargs):
                return InvalidJsonResponse()

        remote_provider = OpenAiCompatibleEmbeddingProvider(
            remote_profile,
            Credential("embedding", "raw-json-secret"),
            transport=Transport(),
        )
        with self.assertRaises(EmbeddingResponseInvalidError) as remote_error:
            await remote_provider.embed(remote_request)
        self.assertEqual("EMBEDDING_RESPONSE_INVALID", remote_error.exception.code)
        self.assertNotIn("provider raw parse detail", str(remote_error.exception))

        local_profile = EmbeddingProfile(
            provider="model2vec",
            model="raw-local-fixture",
            embedding_version="model2vec-v1",
            dimensions=2,
        )
        local_request = EmbeddingRequest(
            profile=local_profile,
            texts=("fixture input",),
            chunk_ids=("chunk-raw-local",),
        )

        def failing_loader(_model: str):
            raise RuntimeError("loader raw detail must not escape")

        with self.assertRaises(EmbeddingRequestFailedError) as loader_error:
            await Model2VecEmbeddingProvider(
                local_profile,
                model_loader=failing_loader,
            ).embed(local_request)
        self.assertTrue(loader_error.exception.retryable)
        self.assertNotIn("loader raw detail", str(loader_error.exception))

        class FailingEncoder:
            def encode(self, _texts):
                raise RuntimeError("encoder raw detail must not escape")

        with self.assertRaises(EmbeddingRequestFailedError) as encoder_error:
            await Model2VecEmbeddingProvider(
                local_profile,
                model_loader=lambda _model: FailingEncoder(),
            ).embed(local_request)
        self.assertTrue(encoder_error.exception.retryable)
        self.assertNotIn("encoder raw detail", str(encoder_error.exception))

    async def test_openai_compatible_invalid_response_shapes_fail_closed_and_redacted(
        self,
    ) -> None:
        from backend.app.domain import Credential, EmbeddingResponseInvalidError
        from backend.app.domain.context import EmbeddingProfile, EmbeddingRequest
        from backend.app.providers.embeddings.openai_compatible import (
            OpenAiCompatibleEmbeddingProvider,
        )

        secret = "response-secret-must-not-escape"
        raw_marker = f"raw-provider-body-{secret}"
        profile = EmbeddingProfile(
            provider="openai-compatible",
            model="fixture-embedding-model",
            embedding_version="openai-compatible-v1",
            dimensions=2,
        )
        request = EmbeddingRequest(
            profile=profile,
            texts=("first", "second"),
            chunk_ids=("chunk-first", "chunk-second"),
        )
        invalid_payloads = {
            "non_object": [raw_marker],
            "unknown_field": {"data": [], "raw": raw_marker},
            "empty": {"data": []},
            "duplicate_index": {
                "data": [
                    {"index": 0, "embedding": [1.0, 0.0]},
                    {"index": 0, "embedding": [0.0, 1.0]},
                ]
            },
            "out_of_range": {
                "data": [
                    {"index": 0, "embedding": [1.0, 0.0]},
                    {"index": 2, "embedding": [0.0, 1.0]},
                ]
            },
            "mixed_dimension": {
                "data": [
                    {"index": 0, "embedding": [1.0, 0.0]},
                    {"index": 1, "embedding": [1.0]},
                ]
            },
            "zero": {
                "data": [
                    {"index": 0, "embedding": [1.0, 0.0]},
                    {"index": 1, "embedding": [0.0, 0.0]},
                ]
            },
            "nan": {
                "data": [
                    {"index": 0, "embedding": [1.0, 0.0]},
                    {"index": 1, "embedding": [float("nan"), 1.0]},
                ]
            },
            "infinity": {
                "data": [
                    {"index": 0, "embedding": [1.0, 0.0]},
                    {"index": 1, "embedding": [float("inf"), 1.0]},
                ]
            },
        }

        for name, payload in invalid_payloads.items():
            with self.subTest(name=name):
                class Response:
                    status_code = 200
                    headers: dict[str, str] = {}

                    def json(self):
                        return payload

                class Transport:
                    async def post(self, *_args, **_kwargs):
                        return Response()

                provider = OpenAiCompatibleEmbeddingProvider(
                    profile,
                    Credential("embedding", secret),
                    transport=Transport(),
                )
                with self.assertRaises(EmbeddingResponseInvalidError) as caught:
                    await provider.embed(request)
                error = caught.exception
                self.assertEqual("EMBEDDING_RESPONSE_INVALID", error.code)
                self.assertFalse(error.retryable)
                for rendered in (str(error), repr(error), error.public_message):
                    self.assertNotIn(secret, rendered)
                    self.assertNotIn(raw_marker, rendered)

    async def test_openai_compatible_redacts_and_types_transport_rate_server_and_auth_failures(
        self,
    ) -> None:
        import asyncio

        from backend.app.domain import Credential, EmbeddingRequestFailedError
        from backend.app.domain.context import EmbeddingProfile, EmbeddingRequest
        from backend.app.providers.embeddings.openai_compatible import (
            OpenAiCompatibleEmbeddingProvider,
        )

        secret = "p3-super-secret-embedding-key"
        raw_body = f'provider raw body includes {secret}'
        profile = EmbeddingProfile(
            provider="openai-compatible",
            model="fixture-embedding-model",
            embedding_version="openai-compatible-v1",
            dimensions=2,
        )
        request = EmbeddingRequest(
            profile=profile,
            texts=("text",),
            chunk_ids=("chunk",),
        )

        class Response:
            def __init__(self, status_code: int, headers=None) -> None:
                self.status_code = status_code
                self.headers = headers or {}

            def json(self):
                return {"error": raw_body}

        cases = (
            (asyncio.TimeoutError(raw_body), None, True, None),
            (None, Response(429, {"Retry-After": "120"}), True, 120),
            (None, Response(500), True, None),
            (None, Response(503), True, None),
            (None, Response(401), False, None),
            (None, Response(403), False, None),
        )
        for failure, response, retryable, retry_after in cases:
            with self.subTest(
                status=getattr(response, "status_code", "timeout"),
                retryable=retryable,
            ):
                class Transport:
                    async def post(self, *_args, **_kwargs):
                        if failure is not None:
                            raise failure
                        return response

                provider = OpenAiCompatibleEmbeddingProvider(
                    profile,
                    Credential("embedding", secret),
                    transport=Transport(),
                    clock=lambda: NOW,
                )
                with self.assertRaises(EmbeddingRequestFailedError) as caught:
                    await provider.embed(request)
                error = caught.exception
                self.assertEqual("EMBEDDING_REQUEST_FAILED", error.code)
                self.assertEqual(retryable, error.retryable)
                self.assertEqual(retry_after, error.retry_after_seconds)
                for rendered in (str(error), repr(error), error.public_message):
                    self.assertNotIn(secret, rendered)
                    self.assertNotIn("provider raw body", rendered)

    async def test_openai_compatible_posts_exact_embedding_batch_and_restores_index_order(
        self,
    ) -> None:
        from backend.app.domain import Credential
        from backend.app.domain.context import EmbeddingProfile, EmbeddingRequest
        from backend.app.providers.embeddings.openai_compatible import (
            OpenAiCompatibleEmbeddingProvider,
        )

        secret = "embedding-secret-must-never-escape"
        credential = Credential("embedding", secret)
        profile = EmbeddingProfile(
            provider="openai-compatible",
            model="fixture-embedding-model",
            embedding_version="openai-compatible-v1",
            dimensions=3,
        )

        class Response:
            status_code = 200
            headers: dict[str, str] = {}

            def json(self):
                return {
                    "data": [
                        {"index": 1, "embedding": [0.0, 1.0, 0.0]},
                        {"index": 0, "embedding": [1.0, 0.0, 0.0]},
                    ]
                }

        class Transport:
            def __init__(self) -> None:
                self.calls: list[dict[str, object]] = []

            async def post(self, path, *, headers, json, timeout):
                self.calls.append(
                    {
                        "path": path,
                        "headers": dict(headers),
                        "json": json,
                        "timeout": timeout,
                    }
                )
                return Response()

        transport = Transport()
        provider = OpenAiCompatibleEmbeddingProvider(
            profile,
            credential,
            transport=transport,
            timeout_seconds=17.5,
        )
        request = EmbeddingRequest(
            profile=profile,
            texts=("first text", "second text"),
            chunk_ids=("chunk-first", "chunk-second"),
        )

        result = await provider.embed(request)

        self.assertEqual(
            [
                {
                    "path": "/embeddings",
                    "headers": {
                        "Authorization": f"Bearer {secret}",
                        "Content-Type": "application/json",
                    },
                    "json": {
                        "model": profile.model,
                        "input": ["first text", "second text"],
                    },
                    "timeout": 17.5,
                }
            ],
            transport.calls,
        )
        self.assertEqual(profile, result.profile)
        self.assertEqual(request.chunk_ids, result.chunk_ids)
        self.assertEqual(
            ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0)),
            result.vectors,
        )
        self.assertNotIn(secret, repr(provider))
        self.assertNotIn(secret, repr(credential))

    async def test_model2vec_provider_loads_only_on_first_explicit_embed_and_reuses_model(
        self,
    ) -> None:
        from backend.app.domain.context import EmbeddingProfile, EmbeddingRequest
        from backend.app.providers.embeddings.model2vec import Model2VecEmbeddingProvider

        profile = EmbeddingProfile(
            provider="model2vec",
            model="fixture-static-model",
            embedding_version="model2vec-0.8.2",
            dimensions=3,
        )
        loads: list[str] = []

        class StaticModelDouble:
            def __init__(self) -> None:
                self.calls: list[tuple[str, ...]] = []

            def encode(self, texts):
                values = tuple(texts)
                self.calls.append(values)
                return [[1.0, 2.0, 3.0] for _ in values]

        model = StaticModelDouble()

        def loader(model_name: str):
            loads.append(model_name)
            return model

        provider = Model2VecEmbeddingProvider(profile, model_loader=loader)
        self.assertEqual([], loads)

        first = await provider.embed(
            EmbeddingRequest(
                profile=profile,
                texts=("alpha", "beta"),
                chunk_ids=("chunk-a", "chunk-b"),
            )
        )
        second = await provider.embed(
            EmbeddingRequest(
                profile=profile,
                texts=("gamma",),
                chunk_ids=("chunk-c",),
            )
        )

        self.assertEqual([profile.model], loads)
        self.assertEqual([("alpha", "beta"), ("gamma",)], model.calls)
        self.assertEqual(profile, first.profile)
        self.assertEqual(("chunk-a", "chunk-b"), first.chunk_ids)
        self.assertEqual(((1.0, 2.0, 3.0), (1.0, 2.0, 3.0)), first.vectors)
        self.assertEqual(("chunk-c",), second.chunk_ids)

    async def test_model2vec_invalid_model_output_fails_closed_with_frozen_response_code(
        self,
    ) -> None:
        from backend.app.domain import EmbeddingResponseInvalidError
        from backend.app.domain.context import EmbeddingProfile, EmbeddingRequest
        from backend.app.providers.embeddings.model2vec import Model2VecEmbeddingProvider

        profile = EmbeddingProfile(
            provider="model2vec",
            model="invalid-static-model",
            embedding_version="model2vec-0.8.2",
            dimensions=2,
        )
        request = EmbeddingRequest(
            profile=profile,
            texts=("invalid model output one", "invalid model output two"),
            chunk_ids=("chunk-invalid-one", "chunk-invalid-two"),
        )
        invalid_outputs = {
            "malformed": object(),
            "empty": [],
            "mixed_dimension": [[1.0, 0.0], [1.0]],
            "non_finite": [[float("nan"), 1.0], [1.0, 0.0]],
            "zero_vector": [[0.0, 0.0], [1.0, 0.0]],
        }

        for name, output in invalid_outputs.items():
            with self.subTest(name=name):
                class StaticModelDouble:
                    def encode(self, _texts):
                        return output

                provider = Model2VecEmbeddingProvider(
                    profile,
                    model_loader=lambda _model: StaticModelDouble(),
                )
                with self.assertRaises(EmbeddingResponseInvalidError) as raised:
                    await provider.embed(request)
                self.assertEqual("EMBEDDING_RESPONSE_INVALID", raised.exception.code)
                self.assertFalse(raised.exception.retryable)

    async def test_index_resume_skips_ready_identity_and_persists_normalized_float32_batches(
        self,
    ) -> None:
        from sqlalchemy import event

        from backend.app.application.document_search import DocumentSearch
        from backend.app.domain.context import (
            ChunkingSpec,
            EmbeddingBatch,
            EmbeddingProfile,
            EmbeddingRequest,
        )
        from backend.app.domain.processing import (
            EmbedJobSpecV1,
            JobResult,
            NewProcessingJob,
            build_index_job_key,
            encode_job_spec_v1,
            hash_job_spec,
        )
        from backend.app.repositories.document_search import (
            SqlAlchemyDocumentSearchRepository,
        )
        from backend.tests.support.p3_database import p3_context_fixture

        markdown = (
            "[page 1]\n# Embedding resume\n\n"
            "Alpha embedding chunk.\n"
            "Beta embedding chunk.\n"
            "Tail embedding sentinel.\n"
        )
        profile = EmbeddingProfile(
            provider="fake-embedding",
            model="fake-model-v1",
            embedding_version="fixture-v1",
            dimensions=3,
        )
        async with p3_context_fixture(
            prefix="study-app-p3-embedding-resume-",
            source_id="src_embedding_resume",
            markdown=markdown,
            spec=ChunkingSpec(target_tokens=4, hard_cap_tokens=4),
            now=NOW,
            pdf_sha256="3" * 64,
            options_hash="4" * 64,
        ) as fixture:
            spec = EmbedJobSpecV1(
                paper_id="paper-1",
                source_document_id="src_embedding_resume",
            )
            raw_spec = encode_job_spec_v1(spec)
            job = NewProcessingJob(
                id="job_embedding_resume",
                spec=spec,
                idempotency_key=build_index_job_key(
                    source_document_id=spec.source_document_id,
                    source_content_sha256=fixture.chunk_set.source_content_sha256,
                    chunking_version=fixture.chunk_set.spec.chunking_version,
                    embedding_provider=profile.provider,
                    embedding_model=profile.model,
                    embedding_version=profile.embedding_version,
                    include_embeddings=True,
                    embedding_options=dict(profile.options),
                ),
                created_at=NOW,
                max_attempts=3,
            )
            async with fixture.unit_of_work_factory() as work:
                await work.jobs.insert_with_spec(
                    job,
                    spec_json=raw_spec,
                    spec_sha256=hash_job_spec(raw_spec),
                )
                await work.commit()
            async with fixture.unit_of_work_factory() as work:
                lease = await work.jobs.claim_next(
                    worker_id="embedding-worker",
                    now=NOW,
                    lease_seconds=3600,
                )
                await work.commit()
            self.assertIsNotNone(lease)
            assert lease is not None

            first_chunk = fixture.chunk_set.chunks[0]
            existing_vector = struct.pack("<3f", 1.0, 0.0, 0.0)
            with closing(sqlite3.connect(fixture.database_path)) as connection:
                connection.execute(
                    "INSERT INTO document_chunk_embeddings("
                    "id,chunk_id,source_document_id,provider,model,embedding_version,"
                    "dimensions,vector,vector_sha256,chunk_content_sha256,status,"
                    "error_code,error_message,created_at,updated_at,stale_at) VALUES("
                    "?,?,?,?,?,?,?,?,?,?,'ready',NULL,NULL,?,?,NULL)",
                    (
                        "embedding_existing",
                        first_chunk.id,
                        first_chunk.source_document_id,
                        profile.provider,
                        profile.model,
                        profile.embedding_version,
                        profile.dimensions,
                        existing_vector,
                        hashlib.sha256(existing_vector).hexdigest(),
                        first_chunk.content_sha256,
                        NOW.isoformat().replace("+00:00", "Z"),
                        NOW.isoformat().replace("+00:00", "Z"),
                    ),
                )
                connection.commit()

            active_transactions: set[int] = set()
            engine = fixture.session_factory.kw["bind"]

            def transaction_started(connection) -> None:
                active_transactions.add(id(connection))

            def transaction_finished(connection) -> None:
                active_transactions.discard(id(connection))

            event.listen(engine.sync_engine, "begin", transaction_started)
            event.listen(engine.sync_engine, "commit", transaction_finished)
            event.listen(engine.sync_engine, "rollback", transaction_finished)

            class RecordingProvider:
                provider_id = profile.provider

                def __init__(self) -> None:
                    self.calls: list[EmbeddingRequest] = []

                async def embed(self, request: EmbeddingRequest) -> EmbeddingBatch:
                    if active_transactions:
                        raise AssertionError(
                            "embedding provider was called during a database transaction"
                        )
                    self.calls.append(request)
                    return EmbeddingBatch(
                        profile=request.profile,
                        chunk_ids=request.chunk_ids,
                        vectors=tuple((3.0, 4.0, 0.0) for _ in request.chunk_ids),
                    )

            provider = RecordingProvider()
            search = DocumentSearch(
                SqlAlchemyDocumentSearchRepository(fixture.session_factory),
                context_builder=fixture.builder,
                embedding_provider=provider,
                clock=lambda: NOW,
                embedding_batch_size=2,
            )
            try:
                result = await search.index(
                    lease,
                    fixture.chunk_set.source_document_id,
                    profile,
                )
            finally:
                event.remove(engine.sync_engine, "begin", transaction_started)
                event.remove(engine.sync_engine, "commit", transaction_finished)
                event.remove(engine.sync_engine, "rollback", transaction_finished)

            requested_ids = tuple(
                chunk_id for call in provider.calls for chunk_id in call.chunk_ids
            )
            self.assertEqual(fixture.chunk_set.chunk_ids[1:], requested_ids)
            self.assertTrue(all(len(call.chunk_ids) <= 2 for call in provider.calls))
            self.assertEqual(len(fixture.chunk_set.chunks), result.total_chunks)
            self.assertEqual(len(fixture.chunk_set.chunks), result.embedded_chunks)
            self.assertEqual(1, result.reused_chunks)
            self.assertEqual(0, result.failed_embeddings)

            with closing(sqlite3.connect(fixture.database_path)) as connection:
                rows = connection.execute(
                    "SELECT e.id,e.chunk_id,e.dimensions,e.vector,e.vector_sha256,"
                    "e.chunk_content_sha256,e.status FROM document_chunk_embeddings e "
                    "JOIN document_chunks c ON c.id=e.chunk_id "
                    "WHERE e.source_document_id=? AND e.provider=? AND e.model=? "
                    "AND e.embedding_version=? ORDER BY c.sequence",
                    (
                        fixture.chunk_set.source_document_id,
                        profile.provider,
                        profile.model,
                        profile.embedding_version,
                    ),
                ).fetchall()
            self.assertEqual(len(fixture.chunk_set.chunks), len(rows))
            self.assertEqual(("embedding_existing", existing_vector), (rows[0][0], rows[0][3]))
            for row, chunk in zip(rows, fixture.chunk_set.chunks, strict=True):
                vector = struct.unpack("<3f", row[3])
                self.assertTrue(math.isclose(1.0, math.sqrt(sum(v * v for v in vector)), rel_tol=1e-6))
                self.assertEqual(hashlib.sha256(row[3]).hexdigest(), row[4])
                self.assertEqual(chunk.content_sha256, row[5])
                self.assertEqual((3, "ready"), (row[2], row[6]))

            async with fixture.unit_of_work_factory() as work:
                await work.jobs.complete(
                    lease,
                    JobResult(result.to_job_result()),
                    now=NOW,
                )
                await work.commit()
            with closing(sqlite3.connect(fixture.database_path)) as connection:
                status, result_json = connection.execute(
                    "SELECT status,result_json FROM processing_jobs WHERE id=?",
                    (job.id,),
                ).fetchone()
            self.assertEqual("succeeded", status)
            self.assertIn('"embeddedChunks"', result_json)


if __name__ == "__main__":
    unittest.main()
