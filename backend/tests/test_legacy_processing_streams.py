from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
from pathlib import Path
import tempfile
import unittest

from sqlalchemy import text

from backend.app.application.context_builder import ContextBuilder
from backend.app.application.document_artifacts import DocumentArtifactService
from backend.app.application.document_search import DocumentSearch
from backend.app.application.legacy_processing_streams import LegacyProcessingStreams
from backend.app.domain.context import ChunkingSpec, EmbeddingProfile
from backend.app.domain.processing import JobProgress, JobResult
from backend.app.repositories.document_search import SqlAlchemyDocumentSearchRepository
from backend.tests.support.p3_database import P3ContextFixture, p3_context_fixture


_NOW = datetime(2026, 8, 14, 1, 0, tzinfo=timezone.utc)


class _StructuredProvider:
    provider_id = "fake-structured"
    model_id = "fake-structured-model"


@dataclass(frozen=True, slots=True)
class _StreamFixture:
    context: P3ContextFixture
    streams: LegacyProcessingStreams


@asynccontextmanager
async def _stream_fixture(*, prefix: str):
    with tempfile.TemporaryDirectory(prefix=prefix) as temp_dir:
        pdf_path = Path(temp_dir) / "paper-1.pdf"
        pdf_path.write_bytes(b"legacy-processing-stream-pdf")
        pdf_sha256 = hashlib.sha256(pdf_path.read_bytes()).hexdigest()
        async with p3_context_fixture(
            prefix=prefix,
            source_id="source-legacy-stream",
            markdown="# Source\n\nDurable stream source.\n",
            spec=ChunkingSpec(target_tokens=8, hard_cap_tokens=12),
            now=_NOW,
            pdf_sha256=pdf_sha256,
            options_hash="b" * 64,
        ) as context:
            async with context.session_factory() as session:
                await session.execute(
                    text("UPDATE papers SET pdf_path=:pdf_path WHERE id='paper-1'"),
                    {"pdf_path": str(pdf_path)},
                )
                await session.execute(text("DELETE FROM papers WHERE id <> 'paper-1'"))
                await session.commit()
            artifact_service = DocumentArtifactService(
                context.unit_of_work_factory,
                context_builder=ContextBuilder(context.unit_of_work_factory),
                structured_provider=_StructuredProvider(),
                clock=lambda: _NOW,
            )
            profile = EmbeddingProfile(
                provider="fake-embedding",
                model="fake-embedding-model",
                embedding_version="fake-v1",
                dimensions=2,
            )
            search = DocumentSearch(
                SqlAlchemyDocumentSearchRepository(context.session_factory),
                index_embedding_profile=profile,
                clock=lambda: _NOW,
                job_id_factory=lambda: "job-legacy-embedding",
            )
            yield _StreamFixture(
                context=context,
                streams=LegacyProcessingStreams(
                    context.unit_of_work_factory,
                    artifact_service=artifact_service,
                    document_search=search,
                    embedding_profile=profile,
                    clock=lambda: _NOW,
                    sleep=asyncio.sleep,
                    poll_interval=0.001,
                ),
            )


async def _complete_artifact_job(
    context: P3ContextFixture,
    *,
    markdown: str,
) -> str:
    async with context.unit_of_work_factory() as work:
        lease = await work.jobs.claim_next(
            worker_id="artifact-worker",
            now=_NOW,
            lease_seconds=3600,
        )
        await work.commit()
    if lease is None:
        raise AssertionError("artifact job was not queued")
    artifact_id = lease.spec.value.artifact_id
    later = _NOW + timedelta(seconds=1)
    async with context.unit_of_work_factory() as work:
        await work.jobs.report_progress(
            lease,
            JobProgress({"phase": "generate", "completed": 1, "total": 1}),
            now=later,
        )
        changed = await work.artifacts.publish_ready(
            artifact_id,
            "running",
            markdown,
            hashlib.sha256(markdown.encode("utf-8")).hexdigest(),
            later,
        )
        if not changed:
            raise AssertionError("artifact publication did not win")
        await work.jobs.complete(
            lease,
            JobResult({"artifactId": artifact_id}),
            now=later,
        )
        await work.commit()
    return lease.job.id


