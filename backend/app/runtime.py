from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
import asyncio
import hashlib
import inspect
import ipaddress
import json
from pathlib import Path
import re
import socket
from typing import Any

from backend.app.config import DatabaseSettings


_PROCESS_ROLES = frozenset({"api", "worker", "scheduler"})
_PRODUCTION_ROLES = _PROCESS_ROLES | {"mcp"}
_HOST_NAME = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?\Z")


class RuntimeRoleError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(f"{code}: {message}")


def parse_process_role(environment: dict[str, str]) -> str:
    """Resolve exactly one process role before any socket or database work."""

    raw = environment.get("API_PROCESS_ROLE")
    declared = environment.get("API_PROCESS_ROLES")
    if not isinstance(raw, str) or raw not in _PROCESS_ROLES:
        raise RuntimeRoleError(
            "PROCESS_ROLE_INVALID",
            "API_PROCESS_ROLE must name exactly one of api, worker, or scheduler.",
        )
    if declared is not None:
        values = tuple(value.strip() for value in declared.split(",") if value.strip())
        if len(values) != 1 or values[0] != raw:
            raise RuntimeRoleError(
                "PROCESS_ROLE_MULTIPLE",
                "A process may not declare multiple runtime roles.",
            )
    return raw


class CandidateRuntimeGuard:
    """Authorize candidate roles from a verified typed identity only."""

    def validate_role(
        self,
        database_identity_manifest: Any,
        *,
        database: DatabaseSettings | None = None,
        environment: str,
        runtime_namespace: str,
        role: str,
        parent_backup: Any = None,
        parent_manifest: Any = None,
    ) -> Any:
        from backend.app.api.compat.database_identity import (
            DatabaseIdentityError,
            VerifiedContainerDatabaseEvidenceIdentity,
            load_database_evidence_identity_manifest,
            verify_database_evidence_identity_subject,
            verify_descendant_database_evidence_identity,
        )

        if role not in _PROCESS_ROLES:
            raise RuntimeRoleError(
                "PROCESS_ROLE_INVALID",
                "The candidate process role is invalid.",
            )
        if not isinstance(runtime_namespace, str) or not runtime_namespace.strip():
            raise RuntimeRoleError(
                "RUNTIME_NAMESPACE_INVALID",
                "A candidate runtime namespace is required.",
            )
        try:
            container_verified = isinstance(
                database_identity_manifest,
                VerifiedContainerDatabaseEvidenceIdentity,
            )
            if container_verified:
                identity = database_identity_manifest.manifest
            else:
                identity = (
                    load_database_evidence_identity_manifest(database_identity_manifest)
                    if not hasattr(database_identity_manifest, "subject_kind")
                    else database_identity_manifest
                )
        except DatabaseIdentityError as error:
            raise RuntimeRoleError(error.code, str(error)) from error
        if environment == "live" or getattr(identity, "subject_kind", None) == "live":
            raise RuntimeRoleError(
                "P4_LIVE_PROMOTION_NOT_AUTHORIZED",
                "P4 candidate roles cannot authorize a Live subject or Live Python role.",
            )
        if environment != "candidate":
            raise RuntimeRoleError(
                "RUNTIME_ENVIRONMENT_INVALID",
                "P4 roles must run in the candidate environment.",
            )
        if not isinstance(database, DatabaseSettings):
            raise RuntimeRoleError(
                "DATABASE_IDENTITY_SUBJECT_MISMATCH",
                "Candidate roles require the exact database subject.",
            )
        try:
            if container_verified:
                verify_database_evidence_identity_subject(
                    database=database.database_path,
                    identity=identity,
                )
            else:
                verify_descendant_database_evidence_identity(
                    database=database.database_path,
                    identity=identity,
                    parent_backup=parent_backup,
                    parent_manifest=parent_manifest,
                )
        except DatabaseIdentityError as error:
            raise RuntimeRoleError(error.code, str(error)) from error
        if not str(getattr(identity, "database_lineage_id", "")) or not str(
            getattr(identity, "subject_database_id", "")
        ):
            raise RuntimeRoleError(
                "DATABASE_IDENTITY_INVALID",
                "Candidate roles require a typed lineage and subject identity.",
            )
        return identity

    def bind_loopback_socket(
        self,
        *,
        host: str = "127.0.0.1",
        port: int = 0,
    ) -> socket.socket:
        try:
            address = ipaddress.ip_address(host)
        except ValueError as error:
            raise RuntimeRoleError(
                "CANDIDATE_BIND_INVALID",
                "Candidate sockets require a literal loopback address.",
            ) from error
        if not address.is_loopback or host in {"0.0.0.0", "::"}:
            raise RuntimeRoleError(
                "CANDIDATE_BIND_INVALID",
                "Candidate sockets may bind only to loopback.",
            )
        family = socket.AF_INET6 if address.version == 6 else socket.AF_INET
        listener = socket.socket(family, socket.SOCK_STREAM)
        try:
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            listener.bind((host, port))
            listener.listen(16)
            return listener
        except Exception:
            listener.close()
            raise


