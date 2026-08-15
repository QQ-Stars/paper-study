from __future__ import annotations

import asyncio
import json
import re
import unittest
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import quote


REPO_ROOT = Path(__file__).resolve().parents[2]
INVENTORY_PATH = REPO_ROOT / "contracts" / "legacy_route_inventory.json"
SERVER_PATH = REPO_ROOT / "server.js"

_CONDITIONAL_ROUTE = re.compile(
    r"if\s*\(p\s*===\s*'(?P<path>/api/[^']+)'\s*&&\s*"
    r"req\.method\s*===\s*'(?P<method>GET|POST)'\)"
)


def _legacy_api_routes() -> set[tuple[str, str]]:
    source = SERVER_PATH.read_text(encoding="utf-8")
    routes = {
        (match.group("method"), match.group("path"))
        for match in _CONDITIONAL_ROUTE.finditer(source)
    }
    if not re.search(r"if\s*\(p\s*===\s*'/api/papers'\)", source):
        raise AssertionError("legacy server no longer exposes unguarded GET /api/papers")
    routes.add(("GET", "/api/papers"))
    return routes


def _inventory_document() -> dict[str, object]:
    return json.loads(INVENTORY_PATH.read_text(encoding="utf-8"))


def _assert_inventory_route_surface(app: object) -> None:
    observed: dict[tuple[str, str], int] = {}
    for route in getattr(app, "routes", ()):  # Starlette owns the route registry.
        path = getattr(route, "path", None)
        if not isinstance(path, str):
            continue
        for method in getattr(route, "methods", set()):
            key = (str(method), path)
            observed[key] = observed.get(key, 0) + 1

    for route in _inventory_document()["routes"]:
        key = (route["method"], route["path"])
        count = observed.get(key, 0)
        if count == 0:
            raise AssertionError(f"{key[0]} {key[1]} missing")
        if count != 1:
            raise AssertionError(f"{key[0]} {key[1]} mounted {count} times")


