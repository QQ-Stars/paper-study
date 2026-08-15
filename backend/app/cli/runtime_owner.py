from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import http.client
import ipaddress
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from typing import Any, Callable, Mapping, Protocol, Sequence

from backend.app.api.compat.database_identity import (
    DatabaseEvidenceIdentityManifest,
    DatabaseEvidenceIdentityService,
    DatabaseIdentityError,
    canonical_json_bytes,
    exclusive_write_bytes,
    load_database_evidence_identity_manifest,
    read_platform_file_identity,
)
from backend.app.api.compat.schema_inventory import (
    SchemaInventoryError,
    capture_inventory,
    compare_inventory,
)
from backend.app.providers.runtime_lease import (
    ProcessEvidence,
    RuntimeProcessSnapshot,
    WindowsRuntimeInspector,
    candidate_runtime_drain_request,
    candidate_runtime_drain_final_fence,
    runtime_pid_is_alive,
)


_OWNER_UNSIGNED_FIELDS = (
    "schemaVersion",
    "markerKind",
    "ownerState",
    "runtimeNamespace",
    "databaseLineageId",
    "subjectDatabaseId",
    "databaseIdentityManifestPath",
    "databaseIdentityManifestFileSha256",
    "originReceiptPath",
    "originReceiptFileSha256",
    "originReceiptSha256",
    "entrypointPath",
    "processId",
    "executablePath",
    "cwd",
    "argv",
    "listenerHost",
    "listenerPort",
    "databasePaths",
    "createdAt",
)
_OWNER_FIELDS = (*_OWNER_UNSIGNED_FIELDS, "ownerMarkerSha256")
_ROLE_LEASE_UNSIGNED_FIELDS = (
    "schemaVersion",
    "leaseKind",
    "environment",
    "databaseLineageId",
    "subjectDatabaseId",
    "runtimeNamespace",
    "role",
    "ownerId",
    "pid",
    "startedAt",
    "expiresAt",
    "keyHash",
)
_ROLE_LEASE_FIELDS = (*_ROLE_LEASE_UNSIGNED_FIELDS, "leaseSha256")
_ROLLBACK_SMOKE_PATHS = (
    "/api/papers",
    "/api/reviews",
    "/pdfbytes",
    "/workspace/",
    "/legacy/",
)


class RuntimeOwnerError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class RuntimeInspector(Protocol):
    def snapshot(self) -> RuntimeProcessSnapshot: ...


class CandidateRollbackRunner(Protocol):
    def start(self, database: Path, profile: str) -> object: ...

    def smoke(self, handle: object) -> dict[str, object]: ...

    def stop(self, handle: object) -> None: ...


class CandidateRuntimeDrain(Protocol):
    def stop_and_wait(
        self,
        *,
        database: Path,
        runtime_namespace: str,
    ) -> tuple[str, ...]: ...

    def release_fence(self) -> None: ...


