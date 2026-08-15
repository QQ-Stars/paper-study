from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import stat
from typing import Any, Callable, Mapping

from backend.app.infrastructure.database_backup import (
    DatabaseBackupError,
    OriginReceiptReport,
    VerificationReport,
    inspect_database,
    verify_backup,
    verify_origin_receipt,
    verify_origin_receipt_envelope,
)


IDENTITY_SCHEMA_VERSION = 1
IDENTITY_MANIFEST_KIND = "database-evidence-identity"
_IDENTITY_UNSIGNED_FIELDS = (
    "schemaVersion",
    "manifestKind",
    "databaseLineageId",
    "subjectDatabaseId",
    "subjectKind",
    "databasePath",
    "resolvedPathHash",
    "platformFileIdentity",
    "parentBackupId",
    "parentManifestSha256",
    "parentDatabaseIdentityManifestPath",
    "parentSubjectDatabaseId",
    "parentIdentityManifestFileSha256",
    "originReceiptPath",
    "originReceiptFileSha256",
    "originReceiptSha256",
    "createdAt",
)
_IDENTITY_FIELDS = (*_IDENTITY_UNSIGNED_FIELDS, "identityManifestSha256")


class DatabaseIdentityError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class PlatformFileIdentity:
    platform: str
    volume_serial_number: str | None = None
    file_id: str | None = None
    device: str | None = None
    inode: str | None = None

    def to_dict(self) -> dict[str, str]:
        if self.platform == "windows":
            assert self.volume_serial_number is not None
            assert self.file_id is not None
            return {
                "platform": "windows",
                "volumeSerialNumber": self.volume_serial_number,
                "fileId": self.file_id,
            }
        assert self.device is not None
        assert self.inode is not None
        return {
            "platform": "posix",
            "device": self.device,
            "inode": self.inode,
        }

    @classmethod
    def from_dict(cls, value: object) -> PlatformFileIdentity:
        if not isinstance(value, dict):
            raise DatabaseIdentityError(
                "DATABASE_IDENTITY_INVALID",
                "platformFileIdentity must be an object.",
            )
        if tuple(value) == ("platform", "volumeSerialNumber", "fileId"):
            if value["platform"] != "windows":
                raise DatabaseIdentityError(
                    "DATABASE_IDENTITY_INVALID",
                    "The Windows platform file identity is invalid.",
                )
            volume = _required_lower_hex(value["volumeSerialNumber"], 8)
            file_id = _required_lower_hex(value["fileId"], 16)
            return cls(
                platform="windows",
                volume_serial_number=volume,
                file_id=file_id,
            )
        if tuple(value) == ("platform", "device", "inode"):
            if value["platform"] != "posix":
                raise DatabaseIdentityError(
                    "DATABASE_IDENTITY_INVALID",
                    "The POSIX platform file identity is invalid.",
                )
            return cls(
                platform="posix",
                device=_required_lower_hex(value["device"], 16),
                inode=_required_lower_hex(value["inode"], 16),
            )
        raise DatabaseIdentityError(
            "DATABASE_IDENTITY_INVALID",
            "platformFileIdentity has missing, unknown, or out-of-order fields.",
        )


@dataclass(frozen=True, slots=True)
class DatabaseEvidenceIdentityManifest:
    database_lineage_id: str
    subject_database_id: str
    subject_kind: str
    database_path: Path
    resolved_path_hash: str
    platform_file_identity: PlatformFileIdentity
    parent_backup_id: str
    parent_manifest_sha256: str
    parent_database_identity_manifest_path: Path | None
    parent_subject_database_id: str | None
    parent_identity_manifest_file_sha256: str | None
    origin_receipt_path: Path
    origin_receipt_file_sha256: str
    origin_receipt_sha256: str
    created_at: str
    identity_manifest_sha256: str
    manifest_path: Path
    identity_manifest_file_sha256: str
    canonical_bytes: bytes


@dataclass(frozen=True, slots=True)
class VerifiedDatabaseEvidenceIdentity:
    manifest: DatabaseEvidenceIdentityManifest
    verification_mode: str = "read_only"

    def __getattr__(self, name: str) -> Any:
        return getattr(self.manifest, name)


@dataclass(frozen=True, slots=True)
class VerifiedContainerDatabaseEvidenceIdentity:
    manifest: DatabaseEvidenceIdentityManifest
    verification_mode: str = "container_runtime_rebind"

    def __getattr__(self, name: str) -> Any:
        return getattr(self.manifest, name)


@dataclass(frozen=True, slots=True)
class VerifiedEvidenceDatabaseBinding:
    manifest: DatabaseEvidenceIdentityManifest
    parent: DatabaseEvidenceIdentityManifest
    verification_mode: str = "p6_exact_evidence_binding"

    def __getattr__(self, name: str) -> Any:
        return getattr(self.manifest, name)


