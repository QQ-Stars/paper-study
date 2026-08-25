from __future__ import annotations

import asyncio
import json
import re
import time
from types import SimpleNamespace
import unittest

from fastapi.testclient import TestClient
from sqlalchemy import text

from backend.app.api.app import create_app
from backend.app.application.legacy_ingest import LegacyIngestService
from backend.app.application.library_queries import LibraryQueries
from backend.app.repositories.unit_of_work import SqlAlchemyUnitOfWork
from backend.tests.support.p3_database import p3_database_fixture


class _NoPdfFiles:
    def has_pdf(self, _paper: object) -> bool:
        return False


class _FailingConfirmProvider:
    async def confirm_candidates(self, *_args: object, **_kwargs: object) -> dict[str, object]:
        return {"ok": False, "added": 0, "error": "provider unavailable"}


class _TruncatedStreamProvider:
    async def stream_events(self, *_args: object, **_kwargs: object):
        yield {"type": "progress", "line": "STAGE::ingest"}


class _AcquisitionProvider:
    def __init__(self, session_factory: object) -> None:
        self._session_factory = session_factory
        self.search_calls: list[tuple[str, ...]] = []
        self.ingest_calls: list[tuple[bool, bool, tuple[str, ...]]] = []

    async def stream_events(
        self,
        command: str,
        args: object = (),
        *,
        terminal_type: str = "result",
        terminal_fields: dict[str, object] | None = None,
        stdin: str | bytes | None = None,
        **_kwargs: object,
    ):
        arguments = tuple(str(item) for item in args)
        if command == "search":
            self.search_calls.append(arguments)
            yield {"type": "progress", "line": "STAGE::search"}
            yield {
                "type": terminal_type,
                "ok": True,
                **dict(terminal_fields or {}),
                "candidates": [self._candidate("direct")],
            }
            return
        if command != "ingest-selected":
            yield {"type": terminal_type, "ok": True, **dict(terminal_fields or {})}
            return

        raw = stdin.decode("utf-8") if isinstance(stdin, bytes) else str(stdin or "[]")
        candidates = json.loads(raw)
        deep = "--deep" in arguments
        download_pdf = "--no-pdf" not in arguments
        added_ids = await self._insert_candidates(candidates)
        self.ingest_calls.append((deep, download_pdf, added_ids))
        yield {"type": "progress", "line": f"INGESTED::{len(added_ids)}"}
        yield {"type": terminal_type, "ok": True, "added": len(added_ids)}

    async def run_job(self, job_id: int) -> object:
        candidate = self._candidate("background")
        async with self._session_factory() as session:
            await session.execute(
                text(
                    "INSERT INTO job_candidates(job_id,title_norm,data,status) "
                    "VALUES(:job_id,:title_norm,:data,'pending')"
                ),
                {
                    "job_id": job_id,
                    "title_norm": "migrationbackgroundcandidate",
                    "data": json.dumps(candidate, ensure_ascii=False),
                },
            )
            await session.execute(
                text(
                    "UPDATE ingest_jobs SET status='review',found=1 "
                    "WHERE id=:job_id"
                ),
                {"job_id": job_id},
            )
            await session.commit()
        return SimpleNamespace(returncode=0, stdout="", stderr="JOBDONE::1::1\n")

    async def run(self, _command: str, _args: object = ()) -> object:
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    async def _insert_candidates(self, candidates: list[object]) -> tuple[str, ...]:
        added: list[str] = []
        async with self._session_factory() as session:
            for item in candidates:
                if not isinstance(item, dict):
                    continue
                title = str(item.get("title") or "").strip()
                suffix = str(item.get("fixtureId") or "candidate").strip()
                identifier = f"acquisition-{suffix}"
                if not title:
                    continue
                result = await session.execute(
                    text(
                        "INSERT OR IGNORE INTO papers("
                        "id,source,title,title_norm,venue,year,type,topic"
                        ") VALUES(:id,'fixture',:title,:title_norm,'arXiv','2026',"
                        "'method','migration regression')"
                    ),
                    {
                        "id": identifier,
                        "title": title,
                        "title_norm": re.sub(r"[^a-z0-9]+", "", title.lower()),
                    },
                )
                if int(result.rowcount or 0) == 1:
                    added.append(identifier)
            await session.commit()
        return tuple(added)

    @staticmethod
    def _candidate(suffix: str) -> dict[str, object]:
        return {
            "fixtureId": suffix,
            "title": f"Migration {suffix.title()} Candidate",
            "source": "arxiv",
            "year": 2026,
            "type": "method",
            "topic": "migration regression",
        }