class _GateAgent:
    def __init__(self, added_paper_id: str) -> None:
        self._added_paper_id = added_paper_id

    async def run(self, command: str, *_args: object, **_kwargs: object) -> object:
        stdout = {
            "expand": '["expanded query"]',
            "translate-text": "\u6d4b\u8bd5\u4e2d\u6587\u9898\u540d\n",
            "ingest": "ingest complete\n",
        }.get(command, "{}")
        return SimpleNamespace(returncode=0, stdout=stdout, stderr="")

    async def stream_events(
        self,
        command: str,
        _args: object = (),
        *,
        terminal_type: str = "result",
        terminal_fields: dict[str, object] | None = None,
        stdin: str | bytes | None = None,
    ):
        del stdin
        if command == "title-translations":
            yield {"type": "progress", "stage": "batch", "total": 3}
            papers = (
                ("paper-1", "Seed One"),
                ("paper-2", "Seed Two"),
                (self._added_paper_id, "Route Gate Paper"),
            )
            for index, (paper_id, title) in enumerate(papers, start=1):
                yield {
                    "type": "progress",
                    "stage": "item",
                    "state": "start",
                    "index": index,
                    "total": 3,
                    "id": paper_id,
                    "title": title,
                }
                yield {
                    "type": "progress",
                    "stage": "item",
                    "state": "done",
                    "index": index,
                    "total": 3,
                    "id": paper_id,
                    "title_zh": "\u6d4b\u8bd5\u4e2d\u6587\u9898\u540d",
                }
            yield {
                "type": "result",
                "ok": True,
                "summary": {
                    "total": 3,
                    "done": 3,
                    "failed": [],
                    "cancelled": False,
                },
            }
            return

        progress = {
            "search": ("SEARCH::fixture",),
            "verify-venue": ("VERIFY::fixture",),
            "ingest-selected": ("INGESTED::1",),
            "explain": ("EXPLAIN::fixture",),
            "explain-batch": (
                "BATCH::fixture",
                "STAGE::reindex::\u91cd\u5efa\u8bed\u4e49\u7d22\u5f15\u2026",
            ),
            "translate": ("TRANSLATE::fixture",),
            "import-pdfs": ("IMPORT::fixture",),
            "download-pdfs": ("DOWNLOAD::fixture",),
            "recommend": ("RECOMMEND::fixture",),
            "embed": ("EMBED::fixture",),
            "semsearch": ("SEARCH::fixture",),
            "norm-venues": ("VENUES::fixture",),
            "citegraph": ("CITE::fixture",),
        }
        for line in progress.get(command, ()):
            yield {"type": "progress", "line": line}

        candidate = {
            "source": "semanticscholar",
            "source_id": "fixture-source",
            "title": "Fixture Candidate",
            "authors": ["Ada"],
            "venue": "CVPR",
            "year": "2026",
            "abstract": "abstract",
            "tldr": "tldr",
            "fields": ["Computer Science"],
            "citations": 7,
            "url": "https://example.test/paper",
            "pdf_url": None,
            "arxiv_id": None,
            "doi": None,
            "s2_id": "fixture-s2",
            "ccf": "A",
            "type": "\u65b9\u6cd5",
            "topic": "\u6d4b\u8bd5",
            "task": None,
            "models": [],
            "datasets": [],
            "contribution": None,
            "llm_tldr": None,
            "tags": [],
            "relevance": 0.9,
            "in_library": False,
            "_cid": None,
        }
        payloads: dict[str, dict[str, object]] = {
            "search": {"ok": True, "candidates": [candidate]},
            "verify-venue": {
                "ok": True,
                "verifications": [
                    {
                        "venue": "CVPR",
                        "year": "2026",
                        "matched": True,
                        "skipped": False,
                        "source_of_truth": "dblp",
                        "changed": False,
                        "orig_venue": "CVPR",
                        "ccf": "A",
                        "note": "",
                        "error": False,
                    }
                ],
            },
            "ingest-selected": {"ok": True, "added": 1},
            "explain": {
                "ok": True,
                "markdown": "# Fixture explainer\n",
                "error": "",
            },
            "explain-batch": {
                "ok": True,
                "summary": {
                    "total": 1,
                    "done": 1,
                    "failed": [],
                    "skipped_no_pdf": [],
                },
                "error": "",
            },
            "translate": {
                "ok": True,
                "markdown": "# Fixture translation\n",
                "error": "",
            },
            "import-pdfs": {
                "ok": True,
                "total": 1,
                "added": 1,
                "dup": 0,
                "failed": 0,
                "error": "",
            },
            "download-pdfs": {
                "ok": True,
                "total": 1,
                "downloaded": 1,
                "skipped": 0,
                "failed": 0,
                "error": "",
            },
            "recommend": {"ok": True, "candidates": [candidate], "error": ""},
            "embed": {"ok": True, "indexed": 1, "total": 1, "error": ""},
            "semsearch": {
                "ok": True,
                "results": [{"id": "fixture-paper", "score": 0.95}],
                "error": "",
            },
            "norm-venues": {
                "ok": True,
                "changed": 1,
                "mapping": {"cvpr": "CVPR"},
                "error": "",
            },
            "citegraph": {
                "ok": True,
                "edges": 1,
                "nodes": 2,
                "error": "",
            },
        }
        payload = payloads.get(
            command,
            {"ok": True, **dict(terminal_fields or {})},
        )
        yield {"type": terminal_type, **payload}

    async def confirm_candidates(
        self,
        job_id: int,
        _candidates: object,
        **_kwargs: object,
    ) -> dict[str, object]:
        return {"ok": True, "added": 1, "error": "", "jobId": job_id}


class _GateProcessingStreams:
    def __init__(self, agent: _GateAgent) -> None:
        self._agent = agent

    async def artifact_events(
        self,
        paper_id: str,
        kind: str,
        *,
        profile: str,
    ):
        del paper_id, profile
        command = "explain" if kind == "explainer" else "translate"
        async for event in self._agent.stream_events(command):
            yield event

    async def embedding_events(self, scope: str):
        del scope
        async for event in self._agent.stream_events("embed"):
            yield event


