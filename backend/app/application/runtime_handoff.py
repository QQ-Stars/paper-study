from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import base64
import hashlib
import json
import os

from backend.app.api.compat.database_identity import (
    DatabaseIdentityError,
    canonical_json_bytes,
    exclusive_write_bytes,
)
from backend.app.application.final_window import (
    ProductionStartupSnapshot,
    _cas_replace,
    _load_lease,
    _self_hashed,
    _timestamp,
    _update_lease,
    _verify_token,
    load_production_startup_snapshot,
)
from backend.app.application.production_rollback import execute_rollback_tail


_AUTHORIZATION_REQUIRED_FIELDS = frozenset(
    {
        "schemaVersion",
        "manifestKind",
        "runId",
        "finalEvidenceRunManifestPath",
        "finalEvidenceRunManifestSha256",
        "startupSnapshotPath",
        "startupSnapshotSha256",
        "cutoverLeasePath",
        "cutoverLeaseSha256",
        "issuedAt",
        "expiresAt",
        "authorizationSha256",
    }
)
_AUTHORIZATION_OPTIONAL_FIELDS = frozenset(
    {
        "authorizationId",
        "buildIdentityManifestPath",
        "buildIdentityManifestSha256",
        "buildId",
        "databaseIdentityManifestPath",
        "databaseIdentityManifestSha256",
        "databaseLineageId",
        "liveSubjectDatabaseId",
        "originReceiptPath",
        "originReceiptFileSha256",
        "cutoverBackupPath",
        "cutoverBackupSha256",
        "cutoverManifestPath",
        "cutoverManifestSha256",
        "runtimeNamespace",
        "roles",
        "nodeOwnerMarkerPath",
        "nodeOwnerMarkerVersion",
        "nodeZeroResourceEvidenceSha256",
    }
)
_RECEIPT_FIELDS = frozenset(
    {
        "schemaVersion",
        "receiptKind",
        "receiptId",
        "runId",
        "authorizationPath",
        "authorizationFileSha256",
        "cutoverLeasePath",
        "cutoverLeaseFileSha256",
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
        "runtimeNamespace",
        "roleLocks",
        "smokeEvidence",
        "committedAt",
        "handoffReceiptSha256",
    }
)


class RuntimeHandoffError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(f"{code}: {message}")


@dataclass(frozen=True, slots=True)
class HandoffLease:
    authorization_path: Path
    authorization_file_sha256: str
    cutover_lease_path: Path
    cutover_lease_payload: bytes
    startup_snapshot: ProductionStartupSnapshot
    owner_marker_path: Path
    pending_owner_payload: bytes
    run_id: str


@dataclass(frozen=True, slots=True)
class HandoffReceipt:
    path: Path
    file_sha256: str
    receipt_id: str
    run_id: str
    startup_snapshot_file_sha256: str
    database_lineage_id: str
    live_subject_database_id: str
    canonical_bytes: bytes