class FilesystemCandidateRuntimeDrain:
    """Request a cross-process candidate stop and wait for role leases to clear."""

    def __init__(
        self,
        *,
        lease_root: str | os.PathLike[str],
        database_identity_manifest: str | os.PathLike[str],
        timeout_seconds: float = 30.0,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self._lease_root = Path(lease_root).expanduser().resolve(strict=False)
        try:
            self._identity = load_database_evidence_identity_manifest(
                database_identity_manifest
            )
        except DatabaseIdentityError as error:
            raise RuntimeOwnerError(error.code, str(error)) from error
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        self._timeout_seconds = timeout_seconds
        self._clock = clock or time.monotonic
        self._active_requests: tuple[tuple[Path, bytes], ...] = ()
        self._active_final_fence: tuple[Path, bytes] | None = None

    def stop_and_wait(
        self,
        *,
        database: Path,
        runtime_namespace: str,
    ) -> tuple[str, ...]:
        if database.resolve() != self._identity.database_path:
            raise RuntimeOwnerError(
                "CANDIDATE_RUNTIME_DRAIN_IDENTITY_MISMATCH",
                "The runtime drain database does not match its candidate identity.",
            )
        if self._active_requests or self._active_final_fence is not None:
            raise RuntimeOwnerError(
                "CANDIDATE_RUNTIME_DRAIN_ALREADY_ACTIVE",
                "This runtime drain already holds admission fences.",
            )
        self._lease_root.mkdir(mode=0o700, parents=True, exist_ok=True)
        roles = ("api", "worker", "scheduler")
        requests: list[tuple[Path, bytes]] = []
        final_fence = candidate_runtime_drain_final_fence(
            self._lease_root,
            self._identity,
            runtime_namespace=runtime_namespace,
        )
        completed = False
        deadline = self._clock() + self._timeout_seconds
        try:
            # Linearize admission before waiting on the first role. The fence is
            # retained through rollback and removed only by release_fence().
            _create_exclusive_drain_fence(*final_fence)
            self._active_final_fence = final_fence
            for role in roles:
                request_path, request_payload = candidate_runtime_drain_request(
                    self._lease_root,
                    self._identity,
                    runtime_namespace=runtime_namespace,
                    role=role,
                )
                _create_or_verify_drain_request(request_path, request_payload)
                requests.append((request_path, request_payload))
                self._active_requests = tuple(requests)
                while role in _matching_candidate_roles(
                    self._lease_root,
                    identity=self._identity,
                    runtime_namespace=runtime_namespace,
                ):
                    if self._clock() >= deadline:
                        raise RuntimeOwnerError(
                            "CANDIDATE_RUNTIME_DRAIN_TIMEOUT",
                            "Candidate roles did not stop before the drain deadline.",
                        )
                    time.sleep(0.05)
            completed = True
            return roles
        finally:
            if not completed:
                self.release_fence()

    def release_fence(self) -> None:
        while self._active_requests:
            request = self._active_requests[-1]
            _remove_drain_requests((request,), missing_ok=False)
            self._active_requests = self._active_requests[:-1]
        if self._active_final_fence is not None:
            _remove_drain_requests(
                (self._active_final_fence,),
                missing_ok=False,
            )
            self._active_final_fence = None


def _create_exclusive_drain_fence(path: Path, payload: bytes) -> None:
    try:
        exclusive_write_bytes(path, payload)
        return
    except DatabaseIdentityError as error:
        if error.code != "EVIDENCE_OUTPUT_EXISTS":
            raise RuntimeOwnerError(error.code, str(error)) from error
    try:
        current_payload = path.read_bytes()
    except OSError as error:
        raise RuntimeOwnerError(
            "CANDIDATE_RUNTIME_DRAIN_REQUEST_INVALID",
            "The active runtime drain fence could not be read.",
        ) from error
    if current_payload != payload:
        raise RuntimeOwnerError(
            "CANDIDATE_RUNTIME_DRAIN_REQUEST_INVALID",
            "The active runtime drain fence failed its identity contract.",
        )
    raise RuntimeOwnerError(
        "CANDIDATE_RUNTIME_DRAIN_ALREADY_ACTIVE",
        "Another runtime drain already owns this candidate admission fence.",
    )


def _create_or_verify_drain_request(path: Path, payload: bytes) -> None:
    try:
        exclusive_write_bytes(path, payload)
        return
    except DatabaseIdentityError as error:
        if error.code != "EVIDENCE_OUTPUT_EXISTS":
            raise RuntimeOwnerError(error.code, str(error)) from error
    try:
        current_payload = path.read_bytes()
    except OSError as error:
        raise RuntimeOwnerError(
            "CANDIDATE_RUNTIME_DRAIN_REQUEST_INVALID",
            "The existing runtime drain request could not be read.",
        ) from error
    if current_payload != payload:
        raise RuntimeOwnerError(
            "CANDIDATE_RUNTIME_DRAIN_REQUEST_INVALID",
            "The existing runtime drain request does not match this candidate role.",
        )


def _matching_candidate_roles(
    root: Path,
    *,
    identity: DatabaseEvidenceIdentityManifest,
    runtime_namespace: str,
) -> set[str]:
    roles: set[str] = set()
    for path in _role_lease_files(root):
        try:
            payload = path.read_bytes()
        except FileNotFoundError:
            continue
        except OSError as error:
            raise RuntimeOwnerError(
                "CANDIDATE_LEASE_READ_FAILED",
                "Could not read a candidate role lease during drain.",
            ) from error
        document = _strict_role_lease_document(payload)
        if _reclaim_expired_dead_api_presence(path, payload, document):
            continue
        if _matching_candidate_lease(
            document,
            identity=identity,
            runtime_namespace=runtime_namespace,
        ):
            roles.add(document["role"])
    return roles


def _reclaim_expired_dead_api_presence(
    path: Path,
    payload: bytes,
    document: Mapping[str, Any],
) -> bool:
    if document["leaseKind"] != "runtime-api-presence":
        return False
    try:
        expires_at = datetime.fromisoformat(
            str(document["expiresAt"]).replace("Z", "+00:00")
        )
        pid = int(document["pid"])
    except (TypeError, ValueError) as error:
        raise RuntimeOwnerError(
            "CANDIDATE_LEASE_INVALID",
            "The candidate API presence expiry or PID is invalid.",
        ) from error
    if (
        expires_at.tzinfo is None
        or expires_at > datetime.now(timezone.utc)
        or runtime_pid_is_alive(pid)
    ):
        return False
    try:
        if path.read_bytes() != payload:
            raise RuntimeOwnerError(
                "CANDIDATE_LEASE_DRIFT",
                "The expired API presence changed before stale recovery.",
            )
        path.unlink()
        return True
    except FileNotFoundError:
        return True
    except RuntimeOwnerError:
        raise
    except OSError as error:
        raise RuntimeOwnerError(
            "CANDIDATE_LEASE_CLEANUP_FAILED",
            "The expired API presence could not be removed safely.",
        ) from error


def _remove_drain_requests(
    requests: tuple[tuple[Path, bytes], ...],
    *,
    missing_ok: bool,
) -> None:
    for path, expected_payload in reversed(requests):
        try:
            current_payload = path.read_bytes()
        except FileNotFoundError:
            if missing_ok:
                continue
            raise RuntimeOwnerError(
                "CANDIDATE_RUNTIME_DRAIN_REQUEST_INVALID",
                "A held runtime drain fence disappeared before release.",
            )
        except OSError as error:
            raise RuntimeOwnerError(
                "CANDIDATE_RUNTIME_DRAIN_REQUEST_CLEANUP_FAILED",
                "The runtime drain request could not be read for cleanup.",
            ) from error
        if current_payload != expected_payload:
            raise RuntimeOwnerError(
                "CANDIDATE_RUNTIME_DRAIN_REQUEST_INVALID",
                "A held runtime drain fence changed before release.",
            )
        try:
            path.unlink()
        except OSError as error:
            raise RuntimeOwnerError(
                "CANDIDATE_RUNTIME_DRAIN_REQUEST_CLEANUP_FAILED",
                "The runtime drain request could not be removed safely.",
            ) from error


@dataclass(frozen=True, slots=True)
class _FrozenNodeHandle:
    process: subprocess.Popen[bytes]
    host: str
    port: int


class FrozenNodeRollbackRunner:
    """Start only the frozen legacy Node profile on an isolated database."""

    def __init__(
        self,
        *,
        application_root: str | os.PathLike[str] | None = None,
        startup_timeout_seconds: float = 15.0,
        request_timeout_seconds: float = 5.0,
    ) -> None:
        root = (
            Path(application_root).expanduser()
            if application_root is not None
            else Path(__file__).resolve().parents[3]
        )
        self._application_root = root.resolve()
        self._startup_timeout_seconds = startup_timeout_seconds
        self._request_timeout_seconds = request_timeout_seconds

    def start(self, database: Path, profile: str) -> object:
        if profile != "frozen-node":
            raise RuntimeOwnerError(
                "CANDIDATE_ROLLBACK_PROFILE_INVALID",
                "The frozen Node runner received an invalid profile.",
            )
        database_path = _absolute_existing_file(database, "candidate database")
        entrypoint = self._application_root / "server.js"
        node = shutil.which("node")
        if node is None or not entrypoint.is_file():
            raise RuntimeOwnerError(
                "FROZEN_NODE_RUNTIME_UNAVAILABLE",
                "The local Node runtime or frozen server entrypoint is unavailable.",
            )
        host = "127.0.0.1"
        port = _assigned_loopback_port(host)
        environment = os.environ.copy()
        environment.update(
            {
                "HOST": host,
                "PORT": str(port),
                "DB_PATH": str(database_path),
                "API_BACKEND_MODE": "legacy",
                "DOCUMENT_PIPELINE_MODE": "legacy",
                "GENERATION_PIPELINE_MODE": "legacy",
                "ARTIFACT_READ_MODE": "legacy",
                "ARTIFACT_WRITE_MODE": "legacy",
                "OCR_ENABLED": "0",
                "OBSIDIAN_ENABLED": "0",
                "NODE_ENV": "test",
            }
        )
        creation_flags = (
            int(getattr(subprocess, "CREATE_NO_WINDOW", 0)) if os.name == "nt" else 0
        )
        process = subprocess.Popen(
            [node, str(entrypoint)],
            cwd=self._application_root,
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=creation_flags,
        )
        handle = _FrozenNodeHandle(process=process, host=host, port=port)
        deadline = time.monotonic() + self._startup_timeout_seconds
        try:
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    raise RuntimeOwnerError(
                        "FROZEN_NODE_START_FAILED",
                        "The frozen Node candidate exited before binding loopback.",
                    )
                try:
                    with socket.create_connection((host, port), timeout=0.2):
                        return handle
                except OSError:
                    time.sleep(0.05)
            raise RuntimeOwnerError(
                "FROZEN_NODE_START_TIMEOUT",
                "The frozen Node candidate did not bind loopback before the deadline.",
            )
        except Exception:
            self.stop(handle)
            raise

    def smoke(self, handle: object) -> dict[str, object]:
        if not isinstance(handle, _FrozenNodeHandle):
            raise RuntimeOwnerError(
                "FROZEN_NODE_HANDLE_INVALID",
                "The frozen Node runner received an invalid process handle.",
            )
        if handle.host != "127.0.0.1" or handle.process.poll() is not None:
            raise RuntimeOwnerError(
                "FROZEN_NODE_NOT_LOOPBACK",
                "The frozen Node candidate is not active on the required loopback host.",
            )
        for path in _ROLLBACK_SMOKE_PATHS:
            connection = http.client.HTTPConnection(
                handle.host,
                handle.port,
                timeout=self._request_timeout_seconds,
            )
            try:
                connection.request(
                    "GET",
                    path,
                    headers={"Host": f"{handle.host}:{handle.port}"},
                )
                response = connection.getresponse()
                response.read()
            except (OSError, http.client.HTTPException) as error:
                raise RuntimeOwnerError(
                    "FROZEN_NODE_SMOKE_FAILED",
                    f"The frozen Node candidate did not answer {path}.",
                ) from error
            finally:
                connection.close()
            if response.status >= 500:
                raise RuntimeOwnerError(
                    "FROZEN_NODE_SMOKE_FAILED",
                    f"The frozen Node candidate returned a server error for {path}.",
                )
        return {"paths": list(_ROLLBACK_SMOKE_PATHS), "loopback": True}

    def stop(self, handle: object) -> None:
        if not isinstance(handle, _FrozenNodeHandle):
            raise RuntimeOwnerError(
                "FROZEN_NODE_HANDLE_INVALID",
                "The frozen Node runner received an invalid process handle.",
            )
        process = handle.process
        if process.poll() is not None:
            return
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


@dataclass(frozen=True, slots=True)
class RuntimeOwnerReport:
    owner_state: str
    process_id: int
    owner_marker_path: Path
    canonical_bytes: bytes
    owner_marker_file_sha256: str
    database_lineage_id: str
    subject_database_id: str
    origin_receipt_file_sha256: str
    verification_mode: str


class RuntimeOwnerService:
    def __init__(
        self,
        inspector: RuntimeInspector,
        *,
        clock: Callable[[], datetime] | None = None,
        identity_service: DatabaseEvidenceIdentityService | None = None,
    ) -> None:
        self._inspector = inspector
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._identity_service = identity_service or DatabaseEvidenceIdentityService()

    def initialize_node_owner(
        self,
        *,
        database_identity_manifest: str | os.PathLike[str],
        p0_origin_receipt: str | os.PathLike[str],
        expected_p0_origin_receipt_sha256: str,
        origin_backup: str | os.PathLike[str],
        origin_manifest: str | os.PathLike[str],
        runtime_namespace: str,
        expected_entrypoint_path: str | os.PathLike[str],
        owner_marker: str | os.PathLike[str],
    ) -> RuntimeOwnerReport:
        identity, process, entrypoint = self._verify_current_owner(
            database_identity_manifest=database_identity_manifest,
            p0_origin_receipt=p0_origin_receipt,
            expected_p0_origin_receipt_sha256=expected_p0_origin_receipt_sha256,
            origin_backup=origin_backup,
            origin_manifest=origin_manifest,
            runtime_namespace=runtime_namespace,
            expected_entrypoint_path=expected_entrypoint_path,
        )
        marker_path = _absolute_output(owner_marker, "owner marker")
        created_at = _timestamp(self._clock())
        unsigned = _owner_document(
            identity=identity,
            process=process,
            runtime_namespace=runtime_namespace,
            entrypoint=entrypoint,
            created_at=created_at,
        )
        marker_sha256 = hashlib.sha256(canonical_json_bytes(unsigned)).hexdigest()
        document = {**unsigned, "ownerMarkerSha256": marker_sha256}
        payload = canonical_json_bytes(document)
        try:
            exclusive_write_bytes(marker_path, payload)
        except DatabaseIdentityError as error:
            raise RuntimeOwnerError(error.code, str(error)) from error
        return _report(
            document,
            marker_path=marker_path,
            payload=payload,
            verification_mode="created",
        )

    def verify_node_owner(
        self,
        *,
        database_identity_manifest: str | os.PathLike[str],
        p0_origin_receipt: str | os.PathLike[str],
        expected_p0_origin_receipt_sha256: str,
        origin_backup: str | os.PathLike[str],
        origin_manifest: str | os.PathLike[str],
        runtime_namespace: str,
        expected_entrypoint_path: str | os.PathLike[str],
        owner_marker: str | os.PathLike[str],
    ) -> RuntimeOwnerReport:
        identity, process, entrypoint = self._verify_current_owner(
            database_identity_manifest=database_identity_manifest,
            p0_origin_receipt=p0_origin_receipt,
            expected_p0_origin_receipt_sha256=expected_p0_origin_receipt_sha256,
            origin_backup=origin_backup,
            origin_manifest=origin_manifest,
            runtime_namespace=runtime_namespace,
            expected_entrypoint_path=expected_entrypoint_path,
        )
        marker_path = _absolute_existing_file(owner_marker, "owner marker")
        try:
            payload = marker_path.read_bytes()
        except OSError as error:
            raise RuntimeOwnerError(
                "OWNER_MARKER_READ_FAILED",
                "Could not read the owner marker.",
            ) from error
        document = _strict_owner_document(payload)
        expected = _owner_document(
            identity=identity,
            process=process,
            runtime_namespace=runtime_namespace,
            entrypoint=entrypoint,
            created_at=document["createdAt"],
        )
        expected_self_hash = hashlib.sha256(canonical_json_bytes(expected)).hexdigest()
        if document != {**expected, "ownerMarkerSha256": expected_self_hash}:
            raise RuntimeOwnerError(
                "OWNER_MARKER_DRIFT",
                "The owner marker no longer matches the verified runtime owner.",
            )
        return _report(
            document,
            marker_path=marker_path,
            payload=payload,
            verification_mode="read_only",
        )

    def _verify_current_owner(
        self,
        *,
        database_identity_manifest: str | os.PathLike[str],
        p0_origin_receipt: str | os.PathLike[str],
        expected_p0_origin_receipt_sha256: str,
        origin_backup: str | os.PathLike[str],
        origin_manifest: str | os.PathLike[str],
        runtime_namespace: str,
        expected_entrypoint_path: str | os.PathLike[str],
    ) -> tuple[DatabaseEvidenceIdentityManifest, ProcessEvidence, Path]:
        if runtime_namespace != "production":
            raise RuntimeOwnerError(
                "RUNTIME_NAMESPACE_INVALID",
                "The Node owner marker is restricted to the production namespace.",
            )
        entrypoint = _absolute_existing_file(expected_entrypoint_path, "entrypoint")
        try:
            identity = self._identity_service.verify_live_database_identity(
                database_identity_manifest=database_identity_manifest,
                p0_origin_receipt=p0_origin_receipt,
                expected_p0_origin_receipt_sha256=expected_p0_origin_receipt_sha256,
                origin_backup=origin_backup,
                origin_manifest=origin_manifest,
            )
        except DatabaseIdentityError as error:
            raise RuntimeOwnerError(error.code, str(error)) from error
        snapshot = self._inspector.snapshot()
        if snapshot.live_python_roles:
            raise RuntimeOwnerError(
                "LIVE_PYTHON_ROLE_PRESENT",
                "A Live Python role is present while Node owns production.",
            )
        if len(snapshot.node_processes) != 1:
            raise RuntimeOwnerError(
                "NODE_OWNER_CARDINALITY_INVALID",
                "Exactly one Node production owner is required.",
            )
        process = snapshot.node_processes[0]
        _verify_process(process, identity=identity, entrypoint=entrypoint)
        return identity, process, entrypoint


def read_node_active_owner_marker(
    owner_marker: str | os.PathLike[str],
) -> RuntimeOwnerReport:
    """Strictly decode existing node_active evidence without process side effects."""

    marker_path = _absolute_existing_file(owner_marker, "Live owner marker")
    payload, document, _metadata = _load_node_active_marker(marker_path)
    return _report(
        document,
        marker_path=marker_path,
        payload=payload,
        verification_mode="read_only_marker",
    )


class CandidateRollbackSmokeService:
    """Run the frozen Node rollback oracle against one descendant database."""

    def __init__(
        self,
        *,
        runner: CandidateRollbackRunner,
        lease_root: str | os.PathLike[str],
        candidate_drain: CandidateRuntimeDrain | None = None,
        runtime_inspector: RuntimeInspector | None = None,
    ) -> None:
        self._runner = runner
        self._lease_root = Path(lease_root).expanduser().resolve(strict=False)
        self._candidate_drain = candidate_drain
        self._runtime_inspector = runtime_inspector

    def run(
        self,
        *,
        database: str | os.PathLike[str],
        database_identity_manifest: str | os.PathLike[str],
        candidate_runtime_namespace: str,
        owner_marker: str | os.PathLike[str],
        rollback_profile: str,
        evidence_output: str | os.PathLike[str],
    ) -> dict[str, Any]:
        database_path = _absolute_existing_file(database, "candidate database")
        identity = _load_candidate_identity(
            database_path=database_path,
            database_identity_manifest=database_identity_manifest,
        )
        namespace = _candidate_namespace(candidate_runtime_namespace)
        if rollback_profile != "frozen-node":
            raise RuntimeOwnerError(
                "CANDIDATE_ROLLBACK_PROFILE_INVALID",
                "The candidate rollback profile must be exactly frozen-node.",
            )
        marker_path = _absolute_existing_file(owner_marker, "Live owner marker")
        marker_payload, marker_document, marker_metadata = _load_node_active_marker(
            marker_path
        )
        runtime_inspector = self._runtime_inspector or _rollback_runtime_inspector(
            marker_document
        )
        evidence_path = _absolute_output(evidence_output, "rollback evidence")
        _validate_rollback_evidence_output(
            evidence_path,
            protected_paths=(
                database_path,
                identity.manifest_path,
                marker_path,
            ),
        )
        if self._lease_root.exists() and not self._lease_root.is_dir():
            raise RuntimeOwnerError(
                "CANDIDATE_LEASE_ROOT_INVALID",
                "The candidate lease root is not a directory.",
            )

        verify_owner = lambda: _verify_live_owner_unchanged(
            marker_path=marker_path,
            expected_payload=marker_payload,
            expected_metadata=marker_metadata,
            expected_document=marker_document,
            runtime_inspector=runtime_inspector,
        )
        verify_owner()
        drained_roles, drain_controller = _drain_candidate_role_leases(
            self._lease_root,
            identity=identity,
            runtime_namespace=namespace,
            database=database_path,
            candidate_drain=self._candidate_drain,
        )
        try:
            _verify_candidate_role_leases_empty(
                self._lease_root,
                identity=identity,
                runtime_namespace=namespace,
            )
            verify_owner()

            with tempfile.TemporaryDirectory(prefix="study-app-p4-rollback-") as temporary:
                inventory_before_path = Path(temporary) / "inventory-before.json"
                inventory_after_path = Path(temporary) / "inventory-after.json"
                try:
                    inventory_before = capture_inventory(
                        database=database_path,
                        database_identity_manifest=identity.manifest_path,
                        output=inventory_before_path,
                    )
                except (DatabaseIdentityError, SchemaInventoryError) as error:
                    raise RuntimeOwnerError(error.code, str(error)) from error
                verify_owner()

                handle: object | None = None
                try:
                    handle = self._runner.start(database_path, rollback_profile)
                    verify_owner()
                    smoke = self._runner.smoke(handle)
                    _validate_rollback_smoke(smoke)
                    verify_owner()
                except RuntimeOwnerError:
                    raise
                except Exception as error:
                    raise RuntimeOwnerError(
                        "CANDIDATE_ROLLBACK_RUNNER_FAILED",
                        "The frozen Node rollback candidate failed.",
                    ) from error
                finally:
                    if handle is not None:
                        try:
                            self._runner.stop(handle)
                        except Exception as error:
                            raise RuntimeOwnerError(
                                "CANDIDATE_ROLLBACK_STOP_FAILED",
                                "The frozen Node rollback candidate could not be stopped.",
                            ) from error
                verify_owner()

                try:
                    inventory_after = capture_inventory(
                        database=database_path,
                        database_identity_manifest=identity.manifest_path,
                        output=inventory_after_path,
                    )
                    compare_inventory(inventory_before, inventory_after)
                except (DatabaseIdentityError, SchemaInventoryError) as error:
                    raise RuntimeOwnerError(error.code, str(error)) from error
                verify_owner()
        finally:
            drain_controller.release_fence()

        document = {
            "schemaVersion": 1,
            "evidenceKind": "candidate-rollback-smoke",
            "ok": True,
            "operation": "candidateRollbackSmoke",
            "databasePath": str(database_path),
            "databaseIdentityManifestPath": str(identity.manifest_path),
            "databaseIdentityManifestFileSha256": identity.identity_manifest_file_sha256,
            "databaseLineageId": identity.database_lineage_id,
            "subjectDatabaseId": identity.subject_database_id,
            "subjectKind": identity.subject_kind,
            "candidateRuntimeNamespace": namespace,
            "rollbackProfile": rollback_profile,
            "ownerMarkerPath": str(marker_path),
            "ownerMarkerFileSha256": hashlib.sha256(marker_payload).hexdigest(),
            "ownerProcessId": marker_document["processId"],
            "ownerListenerHost": marker_document["listenerHost"],
            "ownerListenerPort": marker_document["listenerPort"],
            "drainedRoles": list(drained_roles),
            "smokePaths": list(_ROLLBACK_SMOKE_PATHS),
            "inventoryBeforeSha256": hashlib.sha256(
                canonical_json_bytes(inventory_before)
            ).hexdigest(),
            "inventoryAfterSha256": hashlib.sha256(
                canonical_json_bytes(inventory_after)
            ).hexdigest(),
            "inventoryMatched": True,
            "ownerMarkerPreserved": True,
        }
        verify_owner()
        try:
            exclusive_write_bytes(evidence_path, canonical_json_bytes(document))
        except DatabaseIdentityError as error:
            raise RuntimeOwnerError(error.code, str(error)) from error
        return document


def _load_candidate_identity(
    *,
    database_path: Path,
    database_identity_manifest: str | os.PathLike[str],
) -> DatabaseEvidenceIdentityManifest:
    try:
        identity = load_database_evidence_identity_manifest(
            database_identity_manifest
        )
        current_file_identity = read_platform_file_identity(database_path)
    except (DatabaseIdentityError, OSError) as error:
        code = getattr(error, "code", "DATABASE_IDENTITY_SUBJECT_MISMATCH")
        raise RuntimeOwnerError(code, str(error)) from error
    descendant_fields = (
        identity.parent_database_identity_manifest_path,
        identity.parent_subject_database_id,
        identity.parent_identity_manifest_file_sha256,
    )
    if (
        identity.subject_kind == "live"
        or not identity.subject_kind
        or any(
            character not in "abcdefghijklmnopqrstuvwxyz0123456789_-"
            for character in identity.subject_kind
        )
        or any(value is None for value in descendant_fields)
    ):
        raise RuntimeOwnerError(
            "CANDIDATE_DATABASE_IDENTITY_INVALID",
            "The rollback database requires a canonical non-Live descendant identity.",
        )
    current_path_hash = hashlib.sha256(str(database_path).encode("utf-8")).hexdigest()
    subject_document = {
        "version": 1,
        "databaseLineageId": identity.database_lineage_id,
        "subjectKind": identity.subject_kind,
        "resolvedPathHash": current_path_hash,
        "platformFileIdentity": current_file_identity.to_dict(),
        "parentBackupId": identity.parent_backup_id,
        "parentManifestSha256": identity.parent_manifest_sha256,
    }
    current_subject_id = hashlib.sha256(
        canonical_json_bytes(subject_document)
    ).hexdigest()
    if (
        identity.database_path != database_path
        or identity.resolved_path_hash != current_path_hash
        or identity.platform_file_identity != current_file_identity
        or identity.subject_database_id != current_subject_id
    ):
        raise RuntimeOwnerError(
            "CANDIDATE_DATABASE_IDENTITY_MISMATCH",
            "The rollback database does not match its descendant identity.",
        )
    assert identity.parent_database_identity_manifest_path is not None
    try:
        parent = load_database_evidence_identity_manifest(
            identity.parent_database_identity_manifest_path
        )
    except DatabaseIdentityError as error:
        raise RuntimeOwnerError(error.code, str(error)) from error
    if (
        parent.subject_kind != "live"
        or identity.parent_subject_database_id != parent.subject_database_id
        or identity.parent_identity_manifest_file_sha256
        != parent.identity_manifest_file_sha256
        or identity.database_lineage_id != parent.database_lineage_id
        or identity.origin_receipt_path != parent.origin_receipt_path
        or identity.origin_receipt_file_sha256 != parent.origin_receipt_file_sha256
        or identity.origin_receipt_sha256 != parent.origin_receipt_sha256
    ):
        raise RuntimeOwnerError(
            "CANDIDATE_DATABASE_IDENTITY_CHAIN_MISMATCH",
            "The rollback descendant identity no longer matches its Live parent.",
        )
    return identity


def _candidate_namespace(value: str) -> str:
    if (
        not isinstance(value, str)
        or value != value.strip()
        or not value
        or value == "production"
    ):
        raise RuntimeOwnerError(
            "CANDIDATE_RUNTIME_NAMESPACE_INVALID",
            "The rollback rehearsal requires a non-production candidate namespace.",
        )
    return value


def _load_node_active_marker(
    marker_path: Path,
) -> tuple[bytes, dict[str, Any], tuple[int, int, object]]:
    try:
        payload = marker_path.read_bytes()
        metadata = marker_path.stat()
        platform_identity = read_platform_file_identity(marker_path)
    except (DatabaseIdentityError, OSError) as error:
        code = getattr(error, "code", "OWNER_MARKER_READ_FAILED")
        raise RuntimeOwnerError(code, "Could not attest the Live owner marker.") from error
    document = _strict_owner_document(payload)
    process_id = document["processId"]
    listener_port = document["listenerPort"]
    database_paths = document["databasePaths"]
    try:
        listener = ipaddress.ip_address(document["listenerHost"])
    except (TypeError, ValueError) as error:
        raise RuntimeOwnerError(
            "OWNER_MARKER_INVALID",
            "The Live owner marker listener is invalid.",
        ) from error
    if (
        document["markerKind"] != "runtime-owner"
        or document["ownerState"] != "node_active"
        or document["runtimeNamespace"] != "production"
        or not isinstance(process_id, int)
        or isinstance(process_id, bool)
        or process_id <= 0
        or not listener.is_loopback
        or not isinstance(listener_port, int)
        or isinstance(listener_port, bool)
        or not 1 <= listener_port <= 65535
        or not isinstance(database_paths, list)
        or len(database_paths) != 1
    ):
        raise RuntimeOwnerError(
            "OWNER_MARKER_NOT_NODE_ACTIVE",
            "The rollback rehearsal requires one exact node_active production marker.",
        )
    database_path = Path(database_paths[0]).expanduser()
    if not database_path.is_absolute() or database_path.resolve() != database_path:
        raise RuntimeOwnerError(
            "OWNER_MARKER_INVALID",
            "The Live owner marker database path is not canonical.",
        )
    return payload, document, (metadata.st_size, metadata.st_mtime_ns, platform_identity)


def _verify_marker_unchanged(
    marker_path: Path,
    *,
    expected_payload: bytes,
    expected_metadata: tuple[int, int, object],
) -> None:
    try:
        payload = marker_path.read_bytes()
        metadata = marker_path.stat()
        platform_identity = read_platform_file_identity(marker_path)
    except (DatabaseIdentityError, OSError) as error:
        raise RuntimeOwnerError(
            "LIVE_OWNER_DRIFT",
            "The Live owner marker disappeared during rollback rehearsal.",
        ) from error
    actual_metadata = (metadata.st_size, metadata.st_mtime_ns, platform_identity)
    if payload != expected_payload or actual_metadata != expected_metadata:
        raise RuntimeOwnerError(
            "LIVE_OWNER_DRIFT",
            "The Live owner marker changed during rollback rehearsal.",
        )
    _strict_owner_document(payload)


def _rollback_runtime_inspector(
    marker_document: Mapping[str, Any],
) -> RuntimeInspector:
    try:
        return WindowsRuntimeInspector(
            expected_entrypoint_path=marker_document["entrypointPath"],
            tracked_database_paths=tuple(marker_document["databasePaths"]),
        )
    except Exception as error:
        raise RuntimeOwnerError(
            "LIVE_OWNER_INSPECTOR_UNAVAILABLE",
            "Could not initialize the read-only Live runtime inspector.",
        ) from error


def _verify_live_owner_unchanged(
    *,
    marker_path: Path,
    expected_payload: bytes,
    expected_metadata: tuple[int, int, object],
    expected_document: Mapping[str, Any],
    runtime_inspector: RuntimeInspector,
) -> None:
    _verify_marker_unchanged(
        marker_path,
        expected_payload=expected_payload,
        expected_metadata=expected_metadata,
    )
    try:
        snapshot = runtime_inspector.snapshot()
    except Exception as error:
        raise RuntimeOwnerError(
            "LIVE_OWNER_INSPECTION_FAILED",
            "Could not re-sample the Live runtime owner.",
        ) from error
    if snapshot.live_python_roles:
        raise RuntimeOwnerError(
            "LIVE_PYTHON_ROLE_PRESENT",
            "A Live Python role appeared during rollback rehearsal.",
        )
    expected_database_paths = tuple(
        Path(value).expanduser().resolve()
        for value in expected_document["databasePaths"]
    )
    owner_matches = tuple(
        process
        for process in snapshot.node_processes
        if process.pid == expected_document["processId"]
    )
    if len(owner_matches) != 1:
        raise RuntimeOwnerError(
            "LIVE_OWNER_DRIFT",
            "The marker PID no longer identifies one Live Node owner.",
        )
    process = owner_matches[0]
    for other in snapshot.node_processes:
        if other is process:
            continue
        other_databases = tuple(
            Path(path).expanduser().resolve() for path in other.database_paths
        )
        if any(path in expected_database_paths for path in other_databases):
            raise RuntimeOwnerError(
                "LIVE_OWNER_DRIFT",
                "Another Node process acquired the Live database handle.",
            )
    if (
        process.pid != expected_document["processId"]
        or process.process_role != "node"
        or process.environment != "live"
        or Path(process.executable_path).expanduser().resolve()
        != Path(expected_document["executablePath"]).expanduser().resolve()
        or Path(process.entrypoint_path).expanduser().resolve()
        != Path(expected_document["entrypointPath"]).expanduser().resolve()
        or Path(process.cwd).expanduser().resolve()
        != Path(expected_document["cwd"]).expanduser().resolve()
        or process.argv != tuple(expected_document["argv"])
        or process.listener_host != expected_document["listenerHost"]
        or process.listener_port != expected_document["listenerPort"]
        or tuple(Path(path).expanduser().resolve() for path in process.database_paths)
        != expected_database_paths
    ):
        raise RuntimeOwnerError(
            "LIVE_OWNER_DRIFT",
            "The Live Node PID, listener, or database handle changed during rollback rehearsal.",
        )
    _verify_marker_unchanged(
        marker_path,
        expected_payload=expected_payload,
        expected_metadata=expected_metadata,
    )


def _validate_rollback_evidence_output(
    output: Path,
    *,
    protected_paths: Sequence[Path],
) -> None:
    if output in protected_paths:
        raise RuntimeOwnerError(
            "CANDIDATE_ROLLBACK_OUTPUT_INVALID",
            "Rollback evidence must be distinct from all protected inputs.",
        )
    if output.exists():
        raise RuntimeOwnerError(
            "EVIDENCE_OUTPUT_EXISTS",
            "Rollback evidence already exists and will not be overwritten.",
        )


def _strict_role_lease_document(payload: bytes) -> dict[str, Any]:
    def reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate key")
            result[key] = value
        return result

    try:
        document = json.loads(
            payload.decode("utf-8"),
            object_pairs_hook=reject_duplicates,
        )
    except (UnicodeError, json.JSONDecodeError, ValueError) as error:
        raise RuntimeOwnerError(
            "CANDIDATE_LEASE_INVALID",
            "A candidate role lease is not valid canonical JSON.",
        ) from error
    if (
        not isinstance(document, dict)
        or tuple(document) != _ROLE_LEASE_FIELDS
        or canonical_json_bytes(document) != payload
    ):
        raise RuntimeOwnerError(
            "CANDIDATE_LEASE_INVALID",
            "A candidate role lease has invalid fields or serialization.",
        )
    unsigned = {field: document[field] for field in _ROLE_LEASE_UNSIGNED_FIELDS}
    lease_kind = document.get("leaseKind")
    role = document.get("role")
    if (
        document["schemaVersion"] != 1
        or document["environment"] != "candidate"
        or (
            (lease_kind == "runtime-role" and role not in {"worker", "scheduler"})
            or (lease_kind == "runtime-api-presence" and role != "api")
            or lease_kind not in {"runtime-role", "runtime-api-presence"}
        )
        or not isinstance(document["leaseSha256"], str)
        or hashlib.sha256(canonical_json_bytes(unsigned)).hexdigest()
        != document["leaseSha256"]
    ):
        raise RuntimeOwnerError(
            "CANDIDATE_LEASE_INVALID",
            "A candidate role lease failed its strict contract.",
        )
    key_document = {
        "version": 1,
        "environment": document["environment"],
        "databaseLineageId": document["databaseLineageId"],
        "subjectDatabaseId": document["subjectDatabaseId"],
        "runtimeNamespace": document["runtimeNamespace"],
        "role": document["role"],
    }
    if lease_kind == "runtime-api-presence":
        key_document.update(
            {
                "ownerId": document["ownerId"],
                "pid": document["pid"],
            }
        )
    if hashlib.sha256(canonical_json_bytes(key_document)).hexdigest() != document[
        "keyHash"
    ]:
        raise RuntimeOwnerError(
            "CANDIDATE_LEASE_INVALID",
            "A candidate role lease key hash is invalid.",
        )
    return document


def _matching_candidate_lease(
    document: Mapping[str, Any],
    *,
    identity: DatabaseEvidenceIdentityManifest,
    runtime_namespace: str,
) -> bool:
    return (
        document["databaseLineageId"] == identity.database_lineage_id
        and document["subjectDatabaseId"] == identity.subject_database_id
        and document["runtimeNamespace"] == runtime_namespace
    )


def _role_lease_files(root: Path) -> tuple[Path, ...]:
    if not root.exists():
        return ()
    try:
        return tuple(sorted(root.glob("*.json"), key=lambda path: path.name))
    except OSError as error:
        raise RuntimeOwnerError(
            "CANDIDATE_LEASE_READ_FAILED",
            "Could not enumerate candidate role leases.",
        ) from error


def _drain_candidate_role_leases(
    root: Path,
    *,
    identity: DatabaseEvidenceIdentityManifest,
    runtime_namespace: str,
    database: Path,
    candidate_drain: CandidateRuntimeDrain | None,
) -> tuple[tuple[str, ...], CandidateRuntimeDrain]:
    snapshot: list[tuple[Path, bytes, dict[str, Any]]] = []
    for path in _role_lease_files(root):
        try:
            payload = path.read_bytes()
        except OSError as error:
            raise RuntimeOwnerError(
                "CANDIDATE_LEASE_READ_FAILED",
                "Could not read a candidate role lease.",
            ) from error
        snapshot.append((path, payload, _strict_role_lease_document(payload)))

    matching = tuple(
        (path, payload, document)
        for path, payload, document in snapshot
        if _matching_candidate_lease(
            document,
            identity=identity,
            runtime_namespace=runtime_namespace,
        )
    )
    controller = candidate_drain or FilesystemCandidateRuntimeDrain(
        lease_root=root,
        database_identity_manifest=identity.manifest_path,
    )
    for path, payload, _document in matching:
        try:
            if path.read_bytes() != payload:
                raise RuntimeOwnerError(
                    "CANDIDATE_LEASE_DRIFT",
                    "A candidate role lease changed before runtime drain.",
                )
        except FileNotFoundError as error:
            raise RuntimeOwnerError(
                "CANDIDATE_LEASE_DRIFT",
                "A candidate role lease disappeared before runtime drain.",
            ) from error
    try:
        drained = controller.stop_and_wait(
            database=database,
            runtime_namespace=runtime_namespace,
        )
    except BaseException:
        # stop_and_wait performs its own cleanup, but a transient Windows
        # sharing violation can interrupt that cleanup. Keep the controller in
        # this production call frame long enough to retry before propagating.
        controller.release_fence()
        raise
    canonical_roles = ("api", "worker", "scheduler")
    if len(drained) != len(canonical_roles) or set(drained) != set(canonical_roles):
        raise RuntimeOwnerError(
            "CANDIDATE_RUNTIME_DRAIN_INCOMPLETE",
            "The runtime controller did not stop and wait for all candidate roles.",
        )
    return canonical_roles, controller


def _verify_candidate_role_leases_empty(
    root: Path,
    *,
    identity: DatabaseEvidenceIdentityManifest,
    runtime_namespace: str,
) -> None:
    for path in _role_lease_files(root):
        try:
            document = _strict_role_lease_document(path.read_bytes())
        except OSError as error:
            raise RuntimeOwnerError(
                "CANDIDATE_LEASE_READ_FAILED",
                "Could not re-read candidate role leases after drain.",
            ) from error
        if _matching_candidate_lease(
            document,
            identity=identity,
            runtime_namespace=runtime_namespace,
        ):
            raise RuntimeOwnerError(
                "CANDIDATE_LEASE_NOT_DRAINED",
                "A matching candidate role lease remains after drain.",
            )


def _validate_rollback_smoke(value: Mapping[str, object]) -> None:
    if (
        not isinstance(value, dict)
        or set(value) != {"paths", "loopback"}
        or value.get("loopback") is not True
        or value.get("paths") != list(_ROLLBACK_SMOKE_PATHS)
    ):
        raise RuntimeOwnerError(
            "CANDIDATE_ROLLBACK_SMOKE_INVALID",
            "The rollback candidate did not satisfy the exact loopback smoke contract.",
        )


def _assigned_loopback_port(host: str) -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind((host, 0))
        return int(listener.getsockname()[1])


def _verify_process(
    process: ProcessEvidence,
    *,
    identity: DatabaseEvidenceIdentityManifest,
    entrypoint: Path,
) -> None:
    if process.pid <= 0 or process.process_role != "node" or process.environment != "live":
        raise RuntimeOwnerError(
            "NODE_OWNER_PROCESS_INVALID",
            "The production owner process metadata is invalid.",
        )
    process_entrypoint = Path(process.entrypoint_path).expanduser().resolve()
    cwd = Path(process.cwd).expanduser().resolve()
    if process_entrypoint != entrypoint or cwd != entrypoint.parent:
        raise RuntimeOwnerError(
            "NODE_OWNER_ENTRYPOINT_MISMATCH",
            "The Node owner entrypoint or cwd does not match the exact server path.",
        )
    if not _argv_names_entrypoint(process.argv, cwd=cwd, entrypoint=entrypoint):
        raise RuntimeOwnerError(
            "NODE_OWNER_ARGV_MISMATCH",
            "The Node owner argv does not name the exact server entrypoint.",
        )
    try:
        listener = ipaddress.ip_address(process.listener_host)
    except ValueError as error:
        raise RuntimeOwnerError(
            "NODE_OWNER_LISTENER_INVALID",
            "The Node owner listener address is invalid.",
        ) from error
    if not listener.is_loopback or not 1 <= process.listener_port <= 65535:
        raise RuntimeOwnerError(
            "NODE_OWNER_LISTENER_INVALID",
            "The Node owner must have one valid loopback listener.",
        )
    database_paths = tuple(Path(path).expanduser().resolve() for path in process.database_paths)
    if database_paths != (identity.database_path,):
        raise RuntimeOwnerError(
            "NODE_OWNER_DATABASE_MISMATCH",
            "The Node owner does not hold the exact Live database file.",
        )


def _argv_names_entrypoint(argv: tuple[str, ...], *, cwd: Path, entrypoint: Path) -> bool:
    if len(argv) < 2:
        return False
    for raw in argv[1:]:
        candidate = Path(raw)
        if not candidate.is_absolute():
            candidate = cwd / candidate
        try:
            if candidate.resolve() == entrypoint:
                return True
        except OSError:
            continue
    return False


def _owner_document(
    *,
    identity: DatabaseEvidenceIdentityManifest,
    process: ProcessEvidence,
    runtime_namespace: str,
    entrypoint: Path,
    created_at: str,
) -> dict[str, Any]:
    return {
        "schemaVersion": 1,
        "markerKind": "runtime-owner",
        "ownerState": "node_active",
        "runtimeNamespace": runtime_namespace,
        "databaseLineageId": identity.database_lineage_id,
        "subjectDatabaseId": identity.subject_database_id,
        "databaseIdentityManifestPath": str(identity.manifest_path),
        "databaseIdentityManifestFileSha256": identity.identity_manifest_file_sha256,
        "originReceiptPath": str(identity.origin_receipt_path),
        "originReceiptFileSha256": identity.origin_receipt_file_sha256,
        "originReceiptSha256": identity.origin_receipt_sha256,
        "entrypointPath": str(entrypoint),
        "processId": process.pid,
        "executablePath": str(Path(process.executable_path).expanduser().resolve()),
        "cwd": str(Path(process.cwd).expanduser().resolve()),
        "argv": list(process.argv),
        "listenerHost": process.listener_host,
        "listenerPort": process.listener_port,
        "databasePaths": [str(Path(path).expanduser().resolve()) for path in process.database_paths],
        "createdAt": created_at,
    }


def _strict_owner_document(payload: bytes) -> dict[str, Any]:
    def reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate key")
            result[key] = value
        return result

    try:
        document = json.loads(
            payload.decode("utf-8"),
            object_pairs_hook=reject_duplicates,
        )
    except (UnicodeError, json.JSONDecodeError, ValueError) as error:
        raise RuntimeOwnerError(
            "OWNER_MARKER_INVALID",
            "The owner marker is not valid canonical UTF-8 JSON.",
        ) from error
    if (
        not isinstance(document, dict)
        or tuple(document) != _OWNER_FIELDS
        or canonical_json_bytes(document) != payload
    ):
        raise RuntimeOwnerError(
            "OWNER_MARKER_INVALID",
            "The owner marker fields or canonical serialization are invalid.",
        )
    unsigned = {field: document[field] for field in _OWNER_UNSIGNED_FIELDS}
    self_hash = document["ownerMarkerSha256"]
    if (
        not isinstance(self_hash, str)
        or hashlib.sha256(canonical_json_bytes(unsigned)).hexdigest() != self_hash
    ):
        raise RuntimeOwnerError(
            "OWNER_MARKER_INVALID",
            "The owner marker self hash is invalid.",
        )
    return document


def _report(
    document: Mapping[str, Any],
    *,
    marker_path: Path,
    payload: bytes,
    verification_mode: str,
) -> RuntimeOwnerReport:
    return RuntimeOwnerReport(
        owner_state=document["ownerState"],
        process_id=document["processId"],
        owner_marker_path=marker_path,
        canonical_bytes=payload,
        owner_marker_file_sha256=hashlib.sha256(payload).hexdigest(),
        database_lineage_id=document["databaseLineageId"],
        subject_database_id=document["subjectDatabaseId"],
        origin_receipt_file_sha256=document["originReceiptFileSha256"],
        verification_mode=verification_mode,
    )


def _absolute_existing_file(value: str | os.PathLike[str], description: str) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        raise RuntimeOwnerError(
            "RUNTIME_OWNER_PATH_INVALID",
            f"The {description} path must be exact and absolute.",
        )
    resolved = path.resolve()
    if not resolved.is_file():
        raise RuntimeOwnerError(
            "RUNTIME_OWNER_PATH_INVALID",
            f"The {description} path does not name a file.",
        )
    return resolved


def _absolute_output(value: str | os.PathLike[str], description: str) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        raise RuntimeOwnerError(
            "RUNTIME_OWNER_PATH_INVALID",
            f"The {description} path must be exact and absolute.",
        )
    return path.resolve(strict=False)


def _timestamp(value: datetime) -> str:
    if value.tzinfo is None:
        raise RuntimeOwnerError(
            "RUNTIME_OWNER_CLOCK_INVALID",
            "Owner timestamps require a timezone-aware clock.",
        )
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace(
        "+00:00",
        "Z",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="study-app-runtime-owner")
    commands = parser.add_subparsers(dest="command", required=True)

    def add_origin_chain_arguments(command: argparse.ArgumentParser) -> None:
        command.add_argument("--database", required=True)
        command.add_argument("--p0-origin-receipt", required=True)
        command.add_argument("--expected-p0-origin-receipt-sha256", required=True)
        command.add_argument("--origin-backup", required=True)
        command.add_argument("--origin-manifest", required=True)

    create = commands.add_parser("create-live-database-identity")
    add_origin_chain_arguments(create)
    create.add_argument("--output", required=True)

    verify = commands.add_parser("verify-live-database-identity")
    add_origin_chain_arguments(verify)
    verify.add_argument("--database-identity-manifest", required=True)

    descendant = commands.add_parser("create-descendant-database-identity")
    descendant.add_argument("--database", required=True)
    descendant.add_argument("--subject-kind", required=True)
    descendant.add_argument("--parent-database-identity-manifest", required=True)
    descendant.add_argument("--parent-backup", required=True)
    descendant.add_argument("--parent-manifest", required=True)
    descendant.add_argument("--output", required=True)

    def add_owner_arguments(command: argparse.ArgumentParser) -> None:
        command.add_argument("--database-identity-manifest", required=True)
        command.add_argument("--p0-origin-receipt", required=True)
        command.add_argument("--expected-p0-origin-receipt-sha256", required=True)
        command.add_argument("--origin-backup", required=True)
        command.add_argument("--origin-manifest", required=True)
        command.add_argument("--runtime-namespace", required=True)
        command.add_argument("--expected-entrypoint-path", required=True)
        command.add_argument("--owner-marker", required=True)

    initialize = commands.add_parser("initialize-node-owner")
    add_owner_arguments(initialize)
    verify_owner = commands.add_parser("verify-node-owner")
    add_owner_arguments(verify_owner)

    rollback = commands.add_parser("candidate-rollback-smoke")
    rollback.add_argument("--database", required=True)
    rollback.add_argument("--database-identity-manifest", required=True)
    rollback.add_argument("--candidate-runtime-namespace", required=True)
    rollback.add_argument("--owner-marker", required=True)
    rollback.add_argument("--rollback-profile", required=True)
    rollback.add_argument("--evidence-output", required=True)
    return parser


def run(
    arguments: Sequence[str],
    *,
    runner: CandidateRollbackRunner | None = None,
    lease_root: str | os.PathLike[str] | None = None,
) -> dict[str, Any]:
    options = build_parser().parse_args(list(arguments))
    if options.command == "create-live-database-identity":
        manifest = DatabaseEvidenceIdentityService().create_live_database_identity(
            database=options.database,
            p0_origin_receipt=options.p0_origin_receipt,
            expected_p0_origin_receipt_sha256=(
                options.expected_p0_origin_receipt_sha256
            ),
            origin_backup=options.origin_backup,
            origin_manifest=options.origin_manifest,
            output=options.output,
        )
        return {
            "ok": True,
            "operation": "createLiveDatabaseIdentity",
            "manifestPath": str(manifest.manifest_path),
            "identityManifestFileSha256": manifest.identity_manifest_file_sha256,
            "databaseLineageId": manifest.database_lineage_id,
            "subjectDatabaseId": manifest.subject_database_id,
            "subjectKind": manifest.subject_kind,
        }
    if options.command == "verify-live-database-identity":
        manifest = DatabaseEvidenceIdentityService().verify_live_database_identity(
            database_identity_manifest=options.database_identity_manifest,
            p0_origin_receipt=options.p0_origin_receipt,
            expected_p0_origin_receipt_sha256=options.expected_p0_origin_receipt_sha256,
            origin_backup=options.origin_backup,
            origin_manifest=options.origin_manifest,
        )
        return {
            "ok": True,
            "operation": "verifyLiveDatabaseIdentity",
            "verificationMode": "read_only",
            "manifestPath": str(manifest.manifest_path),
            "identityManifestFileSha256": manifest.identity_manifest_file_sha256,
            "databaseLineageId": manifest.database_lineage_id,
            "subjectDatabaseId": manifest.subject_database_id,
            "subjectKind": manifest.subject_kind,
        }
    if options.command == "create-descendant-database-identity":
        manifest = DatabaseEvidenceIdentityService().create_descendant_database_identity(
            database=options.database,
            subject_kind=options.subject_kind,
            parent_database_identity_manifest=options.parent_database_identity_manifest,
            parent_backup=options.parent_backup,
            parent_manifest=options.parent_manifest,
            output=options.output,
        )
        return {
            "ok": True,
            "operation": "createDescendantDatabaseIdentity",
            "manifestPath": str(manifest.manifest_path),
            "identityManifestFileSha256": manifest.identity_manifest_file_sha256,
            "databaseLineageId": manifest.database_lineage_id,
            "subjectDatabaseId": manifest.subject_database_id,
            "subjectKind": manifest.subject_kind,
        }
    if options.command == "candidate-rollback-smoke":
        manifest_path = _absolute_existing_file(
            options.database_identity_manifest,
            "candidate database identity manifest",
        )
        resolved_lease_root = (
            Path(lease_root).expanduser().resolve(strict=False)
            if lease_root is not None
            else manifest_path.parent / "candidate-leases"
        )
        return CandidateRollbackSmokeService(
            runner=runner or FrozenNodeRollbackRunner(),
            lease_root=resolved_lease_root,
            candidate_drain=FilesystemCandidateRuntimeDrain(
                lease_root=resolved_lease_root,
                database_identity_manifest=manifest_path,
            ),
        ).run(
            database=options.database,
            database_identity_manifest=manifest_path,
            candidate_runtime_namespace=options.candidate_runtime_namespace,
            owner_marker=options.owner_marker,
            rollback_profile=options.rollback_profile,
            evidence_output=options.evidence_output,
        )
    if options.command in {"initialize-node-owner", "verify-node-owner"}:
        try:
            identity = load_database_evidence_identity_manifest(
                options.database_identity_manifest
            )
        except DatabaseIdentityError as error:
            raise RuntimeOwnerError(error.code, str(error)) from error
        inspector = WindowsRuntimeInspector(
            expected_entrypoint_path=options.expected_entrypoint_path,
            tracked_database_paths=(identity.database_path,),
        )
        service = RuntimeOwnerService(inspector)
        arguments = {
            "database_identity_manifest": options.database_identity_manifest,
            "p0_origin_receipt": options.p0_origin_receipt,
            "expected_p0_origin_receipt_sha256": options.expected_p0_origin_receipt_sha256,
            "origin_backup": options.origin_backup,
            "origin_manifest": options.origin_manifest,
            "runtime_namespace": options.runtime_namespace,
            "expected_entrypoint_path": options.expected_entrypoint_path,
            "owner_marker": options.owner_marker,
        }
        report = (
            service.initialize_node_owner(**arguments)
            if options.command == "initialize-node-owner"
            else service.verify_node_owner(**arguments)
        )
        return {
            "ok": True,
            "operation": (
                "initializeNodeOwner"
                if options.command == "initialize-node-owner"
                else "verifyNodeOwner"
            ),
            "verificationMode": report.verification_mode,
            "ownerState": report.owner_state,
            "ownerMarkerPath": str(report.owner_marker_path),
            "ownerMarkerFileSha256": report.owner_marker_file_sha256,
            "processId": report.process_id,
            "databaseLineageId": report.database_lineage_id,
            "subjectDatabaseId": report.subject_database_id,
            "originReceiptFileSha256": report.origin_receipt_file_sha256,
        }
    raise RuntimeOwnerError("RUNTIME_OWNER_COMMAND_INVALID", "Unsupported command.")


def main(arguments: Sequence[str] | None = None) -> int:
    try:
        payload = run(sys.argv[1:] if arguments is None else arguments)
    except (DatabaseIdentityError, RuntimeOwnerError) as error:
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": {"code": error.code, "message": str(error)},
                },
                ensure_ascii=False,
                separators=(",", ":"),
            ),
            file=sys.stderr,
        )
        return 2
    except Exception:
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": {
                        "code": "RUNTIME_OWNER_UNEXPECTED_ERROR",
                        "message": "The runtime owner command failed unexpectedly.",
                    },
                },
                ensure_ascii=False,
                separators=(",", ":"),
            ),
            file=sys.stderr,
        )
        return 2
    print(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