class ContainerDatabaseIdentityService:
    """Rebind transported candidate evidence to one container-local DB file."""

    def __init__(self, *, clock: Callable[[], datetime] | None = None) -> None:
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def ensure_runtime_identity(
        self,
        *,
        database: str | os.PathLike[str],
        host_database_identity_manifest: str | os.PathLike[str],
        parent_database_identity_manifest: str | os.PathLike[str],
        origin_receipt: str | os.PathLike[str],
        parent_backup: str | os.PathLike[str],
        parent_manifest: str | os.PathLike[str],
        owner: object,
        output: str | os.PathLike[str],
    ) -> VerifiedContainerDatabaseEvidenceIdentity:
        database_path = _resolve_existing_file(database, "container database")
        host_path = _resolve_existing_file(
            host_database_identity_manifest,
            "host candidate identity manifest",
        )
        parent_path = _resolve_existing_file(
            parent_database_identity_manifest,
            "mounted parent identity manifest",
        )
        receipt_path = _resolve_existing_file(origin_receipt, "mounted origin receipt")
        output_path = _resolve_output_path(output)
        if output_path in {database_path, host_path, parent_path, receipt_path}:
            raise DatabaseIdentityError(
                "DATABASE_IDENTITY_PATH_INVALID",
                "The runtime identity output must be distinct from all evidence inputs.",
            )

        host = load_database_evidence_identity_manifest(host_path)
        parent = load_database_evidence_identity_manifest(parent_path)
        _verify_embedded_subject_id(host)
        _verify_embedded_subject_id(parent)
        try:
            receipt = verify_origin_receipt_envelope(
                receipt_path,
                parent.origin_receipt_file_sha256,
            )
            backup = verify_backup(parent_backup, parent_manifest)
        except DatabaseBackupError as error:
            raise DatabaseIdentityError(error.code, str(error)) from error

        owner_state = getattr(owner, "owner_state", None)
        owner_subject = getattr(owner, "subject_database_id", None)
        owner_lineage = getattr(owner, "database_lineage_id", None)
        owner_receipt_sha = getattr(owner, "origin_receipt_file_sha256", None)
        parent_fields = (
            parent.parent_database_identity_manifest_path,
            parent.parent_subject_database_id,
            parent.parent_identity_manifest_file_sha256,
        )
        if (
            owner_state != "node_active"
            or parent.subject_kind != "live"
            or any(value is not None for value in parent_fields)
            or owner_subject != parent.subject_database_id
            or owner_lineage != parent.database_lineage_id
            or owner_receipt_sha != parent.origin_receipt_file_sha256
        ):
            raise DatabaseIdentityError(
                "CONTAINER_IDENTITY_OWNER_MISMATCH",
                "Mounted parent evidence does not match the exact Live owner subject.",
            )
        if (
            host.subject_kind == "live"
            or host.parent_subject_database_id != parent.subject_database_id
            or host.parent_identity_manifest_file_sha256
            != parent.identity_manifest_file_sha256
            or host.database_lineage_id != parent.database_lineage_id
            or host.origin_receipt_file_sha256 != parent.origin_receipt_file_sha256
            or host.origin_receipt_sha256 != parent.origin_receipt_sha256
        ):
            raise DatabaseIdentityError(
                "CONTAINER_IDENTITY_CHAIN_MISMATCH",
                "Host candidate evidence does not match the mounted Live parent.",
            )
        if (
            receipt.database_lineage_id != parent.database_lineage_id
            or receipt.receipt_sha256 != parent.origin_receipt_sha256
            or receipt.backup_id != parent.parent_backup_id
            or receipt.manifest_sha256 != parent.parent_manifest_sha256
        ):
            raise DatabaseIdentityError(
                "CONTAINER_IDENTITY_RECEIPT_MISMATCH",
                "The transported OriginReceipt does not match the Live parent anchor.",
            )
        if (
            backup.backup_id != host.parent_backup_id
            or backup.manifest_file_sha256 != host.parent_manifest_sha256
            or inspect_database(database_path).logical_sha256 != backup.logical_sha256
        ):
            raise DatabaseIdentityError(
                "CONTAINER_IDENTITY_RESTORE_MISMATCH",
                "The container database does not match the verified candidate backup.",
            )

        if not output_path.exists():
            platform_identity = read_platform_file_identity(database_path)
            resolved_path_hash = _sha256_text(str(database_path))
            subject_kind = "p4_container_candidate"
            unsigned = {
                "schemaVersion": IDENTITY_SCHEMA_VERSION,
                "manifestKind": IDENTITY_MANIFEST_KIND,
                "databaseLineageId": parent.database_lineage_id,
                "subjectDatabaseId": _subject_database_id(
                    database_lineage_id=parent.database_lineage_id,
                    subject_kind=subject_kind,
                    resolved_path_hash=resolved_path_hash,
                    platform_file_identity=platform_identity,
                    parent_backup_id=backup.backup_id,
                    parent_manifest_sha256=backup.manifest_file_sha256,
                ),
                "subjectKind": subject_kind,
                "databasePath": str(database_path),
                "resolvedPathHash": resolved_path_hash,
                "platformFileIdentity": platform_identity.to_dict(),
                "parentBackupId": backup.backup_id,
                "parentManifestSha256": backup.manifest_file_sha256,
                "parentDatabaseIdentityManifestPath": str(parent_path),
                "parentSubjectDatabaseId": parent.subject_database_id,
                "parentIdentityManifestFileSha256": parent.identity_manifest_file_sha256,
                "originReceiptPath": str(receipt_path),
                "originReceiptFileSha256": receipt.origin_receipt_file_sha256,
                "originReceiptSha256": receipt.receipt_sha256,
                "createdAt": _utc_timestamp(self._clock()),
            }
            payload = canonical_json_bytes(
                {
                    **unsigned,
                    "identityManifestSha256": hashlib.sha256(
                        canonical_json_bytes(unsigned)
                    ).hexdigest(),
                }
            )
            try:
                exclusive_write_bytes(output_path, payload)
            except DatabaseIdentityError as error:
                if error.code != "EVIDENCE_OUTPUT_EXISTS":
                    raise

        runtime = load_database_evidence_identity_manifest(output_path)
        verify_database_evidence_identity_subject(database=database_path, identity=runtime)
        if (
            runtime.subject_kind != "p4_container_candidate"
            or runtime.database_lineage_id != parent.database_lineage_id
            or runtime.parent_backup_id != backup.backup_id
            or runtime.parent_manifest_sha256 != backup.manifest_file_sha256
            or runtime.parent_database_identity_manifest_path != parent_path
            or runtime.parent_subject_database_id != parent.subject_database_id
            or runtime.parent_identity_manifest_file_sha256
            != parent.identity_manifest_file_sha256
            or runtime.origin_receipt_path != receipt_path
            or runtime.origin_receipt_file_sha256
            != receipt.origin_receipt_file_sha256
            or runtime.origin_receipt_sha256 != receipt.receipt_sha256
        ):
            raise DatabaseIdentityError(
                "CONTAINER_RUNTIME_IDENTITY_MISMATCH",
                "The shared runtime identity does not match the mounted evidence.",
            )
        return VerifiedContainerDatabaseEvidenceIdentity(manifest=runtime)


