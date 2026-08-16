from __future__ import annotations

import asyncio
import contextlib
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path
import re
import sqlite3
from types import SimpleNamespace
import tempfile
import unittest
from urllib.parse import quote

from fastapi.testclient import TestClient

from backend.app.api.app import create_app
from backend.app.repositories.unit_of_work import SqlAlchemyUnitOfWork
from backend.tests.support.node_contract_server import start_node_contract_server
from backend.tests.support.p3_database import p3_database_fixture


class _NoPdfFiles:
    def open_for_id(self, _paper_id: str, stored_path: object = None) -> None:
        return None

    def resolve_for_id(self, _paper_id: str, stored_path: object = None) -> None:
        return None

    def has_pdf(self, _paper: object) -> bool:
        return False

    async def delete_for_paper(self, _paper_id: str) -> None:
        return None


class _PaperRuntime:
    def __init__(self) -> None:
        self.next_id = "manual-fixture"
        self.now = datetime(2026, 8, 14, tzinfo=timezone.utc)

    def id_factory(self, _title: str) -> str:
        return self.next_id

    def clock(self) -> datetime:
        return self.now


class _SettingsKeyring:
    """Deterministic keyring double for the legacy settings tracer."""

    def __init__(self) -> None:
        self.values: dict[tuple[str, str], str] = {}
        self.calls: list[tuple[str, str, str | None]] = []

    def get_password(self, service: str, username: str) -> str | None:
        self.calls.append(("get", service, username))
        return self.values.get((service, username))

    def set_password(self, service: str, username: str, value: str) -> None:
        self.calls.append(("set", service, username))
        self.values[(service, username)] = value

    def delete_password(self, service: str, username: str) -> None:
        self.calls.append(("delete", service, username))
        self.values.pop((service, username), None)


class _LazySettingsService:
    """Load the production service only after the route exists (for a real RED)."""

    def __init__(
        self,
        *,
        settings_path: Path,
        root: Path,
        credential_service: object,
        environment_snapshot: dict[str, str],
        llm_transport_calls: list[tuple[str, str]],
    ) -> None:
        self._settings_path = settings_path
        self._root = root
        self._credential_service = credential_service
        self._environment_snapshot = environment_snapshot
        self._llm_transport_calls = llm_transport_calls
        self._service: object | None = None

    def _load(self) -> object:
        if self._service is None:
            from backend.app.application.settings import SettingsService

            self._service = SettingsService(
                settings_path=self._settings_path,
                root=self._root,
                credential_service=self._credential_service,
                environment_snapshot=self._environment_snapshot,
                llm_transport=self._record_llm_call,
            )
        return self._service

    def _record_llm_call(self, credential: object, prompt: str) -> bool:
        self._llm_transport_calls.append(
            (getattr(getattr(credential, "kind", None), "value", ""), prompt)
        )
        return True

    def __getattr__(self, name: str) -> object:
        return getattr(self._load(), name)


class _LazyReviewScheduler:
    def __init__(self, session_factory: object) -> None:
        self._session_factory = session_factory
        self._service: object | None = None

    def _load(self) -> object:
        if self._service is None:
            from backend.app.application.review_scheduler import ReviewScheduler

            self._service = ReviewScheduler(
                lambda: SqlAlchemyUnitOfWork(self._session_factory)
            )
        return self._service

    def __getattr__(self, name: str) -> object:
        return getattr(self._load(), name)


class _LazyArtifactStore:
    def __init__(self, session_factory: object, root: Path) -> None:
        self._session_factory = session_factory
        self._root = root
        self._service: object | None = None

    def _load(self) -> object:
        if self._service is None:
            from backend.app.application.artifact_store import ArtifactStore

            self._service = ArtifactStore(
                lambda: SqlAlchemyUnitOfWork(self._session_factory),
                legacy_markdown_root=self._root,
                has_pdf=lambda _paper_id: False,
            )
        return self._service

    def __getattr__(self, name: str) -> object:
        return getattr(self._load(), name)