@dataclass(frozen=True, slots=True)
class ProductionRuntimeAdmission:
    """Typed capability produced only after the final-window handoff begins."""

    run_id: str
    role: str
    runtime_namespace: str
    database_identity_manifest_path: Path
    database_lineage_id: str
    subject_database_id: str
    cutover_lease_path: Path
    cutover_lease_payload: bytes
    owner_marker_path: Path
    pending_owner_payload: bytes
    admission_mode: str = "handoff_pending"


class ProductionRuntimeGuard:
    """Revalidate a consumed promotion handoff before runtime side effects."""

    def __init__(self, *, clock: Callable[[], datetime] | None = None) -> None:
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def validate_active_owner(
        self,
        *,
        handoff_receipt: str | Path,
        expected_handoff_receipt_sha256: str,
        owner_marker: str | Path,
        database: DatabaseSettings,
        environment: str,
        runtime_namespace: str,
        role: str,
    ) -> ProductionRuntimeAdmission:
        from backend.app.api.compat.build_identity import load_build_identity_manifest
        from backend.app.api.compat.database_identity import (
            load_database_evidence_identity_manifest,
            verify_database_evidence_identity_subject,
        )
        from backend.app.api.compat.evidence_capture import load_evidence_run_manifest
        from backend.app.application.final_window import load_production_startup_snapshot
        from backend.app.application.runtime_handoff import (
            _load_runtime_owner,
            load_handoff_receipt,
        )
        from backend.app.infrastructure.database_backup import verify_origin_receipt

        if (
            not isinstance(database, DatabaseSettings)
            or environment != "live"
            or runtime_namespace != "production"
            or role not in _PRODUCTION_ROLES
        ):
            raise RuntimeRoleError(
                "PRODUCTION_ADMISSION_INVALID",
                "Active production admission requires one Live Python process role.",
            )
        try:
            receipt = load_handoff_receipt(
                handoff_receipt,
                expected_file_sha256=expected_handoff_receipt_sha256,
            )
            receipt_document = json.loads(receipt.canonical_bytes.decode("utf-8"))
            owner_path = Path(owner_marker).resolve(strict=True)
            owner_payload = owner_path.read_bytes()
            owner = _load_runtime_owner(owner_payload)
            snapshot = load_production_startup_snapshot(
                str(receipt_document["startupSnapshotPath"]),
                expected_file_sha256=str(
                    receipt_document["startupSnapshotFileSha256"]
                ),
                require_frozen_node_executable=False,
            )
            build = load_build_identity_manifest(snapshot.build_identity_manifest_path)
            identity = load_database_evidence_identity_manifest(
                snapshot.database_identity_manifest_path
            )
            run = load_evidence_run_manifest(
                snapshot.run_manifest_path,
                expected_file_sha256=snapshot.run_manifest_file_sha256,
            )
            origin = verify_origin_receipt(
                snapshot.origin_receipt_path,
                snapshot.origin_receipt_file_sha256,
            )
            verify_database_evidence_identity_subject(
                database=database.database_path,
                identity=identity,
            )
            lease_input = Path(str(receipt_document["cutoverLeasePath"])).expanduser()
            if not lease_input.is_absolute():
                raise ValueError("the completed cutover lease path is not absolute")
            lease_path = lease_input.resolve(strict=False)
            if lease_path != lease_input:
                raise ValueError("the completed cutover lease path is not canonical")
            lease_file_sha256 = str(receipt_document["cutoverLeaseFileSha256"])
            if re.fullmatch(r"[0-9a-f]{64}", lease_file_sha256) is None:
                raise ValueError("the completed cutover lease hash is invalid")
            lease_path, lease_payload, lease = _load_optional_completed_lease(
                lease_path,
                expected_file_sha256=lease_file_sha256,
            )
        except Exception as error:
            raise RuntimeRoleError(
                "PRODUCTION_ADMISSION_INVALID",
                "The active production identity chain is invalid.",
            ) from error
        if (
            owner.get("ownerState") != "python_active"
            or owner.get("runtimeNamespace") != "production"
            or owner.get("handoffReceiptPath") != str(receipt.path)
            or owner.get("handoffReceiptFileSha256") != receipt.file_sha256
            or owner.get("startupSnapshotPath") != str(snapshot.path)
            or owner.get("startupSnapshotFileSha256") != snapshot.file_sha256
            or owner.get("buildIdentityManifestPath") != str(build.manifest_path)
            or owner.get("buildIdentityManifestSha256") != build.manifest_file_sha256
            or owner.get("databaseIdentityManifestPath") != str(identity.manifest_path)
            or owner.get("databaseIdentityManifestSha256")
            != identity.identity_manifest_file_sha256
            or owner.get("databaseLineageId") != identity.database_lineage_id
            or owner.get("subjectDatabaseId") != identity.subject_database_id
            or receipt.run_id != snapshot.run_id
            or receipt.database_lineage_id != identity.database_lineage_id
            or receipt.live_subject_database_id != identity.subject_database_id
            or receipt_document.get("buildId") != build.build_id
            or receipt_document.get("buildIdentityManifestPath")
            != str(build.manifest_path)
            or receipt_document.get("buildIdentityManifestSha256")
            != build.manifest_file_sha256
            or receipt_document.get("databaseIdentityManifestPath")
            != str(identity.manifest_path)
            or receipt_document.get("databaseIdentityManifestSha256")
            != identity.identity_manifest_file_sha256
            or receipt_document.get("originReceiptPath") != str(origin.receipt_path)
            or receipt_document.get("originReceiptFileSha256")
            != origin.origin_receipt_file_sha256
            or run.phase != "final"
            or run.run_id != snapshot.run_id
            or owner.get("cutoverLeasePath") != str(lease_path)
            or receipt_document.get("cutoverLeasePath") != str(lease_path)
            or (
                lease is not None
                and (
                    lease.get("phase") != "completed"
                    or lease.get("runId") != snapshot.run_id
                )
            )
            or Path(str(snapshot.rollback_map["databasePath"])).resolve(strict=True)
            != database.database_path
        ):
            raise RuntimeRoleError(
                "PRODUCTION_ADMISSION_INVALID",
                "The active Python owner, receipt, lease, or database identity mismatched.",
            )
        return ProductionRuntimeAdmission(
            run_id=snapshot.run_id,
            role=role,
            runtime_namespace=runtime_namespace,
            database_identity_manifest_path=identity.manifest_path,
            database_lineage_id=identity.database_lineage_id,
            subject_database_id=identity.subject_database_id,
            cutover_lease_path=lease_path,
            cutover_lease_payload=lease_payload,
            owner_marker_path=owner_path,
            pending_owner_payload=owner_payload,
            admission_mode="python_active",
        )

    def validate_pending_handoff(
        self,
        *,
        authorization: str | Path,
        expected_authorization_sha256: str,
        final_evidence_run_manifest: str | Path,
        expected_final_evidence_run_manifest_sha256: str,
        cutover_lease: str | Path,
        startup_snapshot: str | Path,
        expected_startup_snapshot_sha256: str,
        build_identity_manifest: str | Path,
        expected_build_identity_manifest_sha256: str,
        database_identity_manifest: str | Path,
        expected_database_identity_manifest_sha256: str,
        owner_marker: str | Path,
        database: DatabaseSettings,
        environment: str,
        runtime_namespace: str,
        role: str,
    ) -> ProductionRuntimeAdmission:
        from backend.app.api.compat.build_identity import load_build_identity_manifest
        from backend.app.api.compat.database_identity import (
            load_database_evidence_identity_manifest,
        )
        from backend.app.api.compat.evidence_capture import load_evidence_run_manifest
        from backend.app.application.final_window import (
            _load_lease,
            load_production_startup_snapshot,
        )
        from backend.app.application.runtime_handoff import (
            HandoffLease,
            _load_authorization,
        )

        try:
            authorization_path, authorization_payload, authorization_document = (
                _load_authorization(
                    authorization,
                    expected_file_sha256=expected_authorization_sha256,
                    now=self._clock(),
                )
            )
            snapshot = load_production_startup_snapshot(
                startup_snapshot,
                expected_file_sha256=expected_startup_snapshot_sha256,
            )
            run = load_evidence_run_manifest(
                final_evidence_run_manifest,
                expected_file_sha256=expected_final_evidence_run_manifest_sha256,
            )
            build = load_build_identity_manifest(build_identity_manifest)
            identity = load_database_evidence_identity_manifest(
                database_identity_manifest
            )
            lease_path, lease_payload, lease = _load_lease(cutover_lease)
            owner_path = Path(owner_marker).resolve(strict=True)
            owner_payload = owner_path.read_bytes()
        except Exception as error:
            raise RuntimeRoleError(
                "PRODUCTION_ADMISSION_INVALID",
                "The pending production handoff artifacts are invalid.",
            ) from error
        if (
            run.phase != "final"
            or run.manifest_path != snapshot.run_manifest_path
            or run.manifest_file_sha256 != snapshot.run_manifest_file_sha256
            or build.manifest_path != snapshot.build_identity_manifest_path
            or build.manifest_file_sha256
            != expected_build_identity_manifest_sha256
            or build.manifest_file_sha256 != snapshot.build_identity_manifest_sha256
            or identity.manifest_path != snapshot.database_identity_manifest_path
            or identity.identity_manifest_file_sha256
            != expected_database_identity_manifest_sha256
            or identity.identity_manifest_file_sha256
            != snapshot.database_identity_manifest_sha256
            or authorization_document.get("runId") != snapshot.run_id
            or Path(
                str(authorization_document.get("finalEvidenceRunManifestPath"))
            ).resolve(strict=True)
            != run.manifest_path
            or authorization_document.get("finalEvidenceRunManifestSha256")
            != run.manifest_file_sha256
            or Path(str(authorization_document.get("startupSnapshotPath"))).resolve(
                strict=True
            )
            != snapshot.path
            or authorization_document.get("startupSnapshotSha256")
            != snapshot.file_sha256
            or Path(str(authorization_document.get("cutoverLeasePath"))).resolve(
                strict=True
            )
            != lease_path
            or authorization_document.get("cutoverLeaseSha256")
            != lease.get("previousLeaseFileSha256")
            or lease.get("phase") != "handoff_pending"
            or lease.get("runId") != snapshot.run_id
            or Path(str(lease.get("ownerMarkerPath"))).resolve(strict=True)
            != owner_path
        ):
            raise RuntimeRoleError(
                "PRODUCTION_ADMISSION_INVALID",
                "The pending production handoff artifacts do not share one identity.",
            )
        handoff = HandoffLease(
            authorization_path=authorization_path,
            authorization_file_sha256=hashlib.sha256(
                authorization_payload
            ).hexdigest(),
            cutover_lease_path=lease_path,
            cutover_lease_payload=lease_payload,
            startup_snapshot=snapshot,
            owner_marker_path=owner_path,
            pending_owner_payload=owner_payload,
            run_id=snapshot.run_id,
        )
        return self.validate_handoff(
            handoff,
            database=database,
            environment=environment,
            runtime_namespace=runtime_namespace,
            role=role,
        )

    def validate_handoff(
        self,
        handoff: object,
        *,
        database: DatabaseSettings,
        environment: str,
        runtime_namespace: str,
        role: str,
    ) -> ProductionRuntimeAdmission:
        from backend.app.api.compat.build_identity import load_build_identity_manifest
        from backend.app.api.compat.database_identity import (
            load_database_evidence_identity_manifest,
            verify_database_evidence_identity_subject,
        )
        from backend.app.api.compat.evidence_capture import load_evidence_run_manifest
        from backend.app.application.final_window import (
            _load_lease,
            load_production_startup_snapshot,
        )
        from backend.app.application.runtime_handoff import (
            HandoffLease,
            _load_authorization,
            _verify_optional_authorization_bindings,
        )
        from backend.app.infrastructure.database_backup import verify_origin_receipt

        if (
            not isinstance(handoff, HandoffLease)
            or not isinstance(database, DatabaseSettings)
            or environment != "live"
            or runtime_namespace != "production"
            or role not in _PRODUCTION_ROLES
        ):
            raise RuntimeRoleError(
                "PRODUCTION_ADMISSION_INVALID",
                "Live runtime admission requires one typed production handoff.",
            )
        try:
            snapshot = load_production_startup_snapshot(
                handoff.startup_snapshot.path,
                expected_file_sha256=handoff.startup_snapshot.file_sha256,
            )
            run = load_evidence_run_manifest(
                snapshot.run_manifest_path,
                expected_file_sha256=snapshot.run_manifest_file_sha256,
            )
            build = load_build_identity_manifest(snapshot.build_identity_manifest_path)
            identity = load_database_evidence_identity_manifest(
                snapshot.database_identity_manifest_path
            )
            origin = verify_origin_receipt(
                snapshot.origin_receipt_path,
                snapshot.origin_receipt_file_sha256,
            )
            verify_database_evidence_identity_subject(
                database=database.database_path,
                identity=identity,
            )
            authorization_path, authorization_payload, authorization = (
                _load_authorization(
                    handoff.authorization_path,
                    expected_file_sha256=handoff.authorization_file_sha256,
                    now=self._clock(),
                )
            )
            lease_path, lease_payload, lease = _load_lease(
                handoff.cutover_lease_path
            )
            _verify_optional_authorization_bindings(
                authorization,
                snapshot=snapshot,
                lease=lease,
            )
            consumed_path = authorization_path.with_name(
                f"{authorization_path.stem}.consumed.json"
            )
            _verify_consumed_authorization(
                consumed_path,
                run_id=snapshot.run_id,
                authorization_path=authorization_path,
                authorization_file_sha256=hashlib.sha256(
                    authorization_payload
                ).hexdigest(),
            )
        except RuntimeRoleError:
            raise
        except Exception as error:
            raise RuntimeRoleError(
                "PRODUCTION_ADMISSION_INVALID",
                "The production handoff identity could not be revalidated.",
            ) from error
        if (
            run.phase != "final"
            or run.run_id != snapshot.run_id
            or run.build_identity_manifest_path != build.manifest_path
            or run.build_identity_manifest_sha256 != build.manifest_file_sha256
            or run.database_identity_manifest_path != identity.manifest_path
            or run.database_identity_manifest_sha256
            != identity.identity_manifest_file_sha256
            or identity.subject_kind != "live"
            or identity.database_lineage_id != snapshot.database_lineage_id
            or identity.subject_database_id != snapshot.live_subject_database_id
            or identity.origin_receipt_path != snapshot.origin_receipt_path
            or identity.origin_receipt_file_sha256
            != snapshot.origin_receipt_file_sha256
            or origin.receipt_path != snapshot.origin_receipt_path
            or origin.origin_receipt_file_sha256
            != snapshot.origin_receipt_file_sha256
            or authorization.get("runId") != snapshot.run_id
            or Path(
                str(authorization.get("finalEvidenceRunManifestPath"))
            ).resolve(strict=True)
            != run.manifest_path
            or authorization.get("finalEvidenceRunManifestSha256")
            != run.manifest_file_sha256
            or Path(str(authorization.get("startupSnapshotPath"))).resolve(
                strict=True
            )
            != snapshot.path
            or authorization.get("startupSnapshotSha256") != snapshot.file_sha256
            or Path(str(authorization.get("cutoverLeasePath"))).resolve(strict=True)
            != lease_path
            or authorization.get("cutoverLeaseSha256")
            != lease.get("previousLeaseFileSha256")
            or Path(str(snapshot.rollback_map["databasePath"])).resolve(strict=True)
            != database.database_path
            or handoff.run_id != snapshot.run_id
            or lease_payload != handoff.cutover_lease_payload
            or lease_path != handoff.cutover_lease_path
            or lease.get("phase") != "handoff_pending"
            or Path(str(lease.get("ownerMarkerPath"))).resolve(strict=True)
            != handoff.owner_marker_path
            or handoff.owner_marker_path.read_bytes() != handoff.pending_owner_payload
        ):
            raise RuntimeRoleError(
                "PRODUCTION_ADMISSION_INVALID",
                "The production handoff no longer identifies the pending Live runtime.",
            )
        return ProductionRuntimeAdmission(
            run_id=snapshot.run_id,
            role=role,
            runtime_namespace=runtime_namespace,
            database_identity_manifest_path=identity.manifest_path,
            database_lineage_id=identity.database_lineage_id,
            subject_database_id=identity.subject_database_id,
            cutover_lease_path=lease_path,
            cutover_lease_payload=lease_payload,
            owner_marker_path=handoff.owner_marker_path,
            pending_owner_payload=handoff.pending_owner_payload,
        )


