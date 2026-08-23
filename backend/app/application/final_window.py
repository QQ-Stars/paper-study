from __future__ import annotations

from collections.abc import Callable, Mapping
from contextlib import contextmanager, nullcontext
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
import base64
import hashlib
import json
import os
import secrets
import stat
import tempfile
import threading
import time
from functools import wraps

from backend.app.api.compat.build_identity import (
    BuildIdentityError,
    load_build_identity_manifest,
)
from backend.app.api.compat.database_identity import (
    DatabaseIdentityError,
    canonical_json_bytes,
    exclusive_write_bytes,
    load_database_evidence_identity_manifest,
)
from backend.app.api.compat.evidence_capture import (
    EvidenceCaptureError,
    load_evidence_run_manifest,
)
from backend.app.application.production_rollback import (
    ProductionRollbackError,
    execute_rollback_tail,
    validate_frozen_node_rollback_map,
)
from backend.app.infrastructure.database_backup import verify_origin_receipt


_SNAPSHOT_FIELDS = frozenset(
    {
        "schemaVersion",
        "snapshotKind",
        "runId",
        "runManifestPath",
        "runManifestFileSha256",
        "buildIdentityManifestPath",
        "buildIdentityManifestSha256",
        "buildId",
        "databaseIdentityManifestPath",
        "databaseIdentityManifestSha256",
        "databaseLineageId",
        "liveSubjectDatabaseId",
        "originReceiptPath",
        "originReceiptFileSha256",
        "runtimeNamespace",
        "productionProfile",
        "rollbackProfile",
        "frozenNodeRollbackMap",
        "frozenNodeRollbackMapSha256",
        "createdAt",
        "startupSnapshotSha256",
    }
)


class FinalWindowError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(f"{code}: {message}")


@dataclass(frozen=True, slots=True)
class ProductionStartupSnapshot:
    path: Path
    file_sha256: str
    run_id: str
    run_manifest_path: Path
    run_manifest_file_sha256: str
    build_identity_manifest_path: Path
    build_identity_manifest_sha256: str
    build_id: str
    database_identity_manifest_path: Path
    database_identity_manifest_sha256: str
    database_lineage_id: str
    live_subject_database_id: str
    origin_receipt_path: Path
    origin_receipt_file_sha256: str
    rollback_map: Mapping[str, object]
    rollback_map_sha256: str
    canonical_bytes: bytes


@dataclass(frozen=True, slots=True)
class CutoverLease:
    path: Path
    token_file_path: Path
    run_id: str
    phase: str
    version: int
    file_sha256: str
    canonical_bytes: bytes


@dataclass(frozen=True, slots=True)
class CutoverLeaseBinding:
    path: Path
    file_sha256: str
    run_id: str
    startup_snapshot_path: Path
    startup_snapshot_sha256: str
    token_file_path: Path
    token_sha256: str
    phase: str


@dataclass(frozen=True, slots=True)
class FrozenNodeRecovery:
    path: Path
    file_sha256: str
    owner_state: str
    run_id: str
    canonical_bytes: bytes


@dataclass(frozen=True, slots=True)
class WatchdogResult:
    action: str
    reason_code: str | None
    recovery: FrozenNodeRecovery | None = None


_LEASE_FIELDS = frozenset(
    {
        "schemaVersion",
        "leaseKind",
        "runId",
        "runManifestPath",
        "runManifestFileSha256",
        "startupSnapshotPath",
        "startupSnapshotFileSha256",
        "buildIdentityManifestPath",
        "buildIdentityManifestSha256",
        "buildId",
        "databaseIdentityManifestPath",
        "databaseIdentityManifestSha256",
        "databaseLineageId",
        "liveSubjectDatabaseId",
        "originReceiptPath",
        "originReceiptFileSha256",
        "ownerMarkerPath",
        "cutoverLeasePath",
        "nodeActiveOwnerFileSha256",
        "nodeActiveOwnerPayloadBase64",
        "cutoverTokenFilePath",
        "cutoverTokenSha256",
        "runtimeNamespace",
        "frozenNodeRollbackMap",
        "frozenNodeRollbackMapSha256",
        "coordinatorPid",
        "operatorPid",
        "watchdogPid",
        "heartbeatDeadline",
        "phase",
        "version",
        "previousLeaseFileSha256",
        "updatedAt",
        "leaseSha256",
    }
)
_RECOVERY_FIELDS = frozenset(
    {
        "schemaVersion",
        "recoveryKind",
        "runId",
        "cutoverLeasePath",
        "cutoverLeaseFileSha256",
        "startupSnapshotPath",
        "startupSnapshotFileSha256",
        "ownerMarkerPath",
        "ownerState",
        "reasonCode",
        "events",
        "legacySmoke",
        "completedAt",
        "recoverySha256",
    }
)
_ABORT_REASONS = frozenset(
    {
        "step11_failure",
        "step12_failure",
        "step13_failure",
        "step14_failure",
        "operator_exit",
        "coordinator_exit",
        "heartbeat_timeout",
        "authorization_expired",
        "authorization_unused",
    }
)
_FILE_CAS_LOCK = threading.RLock()
_LEASE_LOCK_STATE = threading.local()
_WINDOWS_REPLACE_RETRY_ATTEMPTS = 20
_WINDOWS_REPLACE_RETRY_SECONDS = 0.025


def _with_cutover_lease_lock(method: Callable[..., object]) -> Callable[..., object]:
    @wraps(method)
    def locked(self: object, *args: object, **kwargs: object) -> object:
        raw_path = kwargs.get("cutover_lease")
        if raw_path is None:
            raise FinalWindowError(
                "CUTOVER_ARGUMENT_INVALID",
                "The cutover lease path is required.",
            )
        try:
            lease_path = Path(os.fspath(raw_path)).resolve(strict=True)
        except (OSError, TypeError, ValueError) as error:
            raise FinalWindowError(
                "CUTOVER_LEASE_INVALID",
                "The final-window lease cannot be locked.",
            ) from error
        with _FILE_CAS_LOCK, _lease_file_lock(lease_path):
            return method(self, *args, **kwargs)

    return locked


