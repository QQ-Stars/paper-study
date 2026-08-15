from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import stat
import tempfile
from typing import Protocol
import uuid

from backend.app.api.compat.build_identity import (
    BuildIdentityError,
    load_build_identity_manifest,
)
from backend.app.api.compat.database_identity import (
    DatabaseEvidenceIdentityService,
    DatabaseIdentityError,
    canonical_json_bytes,
    exclusive_write_bytes,
    load_database_evidence_identity_manifest,
    read_platform_file_identity,
    verify_database_evidence_identity_subject,
    verify_descendant_database_evidence_identity,
)
from backend.app.api.compat.schema_inventory import (
    SchemaInventoryError,
    capture_inventory,
)
from backend.app.application.production_rollback import ROLLBACK_TAIL_EVENTS
from backend.app.infrastructure.database_backup import (
    DatabaseBackupError,
    inspect_database,
    restore_backup_for_validation,
    verify_backup,
)


RECOVERY_SMOKE_EVENTS = (
    "frozen_node_stopped",
    "legacy_connections_released",
    "python_api_started",
    "worker_lock_acquired",
    "scheduler_lock_acquired",
    "mcp_ready",
    "readiness_passed",
    "readonly_smoke_passed",
    "python_runtime_stopped",
    "role_locks_released",
)


class CompatibilityRehearsalError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(f"{code}: {message}")


@dataclass(frozen=True, slots=True)
class CompatibilitySmokeRequest:
    operation: str
    database_path: Path
    build_identity_manifest_path: Path
    build_id: str
    database_identity_manifest_path: Path
    database_lineage_id: str
    subject_database_id: str
    profile: str


@dataclass(frozen=True, slots=True)
class CompatibilitySmokeObservation:
    database_path: Path
    events: tuple[str, ...]
    stopped: bool
    live_path_access_count: int = 0
    live_owner_write_count: int = 0
    real_network_call_count: int = 0


class CompatibilitySmokeRunner(Protocol):
    def run(self, request: CompatibilitySmokeRequest) -> CompatibilitySmokeObservation: ...


@dataclass(frozen=True, slots=True)
class CompatibilitySmokeResult:
    evidence_path: Path
    evidence_file_sha256: str
    build_id: str
    database_lineage_id: str
    subject_database_id: str
    events: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "ok": True,
            "evidencePath": str(self.evidence_path),
            "evidenceFileSha256": self.evidence_file_sha256,
            "buildId": self.build_id,
            "databaseLineageId": self.database_lineage_id,
            "subjectDatabaseId": self.subject_database_id,
            "events": list(self.events),
        }


@dataclass(frozen=True, slots=True)
class RestoreInstallRehearsalResult:
    target_database_path: Path
    recovery_database_path: Path
    installed_database_identity_manifest_path: Path
    inventory_path: Path
    evidence_path: Path
    evidence_file_sha256: str
    build_id: str
    database_lineage_id: str
    subject_database_id: str

    def to_dict(self) -> dict[str, object]:
        return {
            "ok": True,
            "targetDatabasePath": str(self.target_database_path),
            "recoveryDatabasePath": str(self.recovery_database_path),
            "installedDatabaseIdentityManifestPath": str(
                self.installed_database_identity_manifest_path
            ),
            "inventoryPath": str(self.inventory_path),
            "evidencePath": str(self.evidence_path),
            "evidenceFileSha256": self.evidence_file_sha256,
            "buildId": self.build_id,
            "databaseLineageId": self.database_lineage_id,
            "subjectDatabaseId": self.subject_database_id,
        }


class RollbackSmokeService:
    def __init__(self, runner: CompatibilitySmokeRunner) -> None:
        self._runner = runner

    def run(
        self,
        *,
        database: Path,
        build_identity_manifest: Path,
        database_identity_manifest: Path,
        rollback_profile: str,
        evidence_output: Path,
    ) -> CompatibilitySmokeResult:
        return _run_smoke(
            runner=self._runner,
            operation="rollback-smoke",
            expected_profile="frozen-node",
            profile=rollback_profile,
            expected_events=ROLLBACK_TAIL_EVENTS,
            database=database,
            build_identity_manifest=build_identity_manifest,
            database_identity_manifest=database_identity_manifest,
            evidence_output=evidence_output,
        )


