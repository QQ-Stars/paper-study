from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest

from fastapi.testclient import TestClient
from sqlalchemy import text

from backend.app.api.app import create_app
from backend.app.application.credentials import CredentialService
from backend.app.application.settings import SettingsService
from backend.app.domain import Credential, CredentialKind, CredentialStatus
from backend.app.providers.credentials.probe import SafeCredentialProbe
from backend.tests.support.p3_database import p3_database_fixture


class _MemoryCredentialStore:
    """Deterministic credential seam used only by this HTTP contract test."""

    def __init__(self) -> None:
        self.values: dict[CredentialKind, str] = {}

    async def get(self, kind: CredentialKind) -> Credential | None:
        normalized = CredentialKind(kind)
        value = self.values.get(normalized)
        return Credential(normalized, value) if value else None

    async def is_configured(self, kind: CredentialKind) -> bool:
        return await self.get(kind) is not None

    async def key_tail(self, kind: CredentialKind) -> str | None:
        credential = await self.get(kind)
        return f"****{credential.value[-4:]}" if credential else None

    async def status(self, kind: CredentialKind) -> CredentialStatus:
        credential = await self.get(kind)
        return CredentialStatus(
            kind=kind,
            has_key=credential is not None,
            key_tail=await self.key_tail(kind),
            environment_managed=False,
        )

    async def update(self, kind: CredentialKind, submitted_value: str) -> CredentialStatus:
        self.values[CredentialKind(kind)] = submitted_value
        return await self.status(kind)

    async def clear(self, kind: CredentialKind) -> CredentialStatus:
        self.values.pop(CredentialKind(kind), None)
        return await self.status(kind)


class _SettingsAwareAgent:
    """Fake agent that observes the public settings/credential seam per call."""

    def __init__(self, settings: SettingsService, artifacts: "_ArtifactStore") -> None:
        self.settings = settings
        self.artifacts = artifacts
        self.calls: list[dict[str, object]] = []

    async def stream_events(
        self,
        command: str,
        args: object = (),
        *,
        terminal_type: str = "result",
        terminal_fields: dict[str, object] | None = None,
        **_kwargs: object,
    ):
        runtime = self.settings.llm_runtime_settings()
        credential = await self.settings.credential_service.credential(CredentialKind.LLM)
        self.calls.append(
            {
                "command": command,
                "args": tuple(str(value) for value in args),
                "provider": runtime.provider,
                "baseUrl": runtime.base_url,
                "model": runtime.model,
                "timeout": runtime.timeout_seconds,
                "apiKey": credential.value if credential else None,
            }
        )
        yield {"type": "progress", "line": f"fixture::{command}"}

        fields = dict(terminal_fields or {})
        if command in {"title-translations", "explain-batch", "ocr-md-batch"}:
            summary_field = next(iter(fields), "summary")
            fields[summary_field] = {
                "total": 1,
                "done": 1,
                "failed": [],
                "skipped_no_pdf": [],
            }
        elif command in {"explain", "translate", "ocr-md"}:
            markdown = f"# fixture {command}\n\nlatest provider result\n"
            fields["markdown"] = markdown
            if command == "explain":
                self.artifacts.contents[("paper-1", "explainer")] = markdown
            elif command == "translate":
                self.artifacts.contents[("paper-1", "translation")] = markdown
        yield {"type": terminal_type, "ok": True, **fields}


class _ArtifactStore:
    def __init__(self) -> None:
        self.contents: dict[tuple[str, str], str] = {}

    async def read_content(self, paper_id: str, kind: str) -> str | None:
        return self.contents.get((paper_id, kind))

    async def title_translation_status(self) -> dict[str, object]:
        return {"total": 1, "translated": 0, "pending": 1}

    async def explain_batch_status(self) -> dict[str, object]:
        return {"total": 1, "explained": 0, "pending": 1}


class _FixturePdfFiles:
    def resolve_for_id(self, _paper_id: str, *, stored_path: str | None = None) -> object:
        del stored_path
        return object()