class FinalWindowCoordinator:
    def __init__(
        self,
        *,
        operations: object,
        watchdog: object,
        token_factory: Callable[[], bytes] | None = None,
        clock: Callable[[], datetime] | None = None,
        coordinator_pid: int | None = None,
    ) -> None:
        self._operations = operations
        self._watchdog = watchdog
        self._token_factory = token_factory or (lambda: secrets.token_bytes(32))
        self._clock = clock or _utc_now
        if coordinator_pid is not None and (
            not isinstance(coordinator_pid, int)
            or isinstance(coordinator_pid, bool)
            or coordinator_pid <= 0
        ):
            raise FinalWindowError(
                "CUTOVER_ARGUMENT_INVALID",
                "The final-window coordinator process is invalid.",
            )
        self._coordinator_pid = (
            coordinator_pid if coordinator_pid is not None else os.getpid()
        )

    def begin_final_window(
        self,
        *,
        final_evidence_run_manifest: str | os.PathLike[str],
        expected_final_evidence_run_manifest_sha256: str,
        startup_snapshot: str | os.PathLike[str],
        expected_startup_snapshot_sha256: str,
        owner_marker: str | os.PathLike[str],
        runtime_namespace: str,
        operator_pid: int,
        heartbeat_timeout_seconds: int,
        lease_output: str | os.PathLike[str],
        token_file_output: str | os.PathLike[str],
    ) -> CutoverLease:
        if (
            runtime_namespace != "production"
            or not isinstance(operator_pid, int)
            or isinstance(operator_pid, bool)
            or operator_pid <= 0
            or not isinstance(heartbeat_timeout_seconds, int)
            or isinstance(heartbeat_timeout_seconds, bool)
            or not 1 <= heartbeat_timeout_seconds <= 300
        ):
            raise FinalWindowError(
                "CUTOVER_ARGUMENT_INVALID",
                "The final window namespace, process, or heartbeat is invalid.",
            )
        snapshot = load_production_startup_snapshot(
            startup_snapshot,
            expected_file_sha256=expected_startup_snapshot_sha256,
        )
        run, database = _verify_snapshot_identity(
            snapshot,
            final_evidence_run_manifest=final_evidence_run_manifest,
            expected_final_evidence_run_manifest_sha256=(
                expected_final_evidence_run_manifest_sha256
            ),
        )
        from backend.app.cli.runtime_owner import read_node_active_owner_marker

        owner_path = Path(owner_marker).resolve(strict=True)
        try:
            owner = read_node_active_owner_marker(owner_path)
            owner_payload = owner_path.read_bytes()
        except Exception as error:
            raise FinalWindowError(
                "CUTOVER_OWNER_INVALID",
                "The final window requires the exact verified node_active owner.",
            ) from error
        if (
            owner.owner_state != "node_active"
            or owner.database_lineage_id != snapshot.database_lineage_id
            or owner.subject_database_id != snapshot.live_subject_database_id
            or owner.origin_receipt_file_sha256 != snapshot.origin_receipt_file_sha256
        ):
            raise FinalWindowError(
                "CUTOVER_OWNER_INVALID",
                "The owner marker does not match the startup database identity.",
            )
        lease_path = Path(lease_output).resolve(strict=False)
        token_path = Path(token_file_output).resolve(strict=False)
        if (
            lease_path.parent != token_path.parent
            or lease_path.exists()
            or token_path.exists()
            or lease_path.name != f"final-window-{run.run_id}.json"
            or token_path.name != f"final-window-{run.run_id}.token"
        ):
            raise FinalWindowError(
                "CUTOVER_OUTPUT_INVALID",
                "The final window requires matching new run-addressed lease and token files.",
            )
        token = self._token_factory()
        if not isinstance(token, bytes) or len(token) != 32:
            raise FinalWindowError(
                "CUTOVER_TOKEN_INVALID",
                "The cutover token source must return exactly 256 bits.",
            )
        create_owner_only_token_file(token_path, token)
        now = self._clock()
        try:
            unsigned = {
                "schemaVersion": 1,
                "leaseKind": "final-window",
                "runId": run.run_id,
                "runManifestPath": str(run.manifest_path),
                "runManifestFileSha256": run.manifest_file_sha256,
                "startupSnapshotPath": str(snapshot.path),
                "startupSnapshotFileSha256": snapshot.file_sha256,
                "buildIdentityManifestPath": str(snapshot.build_identity_manifest_path),
                "buildIdentityManifestSha256": snapshot.build_identity_manifest_sha256,
                "buildId": snapshot.build_id,
                "databaseIdentityManifestPath": str(snapshot.database_identity_manifest_path),
                "databaseIdentityManifestSha256": snapshot.database_identity_manifest_sha256,
                "databaseLineageId": snapshot.database_lineage_id,
                "liveSubjectDatabaseId": snapshot.live_subject_database_id,
                "originReceiptPath": str(snapshot.origin_receipt_path),
                "originReceiptFileSha256": snapshot.origin_receipt_file_sha256,
                "ownerMarkerPath": str(owner_path),
                "cutoverLeasePath": str(lease_path),
                "nodeActiveOwnerFileSha256": hashlib.sha256(owner_payload).hexdigest(),
                "nodeActiveOwnerPayloadBase64": base64.b64encode(owner_payload).decode("ascii"),
                "cutoverTokenFilePath": str(token_path),
                "cutoverTokenSha256": hashlib.sha256(token).hexdigest(),
                "runtimeNamespace": runtime_namespace,
                "frozenNodeRollbackMap": dict(snapshot.rollback_map),
                "frozenNodeRollbackMapSha256": snapshot.rollback_map_sha256,
                "coordinatorPid": self._coordinator_pid,
                "operatorPid": operator_pid,
                "watchdogPid": 0,
                "heartbeatDeadline": _timestamp(
                    now + timedelta(seconds=heartbeat_timeout_seconds)
                ),
                "phase": "arming",
                "version": 1,
                "previousLeaseFileSha256": "0" * 64,
                "updatedAt": _timestamp(now),
            }
            payload = _self_hashed(unsigned, "leaseSha256")
            exclusive_write_bytes(lease_path, payload)
            watchdog_pid = self._watchdog.start(lease_path, token_path)
            if (
                not isinstance(watchdog_pid, int)
                or isinstance(watchdog_pid, bool)
                or watchdog_pid <= 0
            ):
                raise FinalWindowError(
                    "CUTOVER_WATCHDOG_FAILED",
                    "The final window watchdog did not report a valid process.",
                )
            lease = _update_lease(
                lease_path,
                expected_payload=payload,
                changes={"phase": "armed", "watchdogPid": watchdog_pid},
                clock=self._clock,
            )
        except Exception:
            _unlink_new_file(lease_path)
            _unlink_new_file(token_path)
            raise
        return CutoverLease(
            path=lease_path,
            token_file_path=token_path,
            run_id=run.run_id,
            phase=lease["phase"],
            version=lease["version"],
            file_sha256=hashlib.sha256(lease_path.read_bytes()).hexdigest(),
            canonical_bytes=lease_path.read_bytes(),
        )

    @_with_cutover_lease_lock
    def quiesce_live(
        self,
        *,
        cutover_lease: str | os.PathLike[str],
        cutover_token_file: str | os.PathLike[str],
    ) -> CutoverLease:
        lease_path, payload, lease = _load_lease(cutover_lease)
        _verify_token(lease, cutover_token_file)
        if lease["phase"] != "armed":
            raise FinalWindowError(
                "CUTOVER_PHASE_INVALID",
                "Only an armed final window can quiesce production.",
            )
        owner_path = Path(lease["ownerMarkerPath"])
        expected_owner = base64.b64decode(lease["nodeActiveOwnerPayloadBase64"], validate=True)
        if owner_path.read_bytes() != expected_owner:
            raise FinalWindowError(
                "CUTOVER_OWNER_CAS_FAILED",
                "The production owner changed before quiesce.",
            )
        evidence = self._operations.quiesce_node()
        if (
            not isinstance(evidence, Mapping)
            or evidence.get("zeroPidPortDatabaseHandles") is not True
        ):
            raise FinalWindowError(
                "CUTOVER_QUIESCE_INCOMPLETE",
                "Production did not prove zero process, port, transaction, and handle state.",
            )
        state_payload = _runtime_owner_state_payload(
            lease,
            owner_state="node_quiesced",
            clock=self._clock,
        )
        _cas_replace(owner_path, expected_owner, state_payload)
        try:
            updated = _update_lease(
                lease_path,
                expected_payload=payload,
                changes={"phase": "node_quiesced"},
                clock=self._clock,
            )
        except Exception:
            _cas_replace(owner_path, state_payload, expected_owner)
            raise
        canonical = lease_path.read_bytes()
        return CutoverLease(
            path=lease_path,
            token_file_path=Path(lease["cutoverTokenFilePath"]),
            run_id=lease["runId"],
            phase=updated["phase"],
            version=updated["version"],
            file_sha256=hashlib.sha256(canonical).hexdigest(),
            canonical_bytes=canonical,
        )

    @_with_cutover_lease_lock
    def abort_cutover(
        self,
        *,
        cutover_lease: str | os.PathLike[str],
        cutover_token_file: str | os.PathLike[str],
        reason: str,
        recovery_output: str | os.PathLike[str],
    ) -> FrozenNodeRecovery:
        lease_path, lease_payload, lease = _load_lease(cutover_lease)
        _verify_token(lease, cutover_token_file)
        if reason not in _ABORT_REASONS:
            raise FinalWindowError("CUTOVER_REASON_INVALID", "The abort reason is not allowlisted.")
        recovery_path = Path(recovery_output).resolve(strict=False)
        run_root = Path(lease["runManifestPath"]).parent
        if recovery_path.parent != run_root or recovery_path.name != "abort-recovery.json":
            raise FinalWindowError(
                "CUTOVER_RECOVERY_OUTPUT_INVALID",
                "The abort recovery must use the exact final run output path.",
            )
        if lease["phase"] == "recovered":
            return _load_recovery(recovery_path, expected_run_id=lease["runId"])
        if lease["phase"] not in {"armed", "node_quiesced", "authorization_issued"}:
            raise FinalWindowError(
                "CUTOVER_PHASE_INVALID",
                "This final-window phase cannot be recovered by abort-cutover.",
            )
        owner_path = Path(lease["ownerMarkerPath"])
        original_owner = base64.b64decode(
            lease["nodeActiveOwnerPayloadBase64"], validate=True
        )
        events: list[str] = []
        smoke: Mapping[str, object] = {"ok": True, "alreadyActive": True}
        if lease["phase"] == "armed":
            if owner_path.read_bytes() != original_owner:
                raise FinalWindowError(
                    "CUTOVER_OWNER_CAS_FAILED",
                    "The armed window owner no longer matches node_active.",
                )
        else:
            quiesced_owner = owner_path.read_bytes()
            try:
                ordered_events, smoke = execute_rollback_tail(
                    operations=self._operations,
                    rollback_map=dict(lease["frozenNodeRollbackMap"]),
                    initial_owner_state="node_quiesced",
                    commit_node_active=lambda handle: _commit_recovered_node_owner(
                        operations=self._operations,
                        handle=handle,
                        rollback_map=dict(lease["frozenNodeRollbackMap"]),
                        owner_path=owner_path,
                        expected_owner_payload=quiesced_owner,
                        previous_node_owner_payload=original_owner,
                    ),
                )
                events = list(ordered_events)
            except Exception as error:
                try:
                    _update_lease(
                        lease_path,
                        expected_payload=lease_payload,
                        changes={"phase": "recovery_failed"},
                        clock=self._clock,
                    )
                except Exception:
                    pass
                if isinstance(error, FinalWindowError):
                    raise
                raise FinalWindowError(
                    "CUTOVER_RECOVERY_FAILED",
                    "The frozen Node recovery tail failed.",
                ) from error
        updated = _update_lease(
            lease_path,
            expected_payload=lease_payload,
            changes={"phase": "recovered"},
            clock=self._clock,
        )
        self._watchdog.stop(lease_path)
        completed_at = _timestamp(self._clock())
        unsigned = {
            "schemaVersion": 1,
            "recoveryKind": "frozen-node-abort",
            "runId": lease["runId"],
            "cutoverLeasePath": str(lease_path),
            "cutoverLeaseFileSha256": hashlib.sha256(
                lease_path.read_bytes()
            ).hexdigest(),
            "startupSnapshotPath": lease["startupSnapshotPath"],
            "startupSnapshotFileSha256": lease["startupSnapshotFileSha256"],
            "ownerMarkerPath": str(owner_path),
            "ownerState": "node_active",
            "reasonCode": reason,
            "events": events,
            "legacySmoke": dict(smoke),
            "completedAt": completed_at,
        }
        recovery_payload = _self_hashed(unsigned, "recoverySha256")
        try:
            exclusive_write_bytes(recovery_path, recovery_payload)
        except DatabaseIdentityError:
            existing = _load_recovery(recovery_path, expected_run_id=lease["runId"])
            return existing
        return _recovery_from_payload(recovery_path, recovery_payload)


