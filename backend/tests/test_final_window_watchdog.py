from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from backend.app.api.compat.database_identity import canonical_json_bytes


def watchdog_operations_factory() -> object:
    events_path = Path(os.environ["P6_WATCHDOG_TEST_EVENTS"])

    class Operations:
        def _record(self, event: str) -> None:
            with events_path.open("a", encoding="utf-8") as handle:
                handle.write(event + "\n")

        def clear_authorization(self) -> None:
            self._record("authorization_cleared")

        def drain_python_ingress(self) -> None:
            self._record("python_ingress_drained")

        def drain_worker_claims(self) -> None:
            self._record("worker_claims_drained")

        def stop_scheduler_obsidian_mcp(self) -> None:
            self._record("scheduler_obsidian_mcp_stopped")

        def stop_fastapi(self) -> None:
            self._record("fastapi_stopped")

        def release_locks_connections(self) -> None:
            self._record("role_locks_connections_released")

        def start_frozen_node(self, _rollback_map: object) -> object:
            self._record("frozen_node_started")
            return object()

        def smoke_legacy(self, _handle: object) -> dict[str, object]:
            self._record("legacy_smoked")
            return {"ok": True}

    return Operations()


class FinalWindowWatchdogTests(unittest.TestCase):
    @unittest.skipUnless(os.name == "nt", "Windows replace semantics only")
    def test_atomic_replace_retries_transient_windows_access_denied(self) -> None:
        from backend.app.application import final_window

        with tempfile.TemporaryDirectory() as raw:
            target = Path(raw) / "lease.json"
            target.write_bytes(b"old")
            real_replace = os.replace
            calls = 0

            def replace(source: Path, destination: Path) -> None:
                nonlocal calls
                calls += 1
                if calls == 1:
                    error = PermissionError("transient Windows replace denial")
                    error.winerror = 5
                    raise error
                real_replace(source, destination)

            with (
                patch.object(final_window.os, "replace", side_effect=replace),
                patch.object(final_window.time, "sleep") as sleep,
            ):
                final_window._atomic_replace(target, b"new")

            self.assertEqual(b"new", target.read_bytes())
            self.assertEqual(2, calls)
            sleep.assert_called_once()

    @unittest.skipUnless(os.name == "nt", "Windows replace semantics only")
    def test_atomic_replace_fails_closed_after_persistent_windows_denial(self) -> None:
        from backend.app.application import final_window

        with tempfile.TemporaryDirectory() as raw:
            target = Path(raw) / "lease.json"
            target.write_bytes(b"old")
            error = PermissionError("persistent Windows replace denial")
            error.winerror = 5
            with (
                patch.object(final_window.os, "replace", side_effect=error),
                patch.object(final_window.time, "sleep") as sleep,
            ):
                with self.assertRaises(PermissionError):
                    final_window._atomic_replace(target, b"new")

            self.assertEqual(b"old", target.read_bytes())
            self.assertGreater(sleep.call_count, 0)

    def test_independent_process_aborts_for_every_unowned_window_condition(self) -> None:
        now = datetime.now(timezone.utc)
        missing_pid = 1_073_741_823
        cases = (
            ("coordinator_exit", missing_pid, os.getpid(), 60, None),
            ("operator_exit", os.getpid(), missing_pid, 60, None),
            ("heartbeat_timeout", os.getpid(), os.getpid(), -60, None),
            ("authorization_unused", os.getpid(), missing_pid, 60, 60),
            ("authorization_expired", os.getpid(), os.getpid(), 60, -60),
        )
        for expected_reason, coordinator_pid, operator_pid, heartbeat_delta, auth_delta in cases:
            with self.subTest(reason=expected_reason), tempfile.TemporaryDirectory() as raw:
                root = Path(raw).resolve()
                lease, token, recovery, owner, original_owner = _write_window(
                    root,
                    now=now,
                    coordinator_pid=coordinator_pid,
                    operator_pid=operator_pid,
                    heartbeat_delta=heartbeat_delta,
                    authorization_delta=auth_delta,
                )
                events = root / "events.log"
                environment = os.environ.copy()
                environment["PYTHONDONTWRITEBYTECODE"] = "1"
                environment["P6_WATCHDOG_TEST_EVENTS"] = str(events)
                completed = subprocess.run(
                    (
                        sys.executable,
                        "-B",
                        "-m",
                        "backend.app.cli.final_window_watchdog",
                        "--cutover-lease",
                        str(lease),
                        "--cutover-token-file",
                        str(token),
                        "--recovery-output",
                        str(recovery),
                        "--operations-factory",
                        "backend.tests.test_final_window_watchdog:watchdog_operations_factory",
                        "--once",
                    ),
                    cwd=Path(__file__).resolve().parents[2],
                    env=environment,
                    capture_output=True,
                    text=True,
                    timeout=15,
                    check=False,
                )
                self.assertEqual(0, completed.returncode, completed.stderr)
                result = json.loads(completed.stdout)
                self.assertEqual("aborted", result["action"])
                self.assertEqual(expected_reason, result["reasonCode"])
                self.assertTrue(recovery.is_file())
                self.assertEqual(original_owner, owner.read_bytes())
                self.assertEqual("recovered", json.loads(lease.read_text())["phase"])
                self.assertEqual(
                    "legacy_smoked",
                    events.read_text(encoding="utf-8").splitlines()[-1],
                )

    def test_concurrent_heartbeats_are_locked_versioned_and_hash_chained(self) -> None:
        from backend.app.application.final_window import (
            FinalWindowError,
            heartbeat_cutover_lease,
        )

        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw).resolve()
            lease, token, _recovery, _owner, _original_owner = _write_window(
                root,
                now=datetime.now(timezone.utc),
                coordinator_pid=os.getpid(),
                operator_pid=os.getpid(),
                heartbeat_delta=60,
                authorization_delta=None,
            )
            barrier = root / "go"
            script = (
                "import json,sys,time; from pathlib import Path; "
                "from backend.app.application.final_window import heartbeat_cutover_lease; "
                "barrier=Path(sys.argv[3]); "
                "\nwhile not barrier.exists(): time.sleep(0.001)\n"
                "r=heartbeat_cutover_lease(cutover_lease=sys.argv[1],"
                "cutover_token_file=sys.argv[2],heartbeat_timeout_seconds=60); "
                "print(json.dumps({'version':r.version,'sha':r.file_sha256}))"
            )
            environment = os.environ.copy()
            environment["PYTHONDONTWRITEBYTECODE"] = "1"
            children: list[subprocess.Popen[str]] = []
            try:
                for _ in range(4):
                    children.append(
                        subprocess.Popen(
                            (sys.executable, "-B", "-c", script, str(lease), str(token), str(barrier)),
                            cwd=Path(__file__).resolve().parents[2],
                            env=environment,
                            stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE,
                            text=True,
                        )
                    )
                barrier.write_text("go", encoding="ascii")
                outputs = [child.communicate(timeout=15) for child in children]
            finally:
                for child in children:
                    if child.poll() is None:
                        child.terminate()
                        child.wait(timeout=5)
            for child, (_stdout, stderr) in zip(children, outputs, strict=True):
                self.assertEqual(0, child.returncode, stderr)
            results = [json.loads(stdout) for stdout, _stderr in outputs]
            self.assertEqual([2, 3, 4, 5], sorted(result["version"] for result in results))
            document = json.loads(lease.read_text(encoding="utf-8"))
            self.assertEqual(5, document["version"])
            unsigned = {key: value for key, value in document.items() if key != "leaseSha256"}
            self.assertEqual(
                hashlib.sha256(canonical_json_bytes(unsigned)).hexdigest(),
                document["leaseSha256"],
            )
            version_four_sha = next(result["sha"] for result in results if result["version"] == 4)
            self.assertEqual(version_four_sha, document["previousLeaseFileSha256"])
            with self.assertRaises(FinalWindowError) as stale:
                heartbeat_cutover_lease(
                    cutover_lease=lease,
                    cutover_token_file=token,
                    heartbeat_timeout_seconds=60,
                    expected_version=1,
                )
            self.assertEqual("CUTOVER_LEASE_CAS_FAILED", stale.exception.code)

    def test_token_file_permissions_are_owner_only_and_fail_closed(self) -> None:
        from backend.app.application.final_window import (
            FinalWindowError,
            create_owner_only_token_file,
            verify_owner_only_token_file,
        )

        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw).resolve()
            token = root / "window.token"
            create_owner_only_token_file(token, b"t" * 32)
            verify_owner_only_token_file(token)
            if os.name != "nt":
                self.assertEqual(0o600, stat.S_IMODE(token.stat().st_mode))

            failed = root / "failed.token"
            with patch(
                "backend.app.application.final_window._apply_owner_only_permissions",
                side_effect=OSError("permission hardening unavailable"),
            ):
                with self.assertRaises(FinalWindowError) as denied:
                    create_owner_only_token_file(failed, b"x" * 32)
            self.assertEqual("CUTOVER_TOKEN_PERMISSIONS_INVALID", denied.exception.code)
            self.assertFalse(failed.exists())

    @unittest.skipUnless(os.name == "nt", "Windows PID semantics only")
    def test_windows_pid_probe_handles_live_and_exited_processes(self) -> None:
        from backend.app.application.final_window import _pid_is_alive

        self.assertTrue(_pid_is_alive(os.getpid()))
        with subprocess.Popen([sys.executable, "-B", "-c", "pass"]) as child:
            exited_pid = child.pid
            child.wait(timeout=10)
        self.assertFalse(_pid_is_alive(exited_pid))


