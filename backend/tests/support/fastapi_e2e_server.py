from __future__ import annotations

import asyncio
from contextlib import closing
from datetime import datetime, timezone
import json
import socket
import sqlite3
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from typing import Any, AsyncIterator

import uvicorn

from backend.app.api.app import create_app
from backend.app.api.dependencies import ApiDependencies
from backend.app.application.artifact_store import ArtifactStore
from backend.app.application.library_queries import LibraryQueries
from backend.app.application.paper_library import PaperLibrary
from backend.app.application.review_scheduler import ReviewScheduler
from backend.app.application.search_coordinator import SearchCoordinator
from backend.app.config import DatabaseSettings
from backend.app.infrastructure.database import create_async_session_factory
from backend.app.providers.pdf_files import PdfFiles
from backend.app.repositories.unit_of_work import SqlAlchemyUnitOfWork
from backend.app.runtime import ApiSettings
from backend.tests.support.p1_database import create_legacy_database, run_alembic


_REVISION = "20260807_03"
_NOW = datetime(2026, 8, 14, 9, 0, tzinfo=timezone.utc)


class _FakeSettings:
    async def view(self) -> dict[str, object]:
        return {
            "provider": "fixture",
            "baseUrl": "http://127.0.0.1/disabled",
            "model": "fixture-model",
            "apiKeyTail": "",
            "hasApiKey": False,
            "s2KeyTail": "",
            "hasS2Key": False,
            "pdfDir": "pdfs",
            "explainerDir": "explainers",
            "translationDir": "translations",
            "defaultPdfDir": "pdfs",
            "defaultExplainerDir": "explainers",
            "defaultTranslationDir": "translations",
            "resolvedPdfDir": "pdfs",
            "resolvedExplainerDir": "explainers",
            "resolvedTranslationDir": "translations",
            "researchTheme": "FastAPI parity",
            "embedProvider": "fixture",
            "embedApiBase": "http://127.0.0.1/disabled",
            "embedApiModel": "fixture-embedding",
            "embedKeyTail": "",
            "hasEmbedKey": False,
        }

    async def update(self, _body: object) -> None:
        return None

    async def test_llm(self) -> dict[str, object]:
        return {"ok": True, "output": "fixture connection verified\n"}


class _FakeAgent:
    def __init__(self, database_path: Path) -> None:
        self._database_path = database_path

    async def run(
        self,
        command: str,
        _args: object = (),
        *,
        stdin: str | bytes | None = None,
    ) -> object:
        del stdin
        outputs = {
            "expand": '["fastapi parity query"]\n',
            "translate-text": "固定译文\n",
        }
        return SimpleNamespace(
            returncode=0,
            stdout=outputs.get(command, "fixture complete\n"),
            stderr="",
        )

    async def stream_events(
        self,
        command: str,
        _args: object = (),
        *,
        terminal_type: str = "result",
        terminal_fields: dict[str, object] | None = None,
        stdin: str | bytes | None = None,
    ) -> AsyncIterator[dict[str, object]]:
        del stdin
        yield {"type": "progress", "line": f"fixture::{command}"}
        result = {
            "type": terminal_type,
            "ok": True,
            **dict(terminal_fields or {}),
        }
        if command == "explain":
            markdown = (
                "# FastAPI parity explainer\n\n"
                "The isolated provider returned this deterministic result.\n"
            )
            with closing(sqlite3.connect(self._database_path)) as connection:
                connection.execute(
                    "UPDATE papers SET explainer = ?, updated_at = ? WHERE id = 'paper-1'",
                    (markdown, "2026-08-14 09:00:00"),
                )
                connection.commit()
            result["markdown"] = markdown
        yield result


class _FakeProcessingStreams:
    def __init__(self, agent: _FakeAgent) -> None:
        self._agent = agent

    async def artifact_events(self, paper_id: str, kind: str, *, profile: str):
        args = ["--id", paper_id]
        if profile == "deep":
            args.append("--deep")
        command = "explain" if kind == "explainer" else "translate"
        async for event in self._agent.stream_events(
            command,
            args,
            terminal_fields={"markdown": ""},
        ):
            yield event

    async def embedding_events(self, scope: str):
        async for event in self._agent.stream_events(
            "embed",
            ["--scope", scope],
            terminal_fields={"indexed": 0, "total": 0},
        ):
            yield event


class _Container:
    schema_revision = _REVISION

    def __init__(self, database_path: Path, session_factory: Any, pdf_files: PdfFiles) -> None:
        work_factory = lambda: SqlAlchemyUnitOfWork(session_factory)
        agent = _FakeAgent(database_path)
        self.session_factory = session_factory
        self.pdf_files = pdf_files
        self.legacy = SimpleNamespace(
            settings=_FakeSettings(),
            paper_library=PaperLibrary(
                work_factory,
                pdf_files=pdf_files,
                id_factory=lambda _title: "paper-e2e-added",
                clock=lambda: _NOW,
            ),
            library_queries=LibraryQueries(work_factory, pdf_files=pdf_files),
            review_scheduler=ReviewScheduler(work_factory, clock=lambda: _NOW),
            artifact_store=ArtifactStore(
                work_factory,
                read_mode="legacy",
                has_pdf=lambda paper_id: pdf_files.resolve_for_id(paper_id) is not None,
                clock=lambda: _NOW,
            ),
            search_coordinator=SearchCoordinator(agent, work_factory),
            agent=agent,
            pdf_files=pdf_files,
            processing_streams=_FakeProcessingStreams(agent),
        )

    async def dispose(self) -> None:
        await self.session_factory.kw["bind"].dispose()


