from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest

from fastapi.testclient import TestClient
from sqlalchemy import text

from backend.app.api.app import create_app
from backend.app.config import DatabaseSettings
from backend.app.infrastructure.database import create_async_session_factory


class _NoPdfFiles:
    def resolve_for_id(self, _paper_id: str, stored_path: object = None) -> None:
        return None


class _ArtifactStore:
    async def explain_batch_status(self) -> dict[str, object]:
        return {
            "ok": True,
            "total": 4,
            "explained": 1,
            "pending": 3,
        }


class _EnrichAgent:
    async def run(
        self,
        command: str,
        args: list[str] | tuple[str, ...],
        **_kwargs: object,
    ) -> SimpleNamespace:
        if command != "enrich" or list(args) != ["--limit", "3"]:
            return SimpleNamespace(
                returncode=1,
                stdout="",
                stderr="unexpected enrich invocation",
            )
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps(
                {
                    "ok": True,
                    "total": 3,
                    "done": 2,
                    "failed": ["paper-3"],
                    "skipped": [],
                }
            ),
            stderr="BATCH::1/3\nITEM::paper-1",
        )


def _app(session_factory: object, *, agent: object | None = None):
    class Services:
        schema_revision = "20260807_03"
        legacy = SimpleNamespace(
            agent=agent,
            artifact_store=_ArtifactStore(),
            pdf_files=_NoPdfFiles(),
        )

        async def dispose(self) -> None:
            return None

    return create_app(
        Services(),
        session_factory,
        required_schema_revision="20260807_03",
    )


async def _create_batch_runs(session_factory: object) -> None:
    async with session_factory() as session:
        await session.execute(
            text(
                """
                CREATE TABLE batch_runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    kind TEXT NOT NULL,
                    finished_at TEXT NOT NULL,
                    total INTEGER NOT NULL,
                    done INTEGER NOT NULL,
                    failed INTEGER NOT NULL,
                    skipped INTEGER NOT NULL,
                    detail TEXT NOT NULL
                )
                """
            )
        )
        await session.commit()


@asynccontextmanager
async def _database_fixture(prefix: str, *, seed_paper: bool = True):
    with tempfile.TemporaryDirectory(prefix=prefix) as temp_dir:
        database_path = Path(temp_dir) / "app.db"
        database_path.touch()
        session_factory = create_async_session_factory(DatabaseSettings(database_path))
        try:
            async with session_factory() as session:
                await session.execute(
                    text(
                        """
                        CREATE TABLE papers (
                            id TEXT PRIMARY KEY,
                            title TEXT NOT NULL DEFAULT '',
                            title_norm TEXT NOT NULL DEFAULT '',
                            year INTEGER,
                            venue TEXT,
                            pdf_path TEXT
                        )
                        """
                    )
                )
                if seed_paper:
                    await session.execute(
                        text(
                            """
                            INSERT INTO papers (id, title, title_norm, year, venue, pdf_path)
                            VALUES (
                                'paper-1', 'Fixture Paper', 'fixture paper', 2024, 'ACL', NULL
                            )
                            """
                        )
                    )
                await session.commit()
            yield SimpleNamespace(
                database_path=database_path,
                session_factory=session_factory,
            )
        finally:
            await session_factory.kw["bind"].dispose()


