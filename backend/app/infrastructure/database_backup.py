from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import stat
import struct
import uuid
from contextlib import closing
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Protocol, Sequence

from backend.app.domain.processing import JobSpecValidationError, decode_job_spec_v1


CURRENT_BACKUP_FORMAT_VERSION = 2
SUPPORTED_BACKUP_FORMAT_VERSIONS = frozenset({1, CURRENT_BACKUP_FORMAT_VERSION})
# Kept as a compatibility alias for callers that imported the original name.
BACKUP_FORMAT_VERSION = CURRENT_BACKUP_FORMAT_VERSION
ORIGIN_RECEIPT_SCHEMA_VERSION = 1
ORIGIN_RECEIPT_MANIFEST_KIND = "p0-origin"
ORIGIN_RECEIPT_FILENAME = "p0-origin-receipt-v1.json"
_ORIGIN_RECEIPT_UNSIGNED_FIELDS = (
    "schemaVersion",
    "manifestKind",
    "backupId",
    "backupPath",
    "backupSha256",
    "manifestPath",
    "manifestSha256",
    "logicalSha256",
    "databaseLineageId",
    "receiptPath",
    "createdAt",
)
_ORIGIN_RECEIPT_FIELDS = (*_ORIGIN_RECEIPT_UNSIGNED_FIELDS, "receiptSha256")
_LOWER_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_LABEL_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_BACKUP_ID_PATTERN = re.compile(r"^[0-9a-f]{32}$")
_COPY_BUFFER_SIZE = 1024 * 1024
P2_CONTENT_PROJECTIONS = {
    "document_sources": (
        (
            "documentSources",
            (
                "id", "paper_id", "mode", "status", "provider", "model",
                "pdf_sha256", "options_hash", "content_sha256", "markdown",
                "page_count", "processing_version", "error_code", "error_message",
                "created_at", "updated_at", "source_key", "ready_at", "stale_at",
            ),
            None,
            "all",
        ),
    ),
    "generated_artifacts": (
        (
            "generatedArtifacts",
            (
                "id", "paper_id", "kind", "source_document_id", "status",
                "content", "content_sha256", "generator_provider",
                "generator_model", "prompt_version", "error_code", "error_message",
                "created_at", "updated_at", "artifact_key", "ready_at", "stale_at",
            ),
            None,
            "all",
        ),
    ),
    "processing_jobs": (
        (
            "processingJobs",
            (
                "id", "paper_id", "job_type", "source_mode", "status",
                "progress_json", "attempt", "max_attempts", "idempotency_key",
                "error_code", "error_message", "created_at", "started_at",
                "finished_at", "cancelled_at", "source_document_id", "artifact_id",
                "spec_json", "available_at", "lease_owner", "lease_token",
                "lease_expires_at", "heartbeat_at", "cancel_requested_at",
                "result_json", "updated_at", "retry_of_job_id", "retry_sequence",
            ),
            None,
            "all",
        ),
        ("processingJobSpecs", ("id", "spec_json"), None, "all"),
    ),
}
P3_CONTENT_PROJECTIONS = {
    "document_chunks": (
        (
            "documentChunks",
            (
                "id", "source_document_id", "sequence", "heading_path", "page_start",
                "page_end", "content", "content_sha256", "token_count", "status",
                "content_kind", "chunk_key", "chunking_version", "source_content_sha256",
                "char_start", "char_end", "created_at", "updated_at", "stale_at",
            ),
            None,
            "all",
        ),
    ),
    "document_chunk_embeddings": (
        (
            "chunkEmbeddings",
            (
                "id", "chunk_id", "source_document_id", "provider", "model",
                "embedding_version", "dimensions", "vector_sha256", "chunk_content_sha256",
                "status", "error_code", "error_message", "created_at", "updated_at",
                "stale_at",
            ),
            None,
            "all",
        ),
    ),
    "artifact_translation_checkpoints": (
        (
            "translationCheckpoints",
            (
                "artifact_id", "chunk_id", "sequence", "source_content_sha256",
                "provider", "model", "prompt_version", "status", "translated_markdown",
                "content_sha256", "attempt", "error_code", "error_message", "created_at",
                "updated_at",
            ),
            None,
            "all",
        ),
    ),
}
_P3_FTS_TABLE = "document_chunks_fts"
_P3_FTS_SHADOW_PREFIX = "document_chunks_fts_"
_P3_FTS_TRIGGER_NAMES = frozenset(
    {
        "document_chunks_fts_ai",
        "document_chunks_fts_ad",
        "document_chunks_fts_au",
    }
)
_P3_FTS_TRIGGER_SHA256 = {
    "document_chunks_fts_ai": "2af48a2c2b8ca8921ad62c9ef0de64bc8fcaf3dd04c1878b8773b448631b4fff",
    "document_chunks_fts_ad": "5413206258bc5f1093b1c8ce6feb933eedb95687419218ff2b8517179c1412e6",
    "document_chunks_fts_au": "849f80306c6109b0b3730e2f32c2d458b0cdc45864a2280956a75f42b74b9330",
}
_P3_REQUIRED_TABLE_COLUMNS = {
    "document_chunks": frozenset(
        {
            "id", "source_document_id", "sequence", "heading_path", "page_start",
            "page_end", "content", "content_sha256", "token_count", "status",
            "content_kind", "chunk_key", "chunking_version", "source_content_sha256",
            "char_start", "char_end", "created_at", "updated_at", "stale_at",
        }
    ),
    "document_chunk_embeddings": frozenset(
        {
            "id", "chunk_id", "source_document_id", "provider", "model",
            "embedding_version", "dimensions", "vector", "vector_sha256",
            "chunk_content_sha256", "status", "error_code", "error_message",
            "created_at", "updated_at", "stale_at",
        }
    ),
    "artifact_translation_checkpoints": frozenset(
        {
            "artifact_id", "chunk_id", "sequence", "source_content_sha256",
            "provider", "model", "prompt_version", "status", "translated_markdown",
            "content_sha256", "attempt", "error_code", "error_message", "created_at",
            "updated_at",
        }
    ),
}
P1_CORE_CONTENT_PROJECTIONS = {
    "document_sources": (
        (
            "p1CoreDocumentSources",
            (
                "id", "paper_id", "mode", "status", "provider", "model",
                "pdf_sha256", "options_hash", "content_sha256", "markdown",
                "page_count", "processing_version", "error_code", "error_message",
                "created_at", "updated_at",
            ),
            None,
            "all",
        ),
    ),
    "generated_artifacts": (
        (
            "p1CoreGeneratedArtifacts",
            (
                "id", "paper_id", "kind", "source_document_id", "status",
                "content", "content_sha256", "generator_provider",
                "generator_model", "prompt_version", "error_code", "error_message",
                "created_at", "updated_at",
            ),
            None,
            "all",
        ),
    ),
    "processing_jobs": (
        (
            "p1CoreProcessingJobs",
            (
                "id", "paper_id", "job_type", "source_mode", "status",
                "progress_json", "attempt", "max_attempts", "idempotency_key",
                "error_code", "error_message", "created_at", "started_at",
                "finished_at", "cancelled_at",
            ),
            None,
            "all",
        ),
    ),
}
_P2_SPEC_GUARD_INVENTORY = {
    "processing_jobs_spec_guard_insert": (
        "processingJobsSpecGuardInsert",
        "499a50aaca8952b838ccea76c2b6db8714f7a9b8c018e2b21bf55eefe7b1b935",
    ),
    "processing_jobs_spec_guard_update": (
        "processingJobsSpecGuardUpdate",
        "eedfd7ec71a936078358508dee5758c7a8a4af9b702c4fd75228b59ef71f8a38",
    ),
}
_MANIFEST_FIELDS = frozenset(
    {
        "formatVersion",
        "backupId",
        "label",
        "createdAt",
        "sourceDatabase",
        "backupFile",
        "sourceJournalMode",
        "sqliteVersion",
        "backupSizeBytes",
        "backupSha256",
        "database",
        "manifestSha256",
    }
)
_DATABASE_FINGERPRINT_FIELDS = frozenset(
    {
        "quickCheck",
        "integrityCheck",
        "foreignKeyViolations",
        "schemaSha256",
        "logicalSha256",
        "tableCounts",
        "tableSha256",
        "contentCounts",
        "contentSha256",
        "legacySchemaMigrations",
        "alembicVersion",
        "pageSize",
        "pageCount",
        "freelistCount",
        "schemaVersion",
        "userVersion",
        "applicationId",
    }
)


class DatabaseBackupError(RuntimeError):
    """A safe, classified failure from the database backup interface."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class _FileIdentity:
    device: int
    inode: int
    size: int
    modified_ns: int
    changed_ns: int


@dataclass(frozen=True)
class DatabaseFingerprint:
    quick_check: str
    integrity_check: str
    foreign_key_violations: int
    schema_sha256: str
    logical_sha256: str
    table_counts: Mapping[str, int]
    table_sha256: Mapping[str, str]
    content_counts: Mapping[str, int]
    content_sha256: Mapping[str, str]
    legacy_schema_migrations: tuple[int, ...]
    alembic_version: str | None
    page_size: int
    page_count: int
    freelist_count: int
    schema_version: int
    user_version: int
    application_id: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "quickCheck": self.quick_check,
            "integrityCheck": self.integrity_check,
            "foreignKeyViolations": self.foreign_key_violations,
            "schemaSha256": self.schema_sha256,
            "logicalSha256": self.logical_sha256,
            "tableCounts": dict(sorted(self.table_counts.items())),
            "tableSha256": dict(sorted(self.table_sha256.items())),
            "contentCounts": dict(sorted(self.content_counts.items())),
            "contentSha256": dict(sorted(self.content_sha256.items())),
            "legacySchemaMigrations": list(self.legacy_schema_migrations),
            "alembicVersion": self.alembic_version,
            "pageSize": self.page_size,
            "pageCount": self.page_count,
            "freelistCount": self.freelist_count,
            "schemaVersion": self.schema_version,
            "userVersion": self.user_version,
            "applicationId": self.application_id,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "DatabaseFingerprint":
        _reject_unknown_fields(
            value,
            allowed_fields=_DATABASE_FINGERPRINT_FIELDS,
            description="Backup manifest database object",
        )
        try:
            table_counts = _string_int_mapping(value["tableCounts"], "tableCounts")
            table_sha256 = _string_hash_mapping(value["tableSha256"], "tableSha256")
            content_counts = _string_int_mapping(value["contentCounts"], "contentCounts")
            content_sha256 = _string_hash_mapping(value["contentSha256"], "contentSha256")
            fingerprint = cls(
                quick_check=_required_string(value, "quickCheck"),
                integrity_check=_required_string(value, "integrityCheck"),
                foreign_key_violations=_required_int(value, "foreignKeyViolations"),
                schema_sha256=_required_sha256(value, "schemaSha256"),
                logical_sha256=_required_sha256(value, "logicalSha256"),
                table_counts=table_counts,
                table_sha256=table_sha256,
                content_counts=content_counts,
                content_sha256=content_sha256,
                legacy_schema_migrations=_required_int_sequence(
                    value,
                    "legacySchemaMigrations",
                ),
                alembic_version=_required_optional_string(value, "alembicVersion"),
                page_size=_required_int(value, "pageSize"),
                page_count=_required_int(value, "pageCount"),
                freelist_count=_required_int(value, "freelistCount"),
                schema_version=_required_int(value, "schemaVersion"),
                user_version=_required_int(value, "userVersion"),
                application_id=_required_int(value, "applicationId"),
            )
        except KeyError as error:
            raise DatabaseBackupError(
                "BACKUP_MANIFEST_INVALID",
                f"Backup manifest is missing {error.args[0]!r}.",
            ) from error
        if fingerprint.quick_check != "ok":
            raise DatabaseBackupError(
                "BACKUP_MANIFEST_INVALID",
                "Backup manifest does not describe a healthy SQLite database.",
            )
        if fingerprint.integrity_check != "ok":
            raise DatabaseBackupError(
                "BACKUP_MANIFEST_INVALID",
                "Backup manifest does not contain a successful SQLite integrity check.",
            )
        if fingerprint.foreign_key_violations != 0:
            raise DatabaseBackupError(
                "BACKUP_MANIFEST_INVALID",
                "Backup manifest contains foreign-key violations.",
            )
        if set(fingerprint.table_counts) != set(fingerprint.table_sha256):
            raise DatabaseBackupError(
                "BACKUP_MANIFEST_INVALID",
                "Backup manifest table counts and hashes do not describe the same tables.",
            )
        if set(fingerprint.content_counts) != set(fingerprint.content_sha256):
            raise DatabaseBackupError(
                "BACKUP_MANIFEST_INVALID",
                "Backup manifest content counts and hashes do not describe the same datasets.",
            )
        return fingerprint


@dataclass(frozen=True)
class BackupManifest:
    format_version: int
    backup_id: str
    label: str
    created_at: str
    source_database: str
    backup_file: str
    source_journal_mode: str
    sqlite_version: str
    backup_size_bytes: int
    backup_sha256: str
    database: DatabaseFingerprint
    manifest_sha256: str

    def payload_dict(self) -> dict[str, Any]:
        return {
            "formatVersion": self.format_version,
            "backupId": self.backup_id,
            "label": self.label,
            "createdAt": self.created_at,
            "sourceDatabase": self.source_database,
            "backupFile": self.backup_file,
            "sourceJournalMode": self.source_journal_mode,
            "sqliteVersion": self.sqlite_version,
            "backupSizeBytes": self.backup_size_bytes,
            "backupSha256": self.backup_sha256,
            "database": self.database.to_dict(),
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            **self.payload_dict(),
            "manifestSha256": self.manifest_sha256,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "BackupManifest":
        _reject_unknown_fields(
            value,
            allowed_fields=_MANIFEST_FIELDS,
            description="Backup manifest",
        )
        try:
            format_version = _required_int(value, "formatVersion")
            backup_id = _required_string(value, "backupId")
            if _BACKUP_ID_PATTERN.fullmatch(backup_id) is None:
                raise DatabaseBackupError(
                    "BACKUP_MANIFEST_INVALID",
                    "Backup manifest field 'backupId' must be a 32-character "
                    "lowercase hexadecimal identifier.",
                )
            label = _required_string(value, "label")
            if _LABEL_PATTERN.fullmatch(label) is None:
                raise DatabaseBackupError(
                    "BACKUP_MANIFEST_INVALID",
                    "Backup manifest contains an invalid audit label.",
                )
            backup_file = _required_string(value, "backupFile")
            if (
                "/" in backup_file
                or "\\" in backup_file
                or not backup_file.endswith(f"-{backup_id[:12]}.sqlite3")
            ):
                raise DatabaseBackupError(
                    "BACKUP_MANIFEST_INVALID",
                    "Backup manifest filename is not bound to its backup identifier.",
                )
            created_at = _required_string(value, "createdAt")
            if re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", created_at) is None:
                raise DatabaseBackupError(
                    "BACKUP_MANIFEST_INVALID",
                    "Backup manifest contains an invalid UTC creation timestamp.",
                )
            manifest = cls(
                format_version=format_version,
                backup_id=backup_id,
                label=label,
                created_at=created_at,
                source_database=_required_string(value, "sourceDatabase"),
                backup_file=backup_file,
                source_journal_mode=_required_string(value, "sourceJournalMode"),
                sqlite_version=_required_string(value, "sqliteVersion"),
                backup_size_bytes=_required_int(value, "backupSizeBytes"),
                backup_sha256=_required_sha256(value, "backupSha256"),
                database=DatabaseFingerprint.from_dict(_required_mapping(value, "database")),
                manifest_sha256=_required_sha256(value, "manifestSha256"),
            )
        except KeyError as error:
            raise DatabaseBackupError(
                "BACKUP_MANIFEST_INVALID",
                f"Backup manifest is missing {error.args[0]!r}.",
            ) from error
        if manifest.format_version not in SUPPORTED_BACKUP_FORMAT_VERSIONS:
            raise DatabaseBackupError(
                "BACKUP_FORMAT_UNSUPPORTED",
                f"Backup format {manifest.format_version} is not supported.",
            )
        if manifest.backup_size_bytes <= 0:
            raise DatabaseBackupError(
                "BACKUP_MANIFEST_INVALID",
                "Backup manifest contains an invalid file size.",
            )
        if _manifest_payload_sha256(manifest) != manifest.manifest_sha256:
            raise DatabaseBackupError(
                "BACKUP_MANIFEST_MISMATCH",
                "Backup manifest metadata does not match its integrity hash.",
            )
        return manifest


@dataclass(frozen=True)
class BackupResult:
    backup_path: Path
    manifest_path: Path
    manifest: BackupManifest
    verification: VerificationReport


@dataclass(frozen=True)
class VerificationReport:
    valid: bool
    format_version: int
    backup_id: str
    backup_sha256: str
    manifest_sha256: str
    manifest_file_sha256: str
    logical_sha256: str
    table_counts: Mapping[str, int]
    table_sha256: Mapping[str, str]
    content_counts: Mapping[str, int]
    content_sha256: Mapping[str, str]
    restored_path: Path | None = None


@dataclass(frozen=True)
class OriginReceiptReport:
    valid: bool
    schema_version: int
    manifest_kind: str
    backup_id: str
    backup_path: Path
    backup_sha256: str
    manifest_path: Path
    manifest_sha256: str
    logical_sha256: str
    database_lineage_id: str
    receipt_path: Path
    created_at: str
    receipt_sha256: str
    origin_receipt_file_sha256: str


@dataclass(frozen=True)
class OriginReceiptEnvelopeReport:
    """Canonical receipt bytes verified independently of their mounted path."""

    receipt_path: Path
    backup_id: str
    backup_sha256: str
    manifest_sha256: str
    logical_sha256: str
    database_lineage_id: str
    receipt_sha256: str
    origin_receipt_file_sha256: str


@dataclass(frozen=True)
class _BoundDestination:
    bound_path: Path
    report_path: Path
    identity: _FileIdentity
    size_bytes: int
    sha256: str


@dataclass
class _PublishedFile:
    identity: _FileIdentity
    windows_handle: Any | None = None

    def close(self) -> None:
        if self.windows_handle is not None:
            self.windows_handle.close()
            self.windows_handle = None


class _OwnedExclusiveFile:
    """An exclusively-created file whose ownership is bound before its first write."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.handle: Any | None = None
        self.identity: _FileIdentity | None = None

    def __enter__(self) -> "_OwnedExclusiveFile":
        descriptor = os.open(
            self.path,
            os.O_CREAT
            | os.O_EXCL
            | os.O_RDWR
            | getattr(os, "O_BINARY", 0),
            0o600,
        )
        try:
            self.handle = os.fdopen(descriptor, "w+b")
        except Exception:
            os.close(descriptor)
            raise
        try:
            self.handle.__enter__()
            self.identity = _identity_from_stat(os.fstat(self.handle.fileno()))
        except Exception:
            self.handle.close()
            self.handle = None
            raise
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        if self.handle is not None:
            self.handle.__exit__(exc_type, exc, traceback)

    def write(self, payload: bytes) -> None:
        if self.handle is None:
            raise RuntimeError("exclusive file is not open")
        self.handle.write(payload)

    def flush_and_sync(self) -> None:
        if self.handle is None:
            raise RuntimeError("exclusive file is not open")
        self.handle.flush()
        os.fsync(self.handle.fileno())


