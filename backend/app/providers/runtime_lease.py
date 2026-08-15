from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import errno
import hashlib
import json
import os
from pathlib import Path
import socket
import struct

from backend.app.api.compat.database_identity import (
    DatabaseIdentityError,
    canonical_json_bytes,
    exclusive_write_bytes,
    load_database_evidence_identity_manifest,
)


@dataclass(frozen=True, slots=True)
class ProcessEvidence:
    pid: int
    executable_path: Path
    entrypoint_path: Path
    cwd: Path
    argv: tuple[str, ...]
    listener_host: str
    listener_port: int
    database_paths: tuple[Path, ...]
    process_role: str
    environment: str


@dataclass(frozen=True, slots=True)
class RuntimeProcessSnapshot:
    node_processes: tuple[ProcessEvidence, ...]
    live_python_roles: tuple[ProcessEvidence, ...] = ()
    database_handle_pids: tuple[int, ...] = ()


class RoleLeaseError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(f"{code}: {message}")


@dataclass(frozen=True, slots=True)
class RoleLeaseHandle:
    path: Path
    canonical_bytes: bytes
    _release_callback: object

    def release(self) -> None:
        callback = self._release_callback
        assert callable(callback)
        callback(self.path, self.canonical_bytes)


class RoleScopedRuntimeLease:
    """Exclusive candidate role presence lease with stale-owner fencing."""

    def __init__(
        self,
        root: str | os.PathLike[str],
        *,
        clock: callable | None = None,
        pid_probe: callable | None = None,
    ) -> None:
        self._root = Path(root).expanduser().resolve(strict=False)
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._pid_probe = pid_probe or _pid_is_alive

    def acquire(
        self,
        database_identity_manifest: str | os.PathLike[str],
        *,
        environment: str,
        runtime_namespace: str,
        role: str,
        owner_id: str,
        pid: int,
        lease_seconds: int = 30,
        production_admission: object | None = None,
    ) -> RoleLeaseHandle:
        if role not in {"worker", "scheduler"}:
            raise RoleLeaseError(
                "ROLE_LEASE_INVALID",
                "Singleton role leases require worker or scheduler.",
            )
        if environment not in {"candidate", "live"}:
            raise RoleLeaseError(
                "RUNTIME_ENVIRONMENT_INVALID",
                "Role leases require the candidate or Live environment.",
            )
        if not isinstance(runtime_namespace, str) or not runtime_namespace.strip():
            raise RoleLeaseError("RUNTIME_NAMESPACE_INVALID", "Runtime namespace is required.")
        if not isinstance(owner_id, str) or not owner_id.strip():
            raise RoleLeaseError("ROLE_LEASE_OWNER_INVALID", "owner_id is required.")
        if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
            raise RoleLeaseError("ROLE_LEASE_PID_INVALID", "pid must be positive.")
        if not isinstance(lease_seconds, int) or isinstance(lease_seconds, bool) or lease_seconds < 1:
            raise RoleLeaseError("ROLE_LEASE_DURATION_INVALID", "lease_seconds must be positive.")
        try:
            identity = load_database_evidence_identity_manifest(database_identity_manifest)
        except DatabaseIdentityError as error:
            raise RoleLeaseError(error.code, str(error)) from error
        if environment == "candidate":
            if identity.subject_kind == "live" or production_admission is not None:
                raise RoleLeaseError(
                    "P4_LIVE_PROMOTION_NOT_AUTHORIZED",
                    "A Live identity or admission cannot own a candidate role lease.",
                )
            _assert_role_admission_open(
                self._root,
                identity,
                runtime_namespace=runtime_namespace,
                role=role,
            )
        else:
            _assert_production_admission(
                production_admission,
                identity,
                runtime_namespace=runtime_namespace,
                role=role,
            )
        key_document = {
            "version": 1,
            "environment": environment,
            "databaseLineageId": identity.database_lineage_id,
            "subjectDatabaseId": identity.subject_database_id,
            "runtimeNamespace": runtime_namespace,
            "role": role,
        }
        key_hash = hashlib.sha256(canonical_json_bytes(key_document)).hexdigest()
        lease_path = self._root / f"{key_hash}.json"
        now = _lease_time(self._clock())
        unsigned = {
            "schemaVersion": 1,
            "leaseKind": "runtime-role",
            "environment": environment,
            "databaseLineageId": identity.database_lineage_id,
            "subjectDatabaseId": identity.subject_database_id,
            "runtimeNamespace": runtime_namespace,
            "role": role,
            "ownerId": owner_id,
            "pid": pid,
            "startedAt": now,
            "expiresAt": _lease_time(self._clock(), seconds=lease_seconds),
            "keyHash": key_hash,
        }
        payload = canonical_json_bytes(
            {**unsigned, "leaseSha256": hashlib.sha256(canonical_json_bytes(unsigned)).hexdigest()}
        )
        for attempt in range(2):
            try:
                exclusive_write_bytes(lease_path, payload)
                try:
                    if environment == "candidate":
                        _assert_role_admission_open(
                            self._root,
                            identity,
                            runtime_namespace=runtime_namespace,
                            role=role,
                        )
                    else:
                        _assert_production_admission(
                            production_admission,
                            identity,
                            runtime_namespace=runtime_namespace,
                            role=role,
                        )
                except RoleLeaseError:
                    _release_lease(lease_path, payload)
                    raise
                return RoleLeaseHandle(
                    path=lease_path,
                    canonical_bytes=payload,
                    _release_callback=_release_lease,
                )
            except DatabaseIdentityError as error:
                if error.code != "EVIDENCE_OUTPUT_EXISTS" or attempt:
                    conflict = _role_conflict_code(role)
                    raise RoleLeaseError(conflict, str(error)) from error
                if not _reclaim_stale_lease(
                    lease_path,
                    now=now,
                    pid_probe=self._pid_probe,
                ):
                    conflict = _role_conflict_code(role)
                    raise RoleLeaseError(conflict, "An active owner already holds the role lease.") from error
        raise AssertionError("unreachable")


