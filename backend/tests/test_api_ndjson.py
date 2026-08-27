from __future__ import annotations

import asyncio
import json
import sqlite3
from types import SimpleNamespace
import unittest

from fastapi.testclient import TestClient

from backend.app.api.app import create_app
from backend.app.api.routes.legacy import _durable_artifact_events
from backend.tests.support.p3_database import p3_database_fixture


class _FakeAgent:
    async def run(self, *_args, **_kwargs):
        return SimpleNamespace(returncode=0, stdout="{}", stderr="")


class _StreamingSearchAgent:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[str, ...]]] = []

    async def stream_events(self, command, args, **_kwargs):
        self.calls.append((command, tuple(str(item) for item in args)))
        yield {"type": "progress", "line": "STAGE::search"}
        yield {
            "type": "result",
            "ok": True,
            "candidates": [{"title": "fixture candidate"}],
        }


class _TruncatedAgent:
    async def stream_events(self, *_args, **_kwargs):
        yield {"type": "progress", "line": "STAGE::search"}


class _RejectingArtifactAgent:
    async def run(self, command: str, *_args, **_kwargs):
        raise AssertionError(f"durable stream must not call legacy agent: {command}")


class _CountingArtifactAgent:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def stream_events(self, command: str, *_args, **_kwargs):
        self.calls.append(command)
        yield {"type": "result", "ok": True, "markdown": "# legacy fallback"}


class _EmptyOcrAgent:
    async def stream_events(self, command: str, *_args, **_kwargs):
        assert command == "ocr-md"
        yield {"type": "result", "ok": True, "markdown": ""}


class _EmptyArtifactAgent:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def stream_events(self, command: str, *_args, **_kwargs):
        self.calls.append(command)
        yield {"type": "result", "ok": True, "markdown": ""}


class _FailingArtifactStreams:
    def __init__(self, error: str) -> None:
        self.error = error

    async def artifact_events(self, paper_id: str, kind: str, *, profile: str):
        del paper_id, kind, profile
        yield {"type": "progress", "line": "JOB::queued"}
        yield {"type": "result", "ok": False, "error": self.error}


class _TruncatedArtifactStreams:
    async def artifact_events(self, paper_id: str, kind: str, *, profile: str):
        del paper_id, kind, profile
        yield {"type": "progress", "line": "JOB::queued"}


class _BlockingArtifactStreams:
    def __init__(self) -> None:
        self.progress_emitted = asyncio.Event()
        self.release_terminal = asyncio.Event()
        self.closed = asyncio.Event()

    async def artifact_events(self, paper_id: str, kind: str, *, profile: str):
        del paper_id, kind, profile
        try:
            self.progress_emitted.set()
            yield {"type": "progress", "line": "JOB::running", "jobId": "job-1"}
            await self.release_terminal.wait()
            yield {"type": "result", "ok": True, "markdown": "# durable result"}
        finally:
            self.closed.set()


class _FailingSession:
    def __init__(self, error: Exception) -> None:
        self.error = error

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None

    async def execute(self, *_args, **_kwargs):
        raise self.error


class _FailingSessionFactory:
    def __init__(self, error: Exception) -> None:
        self.error = error

    def __call__(self):
        return _FailingSession(self.error)


class _FailingEmbeddingStreams:
    def __init__(self, error: str) -> None:
        self.error = error

    async def embedding_events(self, scope: str):
        del scope
        yield {"type": "progress", "line": "JOB::queued"}
        yield {
            "type": "result",
            "ok": False,
            "indexed": 0,
            "total": 1,
            "error": self.error,
        }


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


class _TruncatedIngest:
    async def ingest_selected_events(self, *_args, **_kwargs):
        yield {"type": "progress", "line": "INGEST::start"}