def _write_window(
    root: Path,
    *,
    now: datetime,
    coordinator_pid: int,
    operator_pid: int,
    heartbeat_delta: int,
    authorization_delta: int | None,
) -> tuple[Path, Path, Path, Path, bytes]:
    run_id = "a" * 32
    run_root = root / f"run-{run_id}"
    run_root.mkdir()
    run_manifest = run_root / "evidence-run-manifest-v1.json"
    run_manifest.write_bytes(b"{}")
    startup = run_root / "production-startup-snapshot-v1.json"
    startup.write_bytes(b"{}")
    build = root / "build.json"
    build.write_bytes(b"{}")
    database_identity = root / "database.json"
    database_identity.write_bytes(b"{}")
    origin = root / "origin.json"
    origin.write_bytes(b"{}")
    database = root / "app.db"
    database.write_bytes(b"db")
    entrypoint = root / "server.js"
    entrypoint.write_bytes(b"server")
    token = root / f"final-window-{run_id}.token"
    from backend.app.application.final_window import create_owner_only_token_file

    create_owner_only_token_file(token, b"z" * 32)
    original_owner = canonical_json_bytes({"ownerState": "node_active"})
    owner = root / "production-owner.json"
    owner.write_bytes(canonical_json_bytes({"ownerState": "node_quiesced"}))
    lease = root / f"final-window-{run_id}.json"
    from backend.tests.test_production_rollback import _rollback_map

    rollback_map = _rollback_map(root=root, database=database)
    unsigned: dict[str, object] = {
        "schemaVersion": 1,
        "leaseKind": "final-window",
        "runId": run_id,
        "runManifestPath": str(run_manifest),
        "runManifestFileSha256": "1" * 64,
        "startupSnapshotPath": str(startup),
        "startupSnapshotFileSha256": "2" * 64,
        "buildIdentityManifestPath": str(build),
        "buildIdentityManifestSha256": "3" * 64,
        "buildId": "4" * 64,
        "databaseIdentityManifestPath": str(database_identity),
        "databaseIdentityManifestSha256": "5" * 64,
        "databaseLineageId": "6" * 64,
        "liveSubjectDatabaseId": "7" * 64,
        "originReceiptPath": str(origin),
        "originReceiptFileSha256": "8" * 64,
        "ownerMarkerPath": str(owner),
        "cutoverLeasePath": str(lease),
        "nodeActiveOwnerFileSha256": hashlib.sha256(original_owner).hexdigest(),
        "nodeActiveOwnerPayloadBase64": __import__("base64").b64encode(original_owner).decode("ascii"),
        "cutoverTokenFilePath": str(token),
        "cutoverTokenSha256": hashlib.sha256(token.read_bytes()).hexdigest(),
        "runtimeNamespace": "production",
        "frozenNodeRollbackMap": rollback_map,
        "frozenNodeRollbackMapSha256": hashlib.sha256(canonical_json_bytes(rollback_map)).hexdigest(),
        "coordinatorPid": coordinator_pid,
        "operatorPid": operator_pid,
        "watchdogPid": 99,
        "heartbeatDeadline": _timestamp(now + timedelta(seconds=heartbeat_delta)),
        "phase": "node_quiesced",
        "version": 1,
        "previousLeaseFileSha256": "0" * 64,
        "updatedAt": _timestamp(now),
    }
    lease_payload = _self_hashed(unsigned, "leaseSha256")
    lease.write_bytes(lease_payload)
    if authorization_delta is not None:
        issued = now - timedelta(seconds=120)
        expires = now + timedelta(seconds=authorization_delta)
        authorization_unsigned = {
            "schemaVersion": 1,
            "manifestKind": "promotion-authorization",
            "runId": run_id,
            "finalEvidenceRunManifestPath": str(run_manifest),
            "finalEvidenceRunManifestSha256": "1" * 64,
            "startupSnapshotPath": str(startup),
            "startupSnapshotSha256": "2" * 64,
            "cutoverLeasePath": str(lease),
            "cutoverLeaseSha256": hashlib.sha256(lease_payload).hexdigest(),
            "issuedAt": _timestamp(issued),
            "expiresAt": _timestamp(expires),
        }
        (run_root / "promotion-authorization.json").write_bytes(
            _self_hashed(authorization_unsigned, "authorizationSha256")
        )
    return lease, token, run_root / "abort-recovery.json", owner, original_owner


def _self_hashed(unsigned: dict[str, object], field: str) -> bytes:
    return canonical_json_bytes(
        {**unsigned, field: hashlib.sha256(canonical_json_bytes(unsigned)).hexdigest()}
    )


def _timestamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


if __name__ == "__main__":
    unittest.main()
