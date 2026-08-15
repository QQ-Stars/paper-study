from __future__ import annotations

from contextlib import closing
from datetime import datetime, timedelta, timezone
import hashlib
import json
import sqlite3
import unittest

from sqlalchemy.exc import SQLAlchemyError

from backend.app.application import generated_artifacts
from backend.app.application.ports.artifact_generator import (
    GeneratorIdentity,
    PaperMetadata,
)
from backend.app.domain import (
    GeneratedArtifact,
    EmptyArtifactError,
    GenerationFailureError,
    JobLeaseLostError,
    PersistenceConflictError,
    SourceDocument,
)
from backend.app.domain.processing import JobSpecValidationError
from backend.app.domain.processing import (
    NewProcessingJob,
    OcrJobSpecV1,
    build_source_job_key,
    build_source_key,
    encode_job_spec_v1,
    hash_canonical_json,
    hash_job_spec,
)
from backend.app.providers.ocr.fake import FakeOcrProvider
from backend.app.providers.ocr.registry import create_test_ocr_registry
from backend.app.repositories.ocr_checkpoints import SqlAlchemyOcrCheckpointRepository
from backend.app.repositories.unit_of_work import SqlAlchemyUnitOfWork
from backend.tests.support.p2_database import p2_database_fixture


NOW = datetime(2026, 8, 10, 12, 0, tzinfo=timezone.utc)
PDF_BYTES = b"%PDF-1.7\n% task-8 fixture\n"
PDF_SHA256 = hashlib.sha256(PDF_BYTES).hexdigest()
OPTIONS_SHA256 = hashlib.sha256(b"{}").hexdigest()
MARKDOWN = "# OCR source\n\nProven text.\n"
MARKDOWN_SHA256 = hashlib.sha256(MARKDOWN.encode("utf-8")).hexdigest()


class FakeExplainerProvider:
    def __init__(self, output: str = "# Explainer\n") -> None:
        self.output = output
        self.identity_calls: list[tuple[str, str]] = []
        self.generate_calls: list[tuple[str, PaperMetadata, str, str]] = []
        self.pdf_open_calls = 0
        self.on_identity = None
        self.on_generate = None
        self.callback_errors: list[Exception] = []

    def identity(self, kind: str, profile: str = "standard") -> GeneratorIdentity:
        self.identity_calls.append((kind, profile))
        if self.on_identity is not None:
            self.on_identity()
        return GeneratorIdentity("fake-llm", "fake-model", f"{kind}-{profile}-v1")

    def generate(
        self,
        kind: str,
        paper: PaperMetadata,
        source_markdown: str,
        profile: str = "standard",
    ) -> str:
        self.generate_calls.append((kind, paper, source_markdown, profile))
        if self.on_generate is not None:
            try:
                self.on_generate()
            except Exception as error:
                self.callback_errors.append(error)
                raise
        return self.output

    def open_pdf(self, *_args, **_kwargs) -> None:
        self.pdf_open_calls += 1
        raise AssertionError("explainer provider must not open the PDF")


class OnePageReader:
    def __init__(self) -> None:
        self.calls = 0

    def page_count(self, _pdf_bytes: bytes) -> int:
        self.calls += 1
        return 1


class OcrExplainerSliceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self._fixture_context = p2_database_fixture(prefix="study-app-p2-explainer-")
        self.fixture = await self._fixture_context.__aenter__()
        self.session_factory = self.fixture.session_factory
        self.pdf_path = self.fixture.database_path.parent / "paper-1.pdf"
        self.pdf_path.write_bytes(PDF_BYTES)
        with closing(sqlite3.connect(self.fixture.database_path)) as connection:
            connection.execute(
                "UPDATE papers SET pdf_path=?, authors=?, abstract=? WHERE id='paper-1'",
                (str(self.pdf_path), '["Ada", "Grace"]', "Seed abstract"),
            )
            connection.commit()
        self.source = SourceDocument(
            id="source-ocr-ready",
            paper_id="paper-1",
            mode="ocr",
            status="ready",
            provider="fake-ocr",
            model="fake-ocr-v1",
            pdf_sha256=PDF_SHA256,
            options_hash=OPTIONS_SHA256,
            processing_version="fake-ocr-processing-v1",
            created_at=NOW,
            updated_at=NOW,
            markdown=MARKDOWN,
            content_sha256=MARKDOWN_SHA256,
            page_count=1,
        )
        async with SqlAlchemyUnitOfWork(self.session_factory) as work:
            await work.sources.add(self.source)
            await work.commit()
        self.provider = FakeExplainerProvider()

    async def asyncTearDown(self) -> None:
        await self._fixture_context.__aexit__(None, None, None)

    def _application(self):
        application_type = getattr(generated_artifacts, "ArtifactGenerator", None)
        self.assertIsNotNone(
            application_type,
            "Task 8 requires the lease-aware ArtifactGenerator application service",
        )
        self.assertFalse(
            getattr(application_type, "_is_protocol", False),
            "the imported provider Protocol is not the Task 8 application service",
        )
        return application_type(
            lambda: SqlAlchemyUnitOfWork(self.session_factory),
            self.provider,
            clock=lambda: NOW,
            artifact_id_factory=lambda: "artifact-explainer-1",
            job_id_factory=lambda: "job-explainer-1",
        )

    def _counts(self) -> tuple[int, int]:
        with closing(sqlite3.connect(self.fixture.database_path)) as connection:
            return (
                connection.execute("SELECT count(*) FROM generated_artifacts").fetchone()[0],
                connection.execute("SELECT count(*) FROM processing_jobs").fetchone()[0],
            )

    async def _enqueue_and_claim(self):
        application = self._application()
        enqueue = await application.enqueue_explainer(
            "paper-1",
            sourceMode="ocr",
            sourceDocumentId=self.source.id,
            profile="deep",
        )
        async with SqlAlchemyUnitOfWork(self.session_factory) as work:
            lease = await work.jobs.claim_next(
                worker_id="worker-task-8",
                now=NOW + timedelta(seconds=1),
                lease_seconds=60,
            )
            await work.commit()
        self.assertIsNotNone(lease)
        return application, enqueue, lease

    async def _seed_old_head(self) -> GeneratedArtifact:
        old_artifact = GeneratedArtifact(
            id="artifact-old-head",
            paper_id="paper-1",
            kind="explainer",
            source_document_id=self.source.id,
            status="ready",
            generator_provider="fake-llm",
            generator_model="fake-model",
            prompt_version="explainer-old-v1",
            created_at=NOW - timedelta(days=1),
            updated_at=NOW - timedelta(days=1),
            content="# Old explainer\n",
            content_sha256=hashlib.sha256(b"# Old explainer\n").hexdigest(),
        )
        async with SqlAlchemyUnitOfWork(self.session_factory) as work:
            await work.artifacts.add(old_artifact)
            self.assertTrue(
                await work.artifacts.publish_head(
                    paper_id="paper-1",
                    kind="explainer",
                    artifact_id=old_artifact.id,
                    expected_artifact_id=None,
                    updated_at=old_artifact.updated_at,
                )
            )
            await work.artifacts.write_legacy_explainer(
                "paper-1", old_artifact.content or "", old_artifact.updated_at
            )
            await work.commit()
        return old_artifact

    async def test_enqueue_rejects_source_mode_mismatch_before_any_write(self) -> None:
        before = self._counts()

        with self.assertRaises(JobSpecValidationError):
            await self._application().enqueue_explainer(
                "paper-1",
                sourceMode="native",
                sourceDocumentId=self.source.id,
                profile="deep",
            )

        self.assertEqual(before, self._counts())
        self.assertEqual([], self.provider.identity_calls)
        self.assertEqual([], self.provider.generate_calls)
        self.assertEqual(0, self.provider.pdf_open_calls)

    async def test_enqueue_rejects_source_owned_by_another_paper_before_any_write(self) -> None:
        before = self._counts()

        with self.assertRaises(JobSpecValidationError):
            await self._application().enqueue_explainer(
                "paper-2",
                sourceMode="ocr",
                sourceDocumentId=self.source.id,
                profile="standard",
            )

        self.assertEqual(before, self._counts())
        self.assertEqual([], self.provider.identity_calls)
        self.assertEqual([], self.provider.generate_calls)

    async def test_enqueue_persists_bound_artifact_job_and_canonical_command(self) -> None:
        result = await self._application().enqueue_explainer(
            "paper-1",
            sourceMode="ocr",
            sourceDocumentId=self.source.id,
            profile="deep",
        )

        self.assertEqual("artifact-explainer-1", result.artifact.id)
        self.assertEqual("queued", result.artifact.status.value)
        self.assertEqual(self.source.id, result.artifact.source_document_id)
        self.assertEqual("fake-llm", result.artifact.generator_provider)
        self.assertEqual("fake-model", result.artifact.generator_model)
        self.assertEqual("explainer-deep-v1", result.artifact.prompt_version)
        self.assertEqual("job-explainer-1", result.job.id)
        self.assertEqual("ocr", result.job.spec.source_mode)
        self.assertFalse(result.deduplicated)
        self.assertEqual([("explainer", "deep")], self.provider.identity_calls)
        self.assertEqual([], self.provider.generate_calls)

        with closing(sqlite3.connect(self.fixture.database_path)) as connection:
            artifact_row = connection.execute(
                "SELECT id,paper_id,kind,source_document_id,status,generator_provider,"
                "generator_model,prompt_version,artifact_key FROM generated_artifacts"
            ).fetchone()
            job_row = connection.execute(
                "SELECT id,paper_id,job_type,source_mode,source_document_id,artifact_id,"
                "status,spec_json FROM processing_jobs"
            ).fetchone()
            event_row = connection.execute(
                "SELECT job_id,sequence,event_type FROM processing_job_events"
            ).fetchone()
        self.assertEqual(
            (
                "artifact-explainer-1",
                "paper-1",
                "explainer",
                self.source.id,
                "queued",
                "fake-llm",
                "fake-model",
                "explainer-deep-v1",
            ),
            artifact_row[:-1],
        )
        self.assertEqual(
            (
                "job-explainer-1",
                "paper-1",
                "explain",
                "ocr",
                self.source.id,
                "artifact-explainer-1",
                "queued",
            ),
            job_row[:-1],
        )
        self.assertTrue(artifact_row[-1])
        command = json.loads(job_row[-1])
        self.assertEqual("ocr", command["sourceMode"])
        self.assertEqual(self.source.id, command["target"]["sourceDocumentId"])
        self.assertEqual("artifact-explainer-1", command["target"]["artifactId"])
        self.assertEqual("deep", command["arguments"]["profile"])
        self.assertEqual(("job-explainer-1", 1, "enqueued"), event_row)

    async def test_repeated_enqueue_returns_existing_artifact_and_job_with_new_id_factories(self) -> None:
        artifact_ids = iter(("artifact-dedupe-first", "artifact-dedupe-second"))
        job_ids = iter(("job-dedupe-first", "job-dedupe-second"))
        application = generated_artifacts.ArtifactGenerator(
            lambda: SqlAlchemyUnitOfWork(self.session_factory),
            self.provider,
            clock=lambda: NOW,
            artifact_id_factory=lambda: next(artifact_ids),
            job_id_factory=lambda: next(job_ids),
        )

        first = await application.enqueue_explainer(
            "paper-1",
            sourceMode="ocr",
            sourceDocumentId=self.source.id,
            profile="deep",
        )
        second = await application.enqueue_explainer(
            "paper-1",
            sourceMode="ocr",
            sourceDocumentId=self.source.id,
            profile="deep",
        )

        self.assertFalse(first.deduplicated)
        self.assertTrue(second.deduplicated)
        self.assertEqual(first.artifact.id, second.artifact.id)
        self.assertEqual(first.job.id, second.job.id)
        self.assertEqual((1, 1), self._counts())

    async def test_enqueue_rejects_not_ready_source_before_provider_or_write(self) -> None:
        queued = SourceDocument(
            id="source-ocr-queued",
            paper_id="paper-1",
            mode="ocr",
            status="queued",
            provider="fake-ocr",
            model="fake-ocr-v1",
            pdf_sha256=PDF_SHA256,
            options_hash=hashlib.sha256(b'{"queued":true}').hexdigest(),
            processing_version="fake-ocr-processing-v1",
            created_at=NOW,
            updated_at=NOW,
        )
        async with SqlAlchemyUnitOfWork(self.session_factory) as work:
            await work.sources.add(queued)
            await work.commit()
        before = self._counts()

        with self.assertRaises(JobSpecValidationError):
            await self._application().enqueue_explainer(
                "paper-1",
                sourceMode="ocr",
                sourceDocumentId=queued.id,
                profile="standard",
            )

        self.assertEqual(before, self._counts())
        self.assertEqual([], self.provider.identity_calls)
        self.assertEqual([], self.provider.generate_calls)

    async def test_enqueue_rejects_pdf_drift_as_stale_before_provider_or_write(self) -> None:
        self.pdf_path.write_bytes(PDF_BYTES + b"changed")
        before = self._counts()

        with self.assertRaises(JobSpecValidationError):
            await self._application().enqueue_explainer(
                "paper-1",
                sourceMode="ocr",
                sourceDocumentId=self.source.id,
                profile="standard",
            )

        self.assertEqual(before, self._counts())
        self.assertEqual([], self.provider.identity_calls)
        self.assertEqual([], self.provider.generate_calls)

    async def test_enqueue_rechecks_persisted_mode_inside_atomic_write(self) -> None:
        def change_source_mode() -> None:
            with closing(sqlite3.connect(self.fixture.database_path)) as connection:
                connection.execute(
                    "UPDATE document_sources SET mode='native' WHERE id=?",
                    (self.source.id,),
                )
                connection.commit()

        self.provider.on_identity = change_source_mode
        before = self._counts()

        with self.assertRaises(JobSpecValidationError):
            await self._application().enqueue_explainer(
                "paper-1",
                sourceMode="ocr",
                sourceDocumentId=self.source.id,
                profile="deep",
            )

        self.assertEqual(before, self._counts())
        self.assertEqual([("explainer", "deep")], self.provider.identity_calls)
        self.assertEqual([], self.provider.generate_calls)

    async def test_enqueue_rechecks_pdf_after_identity_before_commit(self) -> None:
        self.provider.on_identity = lambda: self.pdf_path.write_bytes(
            PDF_BYTES + b"late drift"
        )
        before = self._counts()

        with self.assertRaises(JobSpecValidationError):
            await self._application().enqueue_explainer(
                "paper-1",
                sourceMode="ocr",
                sourceDocumentId=self.source.id,
                profile="deep",
            )

        self.assertEqual(before, self._counts())
        self.assertEqual([("explainer", "deep")], self.provider.identity_calls)
        self.assertEqual([], self.provider.generate_calls)

    async def test_generate_uses_source_markdown_and_atomically_publishes_all_projections(self) -> None:
        old_artifact = GeneratedArtifact(
            id="artifact-old-head",
            paper_id="paper-1",
            kind="explainer",
            source_document_id=self.source.id,
            status="ready",
            generator_provider="fake-llm",
            generator_model="fake-model",
            prompt_version="explainer-old-v1",
            created_at=NOW - timedelta(days=1),
            updated_at=NOW - timedelta(days=1),
            content="# Old explainer\n",
            content_sha256=hashlib.sha256(b"# Old explainer\n").hexdigest(),
        )
        async with SqlAlchemyUnitOfWork(self.session_factory) as work:
            await work.artifacts.add(old_artifact)
            self.assertTrue(
                await work.artifacts.publish_head(
                    paper_id="paper-1",
                    kind="explainer",
                    artifact_id=old_artifact.id,
                    expected_artifact_id=None,
                    updated_at=old_artifact.updated_at,
                )
            )
            await work.artifacts.write_legacy_explainer(
                "paper-1", old_artifact.content or "", old_artifact.updated_at
            )
            await work.commit()
        application, enqueue, lease = await self._enqueue_and_claim()

        published = await application.generate_explainer(lease, self.source.id)

        self.assertEqual(enqueue.artifact.id, published.id)
        self.assertEqual("ready", published.status.value)
        self.assertEqual("# Explainer\n", published.content)
        self.assertEqual(self.source.id, published.source_document_id)
        self.assertEqual("fake-llm", published.generator_provider)
        self.assertEqual("fake-model", published.generator_model)
        self.assertEqual("explainer-deep-v1", published.prompt_version)
        self.assertEqual(
            [("explainer", self.provider.generate_calls[0][1], MARKDOWN, "deep")],
            self.provider.generate_calls,
        )
        self.assertEqual(0, self.provider.pdf_open_calls)

        with closing(sqlite3.connect(self.fixture.database_path)) as connection:
            new_row = connection.execute(
                "SELECT status,content,content_sha256,ready_at,stale_at "
                "FROM generated_artifacts WHERE id=?",
                (enqueue.artifact.id,),
            ).fetchone()
            old_row = connection.execute(
                "SELECT status,stale_at FROM generated_artifacts WHERE id=?",
                (old_artifact.id,),
            ).fetchone()
            head = connection.execute(
                "SELECT artifact_id FROM paper_artifact_heads "
                "WHERE paper_id='paper-1' AND kind='explainer'"
            ).fetchone()
            legacy = connection.execute(
                "SELECT explainer FROM papers WHERE id='paper-1'"
            ).fetchone()
            job = connection.execute(
                "SELECT status,result_json,lease_owner,lease_token,lease_expires_at "
                "FROM processing_jobs WHERE id=?",
                (enqueue.job.id,),
            ).fetchone()
            events = connection.execute(
                "SELECT sequence,event_type FROM processing_job_events "
                "WHERE job_id=? ORDER BY sequence",
                (enqueue.job.id,),
            ).fetchall()
        expected_sha = hashlib.sha256(b"# Explainer\n").hexdigest()
        self.assertEqual(("ready", "# Explainer\n", expected_sha), new_row[:3])
        self.assertIsNotNone(new_row[3])
        self.assertIsNone(new_row[4])
        self.assertEqual("stale", old_row[0])
        self.assertIsNotNone(old_row[1])
        self.assertEqual((enqueue.artifact.id,), head)
        self.assertEqual(("# Explainer\n",), legacy)
        self.assertEqual("succeeded", job[0])
        self.assertEqual(
            {"artifactId": enqueue.artifact.id, "contentSha256": expected_sha},
            json.loads(job[1]),
        )
        self.assertEqual((None, None, None), job[2:])
        self.assertEqual([(1, "enqueued"), (2, "claimed"), (3, "succeeded")], events)

    async def test_generate_rejects_source_that_becomes_stale_after_provider_call(self) -> None:
        with closing(sqlite3.connect(self.fixture.database_path)) as connection:
            connection.execute(
                "UPDATE papers SET explainer='legacy-stable' WHERE id='paper-1'"
            )
            connection.commit()
        application, enqueue, lease = await self._enqueue_and_claim()

        def mark_source_stale() -> None:
            with closing(sqlite3.connect(self.fixture.database_path)) as connection:
                connection.execute(
                    "UPDATE document_sources SET status='stale',stale_at=? WHERE id=?",
                    (NOW.isoformat(), self.source.id),
                )
                connection.commit()

        self.provider.on_generate = mark_source_stale

        with self.assertRaises(PersistenceConflictError):
            await application.generate_explainer(lease, self.source.id)

        with closing(sqlite3.connect(self.fixture.database_path)) as connection:
            artifact = connection.execute(
                "SELECT status,content,ready_at FROM generated_artifacts WHERE id=?",
                (enqueue.artifact.id,),
            ).fetchone()
            head_count = connection.execute(
                "SELECT count(*) FROM paper_artifact_heads"
            ).fetchone()[0]
            legacy = connection.execute(
                "SELECT explainer FROM papers WHERE id='paper-1'"
            ).fetchone()[0]
            job = connection.execute(
                "SELECT status,result_json FROM processing_jobs WHERE id=?",
                (enqueue.job.id,),
            ).fetchone()
            succeeded_events = connection.execute(
                "SELECT count(*) FROM processing_job_events "
                "WHERE job_id=? AND event_type='succeeded'",
                (enqueue.job.id,),
            ).fetchone()[0]
        self.assertEqual(("running", None, None), artifact)
        self.assertEqual(0, head_count)
        self.assertEqual("legacy-stable", legacy)
        self.assertEqual(("running", None), job)
        self.assertEqual(0, succeeded_events)

    async def test_publish_rolls_back_when_each_write_point_fails(self) -> None:
        old_artifact = await self._seed_old_head()
        failure_triggers = {
            "new_artifact": (
                "generated_artifacts",
                "UPDATE",
                "OLD.id='artifact-explainer-1' AND NEW.status='ready'",
            ),
            "old_head": (
                "generated_artifacts",
                "UPDATE",
                "OLD.id='artifact-old-head' AND NEW.status='stale'",
            ),
            "head": (
                "paper_artifact_heads",
                "UPDATE",
                "NEW.artifact_id='artifact-explainer-1'",
            ),
            "legacy": (
                "papers",
                "UPDATE",
                "NEW.id='paper-1' AND NEW.explainer='# Explainer' || char(10)",
            ),
            "job": (
                "processing_jobs",
                "UPDATE",
                "NEW.id='job-explainer-1' AND NEW.status='succeeded'",
            ),
        }

        for write_point, (table, operation, condition) in failure_triggers.items():
            with self.subTest(write_point=write_point):
                application, enqueue, lease = await self._enqueue_and_claim()
                trigger_name = f"fail_explainer_{write_point}"
                with closing(sqlite3.connect(self.fixture.database_path)) as connection:
                    connection.execute(
                        f"CREATE TRIGGER {trigger_name} BEFORE {operation} ON {table} "
                        f"WHEN {condition} BEGIN "
                        f"SELECT RAISE(ABORT, 'injected-{write_point}'); END"
                    )
                    connection.commit()

                with self.assertRaises(SQLAlchemyError):
                    await application.generate_explainer(lease, self.source.id)

                with closing(sqlite3.connect(self.fixture.database_path)) as connection:
                    connection.execute(f"DROP TRIGGER {trigger_name}")
                    new_artifact = connection.execute(
                        "SELECT status,content,ready_at,stale_at FROM generated_artifacts WHERE id=?",
                        (enqueue.artifact.id,),
                    ).fetchone()
                    old_row = connection.execute(
                        "SELECT status,stale_at FROM generated_artifacts WHERE id=?",
                        (old_artifact.id,),
                    ).fetchone()
                    head = connection.execute(
                        "SELECT artifact_id FROM paper_artifact_heads "
                        "WHERE paper_id='paper-1' AND kind='explainer'"
                    ).fetchone()
                    legacy = connection.execute(
                        "SELECT explainer FROM papers WHERE id='paper-1'"
                    ).fetchone()
                    job = connection.execute(
                        "SELECT status,result_json FROM processing_jobs WHERE id=?",
                        (enqueue.job.id,),
                    ).fetchone()
                    succeeded_events = connection.execute(
                        "SELECT count(*) FROM processing_job_events "
                        "WHERE job_id=? AND event_type='succeeded'",
                        (enqueue.job.id,),
                    ).fetchone()[0]
                    connection.execute("PRAGMA foreign_keys=ON")
                    connection.execute(
                        "DELETE FROM processing_jobs WHERE id=?", (enqueue.job.id,)
                    )
                    connection.execute(
                        "DELETE FROM generated_artifacts WHERE id=?", (enqueue.artifact.id,)
                    )
                    connection.commit()
                self.assertEqual(("running", None, None, None), new_artifact)
                self.assertEqual(("ready", None), old_row)
                self.assertEqual((old_artifact.id,), head)
                self.assertEqual((old_artifact.content,), legacy)
                self.assertEqual(("running", None), job)
                self.assertEqual(0, succeeded_events)

    async def test_publish_loses_head_cas_without_overwriting_concurrent_winner(self) -> None:
        old_artifact = await self._seed_old_head()
        application, enqueue, lease = await self._enqueue_and_claim()
        competitor_content = "# Concurrent winner\n"
        competitor_sha = hashlib.sha256(competitor_content.encode("utf-8")).hexdigest()
        now_text = NOW.isoformat().replace("+00:00", "Z")

        def publish_competitor() -> None:
            with closing(sqlite3.connect(self.fixture.database_path)) as connection:
                connection.execute(
                    "INSERT INTO generated_artifacts("
                    "id,paper_id,kind,source_document_id,status,content,content_sha256,"
                    "generator_provider,generator_model,prompt_version,error_code,error_message,"
                    "created_at,updated_at,artifact_key,ready_at,stale_at"
                    ") VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        "artifact-concurrent-winner",
                        "paper-1",
                        "explainer",
                        self.source.id,
                        "ready",
                        competitor_content,
                        competitor_sha,
                        "other-llm",
                        "other-model",
                        "other-prompt-v1",
                        None,
                        None,
                        now_text,
                        now_text,
                        None,
                        now_text,
                        None,
                    ),
                )
                connection.execute(
                    "UPDATE generated_artifacts SET status='stale',stale_at=?,updated_at=? "
                    "WHERE id=?",
                    (now_text, now_text, old_artifact.id),
                )
                connection.execute(
                    "UPDATE paper_artifact_heads SET artifact_id=?,updated_at=? "
                    "WHERE paper_id='paper-1' AND kind='explainer'",
                    ("artifact-concurrent-winner", now_text),
                )
                connection.execute(
                    "UPDATE papers SET explainer=? WHERE id='paper-1'",
                    (competitor_content,),
                )
                connection.commit()

        self.provider.on_generate = publish_competitor

        try:
            with self.assertRaises(PersistenceConflictError):
                await application.generate_explainer(lease, self.source.id)
        except GenerationFailureError:
            self.fail(f"concurrent publisher setup failed: {self.provider.callback_errors!r}")

        with closing(sqlite3.connect(self.fixture.database_path)) as connection:
            attempted = connection.execute(
                "SELECT status,content,ready_at FROM generated_artifacts WHERE id=?",
                (enqueue.artifact.id,),
            ).fetchone()
            head = connection.execute(
                "SELECT artifact_id FROM paper_artifact_heads "
                "WHERE paper_id='paper-1' AND kind='explainer'"
            ).fetchone()
            legacy = connection.execute(
                "SELECT explainer FROM papers WHERE id='paper-1'"
            ).fetchone()
            job = connection.execute(
                "SELECT status,result_json FROM processing_jobs WHERE id=?",
                (enqueue.job.id,),
            ).fetchone()
        self.assertEqual(("running", None, None), attempted)
        self.assertEqual(("artifact-concurrent-winner",), head)
        self.assertEqual((competitor_content,), legacy)
        self.assertEqual(("running", None), job)

    async def test_stale_lease_token_cannot_publish_or_advance_any_projection(self) -> None:
        with closing(sqlite3.connect(self.fixture.database_path)) as connection:
            connection.execute(
                "UPDATE papers SET explainer='legacy-stable' WHERE id='paper-1'"
            )
            connection.commit()
        application, enqueue, lease = await self._enqueue_and_claim()

        def replace_lease_token() -> None:
            with closing(sqlite3.connect(self.fixture.database_path)) as connection:
                connection.execute(
                    "UPDATE processing_jobs SET lease_token='replacement-token' WHERE id=?",
                    (enqueue.job.id,),
                )
                connection.commit()

        self.provider.on_generate = replace_lease_token

        with self.assertRaises(JobLeaseLostError):
            await application.generate_explainer(lease, self.source.id)

        with closing(sqlite3.connect(self.fixture.database_path)) as connection:
            artifact = connection.execute(
                "SELECT status,content,ready_at FROM generated_artifacts WHERE id=?",
                (enqueue.artifact.id,),
            ).fetchone()
            head_count = connection.execute(
                "SELECT count(*) FROM paper_artifact_heads"
            ).fetchone()[0]
            legacy = connection.execute(
                "SELECT explainer FROM papers WHERE id='paper-1'"
            ).fetchone()[0]
            job = connection.execute(
                "SELECT status,result_json,lease_token FROM processing_jobs WHERE id=?",
                (enqueue.job.id,),
            ).fetchone()
            succeeded_events = connection.execute(
                "SELECT count(*) FROM processing_job_events "
                "WHERE job_id=? AND event_type='succeeded'",
                (enqueue.job.id,),
            ).fetchone()[0]
        self.assertEqual(("running", None, None), artifact)
        self.assertEqual(0, head_count)
        self.assertEqual("legacy-stable", legacy)
        self.assertEqual(("running", None, "replacement-token"), job)
        self.assertEqual(0, succeeded_events)

    async def test_expired_lease_cannot_publish_before_recovery_claims_it(self) -> None:
        with closing(sqlite3.connect(self.fixture.database_path)) as connection:
            connection.execute(
                "UPDATE papers SET explainer='legacy-stable' WHERE id='paper-1'"
            )
            connection.commit()
        application, enqueue, lease = await self._enqueue_and_claim()

        def expire_lease() -> None:
            expired_at = (NOW - timedelta(seconds=1)).isoformat().replace(
                "+00:00", "Z"
            )
            with closing(sqlite3.connect(self.fixture.database_path)) as connection:
                connection.execute(
                    "UPDATE processing_jobs SET lease_expires_at=? WHERE id=?",
                    (expired_at, enqueue.job.id),
                )
                connection.commit()

        self.provider.on_generate = expire_lease

        with self.assertRaises(JobLeaseLostError):
            await application.generate_explainer(lease, self.source.id)

        with closing(sqlite3.connect(self.fixture.database_path)) as connection:
            artifact = connection.execute(
                "SELECT status,content,ready_at FROM generated_artifacts WHERE id=?",
                (enqueue.artifact.id,),
            ).fetchone()
            head_count = connection.execute(
                "SELECT count(*) FROM paper_artifact_heads"
            ).fetchone()[0]
            legacy = connection.execute(
                "SELECT explainer FROM papers WHERE id='paper-1'"
            ).fetchone()[0]
            job = connection.execute(
                "SELECT status,result_json FROM processing_jobs WHERE id=?",
                (enqueue.job.id,),
            ).fetchone()
        self.assertEqual(("running", None, None), artifact)
        self.assertEqual(0, head_count)
        self.assertEqual("legacy-stable", legacy)
        self.assertEqual(("running", None), job)

    async def test_empty_generator_output_never_publishes_or_succeeds_job(self) -> None:
        self.provider.output = " \r\n\t"
        with closing(sqlite3.connect(self.fixture.database_path)) as connection:
            connection.execute(
                "UPDATE papers SET explainer='legacy-stable' WHERE id='paper-1'"
            )
            connection.commit()
        application, enqueue, lease = await self._enqueue_and_claim()

        with self.assertRaises(EmptyArtifactError):
            await application.generate_explainer(lease, self.source.id)

        with closing(sqlite3.connect(self.fixture.database_path)) as connection:
            artifact = connection.execute(
                "SELECT status,content,content_sha256,ready_at "
                "FROM generated_artifacts WHERE id=?",
                (enqueue.artifact.id,),
            ).fetchone()
            head_count = connection.execute(
                "SELECT count(*) FROM paper_artifact_heads"
            ).fetchone()[0]
            legacy = connection.execute(
                "SELECT explainer FROM papers WHERE id='paper-1'"
            ).fetchone()[0]
            job = connection.execute(
                "SELECT status,result_json FROM processing_jobs WHERE id=?",
                (enqueue.job.id,),
            ).fetchone()
        self.assertEqual(("running", None, None, None), artifact)
        self.assertEqual(0, head_count)
        self.assertEqual("legacy-stable", legacy)
        self.assertEqual(("running", None), job)

    async def test_fake_ocr_source_becomes_ready_then_drives_explainer_publication(self) -> None:
        from backend.app.application.source_documents import build_ocr_source_processor

        paper_pdf = self.fixture.database_path.parent / "paper-2.pdf"
        paper_pdf.write_bytes(b"%PDF-1.7\n% fake OCR paper\n")
        paper_pdf_sha = hashlib.sha256(paper_pdf.read_bytes()).hexdigest()
        with closing(sqlite3.connect(self.fixture.database_path)) as connection:
            connection.execute(
                "UPDATE papers SET pdf_path=?,authors=?,abstract=? WHERE id='paper-2'",
                (str(paper_pdf), '["Katherine"]', "OCR paper"),
            )
            connection.commit()
        source = SourceDocument(
            id="source-ocr-vertical-slice",
            paper_id="paper-2",
            mode="ocr",
            status="queued",
            provider="fake",
            model="fake-ocr-v1",
            pdf_sha256=paper_pdf_sha,
            options_hash=hash_canonical_json(
                {"pageBatchSize": 1, "maxConcurrency": 1}
            ),
            processing_version="fake-ocr-v1",
            created_at=NOW,
            updated_at=NOW,
        )
        source_spec = OcrJobSpecV1(
            paper_id=source.paper_id,
            source_document_id=source.id,
            provider=source.provider,
            model=source.model,
        )
        source_spec_json = encode_job_spec_v1(source_spec)
        source_key = build_source_key(
            paper_id=source.paper_id,
            mode=source.mode.value,
            provider=source.provider,
            model=source.model,
            pdf_sha256=source.pdf_sha256,
            options_hash=source.options_hash,
            processing_version=source.processing_version,
        )
        source_job = NewProcessingJob(
            id="job-ocr-vertical-slice",
            spec=source_spec,
            idempotency_key=build_source_job_key(
                source_key, hash_job_spec(source_spec_json)
            ),
            created_at=NOW,
        )
        async with SqlAlchemyUnitOfWork(self.session_factory) as work:
            _, enqueue = await work.sources.enqueue_with_job(
                source,
                source_job,
                spec_json=source_spec_json,
                spec_sha256=hash_job_spec(source_spec_json),
            )
            await work.commit()
        self.assertFalse(enqueue.deduplicated)
        async with SqlAlchemyUnitOfWork(self.session_factory) as work:
            source_lease = await work.jobs.claim_next(
                worker_id="worker-ocr-slice",
                now=NOW + timedelta(seconds=1),
                lease_seconds=60,
            )
            await work.commit()
        self.assertIsNotNone(source_lease)

        fake_ocr = FakeOcrProvider(pages={1: "# OCR vertical source\n"})
        page_reader = OnePageReader()
        checkpoints = SqlAlchemyOcrCheckpointRepository(
            self.session_factory,
            clock=lambda: NOW + timedelta(seconds=2),
        )
        processor = build_ocr_source_processor(
            create_test_ocr_registry({"fake": fake_ocr}),
            page_reader=page_reader,
            checkpoint_repository=checkpoints,
            clock=lambda: NOW + timedelta(seconds=3),
        )
        ready_source = await processor.process(
            source_lease,
            source.id,
            work_factory=lambda: SqlAlchemyUnitOfWork(self.session_factory),
        )
        self.assertEqual("ready", ready_source.status.value)
        self.assertEqual("# OCR vertical source\n", ready_source.markdown)
        self.assertEqual(1, len(fake_ocr.calls))
        self.assertEqual(1, page_reader.calls)

        application = self._application()
        artifact_enqueue = await application.enqueue_explainer(
            "paper-2",
            sourceMode="ocr",
            sourceDocumentId=source.id,
            profile="deep",
        )
        async with SqlAlchemyUnitOfWork(self.session_factory) as work:
            explainer_lease = await work.jobs.claim_next(
                worker_id="worker-explainer-slice",
                now=NOW + timedelta(seconds=4),
                lease_seconds=60,
            )
            await work.commit()
        self.assertIsNotNone(explainer_lease)
        published = await application.generate_explainer(explainer_lease, source.id)

        self.assertEqual("ready", published.status.value)
        self.assertEqual(source.id, published.source_document_id)
        self.assertEqual(
            ready_source.markdown,
            self.provider.generate_calls[-1][2],
        )
        self.assertEqual(0, self.provider.pdf_open_calls)
        with closing(sqlite3.connect(self.fixture.database_path)) as connection:
            source_job_status = connection.execute(
                "SELECT status FROM processing_jobs WHERE id=?", (source_job.id,)
            ).fetchone()
            artifact_job_status = connection.execute(
                "SELECT status FROM processing_jobs WHERE id=?",
                (artifact_enqueue.job.id,),
            ).fetchone()
            head = connection.execute(
                "SELECT artifact_id FROM paper_artifact_heads "
                "WHERE paper_id='paper-2' AND kind='explainer'"
            ).fetchone()
            legacy = connection.execute(
                "SELECT explainer FROM papers WHERE id='paper-2'"
            ).fetchone()
        self.assertEqual(("succeeded",), source_job_status)
        self.assertEqual(("succeeded",), artifact_job_status)
        self.assertEqual((published.id,), head)
        self.assertEqual((published.content,), legacy)


if __name__ == "__main__":
    unittest.main()