class ProductionPromotionCoordinator:
    def __init__(
        self,
        *,
        operations: object,
        clock: Callable[[], datetime] | None = None,
        receipt_id_factory: Callable[[], str] | None = None,
    ) -> None:
        self._operations = operations
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._receipt_id_factory = receipt_id_factory or (
            lambda: os.urandom(16).hex()
        )

    def begin_handoff(
        self,
        *,
        authorization: str | os.PathLike[str],
        expected_authorization_sha256: str,
        cutover_lease: str | os.PathLike[str],
        cutover_token_file: str | os.PathLike[str],
        startup_snapshot: str | os.PathLike[str],
        expected_startup_snapshot_sha256: str,
        owner_marker: str | os.PathLike[str],
    ) -> HandoffLease:
        authorization_path, authorization_payload, authorization_document = (
            _load_authorization(
                authorization,
                expected_file_sha256=expected_authorization_sha256,
                now=self._clock(),
            )
        )
        consumed_path = authorization_path.with_name(
            f"{authorization_path.stem}.consumed.json"
        )
        if consumed_path.exists():
            raise RuntimeHandoffError(
                "PROMOTION_AUTHORIZATION_REPLAYED",
                "The promotion authorization was already consumed.",
            )
        snapshot = load_production_startup_snapshot(
            startup_snapshot,
            expected_file_sha256=expected_startup_snapshot_sha256,
        )
        lease_path, lease_payload, lease = _load_lease(cutover_lease)
        _verify_token(lease, cutover_token_file)
        marker_path = Path(owner_marker).resolve(strict=True)
        if (
            authorization_document["runId"] != snapshot.run_id
            or authorization_document["runId"] != lease["runId"]
            or Path(authorization_document["finalEvidenceRunManifestPath"]).resolve(
                strict=True
            )
            != snapshot.run_manifest_path
            or authorization_document["finalEvidenceRunManifestSha256"]
            != snapshot.run_manifest_file_sha256
            or Path(authorization_document["startupSnapshotPath"]).resolve(strict=True)
            != snapshot.path
            or authorization_document["startupSnapshotSha256"] != snapshot.file_sha256
            or Path(authorization_document["cutoverLeasePath"]).resolve(strict=True)
            != lease_path
            or authorization_document["cutoverLeaseSha256"]
            != hashlib.sha256(lease_payload).hexdigest()
            or marker_path != Path(lease["ownerMarkerPath"]).resolve(strict=True)
            or lease["phase"] not in {"node_quiesced", "authorization_issued"}
        ):
            raise RuntimeHandoffError(
                "PROMOTION_IDENTITY_MISMATCH",
                "Authorization, startup snapshot, lease, or owner marker does not match.",
            )
        _verify_optional_authorization_bindings(
            authorization_document,
            snapshot=snapshot,
            lease=lease,
        )
        owner_payload = marker_path.read_bytes()
        owner_document = _load_runtime_owner(owner_payload)
        if (
            owner_document.get("ownerState") != "node_quiesced"
            or owner_document.get("runId") != snapshot.run_id
        ):
            raise RuntimeHandoffError(
                "PROMOTION_OWNER_INVALID",
                "Promotion must take over the exact node_quiesced owner.",
            )
        consumed_unsigned = {
            "schemaVersion": 1,
            "markerKind": "promotion-authorization-consumed",
            "runId": snapshot.run_id,
            "authorizationPath": str(authorization_path),
            "authorizationFileSha256": hashlib.sha256(
                authorization_payload
            ).hexdigest(),
            "consumedAt": _timestamp(self._clock()),
        }
        consumed_payload = _self_hashed(consumed_unsigned, "consumptionSha256")
        try:
            exclusive_write_bytes(consumed_path, consumed_payload)
        except DatabaseIdentityError as error:
            raise RuntimeHandoffError(
                "PROMOTION_AUTHORIZATION_REPLAYED",
                "The promotion authorization was already consumed.",
            ) from error
        pending_payload = _handoff_owner_payload(
            snapshot=snapshot,
            lease=lease,
            authorization_path=authorization_path,
            authorization_file_sha256=hashlib.sha256(authorization_payload).hexdigest(),
            owner_state="handoff_pending",
            clock=self._clock,
        )
        _cas_replace(marker_path, owner_payload, pending_payload)
        try:
            updated = _update_lease(
                lease_path,
                expected_payload=lease_payload,
                changes={"phase": "handoff_pending"},
                clock=self._clock,
            )
        except Exception:
            _cas_replace(marker_path, pending_payload, owner_payload)
            raise
        updated_payload = lease_path.read_bytes()
        takeover = getattr(self._operations, "takeover_watchdog", None)
        if callable(takeover):
            takeover(lease_path)
        return HandoffLease(
            authorization_path=authorization_path,
            authorization_file_sha256=hashlib.sha256(authorization_payload).hexdigest(),
            cutover_lease_path=lease_path,
            cutover_lease_payload=updated_payload,
            startup_snapshot=snapshot,
            owner_marker_path=marker_path,
            pending_owner_payload=pending_payload,
            run_id=snapshot.run_id,
        )

    def commit_python_owner(
        self,
        handoff: HandoffLease,
        *,
        smoke_evidence: Mapping[str, object],
        handoff_receipt_output: str | os.PathLike[str],
    ) -> HandoffReceipt:
        if not isinstance(handoff, HandoffLease):
            raise RuntimeHandoffError(
                "HANDOFF_LEASE_INVALID",
                "A typed handoff lease is required.",
            )
        role_locks = smoke_evidence.get("roleLocks")
        if (
            smoke_evidence.get("ok") is not True
            or not isinstance(role_locks, dict)
            or set(role_locks) != {"worker", "scheduler"}
            or any(not _is_sha(value) for value in role_locks.values())
        ):
            raise RuntimeHandoffError(
                "PYTHON_PROMOTION_SMOKE_INVALID",
                "Python promotion requires successful smoke and both role lock identities.",
            )
        lease_path, lease_payload, lease = _load_lease(handoff.cutover_lease_path)
        if (
            lease_payload != handoff.cutover_lease_payload
            or lease["phase"] != "handoff_pending"
            or handoff.owner_marker_path.read_bytes() != handoff.pending_owner_payload
        ):
            raise RuntimeHandoffError(
                "HANDOFF_LEASE_INVALID",
                "The handoff lease or pending owner changed before commit.",
            )
        receipt_id = self._receipt_id_factory()
        if (
            not isinstance(receipt_id, str)
            or len(receipt_id) != 32
            or any(character not in "0123456789abcdef" for character in receipt_id)
        ):
            raise RuntimeHandoffError(
                "HANDOFF_RECEIPT_INVALID",
                "The handoff receipt id must be lowercase 32-hex.",
            )
        receipt_path = Path(handoff_receipt_output).resolve(strict=False)
        if receipt_path.exists() or not receipt_path.parent.is_dir():
            raise RuntimeHandoffError(
                "HANDOFF_RECEIPT_OUTPUT_INVALID",
                "The handoff receipt output must be an exclusive new file.",
            )
        snapshot = handoff.startup_snapshot
        unsigned = {
            "schemaVersion": 1,
            "receiptKind": "python-production-handoff",
            "receiptId": receipt_id,
            "runId": handoff.run_id,
            "authorizationPath": str(handoff.authorization_path),
            "authorizationFileSha256": handoff.authorization_file_sha256,
            "cutoverLeasePath": str(lease_path),
            "cutoverLeaseFileSha256": hashlib.sha256(lease_payload).hexdigest(),
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
            "ownerMarkerPath": str(handoff.owner_marker_path),
            "runtimeNamespace": "production",
            "roleLocks": dict(role_locks),
            "smokeEvidence": dict(smoke_evidence),
            "committedAt": _timestamp(self._clock()),
        }
        receipt_payload = _self_hashed(unsigned, "handoffReceiptSha256")
        receipt_file_sha = hashlib.sha256(receipt_payload).hexdigest()
        active_payload = _python_owner_payload(
            snapshot=snapshot,
            lease=lease,
            receipt_path=receipt_path,
            receipt_file_sha256=receipt_file_sha,
            clock=self._clock,
        )
        try:
            exclusive_write_bytes(receipt_path, receipt_payload)
        except DatabaseIdentityError as error:
            raise RuntimeHandoffError(error.code, str(error)) from error
        try:
            _cas_replace(
                handoff.owner_marker_path,
                handoff.pending_owner_payload,
                active_payload,
            )
            _update_lease(
                lease_path,
                expected_payload=lease_payload,
                changes={"phase": "completed"},
                clock=self._clock,
            )
        except Exception as error:
            self.rollback_to_frozen_node(handoff, reason="promotion_commit_failed")
            raise RuntimeHandoffError(
                "PYTHON_PROMOTION_COMMIT_FAILED",
                "Python owner commit failed and rollback was invoked.",
            ) from error
        stop_watchdog = getattr(self._operations, "stop_watchdog", None)
        if callable(stop_watchdog):
            stop_watchdog(lease_path)
        return _receipt_from_payload(receipt_path, receipt_payload)

    def rollback_to_frozen_node(
        self,
        handoff: HandoffLease,
        *,
        reason: str,
    ) -> tuple[str, ...]:
        del reason
        lease_path, lease_payload, lease = _load_lease(handoff.cutover_lease_path)
        current_owner = handoff.owner_marker_path.read_bytes()
        original_owner = base64.b64decode(
            str(lease["nodeActiveOwnerPayloadBase64"]), validate=True
        )
        events, _smoke = execute_rollback_tail(
            operations=self._operations,
            rollback_map=dict(lease["frozenNodeRollbackMap"]),
            initial_owner_state="handoff_pending",
            commit_node_active=lambda: _cas_replace(
                handoff.owner_marker_path,
                current_owner,
                original_owner,
            ),
        )
        _update_lease(
            lease_path,
            expected_payload=lease_payload,
            changes={"phase": "recovered"},
            clock=self._clock,
        )
        return events