class RecoverySmokeService:
    def __init__(self, runner: CompatibilitySmokeRunner) -> None:
        self._runner = runner

    def run(
        self,
        *,
        database: Path,
        build_identity_manifest: Path,
        database_identity_manifest: Path,
        python_profile: str,
        evidence_output: Path,
    ) -> CompatibilitySmokeResult:
        return _run_smoke(
            runner=self._runner,
            operation="recovery-smoke",
            expected_profile="production",
            profile=python_profile,
            expected_events=RECOVERY_SMOKE_EVENTS,
            database=database,
            build_identity_manifest=build_identity_manifest,
            database_identity_manifest=database_identity_manifest,
            evidence_output=evidence_output,
        )


class RestoreInstallRehearsalService:
    def run(
        self,
        *,
        backup: Path,
        manifest: Path,
        target_database: Path,
        expected_target_sha256: str,
        rehearsal_root: Path,
        build_identity_manifest: Path,
        parent_database_identity_manifest: Path,
        installed_database_identity_output: Path,
        evidence_output: Path,
    ) -> RestoreInstallRehearsalResult:
        if not _is_sha256(expected_target_sha256):
            raise CompatibilityRehearsalError(
                "RESTORE_REHEARSAL_TARGET_SHA_INVALID",
                "The expected target SHA-256 must be one exact lowercase digest.",
            )
        root = _exact_existing_directory(rehearsal_root, "rehearsal root")
        target = _exact_unaliased_file(target_database, "rehearsal target database")
        _require_contained(target, root)
        identity_output = _exact_new_output(
            installed_database_identity_output,
            "installed database identity",
        )
        evidence_path = _exact_new_output(evidence_output, "restore rehearsal evidence")
        inventory_path = evidence_path.with_name(f"{evidence_path.stem}.inventory.json")
        if inventory_path.exists() or not inventory_path.parent.is_dir():
            raise CompatibilityRehearsalError(
                "RESTORE_REHEARSAL_OUTPUT_INVALID",
                "The derived inventory evidence path must be exclusive-new.",
            )
        for output in (identity_output, inventory_path, evidence_path):
            if output.parent not in {root, root.parent}:
                raise CompatibilityRehearsalError(
                    "RESTORE_REHEARSAL_OUTPUT_INVALID",
                    "Restore rehearsal outputs must remain in the isolated run root.",
                )

        backup_path = _exact_unaliased_file(backup, "backup")
        manifest_path = _exact_unaliased_file(manifest, "backup Manifest")
        build_identity_path = _exact_unaliased_file(
            build_identity_manifest,
            "build identity manifest",
        )
        parent_identity_path = _exact_unaliased_file(
            parent_database_identity_manifest,
            "parent database identity manifest",
        )
        try:
            build = load_build_identity_manifest(build_identity_path)
            parent = load_database_evidence_identity_manifest(
                parent_identity_path
            )
            if parent.subject_kind != "live":
                raise CompatibilityRehearsalError(
                    "RESTORE_REHEARSAL_PARENT_INVALID",
                    "Restore-install rehearsal requires an exact Live parent identity.",
                )
            verify_database_evidence_identity_subject(
                database=parent.database_path,
                identity=parent,
            )
            verification = verify_backup(backup_path, manifest_path)
            backup_fingerprint = inspect_database(backup_path)
        except CompatibilityRehearsalError:
            raise
        except (BuildIdentityError, DatabaseIdentityError, DatabaseBackupError, OSError) as error:
            raise CompatibilityRehearsalError(
                "RESTORE_REHEARSAL_INPUT_INVALID",
                "Restore-install rehearsal rejected a typed identity or backup input.",
            ) from error

        target_platform_identity = read_platform_file_identity(target)
        if (
            target == parent.database_path
            or target_platform_identity == parent.platform_file_identity
        ):
            raise CompatibilityRehearsalError(
                "RESTORE_REHEARSAL_LIVE_TARGET",
                "Restore-install rehearsal cannot target or alias the Live parent database.",
            )
        protected_paths = {
            backup_path,
            manifest_path,
            target,
            build.manifest_path,
            parent.manifest_path,
            parent.database_path,
        }
        if target in {backup_path, manifest_path, build.manifest_path, parent.manifest_path}:
            raise CompatibilityRehearsalError(
                "RESTORE_REHEARSAL_TARGET_INVALID",
                "The isolated install target must be distinct from all evidence inputs.",
            )
        outputs = (identity_output, inventory_path, evidence_path)
        if len(set(outputs)) != len(outputs) or any(
            output in protected_paths for output in outputs
        ):
            raise CompatibilityRehearsalError(
                "RESTORE_REHEARSAL_OUTPUT_INVALID",
                "Restore rehearsal evidence must be distinct from every protected input.",
            )
        _assert_no_sidecars(target)
        actual_target_sha256 = _file_sha256(target)
        if actual_target_sha256 != expected_target_sha256:
            raise CompatibilityRehearsalError(
                "RESTORE_REHEARSAL_TARGET_DRIFT",
                "The isolated target changed before restore installation.",
            )
        _validate_p3_fingerprint(backup_fingerprint)

        root_identity = _directory_identity(root)
        target_parent_identity = _directory_identity(target.parent)
        recovery_path = target.with_name(
            f"{target.name}.recovery-{expected_target_sha256}"
        )
        stage_path = target.with_name(f".{target.name}.install-{uuid.uuid4().hex}.tmp")
        if recovery_path.exists() or stage_path.exists():
            raise CompatibilityRehearsalError(
                "RESTORE_REHEARSAL_RECOVERY_EXISTS",
                "Restore-install recovery and staging paths must be exclusive-new.",
            )

        validation_root = root / f"restore-install-validation-{uuid.uuid4().hex}"
        try:
            restored = restore_backup_for_validation(
                backup_path,
                manifest_path,
                validation_root,
            )
            if restored.restored_path is None:
                raise CompatibilityRehearsalError(
                    "RESTORE_REHEARSAL_SOURCE_INVALID",
                    "The bound restore-check did not produce an installed source.",
                )
            restored_path = restored.restored_path.resolve(strict=True)
            _require_contained(restored_path, root)
            if (
                restored.backup_id != verification.backup_id
                or restored.manifest_file_sha256 != verification.manifest_file_sha256
                or _file_sha256(restored_path) != verification.backup_sha256
            ):
                raise CompatibilityRehearsalError(
                    "RESTORE_REHEARSAL_SOURCE_INVALID",
                    "The bound restore-check source does not match the exact backup pair.",
                )
            if _file_sha256(backup_path) != verification.backup_sha256:
                raise CompatibilityRehearsalError(
                    "RESTORE_REHEARSAL_SOURCE_INVALID",
                    "The exact backup changed after the bound restore-check.",
                )
            _copy_exclusive(backup_path, stage_path)
            if _file_sha256(stage_path) != verification.backup_sha256:
                raise CompatibilityRehearsalError(
                    "RESTORE_REHEARSAL_STAGE_INVALID",
                    "The staged install copy does not match the verified backup.",
                )
            _assert_directory_identity(root, root_identity)
            _assert_directory_identity(target.parent, target_parent_identity)
            _assert_no_sidecars(target)
            if (
                read_platform_file_identity(target) != target_platform_identity
                or _file_sha256(target) != expected_target_sha256
            ):
                raise CompatibilityRehearsalError(
                    "RESTORE_REHEARSAL_TARGET_DRIFT",
                    "The isolated target changed before atomic installation.",
                )
            os.replace(target, recovery_path)
            try:
                os.replace(stage_path, target)
            except Exception:
                os.replace(recovery_path, target)
                raise
            _fsync_directory(target.parent)
        except CompatibilityRehearsalError:
            raise
        except (DatabaseBackupError, OSError) as error:
            raise CompatibilityRehearsalError(
                "RESTORE_REHEARSAL_INSTALL_FAILED",
                "The isolated bound restore installation failed.",
            ) from error

        try:
            if (
                _file_sha256(target) != verification.backup_sha256
                or _file_sha256(recovery_path) != expected_target_sha256
            ):
                raise CompatibilityRehearsalError(
                    "RESTORE_REHEARSAL_INSTALL_MISMATCH",
                    "Installed or retained database bytes failed SHA-256 verification.",
                )
            installed_fingerprint = inspect_database(target)
            _validate_p3_fingerprint(installed_fingerprint)
            temporary_identity_path = validation_root / "installed-identity.json"
            installed = DatabaseEvidenceIdentityService().create_descendant_database_identity(
                database=target,
                subject_kind="restore_install_rehearsal",
                parent_database_identity_manifest=parent.manifest_path,
                parent_backup=backup_path,
                parent_manifest=manifest_path,
                output=temporary_identity_path,
            )
            temporary_inventory_path = validation_root / "installed-inventory.json"
            inventory = capture_inventory(
                database=target,
                database_identity_manifest=temporary_identity_path,
                output=temporary_inventory_path,
            )
        except (
            CompatibilityRehearsalError,
            DatabaseBackupError,
            DatabaseIdentityError,
            SchemaInventoryError,
            OSError,
        ) as error:
            _restore_original_target(
                target=target,
                recovery_path=recovery_path,
                failed_install_path=stage_path,
            )
            if isinstance(error, CompatibilityRehearsalError):
                raise
            raise CompatibilityRehearsalError(
                "RESTORE_REHEARSAL_POST_INSTALL_INVALID",
                "The installed database failed identity or P3 inventory verification.",
            ) from error

        identity_payload = installed.canonical_bytes
        inventory_payload = canonical_json_bytes(inventory)
        try:
            exclusive_write_bytes(identity_output, identity_payload)
            exclusive_write_bytes(inventory_path, inventory_payload)
        except DatabaseIdentityError as error:
            raise CompatibilityRehearsalError(error.code, str(error)) from error
        installed_identity = load_database_evidence_identity_manifest(identity_output)
        installed_platform_identity = read_platform_file_identity(target)
        document = {
            "schemaVersion": 1,
            "evidenceKind": "restore-install-rehearsal",
            "ok": True,
            "buildIdentityManifestPath": str(build.manifest_path),
            "buildIdentityManifestFileSha256": build.manifest_file_sha256,
            "buildId": build.build_id,
            "backupPath": str(backup_path),
            "backupId": verification.backup_id,
            "backupSha256": verification.backup_sha256,
            "manifestPath": str(manifest_path),
            "manifestFileSha256": verification.manifest_file_sha256,
            "targetDatabasePath": str(target),
            "expectedTargetSha256": expected_target_sha256,
            "recoveryDatabasePath": str(recovery_path),
            "installedDatabaseSha256": verification.backup_sha256,
            "installedDatabaseIdentityManifestPath": str(identity_output),
            "installedDatabaseIdentityManifestFileSha256": (
                installed_identity.identity_manifest_file_sha256
            ),
            "databaseLineageId": installed_identity.database_lineage_id,
            "subjectDatabaseId": installed_identity.subject_database_id,
            "subjectKind": installed_identity.subject_kind,
            "parentDatabaseIdentityManifestPath": str(parent.manifest_path),
            "parentDatabaseIdentityManifestFileSha256": parent.identity_manifest_file_sha256,
            "parentSubjectDatabaseId": parent.subject_database_id,
            "originalTargetPlatformIdentity": target_platform_identity.to_dict(),
            "installedPlatformFileIdentity": installed_platform_identity.to_dict(),
            "inventoryPath": str(inventory_path),
            "inventoryFileSha256": hashlib.sha256(inventory_payload).hexdigest(),
            "alembicRevision": inventory["alembic"]["revision"],
            "processingJobCount": inventory["processingJobs"]["count"],
            "processingJobSpecCount": inventory["processingJobSpecs"]["count"],
            "processingJobStrictDecodeCount": inventory["processingJobs"][
                "strictDecodeCount"
            ],
            "triggerCount": len(inventory["triggers"]),
        }
        evidence_payload = canonical_json_bytes(document)
        try:
            exclusive_write_bytes(evidence_path, evidence_payload)
        except DatabaseIdentityError as error:
            raise CompatibilityRehearsalError(error.code, str(error)) from error
        return RestoreInstallRehearsalResult(
            target_database_path=target,
            recovery_database_path=recovery_path,
            installed_database_identity_manifest_path=identity_output,
            inventory_path=inventory_path,
            evidence_path=evidence_path,
            evidence_file_sha256=hashlib.sha256(evidence_payload).hexdigest(),
            build_id=build.build_id,
            database_lineage_id=installed_identity.database_lineage_id,
            subject_database_id=installed_identity.subject_database_id,
        )