def _assert_production_admission(
    admission: object,
    identity: object,
    *,
    runtime_namespace: str,
    role: str,
) -> None:
    from backend.app.runtime import (
        RuntimeRoleError,
        validate_production_runtime_admission,
    )

    if admission is None:
        raise RoleLeaseError(
            "PRODUCTION_ADMISSION_REQUIRED",
            "A typed production admission is required for a Live role lease.",
        )
    try:
        validate_production_runtime_admission(
            admission,
            identity,
            runtime_namespace=runtime_namespace,
            role=role,
        )
    except RuntimeRoleError as error:
        raise RoleLeaseError(error.code, str(error)) from error


class ApiRuntimePresence:
    """Non-singleton API presence used only to acknowledge a complete drain."""

    def __init__(
        self,
        root: str | os.PathLike[str],
        *,
        clock: callable | None = None,
    ) -> None:
        self._root = Path(root).expanduser().resolve(strict=False)
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def acquire(
        self,
        database_identity_manifest: str | os.PathLike[str],
        *,
        environment: str,
        runtime_namespace: str,
        owner_id: str,
        pid: int,
        lease_seconds: int = 30,
    ) -> RoleLeaseHandle:
        if environment != "candidate":
            raise RoleLeaseError(
                "P4_LIVE_PROMOTION_NOT_AUTHORIZED",
                "API presence cannot be acquired for a Live environment.",
            )
        if not isinstance(runtime_namespace, str) or not runtime_namespace.strip():
            raise RoleLeaseError("RUNTIME_NAMESPACE_INVALID", "Runtime namespace is required.")
        if not isinstance(owner_id, str) or not owner_id.strip():
            raise RoleLeaseError("ROLE_LEASE_OWNER_INVALID", "owner_id is required.")
        if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
            raise RoleLeaseError("ROLE_LEASE_PID_INVALID", "pid must be positive.")
        if not isinstance(lease_seconds, int) or isinstance(lease_seconds, bool) or lease_seconds < 1:
            raise RoleLeaseError("ROLE_LEASE_DURATION_INVALID", "lease_seconds must be positive.")
        try:
            identity = load_database_evidence_identity_manifest(
                database_identity_manifest
            )
        except DatabaseIdentityError as error:
            raise RoleLeaseError(error.code, str(error)) from error
        if identity.subject_kind == "live":
            raise RoleLeaseError(
                "P4_LIVE_PROMOTION_NOT_AUTHORIZED",
                "A Live database identity cannot own candidate API presence.",
            )
        _assert_role_admission_open(
            self._root,
            identity,
            runtime_namespace=runtime_namespace,
            role="api",
        )
        key_document = {
            "version": 1,
            "environment": environment,
            "databaseLineageId": identity.database_lineage_id,
            "subjectDatabaseId": identity.subject_database_id,
            "runtimeNamespace": runtime_namespace,
            "role": "api",
            "ownerId": owner_id,
            "pid": pid,
        }
        key_hash = hashlib.sha256(canonical_json_bytes(key_document)).hexdigest()
        presence_path = self._root / f"api-presence-{key_hash}.json"
        now = _lease_time(self._clock())
        unsigned = {
            "schemaVersion": 1,
            "leaseKind": "runtime-api-presence",
            "environment": environment,
            "databaseLineageId": identity.database_lineage_id,
            "subjectDatabaseId": identity.subject_database_id,
            "runtimeNamespace": runtime_namespace,
            "role": "api",
            "ownerId": owner_id,
            "pid": pid,
            "startedAt": now,
            "expiresAt": _lease_time(self._clock(), seconds=lease_seconds),
            "keyHash": key_hash,
        }
        payload = canonical_json_bytes(
            {
                **unsigned,
                "leaseSha256": hashlib.sha256(
                    canonical_json_bytes(unsigned)
                ).hexdigest(),
            }
        )
        try:
            exclusive_write_bytes(presence_path, payload)
        except DatabaseIdentityError as error:
            raise RoleLeaseError(
                "API_PRESENCE_ALREADY_EXISTS",
                str(error),
            ) from error
        try:
            _assert_role_admission_open(
                self._root,
                identity,
                runtime_namespace=runtime_namespace,
                role="api",
            )
        except RoleLeaseError:
            _release_lease(presence_path, payload)
            raise
        return RoleLeaseHandle(
            path=presence_path,
            canonical_bytes=payload,
            _release_callback=_release_lease,
        )