class NdjsonApiTests(unittest.TestCase):
    def test_durable_artifact_progress_is_yielded_before_terminal_arrives(self) -> None:
        async def scenario() -> None:
            streams = _BlockingArtifactStreams()
            request = SimpleNamespace(
                app=SimpleNamespace(
                    state=SimpleNamespace(
                        container=SimpleNamespace(
                            legacy=SimpleNamespace(
                                agent=_RejectingArtifactAgent(),
                                processing_streams=streams,
                            )
                        )
                    )
                )
            )
            events = _durable_artifact_events(
                request,
                "paper-1",
                "explainer",
                profile="standard",
                agent_command="explain",
                agent_args=("--id", "paper-1"),
            )
            first_task = asyncio.create_task(anext(events))
            await asyncio.wait_for(streams.progress_emitted.wait(), timeout=0.5)
            try:
                first = await asyncio.wait_for(
                    asyncio.shield(first_task),
                    timeout=0.1,
                )
            except BaseException:
                streams.release_terminal.set()
                await first_task
                await events.aclose()
                raise

            self.assertEqual(
                {"type": "progress", "line": "JOB::running", "jobId": "job-1"},
                first,
            )
            self.assertFalse(streams.release_terminal.is_set())
            streams.release_terminal.set()
            remaining = [event async for event in events]
            await asyncio.wait_for(streams.closed.wait(), timeout=0.5)
            all_events = [first, *remaining]
            terminals = [
                event
                for event in all_events
                if event.get("type") in {"done", "result"}
            ]
            self.assertEqual(1, len(terminals))
            self.assertEqual(terminals[0], all_events[-1])
            self.assertEqual("# durable result", terminals[0]["markdown"])

        asyncio.run(scenario())

    def test_get_ocr_md_database_failure_is_not_reported_as_empty_success(self) -> None:
        class Services:
            schema_revision = "20260807_03"
            legacy = SimpleNamespace()

            async def dispose(self) -> None:
                return None

        app = create_app(
            Services(),
            _FailingSessionFactory(RuntimeError("database connection lost")),
            required_schema_revision="20260807_03",
        )
        with TestClient(app) as client:
            response = client.get("/api/ocr-md?id=paper-1")

        self.assertEqual(500, response.status_code, response.text)
        self.assertEqual("请求处理失败", response.text)

    def test_get_ocr_md_missing_table_remains_an_empty_result(self) -> None:
        class Services:
            schema_revision = "20260807_03"
            legacy = SimpleNamespace()

            async def dispose(self) -> None:
                return None

        app = create_app(
            Services(),
            _FailingSessionFactory(sqlite3.OperationalError("no such table: ocr_markdown")),
            required_schema_revision="20260807_03",
        )
        with TestClient(app) as client:
            response = client.get("/api/ocr-md?id=paper-1")

        self.assertEqual(200, response.status_code, response.text)
        self.assertEqual("", response.text)

    def test_search_route_synthesizes_terminal_when_agent_stream_ends(self) -> None:
        async def scenario() -> None:
            async with p3_database_fixture(prefix="study-app-p4-search-truncated-") as fixture:
                class Services:
                    schema_revision = "20260807_03"
                    legacy = SimpleNamespace(agent=_TruncatedAgent())

                    async def dispose(self) -> None:
                        return None

                app = create_app(
                    Services(),
                    fixture.session_factory,
                    required_schema_revision="20260807_03",
                )
                with TestClient(app) as client:
                    response = client.post(
                        "/api/search",
                        json={"query": "truncated", "sources": ["arxiv"]},
                    )
                self.assertEqual(200, response.status_code, response.text)
                events = [json.loads(line) for line in response.text.splitlines()]
                self.assertEqual("progress", events[0]["type"])
                self.assertEqual(
                    {
                        "type": "result",
                        "ok": False,
                        "candidates": [],
                        "error": "legacy agent stream ended without terminal event",
                    },
                    events[-1],
                )

        asyncio.run(scenario())

    def test_search_route_preserves_options_and_terminal_candidates(self) -> None:
        async def scenario() -> None:
            async with p3_database_fixture(prefix="study-app-p4-search-route-") as fixture:
                agent = _StreamingSearchAgent()

                class Services:
                    schema_revision = "20260807_03"
                    legacy = SimpleNamespace(agent=agent)

                    async def dispose(self) -> None:
                        return None

                app = create_app(
                    Services(),
                    fixture.session_factory,
                    required_schema_revision="20260807_03",
                )
                with TestClient(app) as client:
                    response = client.post(
                        "/api/search",
                        json={
                            "query": "migration regression",
                            "sources": ["arxiv"],
                            "years": "2020-2025",
                            "max": 4,
                            "minRelevance": 0.7,
                            "expand": True,
                            "onlyA": True,
                            "queries": ["migration regression", "compatibility"],
                        },
                    )
                self.assertEqual(200, response.status_code, response.text)
                self.assertEqual(
                    "application/x-ndjson; charset=utf-8",
                    response.headers.get("content-type"),
                )
                events = [json.loads(line) for line in response.text.splitlines()]
                self.assertEqual("progress", events[0]["type"])
                self.assertEqual("result", events[-1]["type"])
                self.assertEqual([{"title": "fixture candidate"}], events[-1]["candidates"])
                self.assertEqual(
                    (
                        "search",
                        (
                            "--query",
                            "migration regression",
                            "--sources",
                            "arxiv",
                            "--years",
                            "2020-2025",
                            "--max",
                            "4",
                            "--min-relevance",
                            "0.7",
                            "--expand",
                            "--only-a",
                            "--queries",
                            '["migration regression", "compatibility"]',
                        ),
                    ),
                    agent.calls[0],
                )

        asyncio.run(scenario())

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

    def test_durable_artifact_failure_does_not_fallback_unless_source_identity_missing(self) -> None:
        async def scenario() -> None:
            for durable_error, should_fallback in (
                ("LLM_RATE_LIMITED", False),
                ("SOURCE_IDENTITY_MISSING", True),
            ):
                with self.subTest(durable_error=durable_error):
                    async with p3_database_fixture(
                        prefix="study-app-p4-durable-fallback-"
                    ) as fixture:
                        agent = _CountingArtifactAgent()
                        streams = _FailingArtifactStreams(durable_error)

                        class Services:
                            schema_revision = "20260807_03"
                            legacy = SimpleNamespace(
                                agent=agent,
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
                            response = client.post(
                                "/api/explain",
                                json={"id": "paper-1"},
                            )

                        self.assertEqual(200, response.status_code, response.text)
                        events = [
                            json.loads(line) for line in response.text.splitlines()
                        ]
                        self.assertEqual("result", events[-1]["type"])
                        if should_fallback:
                            self.assertTrue(events[-1]["ok"])
                            self.assertEqual("# legacy fallback", events[-1]["markdown"])
                            self.assertEqual(["explain"], agent.calls)
                        else:
                            self.assertFalse(events[-1]["ok"])
                            self.assertEqual(durable_error, events[-1]["error"])
                            self.assertEqual([], agent.calls)

        asyncio.run(scenario())

    def test_durable_artifact_without_terminal_is_failed_without_legacy_fallback(self) -> None:
        async def scenario() -> None:
            async with p3_database_fixture(
                prefix="study-app-p4-durable-no-terminal-"
            ) as fixture:
                agent = _CountingArtifactAgent()

                class Services:
                    schema_revision = "20260807_03"
                    legacy = SimpleNamespace(
                        agent=agent,
                        processing_streams=_TruncatedArtifactStreams(),
                    )

                    async def dispose(self) -> None:
                        return None

                app = create_app(
                    Services(),
                    fixture.session_factory,
                    required_schema_revision="20260807_03",
                )
                with TestClient(app) as client:
                    response = client.post("/api/explain", json={"id": "paper-1"})

                self.assertEqual(200, response.status_code, response.text)
                events = [json.loads(line) for line in response.text.splitlines()]
                self.assertEqual("result", events[-1]["type"])
                self.assertFalse(events[-1]["ok"])
                self.assertEqual("PROCESSING_STREAM_NO_TERMINAL", events[-1]["error"])
                self.assertEqual([], agent.calls)

        asyncio.run(scenario())

    def test_durable_embedding_failure_falls_back_only_for_compatibility_errors(self) -> None:
        async def scenario() -> None:
            for durable_error, should_fallback in (
                ("PROCESSING_JOB_FAILED", False),
                ("SOURCE_IDENTITY_MISSING", True),
                ("EMBEDDING_PROFILE_UNAVAILABLE", True),
            ):
                with self.subTest(durable_error=durable_error):
                    async with p3_database_fixture(
                        prefix="study-app-p4-durable-embedding-fallback-"
                    ) as fixture:
                        agent = _CountingArtifactAgent()
                        streams = _FailingEmbeddingStreams(durable_error)

                        class Services:
                            schema_revision = "20260807_03"
                            legacy = SimpleNamespace(
                                agent=agent,
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
                            response = client.post("/api/embed", json={"scope": "all"})

                        self.assertEqual(200, response.status_code, response.text)
                        events = [json.loads(line) for line in response.text.splitlines()]
                        self.assertEqual("progress", events[0]["type"])
                        if should_fallback:
                            self.assertTrue(events[-1]["ok"])
                            self.assertEqual("# legacy fallback", events[-1].get("markdown"))
                            self.assertEqual(["embed"], agent.calls)
                        else:
                            self.assertEqual(
                                {
                                    "type": "result",
                                    "ok": False,
                                    "indexed": 0,
                                    "total": 1,
                                    "error": durable_error,
                                },
                                events[-1],
                            )
                            self.assertEqual([], agent.calls)

        asyncio.run(scenario())

    def test_ocr_success_requires_nonempty_markdown(self) -> None:
        async def scenario() -> None:
            async with p3_database_fixture(prefix="study-app-p4-ocr-empty-terminal-") as fixture:
                class Services:
                    schema_revision = "20260807_03"
                    legacy = SimpleNamespace(
                        agent=_EmptyOcrAgent(),
                        pdf_files=SimpleNamespace(
                            resolve_for_id=lambda _paper_id, stored_path=None: object()
                        ),
                    )

                    async def dispose(self) -> None:
                        return None

                app = create_app(
                    Services(),
                    fixture.session_factory,
                    required_schema_revision="20260807_03",
                )
                with TestClient(app) as client:
                    response = client.post("/api/ocr-md", json={"id": "paper-1"})
                self.assertEqual(200, response.status_code, response.text)
                events = [json.loads(line) for line in response.text.splitlines()]
                self.assertEqual(
                    {
                        "type": "result",
                        "ok": False,
                        "markdown": "",
                        "error": "OCR 结果为空，请重试或检查 OCR 配置",
                    },
                    events[-1],
                )

        asyncio.run(scenario())

    def test_ocr_database_lookup_failure_is_not_converted_to_pdf_processing(self) -> None:
        class _BrokenLibraryQueries:
            async def get_paper(self, _paper_id: str) -> dict[str, object] | None:
                raise RuntimeError("database connection lost")

        class Services:
            schema_revision = "20260807_03"
            legacy = SimpleNamespace(
                agent=_EmptyOcrAgent(),
                library_queries=_BrokenLibraryQueries(),
                pdf_files=SimpleNamespace(
                    resolve_for_id=lambda _paper_id, stored_path=None: object()
                ),
            )

            async def dispose(self) -> None:
                return None

        app = create_app(
            Services(),
            _FailingSessionFactory(RuntimeError("database connection lost")),
            required_schema_revision="20260807_03",
        )
        with TestClient(app) as client:
            response = client.post("/api/ocr-md", json={"id": "paper-1"})

        self.assertEqual(500, response.status_code, response.text)
        self.assertEqual(
            {"ok": False, "error": "请求处理失败"},
            response.json(),
        )

    def test_legacy_explain_and_translate_success_require_nonempty_markdown(self) -> None:
        async def scenario() -> None:
            for path, command in (
                ("/api/explain", "explain"),
                ("/api/translate", "translate"),
            ):
                with self.subTest(path=path):
                    async with p3_database_fixture(
                        prefix="study-app-p4-empty-artifact-terminal-"
                    ) as fixture:
                        agent = _EmptyArtifactAgent()

                        class Services:
                            schema_revision = "20260807_03"
                            legacy = SimpleNamespace(
                                agent=agent,
                                processing_streams=_FailingArtifactStreams(
                                    "SOURCE_IDENTITY_MISSING"
                                ),
                            )

                            async def dispose(self) -> None:
                                return None

                        app = create_app(
                            Services(),
                            fixture.session_factory,
                            required_schema_revision="20260807_03",
                        )
                        with TestClient(app) as client:
                            response = client.post(path, json={"id": "paper-1"})

                    self.assertEqual(200, response.status_code, response.text)
                    events = [json.loads(line) for line in response.text.splitlines()]
                    self.assertEqual(1, len([e for e in events if e["type"] == "result"]))
                    self.assertEqual("result", events[-1]["type"])
                    self.assertFalse(events[-1]["ok"])
                    self.assertEqual("", events[-1]["markdown"])
                    self.assertEqual("模型返回为空，请检查模型配置后重试", events[-1]["error"])
                    self.assertEqual([command], agent.calls)

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

    def test_ingest_route_synthesizes_terminal_when_service_stream_ends(self) -> None:
        async def scenario() -> None:
            async with p3_database_fixture(prefix="study-app-p4-ingest-truncated-") as fixture:
                class Services:
                    schema_revision = "20260807_03"
                    legacy = SimpleNamespace(legacy_ingest=_TruncatedIngest())

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
                        json={"candidates": [{"title": "truncated"}]},
                    )
                events = [json.loads(line) for line in response.text.splitlines()]
                self.assertEqual("progress", events[0]["type"])
                self.assertEqual(
                    {
                        "type": "done",
                        "ok": False,
                        "added": 0,
                        "error": "导入流未返回完成状态",
                    },
                    events[-1],
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