def _run_smoke(
    *,
    runner: CompatibilitySmokeRunner,
    operation: str,
    expected_profile: str,
    profile: str,
    expected_events: tuple[str, ...],
    database: Path,
    build_identity_manifest: Path,
    database_identity_manifest: Path,
    evidence_output: Path,
) -> CompatibilitySmokeResult:
    if profile != expected_profile:
        raise CompatibilityRehearsalError(
            "COMPATIBILITY_REHEARSAL_PROFILE_INVALID",
            f"{operation} requires the exact {expected_profile!r} profile.",
        )
    database_path = _exact_existing_file(database, "rehearsal database")
    output_path = _exact_new_output(evidence_output, "rehearsal evidence")
    build_identity_path = _exact_unaliased_file(
        build_identity_manifest,
        "build identity manifest",
    )
    database_identity_path = _exact_unaliased_file(
        database_identity_manifest,
        "database identity manifest",
    )
    try:
        build = load_build_identity_manifest(build_identity_path)
        identity = load_database_evidence_identity_manifest(database_identity_path)
        verify_descendant_database_evidence_identity(
            database=database_path,
            identity=identity,
        )
        platform_identity = read_platform_file_identity(database_path)
    except (BuildIdentityError, DatabaseIdentityError, OSError) as error:
        raise CompatibilityRehearsalError(
            "COMPATIBILITY_REHEARSAL_IDENTITY_INVALID",
            f"{operation} rejected a typed build or database identity.",
        ) from error
    protected = {
        database_path,
        build.manifest_path,
        identity.manifest_path,
        identity.parent_database_identity_manifest_path,
    }
    if output_path in protected:
        raise CompatibilityRehearsalError(
            "COMPATIBILITY_REHEARSAL_OUTPUT_INVALID",
            "Rehearsal evidence must be distinct from all identity inputs.",
        )

    request = CompatibilitySmokeRequest(
        operation=operation,
        database_path=database_path,
        build_identity_manifest_path=build.manifest_path,
        build_id=build.build_id,
        database_identity_manifest_path=identity.manifest_path,
        database_lineage_id=identity.database_lineage_id,
        subject_database_id=identity.subject_database_id,
        profile=profile,
    )
    try:
        observation = runner.run(request)
    except Exception as error:
        raise CompatibilityRehearsalError(
            "COMPATIBILITY_REHEARSAL_RUNNER_FAILED",
            f"{operation} failed inside the isolated runtime boundary.",
        ) from error
    try:
        observed_database_path = observation.database_path.resolve(strict=True)
    except OSError as error:
        raise CompatibilityRehearsalError(
            "COMPATIBILITY_REHEARSAL_OBSERVATION_INVALID",
            f"{operation} returned an unreadable database observation.",
        ) from error
    if (
        observed_database_path != database_path
        or observation.events != expected_events
        or not observation.stopped
        or observation.live_path_access_count != 0
        or observation.live_owner_write_count != 0
        or observation.real_network_call_count != 0
    ):
        raise CompatibilityRehearsalError(
            "COMPATIBILITY_REHEARSAL_OBSERVATION_INVALID",
            f"{operation} returned an invalid isolation or event-order observation.",
        )

    try:
        verify_descendant_database_evidence_identity(
            database=database_path,
            identity=identity,
        )
        if read_platform_file_identity(database_path) != platform_identity:
            raise CompatibilityRehearsalError(
                "COMPATIBILITY_REHEARSAL_DATABASE_REPLACED",
                f"{operation} replaced the descendant database file object.",
            )
        with tempfile.TemporaryDirectory(prefix="study-app-p6-smoke-") as temporary:
            inventory = capture_inventory(
                database=database_path,
                database_identity_manifest=identity.manifest_path,
                output=Path(temporary) / "inventory.json",
            )
    except CompatibilityRehearsalError:
        raise
    except (DatabaseIdentityError, SchemaInventoryError, OSError) as error:
        raise CompatibilityRehearsalError(
            "COMPATIBILITY_REHEARSAL_DATABASE_INVALID",
            f"{operation} did not preserve the exact P3 database contract.",
        ) from error

    document = {
        "schemaVersion": 1,
        "evidenceKind": operation,
        "ok": True,
        "operation": operation,
        "profile": profile,
        "buildIdentityManifestPath": str(build.manifest_path),
        "buildIdentityManifestFileSha256": build.manifest_file_sha256,
        "buildId": build.build_id,
        "databasePath": str(database_path),
        "databaseIdentityManifestPath": str(identity.manifest_path),
        "databaseIdentityManifestFileSha256": identity.identity_manifest_file_sha256,
        "databaseLineageId": identity.database_lineage_id,
        "subjectDatabaseId": identity.subject_database_id,
        "subjectKind": identity.subject_kind,
        "parentSubjectDatabaseId": identity.parent_subject_database_id,
        "platformFileIdentity": platform_identity.to_dict(),
        "inventorySha256": hashlib.sha256(canonical_json_bytes(inventory)).hexdigest(),
        "events": list(observation.events),
        "runtimeStopped": observation.stopped,
        "livePathAccessCount": observation.live_path_access_count,
        "liveOwnerWriteCount": observation.live_owner_write_count,
        "realNetworkCallCount": observation.real_network_call_count,
    }
    payload = canonical_json_bytes(document)
    try:
        exclusive_write_bytes(output_path, payload)
    except DatabaseIdentityError as error:
        raise CompatibilityRehearsalError(error.code, str(error)) from error
    return CompatibilitySmokeResult(
        evidence_path=output_path,
        evidence_file_sha256=hashlib.sha256(payload).hexdigest(),
        build_id=build.build_id,
        database_lineage_id=identity.database_lineage_id,
        subject_database_id=identity.subject_database_id,
        events=observation.events,
    )