def candidate_runtime_drain_request(
    root: str | os.PathLike[str],
    identity: object,
    *,
    runtime_namespace: str,
    role: str | None = None,
) -> tuple[Path, bytes]:
    if role is not None and role not in {"api", "worker", "scheduler"}:
        raise RoleLeaseError(
            "ROLE_LEASE_INVALID",
            "Candidate drain requests require api, worker, or scheduler.",
        )
    document = {
        "schemaVersion": 1,
        "requestKind": "candidate-runtime-drain",
        "databaseLineageId": str(getattr(identity, "database_lineage_id")),
        "subjectDatabaseId": str(getattr(identity, "subject_database_id")),
        "runtimeNamespace": runtime_namespace,
    }
    if role is not None:
        document["role"] = role
    payload = canonical_json_bytes(document)
    key = hashlib.sha256(payload).hexdigest()
    return Path(root).expanduser().resolve(strict=False) / f"drain-{key}.request", payload


def candidate_runtime_drain_final_fence(
    root: str | os.PathLike[str],
    identity: object,
    *,
    runtime_namespace: str,
) -> tuple[Path, bytes]:
    """Return the retained admission fence for a completed candidate drain."""
    document = {
        "schemaVersion": 1,
        "requestKind": "candidate-runtime-drain-final",
        "databaseLineageId": str(getattr(identity, "database_lineage_id")),
        "subjectDatabaseId": str(getattr(identity, "subject_database_id")),
        "runtimeNamespace": runtime_namespace,
    }
    payload = canonical_json_bytes(document)
    key = hashlib.sha256(payload).hexdigest()
    return (
        Path(root).expanduser().resolve(strict=False) / f"drain-final-{key}.fence",
        payload,
    )


def _assert_role_admission_open(
    root: Path,
    identity: object,
    *,
    runtime_namespace: str,
    role: str,
) -> None:
    requests = (
        candidate_runtime_drain_final_fence(
            root,
            identity,
            runtime_namespace=runtime_namespace,
        ),
        candidate_runtime_drain_request(
            root,
            identity,
            runtime_namespace=runtime_namespace,
            role=role,
        ),
        candidate_runtime_drain_request(
            root,
            identity,
            runtime_namespace=runtime_namespace,
        ),
    )
    for request_path, expected_payload in requests:
        try:
            current_payload = request_path.read_bytes()
        except FileNotFoundError:
            continue
        except OSError as error:
            raise RoleLeaseError(
                "CANDIDATE_RUNTIME_DRAIN_REQUEST_INVALID",
                "The candidate drain fence could not be read.",
            ) from error
        if current_payload != expected_payload:
            raise RoleLeaseError(
                "CANDIDATE_RUNTIME_DRAIN_REQUEST_INVALID",
                "The candidate drain fence failed its identity contract.",
            )
        raise RoleLeaseError(
            "CANDIDATE_RUNTIME_DRAINING",
            "The candidate role is draining and cannot admit a new owner.",
        )


def _role_conflict_code(role: str) -> str:
    return {
        "api": "API_ALREADY_OWNED",
        "worker": "WORKER_ALREADY_OWNED",
        "scheduler": "SCHEDULER_ALREADY_OWNED",
    }[role]


def _release_lease(path: Path, expected: bytes) -> None:
    try:
        current = path.read_bytes()
    except FileNotFoundError:
        return
    if current != expected:
        raise RoleLeaseError(
            "ROLE_LEASE_DRIFT",
            "The role lease changed before release and will not be removed.",
        )
    try:
        path.unlink()
    except FileNotFoundError:
        return


def _reclaim_stale_lease(
    path: Path,
    *,
    now: str,
    pid_probe: callable,
) -> bool:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, ValueError):
        return False
    try:
        expires = datetime.fromisoformat(str(document["expiresAt"]).replace("Z", "+00:00"))
        pid = int(document["pid"])
        current = path.read_bytes()
    except (KeyError, TypeError, ValueError, OSError):
        return False
    if expires > datetime.fromisoformat(now.replace("Z", "+00:00")) or pid_probe(pid):
        return False
    try:
        if path.read_bytes() != current:
            return False
        path.unlink()
        return True
    except (FileNotFoundError, OSError):
        return False