class LiveDatabaseIdentityVerifier:
    """Read-only verifier used to resume after identity/marker partial state."""

    def verify_existing(
        self,
        *,
        database: str | os.PathLike[str],
        database_identity_manifest: str | os.PathLike[str],
        p0_origin_receipt: str | os.PathLike[str],
        expected_p0_origin_receipt_sha256: str,
        origin_backup: str | os.PathLike[str],
        origin_manifest: str | os.PathLike[str],
    ) -> VerifiedDatabaseEvidenceIdentity:
        database_path = _resolve_existing_file(database, "database")
        manifest_path = _resolve_existing_file(
            database_identity_manifest,
            "database identity manifest",
        )
        before_bytes = manifest_path.read_bytes()
        before_identity = read_platform_file_identity(manifest_path)
        manifest = DatabaseEvidenceIdentityService().verify_live_database_identity(
            database_identity_manifest=manifest_path,
            p0_origin_receipt=p0_origin_receipt,
            expected_p0_origin_receipt_sha256=expected_p0_origin_receipt_sha256,
            origin_backup=origin_backup,
            origin_manifest=origin_manifest,
        )
        if manifest.database_path != database_path:
            raise DatabaseIdentityError(
                "DATABASE_IDENTITY_SUBJECT_MISMATCH",
                "The explicit database path does not match the identity manifest.",
            )
        after_bytes = manifest_path.read_bytes()
        after_identity = read_platform_file_identity(manifest_path)
        if before_bytes != after_bytes or before_identity != after_identity:
            raise DatabaseIdentityError(
                "DATABASE_IDENTITY_DRIFT",
                "The database identity changed during read-only verification.",
            )
        return VerifiedDatabaseEvidenceIdentity(manifest=manifest)