async def _complete_embedding_job(context: P3ContextFixture) -> str:
    async with context.unit_of_work_factory() as work:
        lease = await work.jobs.claim_next(
            worker_id="embedding-worker",
            now=_NOW,
            lease_seconds=3600,
        )
        await work.commit()
    if lease is None:
        raise AssertionError("embedding job was not queued")
    async with context.unit_of_work_factory() as work:
        await work.jobs.report_progress(
            lease,
            JobProgress({"phase": "embed", "completed": 1, "total": 2}),
            now=_NOW + timedelta(seconds=1),
        )
        await work.commit()
    async with context.unit_of_work_factory() as work:
        await work.jobs.report_progress(
            lease,
            JobProgress({"phase": "embed", "completed": 2, "total": 2}),
            now=_NOW + timedelta(seconds=2),
        )
        await work.commit()
    async with context.unit_of_work_factory() as work:
        await work.jobs.complete(
            lease,
            JobResult({"indexed": 1}),
            now=_NOW + timedelta(seconds=3),
        )
        await work.commit()
    return lease.job.id


class LegacyProcessingStreamsTests(unittest.IsolatedAsyncioTestCase):
    async def test_disconnect_detaches_persisted_job_without_cancelling_worker(self) -> None:
        async with _stream_fixture(prefix="legacy-stream-disconnect-") as fixture:
            subscriber = fixture.streams.artifact_events("paper-1")
            first = await anext(subscriber)
            self.assertEqual("progress", first["type"])
            self.assertEqual("enqueued", first["event"])
            self.assertEqual("enqueued", first["line"])
            job_id = first["jobId"]
            await subscriber.aclose()

            async with fixture.context.unit_of_work_factory() as work:
                queued = await work.jobs.get(job_id)
            self.assertEqual("queued", queued.status)

            markdown = "# Persisted explainer\n\nWorker completed after detach.\n"
            self.assertEqual(
                job_id,
                await _complete_artifact_job(fixture.context, markdown=markdown),
            )
            async with fixture.context.unit_of_work_factory() as work:
                terminal = await work.jobs.get(job_id)
                persisted_events = await work.jobs.list_events_after(
                    job_id,
                    after_sequence=-1,
                    limit=100,
                )
            self.assertEqual("succeeded", terminal.status)
            self.assertNotIn(
                "cancelled",
                {event.event_type for event in persisted_events},
            )
            self.assertNotIn(
                "cancel_requested",
                {event.event_type for event in persisted_events},
            )

            replay = [
                event
                async for event in fixture.streams.artifact_events("paper-1")
            ]
            results = [event for event in replay if event["type"] == "result"]
            self.assertEqual(1, len(results))
            self.assertTrue(results[0]["ok"])
            self.assertEqual(markdown, results[0]["markdown"])

    async def test_slow_consumer_preserves_event_order(self) -> None:
        async with _stream_fixture(prefix="legacy-stream-slow-") as fixture:
            subscriber = fixture.streams.embedding_events(scope="all")
            observed = [await anext(subscriber)]
            self.assertEqual("enqueued", observed[0]["event"])
            job_id = observed[0]["jobId"]

            self.assertEqual(
                job_id,
                await _complete_embedding_job(fixture.context),
            )
            while True:
                await asyncio.sleep(0.005)
                try:
                    observed.append(await anext(subscriber))
                except StopAsyncIteration:
                    break

            progress = [event for event in observed if event["type"] == "progress"]
            self.assertEqual(
                ["enqueued", "claimed", "progress", "progress", "succeeded"],
                [event["event"] for event in progress],
            )
            sequences = [event["sequence"] for event in progress]
            self.assertEqual(sorted(sequences), sequences)
            self.assertEqual(len(sequences), len(set(sequences)))
            results = [event for event in observed if event["type"] == "result"]
            self.assertEqual(
                [{"type": "result", "ok": True, "indexed": 1, "total": 1}],
                results,
            )


if __name__ == "__main__":
    unittest.main()
