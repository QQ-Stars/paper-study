from __future__ import annotations

from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from fastapi.testclient import TestClient

from backend.app.api.app import create_app
from backend.app.api.dependencies import ApiDependencies
from backend.app.config import DatabaseSettings
from backend.app.infrastructure.database import create_async_session_factory
from backend.app.runtime import ApiSettings
from backend.app.application.reproductions import ReproductionWorkspace
from backend.app.repositories.unit_of_work import SqlAlchemyUnitOfWork
from backend.tests.support.p1_database import create_legacy_database, run_alembic


class _Application:
    schema_revision = "20260825_04"

    def __init__(self, session_factory):
        self.session_factory = session_factory
        self.reproduction_workspace = ReproductionWorkspace(
            lambda: SqlAlchemyUnitOfWork(session_factory)
        )

    async def dispose(self) -> None:
        await self.session_factory.kw["bind"].dispose()


class ReproductionApiContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory(prefix="study-app-reproduction-api-")
        self.root = Path(self._temp.name)
        self.database_path = self.root / "database" / "app.db"
        create_legacy_database(self.database_path)
        run_alembic(self.database_path, "20260825_04")
        self.session_factory = create_async_session_factory(
            DatabaseSettings(self.database_path)
        )
        self.application = _Application(self.session_factory)
        self.application.reproduction_workspace = ReproductionWorkspace(
            lambda: SqlAlchemyUnitOfWork(self.session_factory),
            artifact_root=self.root / "artifacts",
        )
        self.client_context = TestClient(
            create_app(
                settings=ApiSettings.for_tests(),
                dependencies=ApiDependencies(self.application, self.session_factory),
                required_schema_revision="20260825_04",
            )
        )
        self.client = self.client_context.__enter__()

    def tearDown(self) -> None:
        self.client_context.__exit__(None, None, None)
        self._temp.cleanup()

    def test_project_document_run_artifact_and_note_flow(self) -> None:
        response = self.client.post(
            "/api/v2/reproductions",
            json={
                "paperId": "paper-1",
                "name": "Baseline reproduction",
                "tags": ["baseline", "vision"],
            },
        )
        self.assertEqual(201, response.status_code, response.text)
        project = response.json()
        project_id = project["id"]
        self.assertEqual("paper-1", project["paperId"])
        self.assertEqual("planned", project["status"])
        self.assertEqual(1, project["revision"])
        self.assertIn("复现目标", project["document"]["content"])

        response = self.client.get(
            "/api/v2/reproductions",
            params={"q": "Baseline", "status": "planned", "tag": "baseline"},
        )
        self.assertEqual(200, response.status_code, response.text)
        listing = response.json()
        self.assertEqual(1, listing["total"])
        self.assertEqual(project_id, listing["items"][0]["id"])

        response = self.client.put(
            f"/api/v2/reproductions/{project_id}/document",
            json={
                "content": "# 复现目标\n\nValidate the baseline.\n",
                "expectedRevision": 1,
            },
        )
        self.assertEqual(200, response.status_code, response.text)
        self.assertEqual(2, response.json()["revision"])

        response = self.client.put(
            f"/api/v2/reproductions/{project_id}/document",
            json={"content": "stale", "expectedRevision": 1},
        )
        self.assertEqual(409, response.status_code, response.text)
        self.assertEqual("REPRODUCTION_CONFLICT", response.json()["error"]["code"])

        response = self.client.post(
            f"/api/v2/reproductions/{project_id}/runs",
            json={
                "environment": "Python 3.12 / CUDA 12.4",
                "command": "python train.py",
                "parameters": {"epochs": 10},
                "dataVersion": "dataset-v1",
                "codeRevision": "abc123",
                "seed": 42,
                "status": "completed",
                "metrics": {"accuracy": 0.81},
                "resultSummary": "Matches the reported trend.",
            },
        )
        self.assertEqual(201, response.status_code, response.text)
        run = response.json()
        self.assertEqual("completed", run["status"])
        self.assertEqual(42, run["seed"])

        response = self.client.post(
            f"/api/v2/reproductions/{project_id}/artifacts",
            json={
                "runId": run["id"],
                "kind": "log",
                "filename": "train.log",
                "storageKey": f"projects/{project_id}/opaque/train.log",
                "mimeType": "text/plain",
                "sizeBytes": 128,
                "sha256": "a" * 64,
            },
        )
        self.assertEqual(201, response.status_code, response.text)
        self.assertEqual("log", response.json()["kind"])

        response = self.client.post(
            f"/api/v2/reproductions/{project_id}/artifacts",
            json={
                "runId": run["id"],
                "kind": "log",
                "filename": "other.log",
                "storageKey": "projects/repro_" + "b" * 32 + "/other.log",
                "mimeType": "text/plain",
                "sizeBytes": 1,
                "sha256": "b" * 64,
            },
        )
        self.assertEqual(422, response.status_code, response.text)
        self.assertEqual("REPRODUCTION_INVALID", response.json()["error"]["code"])

        detail = self.client.get(f"/api/v2/reproductions/{project_id}")
        self.assertEqual(200, detail.status_code, detail.text)
        self.assertEqual("train.log", detail.json()["artifacts"][0]["filename"])
        self.assertIn("sizeBytes", detail.json()["artifacts"][0])

        response = self.client.post(
            f"/api/v2/reproductions/{project_id}/notes",
            json={"content": "Investigate the seed variance."},
        )
        self.assertEqual(201, response.status_code, response.text)
        self.assertEqual("Investigate the seed variance.", response.json()["content"])

        detail = self.client.get(f"/api/v2/reproductions/{project_id}")
        self.assertEqual(200, detail.status_code, detail.text)
        self.assertEqual("Investigate the seed variance.", detail.json()["notes"][0]["content"])
        self.assertIn("createdAt", detail.json()["notes"][0])

        response = self.client.post(
            f"/api/v2/reproductions/{project_id}/archive",
            json={"expectedRevision": 2},
        )
        self.assertEqual(200, response.status_code, response.text)
        self.assertEqual("archived", response.json()["status"])

        for field, value in (("name", "Changed after archive"), ("tags", ["changed"]), ("status", "planned")):
            with self.subTest(field=field):
                response = self.client.patch(
                    f"/api/v2/reproductions/{project_id}",
                    json={"expectedRevision": 3, field: value},
                )
                self.assertEqual(409, response.status_code, response.text)
                self.assertEqual("REPRODUCTION_ARCHIVED", response.json()["error"]["code"])

        response = self.client.patch(
            f"/api/v2/reproductions/{project_id}",
            json={"expectedRevision": 3, "status": "archived"},
        )
        self.assertEqual(200, response.status_code, response.text)
        self.assertEqual(3, response.json()["revision"])

        response = self.client.request(
            "DELETE",
            f"/api/v2/reproductions/{project_id}",
            json={"expectedRevision": 3},
        )
        self.assertEqual(204, response.status_code, response.text)
        response = self.client.get(f"/api/v2/reproductions/{project_id}")
        self.assertEqual(404, response.status_code, response.text)

    def test_invalid_paper_and_unknown_fields_fail_closed(self) -> None:
        response = self.client.post(
            "/api/v2/reproductions",
            json={"paperId": "missing", "name": "No paper"},
        )
        self.assertEqual(404, response.status_code, response.text)
        self.assertEqual("PAPER_NOT_FOUND", response.json()["error"]["code"])

        response = self.client.post(
            "/api/v2/reproductions",
            json={"paperId": "paper-1", "name": "Strict", "unknown": True},
        )
        self.assertEqual(422, response.status_code, response.text)
        self.assertEqual("INVALID_REQUEST", response.json()["error"]["code"])

    def test_projects_can_be_sorted_by_name(self) -> None:
        for name in ("Zeta project", "Alpha project"):
            response = self.client.post(
                "/api/v2/reproductions",
                json={"name": name},
            )
            self.assertEqual(201, response.status_code, response.text)
        response = self.client.get("/api/v2/reproductions", params={"sort": "name"})
        self.assertEqual(200, response.status_code, response.text)
        self.assertEqual(
            ["Alpha project", "Zeta project"],
            [item["name"] for item in response.json()["items"]],
        )

    def test_multipart_artifact_upload_is_bounded_and_server_owned(self) -> None:
        response = self.client.post(
            "/api/v2/reproductions",
            json={"name": "Attachment safety"},
        )
        self.assertEqual(201, response.status_code, response.text)
        project_id = response.json()["id"]
        payload = b"# captured output\n"
        response = self.client.post(
            f"/api/v2/reproductions/{project_id}/artifacts",
            files={"file": ("captured.md", payload, "text/markdown")},
        )
        self.assertEqual(201, response.status_code, response.text)
        artifact = response.json()
        self.assertRegex(artifact["id"], r"^artifact_[a-f0-9]{32}$")
        self.assertEqual(len(payload), artifact["sizeBytes"])
        self.assertEqual(hashlib.sha256(payload).hexdigest(), artifact["sha256"])
        self.assertEqual("projects", artifact["storageKey"].split("/", 1)[0])
        stored = self.application.reproduction_workspace.artifact_path(
            artifact["storageKey"], project_id=project_id
        )
        self.assertTrue(stored.is_file())
        self.assertEqual(payload, stored.read_bytes())
        self.assertFalse(any(path.suffix == ".tmp" for path in stored.parent.iterdir()))
        response = self.client.get(
            f"/api/v2/reproductions/{project_id}/artifacts/{artifact['id']}/download"
        )
        self.assertEqual(200, response.status_code, response.text)
        self.assertEqual(payload, response.content)

        response = self.client.post(
            f"/api/v2/reproductions/{project_id}/artifacts",
            files={"file": ("../secret.md", payload, "text/markdown")},
        )
        self.assertEqual(422, response.status_code, response.text)

        response = self.client.post(
            f"/api/v2/reproductions/{project_id}/artifacts",
            files={"file": ("payload.exe", b"MZ", "application/octet-stream")},
        )
        self.assertEqual(422, response.status_code, response.text)

        response = self.client.post(
            f"/api/v2/reproductions/{project_id}/artifacts",
            files={"file": ("payload.txt", payload, "text/markdown")},
        )
        self.assertEqual(422, response.status_code, response.text)

    def test_json_artifact_storage_key_must_belong_to_project(self) -> None:
        response = self.client.post(
            "/api/v2/reproductions",
            json={"name": "JSON artifact boundary"},
        )
        self.assertEqual(201, response.status_code, response.text)
        project_id = response.json()["id"]
        response = self.client.post(
            f"/api/v2/reproductions/{project_id}/artifacts",
            json={
                "kind": "log",
                "filename": "foreign.log",
                "storageKey": "projects/repro_other/foreign.log",
                "mimeType": "text/plain",
                "sizeBytes": 1,
                "sha256": "a" * 64,
            },
        )
        self.assertEqual(422, response.status_code, response.text)
        self.assertEqual("REPRODUCTION_INVALID", response.json()["error"]["code"])

    def test_multipart_artifact_rejects_unknown_run_and_archived_project(self) -> None:
        response = self.client.post(
            "/api/v2/reproductions",
            json={"name": "Run attachment safety"},
        )
        self.assertEqual(201, response.status_code, response.text)
        project_id = response.json()["id"]
        payload = b"log\n"
        response = self.client.post(
            f"/api/v2/reproductions/{project_id}/artifacts",
            data={"runId": "run_" + "a" * 32},
            files={"file": ("run.log", payload, "text/plain")},
        )
        self.assertEqual(422, response.status_code, response.text)

        response = self.client.post(
            f"/api/v2/reproductions/{project_id}/archive",
            json={"expectedRevision": 1},
        )
        self.assertEqual(200, response.status_code, response.text)
        response = self.client.post(
            f"/api/v2/reproductions/{project_id}/artifacts",
            files={"file": ("run.log", payload, "text/plain")},
        )
        self.assertEqual(409, response.status_code, response.text)
        self.assertEqual("REPRODUCTION_ARCHIVED", response.json()["error"]["code"])


if __name__ == "__main__":
    unittest.main()
