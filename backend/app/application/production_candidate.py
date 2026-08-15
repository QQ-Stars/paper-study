from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import Path
import uuid
from typing import Protocol

from backend.app.api.compat.build_identity import (
    BuildIdentityError,
    load_build_identity_manifest,
)
from backend.app.api.compat.data_fingerprint import (
    APPLICATION_TABLES,
    LEGACY_TABLES,
    DataFingerprintError,
    capture_fingerprint,
    compare_fingerprints,
)
from backend.app.api.compat.database_identity import (
    DatabaseEvidenceIdentityService,
    DatabaseIdentityError,
    canonical_json_bytes,
    exclusive_write_bytes,
    load_database_evidence_identity_manifest,
    read_platform_file_identity,
    verify_database_evidence_identity_subject,
)
from backend.app.infrastructure.database_backup import (
    DatabaseBackupError,
    restore_backup_for_validation,
)


class CandidateWriteSmokeError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class CandidateWriteMutation:
    table: str
    operation: str
    primary_keys: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CandidateSmokeRequest:
    database_path: Path
    database_identity_manifest_path: Path
    build_id: str
    runtime_namespace: str


@dataclass(frozen=True, slots=True)
class CandidateSmokeObservation:
    database_path: Path
    request_id: str
    paper_id: str
    job_id: str
    source_document_id: str
    artifact_id: str
    roles: tuple[str, ...]
    loopback_bindings: tuple[tuple[str, str, int], ...]
    endpoints: tuple[str, ...]
    mutations: tuple[CandidateWriteMutation, ...]
    fake_provider_calls: int
    real_provider_calls: int
    real_network_calls: int
    live_path_access_count: int
    owner_marker_write_count: int
    user_pdf_access_count: int
    stopped: bool


class CandidateWriteSmokeRunner(Protocol):
    async def run(self, request: CandidateSmokeRequest) -> CandidateSmokeObservation: ...


@dataclass(frozen=True, slots=True)
class CandidateWriteSmokeResult:
    restored_database_path: Path
    descendant_database_identity_manifest_path: Path
    before_path: Path
    after_path: Path
    delta_ledger_path: Path
    request_id: str
    job_id: str
    source_document_id: str
    artifact_id: str
    database_lineage_id: str
    subject_database_id: str
    parent_subject_database_id: str
    build_id: str

    def to_dict(self) -> dict[str, object]:
        return {
            "ok": True,
            "restoredDatabasePath": str(self.restored_database_path),
            "descendantDatabaseIdentityManifestPath": str(
                self.descendant_database_identity_manifest_path
            ),
            "beforePath": str(self.before_path),
            "afterPath": str(self.after_path),
            "deltaLedgerPath": str(self.delta_ledger_path),
            "requestId": self.request_id,
            "jobId": self.job_id,
            "sourceDocumentId": self.source_document_id,
            "artifactId": self.artifact_id,
            "databaseLineageId": self.database_lineage_id,
            "subjectDatabaseId": self.subject_database_id,
            "parentSubjectDatabaseId": self.parent_subject_database_id,
            "buildId": self.build_id,
        }