def _exact_existing_file(value: Path, description: str) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        raise CompatibilityRehearsalError(
            "COMPATIBILITY_REHEARSAL_PATH_INVALID",
            f"The {description} path must be exact and absolute.",
        )
    try:
        resolved = path.resolve(strict=True)
    except OSError as error:
        raise CompatibilityRehearsalError(
            "COMPATIBILITY_REHEARSAL_PATH_INVALID",
            f"The {description} path does not exist.",
        ) from error
    if not resolved.is_file():
        raise CompatibilityRehearsalError(
            "COMPATIBILITY_REHEARSAL_PATH_INVALID",
            f"The {description} path must name a file.",
        )
    return resolved


def _exact_new_output(value: Path, description: str) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        raise CompatibilityRehearsalError(
            "COMPATIBILITY_REHEARSAL_PATH_INVALID",
            f"The {description} path must be exact and absolute.",
        )
    output = path.resolve(strict=False)
    if not output.parent.is_dir() or output.exists():
        raise CompatibilityRehearsalError(
            "COMPATIBILITY_REHEARSAL_OUTPUT_INVALID",
            f"The {description} path must be exclusive-new in an existing directory.",
        )
    return output


def _exact_existing_directory(value: Path, description: str) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        raise CompatibilityRehearsalError(
            "RESTORE_REHEARSAL_PATH_INVALID",
            f"The {description} path must be exact and absolute.",
        )
    try:
        resolved = path.resolve(strict=True)
    except OSError as error:
        raise CompatibilityRehearsalError(
            "RESTORE_REHEARSAL_PATH_INVALID",
            f"The {description} path does not exist.",
        ) from error
    if resolved != path or not resolved.is_dir():
        raise CompatibilityRehearsalError(
            "RESTORE_REHEARSAL_PATH_INVALID",
            f"The {description} must be an unaliased existing directory.",
        )
    _directory_identity(resolved)
    return resolved


