from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
import asyncio
from contextlib import contextmanager
import hashlib
from io import StringIO
import json
from pathlib import Path
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest import mock
from unittest.mock import patch

from backend.tests.support.p4_identity import p4_identity_fixture, sha256_file


def _api():
    from backend.app.api.compat.database_identity import DatabaseEvidenceIdentityService
    from backend.app.cli.runtime_owner import RuntimeOwnerService
    from backend.app.providers.runtime_lease import (
        ProcessEvidence,
        RuntimeProcessSnapshot,
    )

    return (
        DatabaseEvidenceIdentityService,
        RuntimeOwnerService,
        ProcessEvidence,
        RuntimeProcessSnapshot,
    )


class _Inspector:
    def __init__(self, snapshot: object) -> None:
        self.snapshot_value = snapshot
        self.calls = 0

    def snapshot(self) -> object:
        self.calls += 1
        return self.snapshot_value


def _identity(fixture: object, service_type: type) -> Path:
    path = fixture.root / "live-database-identity-v1.json"
    service_type().create_live_database_identity(
        database=fixture.database_path,
        p0_origin_receipt=fixture.receipt_path,
        expected_p0_origin_receipt_sha256=fixture.receipt_file_sha256,
        origin_backup=fixture.origin_backup_path,
        origin_manifest=fixture.origin_manifest_path,
        output=path,
    )
    return path


def _candidate_identity(fixture: object, service_type: type) -> Path:
    from backend.app.infrastructure.database_backup import create_verified_backup

    parent_path = _identity(fixture, service_type)
    backup = create_verified_backup(
        fixture.database_path,
        fixture.root / "candidate-backups",
        label="candidate",
    )
    shutil.copyfile(backup.backup_path, fixture.candidate_database_path)
    output = fixture.root / "candidate-database-identity-v1.json"
    service_type().create_descendant_database_identity(
        database=fixture.candidate_database_path,
        subject_kind="p4_candidate",
        parent_database_identity_manifest=parent_path,
        parent_backup=backup.backup_path,
        parent_manifest=backup.manifest_path,
        output=output,
    )
    return output


def _process(fixture: object, process_type: type, **changes: object):
    values = {
        "pid": os.getpid(),
        "executable_path": Path("C:/Program Files/nodejs/node.exe"),
        "entrypoint_path": fixture.entrypoint_path,
        "cwd": fixture.entrypoint_path.parent,
        "argv": ("node", str(fixture.entrypoint_path)),
        "listener_host": "127.0.0.1",
        "listener_port": 43123,
        "database_paths": (fixture.database_path,),
        "process_role": "node",
        "environment": "live",
    }
    values.update(changes)
    return process_type(**values)


def _snapshot(snapshot_type: type, nodes: tuple[object, ...], python_roles: tuple[object, ...] = ()):
    return snapshot_type(node_processes=nodes, live_python_roles=python_roles)


def _owner_arguments(fixture: object, identity_path: Path, marker_path: Path) -> dict[str, object]:
    return {
        "database_identity_manifest": identity_path,
        "p0_origin_receipt": fixture.receipt_path,
        "expected_p0_origin_receipt_sha256": fixture.receipt_file_sha256,
        "origin_backup": fixture.origin_backup_path,
        "origin_manifest": fixture.origin_manifest_path,
        "runtime_namespace": "production",
        "expected_entrypoint_path": fixture.entrypoint_path,
        "owner_marker": marker_path,
    }


@contextmanager
def _real_runtime_process(
    executable: str,
    arguments: list[str],
    *,
    cwd: Path,
):
    process = subprocess.Popen(
        [executable, *arguments],
        cwd=cwd,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
    )
    try:
        assert process.stdout is not None
        ready = process.stdout.readline().strip()
        if ready != "READY":
            assert process.stderr is not None
            raise AssertionError(
                f"runtime fixture did not become ready: {ready!r} {process.stderr.read()!r}"
            )
        yield process
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=10)
        if process.stdout is not None:
            process.stdout.close()
        if process.stderr is not None:
            process.stderr.close()