def load_handoff_receipt(
    path: str | os.PathLike[str],
    *,
    expected_file_sha256: str | None = None,
) -> HandoffReceipt:
    receipt_path = Path(path).resolve(strict=True)
    try:
        payload = receipt_path.read_bytes()
        document = _strict_document(payload, _RECEIPT_FIELDS)
    except (OSError, ValueError) as error:
        raise RuntimeHandoffError(
            "HANDOFF_RECEIPT_INVALID",
            "The handoff receipt is invalid.",
        ) from error
    file_sha = hashlib.sha256(payload).hexdigest()
    unsigned = {
        key: value for key, value in document.items() if key != "handoffReceiptSha256"
    }
    if (
        expected_file_sha256 is not None
        and file_sha != expected_file_sha256
        or document.get("schemaVersion") != 1
        or document.get("receiptKind") != "python-production-handoff"
        or document.get("runtimeNamespace") != "production"
        or hashlib.sha256(canonical_json_bytes(unsigned)).hexdigest()
        != document.get("handoffReceiptSha256")
    ):
        raise RuntimeHandoffError(
            "HANDOFF_RECEIPT_INVALID",
            "The handoff receipt identity or self hash is invalid.",
        )
    return _receipt_from_payload(receipt_path, payload)


def _load_authorization(
    value: str | os.PathLike[str],
    *,
    expected_file_sha256: str,
    now: datetime,
) -> tuple[Path, bytes, dict[str, object]]:
    path = Path(value).resolve(strict=True)
    try:
        payload = path.read_bytes()
        document = _strict_authorization(payload)
        issued = _parse_time(document["issuedAt"])
        expires = _parse_time(document["expiresAt"])
    except (OSError, KeyError, TypeError, ValueError) as error:
        raise RuntimeHandoffError(
            "PROMOTION_AUTHORIZATION_INVALID",
            "The promotion authorization is invalid.",
        ) from error
    unsigned = {
        key: item for key, item in document.items() if key != "authorizationSha256"
    }
    if (
        hashlib.sha256(payload).hexdigest() != expected_file_sha256
        or document.get("schemaVersion") != 1
        or document.get("manifestKind") != "promotion-authorization"
        or hashlib.sha256(canonical_json_bytes(unsigned)).hexdigest()
        != document.get("authorizationSha256")
        or expires <= issued
        or expires - issued > _minutes(15)
        or now.tzinfo is None
        or now.astimezone(timezone.utc) < issued
        or now.astimezone(timezone.utc) >= expires
    ):
        raise RuntimeHandoffError(
            "PROMOTION_AUTHORIZATION_INVALID",
            "The promotion authorization hash, TTL, or validity window is invalid.",
        )
    return path, payload, document