def _assert_owner_only_directory_mode(mode: int) -> None:
    if stat.S_IMODE(mode) & 0o077:
        raise DatabaseBackupError(
            "BACKUP_OUTPUT_DIRECTORY_PERMISSIONS",
            "Backup output directory must not grant group or world access.",
        )


class BoundValidationDirectory:
    path: Path

    def copy_verified_database(self, source: Path, name: str) -> None:
        raise NotImplementedError

    def verify_destination(self, name: str) -> _BoundDestination:
        raise NotImplementedError

    def close(self) -> None:
        raise NotImplementedError

    def _abort(self) -> None:
        raise NotImplementedError

    def _close_bound_handles(self) -> None:
        raise NotImplementedError


class BoundRestoreRoot:
    def create_validation_directory(self, prefix: str) -> BoundValidationDirectory:
        raise NotImplementedError

    def close(self) -> None:
        raise NotImplementedError

    def _abort(self) -> None:
        raise NotImplementedError


class BoundRootPlatform(Protocol):
    """Platform operations required by a bound restore rehearsal."""

    def is_supported(self) -> bool: ...

    def open_restore_root(self, path: Path) -> BoundRestoreRoot: ...

    def fingerprint_bound_restore(
        self,
        destination: _BoundDestination,
        manifest: BackupManifest,
    ) -> DatabaseFingerprint: ...


class _NativeBoundRootPlatform:
    def is_supported(self) -> bool:
        return os.name == "nt" or _posix_bound_restore_supported()

    def open_restore_root(self, path: Path) -> BoundRestoreRoot:
        return open_bound_restore_root(path)

    def fingerprint_bound_restore(
        self,
        destination: _BoundDestination,
        manifest: BackupManifest,
    ) -> DatabaseFingerprint:
        return _fingerprint_bound_restore(destination, manifest)


_NATIVE_BOUND_ROOT_PLATFORM = _NativeBoundRootPlatform()


def _validate_bound_child_name(name: str) -> str:
    if not name or Path(name).name != name or "/" in name or "\\" in name:
        raise DatabaseBackupError(
            "RESTORE_DESTINATION_INVALID",
            "Restore validation requires a plain generated child name.",
        )
    return name


def _sha256_descriptor(descriptor: int) -> str:
    hasher = hashlib.sha256()
    original_offset = os.lseek(descriptor, 0, os.SEEK_CUR)
    try:
        os.lseek(descriptor, 0, os.SEEK_SET)
        while True:
            chunk = os.read(descriptor, _COPY_BUFFER_SIZE)
            if not chunk:
                break
            hasher.update(chunk)
    finally:
        os.lseek(descriptor, original_offset, os.SEEK_SET)
    return hasher.hexdigest()


class _PosixBoundValidationDirectory(BoundValidationDirectory):
    def __init__(
        self,
        root: "_PosixBoundRestoreRoot",
        name: str,
        descriptor: int,
        identity: _FileIdentity,
    ) -> None:
        self._root = root
        self._name = name
        self._descriptor = descriptor
        self._identity = identity
        self._destination_descriptors: dict[str, int] = {}
        self._destination_identities: dict[str, _FileIdentity] = {}
        self._closed = False
        self.path = root.path / name

    def copy_verified_database(self, source: Path, name: str) -> None:
        destination_name = _validate_bound_child_name(name)
        flags = os.O_CREAT | os.O_EXCL | os.O_RDWR | os.O_NOFOLLOW
        descriptor: int | None = None
        try:
            descriptor = os.open(destination_name, flags, 0o600, dir_fd=self._descriptor)
            identity = _identity_from_stat(os.fstat(descriptor))
            self._destination_descriptors[destination_name] = descriptor
            self._destination_identities[destination_name] = identity
            with source.open("rb") as source_handle:
                while True:
                    chunk = source_handle.read(_COPY_BUFFER_SIZE)
                    if not chunk:
                        break
                    view = memoryview(chunk)
                    while view:
                        written = os.write(descriptor, view)
                        view = view[written:]
            os.fsync(descriptor)
        except Exception:
            if descriptor is not None and destination_name not in self._destination_descriptors:
                os.close(descriptor)
            raise

    def verify_destination(self, name: str) -> _BoundDestination:
        destination_name = _validate_bound_child_name(name)
        descriptor = self._destination_descriptors[destination_name]
        expected_identity = self._destination_identities[destination_name]
        current_identity = _identity_from_stat(os.fstat(descriptor))
        if not _same_file_object(current_identity, expected_identity):
            raise DatabaseBackupError(
                "RESTORE_PUBLISH_OWNERSHIP_CHANGED",
                "The restored database identity changed during validation.",
            )
        for suffix in ("-wal", "-shm", "-journal"):
            try:
                os.stat(
                    f"{destination_name}{suffix}",
                    dir_fd=self._descriptor,
                    follow_symlinks=False,
                )
            except FileNotFoundError:
                continue
            raise DatabaseBackupError(
                "BACKUP_SIDECAR_PRESENT",
                "Generated SQLite database unexpectedly retained a sidecar.",
            )
        bound_path = Path(f"/proc/self/fd/{descriptor}")
        try:
            bound_path.resolve(strict=True)
        except OSError as error:
            raise DatabaseBackupError(
                "RESTORE_BOUND_DIRECTORY_UNSUPPORTED",
                "This POSIX platform cannot expose a descriptor-bound SQLite path.",
            ) from error
        return _BoundDestination(
            bound_path=bound_path,
            report_path=self.path / destination_name,
            identity=current_identity,
            size_bytes=int(os.fstat(descriptor).st_size),
            sha256=_sha256_descriptor(descriptor),
        )

    def close(self) -> None:
        for descriptor in self._destination_descriptors.values():
            os.close(descriptor)
        self._destination_descriptors.clear()

    def _close_bound_handles(self) -> None:
        if self._closed:
            return
        self.close()
        os.close(self._descriptor)
        self._closed = True

    def _abort(self) -> None:
        if self._closed:
            return
        for name, descriptor in list(self._destination_descriptors.items()):
            expected_identity = self._destination_identities[name]
            try:
                descriptor_identity = _identity_from_stat(os.fstat(descriptor))
                path_identity = _identity_from_stat(
                    os.stat(name, dir_fd=self._descriptor, follow_symlinks=False)
                )
                if _same_file_object(descriptor_identity, expected_identity) and _same_file_object(
                    path_identity,
                    expected_identity,
                ):
                    os.close(descriptor)
                    del self._destination_descriptors[name]
                    os.unlink(name, dir_fd=self._descriptor)
            except (FileNotFoundError, OSError):
                continue
        self.close()
        try:
            current_identity = _identity_from_stat(os.fstat(self._descriptor))
            parent_identity = _identity_from_stat(
                os.stat(self._name, dir_fd=self._root._descriptor, follow_symlinks=False)
            )
            if _same_file_object(current_identity, self._identity) and _same_file_object(
                parent_identity,
                self._identity,
            ):
                os.close(self._descriptor)
                self._closed = True
                os.rmdir(self._name, dir_fd=self._root._descriptor)
                return
        except (FileNotFoundError, OSError):
            pass
        os.close(self._descriptor)
        self._closed = True


class _PosixBoundRestoreRoot(BoundRestoreRoot):
    def __init__(self, path: Path, descriptor: int, identity: _FileIdentity) -> None:
        self.path = path
        self._descriptor = descriptor
        self._identity = identity
        self._children: list[_PosixBoundValidationDirectory] = []
        self._closed = False

    def create_validation_directory(self, prefix: str) -> BoundValidationDirectory:
        name = _validate_bound_child_name(f"{prefix}{uuid.uuid4().hex}")
        os.mkdir(name, mode=0o700, dir_fd=self._descriptor)
        descriptor: int | None = None
        identity: _FileIdentity | None = None
        try:
            flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
            descriptor = os.open(name, flags, dir_fd=self._descriptor)
            metadata = os.fstat(descriptor)
            if not stat.S_ISDIR(metadata.st_mode):
                raise DatabaseBackupError(
                    "RESTORE_OUTPUT_DIRECTORY_INVALID",
                    "Generated restore validation child is not a physical directory.",
                )
            identity = _identity_from_stat(metadata)
            child = _PosixBoundValidationDirectory(
                self,
                name,
                descriptor,
                identity,
            )
            self._children.append(child)
            return child
        except Exception:
            if descriptor is not None:
                try:
                    current_identity = _identity_from_stat(os.fstat(descriptor))
                    path_identity = _identity_from_stat(
                        os.stat(name, dir_fd=self._descriptor, follow_symlinks=False)
                    )
                    owned = (
                        identity is not None
                        and _same_file_object(current_identity, identity)
                        and _same_file_object(path_identity, identity)
                    )
                except OSError:
                    owned = False
                os.close(descriptor)
                if owned:
                    try:
                        os.rmdir(name, dir_fd=self._descriptor)
                    except OSError:
                        pass
            raise

    def _assert_current_path(self) -> None:
        try:
            current_identity = _read_directory_identity(self.path)
        except (FileNotFoundError, DatabaseBackupError) as error:
            raise DatabaseBackupError(
                "RESTORE_OUTPUT_DIRECTORY_CHANGED",
                "Restore validation output ownership changed during validation.",
            ) from error
        if not _same_file_object(current_identity, self._identity):
            raise DatabaseBackupError(
                "RESTORE_OUTPUT_DIRECTORY_CHANGED",
                "Restore validation output ownership changed during validation.",
            )

    def close(self) -> None:
        if self._closed:
            return
        try:
            self._assert_current_path()
        except DatabaseBackupError:
            for child in self._children:
                child._abort()
            os.close(self._descriptor)
            self._closed = True
            raise
        for child in self._children:
            child._close_bound_handles()
        os.close(self._descriptor)
        self._closed = True

    def _abort(self) -> None:
        if self._closed:
            return
        for child in self._children:
            child._abort()
        os.close(self._descriptor)
        self._closed = True


def _windows_directory_handle_identity(raw_handle: int) -> tuple[_FileIdentity, int]:
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

    get_information = ctypes.WinDLL("kernel32", use_last_error=True).GetFileInformationByHandle
    get_information.argtypes = (wintypes.HANDLE, ctypes.POINTER(ByHandleFileInformation))
    get_information.restype = wintypes.BOOL
    information = ByHandleFileInformation()
    if not get_information(raw_handle, ctypes.byref(information)):
        raise OSError(ctypes.get_last_error(), "GetFileInformationByHandle failed")
    identity = _FileIdentity(
        device=int(information.volume_serial_number),
        inode=(int(information.file_index_high) << 32) | int(information.file_index_low),
        size=(int(information.file_size_high) << 32) | int(information.file_size_low),
        modified_ns=((int(information.write_time.high) << 32) | int(information.write_time.low)) * 100,
        changed_ns=0,
    )
    return identity, int(information.attributes)


def _open_windows_directory_handle(
    path: Path,
    *,
    protect_delete: bool = False,
    compatible_with_protected_handle: bool = False,
) -> tuple[int, _FileIdentity]:
    import ctypes
    from ctypes import wintypes

    read_attributes = 0x00000080
    delete_access = 0x00010000
    file_add_file = 0x00000002
    file_add_subdirectory = 0x00000004
    file_traverse = 0x00000020
    share_read = 0x00000001
    share_write = 0x00000002
    share_delete = 0x00000004
    open_existing = 3
    open_reparse_point = 0x00200000
    backup_semantics = 0x02000000
    invalid_handle_value = ctypes.c_void_p(-1).value
    create_file = ctypes.WinDLL("kernel32", use_last_error=True).CreateFileW
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
    raw_handle = create_file(
        str(path),
        read_attributes
        | (
            delete_access | file_add_file | file_add_subdirectory | file_traverse
            if protect_delete
            else 0
        ),
        share_read
        | share_write
        | (share_delete if compatible_with_protected_handle else 0),
        None,
        open_existing,
        open_reparse_point | backup_semantics,
        None,
    )
    if raw_handle == invalid_handle_value:
        raise OSError(ctypes.get_last_error(), "CreateFileW failed")
    try:
        identity, attributes = _windows_directory_handle_identity(raw_handle)
        reparse_attribute = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
        if attributes & reparse_attribute:
            raise DatabaseBackupError(
                "RESTORE_OUTPUT_DIRECTORY_INVALID",
                "Restore validation output must be a physical, non-reparse directory.",
            )
        return int(raw_handle), identity
    except Exception:
        ctypes.WinDLL("kernel32", use_last_error=True).CloseHandle(raw_handle)
        raise


def _close_windows_handle(raw_handle: int) -> None:
    import ctypes
    from ctypes import wintypes

    close_handle = ctypes.WinDLL("kernel32", use_last_error=True).CloseHandle
    close_handle.argtypes = (wintypes.HANDLE,)
    close_handle.restype = wintypes.BOOL
    close_handle(raw_handle)


def _nt_create_windows_relative(
    parent_handle: int,
    name: str,
    *,
    desired_access: int,
    file_attributes: int,
    share_access: int,
    create_options: int,
) -> int:
    import ctypes
    from ctypes import wintypes

    class UnicodeString(ctypes.Structure):
        _fields_ = (
            ("length", wintypes.USHORT),
            ("maximum_length", wintypes.USHORT),
            ("buffer", wintypes.LPWSTR),
        )

    class ObjectAttributes(ctypes.Structure):
        _fields_ = (
            ("length", wintypes.ULONG),
            ("root_directory", wintypes.HANDLE),
            ("object_name", ctypes.POINTER(UnicodeString)),
            ("attributes", wintypes.ULONG),
            ("security_descriptor", wintypes.LPVOID),
            ("security_quality_of_service", wintypes.LPVOID),
        )

    class IoStatusBlock(ctypes.Structure):
        _fields_ = (
            ("status", wintypes.LPVOID),
            ("information", ctypes.c_size_t),
        )

    file_create = 2
    object_case_insensitive = 0x00000040
    encoded_name = name.encode("utf-16-le")
    name_buffer = ctypes.create_unicode_buffer(name)
    unicode_name = UnicodeString(
        len(encoded_name),
        len(encoded_name) + 2,
        ctypes.cast(name_buffer, wintypes.LPWSTR),
    )
    object_attributes = ObjectAttributes(
        ctypes.sizeof(ObjectAttributes),
        wintypes.HANDLE(parent_handle),
        ctypes.pointer(unicode_name),
        object_case_insensitive,
        None,
        None,
    )
    io_status = IoStatusBlock()
    raw_handle = wintypes.HANDLE()
    ntdll = ctypes.WinDLL("ntdll", use_last_error=True)
    nt_create_file = ntdll.NtCreateFile
    nt_create_file.argtypes = (
        ctypes.POINTER(wintypes.HANDLE),
        wintypes.DWORD,
        ctypes.POINTER(ObjectAttributes),
        ctypes.POINTER(IoStatusBlock),
        wintypes.LPVOID,
        wintypes.ULONG,
        wintypes.ULONG,
        wintypes.ULONG,
        wintypes.ULONG,
        wintypes.LPVOID,
        wintypes.ULONG,
    )
    nt_create_file.restype = ctypes.c_long
    status = int(
        nt_create_file(
            ctypes.byref(raw_handle),
            desired_access,
            ctypes.byref(object_attributes),
            ctypes.byref(io_status),
            None,
            file_attributes,
            share_access,
            file_create,
            create_options,
            None,
            0,
        )
    )
    if status < 0:
        rtl_status_to_dos_error = ntdll.RtlNtStatusToDosError
        rtl_status_to_dos_error.argtypes = (ctypes.c_long,)
        rtl_status_to_dos_error.restype = wintypes.ULONG
        raise OSError(
            int(rtl_status_to_dos_error(status)),
            "NtCreateFile failed",
        )
    if raw_handle.value is None:
        raise OSError("NtCreateFile returned an invalid handle")
    return int(raw_handle.value)


def _create_windows_bound_directory(
    parent_handle: int,
    name: str,
) -> tuple[int, _FileIdentity]:
    read_attributes = 0x00000080
    delete_access = 0x00010000
    file_add_file = 0x00000002
    file_add_subdirectory = 0x00000004
    file_traverse = 0x00000020
    synchronize = 0x00100000
    share_read = 0x00000001
    share_write = 0x00000002
    directory_attribute = 0x00000010
    file_directory_file = 0x00000001
    file_synchronous_io_nonalert = 0x00000020
    file_open_reparse_point = 0x00200000
    raw_handle = _nt_create_windows_relative(
        parent_handle,
        name,
        desired_access=(
            read_attributes
            | delete_access
            | file_add_file
            | file_add_subdirectory
            | file_traverse
            | synchronize
        ),
        file_attributes=directory_attribute,
        share_access=share_read | share_write,
        create_options=(
            file_directory_file
            | file_synchronous_io_nonalert
            | file_open_reparse_point
        ),
    )
    try:
        identity, attributes = _windows_directory_handle_identity(raw_handle)
        reparse_attribute = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
        directory_attribute = getattr(stat, "FILE_ATTRIBUTE_DIRECTORY", 0x10)
        if attributes & reparse_attribute or not attributes & directory_attribute:
            raise DatabaseBackupError(
                "RESTORE_OUTPUT_DIRECTORY_INVALID",
                "Generated restore validation child is not a physical directory.",
            )
        return raw_handle, identity
    except Exception:
        _best_effort_cleanup(
            _set_windows_handle_delete_disposition,
            raw_handle,
            strict=False,
            failure_code="BACKUP_STAGING_CLEANUP_FAILED",
            failure_message="Could not safely remove the bound restore directory.",
        )
        _close_windows_handle(raw_handle)
        raise


