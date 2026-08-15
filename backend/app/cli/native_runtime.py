from __future__ import annotations

import argparse
from collections.abc import Callable, Sequence
import json
from pathlib import Path
import sys
from typing import Any

from backend.app.api.compat.database_identity import (
    load_database_evidence_identity_manifest,
)
from backend.app.cli.runtime_owner import RuntimeOwnerError, RuntimeOwnerService
from backend.app.providers.native_runtime import (
    NativeRuntimeError,
    NativeWindowsRuntimeOperations,
    write_native_runtime_spec,
)


OperationsFactory = Callable[..., NativeWindowsRuntimeOperations]


def recover_stale_node_owner(
    *,
    operations: object,
    rollback_map: object,
    reattest: Callable[[], object],
) -> dict[str, object]:
    handle = operations.start_frozen_node(rollback_map)
    try:
        smoke = operations.smoke_legacy(handle)
        report = reattest()
    except BaseException:
        operations.stop_frozen_node(handle)
        raise
    return {"handle": handle, "smoke": smoke, "report": report}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="study-native-runtime")
    commands = parser.add_subparsers(dest="command", required=True)

    configure = commands.add_parser("configure")
    configure.add_argument("--repository", required=True)
    configure.add_argument("--python-executable", required=True)
    configure.add_argument("--requirements-lock", required=True)
    configure.add_argument("--node-executable", required=True)
    configure.add_argument("--node-entrypoint", required=True)
    configure.add_argument("--database", required=True)
    configure.add_argument("--database-identity-manifest", required=True)
    configure.add_argument("--owner-marker", required=True)
    configure.add_argument("--runtime-lease-directory", required=True)
    configure.add_argument("--processing-cursor-secret-file", required=True)
    configure.add_argument("--api-port", type=int, default=5173)
    configure.add_argument("--output", required=True)

    start = commands.add_parser("start")
    _add_runtime_arguments(start)
    start.add_argument("--owner-marker", required=True)

    status = commands.add_parser("status")
    _add_runtime_arguments(status)

    stop = commands.add_parser("stop")
    _add_runtime_arguments(stop)

    recover = commands.add_parser("recover-stale-node-owner")
    _add_runtime_arguments(recover)
    recover.add_argument("--database-identity-manifest", required=True)
    recover.add_argument("--p0-origin-receipt", required=True)
    recover.add_argument("--expected-p0-origin-receipt-sha256", required=True)
    recover.add_argument("--origin-backup", required=True)
    recover.add_argument("--origin-manifest", required=True)
    recover.add_argument("--owner-marker", required=True)
    return parser


def run(
    arguments: Sequence[str],
    *,
    operations_factory: OperationsFactory = NativeWindowsRuntimeOperations,
) -> dict[str, Any]:
    options = build_parser().parse_args(list(arguments))
    if options.command == "configure":
        path, file_sha256 = write_native_runtime_spec(
            repository=options.repository,
            python_executable=options.python_executable,
            requirements_lock=options.requirements_lock,
            node_executable=options.node_executable,
            node_entrypoint=options.node_entrypoint,
            database=options.database,
            database_identity_manifest=options.database_identity_manifest,
            owner_marker=options.owner_marker,
            runtime_lease_directory=options.runtime_lease_directory,
            processing_cursor_secret_file=options.processing_cursor_secret_file,
            api_port=options.api_port,
            output=options.output,
        )
        return {
            "ok": True,
            "operation": "configure",
            "deploymentKind": "native-windows",
            "nativeRuntimeSpecPath": str(path),
            "nativeRuntimeSpecSha256": file_sha256,
        }
    operations = operations_factory(
        native_runtime_spec=_path(options.native_runtime_spec),
        build_identity_manifest=_path(options.build_identity_manifest),
        state_directory=_path(options.state_directory),
    )
    if options.command == "start":
        processes = operations.start_active_python_roles(
            owner_marker=_path(options.owner_marker),
        )
        try:
            smoke = operations.smoke_python(processes)
        except BaseException:
            operations.drain_python_roles(processes)
            raise
        return {
            "ok": True,
            "operation": "start",
            "deploymentKind": "native-windows",
            "state": "running",
            "roles": list(processes.roles),
            "processIds": [process.pid for process in processes.processes],
            "smoke": smoke,
        }
    if options.command == "status":
        return {
            "operation": "status",
            "deploymentKind": "native-windows",
            **operations.status_python_roles(),
        }
    if options.command == "stop":
        evidence = operations.stop_active_python_roles()
        return {
            "ok": evidence.zero_processes,
            "operation": "stop",
            "deploymentKind": "native-windows",
            "state": "stopped" if evidence.zero_processes else "degraded",
            "stoppedRoles": list(evidence.stopped_roles),
            "zeroProcesses": evidence.zero_processes,
        }
    if options.command == "recover-stale-node-owner":
        rollback_map = operations.frozen_node_rollback_map_from_owner(
            options.owner_marker
        )
        identity = load_database_evidence_identity_manifest(
            options.database_identity_manifest
        )
        from backend.app.providers.runtime_lease import WindowsRuntimeInspector

        owner_service = RuntimeOwnerService(
            WindowsRuntimeInspector(
                expected_entrypoint_path=str(rollback_map["entrypointPath"]),
                tracked_database_paths=(identity.database_path,),
            )
        )
        recovery = recover_stale_node_owner(
            operations=operations,
            rollback_map=rollback_map,
            reattest=lambda: owner_service.reattest_stale_node_owner(
                database_identity_manifest=options.database_identity_manifest,
                p0_origin_receipt=options.p0_origin_receipt,
                expected_p0_origin_receipt_sha256=(
                    options.expected_p0_origin_receipt_sha256
                ),
                origin_backup=options.origin_backup,
                origin_manifest=options.origin_manifest,
                runtime_namespace="production",
                expected_entrypoint_path=str(rollback_map["entrypointPath"]),
                owner_marker=options.owner_marker,
            ),
        )
        report = recovery["report"]
        smoke = recovery["smoke"]
        return {
            "ok": True,
            "operation": "recover-stale-node-owner",
            "deploymentKind": "native-windows",
            "ownerState": report.owner_state,
            "processId": report.process_id,
            "ownerMarkerPath": str(report.owner_marker_path),
            "ownerMarkerFileSha256": report.owner_marker_file_sha256,
            "verificationMode": report.verification_mode,
            "legacySmoke": smoke,
        }
    raise AssertionError(f"unsupported command: {options.command}")


def main(arguments: Sequence[str] | None = None) -> int:
    try:
        result = run(sys.argv[1:] if arguments is None else arguments)
    except (NativeRuntimeError, RuntimeOwnerError, OSError, ValueError) as error:
        result = {
            "ok": False,
            "error": {
                "code": str(getattr(error, "code", "NATIVE_RUNTIME_FAILED")),
                "message": str(error),
            },
        }
        sys.stderr.write(json.dumps(result, ensure_ascii=False, separators=(",", ":")) + "\n")
        return 2
    sys.stdout.write(json.dumps(result, ensure_ascii=False, separators=(",", ":")) + "\n")
    return 0 if result.get("ok") is True else 1


def _add_runtime_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--native-runtime-spec", required=True)
    parser.add_argument("--build-identity-manifest", required=True)
    parser.add_argument("--state-directory", required=True)


def _path(value: str) -> Path:
    return Path(value).expanduser().resolve(strict=False)


if __name__ == "__main__":
    raise SystemExit(main())
