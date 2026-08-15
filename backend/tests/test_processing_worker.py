from __future__ import annotations

import asyncio
import hashlib
import io
import json
import os
import tempfile
import unittest
from unittest import mock
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path

if (
    os.name == "nt"
    and hasattr(os, "add_dll_directory")
    and os.environ.get("P3_SQLITE_DLL_DIR")
):
    _SQLITE_DLL_HANDLE = os.add_dll_directory(os.environ["P3_SQLITE_DLL_DIR"])

import sqlite3

from backend.app.config import DatabaseSettings
from backend.app.domain import (
    JobLeaseLostError,
    JobNotCancellableError,
    JobNotRetryableError,
    OcrRateLimitedError,
    OcrResponseInvalidError,
    OcrServerError,
    OcrTimeoutError,
    SourceDocument,
)
from backend.app.domain.processing import (
    JobProgress,
    JobResult,
    JobFailure,
    NewProcessingJob,
    ObsidianSyncJobSpecV1,
    OcrJobSpecV1,
    SourceMaterializeJobSpecV1,
    build_source_job_key,
    build_source_key,
    encode_job_spec_v1,
    hash_job_spec,
)
from backend.app.infrastructure.database import create_async_session_factory
from backend.app.repositories.unit_of_work import SqlAlchemyUnitOfWork
from backend.tests.support.p1_database import create_legacy_database, run_alembic
from backend.tests.support.p2_database import p2_database_fixture