def _create_windows_owned_exclusive_file(
    parent_handle: int,
    name: str,
) -> tuple[Any, _FileIdentity]:
    import msvcrt

    generic_read = 0x80000000
    generic_write = 0x40000000
    delete_access = 0x00010000
    synchronize = 0x00100000
    share_read = 0x00000001
    share_write = 0x00000002
    normal_attributes = 0x00000080
    file_non_directory_file = 0x00000040
    file_synchronous_io_nonalert = 0x00000020
    file_open_reparse_point = 0x00200000
    raw_handle = _nt_create_windows_relative(
        parent_handle,
        name,
        desired_access=generic_read | generic_write | delete_access | synchronize,
        file_attributes=normal_attributes,
        share_access=share_read | share_write,
        create_options=(
            file_non_directory_file
            | file_synchronous_io_nonalert
            | file_open_reparse_point
        ),
    )
    descriptor: int | None = None
    handle: Any | None = None
    try:
        descriptor = msvcrt.open_osfhandle(
            raw_handle,
            os.O_RDWR | getattr(os, "O_BINARY", 0),
        )
        handle = os.fdopen(descriptor, "r+b", buffering=0)
        descriptor = None
        identity = _identity_from_stat(os.fstat(handle.fileno()))
        return handle, identity
    except Exception:
        if handle is not None:
            _best_effort_cleanup(
                _set_windows_handle_delete_disposition,
                int(msvcrt.get_osfhandle(handle.fileno())),
                strict=False,
                failure_code="BACKUP_CLEANUP_FAILED",
                failure_message="Could not safely remove the bound restore file.",
            )
            handle.close()
        elif descriptor is not None:
            _best_effort_cleanup(
                _set_windows_handle_delete_disposition,
                int(msvcrt.get_osfhandle(descriptor)),
                strict=False,
                failure_code="BACKUP_CLEANUP_FAILED",
                failure_message="Could not safely remove the bound restore file.",
            )
            os.close(descriptor)
        else:
            _best_effort_cleanup(
                _set_windows_handle_delete_disposition,
                raw_handle,
                strict=False,
                failure_code="BACKUP_CLEANUP_FAILED",
                failure_message="Could not safely remove the bound restore file.",
            )
            _close_windows_handle(raw_handle)
        raise


def _set_windows_handle_delete_disposition(
    raw_handle: int,
    *,
    strict: bool,
    failure_code: str,
    failure_message: str,
) -> bool:
    import ctypes
    from ctypes import wintypes

    file_disposition_info_class = 4

    class FileDispositionInfo(ctypes.Structure):
        _fields_ = [("delete_file", wintypes.BOOL)]

    set_file_information = ctypes.WinDLL(
        "kernel32",
        use_last_error=True,
    ).SetFileInformationByHandle
    set_file_information.argtypes = (
        wintypes.HANDLE,
        ctypes.c_int,
        wintypes.LPVOID,
        wintypes.DWORD,
    )
    set_file_information.restype = wintypes.BOOL
    disposition = FileDispositionInfo(True)
    if set_file_information(
        wintypes.HANDLE(raw_handle),
        file_disposition_info_class,
        ctypes.byref(disposition),
        ctypes.sizeof(disposition),
    ):
        return True
    if strict:
        raise DatabaseBackupError(failure_code, failure_message)
    return False


def _mark_windows_file_handle_for_deletion(
    handle: Any,
    *,
    expected_identity: _FileIdentity,
    strict: bool,
) -> bool:
    import msvcrt

    current_identity = _identity_from_stat(os.fstat(handle.fileno()))
    if not _same_file_object(current_identity, expected_identity):
        if strict:
            raise DatabaseBackupError(
                "BACKUP_CLEANUP_OWNERSHIP_CHANGED",
                "Refusing to remove a replaced bound restore file.",
            )
        return False
    return _set_windows_handle_delete_disposition(
        int(msvcrt.get_osfhandle(handle.fileno())),
        strict=strict,
        failure_code="BACKUP_CLEANUP_FAILED",
        failure_message="Could not safely remove the bound restore file.",
    )


def _mark_windows_directory_handle_for_deletion(
    raw_handle: int,
    *,
    expected_identity: _FileIdentity,
    strict: bool,
) -> bool:
    try:
        current_identity, attributes = _windows_directory_handle_identity(raw_handle)
    except OSError as error:
        if strict:
            raise DatabaseBackupError(
                "BACKUP_STAGING_CLEANUP_FAILED",
                "Could not inspect the bound restore directory for safe removal.",
            ) from error
        return False
    reparse_attribute = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    if (
        attributes & reparse_attribute
        or not _same_file_object(current_identity, expected_identity)
    ):
        if strict:
            raise DatabaseBackupError(
                "BACKUP_STAGING_OWNERSHIP_CHANGED",
                "Refusing to remove a replaced bound restore directory.",
            )
        return False
    return _set_windows_handle_delete_disposition(
        raw_handle,
        strict=strict,
        failure_code="BACKUP_STAGING_CLEANUP_FAILED",
        failure_message="Could not safely remove the bound restore directory.",
    )


class _WindowsBoundValidationDirectory(BoundValidationDirectory):
    def __init__(
        self,
        root: "_WindowsBoundRestoreRoot",
        name: str,
        raw_handle: int,
        identity: _FileIdentity,
    ) -> None:
        self._root = root
        self._name = name
        self._raw_handle = raw_handle
        self._identity = identity
        self._destination_handles: dict[str, Any] = {}
        self._destination_identities: dict[str, _FileIdentity] = {}
        self._closed = False
        self.path = root.path / name

    def copy_verified_database(self, source: Path, name: str) -> None:
        destination_name = _validate_bound_child_name(name)
        destination, identity = _create_windows_owned_exclusive_file(
            self._raw_handle,
            destination_name,
        )
        self._destination_handles[destination_name] = destination
        try:
            self._destination_identities[destination_name] = identity
            with source.open("rb") as source_handle:
                while True:
                    chunk = source_handle.read(_COPY_BUFFER_SIZE)
                    if not chunk:
                        break
                    destination.write(chunk)
            destination.flush()
            os.fsync(destination.fileno())
        except Exception:
            raise

    def verify_destination(self, name: str) -> _BoundDestination:
        destination_name = _validate_bound_child_name(name)
        destination_path = self.path / destination_name
        destination_handle = self._destination_handles[destination_name]
        expected_identity = self._destination_identities[destination_name]
        handle_identity = _identity_from_stat(os.fstat(destination_handle.fileno()))
        path_identity = _read_file_identity(destination_path)
        if not _same_file_object(handle_identity, expected_identity) or not _same_file_object(
            path_identity,
            expected_identity,
        ):
            raise DatabaseBackupError(
                "RESTORE_PUBLISH_OWNERSHIP_CHANGED",
                "The restored database was replaced during validation.",
            )
        _assert_database_has_no_sidecars(destination_path)
        return _BoundDestination(
            bound_path=destination_path,
            report_path=destination_path,
            identity=handle_identity,
            size_bytes=int(os.fstat(destination_handle.fileno()).st_size),
            sha256=_sha256_descriptor(destination_handle.fileno()),
        )

    def close(self) -> None:
        for handle in self._destination_handles.values():
            handle.close()
        self._destination_handles.clear()

    def _close_bound_handles(self) -> None:
        if self._closed:
            return
        self.close()
        _close_windows_handle(self._raw_handle)
        self._closed = True

    def _abort(self) -> None:
        if self._closed:
            return
        for name, expected_identity in self._destination_identities.items():
            handle = self._destination_handles.get(name)
            if handle is None:
                continue
            try:
                _mark_windows_file_handle_for_deletion(
                    handle,
                    expected_identity=expected_identity,
                    strict=False,
                )
            except (OSError, DatabaseBackupError):
                continue
        self.close()
        _best_effort_cleanup(
            _mark_windows_directory_handle_for_deletion,
            self._raw_handle,
            expected_identity=self._identity,
            strict=False,
        )
        _close_windows_handle(self._raw_handle)
        self._closed = True


class _WindowsBoundRestoreRoot(BoundRestoreRoot):
    def __init__(self, path: Path, raw_handle: int, identity: _FileIdentity) -> None:
        self.path = path
        self._raw_handle = raw_handle
        self._identity = identity
        self._children: list[_WindowsBoundValidationDirectory] = []
        self._closed = False

    def create_validation_directory(self, prefix: str) -> BoundValidationDirectory:
        name = _validate_bound_child_name(f"{prefix}{uuid.uuid4().hex}")
        raw_handle: int | None = None
        identity: _FileIdentity | None = None
        try:
            raw_handle, identity = _create_windows_bound_directory(
                self._raw_handle,
                name,
            )
            child = _WindowsBoundValidationDirectory(self, name, raw_handle, identity)
            self._children.append(child)
            return child
        except Exception:
            if raw_handle is not None:
                if identity is not None:
                    _best_effort_cleanup(
                        _mark_windows_directory_handle_for_deletion,
                        raw_handle,
                        expected_identity=identity,
                        strict=False,
                    )
                _close_windows_handle(raw_handle)
            raise

    def _assert_current_path(self) -> None:
        probe_handle: int | None = None
        try:
            _read_directory_identity(self.path)
            probe_handle, identity = _open_windows_directory_handle(
                self.path,
                compatible_with_protected_handle=True,
            )
            if not _same_file_object(identity, self._identity):
                raise DatabaseBackupError(
                    "RESTORE_OUTPUT_DIRECTORY_CHANGED",
                    "Restore validation output ownership changed during validation.",
                )
        except (OSError, FileNotFoundError, DatabaseBackupError) as error:
            if isinstance(error, DatabaseBackupError) and error.code == "RESTORE_OUTPUT_DIRECTORY_CHANGED":
                raise
            raise DatabaseBackupError(
                "RESTORE_OUTPUT_DIRECTORY_CHANGED",
                "Restore validation output ownership changed during validation.",
            ) from error
        finally:
            if probe_handle is not None:
                _close_windows_handle(probe_handle)

    def close(self) -> None:
        if self._closed:
            return
        try:
            self._assert_current_path()
        except DatabaseBackupError:
            for child in self._children:
                child._abort()
            _close_windows_handle(self._raw_handle)
            self._closed = True
            raise
        for child in self._children:
            child._close_bound_handles()
        _close_windows_handle(self._raw_handle)
        self._closed = True

    def _abort(self) -> None:
        if self._closed:
            return
        for child in self._children:
            child._abort()
        _close_windows_handle(self._raw_handle)
        self._closed = True


def open_bound_restore_root(path: Path) -> BoundRestoreRoot:
    if os.name == "nt":
        raw_handle: int | None = None
        probe_handle: int | None = None
        try:
            raw_handle, identity = _open_windows_directory_handle(
                path,
                protect_delete=True,
            )
            _read_directory_identity(path)
            probe_handle, current_identity = _open_windows_directory_handle(
                path,
                compatible_with_protected_handle=True,
            )
            if not _same_file_object(current_identity, identity):
                raise DatabaseBackupError(
                    "RESTORE_OUTPUT_DIRECTORY_CHANGED",
                    "Restore validation output ownership changed while it was being bound.",
                )
            return _WindowsBoundRestoreRoot(path, raw_handle, identity)
        except DatabaseBackupError:
            if raw_handle is not None:
                _close_windows_handle(raw_handle)
            raise
        except OSError as error:
            if raw_handle is not None:
                _close_windows_handle(raw_handle)
            raise DatabaseBackupError(
                "RESTORE_BOUND_DIRECTORY_UNSUPPORTED",
                "Could not bind the restore validation output directory.",
            ) from error
        finally:
            if probe_handle is not None:
                _close_windows_handle(probe_handle)

    if not _posix_bound_restore_supported():
        raise DatabaseBackupError(
            "RESTORE_BOUND_DIRECTORY_UNSUPPORTED",
            "This platform cannot bind restore validation operations to a directory descriptor.",
        )
    descriptor: int | None = None
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISDIR(metadata.st_mode)
            or metadata.st_uid != os.geteuid()
            or stat.S_IMODE(metadata.st_mode) != 0o700
        ):
            raise DatabaseBackupError(
                "RESTORE_OUTPUT_DIRECTORY_PRIVATE_REQUIRED",
                "POSIX restore validation requires a private owner-only output directory.",
            )
        identity = _identity_from_stat(metadata)
        descriptor_path = Path(f"/proc/self/fd/{descriptor}")
        descriptor_path.resolve(strict=True)
        current_identity = _read_directory_identity(path)
        if not _same_file_object(current_identity, identity):
            raise DatabaseBackupError(
                "RESTORE_OUTPUT_DIRECTORY_CHANGED",
                "Restore validation output ownership changed while it was being bound.",
            )
        return _PosixBoundRestoreRoot(path, descriptor, identity)
    except DatabaseBackupError:
        if descriptor is not None:
            os.close(descriptor)
        raise
    except OSError as error:
        if descriptor is not None:
            os.close(descriptor)
        raise DatabaseBackupError(
            "RESTORE_BOUND_DIRECTORY_UNSUPPORTED",
            "This POSIX platform cannot expose a descriptor-bound SQLite path.",
        ) from error


def _posix_bound_restore_supported() -> bool:
    if os.name != "posix" or not hasattr(os, "O_DIRECTORY") or not hasattr(os, "O_NOFOLLOW"):
        return False
    required_dir_fd_operations = (os.mkdir, os.open, os.rmdir, os.stat, os.unlink)
    if any(operation not in os.supports_dir_fd for operation in required_dir_fd_operations):
        return False
    return Path("/proc/self/fd").is_dir()


@dataclass
class _CriticalContentHasher:
    name: str
    columns: tuple[str, ...]
    indexes: tuple[int, ...]
    filter_index: int | None
    filter_mode: str
    hasher: Any
    count: int = 0

    def add(self, row: Sequence[Any]) -> None:
        if self.filter_index is not None:
            filter_value = row[self.filter_index]
            if self.filter_mode == "nonblank":
                if not isinstance(filter_value, str) or not filter_value.strip():
                    return
            elif self.filter_mode == "nonnull" and filter_value is None:
                return
        _update_frame(self.hasher, b"row", b"")
        for index in self.indexes:
            tag, payload = _encode_sqlite_value(row[index])
            _update_frame(self.hasher, tag, payload)
        self.count += 1

    def digest(self) -> str:
        return self.hasher.hexdigest()


def inspect_database(database_path: str | os.PathLike[str]) -> DatabaseFingerprint:
    """Return a read-only fingerprint of an existing SQLite database."""

    resolved_path = Path(database_path).expanduser().resolve()
    _validate_source_database(resolved_path)
    return _fingerprint_database(resolved_path)