class _GateSettings:
    def __init__(self) -> None:
        self._runtime_root: Path | None = None

    def bind_runtime_root(self, root: Path) -> None:
        self._runtime_root = root.resolve()

    async def view(self) -> dict[str, object]:
        if self._runtime_root is None:
            raise RuntimeError("gate settings runtime root is not bound")
        pdf_dir = self._runtime_root / "pdfs"
        explainer_dir = self._runtime_root / "explainers"
        translation_dir = self._runtime_root / "translations"
        return {
            "provider": "openai",
            "baseUrl": "https://fixture.invalid/v1",
            "model": "fixture-model",
            "apiKeyTail": "****-key",
            "hasApiKey": True,
            "s2KeyTail": "",
            "hasS2Key": False,
            "pdfDir": str(pdf_dir),
            "explainerDir": str(explainer_dir),
            "translationDir": str(translation_dir),
            "defaultPdfDir": str(Path("data") / "pdfs"),
            "defaultExplainerDir": str(Path("data") / "explainers"),
            "defaultTranslationDir": str(Path("data") / "translations"),
            "resolvedPdfDir": str(pdf_dir),
            "resolvedExplainerDir": str(explainer_dir),
            "resolvedTranslationDir": str(translation_dir),
            "researchTheme": "",
            "embedProvider": "local",
            "embedApiBase": "",
            "embedApiModel": "",
            "embedKeyTail": "",
            "hasEmbedKey": False,
        }

    async def update(self, _fields: object) -> None:
        return None

    async def test_llm(self) -> dict[str, object]:
        return {"ok": True, "output": "pong\n"}


def _request_for_inventory_route(
    method: str,
    path: str,
    *,
    scan_directory: Path,
) -> tuple[str, object | None]:
    get_paths = {
        "/api/paper/get": "/api/paper/get?id=paper-1",
        "/api/note": "/api/note?id=paper-1",
        "/api/explainer": "/api/explainer?id=paper-1",
        "/api/translation": "/api/translation?id=paper-1",
        "/api/scan-pdfs": f"/api/scan-pdfs?dir={quote(str(scan_directory), safe='')}",
        "/api/pdf/status": "/api/pdf/status?id=paper-1",
        "/api/jobs/detail": "/api/jobs/detail?id=1",
    }
    payloads: dict[str, object] = {
        "/api/reviews/start": {"id": "paper-1"},
        "/api/reviews/complete": {"id": "paper-1"},
        "/api/note": {"id": "paper-1", "content": "route gate note"},
        "/api/progress": {"id": "paper-1", "status": "已理解"},
        "/api/favorite": {"id": "paper-1", "favorite": True},
        "/api/delete": {"id": "missing-paper"},
        "/api/paper/add": {"title": "Route Gate Paper"},
        "/api/paper/update": {"id": "paper-1", "title": "Seed One"},
        "/api/expand": {"query": "retrieval", "expandN": 2},
        "/api/ingest": {"query": "retrieval", "sources": ["arxiv"]},
        "/api/search": {"query": "retrieval", "sources": ["arxiv"]},
        "/api/verify-venue": {"candidates": []},
        "/api/ingest-selected": {"candidates": [{}]},
        "/api/title-translations": {},
        "/api/explain": {"id": "paper-1"},
        "/api/explain-batch": {},
        "/api/translate": {"id": "paper-1"},
        "/api/translate-text": {"text": "selected text"},
        "/api/import-pdfs": {"paths": ["fixture.pdf"]},
        "/api/download-pdfs": {"ids": ["paper-1"]},
        "/api/recommend": {"id": "paper-1"},
        "/api/embed": {"scope": "missing"},
        "/api/semsearch": {"query": "retrieval"},
        "/api/norm-venues": {},
        "/api/cite-build": {},
        "/api/settings": {},
        "/api/test-llm": {},
        "/api/jobs": {"query": "retrieval", "sources": ["arxiv"]},
        "/api/jobs/delete": {"id": 9999},
        "/api/jobs/confirm": {"jobId": 1, "candidates": [{}]},
        "/api/schedules": {
            "query": "retrieval",
            "sources": ["arxiv"],
            "everyDays": 7,
        },
        "/api/schedules/toggle": {"id": 1, "enabled": False},
        "/api/schedules/delete": {"id": 1},
    }
    if method == "GET":
        return get_paths.get(path, path), None
    return path, payloads.get(path, {})