def _exact_unaliased_file(value: Path, description: str) -> Path:
    path = Path(value).expanduser()
    resolved = _exact_existing_file(path, description)
    if path != resolved or path.is_symlink():
        raise CompatibilityRehearsalError(
            "RESTORE_REHEARSAL_PATH_INVALID",
            f"The {description} must be an unaliased physical file.",
        )
    return resolved


def _require_contained(path: Path, root: Path) -> None:
    try:
        path.relative_to(root)
    except ValueError as error:
        raise CompatibilityRehearsalError(
            "RESTORE_REHEARSAL_TARGET_ESCAPED",
            "The restore-install target must remain below the exact rehearsal root.",
        ) from error


def _directory_identity(path: Path) -> tuple[int, int, int]:
    try:
        metadata = path.stat(follow_symlinks=False)
    except OSError as error:
        raise CompatibilityRehearsalError(
            "RESTORE_REHEARSAL_ROOT_INVALID",
            "The rehearsal directory could not be bound.",
        ) from error
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    attributes = int(getattr(metadata, "st_file_attributes", 0))
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or path.is_symlink()
        or attributes & reparse_flag
        or path.resolve(strict=True) != path
    ):
        raise CompatibilityRehearsalError(
            "RESTORE_REHEARSAL_ROOT_INVALID",
            "The rehearsal root and target parent must not be links or reparse points.",
        )
    return (int(metadata.st_dev), int(metadata.st_ino), attributes)