def create_verified_backup(
    source_database: str | os.PathLike[str],
    output_directory: str | os.PathLike[str],
    *,
    label: str = "manual",
) -> BackupResult:
    """Create, fingerprint, publish, and re-verify one consistent SQLite backup."""

    source_path = Path(source_database).expanduser().resolve()
    output_path = Path(output_directory).expanduser().resolve()
    safe_label = _validate_label(label)
    _validate_source_database(source_path)
    try:
        output_path.mkdir(mode=0o700, parents=True, exist_ok=True)
        output_directory_identity = _read_directory_identity(output_path)
    except DatabaseBackupError as error:
        raise DatabaseBackupError(
            "BACKUP_OUTPUT_DIRECTORY_FAILED",
            "Could not bind backup output directory ownership.",
        ) from error
    except OSError as error:
        raise DatabaseBackupError(
            "BACKUP_OUTPUT_DIRECTORY_FAILED",
            f"Could not create backup output directory: {error}",
        ) from error
    if os.name != "nt":
        _assert_owner_only_directory_mode(output_path.lstat().st_mode)

    backup_id = uuid.uuid4().hex
    now = datetime.now(timezone.utc)
    timestamp = now.strftime("%Y%m%dT%H%M%SZ")
    source_stem = _safe_filename_part(source_path.stem)
    backup_name = f"{source_stem}-{safe_label}-{timestamp}-{backup_id[:12]}.sqlite3"
    backup_path = output_path / backup_name
    manifest_path = output_path / f"{backup_name}.manifest.json"
    staging_directory = output_path / f".backup-stage-{backup_id}"
    temporary_backup = staging_directory / "backup.sqlite3"

    if backup_path == source_path or temporary_backup == source_path:
        raise DatabaseBackupError(
            "BACKUP_TARGET_INVALID",
            "Backup output must not resolve to the source database.",
        )

    published_backup: _PublishedFile | None = None
    published_manifest: _PublishedFile | None = None
    temporary_backup_identity: _FileIdentity | None = None
    staging_directory_identity: _FileIdentity | None = None
    temporary_backup_owned = False
    staging_directory_created = False
    try:
        staging_directory.mkdir(mode=0o700, exist_ok=False)
        staging_directory_created = True
        staging_directory_identity = _read_directory_identity(staging_directory)
        with closing(_open_readonly(source_path)) as source:
            source_journal_mode = str(source.execute("PRAGMA journal_mode").fetchone()[0]).lower()
            _assert_healthy_database(source, "source database")
            try:
                with _OwnedExclusiveFile(temporary_backup) as temporary_handle:
                    temporary_backup_owned = True
                    temporary_backup_identity = temporary_handle.identity
            except FileExistsError as error:
                raise DatabaseBackupError(
                    "BACKUP_STAGING_COLLISION",
                    "The private backup staging file already exists.",
                ) from error
            destination = sqlite3.connect(temporary_backup, timeout=30.0)
            try:
                source.backup(destination, pages=256, sleep=0.05)
                destination.commit()
                journal_mode_row = destination.execute(
                    "PRAGMA journal_mode = DELETE"
                ).fetchone()
                journal_mode = (
                    str(journal_mode_row[0]).lower()
                    if journal_mode_row is not None
                    else ""
                )
                if journal_mode != "delete":
                    raise DatabaseBackupError(
                        "BACKUP_JOURNAL_MODE_FAILED",
                        "Could not normalize the backup copy to DELETE journal mode.",
                    )
            finally:
                destination.close()

        _remove_database_sidecars(
            temporary_backup,
            owned_directory=staging_directory,
            expected_directory_identity=staging_directory_identity,
        )
        _fsync_file(temporary_backup)
        current_temporary_identity = _read_file_identity(temporary_backup)
        if temporary_backup_identity is None or not _same_file_object(
            current_temporary_identity,
            temporary_backup_identity,
        ):
            raise DatabaseBackupError(
                "BACKUP_STAGING_OWNERSHIP_CHANGED",
                "The private backup staging file was replaced during creation.",
            )
        temporary_backup_identity = current_temporary_identity
        database = _fingerprint_database(temporary_backup)
        published_backup = _publish_new_file(
            temporary_backup,
            backup_path,
            expected_identity=temporary_backup_identity,
            owned_directory=staging_directory,
            expected_directory_identity=staging_directory_identity,
        )
        temporary_backup_owned = False

        manifest = BackupManifest(
            format_version=BACKUP_FORMAT_VERSION,
            backup_id=backup_id,
            label=safe_label,
            created_at=now.isoformat(timespec="seconds").replace("+00:00", "Z"),
            source_database=str(source_path),
            backup_file=backup_path.name,
            source_journal_mode=source_journal_mode,
            sqlite_version=sqlite3.sqlite_version,
            backup_size_bytes=backup_path.stat().st_size,
            backup_sha256=_sha256_file(backup_path),
            database=database,
            manifest_sha256="0" * 64,
        )
        manifest = replace(
            manifest,
            manifest_sha256=_manifest_payload_sha256(manifest),
        )
        published_manifest = _write_manifest(
            manifest_path,
            manifest,
            staging_directory=staging_directory,
            staging_directory_identity=staging_directory_identity,
        )
        verification = verify_backup(backup_path, manifest_path)
        current_backup_identity = _read_file_identity(backup_path)
        if not _same_file_object(current_backup_identity, published_backup.identity):
            raise DatabaseBackupError(
                "BACKUP_PUBLISH_OWNERSHIP_CHANGED",
                "The published backup was replaced during verification.",
            )
        final_backup_size = backup_path.stat().st_size
        final_backup_sha256 = _sha256_file(backup_path)
        if (
            final_backup_size != manifest.backup_size_bytes
            or final_backup_sha256 != manifest.backup_sha256
        ):
            raise DatabaseBackupError(
                "BACKUP_FILE_CHANGED_AFTER_VERIFY",
                "The published backup contents changed after verification.",
            )
        current_manifest_identity = _read_file_identity(manifest_path)
        if not _same_file_object(current_manifest_identity, published_manifest.identity):
            raise DatabaseBackupError(
                "BACKUP_PUBLISH_OWNERSHIP_CHANGED",
                "The published backup manifest was replaced during verification.",
            )
        current_manifest, current_manifest_file_sha256 = _load_manifest_document(manifest_path)
        if (
            current_manifest != manifest
            or current_manifest_file_sha256 != verification.manifest_file_sha256
        ):
            raise DatabaseBackupError(
                "BACKUP_MANIFEST_CHANGED_AFTER_VERIFY",
                "The published backup manifest changed after verification.",
            )
        _assert_database_has_no_sidecars(backup_path)
        _remove_owned_staging_directory(
            staging_directory,
            strict=True,
            expected_identity=staging_directory_identity,
            owned_parent_directory=output_path,
            expected_parent_identity=output_directory_identity,
        )
        staging_directory_created = False
        published_manifest.close()
        published_backup.close()
        return BackupResult(
            backup_path=backup_path,
            manifest_path=manifest_path,
            manifest=manifest,
            verification=verification,
        )
    except DatabaseBackupError:
        if published_manifest is not None:
            _best_effort_cleanup(
                _cleanup_published_file,
                published_manifest,
                manifest_path,
                strict=False,
                owned_directory=output_path,
                expected_directory_identity=output_directory_identity,
            )
        if published_backup is not None:
            _best_effort_cleanup(
                _cleanup_published_file,
                published_backup,
                backup_path,
                strict=False,
                owned_directory=output_path,
                expected_directory_identity=output_directory_identity,
            )
        if temporary_backup_owned:
            _best_effort_cleanup(
                _unlink_owned_file,
                temporary_backup,
                strict=False,
                expected_identity=temporary_backup_identity,
                owned_directory=staging_directory,
                expected_directory_identity=staging_directory_identity,
            )
            _best_effort_cleanup(
                _remove_database_sidecars,
                temporary_backup,
                owned_directory=staging_directory,
                expected_directory_identity=staging_directory_identity,
                strict=False,
            )
        if staging_directory_created:
            _best_effort_cleanup(
                _remove_owned_staging_directory,
                staging_directory,
                strict=False,
                expected_identity=staging_directory_identity,
                owned_parent_directory=output_path,
                expected_parent_identity=output_directory_identity,
            )
        raise
    except Exception as error:
        if published_manifest is not None:
            _best_effort_cleanup(
                _cleanup_published_file,
                published_manifest,
                manifest_path,
                strict=False,
                owned_directory=output_path,
                expected_directory_identity=output_directory_identity,
            )
        if published_backup is not None:
            _best_effort_cleanup(
                _cleanup_published_file,
                published_backup,
                backup_path,
                strict=False,
                owned_directory=output_path,
                expected_directory_identity=output_directory_identity,
            )
        if temporary_backup_owned:
            _best_effort_cleanup(
                _unlink_owned_file,
                temporary_backup,
                strict=False,
                expected_identity=temporary_backup_identity,
                owned_directory=staging_directory,
                expected_directory_identity=staging_directory_identity,
            )
            _best_effort_cleanup(
                _remove_database_sidecars,
                temporary_backup,
                owned_directory=staging_directory,
                expected_directory_identity=staging_directory_identity,
                strict=False,
            )
        if staging_directory_created:
            _best_effort_cleanup(
                _remove_owned_staging_directory,
                staging_directory,
                strict=False,
                expected_identity=staging_directory_identity,
                owned_parent_directory=output_path,
                expected_parent_identity=output_directory_identity,
            )
        raise DatabaseBackupError(
            "BACKUP_CREATE_FAILED",
            f"Could not create a verified SQLite backup: {error}",
        ) from error


def verify_backup(
    backup_database: str | os.PathLike[str],
    manifest_file: str | os.PathLike[str],
) -> VerificationReport:
    """Verify file integrity and the complete logical fingerprint in a manifest."""

    backup_path = Path(backup_database).expanduser().resolve()
    manifest_path = Path(manifest_file).expanduser().resolve()
    manifest, manifest_file_sha256 = _load_manifest_document(manifest_path)
    return _verify_backup_against_manifest(
        backup_path,
        manifest,
        manifest_file_sha256=manifest_file_sha256,
    )


def seal_origin_receipt(
    backup_database: str | os.PathLike[str],
    manifest_file: str | os.PathLike[str],
    receipt_file: str | os.PathLike[str],
) -> OriginReceiptReport:
    """Exclusively seal one verified backup/Manifest pair as the P0 origin."""

    backup_path = Path(backup_database).expanduser().resolve()
    manifest_path = Path(manifest_file).expanduser().resolve()
    receipt_path = Path(receipt_file).expanduser().resolve()
    if receipt_path.name != ORIGIN_RECEIPT_FILENAME:
        raise DatabaseBackupError(
            "ORIGIN_RECEIPT_PATH_INVALID",
            "The P0 origin receipt must use its fixed versioned filename.",
        )
    try:
        verification = verify_backup(backup_path, manifest_path)
    except DatabaseBackupError as error:
        raise DatabaseBackupError(
            "ORIGIN_RECEIPT_REFERENCE_INVALID",
            "Could not verify the exact backup and Manifest for the P0 origin receipt.",
        ) from error
    created_at = datetime.now(timezone.utc).isoformat(timespec="seconds").replace(
        "+00:00",
        "Z",
    )
    lineage_document = {
        "version": 1,
        "originBackupId": verification.backup_id,
        "originManifestSha256": verification.manifest_file_sha256,
        "originLogicalSha256": verification.logical_sha256,
    }
    database_lineage_id = hashlib.sha256(
        _canonical_json_bytes(lineage_document)
    ).hexdigest()
    unsigned_document = {
        "schemaVersion": ORIGIN_RECEIPT_SCHEMA_VERSION,
        "manifestKind": ORIGIN_RECEIPT_MANIFEST_KIND,
        "backupId": verification.backup_id,
        "backupPath": str(backup_path),
        "backupSha256": verification.backup_sha256,
        "manifestPath": str(manifest_path),
        "manifestSha256": verification.manifest_file_sha256,
        "logicalSha256": verification.logical_sha256,
        "databaseLineageId": database_lineage_id,
        "receiptPath": str(receipt_path),
        "createdAt": created_at,
    }
    receipt_sha256 = hashlib.sha256(
        _canonical_json_bytes(unsigned_document)
    ).hexdigest()
    document = {**unsigned_document, "receiptSha256": receipt_sha256}
    payload = _canonical_json_bytes(document)
    expected_file_sha256 = hashlib.sha256(payload).hexdigest()
    receipt_directory = receipt_path.parent
    receipt_identity: _FileIdentity | None = None
    directory_identity: _FileIdentity | None = None
    try:
        receipt_directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        directory_identity = _read_directory_identity(receipt_directory)
        with _OwnedExclusiveFile(receipt_path) as owned_receipt:
            receipt_identity = owned_receipt.identity
            if receipt_identity is None:
                raise DatabaseBackupError(
                    "ORIGIN_RECEIPT_WRITE_FAILED",
                    "Could not bind ownership of the P0 origin receipt.",
                )
            current_directory_identity = _read_directory_identity(receipt_directory)
            if not _same_file_object(current_directory_identity, directory_identity):
                raise DatabaseBackupError(
                    "ORIGIN_RECEIPT_DIRECTORY_CHANGED",
                    "The P0 origin receipt directory changed before publication.",
                )
            owned_receipt.write(payload)
            owned_receipt.flush_and_sync()
            assert owned_receipt.handle is not None
            bound_identity = _identity_from_stat(os.fstat(owned_receipt.handle.fileno()))
            if not _same_file_object(bound_identity, receipt_identity):
                raise DatabaseBackupError(
                    "ORIGIN_RECEIPT_OWNERSHIP_CHANGED",
                    "The P0 origin receipt identity changed during publication.",
                )
            if _sha256_descriptor(owned_receipt.handle.fileno()) != expected_file_sha256:
                raise DatabaseBackupError(
                    "ORIGIN_RECEIPT_WRITE_FAILED",
                    "The P0 origin receipt failed its bound SHA-256 check.",
                )
    except FileExistsError as error:
        raise DatabaseBackupError(
            "ORIGIN_RECEIPT_EXISTS",
            "The fixed P0 origin receipt already exists and will not be overwritten.",
        ) from error
    except DatabaseBackupError:
        if receipt_identity is not None and directory_identity is not None:
            _best_effort_cleanup(
                _unlink_owned_file,
                receipt_path,
                strict=False,
                expected_identity=receipt_identity,
                owned_directory=receipt_directory,
                expected_directory_identity=directory_identity,
            )
        raise
    except Exception as error:
        if receipt_identity is not None and directory_identity is not None:
            _best_effort_cleanup(
                _unlink_owned_file,
                receipt_path,
                strict=False,
                expected_identity=receipt_identity,
                owned_directory=receipt_directory,
                expected_directory_identity=directory_identity,
            )
        raise DatabaseBackupError(
            "ORIGIN_RECEIPT_WRITE_FAILED",
            "Could not publish the fixed P0 origin receipt.",
        ) from error

    try:
        return verify_origin_receipt(receipt_path, expected_file_sha256)
    except DatabaseBackupError:
        if receipt_identity is not None and directory_identity is not None:
            _best_effort_cleanup(
                _unlink_owned_file,
                receipt_path,
                strict=False,
                expected_identity=receipt_identity,
                owned_directory=receipt_directory,
                expected_directory_identity=directory_identity,
            )
        raise


def verify_origin_receipt(
    receipt_file: str | os.PathLike[str],
    expected_receipt_file_sha256: str,
) -> OriginReceiptReport:
    """Read-only verification of a canonical P0 origin receipt."""

    receipt_path = Path(receipt_file).expanduser().resolve()
    if not isinstance(expected_receipt_file_sha256, str) or not _LOWER_SHA256_PATTERN.fullmatch(
        expected_receipt_file_sha256
    ):
        raise DatabaseBackupError(
            "ORIGIN_RECEIPT_EXPECTED_SHA_INVALID",
            "Expected origin receipt file SHA-256 must be lowercase hexadecimal.",
        )
    try:
        with receipt_path.open("rb") as handle:
            initial_identity = _identity_from_stat(os.fstat(handle.fileno()))
            payload = handle.read()
            final_identity = _identity_from_stat(os.fstat(handle.fileno()))
            path_identity = _read_file_identity(receipt_path)
    except FileNotFoundError as error:
        raise DatabaseBackupError(
            "ORIGIN_RECEIPT_NOT_FOUND",
            "The P0 origin receipt does not exist.",
        ) from error
    except DatabaseBackupError:
        raise
    except OSError as error:
        raise DatabaseBackupError(
            "ORIGIN_RECEIPT_READ_FAILED",
            "Could not read the P0 origin receipt.",
        ) from error
    if not _same_file_object(initial_identity, final_identity) or not _same_file_object(
        final_identity,
        path_identity,
    ):
        raise DatabaseBackupError(
            "ORIGIN_RECEIPT_OWNERSHIP_CHANGED",
            "The P0 origin receipt identity changed during verification.",
        )
    actual_file_sha256 = hashlib.sha256(payload).hexdigest()
    if actual_file_sha256 != expected_receipt_file_sha256:
        raise DatabaseBackupError(
            "ORIGIN_RECEIPT_FILE_SHA_MISMATCH",
            "The P0 origin receipt file SHA-256 does not match the expected value.",
        )
    document = _load_origin_receipt_document(payload)
    if document["receiptPath"] != str(receipt_path):
        raise DatabaseBackupError(
            "ORIGIN_RECEIPT_INVALID",
            "The P0 origin receipt path does not match its bound location.",
        )
    backup_path, manifest_path = _verify_origin_receipt_reference(document)
    return OriginReceiptReport(
        valid=True,
        schema_version=document["schemaVersion"],
        manifest_kind=document["manifestKind"],
        backup_id=document["backupId"],
        backup_path=backup_path,
        backup_sha256=document["backupSha256"],
        manifest_path=manifest_path,
        manifest_sha256=document["manifestSha256"],
        logical_sha256=document["logicalSha256"],
        database_lineage_id=document["databaseLineageId"],
        receipt_path=receipt_path,
        created_at=document["createdAt"],
        receipt_sha256=document["receiptSha256"],
        origin_receipt_file_sha256=actual_file_sha256,
    )


def verify_origin_receipt_envelope(
    receipt_file: str | os.PathLike[str],
    expected_receipt_file_sha256: str,
) -> OriginReceiptEnvelopeReport:
    """Verify transported receipt bytes without rebinding their original path."""

    receipt_path = Path(receipt_file).expanduser().resolve()
    if (
        not isinstance(expected_receipt_file_sha256, str)
        or not _LOWER_SHA256_PATTERN.fullmatch(expected_receipt_file_sha256)
    ):
        raise DatabaseBackupError(
            "ORIGIN_RECEIPT_EXPECTED_SHA_INVALID",
            "Expected origin receipt file SHA-256 must be lowercase hexadecimal.",
        )
    try:
        with receipt_path.open("rb") as handle:
            initial_identity = _identity_from_stat(os.fstat(handle.fileno()))
            payload = handle.read()
            final_identity = _identity_from_stat(os.fstat(handle.fileno()))
            path_identity = _read_file_identity(receipt_path)
    except FileNotFoundError as error:
        raise DatabaseBackupError(
            "ORIGIN_RECEIPT_NOT_FOUND",
            "The transported P0 origin receipt does not exist.",
        ) from error
    except DatabaseBackupError:
        raise
    except OSError as error:
        raise DatabaseBackupError(
            "ORIGIN_RECEIPT_READ_FAILED",
            "Could not read the transported P0 origin receipt.",
        ) from error
    if not _same_file_object(initial_identity, final_identity) or not _same_file_object(
        final_identity,
        path_identity,
    ):
        raise DatabaseBackupError(
            "ORIGIN_RECEIPT_OWNERSHIP_CHANGED",
            "The transported P0 origin receipt changed during verification.",
        )
    actual_file_sha256 = hashlib.sha256(payload).hexdigest()
    if actual_file_sha256 != expected_receipt_file_sha256:
        raise DatabaseBackupError(
            "ORIGIN_RECEIPT_FILE_SHA_MISMATCH",
            "The transported origin receipt file SHA-256 does not match.",
        )
    document = _load_origin_receipt_document(payload)
    return OriginReceiptEnvelopeReport(
        receipt_path=receipt_path,
        backup_id=document["backupId"],
        backup_sha256=document["backupSha256"],
        manifest_sha256=document["manifestSha256"],
        logical_sha256=document["logicalSha256"],
        database_lineage_id=document["databaseLineageId"],
        receipt_sha256=document["receiptSha256"],
        origin_receipt_file_sha256=actual_file_sha256,
    )