class SettingsAndProcessingHttpTests(unittest.TestCase):
    def test_saved_settings_are_used_by_next_http_processing_request(self) -> None:
        async def scenario() -> None:
            async with p3_database_fixture(prefix="study-app-http-settings-processing-") as fixture:
                with tempfile.TemporaryDirectory(prefix="study-app-http-settings-") as temp:
                    root = Path(temp)
                    settings_path = root / "settings.json"
                    credentials = _MemoryCredentialStore()
                    holder: dict[str, SettingsService] = {}
                    probe_calls: list[tuple[str, str, str, str | None]] = []

                    async def transport(credential: Credential, _prompt: str) -> bool:
                        runtime = holder["service"].llm_runtime_settings()
                        probe_calls.append(
                            (
                                runtime.provider,
                                runtime.base_url or "",
                                runtime.model,
                                credential.value,
                            )
                        )
                        return True

                    settings = SettingsService(
                        settings_path=settings_path,
                        root=root,
                        credential_service=CredentialService(
                            credentials,
                            SafeCredentialProbe(llm_transport=transport),
                        ),
                        environment_snapshot={
                            "LLM_PROVIDER": "environment-provider",
                            "LLM_BASE_URL": "https://environment.invalid/v1",
                            "LLM_MODEL": "environment-model",
                        },
                        default_dirs={
                            "pdfDir": root / "pdfs",
                            "explainerDir": root / "explainers",
                            "translationDir": root / "translations",
                            "ocrMarkdownDir": root / "ocr",
                        },
                        llm_transport=transport,
                    )
                    holder["service"] = settings
                    artifacts = _ArtifactStore()
                    agent = _SettingsAwareAgent(settings, artifacts)

                    async with fixture.session_factory() as session:
                        await session.execute(
                            text(
                                "INSERT INTO ocr_markdown(paper_id,content) "
                                "VALUES ('paper-1', '# seeded OCR')"
                            )
                        )
                        await session.commit()

                    class Services:
                        schema_revision = "20260807_03"
                        legacy = SimpleNamespace(
                            settings=settings,
                            agent=agent,
                            artifact_store=artifacts,
                            pdf_files=_FixturePdfFiles(),
                            library_queries=SimpleNamespace(
                                get_paper=lambda _paper_id: _async_paper()
                            ),
                        )

                        async def dispose(self) -> None:
                            return None

                    async def _async_paper() -> dict[str, object]:
                        return {"id": "paper-1", "pdf_path": "paper-1.pdf"}

                    app = create_app(
                        Services(),
                        fixture.session_factory,
                        required_schema_revision="20260807_03",
                    )
                    with TestClient(app) as client:
                        saved = client.post(
                            "/api/settings",
                            json={
                                "provider": "saved-provider",
                                "baseUrl": "https://saved.invalid/v1",
                                "model": "saved-model",
                                "llmTimeout": 2300,
                                "apiKey": "saved-secret-1234",
                            },
                        )
                        self.assertEqual(200, saved.status_code, saved.text)
                        self.assertEqual({"ok": True}, saved.json())

                        tested = client.post("/api/test-llm")
                        self.assertEqual(200, tested.status_code, tested.text)
                        self.assertEqual(True, tested.json()["ok"], tested.text)
                        self.assertNotIn("saved-secret-1234", tested.text)

                        requests = (
                            ("/api/explain", {"id": "paper-1"}),
                            ("/api/translate", {"id": "paper-1"}),
                            ("/api/ocr-md", {"id": "paper-1"}),
                            ("/api/title-translations", {"limit": 7}),
                            ("/api/explain-batch", {"limit": 4}),
                            ("/api/ocr-md-batch", {"limit": 6}),
                        )
                        for path, body in requests:
                            response = client.post(path, json=body)
                            self.assertEqual(200, response.status_code, f"{path}: {response.text}")
                            self.assertEqual(
                                "application/x-ndjson; charset=utf-8",
                                response.headers.get("content-type"),
                                path,
                            )
                            events = _ndjson(response)
                            self.assertEqual(1, len([event for event in events if event["type"] in {"done", "result"}]), path)
                            self.assertIn(events[-1]["type"], {"done", "result"}, path)
                            self.assertTrue(events[-1]["ok"], path)

                        changed = client.post(
                            "/api/settings",
                            json={
                                "provider": "next-provider",
                                "baseUrl": "https://next.invalid/v1",
                                "model": "next-model",
                                "llmTimeout": 4100,
                                "apiKey": "next-secret-5678",
                            },
                        )
                        self.assertEqual(200, changed.status_code, changed.text)

                        next_tested = client.post("/api/test-llm")
                        self.assertEqual(200, next_tested.status_code, next_tested.text)
                        self.assertTrue(next_tested.json()["ok"], next_tested.text)

                        next_explain = client.post(
                            "/api/explain", json={"id": "paper-1"}
                        )
                        self.assertEqual(200, next_explain.status_code, next_explain.text)
                        next_events = _ndjson(next_explain)
                        self.assertTrue(next_events[-1]["ok"])

                        self.assertEqual(
                            [
                                ("saved-provider", "https://saved.invalid/v1", "saved-model", "saved-secret-1234"),
                                ("next-provider", "https://next.invalid/v1", "next-model", "next-secret-5678"),
                            ],
                            probe_calls,
                        )
                        self.assertEqual(7, len(agent.calls))
                        self.assertEqual(
                            [
                                ("explain", ("--id", "paper-1")),
                                ("translate", ("--id", "paper-1")),
                                ("ocr-md", ("--id", "paper-1")),
                                ("title-translations", ("--limit", "7")),
                                ("explain-batch", ("--limit", "4")),
                                ("ocr-md-batch", ("--limit", "6")),
                                ("explain", ("--id", "paper-1")),
                            ],
                            [(str(call["command"]), call["args"]) for call in agent.calls],
                        )
                        for observed in agent.calls[:6]:
                            self.assertEqual(
                                {
                                    "provider": "saved-provider",
                                    "baseUrl": "https://saved.invalid/v1",
                                    "model": "saved-model",
                                    "timeout": 2.3,
                                    "apiKey": "saved-secret-1234",
                                },
                                {key: observed[key] for key in ("provider", "baseUrl", "model", "timeout", "apiKey")},
                            )
                        self.assertEqual("next-provider", agent.calls[-1]["provider"])
                        self.assertEqual("next-secret-5678", agent.calls[-1]["apiKey"])

                        for path, expected in (
                            ("/api/explainer?id=paper-1", "# fixture explain"),
                            ("/api/translation?id=paper-1", "# fixture translate"),
                            ("/api/ocr-md?id=paper-1", "# seeded OCR"),
                        ):
                            fetched = client.get(path)
                            self.assertEqual(200, fetched.status_code, path)
                            self.assertIn(expected, fetched.text, path)

        asyncio.run(scenario())


def _ndjson(response: object) -> list[dict[str, object]]:
    body = getattr(response, "text")
    return [json.loads(line) for line in body.splitlines() if line.strip()]


if __name__ == "__main__":
    unittest.main()