def _strict_authorization(payload: bytes) -> dict[str, object]:
    document = _decode_json(payload)
    fields = set(document)
    if (
        not _AUTHORIZATION_REQUIRED_FIELDS.issubset(fields)
        or not fields.issubset(
            _AUTHORIZATION_REQUIRED_FIELDS | _AUTHORIZATION_OPTIONAL_FIELDS
        )
        or canonical_json_bytes(document) != payload
    ):
        raise ValueError("invalid authorization schema")
    return document


def _verify_optional_authorization_bindings(
    authorization: Mapping[str, object],
    *,
    snapshot: ProductionStartupSnapshot,
    lease: Mapping[str, object],
) -> None:
    expected = {
        "buildIdentityManifestPath": str(snapshot.build_identity_manifest_path),
        "buildIdentityManifestSha256": snapshot.build_identity_manifest_sha256,
        "buildId": snapshot.build_id,
        "databaseIdentityManifestPath": str(snapshot.database_identity_manifest_path),
        "databaseIdentityManifestSha256": snapshot.database_identity_manifest_sha256,
        "databaseLineageId": snapshot.database_lineage_id,
        "liveSubjectDatabaseId": snapshot.live_subject_database_id,
        "originReceiptPath": str(snapshot.origin_receipt_path),
        "originReceiptFileSha256": snapshot.origin_receipt_file_sha256,
        "runtimeNamespace": "production",
        "nodeOwnerMarkerPath": lease["ownerMarkerPath"],
    }
    for field, value in expected.items():
        if field in authorization and authorization[field] != value:
            raise RuntimeHandoffError(
                "PROMOTION_IDENTITY_MISMATCH",
                f"The promotion authorization {field} binding does not match.",
            )