def _canonical_json_bytes(document: Mapping[str, Any]) -> bytes:
    return json.dumps(
        document,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")


def _verify_origin_receipt_reference(
    document: Mapping[str, Any],
) -> tuple[Path, Path]:
    try:
        backup_path = Path(document["backupPath"])
        manifest_path = Path(document["manifestPath"])
        if not backup_path.is_absolute() or not manifest_path.is_absolute():
            raise ValueError("origin paths must be absolute")
        resolved_backup_path = backup_path.resolve()
        resolved_manifest_path = manifest_path.resolve()
        if (
            str(resolved_backup_path) != document["backupPath"]
            or str(resolved_manifest_path) != document["manifestPath"]
        ):
            raise ValueError("origin paths must be canonical")
        verification = verify_backup(
            resolved_backup_path,
            resolved_manifest_path,
        )
    except (DatabaseBackupError, OSError, ValueError) as error:
        raise DatabaseBackupError(
            "ORIGIN_RECEIPT_REFERENCE_INVALID",
            "The P0 origin receipt reference is unavailable or no longer exact.",
        ) from error
    if (
        verification.backup_id != document["backupId"]
        or verification.backup_sha256 != document["backupSha256"]
        or verification.manifest_file_sha256 != document["manifestSha256"]
        or verification.logical_sha256 != document["logicalSha256"]
    ):
        raise DatabaseBackupError(
            "ORIGIN_RECEIPT_REFERENCE_INVALID",
            "The P0 origin receipt reference is unavailable or no longer exact.",
        )
    return resolved_backup_path, resolved_manifest_path


def _load_origin_receipt_document(payload: bytes) -> dict[str, Any]:
    def reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        document: dict[str, Any] = {}
        for key, value in pairs:
            if key in document:
                raise ValueError("duplicate JSON key")
            document[key] = value
        return document

    try:
        document = json.loads(
            payload.decode("utf-8"),
            object_pairs_hook=reject_duplicate_keys,
        )
    except (UnicodeError, json.JSONDecodeError, ValueError) as error:
        raise DatabaseBackupError(
            "ORIGIN_RECEIPT_INVALID",
            "The P0 origin receipt is not valid canonical UTF-8 JSON.",
        ) from error
    if not isinstance(document, dict) or tuple(document) != _ORIGIN_RECEIPT_FIELDS:
        raise DatabaseBackupError(
            "ORIGIN_RECEIPT_INVALID",
            "The P0 origin receipt fields are missing, unknown, or out of order.",
        )
    if _canonical_json_bytes(document) != payload:
        raise DatabaseBackupError(
            "ORIGIN_RECEIPT_INVALID",
            "The P0 origin receipt must use canonical JSON serialization.",
        )
    schema_version = document["schemaVersion"]
    if (
        isinstance(schema_version, bool)
        or not isinstance(schema_version, int)
        or schema_version != ORIGIN_RECEIPT_SCHEMA_VERSION
    ):
        raise DatabaseBackupError(
            "ORIGIN_RECEIPT_INVALID",
            "The P0 origin receipt schema version is invalid.",
        )
    string_fields = _ORIGIN_RECEIPT_FIELDS[1:]
    if any(
        not isinstance(document[field], str)
        or not document[field]
        or "\x00" in document[field]
        for field in string_fields
    ):
        raise DatabaseBackupError(
            "ORIGIN_RECEIPT_INVALID",
            "The P0 origin receipt contains an invalid string field.",
        )
    if document["manifestKind"] != ORIGIN_RECEIPT_MANIFEST_KIND:
        raise DatabaseBackupError(
            "ORIGIN_RECEIPT_INVALID",
            "The P0 origin receipt manifest kind is invalid.",
        )
    for field in (
        "backupSha256",
        "manifestSha256",
        "logicalSha256",
        "databaseLineageId",
        "receiptSha256",
    ):
        if not _LOWER_SHA256_PATTERN.fullmatch(document[field]):
            raise DatabaseBackupError(
                "ORIGIN_RECEIPT_INVALID",
                "The P0 origin receipt contains an invalid SHA-256 field.",
            )
    lineage_document = {
        "version": 1,
        "originBackupId": document["backupId"],
        "originManifestSha256": document["manifestSha256"],
        "originLogicalSha256": document["logicalSha256"],
    }
    expected_lineage_id = hashlib.sha256(
        _canonical_json_bytes(lineage_document)
    ).hexdigest()
    if document["databaseLineageId"] != expected_lineage_id:
        raise DatabaseBackupError(
            "ORIGIN_RECEIPT_INVALID",
            "The P0 origin receipt database lineage does not match its inputs.",
        )
    unsigned_document = {
        field: document[field] for field in _ORIGIN_RECEIPT_UNSIGNED_FIELDS
    }
    expected_receipt_sha256 = hashlib.sha256(
        _canonical_json_bytes(unsigned_document)
    ).hexdigest()
    if document["receiptSha256"] != expected_receipt_sha256:
        raise DatabaseBackupError(
            "ORIGIN_RECEIPT_INVALID",
            "The P0 origin receipt payload SHA-256 is invalid.",
        )
    return document


def _verify_backup_against_manifest(
    backup_path: Path,
    manifest: BackupManifest,
    *,
    manifest_file_sha256: str,
) -> VerificationReport:
    try:
        if not backup_path.is_file():
            raise DatabaseBackupError(
                "BACKUP_NOT_FOUND",
                f"Backup does not exist: {backup_path}",
            )
        if backup_path.name != manifest.backup_file:
            raise DatabaseBackupError(
                "BACKUP_FILE_MISMATCH",
                "Backup filename does not match its manifest.",
            )
        initial_identity = _read_file_identity(backup_path)
        _assert_database_has_no_sidecars(backup_path)
        actual_size = backup_path.stat().st_size
        if actual_size != manifest.backup_size_bytes:
            raise DatabaseBackupError(
                "BACKUP_FILE_MISMATCH",
                "Backup size does not match its manifest.",
            )
        actual_sha256 = _sha256_file(backup_path)
        if actual_sha256 != manifest.backup_sha256:
            raise DatabaseBackupError(
                "BACKUP_FILE_MISMATCH",
                "Backup SHA-256 does not match its manifest.",
            )
        hashed_identity = _read_file_identity(backup_path)
        if not _same_file_object(hashed_identity, initial_identity):
            raise DatabaseBackupError(
                "BACKUP_VERIFY_OWNERSHIP_CHANGED",
                "Backup file identity changed during verification.",
            )
    except DatabaseBackupError:
        raise
    except OSError as error:
        raise DatabaseBackupError(
            "BACKUP_VERIFY_FAILED",
            f"Could not read the SQLite backup for verification: {error}",
        ) from error

    try:
        actual_database = _fingerprint_database(
            backup_path,
            format_version=manifest.format_version,
        )
        _assert_database_has_no_sidecars(backup_path)
        final_size = backup_path.stat().st_size
        final_sha256 = _sha256_file(backup_path)
        final_identity = _read_file_identity(backup_path)
    except DatabaseBackupError:
        raise
    except OSError as error:
        raise DatabaseBackupError(
            "BACKUP_VERIFY_FAILED",
            f"Could not complete the SQLite backup verification: {error}",
        ) from error
    if not _same_file_object(final_identity, initial_identity):
        raise DatabaseBackupError(
            "BACKUP_VERIFY_OWNERSHIP_CHANGED",
            "Backup file identity changed during logical verification.",
        )
    if final_size != manifest.backup_size_bytes or final_sha256 != manifest.backup_sha256:
        raise DatabaseBackupError(
            "BACKUP_FILE_CHANGED_DURING_VERIFY",
            "Backup file contents changed during logical verification.",
        )
    if actual_database != manifest.database:
        raise DatabaseBackupError(
            "BACKUP_LOGICAL_MISMATCH",
            "Backup schema or logical table content does not match its manifest.",
        )
    return VerificationReport(
        valid=True,
        format_version=manifest.format_version,
        backup_id=manifest.backup_id,
        backup_sha256=actual_sha256,
        manifest_sha256=manifest.manifest_sha256,
        manifest_file_sha256=manifest_file_sha256,
        logical_sha256=actual_database.logical_sha256,
        table_counts=dict(actual_database.table_counts),
        table_sha256=dict(actual_database.table_sha256),
        content_counts=dict(actual_database.content_counts),
        content_sha256=dict(actual_database.content_sha256),
    )


def restore_backup_for_validation(
    backup_database: str | os.PathLike[str],
    manifest_file: str | os.PathLike[str],
    output_directory: str | os.PathLike[str],
    *,
    bound_root_platform: BoundRootPlatform | None = None,
) -> VerificationReport:
    """Restore into a generated isolation directory for rollback rehearsal."""

    platform = bound_root_platform or _NATIVE_BOUND_ROOT_PLATFORM
    backup_path = Path(backup_database).expanduser().resolve()
    manifest_path = Path(manifest_file).expanduser().resolve()
    requested_output_path = Path(os.path.abspath(Path(output_directory).expanduser()))
    _validate_restore_output_directory(requested_output_path)
    if _path_contains_reparse_point(requested_output_path):
        raise DatabaseBackupError(
            "RESTORE_OUTPUT_DIRECTORY_INVALID",
            "Restore validation output must not traverse a symlink or reparse point.",
        )
    output_path = requested_output_path.resolve()
    manifest, manifest_file_sha256 = _load_manifest_document(manifest_path)
    verification = _verify_backup_against_manifest(
        backup_path,
        manifest,
        manifest_file_sha256=manifest_file_sha256,
    )
    if _path_contains_reparse_point(requested_output_path):
        raise DatabaseBackupError(
            "RESTORE_OUTPUT_DIRECTORY_INVALID",
            "Restore validation output became a symlink or reparse point during verification.",
        )
    if not platform.is_supported():
        raise DatabaseBackupError(
            "RESTORE_BOUND_DIRECTORY_UNSUPPORTED",
            "This platform cannot bind restore validation operations to a directory descriptor.",
        )
    destination_path = output_path / "restore-validation-probe" / "app.db"
    _assert_restore_destination_safe(
        destination_path,
        backup_path=backup_path,
        source_database=manifest.source_database,
    )
    bound_root: BoundRestoreRoot | None = None
    validation_directory: BoundValidationDirectory | None = None
    try:
        output_path.mkdir(mode=0o700, parents=True, exist_ok=True)
        if _path_contains_reparse_point(requested_output_path):
            raise DatabaseBackupError(
                "RESTORE_OUTPUT_DIRECTORY_INVALID",
                "Restore validation output became a symlink or reparse point before use.",
            )
        bound_root = platform.open_restore_root(output_path)
        validation_directory = bound_root.create_validation_directory(
            "restore-validation-"
        )
        validation_directory.copy_verified_database(backup_path, "app.db")
        initial_destination = validation_directory.verify_destination("app.db")
        if initial_destination.sha256 != manifest.backup_sha256:
            raise DatabaseBackupError(
                "RESTORE_COPY_MISMATCH",
                "Restored copy failed its SHA-256 check before publication.",
            )
        restored_database = platform.fingerprint_bound_restore(
            initial_destination,
            manifest,
        )
        if restored_database != manifest.database:
            raise DatabaseBackupError(
                "RESTORE_LOGICAL_MISMATCH",
                "Restored database does not match the backup manifest.",
            )
        final_destination = validation_directory.verify_destination("app.db")
        if not _same_file_object(
            final_destination.identity,
            initial_destination.identity,
        ):
            raise DatabaseBackupError(
                "RESTORE_PUBLISH_OWNERSHIP_CHANGED",
                "The restored database was replaced during validation.",
            )
        if (
            final_destination.size_bytes != manifest.backup_size_bytes
            or final_destination.sha256 != manifest.backup_sha256
        ):
            raise DatabaseBackupError(
                "RESTORE_FILE_CHANGED_DURING_VALIDATION",
                "The restored database contents changed during validation.",
            )
        report = VerificationReport(
            valid=verification.valid,
            format_version=verification.format_version,
            backup_id=verification.backup_id,
            backup_sha256=verification.backup_sha256,
            manifest_sha256=verification.manifest_sha256,
            manifest_file_sha256=verification.manifest_file_sha256,
            logical_sha256=restored_database.logical_sha256,
            table_counts=dict(restored_database.table_counts),
            table_sha256=dict(restored_database.table_sha256),
            content_counts=dict(restored_database.content_counts),
            content_sha256=dict(restored_database.content_sha256),
            restored_path=final_destination.report_path,
        )
        bound_root.close()
        bound_root = None
        return report
    except DatabaseBackupError:
        if validation_directory is not None:
            _best_effort_cleanup(validation_directory._abort)
        if bound_root is not None:
            _best_effort_cleanup(bound_root._abort)
        raise
    except Exception as error:
        if validation_directory is not None:
            _best_effort_cleanup(validation_directory._abort)
        if bound_root is not None:
            _best_effort_cleanup(bound_root._abort)
        raise DatabaseBackupError(
            "RESTORE_VALIDATION_FAILED",
            "Could not restore the verified SQLite backup for validation.",
        ) from error


def _fingerprint_bound_restore(
    destination: _BoundDestination,
    manifest: BackupManifest,
) -> DatabaseFingerprint:
    """Return the logical fingerprint of an exact, handle-bound restore copy."""

    if os.name == "nt":
        # Python's Windows SQLite VFS does not share delete access. Opening the
        # destination by pathname would require releasing the no-share-delete
        # handle that binds both ownership and abort cleanup. The caller has
        # already proved the bound destination's complete file SHA-256 equals
        # the independently verified backup, so their logical fingerprints are
        # identical without reopening the pathname.
        return manifest.database
    return _fingerprint_database(
        destination.bound_path,
        format_version=manifest.format_version,
    )


def _remove_empty_directory(path: Path) -> None:
    try:
        path.rmdir()
    except FileNotFoundError:
        return
    except OSError:
        return


def _best_effort_cleanup(
    operation: Callable[..., Any],
    /,
    *args: Any,
    **kwargs: Any,
) -> None:
    """Run non-strict cleanup without ever replacing the primary failure."""

    try:
        operation(*args, **kwargs)
    except Exception:
        return


def _remove_owned_staging_directory(
    path: Path,
    *,
    strict: bool,
    expected_identity: _FileIdentity | None = None,
    owned_parent_directory: Path | None = None,
    expected_parent_identity: _FileIdentity | None = None,
) -> bool:
    """Remove only an empty invocation-owned staging directory.

    Generated files are cleaned individually before this function is called.  Deliberately
    avoiding recursive deletion means a collision or replacement can never cause unrelated
    directory contents to be removed.
    """

    if expected_identity is None:
        if strict:
            raise DatabaseBackupError(
                "BACKUP_STAGING_OWNERSHIP_UNKNOWN",
                "Refusing to remove a staging directory without bound ownership.",
            )
        return False
    try:
        current_identity = _read_directory_identity(path)
    except FileNotFoundError:
        return False
    except DatabaseBackupError:
        if strict:
            raise
        return False
    if not _same_file_object(current_identity, expected_identity):
        if strict:
            raise DatabaseBackupError(
                "BACKUP_STAGING_OWNERSHIP_CHANGED",
                "Refusing to remove a replaced staging directory.",
            )
        return False
    if os.name == "nt":
        return _remove_owned_directory_windows(
            path,
            strict=strict,
            expected_identity=expected_identity,
        )
    if (
        owned_parent_directory is None
        or expected_parent_identity is None
        or os.path.normcase(os.path.abspath(os.fspath(path.parent)))
        != os.path.normcase(os.path.abspath(os.fspath(owned_parent_directory)))
        or not _is_private_posix_directory(path)
    ):
        if strict:
            raise DatabaseBackupError(
                "BACKUP_STAGING_CLEANUP_UNSUPPORTED",
                "POSIX staging cleanup requires a bound parent and a private directory.",
            )
        return False
    try:
        parent_identity = _read_directory_identity(owned_parent_directory)
        traverses_reparse = _path_contains_reparse_point(path)
    except (FileNotFoundError, DatabaseBackupError):
        if strict:
            raise DatabaseBackupError(
                "BACKUP_STAGING_OWNERSHIP_CHANGED",
                "Refusing to remove a staging directory whose parent ownership changed.",
            )
        return False
    if (
        traverses_reparse
        or not _same_file_object(parent_identity, expected_parent_identity)
    ):
        if strict:
            raise DatabaseBackupError(
                "BACKUP_STAGING_OWNERSHIP_CHANGED",
                "Refusing to remove a staging directory whose parent ownership changed.",
            )
        return False
    try:
        final_identity = _read_directory_identity(path)
        final_parent_identity = _read_directory_identity(owned_parent_directory)
        final_traverses_reparse = _path_contains_reparse_point(path)
    except (FileNotFoundError, DatabaseBackupError):
        if strict:
            raise DatabaseBackupError(
                "BACKUP_STAGING_OWNERSHIP_CHANGED",
                "Refusing to remove a staging directory after ownership drift.",
            )
        return False
    if (
        final_traverses_reparse
        or not _same_file_object(final_identity, expected_identity)
        or not _same_file_object(final_parent_identity, expected_parent_identity)
    ):
        if strict:
            raise DatabaseBackupError(
                "BACKUP_STAGING_OWNERSHIP_CHANGED",
                "Refusing to remove a replaced staging directory.",
            )
        return False
    try:
        path.rmdir()
    except FileNotFoundError:
        return False
    except OSError as error:
        if strict:
            raise DatabaseBackupError(
                "BACKUP_STAGING_CLEANUP_FAILED",
                f"Could not remove private backup staging directory: {error}",
            ) from error
        return False
    return True


def _remove_owned_directory_windows(
    path: Path,
    *,
    strict: bool,
    expected_identity: _FileIdentity,
) -> bool:
    """Delete an empty directory through a bound Windows handle."""

    import ctypes
    from ctypes import wintypes

    delete_access = 0x00010000
    read_attributes = 0x00000080
    share_read = 0x00000001
    share_write = 0x00000002
    open_existing = 3
    open_reparse_point = 0x00200000
    backup_semantics = 0x02000000
    invalid_handle_value = ctypes.c_void_p(-1).value
    file_disposition_info_class = 4

    class FileDispositionInfo(ctypes.Structure):
        _fields_ = [("delete_file", wintypes.BOOL)]

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
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = (wintypes.HANDLE,)
    close_handle.restype = wintypes.BOOL
    set_file_information = kernel32.SetFileInformationByHandle
    set_file_information.argtypes = (
        wintypes.HANDLE,
        ctypes.c_int,
        wintypes.LPVOID,
        wintypes.DWORD,
    )
    set_file_information.restype = wintypes.BOOL

    raw_handle = create_file(
        str(path),
        delete_access | read_attributes,
        share_read | share_write,
        None,
        open_existing,
        open_reparse_point | backup_semantics,
        None,
    )
    if raw_handle == invalid_handle_value:
        windows_error = ctypes.get_last_error()
        if windows_error in {2, 3}:
            return False
        if strict:
            raise DatabaseBackupError(
                "BACKUP_STAGING_CLEANUP_FAILED",
                "Could not open the private staging directory for safe removal.",
            )
        return False

    try:
        try:
            handle_bound_identity = _read_directory_identity(path)
        except FileNotFoundError:
            return False
        except DatabaseBackupError:
            if strict:
                raise
            return False
        if not _same_file_object(handle_bound_identity, expected_identity):
            if strict:
                raise DatabaseBackupError(
                    "BACKUP_STAGING_OWNERSHIP_CHANGED",
                    "Refusing to remove a replaced staging directory.",
                )
            return False
        disposition = FileDispositionInfo(True)
        if not set_file_information(
            raw_handle,
            file_disposition_info_class,
            ctypes.byref(disposition),
            ctypes.sizeof(disposition),
        ):
            if strict:
                raise DatabaseBackupError(
                    "BACKUP_STAGING_CLEANUP_FAILED",
                    "Could not safely remove the private staging directory.",
                )
            return False
    finally:
        close_handle(raw_handle)
    return True


def _unlink_owned_file(
    path: Path,
    *,
    strict: bool,
    expected_identity: _FileIdentity | None = None,
    owned_directory: Path | None = None,
    expected_directory_identity: _FileIdentity | None = None,
) -> bool:
    try:
        current_identity = _read_file_identity(path)
    except FileNotFoundError:
        return False
    except DatabaseBackupError:
        if strict:
            raise
        return False
    bound_identity = expected_identity or current_identity
    if not _same_file_object(current_identity, bound_identity):
        if strict:
            raise DatabaseBackupError(
                "BACKUP_CLEANUP_OWNERSHIP_CHANGED",
                f"Refusing to remove replaced file {path.name}.",
            )
        return False
    if os.name == "nt":
        return _unlink_owned_file_windows(
            path,
            strict=strict,
            expected_identity=bound_identity,
        )
    if (
        owned_directory is None
        or expected_directory_identity is None
        or os.path.normcase(os.path.abspath(os.fspath(path.parent)))
        != os.path.normcase(os.path.abspath(os.fspath(owned_directory)))
        or not _is_private_posix_directory(owned_directory)
    ):
        if strict:
            raise DatabaseBackupError(
                "BACKUP_CLEANUP_UNSUPPORTED",
                "POSIX file cleanup requires a bound private owning directory.",
            )
        return False
    try:
        directory_identity = _read_directory_identity(owned_directory)
        traverses_reparse = _path_contains_reparse_point(owned_directory)
    except (FileNotFoundError, DatabaseBackupError):
        if strict:
            raise DatabaseBackupError(
                "BACKUP_CLEANUP_OWNERSHIP_CHANGED",
                f"Refusing to remove generated file {path.name} after owner drift.",
            )
        return False
    if (
        traverses_reparse
        or not _same_file_object(directory_identity, expected_directory_identity)
    ):
        if strict:
            raise DatabaseBackupError(
                "BACKUP_CLEANUP_OWNERSHIP_CHANGED",
                f"Refusing to remove generated file {path.name} after owner drift.",
            )
        return False
    try:
        final_identity = _read_file_identity(path)
        final_directory_identity = _read_directory_identity(owned_directory)
        final_traverses_reparse = _path_contains_reparse_point(owned_directory)
    except (FileNotFoundError, DatabaseBackupError):
        if strict:
            raise DatabaseBackupError(
                "BACKUP_CLEANUP_OWNERSHIP_CHANGED",
                f"Refusing to remove generated file {path.name} after owner drift.",
            )
        return False
    if (
        final_traverses_reparse
        or not _same_file_object(final_identity, bound_identity)
        or not _same_file_object(
            final_directory_identity,
            expected_directory_identity,
        )
    ):
        if strict:
            raise DatabaseBackupError(
                "BACKUP_CLEANUP_OWNERSHIP_CHANGED",
                f"Refusing to remove replaced file {path.name}.",
            )
        return False
    try:
        path.unlink(missing_ok=True)
    except OSError as error:
        if strict:
            raise DatabaseBackupError(
                "BACKUP_CLEANUP_FAILED",
                f"Could not remove generated file {path.name}: {error}",
            ) from error
        return False
    return True


def _unlink_owned_file_windows(
    path: Path,
    *,
    strict: bool,
    expected_identity: _FileIdentity,
) -> bool:
    """Delete the file object opened by handle, never a later pathname replacement."""

    import ctypes
    import msvcrt
    from ctypes import wintypes

    delete_access = 0x00010000
    read_attributes = 0x00000080
    share_read = 0x00000001
    share_write = 0x00000002
    share_delete = 0x00000004
    open_existing = 3
    open_reparse_point = 0x00200000
    invalid_handle_value = ctypes.c_void_p(-1).value
    file_disposition_info_class = 4

    class FileDispositionInfo(ctypes.Structure):
        _fields_ = [("delete_file", wintypes.BOOL)]

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
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = (wintypes.HANDLE,)
    close_handle.restype = wintypes.BOOL
    set_file_information = kernel32.SetFileInformationByHandle
    set_file_information.argtypes = (
        wintypes.HANDLE,
        ctypes.c_int,
        wintypes.LPVOID,
        wintypes.DWORD,
    )
    set_file_information.restype = wintypes.BOOL

    raw_handle = create_file(
        str(path),
        delete_access | read_attributes,
        share_read | share_write | share_delete,
        None,
        open_existing,
        open_reparse_point,
        None,
    )
    if raw_handle == invalid_handle_value:
        windows_error = ctypes.get_last_error()
        if windows_error in {2, 3}:
            return False
        if strict:
            raise DatabaseBackupError(
                "BACKUP_CLEANUP_FAILED",
                f"Could not open generated file {path.name} for safe removal.",
            )
        return False

    descriptor: int | None = None
    try:
        descriptor = msvcrt.open_osfhandle(int(raw_handle), os.O_RDONLY)
        raw_handle = None
        handle_identity = _identity_from_stat(os.fstat(descriptor))
        if not _same_file_object(handle_identity, expected_identity):
            if strict:
                raise DatabaseBackupError(
                    "BACKUP_CLEANUP_OWNERSHIP_CHANGED",
                    f"Refusing to remove replaced file {path.name}.",
                )
            return False
        disposition = FileDispositionInfo(True)
        native_handle = wintypes.HANDLE(msvcrt.get_osfhandle(descriptor))
        if not set_file_information(
            native_handle,
            file_disposition_info_class,
            ctypes.byref(disposition),
            ctypes.sizeof(disposition),
        ):
            if strict:
                raise DatabaseBackupError(
                    "BACKUP_CLEANUP_FAILED",
                    f"Could not safely remove generated file {path.name}.",
                )
            return False
    except DatabaseBackupError:
        raise
    except OSError as error:
        if strict:
            raise DatabaseBackupError(
                "BACKUP_CLEANUP_FAILED",
                f"Could not safely remove generated file {path.name}: {error}",
            ) from error
        return False
    finally:
        if descriptor is not None:
            os.close(descriptor)
        elif raw_handle not in {None, invalid_handle_value}:
            close_handle(raw_handle)
    return True


def _read_file_identity(path: Path) -> _FileIdentity:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        raise
    except OSError as error:
        raise DatabaseBackupError(
            "BACKUP_FILE_IDENTITY_FAILED",
            f"Could not inspect generated file identity for {path.name}.",
        ) from error
    if not stat.S_ISREG(metadata.st_mode):
        raise DatabaseBackupError(
            "BACKUP_FILE_IDENTITY_INVALID",
            f"Generated path is not a regular file: {path.name}.",
        )
    return _identity_from_stat(metadata)


def _read_directory_identity(path: Path) -> _FileIdentity:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        raise
    except OSError as error:
        raise DatabaseBackupError(
            "BACKUP_DIRECTORY_IDENTITY_FAILED",
            f"Could not inspect generated directory identity for {path.name}.",
        ) from error
    reparse_attribute = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    file_attributes = int(getattr(metadata, "st_file_attributes", 0))
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or bool(file_attributes & reparse_attribute)
    ):
        raise DatabaseBackupError(
            "BACKUP_DIRECTORY_IDENTITY_INVALID",
            f"Generated path is not a physical, non-reparse directory: {path.name}.",
        )
    return _identity_from_stat(metadata)