def _lease_time(value: datetime, *, seconds: int = 0) -> str:
    if value.tzinfo is None:
        raise RoleLeaseError("ROLE_LEASE_CLOCK_INVALID", "Lease clock must be timezone-aware.")
    from datetime import timedelta

    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace(
        "+00:00", "Z"
    ) if seconds == 0 else (value.astimezone(timezone.utc) + timedelta(seconds=seconds)).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _pid_is_alive(pid: int) -> bool:
    if os.name != "nt":
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        except OSError as error:
            return error.errno != errno.ESRCH
        return True
    import ctypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    process = kernel32.OpenProcess(
        0x1000,
        False,
        pid,
    )
    if not process:
        # ERROR_INVALID_PARAMETER is how OpenProcess reports a PID that does
        # not exist. Access-denied and indeterminate failures must fail closed.
        return ctypes.get_last_error() != 87
    kernel32.CloseHandle(process)
    return True


def runtime_pid_is_alive(pid: int) -> bool:
    """Expose the same PID probe used by role leases to drain recovery."""
    return _pid_is_alive(pid)


class WindowsRuntimeInspector:
    """Collect process, listener, and open-file evidence without process mutation."""

    def __init__(
        self,
        *,
        expected_entrypoint_path: str | os.PathLike[str],
        tracked_database_paths: tuple[str | os.PathLike[str], ...],
    ) -> None:
        if os.name != "nt":
            raise RuntimeError("WindowsRuntimeInspector is only available on Windows")
        self._entrypoint = Path(expected_entrypoint_path).expanduser().resolve()
        self._databases = tuple(
            Path(path).expanduser().resolve() for path in tracked_database_paths
        )
        if not self._entrypoint.is_file() or any(
            not path.is_file() for path in self._databases
        ):
            raise ValueError("runtime evidence paths must name existing files")

    def snapshot(self) -> RuntimeProcessSnapshot:
        listeners = _windows_tcp_listeners()
        process_ids = _windows_process_ids()
        candidates: list[tuple[str, int, Path, Path, tuple[str, ...], str, str]] = []
        for pid in process_ids:
            try:
                executable, cwd, argv = _windows_process_metadata(pid)
            except (OSError, RuntimeError, ValueError):
                continue
            executable_name = executable.name.casefold()
            if executable_name == "node.exe":
                entrypoint = _resolved_argv_path(
                    argv,
                    cwd=cwd,
                    expected=self._entrypoint,
                )
                if entrypoint is None:
                    continue
                candidates.append(
                    ("node", pid, executable, cwd, argv, "node", "live")
                )
                continue
            if executable_name not in ("python.exe", "pythonw.exe"):
                continue
            role = _argument_value(argv, "--study-app-role")
            environment = _argument_value(argv, "--study-app-environment")
            if role not in ("api", "worker", "scheduler") or environment != "live":
                continue
            candidates.append(
                ("python", pid, executable, cwd, argv, role, environment)
            )
        # File-handle evidence must include every process, not only recognized
        # runtime roles. Editor-launched MCP processes and other holders are
        # valid reasons to fail the final-window zero-handle gate.
        observed_pids = frozenset(process_ids)
        database_users = {
            path: _windows_processes_using_file(path, observed_pids)
            for path in self._databases
        }
        nodes: list[ProcessEvidence] = []
        python_roles: list[ProcessEvidence] = []
        for kind, pid, executable, cwd, argv, role, environment in candidates:
            database_paths = tuple(
                path for path in self._databases if pid in database_users[path]
            )
            process_listeners = listeners.get(pid, ())
            listener_host, listener_port = (
                process_listeners[0] if len(process_listeners) == 1 else ("", 0)
            )
            evidence = ProcessEvidence(
                pid=pid,
                executable_path=executable,
                entrypoint_path=self._entrypoint if kind == "node" else executable,
                cwd=cwd,
                argv=argv,
                listener_host=listener_host,
                listener_port=listener_port,
                database_paths=database_paths,
                process_role=role,
                environment=environment,
            )
            (nodes if kind == "node" else python_roles).append(evidence)
        return RuntimeProcessSnapshot(
            node_processes=tuple(sorted(nodes, key=lambda item: item.pid)),
            live_python_roles=tuple(sorted(python_roles, key=lambda item: item.pid)),
            database_handle_pids=tuple(
                sorted({pid for users in database_users.values() for pid in users})
            ),
        )


def _argument_value(argv: tuple[str, ...], name: str) -> str | None:
    try:
        index = argv.index(name)
    except ValueError:
        return None
    if index + 1 >= len(argv):
        return None
    return argv[index + 1]


