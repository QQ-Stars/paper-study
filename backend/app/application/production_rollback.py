from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import base64
import hashlib
import json
import os
import re

from backend.app.api.compat.database_identity import (
    DatabaseIdentityError,
    canonical_json_bytes,
    exclusive_write_bytes,
)


ROLLBACK_TAIL_EVENTS = (
    "authorization_cleared",
    "python_ingress_drained",
    "worker_claims_drained",
    "scheduler_obsidian_mcp_stopped",
    "fastapi_stopped",
    "role_locks_connections_released",
    "frozen_node_started",
    "legacy_smoked",
    "owner_node_active",
)

_ROLLBACK_STATES = frozenset({"node_quiesced", "handoff_pending", "python_active"})
_CONTAINER_MAP_FIELDS = frozenset(
    {
        "imageDigest",
        "entrypointPath",
        "cwd",
        "host",
        "ports",
        "databasePath",
        "environment",
    }
)
_NATIVE_MAP_FIELDS = frozenset(
    {
        "deploymentKind",
        "executablePath",
        "executableSha256",
        "entrypointPath",
        "entrypointSha256",
        "cwd",
        "host",
        "ports",
        "databasePath",
        "environment",
    }
)
_ROLLBACK_ENVIRONMENT = {
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


class ProductionRollbackError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(f"{code}: {message}")


@dataclass(frozen=True, slots=True)
class ProductionRecovery:
    path: Path
    file_sha256: str
    receipt_id: str
    owner_state: str
    events: tuple[str, ...]
    canonical_bytes: bytes


_RECOVERY_LEASE_FIELDS = frozenset(
    {
        "schemaVersion",
        "leaseKind",
        "receiptId",
        "handoffReceiptPath",
        "handoffReceiptFileSha256",
        "startupSnapshotPath",
        "startupSnapshotFileSha256",
        "buildIdentityManifestPath",
        "buildIdentityManifestSha256",
        "databaseIdentityManifestPath",
        "databaseIdentityManifestSha256",
        "originReceiptPath",
        "originReceiptFileSha256",
        "ownerMarkerPath",
        "cutoverLeasePath",
        "rollbackMapSha256",
        "events",
        "phase",
        "version",
        "previousLeaseFileSha256",
        "updatedAt",
        "recoveryLeaseSha256",
    }
)
_RECOVERY_FIELDS = frozenset(
    {
        "schemaVersion",
        "recoveryKind",
        "receiptId",
        "handoffReceiptPath",
        "handoffReceiptFileSha256",
        "startupSnapshotPath",
        "startupSnapshotFileSha256",
        "ownerMarkerPath",
        "ownerState",
        "events",
        "legacySmoke",
        "completedAt",
        "recoverySha256",
    }
)


class ProductionRollbackCoordinator:
    def __init__(
        self,
        *,
        operations: object,
        clock: Callable[[], datetime] | None = None,
        crash_after_event: Callable[[str], None] | None = None,
    ) -> None:
        self._operations = operations
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._crash_after_event = crash_after_event

    def rollback_production(
        self,
        *,
        handoff_receipt: str | os.PathLike[str],
        expected_handoff_receipt_sha256: str,
        startup_snapshot: str | os.PathLike[str],
        expected_startup_snapshot_sha256: str,
        build_identity_manifest: str | os.PathLike[str],
        database_identity_manifest: str | os.PathLike[str],
        p0_origin_receipt: str | os.PathLike[str],
        expected_p0_origin_receipt_sha256: str,
        owner_marker: str | os.PathLike[str],
        recovery_lease_output: str | os.PathLike[str],
        recovery_output: str | os.PathLike[str],
    ) -> ProductionRecovery:
        bundle = _verify_rollback_identities(
            handoff_receipt=handoff_receipt,
            expected_handoff_receipt_sha256=expected_handoff_receipt_sha256,
            startup_snapshot=startup_snapshot,
            expected_startup_snapshot_sha256=expected_startup_snapshot_sha256,
            build_identity_manifest=build_identity_manifest,
            database_identity_manifest=database_identity_manifest,
            p0_origin_receipt=p0_origin_receipt,
            expected_p0_origin_receipt_sha256=expected_p0_origin_receipt_sha256,
            owner_marker=owner_marker,
        )
        receipt = bundle["receipt"]
        receipt_document = bundle["receipt_document"]
        snapshot = bundle["snapshot"]
        cutover_lease = bundle["cutover_lease"]
        owner_path = bundle["owner_path"]
        original_owner = bundle["original_owner"]
        recovery_lease_path = Path(recovery_lease_output).resolve(strict=False)
        recovery_path = Path(recovery_output).resolve(strict=False)
        if (
            recovery_lease_path == recovery_path
            or recovery_lease_path.exists() and not recovery_lease_path.is_file()
            or recovery_path.exists() and not recovery_path.is_file()
            or recovery_lease_path.parent != recovery_path.parent
        ):
            raise ProductionRollbackError(
                "PRODUCTION_ROLLBACK_OUTPUT_INVALID",
                "Recovery outputs must be distinct files in one explicit directory.",
            )
        if recovery_lease_path.exists():
            lease_payload, recovery_lease = _load_recovery_lease(
                recovery_lease_path,
                expected_receipt_path=receipt.path,
                expected_receipt_sha256=receipt.file_sha256,
            )
        else:
            lease_payload, recovery_lease = _create_recovery_lease(
                path=recovery_lease_path,
                receipt_document=receipt_document,
                receipt_path=receipt.path,
                receipt_file_sha256=receipt.file_sha256,
                snapshot_path=snapshot.path,
                snapshot_file_sha256=snapshot.file_sha256,
                build_identity_manifest_path=snapshot.build_identity_manifest_path,
                build_identity_manifest_sha256=snapshot.build_identity_manifest_sha256,
                database_identity_manifest_path=snapshot.database_identity_manifest_path,
                database_identity_manifest_sha256=snapshot.database_identity_manifest_sha256,
                origin_receipt_path=snapshot.origin_receipt_path,
                origin_receipt_file_sha256=snapshot.origin_receipt_file_sha256,
                owner_marker_path=owner_path,
                cutover_lease_path=bundle["cutover_lease_path"],
                rollback_map_sha256=snapshot.rollback_map_sha256,
                clock=self._clock,
            )
        events = list(recovery_lease["events"])
        if recovery_lease["phase"] == "completed":
            return _load_production_recovery(
                recovery_path,
                expected_receipt_id=receipt.receipt_id,
            )
        if recovery_lease["phase"] not in {"running", "recovery_failed"}:
            raise ProductionRollbackError(
                "PRODUCTION_RECOVERY_LEASE_INVALID",
                "The production recovery lease phase is invalid.",
            )
        expected_sequence = ("owner_handoff_pending", *ROLLBACK_TAIL_EVENTS)
        if tuple(events) != expected_sequence[: len(events)]:
            raise ProductionRollbackError(
                "PRODUCTION_RECOVERY_LEASE_INVALID",
                "The durable recovery event prefix is invalid.",
            )
        owner_payload = owner_path.read_bytes()
        if not events:
            owner_document = _load_p6_owner(owner_payload)
            if (
                owner_document.get("ownerState") != "python_active"
                or owner_document.get("handoffReceiptPath") != str(receipt.path)
                or owner_document.get("handoffReceiptFileSha256")
                != receipt.file_sha256
            ):
                raise ProductionRollbackError(
                    "PRODUCTION_ROLLBACK_OWNER_MISMATCH",
                    "The active Python owner does not name the exact handoff receipt.",
                )
            pending_payload = _pending_owner_payload(owner_document)
            from backend.app.application.final_window import _cas_replace

            _cas_replace(owner_path, owner_payload, pending_payload)
            owner_payload = pending_payload
            lease_payload, recovery_lease = self._record_event(
                recovery_lease_path,
                lease_payload,
                recovery_lease,
                "owner_handoff_pending",
            )
            events.append("owner_handoff_pending")
        elif events[-1] != "owner_node_active":
            current_document = _load_p6_owner(owner_payload)
            if current_document.get("ownerState") != "handoff_pending":
                raise ProductionRollbackError(
                    "PRODUCTION_ROLLBACK_OWNER_MISMATCH",
                    "Resumed recovery requires the durable handoff_pending owner.",
                )
        elif owner_payload != original_owner:
            raise ProductionRollbackError(
                "PRODUCTION_ROLLBACK_OWNER_MISMATCH",
                "Completed recovery did not restore the frozen Node owner bytes.",
            )

        operation_steps = (
            ("authorization_cleared", "clear_authorization"),
            ("python_ingress_drained", "drain_python_ingress"),
            ("worker_claims_drained", "drain_worker_claims"),
            ("scheduler_obsidian_mcp_stopped", "stop_scheduler_obsidian_mcp"),
            ("fastapi_stopped", "stop_fastapi"),
            ("role_locks_connections_released", "release_locks_connections"),
        )
        handle: object | None = None
        smoke: Mapping[str, object] = {"ok": True}
        try:
            for event, method_name in operation_steps:
                if event in events:
                    continue
                method = getattr(self._operations, method_name, None)
                if not callable(method):
                    raise ProductionRollbackError(
                        "ROLLBACK_OPERATIONS_INVALID",
                        f"The rollback operations object omitted {method_name}.",
                    )
                method()
                lease_payload, recovery_lease = self._record_event(
                    recovery_lease_path,
                    lease_payload,
                    recovery_lease,
                    event,
                )
                events.append(event)
            if "frozen_node_started" not in events:
                start = getattr(self._operations, "start_frozen_node", None)
                if not callable(start):
                    raise ProductionRollbackError(
                        "ROLLBACK_OPERATIONS_INVALID",
                        "The rollback operations object omitted frozen Node start.",
                    )
                handle = start(dict(snapshot.rollback_map))
                lease_payload, recovery_lease = self._record_event(
                    recovery_lease_path,
                    lease_payload,
                    recovery_lease,
                    "frozen_node_started",
                )
                events.append("frozen_node_started")
            elif "legacy_smoked" not in events:
                attach = getattr(self._operations, "attach_frozen_node", None)
                if not callable(attach):
                    raise ProductionRollbackError(
                        "ROLLBACK_OPERATIONS_INVALID",
                        "Resumed rollback requires an existing frozen Node attachment.",
                    )
                handle = attach(dict(snapshot.rollback_map))
            if "legacy_smoked" not in events:
                smoke_method = getattr(self._operations, "smoke_legacy", None)
                if not callable(smoke_method):
                    raise ProductionRollbackError(
                        "ROLLBACK_OPERATIONS_INVALID",
                        "The rollback operations object omitted legacy smoke.",
                    )
                smoke = smoke_method(handle)
                if not isinstance(smoke, Mapping) or smoke.get("ok") is not True:
                    raise ProductionRollbackError(
                        "ROLLBACK_LEGACY_SMOKE_FAILED",
                        "The frozen Node legacy smoke failed.",
                    )
                lease_payload, recovery_lease = self._record_event(
                    recovery_lease_path,
                    lease_payload,
                    recovery_lease,
                    "legacy_smoked",
                )
                events.append("legacy_smoked")
            if "owner_node_active" not in events:
                from backend.app.application.final_window import _cas_replace

                current_owner = owner_path.read_bytes()
                if current_owner != original_owner:
                    _cas_replace(owner_path, current_owner, original_owner)
                lease_payload, recovery_lease = self._record_event(
                    recovery_lease_path,
                    lease_payload,
                    recovery_lease,
                    "owner_node_active",
                )
                events.append("owner_node_active")
        except BaseException:
            if self._crash_after_event is None:
                try:
                    _set_recovery_phase(
                        recovery_lease_path,
                        expected_payload=lease_payload,
                        document=recovery_lease,
                        phase="recovery_failed",
                        clock=self._clock,
                    )
                except Exception:
                    pass
            raise
        validate_rollback_tail(
            initial_owner_state="python_active",
            events=events,
        )
        recovery_unsigned = {
            "schemaVersion": 1,
            "recoveryKind": "production-rollback",
            "receiptId": receipt.receipt_id,
            "handoffReceiptPath": str(receipt.path),
            "handoffReceiptFileSha256": receipt.file_sha256,
            "startupSnapshotPath": str(snapshot.path),
            "startupSnapshotFileSha256": snapshot.file_sha256,
            "ownerMarkerPath": str(owner_path),
            "ownerState": "node_active",
            "events": events,
            "legacySmoke": dict(smoke),
            "completedAt": _aware_timestamp(self._clock()),
        }
        recovery_payload = _hashed_document(recovery_unsigned, "recoverySha256")
        if recovery_path.exists():
            existing = _load_production_recovery(
                recovery_path,
                expected_receipt_id=receipt.receipt_id,
            )
            if existing.canonical_bytes != recovery_payload:
                raise ProductionRollbackError(
                    "PRODUCTION_RECOVERY_MISMATCH",
                    "The existing production recovery record does not match.",
                )
            recovery = existing
        else:
            try:
                exclusive_write_bytes(recovery_path, recovery_payload)
            except DatabaseIdentityError as error:
                raise ProductionRollbackError(error.code, str(error)) from error
            recovery = _recovery_from_payload(recovery_path, recovery_payload)
        _set_recovery_phase(
            recovery_lease_path,
            expected_payload=lease_payload,
            document=recovery_lease,
            phase="completed",
            clock=self._clock,
        )
        return recovery

    def _record_event(
        self,
        path: Path,
        payload: bytes,
        document: Mapping[str, object],
        event: str,
    ) -> tuple[bytes, dict[str, object]]:
        events = [*document["events"], event]
        updated_payload, updated = _update_recovery_lease(
            path,
            expected_payload=payload,
            document=document,
            changes={"events": events, "phase": "running"},
            clock=self._clock,
        )
        if self._crash_after_event is not None:
            self._crash_after_event(event)
        return updated_payload, updated


def validate_rollback_tail(
    *,
    initial_owner_state: str,
    events: Sequence[str],
    completed: bool = True,
) -> tuple[str, ...]:
    if initial_owner_state not in _ROLLBACK_STATES:
        raise ProductionRollbackError(
            "ROLLBACK_ORDER_INVALID",
            "Rollback must start from a known non-active or Python owner state.",
        )
    actual = tuple(events)
    expected = ROLLBACK_TAIL_EVENTS
    if initial_owner_state == "python_active":
        expected = ("owner_handoff_pending", *expected)
    valid = actual == expected if completed else actual == expected[: len(actual)]
    if (
        not valid
        or len(actual) > len(expected)
        or (not completed and "owner_node_active" in actual)
    ):
        raise ProductionRollbackError(
            "ROLLBACK_ORDER_INVALID",
            "Rollback events do not preserve the required non-active recovery order.",
        )
    return actual


def validate_frozen_node_rollback_map(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        raise _invalid_map("The frozen Node rollback map fields are invalid.")
    if value.get("deploymentKind") == "native-windows":
        if set(value) != _NATIVE_MAP_FIELDS:
            raise _invalid_map("The native frozen Node rollback map fields are invalid.")
        return _validate_native_rollback_map(value)
    if set(value) != _CONTAINER_MAP_FIELDS:
        raise _invalid_map("The frozen Node rollback map fields are invalid.")
    digest = value.get("imageDigest")
    if not isinstance(digest, str) or re.fullmatch(r"sha256:[0-9a-f]{64}", digest) is None:
        raise _invalid_map("The frozen Node image digest is invalid.")
    for field in ("entrypointPath", "cwd", "databasePath"):
        raw = value.get(field)
        if not isinstance(raw, str) or not raw or not Path(raw).is_absolute():
            raise _invalid_map(f"The rollback {field} must be an absolute path.")
    if value.get("host") not in {"127.0.0.1", "::1"}:
        raise _invalid_map("The frozen Node host must be loopback.")
    ports = value.get("ports")
    if (
        not isinstance(ports, dict)
        or not ports
        or any(
            not isinstance(name, str)
            or not name
            or not isinstance(port, int)
            or isinstance(port, bool)
            or not 1 <= port <= 65535
            for name, port in ports.items()
        )
    ):
        raise _invalid_map("The frozen Node port map is invalid.")
    environment = value.get("environment")
    if not isinstance(environment, Mapping) or dict(environment) != _ROLLBACK_ENVIRONMENT:
        raise _invalid_map("The frozen Node startup environment is not the exact rollback map.")
    return {
        "imageDigest": digest,
        "entrypointPath": value["entrypointPath"],
        "cwd": value["cwd"],
        "host": value["host"],
        "ports": dict(ports),
        "databasePath": value["databasePath"],
        "environment": dict(environment),
    }


def _validate_native_rollback_map(value: Mapping[str, object]) -> dict[str, object]:
    paths: dict[str, Path] = {}
    for field in ("executablePath", "entrypointPath", "cwd", "databasePath"):
        raw = value.get(field)
        if not isinstance(raw, str) or not raw or not Path(raw).is_absolute():
            raise _invalid_map(f"The native rollback {field} must be an absolute path.")
        path = Path(raw)
        try:
            resolved = path.resolve(strict=True)
        except OSError as error:
            raise _invalid_map(f"The native rollback {field} does not exist.") from error
        if resolved != path:
            raise _invalid_map(f"The native rollback {field} must be canonical.")
        paths[field] = resolved
    if not paths["executablePath"].is_file() or not paths["entrypointPath"].is_file():
        raise _invalid_map("The native rollback executables must be files.")
    if not paths["cwd"].is_dir() or not paths["databasePath"].is_file():
        raise _invalid_map("The native rollback cwd or database path is invalid.")
    for path_field, sha_field in (
        ("executablePath", "executableSha256"),
        ("entrypointPath", "entrypointSha256"),
    ):
        expected = value.get(sha_field)
        actual = hashlib.sha256(paths[path_field].read_bytes()).hexdigest()
        if (
            not isinstance(expected, str)
            or re.fullmatch(r"[0-9a-f]{64}", expected) is None
            or expected != actual
        ):
            raise _invalid_map(f"The native rollback {sha_field} is invalid or stale.")
    if value.get("host") not in {"127.0.0.1", "::1"}:
        raise _invalid_map("The native frozen Node host must be loopback.")
    ports = value.get("ports")
    if (
        not isinstance(ports, dict)
        or not ports
        or any(
            not isinstance(name, str)
            or not name
            or not isinstance(port, int)
            or isinstance(port, bool)
            or not 1 <= port <= 65535
            for name, port in ports.items()
        )
    ):
        raise _invalid_map("The native frozen Node port map is invalid.")
    environment = value.get("environment")
    if not isinstance(environment, Mapping) or dict(environment) != _ROLLBACK_ENVIRONMENT:
        raise _invalid_map("The native frozen Node environment is not the rollback map.")
    return {
        "deploymentKind": "native-windows",
        "executablePath": str(paths["executablePath"]),
        "executableSha256": value["executableSha256"],
        "entrypointPath": str(paths["entrypointPath"]),
        "entrypointSha256": value["entrypointSha256"],
        "cwd": str(paths["cwd"]),
        "host": value["host"],
        "ports": dict(ports),
        "databasePath": str(paths["databasePath"]),
        "environment": dict(environment),
    }


def execute_rollback_tail(
    *,
    operations: object,
    rollback_map: Mapping[str, object],
    initial_owner_state: str,
    commit_nonactive_owner: Callable[[], None] | None = None,
    commit_node_active: Callable[[], None],
    on_event: Callable[[str], None] | None = None,
) -> tuple[tuple[str, ...], Mapping[str, object]]:
    frozen_map = validate_frozen_node_rollback_map(rollback_map)
    events: list[str] = []

    def record(event: str) -> None:
        events.append(event)
        if on_event is not None:
            on_event(event)

    if initial_owner_state == "python_active":
        if commit_nonactive_owner is None:
            raise ProductionRollbackError(
                "ROLLBACK_ORDER_INVALID",
                "Python-active rollback requires a durable pending-owner CAS.",
            )
        commit_nonactive_owner()
        record("owner_handoff_pending")
    elif initial_owner_state not in {"node_quiesced", "handoff_pending"}:
        raise ProductionRollbackError(
            "ROLLBACK_ORDER_INVALID",
            "Rollback did not start from an accepted owner state.",
        )

    operation_steps = (
        ("clear_authorization", "authorization_cleared"),
        ("drain_python_ingress", "python_ingress_drained"),
        ("drain_worker_claims", "worker_claims_drained"),
        ("stop_scheduler_obsidian_mcp", "scheduler_obsidian_mcp_stopped"),
        ("stop_fastapi", "fastapi_stopped"),
        ("release_locks_connections", "role_locks_connections_released"),
    )
    for method_name, event in operation_steps:
        method = getattr(operations, method_name, None)
        if not callable(method):
            raise ProductionRollbackError(
                "ROLLBACK_OPERATIONS_INVALID",
                f"The rollback operations object omitted {method_name}.",
            )
        method()
        record(event)
    start = getattr(operations, "start_frozen_node", None)
    smoke_method = getattr(operations, "smoke_legacy", None)
    if not callable(start) or not callable(smoke_method):
        raise ProductionRollbackError(
            "ROLLBACK_OPERATIONS_INVALID",
            "The rollback operations object omitted frozen Node start or smoke.",
        )
    handle = start(frozen_map)
    record("frozen_node_started")
    smoke = smoke_method(handle)
    if not isinstance(smoke, Mapping) or smoke.get("ok") is not True:
        raise ProductionRollbackError(
            "ROLLBACK_LEGACY_SMOKE_FAILED",
            "The frozen Node legacy smoke did not pass.",
        )
    record("legacy_smoked")
    commit_node_active()
    record("owner_node_active")
    validate_rollback_tail(
        initial_owner_state=initial_owner_state,
        events=events,
    )
    return tuple(events), smoke


def _verify_rollback_identities(
    *,
    handoff_receipt: str | os.PathLike[str],
    expected_handoff_receipt_sha256: str,
    startup_snapshot: str | os.PathLike[str],
    expected_startup_snapshot_sha256: str,
    build_identity_manifest: str | os.PathLike[str],
    database_identity_manifest: str | os.PathLike[str],
    p0_origin_receipt: str | os.PathLike[str],
    expected_p0_origin_receipt_sha256: str,
    owner_marker: str | os.PathLike[str],
) -> dict[str, object]:
    try:
        from backend.app.api.compat.build_identity import load_build_identity_manifest
        from backend.app.api.compat.database_identity import (
            load_database_evidence_identity_manifest,
            verify_database_evidence_identity_subject,
        )
        from backend.app.application.final_window import (
            _load_lease,
            load_production_startup_snapshot,
        )
        from backend.app.application.runtime_handoff import load_handoff_receipt
        from backend.app.infrastructure.database_backup import verify_origin_receipt

        receipt = load_handoff_receipt(
            handoff_receipt,
            expected_file_sha256=expected_handoff_receipt_sha256,
        )
        receipt_document = json.loads(receipt.canonical_bytes.decode("utf-8"))
        snapshot = load_production_startup_snapshot(
            startup_snapshot,
            expected_file_sha256=expected_startup_snapshot_sha256,
        )
        build = load_build_identity_manifest(build_identity_manifest)
        database = load_database_evidence_identity_manifest(
            database_identity_manifest
        )
        verify_database_evidence_identity_subject(
            database=database.database_path,
            identity=database,
        )
        origin = verify_origin_receipt(
            p0_origin_receipt,
            expected_p0_origin_receipt_sha256,
        )
        cutover_lease_path, cutover_payload, cutover_lease = _load_lease(
            receipt_document["cutoverLeasePath"]
        )
        owner_path = Path(owner_marker).resolve(strict=True)
        original_owner = base64.b64decode(
            str(cutover_lease["nodeActiveOwnerPayloadBase64"]), validate=True
        )
    except Exception as error:
        raise ProductionRollbackError(
            "PRODUCTION_ROLLBACK_IDENTITY_MISMATCH",
            "A rollback receipt, startup, build, database, origin, lease, or owner identity is invalid.",
        ) from error
    receipt_cutover_sha = receipt_document["cutoverLeaseFileSha256"]
    cutover_phase = cutover_lease["phase"]
    cutover_chain_matches = (
        cutover_phase == "completed"
        and cutover_lease["previousLeaseFileSha256"] == receipt_cutover_sha
    ) or cutover_phase == "recovered"
    if (
        Path(handoff_receipt).resolve(strict=True) != receipt.path
        or Path(startup_snapshot).resolve(strict=True) != snapshot.path
        or Path(build_identity_manifest).resolve(strict=True)
        != snapshot.build_identity_manifest_path
        or build.manifest_file_sha256 != snapshot.build_identity_manifest_sha256
        or build.build_id != snapshot.build_id
        or Path(database_identity_manifest).resolve(strict=True)
        != snapshot.database_identity_manifest_path
        or database.identity_manifest_file_sha256
        != snapshot.database_identity_manifest_sha256
        or database.subject_kind != "live"
        or database.database_lineage_id != snapshot.database_lineage_id
        or database.subject_database_id != snapshot.live_subject_database_id
        or Path(p0_origin_receipt).resolve(strict=True)
        != snapshot.origin_receipt_path
        or origin.origin_receipt_file_sha256
        != snapshot.origin_receipt_file_sha256
        or receipt_document["startupSnapshotPath"] != str(snapshot.path)
        or receipt_document["startupSnapshotFileSha256"] != snapshot.file_sha256
        or receipt_document["buildIdentityManifestPath"]
        != str(build.manifest_path)
        or receipt_document["buildIdentityManifestSha256"]
        != build.manifest_file_sha256
        or receipt_document["databaseIdentityManifestPath"]
        != str(database.manifest_path)
        or receipt_document["databaseIdentityManifestSha256"]
        != database.identity_manifest_file_sha256
        or receipt_document["originReceiptPath"] != str(origin.receipt_path)
        or receipt_document["originReceiptFileSha256"]
        != origin.origin_receipt_file_sha256
        or receipt_document["ownerMarkerPath"] != str(owner_path)
        or cutover_lease_path != Path(receipt_document["cutoverLeasePath"])
        or cutover_lease["runId"] != receipt.run_id
        or cutover_lease["startupSnapshotPath"] != str(snapshot.path)
        or cutover_lease["startupSnapshotFileSha256"] != snapshot.file_sha256
        or not cutover_chain_matches
        or hashlib.sha256(original_owner).hexdigest()
        != cutover_lease["nodeActiveOwnerFileSha256"]
    ):
        raise ProductionRollbackError(
            "PRODUCTION_ROLLBACK_IDENTITY_MISMATCH",
            "The rollback identities do not form one exact receipt-bound chain.",
        )
    return {
        "receipt": receipt,
        "receipt_document": receipt_document,
        "snapshot": snapshot,
        "build": build,
        "database": database,
        "origin": origin,
        "cutover_lease_path": cutover_lease_path,
        "cutover_lease_payload": cutover_payload,
        "cutover_lease": cutover_lease,
        "owner_path": owner_path,
        "original_owner": original_owner,
    }


def _create_recovery_lease(
    *,
    path: Path,
    receipt_document: Mapping[str, object],
    receipt_path: Path,
    receipt_file_sha256: str,
    snapshot_path: Path,
    snapshot_file_sha256: str,
    build_identity_manifest_path: Path,
    build_identity_manifest_sha256: str,
    database_identity_manifest_path: Path,
    database_identity_manifest_sha256: str,
    origin_receipt_path: Path,
    origin_receipt_file_sha256: str,
    owner_marker_path: Path,
    cutover_lease_path: Path,
    rollback_map_sha256: str,
    clock: Callable[[], datetime],
) -> tuple[bytes, dict[str, object]]:
    if not path.parent.is_dir() or path.exists():
        raise ProductionRollbackError(
            "PRODUCTION_ROLLBACK_OUTPUT_INVALID",
            "The recovery lease output must be a new file in an existing directory.",
        )
    unsigned = {
        "schemaVersion": 1,
        "leaseKind": "production-rollback",
        "receiptId": receipt_document["receiptId"],
        "handoffReceiptPath": str(receipt_path),
        "handoffReceiptFileSha256": receipt_file_sha256,
        "startupSnapshotPath": str(snapshot_path),
        "startupSnapshotFileSha256": snapshot_file_sha256,
        "buildIdentityManifestPath": str(build_identity_manifest_path),
        "buildIdentityManifestSha256": build_identity_manifest_sha256,
        "databaseIdentityManifestPath": str(database_identity_manifest_path),
        "databaseIdentityManifestSha256": database_identity_manifest_sha256,
        "originReceiptPath": str(origin_receipt_path),
        "originReceiptFileSha256": origin_receipt_file_sha256,
        "ownerMarkerPath": str(owner_marker_path),
        "cutoverLeasePath": str(cutover_lease_path),
        "rollbackMapSha256": rollback_map_sha256,
        "events": [],
        "phase": "running",
        "version": 1,
        "previousLeaseFileSha256": "0" * 64,
        "updatedAt": _aware_timestamp(clock()),
    }
    payload = _hashed_document(unsigned, "recoveryLeaseSha256")
    try:
        exclusive_write_bytes(path, payload)
    except DatabaseIdentityError as error:
        raise ProductionRollbackError(error.code, str(error)) from error
    return payload, _strict_document(payload, _RECOVERY_LEASE_FIELDS)


def _load_recovery_lease(
    path: Path,
    *,
    expected_receipt_path: Path,
    expected_receipt_sha256: str,
) -> tuple[bytes, dict[str, object]]:
    try:
        payload = path.read_bytes()
        document = _strict_document(payload, _RECOVERY_LEASE_FIELDS)
    except (OSError, ValueError) as error:
        raise ProductionRollbackError(
            "PRODUCTION_RECOVERY_LEASE_INVALID",
            "The durable production recovery lease is invalid.",
        ) from error
    unsigned = {
        key: value for key, value in document.items() if key != "recoveryLeaseSha256"
    }
    if (
        document.get("schemaVersion") != 1
        or document.get("leaseKind") != "production-rollback"
        or document.get("handoffReceiptPath") != str(expected_receipt_path)
        or document.get("handoffReceiptFileSha256")
        != expected_receipt_sha256
        or hashlib.sha256(canonical_json_bytes(unsigned)).hexdigest()
        != document.get("recoveryLeaseSha256")
    ):
        raise ProductionRollbackError(
            "PRODUCTION_RECOVERY_LEASE_INVALID",
            "The durable recovery lease binding or self hash is invalid.",
        )
    return payload, document


def _update_recovery_lease(
    path: Path,
    *,
    expected_payload: bytes,
    document: Mapping[str, object],
    changes: Mapping[str, object],
    clock: Callable[[], datetime],
) -> tuple[bytes, dict[str, object]]:
    from backend.app.application.final_window import _atomic_replace

    current = path.read_bytes()
    if current != expected_payload:
        raise ProductionRollbackError(
            "PRODUCTION_RECOVERY_LEASE_CAS_FAILED",
            "The production recovery lease changed concurrently.",
        )
    unsigned = {
        key: value for key, value in document.items() if key != "recoveryLeaseSha256"
    }
    unsigned.update(changes)
    unsigned["version"] = int(document["version"]) + 1
    unsigned["previousLeaseFileSha256"] = hashlib.sha256(current).hexdigest()
    unsigned["updatedAt"] = _aware_timestamp(clock())
    payload = _hashed_document(unsigned, "recoveryLeaseSha256")
    _atomic_replace(path, payload)
    return payload, _strict_document(payload, _RECOVERY_LEASE_FIELDS)


def _set_recovery_phase(
    path: Path,
    *,
    expected_payload: bytes,
    document: Mapping[str, object],
    phase: str,
    clock: Callable[[], datetime],
) -> tuple[bytes, dict[str, object]]:
    return _update_recovery_lease(
        path,
        expected_payload=expected_payload,
        document=document,
        changes={"phase": phase},
        clock=clock,
    )


def _load_p6_owner(payload: bytes) -> dict[str, object]:
    try:
        document = _decode_document(payload)
    except ValueError as error:
        raise ProductionRollbackError(
            "PRODUCTION_ROLLBACK_OWNER_MISMATCH",
            "The P6 runtime owner marker is invalid.",
        ) from error
    unsigned = {key: value for key, value in document.items() if key != "ownerMarkerSha256"}
    if (
        document.get("schemaVersion") != 2
        or document.get("markerKind") != "runtime-owner"
        or canonical_json_bytes(document) != payload
        or hashlib.sha256(canonical_json_bytes(unsigned)).hexdigest()
        != document.get("ownerMarkerSha256")
    ):
        raise ProductionRollbackError(
            "PRODUCTION_ROLLBACK_OWNER_MISMATCH",
            "The P6 runtime owner marker self hash is invalid.",
        )
    return document


def _pending_owner_payload(document: Mapping[str, object]) -> bytes:
    unsigned = {
        key: value for key, value in document.items() if key != "ownerMarkerSha256"
    }
    unsigned["ownerState"] = "handoff_pending"
    unsigned["updatedAt"] = _aware_timestamp(datetime.now(timezone.utc))
    return _hashed_document(unsigned, "ownerMarkerSha256")


def _load_production_recovery(
    path: Path,
    *,
    expected_receipt_id: str,
) -> ProductionRecovery:
    try:
        payload = path.read_bytes()
        document = _strict_document(payload, _RECOVERY_FIELDS)
    except (OSError, ValueError) as error:
        raise ProductionRollbackError(
            "PRODUCTION_RECOVERY_INVALID",
            "The production recovery record is invalid.",
        ) from error
    unsigned = {key: value for key, value in document.items() if key != "recoverySha256"}
    if (
        document.get("schemaVersion") != 1
        or document.get("recoveryKind") != "production-rollback"
        or document.get("receiptId") != expected_receipt_id
        or document.get("ownerState") != "node_active"
        or hashlib.sha256(canonical_json_bytes(unsigned)).hexdigest()
        != document.get("recoverySha256")
    ):
        raise ProductionRollbackError(
            "PRODUCTION_RECOVERY_INVALID",
            "The production recovery record binding or self hash is invalid.",
        )
    return _recovery_from_payload(path, payload)


def _recovery_from_payload(path: Path, payload: bytes) -> ProductionRecovery:
    document = _strict_document(payload, _RECOVERY_FIELDS)
    return ProductionRecovery(
        path=path,
        file_sha256=hashlib.sha256(payload).hexdigest(),
        receipt_id=str(document["receiptId"]),
        owner_state=str(document["ownerState"]),
        events=tuple(document["events"]),
        canonical_bytes=payload,
    )


def _strict_document(
    payload: bytes,
    fields: frozenset[str],
) -> dict[str, object]:
    document = _decode_document(payload)
    if set(document) != fields or canonical_json_bytes(document) != payload:
        raise ValueError("invalid canonical document")
    return document


def _decode_document(payload: bytes) -> dict[str, object]:
    duplicates: list[str] = []

    def pairs(values: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in values:
            if key in result:
                duplicates.append(key)
            result[key] = value
        return result

    try:
        document = json.loads(payload.decode("utf-8"), object_pairs_hook=pairs)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("invalid JSON") from error
    if duplicates or not isinstance(document, dict):
        raise ValueError("invalid JSON object")
    return document


def _hashed_document(unsigned: Mapping[str, object], field: str) -> bytes:
    digest = hashlib.sha256(canonical_json_bytes(unsigned)).hexdigest()
    return canonical_json_bytes({**unsigned, field: digest})


def _aware_timestamp(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ProductionRollbackError(
            "PRODUCTION_ROLLBACK_CLOCK_INVALID",
            "Production rollback timestamps must be timezone-aware.",
        )
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _invalid_map(message: str) -> ProductionRollbackError:
    return ProductionRollbackError("ROLLBACK_MAP_INVALID", message)


__all__ = [
    "ProductionRecovery",
    "ProductionRollbackCoordinator",
    "ProductionRollbackError",
    "ROLLBACK_TAIL_EVENTS",
    "execute_rollback_tail",
    "validate_frozen_node_rollback_map",
    "validate_rollback_tail",
]