class FinalWindowWatchdog:
    """Process-independent monitor for one exact final-window capability."""

    def __init__(
        self,
        *,
        operations: object,
        clock: Callable[[], datetime] | None = None,
        process_probe: Callable[[int], bool] | None = None,
    ) -> None:
        self._operations = operations
        self._clock = clock or _utc_now
        self._process_probe = process_probe or _pid_is_alive

    def run_once(
        self,
        *,
        cutover_lease: str | os.PathLike[str],
        cutover_token_file: str | os.PathLike[str],
        recovery_output: str | os.PathLike[str],
    ) -> WatchdogResult:
        lease_path, _payload, lease = _load_lease(cutover_lease)
        _verify_token(lease, cutover_token_file)
        phase = str(lease["phase"])
        if phase in {
            "handoff_pending",
            "python_active",
            "completed",
            "recovered",
            "recovery_failed",
        }:
            return WatchdogResult(action="released", reason_code=None)
        reason = _watchdog_abort_reason(
            lease_path,
            lease,
            lease_file_sha256=hashlib.sha256(_payload).hexdigest(),
            now=self._clock(),
            process_probe=self._process_probe,
        )
        if reason is None:
            return WatchdogResult(action="monitoring", reason_code=None)
        coordinator = FinalWindowCoordinator(
            operations=self._operations,
            watchdog=_PassiveWatchdog(),
            clock=self._clock,
        )
        recovery = coordinator.abort_cutover(
            cutover_lease=lease_path,
            cutover_token_file=cutover_token_file,
            reason=reason,
            recovery_output=recovery_output,
        )
        return WatchdogResult(
            action="aborted",
            reason_code=reason,
            recovery=recovery,
        )


class _PassiveWatchdog:
    def stop(self, _lease_path: Path) -> None:
        return None


def _watchdog_abort_reason(
    lease_path: Path,
    lease: Mapping[str, object],
    *,
    lease_file_sha256: str,
    now: datetime,
    process_probe: Callable[[int], bool],
) -> str | None:
    instant = _aware_utc(now, code="CUTOVER_WATCHDOG_CLOCK_INVALID")
    coordinator_alive = process_probe(_required_positive_int(lease["coordinatorPid"]))
    operator_alive = process_probe(_required_positive_int(lease["operatorPid"]))
    authorization = _load_watchdog_authorization(
        lease_path,
        lease,
        lease_file_sha256=lease_file_sha256,
    )
    if authorization is None and lease.get("phase") == "authorization_issued":
        return "authorization_unused"
    if authorization is not None:
        expires = _parse_utc_timestamp(authorization["expiresAt"])
        if instant >= expires:
            return "authorization_expired"
        if not coordinator_alive or not operator_alive:
            return "authorization_unused"
    if not coordinator_alive:
        return "coordinator_exit"
    if not operator_alive:
        return "operator_exit"
    if instant >= _parse_utc_timestamp(lease["heartbeatDeadline"]):
        return "heartbeat_timeout"
    return None


def _load_watchdog_authorization(
    lease_path: Path,
    lease: Mapping[str, object],
    *,
    lease_file_sha256: str,
) -> dict[str, object] | None:
    authorization_path = Path(str(lease["runManifestPath"])).parent / "promotion-authorization.json"
    try:
        payload = authorization_path.read_bytes()
    except FileNotFoundError:
        return None
    except OSError as error:
        raise FinalWindowError(
            "CUTOVER_AUTHORIZATION_INVALID",
            "The watchdog cannot read the promotion authorization.",
        ) from error
    try:
        document = _canonical_mapping(payload)
        unsigned = {
            key: value
            for key, value in document.items()
            if key != "authorizationSha256"
        }
        if (
            document.get("schemaVersion") != 1
            or document.get("manifestKind") != "promotion-authorization"
            or document.get("runId") != lease.get("runId")
            or Path(str(document["finalEvidenceRunManifestPath"])).resolve(strict=True)
            != Path(str(lease["runManifestPath"])).resolve(strict=True)
            or document.get("finalEvidenceRunManifestSha256")
            != lease.get("runManifestFileSha256")
            or Path(str(document["startupSnapshotPath"])).resolve(strict=True)
            != Path(str(lease["startupSnapshotPath"])).resolve(strict=True)
            or document.get("startupSnapshotSha256")
            != lease.get("startupSnapshotFileSha256")
            or Path(str(document["cutoverLeasePath"])).resolve(strict=True) != lease_path
            or document.get("cutoverLeaseSha256") != lease_file_sha256
            or hashlib.sha256(canonical_json_bytes(unsigned)).hexdigest()
            != document.get("authorizationSha256")
        ):
            raise ValueError("authorization binding mismatch")
        issued = _parse_utc_timestamp(document["issuedAt"])
        expires = _parse_utc_timestamp(document["expiresAt"])
        if expires <= issued or expires - issued > timedelta(minutes=15):
            raise ValueError("authorization TTL invalid")
    except (KeyError, OSError, TypeError, ValueError) as error:
        raise FinalWindowError(
            "CUTOVER_AUTHORIZATION_INVALID",
            "The watchdog promotion authorization is invalid.",
        ) from error
    return document


def _canonical_mapping(payload: bytes) -> dict[str, object]:
    duplicates: list[str] = []

    def pairs(values: list[tuple[str, object]]) -> dict[str, object]:
        document: dict[str, object] = {}
        for key, value in values:
            if key in document:
                duplicates.append(key)
            document[key] = value
        return document

    try:
        document = json.loads(payload.decode("utf-8"), object_pairs_hook=pairs)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("invalid JSON") from error
    if duplicates or not isinstance(document, dict) or canonical_json_bytes(document) != payload:
        raise ValueError("invalid canonical document")
    return document