def _pdf_fixture() -> bytes:
    content = "\n".join(
        (
            "BT",
            "/F1 20 Tf",
            "72 720 Td",
            "(FastAPI parity deterministic PDF fixture) Tj",
            "ET",
        )
    )
    objects = (
        "<< /Type /Catalog /Pages 2 0 R >>",
        "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        "/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
        f"<< /Length {len(content.encode('ascii'))} >>\nstream\n{content}\nendstream",
        "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    )
    source = "%PDF-1.4\n%fixture\n"
    offsets = [0]
    for index, value in enumerate(objects, start=1):
        offsets.append(len(source.encode("ascii")))
        source += f"{index} 0 obj\n{value}\nendobj\n"
    xref_offset = len(source.encode("ascii"))
    source += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n"
    source += "".join(f"{offset:010d} 00000 n \n" for offset in offsets[1:])
    source += (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
        f"startxref\n{xref_offset}\n%%EOF\n"
    )
    return source.encode("ascii")


def _prepare_database(root: Path) -> tuple[Path, PdfFiles]:
    database_path = root / "database" / "app.db"
    create_legacy_database(database_path)
    run_alembic(database_path, "20260807_02")
    run_alembic(database_path, _REVISION)
    pdf_root = root / "pdfs"
    pdf_root.mkdir()
    pdf_path = pdf_root / "paper-1.pdf"
    pdf_path.write_bytes(_pdf_fixture())
    with closing(sqlite3.connect(database_path)) as connection:
        connection.execute(
            "UPDATE papers SET title = ?, title_zh = ?, title_norm = ?, authors = ?, "
            "venue = ?, year = ?, abstract = ?, tldr = ?, contribution = ?, type = ?, "
            "topic = ?, pdf_path = ?, created_at = ?, updated_at = ? WHERE id = 'paper-1'",
            (
                "FastAPI Parity Paper",
                "FastAPI 端到端校验论文",
                "fastapiparitypaper",
                '["Ada Test","Lin Fixture"]',
                "CVPR",
                "2026",
                "An isolated FastAPI parity paper.",
                "A deterministic browser-to-database fixture.",
                "Verifies the candidate HTTP boundary.",
                "方法",
                "兼容迁移",
                str(pdf_path),
                "2026-08-01 00:00:00",
                "2026-08-14 09:00:00",
            ),
        )
        connection.execute(
            "UPDATE paper_reviews SET started_at = '2026-08-01', next_due_at = '2026-08-02', "
            "updated_at = '2026-08-01' WHERE paper_id = 'paper-1'"
        )
        connection.commit()
    return database_path, PdfFiles(root=root, default_directory=pdf_root)


async def _serve() -> None:
    for name, expected in (
        ("API_PROCESS_ROLE", "api"),
        ("OCR_ENABLED", "0"),
        ("OBSIDIAN_ENABLED", "0"),
    ):
        if __import__("os").environ.get(name) != expected:
            raise RuntimeError(f"{name} must equal {expected} for the E2E fixture")

    with tempfile.TemporaryDirectory(prefix="study-app-fastapi-e2e-") as temp_dir:
        database_path, pdf_files = _prepare_database(Path(temp_dir))
        session_factory = create_async_session_factory(DatabaseSettings(database_path))
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(("127.0.0.1", 0))
        listener.listen(128)
        port = int(listener.getsockname()[1])
        container = _Container(database_path, session_factory, pdf_files)
        app = create_app(
            ApiSettings(bind_host="127.0.0.1", bind_port=port),
            ApiDependencies(container, session_factory),
            required_schema_revision=_REVISION,
        )
        server = uvicorn.Server(
            uvicorn.Config(
                app,
                host="127.0.0.1",
                port=port,
                access_log=False,
                log_level="warning",
            )
        )
        serve_task = asyncio.create_task(server.serve(sockets=[listener]))
        while not server.started and not serve_task.done():
            await asyncio.sleep(0.01)
        if serve_task.done():
            await serve_task
            raise RuntimeError("Uvicorn stopped before readiness")
        print(
            json.dumps(
                {
                    "event": "ready",
                    "baseUrl": f"http://127.0.0.1:{port}",
                    "database": str(database_path),
                },
                separators=(",", ":"),
            ),
            flush=True,
        )
        await asyncio.to_thread(sys.stdin.readline)
        server.should_exit = True
        await serve_task


if __name__ == "__main__":
    asyncio.run(_serve())