class OptimizationRouteTests(unittest.TestCase):
    def test_ocr_batch_status_exposes_the_latest_persisted_run(self) -> None:
        async def scenario() -> None:
            async with _database_fixture(
                prefix="study-app-optimization-ocr-last-run-"
            ) as fixture:
                await _create_batch_runs(fixture.session_factory)
                async with fixture.session_factory() as session:
                    await session.execute(
                        text(
                            """
                            INSERT INTO batch_runs
                                (kind, finished_at, total, done, failed, skipped, detail)
                            VALUES
                                ('ocr', '2026-08-23T09:30:00+08:00', 9, 6, 2, 1,
                                 '{"failed":["paper-3","paper-8"]}')
                            """
                        )
                    )
                    await session.commit()

                with TestClient(_app(fixture.session_factory)) as client:
                    response = client.get("/api/ocr-md-batch")

                self.assertEqual(200, response.status_code, response.text)
                self.assertEqual(
                    {
                        "id": 1,
                        "kind": "ocr",
                        "finishedAt": "2026-08-23T09:30:00+08:00",
                        "total": 9,
                        "done": 6,
                        "failed": 2,
                        "skipped": 1,
                        "detail": {"failed": ["paper-3", "paper-8"]},
                    },
                    response.json()["lastRun"],
                )

        asyncio.run(scenario())

    def test_explain_batch_status_exposes_only_the_latest_explain_run(self) -> None:
        async def scenario() -> None:
            async with _database_fixture(
                prefix="study-app-optimization-explain-last-run-"
            ) as fixture:
                await _create_batch_runs(fixture.session_factory)
                async with fixture.session_factory() as session:
                    await session.execute(
                        text(
                            """
                            INSERT INTO batch_runs
                                (kind, finished_at, total, done, failed, skipped, detail)
                            VALUES
                                ('explain', '2026-08-22T08:00:00+08:00', 2, 2, 0, 0, '{}'),
                                ('ocr', '2026-08-23T08:30:00+08:00', 5, 4, 1, 0, '{}'),
                                ('explain', '2026-08-23T10:00:00+08:00', 7, 5, 1, 1,
                                 '{"skipped":["paper-4"]}')
                            """
                        )
                    )
                    await session.commit()

                with TestClient(_app(fixture.session_factory)) as client:
                    response = client.get("/api/explain-batch")

                self.assertEqual(200, response.status_code, response.text)
                self.assertEqual(4, response.json()["total"])
                self.assertEqual(
                    {
                        "id": 3,
                        "kind": "explain",
                        "finishedAt": "2026-08-23T10:00:00+08:00",
                        "total": 7,
                        "done": 5,
                        "failed": 1,
                        "skipped": 1,
                        "detail": {"skipped": ["paper-4"]},
                    },
                    response.json()["lastRun"],
                )

        asyncio.run(scenario())

    def test_duplicate_scan_returns_read_only_pairs_from_the_active_database(self) -> None:
        async def scenario() -> None:
            async with _database_fixture(
                prefix="study-app-optimization-duplicate-scan-"
            ) as fixture:
                async with fixture.session_factory() as session:
                    await session.execute(
                        text(
                            """
                            INSERT INTO papers (id, title, title_norm, year, venue, pdf_path)
                            VALUES
                                ('paper-2', 'Graph Methods', 'graph methods', 2023, 'ICLR', NULL),
                                ('paper-3', 'Graph Methods', 'graph methods', 2024, 'NeurIPS', NULL),
                                ('paper-4', 'Unrelated Optimization', 'unrelated optimization',
                                 2022, 'ICML', NULL)
                            """
                        )
                    )
                    await session.commit()

                with TestClient(_app(fixture.session_factory)) as client:
                    response = client.get("/api/dup-scan")

                self.assertEqual(200, response.status_code, response.text)
                payload = response.json()
                self.assertTrue(payload["ok"])
                self.assertEqual(1, payload["count"])
                self.assertEqual("Graph Methods", payload["pairs"][0]["left"]["title"])
                self.assertEqual("Graph Methods", payload["pairs"][0]["right"]["title"])
                self.assertEqual(1.0, payload["pairs"][0]["similarity"])

        asyncio.run(scenario())

    def test_enrich_status_reports_missing_metadata_and_author_coverage(self) -> None:
        async def scenario() -> None:
            async with _database_fixture(
                prefix="study-app-optimization-enrich-status-"
            ) as fixture:
                async with fixture.session_factory() as session:
                    await session.execute(
                        text(
                            """
                            INSERT INTO papers (id, title, title_norm, year, venue, pdf_path)
                            VALUES
                                ('paper-2', 'Missing Year', 'missing year', NULL, 'KDD', NULL),
                                ('paper-3', 'Missing Venue', 'missing venue', 2021, '', NULL)
                            """
                        )
                    )
                    await session.execute(
                        text(
                            """
                            CREATE TABLE paper_authors (
                                paper_id TEXT PRIMARY KEY,
                                authors TEXT NOT NULL,
                                updated_at TEXT NOT NULL
                            )
                            """
                        )
                    )
                    await session.execute(
                        text(
                            """
                            INSERT INTO paper_authors (paper_id, authors, updated_at)
                            VALUES
                                ('paper-1', '["Ada Lovelace"]', '2026-08-23T09:00:00+08:00'),
                                ('paper-2', '["Grace Hopper"]', '2026-08-23T09:01:00+08:00')
                            """
                        )
                    )
                    await session.commit()

                with TestClient(_app(fixture.session_factory)) as client:
                    response = client.get("/api/enrich-status")

                self.assertEqual(200, response.status_code, response.text)
                self.assertEqual(
                    {
                        "ok": True,
                        "total": 3,
                        "missingYear": 1,
                        "missingVenue": 1,
                        "missingMetadata": 2,
                        "withAuthors": 2,
                        "missingAuthors": 1,
                        "pending": 2,
                    },
                    response.json(),
                )

        asyncio.run(scenario())

    def test_paper_authors_returns_the_stored_author_names(self) -> None:
        async def scenario() -> None:
            async with _database_fixture(
                prefix="study-app-optimization-paper-authors-"
            ) as fixture:
                async with fixture.session_factory() as session:
                    await session.execute(
                        text(
                            """
                            CREATE TABLE paper_authors (
                                paper_id TEXT PRIMARY KEY,
                                authors TEXT NOT NULL,
                                updated_at TEXT NOT NULL
                            )
                            """
                        )
                    )
                    await session.execute(
                        text(
                            """
                            INSERT INTO paper_authors (paper_id, authors, updated_at)
                            VALUES (
                                'paper-1',
                                '["Ada Lovelace","Alan Turing"]',
                                '2026-08-23T09:00:00+08:00'
                            )
                            """
                        )
                    )
                    await session.commit()

                with TestClient(_app(fixture.session_factory)) as client:
                    response = client.get("/api/paper-authors?id=paper-1")

                self.assertEqual(200, response.status_code, response.text)
                self.assertEqual(
                    {
                        "ok": True,
                        "id": "paper-1",
                        "authors": ["Ada Lovelace", "Alan Turing"],
                    },
                    response.json(),
                )

        asyncio.run(scenario())

    def test_enrich_streams_progress_and_a_compatible_terminal_result(self) -> None:
        async def scenario() -> None:
            async with _database_fixture(
                prefix="study-app-optimization-enrich-stream-"
            ) as fixture:
                with TestClient(
                    _app(fixture.session_factory, agent=_EnrichAgent())
                ) as client:
                    response = client.post("/api/enrich", json={"limit": 3})

                self.assertEqual(200, response.status_code, response.text)
                self.assertEqual(
                    "application/x-ndjson; charset=utf-8",
                    response.headers["content-type"],
                )
                events = [json.loads(line) for line in response.text.splitlines()]
                self.assertEqual(
                    [
                        {"type": "progress", "line": "BATCH::1/3"},
                        {"type": "progress", "line": "ITEM::paper-1"},
                    ],
                    events[:-1],
                )
                self.assertIn(events[-1]["type"], {"done", "result"})
                self.assertEqual(
                    {
                        "ok": True,
                        "total": 3,
                        "done": 2,
                        "failed": ["paper-3"],
                        "skipped": [],
                    },
                    {key: events[-1][key] for key in ("ok", "total", "done", "failed", "skipped")},
                )

        asyncio.run(scenario())

    def test_optional_persistence_tables_can_be_absent(self) -> None:
        async def scenario() -> None:
            async with _database_fixture(
                prefix="study-app-optimization-optional-tables-"
            ) as fixture:
                with TestClient(_app(fixture.session_factory)) as client:
                    ocr = client.get("/api/ocr-md-batch")
                    explain = client.get("/api/explain-batch")
                    authors = client.get("/api/paper-authors?id=paper-1")
                    enrich = client.get("/api/enrich-status")

                for response in (ocr, explain, authors, enrich):
                    self.assertEqual(200, response.status_code, response.text)
                self.assertIsNone(ocr.json()["lastRun"])
                self.assertIsNone(explain.json()["lastRun"])
                self.assertEqual([], authors.json()["authors"])
                self.assertEqual(0, enrich.json()["withAuthors"])
                self.assertEqual(1, enrich.json()["missingAuthors"])
                self.assertEqual(1, enrich.json()["pending"])

        asyncio.run(scenario())

    def test_duplicate_scan_of_an_empty_library_returns_no_pairs(self) -> None:
        async def scenario() -> None:
            async with _database_fixture(
                prefix="study-app-optimization-empty-duplicate-scan-",
                seed_paper=False,
            ) as fixture:
                with TestClient(_app(fixture.session_factory)) as client:
                    response = client.get("/api/dup-scan")

                self.assertEqual(200, response.status_code, response.text)
                self.assertEqual(
                    {"ok": True, "count": 0, "pairs": []},
                    response.json(),
                )

        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()