class _LazyLegacyServices:
    def __init__(self, session_factory: object, runtime: _PaperRuntime) -> None:
        self._session_factory = session_factory
        self._runtime = runtime
        self._services: object | None = None

    def __getattr__(self, name: str) -> object:
        if self._services is None:
            from backend.app.application.library_queries import LibraryQueries
            from backend.app.application.paper_library import PaperLibrary

            work_factory = lambda: SqlAlchemyUnitOfWork(self._session_factory)
            pdf_files = _NoPdfFiles()
            self._services = SimpleNamespace(
                paper_library=PaperLibrary(
                    work_factory,
                    pdf_files=pdf_files,
                    id_factory=self._runtime.id_factory,
                    clock=self._runtime.clock,
                ),
                library_queries=LibraryQueries(
                    work_factory,
                    pdf_files=pdf_files,
                ),
                pdf_files=pdf_files,
            )
        return getattr(self._services, name)


class _FakeDiscoveryAgent:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[str, ...], str | bytes | None]] = []

    async def run(
        self,
        command: str,
        args: tuple[str, ...] | list[str] = (),
        *,
        stdin: str | bytes | None = None,
    ) -> object:
        self.calls.append((command, tuple(args), stdin))
        outputs = {
            "ingest": (0, "ingest complete\n", ""),
            "expand": (0, '["expanded query"]\n', ""),
            "translate-text": (0, "测试译文\n", ""),
        }
        returncode, stdout, stderr = outputs.get(
            command, (1, "", f"unexpected command: {command}\n")
        )
        return SimpleNamespace(
            returncode=returncode,
            stdout=stdout,
            stderr=stderr,
        )


class _LazySearchCoordinator:
    def __init__(self, agent: object, session_factory: object) -> None:
        self._agent = agent
        self._session_factory = session_factory
        self._service: object | None = None

    def _load(self) -> object:
        if self._service is None:
            from backend.app.application.search_coordinator import SearchCoordinator

            async def translate_direct(text: str) -> str:
                if text == "fallback":
                    raise RuntimeError("force deterministic fallback")
                return "测试中文题名"

            self._service = SearchCoordinator(
                self._agent,
                lambda: SqlAlchemyUnitOfWork(self._session_factory),
                translate_text_direct=translate_direct,
            )
        return self._service

    def __getattr__(self, name: str) -> object:
        return getattr(self._load(), name)


class _SafeDiscoverySettings:
    def __init__(self) -> None:
        self.probe_calls = 0

    async def test_llm(self) -> dict[str, object]:
        self.probe_calls += 1
        return {"ok": True, "output": "pong\n"}