def validate_production_runtime_admission(
    admission: object,
    identity: object,
    *,
    runtime_namespace: str,
    role: str,
) -> None:
    from backend.app.application.final_window import _load_lease

    if not isinstance(admission, ProductionRuntimeAdmission):
        raise RuntimeRoleError(
            "PRODUCTION_ADMISSION_REQUIRED",
            "A typed production runtime admission is required.",
        )
    receipt_binding_is_valid = False
    lease_expected_sha256 = ""
    try:
        owner_payload = admission.owner_marker_path.read_bytes()
        lease_path = admission.cutover_lease_path.resolve(strict=False)
        lease_payload = b""
        lease: dict[str, object] | None = None
        if admission.admission_mode == "python_active":
            from backend.app.application.runtime_handoff import (
                _load_runtime_owner,
                load_handoff_receipt,
            )

            owner = _load_runtime_owner(owner_payload)
            receipt = load_handoff_receipt(
                str(owner["handoffReceiptPath"]),
                expected_file_sha256=str(owner["handoffReceiptFileSha256"]),
            )
            receipt_document = json.loads(receipt.canonical_bytes.decode("utf-8"))
            lease_expected_sha256 = str(
                receipt_document["cutoverLeaseFileSha256"]
            )
            receipt_binding_is_valid = (
                owner.get("ownerState") == "python_active"
                and owner.get("runId") == admission.run_id
                and owner.get("cutoverLeasePath") == str(lease_path)
                and owner.get("handoffReceiptPath") == str(receipt.path)
                and owner.get("handoffReceiptFileSha256") == receipt.file_sha256
                and receipt.run_id == admission.run_id
                and receipt_document.get("cutoverLeasePath") == str(lease_path)
                and re.fullmatch(r"[0-9a-f]{64}", lease_expected_sha256)
                is not None
            )
            lease_path, lease_payload, lease = _load_optional_completed_lease(
                lease_path,
                expected_file_sha256=lease_expected_sha256,
            )
        else:
            lease_path, lease_payload, lease = _load_lease(lease_path)
    except Exception as error:
        raise RuntimeRoleError(
            "PRODUCTION_ADMISSION_INVALID",
            "The production runtime admission could not be revalidated.",
        ) from error
    if admission.admission_mode == "python_active":
        lease_is_valid = (
            receipt_binding_is_valid
            and lease_path == admission.cutover_lease_path
            and (
                lease is None
                or (
                    hashlib.sha256(lease_payload).hexdigest()
                    == lease_expected_sha256
                    and lease.get("phase") == "completed"
                    and lease.get("runId") == admission.run_id
                )
            )
        )
    elif admission.admission_mode == "handoff_pending":
        lease_is_valid = (
            lease is not None
            and lease_path == admission.cutover_lease_path
            and lease_payload == admission.cutover_lease_payload
            and lease.get("phase") == "handoff_pending"
            and lease.get("runId") == admission.run_id
        )
    else:
        lease_is_valid = False
    if (
        admission.admission_mode not in {"handoff_pending", "python_active"}
        or admission.role != role
        or admission.runtime_namespace != runtime_namespace
        or admission.database_identity_manifest_path
        != getattr(identity, "manifest_path", None)
        or admission.database_lineage_id
        != getattr(identity, "database_lineage_id", None)
        or admission.subject_database_id != getattr(identity, "subject_database_id", None)
        or getattr(identity, "subject_kind", None) != "live"
        or not lease_is_valid
        or owner_payload != admission.pending_owner_payload
    ):
        raise RuntimeRoleError(
            "PRODUCTION_ADMISSION_INVALID",
            "The production runtime admission identity or handoff state changed.",
        )