def _assert_terminal_event(
    testcase: unittest.TestCase,
    body: bytes,
    *,
    terminal_type: str,
    label: str,
) -> None:
    events = [json.loads(line) for line in body.splitlines() if line.strip()]
    testcase.assertGreaterEqual(len(events), 1, label)
    terminals = [
        index
        for index, event in enumerate(events)
        if event.get("type") in {"result", "done"}
    ]
    testcase.assertEqual([len(events) - 1], terminals, label)
    testcase.assertEqual(terminal_type, events[-1].get("type"), label)


def _assert_response_body_parity(
    testcase: unittest.TestCase,
    node_body: bytes,
    fastapi_body: bytes,
    *,
    response_kind: str,
    label: str,
    value_aliases: dict[str, str] | None = None,
) -> None:
    if response_kind == "json":
        node_value = json.loads(node_body)
        fastapi_value = json.loads(fastapi_body)
    elif response_kind == "ndjson":
        node_value = [json.loads(line) for line in node_body.splitlines() if line.strip()]
        fastapi_value = [
            json.loads(line) for line in fastapi_body.splitlines() if line.strip()
        ]
    elif response_kind == "text":
        node_value = node_body.decode("utf-8")
        fastapi_value = fastapi_body.decode("utf-8")
    elif response_kind == "bytes":
        node_value = node_body
        fastapi_value = fastapi_body
    else:
        raise AssertionError(f"{label} has unknown response kind {response_kind!r}")
    if value_aliases:
        node_value = _alias_response_values(node_value, value_aliases)
        fastapi_value = _alias_response_values(fastapi_value, value_aliases)
    testcase.assertEqual(node_value, fastapi_value, f"{label} response body")


def _alias_response_values(value: object, aliases: dict[str, str]) -> object:
    if isinstance(value, str):
        return aliases.get(value, value)
    if isinstance(value, list):
        return [_alias_response_values(item, aliases) for item in value]
    if isinstance(value, dict):
        return {
            key: _alias_response_values(item, aliases)
            for key, item in value.items()
        }
    return value


