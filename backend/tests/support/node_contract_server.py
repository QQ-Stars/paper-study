from __future__ import annotations

import http.client
import json
import os
import shutil
import socket
import sqlite3
import subprocess
import tempfile
import time
from contextlib import closing, contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator


REPO_ROOT = Path(__file__).resolve().parents[3]
SERVER_PATH = REPO_ROOT / "server.js"
PRELOAD_PATH = REPO_ROOT / "test" / "fixtures" / "legacy-server-preload.js"
_READY_TIMEOUT_SECONDS = 10.0
_STOP_TIMEOUT_SECONDS = 2.0


def _reserve_loopback_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


@dataclass(slots=True)
class NodeContractServer:
    temporary_root: Path
    database_path: Path
    port: int
    _temporary_directory: tempfile.TemporaryDirectory[str]
    _process: subprocess.Popen[str]

    def get(self, resource: str) -> tuple[int, str, str]:
        return self.request("GET", resource)

    def request(
        self,
        method: str,
        resource: str,
        payload: object | None = None,
    ) -> tuple[int, str, str]:
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=2)
        try:
            body: bytes | None = None
            headers: dict[str, str] = {}
            if payload is not None:
                body = json.dumps(
                    payload,
                    ensure_ascii=False,
                    separators=(",", ":"),
                ).encode("utf-8")
                headers["Content-Type"] = "application/json"
            connection.request(method, resource, body=body, headers=headers)
            response = connection.getresponse()
            return response.status, response.getheader("Content-Type") or "", response.read().decode("utf-8")
        finally:
            connection.close()

    def close(self) -> None:
        process = self._process
        try:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=_STOP_TIMEOUT_SECONDS)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=_STOP_TIMEOUT_SECONDS)
            process.communicate(timeout=0.1)
        finally:
            if process.stdout is not None:
                process.stdout.close()
            if process.stderr is not None:
                process.stderr.close()
            self._temporary_directory.cleanup()


@contextmanager
def start_node_contract_server(
    *,
    database_template: Path | None = None,
) -> Iterator[NodeContractServer]:
    node = shutil.which("node")
    if node is None:
        raise RuntimeError("node executable is required for the legacy contract server")

    temporary_directory = tempfile.TemporaryDirectory(prefix="study-app-node-contract-")
    root = Path(temporary_directory.name).resolve()
    database_path = root / "app.db"
    if database_template is not None:
        template = database_template.resolve(strict=True)
        with closing(sqlite3.connect(template)) as source, closing(
            sqlite3.connect(database_path)
        ) as target:
            source.backup(target)
    settings_path = root / "settings.json"
    pdf_directory = root / "pdfs"
    explainer_directory = root / "explainers"
    translation_directory = root / "translations"
    for directory in (pdf_directory, explainer_directory, translation_directory):
        directory.mkdir()
    settings_path.write_text(
        json.dumps(
            {
                "provider": "openai",
                "baseUrl": "https://fixture.invalid/v1",
                "model": "fixture-model",
                "apiKey": "fixture-key",
                "pdfDir": str(pdf_directory),
                "explainerDir": str(explainer_directory),
                "translationDir": str(translation_directory),
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    port = _reserve_loopback_port()
    environment = os.environ.copy()
    environment.update(
        {
            "DB_PATH": str(database_path),
            "SETTINGS_PATH": str(settings_path),
            "HOST": "127.0.0.1",
            "PORT": str(port),
            "DISABLE_SCHEDULES": "1",
            "API_BACKEND_MODE": "legacy",
            "DOCUMENT_PIPELINE_MODE": "legacy",
            "GENERATION_PIPELINE_MODE": "legacy",
            "ARTIFACT_READ_MODE": "legacy",
            "ARTIFACT_WRITE_MODE": "legacy",
            "OCR_ENABLED": "0",
            "PYTHONIOENCODING": "utf-8",
            "PYTHONUTF8": "1",
        }
    )
    process = subprocess.Popen(
        [node, "--require", str(PRELOAD_PATH), str(SERVER_PATH)],
        cwd=REPO_ROOT,
        env=environment,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    server = NodeContractServer(root, database_path, port, temporary_directory, process)
    try:
        deadline = time.monotonic() + _READY_TIMEOUT_SECONDS
        while time.monotonic() < deadline:
            if process.poll() is not None:
                stdout, stderr = process.communicate()
                raise RuntimeError(
                    f"legacy contract server exited before readiness: {stdout}\n{stderr}"
                )
            try:
                status, _, _ = server.get("/api/papers")
                if status == 200:
                    break
            except OSError:
                pass
            time.sleep(0.05)
        else:
            raise RuntimeError("legacy contract server readiness timed out")
        yield server
    finally:
        server.close()