class ProcessingWorkerLoopTests(unittest.IsolatedAsyncioTestCase):
    async def test_p3_worker_settles_oversized_atomic_chunk_as_typed_failure(self) -> None:
        from backend.app.bootstrap import bootstrap_processing_worker
        from backend.app.domain.context import EmbeddingProfile
        from backend.app.repositories.unit_of_work import SqlAlchemyUnitOfWork
        from backend.tests.support.p3_database import p3_database_fixture

        now = datetime(2026, 8, 13, 12, 45, tzinfo=timezone.utc)
        markdown = "```\n" + ("token " * 8193) + "\n```"
        pdf_bytes = b"p3 oversized atomic worker pdf"
        pdf_sha = hashlib.sha256(pdf_bytes).hexdigest()
        async with p3_database_fixture(
            prefix="study-app-worker-p3-oversized-atomic-",
        ) as fixture:
            pdf_path = fixture.database_path.parent / "oversized-atomic.pdf"
            pdf_path.write_bytes(pdf_bytes)
            factory = lambda: SqlAlchemyUnitOfWork(fixture.session_factory)
            async with factory() as work:
                await work.sources.add(
                    SourceDocument(
                        id="src_worker_oversized_atomic",
                        paper_id="paper-1",
                        mode="native",
                        status="ready",
                        provider="local",
                        model="pymupdf4llm-pymupdf",
                        pdf_sha256=pdf_sha,
                        options_hash="b" * 64,
                        processing_version="native-v1",
                        created_at=now,
                        updated_at=now,
                        markdown=markdown,
                        content_sha256=hashlib.sha256(markdown.encode("utf-8")).hexdigest(),
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

            class TranslationProvider:
                provider_id = "oversized-atomic-translation"
                model_id = "oversized-atomic-model"
                prompt_version = "oversized-atomic-v1"

                async def translate(self, _request):
                    raise AssertionError("oversized atomic materialization must fail first")

            class StructuredProvider:
                provider_id = "oversized-atomic-structured"
                model_id = "oversized-atomic-structured-model"

                async def generate(self, _request):
                    raise AssertionError("translation must not call structured provider")

            class EmbeddingProvider:
                provider_id = "oversized-atomic-embedding"

                async def embed(self, _request):
                    raise AssertionError("translation must not call embedding provider")

            container = bootstrap_processing_worker(
                DatabaseSettings(fixture.database_path),
                required_schema_revision="20260807_03",
                worker_id="worker-p3-oversized-atomic",
                clock=lambda: now,
                translation_provider_factory=TranslationProvider,
                structured_provider_factory=StructuredProvider,
                embedding_profile=EmbeddingProfile(
                    provider="oversized-atomic-embedding",
                    model="oversized-atomic-embedding-model",
                    embedding_version="oversized-atomic-embedding-v1",
                    dimensions=2,
                ),
                embedding_provider_factory=lambda _profile, _credential: EmbeddingProvider(),
            )
            try:
                assert container.document_artifacts is not None
                enqueued = await container.document_artifacts.enqueue(
                    "paper-1",
                    "src_worker_oversized_atomic",
                    "native",
                    "translation",
                    now=now,
                )
                self.assertTrue(await container.processing_worker.run_once())
            finally:
                await container.dispose()

            with closing(sqlite3.connect(fixture.database_path)) as connection:
                job = connection.execute(
                    "SELECT status,error_code,finished_at FROM processing_jobs WHERE id=?",
                    (enqueued.job.id,),
                ).fetchone()
                chunks = connection.execute(
                    "SELECT COUNT(*) FROM document_chunks WHERE source_document_id=?",
                    ("src_worker_oversized_atomic",),
                ).fetchone()[0]
            self.assertEqual(("failed", "CHUNK_ATOMIC_BLOCK_TOO_LARGE"), job[:2])
            self.assertIsNotNone(job[2])
            self.assertEqual(0, chunks)

    async def test_p3_translation_worker_claims_and_atomically_settles_without_double_complete(self) -> None:
        """The final-stage bootstrap must let the P3 artifact service settle its own CAS."""
        from backend.app.bootstrap import bootstrap_processing_worker
        from backend.app.application.ports.translation_provider import TranslationRequest
        from backend.tests.support.p3_database import p3_context_fixture
        from backend.app.domain.context import ChunkingSpec

        now = datetime(2026, 8, 13, 13, 0, tzinfo=timezone.utc)
        pdf_bytes = b"p3 worker translation pdf"
        pdf_sha = hashlib.sha256(pdf_bytes).hexdigest()
        markdown = (
            "# Abstract\n\nworker translation head.\n\n"
            "# Methods\n\nworker translation body.\n\n"
            "# Conclusion\n\nworker translation tail.\n"
        )
        async with p3_context_fixture(
            prefix="study-app-worker-p3-translation-",
            source_id="src_worker_p3_translation",
            markdown=markdown,
            spec=ChunkingSpec(target_tokens=4, hard_cap_tokens=4),
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

            class TranslationProvider:
                provider_id = "worker-translation"
                model_id = "worker-translation-model"
                prompt_version = "worker-translation-v1"

                def __init__(self) -> None:
                    self.calls: list[TranslationRequest] = []

                async def translate(self, request: TranslationRequest) -> str:
                    with closing(sqlite3.connect(fixture.database_path)) as connection:
                        self.assert_no_transaction(connection)
                    self.calls.append(request)
                    return f"<translated-{request.sequence}>{request.markdown}</translated-{request.sequence}>"

                @staticmethod
                def assert_no_transaction(connection: sqlite3.Connection) -> None:
                    if connection.in_transaction:
                        raise AssertionError("provider called with an active database transaction")

            class StructuredProvider:
                provider_id = "worker-structured"
                model_id = "worker-structured-model"

                async def generate(self, _request):
                    raise AssertionError("translation worker must not call the structured provider")

            class EmbeddingProvider:
                provider_id = "worker-embedding"

                async def embed(self, _request):
                    raise AssertionError("translation worker must not call the embedding provider")

            class CredentialStore:
                async def get(self, _kind):
                    raise AssertionError("translation worker must not request embedding credentials")

            provider = TranslationProvider()
            container = bootstrap_processing_worker(
                DatabaseSettings(fixture.database_path),
                required_schema_revision="20260807_03",
                worker_id="worker-p3-translation",
                clock=lambda: now,
                translation_provider_factory=lambda: provider,
                structured_provider_factory=StructuredProvider,
                embedding_profile=__import__(
                    "backend.app.domain.context", fromlist=["EmbeddingProfile"]
                ).EmbeddingProfile(
                    provider="worker-embedding",
                    model="worker-embedding-model",
                    embedding_version="worker-embedding-v1",
                    dimensions=2,
                ),
                embedding_provider_factory=lambda _profile, _credential: EmbeddingProvider(),
                credential_store=CredentialStore(),
            )
            assert container.document_artifacts is not None
            enqueued = await container.document_artifacts.enqueue(
                "paper-1",
                "src_worker_p3_translation",
                "native",
                "translation",
                now=now,
            )

            from backend.app.repositories.processing_jobs import (
                SqlAlchemyProcessingJobRepository,
            )

            generic_complete_calls: list[str] = []
            original_complete = SqlAlchemyProcessingJobRepository.complete

            async def tracked_complete(repository, lease, result, *, now, **kwargs):
                generic_complete_calls.append(lease.job.id)
                return await original_complete(
                    repository,
                    lease,
                    result,
                    now=now,
                    **kwargs,
                )

            try:
                with mock.patch.object(
                    SqlAlchemyProcessingJobRepository,
                    "complete",
                    tracked_complete,
                ):
                    self.assertTrue(await container.processing_worker.run_once())
            finally:
                await container.dispose()

            expected_provider_calls = [
                chunk for chunk in fixture.chunk_set.chunks if chunk.content_kind != "verbatim"
            ]
            self.assertEqual(len(expected_provider_calls), len(provider.calls))
            self.assertEqual(
                list(range(len(expected_provider_calls))),
                [request.sequence for request in provider.calls],
            )
            self.assertEqual([], generic_complete_calls)
            with closing(sqlite3.connect(fixture.database_path)) as connection:
                checkpoints_rows = connection.execute(
                    "SELECT sequence,status FROM artifact_translation_checkpoints "
                    "WHERE artifact_id=? ORDER BY sequence",
                    (enqueued.artifact.id,),
                ).fetchall()
                artifact_row = connection.execute(
                    "SELECT status,content FROM generated_artifacts WHERE id=?",
                    (enqueued.artifact.id,),
                ).fetchone()
                job_row = connection.execute(
                    "SELECT status,result_json FROM processing_jobs WHERE id=?",
                    (enqueued.job.id,),
                ).fetchone()
                terminal_events = connection.execute(
                    "SELECT event_type FROM processing_job_events WHERE job_id=? "
                    "AND event_type IN ('succeeded','failed','cancelled')",
                    (enqueued.job.id,),
                ).fetchall()
            self.assertEqual(
                [(index, "succeeded") for index in range(len(fixture.chunk_set.chunks))],
                checkpoints_rows,
            )
            self.assertEqual("succeeded", job_row[0])
            self.assertEqual("ready", artifact_row[0])
            self.assertEqual(
                "".join(
                    (
                        chunk.content
                        if chunk.content_kind == "verbatim"
                        else f"<translated-{chunk.sequence}>{chunk.content}</translated-{chunk.sequence}>"
                    )
                    for chunk in fixture.chunk_set.chunks
                ),
                artifact_row[1],
            )
            self.assertEqual([("succeeded",)], terminal_events)

    async def test_p3_artifact_worker_materializes_missing_chunks_before_read_only_service_run(
        self,
    ) -> None:
        from backend.app.application.ports.translation_provider import TranslationRequest
        from backend.app.bootstrap import bootstrap_processing_worker
        from backend.app.domain.context import ChunkingSpec, EmbeddingProfile
        from backend.tests.support.p3_database import p3_context_fixture

        now = datetime(2026, 8, 13, 13, 30, tzinfo=timezone.utc)
        markdown = (
            "# Worker materialization\n\n"
            "Worker chunk materialization body.\n\n"
            "WORKER_CHUNK_MATERIALIZATION_TAIL_SENTINEL\n"
        )
        pdf_bytes = b"p3 worker artifact materialization pdf"
        async with p3_context_fixture(
            prefix="study-app-worker-p3-missing-chunks-",
            source_id="src_worker_missing_chunks",
            markdown=markdown,
            spec=ChunkingSpec(),
            now=now,
            pdf_sha256=hashlib.sha256(pdf_bytes).hexdigest(),
            options_hash="d" * 64,
        ) as fixture:
            pdf_path = fixture.database_path.parent / "worker-missing-chunks.pdf"
            pdf_path.write_bytes(pdf_bytes)
            with closing(sqlite3.connect(fixture.database_path)) as connection:
                connection.execute(
                    "UPDATE papers SET pdf_path=? WHERE id='paper-1'",
                    (str(pdf_path),),
                )
                connection.execute(
                    "DELETE FROM document_chunks WHERE source_document_id=?",
                    (fixture.chunk_set.source_document_id,),
                )
                connection.commit()

            class TranslationProvider:
                provider_id = "worker-materialization-translation"
                model_id = "worker-materialization-model"
                prompt_version = "worker-materialization-v1"

                def __init__(self) -> None:
                    self.calls: list[TranslationRequest] = []

                async def translate(self, request: TranslationRequest) -> str:
                    self.calls.append(request)
                    return request.markdown

            class StructuredProvider:
                provider_id = "worker-materialization-structured"
                model_id = "worker-materialization-structured-model"

                async def generate(self, _request):
                    raise AssertionError("translation must not call structured provider")

            class EmbeddingProvider:
                provider_id = "worker-materialization-embedding"

                async def embed(self, _request):
                    raise AssertionError("translation must not call embedding provider")

            provider = TranslationProvider()
            container = bootstrap_processing_worker(
                DatabaseSettings(fixture.database_path),
                required_schema_revision="20260807_03",
                worker_id="worker-p3-missing-chunks",
                clock=lambda: now,
                translation_provider_factory=lambda: provider,
                structured_provider_factory=StructuredProvider,
                embedding_profile=EmbeddingProfile(
                    provider="worker-materialization-embedding",
                    model="worker-materialization-embedding-model",
                    embedding_version="worker-materialization-embedding-v1",
                    dimensions=2,
                ),
                embedding_provider_factory=lambda _profile, _credential: EmbeddingProvider(),
            )
            try:
                assert container.document_artifacts is not None
                enqueued = await container.document_artifacts.enqueue(
                    "paper-1",
                    fixture.chunk_set.source_document_id,
                    "native",
                    "translation",
                    now=now,
                )
                self.assertTrue(await container.processing_worker.run_once())
            finally:
                await container.dispose()

            self.assertTrue(provider.calls)
            with closing(sqlite3.connect(fixture.database_path)) as connection:
                chunks = connection.execute(
                    "SELECT sequence,content FROM document_chunks "
                    "WHERE source_document_id=? AND status='ready' ORDER BY sequence",
                    (fixture.chunk_set.source_document_id,),
                ).fetchall()
                artifact = connection.execute(
                    "SELECT status,content FROM generated_artifacts WHERE id=?",
                    (enqueued.artifact.id,),
                ).fetchone()
            self.assertEqual(markdown, "".join(row[1] for row in chunks))
            self.assertIn("WORKER_CHUNK_MATERIALIZATION_TAIL_SENTINEL", chunks[-1][1])
            self.assertEqual(("ready", markdown), artifact)

    async def test_run_once_returns_idle_after_one_short_claim_transaction(self) -> None:
        from backend.app.workers.processing_worker import ProcessingWorker

        now = datetime(2026, 8, 10, 8, 0, tzinfo=timezone.utc)
        async with p2_database_fixture(prefix="study-app-worker-idle-") as database:
            worker = ProcessingWorker(
                lambda: SqlAlchemyUnitOfWork(database.session_factory),
                handlers={},
                worker_id="worker-idle",
                clock=lambda: now,
            )

            self.assertFalse(await worker.run_once())

    async def test_run_once_dispatches_canonical_type_and_cas_completes_generic_result(self) -> None:
        from backend.app.workers.processing_worker import ProcessingWorker

        now = datetime(2026, 8, 10, 8, 5, tzinfo=timezone.utc)
        observed = []

        async def handle(lease):
            observed.append((lease.job.id, lease.spec.value, lease.spec.raw_json))
            return JobResult({"stage": "done"})

        async with p2_database_fixture(prefix="study-app-worker-dispatch-") as database:
            job = await self._enqueue_source_job(
                database.session_factory,
                now=now,
                job_id="job-worker-dispatch",
                source_id="source-worker-dispatch",
            )
            worker = ProcessingWorker(
                lambda: SqlAlchemyUnitOfWork(database.session_factory),
                handlers={"source_materialize": handle},
                worker_id="worker-dispatch",
                clock=lambda: now,
            )

            self.assertTrue(await worker.run_once())

            self.assertEqual(
                [(job.id, job.spec, encode_job_spec_v1(job.spec))],
                observed,
            )
            with closing(sqlite3.connect(database.database_path)) as connection:
                row = connection.execute(
                    "SELECT status,result_json,lease_owner,lease_token FROM processing_jobs WHERE id=?",
                    (job.id,),
                ).fetchone()
            self.assertEqual(("succeeded", '{"stage":"done"}', None, None), row)

    async def test_self_settling_handler_outcome_prevents_double_complete(self) -> None:
        from backend.app.workers.processing_worker import (
            ProcessingHandlerOutcome,
            ProcessingWorker,
        )
        now = datetime(2026, 8, 10, 8, 10, tzinfo=timezone.utc)
        async with p2_database_fixture(prefix="study-app-worker-settled-") as database:
            job = await self._enqueue_source_job(
                database.session_factory,
                now=now,
                job_id="job-worker-settled",
                source_id="source-worker-settled",
            )

            async def handle(lease):
                async with SqlAlchemyUnitOfWork(database.session_factory) as work:
                    await work.jobs.complete(
                        lease,
                        JobResult({"published": True}),
                        now=now,
                    )
                    await work.commit()
                return ProcessingHandlerOutcome.settled()

            worker = ProcessingWorker(
                lambda: SqlAlchemyUnitOfWork(database.session_factory),
                handlers={"source_materialize": handle},
                worker_id="worker-settled",
                clock=lambda: now,
            )

            self.assertTrue(await worker.run_once())

            with closing(sqlite3.connect(database.database_path)) as connection:
                job_status = connection.execute(
                    "SELECT status FROM processing_jobs WHERE id=?", (job.id,),
                ).fetchone()[0]
                terminal_events = connection.execute(
                    "SELECT event_type FROM processing_job_events WHERE job_id=? "
                    "AND event_type IN ('succeeded','failed','cancelled')",
                    (job.id,),
                ).fetchall()
            self.assertEqual("succeeded", job_status)
            self.assertEqual([("succeeded",)], terminal_events)

    async def test_native_source_handler_commits_ready_source_and_succeeded_job(self) -> None:
        from backend.app.application.ports import ExtractedSource
        from backend.app.application.source_documents import (
            SourceDocumentProcessor,
            build_native_source_processor,
        )
        from backend.app.workers.processing_worker import (
            ProcessingHandlerOutcome,
            ProcessingWorker,
        )
        from threading import Event, get_ident

        started_at = datetime(2026, 8, 10, 8, 12, tzinfo=timezone.utc)
        current = [started_at]
        pdf_bytes = b"native worker fixture"
        extract_started = asyncio.Event()
        release_extract = Event()
        waiter_stopped = asyncio.Event()
        heartbeat_snapshot = []
        waiter_calls = 0
        event_loop_thread = get_ident()
        event_loop = asyncio.get_running_loop()

        class NativeExtractor:
            def extract(self, _path: Path) -> ExtractedSource:
                if get_ident() == event_loop_thread:
                    raise AssertionError("native extraction must run off the event loop")
                event_loop.call_soon_threadsafe(extract_started.set)
                release_extract.wait()
                return ExtractedSource(
                    markdown="# Committed native source\n",
                    content_sha256="0" * 64,
                    page_count=1,
                    provider="local",
                    model="pymupdf",
                    processing_version="native-v1",
                )

        async with p2_database_fixture(prefix="study-app-worker-native-commit-") as database:
            pdf_path = database.database_path.parent / "paper.pdf"
            pdf_path.write_bytes(pdf_bytes)
            with closing(sqlite3.connect(database.database_path)) as connection:
                connection.execute(
                    "UPDATE papers SET pdf_path=? WHERE id='paper-1'",
                    (str(pdf_path),),
                )
                connection.commit()

            source = SourceDocument(
                id="source-worker-native-commit",
                paper_id="paper-1",
                mode="native",
                status="queued",
                provider="local",
                model="pymupdf",
                pdf_sha256=hashlib.sha256(pdf_bytes).hexdigest(),
                options_hash="c" * 64,
                processing_version="native-v1",
                created_at=started_at,
                updated_at=started_at,
            )
            spec = SourceMaterializeJobSpecV1(
                paper_id="paper-1",
                source_document_id=source.id,
                processing_version="native-v1",
            )
            raw = encode_job_spec_v1(spec)
            job = NewProcessingJob(
                id="job-worker-native-commit",
                spec=spec,
                idempotency_key=build_source_job_key(
                    build_source_key(
                        paper_id=source.paper_id,
                        mode=source.mode,
                        provider=source.provider,
                        model=source.model,
                        pdf_sha256=source.pdf_sha256,
                        options_hash=source.options_hash,
                        processing_version=source.processing_version,
                    ),
                    hash_job_spec(raw),
                ),
                created_at=started_at,
            )
            async with SqlAlchemyUnitOfWork(database.session_factory) as work:
                await work.sources.add(source)
                await work.jobs.insert_with_spec(
                    job,
                    spec_json=raw,
                    spec_sha256=hash_job_spec(raw),
                )
                await work.commit()

            source_processor = SourceDocumentProcessor(
                lambda: SqlAlchemyUnitOfWork(database.session_factory),
                native_factory=lambda: build_native_source_processor(
                    NativeExtractor(),
                    clock=lambda: current[0],
                ),
                ocr_factory=lambda: (_ for _ in ()).throw(AssertionError("OCR")),
                clock=lambda: current[0],
            )

            async def handle(lease):
                await source_processor.process(lease, spec.source_document_id)
                return ProcessingHandlerOutcome.settled()

            async def heartbeat_waiter(_interval_seconds: float) -> None:
                nonlocal waiter_calls
                waiter_calls += 1
                if waiter_calls == 1:
                    await extract_started.wait()
                    current[0] = started_at + timedelta(seconds=10)
                    return
                try:
                    with closing(sqlite3.connect(database.database_path)) as connection:
                        heartbeat_snapshot.append(
                            connection.execute(
                                "SELECT heartbeat_at,lease_expires_at FROM processing_jobs WHERE id=?",
                                (job.id,),
                            ).fetchone()
                        )
                    release_extract.set()
                    await asyncio.Event().wait()
                finally:
                    waiter_stopped.set()

            worker = ProcessingWorker(
                lambda: SqlAlchemyUnitOfWork(database.session_factory),
                handlers={"source_materialize": handle},
                worker_id="worker-native-commit",
                clock=lambda: current[0],
                lease_seconds=30,
                heartbeat_interval_seconds=10,
                heartbeat_waiter=heartbeat_waiter,
            )

            self.assertTrue(await worker.run_once())

            self.assertTrue(waiter_stopped.is_set())
            self.assertEqual(2, waiter_calls)
            self.assertEqual(
                [
                    (
                        (started_at + timedelta(seconds=10)).isoformat().replace("+00:00", "Z"),
                        (started_at + timedelta(seconds=40)).isoformat().replace("+00:00", "Z"),
                    )
                ],
                heartbeat_snapshot,
            )
            with closing(sqlite3.connect(database.database_path)) as connection:
                source_row = connection.execute(
                    "SELECT status,markdown FROM document_sources WHERE id=?",
                    (source.id,),
                ).fetchone()
                job_row = connection.execute(
                    "SELECT status,result_json,heartbeat_at,lease_expires_at FROM processing_jobs WHERE id=?",
                    (job.id,),
                ).fetchone()
            self.assertEqual(("ready", "# Committed native source\n"), source_row)
            self.assertEqual(
                ("succeeded", '{"sourceDocumentId":"source-worker-native-commit"}', None, None),
                job_row,
            )

    async def test_ocr_source_handler_renews_while_provider_waits_then_publishes(self) -> None:
        from backend.app.application.ports.ocr_provider import OcrPageResult, OcrResult
        from backend.app.application.source_documents import (
            SourceDocumentProcessor,
            build_ocr_source_processor,
        )
        from backend.app.domain.processing import hash_canonical_json
        from backend.app.repositories.ocr_checkpoints import SqlAlchemyOcrCheckpointRepository
        from backend.app.workers.processing_worker import (
            ProcessingHandlerOutcome,
            ProcessingWorker,
        )
        from sqlalchemy import text

        started_at = datetime(2026, 8, 10, 8, 12, 30, tzinfo=timezone.utc)
        current = [started_at]
        pdf_bytes = b"ocr worker fixture"
        provider_started = asyncio.Event()
        release_provider = asyncio.Event()
        waiter_stopped = asyncio.Event()
        heartbeat_snapshot = []
        waiter_calls = 0
        native_calls = []

        class PageReader:
            def page_count(self, _pdf_bytes: bytes) -> int:
                return 1

        class FakeProvider:
            provider_id = "fake"

            def __init__(self) -> None:
                self.calls = []

            async def extract_batch(self, request) -> OcrResult:
                self.calls.append(request)
                provider_started.set()
                await release_provider.wait()
                markdown = "# Committed OCR source\n"
                return OcrResult(
                    pages=(
                        OcrPageResult(
                            page_number=1,
                            markdown=markdown,
                            content_sha256=hashlib.sha256(markdown.encode("utf-8")).hexdigest(),
                            provider_page_id="page-1",
                        ),
                    ),
                    provider=self.provider_id,
                    model=request.model,
                    processing_version="fake-ocr-v1",
                    provider_request_id="request-1",
                )

        class Registry:
            def __init__(self, provider: FakeProvider) -> None:
                self.provider = provider

            def resolve(self, provider_id: str) -> FakeProvider:
                self.assert_provider(provider_id)
                return self.provider

            @staticmethod
            def assert_provider(provider_id: str) -> None:
                if provider_id != "fake":
                    raise AssertionError("unexpected OCR provider")

        class SnapshotUnitOfWork(SqlAlchemyUnitOfWork):
            async def __aenter__(self):
                work = await super().__aenter__()
                await self._require_session().execute(text("BEGIN"))
                return work

        async with p2_database_fixture(prefix="study-app-worker-ocr-heartbeat-") as database:
            pdf_path = database.database_path.parent / "paper.pdf"
            pdf_path.write_bytes(pdf_bytes)
            with closing(sqlite3.connect(database.database_path)) as connection:
                connection.execute(
                    "UPDATE papers SET pdf_path=? WHERE id='paper-1'",
                    (str(pdf_path),),
                )
                connection.commit()

            options_hash = hash_canonical_json({"pageBatchSize": 1, "maxConcurrency": 1})
            source = SourceDocument(
                id="source-worker-ocr-heartbeat",
                paper_id="paper-1",
                mode="ocr",
                status="queued",
                provider="fake",
                model="fake-ocr-v1",
                pdf_sha256=hashlib.sha256(pdf_bytes).hexdigest(),
                options_hash=options_hash,
                processing_version="fake-ocr-v1",
                created_at=started_at,
                updated_at=started_at,
            )
            spec = OcrJobSpecV1(
                paper_id=source.paper_id,
                source_document_id=source.id,
                provider=source.provider,
                model=source.model,
            )
            raw = encode_job_spec_v1(spec)
            job = NewProcessingJob(
                id="job-worker-ocr-heartbeat",
                spec=spec,
                idempotency_key=build_source_job_key(
                    build_source_key(
                        paper_id=source.paper_id,
                        mode=source.mode,
                        provider=source.provider,
                        model=source.model,
                        pdf_sha256=source.pdf_sha256,
                        options_hash=source.options_hash,
                        processing_version=source.processing_version,
                    ),
                    hash_job_spec(raw),
                ),
                created_at=started_at,
            )
            async with SqlAlchemyUnitOfWork(database.session_factory) as work:
                await work.sources.add(source)
                await work.jobs.insert_with_spec(job, spec_json=raw, spec_sha256=hash_job_spec(raw))
                await work.commit()

            provider = FakeProvider()
            checkpoints = SqlAlchemyOcrCheckpointRepository(
                database.session_factory,
                clock=lambda: current[0],
            )

            def native_factory():
                native_calls.append(True)
                raise AssertionError("native processor must not be selected")

            source_processor = SourceDocumentProcessor(
                lambda: SnapshotUnitOfWork(database.session_factory),
                native_factory=native_factory,
                ocr_factory=lambda: build_ocr_source_processor(
                    Registry(provider),
                    page_reader=PageReader(),
                    checkpoint_repository=checkpoints,
                    clock=lambda: current[0],
                ),
                clock=lambda: current[0],
            )

            async def handle(lease):
                await source_processor.process(lease, spec.source_document_id)
                return ProcessingHandlerOutcome.settled()

            async def heartbeat_waiter(_interval_seconds: float) -> None:
                nonlocal waiter_calls
                waiter_calls += 1
                if waiter_calls == 1:
                    await provider_started.wait()
                    current[0] = started_at + timedelta(seconds=10)
                    return
                try:
                    with closing(sqlite3.connect(database.database_path)) as connection:
                        heartbeat_snapshot.append(
                            connection.execute(
                                "SELECT heartbeat_at,lease_expires_at FROM processing_jobs WHERE id=?",
                                (job.id,),
                            ).fetchone()
                        )
                    release_provider.set()
                    await asyncio.Event().wait()
                finally:
                    waiter_stopped.set()

            worker = ProcessingWorker(
                lambda: SqlAlchemyUnitOfWork(database.session_factory),
                handlers={"ocr": handle},
                worker_id="worker-ocr-heartbeat",
                clock=lambda: current[0],
                lease_seconds=30,
                heartbeat_interval_seconds=10,
                heartbeat_waiter=heartbeat_waiter,
            )

            self.assertTrue(await worker.run_once())

            self.assertTrue(waiter_stopped.is_set())
            self.assertEqual(2, waiter_calls)
            self.assertEqual(
                [
                    (
                        (started_at + timedelta(seconds=10)).isoformat().replace("+00:00", "Z"),
                        (started_at + timedelta(seconds=40)).isoformat().replace("+00:00", "Z"),
                    )
                ],
                heartbeat_snapshot,
            )
            self.assertEqual([[1]], [list(request.page_numbers) for request in provider.calls])
            self.assertEqual([], native_calls)
            with closing(sqlite3.connect(database.database_path)) as connection:
                source_row = connection.execute(
                    "SELECT status,markdown FROM document_sources WHERE id=?",
                    (source.id,),
                ).fetchone()
                checkpoint_rows = connection.execute(
                    "SELECT page_number,status,markdown FROM ocr_page_checkpoints "
                    "WHERE source_document_id=? ORDER BY page_number",
                    (source.id,),
                ).fetchall()
                job_row = connection.execute(
                    "SELECT status,result_json,heartbeat_at,lease_expires_at FROM processing_jobs WHERE id=?",
                    (job.id,),
                ).fetchone()
            self.assertEqual(("ready", "# Committed OCR source\n"), source_row)
            self.assertEqual([(1, "succeeded", "# Committed OCR source\n")], checkpoint_rows)
            self.assertEqual(
                ("succeeded", '{"sourceDocumentId":"source-worker-ocr-heartbeat"}', None, None),
                job_row,
            )

    async def test_expired_lease_cannot_progress_complete_or_fail_before_replacement_claim(self) -> None:
        started_at = datetime(2026, 8, 10, 8, 13, tzinfo=timezone.utc)
        operations = (
            (
                "progress",
                lambda jobs, lease: jobs.report_progress(
                    lease,
                    JobProgress({"phase": "stale"}),
                    now=started_at + timedelta(seconds=31),
                ),
            ),
            (
                "complete",
                lambda jobs, lease: jobs.complete(
                    lease,
                    JobResult({"outcome": "stale"}),
                    now=started_at + timedelta(seconds=31),
                ),
            ),
            (
                "fail",
                lambda jobs, lease: jobs.fail(
                    lease,
                    JobFailure(code="STALE", retryable=False),
                    now=started_at + timedelta(seconds=31),
                ),
            ),
        )

        async with p2_database_fixture(prefix="study-app-worker-expired-fence-") as database:
            for index, (name, operation) in enumerate(operations):
                with self.subTest(operation=name):
                    job = await self._enqueue_source_job(
                        database.session_factory,
                        now=started_at + timedelta(microseconds=index),
                        job_id=f"job-expired-{name}",
                        source_id=f"source-expired-{name}",
                    )
                    async with SqlAlchemyUnitOfWork(database.session_factory) as work:
                        lease = await work.jobs.claim_next(
                            worker_id=f"worker-expired-{name}",
                            now=started_at + timedelta(microseconds=index),
                            lease_seconds=30,
                        )
                        await work.commit()
                    assert lease is not None

                    with closing(sqlite3.connect(database.database_path)) as connection:
                        before = (
                            connection.execute(
                                "SELECT * FROM processing_jobs WHERE id=?",
                                (job.id,),
                            ).fetchone(),
                            connection.execute(
                                "SELECT * FROM document_sources WHERE id=?",
                                (f"source-expired-{name}",),
                            ).fetchone(),
                            connection.execute(
                                "SELECT * FROM processing_job_events WHERE job_id=? ORDER BY sequence",
                                (job.id,),
                            ).fetchall(),
                        )

                    with self.assertRaises(JobLeaseLostError) as caught:
                        async with SqlAlchemyUnitOfWork(database.session_factory) as work:
                            await operation(work.jobs, lease)
                    self.assertEqual("JOB_LEASE_LOST", caught.exception.code)

                    with closing(sqlite3.connect(database.database_path)) as connection:
                        after = (
                            connection.execute(
                                "SELECT * FROM processing_jobs WHERE id=?",
                                (job.id,),
                            ).fetchone(),
                            connection.execute(
                                "SELECT * FROM document_sources WHERE id=?",
                                (f"source-expired-{name}",),
                            ).fetchone(),
                            connection.execute(
                                "SELECT * FROM processing_job_events WHERE job_id=? ORDER BY sequence",
                                (job.id,),
                            ).fetchall(),
                        )
                    self.assertEqual(before, after)

    async def test_handler_that_outlives_lease_is_fenced_at_worker_settlement(self) -> None:
        from backend.app.workers.processing_worker import ProcessingWorker

        started_at = datetime(2026, 8, 10, 8, 14, tzinfo=timezone.utc)
        current = [started_at]
        async with p2_database_fixture(prefix="study-app-worker-long-handler-") as database:
            job = await self._enqueue_source_job(
                database.session_factory,
                now=started_at,
                job_id="job-worker-long-handler",
                source_id="source-worker-long-handler",
            )

            async def handle(_lease):
                current[0] = started_at + timedelta(seconds=31)
                return JobResult({"outcome": "too-late"})

            worker = ProcessingWorker(
                lambda: SqlAlchemyUnitOfWork(database.session_factory),
                handlers={"source_materialize": handle},
                worker_id="worker-long-handler",
                clock=lambda: current[0],
                lease_seconds=30,
            )

            self.assertTrue(await worker.run_once())

            with closing(sqlite3.connect(database.database_path)) as connection:
                row = connection.execute(
                    "SELECT status,result_json,lease_owner FROM processing_jobs WHERE id=?",
                    (job.id,),
                ).fetchone()
                source_status = connection.execute(
                    "SELECT status FROM document_sources WHERE id='source-worker-long-handler'",
                ).fetchone()[0]
                events = connection.execute(
                    "SELECT event_type FROM processing_job_events WHERE job_id=? ORDER BY sequence",
                    (job.id,),
                ).fetchall()
            self.assertEqual(("running", None, "worker-long-handler"), row)
            self.assertEqual("running", source_status)
            self.assertEqual([("enqueued",), ("claimed",)], events)

    async def test_expired_handler_failure_cannot_schedule_retry_or_escape_worker(self) -> None:
        from backend.app.workers.processing_worker import ProcessingWorker

        started_at = datetime(2026, 8, 10, 8, 15, tzinfo=timezone.utc)
        current = [started_at]
        async with p2_database_fixture(prefix="study-app-worker-expired-failure-") as database:
            job = await self._enqueue_source_job(
                database.session_factory,
                now=started_at,
                job_id="job-worker-expired-failure",
                source_id="source-worker-expired-failure",
            )

            async def handle(_lease):
                current[0] = started_at + timedelta(seconds=31)
                raise OcrTimeoutError()

            worker = ProcessingWorker(
                lambda: SqlAlchemyUnitOfWork(database.session_factory),
                handlers={"source_materialize": handle},
                worker_id="worker-expired-failure",
                clock=lambda: current[0],
                lease_seconds=30,
            )

            self.assertTrue(await worker.run_once())

            with closing(sqlite3.connect(database.database_path)) as connection:
                row = connection.execute(
                    "SELECT status,error_code,lease_owner FROM processing_jobs WHERE id=?",
                    (job.id,),
                ).fetchone()
                events = connection.execute(
                    "SELECT event_type FROM processing_job_events WHERE job_id=? ORDER BY sequence",
                    (job.id,),
                ).fetchall()
            self.assertEqual(("running", None, "worker-expired-failure"), row)
            self.assertEqual([("enqueued",), ("claimed",)], events)

    async def test_heartbeat_renews_long_handler_lease_and_stops_with_handler(self) -> None:
        from backend.app.workers.processing_worker import ProcessingWorker

        started_at = datetime(2026, 8, 10, 8, 16, tzinfo=timezone.utc)
        current = [started_at]
        first_renewed = asyncio.Event()
        hold_second_tick = asyncio.Event()
        waiter_stopped = asyncio.Event()
        waiter_calls = 0

        async def heartbeat_waiter(_interval_seconds: float) -> None:
            nonlocal waiter_calls
            waiter_calls += 1
            if waiter_calls == 1:
                current[0] = started_at + timedelta(seconds=20)
                return
            current[0] = started_at + timedelta(seconds=40)
            first_renewed.set()
            try:
                await hold_second_tick.wait()
            finally:
                waiter_stopped.set()

        async with p2_database_fixture(prefix="study-app-worker-heartbeat-") as database:
            job = await self._enqueue_source_job(
                database.session_factory,
                now=started_at,
                job_id="job-worker-heartbeat",
                source_id="source-worker-heartbeat",
            )

            async def handle(_lease):
                await first_renewed.wait()
                return JobResult({"outcome": "renewed"})

            worker = ProcessingWorker(
                lambda: SqlAlchemyUnitOfWork(database.session_factory),
                handlers={"source_materialize": handle},
                worker_id="worker-heartbeat",
                clock=lambda: current[0],
                lease_seconds=30,
                heartbeat_interval_seconds=10,
                heartbeat_waiter=heartbeat_waiter,
            )

            self.assertTrue(await worker.run_once())

            self.assertTrue(waiter_stopped.is_set())
            self.assertEqual(2, waiter_calls)
            with closing(sqlite3.connect(database.database_path)) as connection:
                row = connection.execute(
                    "SELECT status,result_json,lease_expires_at "
                    "FROM processing_jobs WHERE id=?",
                    (job.id,),
                ).fetchone()
            self.assertEqual("succeeded", row[0])
            self.assertEqual('{"outcome":"renewed"}', row[1])
            self.assertIsNone(row[2])

    async def test_heartbeat_cancel_race_checkpoints_cancelled_and_stops_handler(self) -> None:
        from backend.app.workers.processing_worker import ProcessingWorker

        started_at = datetime(2026, 8, 10, 8, 17, tzinfo=timezone.utc)
        current = [started_at]
        handler_stopped = asyncio.Event()

        async with p2_database_fixture(prefix="study-app-worker-heartbeat-cancel-") as database:
            job = await self._enqueue_source_job(
                database.session_factory,
                now=started_at,
                job_id="job-worker-heartbeat-cancel",
                source_id="source-worker-heartbeat-cancel",
            )

            async def heartbeat_waiter(_interval_seconds: float) -> None:
                current[0] = started_at + timedelta(seconds=1)
                async with SqlAlchemyUnitOfWork(database.session_factory) as work:
                    await work.jobs.cancel(job.id, now=current[0])
                    await work.commit()

            async def handle(_lease):
                try:
                    await asyncio.Event().wait()
                finally:
                    handler_stopped.set()

            worker = ProcessingWorker(
                lambda: SqlAlchemyUnitOfWork(database.session_factory),
                handlers={"source_materialize": handle},
                worker_id="worker-heartbeat-cancel",
                clock=lambda: current[0],
                lease_seconds=30,
                heartbeat_interval_seconds=10,
                heartbeat_waiter=heartbeat_waiter,
            )

            self.assertTrue(await worker.run_once())

            self.assertTrue(handler_stopped.is_set())
            with closing(sqlite3.connect(database.database_path)) as connection:
                row = connection.execute(
                    "SELECT status,cancelled_at,lease_owner,lease_token FROM processing_jobs WHERE id=?",
                    (job.id,),
                ).fetchone()
                source_status = connection.execute(
                    "SELECT status FROM document_sources WHERE id='source-worker-heartbeat-cancel'",
                ).fetchone()[0]
                events = connection.execute(
                    "SELECT event_type FROM processing_job_events WHERE job_id=? ORDER BY sequence",
                    (job.id,),
                ).fetchall()
            self.assertEqual("cancelled", row[0])
            self.assertIsNotNone(row[1])
            self.assertEqual((None, None), row[2:])
            self.assertEqual("cancelled", source_status)
            self.assertEqual(
                [("enqueued",), ("claimed",), ("cancel_requested",), ("cancelled",)],
                events,
            )

    async def test_application_add_rejects_new_legacy_imported_envelope_without_row(self) -> None:
        from backend.app.domain import ProcessingJob
        from backend.app.domain.processing import JobSpecValidationError

        now = datetime(2026, 8, 10, 8, 18, tzinfo=timezone.utc)
        legacy_job = ProcessingJob(
            id="job-application-legacy",
            job_type="obsidian_sync",
            status="queued",
            idempotency_key="application-legacy-key",
            created_at=now,
        )
        async with p2_database_fixture(prefix="study-app-worker-legacy-add-") as database:
            with self.assertRaises(JobSpecValidationError) as caught:
                async with SqlAlchemyUnitOfWork(database.session_factory) as work:
                    await work.jobs.add(legacy_job)
                    await work.commit()
            self.assertEqual("JOB_SPEC_INVALID", caught.exception.code)

            with closing(sqlite3.connect(database.database_path)) as connection:
                job_count = connection.execute(
                    "SELECT count(*) FROM processing_jobs WHERE id=?",
                    (legacy_job.id,),
                ).fetchone()[0]
                event_count = connection.execute(
                    "SELECT count(*) FROM processing_job_events WHERE job_id=?",
                    (legacy_job.id,),
                ).fetchone()[0]
            self.assertEqual((0, 0), (job_count, event_count))

    async def test_migration_legacy_job_fails_unrecoverable_without_dispatch_or_retry(self) -> None:
        from backend.app.workers.processing_worker import ProcessingWorker

        now = datetime(2026, 8, 10, 8, 19, tzinfo=timezone.utc)

        def prepare_legacy(database_path: Path) -> None:
            with closing(sqlite3.connect(database_path)) as connection:
                connection.execute(
                    "INSERT INTO processing_jobs("
                    "id,paper_id,job_type,source_mode,status,progress_json,attempt,max_attempts,"
                    "idempotency_key,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (
                        "job-migration-legacy",
                        "paper-1",
                        "source_materialize",
                        "native",
                        "queued",
                        "{}",
                        0,
                        3,
                        "migration-legacy-key",
                        now.isoformat().replace("+00:00", "Z"),
                    ),
                )
                connection.commit()

        dispatches = []
        async with p2_database_fixture(
            prefix="study-app-worker-legacy-migration-",
            prepare_legacy=prepare_legacy,
        ) as database:
            async def forbidden_dispatch(lease):
                dispatches.append(lease.job.id)
                return JobResult({"outcome": "must-not-run"})

            worker = ProcessingWorker(
                lambda: SqlAlchemyUnitOfWork(database.session_factory),
                handlers={"source_materialize": forbidden_dispatch},
                worker_id="worker-migration-legacy",
                clock=lambda: now,
            )

            self.assertTrue(await worker.run_once())

            self.assertEqual([], dispatches)
            with closing(sqlite3.connect(database.database_path)) as connection:
                row = connection.execute(
                    "SELECT status,error_code,lease_owner,lease_token FROM processing_jobs WHERE id=?",
                    ("job-migration-legacy",),
                ).fetchone()
                events = connection.execute(
                    "SELECT event_type,error_code FROM processing_job_events "
                    "WHERE job_id=? ORDER BY sequence",
                    ("job-migration-legacy",),
                ).fetchall()
            self.assertEqual(("failed", "JOB_SPEC_UNRECOVERABLE", None, None), row)
            self.assertEqual(
                [("claimed", None), ("failed", "JOB_SPEC_UNRECOVERABLE")],
                events,
            )

            with self.assertRaises(JobNotRetryableError) as caught:
                async with SqlAlchemyUnitOfWork(database.session_factory) as work:
                    await work.jobs.retry("job-migration-legacy", now=now + timedelta(seconds=1))
            self.assertEqual("JOB_NOT_RETRYABLE", caught.exception.code)

    async def test_migration_legacy_terminal_row_cannot_be_explicitly_retried(self) -> None:
        now = datetime(2026, 8, 10, 8, 20, tzinfo=timezone.utc)

        def prepare_legacy(database_path: Path) -> None:
            with closing(sqlite3.connect(database_path)) as connection:
                connection.execute(
                    "INSERT INTO processing_jobs("
                    "id,paper_id,job_type,source_mode,status,progress_json,attempt,max_attempts,"
                    "idempotency_key,error_code,created_at,finished_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        "job-migration-legacy-failed",
                        "paper-1",
                        "translate",
                        "native",
                        "failed",
                        "{}",
                        1,
                        3,
                        "migration-legacy-failed-key",
                        "LEGACY_FAILURE",
                        now.isoformat().replace("+00:00", "Z"),
                        now.isoformat().replace("+00:00", "Z"),
                    ),
                )
                connection.commit()

        async with p2_database_fixture(
            prefix="study-app-worker-legacy-retry-",
            prepare_legacy=prepare_legacy,
        ) as database:
            with self.assertRaises(JobNotRetryableError) as caught:
                async with SqlAlchemyUnitOfWork(database.session_factory) as work:
                    await work.jobs.retry(
                        "job-migration-legacy-failed",
                        now=now + timedelta(seconds=1),
                    )
            self.assertEqual("JOB_NOT_RETRYABLE", caught.exception.code)

            with closing(sqlite3.connect(database.database_path)) as connection:
                rows = connection.execute(
                    "SELECT id,status,error_code FROM processing_jobs ORDER BY id",
                ).fetchall()
                events = connection.execute(
                    "SELECT count(*) FROM processing_job_events",
                ).fetchone()[0]
            self.assertEqual(
                [("job-migration-legacy-failed", "failed", "LEGACY_FAILURE")],
                rows,
            )
            self.assertEqual(0, events)

    async def test_reserved_canonical_type_without_handler_fails_closed(self) -> None:
        from backend.app.workers.processing_worker import ProcessingWorker

        now = datetime(2026, 8, 10, 8, 15, tzinfo=timezone.utc)
        async with p2_database_fixture(prefix="study-app-worker-reserved-") as database:
            spec = ObsidianSyncJobSpecV1()
            raw = encode_job_spec_v1(spec)
            job = NewProcessingJob(
                id="job-worker-reserved",
                spec=spec,
                idempotency_key="worker-reserved-key",
                created_at=now,
            )
            async with SqlAlchemyUnitOfWork(database.session_factory) as work:
                await work.jobs.insert_with_spec(
                    job,
                    spec_json=raw,
                    spec_sha256=hash_job_spec(raw),
                )
                await work.commit()
            worker = ProcessingWorker(
                lambda: SqlAlchemyUnitOfWork(database.session_factory),
                handlers={},
                worker_id="worker-reserved",
                clock=lambda: now,
            )

            self.assertTrue(await worker.run_once())

            with closing(sqlite3.connect(database.database_path)) as connection:
                row = connection.execute(
                    "SELECT status,error_code,finished_at,lease_owner,lease_token "
                    "FROM processing_jobs WHERE id=?",
                    (job.id,),
                ).fetchone()
            self.assertEqual("failed", row[0])
            self.assertEqual("JOB_TYPE_UNSUPPORTED", row[1])
            self.assertIsNotNone(row[2])
            self.assertIsNone(row[3])
            self.assertIsNone(row[4])

    async def test_typed_ocr_rate_limit_uses_bounded_retry_after(self) -> None:
        from backend.app.workers.processing_worker import ProcessingWorker

        now = datetime(2026, 8, 10, 8, 20, tzinfo=timezone.utc)
        cases = (
            ("valid", 120, 120),
            ("missing", None, 5),
            ("invalid", "not-seconds", 5),
            ("overlong", 1_000, 900),
        )
        for suffix, retry_after, expected_delay in cases:
            with self.subTest(case=suffix):
                async with p2_database_fixture(
                    prefix=f"study-app-worker-rate-{suffix}-"
                ) as database:
                    job = await self._enqueue_source_job(
                        database.session_factory,
                        now=now,
                        job_id=f"job-worker-rate-{suffix}",
                        source_id=f"source-worker-rate-{suffix}",
                        max_attempts=3,
                        source_mode="ocr",
                    )

                    async def handle(_lease, value=retry_after):
                        raise OcrRateLimitedError(retry_after_seconds=value)

                    worker = ProcessingWorker(
                        lambda: SqlAlchemyUnitOfWork(database.session_factory),
                        handlers={"ocr": handle},
                        worker_id=f"worker-rate-{suffix}",
                        clock=lambda: now,
                    )

                    self.assertTrue(await worker.run_once())

                    with closing(sqlite3.connect(database.database_path)) as connection:
                        row = connection.execute(
                            "SELECT status,error_code,available_at,attempt,lease_owner,lease_token "
                            "FROM processing_jobs WHERE id=?",
                            (job.id,),
                        ).fetchone()
                        source_status = connection.execute(
                            "SELECT status FROM document_sources WHERE id=?",
                            (job.spec.source_document_id,),
                        ).fetchone()[0]
                    self.assertEqual("queued", row[0])
                    self.assertEqual("OCR_RATE_LIMITED", row[1])
                    self.assertEqual(now + timedelta(seconds=expected_delay), datetime.fromisoformat(row[2].replace("Z", "+00:00")))
                    self.assertEqual(1, row[3])
                    self.assertIsNone(row[4])
                    self.assertIsNone(row[5])
                    self.assertEqual("queued", source_status)

    async def test_stale_handler_lease_cannot_settle_after_crash_recovery(self) -> None:
        from backend.app.workers.processing_worker import ProcessingWorker

        started_at = datetime(2026, 8, 10, 8, 25, tzinfo=timezone.utc)
        current = [started_at]
        async with p2_database_fixture(prefix="study-app-worker-recovery-") as database:
            job = await self._enqueue_source_job(
                database.session_factory,
                now=started_at,
                job_id="job-worker-recovery",
                source_id="source-worker-recovery",
            )

            async def lose_lease(_lease):
                current[0] = started_at + timedelta(seconds=31)
                async with SqlAlchemyUnitOfWork(database.session_factory) as work:
                    replacement = await work.jobs.claim_next(
                        worker_id="worker-replacement",
                        now=current[0],
                        lease_seconds=30,
                    )
                    await work.commit()
                self.assertIsNotNone(replacement)
                raise JobLeaseLostError(operation="worker_checkpoint")

            worker = ProcessingWorker(
                lambda: SqlAlchemyUnitOfWork(database.session_factory),
                handlers={"source_materialize": lose_lease},
                worker_id="worker-crashed",
                clock=lambda: current[0],
                lease_seconds=30,
            )

            self.assertTrue(await worker.run_once())

            with closing(sqlite3.connect(database.database_path)) as connection:
                row = connection.execute(
                    "SELECT status,lease_owner,error_code FROM processing_jobs WHERE id=?",
                    (job.id,),
                ).fetchone()
                events = connection.execute(
                    "SELECT event_type FROM processing_job_events WHERE job_id=? ORDER BY sequence",
                    (job.id,),
                ).fetchall()
            self.assertEqual(("running", "worker-replacement", None), row)
            self.assertEqual(
                [("enqueued",), ("claimed",), ("lease_recovered",), ("claimed",)],
                events,
            )

    async def test_forever_stop_during_handler_settles_current_job_and_stops_new_claims(self) -> None:
        from backend.app.workers.processing_worker import ProcessingWorker

        now = datetime(2026, 8, 10, 8, 30, tzinfo=timezone.utc)
        stop_event = asyncio.Event()
        calls = []
        iteration_hooks = []

        async def before_iteration():
            iteration_hooks.append("flush_due")

        async def handle(lease):
            calls.append(lease.job.id)
            stop_event.set()
            return JobResult({"stage": "settled-before-stop"})

        async with p2_database_fixture(prefix="study-app-worker-stop-") as database:
            first = await self._enqueue_source_job(
                database.session_factory,
                now=now,
                job_id="job-worker-stop-a",
                source_id="source-worker-stop-a",
            )
            second = await self._enqueue_source_job(
                database.session_factory,
                now=now,
                job_id="job-worker-stop-b",
                source_id="source-worker-stop-b",
            )
            worker = ProcessingWorker(
                lambda: SqlAlchemyUnitOfWork(database.session_factory),
                handlers={"source_materialize": handle},
                worker_id="worker-stop",
                clock=lambda: now,
            )

            await worker.run_forever(
                stop_event=stop_event,
                iteration_hook=before_iteration,
            )

            self.assertEqual([first.id], calls)
            self.assertEqual(["flush_due"], iteration_hooks)
            with closing(sqlite3.connect(database.database_path)) as connection:
                rows = connection.execute(
                    "SELECT id,status FROM processing_jobs ORDER BY id"
                ).fetchall()
            self.assertEqual(
                [(first.id, "succeeded"), (second.id, "queued")],
                rows,
            )

    async def test_typed_timeout_server_and_invalid_response_policy_logs_safe_fields(self) -> None:
        from backend.app.workers.processing_worker import ProcessingWorker

        now = datetime(2026, 8, 10, 8, 35, tzinfo=timezone.utc)
        cases = (
            ("timeout", OcrTimeoutError(), "queued", now + timedelta(seconds=5), "retry_scheduled"),
            ("server", OcrServerError(), "queued", now + timedelta(seconds=5), "retry_scheduled"),
            (
                "invalid",
                OcrResponseInvalidError(raw_response="provider-secret-body"),
                "failed",
                now,
                "failed",
            ),
        )
        for suffix, failure, expected_status, expected_available_at, expected_stage in cases:
            with self.subTest(case=suffix):
                logs = []
                async with p2_database_fixture(
                    prefix=f"study-app-worker-policy-{suffix}-"
                ) as database:
                    job = await self._enqueue_source_job(
                        database.session_factory,
                        now=now,
                        job_id=f"job-worker-policy-{suffix}",
                        source_id=f"source-worker-policy-{suffix}",
                        max_attempts=3,
                        source_mode="ocr",
                    )

                    async def handle(_lease, error=failure):
                        raise error

                    worker = ProcessingWorker(
                        lambda: SqlAlchemyUnitOfWork(database.session_factory),
                        handlers={"ocr": handle},
                        worker_id=f"worker-policy-{suffix}",
                        clock=lambda: now,
                        logger=logs.append,
                    )

                    self.assertTrue(await worker.run_once())

                    with closing(sqlite3.connect(database.database_path)) as connection:
                        row = connection.execute(
                            "SELECT status,error_code,available_at,finished_at FROM processing_jobs WHERE id=?",
                            (job.id,),
                        ).fetchone()
                    self.assertEqual(expected_status, row[0])
                    self.assertEqual(failure.code, row[1])
                    self.assertEqual(
                        expected_available_at,
                        datetime.fromisoformat(row[2].replace("Z", "+00:00")),
                    )
                    self.assertEqual(expected_status == "failed", row[3] is not None)
                    self.assertEqual(
                        [{
                            "jobId": job.id,
                            "stage": expected_stage,
                            "attempt": 1,
                            "code": failure.code,
                        }],
                        logs,
                    )
                    self.assertNotIn("provider-secret-body", repr(logs))

    async def test_cli_rejects_missing_multiple_and_future_schema_revisions_before_worker(self) -> None:
        from backend.app.cli import processing_worker as worker_cli
        from backend.tests.support.p3_database import p3_database_fixture

        cases = ("missing", "multiple", "future")
        for case in cases:
            with self.subTest(case=case):
                async with p3_database_fixture(
                    prefix=f"study-app-worker-cli-{case}-"
                ) as database:
                    with closing(sqlite3.connect(database.database_path)) as connection:
                        if case == "missing":
                            connection.execute("DELETE FROM alembic_version")
                        elif case == "multiple":
                            connection.execute(
                                "INSERT INTO alembic_version(version_num) VALUES ('20260807_02')"
                            )
                        else:
                            connection.execute(
                                "UPDATE alembic_version SET version_num='20260807_04'"
                            )
                        connection.commit()
                    stderr = io.StringIO()
                    worker_calls = []

                    def forbidden_worker(_settings):
                        worker_calls.append("called")
                        raise AssertionError("worker must not be composed")

                    exit_code = await worker_cli.run(
                        ["--once"],
                        environment={"DB_PATH": str(database.database_path)},
                        stderr=stderr,
                        worker_factory=forbidden_worker,
                    )

                    self.assertNotEqual(0, exit_code)
                    payload = json.loads(stderr.getvalue())
                    self.assertEqual("SCHEMA_REVISION_MISMATCH", payload["error"]["code"])
                    self.assertEqual("20260807_03", payload["error"]["details"]["expected_revision"])
                    self.assertEqual([], worker_calls)

    async def test_cli_runs_p3_head_and_composes_the_p3_worker(self) -> None:
        from backend.app.cli import processing_worker as worker_cli
        from backend.tests.support.p3_database import p3_database_fixture

        calls = []

        class FakeWorker:
            async def run_once(self):
                calls.append("run_once")
                return False

        class FakeContainer:
            processing_worker = FakeWorker()

            async def dispose(self):
                calls.append("dispose")

        def compose(settings, **kwargs):
            calls.append((settings.database_path, kwargs["required_schema_revision"]))
            return FakeContainer()

        async with p3_database_fixture(prefix="study-app-worker-cli-p3-") as database:
            with mock.patch.object(worker_cli, "bootstrap_processing_worker", compose):
                exit_code = await worker_cli.run(
                    ["--once"],
                    environment={"DB_PATH": str(database.database_path)},
                )

        self.assertEqual(0, exit_code)
        self.assertEqual(
            [
                (database.database_path, "20260807_03"),
                "run_once",
                "dispose",
            ],
            calls,
        )

    async def test_cli_once_runs_once_and_forever_uses_injected_stop_lifecycle(self) -> None:
        from backend.app.cli import processing_worker as worker_cli
        from backend.tests.support.p3_database import p3_database_fixture

        calls = []
        cleanup_calls = []
        waiter = object()

        class FakeWorker:
            async def run_once(self):
                calls.append("once")
                return False

            async def run_forever(self, *, stop_event, waiter):
                calls.append(("forever", stop_event.is_set(), waiter))

        def compose(_settings):
            calls.append("compose")
            return FakeWorker()

        def register_stop(stop_event):
            calls.append("signals")
            stop_event.set()
            return lambda: cleanup_calls.append("cleaned")

        async with p3_database_fixture(prefix="study-app-worker-cli-run-") as database:
            environment = {"DB_PATH": str(database.database_path)}
            once_code = await worker_cli.run(
                ["--once"],
                environment=environment,
                worker_factory=compose,
            )
            forever_code = await worker_cli.run(
                ["--forever"],
                environment=environment,
                worker_factory=compose,
                waiter=waiter,
                signal_registrar=register_stop,
            )

        self.assertEqual(0, once_code)
        self.assertEqual(0, forever_code)
        self.assertEqual(
            [
                "compose",
                "once",
                "compose",
                "signals",
                ("forever", True, waiter),
            ],
            calls,
        )
        self.assertEqual(["cleaned"], cleanup_calls)

    async def test_bootstrap_composes_p2_worker_without_autostart_and_keeps_revision_gate_parameterized(self) -> None:
        from backend.app.bootstrap import bootstrap_processing_worker, verify_schema_revision
        from backend.app.domain import SchemaRevisionMismatchError

        async with p2_database_fixture(prefix="study-app-worker-bootstrap-") as database:
            settings = DatabaseSettings(database.database_path)
            verify_schema_revision(settings, "20260807_02")
            with self.assertRaises(SchemaRevisionMismatchError):
                verify_schema_revision(settings, "20260807_01")

            container = bootstrap_processing_worker(
                settings,
                required_schema_revision="20260807_02",
                worker_id="worker-bootstrap",
            )
            try:
                self.assertEqual("20260807_02", container.schema_revision)
                self.assertEqual(
                    frozenset({"source_materialize", "ocr", "explain"}),
                    container.processing_worker.handler_types,
                )
                self.assertFalse(await container.processing_worker.run_once())
            finally:
                await container.dispose()

    async def test_bootstrap_composes_p3_worker_only_at_final_revision_with_explicit_provider_seams(self) -> None:
        from backend.app.bootstrap import bootstrap_processing_worker
        from backend.app.config import DatabaseSettings
        from backend.app.domain import SchemaRevisionMismatchError
        from backend.app.domain.context import EmbeddingProfile
        from backend.tests.support.p3_database import p3_database_fixture

        class TranslationFixtureProvider:
            provider_id = "fixture-translation"
            model_id = "fixture-translation-model"
            prompt_version = "fixture-translation-v1"

        class StructuredFixtureProvider:
            provider_id = "fixture-structured"
            model_id = "fixture-structured-model"

        class EmbeddingFixtureProvider:
            provider_id = "fixture-embedding"

        profile = EmbeddingProfile(
            provider="fixture-embedding",
            model="fixture-embedding-model",
            embedding_version="fixture-embedding-v1",
            dimensions=2,
        )
        async with p3_database_fixture(prefix="study-app-worker-p3-bootstrap-") as database:
            settings = DatabaseSettings(database.database_path)
            with self.assertRaises(SchemaRevisionMismatchError):
                bootstrap_processing_worker(
                    settings,
                    required_schema_revision="20260807_02",
                    worker_id="worker-p3-wrong-revision",
                )

            container = bootstrap_processing_worker(
                settings,
                required_schema_revision="20260807_03",
                worker_id="worker-p3",
                translation_provider_factory=TranslationFixtureProvider,
                structured_provider_factory=StructuredFixtureProvider,
                embedding_profile=profile,
                embedding_provider_factory=lambda _profile, _credential: EmbeddingFixtureProvider(),
            )
            try:
                self.assertEqual("20260807_03", container.schema_revision)
                self.assertEqual(
                    frozenset({
                        "source_materialize",
                        "ocr",
                        "translate",
                        "explain",
                        "embed",
                    }),
                    container.processing_worker.handler_types,
                )
                self.assertIsNotNone(container.document_artifacts)
                self.assertIsNotNone(container.document_search)
                self.assertIs(profile, container.embedding_profile)
                self.assertIsNotNone(container.p3_translation_provider)
                self.assertIsNotNone(container.p3_structured_provider)
            finally:
                await container.dispose()

    async def test_bootstrap_composes_obsidian_worker_lifecycle_only_when_enabled(self) -> None:
        from backend.app.bootstrap import bootstrap_processing_worker
        from backend.app.config import DatabaseSettings
        from backend.app.domain.context import EmbeddingProfile
        from backend.tests.support.p3_database import p3_database_fixture

        class TranslationFixtureProvider:
            provider_id = "fixture-translation"
            model_id = "fixture-translation-model"
            prompt_version = "fixture-translation-v1"

        class StructuredFixtureProvider:
            provider_id = "fixture-structured"
            model_id = "fixture-structured-model"

        class EmbeddingFixtureProvider:
            provider_id = "fixture-embedding"

        profile = EmbeddingProfile(
            provider="fixture-embedding",
            model="fixture-embedding-model",
            embedding_version="fixture-embedding-v1",
            dimensions=2,
        )
        now = datetime(2026, 8, 15, tzinfo=timezone.utc)
        async with p3_database_fixture(prefix="study-app-worker-p5-bootstrap-") as database:
            root = database.database_path.parents[1]
            vault = root / "vault"
            vault.mkdir()
            common = {
                "required_schema_revision": "20260807_03",
                "translation_provider_factory": TranslationFixtureProvider,
                "structured_provider_factory": StructuredFixtureProvider,
                "embedding_profile": profile,
                "embedding_provider_factory": (
                    lambda _profile, _credential: EmbeddingFixtureProvider()
                ),
                "environment_snapshot": {
                    "OBSIDIAN_ENABLED": "1",
                    "OBSIDIAN_AUTO_EXPORT": "1",
                    "OBSIDIAN_VAULT_PATH": str(vault),
                },
                "legacy_settings_path": root / "settings.json",
                "clock": lambda: now,
            }
            enabled = bootstrap_processing_worker(
                DatabaseSettings(database.database_path),
                worker_id="worker-p5-enabled",
                obsidian_enabled=True,
                **common,
            )
            try:
                self.assertTrue(
                    {"obsidian_export", "obsidian_sync"}.issubset(
                        enabled.processing_worker.handler_types
                    )
                )
                self.assertIsNotNone(enabled.obsidian_jobs)
                self.assertIsNotNone(enabled.obsidian_auto_export)
                self.assertIsNotNone(enabled.obsidian_startup_reconciler)
            finally:
                await enabled.dispose()

            disabled = bootstrap_processing_worker(
                DatabaseSettings(database.database_path),
                worker_id="worker-p5-disabled",
                obsidian_enabled=False,
                **common,
            )
            try:
                self.assertFalse(
                    {"obsidian_export", "obsidian_sync"}
                    & disabled.processing_worker.handler_types
                )
                self.assertIsNone(disabled.obsidian_jobs)
                self.assertIsNone(disabled.obsidian_auto_export)
                self.assertIsNone(disabled.obsidian_startup_reconciler)

                spec = ObsidianSyncJobSpecV1()
                raw = encode_job_spec_v1(spec)
                queued = NewProcessingJob(
                    id="job-p5-disabled-obsidian",
                    spec=spec,
                    idempotency_key="p5-disabled-obsidian-key",
                    created_at=now,
                )
                async with SqlAlchemyUnitOfWork(database.session_factory) as work:
                    await work.jobs.insert_with_spec(
                        queued,
                        spec_json=raw,
                        spec_sha256=hash_job_spec(raw),
                    )
                    await work.commit()

                self.assertFalse(await disabled.processing_worker.run_once())
                with closing(sqlite3.connect(database.database_path)) as connection:
                    status = connection.execute(
                        "SELECT status FROM processing_jobs WHERE id=?",
                        (queued.id,),
                    ).fetchone()[0]
                self.assertEqual("queued", status)
            finally:
                await disabled.dispose()

    async def test_cli_default_once_composes_runs_and_disposes_idle_p3_worker(self) -> None:
        from backend.app.cli import processing_worker as worker_cli
        from backend.app.domain.context import EmbeddingProfile
        from backend.tests.support.p3_database import p3_database_fixture

        class TranslationProvider:
            provider_id = "cli-translation"
            model_id = "cli-translation-model"
            prompt_version = "cli-translation-v1"

        class StructuredProvider:
            provider_id = "cli-structured"
            model_id = "cli-structured-model"

        class EmbeddingProvider:
            provider_id = "cli-embedding"

        profile = EmbeddingProfile(
            provider="cli-embedding",
            model="cli-embedding-model",
            embedding_version="cli-embedding-v1",
            dimensions=2,
        )

        async with p3_database_fixture(prefix="study-app-worker-cli-default-") as database:
            exit_code = await worker_cli.run(
                ["--once"],
                environment={"DB_PATH": str(database.database_path)},
                translation_provider_factory=TranslationProvider,
                structured_provider_factory=StructuredProvider,
                embedding_profile=profile,
                embedding_provider_factory=lambda _profile, _credential: EmbeddingProvider(),
            )

            self.assertEqual(0, exit_code)
            with closing(sqlite3.connect(database.database_path)) as connection:
                self.assertEqual(
                    [("20260807_03",)],
                    connection.execute("SELECT version_num FROM alembic_version").fetchall(),
                )
                self.assertEqual(
                    0,
                    connection.execute("SELECT count(*) FROM processing_jobs").fetchone()[0],
                )

    async def test_cli_missing_p3_provider_configuration_fails_before_claim(self) -> None:
        from backend.app.cli import processing_worker as worker_cli
        from backend.tests.support.p3_database import p3_database_fixture

        async with p3_database_fixture(
            prefix="study-app-worker-cli-missing-p3-config-"
        ) as database:
            job = await self._enqueue_source_job(
                database.session_factory,
                now=datetime(2026, 8, 10, 8, 45, tzinfo=timezone.utc),
                job_id="job-cli-missing-p3-config",
                source_id="source-cli-missing-p3-config",
            )
            stderr = io.StringIO()

            exit_code = await worker_cli.run(
                ["--once"],
                environment={"DB_PATH": str(database.database_path)},
                stderr=stderr,
            )

            self.assertEqual(2, exit_code)
            self.assertEqual(
                "WORKER_CONFIGURATION_INVALID",
                json.loads(stderr.getvalue())["error"]["code"],
            )
            with closing(sqlite3.connect(database.database_path)) as connection:
                row = connection.execute(
                    "SELECT status,attempt,lease_owner,lease_token,error_code "
                    "FROM processing_jobs WHERE id=?",
                    (job.id,),
                ).fetchone()
            self.assertEqual(("queued", 0, None, None, None), row)

    async def test_p3_bootstrap_rejects_missing_provider_configuration_before_engine_creation(
        self,
    ) -> None:
        """A configuration error must not allocate an undisposable async engine."""
        from backend.app import bootstrap as bootstrap_module
        from backend.app.bootstrap import bootstrap_processing_worker
        from backend.tests.support.p3_database import p3_database_fixture

        async with p3_database_fixture(
            prefix="study-app-worker-bootstrap-missing-p3-config-"
        ) as database:
            with mock.patch.object(
                bootstrap_module,
                "create_async_session_factory",
                side_effect=AssertionError("P3 configuration must be checked before engine creation"),
            ) as create_session_factory:
                with self.assertRaisesRegex(ValueError, "P3 composition requires"):
                    bootstrap_processing_worker(
                        DatabaseSettings(database.database_path),
                        required_schema_revision="20260807_03",
                        worker_id="worker-missing-p3-config",
                    )
            create_session_factory.assert_not_called()

    async def test_signal_installer_registers_sigint_and_sigterm_and_restores_both(self) -> None:
        from backend.app.cli.processing_worker import install_signal_handlers

        class FakeSignals:
            SIGINT = 2
            SIGTERM = 15

            def __init__(self) -> None:
                self.handlers = {self.SIGINT: "old-int", self.SIGTERM: "old-term"}
                self.writes = []

            def getsignal(self, signum):
                return self.handlers[signum]

            def signal(self, signum, handler):
                self.handlers[signum] = handler
                self.writes.append((signum, handler))

        signals = FakeSignals()
        stop_event = asyncio.Event()
        cleanup = install_signal_handlers(stop_event, signal_api=signals)

        for signum in (signals.SIGINT, signals.SIGTERM):
            stop_event.clear()
            signals.handlers[signum](signum, None)
            self.assertTrue(stop_event.is_set())

        cleanup()
        self.assertEqual("old-int", signals.handlers[signals.SIGINT])
        self.assertEqual("old-term", signals.handlers[signals.SIGTERM])

    async def _enqueue_source_job(
        self,
        session_factory,
        *,
        now: datetime,
        job_id: str,
        source_id: str,
        max_attempts: int = 3,
        source_mode: str = "native",
    ) -> NewProcessingJob:
        source = SourceDocument(
            id=source_id,
            paper_id="paper-1",
            mode=source_mode,
            status="queued",
            provider="fake" if source_mode == "ocr" else "local",
            model="fake-ocr-v1" if source_mode == "ocr" else "pymupdf",
            pdf_sha256=hashlib.sha256(source_id.encode("utf-8")).hexdigest(),
            options_hash="c" * 64,
            processing_version="native-v1",
            created_at=now,
            updated_at=now,
        )
        spec = (
            OcrJobSpecV1(
                paper_id="paper-1",
                source_document_id=source.id,
                provider=source.provider,
                model=source.model,
            )
            if source_mode == "ocr"
            else SourceMaterializeJobSpecV1(
                paper_id="paper-1",
                source_document_id=source.id,
                processing_version="native-v1",
            )
        )
        raw = encode_job_spec_v1(spec)
        job = NewProcessingJob(
            id=job_id,
            spec=spec,
            idempotency_key=build_source_job_key(
                build_source_key(
                    paper_id=source.paper_id,
                    mode=source.mode,
                    provider=source.provider,
                    model=source.model,
                    pdf_sha256=source.pdf_sha256,
                    options_hash=source.options_hash,
                    processing_version=source.processing_version,
                ),
                hash_job_spec(raw),
            ),
            created_at=now,
            max_attempts=max_attempts,
        )
        async with SqlAlchemyUnitOfWork(session_factory) as work:
            await work.sources.add(source)
            await work.jobs.insert_with_spec(
                job,
                spec_json=raw,
                spec_sha256=hash_job_spec(raw),
            )
            await work.commit()
        return job


class ProcessingCancellationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory(prefix="study-app-processing-cancel-")
        self.database_path = Path(self._temp.name) / "queue" / "app.db"
        create_legacy_database(self.database_path)
        run_alembic(self.database_path, "20260807_02")
        self.session_factory = create_async_session_factory(DatabaseSettings(self.database_path))
        self.engine = self.session_factory.kw["bind"]
        self.now = datetime(2026, 8, 9, 14, 0, tzinfo=timezone.utc)

    async def asyncTearDown(self) -> None:
        await self.engine.dispose()
        self._temp.cleanup()

    async def test_queued_cancel_settles_job_and_target_atomically_with_safe_event(self) -> None:
        job = await self._enqueue_source_job("job-queued", "source-queued")
        raw = encode_job_spec_v1(job.spec)

        async with SqlAlchemyUnitOfWork(self.session_factory) as work:
            cancelled = await work.jobs.cancel(job.id, now=self.now)
            await work.commit()

        self.assertEqual("cancelled", cancelled.status.value)
        with closing(sqlite3.connect(self.database_path)) as connection:
            job_row = connection.execute(
                "SELECT status,finished_at,cancelled_at,cancel_requested_at,spec_json "
                "FROM processing_jobs WHERE id=?", (job.id,),
            ).fetchone()
            target_row = connection.execute(
                "SELECT status FROM document_sources WHERE id=?", ("source-queued",),
            ).fetchone()
            events = connection.execute(
                "SELECT event_type,progress_json,error_code FROM processing_job_events "
                "WHERE job_id=? ORDER BY sequence", (job.id,),
            ).fetchall()
        self.assertEqual(("cancelled",), target_row)
        self.assertEqual("cancelled", job_row[0])
        self.assertEqual(self.now, datetime.fromisoformat(job_row[1].replace("Z", "+00:00")))
        self.assertEqual(self.now, datetime.fromisoformat(job_row[2].replace("Z", "+00:00")))
        self.assertIsNone(job_row[3])
        self.assertEqual(raw, job_row[4])
        self.assertEqual(hash_job_spec(raw), hash_job_spec(job_row[4]))
        self.assertEqual(job.spec, __import__("backend.app.domain.processing", fromlist=["decode_job_spec_v1"]).decode_job_spec_v1(job_row[4]))
        self.assertEqual([("enqueued", "{}", None), ("cancelled", "{}", None)], events)

    async def test_running_cancel_requests_then_current_checkpoint_settles_cancelled(self) -> None:
        job = await self._enqueue_source_job("job-running", "source-running")
        async with SqlAlchemyUnitOfWork(self.session_factory) as work:
            lease = await work.jobs.claim_next(worker_id="worker-a", now=self.now, lease_seconds=30)
            await work.commit()
        self.assertIsNotNone(lease)

        requested_at = self.now + timedelta(seconds=1)
        async with SqlAlchemyUnitOfWork(self.session_factory) as work:
            requested = await work.jobs.cancel(job.id, now=requested_at)
            await work.commit()
        self.assertEqual("running", requested.status.value)

        checkpoint_at = self.now + timedelta(seconds=2)
        progress = JobProgress({"stage": "pdf_loaded", "pagesCompleted": 2})
        async with SqlAlchemyUnitOfWork(self.session_factory) as work:
            outcome = await work.jobs.checkpoint(lease, progress, now=checkpoint_at)
            await work.commit()
        self.assertEqual("cancelled", outcome)

        with closing(sqlite3.connect(self.database_path)) as connection:
            row = connection.execute(
                "SELECT status,progress_json,cancel_requested_at,finished_at,cancelled_at,"
                "lease_owner,lease_token,spec_json FROM processing_jobs WHERE id=?", (job.id,),
            ).fetchone()
            target = connection.execute(
                "SELECT status FROM document_sources WHERE id=?", ("source-running",),
            ).fetchone()
            events = connection.execute(
                "SELECT event_type,progress_json FROM processing_job_events "
                "WHERE job_id=? ORDER BY sequence", (job.id,),
            ).fetchall()
        self.assertEqual("cancelled", row[0])
        self.assertEqual('{"pagesCompleted":2,"stage":"pdf_loaded"}', row[1])
        self.assertEqual(requested_at, datetime.fromisoformat(row[2].replace("Z", "+00:00")))
        self.assertEqual(checkpoint_at, datetime.fromisoformat(row[3].replace("Z", "+00:00")))
        self.assertEqual(checkpoint_at, datetime.fromisoformat(row[4].replace("Z", "+00:00")))
        self.assertIsNone(row[5])
        self.assertIsNone(row[6])
        self.assertEqual(encode_job_spec_v1(job.spec), row[7])
        self.assertEqual(("cancelled",), target)
        self.assertEqual(
            [
                ("enqueued", "{}"),
                ("claimed", "{}"),
                ("cancel_requested", "{}"),
                ("cancelled", '{"pagesCompleted":2,"stage":"pdf_loaded"}'),
            ],
            events,
        )

    async def test_terminal_cancel_is_typed_and_has_zero_mutation(self) -> None:
        job = await self._enqueue_source_job("job-terminal", "source-terminal")
        async with SqlAlchemyUnitOfWork(self.session_factory) as work:
            await work.jobs.cancel(job.id, now=self.now)
            await work.commit()
        before = self._job_evidence(job.id, "source-terminal")

        with self.assertRaises(JobNotCancellableError) as caught:
            async with SqlAlchemyUnitOfWork(self.session_factory) as work:
                await work.jobs.cancel(job.id, now=self.now + timedelta(seconds=1))

        self.assertEqual("JOB_NOT_CANCELLABLE", caught.exception.code)
        self.assertEqual(before, self._job_evidence(job.id, "source-terminal"))

    async def test_cancel_and_complete_have_one_terminal_cas_winner(self) -> None:
        job = await self._enqueue_source_job("job-race", "source-race")
        async with SqlAlchemyUnitOfWork(self.session_factory) as work:
            lease = await work.jobs.claim_next(worker_id="worker-race", now=self.now, lease_seconds=30)
            await work.commit()
        self.assertIsNotNone(lease)

        async with SqlAlchemyUnitOfWork(self.session_factory) as work:
            await work.jobs.cancel(job.id, now=self.now + timedelta(seconds=1))
            await work.commit()

        async with SqlAlchemyUnitOfWork(self.session_factory) as work:
            winner = await work.jobs.complete(
                lease, JobResult({"contentSha256": "x"}), now=self.now + timedelta(seconds=2),
            )
            await work.commit()
        self.assertEqual("cancelled", winner.status.value)
        evidence = self._job_evidence(job.id, "source-race")
        with closing(sqlite3.connect(self.database_path)) as connection:
            terminal_events = connection.execute(
                "SELECT event_type FROM processing_job_events WHERE job_id=? "
                "AND event_type IN ('cancelled','succeeded','failed')", (job.id,),
            ).fetchall()
        self.assertEqual("cancelled", evidence[0][4])
        self.assertEqual([("cancelled",)], terminal_events)

    async def test_explicit_retry_copies_terminal_spec_and_records_parent_lineage_event(self) -> None:
        parent = await self._enqueue_source_job("job-retry", "source-retry")
        raw = encode_job_spec_v1(parent.spec)
        async with SqlAlchemyUnitOfWork(self.session_factory) as work:
            lease = await work.jobs.claim_next(worker_id="worker-retry", now=self.now, lease_seconds=30)
            await work.commit()
        assert lease is not None
        async with SqlAlchemyUnitOfWork(self.session_factory) as work:
            await work.jobs.fail(
                lease,
                JobFailure(code="SOURCE_FAILED", retryable=False),
                now=self.now + timedelta(seconds=1),
            )
            await work.commit()

        async with SqlAlchemyUnitOfWork(self.session_factory) as work:
            retry = await work.jobs.retry(parent.id, now=self.now + timedelta(seconds=2))
            await work.commit()

        self.assertFalse(retry.deduplicated)
        with closing(sqlite3.connect(self.database_path)) as connection:
            parent_row = connection.execute(
                "SELECT status,spec_json FROM processing_jobs WHERE id=?", (parent.id,),
            ).fetchone()
            child_row = connection.execute(
                "SELECT status,retry_of_job_id,retry_sequence,spec_json FROM processing_jobs WHERE id=?",
                (retry.job.id,),
            ).fetchone()
            parent_events = connection.execute(
                "SELECT event_type FROM processing_job_events WHERE job_id=? ORDER BY sequence", (parent.id,),
            ).fetchall()
        self.assertEqual(("failed", raw), parent_row)
        self.assertEqual(("queued", parent.id, 1, raw), child_row)
        self.assertEqual(
            [("enqueued",), ("claimed",), ("failed",), ("retry_scheduled",)], parent_events,
        )

    async def test_cancelled_parent_retry_deduplicates_concurrently_with_exact_spec_bytes(self) -> None:
        parent = await self._enqueue_source_job("job-retry-cancelled", "source-retry-cancelled")
        raw = encode_job_spec_v1(parent.spec)
        async with SqlAlchemyUnitOfWork(self.session_factory) as work:
            await work.jobs.cancel(parent.id, now=self.now)
            await work.commit()

        async def retry_once():
            async with SqlAlchemyUnitOfWork(self.session_factory) as work:
                result = await work.jobs.retry(parent.id, now=self.now + timedelta(seconds=1))
                await work.commit()
                return result

        first, second = await asyncio.gather(retry_once(), retry_once())
        self.assertEqual(first.job.id, second.job.id)
        self.assertEqual([False, True], sorted((first.deduplicated, second.deduplicated)))
        with closing(sqlite3.connect(self.database_path)) as connection:
            parent_row = connection.execute(
                "SELECT status,spec_json FROM processing_jobs WHERE id=?", (parent.id,),
            ).fetchone()
            descendants = connection.execute(
                "SELECT retry_of_job_id,retry_sequence,spec_json FROM processing_jobs WHERE retry_of_job_id=?",
                (parent.id,),
            ).fetchall()
        self.assertEqual(("cancelled", raw), parent_row)
        self.assertEqual([(parent.id, 1, raw)], descendants)

    async def test_explicit_retry_rejects_succeeded_and_nonterminal_parents_without_mutation(self) -> None:
        succeeded = await self._enqueue_source_job("job-retry-succeeded", "source-retry-succeeded")
        async with SqlAlchemyUnitOfWork(self.session_factory) as work:
            lease = await work.jobs.claim_next(worker_id="worker-succeeded", now=self.now, lease_seconds=30)
            await work.commit()
        assert lease is not None and lease.job.id == succeeded.id
        async with SqlAlchemyUnitOfWork(self.session_factory) as work:
            await work.jobs.complete(lease, JobResult({"contentSha256": "ok"}), now=self.now + timedelta(seconds=1))
            await work.commit()
        queued = await self._enqueue_source_job("job-retry-queued", "source-retry-queued")

        for parent, source_id in ((succeeded, "source-retry-succeeded"), (queued, "source-retry-queued")):
            before = self._job_evidence(parent.id, source_id)
            with self.assertRaises(JobNotRetryableError) as caught:
                async with SqlAlchemyUnitOfWork(self.session_factory) as work:
                    await work.jobs.retry(parent.id, now=self.now + timedelta(seconds=2))
            self.assertEqual("JOB_NOT_RETRYABLE", caught.exception.code)
            self.assertEqual(before, self._job_evidence(parent.id, source_id))

    async def test_explicit_retry_rejects_nonretryable_failure_and_stale_target_without_mutation(self) -> None:
        nonretryable = await self._enqueue_source_job("job-retry-nonretryable", "source-retry-nonretryable")
        async with SqlAlchemyUnitOfWork(self.session_factory) as work:
            lease = await work.jobs.claim_next(worker_id="worker-nonretryable", now=self.now, lease_seconds=30)
            await work.commit()
        assert lease is not None
        async with SqlAlchemyUnitOfWork(self.session_factory) as work:
            await work.jobs.fail(
                lease, JobFailure(code="NATIVE_TEXT_EMPTY", retryable=False), now=self.now + timedelta(seconds=1),
            )
            await work.commit()

        stale = await self._enqueue_source_job("job-retry-stale", "source-retry-stale")
        async with SqlAlchemyUnitOfWork(self.session_factory) as work:
            lease = await work.jobs.claim_next(worker_id="worker-stale", now=self.now, lease_seconds=30)
            await work.commit()
        assert lease is not None
        async with SqlAlchemyUnitOfWork(self.session_factory) as work:
            await work.jobs.fail(lease, JobFailure(code="TEST_FAILURE"), now=self.now + timedelta(seconds=1))
            await work.commit()
        with closing(sqlite3.connect(self.database_path)) as connection:
            connection.execute("UPDATE document_sources SET status='stale' WHERE id=?", ("source-retry-stale",))
            connection.commit()

        for parent, source_id in (
            (nonretryable, "source-retry-nonretryable"),
            (stale, "source-retry-stale"),
        ):
            before = self._job_evidence(parent.id, source_id)
            with self.assertRaises(JobNotRetryableError) as caught:
                async with SqlAlchemyUnitOfWork(self.session_factory) as work:
                    await work.jobs.retry(parent.id, now=self.now + timedelta(seconds=2))
            self.assertEqual("JOB_NOT_RETRYABLE", caught.exception.code)
            self.assertEqual(before, self._job_evidence(parent.id, source_id))

    async def test_expired_lease_reclaims_exact_spec_and_stale_worker_cannot_publish(self) -> None:
        job = await self._enqueue_source_job("job-recovery", "source-recovery")
        raw = encode_job_spec_v1(job.spec)
        async with SqlAlchemyUnitOfWork(self.session_factory) as work:
            old_lease = await work.jobs.claim_next(worker_id="worker-old", now=self.now, lease_seconds=10)
            await work.commit()
        assert old_lease is not None

        async with SqlAlchemyUnitOfWork(self.session_factory) as work:
            new_lease = await work.jobs.claim_next(
                worker_id="worker-new", now=self.now + timedelta(seconds=11), lease_seconds=30,
            )
            await work.commit()
        assert new_lease is not None
        self.assertEqual(job.id, new_lease.job.id)
        self.assertNotEqual(old_lease.token, new_lease.token)
        with closing(sqlite3.connect(self.database_path)) as connection:
            self.assertEqual(raw, connection.execute(
                "SELECT spec_json FROM processing_jobs WHERE id=?", (job.id,),
            ).fetchone()[0])
            self.assertEqual(
                [("enqueued",), ("claimed",), ("lease_recovered",), ("claimed",)],
                connection.execute(
                    "SELECT event_type FROM processing_job_events WHERE job_id=? ORDER BY sequence", (job.id,),
                ).fetchall(),
            )

        before = self._job_evidence(job.id, "source-recovery")
        with self.assertRaises(JobLeaseLostError):
            async with SqlAlchemyUnitOfWork(self.session_factory) as work:
                await work.jobs.complete(
                    old_lease, JobResult({"contentSha256": "stale"}), now=self.now + timedelta(seconds=12),
                )
        self.assertEqual(before, self._job_evidence(job.id, "source-recovery"))

    async def _enqueue_source_job(self, job_id: str, source_id: str) -> NewProcessingJob:
        source = SourceDocument(
            id=source_id, paper_id="paper-1", mode="native", status="queued", provider="local",
            model="pymupdf", pdf_sha256=hashlib.sha256(source_id.encode("utf-8")).hexdigest(),
            options_hash="c" * 64, processing_version="native-v1", created_at=self.now, updated_at=self.now,
        )
        spec = SourceMaterializeJobSpecV1(
            paper_id="paper-1", source_document_id=source.id, processing_version="native-v1",
        )
        raw = encode_job_spec_v1(spec)
        job = NewProcessingJob(
            id=job_id,
            spec=spec,
            idempotency_key=build_source_job_key(
                build_source_key(
                    paper_id=source.paper_id, mode=source.mode, provider=source.provider, model=source.model,
                    pdf_sha256=source.pdf_sha256, options_hash=source.options_hash,
                    processing_version=source.processing_version,
                ),
                hash_job_spec(raw),
            ),
            created_at=self.now,
            max_attempts=3,
        )
        async with SqlAlchemyUnitOfWork(self.session_factory) as work:
            await work.sources.add(source)
            await work.jobs.insert_with_spec(job, spec_json=raw, spec_sha256=hash_job_spec(raw))
            await work.commit()
        return job

    def _job_evidence(self, job_id: str, source_id: str) -> tuple[object, ...]:
        with closing(sqlite3.connect(self.database_path)) as connection:
            job = connection.execute(
                "SELECT * FROM processing_jobs WHERE id=?", (job_id,),
            ).fetchone()
            source = connection.execute(
                "SELECT * FROM document_sources WHERE id=?", (source_id,),
            ).fetchone()
            events = connection.execute(
                "SELECT * FROM processing_job_events WHERE job_id=? ORDER BY sequence", (job_id,),
            ).fetchall()
        return job, source, tuple(events)