def _resolved_argv_path(
    argv: tuple[str, ...],
    *,
    cwd: Path,
    expected: Path,
) -> Path | None:
    for raw in argv[1:]:
        if raw.startswith("--"):
            continue
        candidate = Path(raw)
        if not candidate.is_absolute():
            candidate = cwd / candidate
        try:
            resolved = candidate.resolve()
        except OSError:
            continue
        if resolved == expected:
            return resolved
    return None


def _windows_process_ids() -> tuple[int, ...]:
    import ctypes
    from ctypes import wintypes

    enum_processes = ctypes.WinDLL("psapi", use_last_error=True).EnumProcesses
    enum_processes.argtypes = (
        ctypes.POINTER(wintypes.DWORD),
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
    )
    enum_processes.restype = wintypes.BOOL
    capacity = 1024
    while True:
        identifiers = (wintypes.DWORD * capacity)()
        needed = wintypes.DWORD()
        if not enum_processes(
            identifiers,
            ctypes.sizeof(identifiers),
            ctypes.byref(needed),
        ):
            raise OSError(ctypes.get_last_error(), "EnumProcesses failed")
        count = int(needed.value) // ctypes.sizeof(wintypes.DWORD)
        if count < capacity:
            return tuple(int(identifiers[index]) for index in range(count) if identifiers[index])
        capacity *= 2


def _windows_process_metadata(pid: int) -> tuple[Path, Path, tuple[str, ...]]:
    import ctypes
    from ctypes import wintypes

    class ProcessBasicInformation(ctypes.Structure):
        _fields_ = (
            ("reserved1", ctypes.c_void_p),
            ("peb_base_address", ctypes.c_void_p),
            ("reserved2", ctypes.c_void_p * 2),
            ("unique_process_id", ctypes.c_size_t),
            ("reserved3", ctypes.c_void_p),
        )

    process_query_information = 0x0400
    process_vm_read = 0x0010
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    open_process = kernel32.OpenProcess
    open_process.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    open_process.restype = wintypes.HANDLE
    handle = open_process(
        process_query_information | process_vm_read,
        False,
        pid,
    )
    if not handle:
        raise OSError(ctypes.get_last_error(), "OpenProcess failed")
    try:
        _require_same_process_bitness(handle)
        query_information = ctypes.WinDLL("ntdll").NtQueryInformationProcess
        query_information.argtypes = (
            wintypes.HANDLE,
            wintypes.ULONG,
            ctypes.c_void_p,
            wintypes.ULONG,
            ctypes.POINTER(wintypes.ULONG),
        )
        query_information.restype = ctypes.c_long
        basic = ProcessBasicInformation()
        returned = wintypes.ULONG()
        status = query_information(
            handle,
            0,
            ctypes.byref(basic),
            ctypes.sizeof(basic),
            ctypes.byref(returned),
        )
        if status != 0 or not basic.peb_base_address:
            raise OSError(int(status), "NtQueryInformationProcess failed")
        pointer_size = ctypes.sizeof(ctypes.c_void_p)
        parameters_pointer_offset = 0x20 if pointer_size == 8 else 0x10
        process_parameters = _read_remote_pointer(
            handle,
            int(basic.peb_base_address) + parameters_pointer_offset,
        )
        if not process_parameters:
            raise RuntimeError("process parameters pointer is null")
        current_directory_offset = 0x38 if pointer_size == 8 else 0x24
        command_line_offset = 0x70 if pointer_size == 8 else 0x40
        cwd_text = _read_remote_unicode_string(
            handle,
            process_parameters + current_directory_offset,
        )
        command_line = _read_remote_unicode_string(
            handle,
            process_parameters + command_line_offset,
        )
        executable = _query_process_image(handle)
        cwd = Path(cwd_text).resolve()
        argv = _command_line_to_argv(command_line)
        if not argv:
            raise RuntimeError("process command line is empty")
        return executable, cwd, argv
    finally:
        kernel32.CloseHandle(handle)


def _require_same_process_bitness(process_handle: object) -> None:
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    is_wow64 = kernel32.IsWow64Process
    is_wow64.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.BOOL))
    is_wow64.restype = wintypes.BOOL
    current = wintypes.BOOL()
    target = wintypes.BOOL()
    if not is_wow64(kernel32.GetCurrentProcess(), ctypes.byref(current)):
        raise OSError(ctypes.get_last_error(), "IsWow64Process current failed")
    if not is_wow64(process_handle, ctypes.byref(target)):
        raise OSError(ctypes.get_last_error(), "IsWow64Process target failed")
    if bool(current.value) != bool(target.value):
        raise RuntimeError("cross-bitness process inspection is unsupported")


def _read_remote_pointer(process_handle: object, address: int) -> int:
    import ctypes

    payload = _read_process_memory(
        process_handle,
        address,
        ctypes.sizeof(ctypes.c_void_p),
    )
    return int.from_bytes(payload, "little")


