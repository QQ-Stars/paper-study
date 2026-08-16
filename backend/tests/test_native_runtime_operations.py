from __future__ import annotations

from pathlib import Path
import asyncio
from io import StringIO
import hashlib
import json
from datetime import datetime, timezone
import os
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch


def _git(root: Path, *arguments: str) -> None:
    completed = subprocess.run(
        ["git", "-C", str(root), *arguments],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if completed.returncode != 0:
        raise AssertionError(completed.stderr or completed.stdout)


class _Child:
    def __init__(self, pid: int) -> None:
        self.pid = pid
        self.terminated = False

    def poll(self) -> int | None:
        return 0 if self.terminated else None

    def terminate(self) -> None:
        self.terminated = True

    def wait(self, timeout: float | None = None) -> int:
        del timeout
        self.terminated = True
        return 0


class _Launcher:
    def __init__(self, *, fail_role: str | None = None) -> None:
        self.fail_role = fail_role
        self.requests: list[object] = []
        self.children: list[_Child] = []

    def start(self, request: object) -> _Child:
        self.requests.append(request)
        if request.role == self.fail_role:
            raise RuntimeError(f"injected {request.role} start failure")
        child = _Child(41000 + len(self.children))
        self.children.append(child)
        return child


def _available_loopback_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _recorded_native_role_identities(
    state_path: Path,
) -> tuple[tuple[int, tuple[str, ...], Path], ...]:
    """Read the identities the native runtime durably recorded for its roles."""
    document = json.loads(state_path.read_text(encoding="utf-8"))
    return tuple(
        (int(row["pid"]), tuple(row["argv"]), Path(str(row["cwd"])))
        for row in document["processes"]
    )


def _live_native_role_processes(
    identities: tuple[tuple[int, tuple[str, ...], Path], ...],
) -> tuple[tuple[int, tuple[str, ...]], ...]:
    """Report live processes carrying a recorded native role identity.

    Windows reuses a process identifier the moment its process exits, so a bare
    PID probe cannot prove a role stopped: it reports whichever unrelated
    process inherited the number. Executable, cwd, and exact argv can, and they
    stay scoped to the roles this test started because the recorded cwd is the
    fixture repository.
    """
    from backend.app.providers.runtime_lease import (
        _windows_process_ids,
        _windows_process_metadata,
    )

    expected = {
        (
            Path(argv[0]).expanduser().resolve(strict=False),
            cwd.expanduser().resolve(strict=False),
            tuple(argv[1:]),
        )
        for _pid, argv, cwd in identities
    }
    survivors: list[tuple[int, tuple[str, ...]]] = []
    for pid in _windows_process_ids():
        try:
            _executable, cwd, argv = _windows_process_metadata(pid)
            program = Path(argv[0]).expanduser().resolve(strict=False)
        except (OSError, RuntimeError, ValueError):
            continue
        if (program, cwd, tuple(argv[1:])) in expected:
            survivors.append((pid, argv))
    return tuple(survivors)


class NativeRuntimeOperationsTests(unittest.TestCase):
    def test_recovered_frozen_node_owner_uses_the_started_process_pid(self) -> None:
        from backend.app.api.compat.database_identity import canonical_json_bytes
        from backend.app.providers import native_runtime

        rollback_environment = {
            "RUNTIME_ENVIRONMENT": "live",
            "RUNTIME_NAMESPACE": "production",
            "API_BACKEND_MODE": "legacy",
            "DOCUMENT_PIPELINE_MODE": "legacy",
            "GENERATION_PIPELINE_MODE": "legacy",
            "ARTIFACT_READ_MODE": "legacy",
            "ARTIFACT_WRITE_MODE": "legacy",
            "OCR_ENABLED": "0",
            "OBSIDIAN_ENABLED": "0",
            "PAPER_STUDY_MCP_MODE": "legacy",
            "UI_ENTRY": "react",
        }
        with tempfile.TemporaryDirectory(prefix="study-app-recovered-owner-") as raw:
            root = Path(raw).resolve()
            executable = root / "node.exe"
            entrypoint = root / "server.js"
            database = root / "app.db"
            owner = root / "production-owner.json"
            executable.write_bytes(b"node-runtime")
            entrypoint.write_text("// server\n", encoding="utf-8")
            database.write_bytes(b"sqlite-fixture")
            expected_owner = b"node-quiesced-owner"
            owner.write_bytes(expected_owner)
            rollback_map = {
                "deploymentKind": "native-windows",
                "executablePath": str(executable),
                "executableSha256": hashlib.sha256(executable.read_bytes()).hexdigest(),
                "entrypointPath": str(entrypoint),
                "entrypointSha256": hashlib.sha256(entrypoint.read_bytes()).hexdigest(),
                "cwd": str(root),
                "host": "127.0.0.1",
                "ports": {"api": 5173},
                "databasePath": str(database),
                "environment": rollback_environment,
            }
            previous_unsigned = {
                "schemaVersion": 1,
                "markerKind": "runtime-owner",
                "ownerState": "node_active",
                "runtimeNamespace": "production",
                "databaseLineageId": "lineage",
                "subjectDatabaseId": "subject",
                "databaseIdentityManifestPath": str(root / "identity.json"),
                "databaseIdentityManifestFileSha256": "a" * 64,
                "originReceiptPath": str(root / "origin.json"),
                "originReceiptFileSha256": "b" * 64,
                "originReceiptSha256": "c" * 64,
                "entrypointPath": str(entrypoint),
                "processId": 41001,
                "executablePath": str(executable),
                "cwd": str(root),
                "argv": [str(executable), str(entrypoint)],
                "listenerHost": "127.0.0.1",
                "listenerPort": 5173,
                "databasePaths": [str(database)],
                "createdAt": "2026-08-15T00:00:00Z",
            }
            previous_owner = canonical_json_bytes(
                {
                    **previous_unsigned,
                    "ownerMarkerSha256": hashlib.sha256(
                        canonical_json_bytes(previous_unsigned)
                    ).hexdigest(),
                }
            )
            configured = SimpleNamespace(
                executable_path=executable,
                entrypoint_path=entrypoint,
                cwd=root,
                argv=(str(executable), str(entrypoint)),
                environment=rollback_environment,
            )
            operations = object.__new__(native_runtime.NativeWindowsRuntimeOperations)
            operations._configuration = SimpleNamespace(
                rollback=configured,
                roles=(
                    SimpleNamespace(
                        role="api",
                        environment={"PRODUCTION_OWNER_MARKER": str(owner)}
                    ),
                ),
            )
            handle = native_runtime.RuntimeProcess(
                role="frozen-node",
                argv=configured.argv,
                cwd=root,
                environment=rollback_environment,
                child=_Child(41002),
            )
            operations._active_frozen_node = handle
            operations._active_rollback_map = rollback_map

            operations.commit_frozen_node_owner(
                handle,
                rollback_map,
                owner_marker=owner,
                expected_owner_payload=expected_owner,
                previous_node_owner_payload=previous_owner,
            )

            document = json.loads(owner.read_text(encoding="utf-8"))
            self.assertEqual(41002, document["processId"])
            self.assertNotEqual(41001, document["processId"])
            unsigned = {
                key: value
                for key, value in document.items()
                if key != "ownerMarkerSha256"
            }
            self.assertEqual(
                hashlib.sha256(canonical_json_bytes(unsigned)).hexdigest(),
                document["ownerMarkerSha256"],
            )

    def test_native_legacy_probe_requires_all_frozen_node_paths(self) -> None:
        from backend.app.providers.native_runtime import (
            NativeLegacyReadinessProbe,
            RuntimeProcess,
        )

        requested: list[str] = []

        class _Response:
            status = 200

            def __enter__(self) -> "_Response":
                return self

            def __exit__(self, *_args: object) -> None:
                return None

            def read(self) -> bytes:
                return b"ok"

        def urlopen(url: str, *, timeout: float) -> _Response:
            self.assertGreater(timeout, 0)
            requested.append(url)
            return _Response()

        process = RuntimeProcess(
            role="frozen-node",
            argv=("node", "server.js"),
            cwd=Path.cwd(),
            environment={"HOST": "127.0.0.1", "PORT": "5173"},
            child=_Child(44501),
        )
        evidence = NativeLegacyReadinessProbe(
            timeout_seconds=1,
            request_timeout_seconds=0.5,
            urlopen=urlopen,
        )(process)

        self.assertTrue(evidence["ok"])
        self.assertEqual(
            [
                "http://127.0.0.1:5173/api/papers",
                "http://127.0.0.1:5173/api/reviews",
                "http://127.0.0.1:5173/pdfbytes",
                "http://127.0.0.1:5173/workspace/",
                "http://127.0.0.1:5173/legacy/",
            ],
            requested,
        )

    def test_stale_owner_recovery_smokes_before_reattest_and_cleans_failure(self) -> None:
        from backend.app.cli.native_runtime import recover_stale_node_owner

        class _Operations:
            def __init__(self) -> None:
                self.events: list[str] = []
                self.handle = SimpleNamespace(pid=44001)

            def reclaim_stale_frozen_node_state(self, _rollback_map: object) -> bool:
                self.events.append("reclaim")
                return True

            def start_frozen_node(self, _rollback_map: object) -> object:
                self.events.append("start")
                return self.handle

            def smoke_legacy(self, handle: object) -> dict[str, object]:
                self.assert_handle(handle)
                self.events.append("smoke")
                return {"ok": True, "paths": ["/api/papers"]}

            def stop_frozen_node(self, handle: object) -> None:
                self.assert_handle(handle)
                self.events.append("stop")

            def assert_handle(self, handle: object) -> None:
                if handle is not self.handle:
                    raise AssertionError("unexpected handle")

        successful = _Operations()
        report = SimpleNamespace(process_id=44001)
        result = recover_stale_node_owner(
            operations=successful,
            rollback_map={"deploymentKind": "native-windows"},
            reattest=lambda: successful.events.append("reattest") or report,
        )
        self.assertIs(report, result["report"])
        self.assertTrue(result["staleStateReclaimed"])
        self.assertEqual(["reclaim", "start", "smoke", "reattest"], successful.events)

        failed = _Operations()

        def fail_reattest() -> object:
            failed.events.append("reattest")
            raise RuntimeError("injected reattestation failure")

        with self.assertRaisesRegex(RuntimeError, "injected reattestation failure"):
            recover_stale_node_owner(
                operations=failed,
                rollback_map={"deploymentKind": "native-windows"},
                reattest=fail_reattest,
            )
        self.assertEqual(
            ["reclaim", "start", "smoke", "reattest", "stop"],
            failed.events,
        )

    def test_stale_owner_recovery_reclaims_only_a_nonlive_exact_node_state(self) -> None:
        from backend.app.api.compat.database_identity import canonical_json_bytes
        from backend.app.providers import native_runtime
        from backend.app.providers.native_runtime import NativeRuntimeError

        with tempfile.TemporaryDirectory(prefix="study-app-stale-node-state-") as raw:
            root = Path(raw)
            node = root / "node.exe"
            server = root / "server.js"
            database = root / "app.db"
            state = root / "node-runtime-state-v1.json"
            node.write_bytes(b"node")
            server.write_text("// server\n", encoding="utf-8")
            database.write_bytes(b"sqlite")
            environment = {
                "RUNTIME_ENVIRONMENT": "live",
                "RUNTIME_NAMESPACE": "production",
                "API_BACKEND_MODE": "legacy",
                "DOCUMENT_PIPELINE_MODE": "legacy",
                "GENERATION_PIPELINE_MODE": "legacy",
                "ARTIFACT_READ_MODE": "legacy",
                "ARTIFACT_WRITE_MODE": "legacy",
                "OCR_ENABLED": "0",
                "OBSIDIAN_ENABLED": "0",
                "PAPER_STUDY_MCP_MODE": "legacy",
                "UI_ENTRY": "react",
            }
            configured = SimpleNamespace(
                executable_path=node.resolve(),
                entrypoint_path=server.resolve(),
                cwd=root.resolve(),
                argv=(str(node.resolve()), str(server.resolve())),
                environment=environment,
            )
            rollback_map = {
                "deploymentKind": "native-windows",
                "executablePath": str(node.resolve()),
                "executableSha256": hashlib.sha256(node.read_bytes()).hexdigest(),
                "entrypointPath": str(server.resolve()),
                "entrypointSha256": hashlib.sha256(server.read_bytes()).hexdigest(),
                "cwd": str(root.resolve()),
                "host": "127.0.0.1",
                "ports": {"api": 5173},
                "databasePath": str(database.resolve()),
                "environment": environment,
            }
            unsigned = {
                "schemaVersion": 1,
                "stateKind": "native-frozen-node-runtime",
                "deploymentKind": "native-windows",
                "buildId": "a" * 64,
                "pid": 41001,
                "argv": list(configured.argv),
                "cwd": str(configured.cwd),
                "rollbackMapSha256": hashlib.sha256(
                    canonical_json_bytes(rollback_map)
                ).hexdigest(),
            }
            payload = canonical_json_bytes(
                {
                    **unsigned,
                    "stateSha256": hashlib.sha256(
                        canonical_json_bytes(unsigned)
                    ).hexdigest(),
                }
            )
            state.write_bytes(payload)
            operations = object.__new__(native_runtime.NativeWindowsRuntimeOperations)
            operations._configuration = SimpleNamespace(rollback=configured)
            operations._build_identity = SimpleNamespace(build_id="b" * 64)
            operations._node_state_path = state
            operations._active_frozen_node = None
            operations._node_state_payload = None
            operations._active_rollback_map = None

            class _LiveChild:
                def __init__(self, pid: int) -> None:
                    self.pid = pid

                def poll(self) -> None:
                    return None

                def process_metadata(self):
                    return configured.executable_path, configured.cwd, configured.argv

                def close(self) -> None:
                    return None

            with patch.object(native_runtime, "_AttachedChild", _LiveChild):
                with self.assertRaises(NativeRuntimeError) as active:
                    operations.reclaim_stale_frozen_node_state(rollback_map)
            self.assertEqual("NATIVE_ROLLBACK_ALREADY_ACTIVE", active.exception.code)
            self.assertEqual(payload, state.read_bytes())

            class _DeadChild:
                def __init__(self, _pid: int) -> None:
                    raise OSError("process no longer exists")

            with patch.object(native_runtime, "_AttachedChild", _DeadChild):
                self.assertTrue(
                    operations.reclaim_stale_frozen_node_state(rollback_map)
                )
            self.assertFalse(state.exists())

    def test_native_quiesce_rejects_foreign_database_holder_before_stopping_node(self) -> None:
        from backend.app.providers import native_runtime
        from backend.app.providers.native_runtime import NativeRuntimeError

        owner_marker = Path("production-owner.json")
        database = Path("app.db")
        entrypoint = Path("server.js")
        configuration = SimpleNamespace(
            rollback=SimpleNamespace(entrypoint_path=entrypoint),
            roles=(
                SimpleNamespace(
                    role="api",
                    environment={
                        "PRODUCTION_OWNER_MARKER": str(owner_marker),
                        "DB_PATH": str(database),
                    },
                ),
            ),
        )
        operations = object.__new__(native_runtime.NativeWindowsRuntimeOperations)
        operations._configuration = configuration
        operations._stop_timeout_seconds = 1.0
        operations._node_state_path = Path("native-node-state.json")
        snapshot = SimpleNamespace(
            node_processes=(SimpleNamespace(pid=41001),),
            live_python_roles=(),
            database_handle_pids=(41001, 41002),
        )

        class _Inspector:
            def __init__(self, **_kwargs: object) -> None:
                pass

            def snapshot(self) -> object:
                return snapshot

        with (
            patch.object(
                native_runtime,
                "_terminate_pid",
            ) as terminate,
            patch(
                "backend.app.cli.runtime_owner.read_node_active_owner_marker",
                return_value=SimpleNamespace(process_id=41001),
            ),
            patch(
                "backend.app.providers.runtime_lease.WindowsRuntimeInspector",
                _Inspector,
            ),
        ):
            with self.assertRaises(NativeRuntimeError) as raised:
                operations.quiesce_node()

        self.assertEqual("NATIVE_NODE_QUIESCE_FAILED", raised.exception.code)
        terminate.assert_not_called()

    def test_active_owner_rollback_map_requires_live_pid_and_exact_frozen_paths(self) -> None:
        from backend.app.api.compat.database_identity import canonical_json_bytes
        from backend.app.providers import native_runtime
        from backend.app.providers.native_runtime import NativeRuntimeError

        rollback_environment = {
            "RUNTIME_ENVIRONMENT": "live",
            "RUNTIME_NAMESPACE": "production",
            "API_BACKEND_MODE": "legacy",
            "DOCUMENT_PIPELINE_MODE": "legacy",
            "GENERATION_PIPELINE_MODE": "legacy",
            "ARTIFACT_READ_MODE": "legacy",
            "ARTIFACT_WRITE_MODE": "legacy",
            "OCR_ENABLED": "0",
            "OBSIDIAN_ENABLED": "0",
            "PAPER_STUDY_MCP_MODE": "legacy",
            "UI_ENTRY": "react",
        }
        with tempfile.TemporaryDirectory(prefix="study-app-active-owner-") as raw:
            root = Path(raw)
            executable = root / "node.exe"
            entrypoint = root / "server.js"
            database = root / "app.db"
            marker = root / "production-owner.json"
            executable.write_bytes(b"node-binary")
            entrypoint.write_text("// frozen node\n", encoding="utf-8")
            database.write_bytes(b"sqlite-fixture")
            configured = SimpleNamespace(
                executable_path=executable,
                entrypoint_path=entrypoint,
                cwd=root,
                argv=(str(executable), str(entrypoint)),
                environment=rollback_environment,
            )
            operations = object.__new__(native_runtime.NativeWindowsRuntimeOperations)
            operations._configuration = SimpleNamespace(rollback=configured)
            unsigned = {
                "schemaVersion": 1,
                "markerKind": "runtime-owner",
                "ownerState": "node_active",
                "runtimeNamespace": "production",
                "databaseLineageId": "lineage",
                "subjectDatabaseId": "subject",
                "databaseIdentityManifestPath": str(root / "identity.json"),
                "databaseIdentityManifestFileSha256": "a" * 64,
                "originReceiptPath": str(root / "origin.json"),
                "originReceiptFileSha256": "b" * 64,
                "originReceiptSha256": "c" * 64,
                "entrypointPath": str(entrypoint),
                "processId": 41001,
                "executablePath": str(executable),
                "cwd": str(root),
                "argv": [str(executable), str(entrypoint)],
                "listenerHost": "127.0.0.1",
                "listenerPort": 5173,
                "databasePaths": [str(database)],
                "createdAt": "2026-08-15T00:00:00Z",
            }
            marker.write_bytes(
                canonical_json_bytes(
                    {
                        **unsigned,
                        "ownerMarkerSha256": hashlib.sha256(
                            canonical_json_bytes(unsigned)
                        ).hexdigest(),
                    }
                )
            )
            with patch(
                "backend.app.providers.runtime_lease.runtime_pid_is_alive",
                return_value=True,
            ):
                rollback_map = operations.frozen_node_rollback_map_from_active_owner(
                    marker
                )
                with self.assertRaises(NativeRuntimeError) as stale_raised:
                    operations.frozen_node_rollback_map_from_owner(marker)
            self.assertEqual("NATIVE_STALE_OWNER_INVALID", stale_raised.exception.code)
            self.assertEqual("native-windows", rollback_map["deploymentKind"])
            self.assertEqual(5173, rollback_map["ports"]["api"])
            self.assertEqual(str(database), rollback_map["databasePath"])

            with patch(
                "backend.app.providers.runtime_lease.runtime_pid_is_alive",
                return_value=False,
            ):
                with self.assertRaises(NativeRuntimeError) as raised:
                    operations.frozen_node_rollback_map_from_active_owner(marker)
            self.assertEqual("NATIVE_ACTIVE_OWNER_INVALID", raised.exception.code)

    def test_stale_owner_reattestation_accepts_only_the_p4_relative_entrypoint_argv(self) -> None:
        from backend.app.api.compat.database_identity import canonical_json_bytes
        from backend.app.providers import native_runtime
        from backend.app.providers.native_runtime import NativeRuntimeError

        with tempfile.TemporaryDirectory(prefix="study-app-stale-owner-") as raw:
            root = Path(raw)
            executable = root / "node.exe"
            entrypoint = root / "server.js"
            database = root / "app.db"
            marker = root / "production-owner.json"
            executable.write_bytes(b"node-binary")
            entrypoint.write_text("// frozen node\n", encoding="utf-8")
            database.write_bytes(b"sqlite-fixture")
            configured = SimpleNamespace(
                executable_path=executable,
                entrypoint_path=entrypoint,
                cwd=root,
                argv=(str(executable), str(entrypoint)),
                environment={
                    "RUNTIME_ENVIRONMENT": "live",
                    "RUNTIME_NAMESPACE": "production",
                    "API_BACKEND_MODE": "legacy",
                    "DOCUMENT_PIPELINE_MODE": "legacy",
                    "GENERATION_PIPELINE_MODE": "legacy",
                    "ARTIFACT_READ_MODE": "legacy",
                    "ARTIFACT_WRITE_MODE": "legacy",
                    "OCR_ENABLED": "0",
                    "OBSIDIAN_ENABLED": "0",
                    "PAPER_STUDY_MCP_MODE": "legacy",
                    "UI_ENTRY": "react",
                },
            )
            operations = object.__new__(native_runtime.NativeWindowsRuntimeOperations)
            operations._configuration = SimpleNamespace(rollback=configured)

            def write_marker(argv: list[str]) -> None:
                unsigned = {
                    "schemaVersion": 1,
                    "markerKind": "runtime-owner",
                    "ownerState": "node_active",
                    "runtimeNamespace": "production",
                    "databaseLineageId": "lineage",
                    "subjectDatabaseId": "subject",
                    "databaseIdentityManifestPath": str(root / "identity.json"),
                    "databaseIdentityManifestFileSha256": "a" * 64,
                    "originReceiptPath": str(root / "origin.json"),
                    "originReceiptFileSha256": "b" * 64,
                    "originReceiptSha256": "c" * 64,
                    "entrypointPath": str(entrypoint),
                    "processId": 41001,
                    "executablePath": str(executable),
                    "cwd": str(root),
                    "argv": argv,
                    "listenerHost": "127.0.0.1",
                    "listenerPort": 5173,
                    "databasePaths": [str(database)],
                    "createdAt": "2026-08-15T00:00:00Z",
                }
                marker.write_bytes(
                    canonical_json_bytes(
                        {
                            **unsigned,
                            "ownerMarkerSha256": hashlib.sha256(
                                canonical_json_bytes(unsigned)
                            ).hexdigest(),
                        }
                    )
                )

            write_marker([str(executable), entrypoint.name])
            with patch(
                "backend.app.providers.runtime_lease.runtime_pid_is_alive",
                return_value=False,
            ):
                rollback_map = (
                    operations.frozen_node_rollback_map_from_stale_owner_for_reattestation(
                        marker
                    )
                )
                with self.assertRaises(NativeRuntimeError) as strict_raised:
                    operations.frozen_node_rollback_map_from_owner(marker)
            self.assertEqual("NATIVE_STALE_OWNER_INVALID", strict_raised.exception.code)
            self.assertEqual(str(entrypoint), rollback_map["entrypointPath"])

            write_marker([str(executable), ".\\server.js"])
            with (
                patch(
                    "backend.app.providers.runtime_lease.runtime_pid_is_alive",
                    return_value=False,
                ),
                self.assertRaises(NativeRuntimeError) as alias_raised,
            ):
                operations.frozen_node_rollback_map_from_stale_owner_for_reattestation(
                    marker
                )
            self.assertEqual("NATIVE_STALE_OWNER_INVALID", alias_raised.exception.code)

    def test_native_operator_exports_canonical_rollback_map_exclusively(self) -> None:
        from backend.app.api.compat.database_identity import canonical_json_bytes
        from backend.app.cli.native_runtime import run

        rollback_map = {
            "deploymentKind": "native-windows",
            "ports": {"api": 5173},
            "databasePath": "C:/study/app.db",
        }

        class _Operations:
            def frozen_node_rollback_map_from_active_owner(
                self,
                owner_marker: str,
            ) -> dict[str, object]:
                self.owner_marker = owner_marker
                return rollback_map

        operations = _Operations()
        with tempfile.TemporaryDirectory(prefix="study-app-export-map-") as raw:
            output = Path(raw) / "rollback-map.json"
            common = [
                "--native-runtime-spec",
                "native-runtime.json",
                "--build-identity-manifest",
                "build-identity.json",
                "--state-directory",
                "runtime-state",
                "--owner-marker",
                "owner.json",
                "--output",
                str(output),
            ]
            result = run(
                ["export-rollback-map", *common],
                operations_factory=lambda **_kwargs: operations,
            )
            payload = canonical_json_bytes(rollback_map)
            self.assertTrue(result["ok"])
            self.assertEqual(payload, output.read_bytes())
            self.assertEqual(hashlib.sha256(payload).hexdigest(), result["rollbackMapSha256"])
            with self.assertRaises(Exception):
                run(
                    ["export-rollback-map", *common],
                    operations_factory=lambda **_kwargs: operations,
                )

    def test_native_operator_configure_writes_complete_canonical_spec(self) -> None:
        from backend.app.cli.native_runtime import run

        with tempfile.TemporaryDirectory(prefix="study-app-native-config-") as raw:
            root = Path(raw)
            repository = root / "repository"
            repository.mkdir()
            python = root / "python.exe"
            python.write_bytes(b"python")
            requirements = repository / "requirements.txt"
            requirements.write_text("fastapi==1\n", encoding="utf-8")
            node = root / "node.exe"
            node.write_bytes(b"node")
            entrypoint = repository / "server.js"
            entrypoint.write_text("// frozen rollback\n", encoding="utf-8")
            database = root / "app.db"
            database.write_bytes(b"database")
            database_identity = root / "database-identity.json"
            database_identity.write_text("{}", encoding="utf-8")
            owner_marker = root / "production-owner.json"
            owner_marker.write_text("{}", encoding="utf-8")
            secret_file = root / "cursor.secret"
            secret = "cursor-secret-0123456789-abcdef"
            secret_file.write_text(secret + "\n", encoding="utf-8")
            output = root / "runtime" / "native-runtime-v1.json"

            result = run(
                [
                    "configure",
                    "--repository",
                    str(repository),
                    "--python-executable",
                    str(python),
                    "--requirements-lock",
                    str(requirements),
                    "--node-executable",
                    str(node),
                    "--node-entrypoint",
                    str(entrypoint),
                    "--database",
                    str(database),
                    "--database-identity-manifest",
                    str(database_identity),
                    "--owner-marker",
                    str(owner_marker),
                    "--runtime-lease-directory",
                    str(root / "leases"),
                    "--processing-cursor-secret-file",
                    str(secret_file),
                    "--api-port",
                    "5173",
                    "--output",
                    str(output),
                ]
            )

            document = json.loads(output.read_text(encoding="utf-8"))
            self.assertTrue(result["ok"])
            self.assertEqual("native-windows", document["deploymentKind"])
            self.assertEqual(
                ["api", "worker", "scheduler", "mcp"],
                list(document["roles"]),
            )
            self.assertEqual(
                "5173", document["roles"]["api"]["environment"]["API_BIND_PORT"]
            )
            self.assertEqual(
                "application",
                document["roles"]["mcp"]["environment"]["PAPER_STUDY_MCP_MODE"],
            )
            self.assertEqual(
                "--supervisor",
                document["roles"]["mcp"]["argv"][-1],
            )
            self.assertEqual(
                "legacy",
                document["frozenNodeRollback"]["environment"]["API_BACKEND_MODE"],
            )
            self.assertEqual(
                secret,
                document["roles"]["worker"]["environment"][
                    "PROCESSING_CURSOR_SECRET"
                ],
            )
            self.assertNotIn(secret, json.dumps(result))
            self.assertEqual(
                hashlib.sha256(output.read_bytes()).hexdigest(),
                result["nativeRuntimeSpecSha256"],
            )

    def test_native_runtime_spec_rejects_stdio_mcp_role(self) -> None:
        from backend.app.providers.native_runtime import (
            NativeRuntimeError,
            load_native_runtime_configuration,
        )

        with tempfile.TemporaryDirectory(prefix="study-app-native-spec-") as raw:
            root = Path(raw)
            python = Path(sys.executable).resolve()
            requirements = root / "requirements.txt"
            requirements.write_text("fastapi==1\n", encoding="utf-8")
            node = root / "node.exe"
            node.write_bytes(b"node")
            entrypoint = root / "server.js"
            entrypoint.write_text("// rollback\n", encoding="utf-8")
            spec = root / "native-runtime-v1.json"
            spec.write_text(
                json.dumps(
                    {
                        "schemaVersion": 1,
                        "deploymentKind": "native-windows",
                        "pythonExecutablePath": str(python),
                        "requirementsLockPath": str(requirements),
                        "applicationCwd": str(root),
                        "roles": {
                            role: {
                                "argv": (
                                    [str(python), "-B", "-m", "agent.mcp_server"]
                                    if role == "mcp"
                                    else [
                                        str(python),
                                        "-B",
                                        "-m",
                                        "backend.app.cli.candidate_runtime",
                                        "--role",
                                        role,
                                    ]
                                ),
                                "environment": {},
                            }
                            for role in ("api", "worker", "scheduler", "mcp")
                        },
                        "frozenNodeRollback": {
                            "executablePath": str(node),
                            "entrypointPath": str(entrypoint),
                            "cwd": str(root),
                            "argv": [str(node), str(entrypoint)],
                            "environment": {},
                        },
                    }
                ),
                encoding="utf-8",
            )

            with self.assertRaises(NativeRuntimeError) as raised:
                load_native_runtime_configuration(spec)

            self.assertEqual("NATIVE_RUNTIME_SPEC_INVALID", raised.exception.code)

    def test_subprocess_runtime_launcher_redirects_role_output_to_log(self) -> None:
        from backend.app.providers.native_runtime import (
            RuntimeLaunchRequest,
            SubprocessRuntimeLauncher,
        )

        with tempfile.TemporaryDirectory(prefix="study-app-native-log-") as raw:
            log_directory = Path(raw) / "logs"
            launcher = SubprocessRuntimeLauncher(log_directory=log_directory)
            child = launcher.start(
                RuntimeLaunchRequest(
                    role="api",
                    argv=(
                        sys.executable,
                        "-c",
                        "import sys; print('native-out'); print('native-err', file=sys.stderr)",
                    ),
                    cwd=Path(raw),
                    environment=dict(os.environ),
                )
            )

            self.assertEqual(0, child.wait(timeout=5))
            log_text = (log_directory / "api.log").read_text(encoding="utf-8")
            self.assertIn("native-out", log_text)
            self.assertIn("native-err", log_text)

    def test_native_readiness_requires_http_takeover_and_exact_mcp_tools(self) -> None:
        from backend.app.providers.native_runtime import (
            NativeRuntimeReadinessProbe,
            RuntimeLaunchRequest,
            RuntimeProcess,
            RuntimeProcessSet,
        )

        requested: list[str] = []

        class _Response:
            status = 200

            def __enter__(self) -> "_Response":
                return self

            def __exit__(self, *_args: object) -> None:
                return None

            def read(self) -> bytes:
                return b'{"status":"ok"}'

        def urlopen(url: str, *, timeout: float) -> _Response:
            self.assertGreater(timeout, 0)
            requested.append(url)
            return _Response()

        expected_tools = (
            "get_explainer",
            "get_paper",
            "get_translation",
            "library_overview",
            "list_categories",
            "list_due_reviews",
            "related_papers",
            "search_papers",
            "semantic_search",
        )
        api_request = RuntimeLaunchRequest(
            role="api",
            argv=("python", "api"),
            cwd=Path.cwd(),
            environment={"API_BIND_HOST": "0.0.0.0", "API_BIND_PORT": "5173"},
        )
        mcp_request = RuntimeLaunchRequest(
            role="mcp",
            argv=(
                sys.executable,
                "-B",
                "-m",
                "agent.mcp_server",
                "--supervisor",
            ),
            cwd=Path.cwd(),
            environment={"PAPER_STUDY_MCP_MODE": "legacy"},
        )
        processes = RuntimeProcessSet(
            (
                RuntimeProcess(
                    role="api",
                    argv=api_request.argv,
                    cwd=api_request.cwd,
                    environment=api_request.environment,
                    child=_Child(43001),
                ),
                RuntimeProcess(
                    role="mcp",
                    argv=mcp_request.argv,
                    cwd=mcp_request.cwd,
                    environment=mcp_request.environment,
                    child=_Child(43002),
                ),
            )
        )
        probe = NativeRuntimeReadinessProbe(
            timeout_seconds=10,
            request_timeout_seconds=10,
            urlopen=urlopen,
        )

        evidence = probe(processes)

        self.assertTrue(evidence["ok"])
        expected_urls = [
            "http://127.0.0.1:5173/health/live",
            "http://127.0.0.1:5173/health/ready",
            "http://127.0.0.1:5173/api/papers",
            "http://127.0.0.1:5173/api/v2/jobs",
            "http://127.0.0.1:5173/workspace/",
            "http://127.0.0.1:5173/legacy/",
        ]
        self.assertGreaterEqual(len(requested), len(expected_urls))
        self.assertEqual(0, len(requested) % len(expected_urls))
        for offset in range(0, len(requested), len(expected_urls)):
            self.assertEqual(expected_urls, requested[offset : offset + len(expected_urls)])
        self.assertEqual(list(expected_tools), evidence["mcp"]["toolNames"])

    def test_mcp_readiness_budget_is_a_process_start_not_an_http_request(self) -> None:
        from backend.app.providers.native_runtime import (
            NativeRuntimeReadinessProbe,
            RuntimeProcess,
            RuntimeProcessSet,
        )

        class _Response:
            status = 200

            def __enter__(self) -> "_Response":
                return self

            def __exit__(self, *_args: object) -> None:
                return None

            def read(self) -> bytes:
                return b"{}"

        def urlopen(_url: str, *, timeout: float) -> _Response:
            self.assertLessEqual(timeout, 3)
            return _Response()

        offered: list[float] = []

        def mcp_probe(_process: RuntimeProcess, timeout: float) -> dict[str, object]:
            offered.append(timeout)
            return {"ok": False, "reason": "mcp-tools-list-timeout"}

        processes = RuntimeProcessSet(
            (
                RuntimeProcess(
                    role="api",
                    argv=("python", "api"),
                    cwd=Path.cwd(),
                    environment={"API_BIND_HOST": "127.0.0.1", "API_BIND_PORT": "5173"},
                    child=_Child(43101),
                ),
                RuntimeProcess(
                    role="mcp",
                    argv=(sys.executable, "-B", "-m", "agent.mcp_server", "--supervisor"),
                    cwd=Path.cwd(),
                    environment={},
                    child=_Child(43102),
                ),
            )
        )
        elapsed = [0.0]

        def clock() -> float:
            return elapsed[0]

        def sleeper(seconds: float) -> None:
            elapsed[0] += seconds

        probe = NativeRuntimeReadinessProbe(
            timeout_seconds=90,
            request_timeout_seconds=3,
            mcp_timeout_seconds=30,
            urlopen=urlopen,
            mcp_probe=mcp_probe,
            clock=clock,
            sleeper=sleeper,
        )
        evidence = probe(processes)

        self.assertFalse(evidence["ok"])
        self.assertEqual({"ok": False, "reason": "mcp-tools-list-timeout"}, evidence["mcp"])
        # Cold-starting the MCP role is a process spawn, so it must never be
        # capped by the per-HTTP-request budget; only the remaining readiness
        # deadline may shrink it.
        self.assertTrue(offered)
        self.assertEqual(30.0, max(offered))
        self.assertLessEqual(max(offered), 30.0)

    def test_native_operator_start_status_and_stop_use_public_runtime_seam(self) -> None:
        from backend.app.cli.native_runtime import run

        class _Operations:
            def __init__(self) -> None:
                self.calls: list[object] = []
                self.processes = SimpleNamespace(
                    roles=("api", "worker", "scheduler", "mcp"),
                    processes=tuple(
                        SimpleNamespace(role=role, pid=42000 + index)
                        for index, role in enumerate(
                            ("api", "worker", "scheduler", "mcp")
                        )
                    ),
                )

            def start_active_python_roles(self, *, owner_marker: Path) -> object:
                self.calls.append(("start", owner_marker))
                return self.processes

            def smoke_python(self, processes: object) -> dict[str, object]:
                self.calls.append(("smoke", processes))
                return {"ok": True, "readiness": {"http": True, "mcp": True}}

            def status_python_roles(self) -> dict[str, object]:
                self.calls.append("status")
                return {"ok": True, "state": "running", "roles": list(self.processes.roles)}

            def stop_active_python_roles(self) -> object:
                self.calls.append("stop")
                return SimpleNamespace(
                    stopped_roles=("mcp", "scheduler", "worker", "api"),
                    zero_processes=True,
                )

            def drain_python_roles(self, processes: object) -> object:
                self.calls.append(("drain", processes))
                return None

        operations = _Operations()
        factory_calls: list[dict[str, object]] = []

        def factory(**arguments: object) -> _Operations:
            factory_calls.append(arguments)
            return operations

        common = [
            "--native-runtime-spec",
            "native-runtime.json",
            "--build-identity-manifest",
            "build-identity.json",
            "--state-directory",
            "runtime-state",
        ]
        started = run(
            ["start", *common, "--owner-marker", "production-owner.json"],
            operations_factory=factory,
        )
        status = run(["status", *common], operations_factory=factory)
        stopped = run(["stop", *common], operations_factory=factory)

        self.assertEqual("running", started["state"])
        self.assertEqual([42000, 42001, 42002, 42003], started["processIds"])
        self.assertEqual("running", status["state"])
        self.assertEqual("stopped", stopped["state"])
        self.assertEqual(3, len(factory_calls))
        self.assertEqual(
            ["start", "smoke", "status", "stop"],
            [call[0] if isinstance(call, tuple) else call for call in operations.calls],
        )

    def test_real_p6_handoff_promotes_native_roles_through_public_coordinator(self) -> None:
        from backend.app.api.compat.build_identity import freeze_build_identity
        from backend.app.api.compat.database_identity import DatabaseEvidenceIdentityService
        from backend.app.api.compat.evidence_capture import create_evidence_run
        from backend.app.api.compat.gates import _issue_authorization
        from backend.app.application.final_window import (
            FinalWindowCoordinator,
            create_production_startup_snapshot,
        )
        from backend.app.application.runtime_handoff import ProductionPromotionCoordinator
        from backend.app.providers.native_runtime import NativeWindowsRuntimeOperations
        from backend.tests.support.p4_identity import p4_identity_fixture
        from backend.tests.test_runtime_ownership import (
            _Inspector,
            _api,
            _owner_arguments,
            _process,
            _snapshot,
        )

        class _Quiesce:
            def quiesce_node(self) -> dict[str, object]:
                return {"zeroPidPortDatabaseHandles": True}

        class _Watchdog:
            def start(self, _lease: Path, _token: Path) -> int:
                return os.getpid()

            def stop(self, _lease: Path) -> None:
                return None

        with p4_identity_fixture() as fixture:
            database = DatabaseEvidenceIdentityService().create_live_database_identity(
                database=fixture.database_path,
                p0_origin_receipt=fixture.receipt_path,
                expected_p0_origin_receipt_sha256=fixture.receipt_file_sha256,
                origin_backup=fixture.origin_backup_path,
                origin_manifest=fixture.origin_manifest_path,
                output=fixture.root / "live-database-identity.json",
            )
            repository = fixture.root / "repository"
            repository.mkdir()
            _git(repository, "init", "--quiet")
            _git(repository, "config", "user.name", "Native Handoff Test")
            _git(repository, "config", "user.email", "handoff@example.test")
            (repository / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
            _git(repository, "add", "--", "app.py")
            _git(repository, "commit", "--quiet", "-m", "fixture")
            python = Path(sys.executable).resolve()
            requirements = fixture.root / "requirements.txt"
            requirements.write_text("fastapi==0.1\n", encoding="utf-8")
            node = fixture.root / "node.exe"
            node.write_bytes(b"node-v1")
            frontend = fixture.root / "frontend"
            frontend.mkdir()
            (frontend / "index.js").write_bytes(b"frontend-v1")
            frontend_manifest = frontend / "manifest.json"
            frontend_manifest.write_text(
                json.dumps({"index.html": {"file": "index.js"}}), encoding="utf-8"
            )
            owner_marker = fixture.root / "production-owner.json"
            lease_root = fixture.root / "production-leases"
            api_port = _available_loopback_port()
            base_environment = {
                "RUNTIME_ENVIRONMENT": "live",
                "RUNTIME_NAMESPACE": "production",
                "DB_PATH": str(fixture.database_path),
                "DATABASE_IDENTITY_MANIFEST": str(database.manifest_path),
                "PRODUCTION_OWNER_MARKER": str(owner_marker),
                "RUNTIME_LEASE_DIR": str(lease_root),
                "PROCESSING_CURSOR_SECRET": "native-handoff-secret-0123456789",
                "API_BIND_HOST": "127.0.0.1",
                "API_BIND_PORT": str(api_port),
                "API_LOOPBACK_PORT_FORWARDING": "0",
                "ALLOW_REMOTE_ACCESS": "0",
                "OCR_ENABLED": "0",
                "OBSIDIAN_ENABLED": "0",
                "UI_ENTRY": "react",
                "PYTHONPATH": str(Path(__file__).resolve().parents[2]),
            }
            rollback_environment = {
                "RUNTIME_ENVIRONMENT": "live",
                "RUNTIME_NAMESPACE": "production",
                "API_BACKEND_MODE": "legacy",
                "DOCUMENT_PIPELINE_MODE": "legacy",
                "GENERATION_PIPELINE_MODE": "legacy",
                "ARTIFACT_READ_MODE": "legacy",
                "ARTIFACT_WRITE_MODE": "legacy",
                "OCR_ENABLED": "0",
                "OBSIDIAN_ENABLED": "0",
                "PAPER_STUDY_MCP_MODE": "legacy",
                "UI_ENTRY": "react",
            }
            runtime_spec = fixture.root / "native-runtime-v1.json"
            runtime_spec.write_text(
                json.dumps(
                    {
                        "schemaVersion": 1,
                        "deploymentKind": "native-windows",
                        "pythonExecutablePath": str(python),
                        "requirementsLockPath": str(requirements),
                        "applicationCwd": str(repository),
                        "roles": {
                            role: {
                                "argv": (
                                    [
                                        str(python),
                                        "-B",
                                        "-m",
                                        "agent.mcp_server",
                                        "--supervisor",
                                    ]
                                    if role == "mcp"
                                    else [
                                        str(python),
                                        "-B",
                                        "-m",
                                        "backend.app.cli.candidate_runtime",
                                        "--role",
                                        role,
                                    ]
                                ),
                                "environment": {
                                    **base_environment,
                                    "API_PROCESS_ROLE": role,
                                    **(
                                        {"PAPER_STUDY_MCP_MODE": "application"}
                                        if role == "mcp"
                                        else {}
                                    ),
                                },
                            }
                            for role in ("api", "worker", "scheduler", "mcp")
                        },
                        "frozenNodeRollback": {
                            "executablePath": str(node),
                            "entrypointPath": str(fixture.entrypoint_path),
                            "cwd": str(fixture.entrypoint_path.parent),
                            "argv": [str(node), str(fixture.entrypoint_path)],
                            "environment": rollback_environment,
                        },
                    }
                ),
                encoding="utf-8",
            )
            build_root = fixture.root / "build-identities"
            build_root.mkdir()
            build = freeze_build_identity(
                repository=repository,
                build_identity_directory=build_root,
                python_artifacts=(requirements,),
                frontend_root=frontend,
                frontend_manifest=frontend_manifest,
                deployment_kind="native-windows",
                native_runtime_spec=runtime_spec,
            )
            evidence_root = fixture.root / "evidence"
            evidence_root.mkdir()
            run = create_evidence_run(
                evidence_root=evidence_root,
                run_id="9" * 32,
                phase="final",
                build_identity_manifest=build.manifest_path,
                database_identity_manifest=database.manifest_path,
                expected_keys=("node-quiesce", "handoff-contract"),
            )
            rollback_map = {
                "deploymentKind": "native-windows",
                "executablePath": str(node.resolve()),
                "executableSha256": hashlib.sha256(node.read_bytes()).hexdigest(),
                "entrypointPath": str(fixture.entrypoint_path.resolve()),
                "entrypointSha256": hashlib.sha256(
                    fixture.entrypoint_path.read_bytes()
                ).hexdigest(),
                "cwd": str(fixture.entrypoint_path.parent.resolve()),
                "host": "127.0.0.1",
                "ports": {"api": 5173},
                "databasePath": str(fixture.database_path.resolve()),
                "environment": rollback_environment,
            }
            startup = create_production_startup_snapshot(
                final_evidence_run_manifest=run.manifest_path,
                expected_final_evidence_run_manifest_sha256=run.manifest_file_sha256,
                build_identity_manifest=build.manifest_path,
                database_identity_manifest=database.manifest_path,
                frozen_node_rollback_map=rollback_map,
                output=run.run_directory / "production-startup-snapshot-v1.json",
            )
            identity_type, owner_type, process_type, snapshot_type = _api()
            self.assertIs(identity_type, DatabaseEvidenceIdentityService)
            owner_type(
                _Inspector(_snapshot(snapshot_type, (_process(fixture, process_type),)))
            ).initialize_node_owner(
                **_owner_arguments(fixture, database.manifest_path, owner_marker)
            )
            final_window = FinalWindowCoordinator(
                operations=_Quiesce(),
                watchdog=_Watchdog(),
                token_factory=lambda: b"n" * 32,
                clock=lambda: datetime(2026, 8, 15, 2, 0, tzinfo=timezone.utc),
                coordinator_pid=os.getpid(),
            )
            lease = final_window.begin_final_window(
                final_evidence_run_manifest=run.manifest_path,
                expected_final_evidence_run_manifest_sha256=run.manifest_file_sha256,
                startup_snapshot=startup.path,
                expected_startup_snapshot_sha256=startup.file_sha256,
                owner_marker=owner_marker,
                runtime_namespace="production",
                operator_pid=os.getpid(),
                heartbeat_timeout_seconds=120,
                lease_output=fixture.root / f"final-window-{run.run_id}.json",
                token_file_output=fixture.root / f"final-window-{run.run_id}.token",
            )
            final_window.quiesce_live(
                cutover_lease=lease.path,
                cutover_token_file=lease.token_file_path,
            )
            authorization_path = run.run_directory / "promotion-authorization.json"
            authorization = _issue_authorization(
                run.run_directory,
                {
                    "runId": run.run_id,
                    "runManifestPath": run.manifest_path,
                    "runManifestSha256": run.manifest_file_sha256,
                    "startupSnapshotPath": startup.path,
                    "startupSnapshotSha256": startup.file_sha256,
                    "cutoverLeasePath": lease.path,
                    "cutoverLeaseSha256": hashlib.sha256(lease.path.read_bytes()).hexdigest(),
                },
                output=authorization_path,
                ttl_seconds=900,
                clock=lambda: datetime(2026, 8, 15, 2, 1, tzinfo=timezone.utc),
            )
            launcher = _Launcher()
            operations = NativeWindowsRuntimeOperations(
                native_runtime_spec=runtime_spec,
                build_identity_manifest=build.manifest_path,
                state_directory=fixture.root / "native-state",
                launcher=launcher,
                readiness_probe=lambda _processes: {"ok": True},
                role_lock_probe=lambda _processes: {
                    "worker": "a" * 64,
                    "scheduler": "b" * 64,
                },
            )
            promoter = ProductionPromotionCoordinator(
                operations=operations,
                clock=lambda: datetime(2026, 8, 15, 2, 2, tzinfo=timezone.utc),
                receipt_id_factory=lambda: "8" * 32,
            )
            handoff = promoter.begin_handoff(
                authorization=authorization_path,
                expected_authorization_sha256=str(authorization["authorizationSha256"]),
                cutover_lease=lease.path,
                cutover_token_file=lease.token_file_path,
                startup_snapshot=startup.path,
                expected_startup_snapshot_sha256=startup.file_sha256,
                owner_marker=owner_marker,
            )
            smoke = operations.run_promotion_smoke(
                python_profile="production",
                rollback_profile="frozen-node",
            )
            receipt = promoter.commit_python_owner(
                handoff,
                smoke_evidence=smoke,
                handoff_receipt_output=run.run_directory / "python-handoff-receipt.json",
            )

            self.assertEqual(("api", "worker", "scheduler", "mcp"), tuple(smoke["roles"]))
            self.assertTrue(receipt.path.is_file())
            owner_document = json.loads(owner_marker.read_text(encoding="utf-8"))
            self.assertEqual("python_active", owner_document["ownerState"])
            self.assertEqual(receipt.file_sha256, owner_document["handoffReceiptFileSha256"])
            operations.drain_python_ingress()
            operations.drain_worker_claims()
            operations.stop_scheduler_obsidian_mcp()
            operations.stop_fastapi()
            operations.release_locks_connections()

            restart_launcher = _Launcher()
            restarted_operations = NativeWindowsRuntimeOperations(
                native_runtime_spec=runtime_spec,
                build_identity_manifest=build.manifest_path,
                state_directory=fixture.root / "native-state",
                launcher=restart_launcher,
                readiness_probe=lambda _processes: {"ok": True},
                role_lock_probe=lambda _processes: {
                    "worker": "c" * 64,
                    "scheduler": "d" * 64,
                },
            )
            restarted = restarted_operations.start_active_python_roles(
                owner_marker=owner_marker,
            )
            restarted_smoke = restarted_operations.smoke_python(restarted)
            self.assertTrue(restarted_smoke["ok"])
            for request in restart_launcher.requests:
                self.assertEqual(
                    str(receipt.path),
                    request.environment["P6_HANDOFF_RECEIPT"],
                )
                self.assertEqual(
                    receipt.file_sha256,
                    request.environment["P6_HANDOFF_RECEIPT_SHA256"],
                )
            restarted_operations.drain_python_roles(restarted)

            from backend.app.cli.native_runtime import run as run_native_runtime

            state_path = fixture.root / "native-state" / "python-runtime-state-v1.json"
            common = [
                "--native-runtime-spec",
                str(runtime_spec),
                "--build-identity-manifest",
                str(build.manifest_path),
                "--state-directory",
                str(fixture.root / "native-state"),
            ]
            native_started: dict[str, object] | None = None
            native_stopped: dict[str, object] | None = None
            native_identities: tuple[tuple[int, tuple[str, ...], Path], ...] = ()
            try:
                try:
                    native_started = run_native_runtime(
                        ["start", *common, "--owner-marker", str(owner_marker)]
                    )
                except Exception as error:
                    log_root = fixture.root / "native-state" / "logs"
                    diagnostics = {
                        path.name: path.read_text(encoding="utf-8", errors="replace")[-4000:]
                        for path in sorted(log_root.glob("*.log"))
                    }
                    self.fail(f"native start failed: {error}; logs={diagnostics}")
                native_status = run_native_runtime(["status", *common])
                self.assertEqual("running", native_status["state"])
                self.assertEqual(
                    ["api", "worker", "scheduler", "mcp"],
                    native_status["roles"],
                )
                native_identities = _recorded_native_role_identities(state_path)
                self.assertEqual(
                    list(native_started["processIds"]),
                    [process_id for process_id, _argv, _cwd in native_identities],
                )
                # The identity probe only proves a stop once it proves a start.
                self.assertEqual(
                    sorted(process_id for process_id, _argv, _cwd in native_identities),
                    sorted(
                        process_id
                        for process_id, _argv in _live_native_role_processes(
                            native_identities
                        )
                    ),
                )
            finally:
                if state_path.exists():
                    native_stopped = run_native_runtime(["stop", *common])

            self.assertIsNotNone(native_started)
            assert native_started is not None
            self.assertEqual("running", native_started["state"])
            self.assertEqual(
                ["api", "worker", "scheduler", "mcp"],
                native_started["roles"],
            )
            self.assertIsNotNone(native_stopped)
            assert native_stopped is not None
            self.assertEqual("stopped", native_stopped["state"])
            self.assertTrue(native_stopped["zeroProcesses"])
            self.assertFalse(
                (fixture.root / "native-state" / "python-runtime-state-v1.json").exists()
            )
            self.assertEqual([], list(lease_root.glob("*.json")))
            self.assertEqual((), _live_native_role_processes(native_identities))

    def test_candidate_runtime_accepts_and_validates_native_process_markers(self) -> None:
        from backend.app.cli.candidate_runtime import run

        with tempfile.TemporaryDirectory(prefix="study-app-native-markers-") as raw:
            database = Path(raw) / "app.db"
            database.write_bytes(b"not-opened")
            stderr = StringIO()
            result = asyncio.run(
                run(
                    [
                        "--role",
                        "api",
                        "--study-app-role",
                        "api",
                        "--study-app-environment",
                        "invalid-environment",
                    ],
                    environment={
                        "API_PROCESS_ROLE": "api",
                        "RUNTIME_ENVIRONMENT": "invalid-environment",
                        "RUNTIME_NAMESPACE": "production",
                        "DB_PATH": str(database),
                        "REQUIRED_SCHEMA_REVISION": "20260807_03",
                    },
                    stderr=stderr,
                )
            )
            self.assertEqual(2, result)
            self.assertEqual(
                "RUNTIME_ENVIRONMENT_INVALID",
                json.loads(stderr.getvalue())["error"]["code"],
            )

    def test_stateless_stop_refuses_zero_while_any_frozen_role_survives(self) -> None:
        from backend.app.api.compat.build_identity import freeze_build_identity
        from backend.app.providers.native_runtime import (
            NativeRuntimeError,
            NativeWindowsRuntimeOperations,
        )

        with tempfile.TemporaryDirectory(prefix="study-app-native-survivor-") as raw:
            root = Path(raw)
            repository = root / "repository"
            (repository / "agent").mkdir(parents=True)
            _git(repository, "init", "--quiet")
            _git(repository, "config", "user.name", "Native Survivor Test")
            _git(repository, "config", "user.email", "survivor@example.test")
            # The mcp role is the one native role that carries no --study-app-role
            # marker, so only a real process identity can prove it stopped.
            (repository / "agent" / "__init__.py").write_text("", encoding="utf-8")
            (repository / "agent" / "mcp_server.py").write_text(
                "import time\n\nif __name__ == '__main__':\n    time.sleep(120)\n",
                encoding="utf-8",
            )
            _git(repository, "add", "--all", "--", ".")
            _git(repository, "commit", "--quiet", "-m", "fixture")

            python = Path(sys.executable).resolve()
            requirements = root / "requirements.txt"
            requirements.write_text("fastapi==0.1\n", encoding="utf-8")
            node = root / "node.exe"
            node.write_bytes(b"node-v1")
            server = root / "server.js"
            server.write_text("console.log('legacy');\n", encoding="utf-8")
            database = root / "app.db"
            database.write_bytes(b"sqlite-fixture")
            frontend = root / "frontend"
            frontend.mkdir()
            (frontend / "index.js").write_bytes(b"frontend-v1")
            frontend_manifest = frontend / "manifest.json"
            frontend_manifest.write_text(
                json.dumps({"index.html": {"file": "index.js"}}), encoding="utf-8"
            )
            role_environment = {
                "RUNTIME_ENVIRONMENT": "live",
                "RUNTIME_NAMESPACE": "production",
                "DB_PATH": str(database),
                "RUNTIME_LEASE_DIR": str(root / "leases"),
            }
            runtime_spec = root / "native-runtime-v1.json"
            runtime_spec.write_text(
                json.dumps(
                    {
                        "schemaVersion": 1,
                        "deploymentKind": "native-windows",
                        "pythonExecutablePath": str(python),
                        "requirementsLockPath": str(requirements),
                        "applicationCwd": str(repository),
                        "roles": {
                            role: {
                                "argv": (
                                    [
                                        str(python),
                                        "-B",
                                        "-m",
                                        "agent.mcp_server",
                                        "--supervisor",
                                    ]
                                    if role == "mcp"
                                    else [
                                        str(python),
                                        "-B",
                                        "-m",
                                        "backend.app.cli.candidate_runtime",
                                        "--role",
                                        role,
                                    ]
                                ),
                                "environment": {
                                    **role_environment,
                                    "API_PROCESS_ROLE": role,
                                },
                            }
                            for role in ("api", "worker", "scheduler", "mcp")
                        },
                        "frozenNodeRollback": {
                            "executablePath": str(node),
                            "entrypointPath": str(server),
                            "cwd": str(root),
                            "argv": [str(node), str(server)],
                            "environment": {
                                "RUNTIME_ENVIRONMENT": "live",
                                "RUNTIME_NAMESPACE": "production",
                                "API_BACKEND_MODE": "legacy",
                                "DOCUMENT_PIPELINE_MODE": "legacy",
                                "GENERATION_PIPELINE_MODE": "legacy",
                                "ARTIFACT_READ_MODE": "legacy",
                                "ARTIFACT_WRITE_MODE": "legacy",
                                "OCR_ENABLED": "0",
                                "OBSIDIAN_ENABLED": "0",
                                "PAPER_STUDY_MCP_MODE": "legacy",
                                "UI_ENTRY": "react",
                            },
                        },
                    }
                ),
                encoding="utf-8",
            )
            identities = root / "identities"
            identities.mkdir()
            build = freeze_build_identity(
                repository=repository,
                build_identity_directory=identities,
                python_artifacts=(requirements,),
                frontend_root=frontend,
                frontend_manifest=frontend_manifest,
                deployment_kind="native-windows",
                native_runtime_spec=runtime_spec,
            )
            operations = NativeWindowsRuntimeOperations(
                native_runtime_spec=runtime_spec,
                build_identity_manifest=build.manifest_path,
                state_directory=root / "state",
            )
            self.assertFalse((root / "state" / "python-runtime-state-v1.json").exists())

            survivor = subprocess.Popen(
                [str(python), "-B", "-m", "agent.mcp_server", "--supervisor"],
                cwd=str(repository),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            try:
                deadline = time.monotonic() + 20
                while time.monotonic() < deadline:
                    if _live_native_role_processes(
                        ((survivor.pid, (str(python), "-B", "-m", "agent.mcp_server",
                                         "--supervisor"), repository),)
                    ):
                        break
                    self.assertIsNone(survivor.poll(), "the survivor exited early")
                    time.sleep(0.1)
                else:
                    self.fail("the survivor process never became observable")
                with self.assertRaises(NativeRuntimeError) as refused:
                    operations.stop_active_python_roles()
                self.assertEqual(
                    "NATIVE_RUNTIME_STATE_MISSING",
                    refused.exception.code,
                )
            finally:
                survivor.kill()
                survivor.wait(timeout=30)

            self.assertEqual(
                (),
                _live_native_role_processes(
                    (
                        (
                            survivor.pid,
                            (
                                str(python),
                                "-B",
                                "-m",
                                "agent.mcp_server",
                                "--supervisor",
                            ),
                            repository,
                        ),
                    )
                ),
            )
            evidence = operations.stop_active_python_roles()
            self.assertTrue(evidence.zero_processes)
            self.assertEqual((), evidence.stopped_roles)

    def test_start_smoke_and_drain_bind_exact_native_roles(self) -> None:
        from backend.app.api.compat.build_identity import freeze_build_identity
        from backend.app.application.final_window import ProductionStartupSnapshot
        from backend.app.providers.native_runtime import NativeWindowsRuntimeOperations

        with tempfile.TemporaryDirectory(prefix="study-app-native-operations-") as raw:
            root = Path(raw)
            repository = root / "repository"
            repository.mkdir()
            _git(repository, "init", "--quiet")
            _git(repository, "config", "user.name", "Native Operations Test")
            _git(repository, "config", "user.email", "operations@example.test")
            (repository / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
            _git(repository, "add", "--", "app.py")
            _git(repository, "commit", "--quiet", "-m", "fixture")

            python = root / "python.exe"
            python.write_bytes(b"python-v1")
            requirements = root / "requirements.txt"
            requirements.write_text("fastapi==0.1\n", encoding="utf-8")
            node = root / "node.exe"
            node.write_bytes(b"node-v1")
            server = repository / "server.js"
            server.write_text("console.log('legacy');\n", encoding="utf-8")
            frontend = root / "frontend"
            frontend.mkdir()
            (frontend / "index.js").write_bytes(b"frontend-v1")
            frontend_manifest = frontend / "manifest.json"
            frontend_manifest.write_text(
                json.dumps({"index.html": {"file": "index.js"}}),
                encoding="utf-8",
            )
            role_environment = {
                "RUNTIME_ENVIRONMENT": "live",
                "RUNTIME_NAMESPACE": "production",
                "DB_PATH": str(root / "app.db"),
                "RUNTIME_LEASE_DIR": str(root / "leases"),
                "PROCESSING_CURSOR_SECRET": "runtime-secret",
            }
            rollback_environment = {
                "RUNTIME_ENVIRONMENT": "live",
                "RUNTIME_NAMESPACE": "production",
                "API_BACKEND_MODE": "legacy",
                "DOCUMENT_PIPELINE_MODE": "legacy",
                "GENERATION_PIPELINE_MODE": "legacy",
                "ARTIFACT_READ_MODE": "legacy",
                "ARTIFACT_WRITE_MODE": "legacy",
                "OCR_ENABLED": "0",
                "OBSIDIAN_ENABLED": "0",
                "PAPER_STUDY_MCP_MODE": "legacy",
                "UI_ENTRY": "react",
            }
            runtime_spec = root / "native-runtime-v1.json"
            runtime_spec.write_text(
                json.dumps(
                    {
                        "schemaVersion": 1,
                        "deploymentKind": "native-windows",
                        "pythonExecutablePath": str(python),
                        "requirementsLockPath": str(requirements),
                        "applicationCwd": str(repository),
                        "roles": {
                            role: {
                                "argv": (
                                    [
                                        str(python),
                                        "-B",
                                        "-m",
                                        "agent.mcp_server",
                                        "--supervisor",
                                    ]
                                    if role == "mcp"
                                    else [
                                        str(python),
                                        "-B",
                                        "-m",
                                        "backend.app.cli.candidate_runtime",
                                        "--role",
                                        role,
                                    ]
                                ),
                                "environment": {
                                    **role_environment,
                                    "API_PROCESS_ROLE": role,
                                    **(
                                        {"PAPER_STUDY_MCP_MODE": "application"}
                                        if role == "mcp"
                                        else {}
                                    ),
                                },
                            }
                            for role in ("api", "worker", "scheduler", "mcp")
                        },
                        "frozenNodeRollback": {
                            "executablePath": str(node),
                            "entrypointPath": str(server),
                            "cwd": str(repository),
                            "argv": [str(node), str(server)],
                            "environment": rollback_environment,
                        },
                    }
                ),
                encoding="utf-8",
            )
            identities = root / "identities"
            identities.mkdir()
            build = freeze_build_identity(
                repository=repository,
                build_identity_directory=identities,
                python_artifacts=(requirements,),
                frontend_root=frontend,
                frontend_manifest=frontend_manifest,
                deployment_kind="native-windows",
                native_runtime_spec=runtime_spec,
            )
            snapshot = ProductionStartupSnapshot(
                path=root / "startup-snapshot.json",
                file_sha256="1" * 64,
                run_id="2" * 32,
                run_manifest_path=root / "evidence-run.json",
                run_manifest_file_sha256="3" * 64,
                build_identity_manifest_path=build.manifest_path,
                build_identity_manifest_sha256=build.manifest_file_sha256,
                build_id=build.build_id,
                database_identity_manifest_path=root / "database-identity.json",
                database_identity_manifest_sha256="4" * 64,
                database_lineage_id="5" * 64,
                live_subject_database_id="6" * 64,
                origin_receipt_path=root / "origin-receipt.json",
                origin_receipt_file_sha256="7" * 64,
                rollback_map={"profile": "frozen-node"},
                rollback_map_sha256="8" * 64,
                canonical_bytes=b"{}",
            )
            dynamic_environment = {
                "P6_PROMOTION_AUTHORIZATION": str(root / "authorization.json"),
                "P6_CUTOVER_LEASE": str(root / "cutover-lease.json"),
                "P6_PRODUCTION_STARTUP_SNAPSHOT": str(snapshot.path),
            }
            launcher = _Launcher()
            readiness_calls: list[object] = []
            legacy_calls: list[object] = []

            def readiness(processes: object) -> dict[str, object]:
                readiness_calls.append(processes)
                return {
                    "ok": True,
                    "endpoints": ["/health/ready", "/api/papers", "mcp:tools/list"],
                }

            operations = NativeWindowsRuntimeOperations(
                native_runtime_spec=runtime_spec,
                build_identity_manifest=build.manifest_path,
                state_directory=root / "state",
                launcher=launcher,
                readiness_probe=readiness,
                role_lock_probe=lambda _processes: {
                    "worker": "a" * 64,
                    "scheduler": "b" * 64,
                },
                legacy_probe=lambda handle: legacy_calls.append(handle)
                or {"ok": True, "endpoint": "/"},
            )
            processes = operations.start_python_roles(
                snapshot,
                runtime_environment=dynamic_environment,
            )

            self.assertEqual(("api", "worker", "scheduler", "mcp"), processes.roles)
            self.assertEqual(4, len(launcher.requests))
            for request, expected_role in zip(
                launcher.requests,
                ("api", "worker", "scheduler", "mcp"),
                strict=True,
            ):
                self.assertEqual(expected_role, request.role)
                self.assertEqual(repository.resolve(), request.cwd)
                self.assertEqual("live", request.environment["RUNTIME_ENVIRONMENT"])
                self.assertEqual(
                    dynamic_environment["P6_CUTOVER_LEASE"],
                    request.environment["P6_CUTOVER_LEASE"],
                )
                if expected_role != "mcp":
                    self.assertEqual(
                        ("--study-app-role", expected_role, "--study-app-environment", "live"),
                        request.argv[-4:],
                    )

            smoke = operations.smoke_python(processes)
            self.assertTrue(smoke["ok"])
            self.assertEqual(
                {"worker": "a" * 64, "scheduler": "b" * 64},
                smoke["roleLocks"],
            )
            self.assertEqual([processes], readiness_calls)

            drained = operations.drain_python_roles(processes)
            self.assertTrue(drained.zero_processes)
            self.assertEqual(("mcp", "scheduler", "worker", "api"), drained.stopped_roles)
            self.assertTrue(all(child.terminated for child in launcher.children))
            self.assertFalse((root / "state" / "python-runtime-state-v1.json").exists())

            database = root / "app.db"
            database.write_bytes(b"sqlite-fixture")
            rollback_map = {
                "deploymentKind": "native-windows",
                "executablePath": str(node.resolve()),
                "executableSha256": hashlib.sha256(node.read_bytes()).hexdigest(),
                "entrypointPath": str(server.resolve()),
                "entrypointSha256": hashlib.sha256(server.read_bytes()).hexdigest(),
                "cwd": str(repository.resolve()),
                "host": "127.0.0.1",
                "ports": {"api": 5173},
                "databasePath": str(database.resolve()),
                "environment": rollback_environment,
            }
            node_handle = operations.start_frozen_node(rollback_map)
            self.assertEqual("frozen-node", launcher.requests[-1].role)
            self.assertEqual((str(node), str(server)), launcher.requests[-1].argv)
            self.assertEqual({"ok": True, "endpoint": "/"}, operations.smoke_legacy(node_handle))
            self.assertEqual([node_handle], legacy_calls)
            self.assertIs(node_handle, operations.attach_frozen_node(rollback_map))

            failing_launcher = _Launcher(fail_role="scheduler")
            failing = NativeWindowsRuntimeOperations(
                native_runtime_spec=runtime_spec,
                build_identity_manifest=build.manifest_path,
                state_directory=root / "failed-state",
                launcher=failing_launcher,
                readiness_probe=readiness,
                role_lock_probe=lambda _processes: {},
            )
            with self.assertRaises(RuntimeError):
                failing.start_python_roles(
                    snapshot,
                    runtime_environment=dynamic_environment,
                )
            self.assertTrue(all(child.terminated for child in failing_launcher.children))
            self.assertFalse(
                (root / "failed-state" / "python-runtime-state-v1.json").exists()
            )


if __name__ == "__main__":
    unittest.main()