def _assert_directory_identity(path: Path, expected: tuple[int, int, int]) -> None:
    if _directory_identity(path) != expected:
        raise CompatibilityRehearsalError(
            "RESTORE_REHEARSAL_ROOT_CHANGED",
            "The rehearsal root or target parent changed during installation.",
        )


def _assert_no_sidecars(database: Path) -> None:
    if any(Path(f"{database}{suffix}").exists() for suffix in ("-wal", "-shm", "-journal")):
        raise CompatibilityRehearsalError(
            "RESTORE_REHEARSAL_WRITER_PRESENT",
            "The isolated target has a SQLite sidecar and is not safe to replace.",
        )


def _is_sha256(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
    except OSError as error:
        raise CompatibilityRehearsalError(
            "RESTORE_REHEARSAL_FILE_UNREADABLE",
            "A restore-install input changed or became unreadable.",
        ) from error
    return digest.hexdigest()


def _copy_exclusive(source: Path, destination: Path) -> None:
    source_descriptor: int | None = None
    destination_descriptor: int | None = None
    try:
        binary_flag = getattr(os, "O_BINARY", 0)
        source_descriptor = os.open(
            source,
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | binary_flag,
        )
        destination_descriptor = os.open(
            destination,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | binary_flag,
            0o600,
        )
        while True:
            block = os.read(source_descriptor, 1024 * 1024)
            if not block:
                break
            view = memoryview(block)
            while view:
                written = os.write(destination_descriptor, view)
                if written <= 0:
                    raise OSError("exclusive restore staging made no progress")
                view = view[written:]
        os.fsync(destination_descriptor)
    except OSError as error:
        raise CompatibilityRehearsalError(
            "RESTORE_REHEARSAL_STAGE_FAILED",
            "The verified backup could not be staged exclusively.",
        ) from error
    finally:
        if source_descriptor is not None:
            os.close(source_descriptor)
        if destination_descriptor is not None:
            os.close(destination_descriptor)


def _fsync_directory(path: Path) -> None:
    if os.name == "nt":
        return
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _restore_original_target(
    *,
    target: Path,
    recovery_path: Path,
    failed_install_path: Path,
) -> None:
    try:
        if recovery_path.is_file():
            if target.exists():
                os.replace(target, failed_install_path)
            os.replace(recovery_path, target)
            _fsync_directory(target.parent)
    except OSError as error:
        raise CompatibilityRehearsalError(
            "RESTORE_REHEARSAL_ROLLBACK_FAILED",
            "The isolated target could not be restored after validation failure.",
        ) from error


def _validate_p3_fingerprint(fingerprint: object) -> None:
    alembic_version = getattr(fingerprint, "alembic_version", None)
    content_counts = getattr(fingerprint, "content_counts", {})
    if (
        alembic_version != "20260807_03"
        or not isinstance(content_counts, dict)
        or content_counts.get("processingJobs")
        != content_counts.get("processingJobSpecs")
        or content_counts.get("processingJobsSpecGuardInsert") != 1
        or content_counts.get("processingJobsSpecGuardUpdate") != 1
        or content_counts.get("documentChunksFtsIntegrity") != 1
    ):
        raise CompatibilityRehearsalError(
            "RESTORE_REHEARSAL_P3_INVENTORY_INVALID",
            "The database does not satisfy the frozen P3 revision and JobSpec inventory.",
        )


__all__ = [
    "CompatibilityRehearsalError",
    "CompatibilitySmokeObservation",
    "CompatibilitySmokeRequest",
    "CompatibilitySmokeResult",
    "CompatibilitySmokeRunner",
    "RECOVERY_SMOKE_EVENTS",
    "ROLLBACK_TAIL_EVENTS",
    "RecoverySmokeService",
    "RestoreInstallRehearsalResult",
    "RestoreInstallRehearsalService",
    "RollbackSmokeService",
]
