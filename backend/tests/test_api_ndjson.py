from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
import unittest

from fastapi.testclient import TestClient

from backend.app.api.app import create_app
from backend.tests.support.p3_database import p3_database_fixture


class _FakeAgent:
    async def run(self, *_args, **_kwargs):
        return SimpleNamespace(returncode=0, stdout="{}", stderr="")


class _RejectingArtifactAgent:
    async def run(self, command: str, *_args, **_kwargs):
        raise AssertionError(f"durable stream must not call legacy agent: {command}")


class _FakeProcessingStreams:
    def __init__(self) -> None:
        self.calls: list[tuple[object, ...]] = []

    async def artifact_events(self, paper_id: str, kind: str, *, profile: str):
        self.calls.append(("artifact", paper_id, kind, profile))
        yield {"type": "progress", "line": "JOB::queued"}
        yield {"type": "result", "ok": True, "markdown": f"# {kind}"}

    async def embedding_events(self, scope: str):
        self.calls.append(("embedding", scope))
        yield {"type": "progress", "line": "JOB::queued"}
        yield {"type": "result", "ok": True, "indexed": 1, "total": 1}


class _FakeIngest:
    async def confirm(self, *_args, **_kwargs):
        return {"type": "done", "ok": True, "added": 0}

    async def ingest_selected_events(self, *_args, **_kwargs):
        yield {"type": "done", "ok": True, "added": 0}


class _DirectIngest:
    def __init__(self) -> None:
        self.direct_calls: list[tuple[list[dict[str, object]], bool, bool]] = []
        self.confirm_calls = 0

    async def confirm(self, *_args, **_kwargs):
        self.confirm_calls += 1
        raise AssertionError("direct ingest must not invent a legacy job id")

    async def ingest_selected_events(
        self,
        candidates: list[dict[str, object]],
        *,
        deep: bool,
        download_pdf: bool,
    ):
        self.direct_calls.append((candidates, deep, download_pdf))
        yield {"type": "progress", "line": "INGESTED::2"}
        yield {"type": "done", "ok": True, "added": 2}