class DatabaseEvidenceIdentityService:
    def __init__(self, *, clock: Callable[[], datetime] | None = None) -> None:
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def create_live_database_identity(
        self,
        *,
        database: str | os.PathLike[str],
        p0_origin_receipt: str | os.PathLike[str],
        expected_p0_origin_receipt_sha256: str,
        origin_backup: str | os.PathLike[str],
        origin_manifest: str | os.PathLike[str],
        output: str | os.PathLike[str],
    ) -> DatabaseEvidenceIdentityManifest:
        receipt, parent = _verify_origin_chain(
            p0_origin_receipt=p0_origin_receipt,
            expected_p0_origin_receipt_sha256=expected_p0_origin_receipt_sha256,
            origin_backup=origin_backup,
            origin_manifest=origin_manifest,
        )
        database_path = _resolve_existing_file(database, "database")
        platform_identity = read_platform_file_identity(database_path)
        resolved_path_hash = _sha256_text(str(database_path))
        subject_kind = "live"
        subject_database_id = _subject_database_id(
            database_lineage_id=receipt.database_lineage_id,
            subject_kind=subject_kind,
            resolved_path_hash=resolved_path_hash,
            platform_file_identity=platform_identity,
            parent_backup_id=parent.backup_id,
            parent_manifest_sha256=parent.manifest_file_sha256,
        )
        created_at = _utc_timestamp(self._clock())
        unsigned = {
            "schemaVersion": IDENTITY_SCHEMA_VERSION,
            "manifestKind": IDENTITY_MANIFEST_KIND,
            "databaseLineageId": receipt.database_lineage_id,
            "subjectDatabaseId": subject_database_id,
            "subjectKind": subject_kind,
            "databasePath": str(database_path),
            "resolvedPathHash": resolved_path_hash,
            "platformFileIdentity": platform_identity.to_dict(),
            "parentBackupId": parent.backup_id,
            "parentManifestSha256": parent.manifest_file_sha256,
            "parentDatabaseIdentityManifestPath": None,
            "parentSubjectDatabaseId": None,
            "parentIdentityManifestFileSha256": None,
            "originReceiptPath": str(receipt.receipt_path),
            "originReceiptFileSha256": receipt.origin_receipt_file_sha256,
            "originReceiptSha256": receipt.receipt_sha256,
            "createdAt": created_at,
        }
        identity_manifest_sha256 = hashlib.sha256(
            canonical_json_bytes(unsigned)
        ).hexdigest()
        document = {
            **unsigned,
            "identityManifestSha256": identity_manifest_sha256,
        }
        payload = canonical_json_bytes(document)
        output_path = _resolve_output_path(output)
        exclusive_write_bytes(output_path, payload)
        return _manifest_from_document(
            document,
            manifest_path=output_path,
            payload=payload,
        )

    def verify_live_database_identity(
        self,
        *,
        database_identity_manifest: str | os.PathLike[str],
        p0_origin_receipt: str | os.PathLike[str],
        expected_p0_origin_receipt_sha256: str,
        origin_backup: str | os.PathLike[str],
        origin_manifest: str | os.PathLike[str],
    ) -> DatabaseEvidenceIdentityManifest:
        receipt, parent = _verify_origin_chain(
            p0_origin_receipt=p0_origin_receipt,
            expected_p0_origin_receipt_sha256=expected_p0_origin_receipt_sha256,
            origin_backup=origin_backup,
            origin_manifest=origin_manifest,
        )
        manifest_path = _resolve_existing_file(
            database_identity_manifest,
            "database identity manifest",
        )
        try:
            payload = manifest_path.read_bytes()
        except OSError as error:
            raise DatabaseIdentityError(
                "DATABASE_IDENTITY_READ_FAILED",
                "Could not read the database identity manifest.",
            ) from error
        document = _strict_json_object(payload, _IDENTITY_FIELDS)
        manifest = _manifest_from_document(
            document,
            manifest_path=manifest_path,
            payload=payload,
        )
        if manifest.subject_kind != "live":
            raise DatabaseIdentityError(
                "DATABASE_IDENTITY_SUBJECT_INVALID",
                "The Node owner requires a Live database identity.",
            )
        if (
            manifest.database_lineage_id != receipt.database_lineage_id
            or manifest.origin_receipt_path != receipt.receipt_path
            or manifest.origin_receipt_file_sha256
            != receipt.origin_receipt_file_sha256
            or manifest.origin_receipt_sha256 != receipt.receipt_sha256
            or manifest.parent_backup_id != parent.backup_id
            or manifest.parent_manifest_sha256 != parent.manifest_file_sha256
            or manifest.parent_database_identity_manifest_path is not None
            or manifest.parent_subject_database_id is not None
            or manifest.parent_identity_manifest_file_sha256 is not None
        ):
            raise DatabaseIdentityError(
                "DATABASE_IDENTITY_CHAIN_MISMATCH",
                "The database identity does not match the retained P0 origin chain.",
            )
        database_path = _resolve_existing_file(manifest.database_path, "database")
        current_identity = read_platform_file_identity(database_path)
        current_path_hash = _sha256_text(str(database_path))
        current_subject_id = _subject_database_id(
            database_lineage_id=receipt.database_lineage_id,
            subject_kind="live",
            resolved_path_hash=current_path_hash,
            platform_file_identity=current_identity,
            parent_backup_id=parent.backup_id,
            parent_manifest_sha256=parent.manifest_file_sha256,
        )
        if (
            database_path != manifest.database_path
            or current_path_hash != manifest.resolved_path_hash
            or current_identity != manifest.platform_file_identity
            or current_subject_id != manifest.subject_database_id
        ):
            raise DatabaseIdentityError(
                "DATABASE_IDENTITY_SUBJECT_MISMATCH",
                "The database path or platform file identity has changed.",
            )
        return manifest

    def create_descendant_database_identity(
        self,
        *,
        database: str | os.PathLike[str],
        subject_kind: str,
        parent_database_identity_manifest: str | os.PathLike[str],
        parent_backup: str | os.PathLike[str],
        parent_manifest: str | os.PathLike[str],
        output: str | os.PathLike[str],
    ) -> DatabaseEvidenceIdentityManifest:
        if (
            not isinstance(subject_kind, str)
            or not subject_kind
            or subject_kind == "live"
            or any(character not in "abcdefghijklmnopqrstuvwxyz0123456789_-" for character in subject_kind)
        ):
            raise DatabaseIdentityError(
                "DATABASE_IDENTITY_SUBJECT_INVALID",
                "A descendant subject kind must be a non-live lowercase identifier.",
            )
        parent_path = _resolve_existing_file(
            parent_database_identity_manifest,
            "parent database identity manifest",
        )
        parent_document = _strict_json_object(
            parent_path.read_bytes(),
            _IDENTITY_FIELDS,
        )
        parent_identity = _manifest_from_document(
            parent_document,
            manifest_path=parent_path,
            payload=parent_path.read_bytes(),
        )
        if parent_identity.subject_kind != "live":
            raise DatabaseIdentityError(
                "DATABASE_IDENTITY_PARENT_INVALID",
                "A descendant must be anchored to a verified Live identity.",
            )
        database_path = _resolve_existing_file(database, "database")
        if database_path == parent_identity.database_path:
            raise DatabaseIdentityError(
                "DATABASE_IDENTITY_DESCENDANT_LIVE_SUBJECT",
                "A descendant identity cannot bind the Live database subject.",
            )
        platform_identity = read_platform_file_identity(database_path)
        if platform_identity == parent_identity.platform_file_identity:
            raise DatabaseIdentityError(
                "DATABASE_IDENTITY_DESCENDANT_LIVE_SUBJECT",
                "A descendant identity cannot alias the Live database file object.",
            )
        receipt = verify_origin_receipt(
            parent_identity.origin_receipt_path,
            parent_identity.origin_receipt_file_sha256,
        )
        parent_backup_path = _resolve_existing_file(parent_backup, "parent backup")
        parent_manifest_path = _resolve_existing_file(parent_manifest, "parent Manifest")
        parent_verification = verify_backup(parent_backup_path, parent_manifest_path)
        if (
            receipt.database_lineage_id != parent_identity.database_lineage_id
            or receipt.receipt_sha256 != parent_identity.origin_receipt_sha256
            or parent_verification.logical_sha256 == ""
        ):
            raise DatabaseIdentityError(
                "DATABASE_IDENTITY_PARENT_INVALID",
                "The descendant parent does not share the Live receipt anchor.",
            )
        if inspect_database(database_path).logical_sha256 != parent_verification.logical_sha256:
            raise DatabaseIdentityError(
                "DATABASE_IDENTITY_RESTORE_MISMATCH",
                "The descendant database does not match the verified parent backup.",
            )
        resolved_path_hash = _sha256_text(str(database_path))
        subject_database_id = _subject_database_id(
            database_lineage_id=parent_identity.database_lineage_id,
            subject_kind=subject_kind,
            resolved_path_hash=resolved_path_hash,
            platform_file_identity=platform_identity,
            parent_backup_id=parent_verification.backup_id,
            parent_manifest_sha256=parent_verification.manifest_file_sha256,
        )
        unsigned = {
            "schemaVersion": IDENTITY_SCHEMA_VERSION,
            "manifestKind": IDENTITY_MANIFEST_KIND,
            "databaseLineageId": parent_identity.database_lineage_id,
            "subjectDatabaseId": subject_database_id,
            "subjectKind": subject_kind,
            "databasePath": str(database_path),
            "resolvedPathHash": resolved_path_hash,
            "platformFileIdentity": platform_identity.to_dict(),
            "parentBackupId": parent_verification.backup_id,
            "parentManifestSha256": parent_verification.manifest_file_sha256,
            "parentDatabaseIdentityManifestPath": str(parent_path),
            "parentSubjectDatabaseId": parent_identity.subject_database_id,
            "parentIdentityManifestFileSha256": parent_identity.identity_manifest_file_sha256,
            "originReceiptPath": str(parent_identity.origin_receipt_path),
            "originReceiptFileSha256": parent_identity.origin_receipt_file_sha256,
            "originReceiptSha256": parent_identity.origin_receipt_sha256,
            "createdAt": _utc_timestamp(self._clock()),
        }
        document = {
            **unsigned,
            "identityManifestSha256": hashlib.sha256(
                canonical_json_bytes(unsigned)
            ).hexdigest(),
        }
        payload = canonical_json_bytes(document)
        output_path = _resolve_output_path(output)
        exclusive_write_bytes(output_path, payload)
        return _manifest_from_document(
            document,
            manifest_path=output_path,
            payload=payload,
        )