def _parse_utc_timestamp(value: object) -> datetime:
    if not isinstance(value, str):
        raise ValueError("timestamp must be a string")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return _aware_utc(parsed, code="CUTOVER_WATCHDOG_CLOCK_INVALID")


def _aware_utc(value: datetime, *, code: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise FinalWindowError(code, "The final-window watchdog clock must be timezone-aware.")
    return value.astimezone(timezone.utc)


def _required_positive_int(value: object) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise FinalWindowError(
            "CUTOVER_LEASE_INVALID",
            "The final-window process identity is invalid.",
        )
    return value


def _pid_is_alive(pid: int) -> bool:
    if os.name == "nt":
        from backend.app.providers.runtime_lease import runtime_pid_is_alive

        return runtime_pid_is_alive(pid)
    try:
        os.kill(pid, 0)
    except (OSError, ValueError):
        return False
    return True


def create_production_startup_snapshot(
    *,
    final_evidence_run_manifest: str | os.PathLike[str],
    expected_final_evidence_run_manifest_sha256: str,
    build_identity_manifest: str | os.PathLike[str],
    database_identity_manifest: str | os.PathLike[str],
    frozen_node_rollback_map: Mapping[str, object],
    output: str | os.PathLike[str],
    clock: Callable[[], datetime] | None = None,
) -> ProductionStartupSnapshot:
    try:
        run = load_evidence_run_manifest(
            final_evidence_run_manifest,
            expected_file_sha256=expected_final_evidence_run_manifest_sha256,
        )
    except EvidenceCaptureError as error:
        raise FinalWindowError("STARTUP_RUN_INVALID", str(error)) from error
    if run.phase != "final":
        raise FinalWindowError(
            "STARTUP_RUN_INVALID",
            "A production startup snapshot requires a final evidence run.",
        )
    try:
        build = load_build_identity_manifest(build_identity_manifest)
        database = load_database_evidence_identity_manifest(database_identity_manifest)
    except (BuildIdentityError, DatabaseIdentityError) as error:
        raise FinalWindowError("STARTUP_IDENTITY_MISMATCH", str(error)) from error
    if (
        build.manifest_path != run.build_identity_manifest_path
        or build.manifest_file_sha256 != run.build_identity_manifest_sha256
        or database.manifest_path != run.database_identity_manifest_path
        or database.identity_manifest_file_sha256
        != run.database_identity_manifest_sha256
        or database.subject_kind != "live"
    ):
        raise FinalWindowError(
            "STARTUP_IDENTITY_MISMATCH",
            "The startup run, build, and Live database identities do not match.",
        )
    try:
        rollback_map = validate_frozen_node_rollback_map(dict(frozen_node_rollback_map))
    except ProductionRollbackError as error:
        raise FinalWindowError("STARTUP_ROLLBACK_MAP_INVALID", str(error)) from error
    rollback_database = Path(str(rollback_map["databasePath"])).resolve(strict=False)
    if rollback_database != database.database_path:
        raise FinalWindowError(
            "STARTUP_IDENTITY_MISMATCH",
            "The frozen Node map does not name the exact Live database subject.",
        )
    output_path = Path(output).resolve(strict=False)
    if output_path.parent.resolve(strict=True) != run.run_directory or output_path.exists():
        raise FinalWindowError(
            "STARTUP_OUTPUT_INVALID",
            "The startup snapshot must be a new file in its final run directory.",
        )
    rollback_sha = hashlib.sha256(canonical_json_bytes(rollback_map)).hexdigest()
    unsigned = {
        "schemaVersion": 1,
        "snapshotKind": "production-startup",
        "runId": run.run_id,
        "runManifestPath": str(run.manifest_path),
        "runManifestFileSha256": run.manifest_file_sha256,
        "buildIdentityManifestPath": str(build.manifest_path),
        "buildIdentityManifestSha256": build.manifest_file_sha256,
        "buildId": build.build_id,
        "databaseIdentityManifestPath": str(database.manifest_path),
        "databaseIdentityManifestSha256": database.identity_manifest_file_sha256,
        "databaseLineageId": database.database_lineage_id,
        "liveSubjectDatabaseId": database.subject_database_id,
        "originReceiptPath": str(database.origin_receipt_path),
        "originReceiptFileSha256": database.origin_receipt_file_sha256,
        "runtimeNamespace": "production",
        "productionProfile": "production",
        "rollbackProfile": "frozen-node",
        "frozenNodeRollbackMap": rollback_map,
        "frozenNodeRollbackMapSha256": rollback_sha,
        "createdAt": _timestamp((clock or _utc_now)()),
    }
    self_hash = hashlib.sha256(canonical_json_bytes(unsigned)).hexdigest()
    payload = canonical_json_bytes({**unsigned, "startupSnapshotSha256": self_hash})
    try:
        exclusive_write_bytes(output_path, payload)
    except DatabaseIdentityError as error:
        raise FinalWindowError("STARTUP_OUTPUT_INVALID", str(error)) from error
    return _snapshot_from_document(output_path, payload, {**unsigned, "startupSnapshotSha256": self_hash})


def load_production_startup_snapshot(
    path: str | os.PathLike[str],
    *,
    expected_file_sha256: str | None = None,
    require_frozen_node_executable: bool = True,
) -> ProductionStartupSnapshot:
    snapshot_path = Path(path).resolve(strict=True)
    try:
        payload = snapshot_path.read_bytes()
    except OSError as error:
        raise FinalWindowError(
            "STARTUP_SNAPSHOT_INVALID",
            "The production startup snapshot cannot be read.",
        ) from error
    file_sha = hashlib.sha256(payload).hexdigest()
    if expected_file_sha256 is not None and file_sha != expected_file_sha256:
        raise FinalWindowError(
            "STARTUP_SNAPSHOT_INVALID",
            "The production startup snapshot file hash does not match.",
        )
    document = _strict_document(payload)
    unsigned = {key: value for key, value in document.items() if key != "startupSnapshotSha256"}
    if (
        document.get("schemaVersion") != 1
        or document.get("snapshotKind") != "production-startup"
        or document.get("runtimeNamespace") != "production"
        or document.get("productionProfile") != "production"
        or document.get("rollbackProfile") != "frozen-node"
        or hashlib.sha256(canonical_json_bytes(unsigned)).hexdigest()
        != document.get("startupSnapshotSha256")
    ):
        raise FinalWindowError(
            "STARTUP_SNAPSHOT_INVALID",
            "The production startup snapshot identity or self hash is invalid.",
        )
    try:
        rollback_map = validate_frozen_node_rollback_map(
            document["frozenNodeRollbackMap"],
            require_frozen_node_executable=require_frozen_node_executable,
        )
    except (KeyError, ProductionRollbackError) as error:
        raise FinalWindowError("STARTUP_SNAPSHOT_INVALID", str(error)) from error
    rollback_sha = hashlib.sha256(canonical_json_bytes(rollback_map)).hexdigest()
    if rollback_sha != document.get("frozenNodeRollbackMapSha256"):
        raise FinalWindowError(
            "STARTUP_SNAPSHOT_INVALID",
            "The frozen Node rollback map hash is invalid.",
        )
    return _snapshot_from_document(snapshot_path, payload, document)


def _snapshot_from_document(
    path: Path,
    payload: bytes,
    document: Mapping[str, object],
) -> ProductionStartupSnapshot:
    try:
        return ProductionStartupSnapshot(
            path=path,
            file_sha256=hashlib.sha256(payload).hexdigest(),
            run_id=_required_string(document["runId"]),
            run_manifest_path=Path(_required_string(document["runManifestPath"])).resolve(strict=True),
            run_manifest_file_sha256=_required_sha(document["runManifestFileSha256"]),
            build_identity_manifest_path=Path(
                _required_string(document["buildIdentityManifestPath"])
            ).resolve(strict=True),
            build_identity_manifest_sha256=_required_sha(
                document["buildIdentityManifestSha256"]
            ),
            build_id=_required_sha(document["buildId"]),
            database_identity_manifest_path=Path(
                _required_string(document["databaseIdentityManifestPath"])
            ).resolve(strict=True),
            database_identity_manifest_sha256=_required_sha(
                document["databaseIdentityManifestSha256"]
            ),
            database_lineage_id=_required_sha(document["databaseLineageId"]),
            live_subject_database_id=_required_sha(document["liveSubjectDatabaseId"]),
            origin_receipt_path=Path(
                _required_string(document["originReceiptPath"])
            ).resolve(strict=True),
            origin_receipt_file_sha256=_required_sha(document["originReceiptFileSha256"]),
            rollback_map=dict(document["frozenNodeRollbackMap"]),
            rollback_map_sha256=_required_sha(document["frozenNodeRollbackMapSha256"]),
            canonical_bytes=payload,
        )
    except (KeyError, TypeError, OSError, ValueError) as error:
        raise FinalWindowError(
            "STARTUP_SNAPSHOT_INVALID",
            "The production startup snapshot contains an invalid typed field.",
        ) from error


def _strict_document(payload: bytes) -> dict[str, object]:
    duplicates: list[str] = []

    def pairs(values: list[tuple[str, object]]) -> dict[str, object]:
        document: dict[str, object] = {}
        for key, value in values:
            if key in document:
                duplicates.append(key)
            document[key] = value
        return document

    try:
        document = json.loads(payload.decode("utf-8"), object_pairs_hook=pairs)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise FinalWindowError(
            "STARTUP_SNAPSHOT_INVALID",
            "The production startup snapshot is not valid JSON.",
        ) from error
    if (
        duplicates
        or not isinstance(document, dict)
        or set(document) != _SNAPSHOT_FIELDS
        or canonical_json_bytes(document) != payload
    ):
        raise FinalWindowError(
            "STARTUP_SNAPSHOT_INVALID",
            "The production startup snapshot schema or serialization is invalid.",
        )
    return document


def _verify_snapshot_identity(
    snapshot: ProductionStartupSnapshot,
    *,
    final_evidence_run_manifest: str | os.PathLike[str],
    expected_final_evidence_run_manifest_sha256: str,
) -> tuple[object, object]:
    try:
        run = load_evidence_run_manifest(
            final_evidence_run_manifest,
            expected_file_sha256=expected_final_evidence_run_manifest_sha256,
        )
        build = load_build_identity_manifest(snapshot.build_identity_manifest_path)
        database = load_database_evidence_identity_manifest(
            snapshot.database_identity_manifest_path
        )
        origin = verify_origin_receipt(
            snapshot.origin_receipt_path,
            snapshot.origin_receipt_file_sha256,
        )
    except Exception as error:
        raise FinalWindowError(
            "CUTOVER_IDENTITY_MISMATCH",
            "The final-window run, build, database, or OriginReceipt is invalid.",
        ) from error
    if (
        run.phase != "final"
        or run.manifest_path != snapshot.run_manifest_path
        or run.manifest_file_sha256 != snapshot.run_manifest_file_sha256
        or build.manifest_path != run.build_identity_manifest_path
        or build.manifest_file_sha256 != run.build_identity_manifest_sha256
        or build.build_id != snapshot.build_id
        or database.manifest_path != run.database_identity_manifest_path
        or database.identity_manifest_file_sha256
        != run.database_identity_manifest_sha256
        or database.subject_kind != "live"
        or database.database_lineage_id != snapshot.database_lineage_id
        or database.subject_database_id != snapshot.live_subject_database_id
        or database.origin_receipt_path != snapshot.origin_receipt_path
        or database.origin_receipt_file_sha256
        != snapshot.origin_receipt_file_sha256
        or origin.receipt_path != snapshot.origin_receipt_path
        or origin.origin_receipt_file_sha256
        != snapshot.origin_receipt_file_sha256
    ):
        raise FinalWindowError(
            "CUTOVER_IDENTITY_MISMATCH",
            "The final-window typed identities do not share one exact binding.",
        )
    return run, database


def _load_lease(
    value: str | os.PathLike[str],
    *,
    require_frozen_node_executable: bool = True,
) -> tuple[Path, bytes, dict[str, object]]:
    path = Path(value).resolve(strict=True)
    try:
        payload = path.read_bytes()
        document = _strict_json_document(payload, _LEASE_FIELDS)
        unsigned = {key: item for key, item in document.items() if key != "leaseSha256"}
        rollback_map = validate_frozen_node_rollback_map(
            document["frozenNodeRollbackMap"],
            require_frozen_node_executable=require_frozen_node_executable,
        )
        decoded_owner = base64.b64decode(
            _required_string(document["nodeActiveOwnerPayloadBase64"]), validate=True
        )
    except (OSError, KeyError, TypeError, ValueError, ProductionRollbackError) as error:
        raise FinalWindowError(
            "CUTOVER_LEASE_INVALID",
            "The final-window lease is invalid.",
        ) from error
    if (
        document.get("schemaVersion") != 1
        or document.get("leaseKind") != "final-window"
        or document.get("runtimeNamespace") != "production"
        or document.get("phase")
        not in {
            "arming",
            "armed",
            "node_quiesced",
            "authorization_issued",
            "handoff_pending",
            "python_active",
            "completed",
            "recovered",
            "recovery_failed",
        }
        or not isinstance(document.get("version"), int)
        or isinstance(document.get("version"), bool)
        or int(document["version"]) < 1
        or hashlib.sha256(canonical_json_bytes(unsigned)).hexdigest()
        != document.get("leaseSha256")
        or hashlib.sha256(decoded_owner).hexdigest()
        != document.get("nodeActiveOwnerFileSha256")
        or hashlib.sha256(canonical_json_bytes(rollback_map)).hexdigest()
        != document.get("frozenNodeRollbackMapSha256")
        or Path(_required_string(document["cutoverTokenFilePath"])).resolve(strict=True)
        != path.with_suffix(".token")
        or Path(_required_string(document["cutoverLeasePath"])).resolve(strict=True)
        != path
        or path.name != f"final-window-{document.get('runId')}.json"
    ):
        raise FinalWindowError(
            "CUTOVER_LEASE_INVALID",
            "The final-window lease identity or self hash is invalid.",
        )
    return path, payload, document


def heartbeat_cutover_lease(
    *,
    cutover_lease: str | os.PathLike[str],
    cutover_token_file: str | os.PathLike[str],
    heartbeat_timeout_seconds: int,
    expected_version: int | None = None,
    clock: Callable[[], datetime] | None = None,
) -> CutoverLease:
    if (
        not isinstance(heartbeat_timeout_seconds, int)
        or isinstance(heartbeat_timeout_seconds, bool)
        or not 1 <= heartbeat_timeout_seconds <= 300
        or (
            expected_version is not None
            and (
                not isinstance(expected_version, int)
                or isinstance(expected_version, bool)
                or expected_version < 1
            )
        )
    ):
        raise FinalWindowError(
            "CUTOVER_ARGUMENT_INVALID",
            "The heartbeat timeout or expected lease version is invalid.",
        )
    lease_path = Path(cutover_lease).resolve(strict=True)
    current_clock = clock or _utc_now
    with _FILE_CAS_LOCK, _lease_file_lock(lease_path):
        _path, payload, lease = _load_lease(lease_path)
        _verify_token(lease, cutover_token_file)
        if lease["phase"] not in {"armed", "node_quiesced", "authorization_issued"}:
            raise FinalWindowError(
                "CUTOVER_PHASE_INVALID",
                "This final-window phase does not accept heartbeats.",
            )
        if expected_version is not None and lease["version"] != expected_version:
            raise FinalWindowError(
                "CUTOVER_LEASE_CAS_FAILED",
                "The final-window lease version changed concurrently.",
            )
        updated = _update_lease(
            lease_path,
            expected_payload=payload,
            changes={
                "heartbeatDeadline": _timestamp(
                    current_clock() + timedelta(seconds=heartbeat_timeout_seconds)
                )
            },
            clock=current_clock,
            lock_held=True,
        )
        canonical = lease_path.read_bytes()
    return CutoverLease(
        path=lease_path,
        token_file_path=Path(str(updated["cutoverTokenFilePath"])),
        run_id=str(updated["runId"]),
        phase=str(updated["phase"]),
        version=int(updated["version"]),
        file_sha256=hashlib.sha256(canonical).hexdigest(),
        canonical_bytes=canonical,
    )


def load_cutover_lease_binding(
    cutover_lease: str | os.PathLike[str],
) -> CutoverLeaseBinding:
    path, payload, lease = _load_lease(cutover_lease)
    try:
        token_path = Path(_required_string(lease["cutoverTokenFilePath"])).resolve(
            strict=True
        )
        token_sha = _required_sha(lease["cutoverTokenSha256"])
        startup_path = Path(_required_string(lease["startupSnapshotPath"])).resolve(
            strict=True
        )
        startup_sha = _required_sha(lease["startupSnapshotFileSha256"])
        run_id = _required_string(lease["runId"])
        phase = _required_string(lease["phase"])
        if hashlib.sha256(token_path.read_bytes()).hexdigest() != token_sha:
            raise ValueError("cutover token hash mismatch")
    except (KeyError, OSError, TypeError, ValueError) as error:
        raise FinalWindowError(
            "CUTOVER_LEASE_INVALID",
            "The final-window capability binding is invalid.",
        ) from error
    return CutoverLeaseBinding(
        path=path,
        file_sha256=hashlib.sha256(payload).hexdigest(),
        run_id=run_id,
        startup_snapshot_path=startup_path,
        startup_snapshot_sha256=startup_sha,
        token_file_path=token_path,
        token_sha256=token_sha,
        phase=phase,
    )


def _verify_token(
    lease: Mapping[str, object],
    token_file: str | os.PathLike[str],
) -> bytes:
    try:
        path = Path(token_file).resolve(strict=True)
        expected_path = Path(_required_string(lease["cutoverTokenFilePath"])).resolve(
            strict=True
        )
        token = path.read_bytes()
    except (OSError, KeyError, ValueError) as error:
        raise FinalWindowError(
            "CUTOVER_TOKEN_MISMATCH",
            "The cutover token capability is invalid.",
        ) from error
    if (
        path != expected_path
        or len(token) != 32
        or hashlib.sha256(token).hexdigest() != lease.get("cutoverTokenSha256")
    ):
        raise FinalWindowError(
            "CUTOVER_TOKEN_MISMATCH",
            "The cutover token capability does not match the lease.",
        )
    verify_owner_only_token_file(path)
    return token


def _update_lease(
    path: Path,
    *,
    expected_payload: bytes,
    changes: Mapping[str, object],
    clock: Callable[[], datetime],
    lock_held: bool = False,
) -> dict[str, object]:
    lock = nullcontext() if lock_held else _lease_file_lock(path)
    with _FILE_CAS_LOCK, lock:
        current = path.read_bytes()
        if current != expected_payload:
            raise FinalWindowError(
                "CUTOVER_LEASE_CAS_FAILED",
                "The final-window lease changed concurrently.",
            )
        document = _strict_json_document(current, _LEASE_FIELDS)
        unsigned = {key: value for key, value in document.items() if key != "leaseSha256"}
        unsigned.update(changes)
        unsigned["version"] = int(document["version"]) + 1
        unsigned["previousLeaseFileSha256"] = hashlib.sha256(current).hexdigest()
        unsigned["updatedAt"] = _timestamp(clock())
        payload = _self_hashed(unsigned, "leaseSha256")
        _atomic_replace(path, payload)
    return _strict_json_document(payload, _LEASE_FIELDS)


@contextmanager
def _lease_file_lock(path: Path):
    held_paths = getattr(_LEASE_LOCK_STATE, "paths", None)
    if held_paths is None:
        held_paths = set()
        _LEASE_LOCK_STATE.paths = held_paths
    lock_key = str(path.resolve(strict=False))
    if lock_key in held_paths:
        yield
        return
    lock_path = path.with_name(path.name + ".lock")
    try:
        descriptor = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
    except OSError as error:
        raise FinalWindowError(
            "CUTOVER_LEASE_LOCK_FAILED",
            "The final-window lease lock could not be opened.",
        ) from error
    locked = False
    try:
        if os.fstat(descriptor).st_size == 0:
            os.write(descriptor, b"\0")
            os.fsync(descriptor)
        os.lseek(descriptor, 0, os.SEEK_SET)
        if os.name == "nt":
            import msvcrt

            deadline = time.monotonic() + 10.0
            while True:
                try:
                    msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
                    locked = True
                    break
                except OSError as error:
                    if time.monotonic() >= deadline:
                        raise FinalWindowError(
                            "CUTOVER_LEASE_LOCK_FAILED",
                            "The final-window lease lock timed out.",
                        ) from error
                    time.sleep(0.01)
        else:
            import fcntl

            fcntl.flock(descriptor, fcntl.LOCK_EX)
            locked = True
        held_paths.add(lock_key)
        yield
    finally:
        held_paths.discard(lock_key)
        if locked:
            try:
                os.lseek(descriptor, 0, os.SEEK_SET)
                if os.name == "nt":
                    import msvcrt

                    msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(descriptor, fcntl.LOCK_UN)
            except OSError:
                pass
        os.close(descriptor)


def _runtime_owner_state_payload(
    lease: Mapping[str, object],
    *,
    owner_state: str,
    clock: Callable[[], datetime],
) -> bytes:
    unsigned = {
        "schemaVersion": 2,
        "markerKind": "runtime-owner",
        "ownerState": owner_state,
        "runtimeNamespace": lease["runtimeNamespace"],
        "runId": lease["runId"],
        "cutoverLeasePath": lease["cutoverLeasePath"],
        "buildIdentityManifestPath": lease["buildIdentityManifestPath"],
        "buildIdentityManifestSha256": lease["buildIdentityManifestSha256"],
        "databaseIdentityManifestPath": lease["databaseIdentityManifestPath"],
        "databaseIdentityManifestSha256": lease["databaseIdentityManifestSha256"],
        "databaseLineageId": lease["databaseLineageId"],
        "subjectDatabaseId": lease["liveSubjectDatabaseId"],
        "previousNodeOwnerFileSha256": lease["nodeActiveOwnerFileSha256"],
        "updatedAt": _timestamp(clock()),
    }
    return _self_hashed(unsigned, "ownerMarkerSha256")


def _commit_recovered_node_owner(
    *,
    operations: object,
    handle: object,
    rollback_map: Mapping[str, object],
    owner_path: Path,
    expected_owner_payload: bytes,
    previous_node_owner_payload: bytes,
) -> None:
    native_commit = getattr(operations, "commit_frozen_node_owner", None)
    if callable(native_commit):
        native_commit(
            handle,
            rollback_map,
            owner_marker=owner_path,
            expected_owner_payload=expected_owner_payload,
            previous_node_owner_payload=previous_node_owner_payload,
        )
        return
    _cas_replace(owner_path, expected_owner_payload, previous_node_owner_payload)


def _load_recovery(
    path: Path,
    *,
    expected_run_id: str,
) -> FrozenNodeRecovery:
    try:
        payload = path.read_bytes()
        document = _strict_json_document(payload, _RECOVERY_FIELDS)
    except (OSError, ValueError) as error:
        raise FinalWindowError(
            "CUTOVER_RECOVERY_INVALID",
            "The existing abort recovery record is invalid.",
        ) from error
    unsigned = {key: value for key, value in document.items() if key != "recoverySha256"}
    if (
        document.get("schemaVersion") != 1
        or document.get("recoveryKind") != "frozen-node-abort"
        or document.get("runId") != expected_run_id
        or document.get("ownerState") != "node_active"
        or hashlib.sha256(canonical_json_bytes(unsigned)).hexdigest()
        != document.get("recoverySha256")
    ):
        raise FinalWindowError(
            "CUTOVER_RECOVERY_INVALID",
            "The existing abort recovery record identity is invalid.",
        )
    return _recovery_from_payload(path, payload)


def _recovery_from_payload(path: Path, payload: bytes) -> FrozenNodeRecovery:
    document = _strict_json_document(payload, _RECOVERY_FIELDS)
    return FrozenNodeRecovery(
        path=path,
        file_sha256=hashlib.sha256(payload).hexdigest(),
        owner_state=_required_string(document["ownerState"]),
        run_id=_required_string(document["runId"]),
        canonical_bytes=payload,
    )


def _strict_json_document(
    payload: bytes,
    expected_fields: frozenset[str],
) -> dict[str, object]:
    duplicates: list[str] = []

    def pairs(values: list[tuple[str, object]]) -> dict[str, object]:
        document: dict[str, object] = {}
        for key, value in values:
            if key in document:
                duplicates.append(key)
            document[key] = value
        return document

    try:
        document = json.loads(payload.decode("utf-8"), object_pairs_hook=pairs)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("invalid JSON") from error
    if (
        duplicates
        or not isinstance(document, dict)
        or set(document) != expected_fields
        or canonical_json_bytes(document) != payload
    ):
        raise ValueError("invalid canonical schema")
    return document


def _self_hashed(unsigned: Mapping[str, object], field: str) -> bytes:
    self_hash = hashlib.sha256(canonical_json_bytes(unsigned)).hexdigest()
    return canonical_json_bytes({**unsigned, field: self_hash})


def create_owner_only_token_file(
    path: str | os.PathLike[str],
    payload: bytes,
) -> Path:
    token_path = Path(path).resolve(strict=False)
    if not isinstance(payload, bytes) or len(payload) != 32:
        raise FinalWindowError(
            "CUTOVER_TOKEN_INVALID",
            "The cutover token must contain exactly 256 bits.",
        )
    _exclusive_private_write(token_path, payload)
    return token_path.resolve(strict=True)


def verify_owner_only_token_file(path: str | os.PathLike[str]) -> None:
    token_path = Path(path).resolve(strict=True)
    try:
        metadata = token_path.lstat()
        if not stat.S_ISREG(metadata.st_mode) or token_path.is_symlink():
            raise OSError("token is not a regular file")
        if os.name == "nt":
            _verify_windows_owner_only_acl(token_path)
        elif metadata.st_uid != os.getuid() or stat.S_IMODE(metadata.st_mode) != 0o600:
            raise OSError("token owner or mode is not private")
    except (OSError, ValueError) as error:
        raise FinalWindowError(
            "CUTOVER_TOKEN_PERMISSIONS_INVALID",
            "The final-window token is not restricted to the current OS user.",
        ) from error


def _exclusive_private_write(path: Path, payload: bytes) -> None:
    if not path.parent.is_dir():
        raise FinalWindowError(
            "CUTOVER_OUTPUT_INVALID",
            "The final-window token parent directory does not exist.",
        )
    try:
        descriptor = (
            _open_windows_owner_only_file(path)
            if os.name == "nt"
            else os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        )
    except OSError as error:
        raise FinalWindowError(
            "CUTOVER_OUTPUT_INVALID",
            "The final-window token could not be exclusively created.",
        ) from error
    try:
        offset = 0
        while offset < len(payload):
            written = os.write(descriptor, payload[offset:])
            if written <= 0:
                raise OSError("short token write")
            offset += written
        os.fsync(descriptor)
    except Exception:
        os.close(descriptor)
        _unlink_new_file(path)
        raise
    else:
        os.close(descriptor)
    try:
        _apply_owner_only_permissions(path)
        verify_owner_only_token_file(path)
    except (OSError, FinalWindowError) as error:
        _unlink_new_file(path)
        raise FinalWindowError(
            "CUTOVER_TOKEN_PERMISSIONS_INVALID",
            "The final-window token permissions could not be restricted.",
        ) from error


def _apply_owner_only_permissions(path: Path) -> None:
    if os.name == "nt":
        _set_windows_owner_only_acl(path)
    else:
        os.chmod(path, 0o600)


def _set_windows_owner_only_acl(path: Path) -> None:
    import ctypes
    from ctypes import wintypes

    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.LocalFree.argtypes = (wintypes.HLOCAL,)
    kernel32.LocalFree.restype = wintypes.HLOCAL
    security_descriptor = _windows_owner_only_security_descriptor(advapi32, kernel32)
    try:
        dacl_security_information = 0x00000004
        protected_dacl_security_information = 0x80000000
        advapi32.SetFileSecurityW.argtypes = (
            wintypes.LPCWSTR,
            wintypes.DWORD,
            wintypes.LPVOID,
        )
        advapi32.SetFileSecurityW.restype = wintypes.BOOL
        if not advapi32.SetFileSecurityW(
            str(path),
            dacl_security_information | protected_dacl_security_information,
            security_descriptor,
        ):
            raise ctypes.WinError(ctypes.get_last_error())
    finally:
        kernel32.LocalFree(security_descriptor)


def _open_windows_owner_only_file(path: Path) -> int:
    import ctypes
    from ctypes import wintypes
    import msvcrt

    class SecurityAttributes(ctypes.Structure):
        _fields_ = (
            ("nLength", wintypes.DWORD),
            ("lpSecurityDescriptor", wintypes.LPVOID),
            ("bInheritHandle", wintypes.BOOL),
        )

    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.LocalFree.argtypes = (wintypes.HLOCAL,)
    kernel32.LocalFree.restype = wintypes.HLOCAL
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    kernel32.CloseHandle.restype = wintypes.BOOL
    kernel32.CreateFileW.argtypes = (
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.POINTER(SecurityAttributes),
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    )
    kernel32.CreateFileW.restype = wintypes.HANDLE
    security_descriptor = _windows_owner_only_security_descriptor(advapi32, kernel32)
    attributes = SecurityAttributes(
        ctypes.sizeof(SecurityAttributes),
        security_descriptor,
        False,
    )
    handle = wintypes.HANDLE()
    try:
        handle = kernel32.CreateFileW(
            str(path),
            0x40000000,
            0,
            ctypes.byref(attributes),
            1,
            0x00000080,
            None,
        )
        if handle == wintypes.HANDLE(-1).value:
            raise ctypes.WinError(ctypes.get_last_error())
    finally:
        kernel32.LocalFree(security_descriptor)
    try:
        return msvcrt.open_osfhandle(
            int(handle),
            os.O_WRONLY | getattr(os, "O_BINARY", 0),
        )
    except Exception:
        kernel32.CloseHandle(handle)
        raise


def _windows_owner_only_security_descriptor(
    advapi32: object,
    kernel32: object,
) -> object:
    import ctypes
    from ctypes import wintypes

    sid = _windows_current_user_sid(advapi32, kernel32)
    sid_buffer = ctypes.create_string_buffer(sid)
    sid_text_pointer = wintypes.LPWSTR()
    advapi32.ConvertSidToStringSidW.argtypes = (
        wintypes.LPVOID,
        ctypes.POINTER(wintypes.LPWSTR),
    )
    advapi32.ConvertSidToStringSidW.restype = wintypes.BOOL
    if not advapi32.ConvertSidToStringSidW(sid_buffer, ctypes.byref(sid_text_pointer)):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        sddl = f"D:P(A;;FA;;;{sid_text_pointer.value})"
    finally:
        kernel32.LocalFree(sid_text_pointer)
    security_descriptor = wintypes.LPVOID()
    descriptor_size = wintypes.DWORD()
    advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = (
        wintypes.LPCWSTR,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.LPVOID),
        ctypes.POINTER(wintypes.DWORD),
    )
    advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW.restype = wintypes.BOOL
    if not advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW(
        sddl,
        1,
        ctypes.byref(security_descriptor),
        ctypes.byref(descriptor_size),
    ):
        raise ctypes.WinError(ctypes.get_last_error())
    return security_descriptor