class CandidateWriteSmokeService:
    async def run(
        self,
        *,
        backup: Path,
        manifest: Path,
        restore_root: Path,
        build_identity_manifest: Path,
        parent_database_identity_manifest: Path,
        descendant_database_identity_output: Path,
        evidence_mode: str,
        evidence_dir: Path,
        runner: CandidateWriteSmokeRunner,
    ) -> CandidateWriteSmokeResult:
        evidence_path, restore_path, identity_output = _validate_paths(
            evidence_mode=evidence_mode,
            evidence_dir=evidence_dir,
            restore_root=restore_root,
            descendant_database_identity_output=descendant_database_identity_output,
        )
        try:
            build_identity = load_build_identity_manifest(build_identity_manifest)
            parent_identity = load_database_evidence_identity_manifest(
                parent_database_identity_manifest
            )
            if parent_identity.subject_kind != "live":
                raise CandidateWriteSmokeError(
                    "CANDIDATE_PARENT_IDENTITY_INVALID",
                    "Candidate write-smoke requires an exact Live parent identity.",
                )

            restored = restore_backup_for_validation(backup, manifest, restore_path)
            if restored.restored_path is None:
                raise CandidateWriteSmokeError(
                    "CANDIDATE_RESTORE_INVALID",
                    "The verified restore did not return a database path.",
                )
            restored_path = restored.restored_path.resolve(strict=True)
            if restored_path.parent.parent != restore_path:
                raise CandidateWriteSmokeError(
                    "CANDIDATE_RESTORE_ESCAPED",
                    "The restored database escaped the requested isolation root.",
                )

            descendant = DatabaseEvidenceIdentityService().create_descendant_database_identity(
                database=restored_path,
                subject_kind="write_smoke",
                parent_database_identity_manifest=parent_identity.manifest_path,
                parent_backup=backup,
                parent_manifest=manifest,
                output=identity_output,
            )
            if (
                descendant.database_path != restored_path
                or descendant.parent_subject_database_id
                != parent_identity.subject_database_id
                or descendant.database_lineage_id
                != parent_identity.database_lineage_id
            ):
                raise CandidateWriteSmokeError(
                    "CANDIDATE_DESCENDANT_IDENTITY_INVALID",
                    "The descendant identity does not bind the restored database and Live parent.",
                )

            before_path = evidence_path / "pre-write-smoke.json"
            after_path = evidence_path / "post-write-smoke.json"
            ledger_path = evidence_path / "write-smoke-delta.json"
            before = capture_fingerprint(database=restored_path, output=before_path)
            initial_file_identity = read_platform_file_identity(restored_path)
            request = CandidateSmokeRequest(
                database_path=restored_path,
                database_identity_manifest_path=identity_output,
                build_id=build_identity.build_id,
                runtime_namespace=f"p6-write-smoke-{uuid.uuid4().hex}",
            )
            observation = await runner.run(request)
            _validate_observation(
                observation,
                restored_path=restored_path,
                initial_file_identity=initial_file_identity,
            )
            after = capture_fingerprint(database=restored_path, output=after_path)
            ledger = _build_delta_ledger(observation, before=before, after=after)
            compare_fingerprints(
                before,
                after,
                mode="explained-write",
                delta_ledger=ledger,
            )
            exclusive_write_bytes(ledger_path, canonical_json_bytes(ledger))
            verify_database_evidence_identity_subject(
                database=restored_path,
                identity=descendant,
            )
        except CandidateWriteSmokeError:
            raise
        except (BuildIdentityError, DatabaseIdentityError) as error:
            raise CandidateWriteSmokeError(
                "CANDIDATE_IDENTITY_INVALID",
                "Candidate write-smoke rejected an identity input.",
            ) from error
        except DatabaseBackupError as error:
            raise CandidateWriteSmokeError(
                "CANDIDATE_BACKUP_INVALID",
                "Candidate write-smoke rejected the backup or restore boundary.",
            ) from error
        except DataFingerprintError as error:
            raise CandidateWriteSmokeError(
                "CANDIDATE_DELTA_INVALID",
                "Candidate write-smoke produced invalid fingerprint evidence.",
            ) from error
        except OSError as error:
            raise CandidateWriteSmokeError(
                "CANDIDATE_EVIDENCE_WRITE_FAILED",
                "Candidate write-smoke could not write isolated evidence.",
            ) from error

        return CandidateWriteSmokeResult(
            restored_database_path=restored_path,
            descendant_database_identity_manifest_path=identity_output,
            before_path=before_path,
            after_path=after_path,
            delta_ledger_path=ledger_path,
            request_id=observation.request_id,
            job_id=observation.job_id,
            source_document_id=observation.source_document_id,
            artifact_id=observation.artifact_id,
            database_lineage_id=descendant.database_lineage_id,
            subject_database_id=descendant.subject_database_id,
            parent_subject_database_id=parent_identity.subject_database_id,
            build_id=build_identity.build_id,
        )


def _validate_paths(
    *,
    evidence_mode: str,
    evidence_dir: Path,
    restore_root: Path,
    descendant_database_identity_output: Path,
) -> tuple[Path, Path, Path]:
    if evidence_mode not in {"provisional", "final"}:
        raise CandidateWriteSmokeError(
            "CANDIDATE_EVIDENCE_MODE_INVALID",
            "Candidate write-smoke evidence mode must be provisional or final.",
        )
    try:
        evidence_path = Path(evidence_dir).expanduser().resolve(strict=True)
    except OSError as error:
        raise CandidateWriteSmokeError(
            "CANDIDATE_EVIDENCE_DIRECTORY_INVALID",
            "Candidate write-smoke requires an existing evidence directory.",
        ) from error
    if not evidence_path.is_dir():
        raise CandidateWriteSmokeError(
            "CANDIDATE_EVIDENCE_DIRECTORY_INVALID",
            "Candidate write-smoke requires an evidence directory.",
        )
    restore_path = Path(restore_root).expanduser().resolve(strict=False)
    identity_output = Path(descendant_database_identity_output).expanduser().resolve(
        strict=False
    )
    if (
        restore_path.parent != evidence_path
        or identity_output.parent != evidence_path
        or identity_output.exists()
        or (restore_path.exists() and not restore_path.is_dir())
    ):
        raise CandidateWriteSmokeError(
            "CANDIDATE_EVIDENCE_PATH_INVALID",
            "Candidate write-smoke outputs must be new paths in the exact evidence directory.",
        )
    for output in (
        evidence_path / "pre-write-smoke.json",
        evidence_path / "post-write-smoke.json",
        evidence_path / "write-smoke-delta.json",
    ):
        if output.exists():
            raise CandidateWriteSmokeError(
                "CANDIDATE_EVIDENCE_OUTPUT_EXISTS",
                "Candidate write-smoke evidence outputs must be exclusive-new files.",
            )
    return evidence_path, restore_path, identity_output