def _load_optional_completed_lease(
    path: Path,
    *,
    expected_file_sha256: str,
) -> tuple[Path, bytes, dict[str, object] | None]:
    from backend.app.application.final_window import _load_lease

    # Older completed cutover leases lived under %TEMP%. The exact, self-hashed
    # receipt and owner remain the durable restart proof after that transient
    # file is cleaned up. Any surviving path must still be the exact lease.
    if not path.exists():
        return path, b"", None
    if not path.is_file():
        raise ValueError("the completed cutover lease path is not a file")
    lease_path, lease_payload, lease = _load_lease(
        path,
        require_frozen_node_executable=False,
    )
    if hashlib.sha256(lease_payload).hexdigest() != expected_file_sha256:
        raise ValueError("the completed cutover lease hash drifted")
    return lease_path, lease_payload, lease


def _verify_consumed_authorization(
    path: Path,
    *,
    run_id: str,
    authorization_path: Path,
    authorization_file_sha256: str,
) -> None:
    from backend.app.api.compat.database_identity import canonical_json_bytes
    import json

    payload = path.resolve(strict=True).read_bytes()
    document = json.loads(payload.decode("utf-8"))
    if not isinstance(document, dict) or set(document) != {
        "schemaVersion",
        "markerKind",
        "runId",
        "authorizationPath",
        "authorizationFileSha256",
        "consumedAt",
        "consumptionSha256",
    }:
        raise RuntimeRoleError(
            "PRODUCTION_ADMISSION_INVALID",
            "The promotion authorization consumption marker is invalid.",
        )
    unsigned = {
        key: value for key, value in document.items() if key != "consumptionSha256"
    }
    if (
        canonical_json_bytes(document) != payload
        or document.get("schemaVersion") != 1
        or document.get("markerKind") != "promotion-authorization-consumed"
        or document.get("runId") != run_id
        or Path(str(document.get("authorizationPath"))).resolve(strict=True)
        != authorization_path
        or document.get("authorizationFileSha256") != authorization_file_sha256
        or document.get("consumptionSha256")
        != hashlib.sha256(canonical_json_bytes(unsigned)).hexdigest()
    ):
        raise RuntimeRoleError(
            "PRODUCTION_ADMISSION_INVALID",
            "The promotion authorization consumption marker does not match.",
        )


