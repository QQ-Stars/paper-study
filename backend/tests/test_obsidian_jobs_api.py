from __future__ import annotations

from pathlib import Path
from datetime import datetime, timezone
from contextlib import closing
import hashlib
import json
import sqlite3
from types import SimpleNamespace
import unittest

from fastapi.testclient import TestClient

from backend.app.api.app import create_app
from backend.app.application.library_queries import LibraryQueries
from backend.app.application.settings import SettingsService
from backend.app.config import DatabaseSettings
from backend.app.domain import CredentialKind, CredentialStatus
from backend.app.infrastructure.database import create_async_session_factory
from backend.app.repositories.unit_of_work import SqlAlchemyUnitOfWork
from backend.app.workers.obsidian import ObsidianJobService
from backend.app.workers.processing_worker import ProcessingWorker
from backend.tests.support.p3_database import p3_database_fixture


class _CredentialService:
    async def status(self, kind: CredentialKind) -> CredentialStatus:
        return CredentialStatus(
            kind=kind, has_key=False, key_tail=None, environment_managed=False
        )


class ObsidianJobsApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self._database_context = p3_database_fixture(
            prefix="study-app-p5-obsidian-api-"
        )
        self.database = await self._database_context.__aenter__()
        root = self.database.database_path.parents[1]
        settings = SettingsService(
            settings_path=root / "settings.json",
            root=root,
            credential_service=_CredentialService(),
            environment_snapshot={},
            default_dirs={
                "pdfDir": root / "pdfs",
                "explainerDir": root / "explainers",
                "translationDir": root / "translations",
            },
        )

        class Services:
            schema_revision = "20260807_03"
            legacy = SimpleNamespace(settings=settings)

            async def dispose(self) -> None:
                return None

        self.client_context = TestClient(
            create_app(
                Services(),
                self.database.session_factory,
                required_schema_revision="20260807_03",
            )
        )
        self.client = self.client_context.__enter__()

    async def _enable_obsidian(self) -> None:
        root = self.database.database_path.parents[1]
        (root / "vault").mkdir(exist_ok=True)
        settings = self.client.app.state.container.legacy.settings
        await settings.update(
            {
                "obsidianEnabled": True,
                "obsidianVaultPath": str(root / "vault"),
                "obsidianRootFolder": "Research",
                "obsidianPdfMode": "none",
            }
        )
        work_factory = lambda: SqlAlchemyUnitOfWork(self.database.session_factory)
        queries = LibraryQueries(
            work_factory,
            pdf_files=SimpleNamespace(has_pdf=lambda _paper: False),
        )
        self.client.app.state.container.obsidian_jobs = ObsidianJobService(
            work_factory,
            settings_service=settings,
            library_queries=queries,
        )

    async def asyncTearDown(self) -> None:
        if self.client_context is not None:
            self.client_context.__exit__(None, None, None)
        await self._database_context.__aexit__(None, None, None)

    async def test_obsidian_is_disabled_by_default(self) -> None:
        before = self._counts("processing_jobs", "obsidian_exports")

        status = self.client.get("/api/v2/obsidian/status")
        self.assertEqual(200, status.status_code, status.text)
        self.assertFalse(status.json()["enabled"])

        for path in (
            "/api/v2/papers/paper-1/exports/obsidian",
            "/api/v2/obsidian/sync",
            "/api/v2/obsidian/test",
        ):
            with self.subTest(path=path):
                response = self.client.post(path, json={})
                self.assertEqual(409, response.status_code, response.text)
                self.assertEqual("OBSIDIAN_DISABLED", response.json()["error"]["code"])

        self.assertEqual(before, self._counts("processing_jobs", "obsidian_exports"))

    async def test_obsidian_exports_schema_matches_p1_contract(self) -> None:
        with sqlite3.connect(self.database.database_path) as connection:
            columns = tuple(
                row[1] for row in connection.execute("PRAGMA table_info(obsidian_exports)")
            )
            foreign_keys = tuple(
                connection.execute("PRAGMA foreign_key_list(obsidian_exports)")
            )
            indexes = tuple(connection.execute("PRAGMA index_list(obsidian_exports)"))
            unexpected = tuple(
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master "
                    "WHERE type='table' AND name IN "
                    "('obsidian_projection', 'vault_exports') ORDER BY name"
                )
            )

        self.assertEqual(
            (
                "id",
                "paper_id",
                "artifact_id",
                "target_path",
                "source_hash",
                "exported_hash",
                "status",
                "exported_at",
                "error_message",
            ),
            columns,
        )
        self.assertTrue(
            any(row[2] == "papers" and row[3] == "paper_id" for row in foreign_keys)
        )
        self.assertTrue(any(row[2] == 1 for row in indexes), indexes)
        self.assertEqual((), unexpected)

    async def test_post_obsidian_enqueues_without_writing_vault(self) -> None:
        await self._enable_obsidian()

        export = self.client.post(
            "/api/v2/papers/paper-1/exports/obsidian",
            json={"dryRun": True},
        )
        sync = self.client.post(
            "/api/v2/obsidian/sync",
            json={
                "dryRun": False,
                "applyCleanup": False,
                "cleanupPlanSha": None,
            },
        )

        self.assertEqual((202, 202), (export.status_code, sync.status_code))
        payloads = (export.json(), sync.json())
        self.assertEqual(
            ("obsidian_export", "obsidian_sync"),
            tuple(payload["job"]["jobType"] for payload in payloads),
        )
        self.assertTrue(all(payload["job"]["status"] == "queued" for payload in payloads))
        self.assertTrue(all(payload["deduplicated"] is False for payload in payloads))
        self.assertNotIn("vaultPath", export.text + sync.text)
        with sqlite3.connect(self.database.database_path) as connection:
            rows = tuple(
                connection.execute(
                    "SELECT job_type,spec_json,progress_json FROM processing_jobs "
                    "WHERE job_type IN ('obsidian_export','obsidian_sync') ORDER BY job_type"
                )
            )
            ledger_count = connection.execute(
                "SELECT count(*) FROM obsidian_exports"
            ).fetchone()[0]
        self.assertEqual(2, len(rows))
        self.assertEqual(("obsidian_export", "obsidian_sync"), tuple(row[0] for row in rows))
        self.assertTrue(all('"schemaVersion":1' in row[1] for row in rows))
        self.assertTrue(all(row[2] == "{}" for row in rows))
        self.assertEqual(0, ledger_count)
        vault = self.database.database_path.parents[1] / "vault"
        self.assertTrue(vault.is_dir())
        self.assertEqual([], list(vault.iterdir()))

    async def test_library_snapshot_uses_the_frozen_content_identity_contract(self) -> None:
        await self._enable_obsidian()

        response = self.client.post(
            "/api/v2/obsidian/sync",
            json={"dryRun": True, "applyCleanup": False, "cleanupPlanSha": None},
        )

        self.assertEqual(202, response.status_code, response.text)
        job_id = response.json()["job"]["id"]
        with closing(sqlite3.connect(self.database.database_path)) as connection:
            raw_spec = connection.execute(
                "SELECT spec_json FROM processing_jobs WHERE id=?",
                (job_id,),
            ).fetchone()[0]
        snapshot = json.loads(raw_spec)["arguments"]["librarySnapshot"]
        self.assertEqual({"items", "sha256"}, set(snapshot))
        self.assertGreater(len(snapshot["items"]), 0)
        for item in snapshot["items"]:
            self.assertEqual(
                {
                    "artifactHeads",
                    "noteSha256",
                    "paperId",
                    "pdfSha256",
                    "sourceContentSha256",
                    "sourceDocumentId",
                },
                set(item),
            )
            self.assertEqual(
                sorted(item["artifactHeads"], key=lambda head: head["kind"]),
                item["artifactHeads"],
            )
        canonical_items = json.dumps(
            {"items": snapshot["items"]},
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
            allow_nan=False,
        ).encode("utf-8")
        self.assertEqual(hashlib.sha256(canonical_items).hexdigest(), snapshot["sha256"])

    async def test_obsidian_job_spec_survives_restart_with_exact_options_and_snapshot(self) -> None:
        from backend.app.workers.obsidian import ObsidianJobHandler

        await self._enable_obsidian()
        cleanup_sha = "a" * 64
        export = self.client.post(
            "/api/v2/papers/paper-1/exports/obsidian",
            json={"dryRun": True},
        ).json()
        sync = self.client.post(
            "/api/v2/obsidian/sync",
            json={
                "dryRun": False,
                "applyCleanup": True,
                "cleanupPlanSha": cleanup_sha,
            },
        ).json()
        job_ids = (export["job"]["id"], sync["job"]["id"])
        with sqlite3.connect(self.database.database_path) as connection:
            before_specs = tuple(
                connection.execute(
                    "SELECT id,spec_json FROM processing_jobs WHERE id IN (?,?) ORDER BY id",
                    job_ids,
                )
            )
            connection.execute(
                "UPDATE processing_jobs SET progress_json = ? WHERE id IN (?,?)",
                ('{"stage":"later_settings","completed":99}', *job_ids),
            )
            connection.commit()

        settings = self.client.app.state.container.legacy.settings
        await settings.update({"obsidianRootFolder": "ChangedAfterEnqueue"})
        self.client_context.__exit__(None, None, None)
        self.client_context = None
        await self.database.session_factory.kw["bind"].dispose()

        restarted_factory = create_async_session_factory(
            DatabaseSettings(self.database.database_path)
        )
        calls: list[object] = []

        async def export_snapshot(spec: object) -> dict[str, int]:
            calls.append(spec)
            return {
                "exported": 0,
                "unchanged": 0,
                "conflicts": 0,
                "errors": 0,
                "skipped": 0,
                "userManaged": 0,
                "orphaned": 0,
                "deleted": 0,
            }

        root = self.database.database_path.parents[1]
        restarted_settings = SettingsService(
            settings_path=root / "settings.json",
            root=root,
            credential_service=_CredentialService(),
            environment_snapshot={},
            default_dirs={
                "pdfDir": root / "pdfs",
                "explainerDir": root / "explainers",
                "translationDir": root / "translations",
            },
        )
        work_factory = lambda: SqlAlchemyUnitOfWork(restarted_factory)
        restarted_service = ObsidianJobService(
            work_factory,
            settings_service=restarted_settings,
            library_queries=LibraryQueries(
                work_factory,
                pdf_files=SimpleNamespace(has_pdf=lambda _paper: False),
            ),
        )
        handler = ObsidianJobHandler(restarted_service, exporter=export_snapshot)
        worker = ProcessingWorker(
            work_factory,
            handlers={"obsidian_export": handler, "obsidian_sync": handler},
            worker_id="obsidian-restart-worker",
            # Enqueued jobs use the real UTC clock; keep the restart worker
            # deterministically after those timestamps on every test day.
            clock=lambda: datetime(2030, 1, 1, tzinfo=timezone.utc),
        )
        try:
            self.assertTrue(await worker.run_once())
            self.assertTrue(await worker.run_once())
            self.assertEqual(2, len(calls))
            export_spec = next(spec for spec in calls if spec.job_type == "obsidian_export")
            sync_spec = next(spec for spec in calls if spec.job_type == "obsidian_sync")
            self.assertTrue(export_spec.dry_run)
            self.assertFalse(sync_spec.dry_run)
            self.assertTrue(sync_spec.apply_cleanup)
            self.assertEqual(cleanup_sha, sync_spec.cleanup_plan_sha)
            self.assertEqual("Research", sync_spec.settings_snapshot["rootFolder"])
            with sqlite3.connect(self.database.database_path) as connection:
                after_specs = tuple(
                    connection.execute(
                        "SELECT id,spec_json FROM processing_jobs WHERE id IN (?,?) ORDER BY id",
                        job_ids,
                    )
                )
                statuses = tuple(
                    row[0]
                    for row in connection.execute(
                        "SELECT status FROM processing_jobs WHERE id IN (?,?) ORDER BY id",
                        job_ids,
                    )
                )
            self.assertEqual(before_specs, after_specs)
            self.assertEqual(("succeeded", "succeeded"), statuses)
        finally:
            await restarted_factory.kw["bind"].dispose()

    async def test_status_and_test_routes_do_not_enqueue_or_project(self) -> None:
        await self._enable_obsidian()
        probe_calls: list[str] = []

        async def test_access() -> bool:
            probe_calls.append("probe")
            return True

        service = self.client.app.state.container.obsidian_jobs
        service.access_tester = test_access
        before = self._counts("processing_jobs", "obsidian_exports")

        status = self.client.get("/api/v2/obsidian/status")
        probe = self.client.post("/api/v2/obsidian/test", json={})

        self.assertEqual(200, status.status_code, status.text)
        self.assertEqual(
            {
                "enabled",
                "vaultConfigured",
                "writable",
                "rootFolder",
                "pdfMode",
                "lastJob",
                "aggregate",
            },
            set(status.json()),
        )
        self.assertEqual(
            {
                "exported",
                "unchanged",
                "conflicts",
                "errors",
                "skipped",
                "userManaged",
                "orphaned",
                "deleted",
            },
            set(status.json()["aggregate"]),
        )
        self.assertNotIn(str(self.database.database_path.parents[1]), status.text)
        self.assertEqual(200, probe.status_code, probe.text)
        self.assertEqual({"ok": True}, probe.json())
        self.assertEqual(["probe"], probe_calls)
        self.assertEqual(before, self._counts("processing_jobs", "obsidian_exports"))

    async def test_worker_terminal_summary_truth_table(self) -> None:
        from backend.app.workers.obsidian import ObsidianJobHandler

        await self._enable_obsidian()
        first = self.client.post(
            "/api/v2/papers/paper-1/exports/obsidian", json={"dryRun": False}
        ).json()["job"]["id"]
        second = self.client.post(
            "/api/v2/papers/paper-2/exports/obsidian", json={"dryRun": True}
        ).json()["job"]["id"]
        third = self.client.post(
            "/api/v2/obsidian/sync", json={"dryRun": False}
        ).json()["job"]["id"]
        expected = {
            first: {
                "exported": 2, "unchanged": 0, "conflicts": 1, "errors": 0,
                "skipped": 0, "userManaged": 0, "orphaned": 0, "deleted": 0,
            },
            second: {
                "exported": 0, "unchanged": 0, "conflicts": 0, "errors": 3,
                "skipped": 0, "userManaged": 0, "orphaned": 0, "deleted": 0,
            },
            third: {
                "exported": 0, "unchanged": 0, "conflicts": 0, "errors": 0,
                "skipped": 0, "userManaged": 0, "orphaned": 0, "deleted": 0,
            },
        }

        async def export_snapshot(spec: object) -> dict[str, int]:
            if spec.job_type == "obsidian_sync":
                return expected[third]
            return expected[first if spec.paper_id == "paper-1" else second]

        work_factory = lambda: SqlAlchemyUnitOfWork(self.database.session_factory)
        handler = ObsidianJobHandler(
            self.client.app.state.container.obsidian_jobs,
            exporter=export_snapshot,
        )
        worker = ProcessingWorker(
            work_factory,
            handlers={"obsidian_export": handler, "obsidian_sync": handler},
            worker_id="obsidian-truth-table-worker",
        )
        self.assertTrue(await worker.run_once())
        self.assertTrue(await worker.run_once())
        self.assertTrue(await worker.run_once())
        self.assertFalse(await worker.run_once())

        connection = sqlite3.connect(self.database.database_path)
        try:
            rows = {
                row[0]: (row[1], row[2])
                for row in connection.execute(
                    "SELECT id,status,result_json FROM processing_jobs WHERE id IN (?,?,?)",
                    (first, second, third),
                )
            }
        finally:
            connection.close()
        for job_id, counts in expected.items():
            with self.subTest(job_id=job_id):
                expected_status = "failed" if counts["errors"] and sum(counts.values()) == counts["errors"] else "succeeded"
                self.assertEqual(expected_status, rows[job_id][0])
                self.assertEqual(counts, __import__("json").loads(rows[job_id][1]))

    def _counts(self, *tables: str) -> tuple[int, ...]:
        with sqlite3.connect(self.database.database_path) as connection:
            return tuple(
                int(connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0])
                for table in tables
            )


if __name__ == "__main__":
    unittest.main()