def _verify_windows_owner_only_acl(path: Path) -> None:
    import ctypes
    from ctypes import wintypes

    class AclSizeInformation(ctypes.Structure):
        _fields_ = (
            ("AceCount", wintypes.DWORD),
            ("AclBytesInUse", wintypes.DWORD),
            ("AclBytesFree", wintypes.DWORD),
        )

    class AceHeader(ctypes.Structure):
        _fields_ = (
            ("AceType", wintypes.BYTE),
            ("AceFlags", wintypes.BYTE),
            ("AceSize", wintypes.WORD),
        )

    class AccessAllowedAce(ctypes.Structure):
        _fields_ = (
            ("Header", AceHeader),
            ("Mask", wintypes.DWORD),
            ("SidStart", wintypes.DWORD),
        )

    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    current_sid = ctypes.create_string_buffer(_windows_current_user_sid(advapi32, kernel32))
    owner_security_information = 0x00000001
    dacl_security_information = 0x00000004
    needed = wintypes.DWORD()
    advapi32.GetFileSecurityW.argtypes = (
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
    )
    advapi32.GetFileSecurityW.restype = wintypes.BOOL
    advapi32.GetFileSecurityW(
        str(path),
        owner_security_information | dacl_security_information,
        None,
        0,
        ctypes.byref(needed),
    )
    if needed.value == 0:
        raise ctypes.WinError(ctypes.get_last_error())
    descriptor = ctypes.create_string_buffer(needed.value)
    if not advapi32.GetFileSecurityW(
        str(path),
        owner_security_information | dacl_security_information,
        descriptor,
        needed,
        ctypes.byref(needed),
    ):
        raise ctypes.WinError(ctypes.get_last_error())
    owner_sid = wintypes.LPVOID()
    owner_defaulted = wintypes.BOOL()
    advapi32.GetSecurityDescriptorOwner.argtypes = (
        wintypes.LPVOID,
        ctypes.POINTER(wintypes.LPVOID),
        ctypes.POINTER(wintypes.BOOL),
    )
    advapi32.GetSecurityDescriptorOwner.restype = wintypes.BOOL
    advapi32.EqualSid.argtypes = (wintypes.LPVOID, wintypes.LPVOID)
    advapi32.EqualSid.restype = wintypes.BOOL
    if not advapi32.GetSecurityDescriptorOwner(
        descriptor,
        ctypes.byref(owner_sid),
        ctypes.byref(owner_defaulted),
    ) or not advapi32.EqualSid(owner_sid, current_sid):
        raise OSError("token owner SID differs from the current user")
    dacl_present = wintypes.BOOL()
    dacl = wintypes.LPVOID()
    dacl_defaulted = wintypes.BOOL()
    advapi32.GetSecurityDescriptorDacl.argtypes = (
        wintypes.LPVOID,
        ctypes.POINTER(wintypes.BOOL),
        ctypes.POINTER(wintypes.LPVOID),
        ctypes.POINTER(wintypes.BOOL),
    )
    advapi32.GetSecurityDescriptorDacl.restype = wintypes.BOOL
    if (
        not advapi32.GetSecurityDescriptorDacl(
            descriptor,
            ctypes.byref(dacl_present),
            ctypes.byref(dacl),
            ctypes.byref(dacl_defaulted),
        )
        or not dacl_present.value
        or not dacl.value
    ):
        raise OSError("token DACL is absent")
    control = wintypes.WORD()
    revision = wintypes.DWORD()
    advapi32.GetSecurityDescriptorControl.argtypes = (
        wintypes.LPVOID,
        ctypes.POINTER(wintypes.WORD),
        ctypes.POINTER(wintypes.DWORD),
    )
    advapi32.GetSecurityDescriptorControl.restype = wintypes.BOOL
    if not advapi32.GetSecurityDescriptorControl(
        descriptor,
        ctypes.byref(control),
        ctypes.byref(revision),
    ) or not control.value & 0x1000:
        raise OSError("token DACL is not protected")
    information = AclSizeInformation()
    advapi32.GetAclInformation.argtypes = (
        wintypes.LPVOID,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
    )
    advapi32.GetAclInformation.restype = wintypes.BOOL
    if not advapi32.GetAclInformation(
        dacl,
        ctypes.byref(information),
        ctypes.sizeof(information),
        2,
    ) or information.AceCount != 1:
        raise OSError("token DACL does not contain exactly one ACE")
    ace_pointer = wintypes.LPVOID()
    advapi32.GetAce.argtypes = (
        wintypes.LPVOID,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.LPVOID),
    )
    advapi32.GetAce.restype = wintypes.BOOL
    if not advapi32.GetAce(dacl, 0, ctypes.byref(ace_pointer)):
        raise ctypes.WinError(ctypes.get_last_error())
    ace = ctypes.cast(ace_pointer, ctypes.POINTER(AccessAllowedAce)).contents
    ace_sid = ctypes.c_void_p(ace_pointer.value + AccessAllowedAce.SidStart.offset)
    if (
        ace.Header.AceType != 0
        or ace.Header.AceFlags != 0
        or ace.Mask != 0x001F01FF
        or not advapi32.EqualSid(ace_sid, current_sid)
    ):
        raise OSError("token DACL grants access beyond the current user")