def _run_full_inventory_gate(testcase: unittest.TestCase) -> None:
    async def scenario() -> None:
        from fastapi.testclient import TestClient

        from backend.app.api.app import create_app
        from backend.app.application.artifact_store import ArtifactStore
        from backend.app.application.legacy_ingest import LegacyIngestService
        from backend.app.application.library_queries import LibraryQueries
        from backend.app.application.paper_library import PaperLibrary
        from backend.app.application.review_scheduler import ReviewScheduler
        from backend.app.application.search_coordinator import SearchCoordinator
        from backend.app.providers.pdf_files import PdfFiles
        from backend.app.repositories.unit_of_work import SqlAlchemyUnitOfWork
        from backend.tests.support.node_contract_server import start_node_contract_server
        from backend.tests.support.p3_database import p3_database_fixture

        async with p3_database_fixture(prefix="study-app-p4-route-gate-") as fixture:
            runtime_root = fixture.database_path.parent / "route-gate-runtime"
            pdf_root = runtime_root / "pdfs"
            markdown_root = runtime_root / "markdown"
            scan_directory = runtime_root / "scan"
            for directory in (pdf_root, markdown_root, scan_directory):
                directory.mkdir(parents=True, exist_ok=True)
            added_paper_id = "route-gate-paper"
            agent = _GateAgent(added_paper_id)
            gate_settings = _GateSettings()
            work_factory = lambda: SqlAlchemyUnitOfWork(fixture.session_factory)
            pdf_files = PdfFiles(root=runtime_root, default_directory=pdf_root)
            legacy_ingest = LegacyIngestService(
                fixture.session_factory,
                provider=agent,
            )
            legacy = SimpleNamespace(
                settings=gate_settings,
                paper_library=PaperLibrary(
                    work_factory,
                    pdf_files=pdf_files,
                    id_factory=lambda _title: added_paper_id,
                ),
                library_queries=LibraryQueries(
                    work_factory,
                    pdf_files=pdf_files,
                ),
                review_scheduler=ReviewScheduler(work_factory),
                artifact_store=ArtifactStore(
                    work_factory,
                    legacy_markdown_root=markdown_root,
                    has_pdf=lambda _paper_id: False,
                ),
                search_coordinator=SearchCoordinator(
                    agent,
                    work_factory,
                    translate_text_direct=lambda _text: (
                        "\u6d4b\u8bd5\u4e2d\u6587\u9898\u540d"
                    ),
                ),
                legacy_ingest=legacy_ingest,
                agent=agent,
                pdf_files=pdf_files,
                processing_streams=_GateProcessingStreams(agent),
            )

            class Services:
                schema_revision = "20260807_03"

                async def dispose(self) -> None:
                    return None

            services = Services()
            services.legacy = legacy
            app = create_app(
                services,
                fixture.session_factory,
                required_schema_revision="20260807_03",
            )
            _assert_inventory_route_surface(app)
            document = _inventory_document()
            exercised: set[tuple[str, str]] = set()
            ndjson_count = 0
            body_failures: list[str] = []
            value_aliases: dict[str, str] = {}
            with (
                start_node_contract_server(database_template=fixture.database_path) as node,
                TestClient(app) as client,
            ):
                gate_settings.bind_runtime_root(node.temporary_root)
                for route in document["routes"]:
                    method = route["method"]
                    path = route["path"]
                    label = f"{method} {path}"
                    resource, payload = _request_for_inventory_route(
                        method,
                        path,
                        scan_directory=scan_directory,
                    )
                    node_status, node_content_type, node_body = node.request(
                        method,
                        resource,
                        payload,
                    )
                    fastapi_response = client.request(
                        method,
                        resource,
                        json=payload,
                    )
                    testcase.assertEqual(route["successStatus"], node_status, label)
                    testcase.assertEqual(
                        route["successStatus"],
                        fastapi_response.status_code,
                        f"{label}: {fastapi_response.text}",
                    )
                    testcase.assertEqual(route["contentType"], node_content_type, label)
                    testcase.assertEqual(
                        route["contentType"],
                        fastapi_response.headers.get("content-type"),
                        label,
                    )
                    if method == "POST" and path == "/api/paper/add":
                        node_id = json.loads(node_body).get("id")
                        fastapi_id = fastapi_response.json().get("id")
                        testcase.assertIsInstance(node_id, str, label)
                        testcase.assertRegex(
                            node_id,
                            r"^manual-route-gate-paper-[a-z0-9]+$",
                            label,
                        )
                        testcase.assertIsInstance(fastapi_id, str, label)
                        testcase.assertNotIn(fastapi_id, {"paper-1", "paper-2"}, label)
                        value_aliases[node_id] = "<generated-paper-id>"
                        value_aliases[fastapi_id] = "<generated-paper-id>"
                    try:
                        _assert_response_body_parity(
                            testcase,
                            node_body.encode("utf-8"),
                            fastapi_response.content,
                            response_kind=route["responseKind"],
                            label=label,
                            value_aliases=value_aliases,
                        )
                    except AssertionError as error:
                        body_failures.append(str(error))
                    if route["responseKind"] == "ndjson":
                        ndjson_count += 1
                        _assert_terminal_event(
                            testcase,
                            node_body.encode("utf-8"),
                            terminal_type=route["terminalType"],
                            label=f"Node {label}",
                        )
                        _assert_terminal_event(
                            testcase,
                            fastapi_response.content,
                            terminal_type=route["terminalType"],
                            label=f"FastAPI {label}",
                        )
                    exercised.add((method, path))

            testcase.assertEqual(48, len(exercised))
            testcase.assertEqual(15, ndjson_count)
            testcase.assertEqual([], body_failures, "\n\n".join(body_failures))

    asyncio.run(scenario())