def _is_private_posix_directory(path: Path) -> bool:
    try:
        metadata = path.lstat()
    except OSError:
        return False
    return (
        stat.S_ISDIR(metadata.st_mode)
        and not stat.S_ISLNK(metadata.st_mode)
        and stat.S_IMODE(metadata.st_mode) == 0o700
    )


def _identity_from_stat(metadata: os.stat_result) -> _FileIdentity:
    return _FileIdentity(
        device=int(metadata.st_dev),
        inode=int(metadata.st_ino),
        size=int(metadata.st_size),
        modified_ns=int(metadata.st_mtime_ns),
        changed_ns=int(metadata.st_ctime_ns),
    )


def _same_file_object(left: _FileIdentity, right: _FileIdentity) -> bool:
    if left.inode != 0 and right.inode != 0:
        return left.device == right.device and left.inode == right.inode
    return left == right


def _database_sidecars(database_path: Path) -> tuple[Path, ...]:
    return (
        database_path.with_name(f"{database_path.name}-wal"),
        database_path.with_name(f"{database_path.name}-shm"),
        database_path.with_name(f"{database_path.name}-journal"),
    )


def _remove_database_sidecars(
    database_path: Path,
    *,
    owned_directory: Path,
    expected_directory_identity: _FileIdentity | None = None,
    strict: bool = True,
) -> None:
    try:
        owned_root = owned_directory.resolve()
        database_parent = database_path.parent.resolve()
    except OSError as error:
        if strict:
            raise DatabaseBackupError(
                "BACKUP_SIDECAR_OWNERSHIP_INVALID",
                "Could not resolve the private directory that owns SQLite sidecars.",
            ) from error
        return
    if database_parent != owned_root:
        if strict:
            raise DatabaseBackupError(
                "BACKUP_SIDECAR_OWNERSHIP_INVALID",
                "Refusing to clean SQLite sidecars outside the invocation-owned directory.",
            )
        return
    for sidecar_path in _database_sidecars(database_path):
        try:
            identity = _read_file_identity(sidecar_path)
        except FileNotFoundError:
            continue
        except DatabaseBackupError as error:
            if strict:
                raise DatabaseBackupError(
                    "BACKUP_SIDECAR_CLEANUP_FAILED",
                    f"Could not inspect generated SQLite sidecar {sidecar_path.name}: {error}",
                ) from error
            continue
        _unlink_owned_file(
            sidecar_path,
            strict=strict,
            expected_identity=identity,
            owned_directory=owned_directory,
            expected_directory_identity=expected_directory_identity,
        )


def _assert_database_has_no_sidecars(database_path: Path) -> None:
    try:
        existing = [
            path.name
            for path in _database_sidecars(database_path)
            if path.exists()
        ]
    except OSError as error:
        raise DatabaseBackupError(
            "BACKUP_SIDECAR_INSPECTION_FAILED",
            "Could not verify that the SQLite backup has no WAL, SHM, or rollback-journal sidecar.",
        ) from error
    if existing:
        raise DatabaseBackupError(
            "BACKUP_SIDECAR_PRESENT",
            "Generated SQLite database unexpectedly retained a WAL, SHM, or rollback-journal sidecar.",
        )


def _validate_source_database(source_path: Path) -> None:
    try:
        if not source_path.exists():
            raise DatabaseBackupError(
                "SOURCE_DATABASE_NOT_FOUND",
                f"Source database does not exist: {source_path}",
            )
        if not source_path.is_file():
            raise DatabaseBackupError(
                "SOURCE_DATABASE_INVALID",
                f"Source database is not a regular file: {source_path}",
            )
        if source_path.stat().st_size <= 0:
            raise DatabaseBackupError(
                "SOURCE_DATABASE_INVALID",
                "Source database is empty.",
            )
    except DatabaseBackupError:
        raise
    except OSError as error:
        raise DatabaseBackupError(
            "SOURCE_DATABASE_INVALID",
            f"Could not inspect source database: {error}",
        ) from error


def _validate_restore_output_directory(output_path: Path) -> None:
    lowered_name = output_path.name.casefold()
    forbidden_endings = (
        ".db",
        ".db3",
        ".sqlite",
        ".sqlite3",
        "-wal",
        "-shm",
        "-journal",
    )
    if lowered_name.endswith(forbidden_endings):
        raise DatabaseBackupError(
            "RESTORE_OUTPUT_DIRECTORY_INVALID",
            "Restore validation output must be a directory root, not a database path.",
        )
    try:
        if output_path.exists() and not output_path.is_dir():
            raise DatabaseBackupError(
                "RESTORE_OUTPUT_DIRECTORY_INVALID",
                "Restore validation output must resolve to a directory.",
            )
    except DatabaseBackupError:
        raise
    except OSError as error:
        raise DatabaseBackupError(
            "RESTORE_OUTPUT_DIRECTORY_INVALID",
            f"Could not inspect restore validation output: {error}",
        ) from error


def _assert_restore_destination_safe(
    destination_path: Path,
    *,
    backup_path: Path,
    source_database: str,
) -> None:
    destination = destination_path.resolve()
    declared_source = Path(source_database).expanduser().resolve()
    forbidden = {
        backup_path.resolve(),
        declared_source,
        *_database_sidecars(backup_path.resolve()),
        *_database_sidecars(declared_source),
    }
    if destination in forbidden:
        raise DatabaseBackupError(
            "RESTORE_DESTINATION_INVALID",
            "Restore validation destination must not target a source, backup, or SQLite sidecar.",
        )


def _path_contains_reparse_point(path: Path) -> bool:
    absolute_path = path.__class__(os.path.abspath(path))
    anchor = path.__class__(absolute_path.anchor)
    current = anchor
    reparse_attribute = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    for part in absolute_path.parts[1:]:
        current = current / part
        try:
            metadata = current.lstat()
        except FileNotFoundError:
            break
        except OSError as error:
            raise DatabaseBackupError(
                "RESTORE_OUTPUT_DIRECTORY_INVALID",
                f"Could not inspect restore output path component {current.name!r}.",
            ) from error
        if stat.S_ISLNK(metadata.st_mode):
            return True
        attributes = int(getattr(metadata, "st_file_attributes", 0))
        if attributes & reparse_attribute:
            return True
    return False


def _validate_label(label: str) -> str:
    value = str(label).strip()
    if not _LABEL_PATTERN.fullmatch(value):
        raise DatabaseBackupError(
            "BACKUP_LABEL_INVALID",
            "Backup label must be 1-64 ASCII letters, digits, dot, underscore, or hyphen.",
        )
    return value


def _safe_filename_part(value: str) -> str:
    sanitized = re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip(".-")
    return sanitized[:64] or "database"