@dataclass(frozen=True, slots=True)
class CandidateDrainReport:
    status: str
    provider_cancelled: bool


class CandidateDrainCoordinator:
    """Quiesce one candidate namespace without touching the Live owner."""

    def __init__(
        self,
        *,
        api: Any,
        worker: Any,
        scheduler: Any,
        provider_scope: Any,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self._api = api
        self._worker = worker
        self._scheduler = scheduler
        self._provider_scope = provider_scope
        self._clock = clock or __import__("time").monotonic

    async def drain(self, deadline: float) -> CandidateDrainReport:
        try:
            self._api.begin_draining()
            await self._wait_until(self._api.wait_for_in_flight, deadline)
            self._api.finish_draining()
            await self._wait_until(self._api.wait_stopped, deadline)
            self._worker.stop_claims()
            await self._wait_until(self._worker.wait_for_in_flight, deadline)
            self._worker.stop()
            await self._wait_until(self._worker.wait_stopped, deadline)
            self._worker.release_lease()
            self._scheduler.stop_ticks()
            await self._wait_until(self._scheduler.settle_started_ticks, deadline)
            self._scheduler.stop()
            await self._wait_until(self._scheduler.wait_stopped, deadline)
            self._scheduler.release_lease()
            return CandidateDrainReport(status="drained", provider_cancelled=False)
        except (asyncio.TimeoutError, TimeoutError):
            self._provider_scope.cancel()
            return CandidateDrainReport(status="timed_out", provider_cancelled=True)

    async def _wait_until(self, operation: Callable[[float], Any], deadline: float) -> None:
        remaining = deadline - self._clock()
        if remaining <= 0:
            raise asyncio.TimeoutError
        result = operation(deadline)
        if inspect.isawaitable(result):
            await asyncio.wait_for(result, timeout=remaining)


@dataclass(frozen=True, slots=True)
class ApiSettings:
    bind_host: str = "127.0.0.1"
    bind_port: int = 8000
    allow_remote_access: bool = False
    loopback_port_forwarding: bool = False
    loopback_forwarder_hosts: tuple[str, ...] = ()
    additional_hosts: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _validate_bind_host(self.bind_host)
        if (
            not isinstance(self.bind_port, int)
            or isinstance(self.bind_port, bool)
            or not 1 <= self.bind_port <= 65535
        ):
            raise ValueError("API bind port is invalid")
        _validate_access_mode(
            self.bind_host,
            allow_remote_access=self.allow_remote_access,
            loopback_port_forwarding=self.loopback_port_forwarding,
        )
        _validate_forwarder_hosts(
            self.loopback_forwarder_hosts,
            enabled=self.loopback_port_forwarding,
        )

    @classmethod
    def for_tests(cls) -> ApiSettings:
        return cls(bind_port=80, additional_hosts=("testserver",))


@dataclass(frozen=True, slots=True)
class ProcessRuntimeSettings:
    database: DatabaseSettings
    process_role: str
    bind_host: str = "127.0.0.1"
    bind_port: int = 8000
    allow_remote_access: bool = False
    loopback_port_forwarding: bool = False
    loopback_forwarder_hosts: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.process_role not in _PROCESS_ROLES:
            raise ValueError("API_PROCESS_ROLE must be api, worker, or scheduler")
        _validate_bind_host(self.bind_host)
        if (
            not isinstance(self.bind_port, int)
            or isinstance(self.bind_port, bool)
            or not 1 <= self.bind_port <= 65535
        ):
            raise ValueError("API bind port is invalid")
        _validate_access_mode(
            self.bind_host,
            allow_remote_access=self.allow_remote_access,
            loopback_port_forwarding=self.loopback_port_forwarding,
        )
        _validate_forwarder_hosts(
            self.loopback_forwarder_hosts,
            enabled=self.loopback_port_forwarding,
        )

    @classmethod
    def from_environment(
        cls,
        database: DatabaseSettings,
        environment: dict[str, str],
    ) -> ProcessRuntimeSettings:
        allow_value = environment.get("ALLOW_REMOTE_ACCESS", "0")
        if allow_value not in {"0", "1"}:
            raise ValueError("ALLOW_REMOTE_ACCESS must be 0 or 1")
        forwarding_value = environment.get("API_LOOPBACK_PORT_FORWARDING", "0")
        if forwarding_value not in {"0", "1"}:
            raise ValueError("API_LOOPBACK_PORT_FORWARDING must be 0 or 1")
        port_value = environment.get("API_BIND_PORT", "8000")
        try:
            bind_port = int(port_value)
        except (TypeError, ValueError):
            raise ValueError("API_BIND_PORT must be an integer") from None
        forwarder_hosts = _parse_forwarder_hosts(
            environment.get("API_LOOPBACK_FORWARDER_HOSTS", ""),
            enabled=forwarding_value == "1",
        )
        return cls(
            database=database,
            process_role=environment.get("API_PROCESS_ROLE", ""),
            bind_host=environment.get("API_BIND_HOST", "127.0.0.1"),
            bind_port=bind_port,
            allow_remote_access=allow_value == "1",
            loopback_port_forwarding=forwarding_value == "1",
            loopback_forwarder_hosts=forwarder_hosts,
        )

    def api_settings(self) -> ApiSettings:
        return ApiSettings(
            bind_host=self.bind_host,
            bind_port=self.bind_port,
            allow_remote_access=self.allow_remote_access,
            loopback_port_forwarding=self.loopback_port_forwarding,
            loopback_forwarder_hosts=self.loopback_forwarder_hosts,
        )


@dataclass(frozen=True, slots=True)
class RolePorts:
    api: Callable[[], Any]
    worker: Callable[[], Any]
    scheduler: Callable[[], Any]


def bootstrap_process_role(
    settings: ProcessRuntimeSettings,
    ports: RolePorts,
    *,
    required_schema_revision: str,
) -> str:
    """Cross the shared schema gate before dispatching one process role."""

    from backend.app.bootstrap import verify_schema_revision

    verify_schema_revision(settings.database, required_schema_revision)
    port = getattr(ports, settings.process_role)
    port()
    return settings.process_role


def _validate_bind_host(value: str) -> None:
    if not isinstance(value, str) or value != value.strip() or not value:
        raise ValueError("API_BIND_HOST is invalid")
    if any(ord(character) <= 32 or ord(character) == 127 for character in value):
        raise ValueError("API_BIND_HOST is invalid")
    if value.startswith("[") or value.endswith("]"):
        raise ValueError("API_BIND_HOST must use an unbracketed socket host")
    try:
        ipaddress.ip_address(value)
    except ValueError:
        if _HOST_NAME.fullmatch(value) is None:
            raise ValueError("API_BIND_HOST is invalid") from None


def _is_loopback_host(value: str) -> bool:
    if value.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(value).is_loopback
    except ValueError:
        return False


def _validate_access_mode(
    bind_host: str,
    *,
    allow_remote_access: bool,
    loopback_port_forwarding: bool,
) -> None:
    if loopback_port_forwarding:
        try:
            is_unspecified = ipaddress.ip_address(bind_host).is_unspecified
        except ValueError:
            is_unspecified = False
        if not is_unspecified or allow_remote_access:
            raise ValueError(
                "loopback port forwarding requires a wildcard listener and remote access disabled"
            )
        return
    if not _is_loopback_host(bind_host) and not allow_remote_access:
        raise ValueError(
            "non-loopback API_BIND_HOST requires ALLOW_REMOTE_ACCESS=1"
        )


def _parse_forwarder_hosts(value: str, *, enabled: bool) -> tuple[str, ...]:
    if not enabled:
        if value:
            raise ValueError(
                "API_LOOPBACK_FORWARDER_HOSTS requires loopback port forwarding"
            )
        return ()
    if not isinstance(value, str) or not value.strip():
        raise ValueError("API_LOOPBACK_FORWARDER_HOSTS is required")
    hosts: list[str] = []
    for raw in value.split(","):
        token = raw.strip()
        if token == "default-gateway":
            token = _default_route_gateway()
        try:
            address = ipaddress.ip_address(token)
        except ValueError:
            raise ValueError("API_LOOPBACK_FORWARDER_HOSTS is invalid") from None
        if address.is_unspecified or address.is_multicast:
            raise ValueError("API_LOOPBACK_FORWARDER_HOSTS is invalid")
        canonical = str(address)
        if canonical not in hosts:
            hosts.append(canonical)
    return tuple(hosts)


def _validate_forwarder_hosts(value: tuple[str, ...], *, enabled: bool) -> None:
    if enabled and not value:
        raise ValueError("loopback forwarding requires a trusted connection source")
    if not enabled and value:
        raise ValueError("forwarder hosts require loopback forwarding")
    for host in value:
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            raise ValueError("loopback forwarder host is invalid") from None
        if str(address) != host or address.is_unspecified or address.is_multicast:
            raise ValueError("loopback forwarder host is invalid")


def _default_route_gateway() -> str:
    route_path = Path("/proc/net/route")
    try:
        rows = route_path.read_text(encoding="ascii").splitlines()[1:]
    except (OSError, UnicodeError):
        raise ValueError("the default forwarding gateway is unavailable") from None
    for row in rows:
        fields = row.split()
        if len(fields) < 4 or fields[1] != "00000000":
            continue
        try:
            flags = int(fields[3], 16)
            packed = bytes.fromhex(fields[2])[::-1]
            gateway = str(ipaddress.IPv4Address(packed))
        except (ValueError, IndexError):
            continue
        if flags & 0x2 and not ipaddress.ip_address(gateway).is_unspecified:
            return gateway
    raise ValueError("the default forwarding gateway is unavailable")