def _windows_current_user_sid(advapi32: object, kernel32: object) -> bytes:
    import ctypes
    from ctypes import wintypes

    class SidAndAttributes(ctypes.Structure):
        _fields_ = (("Sid", wintypes.LPVOID), ("Attributes", wintypes.DWORD))

    class TokenUser(ctypes.Structure):
        _fields_ = (("User", SidAndAttributes),)

    kernel32.GetCurrentProcess.argtypes = ()
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    kernel32.CloseHandle.restype = wintypes.BOOL
    advapi32.OpenProcessToken.argtypes = (
        wintypes.HANDLE,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.HANDLE),
    )
    advapi32.OpenProcessToken.restype = wintypes.BOOL
    advapi32.GetTokenInformation.argtypes = (
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
    )
    advapi32.GetTokenInformation.restype = wintypes.BOOL
    advapi32.GetLengthSid.argtypes = (wintypes.LPVOID,)
    advapi32.GetLengthSid.restype = wintypes.DWORD
    token = wintypes.HANDLE()
    if not advapi32.OpenProcessToken(
        kernel32.GetCurrentProcess(),
        0x0008,
        ctypes.byref(token),
    ):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        needed = wintypes.DWORD()
        advapi32.GetTokenInformation(token, 1, None, 0, ctypes.byref(needed))
        if needed.value == 0:
            raise ctypes.WinError(ctypes.get_last_error())
        token_buffer = ctypes.create_string_buffer(needed.value)
        if not advapi32.GetTokenInformation(
            token,
            1,
            token_buffer,
            needed,
            ctypes.byref(needed),
        ):
            raise ctypes.WinError(ctypes.get_last_error())
        user = ctypes.cast(token_buffer, ctypes.POINTER(TokenUser)).contents
        length = advapi32.GetLengthSid(user.User.Sid)
        if length <= 0:
            raise ctypes.WinError(ctypes.get_last_error())
        return ctypes.string_at(user.User.Sid, length)
    finally:
        kernel32.CloseHandle(token)


