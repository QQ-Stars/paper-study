from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import os
import unittest

if os.name == "nt" and hasattr(os, "add_dll_directory") and os.environ.get("P3_SQLITE_DLL_DIR"):
    _SQLITE_DLL_HANDLE = os.add_dll_directory(os.environ["P3_SQLITE_DLL_DIR"])


NOW = datetime(2026, 8, 13, 9, 0, tzinfo=timezone.utc)


class SourceFreshnessCascadeTests(unittest.IsolatedAsyncioTestCase):
    async def test_source_processor_ready_publication_atomically_activates_new_identity(self) -> None:
        from contextlib import closing
        from datetime import timedelta
        import hashlib
        from pathlib import Path
        import sqlite3

        from backend.app.application.ports import ExtractedSource
        from backend.app.application.source_documents import (
            SourceDocumentProcessor,
            build_native_source_processor,
        )
        from backend.app.domain import SourceDocument
        from backend.app.domain.processing import (
            NewProcessingJob,
            SourceMaterializeJobSpecV1,
            build_source_job_key,
            build_source_key,
            encode_job_spec_v1,
            hash_job_spec,
        )
        from backend.app.repositories.unit_of_work import SqlAlchemyUnitOfWork
        from backend.tests.support.p3_database import p3_freshness_fixture

        class NativeExtractor:
            provider = "local"
            model = "pymupdf4llm-pymupdf-v2"
            processing_version = "native-v2"

            def extract(self, _path: Path) -> ExtractedSource:
                markdown = "# Active source\n\nreplacement identity.\n"
                return ExtractedSource(
                    markdown=markdown,
                    content_sha256=hashlib.sha256(markdown.encode("utf-8")).hexdigest(),
                    page_count=1,
                    provider=self.provider,
                    model=self.model,
                    processing_version=self.processing_version,
                )

        async with p3_freshness_fixture(
            prefix="study-app-p3-freshness-worker-activation-",
            now=NOW,
        ) as fixture:
            pdf_bytes = b"source activation worker fixture"
            pdf_path = fixture.database_path.parent / "active-source.pdf"
            pdf_path.write_bytes(pdf_bytes)
            pdf_sha = hashlib.sha256(pdf_bytes).hexdigest()
            source = SourceDocument(
                id="src_worker_active_native",
                paper_id="paper-1",
                mode="native",
                status="queued",
                provider=NativeExtractor.provider,
                model=NativeExtractor.model,
                pdf_sha256=pdf_sha,
                options_hash="d" * 64,
                processing_version=NativeExtractor.processing_version,
                created_at=NOW - timedelta(minutes=1),
                updated_at=NOW - timedelta(minutes=1),
            )
            spec = SourceMaterializeJobSpecV1(
                paper_id=source.paper_id,
                source_document_id=source.id,
                processing_version=source.processing_version,
            )
            raw_spec = encode_job_spec_v1(spec)
            job = NewProcessingJob(
                id="job_worker_active_native",
                spec=spec,
                idempotency_key=build_source_job_key(
                    build_source_key(
                        paper_id=source.paper_id,
                        mode=source.mode.value,
                        provider=source.provider,
                        model=source.model,
                        pdf_sha256=source.pdf_sha256,
                        options_hash=source.options_hash,
                        processing_version=source.processing_version,
                    ),
                    hash_job_spec(raw_spec),
                ),
                created_at=source.created_at,
            )
            async with fixture.unit_of_work_factory() as work:
                await work.sources.add(source)
                await work.jobs.insert_with_spec(
                    job,
                    spec_json=raw_spec,
                    spec_sha256=hash_job_spec(raw_spec),
                )
                await work.commit()
            with closing(sqlite3.connect(fixture.database_path)) as connection:
                connection.execute(
                    "UPDATE papers SET pdf_path=? WHERE id='paper-1'",
                    (str(pdf_path),),
                )
                connection.commit()
            async with fixture.unit_of_work_factory() as work:
                lease = await work.jobs.claim_next(
                    worker_id="source-activation-worker",
                    now=NOW,
                    lease_seconds=60,
                )
                await work.commit()
            self.assertIsNotNone(lease)
            assert lease is not None
            self.assertEqual(job.id, lease.job.id)

            processor = SourceDocumentProcessor(
                lambda: SqlAlchemyUnitOfWork(fixture.session_factory),
                native_factory=lambda: build_native_source_processor(
                    NativeExtractor(),
                    clock=lambda: NOW,
                ),
                ocr_factory=lambda: (_ for _ in ()).throw(
                    AssertionError("native source must not construct OCR")
                ),
                clock=lambda: NOW,
            )
            ready = await processor.process(lease, source.id)

            self.assertEqual("ready", ready.status.value)
            with closing(sqlite3.connect(fixture.database_path)) as connection:
                source_rows = dict(
                    connection.execute(
                        "SELECT id,status FROM document_sources WHERE id IN (?,?)",
                        (fixture.source_id, source.id),
                    ).fetchall()
                )
                artifact_rows = dict(
                    connection.execute(
                        "SELECT id,status FROM generated_artifacts "
                        "WHERE source_document_id=?",
                        (fixture.source_id,),
                    ).fetchall()
                )
                chunk_statuses = connection.execute(
                    "SELECT DISTINCT status FROM document_chunks "
                    "WHERE source_document_id=?",
                    (fixture.source_id,),
                ).fetchall()
                embedding_statuses = connection.execute(
                    "SELECT DISTINCT status FROM document_chunk_embeddings "
                    "WHERE source_document_id=?",
                    (fixture.source_id,),
                ).fetchall()
                head_count = connection.execute(
                    "SELECT count(*) FROM paper_artifact_heads "
                    "WHERE paper_id='paper-1' AND kind='explainer'"
                ).fetchone()[0]
                job_rows = dict(
                    connection.execute(
                        "SELECT id,status FROM processing_jobs "
                        "WHERE id IN (?,?,?)",
                        (job.id, fixture.queued_job_id, fixture.running_job_id),
                    ).fetchall()
                )
            self.assertEqual("stale", source_rows[fixture.source_id])
            self.assertEqual("ready", source_rows[source.id])
            self.assertEqual("stale", artifact_rows[fixture.artifact_ids[0]])
            self.assertEqual("cancelled", artifact_rows[fixture.artifact_ids[1]])
            self.assertEqual([("stale",)], chunk_statuses)
            self.assertEqual([("stale",)], embedding_statuses)
            self.assertEqual(0, head_count)
            self.assertEqual("succeeded", job_rows[job.id])
            self.assertEqual("cancelled", job_rows[fixture.queued_job_id])
            self.assertEqual("running", job_rows[fixture.running_job_id])

    async def test_pdf_sha_change_atomically_stales_dependency_graph_and_preserves_legacy(self) -> None:
        from sqlalchemy import text

        from backend.app.application.source_freshness import SourceFreshnessService
        from backend.tests.support.p3_database import p3_freshness_fixture

        async with p3_freshness_fixture(
            prefix="study-app-p3-freshness-pdf-",
            now=NOW,
        ) as fixture:
            async with fixture.session_factory() as session:
                legacy_before = (
                    await session.execute(
                        text(
                            "SELECT p.explainer,t.content "
                            "FROM papers p LEFT JOIN translations t ON t.paper_id=p.id "
                            "WHERE p.id='paper-1'"
                        )
                    )
                ).one()

            result = await SourceFreshnessService(
                fixture.unit_of_work_factory
            ).reconcile_pdf(
                "paper-1",
                "c" * 64,
                now=NOW,
            )

            self.assertEqual((fixture.source_id,), result.source_ids)
            self.assertEqual(
                {fixture.artifact_ids[0], fixture.artifact_ids[3]},
                set(result.artifact_ids),
            )
            self.assertEqual(set(fixture.chunk_ids), set(result.chunk_ids))
            self.assertEqual(set(fixture.embedding_ids), set(result.embedding_ids))
            self.assertEqual(("paper-1:explainer",), result.removed_head_keys)
            self.assertEqual((fixture.queued_job_id,), result.cancelled_job_ids)
            self.assertEqual((fixture.running_job_id,), result.cancel_requested_job_ids)

            async with fixture.session_factory() as session:
                source_status = (
                    await session.execute(
                        text("SELECT status,stale_at FROM document_sources WHERE id=:id"),
                        {"id": fixture.source_id},
                    )
                ).one()
                artifacts = (
                    await session.execute(
                        text(
                            "SELECT id,status,stale_at FROM generated_artifacts "
                            "WHERE source_document_id=:id ORDER BY id"
                        ),
                        {"id": fixture.source_id},
                    )
                ).all()
                chunks = (
                    await session.execute(
                        text(
                            "SELECT id,status,stale_at FROM document_chunks "
                            "WHERE source_document_id=:id ORDER BY sequence"
                        ),
                        {"id": fixture.source_id},
                    )
                ).all()
                embeddings = (
                    await session.execute(
                        text(
                            "SELECT id,status,stale_at FROM document_chunk_embeddings "
                            "WHERE source_document_id=:id ORDER BY id"
                        ),
                        {"id": fixture.source_id},
                    )
                ).all()
                heads = (
                    await session.execute(
                        text("SELECT count(*) FROM paper_artifact_heads WHERE paper_id='paper-1'")
                    )
                ).scalar_one()
                jobs = dict(
                    (
                        row.id,
                        (row.status, row.cancel_requested_at, row.cancelled_at),
                    )
                    for row in (
                        await session.execute(
                            text(
                                "SELECT id,status,cancel_requested_at,cancelled_at "
                                "FROM processing_jobs WHERE id IN (:queued,:running,:terminal)"
                            ),
                            {
                                "queued": fixture.queued_job_id,
                                "running": fixture.running_job_id,
                                "terminal": fixture.terminal_job_id,
                            },
                        )
                    ).all()
                )
                legacy_after = (
                    await session.execute(
                        text(
                            "SELECT p.explainer,t.content "
                            "FROM papers p LEFT JOIN translations t ON t.paper_id=p.id "
                            "WHERE p.id='paper-1'"
                        )
                    )
                ).one()

            self.assertEqual("stale", source_status.status)
            self.assertIsNotNone(source_status.stale_at)
            artifact_states = {
                row.id: (row.status, row.stale_at) for row in artifacts
            }
            self.assertEqual("stale", artifact_states[fixture.artifact_ids[0]][0])
            self.assertIsNotNone(artifact_states[fixture.artifact_ids[0]][1])
            self.assertEqual("cancelled", artifact_states[fixture.artifact_ids[1]][0])
            self.assertIsNone(artifact_states[fixture.artifact_ids[1]][1])
            self.assertEqual("running", artifact_states[fixture.artifact_ids[2]][0])
            self.assertEqual("stale", artifact_states[fixture.artifact_ids[3]][0])
            self.assertIsNotNone(artifact_states[fixture.artifact_ids[3]][1])
            self.assertTrue(all(row.status == "stale" and row.stale_at for row in chunks))
            self.assertTrue(all(row.status == "stale" and row.stale_at for row in embeddings))
            self.assertEqual(0, heads)
            self.assertEqual("cancelled", jobs[fixture.queued_job_id][0])
            self.assertIsNotNone(jobs[fixture.queued_job_id][2])
            self.assertEqual("running", jobs[fixture.running_job_id][0])
            self.assertIsNotNone(jobs[fixture.running_job_id][1])
            self.assertEqual(("succeeded", None, None), jobs[fixture.terminal_job_id])
            self.assertEqual(legacy_before, legacy_after)

    async def test_activate_source_stales_only_different_same_mode_identity_and_is_idempotent(self) -> None:
        import hashlib

        from sqlalchemy import text

        from backend.app.application.source_freshness import SourceFreshnessService
        from backend.app.domain import SourceDocument
        from backend.tests.support.p3_database import p3_freshness_fixture

        active_markdown = "# Active native\n\nnew identity.\n"
        ocr_markdown = "# Active OCR\n\ncoexisting mode.\n"
        async with p3_freshness_fixture(
            prefix="study-app-p3-freshness-activate-",
            now=NOW,
        ) as fixture:
            async with fixture.unit_of_work_factory() as work:
                await work.sources.add(
                    SourceDocument(
                        id="src_active_native",
                        paper_id="paper-1",
                        mode="native",
                        status="ready",
                        provider="local-v2",
                        model="pymupdf4llm-pymupdf-v2",
                        pdf_sha256="a" * 64,
                        options_hash="d" * 64,
                        processing_version="native-v2",
                        created_at=NOW,
                        updated_at=NOW,
                        markdown=active_markdown,
                        content_sha256=hashlib.sha256(
                            active_markdown.encode("utf-8")
                        ).hexdigest(),
                        page_count=1,
                    )
                )
                await work.sources.add(
                    SourceDocument(
                        id="src_active_ocr",
                        paper_id="paper-1",
                        mode="ocr",
                        status="ready",
                        provider="fake-ocr",
                        model="fake-ocr-v1",
                        pdf_sha256="a" * 64,
                        options_hash="e" * 64,
                        processing_version="ocr-v1",
                        created_at=NOW,
                        updated_at=NOW,
                        markdown=ocr_markdown,
                        content_sha256=hashlib.sha256(
                            ocr_markdown.encode("utf-8")
                        ).hexdigest(),
                        page_count=1,
                    )
                )
                await work.commit()

            service = SourceFreshnessService(fixture.unit_of_work_factory)
            result = await service.activate_source("src_active_native", now=NOW)
            repeated = await service.activate_source("src_active_native", now=NOW)

            self.assertEqual((fixture.source_id,), result.source_ids)
            self.assertEqual((), repeated.source_ids)
            async with fixture.session_factory() as session:
                statuses = dict(
                    (
                        await session.execute(
                            text(
                                "SELECT id,status FROM document_sources "
                                "WHERE id IN (:old,:native,:ocr) ORDER BY id"
                            ),
                            {
                                "old": fixture.source_id,
                                "native": "src_active_native",
                                "ocr": "src_active_ocr",
                            },
                        )
                    ).all()
                )
            self.assertEqual("stale", statuses[fixture.source_id])
            self.assertEqual("ready", statuses["src_active_native"])
            self.assertEqual("ready", statuses["src_active_ocr"])

    async def test_activate_source_detects_each_provider_model_options_or_processing_drift(self) -> None:
        import hashlib

        from sqlalchemy import text

        from backend.app.application.source_freshness import SourceFreshnessService
        from backend.app.domain import SourceDocument
        from backend.app.repositories.unit_of_work import SqlAlchemyUnitOfWork
        from backend.tests.support.p3_database import p3_database_fixture

        base = {
            "paper_id": "paper-1",
            "mode": "native",
            "provider": "local",
            "model": "pymupdf4llm-pymupdf",
            "pdf_sha256": "a" * 64,
            "options_hash": "b" * 64,
            "processing_version": "native-v2",
        }
        variants = {
            "src_provider_drift": {"provider": "local-old"},
            "src_model_drift": {"model": "pymupdf-old"},
            "src_options_drift": {"options_hash": "c" * 64},
            "src_processing_drift": {"processing_version": "native-v1"},
        }
        async with p3_database_fixture(
            prefix="study-app-p3-freshness-identities-"
        ) as fixture:
            factory = lambda: SqlAlchemyUnitOfWork(fixture.session_factory)
            async with factory() as work:
                for source_id, overrides in {
                    "src_identity_active": {},
                    **variants,
                }.items():
                    markdown = f"# {source_id}\n\nidentity fixture.\n"
                    await work.sources.add(
                        SourceDocument(
                            id=source_id,
                            status="ready",
                            created_at=NOW,
                            updated_at=NOW,
                            markdown=markdown,
                            content_sha256=hashlib.sha256(
                                markdown.encode("utf-8")
                            ).hexdigest(),
                            page_count=1,
                            **{**base, **overrides},
                        )
                    )
                await work.commit()

            result = await SourceFreshnessService(factory).activate_source(
                "src_identity_active",
                now=NOW,
            )

            self.assertEqual(tuple(sorted(variants)), result.source_ids)
            async with fixture.session_factory() as session:
                statuses = dict(
                    (
                        await session.execute(
                            text(
                                "SELECT id,status FROM document_sources "
                                "WHERE id LIKE 'src_%drift' OR id='src_identity_active'"
                            )
                        )
                    ).all()
                )
            self.assertEqual("ready", statuses["src_identity_active"])
            self.assertTrue(
                all(statuses[source_id] == "stale" for source_id in variants)
            )

    async def test_cascade_rolls_back_when_any_dependency_write_fails(self) -> None:
        from sqlalchemy import text

        from backend.app.application.source_freshness import SourceFreshnessService
        from backend.app.infrastructure.database_backup import inspect_database
        from backend.tests.support.p3_database import p3_freshness_fixture

        failure_points = (
            ("source", "BEFORE UPDATE ON document_sources", "OLD.id='src_freshness'"),
            ("ready-artifact", "BEFORE UPDATE ON generated_artifacts", "OLD.status='ready'"),
            ("chunk", "BEFORE UPDATE ON document_chunks", "OLD.source_document_id='src_freshness'"),
            ("embedding", "BEFORE UPDATE ON document_chunk_embeddings", "OLD.source_document_id='src_freshness'"),
            ("head", "BEFORE DELETE ON paper_artifact_heads", "OLD.paper_id='paper-1'"),
            ("queued-artifact", "BEFORE UPDATE ON generated_artifacts", "OLD.status='queued'"),
            ("queued-job", "BEFORE UPDATE ON processing_jobs", "OLD.status='queued'"),
            ("cancelled-event", "BEFORE INSERT ON processing_job_events", "NEW.event_type='cancelled'"),
            ("running-job", "BEFORE UPDATE ON processing_jobs", "OLD.status='running'"),
            ("cancel-requested-event", "BEFORE INSERT ON processing_job_events", "NEW.event_type='cancel_requested'"),
        )
        async with p3_freshness_fixture(
            prefix="study-app-p3-freshness-rollback-",
            now=NOW,
        ) as fixture:
            service = SourceFreshnessService(fixture.unit_of_work_factory)
            for label, operation, predicate in failure_points:
                trigger_name = f"freshness_fail_{label.replace('-', '_')}"
                with self.subTest(write_point=label):
                    async with fixture.session_factory() as session:
                        await session.execute(
                            text(
                                f"CREATE TRIGGER {trigger_name} {operation} "
                                f"WHEN {predicate} BEGIN "
                                "SELECT RAISE(ABORT,'FRESHNESS_INJECTED_FAILURE'); END"
                            )
                        )
                        await session.commit()
                    before = inspect_database(fixture.database_path)
                    try:
                        with self.assertRaises(Exception) as raised:
                            await service.reconcile_pdf("paper-1", "c" * 64, now=NOW)
                        self.assertIn("FRESHNESS_INJECTED_FAILURE", str(raised.exception))
                        after = inspect_database(fixture.database_path)
                        self.assertEqual(before.table_counts, after.table_counts)
                        self.assertEqual(before.table_sha256, after.table_sha256)
                        self.assertEqual(before.content_counts, after.content_counts)
                        self.assertEqual(before.content_sha256, after.content_sha256)
                    finally:
                        async with fixture.session_factory() as session:
                            await session.execute(text(f"DROP TRIGGER IF EXISTS {trigger_name}"))
                            await session.commit()

    async def test_competing_activations_leave_exactly_one_same_mode_identity_ready(self) -> None:
        import hashlib

        from sqlalchemy import text

        from backend.app.application.source_freshness import SourceFreshnessService, StaleResult
        from backend.app.domain import SourceDocument
        from backend.app.repositories.unit_of_work import SqlAlchemyUnitOfWork
        from backend.tests.support.p3_database import p3_database_fixture

        async with p3_database_fixture(
            prefix="study-app-p3-freshness-race-"
        ) as fixture:
            factory = lambda: SqlAlchemyUnitOfWork(fixture.session_factory)
            async with factory() as work:
                for index in (1, 2):
                    markdown = f"# race {index}\n\nidentity {index}.\n"
                    await work.sources.add(
                        SourceDocument(
                            id=f"src_race_{index}",
                            paper_id="paper-1",
                            mode="native",
                            status="ready",
                            provider=f"local-{index}",
                            model=f"model-{index}",
                            pdf_sha256="a" * 64,
                            options_hash=str(index) * 64,
                            processing_version=f"native-v{index}",
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

            service = SourceFreshnessService(factory)
            outcomes = await asyncio.gather(
                service.activate_source("src_race_1", now=NOW),
                service.activate_source("src_race_2", now=NOW),
                return_exceptions=True,
            )

            self.assertEqual(1, sum(isinstance(value, StaleResult) for value in outcomes))
            errors = [value for value in outcomes if isinstance(value, BaseException)]
            self.assertEqual(1, len(errors))
            self.assertIn("SOURCE_NOT_READY", str(errors[0]))
            async with fixture.session_factory() as session:
                rows = (
                    await session.execute(
                        text(
                            "SELECT id,status FROM document_sources "
                            "WHERE id IN ('src_race_1','src_race_2') ORDER BY id"
                        )
                    )
                ).all()
            self.assertEqual(1, sum(row.status == "ready" for row in rows))
            self.assertEqual(1, sum(row.status == "stale" for row in rows))

    async def test_stale_chunks_remain_indexed_but_lexical_search_never_returns_them(self) -> None:
        from sqlalchemy import text

        from backend.app.application.source_freshness import SourceFreshnessService
        from backend.app.domain.context import SearchRequest
        from backend.app.repositories.document_search import SqlAlchemyDocumentSearchRepository
        from backend.tests.support.p3_database import p3_freshness_fixture

        async with p3_freshness_fixture(
            prefix="study-app-p3-freshness-search-",
            now=NOW,
        ) as fixture:
            repository = SqlAlchemyDocumentSearchRepository(fixture.session_factory)
            request = SearchRequest(query="freshness", mode="lexical")
            before = await repository.lexical(request)
            self.assertTrue(before)
            self.assertTrue(
                all(hit.source_document_id == fixture.source_id for hit in before)
            )

            await SourceFreshnessService(
                fixture.unit_of_work_factory
            ).reconcile_pdf("paper-1", "c" * 64, now=NOW)
            after = await repository.lexical(request)

            self.assertEqual((), after)
            async with fixture.session_factory() as session:
                retained_chunks = (
                    await session.execute(
                        text(
                            "SELECT count(*) FROM document_chunks "
                            "WHERE source_document_id=:source_id AND status='stale'"
                        ),
                        {"source_id": fixture.source_id},
                    )
                ).scalar_one()
                retained_fts = (
                    await session.execute(
                        text(
                            "SELECT count(*) FROM document_chunks c "
                            "JOIN document_chunks_fts f ON f.rowid=c.rowid "
                            "WHERE c.source_document_id=:source_id"
                        ),
                        {"source_id": fixture.source_id},
                    )
                ).scalar_one()
            self.assertEqual(len(fixture.chunk_ids), retained_chunks)
            self.assertEqual(len(fixture.chunk_ids), retained_fts)


if __name__ == "__main__":
    unittest.main()