class NdjsonApiTests(unittest.TestCase):
    def test_p3_bootstrap_composes_durable_legacy_processing_streams(self) -> None:
        async def scenario() -> None:
            from backend.app.application.legacy_processing_streams import (
                LegacyProcessingStreams,
            )
            from backend.app.bootstrap import RolloutSettings, bootstrap
            from backend.app.config import DatabaseSettings
            from backend.app.domain.context import EmbeddingProfile

            class TranslationProvider:
                provider_id = "fixture-translation"
                model_id = "fixture-translation-model"
                prompt_version = "fixture-translation-v1"

            class StructuredProvider:
                provider_id = "fixture-structured"
                model_id = "fixture-structured-model"

            profile = EmbeddingProfile(
                provider="fixture-embedding",
                model="fixture-embedding-model",
                embedding_version="fixture-embedding-v1",
                dimensions=2,
            )
            async with p3_database_fixture(prefix="study-app-p4-stream-bootstrap-") as fixture:
                container = bootstrap(
                    RolloutSettings(
                        api_backend_mode="python",
                        document_pipeline_mode="p1",
                        generation_pipeline_mode="p1",
                        artifact_read_mode="prefer_new",
                        artifact_write_mode="dual",
                        processing_cursor_secret="s" * 32,
                    ),
                    DatabaseSettings(fixture.database_path),
                    required_schema_revision="20260807_03",
                    translation_provider_factory=TranslationProvider,
                    structured_provider_factory=StructuredProvider,
                    embedding_profile=profile,
                    embedding_provider_factory=lambda _profile, _credential: object(),
                )
                try:
                    self.assertIsInstance(
                        container.legacy.processing_streams,
                        LegacyProcessingStreams,
                    )
                finally:
                    await container.dispose()

        asyncio.run(scenario())

    def test_artifact_and_embedding_streams_use_durable_processing_jobs(self) -> None:
        async def scenario() -> None:
            async with p3_database_fixture(prefix="study-app-p4-durable-streams-") as fixture:
                streams = _FakeProcessingStreams()

                class Services:
                    schema_revision = "20260807_03"
                    legacy = SimpleNamespace(
                        agent=_RejectingArtifactAgent(),
                        processing_streams=streams,
                    )

                    async def dispose(self) -> None:
                        return None

                app = create_app(
                    Services(),
                    fixture.session_factory,
                    required_schema_revision="20260807_03",
                )
                with TestClient(app) as client:
                    requests = (
                        ("/api/explain", {"id": "paper-1", "deep": True}),
                        ("/api/translate", {"id": "paper-1"}),
                        ("/api/embed", {"scope": "all"}),
                    )
                    terminals = []
                    for path, body in requests:
                        response = client.post(path, json=body)
                        self.assertEqual(200, response.status_code, response.text)
                        events = [json.loads(line) for line in response.text.splitlines()]
                        self.assertEqual("progress", events[0]["type"], path)
                        self.assertTrue(events[-1]["ok"], path)
                        terminals.append(events[-1])

                self.assertEqual("# explainer", terminals[0]["markdown"])
                self.assertEqual("# translation", terminals[1]["markdown"])
                self.assertEqual((1, 1), (terminals[2]["indexed"], terminals[2]["total"]))
                self.assertEqual(
                    [
                        ("artifact", "paper-1", "explainer", "deep"),
                        ("artifact", "paper-1", "translation", "standard"),
                        ("embedding", "all"),
                    ],
                    streams.calls,
                )

        asyncio.run(scenario())

    def test_direct_ingest_selected_never_invents_job_zero(self) -> None:
        async def scenario() -> None:
            async with p3_database_fixture(prefix="study-app-p4-direct-ingest-") as fixture:
                ingest = _DirectIngest()

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
                        json={
                            "candidates": [{"title": "one"}, {"title": "two"}],
                            "deep": True,
                            "downloadPdf": False,
                        },
                    )
                self.assertEqual(200, response.status_code, response.text)
                self.assertEqual(
                    [
                        {"type": "progress", "line": "INGESTED::2"},
                        {"type": "done", "ok": True, "added": 2},
                    ],
                    [json.loads(line) for line in response.text.splitlines()],
                )
                self.assertEqual(0, ingest.confirm_calls)
                self.assertEqual(
                    [([{"title": "one"}, {"title": "two"}], True, False)],
                    ingest.direct_calls,
                )

        asyncio.run(scenario())

    def test_all_streams_match_node_event_contract(self) -> None:
        async def scenario() -> None:
            async with p3_database_fixture(prefix="study-app-p4-ndjson-") as fixture:
                class Services:
                    schema_revision = "20260807_03"
                    legacy = SimpleNamespace(agent=_FakeAgent(), legacy_ingest=_FakeIngest())

                    async def dispose(self) -> None:
                        await fixture.session_factory.kw["bind"].dispose()

                app = create_app(
                    Services(),
                    fixture.session_factory,
                    required_schema_revision="20260807_03",
                )
                requests = (
                    ("POST", "/api/title-translations", {}),
                    ("POST", "/api/search", {"query": "x", "sources": ["arxiv"]}),
                    ("POST", "/api/ingest-selected", {"candidates": [{}]}),
                    ("POST", "/api/verify-venue", {"candidates": []}),
                    ("POST", "/api/explain", {"id": "paper-1"}),
                    ("POST", "/api/explain-batch", {}),
                    ("POST", "/api/translate", {"id": "paper-1"}),
                    ("POST", "/api/recommend", {"id": "paper-1"}),
                    ("POST", "/api/embed", {"scope": "missing"}),
                    ("POST", "/api/semsearch", {"query": "x"}),
                    ("POST", "/api/import-pdfs", {"paths": ["paper.pdf"]}),
                    ("POST", "/api/download-pdfs", {"ids": ["paper-1"]}),
                    ("POST", "/api/norm-venues", {}),
                    ("POST", "/api/cite-build", {}),
                    ("POST", "/api/jobs/confirm", {"jobId": 1, "candidates": [{}]}),
                )
                with TestClient(app) as client:
                    for method, path, body in requests:
                        response = client.request(method, path, json=body)
                        self.assertEqual(200, response.status_code, f"{method} {path}: {response.text}")
                        self.assertEqual(
                            "application/x-ndjson; charset=utf-8",
                            response.headers.get("content-type"),
                            path,
                        )
                        lines = response.content.splitlines()
                        self.assertGreaterEqual(len(lines), 1, path)
                        events = [json.loads(line.decode("utf-8")) for line in lines]
                        terminal_indexes = [
                            index
                            for index, event in enumerate(events)
                            if event.get("type") in {"result", "done"}
                        ]
                        self.assertEqual([len(events) - 1], terminal_indexes, path)
                        self.assertIn(events[-1]["type"], {"result", "done"})
                        for event in events:
                            self.assertIsInstance(event, dict)
                            self.assertIn(event["type"], {"progress", "result", "done"})

        asyncio.run(scenario())

if __name__ == "__main__":
    unittest.main()
