from __future__ import annotations

from contextlib import closing
from datetime import datetime, timezone
import hashlib
from pathlib import Path
import sqlite3
from types import SimpleNamespace
import unittest

from backend.app.application.generated_artifacts import ArtifactGenerator
from backend.app.application.library_queries import LibraryQueries
from backend.app.application.obsidian_auto_export import ObsidianAutoExportPolicy
from backend.app.application.ports.obsidian_auto_export import ObsidianAutoExportPort
from backend.app.application.settings import SettingsService
from backend.app.domain import (
    CredentialKind,
    CredentialStatus,
    GeneratedArtifact,
    SourceDocument,
)
from backend.app.workers.obsidian import ObsidianJobService
from backend.app.workers.runtime import ObsidianStartupReconciler
from backend.app.repositories.unit_of_work import SqlAlchemyUnitOfWork
from backend.tests.support.p3_database import p3_database_fixture


NOW = datetime(2026, 8, 15, 8, 0, tzinfo=timezone.utc)
PDF_BYTES = b"%PDF-1.4\n% auto export fixture\n"
MARKDOWN = "# Source\n\nready source\n"


class _Provider:
    def __init__(self) -> None:
        self.generate_calls = 0

    def identity(self, _kind: str, profile: str = "standard") -> object:
        return SimpleNamespace(
            provider="fake-llm",
            model="fake-model",
            prompt_version=f"explainer-{profile}-v1",
        )

    def generate(self, _kind: str, _paper: object, _markdown: str, _profile: str) -> str:
        self.generate_calls += 1
        return "# Explainer\n"


class _CredentialService:
    async def status(self, kind: CredentialKind) -> CredentialStatus:
        return CredentialStatus(
            kind=kind,
            has_key=False,
            key_tail=None,
            environment_managed=False,
        )


class _Clock:
    def __init__(self, value: datetime) -> None:
        self.value = value

    def __call__(self) -> datetime:
        return self.value


class _RecordingPort(ObsidianAutoExportPort):
    def __init__(self, *, database_path: Path, fail: bool = False) -> None:
        self.database_path = database_path
        self.fail = fail
        self.calls: list[tuple[str, str, datetime, str, str]] = []

    async def on_artifact_ready(
        self, paper_id: str, artifact_id: str, committed_at: datetime
    ) -> None:
        with closing(sqlite3.connect(self.database_path)) as connection:
            artifact_status = connection.execute(
                "SELECT status FROM generated_artifacts WHERE id=?", (artifact_id,)
            ).fetchone()[0]
            job_status = connection.execute(
                "SELECT status FROM processing_jobs WHERE artifact_id=?", (artifact_id,)
            ).fetchone()[0]
        self.calls.append(
            (paper_id, artifact_id, committed_at, artifact_status, job_status)
        )
        if self.fail:
            raise RuntimeError("synthetic enqueue failure")


class ObsidianAutoExportTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self._database_context = p3_database_fixture(
            prefix="study-app-p5-auto-export-"
        )
        self.database = await self._database_context.__aenter__()
        self.root = self.database.database_path.parents[1]
        self.pdf_path = self.root / "paper-1.pdf"
        self.pdf_path.write_bytes(PDF_BYTES)
        with closing(sqlite3.connect(self.database.database_path)) as connection:
            connection.execute(
                "UPDATE papers SET pdf_path=? WHERE id='paper-1'",
                (str(self.pdf_path),),
            )
            connection.commit()
        self.work_factory = lambda: SqlAlchemyUnitOfWork(self.database.session_factory)
        self.source = SourceDocument(
            id="source-auto-export",
            paper_id="paper-1",
            mode="ocr",
            status="ready",
            provider="fake-ocr",
            model="fake-ocr-v1",
            pdf_sha256=hashlib.sha256(PDF_BYTES).hexdigest(),
            options_hash=hashlib.sha256(b"{}").hexdigest(),
            processing_version="fake-ocr-processing-v1",
            created_at=NOW,
            updated_at=NOW,
            markdown=MARKDOWN,
            content_sha256=hashlib.sha256(MARKDOWN.encode("utf-8")).hexdigest(),
            page_count=1,
        )
        async with self.work_factory() as work:
            await work.sources.add(self.source)
            await work.commit()
        self.provider = _Provider()
        self.settings = SettingsService(
            settings_path=self.root / "settings.json",
            root=self.root,
            credential_service=_CredentialService(),
            environment_snapshot={},
            default_dirs={
                "pdfDir": self.root / "pdfs",
                "explainerDir": self.root / "explainers",
                "translationDir": self.root / "translations",
            },
        )

    async def asyncTearDown(self) -> None:
        await self._database_context.__aexit__(None, None, None)

    async def _enqueue_and_claim(self, port: ObsidianAutoExportPort):
        application = ArtifactGenerator(
            self.work_factory,
            self.provider,
            clock=lambda: NOW,
            artifact_id_factory=lambda: "artifact-auto-export",
            job_id_factory=lambda: "job-auto-export",
            auto_export=port,
        )
        enqueue = await application.enqueue_explainer(
            "paper-1",
            sourceMode="ocr",
            sourceDocumentId=self.source.id,
            profile="standard",
        )
        async with self.work_factory() as work:
            lease = await work.jobs.claim_next(
                worker_id="auto-export-worker",
                now=NOW,
                lease_seconds=3600,
            )
            await work.commit()
        self.assertIsNotNone(lease)
        return application, enqueue, lease

    async def test_artifact_ready_notifies_only_after_commit(self) -> None:
        port = _RecordingPort(database_path=self.database.database_path)
        application, enqueue, lease = await self._enqueue_and_claim(port)

        published = await application.generate_explainer(lease, self.source.id)

        self.assertEqual(enqueue.artifact.id, published.id)
        self.assertEqual(
            [
                (
                    "paper-1",
                    published.id,
                    NOW,
                    "ready",
                    "succeeded",
                )
            ],
            port.calls,
        )

    async def test_enqueue_failure_never_changes_generation_success(self) -> None:
        port = _RecordingPort(
            database_path=self.database.database_path,
            fail=True,
        )
        application, _enqueue, lease = await self._enqueue_and_claim(port)

        published = await application.generate_explainer(lease, self.source.id)

        self.assertEqual("ready", published.status.value)
        self.assertEqual(1, self.provider.generate_calls)
        self.assertEqual(1, len(port.calls))
        with closing(sqlite3.connect(self.database.database_path)) as connection:
            row = connection.execute(
                "SELECT a.status,j.status,a.content FROM generated_artifacts a "
                "JOIN processing_jobs j ON j.artifact_id=a.id WHERE a.id=?",
                (published.id,),
            ).fetchone()
        self.assertEqual(("ready", "succeeded", "# Explainer\n"), row)

    async def test_p3_artifact_ready_notifies_only_after_commit(self) -> None:
        from backend.app.application.document_artifacts import DocumentArtifactService
        from backend.app.domain.context import ChunkingSpec
        from backend.app.repositories.translation_checkpoints import (
            SqlAlchemyTranslationCheckpointRepository,
        )
        from backend.tests.support.p3_database import p3_translation_fixture

        async with p3_translation_fixture(
            prefix="study-app-p5-auto-export-p3-",
            markdown="translation auto-export sentinel\n",
            spec=ChunkingSpec(target_tokens=8, hard_cap_tokens=8),
            now=NOW,
        ) as fixture:
            class TranslationProvider:
                provider_id = fixture.provider
                model_id = fixture.model
                prompt_version = fixture.prompt_version

                async def translate(self, request):
                    return f"translated-{request.sequence}\n"

            port = _RecordingPort(database_path=fixture.database_path)
            service = DocumentArtifactService(
                fixture.unit_of_work_factory,
                context_builder=fixture.builder,
                translation_provider=TranslationProvider(),
                checkpoint_repository=SqlAlchemyTranslationCheckpointRepository(
                    fixture.session_factory,
                    clock=lambda: NOW,
                ),
                clock=lambda: NOW,
                auto_export=port,
            )

            published = await service.run(fixture.lease, fixture.artifact_id)

            self.assertEqual(
                [
                    (
                        "paper-1",
                        published.id,
                        NOW,
                        "ready",
                        "succeeded",
                    )
                ],
                port.calls,
            )

    async def test_default_off_has_zero_hook_queue_materialization_and_ocr(self) -> None:
        calls: list[tuple[str, bool]] = []

        class QueueSpy:
            async def enqueue_export(self, paper_id: str, *, dry_run: bool):
                calls.append((paper_id, dry_run))

        clock = _Clock(NOW)
        policy = ObsidianAutoExportPolicy(
            self.work_factory,
            settings_service=self.settings,
            job_service=QueueSpy(),
            clock=clock,
            debounce_seconds=1,
        )
        artifact = GeneratedArtifact(
            id="artifact-policy-off",
            paper_id="paper-1",
            kind="explainer",
            source_document_id=self.source.id,
            status="ready",
            generator_provider="fake",
            generator_model="fake-model",
            prompt_version="fake-v1",
            created_at=NOW,
            updated_at=NOW,
            content="# Ready\n",
            content_sha256=hashlib.sha256(b"# Ready\n").hexdigest(),
        )
        async with self.work_factory() as work:
            await work.artifacts.add(artifact)
            await work.commit()

        for patch in (
            {},
            {"obsidianEnabled": True, "obsidianAutoExport": False},
            {"obsidianEnabled": False, "obsidianAutoExport": True},
        ):
            if patch:
                await self.settings.update(patch)
            await policy.on_artifact_ready("paper-1", artifact.id, NOW)
            clock.value = datetime(2026, 8, 15, 8, 0, 2, tzinfo=timezone.utc)
            await policy.flush_due()

        self.assertEqual([], calls)
        self.assertEqual(0, policy.pending_count)

    async def test_auto_export_is_optional_coalesced_and_idempotent_per_paper(self) -> None:
        from backend.app.domain.processing import decode_job_spec_v1

        await self.settings.update(
            {
                "obsidianEnabled": True,
                "obsidianAutoExport": True,
                "obsidianVaultPath": str(self.root / "vault"),
            }
        )
        source_two = SourceDocument(
            id="source-auto-export-two",
            paper_id="paper-2",
            mode="ocr",
            status="ready",
            provider="fake-ocr",
            model="fake-ocr-v1",
            pdf_sha256="b" * 64,
            options_hash="c" * 64,
            processing_version="fake-ocr-processing-v1",
            created_at=NOW,
            updated_at=NOW,
            markdown=MARKDOWN,
            content_sha256=hashlib.sha256(MARKDOWN.encode("utf-8")).hexdigest(),
            page_count=1,
        )
        artifacts = (
            GeneratedArtifact(
                id="artifact-policy-old",
                paper_id="paper-1",
                kind="explainer",
                source_document_id=self.source.id,
                status="ready",
                generator_provider="fake",
                generator_model="fake-model",
                prompt_version="old-v1",
                created_at=NOW,
                updated_at=NOW,
                content="# Old\n",
                content_sha256=hashlib.sha256(b"# Old\n").hexdigest(),
            ),
            GeneratedArtifact(
                id="artifact-policy-latest",
                paper_id="paper-1",
                kind="explainer",
                source_document_id=self.source.id,
                status="ready",
                generator_provider="fake",
                generator_model="fake-model",
                prompt_version="latest-v1",
                created_at=NOW,
                updated_at=NOW,
                content="# Latest\n",
                content_sha256=hashlib.sha256(b"# Latest\n").hexdigest(),
            ),
            GeneratedArtifact(
                id="artifact-policy-paper-two",
                paper_id="paper-2",
                kind="explainer",
                source_document_id=source_two.id,
                status="ready",
                generator_provider="fake",
                generator_model="fake-model",
                prompt_version="paper-two-v1",
                created_at=NOW,
                updated_at=NOW,
                content="# Two\n",
                content_sha256=hashlib.sha256(b"# Two\n").hexdigest(),
            ),
        )
        async with self.work_factory() as work:
            await work.sources.add(source_two)
            for artifact in artifacts:
                await work.artifacts.add(artifact)
            self.assertTrue(
                await work.artifacts.publish_head(
                    paper_id="paper-1",
                    kind="explainer",
                    artifact_id=artifacts[1].id,
                    expected_artifact_id=None,
                    updated_at=NOW,
                )
            )
            self.assertTrue(
                await work.artifacts.publish_head(
                    paper_id="paper-2",
                    kind="explainer",
                    artifact_id=artifacts[2].id,
                    expected_artifact_id=None,
                    updated_at=NOW,
                )
            )
            await work.commit()
        queries = LibraryQueries(
            self.work_factory,
            pdf_files=SimpleNamespace(has_pdf=lambda _paper: False),
        )
        jobs = ObsidianJobService(
            self.work_factory,
            settings_service=self.settings,
            library_queries=queries,
            clock=lambda: NOW,
            access_tester=lambda: True,
        )
        clock = _Clock(NOW)
        policy = ObsidianAutoExportPolicy(
            self.work_factory,
            settings_service=self.settings,
            job_service=jobs,
            clock=clock,
            debounce_seconds=5,
        )

        await policy.on_artifact_ready("paper-1", artifacts[0].id, NOW)
        await policy.on_artifact_ready("paper-1", artifacts[1].id, NOW)
        await policy.on_artifact_ready("paper-2", artifacts[2].id, NOW)
        await policy.flush_due()
        self.assertEqual(0, self._job_count())
        clock.value = datetime(2026, 8, 15, 8, 0, 6, tzinfo=timezone.utc)
        await policy.flush_due()
        self.assertEqual(2, self._job_count())
        with closing(sqlite3.connect(self.database.database_path)) as connection:
            rows = tuple(
                connection.execute(
                    "SELECT paper_id,spec_json FROM processing_jobs "
                    "WHERE job_type='obsidian_export' ORDER BY paper_id"
                )
            )
        paper_one_spec = decode_job_spec_v1(rows[0][1])
        self.assertFalse(paper_one_spec.dry_run)
        self.assertFalse(paper_one_spec.apply_cleanup)
        self.assertIsNone(paper_one_spec.cleanup_plan_sha)
        self.assertEqual(
            artifacts[1].id,
            next(
                head["artifactId"]
                for head in paper_one_spec.library_snapshot["items"][0]["artifactHeads"]
                if head["kind"] == "explainer"
            ),
        )

        await policy.on_artifact_ready("paper-1", artifacts[1].id, clock.value)
        clock.value = datetime(2026, 8, 15, 8, 0, 12, tzinfo=timezone.utc)
        await policy.flush_due()
        self.assertEqual(2, self._job_count())

    async def test_startup_reconciliation_enqueues_missing_latest_snapshots_without_generating(
        self,
    ) -> None:
        await self.settings.update(
            {
                "obsidianEnabled": True,
                "obsidianAutoExport": True,
                "obsidianVaultPath": str(self.root / "vault"),
            }
        )
        artifact = GeneratedArtifact(
            id="artifact-reconciliation",
            paper_id="paper-1",
            kind="explainer",
            source_document_id=self.source.id,
            status="ready",
            generator_provider="fake",
            generator_model="fake-model",
            prompt_version="reconciliation-v1",
            created_at=NOW,
            updated_at=NOW,
            content="# Reconciled\n",
            content_sha256=hashlib.sha256(b"# Reconciled\n").hexdigest(),
        )
        async with self.work_factory() as work:
            await work.artifacts.add(artifact)
            self.assertTrue(
                await work.artifacts.publish_head(
                    paper_id="paper-1",
                    kind="explainer",
                    artifact_id=artifact.id,
                    expected_artifact_id=None,
                    updated_at=NOW,
                )
            )
            await work.commit()
        queries = LibraryQueries(
            self.work_factory,
            pdf_files=SimpleNamespace(has_pdf=lambda _paper: False),
        )
        jobs = ObsidianJobService(
            self.work_factory,
            settings_service=self.settings,
            library_queries=queries,
            clock=lambda: NOW,
            access_tester=lambda: True,
        )
        reconciler = ObsidianStartupReconciler(
            self.work_factory,
            settings_service=self.settings,
            library_queries=queries,
            job_service=jobs,
            batch_size=1,
        )

        self.assertEqual(1, await reconciler.run())
        self.assertEqual(1, self._job_count())
        self.assertEqual(0, await reconciler.run())
        self.assertEqual(1, self._job_count())

    def _job_count(self) -> int:
        with closing(sqlite3.connect(self.database.database_path)) as connection:
            return int(
                connection.execute(
                    "SELECT count(*) FROM processing_jobs WHERE job_type='obsidian_export'"
                ).fetchone()[0]
            )


if __name__ == "__main__":
    unittest.main()