def _validate_observation(
    observation: CandidateSmokeObservation,
    *,
    restored_path: Path,
    initial_file_identity: object,
) -> None:
    try:
        observed_path = observation.database_path.resolve(strict=True)
    except OSError as error:
        raise CandidateWriteSmokeError(
            "CANDIDATE_DATABASE_IDENTITY_CHANGED",
            "The candidate returned an unreadable database path.",
        ) from error
    identifiers = (
        observation.request_id,
        observation.paper_id,
        observation.job_id,
        observation.source_document_id,
        observation.artifact_id,
    )
    expected_endpoints = {
        "/health/ready",
        "/api/papers",
        "/api/v2/jobs",
        f"/api/v2/papers/{observation.paper_id}/sources",
        f"/api/v2/jobs/{observation.job_id}/events",
        "/workspace/",
        "/legacy/",
        "mcp:tools/list",
    }
    zero_side_effects = (
        observation.real_provider_calls,
        observation.real_network_calls,
        observation.live_path_access_count,
        observation.owner_marker_write_count,
        observation.user_pdf_access_count,
    )
    if (
        observed_path != restored_path
        or read_platform_file_identity(restored_path) != initial_file_identity
        or any(not isinstance(value, str) or not value.strip() for value in identifiers)
        or observation.roles != ("api", "worker", "scheduler")
        or len(observation.loopback_bindings) != 1
        or observation.loopback_bindings[0][0:2] != ("api", "127.0.0.1")
        or not isinstance(observation.loopback_bindings[0][2], int)
        or not 0 < observation.loopback_bindings[0][2] < 65536
        or set(observation.endpoints) != expected_endpoints
        or len(observation.endpoints) != len(expected_endpoints)
        or observation.fake_provider_calls < 1
        or any(value != 0 for value in zero_side_effects)
        or not observation.stopped
    ):
        raise CandidateWriteSmokeError(
            "CANDIDATE_SMOKE_INVALID",
            "The candidate smoke result violated its isolated runtime contract.",
        )
    allowed_tables = set(APPLICATION_TABLES) - set(LEGACY_TABLES)
    seen_tables: set[str] = set()
    for mutation in observation.mutations:
        if (
            mutation.table not in allowed_tables
            or mutation.table in seen_tables
            or mutation.operation not in {"insert", "update", "delete"}
            or not mutation.primary_keys
            or len(set(mutation.primary_keys)) != len(mutation.primary_keys)
            or any(not key for key in mutation.primary_keys)
        ):
            raise CandidateWriteSmokeError(
                "CANDIDATE_SMOKE_DELTA_INVALID",
                "The candidate smoke mutation inventory is invalid.",
            )
        seen_tables.add(mutation.table)
    if not seen_tables:
        raise CandidateWriteSmokeError(
            "CANDIDATE_SMOKE_DELTA_INVALID",
            "The candidate smoke did not report any changed auxiliary row.",
        )


def _build_delta_ledger(
    observation: CandidateSmokeObservation,
    *,
    before: dict[str, object],
    after: dict[str, object],
) -> dict[str, object]:
    before_tables = before["tables"]
    after_tables = after["tables"]
    assert isinstance(before_tables, dict)
    assert isinstance(after_tables, dict)
    entries: list[dict[str, object]] = []
    for mutation in observation.mutations:
        before_table = before_tables[mutation.table]
        after_table = after_tables[mutation.table]
        assert isinstance(before_table, dict)
        assert isinstance(after_table, dict)
        unsigned: dict[str, object] = {
            "table": mutation.table,
            "operation": mutation.operation,
            "primaryKeys": list(mutation.primary_keys),
            "countDelta": {"insert": 1, "update": 0, "delete": -1}[
                mutation.operation
            ],
            "beforePrimaryKeySetSha256": before_table["primaryKeySetSha256"],
            "afterPrimaryKeySetSha256": after_table["primaryKeySetSha256"],
            "beforeRowSha256": before_table["rowSha256"],
            "afterRowSha256": after_table["rowSha256"],
            "jobId": observation.job_id,
            "sourceDocumentId": observation.source_document_id,
            "artifactId": observation.artifact_id,
        }
        entries.append(
            {
                **unsigned,
                "evidenceSha256": hashlib.sha256(
                    canonical_json_bytes(unsigned)
                ).hexdigest(),
            }
        )
    return {"schemaVersion": 1, "entries": entries}