def _read_remote_unicode_string(process_handle: object, address: int) -> str:
    import ctypes

    pointer_size = ctypes.sizeof(ctypes.c_void_p)
    header_size = 16 if pointer_size == 8 else 8
    header = _read_process_memory(process_handle, address, header_size)
    length = int.from_bytes(header[0:2], "little")
    maximum_length = int.from_bytes(header[2:4], "little")
    pointer_offset = 8 if pointer_size == 8 else 4
    buffer_address = int.from_bytes(
        header[pointer_offset : pointer_offset + pointer_size],
        "little",
    )
    if length > maximum_length or length % 2 or length > 32768 or not buffer_address:
        raise RuntimeError("remote UNICODE_STRING is invalid")
    return _read_process_memory(process_handle, buffer_address, length).decode("utf-16-le")


def _read_process_memory(process_handle: object, address: int, size: int) -> bytes:
    import ctypes
    from ctypes import wintypes

    buffer = ctypes.create_string_buffer(size)
    read = ctypes.c_size_t()
    read_process_memory = ctypes.WinDLL(
        "kernel32",
        use_last_error=True,
    ).ReadProcessMemory
    read_process_memory.argtypes = (
        wintypes.HANDLE,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_size_t,
        ctypes.POINTER(ctypes.c_size_t),
    )
    read_process_memory.restype = wintypes.BOOL
    if not read_process_memory(
        process_handle,
        ctypes.c_void_p(address),
        buffer,
        size,
        ctypes.byref(read),
    ) or read.value != size:
        raise OSError(ctypes.get_last_error(), "ReadProcessMemory failed")
    return buffer.raw


def _query_process_image(process_handle: object) -> Path:
    import ctypes
    from ctypes import wintypes

    capacity = 32768
    buffer = ctypes.create_unicode_buffer(capacity)
    size = wintypes.DWORD(capacity)
    query_image = ctypes.WinDLL(
        "kernel32",
        use_last_error=True,
    ).QueryFullProcessImageNameW
    query_image.argtypes = (
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.LPWSTR,
        ctypes.POINTER(wintypes.DWORD),
    )
    query_image.restype = wintypes.BOOL
    if not query_image(process_handle, 0, buffer, ctypes.byref(size)):
        raise OSError(ctypes.get_last_error(), "QueryFullProcessImageNameW failed")
    return Path(buffer.value).resolve()


def _command_line_to_argv(command_line: str) -> tuple[str, ...]:
    import ctypes

    argc = ctypes.c_int()
    shell32 = ctypes.WinDLL("shell32", use_last_error=True)
    parse = shell32.CommandLineToArgvW
    parse.argtypes = (ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_int))
    parse.restype = ctypes.POINTER(ctypes.c_wchar_p)
    values = parse(command_line, ctypes.byref(argc))
    if not values:
        raise OSError(ctypes.get_last_error(), "CommandLineToArgvW failed")
    try:
        return tuple(values[index] for index in range(argc.value))
    finally:
        ctypes.WinDLL("kernel32").LocalFree(values)


def _windows_tcp_listeners() -> dict[int, tuple[tuple[str, int], ...]]:
    rows = [*_windows_tcp4_listeners(), *_windows_tcp6_listeners()]
    grouped: dict[int, set[tuple[str, int]]] = {}
    for pid, host, port in rows:
        grouped.setdefault(pid, set()).add((host, port))
    return {
        pid: tuple(sorted(addresses))
        for pid, addresses in grouped.items()
    }


def _windows_tcp_table(address_family: int) -> bytes:
    import ctypes
    from ctypes import wintypes

    get_table = ctypes.WinDLL("iphlpapi", use_last_error=True).GetExtendedTcpTable
    get_table.argtypes = (
        ctypes.c_void_p,
        ctypes.POINTER(wintypes.ULONG),
        wintypes.BOOL,
        wintypes.ULONG,
        ctypes.c_int,
        wintypes.ULONG,
    )
    get_table.restype = wintypes.DWORD
    size = wintypes.ULONG()
    table_owner_pid_all = 5
    insufficient_buffer = 122
    result = get_table(
        None,
        ctypes.byref(size),
        False,
        address_family,
        table_owner_pid_all,
        0,
    )
    if result not in (0, insufficient_buffer):
        raise OSError(int(result), "GetExtendedTcpTable sizing failed")
    buffer = ctypes.create_string_buffer(size.value)
    result = get_table(
        buffer,
        ctypes.byref(size),
        False,
        address_family,
        table_owner_pid_all,
        0,
    )
    if result != 0:
        raise OSError(int(result), "GetExtendedTcpTable failed")
    return buffer.raw[: size.value]