def canonical_json_bytes(document: Mapping[str, Any]) -> bytes:
    return json.dumps(
        document,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")


def load_database_evidence_identity_manifest(
    value: str | os.PathLike[str],
) -> DatabaseEvidenceIdentityManifest:
    path = _resolve_existing_file(value, "database identity manifest")
    payload = path.read_bytes()
    document = _strict_json_object(payload, _IDENTITY_FIELDS)
    return _manifest_from_document(document, manifest_path=path, payload=payload)


def verify_database_evidence_identity_subject(
    *,
    database: str | os.PathLike[str],
    identity: DatabaseEvidenceIdentityManifest,
) -> DatabaseEvidenceIdentityManifest:
    """Bind a decoded evidence manifest to the exact current database file."""

    database_path = _resolve_existing_file(database, "database")
    platform_identity = read_platform_file_identity(database_path)
    resolved_path_hash = _sha256_text(str(database_path))
    subject_database_id = _subject_database_id(
        database_lineage_id=identity.database_lineage_id,
        subject_kind=identity.subject_kind,
        resolved_path_hash=resolved_path_hash,
        platform_file_identity=platform_identity,
        parent_backup_id=identity.parent_backup_id,
        parent_manifest_sha256=identity.parent_manifest_sha256,
    )
    if (
        identity.database_path != database_path
        or identity.resolved_path_hash != resolved_path_hash
        or identity.platform_file_identity != platform_identity
        or identity.subject_database_id != subject_database_id
    ):
        raise DatabaseIdentityError(
            "DATABASE_IDENTITY_SUBJECT_MISMATCH",
            "The database path or platform file identity does not match the manifest.",
        )
    return identity


def verify_descendant_database_evidence_identity(
    *,
    database: str | os.PathLike[str],
    identity: DatabaseEvidenceIdentityManifest,
    parent_backup: str | os.PathLike[str] | None = None,
    parent_manifest: str | os.PathLike[str] | None = None,
) -> DatabaseEvidenceIdentityManifest:
    """Verify a non-Live subject, its Live parent, and optional backup contents."""

    parent_fields = (
        identity.parent_database_identity_manifest_path,
        identity.parent_subject_database_id,
        identity.parent_identity_manifest_file_sha256,
    )
    if (
        identity.subject_kind == "live"
        or not identity.subject_kind
        or any(value is None for value in parent_fields)
    ):
        raise DatabaseIdentityError(
            "DATABASE_IDENTITY_DESCENDANT_INVALID",
            "A candidate requires a canonical non-Live descendant identity.",
        )
    verify_database_evidence_identity_subject(database=database, identity=identity)
    assert identity.parent_database_identity_manifest_path is not None
    parent = load_database_evidence_identity_manifest(
        identity.parent_database_identity_manifest_path
    )
    if parent.subject_kind != "live":
        raise DatabaseIdentityError(
            "DATABASE_IDENTITY_PARENT_INVALID",
            "A candidate descendant must be anchored to a Live parent identity.",
        )
    verify_database_evidence_identity_subject(
        database=parent.database_path,
        identity=parent,
    )
    if (
        identity.database_path == parent.database_path
        or identity.platform_file_identity == parent.platform_file_identity
    ):
        raise DatabaseIdentityError(
            "DATABASE_IDENTITY_DESCENDANT_LIVE_SUBJECT",
            "A descendant identity cannot bind or alias the Live database subject.",
        )
    if (
        identity.parent_subject_database_id != parent.subject_database_id
        or identity.parent_identity_manifest_file_sha256
        != parent.identity_manifest_file_sha256
        or identity.database_lineage_id != parent.database_lineage_id
        or identity.origin_receipt_path != parent.origin_receipt_path
        or identity.origin_receipt_file_sha256 != parent.origin_receipt_file_sha256
        or identity.origin_receipt_sha256 != parent.origin_receipt_sha256
    ):
        raise DatabaseIdentityError(
            "DATABASE_IDENTITY_CHAIN_MISMATCH",
            "The descendant identity no longer matches its exact Live parent.",
        )
    receipt = verify_origin_receipt(
        parent.origin_receipt_path,
        parent.origin_receipt_file_sha256,
    )
    if (
        receipt.database_lineage_id != parent.database_lineage_id
        or receipt.receipt_sha256 != parent.origin_receipt_sha256
    ):
        raise DatabaseIdentityError(
            "DATABASE_IDENTITY_CHAIN_MISMATCH",
            "The Live parent no longer matches its retained origin receipt.",
        )

    if (parent_backup is None) != (parent_manifest is None):
        raise DatabaseIdentityError(
            "DATABASE_IDENTITY_PARENT_EVIDENCE_REQUIRED",
            "Parent backup and Manifest must be supplied together.",
        )
    if parent_backup is not None and parent_manifest is not None:
        verification = verify_backup(parent_backup, parent_manifest)
        if (
            verification.backup_id != identity.parent_backup_id
            or verification.manifest_file_sha256 != identity.parent_manifest_sha256
            or inspect_database(identity.database_path).logical_sha256
            != verification.logical_sha256
        ):
            raise DatabaseIdentityError(
                "DATABASE_IDENTITY_RESTORE_MISMATCH",
                "The candidate database no longer matches its exact verified parent backup.",
            )
    return identity


def verify_evidence_database_binding(
    *,
    database: str | os.PathLike[str],
    database_identity_manifest: str | os.PathLike[str],
    parent_database_identity_manifest: str | os.PathLike[str],
    parent_backup: str | os.PathLike[str],
    parent_manifest: str | os.PathLike[str],
    origin_receipt: str | os.PathLike[str],
    expected_origin_receipt_file_sha256: str,
    expected_subject_kind: str,
) -> VerifiedEvidenceDatabaseBinding:
    """Verify one P6 capture against its exact P4 subject and parent chain."""

    identity_path = _resolve_existing_file(
        database_identity_manifest,
        "database identity manifest",
    )
    parent_path = _resolve_existing_file(
        parent_database_identity_manifest,
        "parent database identity manifest",
    )
    receipt_path = _resolve_existing_file(origin_receipt, "origin receipt")
    identity = load_database_evidence_identity_manifest(identity_path)
    parent = load_database_evidence_identity_manifest(parent_path)
    if (
        not isinstance(expected_subject_kind, str)
        or not expected_subject_kind
        or identity.subject_kind != expected_subject_kind
        or identity.parent_database_identity_manifest_path != parent_path
        or identity.parent_subject_database_id != parent.subject_database_id
        or identity.parent_identity_manifest_file_sha256
        != parent.identity_manifest_file_sha256
        or identity.origin_receipt_path != receipt_path
        or parent.origin_receipt_path != receipt_path
    ):
        raise DatabaseIdentityError(
            "EVIDENCE_DATABASE_BINDING_MISMATCH",
            "The capture identity does not match the exact subject or parent chain.",
        )
    try:
        receipt = verify_origin_receipt(
            receipt_path,
            expected_origin_receipt_file_sha256,
        )
        verify_descendant_database_evidence_identity(
            database=database,
            identity=identity,
            parent_backup=parent_backup,
            parent_manifest=parent_manifest,
        )
    except DatabaseBackupError as error:
        raise DatabaseIdentityError(error.code, str(error)) from error
    if (
        receipt.origin_receipt_file_sha256 != identity.origin_receipt_file_sha256
        or receipt.receipt_sha256 != identity.origin_receipt_sha256
        or receipt.database_lineage_id != identity.database_lineage_id
        or parent.subject_kind != "live"
        or parent.database_lineage_id != identity.database_lineage_id
    ):
        raise DatabaseIdentityError(
            "EVIDENCE_DATABASE_BINDING_MISMATCH",
            "The capture identity does not match the exact OriginReceipt anchor.",
        )
    return VerifiedEvidenceDatabaseBinding(manifest=identity, parent=parent)


def exclusive_write_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    descriptor: int | None = None
    try:
        descriptor = os.open(
            path,
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_BINARY", 0),
            0o600,
        )
        view = memoryview(payload)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise OSError("exclusive write made no progress")
            view = view[written:]
        os.fsync(descriptor)
    except FileExistsError as error:
        raise DatabaseIdentityError(
            "EVIDENCE_OUTPUT_EXISTS",
            f"Evidence output already exists and will not be overwritten: {path}",
        ) from error
    except DatabaseIdentityError:
        raise
    except OSError as error:
        raise DatabaseIdentityError(
            "EVIDENCE_OUTPUT_WRITE_FAILED",
            f"Could not exclusively publish evidence output: {path}",
        ) from error
    finally:
        if descriptor is not None:
            os.close(descriptor)


def read_platform_file_identity(path: Path) -> PlatformFileIdentity:
    if os.name == "nt":
        return _read_windows_platform_file_identity(path)
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise DatabaseIdentityError(
                "DATABASE_PATH_INVALID",
                "The database must be a physical regular file.",
            )
        return PlatformFileIdentity(
            platform="posix",
            device=f"{int(metadata.st_dev):016x}",
            inode=f"{int(metadata.st_ino):016x}",
        )
    finally:
        os.close(descriptor)


def _read_windows_platform_file_identity(path: Path) -> PlatformFileIdentity:
    import ctypes
    from ctypes import wintypes

    class FileTime(ctypes.Structure):
        _fields_ = (("low", wintypes.DWORD), ("high", wintypes.DWORD))

    class ByHandleFileInformation(ctypes.Structure):
        _fields_ = (
            ("attributes", wintypes.DWORD),
            ("creation_time", FileTime),
            ("access_time", FileTime),
            ("write_time", FileTime),
            ("volume_serial_number", wintypes.DWORD),
            ("file_size_high", wintypes.DWORD),
            ("file_size_low", wintypes.DWORD),
            ("number_of_links", wintypes.DWORD),
            ("file_index_high", wintypes.DWORD),
            ("file_index_low", wintypes.DWORD),
        )

    read_attributes = 0x00000080
    share_read = 0x00000001
    share_write = 0x00000002
    share_delete = 0x00000004
    open_existing = 3
    open_reparse_point = 0x00200000
    invalid_handle = ctypes.c_void_p(-1).value
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    create_file = kernel32.CreateFileW
    create_file.argtypes = (
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    )
    create_file.restype = wintypes.HANDLE
    handle = create_file(
        str(path),
        read_attributes,
        share_read | share_write | share_delete,
        None,
        open_existing,
        open_reparse_point,
        None,
    )
    if handle == invalid_handle:
        raise DatabaseIdentityError(
            "DATABASE_PATH_INVALID",
            f"Could not open the database for platform identity: {path}",
        )
    try:
        get_information = kernel32.GetFileInformationByHandle
        get_information.argtypes = (
            wintypes.HANDLE,
            ctypes.POINTER(ByHandleFileInformation),
        )
        get_information.restype = wintypes.BOOL
        information = ByHandleFileInformation()
        if not get_information(handle, ctypes.byref(information)):
            raise DatabaseIdentityError(
                "DATABASE_PATH_INVALID",
                "Could not read the database platform file identity.",
            )
        directory_attribute = 0x10
        reparse_attribute = 0x400
        if information.attributes & (directory_attribute | reparse_attribute):
            raise DatabaseIdentityError(
                "DATABASE_PATH_INVALID",
                "The database must be a physical, non-reparse file.",
            )
        file_id = (
            int(information.file_index_high) << 32
        ) | int(information.file_index_low)
        return PlatformFileIdentity(
            platform="windows",
            volume_serial_number=f"{int(information.volume_serial_number):08x}",
            file_id=f"{file_id:016x}",
        )
    finally:
        kernel32.CloseHandle(handle)


def _verify_origin_chain(
    *,
    p0_origin_receipt: str | os.PathLike[str],
    expected_p0_origin_receipt_sha256: str,
    origin_backup: str | os.PathLike[str],
    origin_manifest: str | os.PathLike[str],
) -> tuple[OriginReceiptReport, VerificationReport]:
    receipt = verify_origin_receipt(
        p0_origin_receipt,
        expected_p0_origin_receipt_sha256,
    )
    backup_path = _resolve_existing_file(origin_backup, "origin backup")
    manifest_path = _resolve_existing_file(origin_manifest, "origin manifest")
    if backup_path != receipt.backup_path or manifest_path != receipt.manifest_path:
        raise DatabaseIdentityError(
            "DATABASE_IDENTITY_ORIGIN_MISMATCH",
            "The origin backup and Manifest must be the exact pair named by the P0 receipt.",
        )
    verification = verify_backup(backup_path, manifest_path)
    if (
        verification.backup_id != receipt.backup_id
        or verification.backup_sha256 != receipt.backup_sha256
        or verification.manifest_file_sha256 != receipt.manifest_sha256
        or verification.logical_sha256 != receipt.logical_sha256
    ):
        raise DatabaseIdentityError(
            "DATABASE_IDENTITY_ORIGIN_MISMATCH",
            "The retained origin no longer matches the P0 receipt.",
        )
    lineage_document = {
        "version": 1,
        "originBackupId": receipt.backup_id,
        "originManifestSha256": receipt.manifest_sha256,
        "originLogicalSha256": receipt.logical_sha256,
    }
    lineage_id = hashlib.sha256(canonical_json_bytes(lineage_document)).hexdigest()
    if lineage_id != receipt.database_lineage_id:
        raise DatabaseIdentityError(
            "DATABASE_IDENTITY_LINEAGE_INVALID",
            "The P0 receipt database lineage cannot be independently reproduced.",
        )
    return receipt, verification


def _subject_database_id(
    *,
    database_lineage_id: str,
    subject_kind: str,
    resolved_path_hash: str,
    platform_file_identity: PlatformFileIdentity,
    parent_backup_id: str,
    parent_manifest_sha256: str,
) -> str:
    document = {
        "version": 1,
        "databaseLineageId": database_lineage_id,
        "subjectKind": subject_kind,
        "resolvedPathHash": resolved_path_hash,
        "platformFileIdentity": platform_file_identity.to_dict(),
        "parentBackupId": parent_backup_id,
        "parentManifestSha256": parent_manifest_sha256,
    }
    return hashlib.sha256(canonical_json_bytes(document)).hexdigest()


def _verify_embedded_subject_id(identity: DatabaseEvidenceIdentityManifest) -> None:
    expected = _subject_database_id(
        database_lineage_id=identity.database_lineage_id,
        subject_kind=identity.subject_kind,
        resolved_path_hash=identity.resolved_path_hash,
        platform_file_identity=identity.platform_file_identity,
        parent_backup_id=identity.parent_backup_id,
        parent_manifest_sha256=identity.parent_manifest_sha256,
    )
    if identity.subject_database_id != expected:
        raise DatabaseIdentityError(
            "DATABASE_IDENTITY_SUBJECT_MISMATCH",
            "The transported database identity subject hash is invalid.",
        )


def _manifest_from_document(
    document: Mapping[str, Any],
    *,
    manifest_path: Path,
    payload: bytes,
) -> DatabaseEvidenceIdentityManifest:
    if tuple(document) != _IDENTITY_FIELDS or canonical_json_bytes(document) != payload:
        raise DatabaseIdentityError(
            "DATABASE_IDENTITY_INVALID",
            "The database identity is not strict canonical JSON.",
        )
    schema_version = document["schemaVersion"]
    if isinstance(schema_version, bool) or schema_version != IDENTITY_SCHEMA_VERSION:
        raise DatabaseIdentityError(
            "DATABASE_IDENTITY_INVALID",
            "The database identity schema version is invalid.",
        )
    if document["manifestKind"] != IDENTITY_MANIFEST_KIND:
        raise DatabaseIdentityError(
            "DATABASE_IDENTITY_INVALID",
            "The database identity manifest kind is invalid.",
        )
    string_fields = (
        "databaseLineageId",
        "subjectDatabaseId",
        "subjectKind",
        "databasePath",
        "resolvedPathHash",
        "parentBackupId",
        "parentManifestSha256",
        "originReceiptPath",
        "originReceiptFileSha256",
        "originReceiptSha256",
        "createdAt",
        "identityManifestSha256",
    )
    if any(
        not isinstance(document[field], str)
        or not document[field]
        or "\x00" in document[field]
        for field in string_fields
    ):
        raise DatabaseIdentityError(
            "DATABASE_IDENTITY_INVALID",
            "The database identity contains an invalid string field.",
        )
    for field in (
        "databaseLineageId",
        "subjectDatabaseId",
        "resolvedPathHash",
        "parentManifestSha256",
        "originReceiptFileSha256",
        "originReceiptSha256",
        "identityManifestSha256",
    ):
        _required_lower_hex(document[field], 64)
    for field in (
        "parentDatabaseIdentityManifestPath",
        "parentSubjectDatabaseId",
        "parentIdentityManifestFileSha256",
    ):
        value = document[field]
        if value is not None and (not isinstance(value, str) or not value):
            raise DatabaseIdentityError(
                "DATABASE_IDENTITY_INVALID",
                f"The database identity field {field} is invalid.",
            )
    unsigned = {field: document[field] for field in _IDENTITY_UNSIGNED_FIELDS}
    expected_self_hash = hashlib.sha256(canonical_json_bytes(unsigned)).hexdigest()
    if document["identityManifestSha256"] != expected_self_hash:
        raise DatabaseIdentityError(
            "DATABASE_IDENTITY_INVALID",
            "The database identity self hash is invalid.",
        )
    database_path = Path(document["databasePath"])
    receipt_path = Path(document["originReceiptPath"])
    if (
        not database_path.is_absolute()
        or database_path.resolve() != database_path
        or not receipt_path.is_absolute()
        or receipt_path.resolve() != receipt_path
    ):
        raise DatabaseIdentityError(
            "DATABASE_IDENTITY_INVALID",
            "The database identity contains a non-canonical path.",
        )
    parent_identity_path_value = document["parentDatabaseIdentityManifestPath"]
    parent_identity_path = (
        None if parent_identity_path_value is None else Path(parent_identity_path_value)
    )
    return DatabaseEvidenceIdentityManifest(
        database_lineage_id=document["databaseLineageId"],
        subject_database_id=document["subjectDatabaseId"],
        subject_kind=document["subjectKind"],
        database_path=database_path,
        resolved_path_hash=document["resolvedPathHash"],
        platform_file_identity=PlatformFileIdentity.from_dict(
            document["platformFileIdentity"]
        ),
        parent_backup_id=document["parentBackupId"],
        parent_manifest_sha256=document["parentManifestSha256"],
        parent_database_identity_manifest_path=parent_identity_path,
        parent_subject_database_id=document["parentSubjectDatabaseId"],
        parent_identity_manifest_file_sha256=document[
            "parentIdentityManifestFileSha256"
        ],
        origin_receipt_path=receipt_path,
        origin_receipt_file_sha256=document["originReceiptFileSha256"],
        origin_receipt_sha256=document["originReceiptSha256"],
        created_at=document["createdAt"],
        identity_manifest_sha256=document["identityManifestSha256"],
        manifest_path=manifest_path,
        identity_manifest_file_sha256=hashlib.sha256(payload).hexdigest(),
        canonical_bytes=payload,
    )


def _strict_json_object(payload: bytes, fields: tuple[str, ...]) -> dict[str, Any]:
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
        raise DatabaseIdentityError(
            "DATABASE_IDENTITY_INVALID",
            "The database identity is not valid canonical UTF-8 JSON.",
        ) from error
    if not isinstance(document, dict) or tuple(document) != fields:
        raise DatabaseIdentityError(
            "DATABASE_IDENTITY_INVALID",
            "The database identity fields are missing, unknown, or out of order.",
        )
    if canonical_json_bytes(document) != payload:
        raise DatabaseIdentityError(
            "DATABASE_IDENTITY_INVALID",
            "The database identity must use canonical JSON serialization.",
        )
    return document


def _resolve_existing_file(value: str | os.PathLike[str], description: str) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        raise DatabaseIdentityError(
            "DATABASE_IDENTITY_PATH_INVALID",
            f"The {description} path must be exact and absolute.",
        )
    resolved = path.resolve()
    if not resolved.is_file():
        raise DatabaseIdentityError(
            "DATABASE_IDENTITY_PATH_INVALID",
            f"The {description} path does not name a file.",
        )
    return resolved


def _resolve_output_path(value: str | os.PathLike[str]) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        raise DatabaseIdentityError(
            "DATABASE_IDENTITY_PATH_INVALID",
            "The evidence output path must be exact and absolute.",
        )
    return path.resolve(strict=False)


def _required_lower_hex(value: object, width: int) -> str:
    if (
        not isinstance(value, str)
        or len(value) != width
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise DatabaseIdentityError(
            "DATABASE_IDENTITY_INVALID",
            "A lowercase hexadecimal identity field is invalid.",
        )
    return value


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _utc_timestamp(value: datetime) -> str:
    if value.tzinfo is None:
        raise DatabaseIdentityError(
            "DATABASE_IDENTITY_CLOCK_INVALID",
            "Identity timestamps require a timezone-aware clock.",
        )
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace(
        "+00:00",
        "Z",
    )
