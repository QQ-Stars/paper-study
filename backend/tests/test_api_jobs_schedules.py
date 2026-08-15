from __future__ import annotations

import asyncio
from contextlib import closing
from types import SimpleNamespace
import sqlite3
import unittest

from fastapi.testclient import TestClient

from backend.app.api.app import create_app
from backend.tests.support.p3_database import p3_database_fixture


class _ProcessingReadOnly:
    async def list_jobs(self, **_kwargs):
        return (), None


class _FakeLegacyIngest:
    def __init__(self, session_factory: object) -> None:
        self._session_factory = session_factory

    async def create_job(self, payload: dict[str, object]) -> int:
        # This fake deliberately mirrors the application seam; the first RED
        # should be a missing route, not an import/fixture failure.
        async with self._session_factory() as session:
            result = await session.execute(
                __import__("sqlalchemy", fromlist=["text"]).text(
                    "INSERT INTO ingest_jobs(query,venues,status) VALUES(:query,:venues,'pending')"
                ),
                {"query": payload["query"], "venues": "arxiv"},
            )
            await session.commit()
            return int(result.lastrowid)

    async def list_jobs(self) -> list[dict[str, object]]:
        async with self._session_factory() as session:
            rows = (
                await session.execute(
                    __import__("sqlalchemy", fromlist=["text"]).text(
                        "SELECT id,query,venues,status FROM ingest_jobs ORDER BY id DESC"
                    )
                )
            ).mappings().all()
            return [dict(row) for row in rows]


class JobApiTests(unittest.TestCase):
    def test_legacy_ingest_jobs_and_v2_processing_jobs_are_distinct(self) -> None:
        async def scenario() -> None:
            async with p3_database_fixture(prefix="study-app-p4-jobs-distinct-") as fixture:
                ingest = _FakeLegacyIngest(fixture.session_factory)

                class Services:
                    schema_revision = "20260807_03"
                    processing_api = _ProcessingReadOnly()
                    legacy = SimpleNamespace(legacy_ingest=ingest)

                    async def dispose(self) -> None:
                        await fixture.session_factory.kw["bind"].dispose()

                app = create_app(
                    Services(),
                    fixture.session_factory,
                    required_schema_revision="20260807_03",
                )
                with closing(sqlite3.connect(fixture.database_path)) as connection:
                    legacy_before = int(
                        connection.execute("SELECT count(*) FROM ingest_jobs").fetchone()[0]
                    )
                with TestClient(app) as client:
                    legacy_create = client.post(
                        "/api/jobs",
                        json={
                            "query": "legacy tracer",
                            "sources": ["arxiv"],
                            "years": "2024-2026",
                            "max": 3,
                        },
                    )
                    self.assertEqual(200, legacy_create.status_code, legacy_create.text)
                    legacy_id = legacy_create.json()["id"]

                    legacy_list = client.get("/api/jobs")
                    self.assertEqual(200, legacy_list.status_code, legacy_list.text)
                    self.assertEqual(legacy_before + 1, len(legacy_list.json()))
                    self.assertTrue(any(row["id"] == legacy_id for row in legacy_list.json()))

                    typed_list = client.get("/api/v2/jobs")
                    self.assertEqual(200, typed_list.status_code, typed_list.text)
                    self.assertEqual([], typed_list.json()["items"])

                with closing(sqlite3.connect(fixture.database_path)) as connection:
                    self.assertEqual(
                        legacy_before + 1,
                        connection.execute("SELECT count(*) FROM ingest_jobs").fetchone()[0],
                    )
                    self.assertEqual(
                        0,
                        connection.execute("SELECT count(*) FROM processing_jobs").fetchone()[0],
                    )
                self.assertIsNotNone(legacy_id)
                await fixture.session_factory.kw["bind"].dispose()

        asyncio.run(scenario())

    def test_schedule_routes_match_node_and_enqueue_once(self) -> None:
        async def scenario() -> None:
            async with p3_database_fixture(prefix="study-app-p4-schedules-") as fixture:
                from backend.app.application.legacy_ingest import LegacyIngestService

                ingest = LegacyIngestService(fixture.session_factory)
                scheduler = __import__(
                    "backend.app.workers.scheduler", fromlist=["LegacyScheduler"]
                ).LegacyScheduler(ingest)

                class Services:
                    schema_revision = "20260807_03"
                    legacy = SimpleNamespace(legacy_ingest=ingest, scheduler=scheduler)

                    async def dispose(self) -> None:
                        await fixture.session_factory.kw["bind"].dispose()

                app = create_app(
                    Services(),
                    fixture.session_factory,
                    required_schema_revision="20260807_03",
                )
                with TestClient(app) as client:
                    listed = client.get("/api/schedules")
                    self.assertEqual(200, listed.status_code, listed.text)
                    created = client.post(
                        "/api/schedules",
                        json={
                            "query": "schedule tracer",
                            "sources": ["arxiv"],
                            "years": "2024-2026",
                            "max": 2,
                            "everyDays": 1,
                        },
                    )
                    self.assertEqual(200, created.status_code, created.text)
                    schedule_id = created.json()["id"]
                    self.assertIsInstance(schedule_id, int)
                    self.assertEqual(
                        {"ok": True},
                        client.post(
                            "/api/schedules/toggle",
                            json={"id": schedule_id, "enabled": False},
                        ).json(),
                    )
                    self.assertEqual(
                        {"ok": True},
                        client.post(
                            "/api/schedules/toggle",
                            json={"id": schedule_id, "enabled": True},
                        ).json(),
                    )

                with closing(sqlite3.connect(fixture.database_path)) as connection:
                    connection.execute(
                        "UPDATE job_schedules SET enabled=0 WHERE id<>?",
                        (schedule_id,),
                    )
                    connection.execute(
                        "UPDATE job_schedules SET next_run='2000-01-01 00:00:00' WHERE id=?",
                        (schedule_id,),
                    )
                    connection.commit()
                    before = int(
                        connection.execute("SELECT count(*) FROM ingest_jobs").fetchone()[0]
                    )

                first = await scheduler.tick()
                second = await scheduler.tick()
                self.assertIn(schedule_id, first.job_ids)
                self.assertEqual((), second.job_ids)
                with closing(sqlite3.connect(fixture.database_path)) as connection:
                    after = int(
                        connection.execute("SELECT count(*) FROM ingest_jobs").fetchone()[0]
                    )
                    self.assertEqual(before + 1, after)
                    row = connection.execute(
                        "SELECT last_run,next_run FROM job_schedules WHERE id=?",
                        (schedule_id,),
                    ).fetchone()
                    self.assertIsNotNone(row[0])
                    self.assertNotEqual("2000-01-01 00:00:00", row[1])

        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()