def _windows_tcp4_listeners() -> tuple[tuple[int, str, int], ...]:
    payload = _windows_tcp_table(socket.AF_INET)
    count = struct.unpack_from("<I", payload, 0)[0]
    rows: list[tuple[int, str, int]] = []
    offset = 4
    for _ in range(count):
        state, local_address, local_port, _remote_address, _remote_port, pid = (
            struct.unpack_from("<IIIIII", payload, offset)
        )
        offset += 24
        if state != 2:
            continue
        host = socket.inet_ntoa(struct.pack("<I", local_address))
        port = socket.ntohs(local_port & 0xFFFF)
        rows.append((pid, host, port))
    return tuple(rows)


def _windows_tcp6_listeners() -> tuple[tuple[int, str, int], ...]:
    payload = _windows_tcp_table(socket.AF_INET6)
    count = struct.unpack_from("<I", payload, 0)[0]
    rows: list[tuple[int, str, int]] = []
    offset = 4
    for _ in range(count):
        local_address = payload[offset : offset + 16]
        local_port = struct.unpack_from("<I", payload, offset + 20)[0]
        state = struct.unpack_from("<I", payload, offset + 48)[0]
        pid = struct.unpack_from("<I", payload, offset + 52)[0]
        offset += 56
        if state != 2:
            continue
        host = socket.inet_ntop(socket.AF_INET6, local_address)
        port = socket.ntohs(local_port & 0xFFFF)
        rows.append((pid, host, port))
    return tuple(rows)


def _windows_processes_using_file(
    path: Path,
    observed_pids: frozenset[int],
) -> frozenset[int]:
    import ctypes
    from ctypes import wintypes

    class RmUniqueProcess(ctypes.Structure):
        _fields_ = (
            ("process_id", wintypes.DWORD),
            ("process_start_time", wintypes.FILETIME),
        )

    class RmProcessInfo(ctypes.Structure):
        _fields_ = (
            ("process", RmUniqueProcess),
            ("app_name", ctypes.c_wchar * 256),
            ("service_short_name", ctypes.c_wchar * 64),
            ("application_type", ctypes.c_int),
            ("app_status", wintypes.ULONG),
            ("terminal_session_id", wintypes.DWORD),
            ("restartable", wintypes.BOOL),
        )

    manager = ctypes.WinDLL("rstrtmgr", use_last_error=True)
    start_session = manager.RmStartSession
    start_session.argtypes = (
        ctypes.POINTER(wintypes.DWORD),
        wintypes.DWORD,
        wintypes.LPWSTR,
    )
    start_session.restype = wintypes.DWORD
    register = manager.RmRegisterResources
    register.argtypes = (
        wintypes.DWORD,
        wintypes.UINT,
        ctypes.POINTER(wintypes.LPCWSTR),
        wintypes.UINT,
        ctypes.c_void_p,
        wintypes.UINT,
        ctypes.c_void_p,
    )
    register.restype = wintypes.DWORD
    get_list = manager.RmGetList
    get_list.argtypes = (
        wintypes.DWORD,
        ctypes.POINTER(wintypes.UINT),
        ctypes.POINTER(wintypes.UINT),
        ctypes.POINTER(RmProcessInfo),
        ctypes.POINTER(wintypes.DWORD),
    )
    get_list.restype = wintypes.DWORD
    end_session = manager.RmEndSession
    end_session.argtypes = (wintypes.DWORD,)
    end_session.restype = wintypes.DWORD

    session = wintypes.DWORD()
    session_key = ctypes.create_unicode_buffer(33)
    result = start_session(ctypes.byref(session), 0, session_key)
    if result != 0:
        return _windows_processes_using_file_via_handles(path, observed_pids)
    try:
        resources = (wintypes.LPCWSTR * 1)(str(path))
        result = register(session, 1, resources, 0, None, 0, None)
        if result != 0:
            return _windows_processes_using_file_via_handles(path, observed_pids)
        needed = wintypes.UINT()
        count = wintypes.UINT()
        reboot_reasons = wintypes.DWORD()
        result = get_list(
            session,
            ctypes.byref(needed),
            ctypes.byref(count),
            None,
            ctypes.byref(reboot_reasons),
        )
        more_data = 234
        if result == 0:
            return frozenset()
        if result != more_data or needed.value == 0:
            return _windows_processes_using_file_via_handles(path, observed_pids)
        entries = (RmProcessInfo * needed.value)()
        count = wintypes.UINT(needed.value)
        result = get_list(
            session,
            ctypes.byref(needed),
            ctypes.byref(count),
            entries,
            ctypes.byref(reboot_reasons),
        )
        if result != 0:
            return _windows_processes_using_file_via_handles(path, observed_pids)
        return frozenset(
            int(entries[index].process.process_id) for index in range(count.value)
            if int(entries[index].process.process_id) in observed_pids
        )
    finally:
        end_session(session)