class AcquisitionFlowTests(unittest.TestCase):
    def test_ingest_stream_adds_failed_terminal_when_provider_ends_early(self) -> None:
        async def scenario() -> None:
            async with p3_database_fixture(prefix="study-app-acquisition-truncated-") as fixture:
                ingest = LegacyIngestService(
                    fixture.session_factory,
                    provider=_TruncatedStreamProvider(),
                )
                events = [
                    event
                    async for event in ingest.ingest_selected_events(
                        [{"title": "truncated"}]
                    )
                ]
                self.assertEqual(
                    [
                        {"type": "progress", "line": "STAGE::ingest"},
                        {
                            "type": "done",
                            "ok": False,
                            "added": 0,
                            "error": "legacy agent stream ended without terminal event",
                        },
                    ],
                    events,
                )

        asyncio.run(scenario())

    def test_fallback_confirm_failure_does_not_mark_candidates_added(self) -> None:
        async def scenario() -> None:
            async with p3_database_fixture(prefix="study-app-acquisition-confirm-failure-") as fixture:
                async with fixture.session_factory() as session:
                    job_result = await session.execute(
                        text(
                            "INSERT INTO ingest_jobs(query,venues,status) "
                            "VALUES('fixture','arxiv','review')"
                        )
                    )
                    job_id = int(job_result.lastrowid)
                    candidate_result = await session.execute(
                        text(
                            "INSERT INTO job_candidates(job_id,title_norm,data,status) "
                            "VALUES(:job_id,'fixturetitle',:data,'pending')"
                        ),
                        {"job_id": job_id, "data": json.dumps({"title": "fixture"})},
                    )
                    candidate_id = int(candidate_result.lastrowid)
                    await session.commit()

                ingest = LegacyIngestService(
                    fixture.session_factory,
                    provider=_FailingConfirmProvider(),
                )
                result = await ingest.confirm(
                    job_id,
                    [{"_cid": candidate_id, "title": "fixture"}],
                )
                self.assertEqual(
                    {
                        "type": "done",
                        "ok": False,
                        "added": 0,
                        "error": "provider unavailable",
                    },
                    result,
                )
                async with fixture.session_factory() as session:
                    row = (
                        await session.execute(
                            text(
                                "SELECT c.status AS status,j.added AS added "
                                "FROM job_candidates c "
                                "JOIN ingest_jobs j ON j.id=c.job_id "
                                "WHERE c.id=:id"
                            ),
                            {"id": candidate_id},
                        )
                    ).mappings().one()
                self.assertEqual("pending", row["status"])
                self.assertEqual(0, row["added"])

        asyncio.run(scenario())

    def test_search_import_background_confirm_round_trip_over_http(self) -> None:
        async def scenario() -> None:
            async with p3_database_fixture(prefix="study-app-acquisition-flow-") as fixture:
                provider = _AcquisitionProvider(fixture.session_factory)
                ingest = LegacyIngestService(
                    fixture.session_factory,
                    provider=provider,
                )
                library_queries = LibraryQueries(
                    lambda: SqlAlchemyUnitOfWork(fixture.session_factory),
                    pdf_files=_NoPdfFiles(),
                    ccf_ranks={},
                )

                class Services:
                    schema_revision = "20260807_03"
                    legacy = SimpleNamespace(
                        agent=provider,
                        legacy_ingest=ingest,
                        library_queries=library_queries,
                    )

                    async def dispose(self) -> None:
                        return None

                app = create_app(
                    Services(),
                    fixture.session_factory,
                    required_schema_revision="20260807_03",
                )
                with TestClient(app) as client:
                    search = client.post(
                        "/api/search",
                        json={
                            "query": "migration regression",
                            "sources": ["arxiv"],
                            "years": "2021-2026",
                            "max": 5,
                            "minRelevance": 0.75,
                            "expand": True,
                            "onlyA": True,
                            "queries": ["migration regression", "FastAPI parity"],
                        },
                    )
                    search_events = self._events(search)
                    self.assertEqual("progress", search_events[0]["type"])
                    self.assertEqual([len(search_events) - 1], self._terminal_indexes(search_events))
                    candidates = search_events[-1]["candidates"]
                    self.assertEqual(["Migration Direct Candidate"], [item["title"] for item in candidates])
                    self.assertEqual(
                        (
                            "--query",
                            "migration regression",
                            "--sources",
                            "arxiv",
                            "--years",
                            "2021-2026",
                            "--max",
                            "5",
                            "--min-relevance",
                            "0.75",
                            "--expand",
                            "--only-a",
                            "--queries",
                            '["migration regression", "FastAPI parity"]',
                        ),
                        provider.search_calls[0],
                    )

                    imported = client.post(
                        "/api/ingest-selected",
                        json={"candidates": candidates, "deep": True, "downloadPdf": False},
                    )
                    import_events = self._events(imported)
                    self.assertEqual([len(import_events) - 1], self._terminal_indexes(import_events))
                    self.assertEqual(1, import_events[-1]["added"])
                    self.assertIn(
                        "acquisition-direct",
                        {paper["id"] for paper in client.get("/api/papers").json()},
                    )

                    created = client.post(
                        "/api/jobs",
                        json={
                            "query": "background migration regression",
                            "sources": ["arxiv"],
                            "years": "2022-2026",
                            "max": 7,
                            "minRelevance": 0.8,
                            "onlyA": True,
                            "queries": ["background migration regression", "job parity"],
                        },
                    )
                    self.assertEqual(200, created.status_code, created.text)
                    job_id = int(created.json()["id"])
                    detail = None
                    for _ in range(100):
                        response = client.get(f"/api/jobs/detail?id={job_id}")
                        detail = response.json()
                        if detail.get("candidates"):
                            break
                        time.sleep(0.01)
                    assert detail is not None
                    self.assertEqual("review", detail["job"]["status"])
                    self.assertEqual(1, len(detail["candidates"]))

                    confirmed = client.post(
                        "/api/jobs/confirm",
                        json={
                            "jobId": job_id,
                            "candidates": detail["candidates"],
                            "deep": False,
                            "downloadPdf": False,
                        },
                    )
                    confirm_events = self._events(confirmed)
                    self.assertEqual([len(confirm_events) - 1], self._terminal_indexes(confirm_events))
                    self.assertEqual(1, confirm_events[-1]["added"])
                    self.assertIn(
                        "acquisition-background",
                        {paper["id"] for paper in client.get("/api/papers").json()},
                    )
                    job = next(item for item in client.get("/api/jobs").json() if item["id"] == job_id)
                    self.assertEqual("done", job["status"])
                    self.assertEqual(1, job["added"])

                self.assertEqual(
                    [(True, False, ("acquisition-direct",)), (False, False, ("acquisition-background",))],
                    provider.ingest_calls,
                )

        asyncio.run(scenario())

    def test_provider_unavailable_is_a_failed_terminal_event(self) -> None:
        async def scenario() -> None:
            async with p3_database_fixture(prefix="study-app-acquisition-unavailable-") as fixture:
                ingest = LegacyIngestService(fixture.session_factory)

                class Services:
                    schema_revision = "20260807_03"
                    legacy = SimpleNamespace(legacy_ingest=ingest)

                    async def dispose(self) -> None:
                        return None

                app = create_app(
                    Services(),
                    fixture.session_factory,
                    required_schema_revision="20260807_03",
                )
                with TestClient(app) as client:
                    response = client.post(
                        "/api/ingest-selected",
                        json={"candidates": [{"title": "Unavailable Provider"}]},
                    )
                events = self._events(response)
                self.assertEqual([0], self._terminal_indexes(events))
                self.assertEqual(
                    {"type": "done", "ok": False, "added": 0, "error": "provider unavailable"},
                    events[0],
                )

        asyncio.run(scenario())

    def _events(self, response: object) -> list[dict[str, object]]:
        self.assertEqual(200, response.status_code, response.text)
        self.assertEqual(
            "application/x-ndjson; charset=utf-8",
            response.headers.get("content-type"),
        )
        return [json.loads(line) for line in response.text.splitlines() if line.strip()]

    @staticmethod
    def _terminal_indexes(events: list[dict[str, object]]) -> list[int]:
        return [
            index
            for index, event in enumerate(events)
            if event.get("type") in {"done", "result"}
        ]


if __name__ == "__main__":
    unittest.main()