def _handoff_owner_payload(
    *,
    snapshot: ProductionStartupSnapshot,
    lease: Mapping[str, object],
    authorization_path: Path,
    authorization_file_sha256: str,
    owner_state: str,
    clock: Callable[[], datetime],
) -> bytes:
    unsigned = {
        "schemaVersion": 2,
        "markerKind": "runtime-owner",
        "ownerState": owner_state,
        "runtimeNamespace": "production",
        "runId": snapshot.run_id,
        "authorizationPath": str(authorization_path),
        "authorizationFileSha256": authorization_file_sha256,
        "cutoverLeasePath": lease["cutoverLeasePath"],
        "startupSnapshotPath": str(snapshot.path),
        "startupSnapshotFileSha256": snapshot.file_sha256,
        "buildIdentityManifestPath": str(snapshot.build_identity_manifest_path),
        "buildIdentityManifestSha256": snapshot.build_identity_manifest_sha256,
        "databaseIdentityManifestPath": str(snapshot.database_identity_manifest_path),
        "databaseIdentityManifestSha256": snapshot.database_identity_manifest_sha256,
        "databaseLineageId": snapshot.database_lineage_id,
        "subjectDatabaseId": snapshot.live_subject_database_id,
        "updatedAt": _timestamp(clock()),
    }
    return _self_hashed(unsigned, "ownerMarkerSha256")


def _python_owner_payload(
    *,
    snapshot: ProductionStartupSnapshot,
    lease: Mapping[str, object],
    receipt_path: Path,
    receipt_file_sha256: str,
    clock: Callable[[], datetime],
) -> bytes:
    unsigned = {
        "schemaVersion": 2,
        "markerKind": "runtime-owner",
        "ownerState": "python_active",
        "runtimeNamespace": "production",
        "runId": snapshot.run_id,
        "cutoverLeasePath": lease["cutoverLeasePath"],
        "startupSnapshotPath": str(snapshot.path),
        "startupSnapshotFileSha256": snapshot.file_sha256,
        "buildIdentityManifestPath": str(snapshot.build_identity_manifest_path),
        "buildIdentityManifestSha256": snapshot.build_identity_manifest_sha256,
        "databaseIdentityManifestPath": str(snapshot.database_identity_manifest_path),
        "databaseIdentityManifestSha256": snapshot.database_identity_manifest_sha256,
        "databaseLineageId": snapshot.database_lineage_id,
        "subjectDatabaseId": snapshot.live_subject_database_id,
        "handoffReceiptPath": str(receipt_path),
        "handoffReceiptFileSha256": receipt_file_sha256,
        "updatedAt": _timestamp(clock()),
    }
    return _self_hashed(unsigned, "ownerMarkerSha256")


def _load_runtime_owner(payload: bytes) -> dict[str, object]:
    document = _decode_json(payload)
    unsigned = {key: value for key, value in document.items() if key != "ownerMarkerSha256"}
    if (
        document.get("schemaVersion") != 2
        or document.get("markerKind") != "runtime-owner"
        or hashlib.sha256(canonical_json_bytes(unsigned)).hexdigest()
        != document.get("ownerMarkerSha256")
        or canonical_json_bytes(document) != payload
    ):
        raise RuntimeHandoffError(
            "PROMOTION_OWNER_INVALID",
            "The runtime owner marker is invalid.",
        )
    return document


def _receipt_from_payload(path: Path, payload: bytes) -> HandoffReceipt:
    document = _strict_document(payload, _RECEIPT_FIELDS)
    return HandoffReceipt(
        path=path,
        file_sha256=hashlib.sha256(payload).hexdigest(),
        receipt_id=str(document["receiptId"]),
        run_id=str(document["runId"]),
        startup_snapshot_file_sha256=str(document["startupSnapshotFileSha256"]),
        database_lineage_id=str(document["databaseLineageId"]),
        live_subject_database_id=str(document["liveSubjectDatabaseId"]),
        canonical_bytes=payload,
    )


def _strict_document(payload: bytes, fields: frozenset[str]) -> dict[str, object]:
    document = _decode_json(payload)
    if set(document) != fields or canonical_json_bytes(document) != payload:
        raise ValueError("invalid canonical schema")
    return document


def _decode_json(payload: bytes) -> dict[str, object]:
    duplicates: list[str] = []

    def pairs(values: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in values:
            if key in result:
                duplicates.append(key)
            result[key] = value
        return result

    document = json.loads(payload.decode("utf-8"), object_pairs_hook=pairs)
    if duplicates or not isinstance(document, dict):
        raise ValueError("invalid JSON object")
    return document


def _parse_time(value: object) -> datetime:
    if not isinstance(value, str):
        raise ValueError("invalid timestamp")
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None or result.utcoffset() is None:
        raise ValueError("invalid timestamp")
    return result.astimezone(timezone.utc)


def _minutes(value: int):
    from datetime import timedelta

    return timedelta(minutes=value)


def _is_sha(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


__all__ = [
    "HandoffLease",
    "HandoffReceipt",
    "ProductionPromotionCoordinator",
    "RuntimeHandoffError",
    "load_handoff_receipt",
]