class LegacyJsonApiTests(unittest.TestCase):
    def test_discovery_pdf_citation_and_llm_json_routes_match_node(self) -> None:
        async def scenario() -> None:
            async with p3_database_fixture(prefix="study-app-p4-discovery-json-") as fixture:
                with start_node_contract_server(database_template=fixture.database_path) as node:
                    from backend.app.application.library_queries import LibraryQueries
                    from backend.app.providers.pdf_files import PdfFiles

                    runtime_root = fixture.database_path.parents[1] / "discovery-runtime"
                    pdf_root = runtime_root / "pdfs"
                    pdf_root.mkdir(parents=True)
                    payload = b"%PDF-discovery\n"
                    (pdf_root / "paper-1.pdf").write_bytes(payload)
                    node_pdf_root = node.temporary_root / "pdfs"
                    (node_pdf_root / "paper-1.pdf").write_bytes(payload)

                    scan_root = runtime_root / "scan-depth"
                    scan_root.mkdir(parents=True)
                    current = scan_root
                    for depth in range(1, 6):
                        current = current / f"level-{depth}"
                        current.mkdir()
                        (current / f"depth-{depth}.pdf").write_bytes(b"")
                    hidden = scan_root / ".hidden"
                    hidden.mkdir()
                    (hidden / "ignored.pdf").write_bytes(b"")

                    limit_root = runtime_root / "scan-limit"
                    limit_root.mkdir()
                    for index in range(2001):
                        (limit_root / f"paper-{index:04d}.pdf").write_bytes(b"")

                    agent = _FakeDiscoveryAgent()
                    coordinator = _LazySearchCoordinator(agent, fixture.session_factory)
                    settings = _SafeDiscoverySettings()
                    pdf_files = PdfFiles(root=runtime_root, default_directory=pdf_root)
                    library_queries = LibraryQueries(
                        lambda: SqlAlchemyUnitOfWork(fixture.session_factory),
                        pdf_files=pdf_files,
                    )

                    class Services:
                        schema_revision = "20260807_03"
                        legacy = SimpleNamespace(
                            search_coordinator=coordinator,
                            agent=agent,
                            settings=settings,
                            pdf_files=pdf_files,
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
                        ingest_payload = {
                            "query": "graph retrieval",
                            "sources": ["arxiv", "invalid", "dblp"],
                            "years": "2020-2026",
                            "max": 999,
                            "minRelevance": 0.25,
                            "deep": True,
                            "expand": True,
                            "downloadPdf": False,
                        }
                        self._assert_exact_node_fastapi_equal(
                            node, client, "POST", "/api/ingest", ingest_payload
                        )
                        self._assert_exact_node_fastapi_equal(
                            node,
                            client,
                            "POST",
                            "/api/expand",
                            {"query": "graph retrieval", "expandN": 9},
                        )

                        for text, expected_status in (("", 400), ("x" * 6001, 413)):
                            node_response = node.request(
                                "POST", "/api/translate-text", {"text": text}
                            )
                            fast_response = client.post(
                                "/api/translate-text", json={"text": text}
                            )
                            self.assertEqual(expected_status, node_response[0])
                            self._assert_response_equal(node_response, fast_response)
                        self._assert_exact_node_fastapi_equal(
                            node,
                            client,
                            "POST",
                            "/api/translate-text",
                            {"text": "selected text"},
                        )
                        fallback = client.post(
                            "/api/translate-text", json={"text": "fallback"}
                        )
                        self.assertEqual(
                            {"ok": True, "text": "测试译文", "error": ""},
                            fallback.json(),
                        )

                        missing_scan = client.get("/api/scan-pdfs")
                        self.assertEqual(400, missing_scan.status_code)
                        self.assertEqual(
                            {"ok": False, "error": "缺少文件夹路径"},
                            missing_scan.json(),
                        )
                        self._assert_exact_node_fastapi_equal(
                            node,
                            client,
                            "GET",
                            f"/api/scan-pdfs?dir={quote(str(scan_root), safe='')}",
                        )
                        limited = client.get(
                            "/api/scan-pdfs", params={"dir": str(limit_root)}
                        )
                        self.assertEqual(200, limited.status_code, limited.text)
                        self.assertTrue(limited.json()["ok"])
                        self.assertEqual(2000, limited.json()["count"])

                        node_status = node.request(
                            "GET", "/api/pdf/status?id=paper-1"
                        )
                        fast_status = client.get(
                            "/api/pdf/status", params={"id": "paper-1"}
                        )
                        self.assertEqual(node_status[0], fast_status.status_code)
                        node_status_body = _json(node_status[2])
                        fast_status_body = fast_status.json()
                        for key in ("ok", "id", "hasPdf", "size", "canDownload"):
                            self.assertEqual(node_status_body[key], fast_status_body[key], key)
                        self.assertEqual("paper-1.pdf", Path(fast_status_body["path"]).name)

                        self._assert_exact_node_fastapi_equal(
                            node, client, "GET", "/api/citegraph"
                        )
                        self._assert_exact_node_fastapi_equal(
                            node, client, "POST", "/api/test-llm", {}
                        )

                    self.assertEqual(
                        (
                            "ingest",
                            (
                                "--query", "graph retrieval",
                                "--sources", "arxiv,dblp",
                                "--years", "2020-2026",
                                "--max", "50",
                                "--min-relevance", "0.25",
                                "--deep", "--expand", "--no-pdf",
                            ),
                            None,
                        ),
                        agent.calls[0],
                    )
                    self.assertIn(
                        ("expand", ("--query", "graph retrieval", "--expand-n", "9"), None),
                        agent.calls,
                    )
                    self.assertIn(("translate-text", (), "fallback"), agent.calls)
                    self.assertEqual(1, settings.probe_calls)
                    self.assertNotIn("ping", [call[0] for call in agent.calls])

        asyncio.run(scenario())

    def test_settings_use_provider_profiles_and_redacted_credentials(self) -> None:
        async def scenario() -> None:
            with tempfile.TemporaryDirectory(prefix="study-app-p4-settings-") as temp:
                root = Path(temp)
                settings_path = root / "settings.json"
                secrets = {
                    "apiKey": "legacy-llm-secret-1111",
                    "ocrApiKey": "legacy-ocr-secret-2222",
                    "embedApiKey": "legacy-embed-secret-3333",
                    "s2ApiKey": "legacy-s2-secret-4444",
                }
                initial_settings = {
                    **secrets,
                    "provider": "qwen",
                    "baseUrl": "https://llm.example.test/v1",
                    "model": "qwen-study",
                    "timeout": 17,
                    "ocrProvider": "synthetic-ocr",
                    "ocrBaseUrl": "https://ocr.example.test/v1",
                    "ocrModel": "ocr-study",
                    "ocrTimeout": 23,
                    "ocrEnabled": False,
                    "ocrPageBatchSize": 2,
                    "ocrMaxConcurrency": 3,
                    "embedProvider": "openai-compatible",
                    "embedApiBase": "https://embed.example.test/v1",
                    "embedApiModel": "embed-study",
                    "s2Provider": "semantic-scholar",
                    "s2Endpoint": "https://s2.example.test/graph/v1",
                    "pdfDir": "papers",
                    "unknownNonSecret": {"preserve": True},
                }
                settings_path.write_text(
                    json.dumps(initial_settings, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                )

                environment = {
                    "LLM_PROVIDER": "environment-provider-must-lose",
                    "LLM_BASE_URL": "https://environment.example.test",
                    "LLM_MODEL": "environment-model-must-lose",
                    "OCR_PROVIDER": "environment-ocr-must-lose",
                    "EMBED_API_BASE": "https://environment-embed.example.test",
                    "S2_API_BASE": "https://environment-s2.example.test",
                }
                keyring = _SettingsKeyring()
                from backend.app.application.credentials import CredentialService
                from backend.app.providers.credentials import (
                    CompositeCredentialStore,
                    EnvironmentCredentialStore,
                    KeyringCredentialStore,
                    LegacySettingsCredentialStore,
                    SafeCredentialProbe,
                )

                credential_store = CompositeCredentialStore(
                    EnvironmentCredentialStore(environment),
                    KeyringCredentialStore(keyring),
                    LegacySettingsCredentialStore(settings_path),
                )
                credential_service = CredentialService(
                    credential_store,
                    SafeCredentialProbe(),
                )
                llm_calls: list[tuple[str, str]] = []
                settings = _LazySettingsService(
                    settings_path=settings_path,
                    root=root,
                    credential_service=credential_service,
                    environment_snapshot=environment,
                    llm_transport_calls=llm_calls,
                )

                async with p3_database_fixture(
                    prefix="study-app-p4-settings-db-"
                ) as fixture:

                    class Services:
                        schema_revision = "20260807_03"
                        legacy = SimpleNamespace(settings=settings)

                        async def dispose(self) -> None:
                            return None

                    app = create_app(
                        Services(),
                        fixture.session_factory,
                        required_schema_revision="20260807_03",
                    )
                    with TestClient(app) as client:
                        with (
                            contextlib.redirect_stdout(io.StringIO()) as stdout,
                            contextlib.redirect_stderr(io.StringIO()) as stderr,
                        ):
                            view_response = client.get("/api/settings")
                        self.assertEqual(200, view_response.status_code, view_response.text)
                        view = view_response.json()
                        self.assertEqual(
                            {
                                "provider": "qwen",
                                "baseUrl": "https://llm.example.test/v1",
                                "model": "qwen-study",
                                "timeout": 17,
                                "ocrProvider": "synthetic-ocr",
                                "ocrBaseUrl": "https://ocr.example.test/v1",
                                "ocrModel": "ocr-study",
                                "ocrTimeout": 23,
                                "ocrEnabled": False,
                                "ocrPageBatchSize": 2,
                                "ocrMaxConcurrency": 3,
                                "embedProvider": "openai-compatible",
                                "embedApiBase": "https://embed.example.test/v1",
                                "embedApiModel": "embed-study",
                                "s2Provider": "semantic-scholar",
                                "s2Endpoint": "https://s2.example.test/graph/v1",
                            },
                            {key: view.get(key) for key in (
                                "provider", "baseUrl", "model", "timeout",
                                "ocrProvider", "ocrBaseUrl", "ocrModel", "ocrTimeout",
                                "ocrEnabled", "ocrPageBatchSize", "ocrMaxConcurrency",
                                "embedProvider", "embedApiBase", "embedApiModel",
                                "s2Provider", "s2Endpoint",
                            )},
                        )
                        self.assertEqual("****1111", view["apiKeyTail"])
                        self.assertEqual("****2222", view["ocrKeyTail"])
                        self.assertEqual("****3333", view["embedKeyTail"])
                        self.assertEqual("****4444", view["s2KeyTail"])
                        self.assertTrue(view["hasApiKey"])
                        self.assertTrue(view["hasOcrKey"])
                        self.assertTrue(view["hasEmbedKey"])
                        self.assertTrue(view["hasS2Key"])
                        for secret in secrets.values():
                            self.assertNotIn(secret, view_response.text)
                            self.assertNotIn(secret, stdout.getvalue() + stderr.getvalue())
                        self.assertNotIn("Authorization", view_response.text)
                        self.assertNotRegex(view_response.text, re.compile(r"[0-9a-f]{64}"))

                        keyring.calls.clear()
                        update_response = client.post(
                            "/api/settings",
                            json={
                                "provider": "openai",
                                "baseUrl": "https://new-llm.example.test/v1",
                                "model": "new-model",
                                "timeout": 31,
                                "ocrEnabled": True,
                                "ocrPageBatchSize": 4,
                                "ocrMaxConcurrency": 2,
                                "apiKey": "   ",
                                "ocrApiKey": "new-ocr-secret-5555",
                                "embedApiKey": "   ",
                                "s2ApiKey": "   ",
                            },
                        )
                        self.assertEqual(200, update_response.status_code, update_response.text)
                        self.assertEqual({"ok": True}, update_response.json())
                        self.assertEqual(
                            [("set", "study-app", "credential:ocr")],
                            [call for call in keyring.calls if call[0] == "set"],
                        )

                        persisted = json.loads(settings_path.read_text(encoding="utf-8"))
                        self.assertEqual("legacy-llm-secret-1111", persisted["apiKey"])
                        self.assertEqual("new-ocr-secret-5555", persisted["ocrApiKey"])
                        self.assertEqual("legacy-embed-secret-3333", persisted["embedApiKey"])
                        self.assertEqual("legacy-s2-secret-4444", persisted["s2ApiKey"])
                        self.assertEqual({"preserve": True}, persisted["unknownNonSecret"])
                        self.assertEqual("openai", persisted["provider"])
                        self.assertEqual(31, persisted["timeout"])

                        clear_response = client.post(
                            "/api/settings",
                            json={"clearCredentials": ["semantic_scholar"]},
                        )
                        self.assertEqual(200, clear_response.status_code, clear_response.text)
                        persisted = json.loads(settings_path.read_text(encoding="utf-8"))
                        self.assertNotIn("s2ApiKey", persisted)
                        self.assertIn("apiKey", persisted)
                        self.assertIn("ocrApiKey", persisted)
                        self.assertIn("embedApiKey", persisted)

                        with (
                            contextlib.redirect_stdout(io.StringIO()) as stdout,
                            contextlib.redirect_stderr(io.StringIO()) as stderr,
                        ):
                            probe_response = client.post("/api/test-llm", json={})
                        self.assertEqual(200, probe_response.status_code, probe_response.text)
                        self.assertTrue(probe_response.json()["ok"])
                        self.assertEqual("llm", llm_calls[0][0])
                        self.assertEqual(
                            "Return exactly STUDY_APP_CREDENTIAL_OK.\n",
                            llm_calls[0][1],
                        )
                        rendered_probe = probe_response.text + stdout.getvalue() + stderr.getvalue()
                        for secret in secrets.values():
                            self.assertNotIn(secret, rendered_probe)
                        self.assertNotIn("Authorization", rendered_probe)
                        self.assertNotRegex(rendered_probe, re.compile(r"[0-9a-f]{64}"))

        asyncio.run(scenario())

    def test_reviews_and_artifact_reads_match_node_golden(self) -> None:
        async def scenario() -> None:
            async with p3_database_fixture(prefix="study-app-p4-review-golden-") as fixture:
                with start_node_contract_server(database_template=fixture.database_path) as node:
                    class Services:
                        schema_revision = "20260807_03"
                        legacy = SimpleNamespace(
                            review_scheduler=_LazyReviewScheduler(fixture.session_factory),
                            artifact_store=_LazyArtifactStore(
                                fixture.session_factory,
                                Path(__file__).resolve().parents[2] / "paper",
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
                        self._assert_exact_node_fastapi_equal(
                            node, client, "GET", "/api/reviews"
                        )
                        self._assert_exact_node_fastapi_equal(
                            node,
                            client,
                            "POST",
                            "/api/reviews/start",
                            {"id": "paper-2"},
                        )
                        self._assert_exact_node_fastapi_equal(
                            node,
                            client,
                            "POST",
                            "/api/reviews/complete",
                            {"id": "paper-1"},
                        )
                        self._assert_exact_node_fastapi_equal(
                            node,
                            client,
                            "POST",
                            "/api/note",
                            {"id": "paper-1", "content": "updated fixture note"},
                        )
                        self._assert_exact_node_fastapi_equal(
                            node,
                            client,
                            "GET",
                            "/api/note?id=paper-1",
                        )
                        self._assert_exact_node_fastapi_equal(
                            node,
                            client,
                            "GET",
                            "/api/explainer?id=paper-1",
                        )
                        self._assert_exact_node_fastapi_equal(
                            node,
                            client,
                            "GET",
                            "/api/translation?id=paper-1",
                        )
                        self._assert_exact_node_fastapi_equal(
                            node, client, "GET", "/api/title-translations"
                        )
                        self._assert_exact_node_fastapi_equal(
                            node, client, "GET", "/api/explain-batch"
                        )

        asyncio.run(scenario())

    def test_paper_routes_match_node_golden(self) -> None:
        async def scenario() -> None:
            async with p3_database_fixture(prefix="study-app-p4-paper-golden-") as fixture:
                with start_node_contract_server(
                    database_template=fixture.database_path
                ) as node:
                    runtime = _PaperRuntime()

                    class Services:
                        schema_revision = "20260807_03"
                        legacy = _LazyLegacyServices(fixture.session_factory, runtime)

                        async def dispose(self) -> None:
                            return None

                    app = create_app(
                        Services(),
                        fixture.session_factory,
                        required_schema_revision="20260807_03",
                    )
                    with TestClient(app) as client:
                        self._assert_node_fastapi_equal(node, client, "GET", "/api/papers")
                        for unguarded_path in (
                            "/api/papers",
                            "/pdfbytes?id=missing-paper",
                            "/papers/missing-paper.pdf",
                        ):
                            self._assert_exact_node_fastapi_equal(
                                node,
                                client,
                                "POST",
                                unguarded_path,
                            )
                        self._assert_node_fastapi_equal(
                            node,
                            client,
                            "GET",
                            "/api/paper/get?id=paper-1",
                        )
                        self._assert_node_fastapi_equal(
                            node,
                            client,
                            "GET",
                            "/api/paper/get?id=missing-paper",
                        )
                        self._assert_node_fastapi_equal(
                            node,
                            client,
                            "GET",
                            "/api/paper/get",
                        )

                        empty = self._assert_node_fastapi_equal(
                            node,
                            client,
                            "POST",
                            "/api/paper/add",
                            {"title": "   "},
                        )
                        self.assertEqual(400, empty[0])

                        add_payload = {
                            "title": "Golden Paper",
                            "title_zh": "金标准论文",
                            "venue": "computer vision and pattern recognition",
                            "year": 2026,
                            "authors": ["Ada", "Lin"],
                            "abstract": "fixture abstract",
                            "tldr": "fixture tldr",
                            "type": "方法",
                            "topic": "测试",
                        }
                        node_add = node.request("POST", "/api/paper/add", add_payload)
                        self.assertEqual(200, node_add[0], node_add[2])
                        node_add_json = _json(node_add[2])
                        runtime.next_id = str(node_add_json["id"])
                        runtime.now = _node_paper_timestamp(
                            node.database_path,
                            runtime.next_id,
                            "created_at",
                        )
                        fast_add = client.post("/api/paper/add", json=add_payload)
                        self._assert_response_equal(node_add, fast_add)

                        encoded_id = quote(runtime.next_id, safe="")
                        self._assert_node_fastapi_equal(
                            node,
                            client,
                            "GET",
                            f"/api/paper/get?id={encoded_id}",
                        )

                        missing_update = self._assert_node_fastapi_equal(
                            node,
                            client,
                            "POST",
                            "/api/paper/update",
                            {"title": "ignored"},
                        )
                        self.assertEqual(400, missing_update[0])
                        unknown_update = self._assert_node_fastapi_equal(
                            node,
                            client,
                            "POST",
                            "/api/paper/update",
                            {"id": "missing-paper", "title": "unknown"},
                        )
                        self.assertEqual({"ok": True, "changes": 0}, unknown_update[2])

                        update_payload = {
                            "id": runtime.next_id,
                            "title": "Golden Paper Revised",
                            "venue": "cvpr",
                            "authors": ["Ada"],
                            "tldr": "",
                            "ignoredField": "must-not-be-written",
                        }
                        node_update = node.request(
                            "POST",
                            "/api/paper/update",
                            update_payload,
                        )
                        runtime.now = _node_paper_timestamp(
                            node.database_path,
                            runtime.next_id,
                            "updated_at",
                        )
                        fast_update = client.post("/api/paper/update", json=update_payload)
                        self._assert_response_equal(node_update, fast_update)
                        self._assert_node_fastapi_equal(
                            node,
                            client,
                            "GET",
                            f"/api/paper/get?id={encoded_id}",
                        )

                        for path, payload in (
                            ("/api/progress", {"id": runtime.next_id, "status": "已理解"}),
                            ("/api/favorite", {"id": runtime.next_id, "favorite": True}),
                        ):
                            self._assert_node_fastapi_equal(
                                node,
                                client,
                                "POST",
                                path,
                                payload,
                            )
                            self._assert_node_fastapi_equal(
                                node,
                                client,
                                "POST",
                                path,
                                {**payload, "id": "missing-paper"},
                            )

                        self._assert_node_fastapi_equal(
                            node,
                            client,
                            "GET",
                            "/api/papers",
                        )
                        self._assert_node_fastapi_equal(
                            node,
                            client,
                            "POST",
                            "/api/delete",
                            {"id": "missing-paper"},
                        )
                        self._assert_node_fastapi_equal(
                            node,
                            client,
                            "POST",
                            "/api/delete",
                            {"id": runtime.next_id},
                        )
                        deleted = self._assert_node_fastapi_equal(
                            node,
                            client,
                            "GET",
                            f"/api/paper/get?id={encoded_id}",
                        )
                        self.assertIsNone(deleted[2])

        asyncio.run(scenario())

    def _assert_node_fastapi_equal(
        self,
        node: object,
        client: TestClient,
        method: str,
        path: str,
        payload: object | None = None,
    ) -> tuple[int, str, object]:
        node_response = node.request(method, path, payload)
        fast_response = client.request(method, path, json=payload)
        self._assert_response_equal(node_response, fast_response)
        body: object = (
            _json(node_response[2])
            if node_response[1].startswith("application/json")
            else node_response[2]
        )
        return node_response[0], node_response[1], body

    def _assert_exact_node_fastapi_equal(
        self,
        node: object,
        client: TestClient,
        method: str,
        path: str,
        payload: object | None = None,
    ) -> None:
        node_response = node.request(method, path, payload)
        fast_response = client.request(method, path, json=payload)
        self.assertEqual(node_response[0], fast_response.status_code, fast_response.text)
        self.assertEqual(node_response[1], fast_response.headers.get("content-type"))
        if node_response[1].startswith("application/json"):
            self.assertEqual(_json(node_response[2]), fast_response.json())
        else:
            self.assertEqual(node_response[2], fast_response.text)

    def _assert_response_equal(self, node_response: tuple[int, str, str], fast_response: object) -> None:
        node_status, node_content_type, node_body = node_response
        self.assertEqual(node_status, fast_response.status_code, fast_response.text)
        self.assertEqual(node_content_type, fast_response.headers.get("content-type"))
        if node_content_type.startswith("application/json"):
            self.assertEqual(_json(node_body), fast_response.json())
        else:
            self.assertIn("FOREIGN KEY constraint failed", node_body)
            self.assertEqual("FOREIGN KEY constraint failed", fast_response.text)


def _json(value: str) -> object:
    import json

    return json.loads(value)


def _node_paper_timestamp(database_path: object, paper_id: str, column: str) -> datetime:
    if column not in {"created_at", "updated_at"}:
        raise AssertionError("unsafe timestamp column")
    with sqlite3.connect(database_path) as connection:
        raw = connection.execute(
            f"SELECT {column} FROM papers WHERE id=?",
            (paper_id,),
        ).fetchone()[0]
    return datetime.fromisoformat(str(raw).replace("Z", "+00:00")).replace(
        tzinfo=timezone.utc
    )
