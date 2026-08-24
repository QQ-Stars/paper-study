from __future__ import annotations

import asyncio
from contextlib import contextmanager
import io
import json
import os
from pathlib import Path
import subprocess
from types import SimpleNamespace
import unittest
from urllib.parse import quote

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.app.api.app import create_app
from backend.app.api.static.frontend_assets import create_frontend_assets_router
from backend.app.providers.pdf_files import PdfFiles
from backend.tests.support.p3_database import p3_database_fixture


class _LibraryQueries:
    def __init__(self, rows: dict[str, dict[str, object]] | None = None) -> None:
        self._rows = rows or {}

    async def get_paper(self, paper_id: str) -> dict[str, object] | None:
        return self._rows.get(paper_id)


class PdfStaticApiTests(unittest.TestCase):
    def test_pdf_response_streams_preopened_handle_without_path_reopen(self) -> None:
        async def scenario() -> None:
            async with p3_database_fixture(prefix="study-app-p4-pdf-open-handle-") as fixture:
                payload = b"%PDF-1.7\npreopened-handle-tail\n"

                class PreopenedPdfFiles:
                    def __init__(self) -> None:
                        self.streams: list[io.BytesIO] = []

                    def open_for_id(self, paper_id: str, stored_path: object = None) -> object | None:
                        if paper_id != "paper-1":
                            return None
                        stream = io.BytesIO(payload)
                        self.streams.append(stream)
                        return SimpleNamespace(stream=stream, size=len(payload))

                    def resolve_for_id(self, _paper_id: str) -> object:
                        raise AssertionError("PDF response must not reopen a validated path")

                pdf_files = PreopenedPdfFiles()

                class Services:
                    schema_revision = "20260807_03"
                    legacy = SimpleNamespace(
                        pdf_files=pdf_files,
                        library_queries=_LibraryQueries(),
                    )

                    async def dispose(self) -> None:
                        return None

                app = create_app(
                    Services(),
                    fixture.session_factory,
                    required_schema_revision="20260807_03",
                )
                with TestClient(app) as client:
                    response = client.get("/pdfbytes", params={"id": "paper-1"})
                    self.assertEqual(200, response.status_code, response.text)
                    self.assertEqual(payload, response.content)
                    self.assertEqual(str(len(payload)), response.headers["content-length"])

                    head = client.head("/papers/paper-1.pdf")
                    self.assertEqual(200, head.status_code, head.text)
                    self.assertEqual(b"", head.content)
                    self.assertEqual(str(len(payload)), head.headers["content-length"])

                self.assertEqual(2, len(pdf_files.streams))
                self.assertTrue(all(stream.closed for stream in pdf_files.streams))

        asyncio.run(scenario())

    def test_pdfbytes_and_papers_match_node(self) -> None:
        async def scenario() -> None:
            async with p3_database_fixture(prefix="study-app-p4-pdf-parity-") as fixture:
                app_root = fixture.database_path.parents[1] / "runtime"
                pdf_root = app_root / "pdfs"
                pdf_root.mkdir(parents=True)
                paper_id = "\u8bba\u6587-\u03b4 2026"
                payload = b"%PDF-1.7\nP4 tail sentinel\n"
                (pdf_root / f"{paper_id}.pdf").write_bytes(payload)
                services = _services(app_root, pdf_root)
                app = create_app(
                    services,
                    fixture.session_factory,
                    required_schema_revision="20260807_03",
                )

                with TestClient(app) as client:
                    pdfbytes = client.get("/pdfbytes", params={"id": paper_id})
                    self.assertEqual(200, pdfbytes.status_code, pdfbytes.text)
                    self.assertEqual("application/octet-stream", pdfbytes.headers["content-type"])
                    self.assertEqual(str(len(payload)), pdfbytes.headers["content-length"])
                    self.assertEqual("no-store", pdfbytes.headers["cache-control"])
                    self.assertEqual(payload, pdfbytes.content)

                    paper_path = f"/papers/{quote(paper_id, safe='')}.pdf"
                    paper = client.get(paper_path)
                    self.assertEqual(200, paper.status_code, paper.text)
                    self.assertEqual("application/pdf", paper.headers["content-type"])
                    self.assertEqual(payload, paper.content)

                    head = client.head(paper_path)
                    self.assertEqual(200, head.status_code, head.text)
                    self.assertEqual("application/pdf", head.headers["content-type"])
                    self.assertEqual(str(len(payload)), head.headers["content-length"])
                    self.assertEqual(b"", head.content)

                    missing_bytes = client.get("/pdfbytes", params={"id": "missing"})
                    self.assertEqual(404, missing_bytes.status_code)
                    self.assertEqual("not found", missing_bytes.text)
                    missing_paper = client.get("/papers/missing.pdf")
                    self.assertEqual(404, missing_paper.status_code)
                    self.assertEqual("PDF not found", missing_paper.text)

        asyncio.run(scenario())

    def test_pdf_paths_cannot_escape_roots(self) -> None:
        async def scenario() -> None:
            async with p3_database_fixture(prefix="study-app-p4-pdf-security-") as fixture:
                app_root = fixture.database_path.parents[1] / "runtime"
                pdf_root = app_root / "pdfs"
                outside_root = app_root / "outside"
                pdf_root.mkdir(parents=True)
                outside_root.mkdir(parents=True)
                (pdf_root / "secret.pdf").write_bytes(b"inside secret")
                (outside_root / "outside.pdf").write_bytes(b"outside secret")

                escape_link = pdf_root / "escape"
                with _directory_link(outside_root, escape_link):
                    rows = {
                        "linked": {
                            "id": "linked",
                            "pdf_path": str(escape_link / "outside.pdf"),
                        }
                    }
                    services = _services(app_root, pdf_root, rows=rows)
                    app = create_app(
                        services,
                        fixture.session_factory,
                        required_schema_revision="20260807_03",
                    )
                    with TestClient(app) as client:
                        unsafe_requests = (
                            ("/pdfbytes?id=..%2Fsecret", "query traversal"),
                            ("/pdfbytes?id=%2Fsecret", "absolute path"),
                            ("/pdfbytes?id=C:%5Csecret", "Windows absolute path"),
                            ("/papers/%2e%2e%2fsecret.pdf", "path traversal"),
                            ("/papers/C:%5Csecret.pdf", "Windows path traversal"),
                        )
                        for path, label in unsafe_requests:
                            response = client.get(path)
                            self.assertEqual(404, response.status_code, f"{label}: {response.text}")

                        linked = client.get("/api/pdf/status", params={"id": "linked"})
                        self.assertEqual(200, linked.status_code, linked.text)
                        self.assertFalse(linked.json()["hasPdf"])

        asyncio.run(scenario())

    def test_workspace_entry_contract(self) -> None:
        async def scenario() -> None:
            async with p3_database_fixture(prefix="study-app-p4-static-integration-") as fixture:
                services = _services(
                    fixture.database_path.parents[1] / "runtime",
                    fixture.database_path.parents[1] / "runtime" / "pdfs",
                )
                app = create_app(
                    services,
                    fixture.session_factory,
                    required_schema_revision="20260807_03",
                )
                with TestClient(app) as client:
                    workspace = client.get("/workspace/", follow_redirects=False)
                    self.assertEqual(200, workspace.status_code, workspace.text)
                    self.assertIn("text/html", workspace.headers["content-type"])
                    self.assertIn("Content-Security-Policy", workspace.headers)
                    unknown_api = client.get("/api/not-real")
                    self.assertEqual(404, unknown_api.status_code)
                    self.assertEqual("API not found", unknown_api.text)

                fixture_root = fixture.database_path.parents[1] / "frontend-fixture"
                react_root = fixture_root / "frontend" / "dist"
                asset_root = react_root / "assets"
                manifest_root = react_root / ".vite"
                asset_root.mkdir(parents=True)
                manifest_root.mkdir(parents=True)

                react_index = b'<div id="root"></div>'
                hashed_name = "index-AbC_d123.js"
                hashed_body = b"hashed"
                source_map_body = b'{"version":3}'
                (react_root / "index.html").write_bytes(react_index)
                (asset_root / hashed_name).write_bytes(hashed_body)
                (asset_root / "license-apache20.txt").write_bytes(b"license")
                (asset_root / f"{hashed_name}.map").write_bytes(source_map_body)
                manifest = {
                    "index.html": {
                        "file": f"assets/{hashed_name}",
                        "isEntry": True,
                    }
                }
                manifest_bytes = json.dumps(manifest, separators=(",", ":")).encode("utf-8")
                (manifest_root / "manifest.json").write_bytes(manifest_bytes)

                static_app = FastAPI()
                static_app.include_router(
                    create_frontend_assets_router(
                        react_root=react_root,
                    )
                )
                with TestClient(static_app) as client:
                    for path, location in (
                        ("/", "/workspace/"),
                        ("/workspace", "/workspace/"),
                    ):
                        response = client.get(path, follow_redirects=False)
                        self.assertEqual(302, response.status_code, path)
                        self.assertEqual(location, response.headers["location"])
                        self.assertEqual("no-store", response.headers["cache-control"])

                    workspace = client.get("/workspace/")
                    self.assertEqual(react_index, workspace.content)
                    self.assertEqual("text/html; charset=utf-8", workspace.headers["content-type"])
                    self.assertEqual("no-cache", workspace.headers["cache-control"])
                    self.assertIn("font-src 'self' data:", workspace.headers["content-security-policy"])

                    deep_link = client.get("/workspace/reader/2401.12345")
                    self.assertEqual(200, deep_link.status_code, deep_link.text)
                    self.assertEqual(react_index, deep_link.content)

                    hashed = client.get(f"/workspace/assets/{hashed_name}")
                    self.assertEqual(hashed_body, hashed.content)
                    self.assertEqual("text/javascript; charset=utf-8", hashed.headers["content-type"])
                    self.assertEqual(
                        "public,max-age=31536000,immutable",
                        hashed.headers["cache-control"],
                    )
                    lookalike = client.get("/workspace/assets/license-apache20.txt")
                    self.assertEqual("no-cache", lookalike.headers["cache-control"])

                    source_map = client.get(f"/workspace/assets/{hashed_name}.map")
                    self.assertEqual(source_map_body, source_map.content)
                    self.assertEqual("application/json; charset=utf-8", source_map.headers["content-type"])
                    self.assertEqual("no-cache", source_map.headers["cache-control"])
                    dotfile = client.get("/workspace/.vite/manifest.json")
                    self.assertEqual(manifest_bytes, dotfile.content)
                    self.assertEqual("no-cache", dotfile.headers["cache-control"])

                    head = client.head(f"/workspace/assets/{hashed_name}")
                    self.assertEqual(200, head.status_code, head.text)
                    self.assertEqual(str(len(hashed_body)), head.headers["content-length"])
                    self.assertEqual(b"", head.content)
                    unsupported = client.post("/workspace/")
                    self.assertEqual(405, unsupported.status_code, unsupported.text)
                    self.assertEqual("GET, HEAD", unsupported.headers["allow"])
                    self.assertEqual("no-store", unsupported.headers["cache-control"])
                    self.assertEqual("method not allowed", unsupported.text)

                    self.assertEqual(404, client.get("/legacy/").status_code)
                    self.assertEqual(404, client.get("/style.css").status_code)

                    missing_asset = client.get("/workspace/assets/missing.js")
                    self.assertEqual(404, missing_asset.status_code)
                    self.assertEqual("not found", missing_asset.text)
                    dotted_route = client.get("/workspace/reader/not-a-file.pdf")
                    self.assertEqual(react_index, dotted_route.content)

                    unsafe_paths = (
                        "/workspace/%",
                        "/workspace/%C0%AF",
                        "/workspace/evil%00.js",
                        "/workspace/%2e%2e/app.py",
                        "/workspace/%2E/library",
                        "/workspace/a%2fb.js",
                        "/workspace/a%5Cb.js",
                        "/workspace/C:/Windows/system.ini",
                        "/%2e%2e/app.py",
                        "/workspace-old/index.html",
                    )
                    for path in unsafe_paths:
                        response = client.get(path)
                        self.assertEqual(403, response.status_code, f"{path}: {response.text}")
                        self.assertEqual("no-store", response.headers["cache-control"])

        asyncio.run(scenario())


def _services(
    app_root: Path,
    pdf_root: Path,
    *,
    rows: dict[str, dict[str, object]] | None = None,
) -> object:
    pdf_files = PdfFiles(root=app_root, default_directory=pdf_root)

    class Services:
        schema_revision = "20260807_03"
        legacy = SimpleNamespace(
            pdf_files=pdf_files,
            library_queries=_LibraryQueries(rows),
        )

        async def dispose(self) -> None:
            return None

    return Services()


@contextmanager
def _directory_link(target: Path, link: Path):
    try:
        os.symlink(target, link, target_is_directory=True)
    except OSError as error:
        if os.name != "nt":
            raise AssertionError(f"unable to create symlink fixture: {error}") from error
        result = subprocess.run(
            ["cmd.exe", "/d", "/c", "mklink", "/J", str(link), str(target)],
            check=False,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise AssertionError(
                f"unable to create junction fixture: {result.stdout}{result.stderr}"
            ) from error
    try:
        yield
    finally:
        if link.is_symlink():
            link.unlink()
        elif link.exists():
            link.rmdir()


if __name__ == "__main__":
    unittest.main()
