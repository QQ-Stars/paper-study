from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
import unittest

from sqlalchemy import text

from backend.app.application.library_queries import LibraryQueries
from backend.app.application.settings import SettingsService
from backend.app.domain import CredentialKind, CredentialStatus, GeneratedArtifact, SourceDocument
from backend.app.providers.pdf_files import PdfFiles
from backend.app.repositories.unit_of_work import SqlAlchemyUnitOfWork
from backend.app.workers.obsidian import ObsidianJobHandler, ObsidianJobService
from backend.app.workers.processing_worker import ProcessingWorker
from backend.tests.support.p3_database import p3_database_fixture


NOW = datetime(2026, 8, 15, 12, 0, tzinfo=timezone.utc)


class _CredentialService:
    async def status(self, kind: CredentialKind) -> CredentialStatus:
        return CredentialStatus(
            kind=kind,
            has_key=False,
            key_tail=None,
            environment_managed=False,
        )


class ObsidianRuntimeExporterTests(unittest.IsolatedAsyncioTestCase):
    async def test_frozen_job_spec_runs_the_real_projection_stack(self) -> None:
        from backend.app.application.obsidian_exporter import ObsidianSpecExporter

        async with p3_database_fixture(
            prefix="study-app-p5-obsidian-runtime-"
        ) as fixture:
            root = fixture.database_path.parents[1]
            vault = root / "vault"
            pdf_directory = root / "pdfs"
            vault.mkdir()
            pdf_directory.mkdir()
            pdf_bytes = b"%PDF-1.7\nruntime-export-tail\n%%EOF\n"
            (pdf_directory / "paper-1.pdf").write_bytes(pdf_bytes)
            pdf_files = PdfFiles(root=root, default_directory=pdf_directory)
            work_factory = lambda: SqlAlchemyUnitOfWork(fixture.session_factory)

            source_markdown = "# Source\n\nsource tail sentinel\n"
            source_sha = hashlib.sha256(source_markdown.encode("utf-8")).hexdigest()
            source = SourceDocument(
                id="source-obsidian-runtime",
                paper_id="paper-1",
                mode="native",
                status="ready",
                provider="local",
                model="native-v1",
                pdf_sha256=hashlib.sha256(pdf_bytes).hexdigest(),
                options_hash="a" * 64,
                processing_version="native-v1",
                created_at=NOW,
                updated_at=NOW,
                markdown=source_markdown,
                content_sha256=source_sha,
                page_count=1,
            )
            artifacts = []
            for kind, content in (
                ("explainer", "# Explainer\n\nexplainer tail sentinel\n"),
                ("translation", "# Translation\n\ntranslation tail sentinel\n"),
            ):
                artifacts.append(
                    GeneratedArtifact(
                        id=f"artifact-{kind}-runtime",
                        paper_id="paper-1",
                        kind=kind,
                        source_document_id=source.id,
                        status="ready",
                        generator_provider="fake",
                        generator_model="fake-model",
                        prompt_version=f"{kind}-v1",
                        created_at=NOW,
                        updated_at=NOW,
                        content=content,
                        content_sha256=hashlib.sha256(content.encode("utf-8")).hexdigest(),
                    )
                )
            async with work_factory() as work:
                await work.sources.add(source)
                for artifact in artifacts:
                    await work.artifacts.add(artifact)
                await work.papers.set_note(
                    "paper-1",
                    "personal note tail sentinel\n",
                    updated_at=NOW.isoformat(),
                )
                await work.commit()
            async with fixture.session_factory() as session:
                for artifact in artifacts:
                    await session.execute(
                        text(
                            "INSERT INTO paper_artifact_heads("
                            "paper_id,kind,artifact_id,updated_at) "
                            "VALUES(:paper_id,:kind,:artifact_id,:updated_at)"
                        ),
                        {
                            "paper_id": "paper-1",
                            "kind": artifact.kind.value,
                            "artifact_id": artifact.id,
                            "updated_at": NOW.isoformat(timespec="microseconds").replace(
                                "+00:00", "Z"
                            ),
                        },
                    )
                await session.commit()

            settings = SettingsService(
                settings_path=root / "settings.json",
                root=root,
                credential_service=_CredentialService(),
                environment_snapshot={},
                default_dirs={
                    "pdfDir": pdf_directory,
                    "explainerDir": root / "explainers",
                    "translationDir": root / "translations",
                },
            )
            await settings.update(
                {
                    "obsidianEnabled": True,
                    "obsidianVaultPath": str(vault),
                    "obsidianRootFolder": "Research",
                    "obsidianPdfMode": "copy",
                }
            )
            queries = LibraryQueries(work_factory, pdf_files=pdf_files)
            service = ObsidianJobService(
                work_factory,
                settings_service=settings,
                library_queries=queries,
                clock=lambda: NOW,
            )
            enqueued = await service.enqueue_export("paper-1", dry_run=False)
            exporter = ObsidianSpecExporter(
                work_factory,
                fixture.session_factory,
                pdf_files=pdf_files,
                clock=lambda: NOW,
            )
            worker = ProcessingWorker(
                work_factory,
                handlers={
                    "obsidian_export": ObsidianJobHandler(service, exporter=exporter)
                },
                worker_id="obsidian-runtime-worker",
                clock=lambda: NOW,
            )

            self.assertTrue(await worker.run_once())

            expected = {
                "Papers/paper-1.md",
                "Sources/paper-1.md",
                "Explainers/paper-1.md",
                "Translations/paper-1.md",
                "Notes/paper-1.md",
                "Attachments/PDF/paper-1.pdf",
            }
            projected = {
                path.relative_to(vault / "Research").as_posix()
                for path in (vault / "Research").rglob("*")
                if path.is_file() and ".paper-study" not in path.parts
            }
            self.assertEqual(expected, projected)
            self.assertEqual(
                pdf_bytes,
                (vault / "Research" / "Attachments" / "PDF" / "paper-1.pdf").read_bytes(),
            )
            paper_markdown = (
                vault / "Research" / "Papers" / "paper-1.md"
            ).read_text(encoding="utf-8")
            self.assertIn(
                "[Open PDF](../Attachments/PDF/paper-1.pdf)",
                paper_markdown,
            )
            async with work_factory() as work:
                stored = await work.jobs.get(enqueued.job.id)
            self.assertEqual("succeeded", stored.status.value)
            async with fixture.session_factory() as session:
                result_json = (
                    await session.execute(
                        text(
                            "SELECT result_json FROM processing_jobs WHERE id=:job_id"
                        ),
                        {"job_id": enqueued.job.id},
                    )
                ).scalar_one()
            counts = json.loads(result_json)
            self.assertEqual(
                {
                    "exported",
                    "unchanged",
                    "conflicts",
                    "errors",
                    "skipped",
                    "userManaged",
                    "orphaned",
                    "deleted",
                },
                set(counts),
            )
            self.assertEqual(6, counts["exported"])
            self.assertEqual(0, sum(value for key, value in counts.items() if key != "exported"))


if __name__ == "__main__":
    unittest.main()