class RuntimeOwnershipTests(unittest.TestCase):
    def test_runtime_owner_cli_verifies_live_and_creates_descendant_identity(self) -> None:
        from backend.app.cli.runtime_owner import run
        from backend.app.infrastructure.database_backup import create_verified_backup

        with p4_identity_fixture() as fixture:
            live_path = fixture.root / "cli-live-identity.json"
            created = run(
                [
                    "create-live-database-identity",
                    "--database",
                    str(fixture.database_path),
                    "--p0-origin-receipt",
                    str(fixture.receipt_path),
                    "--expected-p0-origin-receipt-sha256",
                    fixture.receipt_file_sha256,
                    "--origin-backup",
                    str(fixture.origin_backup_path),
                    "--origin-manifest",
                    str(fixture.origin_manifest_path),
                    "--output",
                    str(live_path),
                ]
            )
            self.assertEqual("live", created["subjectKind"])
            verified = run(
                [
                    "verify-live-database-identity",
                    "--database",
                    str(fixture.database_path),
                    "--database-identity-manifest",
                    str(live_path),
                    "--p0-origin-receipt",
                    str(fixture.receipt_path),
                    "--expected-p0-origin-receipt-sha256",
                    fixture.receipt_file_sha256,
                    "--origin-backup",
                    str(fixture.origin_backup_path),
                    "--origin-manifest",
                    str(fixture.origin_manifest_path),
                ]
            )
            self.assertEqual("read_only", verified["verificationMode"])
            backup = create_verified_backup(
                fixture.database_path,
                fixture.root / "cli-backups",
                label="p4-cli",
            )
            shutil.copyfile(backup.backup_path, fixture.candidate_database_path)
            descendant_path = fixture.root / "cli-descendant-identity.json"
            descendant = run(
                [
                    "create-descendant-database-identity",
                    "--database",
                    str(fixture.candidate_database_path),
                    "--subject-kind",
                    "p4_rehearsal",
                    "--parent-database-identity-manifest",
                    str(live_path),
                    "--parent-backup",
                    str(backup.backup_path),
                    "--parent-manifest",
                    str(backup.manifest_path),
                    "--output",
                    str(descendant_path),
                ]
            )
            self.assertEqual("p4_rehearsal", descendant["subjectKind"])
            self.assertEqual(created["databaseLineageId"], descendant["databaseLineageId"])

    def test_candidate_drain_quiesces_api_worker_scheduler_and_preserves_live_node(
        self,
    ) -> None:
        from backend.app.runtime import CandidateDrainCoordinator

        async def scenario() -> None:
            events: list[str] = []
            api_release = asyncio.Event()
            worker_release = asyncio.Event()
            scheduler_release = asyncio.Event()

            class Api:
                draining = False

                def begin_draining(self) -> None:
                    self.draining = True
                    events.append("api.draining")

                async def wait_for_in_flight(self, _deadline: float) -> None:
                    events.append("api.wait")
                    await api_release.wait()

                def admit(self) -> bool:
                    return not self.draining

                def finish_draining(self) -> None:
                    events.append("api.stopped")

                async def wait_stopped(self, _deadline: float) -> None:
                    events.append("api.wait_stopped")

            class Worker:
                claims_stopped = False

                def stop_claims(self) -> None:
                    self.claims_stopped = True
                    events.append("worker.stop_claims")

                async def wait_for_in_flight(self, _deadline: float) -> None:
                    events.append("worker.wait")
                    await worker_release.wait()

                def stop(self) -> None:
                    events.append("worker.stopped")

                async def wait_stopped(self, _deadline: float) -> None:
                    events.append("worker.wait_stopped")

                def release_lease(self) -> None:
                    events.append("worker.release")

            class Scheduler:
                ticks_stopped = False

                def stop_ticks(self) -> None:
                    self.ticks_stopped = True
                    events.append("scheduler.stop_ticks")

                async def settle_started_ticks(self, _deadline: float) -> None:
                    events.append("scheduler.wait")
                    await scheduler_release.wait()
                    events.append("scheduler.next_run_committed")

                def stop(self) -> None:
                    events.append("scheduler.stopped")

                async def wait_stopped(self, _deadline: float) -> None:
                    events.append("scheduler.wait_stopped")

                def release_lease(self) -> None:
                    events.append("scheduler.release")

            class Provider:
                def cancel(self) -> None:
                    events.append("provider.cancel")

            api = Api()
            worker = Worker()
            scheduler = Scheduler()
            coordinator = CandidateDrainCoordinator(
                api=api,
                worker=worker,
                scheduler=scheduler,
                provider_scope=Provider(),
                clock=asyncio.get_running_loop().time,
            )
            live_evidence = ("live-node-pid", "live-port", "live-marker-sha")
            task = asyncio.create_task(
                coordinator.drain(asyncio.get_running_loop().time() + 5)
            )
            for _ in range(20):
                if "api.wait" in events:
                    break
                await asyncio.sleep(0)
            self.assertTrue(api.draining)
            self.assertFalse(api.admit())
            self.assertEqual(["api.draining", "api.wait"], events)
            api_release.set()
            for _ in range(20):
                if worker.claims_stopped:
                    break
                await asyncio.sleep(0)
            self.assertTrue(worker.claims_stopped)
            self.assertFalse(api.admit())
            worker_release.set()
            for _ in range(20):
                if scheduler.ticks_stopped:
                    break
                await asyncio.sleep(0)
            self.assertTrue(scheduler.ticks_stopped)
            scheduler_release.set()
            report = await task
            self.assertEqual("drained", report.status)
            self.assertNotIn("provider.cancel", events)
            self.assertLess(events.index("api.stopped"), events.index("api.wait_stopped"))
            self.assertLess(events.index("api.wait_stopped"), events.index("worker.stop_claims"))
            self.assertLess(events.index("worker.stopped"), events.index("worker.wait_stopped"))
            self.assertLess(events.index("worker.wait_stopped"), events.index("worker.release"))
            self.assertLess(events.index("scheduler.stopped"), events.index("scheduler.wait_stopped"))
            self.assertLess(events.index("scheduler.wait_stopped"), events.index("scheduler.release"))
            self.assertEqual(live_evidence, ("live-node-pid", "live-port", "live-marker-sha"))

        asyncio.run(scenario())

    def test_candidate_drain_timeout_cancels_only_candidate_provider_and_preserves_committed_artifact(
        self,
    ) -> None:
        from backend.app.runtime import CandidateDrainCoordinator

        async def scenario() -> None:
            events: list[str] = []

            class Api:
                def begin_draining(self) -> None:
                    events.append("api.draining")

                async def wait_for_in_flight(self, _deadline: float) -> None:
                    await asyncio.Event().wait()

                def finish_draining(self) -> None:
                    events.append("api.stopped")

            class Worker:
                async def wait_for_in_flight(self, _deadline: float) -> None:
                    await asyncio.Event().wait()

                def stop_claims(self) -> None:
                    events.append("worker.stop_claims")

                def release_lease(self) -> None:
                    events.append("worker.release")

            class Scheduler:
                async def settle_started_ticks(self, _deadline: float) -> None:
                    await asyncio.Event().wait()

                def stop_ticks(self) -> None:
                    events.append("scheduler.stop_ticks")

                def release_lease(self) -> None:
                    events.append("scheduler.release")

            class Provider:
                cancelled = 0

                def cancel(self) -> None:
                    self.cancelled += 1
                    events.append("provider.cancel")

            provider = Provider()
            committed_artifact = {"status": "committed", "contentSha256": "fixed"}
            coordinator = CandidateDrainCoordinator(
                api=Api(),
                worker=Worker(),
                scheduler=Scheduler(),
                provider_scope=provider,
                clock=asyncio.get_running_loop().time,
            )
            report = await coordinator.drain(asyncio.get_running_loop().time() + 0.03)
            self.assertEqual("timed_out", report.status)
            self.assertEqual(1, provider.cancelled)
            self.assertEqual({"status": "committed", "contentSha256": "fixed"}, committed_artifact)
            self.assertEqual(["api.draining", "provider.cancel"], events)

        asyncio.run(scenario())

    def test_candidate_process_requires_exactly_one_role(self) -> None:
        from backend.app.runtime import RuntimeRoleError, parse_process_role

        self.assertEqual("api", parse_process_role({"API_PROCESS_ROLE": "api"}))
        for environment in (
            {},
            {"API_PROCESS_ROLE": "api,worker"},
            {"API_PROCESS_ROLE": "api", "API_PROCESS_ROLES": "api,worker"},
            {"API_PROCESS_ROLE": "unknown"},
        ):
            with self.subTest(environment=environment):
                with self.assertRaises(RuntimeRoleError):
                    parse_process_role(environment)

    def test_candidate_api_worker_scheduler_coexist_in_same_namespace(self) -> None:
        from backend.app.config import DatabaseSettings
        from backend.app.runtime import CandidateRuntimeGuard
        from backend.app.providers.runtime_lease import (
            ApiRuntimePresence,
            RoleScopedRuntimeLease,
        )

        with p4_identity_fixture() as fixture:
            identity_type, _owner_type, _process_type, _snapshot_type = _api()
            identity_path = _candidate_identity(fixture, identity_type)
            guard = CandidateRuntimeGuard()
            guard.validate_role(
                identity_path,
                database=DatabaseSettings(fixture.candidate_database_path),
                environment="candidate",
                runtime_namespace="p4-test",
                role="api",
            )
            lease_root = fixture.root / "leases"
            api_presence = ApiRuntimePresence(lease_root)
            api_one = api_presence.acquire(
                identity_path,
                environment="candidate",
                runtime_namespace="p4-test",
                owner_id="api-1",
                pid=99,
            )
            api_two = api_presence.acquire(
                identity_path,
                environment="candidate",
                runtime_namespace="p4-test",
                owner_id="api-2",
                pid=100,
            )
            store = RoleScopedRuntimeLease(lease_root, pid_probe=lambda _pid: True)
            worker = store.acquire(
                identity_path,
                environment="candidate",
                runtime_namespace="p4-test",
                role="worker",
                owner_id="worker-1",
                pid=101,
            )
            scheduler = store.acquire(
                identity_path,
                environment="candidate",
                runtime_namespace="p4-test",
                role="scheduler",
                owner_id="scheduler-1",
                pid=102,
            )
            self.assertNotEqual(api_one.path, api_two.path)
            self.assertNotEqual(worker.path, scheduler.path)
            self.assertEqual(4, len(tuple(lease_root.glob("*.json"))))
            api_one.release()
            api_two.release()
            worker.release()
            scheduler.release()

    def test_candidate_startup_uses_singleton_lease_only_for_worker_and_scheduler(
        self,
    ) -> None:
        """The API participates in drain control but never owns a singleton lease."""
        from backend.app.cli import candidate_runtime

        root = Path(tempfile.mkdtemp(prefix="p4-role-startup-"))
        try:
            database = root / "candidate.db"
            database.write_bytes(b"SQLite format 3\x00")
            identity_path = root / "candidate-identity.json"
            identity_path.write_text("{}\n", encoding="utf-8")
            parent_backup = root / "parent.backup"
            parent_manifest = root / "parent.manifest.json"
            owner_marker = root / "owner.json"
            for path in (parent_backup, parent_manifest, owner_marker):
                path.write_text("evidence\n", encoding="utf-8")

            identity = SimpleNamespace(
                database_lineage_id="lineage-p4",
                subject_database_id="candidate-p4",
                parent_subject_database_id="live-p4",
                origin_receipt_file_sha256="receipt-file-sha",
            )
            owner = SimpleNamespace(
                database_lineage_id=identity.database_lineage_id,
                subject_database_id=identity.parent_subject_database_id,
                origin_receipt_file_sha256=identity.origin_receipt_file_sha256,
            )
            base_environment = {
                "RUNTIME_ENVIRONMENT": "candidate",
                "RUNTIME_NAMESPACE": "p4-role-startup",
                "DB_PATH": str(database),
                "DATABASE_IDENTITY_MANIFEST": str(identity_path),
                "CANDIDATE_PARENT_BACKUP": str(parent_backup),
                "CANDIDATE_PARENT_MANIFEST": str(parent_manifest),
                "PRODUCTION_OWNER_MARKER": str(owner_marker),
                "RUNTIME_LEASE_DIR": str(root / "leases"),
                "REQUIRED_SCHEMA_REVISION": "20260807_03",
            }

            for role in ("api", "worker", "scheduler"):
                with self.subTest(role=role):
                    environment = {
                        **base_environment,
                        "API_PROCESS_ROLE": role,
                    }
                    container = SimpleNamespace(
                        dispose=mock.AsyncMock(),
                        processing_worker=object(),
                        legacy=SimpleNamespace(scheduler=object()),
                        session_factory=object(),
                    )
                    lease_handle = mock.Mock()
                    lease_type = mock.Mock()
                    lease_type.return_value.acquire.return_value = lease_handle
                    api_presence_handle = mock.Mock()
                    api_presence_type = mock.Mock()
                    api_presence_type.return_value.acquire.return_value = (
                        api_presence_handle
                    )

                    patches = [
                        mock.patch.object(
                            candidate_runtime,
                            "CandidateRuntimeGuard",
                            return_value=mock.Mock(
                                validate_role=mock.Mock(return_value=identity)
                            ),
                        ),
                        mock.patch.object(
                            candidate_runtime,
                            "read_node_active_owner_marker",
                            return_value=owner,
                        ),
                        mock.patch.object(candidate_runtime, "verify_schema_revision"),
                        mock.patch.object(
                            candidate_runtime,
                            "legacy_p3_provider_factories",
                            return_value=(None, None),
                        ),
                        mock.patch.object(
                            candidate_runtime,
                            "RoleScopedRuntimeLease",
                            lease_type,
                        ),
                        mock.patch.object(
                            candidate_runtime,
                            "ApiRuntimePresence",
                            api_presence_type,
                            create=True,
                        ),
                        mock.patch.object(
                            candidate_runtime,
                            "bootstrap",
                            return_value=container,
                        ),
                        mock.patch.object(
                            candidate_runtime,
                            "bootstrap_processing_worker",
                            return_value=container,
                        ),
                        mock.patch.object(
                            candidate_runtime,
                            "create_app",
                            return_value=object(),
                        ),
                        mock.patch.object(
                            candidate_runtime,
                            "_run_api",
                            new=mock.AsyncMock(),
                        ),
                        mock.patch.object(
                            candidate_runtime,
                            "_run_worker",
                            new=mock.AsyncMock(),
                        ),
                        mock.patch.object(
                            candidate_runtime,
                            "_run_scheduler",
                            new=mock.AsyncMock(),
                        ),
                    ]
                    with mock.patch.dict(
                        candidate_runtime.os.environ,
                        environment,
                        clear=False,
                    ):
                        for patcher in patches:
                            patcher.start()
                        try:
                            result = asyncio.run(
                                candidate_runtime.run(
                                    ["--role", role],
                                    environment=environment,
                                    stderr=StringIO(),
                                )
                            )
                        finally:
                            for patcher in reversed(patches):
                                patcher.stop()

                    self.assertEqual(0, result)
                    if role == "api":
                        lease_type.return_value.acquire.assert_not_called()
                        lease_handle.release.assert_not_called()
                        api_presence_type.return_value.acquire.assert_called_once()
                        api_presence_handle.release.assert_called_once()
                    else:
                        api_presence_type.return_value.acquire.assert_not_called()
                        api_presence_handle.release.assert_not_called()
                        lease_type.return_value.acquire.assert_called_once()
                        self.assertEqual(
                            role,
                            lease_type.return_value.acquire.call_args.kwargs["role"],
                        )
                        lease_handle.release.assert_called_once()
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_candidate_role_rejects_swapped_database_identity(self) -> None:
        from backend.app.config import DatabaseSettings
        from backend.app.runtime import CandidateRuntimeGuard, RuntimeRoleError

        with p4_identity_fixture() as fixture:
            identity_type, _owner_type, _process_type, _snapshot_type = _api()
            identity_path = _candidate_identity(fixture, identity_type)
            swapped_database = fixture.root / "swapped-candidate.db"
            shutil.copy2(fixture.candidate_database_path, swapped_database)

            with self.assertRaises(RuntimeRoleError) as caught:
                CandidateRuntimeGuard().validate_role(
                    identity_path,
                    database=DatabaseSettings(swapped_database),
                    environment="candidate",
                    runtime_namespace="p4-test",
                    role="worker",
                )

            self.assertEqual(
                "DATABASE_IDENTITY_SUBJECT_MISMATCH",
                caught.exception.code,
            )

    def test_candidate_worker_rejects_second_owner_in_same_namespace(self) -> None:
        from backend.app.providers.runtime_lease import RoleLeaseError, RoleScopedRuntimeLease

        with p4_identity_fixture() as fixture:
            identity_type, _owner_type, _process_type, _snapshot_type = _api()
            identity_path = _candidate_identity(fixture, identity_type)
            store = RoleScopedRuntimeLease(fixture.root / "leases", pid_probe=lambda _pid: True)
            first = store.acquire(
                identity_path,
                environment="candidate",
                runtime_namespace="p4-test",
                role="worker",
                owner_id="worker-1",
                pid=101,
            )
            try:
                with self.assertRaisesRegex(RoleLeaseError, "WORKER_ALREADY_OWNED"):
                    store.acquire(
                        identity_path,
                        environment="candidate",
                        runtime_namespace="p4-test",
                        role="worker",
                        owner_id="worker-2",
                        pid=102,
                    )
            finally:
                first.release()

    def test_candidate_scheduler_rejects_second_owner_in_same_namespace(self) -> None:
        from backend.app.providers.runtime_lease import RoleLeaseError, RoleScopedRuntimeLease

        with p4_identity_fixture() as fixture:
            identity_type, _owner_type, _process_type, _snapshot_type = _api()
            identity_path = _candidate_identity(fixture, identity_type)
            store = RoleScopedRuntimeLease(fixture.root / "leases", pid_probe=lambda _pid: True)
            first = store.acquire(
                identity_path,
                environment="candidate",
                runtime_namespace="p4-test",
                role="scheduler",
                owner_id="scheduler-1",
                pid=101,
            )
            try:
                with self.assertRaisesRegex(RoleLeaseError, "SCHEDULER_ALREADY_OWNED"):
                    store.acquire(
                        identity_path,
                        environment="candidate",
                        runtime_namespace="p4-test",
                        role="scheduler",
                        owner_id="scheduler-2",
                        pid=102,
                    )
            finally:
                first.release()

    def test_expired_role_lease_distinguishes_live_owner_from_reused_pid(self) -> None:
        from backend.app.providers.runtime_lease import RoleLeaseError, RoleScopedRuntimeLease

        lease_started = datetime(2026, 8, 20, 0, 0, tzinfo=timezone.utc)
        retry_at = datetime(2026, 8, 23, 0, 0, tzinfo=timezone.utc)
        with p4_identity_fixture() as fixture:
            identity_type, _owner_type, _process_type, _snapshot_type = _api()
            identity_path = _candidate_identity(fixture, identity_type)
            lease_root = fixture.root / "leases"
            first = RoleScopedRuntimeLease(
                lease_root,
                clock=lambda: lease_started,
                pid_probe=lambda _pid: True,
                process_started_at_probe=lambda _pid: lease_started,
            ).acquire(
                identity_path,
                environment="candidate",
                runtime_namespace="p4-pid-reuse",
                role="scheduler",
                owner_id="scheduler-original",
                pid=101,
                lease_seconds=30,
            )
            try:
                same_process = RoleScopedRuntimeLease(
                    lease_root,
                    clock=lambda: retry_at,
                    pid_probe=lambda _pid: True,
                    process_started_at_probe=lambda _pid: lease_started,
                )
                with self.assertRaisesRegex(RoleLeaseError, "SCHEDULER_ALREADY_OWNED"):
                    same_process.acquire(
                        identity_path,
                        environment="candidate",
                        runtime_namespace="p4-pid-reuse",
                        role="scheduler",
                        owner_id="scheduler-blocked",
                        pid=102,
                    )

                reused_pid = RoleScopedRuntimeLease(
                    lease_root,
                    clock=lambda: retry_at,
                    pid_probe=lambda _pid: True,
                    process_started_at_probe=lambda _pid: retry_at,
                )
                replacement = reused_pid.acquire(
                    identity_path,
                    environment="candidate",
                    runtime_namespace="p4-pid-reuse",
                    role="scheduler",
                    owner_id="scheduler-replacement",
                    pid=103,
                )
                try:
                    document = json.loads(replacement.canonical_bytes.decode("utf-8"))
                    self.assertEqual("scheduler-replacement", document["ownerId"])
                finally:
                    replacement.release()
            finally:
                first.release()

    def test_candidate_role_lease_rejects_admission_while_drain_fence_exists(
        self,
    ) -> None:
        from backend.app.api.compat.database_identity import (
            canonical_json_bytes,
            load_database_evidence_identity_manifest,
        )
        from backend.app.providers.runtime_lease import ApiRuntimePresence, RoleLeaseError

        with p4_identity_fixture() as fixture:
            identity_type, _owner_type, _process_type, _snapshot_type = _api()
            identity_path = _candidate_identity(fixture, identity_type)
            identity = load_database_evidence_identity_manifest(identity_path)
            lease_root = fixture.root / "leases"
            lease_root.mkdir()
            request = {
                "schemaVersion": 1,
                "requestKind": "candidate-runtime-drain",
                "databaseLineageId": identity.database_lineage_id,
                "subjectDatabaseId": identity.subject_database_id,
                "runtimeNamespace": "p4-test",
                "role": "api",
            }
            payload = canonical_json_bytes(request)
            request_path = lease_root / (
                f"drain-{hashlib.sha256(payload).hexdigest()}.request"
            )
            request_path.write_bytes(payload)

            with self.assertRaises(RoleLeaseError) as raised:
                ApiRuntimePresence(lease_root).acquire(
                    identity_path,
                    environment="candidate",
                    runtime_namespace="p4-test",
                    owner_id="api-after-drain",
                    pid=os.getpid(),
                )

            self.assertEqual("CANDIDATE_RUNTIME_DRAINING", raised.exception.code)
            self.assertEqual((), tuple(lease_root.glob("*.json")))

    def test_filesystem_drain_orders_roles_and_holds_admission_fences(self) -> None:
        from backend.app.cli.runtime_owner import FilesystemCandidateRuntimeDrain
        from backend.app.providers.runtime_lease import (
            ApiRuntimePresence,
            RoleLeaseError,
            RoleScopedRuntimeLease,
        )

        with p4_identity_fixture() as fixture:
            identity_type, _owner_type, _process_type, _snapshot_type = _api()
            identity_path = _candidate_identity(fixture, identity_type)
            lease_root = fixture.root / "leases"
            store = RoleScopedRuntimeLease(lease_root, pid_probe=lambda _pid: True)
            api_presence = ApiRuntimePresence(lease_root)

            def acquire(role: str, owner_id: str):
                if role == "api":
                    return api_presence.acquire(
                        identity_path,
                        environment="candidate",
                        runtime_namespace="p4-ordered-drain",
                        owner_id=owner_id,
                        pid=os.getpid(),
                    )
                return store.acquire(
                    identity_path,
                    environment="candidate",
                    runtime_namespace="p4-ordered-drain",
                    role=role,
                    owner_id=owner_id,
                    pid=os.getpid(),
                )

            handles = {
                role: acquire(role, f"{role}-before-drain")
                for role in ("api", "worker", "scheduler")
            }
            drain = FilesystemCandidateRuntimeDrain(
                lease_root=lease_root,
                database_identity_manifest=identity_path,
                timeout_seconds=3,
            )
            result: list[tuple[str, ...]] = []
            errors: list[BaseException] = []

            def run_drain() -> None:
                try:
                    result.append(
                        drain.stop_and_wait(
                            database=fixture.candidate_database_path,
                            runtime_namespace="p4-ordered-drain",
                        )
                    )
                except BaseException as error:
                    errors.append(error)

            def wait_for_roles(expected: set[str]) -> None:
                deadline = time.monotonic() + 2
                while time.monotonic() < deadline:
                    roles = {
                        document.get("role")
                        for path in lease_root.glob("*.request")
                        for document in (json.loads(path.read_bytes()),)
                    }
                    if roles == expected:
                        return
                    time.sleep(0.01)
                self.fail(f"drain request roles did not become {expected!r}")

            thread = threading.Thread(target=run_drain)
            thread.start()
            try:
                wait_for_roles({"api"})
                handles["api"].release()
                wait_for_roles({"api", "worker"})
                handles["worker"].release()
                wait_for_roles({"api", "worker", "scheduler"})
                handles["scheduler"].release()
                thread.join(timeout=3)
                self.assertFalse(thread.is_alive())
                self.assertEqual([], errors)
                self.assertEqual([("api", "worker", "scheduler")], result)

                for role in ("api", "worker", "scheduler"):
                    with self.subTest(role=role):
                        with self.assertRaises(RoleLeaseError) as raised:
                            acquire(role, f"{role}-during-drain")
                        self.assertEqual(
                            "CANDIDATE_RUNTIME_DRAINING",
                            raised.exception.code,
                        )

                drain.release_fence()
                admitted = acquire("api", "api-after-drain")
                admitted.release()
            finally:
                for handle in handles.values():
                    handle.release()
                if thread.is_alive():
                    thread.join(timeout=3)

    def test_filesystem_drain_final_fence_blocks_admission_before_return(self) -> None:
        from backend.app.cli import runtime_owner
        from backend.app.api.compat.database_identity import canonical_json_bytes
        from backend.app.cli.runtime_owner import FilesystemCandidateRuntimeDrain
        from backend.app.providers.runtime_lease import (
            ApiRuntimePresence,
            RoleLeaseError,
            RoleScopedRuntimeLease,
            candidate_runtime_drain_request,
        )

        with p4_identity_fixture() as fixture:
            identity_type, _owner_type, _process_type, _snapshot_type = _api()
            identity_path = _candidate_identity(fixture, identity_type)
            identity = runtime_owner.load_database_evidence_identity_manifest(identity_path)
            lease_root = fixture.root / "leases"
            store = RoleScopedRuntimeLease(lease_root, pid_probe=lambda _pid: True)
            api_presence = ApiRuntimePresence(lease_root)

            def acquire(role: str, owner_id: str):
                if role == "api":
                    return api_presence.acquire(
                        identity_path,
                        environment="candidate",
                        runtime_namespace="p4-final-fence",
                        owner_id=owner_id,
                        pid=os.getpid(),
                    )
                return store.acquire(
                    identity_path,
                    environment="candidate",
                    runtime_namespace="p4-final-fence",
                    role=role,
                    owner_id=owner_id,
                    pid=os.getpid(),
                )

            handles = {
                role: acquire(role, f"{role}-before-final-fence")
                for role in ("api", "worker", "scheduler")
            }
            final_unsigned = {
                "schemaVersion": 1,
                "requestKind": "candidate-runtime-drain-final",
                "databaseLineageId": identity.database_lineage_id,
                "subjectDatabaseId": identity.subject_database_id,
                "runtimeNamespace": "p4-final-fence",
            }
            final_payload = canonical_json_bytes(final_unsigned)
            final_path = lease_root / (
                f"drain-final-{hashlib.sha256(final_payload).hexdigest()}.fence"
            )
            role_request_by_role = {
                role: candidate_runtime_drain_request(
                    lease_root,
                    identity,
                    runtime_namespace="p4-final-fence",
                    role=role,
                )[0]
                for role in ("api", "worker", "scheduler")
            }
            role_request_paths = set(role_request_by_role.values())
            observed_final_empty = threading.Event()
            resume_drain = threading.Event()
            release_roles = threading.Event()
            result: list[tuple[str, ...]] = []
            errors: list[BaseException] = []
            original_matching = runtime_owner._matching_candidate_roles

            def release_roles_in_order() -> None:
                for role in ("api", "worker", "scheduler"):
                    request_path = role_request_by_role[role]
                    while not request_path.exists():
                        if release_roles.is_set():
                            return
                        time.sleep(0.01)
                    for _attempt in range(50):
                        try:
                            handles[role].release()
                            break
                        except PermissionError:
                            time.sleep(0.01)
                    else:
                        raise AssertionError(f"could not release {role} lease")

            def matching_with_final_barrier(*args: object, **kwargs: object) -> set[str]:
                roles = original_matching(*args, **kwargs)
                if not roles and role_request_paths.issubset(
                    {path for path in lease_root.glob("*.request")}
                ):
                    if not observed_final_empty.is_set():
                        observed_final_empty.set()
                        if not resume_drain.wait(timeout=3):
                            raise AssertionError("drain final barrier was not released")
                return roles

            drain = FilesystemCandidateRuntimeDrain(
                lease_root=lease_root,
                database_identity_manifest=identity_path,
                timeout_seconds=3,
            )

            def run_drain() -> None:
                try:
                    result.append(
                        drain.stop_and_wait(
                            database=fixture.candidate_database_path,
                            runtime_namespace="p4-final-fence",
                        )
                    )
                except BaseException as error:
                    errors.append(error)

            thread = threading.Thread(target=run_drain)
            releaser = threading.Thread(target=release_roles_in_order)
            try:
                with mock.patch.object(
                    runtime_owner,
                    "_matching_candidate_roles",
                    side_effect=matching_with_final_barrier,
                ):
                    releaser.start()
                    thread.start()
                    self.assertTrue(observed_final_empty.wait(timeout=3))
                    self.assertTrue(
                        final_path.is_file(),
                        "the final admission fence must exist before drain returns",
                    )
                    for role in ("api", "worker", "scheduler"):
                        with self.subTest(role=role):
                            with self.assertRaises(RoleLeaseError) as raised:
                                acquire(role, f"{role}-during-final-fence")
                            self.assertEqual(
                                "CANDIDATE_RUNTIME_DRAINING",
                                raised.exception.code,
                            )
                    resume_drain.set()
                    thread.join(timeout=3)
            finally:
                resume_drain.set()
                release_roles.set()
                if thread.is_alive():
                    thread.join(timeout=3)
                if releaser.is_alive():
                    releaser.join(timeout=3)
                for handle in handles.values():
                    handle.release()

            self.assertFalse(thread.is_alive())
            self.assertEqual([], errors)
            self.assertEqual([("api", "worker", "scheduler")], result)
            # A completed drain retains the final fence until its owner releases it.
            self.assertTrue(final_path.is_file())
            drain.release_fence()
            self.assertFalse(final_path.exists())
            admitted = acquire("api", "api-after-final-fence")
            admitted.release()

    def test_filesystem_drain_final_fence_has_one_exclusive_owner(self) -> None:
        from backend.app.cli.runtime_owner import (
            FilesystemCandidateRuntimeDrain,
            RuntimeOwnerError,
        )

        with p4_identity_fixture() as fixture:
            identity_type, _owner_type, _process_type, _snapshot_type = _api()
            identity_path = _candidate_identity(fixture, identity_type)
            lease_root = fixture.root / "leases"
            first = FilesystemCandidateRuntimeDrain(
                lease_root=lease_root,
                database_identity_manifest=identity_path,
                timeout_seconds=2,
            )
            second = FilesystemCandidateRuntimeDrain(
                lease_root=lease_root,
                database_identity_manifest=identity_path,
                timeout_seconds=2,
            )
            try:
                self.assertEqual(
                    ("api", "worker", "scheduler"),
                    first.stop_and_wait(
                        database=fixture.candidate_database_path,
                        runtime_namespace="p4-exclusive-drain",
                    ),
                )
                with self.assertRaises(RuntimeOwnerError) as raised:
                    second.stop_and_wait(
                        database=fixture.candidate_database_path,
                        runtime_namespace="p4-exclusive-drain",
                    )
                self.assertEqual(
                    "CANDIDATE_RUNTIME_DRAIN_ALREADY_ACTIVE",
                    raised.exception.code,
                )
            finally:
                try:
                    second.release_fence()
                except RuntimeOwnerError:
                    pass
                first.release_fence()

    def test_filesystem_drain_release_retries_after_partial_unlink_failure(
        self,
    ) -> None:
        from backend.app.cli import runtime_owner
        from backend.app.cli.runtime_owner import (
            FilesystemCandidateRuntimeDrain,
            RuntimeOwnerError,
        )

        with p4_identity_fixture() as fixture:
            identity_type, _owner_type, _process_type, _snapshot_type = _api()
            identity_path = _candidate_identity(fixture, identity_type)
            lease_root = fixture.root / "leases"
            drain = FilesystemCandidateRuntimeDrain(
                lease_root=lease_root,
                database_identity_manifest=identity_path,
                timeout_seconds=2,
            )
            drain.stop_and_wait(
                database=fixture.candidate_database_path,
                runtime_namespace="p4-retry-release",
            )
            original_remove = runtime_owner._remove_drain_requests
            failed_once = False

            def fail_final_once(
                requests: tuple[tuple[Path, bytes], ...],
                *,
                missing_ok: bool,
            ) -> None:
                nonlocal failed_once
                if (
                    requests
                    and requests[0][0].suffix == ".fence"
                    and not failed_once
                ):
                    failed_once = True
                    raise RuntimeOwnerError(
                        "CANDIDATE_RUNTIME_DRAIN_REQUEST_CLEANUP_FAILED",
                        "simulated Windows sharing violation",
                    )
                original_remove(requests, missing_ok=missing_ok)

            with mock.patch.object(
                runtime_owner,
                "_remove_drain_requests",
                side_effect=fail_final_once,
            ):
                with self.assertRaises(RuntimeOwnerError):
                    drain.release_fence()

            self.assertTrue(failed_once)
            self.assertTrue(tuple(lease_root.glob("*.fence")))
            drain.release_fence()
            self.assertEqual((), tuple(lease_root.glob("*.request")))
            self.assertEqual((), tuple(lease_root.glob("*.fence")))

    def test_filesystem_drain_reclaims_expired_dead_api_presence(self) -> None:
        from backend.app.cli import runtime_owner
        from backend.app.cli.runtime_owner import FilesystemCandidateRuntimeDrain
        from backend.app.providers.runtime_lease import ApiRuntimePresence

        with p4_identity_fixture() as fixture:
            with subprocess.Popen([sys.executable, "-B", "-c", "pass"]) as child:
                exited_pid = child.pid
                child.wait(timeout=10)
            identity_type, _owner_type, _process_type, _snapshot_type = _api()
            identity_path = _candidate_identity(fixture, identity_type)
            lease_root = fixture.root / "leases"
            expired = ApiRuntimePresence(
                lease_root,
                clock=lambda: datetime(2000, 1, 1, tzinfo=timezone.utc),
            ).acquire(
                identity_path,
                environment="candidate",
                runtime_namespace="p4-dead-api",
                owner_id="dead-api",
                pid=exited_pid,
                lease_seconds=1,
            )
            drain = FilesystemCandidateRuntimeDrain(
                lease_root=lease_root,
                database_identity_manifest=identity_path,
                timeout_seconds=0.2,
            )
            try:
                with mock.patch.object(
                    runtime_owner,
                    "runtime_pid_is_alive",
                    return_value=False,
                ):
                    self.assertEqual(
                        ("api", "worker", "scheduler"),
                        drain.stop_and_wait(
                            database=fixture.candidate_database_path,
                            runtime_namespace="p4-dead-api",
                        ),
                    )
                self.assertFalse(expired.path.exists())
            finally:
                drain.release_fence()
                expired.release()

    def test_runtime_pid_probe_treats_indeterminate_errors_as_alive(self) -> None:
        from backend.app.providers import runtime_lease

        if os.name == "nt":
            import ctypes

            kernel32 = SimpleNamespace(
                OpenProcess=mock.Mock(return_value=0),
                GetExitCodeProcess=mock.Mock(),
                CloseHandle=mock.Mock(),
            )
            with (
                mock.patch.object(ctypes, "WinDLL", return_value=kernel32),
                mock.patch.object(ctypes, "get_last_error", return_value=5),
            ):
                self.assertTrue(runtime_lease.runtime_pid_is_alive(12345))
            with (
                mock.patch.object(ctypes, "WinDLL", return_value=kernel32),
                mock.patch.object(ctypes, "get_last_error", return_value=87),
            ):
                self.assertFalse(runtime_lease.runtime_pid_is_alive(12345))
        else:
            with mock.patch.object(
                runtime_lease.os,
                "kill",
                side_effect=PermissionError,
            ):
                self.assertTrue(runtime_lease.runtime_pid_is_alive(12345))
            with mock.patch.object(
                runtime_lease.os,
                "kill",
                side_effect=ProcessLookupError,
            ):
                self.assertFalse(runtime_lease.runtime_pid_is_alive(12345))

    @unittest.skipUnless(os.name == "nt", "Windows platform evidence contract")
    def test_windows_inspector_reports_database_handles_from_non_candidate_pids(
        self,
    ) -> None:
        from backend.app.providers import runtime_lease

        with tempfile.TemporaryDirectory(prefix="study-app-runtime-handles-") as raw:
            root = Path(raw)
            entrypoint = root / "server.js"
            database = root / "app.db"
            entrypoint.write_text("// fixture\n", encoding="utf-8")
            database.write_bytes(b"sqlite-fixture")
            metadata = {
                41001: (Path("node.exe"), root, ("node.exe", str(entrypoint))),
                41002: (Path("python.exe"), root, ("python.exe", "agent/mcp_server.py")),
            }
            observed_pids: list[frozenset[int]] = []

            def metadata_for(pid: int) -> tuple[Path, Path, tuple[str, ...]]:
                if pid not in metadata:
                    raise OSError("metadata unavailable")
                return metadata[pid]

            def database_users(
                _path: Path,
                pids: frozenset[int],
            ) -> frozenset[int]:
                observed_pids.append(pids)
                return frozenset({41002})

            with (
                patch.object(
                    runtime_lease,
                    "_windows_process_ids",
                    return_value=(41001, 41002, 41003),
                ),
                patch.object(
                    runtime_lease,
                    "_windows_process_metadata",
                    side_effect=metadata_for,
                ),
                patch.object(runtime_lease, "_windows_tcp_listeners", return_value={}),
                patch.object(
                    runtime_lease,
                    "_windows_processes_using_file",
                    side_effect=database_users,
                ),
            ):
                snapshot = runtime_lease.WindowsRuntimeInspector(
                    expected_entrypoint_path=entrypoint,
                    tracked_database_paths=(database,),
                ).snapshot()

            self.assertEqual([frozenset({41001, 41002, 41003})], observed_pids)
            self.assertEqual((41002,), snapshot.database_handle_pids)

    def test_filesystem_drain_failed_stop_retains_retryable_cleanup_state(
        self,
    ) -> None:
        from backend.app.cli import runtime_owner
        from backend.app.cli.runtime_owner import (
            FilesystemCandidateRuntimeDrain,
            RuntimeOwnerError,
        )
        from backend.app.providers.runtime_lease import RoleScopedRuntimeLease

        with p4_identity_fixture() as fixture:
            identity_type, _owner_type, _process_type, _snapshot_type = _api()
            identity_path = _candidate_identity(fixture, identity_type)
            lease_root = fixture.root / "leases"
            worker = RoleScopedRuntimeLease(
                lease_root,
                pid_probe=lambda _pid: True,
            ).acquire(
                identity_path,
                environment="candidate",
                runtime_namespace="p4-failed-stop",
                role="worker",
                owner_id="worker-timeout",
                pid=os.getpid(),
            )
            clock_values = iter((0.0, 1.0))
            drain = FilesystemCandidateRuntimeDrain(
                lease_root=lease_root,
                database_identity_manifest=identity_path,
                timeout_seconds=0.5,
                clock=lambda: next(clock_values),
            )
            original_remove = runtime_owner._remove_drain_requests
            failed_once = False

            def fail_final_once(
                requests: tuple[tuple[Path, bytes], ...],
                *,
                missing_ok: bool,
            ) -> None:
                nonlocal failed_once
                if (
                    requests
                    and requests[0][0].suffix == ".fence"
                    and not failed_once
                ):
                    failed_once = True
                    raise RuntimeOwnerError(
                        "CANDIDATE_RUNTIME_DRAIN_REQUEST_CLEANUP_FAILED",
                        "simulated failed-stop sharing violation",
                    )
                original_remove(requests, missing_ok=missing_ok)

            try:
                with mock.patch.object(
                    runtime_owner,
                    "_remove_drain_requests",
                    side_effect=fail_final_once,
                ):
                    with self.assertRaises(RuntimeOwnerError):
                        drain.stop_and_wait(
                            database=fixture.candidate_database_path,
                            runtime_namespace="p4-failed-stop",
                        )
                self.assertTrue(failed_once)
                self.assertTrue(tuple(lease_root.glob("*.fence")))
                drain.release_fence()
                self.assertEqual((), tuple(lease_root.glob("*.request")))
                self.assertEqual((), tuple(lease_root.glob("*.fence")))
            finally:
                worker.release()

    def test_candidate_api_binds_random_loopback_only(self) -> None:
        from backend.app.runtime import CandidateRuntimeGuard, RuntimeRoleError

        guard = CandidateRuntimeGuard()
        with guard.bind_loopback_socket() as listener:
            self.assertEqual("127.0.0.1", listener.getsockname()[0])
            self.assertGreater(listener.getsockname()[1], 0)
        for host in ("0.0.0.0", "::"):
            with self.subTest(host=host):
                with self.assertRaises(RuntimeRoleError):
                    guard.bind_loopback_socket(host=host)

    def test_candidate_guard_rejects_self_hashed_manifest_bound_to_live_subject(
        self,
    ) -> None:
        from backend.app.api.compat.database_identity import canonical_json_bytes
        from backend.app.config import DatabaseSettings
        from backend.app.runtime import CandidateRuntimeGuard, RuntimeRoleError

        with p4_identity_fixture() as fixture:
            identity_type, _owner_type, _process_type, _snapshot_type = _api()
            live_identity = _identity(fixture, identity_type)
            document = json.loads(live_identity.read_bytes())
            document["subjectKind"] = "p4_candidate"
            document["parentDatabaseIdentityManifestPath"] = str(live_identity)
            document["parentSubjectDatabaseId"] = document["subjectDatabaseId"]
            document["parentIdentityManifestFileSha256"] = sha256_file(live_identity)
            subject = {
                "version": 1,
                "databaseLineageId": document["databaseLineageId"],
                "subjectKind": document["subjectKind"],
                "resolvedPathHash": document["resolvedPathHash"],
                "platformFileIdentity": document["platformFileIdentity"],
                "parentBackupId": document["parentBackupId"],
                "parentManifestSha256": document["parentManifestSha256"],
            }
            document["subjectDatabaseId"] = hashlib.sha256(
                canonical_json_bytes(subject)
            ).hexdigest()
            unsigned = {
                key: value
                for key, value in document.items()
                if key != "identityManifestSha256"
            }
            document["identityManifestSha256"] = hashlib.sha256(
                canonical_json_bytes(unsigned)
            ).hexdigest()
            forged = fixture.root / "forged-live-candidate-identity.json"
            forged.write_bytes(canonical_json_bytes(document))

            with self.assertRaises(RuntimeRoleError) as caught:
                CandidateRuntimeGuard().validate_role(
                    forged,
                    database=DatabaseSettings(fixture.database_path),
                    environment="candidate",
                    runtime_namespace="p4-forged",
                    role="api",
                )
            self.assertEqual(
                "DATABASE_IDENTITY_DESCENDANT_LIVE_SUBJECT",
                caught.exception.code,
            )

    def test_candidate_runtime_requires_exact_parent_evidence_before_side_effects(
        self,
    ) -> None:
        from backend.app.cli.candidate_runtime import run

        with p4_identity_fixture() as fixture:
            identity_type, _owner_type, _process_type, _snapshot_type = _api()
            candidate_identity = _candidate_identity(fixture, identity_type)
            invalid_marker = fixture.root / "invalid-owner-marker.json"
            invalid_marker.write_text("{}\n", encoding="utf-8")
            stderr = StringIO()
            result = asyncio.run(
                run(
                    ["--role", "api"],
                    environment={
                        "API_PROCESS_ROLE": "api",
                        "RUNTIME_ENVIRONMENT": "candidate",
                        "RUNTIME_NAMESPACE": "p4-parent-evidence",
                        "DB_PATH": str(fixture.candidate_database_path),
                        "DATABASE_IDENTITY_MANIFEST": str(candidate_identity),
                        "PRODUCTION_OWNER_MARKER": str(invalid_marker),
                        "RUNTIME_LEASE_DIR": str(fixture.root / "leases"),
                        "REQUIRED_SCHEMA_REVISION": "20260807_03",
                    },
                    stderr=stderr,
                )
            )

            self.assertEqual(2, result)
            self.assertEqual(
                "CANDIDATE_PARENT_BACKUP_INVALID",
                json.loads(stderr.getvalue())["error"]["code"],
            )
            self.assertFalse((fixture.root / "leases").exists())

    def test_candidate_runtime_rejects_decoy_parent_not_bound_to_live_owner_subject(
        self,
    ) -> None:
        from backend.app.cli.candidate_runtime import run
        from backend.app.infrastructure.database_backup import create_verified_backup

        with p4_identity_fixture() as fixture:
            identity_type, owner_type, process_type, snapshot_type = _api()
            live_identity = _identity(fixture, identity_type)
            marker = fixture.root / "production-owner.json"
            owner_type(
                _Inspector(_snapshot(snapshot_type, (_process(fixture, process_type),)))
            ).initialize_node_owner(
                **_owner_arguments(fixture, live_identity, marker)
            )

            decoy_database = fixture.root / "decoy-live.db"
            shutil.copyfile(fixture.database_path, decoy_database)
            decoy_identity = fixture.root / "decoy-live-identity.json"
            identity_type().create_live_database_identity(
                database=decoy_database,
                p0_origin_receipt=fixture.receipt_path,
                expected_p0_origin_receipt_sha256=fixture.receipt_file_sha256,
                origin_backup=fixture.origin_backup_path,
                origin_manifest=fixture.origin_manifest_path,
                output=decoy_identity,
            )
            backup = create_verified_backup(
                decoy_database,
                fixture.root / "decoy-parent-backup",
                label="decoy-parent",
            )
            shutil.copyfile(backup.backup_path, fixture.candidate_database_path)
            candidate_identity = fixture.root / "decoy-candidate-identity.json"
            identity_type().create_descendant_database_identity(
                database=fixture.candidate_database_path,
                subject_kind="p4_candidate",
                parent_database_identity_manifest=decoy_identity,
                parent_backup=backup.backup_path,
                parent_manifest=backup.manifest_path,
                output=candidate_identity,
            )
            invalid_lease_root = fixture.root / "lease-root-is-a-file"
            invalid_lease_root.write_text("sentinel\n", encoding="utf-8")
            stderr = StringIO()

            result = asyncio.run(
                run(
                    ["--role", "api"],
                    environment={
                        "API_PROCESS_ROLE": "api",
                        "RUNTIME_ENVIRONMENT": "candidate",
                        "RUNTIME_NAMESPACE": "p4-decoy-parent",
                        "DB_PATH": str(fixture.candidate_database_path),
                        "DATABASE_IDENTITY_MANIFEST": str(candidate_identity),
                        "CANDIDATE_PARENT_BACKUP": str(backup.backup_path),
                        "CANDIDATE_PARENT_MANIFEST": str(backup.manifest_path),
                        "PRODUCTION_OWNER_MARKER": str(marker),
                        "RUNTIME_LEASE_DIR": str(invalid_lease_root),
                        "REQUIRED_SCHEMA_REVISION": "20260807_03",
                    },
                    stderr=stderr,
                )
            )

            self.assertEqual(2, result)
            self.assertEqual(
                "OWNER_MARKER_IDENTITY_MISMATCH",
                json.loads(stderr.getvalue())["error"]["code"],
            )
            self.assertEqual("sentinel\n", invalid_lease_root.read_text(encoding="utf-8"))

    def test_candidate_rollback_cli_uses_real_runtime_drain_before_starting_node(
        self,
    ) -> None:
        from backend.app.cli.runtime_owner import run
        from backend.app.providers.runtime_lease import (
            ApiRuntimePresence,
            RoleLeaseError,
            RoleScopedRuntimeLease,
        )

        with p4_identity_fixture() as fixture:
            identity_type, owner_type, process_type, snapshot_type = _api()
            candidate_identity = _candidate_identity(fixture, identity_type)
            live_identity = fixture.root / "live-database-identity-v1.json"
            marker = fixture.root / "production-owner.json"
            process = _process(fixture, process_type)
            inspector = _Inspector(_snapshot(snapshot_type, (process,)))
            owner_type(inspector).initialize_node_owner(
                **_owner_arguments(fixture, live_identity, marker)
            )

            lease_root = fixture.root / "candidate-leases"
            leases = RoleScopedRuntimeLease(lease_root, pid_probe=lambda _pid: True)
            api_presence = ApiRuntimePresence(lease_root)

            def acquire(role: str, owner_id: str):
                if role == "api":
                    return api_presence.acquire(
                        candidate_identity,
                        environment="candidate",
                        runtime_namespace="p4-rehearsal",
                        owner_id=owner_id,
                        pid=os.getpid(),
                    )
                return leases.acquire(
                    candidate_identity,
                    environment="candidate",
                    runtime_namespace="p4-rehearsal",
                    role=role,
                    owner_id=owner_id,
                    pid=os.getpid(),
                )

            handles = tuple(
                acquire(role, f"{role}-cli-drain")
                for role in ("api", "worker", "scheduler")
            )
            events: list[str] = []

            def release_after_drain_request() -> None:
                deadline = time.monotonic() + 2
                while time.monotonic() < deadline:
                    requests = tuple(lease_root.glob("*.request"))
                    if requests:
                        events.append("drain-requested")
                        for handle in handles:
                            handle.release()
                        return
                    time.sleep(0.01)

            watcher = threading.Thread(target=release_after_drain_request)
            watcher.start()
            test_case = self

            class Runner:
                def start(self, database: Path, profile: str) -> object:
                    test_case.assertEqual(fixture.candidate_database_path, database)
                    test_case.assertEqual("frozen-node", profile)
                    test_case.assertFalse(tuple(lease_root.glob("*.json")))
                    admission_codes: list[str] = []

                    def attempt_admission(role: str) -> None:
                        try:
                            unexpected = acquire(role, f"{role}-rollback-race")
                        except RoleLeaseError as error:
                            admission_codes.append(error.code)
                        else:
                            unexpected.release()
                            admission_codes.append("ADMITTED")

                    contenders = tuple(
                        threading.Thread(target=attempt_admission, args=(role,))
                        for role in ("api", "worker", "scheduler")
                    )
                    for contender in contenders:
                        contender.start()
                    for contender in contenders:
                        contender.join(timeout=2)
                    test_case.assertEqual(
                        ["CANDIDATE_RUNTIME_DRAINING"] * 3,
                        sorted(admission_codes),
                    )
                    events.append("node-started")
                    return object()

                def smoke(self, _handle: object) -> dict[str, object]:
                    return {
                        "paths": [
                            "/api/papers",
                            "/api/reviews",
                            "/pdfbytes",
                            "/workspace/",
                            "/legacy/",
                        ],
                        "loopback": True,
                    }

                def stop(self, _handle: object) -> None:
                    test_case.assertEqual(3, len(tuple(lease_root.glob("*.request"))))

            try:
                with mock.patch(
                    "backend.app.cli.runtime_owner._rollback_runtime_inspector",
                    return_value=inspector,
                ):
                    result = run(
                        [
                            "candidate-rollback-smoke",
                            "--database",
                            str(fixture.candidate_database_path),
                            "--database-identity-manifest",
                            str(candidate_identity),
                            "--candidate-runtime-namespace",
                            "p4-rehearsal",
                            "--owner-marker",
                            str(marker),
                            "--rollback-profile",
                            "frozen-node",
                            "--evidence-output",
                            str(fixture.root / "candidate-rollback-cli.json"),
                        ],
                        runner=Runner(),
                        lease_root=lease_root,
                    )
            finally:
                for handle in handles:
                    handle.release()
                watcher.join(timeout=3)

            self.assertEqual(["drain-requested", "node-started"], events)
            self.assertEqual(["api", "worker", "scheduler"], result["drainedRoles"])
            self.assertEqual((), tuple(lease_root.glob("*.request")))

    def test_candidate_rollback_smoke_isolated_and_preserves_full_inventory_and_live_owner(
        self,
    ) -> None:
        """The rollback rehearsal must be observable without touching the Live owner."""

        from backend.app.cli.runtime_owner import CandidateRollbackSmokeService
        from backend.app.providers.runtime_lease import RoleScopedRuntimeLease

        with p4_identity_fixture() as fixture:
            identity_type, owner_type, process_type, snapshot_type = _api()
            candidate_identity = _candidate_identity(fixture, identity_type)
            live_identity = fixture.root / "live-database-identity-v1.json"
            marker = fixture.root / "production-owner.json"
            process = _process(fixture, process_type)
            owner_type(_Inspector(_snapshot(snapshot_type, (process,)))).initialize_node_owner(
                **_owner_arguments(fixture, live_identity, marker)
            )
            marker_before = marker.read_bytes()
            rollback_process = replace(
                process,
                pid=process.pid + 100,
                listener_port=process.listener_port + 100,
                database_paths=(fixture.candidate_database_path,),
            )

            class RollbackInspector:
                calls = 0

                def snapshot(self) -> object:
                    self.calls += 1
                    nodes = (
                        (process, rollback_process)
                        if self.calls >= 4
                        else (process,)
                    )
                    return _snapshot(snapshot_type, nodes)

            rollback_inspector = RollbackInspector()

            lease_root = fixture.root / "candidate-leases"
            leases = RoleScopedRuntimeLease(lease_root, pid_probe=lambda _pid: True)
            worker = leases.acquire(
                candidate_identity,
                environment="candidate",
                runtime_namespace="p4-rehearsal",
                role="worker",
                owner_id="worker-rollback",
                pid=os.getpid(),
            )
            scheduler = leases.acquire(
                candidate_identity,
                environment="candidate",
                runtime_namespace="p4-rehearsal",
                role="scheduler",
                owner_id="scheduler-rollback",
                pid=os.getpid(),
            )

            events: list[str] = []
            drain_active = [False]
            test_case = self

            class Runner:
                def start(self, database: Path, profile: str) -> object:
                    test_case.assertTrue(drain_active[0])
                    events.append(f"start:{profile}:{database.name}")
                    return object()

                def smoke(self, _handle: object) -> dict[str, object]:
                    test_case.assertTrue(drain_active[0])
                    events.append("smoke")
                    return {
                        "paths": [
                            "/api/papers",
                            "/api/reviews",
                            "/pdfbytes",
                            "/workspace/",
                            "/legacy/",
                        ],
                        "loopback": True,
                    }

                def stop(self, _handle: object) -> None:
                    test_case.assertTrue(drain_active[0])
                    events.append("stop")

            class Drain:
                def stop_and_wait(
                    self,
                    *,
                    database: Path,
                    runtime_namespace: str,
                ) -> tuple[str, ...]:
                    self_database = database
                    self_namespace = runtime_namespace
                    events.extend(
                        (
                            "drain.api.stop",
                            "drain.api.wait_stopped",
                            "drain.worker.stop",
                            "drain.worker.wait_stopped",
                            "drain.scheduler.stop",
                            "drain.scheduler.wait_stopped",
                        )
                    )
                    test_case.assertEqual(fixture.candidate_database_path, self_database)
                    test_case.assertEqual("p4-rehearsal", self_namespace)
                    test_case.assertTrue(worker.path.is_file())
                    test_case.assertTrue(scheduler.path.is_file())
                    worker.release()
                    scheduler.release()
                    drain_active[0] = True
                    return ("api", "worker", "scheduler")

                def release_fence(self) -> None:
                    test_case.assertTrue(drain_active[0])
                    test_case.assertEqual("stop", events[-1])
                    drain_active[0] = False
                    events.append("drain.release")

            try:
                evidence = fixture.root / "candidate-rollback-smoke.json"
                result = CandidateRollbackSmokeService(
                    runner=Runner(),
                    lease_root=lease_root,
                    candidate_drain=Drain(),
                    runtime_inspector=rollback_inspector,
                ).run(
                    database=fixture.candidate_database_path,
                    database_identity_manifest=candidate_identity,
                    candidate_runtime_namespace="p4-rehearsal",
                    owner_marker=marker,
                    rollback_profile="frozen-node",
                    evidence_output=evidence,
                )
            finally:
                # Cleanup remains idempotent after the runtime controller releases them.
                worker.release()
                scheduler.release()

            self.assertTrue(result["ok"])
            self.assertEqual("candidateRollbackSmoke", result["operation"])
            self.assertEqual(
                ["api", "worker", "scheduler"],
                result["drainedRoles"],
            )
            self.assertEqual(
                [
                    "drain.api.stop",
                    "drain.api.wait_stopped",
                    "drain.worker.stop",
                    "drain.worker.wait_stopped",
                    "drain.scheduler.stop",
                    "drain.scheduler.wait_stopped",
                    "start:frozen-node:app.db",
                    "smoke",
                    "stop",
                    "drain.release",
                ],
                events,
            )
            self.assertFalse(drain_active[0])
            self.assertEqual(marker_before, marker.read_bytes())
            self.assertGreaterEqual(rollback_inspector.calls, 8)
            self.assertFalse(tuple(lease_root.glob("*.json")))
            self.assertTrue(evidence.is_file())

    def test_candidate_rollback_retries_cleanup_when_stop_fails(self) -> None:
        from backend.app.cli.runtime_owner import (
            CandidateRollbackSmokeService,
            RuntimeOwnerError,
        )

        with p4_identity_fixture() as fixture:
            identity_type, owner_type, process_type, snapshot_type = _api()
            candidate_identity = _candidate_identity(fixture, identity_type)
            live_identity = fixture.root / "live-database-identity-v1.json"
            marker = fixture.root / "production-owner.json"
            process = _process(fixture, process_type)
            snapshot = _snapshot(snapshot_type, (process,))
            owner_type(_Inspector(snapshot)).initialize_node_owner(
                **_owner_arguments(fixture, live_identity, marker)
            )
            marker_before = marker.read_bytes()

            class Runner:
                def start(self, _database: Path, _profile: str) -> object:
                    raise AssertionError("rollback runner must not start")

                def smoke(self, _handle: object) -> dict[str, object]:
                    raise AssertionError("rollback smoke must not run")

                def stop(self, _handle: object) -> None:
                    raise AssertionError("rollback runner must not stop")

            class Drain:
                stop_calls = 0
                release_calls = 0

                def stop_and_wait(
                    self,
                    *,
                    database: Path,
                    runtime_namespace: str,
                ) -> tuple[str, ...]:
                    self.stop_calls += 1
                    self.database = database
                    self.runtime_namespace = runtime_namespace
                    raise RuntimeOwnerError(
                        "CANDIDATE_RUNTIME_DRAIN_REQUEST_CLEANUP_FAILED",
                        "simulated first Windows cleanup failure",
                    )

                def release_fence(self) -> None:
                    self.release_calls += 1

            drain = Drain()
            evidence = fixture.root / "cleanup-retry-evidence.json"
            with self.assertRaises(RuntimeOwnerError) as raised:
                CandidateRollbackSmokeService(
                    runner=Runner(),
                    lease_root=fixture.root / "candidate-leases",
                    candidate_drain=drain,
                    runtime_inspector=_Inspector(snapshot),
                ).run(
                    database=fixture.candidate_database_path,
                    database_identity_manifest=candidate_identity,
                    candidate_runtime_namespace="p4-cleanup-retry",
                    owner_marker=marker,
                    rollback_profile="frozen-node",
                    evidence_output=evidence,
                )

            self.assertEqual(
                "CANDIDATE_RUNTIME_DRAIN_REQUEST_CLEANUP_FAILED",
                raised.exception.code,
            )
            self.assertEqual(1, drain.stop_calls)
            self.assertEqual(1, drain.release_calls)
            self.assertEqual(fixture.candidate_database_path, drain.database)
            self.assertEqual("p4-cleanup-retry", drain.runtime_namespace)
            self.assertFalse(evidence.exists())
            self.assertEqual(marker_before, marker.read_bytes())

    def test_candidate_rollback_fails_closed_on_live_runtime_drift(self) -> None:
        from backend.app.cli.runtime_owner import (
            CandidateRollbackSmokeService,
            RuntimeOwnerError,
        )

        with p4_identity_fixture() as fixture:
            identity_type, owner_type, process_type, snapshot_type = _api()
            candidate_identity = _candidate_identity(fixture, identity_type)
            live_identity = fixture.root / "live-database-identity-v1.json"
            marker = fixture.root / "production-owner.json"
            process = _process(fixture, process_type)
            stable = _snapshot(snapshot_type, (process,))
            owner_type(_Inspector(stable)).initialize_node_owner(
                **_owner_arguments(fixture, live_identity, marker)
            )
            marker_before = marker.read_bytes()
            live_python = replace(
                process,
                pid=process.pid + 20,
                process_role="worker",
                environment="live",
            )
            cases = {
                "pid": (
                    _snapshot(snapshot_type, (replace(process, pid=process.pid + 1),)),
                    "LIVE_OWNER_DRIFT",
                ),
                "listener": (
                    _snapshot(
                        snapshot_type,
                        (replace(process, listener_port=process.listener_port + 1),),
                    ),
                    "LIVE_OWNER_DRIFT",
                ),
                "database handle": (
                    _snapshot(snapshot_type, (replace(process, database_paths=()),)),
                    "LIVE_OWNER_DRIFT",
                ),
                "competing live database handle": (
                    _snapshot(
                        snapshot_type,
                        (
                            process,
                            replace(
                                process,
                                pid=process.pid + 2,
                                database_paths=(fixture.database_path,),
                            ),
                        ),
                    ),
                    "LIVE_OWNER_DRIFT",
                ),
                "live python role": (
                    _snapshot(snapshot_type, (process,), (live_python,)),
                    "LIVE_PYTHON_ROLE_PRESENT",
                ),
            }

            for label, (drifted, expected_code) in cases.items():
                with self.subTest(label=label):
                    events: list[str] = []

                    class Inspector:
                        calls = 0

                        def snapshot(self) -> object:
                            self.calls += 1
                            return drifted if self.calls >= 4 else stable

                    class Runner:
                        def start(self, _database: Path, _profile: str) -> object:
                            events.append("start")
                            return object()

                        def smoke(self, _handle: object) -> dict[str, object]:
                            events.append("smoke")
                            return {"paths": [], "loopback": True}

                        def stop(self, _handle: object) -> None:
                            events.append("stop")

                    output = fixture.root / f"drift-{label.replace(' ', '-')}.json"
                    with self.assertRaises(RuntimeOwnerError) as raised:
                        CandidateRollbackSmokeService(
                            runner=Runner(),
                            lease_root=fixture.root / "candidate-leases",
                            runtime_inspector=Inspector(),
                        ).run(
                            database=fixture.candidate_database_path,
                            database_identity_manifest=candidate_identity,
                            candidate_runtime_namespace="p4-rehearsal",
                            owner_marker=marker,
                            rollback_profile="frozen-node",
                            evidence_output=output,
                        )
                    self.assertEqual(expected_code, raised.exception.code)
                    self.assertEqual(["start", "stop"], events)
                    self.assertFalse(output.exists())
                    self.assertEqual(marker_before, marker.read_bytes())

    def test_frozen_node_rollback_runner_smokes_real_isolated_database(self) -> None:
        from backend.app.cli.runtime_owner import (
            CandidateRollbackSmokeService,
            FrozenNodeRollbackRunner,
        )

        with p4_identity_fixture() as fixture:
            identity_type, owner_type, process_type, snapshot_type = _api()
            candidate_identity = _candidate_identity(fixture, identity_type)
            live_identity = fixture.root / "live-database-identity-v1.json"
            marker = fixture.root / "production-owner.json"
            process = _process(fixture, process_type)
            owner_type(_Inspector(_snapshot(snapshot_type, (process,)))).initialize_node_owner(
                **_owner_arguments(fixture, live_identity, marker)
            )
            marker_before = marker.read_bytes()

            evidence = fixture.root / "real-candidate-rollback-smoke.json"
            result = CandidateRollbackSmokeService(
                runner=FrozenNodeRollbackRunner(
                    application_root=Path(__file__).resolve().parents[2]
                ),
                lease_root=fixture.root / "candidate-leases",
                runtime_inspector=_Inspector(_snapshot(snapshot_type, (process,))),
            ).run(
                database=fixture.candidate_database_path,
                database_identity_manifest=candidate_identity,
                candidate_runtime_namespace="p4-rehearsal",
                owner_marker=marker,
                rollback_profile="frozen-node",
                evidence_output=evidence,
            )

            self.assertTrue(result["ok"])
            self.assertEqual(
                [
                    "/api/papers",
                    "/api/reviews",
                    "/pdfbytes",
                    "/workspace/",
                    "/legacy/",
                ],
                result["smokePaths"],
            )
            self.assertEqual(marker_before, marker.read_bytes())
            self.assertTrue(evidence.is_file())

    def test_candidate_rollback_prevalidates_all_leases_before_drain(self) -> None:
        from backend.app.cli.runtime_owner import (
            CandidateRollbackSmokeService,
            RuntimeOwnerError,
        )
        from backend.app.providers.runtime_lease import RoleScopedRuntimeLease

        with p4_identity_fixture() as fixture:
            identity_type, owner_type, process_type, snapshot_type = _api()
            candidate_identity = _candidate_identity(fixture, identity_type)
            marker = fixture.root / "production-owner.json"
            process = _process(fixture, process_type)
            owner_type(_Inspector(_snapshot(snapshot_type, (process,)))).initialize_node_owner(
                **_owner_arguments(
                    fixture,
                    fixture.root / "live-database-identity-v1.json",
                    marker,
                )
            )
            lease_root = fixture.root / "candidate-leases"
            worker = RoleScopedRuntimeLease(
                lease_root,
                pid_probe=lambda _pid: True,
            ).acquire(
                candidate_identity,
                environment="candidate",
                runtime_namespace="p4-rehearsal",
                role="worker",
                owner_id="worker-rollback",
                pid=os.getpid(),
            )
            (lease_root / "zz-invalid.json").write_bytes(b"not-json")
            events: list[str] = []

            class Runner:
                def start(self, _database: Path, _profile: str) -> object:
                    events.append("start")
                    return object()

                def smoke(self, _handle: object) -> dict[str, object]:
                    events.append("smoke")
                    return {}

                def stop(self, _handle: object) -> None:
                    events.append("stop")

            try:
                with self.assertRaises(RuntimeOwnerError) as raised:
                    CandidateRollbackSmokeService(
                        runner=Runner(),
                        lease_root=lease_root,
                        runtime_inspector=_Inspector(
                            _snapshot(snapshot_type, (process,))
                        ),
                    ).run(
                        database=fixture.candidate_database_path,
                        database_identity_manifest=candidate_identity,
                        candidate_runtime_namespace="p4-rehearsal",
                        owner_marker=marker,
                        rollback_profile="frozen-node",
                        evidence_output=fixture.root / "must-not-exist.json",
                    )
                self.assertEqual("CANDIDATE_LEASE_INVALID", raised.exception.code)
                self.assertTrue(worker.path.is_file())
                self.assertEqual([], events)
                self.assertFalse((fixture.root / "must-not-exist.json").exists())
            finally:
                worker.release()

    def test_p4_refuses_live_python_roles_while_node_is_production_owner(self) -> None:
        from backend.app.runtime import CandidateRuntimeGuard, RuntimeRoleError

        with p4_identity_fixture() as fixture:
            identity_type, _owner_type, _process_type, _snapshot_type = _api()
            identity_path = _identity(fixture, identity_type)
            guard = CandidateRuntimeGuard()
            with self.assertRaisesRegex(RuntimeRoleError, "P4_LIVE_PROMOTION_NOT_AUTHORIZED"):
                guard.validate_role(
                    identity_path,
                    environment="live",
                    runtime_namespace="production",
                    role="worker",
                )
            self.assertFalse((fixture.root / "leases").exists())

    @unittest.skipUnless(os.name == "nt", "Windows platform evidence contract")
    def test_windows_inspector_attests_real_processes_listeners_and_database_handles(
        self,
    ) -> None:
        from backend.app.providers.runtime_lease import WindowsRuntimeInspector

        node = shutil.which("node.exe")
        self.assertIsNotNone(node)
        assert node is not None
        with p4_identity_fixture() as fixture:
            identity_type, owner_type, _process_type, _snapshot_type = _api()
            identity_path = _identity(fixture, identity_type)
            alternate_database = fixture.root / "alternate-runtime.db"
            shutil.copyfile(fixture.database_path, alternate_database)
            fixture.entrypoint_path.write_text(
                """const fs = require('fs');
const net = require('net');
const args = Object.fromEntries(process.argv.slice(2).reduce((rows, value, index, all) => {
  if (value.startsWith('--')) rows.push([value.slice(2), all[index + 1]]);
  return rows;
}, []));
const descriptor = fs.openSync(args.database, 'r');
const server = net.createServer(() => {});
server.listen(0, args.host, () => process.stdout.write('READY\\n'));
const stop = () => server.close(() => { fs.closeSync(descriptor); process.exit(0); });
process.on('SIGTERM', stop);
process.on('SIGINT', stop);
""",
                encoding="utf-8",
            )
            inspector = WindowsRuntimeInspector(
                expected_entrypoint_path=fixture.entrypoint_path,
                tracked_database_paths=(fixture.database_path, alternate_database),
            )
            service = owner_type(inspector)

            with self.assertRaises(Exception):
                service.initialize_node_owner(
                    **_owner_arguments(
                        fixture,
                        identity_path,
                        fixture.root / "zero-node-owner.json",
                    )
                )

            valid_arguments = [
                str(fixture.entrypoint_path),
                "--database",
                str(fixture.database_path),
                "--host",
                "127.0.0.1",
            ]
            with _real_runtime_process(
                node,
                valid_arguments,
                cwd=fixture.entrypoint_path.parent,
            ) as first:
                snapshot = inspector.snapshot()
                self.assertEqual((first.pid,), tuple(item.pid for item in snapshot.node_processes))
                self.assertEqual(
                    (fixture.database_path,),
                    snapshot.node_processes[0].database_paths,
                )
                service.initialize_node_owner(
                    **_owner_arguments(
                        fixture,
                        identity_path,
                        fixture.root / "valid-real-owner.json",
                    )
                )
                with _real_runtime_process(
                    node,
                    valid_arguments,
                    cwd=fixture.entrypoint_path.parent,
                ):
                    with self.assertRaises(Exception):
                        service.initialize_node_owner(
                            **_owner_arguments(
                                fixture,
                                identity_path,
                                fixture.root / "two-node-owner.json",
                            )
                        )

            with _real_runtime_process(
                node,
                [
                    str(fixture.entrypoint_path),
                    "--database",
                    str(fixture.database_path),
                    "--host",
                    "0.0.0.0",
                ],
                cwd=fixture.entrypoint_path.parent,
            ):
                with self.assertRaises(Exception):
                    service.initialize_node_owner(
                        **_owner_arguments(
                            fixture,
                            identity_path,
                            fixture.root / "non-loopback-owner.json",
                        )
                    )

            with _real_runtime_process(
                node,
                [
                    str(fixture.entrypoint_path),
                    "--database",
                    str(alternate_database),
                    "--host",
                    "127.0.0.1",
                ],
                cwd=fixture.entrypoint_path.parent,
            ):
                with self.assertRaises(Exception):
                    service.initialize_node_owner(
                        **_owner_arguments(
                            fixture,
                            identity_path,
                            fixture.root / "wrong-database-owner.json",
                        )
                    )

            with _real_runtime_process(
                node,
                valid_arguments,
                cwd=fixture.entrypoint_path.parent,
            ):
                with _real_runtime_process(
                    sys.executable,
                    [
                        "-c",
                        "import sys,time; print('READY', flush=True); time.sleep(60)",
                        "--study-app-role",
                        "worker",
                        "--study-app-environment",
                        "live",
                    ],
                    cwd=fixture.entrypoint_path.parent,
                ):
                    with self.assertRaises(Exception):
                        service.initialize_node_owner(
                            **_owner_arguments(
                                fixture,
                                identity_path,
                                fixture.root / "live-python-owner.json",
                            )
                        )

    def test_exact_live_identity_without_marker_resumes_after_read_only_verification(
        self,
    ) -> None:
        from backend.app.api.compat.database_identity import read_platform_file_identity

        with p4_identity_fixture() as fixture:
            identity_type, owner_type, process_type, snapshot_type = _api()
            identity_path = _identity(fixture, identity_type)
            identity_before = (
                identity_path.read_bytes(),
                sha256_file(identity_path),
                read_platform_file_identity(identity_path),
                identity_path.stat().st_mtime_ns,
            )
            process = _process(fixture, process_type)
            service = owner_type(_Inspector(_snapshot(snapshot_type, (process,))))
            marker = fixture.root / "production-owner.json"
            arguments = _owner_arguments(fixture, identity_path, marker)

            with mock.patch(
                "backend.app.cli.runtime_owner.exclusive_write_bytes",
                side_effect=RuntimeError("injected crash before marker publication"),
            ):
                with self.assertRaisesRegex(RuntimeError, "injected crash"):
                    service.initialize_node_owner(**arguments)
            self.assertTrue(identity_path.is_file())
            self.assertFalse(marker.exists())
            self.assertEqual(
                identity_before,
                (
                    identity_path.read_bytes(),
                    sha256_file(identity_path),
                    read_platform_file_identity(identity_path),
                    identity_path.stat().st_mtime_ns,
                ),
            )

            from backend.app.api.compat.database_identity import LiveDatabaseIdentityVerifier

            verifier = LiveDatabaseIdentityVerifier()
            verified = verifier.verify_existing(
                database=fixture.database_path,
                database_identity_manifest=identity_path,
                p0_origin_receipt=fixture.receipt_path,
                expected_p0_origin_receipt_sha256=fixture.receipt_file_sha256,
                origin_backup=fixture.origin_backup_path,
                origin_manifest=fixture.origin_manifest_path,
            )
            self.assertEqual("read_only", verified.verification_mode)
            self.assertEqual("live", verified.subject_kind)
            self.assertEqual(sha256_file(identity_path), verified.identity_manifest_file_sha256)
            service.initialize_node_owner(**arguments)
            self.assertTrue(marker.is_file())
            self.assertEqual(
                identity_before,
                (
                    identity_path.read_bytes(),
                    sha256_file(identity_path),
                    read_platform_file_identity(identity_path),
                    identity_path.stat().st_mtime_ns,
                ),
            )

            noncanonical = fixture.root / "noncanonical-identity.json"
            noncanonical.write_bytes(identity_before[0] + b" ")
            with self.assertRaises(Exception):
                verifier.verify_existing(
                    database=fixture.database_path,
                    database_identity_manifest=noncanonical,
                    p0_origin_receipt=fixture.receipt_path,
                    expected_p0_origin_receipt_sha256=fixture.receipt_file_sha256,
                    origin_backup=fixture.origin_backup_path,
                    origin_manifest=fixture.origin_manifest_path,
                )
            with self.assertRaises(Exception):
                verifier.verify_existing(
                    database=fixture.alternate_backup_path,
                    database_identity_manifest=identity_path,
                    p0_origin_receipt=fixture.receipt_path,
                    expected_p0_origin_receipt_sha256=fixture.receipt_file_sha256,
                    origin_backup=fixture.origin_backup_path,
                    origin_manifest=fixture.origin_manifest_path,
                )
            self.assertEqual(identity_before[0], identity_path.read_bytes())

    def test_initialize_and_verify_owner_require_same_p0_receipt_sha(self) -> None:
        with p4_identity_fixture() as fixture:
            identity_type, owner_type, process_type, snapshot_type = _api()
            identity_path = _identity(fixture, identity_type)
            process = _process(fixture, process_type)
            inspector = _Inspector(_snapshot(snapshot_type, (process,)))
            service = owner_type(inspector, clock=lambda: datetime(2026, 8, 14, tzinfo=timezone.utc))
            marker = fixture.root / "production-owner.json"
            arguments = _owner_arguments(fixture, identity_path, marker)

            wrong = {**arguments, "expected_p0_origin_receipt_sha256": "0" * 64}
            with self.assertRaises(Exception):
                service.initialize_node_owner(**wrong)
            self.assertFalse(marker.exists())
            service.initialize_node_owner(**arguments)
            before = marker.read_bytes()
            with self.assertRaises(Exception):
                service.verify_node_owner(**wrong)
            self.assertEqual(before, marker.read_bytes())
            verified = service.verify_node_owner(**arguments)
            self.assertEqual("read_only", verified.verification_mode)

    def test_initialize_node_owner_exclusive_creates_attested_node_active_marker(self) -> None:
        with p4_identity_fixture() as fixture:
            identity_type, owner_type, process_type, snapshot_type = _api()
            identity_path = _identity(fixture, identity_type)
            process = _process(fixture, process_type)
            service = owner_type(
                _Inspector(_snapshot(snapshot_type, (process,))),
                clock=lambda: datetime(2026, 8, 14, tzinfo=timezone.utc),
            )
            marker = fixture.root / "production-owner.json"
            arguments = _owner_arguments(fixture, identity_path, marker)
            created = service.initialize_node_owner(**arguments)
            self.assertEqual("node_active", created.owner_state)
            self.assertEqual(process.pid, created.process_id)
            self.assertEqual(marker, created.owner_marker_path)
            self.assertEqual(marker.read_bytes(), created.canonical_bytes)
            with self.assertRaisesRegex(Exception, "exists|already|exclusive"):
                service.initialize_node_owner(**arguments)
            self.assertEqual("read_only", service.verify_node_owner(**arguments).verification_mode)

    def test_initialize_node_owner_rejects_missing_multiple_python_live_or_existing_marker(self) -> None:
        with p4_identity_fixture() as fixture:
            identity_type, owner_type, process_type, snapshot_type = _api()
            identity_path = _identity(fixture, identity_type)
            node = _process(fixture, process_type)
            marker = fixture.root / "production-owner.json"
            arguments = _owner_arguments(fixture, identity_path, marker)
            snapshots = (
                _snapshot(snapshot_type, ()),
                _snapshot(snapshot_type, (node, replace(node, pid=node.pid + 1))),
                _snapshot(
                    snapshot_type,
                    (node,),
                    (replace(node, pid=node.pid + 2, process_role="worker", environment="live"),),
                ),
            )
            for snapshot in snapshots:
                with self.subTest(snapshot=snapshot):
                    service = owner_type(_Inspector(snapshot))
                    with self.assertRaises(Exception):
                        service.initialize_node_owner(**arguments)
                    self.assertFalse(marker.exists())

            marker.write_bytes(b"pre-existing marker")
            before = marker.read_bytes()
            service = owner_type(_Inspector(_snapshot(snapshot_type, (node,))))
            with self.assertRaises(Exception):
                service.initialize_node_owner(**arguments)
            self.assertEqual(before, marker.read_bytes())

    def test_verify_node_owner_is_read_only_and_rejects_marker_origin_or_process_drift(self) -> None:
        with p4_identity_fixture() as fixture:
            identity_type, owner_type, process_type, snapshot_type = _api()
            identity_path = _identity(fixture, identity_type)
            node = _process(fixture, process_type)
            inspector = _Inspector(_snapshot(snapshot_type, (node,)))
            service = owner_type(inspector)
            marker = fixture.root / "production-owner.json"
            arguments = _owner_arguments(fixture, identity_path, marker)
            service.initialize_node_owner(**arguments)
            before = (marker.read_bytes(), sha256_file(marker), marker.stat().st_mtime_ns)
            service.verify_node_owner(**arguments)
            self.assertEqual(before, (marker.read_bytes(), sha256_file(marker), marker.stat().st_mtime_ns))

            inspector.snapshot_value = _snapshot(
                snapshot_type,
                (replace(node, listener_port=node.listener_port + 1),),
            )
            with self.assertRaises(Exception):
                service.verify_node_owner(**arguments)
            self.assertEqual(before, (marker.read_bytes(), sha256_file(marker), marker.stat().st_mtime_ns))

            inspector.snapshot_value = _snapshot(snapshot_type, (node,))
            marker.write_bytes(marker.read_bytes() + b" ")
            tampered = marker.read_bytes()
            with self.assertRaises(Exception):
                service.verify_node_owner(**arguments)
            self.assertEqual(tampered, marker.read_bytes())

    def test_stale_node_owner_reattestation_requires_dead_old_pid_and_exact_replacement(self) -> None:
        with p4_identity_fixture() as fixture:
            identity_type, owner_type, process_type, snapshot_type = _api()
            identity_path = _identity(fixture, identity_type)
            old_process = _process(fixture, process_type, pid=41001)
            inspector = _Inspector(_snapshot(snapshot_type, (old_process,)))
            marker = fixture.root / "production-owner.json"
            arguments = _owner_arguments(fixture, identity_path, marker)
            owner_type(inspector).initialize_node_owner(**arguments)
            old_payload = marker.read_bytes()

            replacement = replace(old_process, pid=41002)
            inspector.snapshot_value = _snapshot(snapshot_type, (replacement,))
            service = owner_type(
                inspector,
                pid_probe=lambda pid: pid == replacement.pid,
            )
            report = service.reattest_stale_node_owner(**arguments)

            self.assertEqual(replacement.pid, report.process_id)
            self.assertEqual("stale_owner_reattested", report.verification_mode)
            self.assertNotEqual(old_payload, marker.read_bytes())
            self.assertEqual(
                replacement.pid,
                json.loads(marker.read_text(encoding="utf-8"))["processId"],
            )

            before_alive_rejection = marker.read_bytes()
            with self.assertRaises(Exception):
                owner_type(
                    inspector,
                    pid_probe=lambda _pid: True,
                ).reattest_stale_node_owner(**arguments)
            self.assertEqual(before_alive_rejection, marker.read_bytes())

            competing = replace(replacement, pid=41003)
            samples = [
                _snapshot(snapshot_type, (replacement,)),
                _snapshot(snapshot_type, (competing,)),
            ]

            class RacingInspector:
                def snapshot(self):
                    return samples.pop(0)

            before_race = marker.read_bytes()
            with self.assertRaises(Exception):
                owner_type(
                    RacingInspector(),
                    pid_probe=lambda pid: pid != replacement.pid,
                ).reattest_stale_node_owner(**arguments)
            self.assertEqual(before_race, marker.read_bytes())

    def test_owner_rejects_same_basename_from_different_directory(self) -> None:
        with p4_identity_fixture() as fixture:
            identity_type, owner_type, process_type, snapshot_type = _api()
            identity_path = _identity(fixture, identity_type)
            wrong = _process(
                fixture,
                process_type,
                entrypoint_path=fixture.same_name_entrypoint_path,
                cwd=fixture.same_name_entrypoint_path.parent,
                argv=("node", str(fixture.same_name_entrypoint_path)),
            )
            marker = fixture.root / "production-owner.json"
            service = owner_type(_Inspector(_snapshot(snapshot_type, (wrong,))))
            with self.assertRaises(Exception):
                service.initialize_node_owner(
                    **_owner_arguments(fixture, identity_path, marker)
                )
            self.assertFalse(marker.exists())


if __name__ == "__main__":
    unittest.main()