def _open_readonly(database_path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(
        database_path.as_uri() + "?mode=ro",
        uri=True,
        timeout=30.0,
    )
    connection.execute("PRAGMA query_only = ON")
    connection.execute("PRAGMA busy_timeout = 30000")
    return connection


def _assert_healthy_database(connection: sqlite3.Connection, description: str) -> None:
    quick_rows = [str(row[0]) for row in connection.execute("PRAGMA quick_check")]
    if quick_rows != ["ok"]:
        raise DatabaseBackupError(
            "SQLITE_QUICK_CHECK_FAILED",
            f"The {description} failed PRAGMA quick_check.",
        )
    integrity_rows = [str(row[0]) for row in connection.execute("PRAGMA integrity_check")]
    if integrity_rows != ["ok"]:
        raise DatabaseBackupError(
            "SQLITE_INTEGRITY_CHECK_FAILED",
            f"The {description} failed PRAGMA integrity_check.",
        )
    foreign_key_violations = sum(1 for _row in connection.execute("PRAGMA foreign_key_check"))
    if foreign_key_violations:
        raise DatabaseBackupError(
            "SQLITE_FOREIGN_KEY_CHECK_FAILED",
            f"The {description} contains {foreign_key_violations} foreign-key violation(s).",
        )


def _fingerprint_database(
    database_path: Path,
    *,
    format_version: int = CURRENT_BACKUP_FORMAT_VERSION,
) -> DatabaseFingerprint:
    if format_version not in SUPPORTED_BACKUP_FORMAT_VERSIONS:
        raise DatabaseBackupError(
            "BACKUP_FORMAT_UNSUPPORTED",
            f"Backup format {format_version} is not supported.",
    )
    try:
        with closing(_open_readonly(database_path)) as connection:
            _assert_p3_fts_integrity(connection)
            _assert_healthy_database(connection, "backup database")
            schema_rows = list(
                connection.execute(
                    """
                    SELECT type, name, tbl_name, sql
                    FROM sqlite_schema
                    WHERE name NOT LIKE 'sqlite_%' AND sql IS NOT NULL
                    ORDER BY type, name
                    """
                )
            )
            logical_schema_rows = [
                row
                for row in schema_rows
                if not _is_p3_fts_physical_schema_row(row)
            ]
            schema_sha256 = _hash_rows(
                ("type", "name", "table", "sql"),
                logical_schema_rows,
            )
            internal_table_clause = (
                "(name NOT LIKE 'sqlite_%' OR name = 'sqlite_sequence')"
                if format_version >= 2
                else "name NOT LIKE 'sqlite_%'"
            )
            table_names = [
                str(row[0])
                for row in connection.execute(
                    f"""
                    SELECT name
                    FROM sqlite_schema
                    WHERE type = 'table' AND {internal_table_clause}
                    ORDER BY name
                    """
                )
            ]
            table_counts: dict[str, int] = {}
            table_sha256: dict[str, str] = {}
            content_counts: dict[str, int] = {}
            content_sha256: dict[str, str] = {}
            schema_content_counts, schema_content_hashes = _p2_schema_content_inventory(
                connection,
                schema_rows,
            )
            content_counts.update(schema_content_counts)
            content_sha256.update(schema_content_hashes)
            for table_name in table_names:
                if _is_p3_fts_physical_table(table_name):
                    continue
                count, table_digest, critical_counts, critical_hashes = _fingerprint_table(
                    connection,
                    table_name,
                )
                table_counts[table_name] = count
                table_sha256[table_name] = table_digest
                content_counts.update(critical_counts)
                content_sha256.update(critical_hashes)
            fts_counts, fts_hashes = _p3_fts_content_inventory(
                connection,
                schema_rows,
                set(table_names),
            )
            content_counts.update(fts_counts)
            content_sha256.update(fts_hashes)
            table_names = [
                table_name
                for table_name in table_names
                if not _is_p3_fts_physical_table(table_name)
            ]
            legacy_schema_migrations = _legacy_schema_migrations(
                connection,
                set(table_names),
            )
            alembic_version = _alembic_version(connection, set(table_names))

            logical_hasher = hashlib.sha256()
            _update_frame(logical_hasher, b"schema", bytes.fromhex(schema_sha256))
            for table_name in table_names:
                _update_frame(logical_hasher, b"table-name", table_name.encode("utf-8"))
                _update_frame(
                    logical_hasher,
                    b"row-count",
                    str(table_counts[table_name]).encode("ascii"),
                )
                _update_frame(
                    logical_hasher,
                    b"table-sha256",
                    bytes.fromhex(table_sha256[table_name]),
                )

            return DatabaseFingerprint(
                quick_check="ok",
                integrity_check="ok",
                foreign_key_violations=0,
                schema_sha256=schema_sha256,
                logical_sha256=logical_hasher.hexdigest(),
                table_counts=table_counts,
                table_sha256=table_sha256,
                content_counts=content_counts,
                content_sha256=content_sha256,
                legacy_schema_migrations=legacy_schema_migrations,
                alembic_version=alembic_version,
                page_size=_pragma_int(connection, "page_size"),
                page_count=_pragma_int(connection, "page_count"),
                freelist_count=_pragma_int(connection, "freelist_count"),
                schema_version=_pragma_int(connection, "schema_version"),
                user_version=_pragma_int(connection, "user_version"),
                application_id=_pragma_int(connection, "application_id"),
            )
    except DatabaseBackupError:
        raise
    except sqlite3.Error as error:
        raise DatabaseBackupError(
            "BACKUP_SQLITE_INVALID",
            f"Could not inspect SQLite backup: {error}",
        ) from error


def _fingerprint_table(
    connection: sqlite3.Connection,
    table_name: str,
) -> tuple[int, str, dict[str, int], dict[str, str]]:
    quoted_table = _quote_identifier(table_name)
    probe = connection.execute(f"SELECT * FROM {quoted_table} LIMIT 0")
    column_names = tuple(str(column[0]) for column in probe.description or ())
    table_info = list(connection.execute(f"PRAGMA table_xinfo({quoted_table})"))
    primary_key_columns = [
        (int(row[5]), str(row[1]))
        for row in table_info
        if int(row[5]) > 0 and str(row[1]) in column_names
    ]
    primary_key_columns.sort(key=lambda item: item[0])
    order_columns = [name for _position, name in primary_key_columns]
    if not order_columns:
        order_columns = list(column_names)

    sql = f"SELECT * FROM {quoted_table}"
    if order_columns:
        sql += " ORDER BY " + ", ".join(_quote_identifier(name) for name in order_columns)
    cursor = connection.execute(sql)
    hasher = hashlib.sha256()
    for column_name in column_names:
        _update_frame(hasher, b"column", column_name.encode("utf-8"))
    critical_hashers = _build_critical_content_hashers(table_name, column_names)
    processing_job_indexes = (
        {name: index for index, name in enumerate(column_names)}
        if table_name == "processing_jobs" and "spec_json" in column_names
        else None
    )

    row_count = 0
    while True:
        rows = cursor.fetchmany(256)
        if not rows:
            break
        for row in rows:
            if processing_job_indexes is not None:
                _validate_processing_job_spec(row, processing_job_indexes)
            _update_frame(hasher, b"row", b"")
            for value in row:
                tag, payload = _encode_sqlite_value(value)
                _update_frame(hasher, tag, payload)
            for critical_hasher in critical_hashers:
                critical_hasher.add(row)
            row_count += 1
    return (
        row_count,
        hasher.hexdigest(),
        {item.name: item.count for item in critical_hashers},
        {item.name: item.digest() for item in critical_hashers},
    )


def _build_critical_content_hashers(
    table_name: str,
    column_names: tuple[str, ...],
) -> list[_CriticalContentHasher]:
    specifications: dict[str, tuple[tuple[str, tuple[str, ...], str | None, str], ...]] = {
        "papers": (
            ("paperIds", ("id",), None, "all"),
            ("explainers", ("id", "explainer"), "explainer", "nonblank"),
        ),
        "translations": (
            ("translations", ("paper_id", "content"), "content", "nonblank"),
        ),
        "notes": (
            ("notes", ("paper_id", "content"), "content", "nonblank"),
        ),
        "paper_vectors": (
            (
                "paperVectors",
                ("paper_id", "dim", "vector"),
                "vector",
                "nonnull",
            ),
        ),
    }
    for p2_table_name, p2_specs in P2_CONTENT_PROJECTIONS.items():
        specifications[p2_table_name] = (
            p2_specs + specifications.get(p2_table_name, ())
        )
    for p3_table_name, p3_specs in P3_CONTENT_PROJECTIONS.items():
        specifications[p3_table_name] = (
            p3_specs + specifications.get(p3_table_name, ())
        )
    for core_table_name, core_specs in P1_CORE_CONTENT_PROJECTIONS.items():
        specifications[core_table_name] = (
            core_specs + specifications.get(core_table_name, ())
        )
    indexes_by_name = {name: index for index, name in enumerate(column_names)}
    result: list[_CriticalContentHasher] = []
    for name, columns, filter_column, filter_mode in specifications.get(table_name, ()):
        if not set(columns).issubset(indexes_by_name):
            continue
        hasher = hashlib.sha256()
        for column in columns:
            _update_frame(hasher, b"column", column.encode("utf-8"))
        result.append(
            _CriticalContentHasher(
                name=name,
                columns=columns,
                indexes=tuple(indexes_by_name[column] for column in columns),
                filter_index=(
                    indexes_by_name[filter_column]
                    if filter_column is not None and filter_column in indexes_by_name
                    else None
                ),
                filter_mode=filter_mode,
                hasher=hasher,
            )
        )
    return result


def _validate_processing_job_spec(
    row: Sequence[Any],
    indexes_by_name: Mapping[str, int],
) -> None:
    row_id = row[indexes_by_name["id"]]
    try:
        decode_job_spec_v1(
            row[indexes_by_name["spec_json"]],
            expected_row={
                name: row[indexes_by_name[name]]
                for name in (
                    "job_type",
                    "paper_id",
                    "source_mode",
                    "source_document_id",
                    "artifact_id",
                )
            },
        )
    except (JobSpecValidationError, UnicodeError) as error:
        raise DatabaseBackupError(
            "JOB_SPEC_INVALID",
            f"Processing job {row_id!r} has an invalid canonical job spec.",
        ) from error


def _p2_schema_content_inventory(
    connection: sqlite3.Connection,
    schema_rows: Sequence[Sequence[Any]],
) -> tuple[dict[str, int], dict[str, str]]:
    processing_columns = (
        _table_columns(connection, "processing_jobs")
        if any(row[0] == "table" and row[1] == "processing_jobs" for row in schema_rows)
        else set()
    )
    if "spec_json" not in processing_columns:
        return {}, {}
    trigger_sql = {
        str(row[1]): str(row[3])
        for row in schema_rows
        if row[0] == "trigger" and row[1] in _P2_SPEC_GUARD_INVENTORY
    }
    observed_guard_names = {
        str(row[1])
        for row in schema_rows
        if row[0] == "trigger"
        and str(row[1]).startswith("processing_jobs_spec_guard_")
    }
    if observed_guard_names != set(_P2_SPEC_GUARD_INVENTORY):
        raise DatabaseBackupError(
            "BACKUP_SCHEMA_INVENTORY_INVALID",
            "P2 processing-job spec guard inventory is not exact.",
        )
    counts: dict[str, int] = {}
    hashes: dict[str, str] = {}
    for trigger_name, (content_name, expected_sha256) in _P2_SPEC_GUARD_INVENTORY.items():
        counts[content_name] = 1
        normalized_sql = " ".join(trigger_sql[trigger_name].split())
        actual_sha256 = hashlib.sha256(normalized_sql.encode("utf-8")).hexdigest()
        if actual_sha256 != expected_sha256:
            raise DatabaseBackupError(
                "BACKUP_SCHEMA_INVENTORY_INVALID",
                f"P2 processing-job spec guard {trigger_name!r} has unexpected SQL.",
            )
        hashes[content_name] = actual_sha256
    return counts, hashes


def _is_p3_fts_physical_table(table_name: str) -> bool:
    return table_name == _P3_FTS_TABLE or table_name.startswith(_P3_FTS_SHADOW_PREFIX)


def _is_p3_fts_physical_schema_row(row: Sequence[Any]) -> bool:
    return (
        str(row[0]) == "table"
        and _is_p3_fts_physical_table(str(row[1]))
    )


def _assert_p3_fts_integrity(connection: sqlite3.Connection) -> None:
    present = connection.execute(
        "SELECT 1 FROM sqlite_schema WHERE type='table' AND name=?",
        (_P3_FTS_TABLE,),
    ).fetchone()
    if present is None:
        return
    try:
        with closing(sqlite3.connect(":memory:")) as integrity_probe:
            connection.backup(integrity_probe)
            integrity_probe.execute(
                "INSERT INTO document_chunks_fts(document_chunks_fts,rank) "
                "VALUES('integrity-check',1)"
            )
    except sqlite3.Error as error:
        raise DatabaseBackupError(
            "BACKUP_FTS_INTEGRITY_INVALID",
            "P3 FTS integrity-check failed.",
        ) from error


def _p3_fts_content_inventory(
    connection: sqlite3.Connection,
    schema_rows: Sequence[Sequence[Any]],
    table_names: set[str],
) -> tuple[dict[str, int], dict[str, str]]:
    if _P3_FTS_TABLE not in table_names:
        if any(
            table in table_names
            for table in (
                "document_chunk_embeddings",
                "artifact_translation_checkpoints",
            )
        ):
            raise DatabaseBackupError(
                "BACKUP_FTS_SCHEMA_INVALID",
                "P3 search tables exist without the external-content FTS index.",
            )
        return {}, {}

    for table_name, required_columns in _P3_REQUIRED_TABLE_COLUMNS.items():
        if table_name not in table_names:
            raise DatabaseBackupError(
                "BACKUP_FTS_SCHEMA_INVALID",
                f"P3 search schema is missing {table_name!r}.",
            )
        if not required_columns.issubset(_table_columns(connection, table_name)):
            raise DatabaseBackupError(
                "BACKUP_FTS_SCHEMA_INVALID",
                f"P3 search table {table_name!r} has an incomplete column contract.",
            )

    virtual_table_sql = {
        str(row[1]): str(row[3])
        for row in schema_rows
        if row[0] == "table" and row[1] == _P3_FTS_TABLE
    }.get(_P3_FTS_TABLE, "")
    normalized_virtual_sql = " ".join(virtual_table_sql.lower().split())
    for fragment in (
        "using fts5",
        "content='document_chunks'",
        "content_rowid='rowid'",
    ):
        if fragment not in normalized_virtual_sql:
            raise DatabaseBackupError(
                "BACKUP_FTS_SCHEMA_INVALID",
                "P3 FTS is not the required external-content trigram index.",
            )
    if not any(
        fragment in normalized_virtual_sql
        for fragment in (
            "trigram case_sensitive 0 remove_diacritics 1",
            "trigram case_sensitive 0",
        )
    ):
        raise DatabaseBackupError(
            "BACKUP_FTS_SCHEMA_INVALID",
            "P3 FTS is not the required external-content trigram index.",
        )

    observed_triggers = {
        str(row[1])
        for row in schema_rows
        if row[0] == "trigger" and str(row[1]).startswith(_P3_FTS_SHADOW_PREFIX)
    }
    if observed_triggers != _P3_FTS_TRIGGER_NAMES:
        raise DatabaseBackupError(
            "BACKUP_FTS_SCHEMA_INVALID",
            "P3 FTS trigger inventory is not exact.",
        )
    trigger_sql = {
        str(row[1]): " ".join(str(row[3]).split())
        for row in schema_rows
        if row[0] == "trigger" and row[1] in _P3_FTS_TRIGGER_NAMES
    }
    if any(
        hashlib.sha256(trigger_sql[name].encode("utf-8")).hexdigest()
        != expected_sha256
        for name, expected_sha256 in _P3_FTS_TRIGGER_SHA256.items()
    ):
        raise DatabaseBackupError(
            "BACKUP_FTS_SCHEMA_INVALID",
            "P3 FTS trigger SQL does not match the fixed synchronization contract.",
        )

    _assert_p3_fts_integrity(connection)

    chunk_rows = list(
        connection.execute(
            "SELECT rowid,id,source_document_id,sequence,COALESCE(heading_path,''),content "
            "FROM document_chunks ORDER BY rowid"
        )
    )
    indexed_rowids = [
        int(row[0])
        for row in connection.execute(
            "SELECT rowid FROM document_chunks_fts ORDER BY rowid"
        )
    ]
    expected_rowids = [int(row[0]) for row in chunk_rows]
    if indexed_rowids != expected_rowids:
        raise DatabaseBackupError(
            "BACKUP_FTS_COVERAGE_INVALID",
            "P3 FTS row coverage does not exactly match document_chunks.",
        )

    coverage_hash = _hash_rows(
        ("rowid", "id", "sourceDocumentId", "sequence", "headingPath", "content"),
        chunk_rows,
    )
    integrity_hash = hashlib.sha256(
        b"document_chunks_fts:integrity-check:ok:v1"
    ).hexdigest()
    return (
        {
            "documentChunksFtsCoverage": len(chunk_rows),
            "documentChunksFtsIntegrity": 1,
        },
        {
            "documentChunksFtsCoverage": coverage_hash,
            "documentChunksFtsIntegrity": integrity_hash,
        },
    )


def _legacy_schema_migrations(
    connection: sqlite3.Connection,
    table_names: set[str],
) -> tuple[int, ...]:
    if "schema_migrations" not in table_names:
        return ()
    if "version" not in _table_columns(connection, "schema_migrations"):
        return ()
    versions: list[int] = []
    for row in connection.execute(
        "SELECT version FROM schema_migrations ORDER BY version"
    ):
        value = row[0]
        if isinstance(value, bool):
            raise DatabaseBackupError(
                "BACKUP_SCHEMA_MIGRATION_INVALID",
                "Legacy schema_migrations contains a non-integer version.",
            )
        try:
            parsed = int(value)
        except (TypeError, ValueError, OverflowError) as error:
            raise DatabaseBackupError(
                "BACKUP_SCHEMA_MIGRATION_INVALID",
                "Legacy schema_migrations contains a non-integer version.",
            ) from error
        if isinstance(value, float) and not value.is_integer():
            raise DatabaseBackupError(
                "BACKUP_SCHEMA_MIGRATION_INVALID",
                "Legacy schema_migrations contains a non-integer version.",
            )
        if isinstance(value, str) and value.strip() != str(parsed):
            raise DatabaseBackupError(
                "BACKUP_SCHEMA_MIGRATION_INVALID",
                "Legacy schema_migrations contains a non-canonical integer version.",
            )
        versions.append(parsed)
    return tuple(versions)


def _alembic_version(
    connection: sqlite3.Connection,
    table_names: set[str],
) -> str | None:
    if "alembic_version" not in table_names:
        return None
    if "version_num" not in _table_columns(connection, "alembic_version"):
        return None
    versions = [
        str(row[0])
        for row in connection.execute(
            "SELECT version_num FROM alembic_version ORDER BY version_num"
        )
    ]
    if not versions:
        return None
    if len(versions) != 1:
        raise DatabaseBackupError(
            "BACKUP_ALEMBIC_STATE_AMBIGUOUS",
            "SQLite contains more than one Alembic head.",
        )
    return versions[0]


def _table_columns(connection: sqlite3.Connection, table_name: str) -> set[str]:
    quoted_table = _quote_identifier(table_name)
    return {
        str(row[1])
        for row in connection.execute(f"PRAGMA table_xinfo({quoted_table})")
    }


def _hash_rows(columns: Sequence[str], rows: Iterable[Sequence[Any]]) -> str:
    hasher = hashlib.sha256()
    for column in columns:
        _update_frame(hasher, b"column", column.encode("utf-8"))
    for row in rows:
        _update_frame(hasher, b"row", b"")
        for value in row:
            tag, payload = _encode_sqlite_value(value)
            _update_frame(hasher, tag, payload)
    return hasher.hexdigest()


def _encode_sqlite_value(value: Any) -> tuple[bytes, bytes]:
    if value is None:
        return b"null", b""
    if isinstance(value, int):
        return b"integer", str(value).encode("ascii")
    if isinstance(value, float):
        return b"real", value.hex().encode("ascii")
    if isinstance(value, str):
        return b"text", value.encode("utf-8")
    if isinstance(value, bytes):
        return b"blob", value
    if isinstance(value, memoryview):
        return b"blob", value.tobytes()
    raise DatabaseBackupError(
        "BACKUP_VALUE_UNSUPPORTED",
        f"SQLite returned unsupported value type {type(value).__name__!r}.",
    )


def _update_frame(hasher: Any, tag: bytes, payload: bytes) -> None:
    hasher.update(struct.pack(">I", len(tag)))
    hasher.update(tag)
    hasher.update(struct.pack(">Q", len(payload)))
    hasher.update(payload)


def _pragma_int(connection: sqlite3.Connection, name: str) -> int:
    row = connection.execute(f"PRAGMA {name}").fetchone()
    if row is None:
        raise DatabaseBackupError(
            "BACKUP_SQLITE_INVALID",
            f"SQLite did not return PRAGMA {name}.",
        )
    return int(row[0])


def _quote_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _sha256_file(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(_COPY_BUFFER_SIZE)
            if not chunk:
                break
            hasher.update(chunk)
    return hasher.hexdigest()


def _fsync_file(path: Path) -> None:
    with path.open("rb+") as handle:
        os.fsync(handle.fileno())


def _publish_new_file(
    temporary_path: Path,
    final_path: Path,
    *,
    expected_identity: _FileIdentity | None = None,
    owned_directory: Path | None = None,
    expected_directory_identity: _FileIdentity | None = None,
) -> _PublishedFile:
    source_identity = expected_identity or _read_file_identity(temporary_path)
    try:
        os.link(temporary_path, final_path)
    except FileExistsError as error:
        _best_effort_cleanup(
            _unlink_owned_file,
            temporary_path,
            strict=False,
            expected_identity=source_identity,
            owned_directory=owned_directory,
            expected_directory_identity=expected_directory_identity,
        )
        raise DatabaseBackupError(
            "BACKUP_TARGET_EXISTS",
            f"Refusing to overwrite existing file: {final_path}",
        ) from error
    except OSError as link_error:
        if os.name == "nt":
            return _publish_new_file_with_windows_rename(
                temporary_path,
                final_path,
                link_error=link_error,
                expected_identity=source_identity,
            )
        _best_effort_cleanup(
            _unlink_owned_file,
            temporary_path,
            strict=False,
            expected_identity=source_identity,
            owned_directory=owned_directory,
            expected_directory_identity=expected_directory_identity,
        )
        raise DatabaseBackupError(
            "BACKUP_ATOMIC_PUBLISH_FAILED",
            f"Could not atomically publish {final_path.name}: {link_error}",
        ) from link_error
    linked_identity = _read_file_identity(final_path)
    if not _same_file_object(linked_identity, source_identity):
        raise DatabaseBackupError(
            "BACKUP_PUBLISH_OWNERSHIP_CHANGED",
            f"Published file identity changed before ownership could be bound: {final_path.name}",
        )
    try:
        _unlink_owned_file(
            temporary_path,
            strict=True,
            expected_identity=linked_identity,
            owned_directory=owned_directory,
            expected_directory_identity=expected_directory_identity,
        )
    except DatabaseBackupError as cleanup_error:
        try:
            removed = _unlink_owned_file(
                final_path,
                strict=True,
                expected_identity=linked_identity,
            )
            if not removed:
                raise DatabaseBackupError(
                    "BACKUP_CLEANUP_FAILED",
                    "Could not roll back a generated publication.",
                )
        except DatabaseBackupError:
            raise DatabaseBackupError(
                "BACKUP_CLEANUP_FAILED",
                "Could not remove the generated temporary link or roll back its publication.",
            ) from cleanup_error
        raise cleanup_error
    final_identity = _read_file_identity(final_path)
    if not _same_file_object(final_identity, source_identity):
        raise DatabaseBackupError(
            "BACKUP_PUBLISH_OWNERSHIP_CHANGED",
            f"Published file identity changed during publication: {final_path.name}",
        )
    return _PublishedFile(identity=final_identity)


def _open_windows_file_for_bound_rename(path: Path) -> tuple[Any, _FileIdentity]:
    import ctypes
    import msvcrt
    from ctypes import wintypes

    generic_read = 0x80000000
    delete_access = 0x00010000
    synchronize = 0x00100000
    share_read = 0x00000001
    share_write = 0x00000002
    share_delete = 0x00000004
    open_existing = 3
    open_reparse_point = 0x00200000
    invalid_handle_value = ctypes.c_void_p(-1).value
    create_file = ctypes.WinDLL("kernel32", use_last_error=True).CreateFileW
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
    raw_handle = create_file(
        str(path),
        generic_read | delete_access | synchronize,
        share_read | share_write | share_delete,
        None,
        open_existing,
        open_reparse_point,
        None,
    )
    if raw_handle == invalid_handle_value:
        raise OSError(ctypes.get_last_error(), "CreateFileW failed")

    descriptor: int | None = None
    handle: Any | None = None
    try:
        descriptor = msvcrt.open_osfhandle(
            int(raw_handle),
            os.O_RDONLY | getattr(os, "O_BINARY", 0),
        )
        raw_handle = None
        handle = os.fdopen(descriptor, "rb", buffering=0)
        descriptor = None
        identity = _identity_from_stat(os.fstat(handle.fileno()))
        return handle, identity
    except Exception:
        if handle is not None:
            handle.close()
        elif descriptor is not None:
            os.close(descriptor)
        elif raw_handle not in {None, invalid_handle_value}:
            _close_windows_handle(int(raw_handle))
        raise


def _reopen_windows_bound_file(handle: Any, *, share_delete: bool) -> Any:
    import ctypes
    import msvcrt
    from ctypes import wintypes

    generic_read = 0x80000000
    share_read = 0x00000001
    share_write = 0x00000002
    share_delete_flag = 0x00000004
    open_reparse_point = 0x00200000
    invalid_handle_value = ctypes.c_void_p(-1).value
    reopen_file = ctypes.WinDLL("kernel32", use_last_error=True).ReOpenFile
    reopen_file.argtypes = (
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.DWORD,
    )
    reopen_file.restype = wintypes.HANDLE
    source_handle = wintypes.HANDLE(msvcrt.get_osfhandle(handle.fileno()))
    raw_handle = reopen_file(
        source_handle,
        generic_read,
        share_read | share_write | (share_delete_flag if share_delete else 0),
        open_reparse_point,
    )
    if raw_handle == invalid_handle_value:
        raise OSError(ctypes.get_last_error(), "ReOpenFile failed")

    descriptor: int | None = None
    reopened: Any | None = None
    try:
        descriptor = msvcrt.open_osfhandle(
            int(raw_handle),
            os.O_RDONLY | getattr(os, "O_BINARY", 0),
        )
        raw_handle = None
        reopened = os.fdopen(descriptor, "rb", buffering=0)
        descriptor = None
        return reopened
    except Exception:
        if reopened is not None:
            reopened.close()
        elif descriptor is not None:
            os.close(descriptor)
        elif raw_handle not in {None, invalid_handle_value}:
            _close_windows_handle(int(raw_handle))
        raise


def _rename_windows_handle_no_replace(handle: Any, final_path: Path) -> None:
    import ctypes
    import msvcrt
    from ctypes import wintypes

    class FileRenameInfo(ctypes.Structure):
        _fields_ = (
            ("replace_if_exists", ctypes.c_ubyte),
            ("root_directory", wintypes.HANDLE),
            ("file_name_length", wintypes.DWORD),
            ("file_name", wintypes.WCHAR * 1),
        )

    # Keep the final component lexical: resolving it could follow a racing
    # symlink/reparse point and publish outside the bound output directory.
    encoded_path = os.path.abspath(os.fspath(final_path)).encode("utf-16-le")
    file_name_offset = FileRenameInfo.file_name.offset
    buffer = ctypes.create_string_buffer(file_name_offset + len(encoded_path) + 2)
    information = FileRenameInfo.from_buffer(buffer)
    information.replace_if_exists = 0
    information.root_directory = None
    information.file_name_length = len(encoded_path)
    ctypes.memmove(
        ctypes.addressof(buffer) + file_name_offset,
        encoded_path,
        len(encoded_path),
    )

    set_information = ctypes.WinDLL(
        "kernel32",
        use_last_error=True,
    ).SetFileInformationByHandle
    set_information.argtypes = (
        wintypes.HANDLE,
        ctypes.c_int,
        wintypes.LPVOID,
        wintypes.DWORD,
    )
    set_information.restype = wintypes.BOOL
    raw_handle = wintypes.HANDLE(msvcrt.get_osfhandle(handle.fileno()))
    if set_information(raw_handle, 3, buffer, len(buffer)):
        return
    windows_error = ctypes.get_last_error()
    if windows_error in {5, 80, 183} and os.path.lexists(final_path):
        raise FileExistsError(windows_error, "Destination already exists", str(final_path))
    raise OSError(windows_error, "SetFileInformationByHandle(FileRenameInfo) failed")


def _publish_new_file_with_windows_rename(
    temporary_path: Path,
    final_path: Path,
    *,
    link_error: OSError,
    expected_identity: _FileIdentity,
) -> _PublishedFile:
    handle: Any | None = None
    renamed = False
    try:
        handle, source_identity = _open_windows_file_for_bound_rename(temporary_path)
        if not _same_file_object(source_identity, expected_identity):
            raise DatabaseBackupError(
                "BACKUP_STAGING_OWNERSHIP_CHANGED",
                "The private backup staging file was replaced before publication.",
            )
        source_sha256 = _sha256_descriptor(handle.fileno())
        hashed_source_identity = _identity_from_stat(os.fstat(handle.fileno()))
        if hashed_source_identity != source_identity:
            raise DatabaseBackupError(
                "BACKUP_STAGING_OWNERSHIP_CHANGED",
                "The private backup staging file changed before publication.",
            )
        try:
            _rename_windows_handle_no_replace(handle, final_path)
            renamed = True
        except FileExistsError as error:
            raise DatabaseBackupError(
                "BACKUP_TARGET_EXISTS",
                f"Refusing to overwrite existing file: {final_path}",
            ) from error
        except OSError as rename_error:
            raise DatabaseBackupError(
                "BACKUP_ATOMIC_PUBLISH_FAILED",
                (
                    f"Could not atomically publish {final_path.name}; hard-link and "
                    f"Windows no-replace rename both failed: {rename_error}"
                ),
            ) from link_error

        transitional_handle = _reopen_windows_bound_file(handle, share_delete=True)
        handle.close()
        handle = transitional_handle
        protected_handle = _reopen_windows_bound_file(handle, share_delete=False)
        handle.close()
        handle = protected_handle
        handle_identity = _identity_from_stat(os.fstat(handle.fileno()))
        path_identity = _read_file_identity(final_path)
        published_sha256 = _sha256_descriptor(handle.fileno())
        final_handle_identity = _identity_from_stat(os.fstat(handle.fileno()))
        final_path_identity = _read_file_identity(final_path)
        if (
            not _same_file_object(path_identity, handle_identity)
            or final_handle_identity != handle_identity
            or not _same_file_object(final_path_identity, final_handle_identity)
            or published_sha256 != source_sha256
        ):
            raise DatabaseBackupError(
                "BACKUP_PUBLISH_OWNERSHIP_CHANGED",
                f"Published file identity changed during publication: {final_path.name}",
            )
        publication = _PublishedFile(
            identity=final_handle_identity,
            windows_handle=handle,
        )
        handle = None
        return publication
    except Exception as error:
        cleanup_error: DatabaseBackupError | None = None
        cleanup_identity: _FileIdentity | None = None
        if handle is not None and renamed:
            try:
                cleanup_identity = _identity_from_stat(os.fstat(handle.fileno()))
            except OSError:
                cleanup_identity = None
        if handle is not None:
            handle.close()
        if cleanup_identity is not None:
            try:
                _unlink_owned_file(
                    final_path,
                    strict=True,
                    expected_identity=cleanup_identity,
                )
            except DatabaseBackupError as caught:
                cleanup_error = caught
        if cleanup_error is not None:
            raise cleanup_error from error
        raise


def _cleanup_published_file(
    publication: _PublishedFile,
    path: Path,
    *,
    strict: bool,
    owned_directory: Path,
    expected_directory_identity: _FileIdentity,
) -> None:
    if publication.windows_handle is not None:
        publication.close()
    _unlink_owned_file(
        path,
        strict=strict,
        expected_identity=publication.identity,
        owned_directory=owned_directory,
        expected_directory_identity=expected_directory_identity,
    )


def _write_manifest(
    manifest_path: Path,
    manifest: BackupManifest,
    *,
    staging_directory: Path,
    staging_directory_identity: _FileIdentity,
) -> _PublishedFile:
    temporary_path = staging_directory / f"manifest-{uuid.uuid4().hex}.json"
    temporary_owned = False
    temporary_identity: _FileIdentity | None = None
    try:
        payload = (
            json.dumps(
                manifest.to_dict(),
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            + "\n"
        ).encode("utf-8")
        with _OwnedExclusiveFile(temporary_path) as handle:
            temporary_owned = True
            temporary_identity = handle.identity
            handle.write(payload)
            handle.flush_and_sync()
        published_file = _publish_new_file(
            temporary_path,
            manifest_path,
            expected_identity=temporary_identity,
            owned_directory=staging_directory,
            expected_directory_identity=staging_directory_identity,
        )
        temporary_owned = False
        return published_file
    except DatabaseBackupError:
        if temporary_owned:
            _best_effort_cleanup(
                _unlink_owned_file,
                temporary_path,
                strict=False,
                expected_identity=temporary_identity,
                owned_directory=staging_directory,
                expected_directory_identity=staging_directory_identity,
            )
        raise
    except OSError as error:
        if temporary_owned:
            _best_effort_cleanup(
                _unlink_owned_file,
                temporary_path,
                strict=False,
                expected_identity=temporary_identity,
                owned_directory=staging_directory,
                expected_directory_identity=staging_directory_identity,
            )
        raise DatabaseBackupError(
            "BACKUP_MANIFEST_WRITE_FAILED",
            "Could not write backup manifest.",
        ) from error


def _manifest_payload_sha256(manifest: BackupManifest) -> str:
    payload = json.dumps(
        manifest.payload_dict(),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _load_manifest(manifest_path: Path) -> BackupManifest:
    manifest, _file_sha256 = _load_manifest_document(manifest_path)
    return manifest


def _load_manifest_document(manifest_path: Path) -> tuple[BackupManifest, str]:
    if not manifest_path.is_file():
        raise DatabaseBackupError(
            "BACKUP_MANIFEST_NOT_FOUND",
            f"Backup manifest does not exist: {manifest_path}",
        )
    try:
        payload = manifest_path.read_bytes()
        value = json.loads(payload.decode("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise DatabaseBackupError(
            "BACKUP_MANIFEST_INVALID",
            f"Could not read backup manifest: {error}",
        ) from error
    if not isinstance(value, dict):
        raise DatabaseBackupError(
            "BACKUP_MANIFEST_INVALID",
            "Backup manifest must be a JSON object.",
        )
    return BackupManifest.from_dict(value), hashlib.sha256(payload).hexdigest()


def _reject_unknown_fields(
    value: Mapping[str, Any],
    *,
    allowed_fields: frozenset[str],
    description: str,
) -> None:
    unknown_fields = sorted(
        repr(key) for key in value if not isinstance(key, str) or key not in allowed_fields
    )
    if unknown_fields:
        rendered_fields = ", ".join(unknown_fields)
        raise DatabaseBackupError(
            "BACKUP_MANIFEST_INVALID",
            f"{description} contains unknown field(s): {rendered_fields}.",
        )


def _required_mapping(value: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    result = value[key]
    if not isinstance(result, dict):
        raise DatabaseBackupError(
            "BACKUP_MANIFEST_INVALID",
            f"Backup manifest field {key!r} must be an object.",
        )
    return result


def _required_string(value: Mapping[str, Any], key: str) -> str:
    result = value[key]
    if not isinstance(result, str) or not result:
        raise DatabaseBackupError(
            "BACKUP_MANIFEST_INVALID",
            f"Backup manifest field {key!r} must be a non-empty string.",
        )
    if "\x00" in result:
        raise DatabaseBackupError(
            "BACKUP_MANIFEST_INVALID",
            f"Backup manifest field {key!r} contains an embedded NUL character.",
        )
    return result


def _required_int(value: Mapping[str, Any], key: str) -> int:
    result = value[key]
    if isinstance(result, bool) or not isinstance(result, int):
        raise DatabaseBackupError(
            "BACKUP_MANIFEST_INVALID",
            f"Backup manifest field {key!r} must be an integer.",
        )
    return result


def _required_optional_string(value: Mapping[str, Any], key: str) -> str | None:
    result = value[key]
    if result is None:
        return None
    if not isinstance(result, str) or not result:
        raise DatabaseBackupError(
            "BACKUP_MANIFEST_INVALID",
            f"Backup manifest field {key!r} must be null or a non-empty string.",
        )
    return result


def _required_int_sequence(value: Mapping[str, Any], key: str) -> tuple[int, ...]:
    result = value[key]
    if not isinstance(result, list):
        raise DatabaseBackupError(
            "BACKUP_MANIFEST_INVALID",
            f"Backup manifest field {key!r} must be an array.",
        )
    if any(isinstance(item, bool) or not isinstance(item, int) for item in result):
        raise DatabaseBackupError(
            "BACKUP_MANIFEST_INVALID",
            f"Backup manifest field {key!r} must contain only integers.",
        )
    return tuple(result)


def _required_sha256(value: Mapping[str, Any], key: str) -> str:
    result = _required_string(value, key)
    if not re.fullmatch(r"[0-9a-f]{64}", result):
        raise DatabaseBackupError(
            "BACKUP_MANIFEST_INVALID",
            f"Backup manifest field {key!r} must be a lowercase SHA-256 value.",
        )
    return result


def _string_int_mapping(value: Any, field: str) -> dict[str, int]:
    if not isinstance(value, dict):
        raise DatabaseBackupError(
            "BACKUP_MANIFEST_INVALID",
            f"Backup manifest field {field!r} must be an object.",
        )
    result: dict[str, int] = {}
    for key, item in value.items():
        if not isinstance(key, str) or isinstance(item, bool) or not isinstance(item, int) or item < 0:
            raise DatabaseBackupError(
                "BACKUP_MANIFEST_INVALID",
                f"Backup manifest field {field!r} contains an invalid table count.",
            )
        result[key] = item
    return result


def _string_hash_mapping(value: Any, field: str) -> dict[str, str]:
    if not isinstance(value, dict):
        raise DatabaseBackupError(
            "BACKUP_MANIFEST_INVALID",
            f"Backup manifest field {field!r} must be an object.",
        )
    result: dict[str, str] = {}
    for key, item in value.items():
        if (
            not isinstance(key, str)
            or not isinstance(item, str)
            or re.fullmatch(r"[0-9a-f]{64}", item) is None
        ):
            raise DatabaseBackupError(
                "BACKUP_MANIFEST_INVALID",
                f"Backup manifest field {field!r} contains an invalid table hash.",
            )
        result[key] = item
    return result
