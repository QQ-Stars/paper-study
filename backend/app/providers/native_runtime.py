from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import time
from typing import Protocol
import urllib.error
import urllib.request

from backend.app.api.compat.build_identity import (
    BuildIdentityManifest,
    native_role_argv,
    verify_native_runtime_spec,
)
from backend.app.api.compat.database_identity import (
    DatabaseIdentityError,
    canonical_json_bytes,
    exclusive_write_bytes,
)
from backend.app.application.final_window import ProductionStartupSnapshot
from backend.app.application.final_window import _load_lease, load_production_startup_snapshot
from backend.app.application.production_rollback import validate_frozen_node_rollback_map


_ROLES = ("api", "worker", "scheduler", "mcp")
_PROCESS_ROLES = frozenset({"api", "worker", "scheduler"})
_READINESS_PATHS = (
    "/health/live",
    "/health/ready",
    "/api/papers",
    "/api/v2/jobs",
    "/workspace/",
    "/legacy/",
)
_LEGACY_SMOKE_PATHS = (
    "/api/papers",
    "/api/reviews",
    "/pdfbytes",
    "/workspace/",
    "/legacy/",
)
_MCP_TOOL_NAMES = (
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


class NativeRuntimeError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(f"{code}: {message}")


@dataclass(frozen=True, slots=True)
class NativeRoleConfiguration:
    role: str
    argv: tuple[str, ...]
    environment: Mapping[str, str]


@dataclass(frozen=True, slots=True)
class NativeRollbackConfiguration:
    executable_path: Path
    entrypoint_path: Path
    cwd: Path
    argv: tuple[str, ...]
    environment: Mapping[str, str]


@dataclass(frozen=True, slots=True)
class NativeRuntimeConfiguration:
    spec_path: Path
    python_executable_path: Path
    requirements_lock_path: Path
    application_cwd: Path
    roles: tuple[NativeRoleConfiguration, ...]
    rollback: NativeRollbackConfiguration


@dataclass(frozen=True, slots=True)
class RuntimeLaunchRequest:
    role: str
    argv: tuple[str, ...]
    cwd: Path
    environment: Mapping[str, str]


class RuntimeChild(Protocol):
    pid: int

    def poll(self) -> int | None: ...

    def terminate(self) -> None: ...

    def wait(self, timeout: float | None = None) -> int: ...


class RuntimeLauncher(Protocol):
    def start(self, request: RuntimeLaunchRequest) -> RuntimeChild: ...


@dataclass(frozen=True, slots=True)
class RuntimeProcess:
    role: str
    argv: tuple[str, ...]
    cwd: Path
    environment: Mapping[str, str]
    child: RuntimeChild

    @property
    def pid(self) -> int:
        return self.child.pid


@dataclass(frozen=True, slots=True)
class RuntimeProcessSet:
    processes: tuple[RuntimeProcess, ...]

    @property
    def roles(self) -> tuple[str, ...]:
        return tuple(process.role for process in self.processes)


@dataclass(frozen=True, slots=True)
class DrainEvidence:
    stopped_roles: tuple[str, ...]
    zero_processes: bool


def write_native_runtime_spec(
    *,
    repository: str | os.PathLike[str],
    python_executable: str | os.PathLike[str],
    requirements_lock: str | os.PathLike[str],
    node_executable: str | os.PathLike[str],
    node_entrypoint: str | os.PathLike[str],
    database: str | os.PathLike[str],
    database_identity_manifest: str | os.PathLike[str],
    owner_marker: str | os.PathLike[str],
    runtime_lease_directory: str | os.PathLike[str],
    processing_cursor_secret_file: str | os.PathLike[str],
    api_port: int,
    output: str | os.PathLike[str],
) -> tuple[Path, str]:
    root = _existing_directory(repository, "application repository")
    python = _existing_file(python_executable, "Python executable")
    requirements = _existing_file(requirements_lock, "requirements lock")
    node = _existing_file(node_executable, "Node executable")
    entrypoint = _existing_file(node_entrypoint, "Node entrypoint")
    database_path = _existing_file(database, "Live database")
    identity_path = _existing_file(
        database_identity_manifest,
        "database identity manifest",
    )
    marker_path = _existing_file(owner_marker, "production owner marker")
    secret_path = _existing_file(
        processing_cursor_secret_file,
        "processing cursor secret file",
    )
    if (
        requirements.parent != root
        or entrypoint.parent != root
        or not isinstance(api_port, int)
        or isinstance(api_port, bool)
        or not 1 <= api_port <= 65535
    ):
        raise NativeRuntimeError(
            "NATIVE_RUNTIME_SPEC_INPUT_INVALID",
            "Runtime files must bind the repository root and a valid API port.",
        )
    try:
        cursor_secret = secret_path.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeError) as error:
        raise NativeRuntimeError(
            "NATIVE_RUNTIME_SECRET_INVALID",
            "The processing cursor secret could not be read.",
        ) from error
    if len(cursor_secret) < 24:
        raise NativeRuntimeError(
            "NATIVE_RUNTIME_SECRET_INVALID",
            "The processing cursor secret must contain at least 24 characters.",
        )
    lease_root = Path(runtime_lease_directory).expanduser().resolve(strict=False)
    common_environment = {
        "RUNTIME_ENVIRONMENT": "live",
        "RUNTIME_NAMESPACE": "production",
        "DB_PATH": str(database_path),
        "DATABASE_IDENTITY_MANIFEST": str(identity_path),
        "PRODUCTION_OWNER_MARKER": str(marker_path),
        "RUNTIME_LEASE_DIR": str(lease_root),
        "REQUIRED_SCHEMA_REVISION": "20260807_03",
        "PROCESSING_CURSOR_SECRET": cursor_secret,
        "API_BIND_HOST": "127.0.0.1",
        "API_BIND_PORT": str(api_port),
        "API_LOOPBACK_PORT_FORWARDING": "0",
        "ALLOW_REMOTE_ACCESS": "0",
        "API_BACKEND_MODE": "python",
        "DOCUMENT_PIPELINE_MODE": "p1",
        "GENERATION_PIPELINE_MODE": "p1",
        "ARTIFACT_READ_MODE": "prefer_new",
        "ARTIFACT_WRITE_MODE": "dual",
        "OCR_ENABLED": "0",
        "OBSIDIAN_ENABLED": "0",
        "UI_ENTRY": "react",
        "EMBED_PROVIDER": "model2vec",
        "EMBED_MODEL": "minishlab/potion-multilingual-128M",
        "EMBEDDING_VERSION": "model2vec-0.8.2",
        "EMBED_DIMENSIONS": "256",
        "PYTHONUNBUFFERED": "1",
        "PYTHONUTF8": "1",
        "PYTHONIOENCODING": "utf-8",
    }
    roles: dict[str, object] = {}
    for role in _ROLES:
        argv = list(_native_role_argv(python, role))
        environment = {**common_environment, "API_PROCESS_ROLE": role}
        if role == "mcp":
            environment["PAPER_STUDY_MCP_MODE"] = "application"
        roles[role] = {"argv": argv, "environment": environment}
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
    document = {
        "schemaVersion": 1,
        "deploymentKind": "native-windows",
        "pythonExecutablePath": str(python),
        "requirementsLockPath": str(requirements),
        "applicationCwd": str(root),
        "roles": roles,
        "frozenNodeRollback": {
            "executablePath": str(node),
            "entrypointPath": str(entrypoint),
            "cwd": str(root),
            "argv": [str(node), str(entrypoint)],
            "environment": rollback_environment,
        },
    }
    payload = canonical_json_bytes(document)
    output_path = Path(output).expanduser().resolve(strict=False)
    try:
        exclusive_write_bytes(output_path, payload)
    except DatabaseIdentityError as error:
        raise NativeRuntimeError(error.code, str(error)) from error
    return output_path, hashlib.sha256(payload).hexdigest()


