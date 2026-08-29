from __future__ import annotations

from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3
import shutil
import tempfile
import unittest
from unittest.mock import patch

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
    schema_revision = "20260826_01"

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
        run_alembic(self.database_path, "20260826_01")
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
                required_schema_revision="20260826_01",
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
        project_dir = self.root / "artifacts" / "projects" / project_id
        self.assertTrue((project_dir / "artifacts").is_dir())
        self.assertEqual(project["document"]["content"], (project_dir / "document.md").read_text(encoding="utf-8"))
        manifest = json.loads((project_dir / "project.json").read_text(encoding="utf-8"))
        self.assertEqual(project_id, manifest["id"])
        self.assertEqual("document.md", manifest["files"]["document"])
        self.assertEqual([], json.loads((project_dir / "runs.json").read_text(encoding="utf-8")))
        self.assertEqual([], json.loads((project_dir / "results.json").read_text(encoding="utf-8")))
        self.assertEqual([], json.loads((project_dir / "notes.json").read_text(encoding="utf-8")))

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
        self.assertEqual(
            response.json()["content"],
            (project_dir / "document.md").read_text(encoding="utf-8"),
        )

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
        self.assertEqual(
            run["id"],
            json.loads((project_dir / "runs.json").read_text(encoding="utf-8"))[0]["id"],
        )

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
        self.assertEqual(
            "Investigate the seed variance.",
            json.loads((project_dir / "notes.json").read_text(encoding="utf-8"))[0]["content"],
        )

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
            json={"name": "No association"},
        )
        self.assertEqual(422, response.status_code, response.text)

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
                json={"paperId": "paper-1", "name": name},
            )
            self.assertEqual(201, response.status_code, response.text)
        response = self.client.get("/api/v2/reproductions", params={"sort": "name"})
        self.assertEqual(200, response.status_code, response.text)
        self.assertEqual(
            ["Alpha project", "Zeta project"],
            [item["name"] for item in response.json()["items"]],
        )

    def test_run_lifecycle_and_result_comparison_are_persisted(self) -> None:
        response = self.client.post(
            "/api/v2/reproductions",
            json={"paperId": "paper-1", "name": "Result comparison"},
        )
        self.assertEqual(201, response.status_code, response.text)
        project_id = response.json()["id"]
        project_dir = self.root / "artifacts" / "projects" / project_id

        response = self.client.post(
            f"/api/v2/reproductions/{project_id}/runs",
            json={
                "environment": "Python 3.12",
                "status": "completed",
                "startedAt": "2026-08-26T01:00:00+00:00",
                "finishedAt": "2026-08-26T01:12:00+00:00",
                "resultSummary": "Baseline completed",
            },
        )
        self.assertEqual(201, response.status_code, response.text)
        run = response.json()
        self.assertEqual("2026-08-26T01:00:00+00:00", run["startedAt"])
        self.assertEqual("2026-08-26T01:12:00+00:00", run["finishedAt"])

        response = self.client.patch(
            f"/api/v2/reproductions/{project_id}/runs/{run['id']}",
            json={"status": "failed", "resultSummary": "CUDA out of memory"},
        )
        self.assertEqual(200, response.status_code, response.text)
        self.assertEqual("failed", response.json()["status"])

        response = self.client.post(
            f"/api/v2/reproductions/{project_id}/results",
            json={
                "metricName": "Top-1 accuracy",
                "paperValue": "74.2%",
                "reproductionValue": "73.1%",
                "difference": "-1.1 pp",
                "differencePercent": "-1.48%",
                "datasetSettings": "ImageNet-1K / 224px",
                "source": "run_1",
                "status": "inconsistent",
                "notes": "Different augmentation policy.",
            },
        )
        self.assertEqual(201, response.status_code, response.text)
        result = response.json()
        self.assertEqual("inconsistent", result["status"])
        self.assertEqual(
            result["id"],
            json.loads((project_dir / "results.json").read_text(encoding="utf-8"))[0]["id"],
        )

        detail = self.client.get(f"/api/v2/reproductions/{project_id}")
        self.assertEqual(200, detail.status_code, detail.text)
        self.assertEqual(result["id"], detail.json()["results"][0]["id"])
        self.assertEqual(1, detail.json()["runCount"])

        response = self.client.delete(
            f"/api/v2/reproductions/{project_id}/runs/{run['id']}"
        )
        self.assertEqual(204, response.status_code, response.text)
        self.assertEqual([], self.client.get(f"/api/v2/reproductions/{project_id}/runs").json())
        self.assertEqual([], json.loads((project_dir / "runs.json").read_text(encoding="utf-8")))

    def test_multipart_artifact_upload_is_bounded_and_server_owned(self) -> None:
        response = self.client.post(
            "/api/v2/reproductions",
            json={"paperId": "paper-1", "name": "Attachment safety"},
        )
        self.assertEqual(201, response.status_code, response.text)
        project_id = response.json()["id"]
        project_dir = self.root / "artifacts" / "projects" / project_id
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
        self.assertEqual("artifacts", artifact["storageKey"].split("/")[2])
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

        image_payload = b"\x89PNG\r\n\x1a\neditor-image"
        response = self.client.post(
            f"/api/v2/reproductions/{project_id}/artifacts",
            files={"file": ("figure.png", image_payload, "image/png")},
        )
        self.assertEqual(201, response.status_code, response.text)
        image_artifact = response.json()
        image_response = self.client.get(
            f"/api/v2/reproductions/{project_id}/artifacts/{image_artifact['id']}/download"
        )
        self.assertEqual(200, image_response.status_code, image_response.text)
        self.assertEqual(image_payload, image_response.content)
        self.assertIn("inline", image_response.headers.get("content-disposition", ""))

        spoofed_image = self.client.post(
            f"/api/v2/reproductions/{project_id}/artifacts",
            files={"file": ("spoofed.png", b"not a png", "image/png")},
        )
        self.assertEqual(422, spoofed_image.status_code, spoofed_image.text)

        html_payload = b"<!doctype html><title>report</title><h1>Result</h1>"
        response = self.client.post(
            f"/api/v2/reproductions/{project_id}/artifacts",
            files={"file": ("report.html", html_payload, "text/html")},
        )
        self.assertEqual(201, response.status_code, response.text)
        html_artifact = response.json()
        html_response = self.client.get(
            f"/api/v2/reproductions/{project_id}/artifacts/{html_artifact['id']}/download"
        )
        self.assertEqual(200, html_response.status_code, html_response.text)
        self.assertEqual(html_payload, html_response.content)
        self.assertIn("attachment", html_response.headers.get("content-disposition", ""))
        self.assertEqual("nosniff", html_response.headers.get("x-content-type-options"))
        self.assertEqual("sandbox", html_response.headers.get("content-security-policy"))

        manifest = json.loads((project_dir / "project.json").read_text(encoding="utf-8"))
        artifact_paths = {item["id"]: item["path"] for item in manifest["artifacts"]}
        self.assertEqual(
            f"artifacts/{html_artifact['id']}.html",
            artifact_paths[html_artifact["id"]],
        )

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
            json={"paperId": "paper-1", "name": "JSON artifact boundary"},
        )
        self.assertEqual(201, response.status_code, response.text)
        project_id = response.json()["id"]
        project_dir = self.root / "artifacts" / "projects" / project_id
        legacy_payload = b"legacy\n"
        legacy_path = project_dir / "legacy.log"
        legacy_path.write_bytes(legacy_payload)
        legacy = self.client.post(
            f"/api/v2/reproductions/{project_id}/artifacts",
            json={
                "kind": "log",
                "filename": "legacy.log",
                "storageKey": f"projects/{project_id}/legacy.log",
                "mimeType": "text/plain",
                "sizeBytes": len(legacy_payload),
                "sha256": hashlib.sha256(legacy_payload).hexdigest(),
            },
        )
        self.assertEqual(201, legacy.status_code, legacy.text)
        legacy_download = self.client.get(
            f"/api/v2/reproductions/{project_id}/artifacts/{legacy.json()['id']}/download"
        )
        self.assertEqual(200, legacy_download.status_code, legacy_download.text)
        self.assertEqual(legacy_payload, legacy_download.content)
        manifest = json.loads((project_dir / "project.json").read_text(encoding="utf-8"))
        self.assertEqual("legacy.log", manifest["artifacts"][0]["path"])

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
            json={"paperId": "paper-1", "name": "Run attachment safety"},
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

    def test_project_copy_owns_its_document_and_artifacts(self) -> None:
        created = self.client.post(
            "/api/v2/reproductions",
            json={"paperId": "paper-1", "name": "Source project", "tags": ["copy"]},
        )
        self.assertEqual(201, created.status_code, created.text)
        source = created.json()
        source_id = source["id"]
        run = self.client.post(
            f"/api/v2/reproductions/{source_id}/runs",
            json={"name": "Source run", "status": "completed"},
        )
        self.assertEqual(201, run.status_code, run.text)
        result = self.client.post(
            f"/api/v2/reproductions/{source_id}/results",
            json={"metricName": "accuracy", "status": "reproduced"},
        )
        self.assertEqual(201, result.status_code, result.text)
        note = self.client.post(
            f"/api/v2/reproductions/{source_id}/notes",
            json={"content": "Source-only note"},
        )
        self.assertEqual(201, note.status_code, note.text)
        image_payload = b"\x89PNG\r\n\x1a\ncopy-source"
        uploaded = self.client.post(
            f"/api/v2/reproductions/{source_id}/artifacts",
            files={"file": ("figure.png", image_payload, "image/png")},
        )
        self.assertEqual(201, uploaded.status_code, uploaded.text)
        source_artifact = uploaded.json()
        source_url = (
            f"/api/v2/reproductions/{source_id}/artifacts/"
            f"{source_artifact['id']}/download"
        )
        copied = self.client.post(
            f"/api/v2/reproductions/{source_id}/copy",
            json={
                "name": "Independent copy",
                "content": f"# Copy\n\n![figure]({source_url})\n",
            },
        )
        self.assertEqual(201, copied.status_code, copied.text)
        copy = copied.json()
        copy_id = copy["id"]
        self.assertNotEqual(source_id, copy_id)
        self.assertEqual("Independent copy", copy["name"])
        self.assertEqual(1, len(copy["artifacts"]))
        self.assertEqual([], copy["runs"])
        self.assertEqual([], copy["results"])
        self.assertEqual([], copy["notes"])
        copy_artifact = copy["artifacts"][0]
        self.assertNotEqual(source_artifact["id"], copy_artifact["id"])
        self.assertNotIn(source_url, copy["document"]["content"])
        copy_url = (
            f"/api/v2/reproductions/{copy_id}/artifacts/"
            f"{copy_artifact['id']}/download"
        )
        self.assertIn(copy_url, copy["document"]["content"])
        self.assertNotEqual(source_artifact["storageKey"], copy_artifact["storageKey"])

        source_path = self.application.reproduction_workspace.artifact_path(
            source_artifact["storageKey"], project_id=source_id
        )
        copy_path = self.application.reproduction_workspace.artifact_path(
            copy_artifact["storageKey"], project_id=copy_id
        )
        self.assertEqual(image_payload, source_path.read_bytes())
        self.assertEqual(image_payload, copy_path.read_bytes())
        self.assertNotEqual(source_path, copy_path)

        archived = self.client.post(
            f"/api/v2/reproductions/{source_id}/archive",
            json={"expectedRevision": source["revision"]},
        )
        self.assertEqual(200, archived.status_code, archived.text)
        deleted = self.client.request(
            "DELETE",
            f"/api/v2/reproductions/{source_id}",
            json={"expectedRevision": archived.json()["revision"]},
        )
        self.assertEqual(204, deleted.status_code, deleted.text)
        self.assertFalse(source_path.parent.parent.exists())
        copy_download = self.client.get(
            f"/api/v2/reproductions/{copy_id}/artifacts/{copy_artifact['id']}/download"
        )
        self.assertEqual(200, copy_download.status_code, copy_download.text)
        self.assertEqual(image_payload, copy_download.content)

        with closing(sqlite3.connect(self.database_path)) as connection:
            for table in (
                "reproduction_projects",
                "reproduction_documents",
                "experiment_runs",
                "reproduction_results",
                "reproduction_artifacts",
                "reproduction_notes",
            ):
                count = connection.execute(
                    f"SELECT count(*) FROM {table} WHERE "
                    + ("id=?" if table == "reproduction_projects" else "project_id=?"),
                    (source_id,),
                ).fetchone()[0]
                self.assertEqual(0, count, table)

    def test_failed_project_copy_does_not_leave_an_orphan_directory(self) -> None:
        created = self.client.post(
            "/api/v2/reproductions",
            json={"paperId": "paper-1", "name": "Incomplete source"},
        )
        self.assertEqual(201, created.status_code, created.text)
        source_id = created.json()["id"]
        payload = b"missing attachment\n"
        registered = self.client.post(
            f"/api/v2/reproductions/{source_id}/artifacts",
            json={
                "kind": "log",
                "filename": "missing.log",
                "storageKey": f"projects/{source_id}/missing.log",
                "mimeType": "text/plain",
                "sizeBytes": len(payload),
                "sha256": hashlib.sha256(payload).hexdigest(),
            },
        )
        self.assertEqual(201, registered.status_code, registered.text)

        projects_root = self.root / "artifacts" / "projects"
        directories_before = {path.name for path in projects_root.iterdir() if path.is_dir()}
        copied = self.client.post(f"/api/v2/reproductions/{source_id}/copy", json={})
        self.assertEqual(422, copied.status_code, copied.text)
        directories_after = {path.name for path in projects_root.iterdir() if path.is_dir()}
        self.assertEqual(directories_before, directories_after)
        self.assertEqual(1, self.client.get("/api/v2/reproductions").json()["total"])

    def test_workspace_sync_failure_keeps_sqlite_success_and_repairs_on_get(self) -> None:
        with patch(
            "backend.app.application.reproductions._atomic_write_text",
            side_effect=OSError("disk unavailable"),
        ):
            created = self.client.post(
                "/api/v2/reproductions",
                json={"paperId": "paper-1", "name": "Deferred mirror"},
            )
        self.assertEqual(201, created.status_code, created.text)
        project = created.json()
        project_dir = self.root / "artifacts" / "projects" / project["id"]
        self.assertFalse((project_dir / "project.json").exists())

        restored = self.client.get(f"/api/v2/reproductions/{project['id']}")
        self.assertEqual(200, restored.status_code, restored.text)
        self.assertTrue((project_dir / "project.json").is_file())
        self.assertEqual(
            restored.json()["document"]["content"],
            (project_dir / "document.md").read_text(encoding="utf-8"),
        )

    def test_project_delete_can_retry_filesystem_cleanup_after_commit(self) -> None:
        created = self.client.post(
            "/api/v2/reproductions",
            json={"paperId": "paper-1", "name": "Retriable delete"},
        )
        self.assertEqual(201, created.status_code, created.text)
        project = created.json()
        project_dir = self.root / "artifacts" / "projects" / project["id"]
        archived = self.client.post(
            f"/api/v2/reproductions/{project['id']}/archive",
            json={"expectedRevision": project["revision"]},
        )
        self.assertEqual(200, archived.status_code, archived.text)

        with patch(
            "backend.app.application.reproductions.shutil.rmtree",
            side_effect=OSError("directory busy"),
        ):
            with self.assertRaises(OSError):
                self.client.request(
                    "DELETE",
                    f"/api/v2/reproductions/{project['id']}",
                    json={"expectedRevision": archived.json()["revision"]},
                )
        self.assertTrue(project_dir.is_dir())
        self.assertEqual(0, self.client.get("/api/v2/reproductions").json()["total"])

        retried = self.client.request(
            "DELETE",
            f"/api/v2/reproductions/{project['id']}",
            json={"expectedRevision": archived.json()["revision"]},
        )
        self.assertEqual(204, retried.status_code, retried.text)
        self.assertFalse(project_dir.exists())

    def test_get_project_backfills_a_missing_workspace_directory(self) -> None:
        response = self.client.post(
            "/api/v2/reproductions",
            json={"paperId": "paper-1", "name": "Legacy project"},
        )
        self.assertEqual(201, response.status_code, response.text)
        project = response.json()
        project_dir = self.root / "artifacts" / "projects" / project["id"]
        self.assertTrue(project_dir.is_dir())
        shutil.rmtree(project_dir)
        self.assertFalse(project_dir.exists())
        restored = self.client.get(f"/api/v2/reproductions/{project['id']}")
        self.assertEqual(200, restored.status_code, restored.text)
        self.assertEqual(
            restored.json()["document"]["content"],
            (project_dir / "document.md").read_text(encoding="utf-8"),
        )
        self.assertTrue((project_dir / "artifacts").is_dir())


if __name__ == "__main__":
    unittest.main()