class LegacyContractInventoryTests(unittest.TestCase):
    def test_inventory_covers_every_server_route(self) -> None:
        document = json.loads(INVENTORY_PATH.read_text(encoding="utf-8"))
        routes = document["routes"]
        self.assertIsInstance(routes, list)

        observed: list[tuple[str, str]] = []
        ndjson_routes = []
        for route in routes:
            self.assertEqual(
                set(route),
                {
                    "method",
                    "path",
                    "responseKind",
                    "successStatus",
                    "contentType",
                    "terminalType",
                },
            )
            method = route["method"]
            path = route["path"]
            self.assertIn(method, {"GET", "POST"})
            self.assertTrue(path.startswith("/api/"))
            self.assertIn(route["responseKind"], {"json", "ndjson", "text", "bytes"})
            self.assertEqual(route["successStatus"], 200)
            self.assertIsInstance(route["contentType"], str)
            observed.append((method, path))
            if route["responseKind"] == "ndjson":
                self.assertIn(route["terminalType"], {"result", "done"})
                ndjson_routes.append(route)
            else:
                self.assertIsNone(route["terminalType"])

        self.assertEqual(len(observed), 48)
        self.assertEqual(len(set(observed)), len(observed))
        self.assertEqual(set(observed), _legacy_api_routes())
        self.assertEqual(len(ndjson_routes), 15)

        self.assertEqual(
            document["staticFamilies"],
            [
                "/pdfbytes",
                "/papers/{paper-id-or-pdf-name}",
                "/workspace/*",
                "/legacy/*",
            ],
        )

    def test_node_golden_capture_uses_isolated_database(self) -> None:
        from backend.tests.support.node_contract_server import start_node_contract_server

        live_database = REPO_ROOT / "data" / "app.db"
        before = (
            live_database.stat().st_size,
            live_database.stat().st_mtime_ns,
            sha256(live_database.read_bytes()).hexdigest(),
        )
        with start_node_contract_server() as server:
            root = server.temporary_root
            self.assertTrue(server.database_path.is_relative_to(root))
            status, content_type, body = server.get("/api/papers")
            self.assertEqual(status, 200)
            self.assertTrue(content_type.startswith("application/json"))
            self.assertEqual(json.loads(body), [])
            self.assertTrue(server.database_path.exists())

        self.assertFalse(root.exists())
        after = (
            live_database.stat().st_size,
            live_database.stat().st_mtime_ns,
            sha256(live_database.read_bytes()).hexdigest(),
        )
        self.assertEqual(after, before)

    def test_gate_detects_missing_route(self) -> None:
        from fastapi import FastAPI

        from backend.app.api.routes.legacy import create_legacy_router

        app = FastAPI()
        app.include_router(create_legacy_router())
        app.router.routes[:] = [
            route
            for route in app.router.routes
            if not (
                getattr(route, "path", None) == "/api/papers"
                and "GET" in getattr(route, "methods", set())
            )
        ]

        with self.assertRaisesRegex(AssertionError, "GET /api/papers missing"):
            _assert_inventory_route_surface(app)

    def test_gate_detects_response_body_drift(self) -> None:
        with self.assertRaisesRegex(AssertionError, "POST /api/example response body"):
            _assert_response_body_parity(
                self,
                b'{"ok":true,"items":[1,null,"fixed"]}',
                b'{"ok":true,"items":[1,"", "fixed"]}',
                response_kind="json",
                label="POST /api/example",
            )

    def test_fastapi_matches_every_inventory_entry(self) -> None:
        _run_full_inventory_gate(self)