def _cas_replace(path: Path, expected: bytes, replacement: bytes) -> None:
    with _FILE_CAS_LOCK:
        try:
            current = path.read_bytes()
        except OSError as error:
            raise FinalWindowError(
                "CUTOVER_OWNER_CAS_FAILED",
                "The runtime owner marker cannot be read for CAS.",
            ) from error
        if current != expected:
            raise FinalWindowError(
                "CUTOVER_OWNER_CAS_FAILED",
                "The runtime owner marker changed concurrently.",
            )
        _atomic_replace(path, replacement)


def _atomic_replace(path: Path, payload: bytes) -> None:
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb", closefd=True) as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        _replace_with_windows_retry(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _replace_with_windows_retry(source: Path, destination: Path) -> None:
    attempts = _WINDOWS_REPLACE_RETRY_ATTEMPTS if os.name == "nt" else 1
    for attempt in range(attempts):
        try:
            os.replace(source, destination)
            return
        except OSError as error:
            retryable = (
                os.name == "nt"
                and getattr(error, "winerror", None) in {5, 32, 33}
                and attempt + 1 < attempts
            )
            if not retryable:
                raise
            time.sleep(_WINDOWS_REPLACE_RETRY_SECONDS)


def _unlink_new_file(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        return

def _required_string(value: object) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError("required string")
    return value


def _required_sha(value: object) -> str:
    text = _required_string(value)
    if len(text) != 64 or any(character not in "0123456789abcdef" for character in text):
        raise ValueError("required sha256")
    return text


def _timestamp(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise FinalWindowError("STARTUP_CLOCK_INVALID", "The startup clock must be timezone-aware.")
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


__all__ = [
    "CutoverLease",
    "CutoverLeaseBinding",
    "FinalWindowError",
    "FinalWindowCoordinator",
    "FinalWindowWatchdog",
    "FrozenNodeRecovery",
    "ProductionStartupSnapshot",
    "WatchdogResult",
    "create_owner_only_token_file",
    "create_production_startup_snapshot",
    "heartbeat_cutover_lease",
    "load_cutover_lease_binding",
    "load_production_startup_snapshot",
    "verify_owner_only_token_file",
]