def _windows_processes_using_file_via_handles(
    path: Path,
    observed_pids: frozenset[int],
) -> frozenset[int]:
    if not observed_pids:
        return frozenset()
    handles = _windows_system_handles(observed_pids)
    return frozenset(
        pid
        for pid, values in handles.items()
        if _windows_process_handles_path(pid, values, path)
    )


def _windows_system_handles(
    observed_pids: frozenset[int],
) -> dict[int, tuple[int, ...]]:
    import ctypes
    from ctypes import wintypes

    query = ctypes.WinDLL("ntdll").NtQuerySystemInformation
    query.argtypes = (
        wintypes.ULONG,
        ctypes.c_void_p,
        wintypes.ULONG,
        ctypes.POINTER(wintypes.ULONG),
    )
    query.restype = ctypes.c_long
    length = 1 << 20
    information_length_mismatch = 0xC0000004
    while True:
        buffer = ctypes.create_string_buffer(length)
        required = wintypes.ULONG()
        status = query(64, buffer, length, ctypes.byref(required))
        unsigned_status = int(status) & 0xFFFFFFFF
        if unsigned_status == information_length_mismatch:
            length = max(length * 2, int(required.value) + 65536)
            if length > 128 * 1024 * 1024:
                raise RuntimeError("Windows system handle table is unexpectedly large")
            continue
        if status != 0:
            raise OSError(int(status), "NtQuerySystemInformation handles failed")
        payload = buffer.raw
        break
    pointer_size = ctypes.sizeof(ctypes.c_void_p)
    count = int.from_bytes(payload[:pointer_size], "little")
    offset = pointer_size * 2
    entry_size = 40 if pointer_size == 8 else 28
    grouped: dict[int, list[int]] = {pid: [] for pid in observed_pids}
    for index in range(count):
        row = offset + index * entry_size
        if row + entry_size > len(payload):
            raise RuntimeError("Windows system handle table is truncated")
        if pointer_size == 8:
            _object, pid, handle_value, _access, _backtrace, _type, _attrs, _reserved = (
                struct.unpack_from("<QQQIHHII", payload, row)
            )
        else:
            _object, pid, handle_value, _access, _backtrace, _type, _attrs, _reserved = (
                struct.unpack_from("<IIIIHHII", payload, row)
            )
        if pid in grouped:
            grouped[pid].append(handle_value)
    return {pid: tuple(values) for pid, values in grouped.items()}


def _windows_process_handles_path(
    pid: int,
    handle_values: tuple[int, ...],
    expected_path: Path,
) -> bool:
    import ctypes
    from ctypes import wintypes

    process_duplicate_handle = 0x0040
    duplicate_same_access = 0x00000002
    file_type_disk = 0x0001
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    open_process = kernel32.OpenProcess
    open_process.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    open_process.restype = wintypes.HANDLE
    process = open_process(process_duplicate_handle, False, pid)
    if not process:
        return False
    current_process = kernel32.GetCurrentProcess()
    duplicate_handle = kernel32.DuplicateHandle
    duplicate_handle.argtypes = (
        wintypes.HANDLE,
        wintypes.HANDLE,
        wintypes.HANDLE,
        ctypes.POINTER(wintypes.HANDLE),
        wintypes.DWORD,
        wintypes.BOOL,
        wintypes.DWORD,
    )
    duplicate_handle.restype = wintypes.BOOL
    try:
        for handle_value in handle_values:
            duplicate = wintypes.HANDLE()
            if not duplicate_handle(
                process,
                wintypes.HANDLE(handle_value),
                current_process,
                ctypes.byref(duplicate),
                0,
                False,
                duplicate_same_access,
            ):
                continue
            try:
                if kernel32.GetFileType(duplicate) != file_type_disk:
                    continue
                actual = _windows_final_path(duplicate)
                if actual is not None and actual == expected_path:
                    return True
            finally:
                kernel32.CloseHandle(duplicate)
        return False
    finally:
        kernel32.CloseHandle(process)


def _windows_final_path(handle: object) -> Path | None:
    import ctypes
    from ctypes import wintypes

    capacity = 32768
    buffer = ctypes.create_unicode_buffer(capacity)
    get_path = ctypes.WinDLL(
        "kernel32",
        use_last_error=True,
    ).GetFinalPathNameByHandleW
    get_path.argtypes = (
        wintypes.HANDLE,
        wintypes.LPWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
    )
    get_path.restype = wintypes.DWORD
    length = get_path(handle, buffer, capacity, 0)
    if length == 0 or length >= capacity:
        return None
    value = buffer.value
    if value.startswith("\\\\?\\UNC\\"):
        value = "\\\\" + value[8:]
    elif value.startswith("\\\\?\\"):
        value = value[4:]
    try:
        return Path(value).resolve()
    except OSError:
        return None