class NativeRuntimeReadinessProbe:
    def __init__(
        self,
        *,
        timeout_seconds: float = 90.0,
        request_timeout_seconds: float = 3.0,
        mcp_timeout_seconds: float = 30.0,
        poll_interval_seconds: float = 0.1,
        urlopen: Callable[..., object] = urllib.request.urlopen,
        mcp_probe: Callable[[RuntimeProcess, float], Mapping[str, object]] | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        if (
            timeout_seconds <= 0
            or request_timeout_seconds <= 0
            or mcp_timeout_seconds <= 0
            or poll_interval_seconds <= 0
        ):
            raise NativeRuntimeError(
                "NATIVE_RUNTIME_ARGUMENT_INVALID",
                "Native readiness timeouts must be positive.",
            )
        self._timeout_seconds = timeout_seconds
        self._request_timeout_seconds = request_timeout_seconds
        # Probing MCP cold-starts a Python process, so it cannot share the
        # per-HTTP-request budget.
        self._mcp_timeout_seconds = mcp_timeout_seconds
        self._poll_interval_seconds = poll_interval_seconds
        self._urlopen = urlopen
        self._mcp_probe = mcp_probe or _probe_mcp_tools_list
        self._clock = clock
        self._sleeper = sleeper

    def __call__(self, processes: RuntimeProcessSet) -> Mapping[str, object]:
        by_role = {process.role: process for process in processes.processes}
        api = by_role.get("api")
        mcp = by_role.get("mcp")
        if api is None or mcp is None:
            return {"ok": False, "reason": "required-role-missing"}
        host = api.environment.get("API_BIND_HOST", "127.0.0.1")
        if host in {"0.0.0.0", "::", "[::]"}:
            host = "127.0.0.1"
        port = api.environment.get("API_BIND_PORT", "")
        if not isinstance(port, str) or not port.isdigit():
            return {"ok": False, "reason": "api-port-invalid"}
        deadline = self._clock() + self._timeout_seconds
        last_reason = "readiness-timeout"
        last_mcp: Mapping[str, object] | None = None
        while True:
            if any(process.child.poll() is not None for process in processes.processes):
                return {"ok": False, "reason": "role-exited"}
            try:
                statuses: dict[str, int] = {}
                for path in _READINESS_PATHS:
                    remaining = deadline - self._clock()
                    if remaining <= 0:
                        raise TimeoutError("readiness deadline expired")
                    url = f"http://{host}:{port}{path}"
                    with self._urlopen(
                        url,
                        timeout=min(self._request_timeout_seconds, remaining),
                    ) as response:
                        status = int(getattr(response, "status"))
                        response.read()
                    if status != 200:
                        raise OSError(f"{path} returned HTTP {status}")
                    statuses[path] = status
                remaining = deadline - self._clock()
                if remaining <= 0:
                    raise TimeoutError("readiness deadline expired")
                mcp_evidence = dict(
                    self._mcp_probe(
                        mcp,
                        min(self._mcp_timeout_seconds, remaining),
                    )
                )
                last_mcp = mcp_evidence
                if (
                    mcp_evidence.get("ok") is not True
                    or tuple(mcp_evidence.get("toolNames", ())) != _MCP_TOOL_NAMES
                ):
                    raise OSError("MCP tools/list did not match the frozen contract")
                return {
                    "ok": True,
                    "http": statuses,
                    "mcp": mcp_evidence,
                }
            except (OSError, TimeoutError, ValueError, TypeError) as error:
                last_reason = type(error).__name__
                remaining = deadline - self._clock()
                if remaining <= 0:
                    failed: dict[str, object] = {"ok": False, "reason": last_reason}
                    if last_mcp is not None:
                        failed["mcp"] = dict(last_mcp)
                    return failed
                self._sleeper(min(self._poll_interval_seconds, remaining))


class NativeLegacyReadinessProbe:
    def __init__(
        self,
        *,
        timeout_seconds: float = 15.0,
        request_timeout_seconds: float = 3.0,
        poll_interval_seconds: float = 0.1,
        urlopen: Callable[..., object] = urllib.request.urlopen,
        clock: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        if (
            timeout_seconds <= 0
            or request_timeout_seconds <= 0
            or poll_interval_seconds <= 0
        ):
            raise NativeRuntimeError(
                "NATIVE_RUNTIME_ARGUMENT_INVALID",
                "Native legacy readiness timeouts must be positive.",
            )
        self._timeout_seconds = timeout_seconds
        self._request_timeout_seconds = request_timeout_seconds
        self._poll_interval_seconds = poll_interval_seconds
        self._urlopen = urlopen
        self._clock = clock
        self._sleeper = sleeper

    def __call__(self, process: RuntimeProcess) -> Mapping[str, object]:
        host = process.environment.get("HOST")
        port = process.environment.get("PORT")
        if host != "127.0.0.1" or not isinstance(port, str) or not port.isdigit():
            return {"ok": False, "reason": "rollback-address-invalid"}
        deadline = self._clock() + self._timeout_seconds
        last_reason = "legacy-readiness-timeout"
        while True:
            if process.child.poll() is not None:
                return {"ok": False, "reason": "rollback-process-exited"}
            try:
                statuses: dict[str, int] = {}
                for path in _LEGACY_SMOKE_PATHS:
                    remaining = deadline - self._clock()
                    if remaining <= 0:
                        raise TimeoutError("legacy readiness deadline expired")
                    url = f"http://{host}:{port}{path}"
                    try:
                        with self._urlopen(
                            url,
                            timeout=min(self._request_timeout_seconds, remaining),
                        ) as response:
                            status = int(getattr(response, "status"))
                            response.read()
                    except urllib.error.HTTPError as error:
                        status = int(error.code)
                        error.read()
                    if status >= 500:
                        raise OSError(f"{path} returned HTTP {status}")
                    statuses[path] = status
                return {
                    "ok": True,
                    "paths": list(_LEGACY_SMOKE_PATHS),
                    "statuses": statuses,
                }
            except (OSError, TimeoutError, ValueError, TypeError) as error:
                last_reason = type(error).__name__
                remaining = deadline - self._clock()
                if remaining <= 0:
                    return {"ok": False, "reason": last_reason}
                self._sleeper(min(self._poll_interval_seconds, remaining))


class NativeWindowsRuntimeOperations:
    def __init__(
        self,
        *,
        native_runtime_spec: str | os.PathLike[str],
        build_identity_manifest: str | os.PathLike[str],
        state_directory: str | os.PathLike[str],
        launcher: RuntimeLauncher | None = None,
        readiness_probe: Callable[[RuntimeProcessSet], Mapping[str, object]] | None = None,
        role_lock_probe: Callable[[RuntimeProcessSet], Mapping[str, str]] | None = None,
        legacy_probe: Callable[[RuntimeProcess], Mapping[str, object]] | None = None,
        stop_timeout_seconds: float = 30.0,
    ) -> None:
        self._configuration = load_native_runtime_configuration(native_runtime_spec)
        self._build_identity = verify_native_runtime_spec(
            build_identity_manifest=build_identity_manifest,
            native_runtime_spec=self._configuration.spec_path,
        )
        self._state_directory = Path(state_directory).expanduser().resolve(strict=False)
        self._state_path = self._state_directory / "python-runtime-state-v1.json"
        self._launcher = launcher or SubprocessRuntimeLauncher(
            log_directory=self._state_directory / "logs"
        )
        self._readiness_probe = readiness_probe or NativeRuntimeReadinessProbe()
        self._role_lock_probe = role_lock_probe or self._read_role_locks
        self._legacy_probe = legacy_probe or NativeLegacyReadinessProbe()
        if stop_timeout_seconds <= 0:
            raise NativeRuntimeError(
                "NATIVE_RUNTIME_ARGUMENT_INVALID",
                "The native runtime stop timeout must be positive.",
            )
        self._stop_timeout_seconds = stop_timeout_seconds
        self._active_processes: RuntimeProcessSet | None = None
        self._state_payload: bytes | None = None
        self._node_state_path = self._state_directory / "node-runtime-state-v1.json"
        self._active_frozen_node: RuntimeProcess | None = None
        self._node_state_payload: bytes | None = None
        self._active_rollback_map: dict[str, object] | None = None
        self._cutover_lease_path: Path | None = None

    def start_python_roles(
        self,
        startup_snapshot: ProductionStartupSnapshot,
        *,
        runtime_environment: Mapping[str, str] | None = None,
    ) -> RuntimeProcessSet:
        self._verify_startup_snapshot(startup_snapshot)
        if self._active_processes is not None or self._state_path.exists():
            raise NativeRuntimeError(
                "NATIVE_RUNTIME_ALREADY_ACTIVE",
                "The native Python runtime already has durable process state.",
            )
        self._ensure_role_lease_directory()
        dynamic = _string_mapping(runtime_environment or {})
        started: list[RuntimeProcess] = []
        try:
            for role_config in self._configuration.roles:
                argv = self._role_launch_argv(role_config)
                environment = {
                    **os.environ,
                    **role_config.environment,
                    **dynamic,
                }
                request = RuntimeLaunchRequest(
                    role=role_config.role,
                    argv=tuple(argv),
                    cwd=self._configuration.application_cwd,
                    environment=environment,
                )
                child = self._launcher.start(request)
                if (
                    not isinstance(child.pid, int)
                    or isinstance(child.pid, bool)
                    or child.pid <= 0
                    or child.poll() is not None
                ):
                    raise NativeRuntimeError(
                        "NATIVE_RUNTIME_START_FAILED",
                        f"The native {role_config.role} role did not stay active.",
                    )
                started.append(
                    RuntimeProcess(
                        role=role_config.role,
                        argv=request.argv,
                        cwd=request.cwd,
                        environment=request.environment,
                        child=child,
                    )
                )
            processes = RuntimeProcessSet(tuple(started))
            payload = self._process_state_payload(processes, startup_snapshot)
            self._state_directory.mkdir(parents=True, exist_ok=True)
            try:
                exclusive_write_bytes(self._state_path, payload)
            except DatabaseIdentityError as error:
                raise NativeRuntimeError(error.code, str(error)) from error
            self._active_processes = processes
            self._state_payload = payload
            return processes
        except BaseException:
            self._stop_processes(tuple(reversed(started)))
            raise

    def start_active_python_roles(
        self,
        *,
        owner_marker: str | os.PathLike[str],
    ) -> RuntimeProcessSet:
        from backend.app.application.runtime_handoff import (
            _load_runtime_owner,
            load_handoff_receipt,
        )
        from backend.app.runtime import DatabaseSettings, ProductionRuntimeGuard

        try:
            owner_path = Path(owner_marker).expanduser().resolve(strict=True)
            owner = _load_runtime_owner(owner_path.read_bytes())
            receipt_path = Path(str(owner["handoffReceiptPath"])).resolve(strict=True)
            receipt_sha = str(owner["handoffReceiptFileSha256"])
            receipt = load_handoff_receipt(
                receipt_path,
                expected_file_sha256=receipt_sha,
            )
            receipt_document = json.loads(receipt.canonical_bytes.decode("utf-8"))
            snapshot = load_production_startup_snapshot(
                str(receipt_document["startupSnapshotPath"]),
                expected_file_sha256=str(
                    receipt_document["startupSnapshotFileSha256"]
                ),
            )
            database = DatabaseSettings(str(snapshot.rollback_map["databasePath"]))
            ProductionRuntimeGuard().validate_active_owner(
                handoff_receipt=receipt.path,
                expected_handoff_receipt_sha256=receipt.file_sha256,
                owner_marker=owner_path,
                database=database,
                environment="live",
                runtime_namespace="production",
                role="api",
            )
        except NativeRuntimeError:
            raise
        except Exception as error:
            raise NativeRuntimeError(
                "NATIVE_ACTIVE_OWNER_INVALID",
                "The active Python owner identity chain is invalid.",
            ) from error
        return self.start_python_roles(
            snapshot,
            runtime_environment={
                "RUNTIME_ENVIRONMENT": "live",
                "RUNTIME_NAMESPACE": "production",
                "DB_PATH": str(database.database_path),
                "DATABASE_IDENTITY_MANIFEST": str(
                    snapshot.database_identity_manifest_path
                ),
                "PRODUCTION_OWNER_MARKER": str(owner_path),
                "P6_HANDOFF_RECEIPT": str(receipt.path),
                "P6_HANDOFF_RECEIPT_SHA256": receipt.file_sha256,
                "REQUIRED_SCHEMA_REVISION": "20260807_03",
            },
        )

    def smoke_python(self, process_set: RuntimeProcessSet) -> dict[str, object]:
        self._require_active_set(process_set)
        if any(process.child.poll() is not None for process in process_set.processes):
            raise NativeRuntimeError(
                "NATIVE_RUNTIME_EXITED",
                "A native runtime role exited before readiness completed.",
            )
        readiness = self._readiness_probe(process_set)
        role_locks = dict(self._role_lock_probe(process_set))
        if (
            not isinstance(readiness, Mapping)
            or readiness.get("ok") is not True
            or set(role_locks) != {"worker", "scheduler"}
            or any(not _is_sha256(value) for value in role_locks.values())
        ):
            raise NativeRuntimeError(
                "NATIVE_RUNTIME_SMOKE_FAILED",
                "Native readiness or role-lock evidence did not pass: "
                + json.dumps(
                    {
                        "readiness": (
                            dict(readiness)
                            if isinstance(readiness, Mapping)
                            else repr(readiness)
                        ),
                        "roleLockRoles": sorted(role_locks),
                    },
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
            )
        return {
            "ok": True,
            "deploymentKind": "native-windows",
            "buildId": self._build_identity.build_id,
            "roles": list(process_set.roles),
            "roleLocks": role_locks,
            "readiness": dict(readiness),
        }

    def takeover_watchdog(self, cutover_lease: str | os.PathLike[str]) -> None:
        lease_path, _payload, lease = _load_lease(cutover_lease)
        if (
            lease.get("phase") != "handoff_pending"
            or Path(str(lease.get("buildIdentityManifestPath"))).resolve(strict=True)
            != self._build_identity.manifest_path
            or lease.get("buildIdentityManifestSha256")
            != self._build_identity.manifest_file_sha256
            or lease.get("buildId") != self._build_identity.build_id
        ):
            raise NativeRuntimeError(
                "NATIVE_HANDOFF_IDENTITY_MISMATCH",
                "The cutover lease does not bind this native runtime identity.",
            )
        self._cutover_lease_path = lease_path

    def run_promotion_smoke(
        self,
        *,
        python_profile: str,
        rollback_profile: str,
    ) -> dict[str, object]:
        if python_profile != "production" or rollback_profile != "frozen-node":
            raise NativeRuntimeError(
                "NATIVE_PROMOTION_PROFILE_INVALID",
                "Native promotion requires production and frozen-node profiles.",
            )
        if self._cutover_lease_path is None:
            raise NativeRuntimeError(
                "NATIVE_HANDOFF_REQUIRED",
                "Native promotion requires a taken-over cutover lease.",
            )
        lease_path, _payload, lease = _load_lease(self._cutover_lease_path)
        if lease.get("phase") != "handoff_pending":
            raise NativeRuntimeError(
                "NATIVE_HANDOFF_PHASE_INVALID",
                "Native promotion can start only during handoff_pending.",
            )
        snapshot = load_production_startup_snapshot(
            str(lease["startupSnapshotPath"]),
            expected_file_sha256=str(lease["startupSnapshotFileSha256"]),
        )
        authorization_path = Path(str(lease["runManifestPath"])).resolve(
            strict=True
        ).parent / "promotion-authorization.json"
        authorization_sha = hashlib.sha256(authorization_path.read_bytes()).hexdigest()
        runtime_environment = {
            "RUNTIME_ENVIRONMENT": "live",
            "RUNTIME_NAMESPACE": "production",
            "DB_PATH": str(snapshot.rollback_map["databasePath"]),
            "DATABASE_IDENTITY_MANIFEST": str(snapshot.database_identity_manifest_path),
            "PRODUCTION_OWNER_MARKER": str(lease["ownerMarkerPath"]),
            "P6_PROMOTION_AUTHORIZATION": str(authorization_path),
            "P6_PROMOTION_AUTHORIZATION_SHA256": authorization_sha,
            "P6_FINAL_EVIDENCE_RUN_MANIFEST": str(snapshot.run_manifest_path),
            "P6_FINAL_EVIDENCE_RUN_MANIFEST_SHA256": snapshot.run_manifest_file_sha256,
            "P6_CUTOVER_LEASE": str(lease_path),
            "P6_PRODUCTION_STARTUP_SNAPSHOT": str(snapshot.path),
            "P6_PRODUCTION_STARTUP_SNAPSHOT_SHA256": snapshot.file_sha256,
            "P6_BUILD_IDENTITY_MANIFEST": str(snapshot.build_identity_manifest_path),
            "P6_BUILD_IDENTITY_MANIFEST_SHA256": snapshot.build_identity_manifest_sha256,
            "P6_DATABASE_IDENTITY_MANIFEST_SHA256": (
                snapshot.database_identity_manifest_sha256
            ),
            "REQUIRED_SCHEMA_REVISION": "20260807_03",
        }
        processes = self.start_python_roles(
            snapshot,
            runtime_environment=runtime_environment,
        )
        return self.smoke_python(processes)

    def stop_watchdog(self, _cutover_lease: str | os.PathLike[str]) -> None:
        _stop_watchdog_pid(_cutover_lease, timeout=self._stop_timeout_seconds)

    def drain_python_roles(self, process_set: RuntimeProcessSet) -> DrainEvidence:
        self._require_active_set(process_set)
        stopped = tuple(process.role for process in reversed(process_set.processes))
        self._stop_processes(tuple(reversed(process_set.processes)))
        zero = all(process.child.poll() is not None for process in process_set.processes)
        if not zero:
            raise NativeRuntimeError(
                "NATIVE_RUNTIME_DRAIN_FAILED",
                "One or more native runtime roles remained active after drain.",
            )
        self._remove_state()
        self._active_processes = None
        return DrainEvidence(stopped_roles=stopped, zero_processes=True)

    def status_python_roles(self) -> dict[str, object]:
        processes = self._maybe_attached_python_processes()
        if processes is None:
            return {
                "ok": True,
                "state": "stopped",
                "roles": [],
                "processes": [],
            }
        rows = [
            {
                "role": process.role,
                "pid": process.pid,
                "alive": process.child.poll() is None,
            }
            for process in processes.processes
        ]
        if not all(bool(row["alive"]) for row in rows):
            return {
                "ok": False,
                "state": "degraded",
                "roles": list(processes.roles),
                "processes": rows,
            }
        smoke = self.smoke_python(processes)
        return {
            "ok": True,
            "state": "running",
            "roles": list(processes.roles),
            "processes": rows,
            "smoke": smoke,
        }

    def stop_active_python_roles(self) -> DrainEvidence:
        processes = self._maybe_attached_python_processes()
        if processes is None:
            return DrainEvidence(stopped_roles=(), zero_processes=True)
        return self.drain_python_roles(processes)

    def clear_authorization(self) -> None:
        # Authorization and its consumed marker are immutable evidence. Owner/lease
        # state closes admission; rollback never deletes either record.
        return None

    def drain_python_ingress(self) -> None:
        self._maybe_attached_python_processes()

    def drain_worker_claims(self) -> None:
        self._stop_role_names({"worker"})

    def stop_scheduler_obsidian_mcp(self) -> None:
        self._stop_role_names({"scheduler", "mcp"})

    def stop_fastapi(self) -> None:
        self._stop_role_names({"api"})

    def release_locks_connections(self) -> None:
        processes = self._maybe_attached_python_processes()
        if processes is None:
            return
        if any(process.child.poll() is None for process in processes.processes):
            raise NativeRuntimeError(
                "NATIVE_RUNTIME_RELEASE_FAILED",
                "Python processes still hold runtime resources.",
            )
        self._remove_state()
        self._active_processes = None

    def start_frozen_node(self, rollback_map: Mapping[str, object]) -> RuntimeProcess:
        validated = validate_frozen_node_rollback_map(dict(rollback_map))
        self._verify_native_rollback_map(validated)
        if self._active_frozen_node is not None or self._node_state_path.exists():
            raise NativeRuntimeError(
                "NATIVE_ROLLBACK_ALREADY_ACTIVE",
                "The frozen Node runtime already has durable process state.",
            )
        ports = validated["ports"]
        assert isinstance(ports, dict)
        request = RuntimeLaunchRequest(
            role="frozen-node",
            argv=self._configuration.rollback.argv,
            cwd=self._configuration.rollback.cwd,
            environment={
                **os.environ,
                **self._configuration.rollback.environment,
                "HOST": str(validated["host"]),
                "PORT": str(ports["api"]),
                "DB_PATH": str(validated["databasePath"]),
            },
        )
        child = self._launcher.start(request)
        if (
            not isinstance(child.pid, int)
            or isinstance(child.pid, bool)
            or child.pid <= 0
            or child.poll() is not None
        ):
            raise NativeRuntimeError(
                "NATIVE_ROLLBACK_START_FAILED",
                "The frozen Node rollback process did not stay active.",
            )
        process = RuntimeProcess(
            role=request.role,
            argv=request.argv,
            cwd=request.cwd,
            environment=request.environment,
            child=child,
        )
        unsigned = {
            "schemaVersion": 1,
            "stateKind": "native-frozen-node-runtime",
            "deploymentKind": "native-windows",
            "buildId": self._build_identity.build_id,
            "pid": process.pid,
            "argv": list(process.argv),
            "cwd": str(process.cwd),
            "rollbackMapSha256": hashlib.sha256(
                canonical_json_bytes(validated)
            ).hexdigest(),
        }
        payload = canonical_json_bytes(
            {
                **unsigned,
                "stateSha256": hashlib.sha256(canonical_json_bytes(unsigned)).hexdigest(),
            }
        )
        self._state_directory.mkdir(parents=True, exist_ok=True)
        try:
            exclusive_write_bytes(self._node_state_path, payload)
        except DatabaseIdentityError as error:
            if child.poll() is None:
                child.terminate()
                child.wait(timeout=self._stop_timeout_seconds)
            raise NativeRuntimeError(error.code, str(error)) from error
        self._active_frozen_node = process
        self._node_state_payload = payload
        self._active_rollback_map = validated
        return process

    def frozen_node_rollback_map_from_owner(
        self,
        owner_marker: str | os.PathLike[str],
    ) -> dict[str, object]:
        return self._frozen_node_rollback_map_from_owner(
            owner_marker,
            require_live=False,
        )

    def frozen_node_rollback_map_from_stale_owner_for_reattestation(
        self,
        owner_marker: str | os.PathLike[str],
    ) -> dict[str, object]:
        return self._frozen_node_rollback_map_from_owner(
            owner_marker,
            require_live=False,
            allow_p4_relative_entrypoint=True,
        )

    def frozen_node_rollback_map_from_active_owner(
        self,
        owner_marker: str | os.PathLike[str],
    ) -> dict[str, object]:
        """Export the exact frozen map while the attested Node owner is live."""
        return self._frozen_node_rollback_map_from_owner(
            owner_marker,
            require_live=True,
        )

    def _frozen_node_rollback_map_from_owner(
        self,
        owner_marker: str | os.PathLike[str],
        *,
        require_live: bool,
        allow_p4_relative_entrypoint: bool = False,
    ) -> dict[str, object]:
        from backend.app.cli.runtime_owner import _strict_owner_document
        from backend.app.providers.runtime_lease import runtime_pid_is_alive

        error_code = (
            "NATIVE_ACTIVE_OWNER_INVALID"
            if require_live
            else "NATIVE_STALE_OWNER_INVALID"
        )
        try:
            owner_path = Path(owner_marker).expanduser().resolve(strict=True)
            owner = _strict_owner_document(owner_path.read_bytes())
        except Exception as error:
            raise NativeRuntimeError(
                error_code,
                "The Node owner marker is invalid.",
            ) from error
        configured = self._configuration.rollback
        database_paths = owner.get("databasePaths")
        listener_port = owner.get("listenerPort")
        old_pid = owner.get("processId")
        owner_argv = tuple(owner.get("argv", ()))
        p4_relative_argv = (
            allow_p4_relative_entrypoint
            and configured.cwd == configured.entrypoint_path.parent
            and owner_argv
            == (str(configured.executable_path), configured.entrypoint_path.name)
        )
        if (
            owner.get("ownerState") != "node_active"
            or owner.get("runtimeNamespace") != "production"
            or owner.get("executablePath") != str(configured.executable_path)
            or owner.get("entrypointPath") != str(configured.entrypoint_path)
            or owner.get("cwd") != str(configured.cwd)
            or (owner_argv != configured.argv and not p4_relative_argv)
            or owner.get("listenerHost") != "127.0.0.1"
            or not isinstance(listener_port, int)
            or isinstance(listener_port, bool)
            or not 1 <= listener_port <= 65535
            or not isinstance(database_paths, list)
            or len(database_paths) != 1
            or not isinstance(old_pid, int)
            or isinstance(old_pid, bool)
            or old_pid <= 0
            or runtime_pid_is_alive(old_pid) is not require_live
        ):
            raise NativeRuntimeError(
                error_code,
                "The owner is not one exact frozen Node runtime in the required state.",
            )
        database_path = Path(str(database_paths[0])).resolve(strict=True)
        rollback_map = {
            "deploymentKind": "native-windows",
            "executablePath": str(configured.executable_path),
            "executableSha256": hashlib.sha256(
                configured.executable_path.read_bytes()
            ).hexdigest(),
            "entrypointPath": str(configured.entrypoint_path),
            "entrypointSha256": hashlib.sha256(
                configured.entrypoint_path.read_bytes()
            ).hexdigest(),
            "cwd": str(configured.cwd),
            "host": "127.0.0.1",
            "ports": {"api": listener_port},
            "databasePath": str(database_path),
            "environment": dict(configured.environment),
        }
        return validate_frozen_node_rollback_map(rollback_map)

    def attach_frozen_node(self, rollback_map: Mapping[str, object]) -> RuntimeProcess:
        validated = validate_frozen_node_rollback_map(dict(rollback_map))
        self._verify_native_rollback_map(validated)
        if self._active_frozen_node is None:
            self._attach_frozen_node_state(validated)
        if (
            self._active_frozen_node is None
            or self._active_frozen_node.child.poll() is not None
            or self._active_rollback_map != validated
        ):
            raise NativeRuntimeError(
                "NATIVE_ROLLBACK_ATTACHMENT_INVALID",
                "The frozen Node process cannot be attached with this rollback map.",
            )
        return self._active_frozen_node

    def stop_frozen_node(self, handle: object) -> None:
        if handle is not self._active_frozen_node or not isinstance(handle, RuntimeProcess):
            raise NativeRuntimeError(
                "NATIVE_ROLLBACK_HANDLE_INVALID",
                "Frozen Node cleanup requires the active process handle.",
            )
        self._stop_processes((handle,))
        if handle.child.poll() is None:
            raise NativeRuntimeError(
                "NATIVE_ROLLBACK_DRAIN_FAILED",
                "The frozen Node process remained active after cleanup.",
            )
        if self._node_state_payload is None:
            raise NativeRuntimeError(
                "NATIVE_ROLLBACK_STATE_MISMATCH",
                "The frozen Node durable state is missing.",
            )
        try:
            current = self._node_state_path.read_bytes()
        except FileNotFoundError as error:
            raise NativeRuntimeError(
                "NATIVE_ROLLBACK_STATE_MISMATCH",
                "The frozen Node durable state disappeared before cleanup.",
            ) from error
        if current != self._node_state_payload:
            raise NativeRuntimeError(
                "NATIVE_ROLLBACK_STATE_MISMATCH",
                "The frozen Node durable state changed before cleanup.",
            )
        self._node_state_path.unlink()
        self._active_frozen_node = None
        self._node_state_payload = None
        self._active_rollback_map = None

    def quiesce_node(self) -> dict[str, object]:
        marker = _configured_value(self._configuration, "PRODUCTION_OWNER_MARKER")
        if marker is None:
            raise NativeRuntimeError(
                "NATIVE_OWNER_MARKER_REQUIRED",
                "Native quiesce requires PRODUCTION_OWNER_MARKER in the role environment.",
            )
        from backend.app.cli.runtime_owner import read_node_active_owner_marker
        from backend.app.providers.runtime_lease import WindowsRuntimeInspector

        owner = read_node_active_owner_marker(marker)
        database = _configured_value(self._configuration, "DB_PATH")
        if database is None:
            raise NativeRuntimeError(
                "NATIVE_DATABASE_PATH_REQUIRED",
                "Native quiesce requires DB_PATH in the role environment.",
            )
        inspector = WindowsRuntimeInspector(
            expected_entrypoint_path=self._configuration.rollback.entrypoint_path,
            tracked_database_paths=(database,),
        )
        before = inspector.snapshot()
        matches = tuple(
            process for process in before.node_processes if process.pid == owner.process_id
        )
        if len(matches) != 1:
            raise NativeRuntimeError(
                "NATIVE_NODE_OWNER_MISMATCH",
                "The owner marker does not identify one running frozen Node process.",
            )
        foreign_database_handles = tuple(
            pid
            for pid in getattr(before, "database_handle_pids", ())
            if pid != owner.process_id
        )
        if foreign_database_handles:
            raise NativeRuntimeError(
                "NATIVE_NODE_QUIESCE_FAILED",
                "A process outside the frozen Node owner still holds the Live database.",
            )
        _terminate_pid(owner.process_id, timeout=self._stop_timeout_seconds)
        after = inspector.snapshot()
        remaining_database_handles = tuple(
            getattr(after, "database_handle_pids", ())
        )
        if after.node_processes or after.live_python_roles or remaining_database_handles:
            raise NativeRuntimeError(
                "NATIVE_NODE_QUIESCE_FAILED",
                "Runtime process, port, or database handles remain after Node quiesce.",
            )
        if self._node_state_path.exists():
            payload = self._node_state_path.read_bytes()
            _validate_runtime_state(payload, expected_kind="native-frozen-node-runtime")
            self._node_state_path.unlink()
        self._active_frozen_node = None
        self._node_state_payload = None
        self._active_rollback_map = None
        return {
            "zeroPidPortDatabaseHandles": True,
            "stoppedProcessId": owner.process_id,
            "databaseHandlePids": list(remaining_database_handles),
        }

    def smoke_legacy(self, handle: object) -> dict[str, object]:
        if handle is not self._active_frozen_node or not isinstance(handle, RuntimeProcess):
            raise NativeRuntimeError(
                "NATIVE_ROLLBACK_HANDLE_INVALID",
                "Legacy smoke requires the active frozen Node handle.",
            )
        if handle.child.poll() is not None:
            raise NativeRuntimeError(
                "NATIVE_ROLLBACK_EXITED",
                "The frozen Node process exited before legacy smoke.",
            )
        evidence = self._legacy_probe(handle)
        if not isinstance(evidence, Mapping) or evidence.get("ok") is not True:
            raise NativeRuntimeError(
                "NATIVE_ROLLBACK_SMOKE_FAILED",
                "The frozen Node legacy smoke failed.",
            )
        return dict(evidence)

    def _verify_native_rollback_map(self, value: Mapping[str, object]) -> None:
        configured = self._configuration.rollback
        if (
            value.get("deploymentKind") != "native-windows"
            or Path(str(value.get("executablePath"))) != configured.executable_path
            or Path(str(value.get("entrypointPath"))) != configured.entrypoint_path
            or Path(str(value.get("cwd"))) != configured.cwd
            or tuple(configured.argv)
            != (str(configured.executable_path), str(configured.entrypoint_path))
            or dict(value.get("environment", {})) != dict(configured.environment)
        ):
            raise NativeRuntimeError(
                "NATIVE_ROLLBACK_IDENTITY_MISMATCH",
                "The rollback map does not match the frozen native runtime identity.",
            )

    def _ensure_attached_python_processes(self) -> RuntimeProcessSet:
        if self._active_processes is not None:
            return self._active_processes
        try:
            payload = self._state_path.read_bytes()
        except FileNotFoundError as error:
            raise NativeRuntimeError(
                "NATIVE_RUNTIME_STATE_MISSING",
                "No durable native Python runtime state exists.",
            ) from error
        document = _validate_runtime_state(payload, expected_kind="native-python-runtime")
        if (
            document.get("buildId") != self._build_identity.build_id
            or document.get("buildIdentityManifestPath")
            != str(self._build_identity.manifest_path)
            or document.get("buildIdentityManifestSha256")
            != self._build_identity.manifest_file_sha256
        ):
            raise NativeRuntimeError(
                "NATIVE_RUNTIME_STATE_MISMATCH",
                "The durable Python runtime state has another build identity.",
            )
        try:
            snapshot = load_production_startup_snapshot(
                str(document["startupSnapshotPath"]),
                expected_file_sha256=str(document["startupSnapshotSha256"]),
            )
            self._verify_startup_snapshot(snapshot)
        except Exception as error:
            raise NativeRuntimeError(
                "NATIVE_RUNTIME_STATE_MISMATCH",
                "The durable Python runtime startup snapshot is invalid.",
            ) from error
        active_environment = self._active_owner_environment(snapshot)
        rows = document.get("processes")
        if not isinstance(rows, list) or tuple(row.get("role") for row in rows) != _ROLES:
            raise NativeRuntimeError(
                "NATIVE_RUNTIME_STATE_MISMATCH",
                "The durable Python runtime process set is invalid.",
            )
        processes: list[RuntimeProcess] = []
        attached: list[_AttachedChild] = []
        try:
            self._attach_python_processes(rows, active_environment, processes, attached)
        except BaseException:
            # A partial attach must release every process handle it opened.
            for opened in attached:
                opened.close()
            raise
        self._active_processes = RuntimeProcessSet(tuple(processes))
        self._state_payload = payload
        return self._active_processes

    def _attach_python_processes(
        self,
        rows: list[object],
        active_environment: Mapping[str, str],
        processes: list[RuntimeProcess],
        attached: list[_AttachedChild],
    ) -> None:
        for row, role_config in zip(rows, self._configuration.roles, strict=True):
            if not isinstance(row, dict):
                raise NativeRuntimeError(
                    "NATIVE_RUNTIME_STATE_MISMATCH",
                    "A durable Python runtime process record is invalid.",
                )
            pid = row.get("pid")
            argv = row.get("argv")
            cwd = row.get("cwd")
            if (
                not isinstance(pid, int)
                or isinstance(pid, bool)
                or pid <= 0
                or not isinstance(argv, list)
                or any(not isinstance(item, str) for item in argv)
                or not isinstance(cwd, str)
            ):
                raise NativeRuntimeError(
                    "NATIVE_RUNTIME_STATE_MISMATCH",
                    "A durable Python runtime process record is invalid.",
                )
            expected_argv = self._role_launch_argv(role_config)
            if tuple(argv) != tuple(expected_argv) or Path(cwd) != self._configuration.application_cwd:
                raise NativeRuntimeError(
                    "NATIVE_RUNTIME_STATE_MISMATCH",
                    "A durable Python runtime process does not match its frozen command.",
                )
            child: _AttachedChild | None = None
            try:
                child = _AttachedChild(pid)
                if os.name == "nt":
                    executable, actual_cwd, actual_argv = child.process_metadata()
                    if (
                        executable != self._configuration.python_executable_path
                        or actual_cwd != self._configuration.application_cwd
                        or not actual_argv
                        or Path(actual_argv[0]).expanduser().resolve(strict=False)
                        != self._configuration.python_executable_path
                        or tuple(actual_argv[1:]) != tuple(expected_argv[1:])
                    ):
                        raise NativeRuntimeError(
                            "NATIVE_RUNTIME_STATE_MISMATCH",
                            "An attached process does not match its frozen executable, cwd, and argv.",
                        )
            except NativeRuntimeError:
                if child is not None:
                    child.close()
                raise
            except Exception as error:
                if child is not None:
                    child.close()
                raise NativeRuntimeError(
                    "NATIVE_RUNTIME_STATE_MISMATCH",
                    "An attached process identity could not be verified.",
                ) from error
            assert child is not None
            attached.append(child)
            processes.append(
                RuntimeProcess(
                    role=str(row["role"]),
                    argv=tuple(argv),
                    cwd=Path(cwd),
                    environment={
                        **os.environ,
                        **role_config.environment,
                        **active_environment,
                    },
                    child=child,
                )
            )

    def _active_owner_environment(
        self,
        snapshot: ProductionStartupSnapshot,
    ) -> dict[str, str]:
        marker_value = _configured_value(self._configuration, "PRODUCTION_OWNER_MARKER")
        if marker_value is None:
            return {}
        try:
            marker_path = Path(marker_value).expanduser().resolve(strict=True)
            marker_payload = marker_path.read_bytes()
            preliminary = json.loads(marker_payload.decode("utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            raise NativeRuntimeError(
                "NATIVE_RUNTIME_OWNER_INVALID",
                "The production owner marker could not be read during attach.",
            ) from error
        if not isinstance(preliminary, dict) or preliminary.get("ownerState") != "python_active":
            return {}
        from backend.app.application.runtime_handoff import (
            _load_runtime_owner,
            load_handoff_receipt,
        )
        from backend.app.config import DatabaseSettings
        from backend.app.runtime import ProductionRuntimeGuard

        try:
            owner = _load_runtime_owner(marker_payload)
            receipt = load_handoff_receipt(
                str(owner["handoffReceiptPath"]),
                expected_file_sha256=str(owner["handoffReceiptFileSha256"]),
            )
            ProductionRuntimeGuard().validate_active_owner(
                handoff_receipt=receipt.path,
                expected_handoff_receipt_sha256=receipt.file_sha256,
                owner_marker=marker_path,
                database=DatabaseSettings(str(snapshot.rollback_map["databasePath"])),
                environment="live",
                runtime_namespace="production",
                role="api",
            )
        except Exception as error:
            raise NativeRuntimeError(
                "NATIVE_RUNTIME_OWNER_INVALID",
                "The active production owner identity chain failed during attach.",
            ) from error
        return {
            "P6_HANDOFF_RECEIPT": str(receipt.path),
            "P6_HANDOFF_RECEIPT_SHA256": receipt.file_sha256,
        }

    def _stop_role_names(self, roles: set[str]) -> None:
        processes = self._maybe_attached_python_processes()
        if processes is None:
            return
        selected = tuple(
            process for process in reversed(processes.processes) if process.role in roles
        )
        self._stop_processes(selected)

    def _role_launch_argv(self, role_config: NativeRoleConfiguration) -> tuple[str, ...]:
        if role_config.role not in _PROCESS_ROLES:
            return tuple(role_config.argv)
        return (
            *role_config.argv,
            "--study-app-role",
            role_config.role,
            "--study-app-environment",
            "live",
        )

    def _live_frozen_role_process_ids(self) -> tuple[int, ...]:
        """Report live processes still carrying a frozen native role identity.

        The mcp role never carries the `--study-app-role` marker the runtime
        inspector matches on, so only executable, cwd, and exact argv can prove
        every role stopped.
        """
        if os.name != "nt":
            return ()
        from backend.app.providers.runtime_lease import (
            _windows_process_ids,
            _windows_process_metadata,
        )

        expected = {
            tuple(self._role_launch_argv(role_config)[1:])
            for role_config in self._configuration.roles
        }
        surviving: list[int] = []
        for pid in _windows_process_ids():
            try:
                executable, cwd, argv = _windows_process_metadata(pid)
            except (OSError, RuntimeError, ValueError):
                continue
            if (
                executable == self._configuration.python_executable_path
                and cwd == self._configuration.application_cwd
                and tuple(argv[1:]) in expected
            ):
                surviving.append(pid)
        return tuple(surviving)

    def _maybe_attached_python_processes(self) -> RuntimeProcessSet | None:
        if self._active_processes is not None or self._state_path.exists():
            return self._ensure_attached_python_processes()
        if os.name == "nt":
            from backend.app.providers.runtime_lease import WindowsRuntimeInspector

            database = _configured_value(self._configuration, "DB_PATH")
            if database is None:
                raise NativeRuntimeError(
                    "NATIVE_DATABASE_PATH_REQUIRED",
                    "Native rollback cannot prove zero Python roles without DB_PATH.",
                )
            snapshot = WindowsRuntimeInspector(
                expected_entrypoint_path=self._configuration.rollback.entrypoint_path,
                tracked_database_paths=(database,),
            ).snapshot()
            if snapshot.live_python_roles or self._live_frozen_role_process_ids():
                raise NativeRuntimeError(
                    "NATIVE_RUNTIME_STATE_MISSING",
                    "Live Python roles exist without durable native runtime state.",
                )
        return None

    def _attach_frozen_node_state(self, validated: dict[str, object]) -> None:
        try:
            payload = self._node_state_path.read_bytes()
        except FileNotFoundError as error:
            raise NativeRuntimeError(
                "NATIVE_ROLLBACK_ATTACHMENT_INVALID",
                "No durable frozen Node process state exists.",
            ) from error
        document = _validate_runtime_state(
            payload,
            expected_kind="native-frozen-node-runtime",
        )
        expected_map_sha = hashlib.sha256(canonical_json_bytes(validated)).hexdigest()
        pid = document.get("pid")
        if (
            document.get("buildId") != self._build_identity.build_id
            or document.get("rollbackMapSha256") != expected_map_sha
            or not isinstance(pid, int)
            or isinstance(pid, bool)
            or pid <= 0
        ):
            raise NativeRuntimeError(
                "NATIVE_ROLLBACK_ATTACHMENT_INVALID",
                "The durable frozen Node state identity is invalid.",
            )
        ports = validated["ports"]
        assert isinstance(ports, dict)
        self._active_frozen_node = RuntimeProcess(
            role="frozen-node",
            argv=self._configuration.rollback.argv,
            cwd=self._configuration.rollback.cwd,
            environment={
                **self._configuration.rollback.environment,
                "HOST": str(validated["host"]),
                "PORT": str(ports["api"]),
                "DB_PATH": str(validated["databasePath"]),
            },
            child=_AttachedChild(pid),
        )
        self._node_state_payload = payload
        self._active_rollback_map = validated

    def _verify_startup_snapshot(self, snapshot: ProductionStartupSnapshot) -> None:
        if (
            not isinstance(snapshot, ProductionStartupSnapshot)
            or snapshot.build_identity_manifest_path != self._build_identity.manifest_path
            or snapshot.build_identity_manifest_sha256
            != self._build_identity.manifest_file_sha256
            or snapshot.build_id != self._build_identity.build_id
        ):
            raise NativeRuntimeError(
                "NATIVE_RUNTIME_IDENTITY_MISMATCH",
                "The startup snapshot does not bind the native build identity.",
            )

    def _require_active_set(self, process_set: RuntimeProcessSet) -> None:
        if not isinstance(process_set, RuntimeProcessSet) or process_set is not self._active_processes:
            raise NativeRuntimeError(
                "NATIVE_RUNTIME_PROCESS_SET_INVALID",
                "The process set is not the active native runtime.",
            )

    def _process_state_payload(
        self,
        processes: RuntimeProcessSet,
        snapshot: ProductionStartupSnapshot,
    ) -> bytes:
        unsigned = {
            "schemaVersion": 1,
            "stateKind": "native-python-runtime",
            "deploymentKind": "native-windows",
            "buildIdentityManifestPath": str(self._build_identity.manifest_path),
            "buildIdentityManifestSha256": self._build_identity.manifest_file_sha256,
            "buildId": self._build_identity.build_id,
            "startupSnapshotPath": str(snapshot.path),
            "startupSnapshotSha256": snapshot.file_sha256,
            "processes": [
                {
                    "role": process.role,
                    "pid": process.pid,
                    "argv": list(process.argv),
                    "cwd": str(process.cwd),
                }
                for process in processes.processes
            ],
        }
        digest = hashlib.sha256(canonical_json_bytes(unsigned)).hexdigest()
        return canonical_json_bytes({**unsigned, "stateSha256": digest})

    def _stop_processes(self, processes: tuple[RuntimeProcess, ...]) -> None:
        requests = self._create_process_role_stop_requests(processes)
        deadline = time.monotonic() + self._stop_timeout_seconds
        failures: list[str] = []
        try:
            for process in processes:
                if process.role == "mcp" and process.child.poll() is None:
                    self._force_stop_child(process.child)
            for process in processes:
                if process.child.poll() is not None:
                    continue
                remaining = max(0.0, deadline - time.monotonic())
                try:
                    process.child.wait(timeout=remaining)
                except subprocess.TimeoutExpired:
                    self._force_stop_child(process.child)
                    try:
                        process.child.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        failures.append(process.role)
        finally:
            self._remove_process_role_stop_requests(requests)
        if failures:
            raise NativeRuntimeError(
                "NATIVE_RUNTIME_DRAIN_TIMEOUT",
                "Native runtime roles did not stop in time: " + ", ".join(failures),
            )

    def _create_process_role_stop_requests(
        self,
        processes: tuple[RuntimeProcess, ...],
    ) -> tuple[tuple[Path, bytes], ...]:
        roles = {process.role for process in processes} & _PROCESS_ROLES
        if not roles:
            return ()
        identity_value = _configured_value(
            self._configuration,
            "DATABASE_IDENTITY_MANIFEST",
        )
        lease_value = _configured_value(self._configuration, "RUNTIME_LEASE_DIR")
        if identity_value is None or lease_value is None:
            for process in processes:
                if process.child.poll() is None:
                    process.child.terminate()
            return ()
        from backend.app.api.compat.database_identity import (
            load_database_evidence_identity_manifest,
        )
        from backend.app.providers.runtime_lease import candidate_runtime_drain_request

        try:
            identity = load_database_evidence_identity_manifest(identity_value)
        except Exception:
            for process in processes:
                if process.child.poll() is None:
                    process.child.terminate()
            return ()
        requests: list[tuple[Path, bytes]] = []
        try:
            for role in _ROLES:
                if role not in roles:
                    continue
                path, payload = candidate_runtime_drain_request(
                    lease_value,
                    identity,
                    runtime_namespace="production",
                    role=role,
                )
                exclusive_write_bytes(path, payload)
                requests.append((path, payload))
        except Exception:
            self._remove_process_role_stop_requests(tuple(requests))
            for process in processes:
                if process.child.poll() is None:
                    process.child.terminate()
            return ()
        return tuple(requests)

    @staticmethod
    def _remove_process_role_stop_requests(
        requests: tuple[tuple[Path, bytes], ...],
    ) -> None:
        for path, expected_payload in reversed(requests):
            try:
                payload = path.read_bytes()
            except FileNotFoundError:
                continue
            if payload != expected_payload:
                raise NativeRuntimeError(
                    "NATIVE_RUNTIME_DRAIN_REQUEST_INVALID",
                    "A native runtime drain request changed before cleanup.",
                )
            path.unlink()

    @staticmethod
    def _force_stop_child(child: RuntimeChild) -> None:
        killer = getattr(child, "kill", None)
        if callable(killer):
            killer()
            return
        child.terminate()

    def _remove_state(self) -> None:
        if self._state_payload is None:
            return
        try:
            current = self._state_path.read_bytes()
        except FileNotFoundError as error:
            raise NativeRuntimeError(
                "NATIVE_RUNTIME_STATE_MISMATCH",
                "The durable native runtime state disappeared before drain.",
            ) from error
        if current != self._state_payload:
            raise NativeRuntimeError(
                "NATIVE_RUNTIME_STATE_MISMATCH",
                "The durable native runtime state changed before drain.",
            )
        self._state_path.unlink()
        self._state_payload = None

    def _read_role_locks(self, _processes: RuntimeProcessSet) -> Mapping[str, str]:
        lease_root = Path(
            next(
                role.environment["RUNTIME_LEASE_DIR"]
                for role in self._configuration.roles
                if role.role == "worker"
            )
        ).expanduser().resolve(strict=True)
        locks: dict[str, str] = {}
        for path in sorted(lease_root.glob("*.json")):
            payload = path.read_bytes()
            try:
                document = json.loads(payload.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                continue
            role = document.get("role") if isinstance(document, dict) else None
            if (
                role in {"worker", "scheduler"}
                and document.get("leaseKind") == "runtime-role"
                and document.get("environment") == "live"
                and document.get("runtimeNamespace") == "production"
            ):
                locks[str(role)] = hashlib.sha256(payload).hexdigest()
        return locks

    def _ensure_role_lease_directory(self) -> Path:
        configured = _configured_value(self._configuration, "RUNTIME_LEASE_DIR")
        if configured is None:
            raise NativeRuntimeError(
                "NATIVE_RUNTIME_LEASE_DIRECTORY_INVALID",
                "All native process roles must bind one runtime lease directory.",
            )
        path = Path(configured).expanduser().resolve(strict=False)
        try:
            path.mkdir(parents=True, exist_ok=True)
            resolved = path.resolve(strict=True)
        except OSError as error:
            raise NativeRuntimeError(
                "NATIVE_RUNTIME_LEASE_DIRECTORY_INVALID",
                "The native runtime lease directory could not be prepared.",
            ) from error
        if not resolved.is_dir():
            raise NativeRuntimeError(
                "NATIVE_RUNTIME_LEASE_DIRECTORY_INVALID",
                "The native runtime lease path is not a directory.",
            )
        return resolved


def load_native_runtime_configuration(
    native_runtime_spec: str | os.PathLike[str],
) -> NativeRuntimeConfiguration:
    path = Path(native_runtime_spec).expanduser().resolve(strict=True)
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise NativeRuntimeError(
            "NATIVE_RUNTIME_SPEC_INVALID",
            "The native runtime specification is not valid JSON.",
        ) from error
    expected = {
        "schemaVersion",
        "deploymentKind",
        "pythonExecutablePath",
        "requirementsLockPath",
        "applicationCwd",
        "roles",
        "frozenNodeRollback",
    }
    if (
        not isinstance(document, dict)
        or set(document) != expected
        or document.get("schemaVersion") != 1
        or document.get("deploymentKind") != "native-windows"
    ):
        raise NativeRuntimeError(
            "NATIVE_RUNTIME_SPEC_INVALID",
            "The native runtime specification schema is invalid.",
        )
    python = _existing_file(document["pythonExecutablePath"], "Python executable")
    requirements = _existing_file(document["requirementsLockPath"], "requirements lock")
    cwd = _existing_directory(document["applicationCwd"], "application cwd")
    roles = document["roles"]
    if not isinstance(roles, dict) or tuple(roles) != _ROLES:
        raise NativeRuntimeError(
            "NATIVE_RUNTIME_SPEC_INVALID",
            "Native roles must be ordered api, worker, scheduler, mcp.",
        )
    parsed_roles: list[NativeRoleConfiguration] = []
    for role in _ROLES:
        value = roles[role]
        if not isinstance(value, dict) or set(value) != {"argv", "environment"}:
            raise NativeRuntimeError(
                "NATIVE_RUNTIME_SPEC_INVALID",
                f"The native {role} role is invalid.",
            )
        argv = _argv(value["argv"])
        if Path(argv[0]).expanduser().resolve(strict=False) != python:
            raise NativeRuntimeError(
                "NATIVE_RUNTIME_SPEC_INVALID",
                f"The native {role} role does not use the frozen Python executable.",
            )
        if argv != _native_role_argv(python, role):
            raise NativeRuntimeError(
                "NATIVE_RUNTIME_SPEC_INVALID",
                f"The native {role} role command does not match the frozen contract.",
            )
        parsed_roles.append(
            NativeRoleConfiguration(
                role=role,
                argv=argv,
                environment=_string_mapping(value["environment"]),
            )
        )
    rollback_value = document["frozenNodeRollback"]
    if not isinstance(rollback_value, dict) or set(rollback_value) != {
        "executablePath",
        "entrypointPath",
        "cwd",
        "argv",
        "environment",
    }:
        raise NativeRuntimeError(
            "NATIVE_RUNTIME_SPEC_INVALID",
            "The frozen Node rollback configuration is invalid.",
        )
    rollback = NativeRollbackConfiguration(
        executable_path=_existing_file(rollback_value["executablePath"], "Node executable"),
        entrypoint_path=_existing_file(rollback_value["entrypointPath"], "Node entrypoint"),
        cwd=_existing_directory(rollback_value["cwd"], "Node cwd"),
        argv=_argv(rollback_value["argv"]),
        environment=_string_mapping(rollback_value["environment"]),
    )
    return NativeRuntimeConfiguration(
        spec_path=path,
        python_executable_path=python,
        requirements_lock_path=requirements,
        application_cwd=cwd,
        roles=tuple(parsed_roles),
        rollback=rollback,
    )


class SubprocessRuntimeLauncher:
    def __init__(self, *, log_directory: str | os.PathLike[str]) -> None:
        self._log_directory = Path(log_directory).expanduser().resolve(strict=False)

    def start(self, request: RuntimeLaunchRequest) -> RuntimeChild:
        if request.role not in {*_ROLES, "frozen-node"}:
            raise NativeRuntimeError(
                "NATIVE_RUNTIME_ROLE_INVALID",
                "The runtime launch role is not supported.",
            )
        self._log_directory.mkdir(parents=True, exist_ok=True)
        log_path = self._log_directory / f"{request.role}.log"
        creation_flags = (
            (
                getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
                | (
                    0
                    if request.role == "mcp"
                    else getattr(subprocess, "CREATE_NO_WINDOW", 0)
                )
            )
            if os.name == "nt"
            else 0
        )
        with log_path.open("ab", buffering=0) as log:
            return subprocess.Popen(
                list(request.argv),
                cwd=request.cwd,
                env=dict(request.environment),
                stdin=None if request.role == "mcp" else subprocess.DEVNULL,
                stdout=log,
                stderr=subprocess.STDOUT,
                creationflags=creation_flags,
            )


class NativeFinalWindowWatchdogLauncher:
    def __init__(
        self,
        *,
        python_executable: str | os.PathLike[str],
        operations_factory: str = (
            "backend.app.providers.native_runtime:create_operations"
        ),
    ) -> None:
        self._python = Path(python_executable).expanduser().resolve(strict=True)
        self._operations_factory = operations_factory
        self._process: subprocess.Popen[bytes] | None = None

    def start(self, lease_path: Path, token_path: Path) -> int:
        if self._process is not None and self._process.poll() is None:
            raise NativeRuntimeError(
                "NATIVE_WATCHDOG_ALREADY_ACTIVE",
                "A native final-window watchdog is already active.",
            )
        recovery_output = lease_path.parent / "abort-recovery.json"
        creation_flags = (
            getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) if os.name == "nt" else 0
        )
        self._process = subprocess.Popen(
            [
                str(self._python),
                "-B",
                "-m",
                "backend.app.cli.final_window_watchdog",
                "--cutover-lease",
                str(lease_path),
                "--cutover-token-file",
                str(token_path),
                "--recovery-output",
                str(recovery_output),
                "--operations-factory",
                self._operations_factory,
            ],
            cwd=Path.cwd(),
            env=dict(os.environ),
            creationflags=creation_flags,
        )
        if self._process.poll() is not None:
            raise NativeRuntimeError(
                "NATIVE_WATCHDOG_START_FAILED",
                "The native final-window watchdog exited during startup.",
            )
        return self._process.pid

    def stop(self, lease_path: Path) -> None:
        if self._process is not None and self._process.poll() is None:
            self._process.terminate()
            self._process.wait(timeout=10)
            return
        _stop_watchdog_pid(lease_path, timeout=10)


def create_operations() -> NativeWindowsRuntimeOperations:
    return NativeWindowsRuntimeOperations(
        native_runtime_spec=_required_environment_path("STUDY_APP_NATIVE_RUNTIME_SPEC"),
        build_identity_manifest=_required_environment_path(
            "P6_BUILD_IDENTITY_MANIFEST"
        ),
        state_directory=_required_environment_path(
            "STUDY_APP_NATIVE_RUNTIME_STATE_DIR",
            require_existing=False,
        ),
    )


def create_watchdog() -> NativeFinalWindowWatchdogLauncher:
    configuration = load_native_runtime_configuration(
        _required_environment_path("STUDY_APP_NATIVE_RUNTIME_SPEC")
    )
    return NativeFinalWindowWatchdogLauncher(
        python_executable=configuration.python_executable_path,
    )


class _AttachedChild:
    def __init__(self, pid: int) -> None:
        self.pid = pid
        self._returncode: int | None = None
        self._handle = _open_windows_process(pid) if os.name == "nt" else None

    def process_metadata(self) -> tuple[Path, Path, tuple[str, ...]]:
        if os.name != "nt" or self._handle is None:
            raise RuntimeError("attached process metadata requires Windows")
        from backend.app.providers.runtime_lease import _windows_process_metadata

        return _windows_process_metadata(self.pid)

    def poll(self) -> int | None:
        if self._returncode is not None:
            return self._returncode
        if self._handle is not None:
            if _wait_windows_process(self._handle, timeout_seconds=0):
                self._returncode = 0
                self.close()
                return self._returncode
            return None
        from backend.app.providers.runtime_lease import runtime_pid_is_alive

        return None if runtime_pid_is_alive(self.pid) else 0

    def terminate(self) -> None:
        if self.poll() is None:
            if self._handle is not None:
                _terminate_windows_process(self._handle)
            else:
                os.kill(self.pid, signal.SIGTERM)

    def kill(self) -> None:
        if self.poll() is not None:
            return
        if self._handle is not None:
            _terminate_windows_process(self._handle)
            return
        os.kill(self.pid, signal.SIGKILL)

    def wait(self, timeout: float | None = None) -> int:
        if self._returncode is not None:
            return self._returncode
        if self._handle is not None:
            if not _wait_windows_process(self._handle, timeout_seconds=timeout):
                raise subprocess.TimeoutExpired(str(self.pid), timeout)
            self._returncode = 0
            self.close()
            return self._returncode
        deadline = time.monotonic() + (30.0 if timeout is None else timeout)
        while self.poll() is None:
            if time.monotonic() >= deadline:
                raise subprocess.TimeoutExpired(str(self.pid), timeout)
            time.sleep(0.05)
        return 0

    def close(self) -> None:
        if self._handle is None:
            return
        handle = self._handle
        self._handle = None
        _close_windows_process(handle)

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            pass


def _probe_mcp_tools_list(
    process: RuntimeProcess,
    timeout: float,
) -> Mapping[str, object]:
    environment = {
        **os.environ,
        **process.environment,
        "PAPER_STUDY_MCP_PROBE": "1",
    }
    creation_flags = (
        int(getattr(subprocess, "CREATE_NO_WINDOW", 0)) if os.name == "nt" else 0
    )
    probe_argv = [argument for argument in process.argv if argument != "--supervisor"]
    child = subprocess.Popen(
        probe_argv,
        cwd=process.cwd,
        env=environment,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        creationflags=creation_flags,
    )
    request_lines = (
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "study-native-readiness", "version": "1"},
            },
        },
        {
            "jsonrpc": "2.0",
            "method": "notifications/initialized",
            "params": {},
        },
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
    )
    request_payload = b"".join(
        json.dumps(line, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        + b"\n"
        for line in request_lines
    )
    try:
        stdout, _stderr = child.communicate(request_payload, timeout=timeout)
    except subprocess.TimeoutExpired:
        child.terminate()
        try:
            child.wait(timeout=2)
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait(timeout=2)
        return {"ok": False, "reason": "mcp-tools-list-timeout"}
    for raw_line in stdout.splitlines():
        try:
            message = json.loads(raw_line.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
        if not isinstance(message, dict) or message.get("id") != 2:
            continue
        result = message.get("result")
        tools = result.get("tools") if isinstance(result, dict) else None
        if not isinstance(tools, list):
            break
        names = sorted(
            str(tool.get("name"))
            for tool in tools
            if isinstance(tool, dict) and isinstance(tool.get("name"), str)
        )
        return {
            "ok": tuple(names) == _MCP_TOOL_NAMES,
            "toolCount": len(names),
            "toolNames": names,
        }
    return {
        "ok": False,
        "reason": "mcp-tools-list-invalid",
        "exitCode": child.returncode,
    }


def _open_windows_process(pid: int) -> object:
    import ctypes
    from ctypes import wintypes

    process_terminate = 0x0001
    process_query_information = 0x0400
    process_vm_read = 0x0010
    synchronize = 0x00100000
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    kernel32.OpenProcess.restype = wintypes.HANDLE
    handle = kernel32.OpenProcess(
        process_terminate | process_query_information | process_vm_read | synchronize,
        False,
        pid,
    )
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    return handle


def _terminate_windows_process(handle: object) -> None:
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.TerminateProcess.argtypes = (wintypes.HANDLE, wintypes.UINT)
    kernel32.TerminateProcess.restype = wintypes.BOOL
    if not kernel32.TerminateProcess(handle, 1):
        raise ctypes.WinError(ctypes.get_last_error())


def _wait_windows_process(handle: object, *, timeout_seconds: float | None) -> bool:
    import ctypes
    from ctypes import wintypes

    wait_object_0 = 0x00000000
    wait_timeout = 0x00000102
    infinite = 0xFFFFFFFF
    milliseconds = (
        infinite
        if timeout_seconds is None
        else min(0xFFFFFFFE, max(0, int(timeout_seconds * 1000)))
    )
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
    kernel32.WaitForSingleObject.restype = wintypes.DWORD
    result = int(kernel32.WaitForSingleObject(handle, milliseconds))
    if result == wait_object_0:
        return True
    if result == wait_timeout:
        return False
    raise ctypes.WinError(ctypes.get_last_error())


def _close_windows_process(handle: object) -> None:
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    kernel32.CloseHandle.restype = wintypes.BOOL
    if not kernel32.CloseHandle(handle):
        raise ctypes.WinError(ctypes.get_last_error())


def _validate_runtime_state(payload: bytes, *, expected_kind: str) -> dict[str, object]:
    try:
        document = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise NativeRuntimeError(
            "NATIVE_RUNTIME_STATE_MISMATCH",
            "The durable runtime state is invalid JSON.",
        ) from error
    if (
        not isinstance(document, dict)
        or document.get("schemaVersion") != 1
        or document.get("stateKind") != expected_kind
        or document.get("deploymentKind") != "native-windows"
        or canonical_json_bytes(document) != payload
    ):
        raise NativeRuntimeError(
            "NATIVE_RUNTIME_STATE_MISMATCH",
            "The durable runtime state schema is invalid.",
        )
    self_hash = document.get("stateSha256")
    unsigned = {key: value for key, value in document.items() if key != "stateSha256"}
    if not _is_sha256(self_hash) or hashlib.sha256(
        canonical_json_bytes(unsigned)
    ).hexdigest() != self_hash:
        raise NativeRuntimeError(
            "NATIVE_RUNTIME_STATE_MISMATCH",
            "The durable runtime state self hash is invalid.",
        )
    return document


def _configured_value(
    configuration: NativeRuntimeConfiguration,
    name: str,
) -> str | None:
    values = {
        role.environment.get(name)
        for role in configuration.roles
        if role.role in _PROCESS_ROLES and role.environment.get(name)
    }
    if len(values) != 1:
        return None
    return next(iter(values))


def _terminate_pid(pid: int, *, timeout: float) -> None:
    child = _AttachedChild(pid)
    child.terminate()
    child.wait(timeout=timeout)


def _stop_watchdog_pid(lease_path: str | os.PathLike[str], *, timeout: float) -> None:
    _path, _payload, lease = _load_lease(lease_path)
    pid = lease.get("watchdogPid")
    if (
        isinstance(pid, int)
        and not isinstance(pid, bool)
        and pid > 0
        and pid != os.getpid()
    ):
        child = _AttachedChild(pid)
        if child.poll() is None:
            child.terminate()
            child.wait(timeout=timeout)


def _required_environment_path(name: str, *, require_existing: bool = True) -> Path:
    value = os.environ.get(name)
    if not isinstance(value, str) or not value.strip():
        raise NativeRuntimeError(
            "NATIVE_RUNTIME_ENVIRONMENT_INVALID",
            f"{name} is required for native runtime operations.",
        )
    path = Path(value).expanduser().resolve(strict=require_existing)
    if require_existing and not path.exists():
        raise NativeRuntimeError(
            "NATIVE_RUNTIME_ENVIRONMENT_INVALID",
            f"{name} does not exist.",
        )
    return path


def _argv(value: object) -> tuple[str, ...]:
    if (
        not isinstance(value, list)
        or not value
        or any(not isinstance(item, str) or not item for item in value)
    ):
        raise NativeRuntimeError(
            "NATIVE_RUNTIME_SPEC_INVALID",
            "A native runtime argv is invalid.",
        )
    return tuple(value)


def _native_role_argv(python: Path, role: str) -> tuple[str, ...]:
    # The frozen build identity owns this contract; the runtime never widens it.
    return native_role_argv(python, role)


def _string_mapping(value: object) -> dict[str, str]:
    if not isinstance(value, Mapping) or any(
        not isinstance(name, str)
        or not name
        or not isinstance(item, str)
        for name, item in value.items()
    ):
        raise NativeRuntimeError(
            "NATIVE_RUNTIME_SPEC_INVALID",
            "A native runtime environment map is invalid.",
        )
    return dict(value)


def _existing_file(value: object, label: str) -> Path:
    if not isinstance(value, str) or not value:
        raise NativeRuntimeError("NATIVE_RUNTIME_SPEC_INVALID", f"{label} is required.")
    path = Path(value).expanduser().resolve(strict=True)
    if not path.is_file():
        raise NativeRuntimeError("NATIVE_RUNTIME_SPEC_INVALID", f"{label} is not a file.")
    return path


def _existing_directory(value: object, label: str) -> Path:
    if not isinstance(value, str) or not value:
        raise NativeRuntimeError("NATIVE_RUNTIME_SPEC_INVALID", f"{label} is required.")
    path = Path(value).expanduser().resolve(strict=True)
    if not path.is_dir():
        raise NativeRuntimeError("NATIVE_RUNTIME_SPEC_INVALID", f"{label} is not a directory.")
    return path


def _is_sha256(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


__all__ = [
    "DrainEvidence",
    "NativeRuntimeError",
    "NativeFinalWindowWatchdogLauncher",
    "NativeWindowsRuntimeOperations",
    "RuntimeLaunchRequest",
    "RuntimeProcess",
    "RuntimeProcessSet",
    "create_operations",
    "create_watchdog",
    "load_native_runtime_configuration",
]
