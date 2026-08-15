from __future__ import annotations

import asyncio
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sqlite3
import tempfile
import unittest

from sqlalchemy import text

from backend.app.config import DatabaseSettings
from backend.app.domain import (
    ArtifactKind,
    ArtifactVersionIdentity,
    GeneratedArtifact,
    PersistenceConflictError,
    ProcessingJob,
    SourceCacheIdentity,
    SourceDocument,
    VaultProjection,
)
from backend.app.domain.processing import (
    NewProcessingJob,
    ObsidianSyncJobSpecV1,
    encode_job_spec_v1,
    hash_job_spec,
)
from backend.app.infrastructure.database import create_async_session_factory
from backend.app.repositories.unit_of_work import SqlAlchemyUnitOfWork
from backend.tests.support.p1_database import create_legacy_database, run_alembic


NOW = datetime(2026, 8, 9, 12, 0, tzinfo=timezone.utc)
SHA_A = "a" * 64
SHA_B = "b" * 64


class P1RepositoryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory(prefix="study-app-p1-repositories-")
        self.database_path = Path(self._temp.name) / "legacy" / "app.db"
        create_legacy_database(self.database_path)
        run_alembic(self.database_path, "20260807_02")
        self.session_factory = create_async_session_factory(DatabaseSettings(self.database_path))
        self.engine = self.session_factory.kw["bind"]

    async def asyncTearDown(self) -> None:
        await self.engine.dispose()
        self._temp.cleanup()

    async def test_engine_construction_is_lazy_and_every_connection_has_sqlite_policy(self) -> None:
        untouched_path = Path(self._temp.name) / "untouched.sqlite3"
        untouched_path.write_bytes(b"not-opened")
        before = untouched_path.read_bytes()
        untouched_factory = create_async_session_factory(DatabaseSettings(untouched_path))
        untouched_engine = untouched_factory.kw["bind"]
        self.assertEqual(before, untouched_path.read_bytes())
        await untouched_engine.dispose()

        async with self.engine.connect() as connection:
            journal_mode = (await connection.execute(text("PRAGMA journal_mode"))).scalar_one()
            foreign_keys = (await connection.execute(text("PRAGMA foreign_keys"))).scalar_one()
            busy_timeout = (await connection.execute(text("PRAGMA busy_timeout"))).scalar_one()
        self.assertEqual("wal", str(journal_mode).lower())
        self.assertEqual(1, foreign_keys)
        self.assertEqual(5000, busy_timeout)

    async def test_paper_and_source_repository_return_frozen_domain_values(self) -> None:
        source = self._source("src_repo")
        async with SqlAlchemyUnitOfWork(self.session_factory) as work:
            paper = await work.papers.get("paper-1")
            self.assertEqual("paper-1", paper.id)
            await work.sources.add(source)
            self.assertEqual(source, await work.sources.get(source.id))
            identity = SourceCacheIdentity.from_document(source)
            self.assertEqual(source, await work.sources.find_by_cache_identity(identity))
            await work.commit()
        self.assertEqual("Paper", type(paper).__name__)
        self.assertEqual("SourceDocument", type(source).__name__)

    async def test_source_duplicate_race_leaves_one_row_and_maps_conflict(self) -> None:
        first = self._source("src_first")
        second = self._source("src_second")

        async def insert(document: SourceDocument) -> object:
            try:
                async with SqlAlchemyUnitOfWork(self.session_factory) as work:
                    await work.sources.add(document)
                    await work.commit()
                return document
            except Exception as error:
                return error

        outcomes = await asyncio.gather(insert(first), insert(second))
        self.assertEqual(1, sum(isinstance(value, SourceDocument) for value in outcomes))
        conflicts = [value for value in outcomes if isinstance(value, PersistenceConflictError)]
        self.assertEqual(1, len(conflicts))
        self.assertNotIn("INSERT", str(conflicts[0]).upper())
        self.assertNotIn("paper-1", str(conflicts[0]))
        with closing(sqlite3.connect(self.database_path)) as connection:
            self.assertEqual(1, connection.execute("SELECT count(*) FROM document_sources").fetchone()[0])

    async def test_artifact_job_and_projection_queries_are_deterministic(self) -> None:
        source = self._source("src_artifacts")
        older = self._artifact("art_old", source.id, NOW)
        newer = self._artifact("art_new", source.id, NOW + timedelta(seconds=1))
        global_job = ProcessingJob(
            id="job_global",
            job_type="obsidian_sync",
            status="queued",
            idempotency_key="global-once",
            created_at=NOW,
        )
        projection = VaultProjection(
            id="exp_repo",
            paper_id="paper-1",
            artifact_id=newer.id,
            target_path="papers/paper-1.md",
            source_hash=SHA_A,
            exported_hash=None,
            status="pending",
        )
        async with SqlAlchemyUnitOfWork(self.session_factory) as work:
            await work.sources.add(source)
            await work.artifacts.add(older)
            await work.artifacts.add(newer)
            global_spec = ObsidianSyncJobSpecV1()
            global_spec_json = encode_job_spec_v1(global_spec)
            await work.jobs.insert_with_spec(
                NewProcessingJob(
                    id=global_job.id,
                    spec=global_spec,
                    idempotency_key=global_job.idempotency_key,
                    created_at=global_job.created_at,
                ),
                spec_json=global_spec_json,
                spec_sha256=hash_job_spec(global_spec_json),
            )
            await work.projections.add(projection)
            await work.commit()

        async with SqlAlchemyUnitOfWork(self.session_factory) as work:
            found = await work.artifacts.find_ready_for_paper("paper-1", ArtifactKind.EXPLAINER)
            self.assertEqual(newer, found)
            self.assertEqual(
                newer,
                await work.artifacts.find_by_version_identity(
                    ArtifactVersionIdentity.from_artifact(newer)
                ),
            )
            self.assertEqual(global_job, await work.jobs.get(global_job.id))
            self.assertEqual(projection, await work.projections.find_by_target_path(projection.target_path))
        self.assertIsNone(global_job.paper_id)
        self.assertIsNone(global_job.source_mode)

    async def test_unit_of_work_commits_together_and_rolls_back_on_exception(self) -> None:
        committed = self._source("src_committed")
        async with SqlAlchemyUnitOfWork(self.session_factory) as work:
            await work.sources.add(committed)
            await work.commit()

        rolled_back = self._source("src_rolled_back", pdf_sha="c" * 64)
        with self.assertRaisesRegex(RuntimeError, "stop"):
            async with SqlAlchemyUnitOfWork(self.session_factory) as work:
                await work.sources.add(rolled_back)
                raise RuntimeError("stop")
        async with SqlAlchemyUnitOfWork(self.session_factory) as work:
            self.assertEqual(committed, await work.sources.get(committed.id))
            self.assertIsNone(await work.sources.get(rolled_back.id))

    async def test_repository_never_commits_and_closed_uow_domain_values_are_detached(self) -> None:
        source = self._source("src_uncommitted")
        async with SqlAlchemyUnitOfWork(self.session_factory) as work:
            await work.sources.add(source)
        with closing(sqlite3.connect(self.database_path)) as connection:
            self.assertEqual(
                0,
                connection.execute(
                    "SELECT count(*) FROM document_sources WHERE id='src_uncommitted'"
                ).fetchone()[0],
            )
        self.assertEqual("paper-1", source.paper_id)
        self.assertEqual("ready", source.status.value)

    async def test_cancellation_during_exit_finishes_rollback_and_close(self) -> None:
        source = self._source("src_cancelled", pdf_sha="d" * 64)
        rollback_started = asyncio.Event()
        allow_rollback = asyncio.Event()
        work = SqlAlchemyUnitOfWork(self.session_factory)

        async def run_cancelled_scope() -> None:
            async with work:
                await work.sources.add(source)
                session = work._session
                original_rollback = session.rollback

                async def delayed_rollback() -> None:
                    rollback_started.set()
                    await allow_rollback.wait()
                    await original_rollback()

                session.rollback = delayed_rollback

        task = asyncio.create_task(run_cancelled_scope())
        await rollback_started.wait()
        task.cancel()
        allow_rollback.set()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertIsNone(work._session)
        with closing(sqlite3.connect(self.database_path)) as connection:
            self.assertEqual(
                0,
                connection.execute(
                    "SELECT count(*) FROM document_sources WHERE id='src_cancelled'"
                ).fetchone()[0],
            )

    def _source(self, identifier: str, *, pdf_sha: str = SHA_A) -> SourceDocument:
        return SourceDocument(
            id=identifier,
            paper_id="paper-1",
            mode="native",
            status="ready",
            provider="local",
            model="model",
            pdf_sha256=pdf_sha,
            options_hash=SHA_B,
            processing_version="v1",
            created_at=NOW,
            updated_at=NOW,
            content_sha256=SHA_A,
            markdown="source",
            page_count=1,
        )

    def _artifact(self, identifier: str, source_id: str, updated_at: datetime) -> GeneratedArtifact:
        return GeneratedArtifact(
            id=identifier,
            paper_id="paper-1",
            kind="explainer",
            source_document_id=source_id,
            status="ready",
            generator_provider="provider",
            generator_model="model",
            prompt_version=identifier,
            created_at=updated_at,
            updated_at=updated_at,
            content=identifier,
            content_sha256=SHA_A,
        )


if __name__ == "__main__":
    unittest.main()
