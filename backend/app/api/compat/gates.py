from __future__ import annotations

import base64
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import secrets
from typing import Callable

from backend.app.api.compat.build_identity import (
    BuildIdentityError,
    BuildIdentityManifest,
    load_build_identity_manifest,
)
from backend.app.api.compat.database_identity import (
    DatabaseIdentityError,
    canonical_json_bytes,
    load_database_evidence_identity_manifest,
    exclusive_write_bytes,
)
from backend.app.infrastructure.database_backup import (
    DatabaseBackupError,
    verify_origin_receipt_envelope,
)
from backend.app.api.compat.suite_applicability import (
    SuiteApplicabilityError,
    load_node_suite_applicability_report,
    load_suite_applicability_report,
)


SHUTDOWN_EVIDENCE_KEYS = (
    "build-identity-verify",
    "bound-root-zero-skip",
    "suite-isolation",
    "backend-suite",
    "legacy-python-suite",
    "mcp-server-suite",
    "node-suite",
    "frontend-vitest",
    "frontend-typecheck",
    "frontend-lint",
    "frontend-build",
    "frontend-e2e",
    "migration-head-ready",
    "http-v2-ndjson-static",
    "runtime-worker-scheduler-obsidian",
    "mcp-credentials",
    "legacy-reconciliation",
    "node-quiesce",
    "cutover-backup-create",
    "cutover-backup-verify",
    "cutover-backup-restore-check",
    "live-pre-fingerprint",
    "live-post-fingerprint",
    "strict-readonly-compare",
    "convergence-gate",
    "candidate-production-profile",
    "candidate-write-smoke",
    "explained-write-compare",
    "frozen-node-rollback",
    "python-recovery",
    "restore-install-rehearsal",
    "final-enum-runbook",
    "handoff-contract",
)


class CompatibilityGateError(RuntimeError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        missing_keys: tuple[str, ...] = (),
    ) -> None:
        super().__init__(message)
        self.code = code
        self.missing_keys = missing_keys


def evaluate_gate(
    evidence_directory: str | Path,
    *,
    phase: str,
    final_evidence_run_manifest: str | os.PathLike[str] | None = None,
    expected_final_evidence_run_manifest_sha256: str | None = None,
    startup_snapshot: str | os.PathLike[str] | None = None,
    expected_startup_snapshot_sha256: str | None = None,
    cutover_lease: str | os.PathLike[str] | None = None,
    authorization_output: str | os.PathLike[str] | None = None,
    authorization_ttl_seconds: int = 900,
    clock: Callable[[], datetime] | None = None,
) -> dict[str, object]:
    if phase not in {"preflight", "convergence", "shutdown"}:
        raise CompatibilityGateError(
            "COMPATIBILITY_PHASE_INVALID",
            "The compatibility gate phase is invalid.",
        )
    root = Path(evidence_directory).resolve(strict=True)
    if not root.is_dir():
        raise CompatibilityGateError(
            "COMPATIBILITY_EVIDENCE_INVALID",
            "The compatibility evidence root is not a directory.",
        )
    record_phase = "provisional" if phase == "preflight" else "final"
    records = _typed_records(root, phase=record_phase)
    expected = SHUTDOWN_EVIDENCE_KEYS if phase == "shutdown" else ()
    present = {document["evidenceKey"] for _, document in records}
    missing = tuple(key for key in expected if key not in present)
    if missing:
        raise CompatibilityGateError(
            "COMPATIBILITY_EVIDENCE_MISSING",
            "Required compatibility evidence is missing.",
            missing_keys=missing,
        )
    bindings = _validate_records(
        root,
        records,
        phase=phase,
        final_evidence_run_manifest=final_evidence_run_manifest,
        expected_final_evidence_run_manifest_sha256=expected_final_evidence_run_manifest_sha256,
        startup_snapshot=startup_snapshot,
        expected_startup_snapshot_sha256=expected_startup_snapshot_sha256,
        cutover_lease=cutover_lease,
    )
    result: dict[str, object] = {
        "ok": True,
        "phase": phase,
        "preflightReady": phase == "preflight",
        "finalEvidence": phase != "preflight",
        "nodeShutdownAllowed": phase == "shutdown",
    }
    if phase == "shutdown" and authorization_output is not None:
        result.update(
            _issue_authorization(
                root,
                bindings,
                output=authorization_output,
                ttl_seconds=authorization_ttl_seconds,
                clock=clock,
            )
        )
    return result


def _typed_records(root: Path, *, phase: str) -> list[tuple[Path, dict[str, object]]]:
    records: list[tuple[Path, dict[str, object]]] = []
    for path in root.rglob("*.json"):
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            continue
        if (
            isinstance(document, dict)
            and document.get("producer") == "compatibility.capture-evidence"
            and document.get("phase") == phase
            and isinstance(document.get("evidenceKey"), str)
        ):
            records.append((path.resolve(strict=True), document))
    return records


def _validate_records(
    root: Path,
    records: list[tuple[Path, dict[str, object]]],
    *,
    phase: str,
    final_evidence_run_manifest: str | os.PathLike[str] | None,
    expected_final_evidence_run_manifest_sha256: str | None,
    startup_snapshot: str | os.PathLike[str] | None,
    expected_startup_snapshot_sha256: str | None,
    cutover_lease: str | os.PathLike[str] | None,
) -> dict[str, object]:
    keys = [str(document["evidenceKey"]) for _, document in records]
    if len(keys) != len(set(keys)):
        raise CompatibilityGateError(
            "COMPATIBILITY_EVIDENCE_DUPLICATE",
            "A compatibility evidence key appears more than once.",
        )
    manifest_path = (
        Path(final_evidence_run_manifest).resolve(strict=True)
        if final_evidence_run_manifest is not None
        else (root / "evidence-run-manifest-v1.json").resolve(strict=True)
    )
    try:
        from backend.app.api.compat.evidence_capture import (
            EvidenceCaptureError,
            load_evidence_run_manifest,
        )

        run = load_evidence_run_manifest(
            manifest_path,
            expected_file_sha256=expected_final_evidence_run_manifest_sha256,
        )
        build = load_build_identity_manifest(run.build_identity_manifest_path)
        database_identity = load_database_evidence_identity_manifest(
            run.database_identity_manifest_path
        )
        receipt = verify_origin_receipt_envelope(
            database_identity.origin_receipt_path,
            database_identity.origin_receipt_file_sha256,
        )
    except (
        BuildIdentityError,
        DatabaseBackupError,
        DatabaseIdentityError,
        EvidenceCaptureError,
        OSError,
        ValueError,
    ) as error:
        raise CompatibilityGateError(
            "COMPATIBILITY_IDENTITY_MISMATCH",
            "The evidence run identity manifests could not be revalidated.",
        ) from error
    expected_run_phase = "provisional" if phase == "preflight" else "final"
    if run.run_directory != root or run.phase != expected_run_phase:
        raise CompatibilityGateError(
            "COMPATIBILITY_EVIDENCE_INVALID",
            "The evidence directory or phase does not match its strict run manifest.",
        )
    if build.manifest_file_sha256 != run.build_identity_manifest_sha256:
        raise CompatibilityGateError(
            "COMPATIBILITY_IDENTITY_MISMATCH",
            "The evidence run BuildIdentity hash no longer matches.",
        )
    if any(key not in run.expected_keys for key in keys) or (
        phase == "shutdown" and run.expected_keys != SHUTDOWN_EVIDENCE_KEYS
    ):
        raise CompatibilityGateError(
            "COMPATIBILITY_EVIDENCE_INVALID",
            "The captured evidence keys do not match the strict run manifest.",
        )
    if (
        receipt.database_lineage_id != database_identity.database_lineage_id
        or receipt.receipt_sha256 != database_identity.origin_receipt_sha256
    ):
        raise CompatibilityGateError(
            "COMPATIBILITY_IDENTITY_MISMATCH",
            "The evidence run database identity no longer matches its OriginReceipt.",
        )
    manifest_path = run.manifest_path
    manifest_sha = run.manifest_file_sha256
    run_id = run.run_id
    run_document = json.loads(run.canonical_bytes.decode("utf-8"))
    build_binding: tuple[object, ...] | None = None
    database_binding: tuple[object, ...] | None = None
    descendant_records: list[dict[str, object]] = []
    origin_binding: tuple[object, ...] | None = None
    startup_binding: tuple[object, ...] | None = None
    lease_binding: tuple[object, ...] | None = None
    indexed: dict[str, dict[str, object]] = {}
    record_paths: dict[str, Path] = {}
    for path, document in records:
        key = str(document["evidenceKey"])
        indexed[key] = document
        record_paths[key] = path
        if (
            document.get("runId") != run_id
            or document.get("runManifestPath") != str(manifest_path)
            or document.get("runManifestFileSha256") != manifest_sha
        ):
            raise CompatibilityGateError(
                "COMPATIBILITY_EVIDENCE_CROSS_RUN",
                f"Evidence {path.name} belongs to another run.",
            )
        unsigned = dict(document)
        record_sha = unsigned.pop("recordSha256", None)
        if record_sha != hashlib.sha256(canonical_json_bytes(unsigned)).hexdigest():
            raise CompatibilityGateError(
                "COMPATIBILITY_EVIDENCE_INVALID",
                f"Evidence {path.name} has an invalid record hash.",
            )
        if any(document.get(field) != 0 for field in ("exitCode", "failures", "skips")):
            raise CompatibilityGateError(
                "COMPATIBILITY_EVIDENCE_FAILED",
                f"Evidence {path.name} is not raw-zero, zero-failure, zero-skip.",
            )
        _verify_artifact_pairs(document, run_root=root)
        current_build = (
            document.get("buildIdentityManifestPath"),
            document.get("buildIdentityManifestSha256"),
            document.get("buildId"),
        )
        build_binding = _same_binding(build_binding, current_build, "build")
        current_database = (
            document.get("databaseIdentityManifestPath"),
            document.get("databaseIdentityManifestSha256"),
            document.get("databaseLineageId"),
            document.get("subjectDatabaseId"),
            document.get("subjectKind"),
        )
        if current_database[4] == "live":
            database_binding = _same_binding(
                database_binding,
                current_database,
                "database",
            )
        else:
            descendant_records.append(document)
        current_origin = (
            document.get("originReceiptPath"),
            document.get("originReceiptFileSha256"),
        )
        origin_binding = _same_binding(origin_binding, current_origin, "OriginReceipt")
        if "startupSnapshotPath" in document or "startupSnapshotSha256" in document:
            startup_binding = _same_binding(
                startup_binding,
                (document.get("startupSnapshotPath"), document.get("startupSnapshotSha256")),
                "startup snapshot",
            )
        lease_fields = (
            "cutoverLeasePath",
            "cutoverLeaseSha256",
            "cutoverTokenFilePath",
            "cutoverTokenSha256",
        )
        if any(field in document for field in lease_fields):
            if (
                any(not isinstance(document.get(field), str) for field in lease_fields)
                or not _is_sha256(str(document["cutoverLeaseSha256"]))
                or not _is_sha256(str(document["cutoverTokenSha256"]))
            ):
                raise CompatibilityGateError(
                    "COMPATIBILITY_IDENTITY_MISMATCH",
                    "A final evidence cutover capability binding is invalid.",
                )
            lease_binding = _same_binding(
                lease_binding,
                (
                    document["cutoverLeasePath"],
                    document["cutoverTokenFilePath"],
                    document["cutoverTokenSha256"],
                ),
                "cutover lease",
            )
        if document.get("resultKind") == "machine-summary":
            _verify_isolation_binding(document, key=key, run_root=root)
        if (
            key in {"backend-suite", "node-suite"}
            and build.deployment_kind == "native-windows"
        ):
            if document.get("resultKind") != "machine-summary":
                raise CompatibilityGateError(
                    "COMPATIBILITY_APPLICABILITY_INVALID",
                    "Native-Windows suite evidence must be a machine summary.",
                )
            _verify_native_windows_applicability(
                document,
                key=key,
                run_root=root,
                build=build,
            )
    if descendant_records:
        if database_binding is None:
            raise CompatibilityGateError(
                "COMPATIBILITY_IDENTITY_MISMATCH",
                "Descendant evidence requires one exact Live database binding.",
            )
        for descendant in descendant_records:
            _verify_descendant_binding(descendant, live_binding=database_binding)
    if phase == "shutdown":
        _verify_shutdown_topology(indexed)
    if startup_snapshot is not None:
        startup_path = Path(startup_snapshot).resolve(strict=True)
        startup_sha = _file_sha256(startup_path)
        if startup_sha != expected_startup_snapshot_sha256 or startup_binding != (str(startup_path), startup_sha):
            raise CompatibilityGateError(
                "COMPATIBILITY_IDENTITY_MISMATCH",
                "The startup snapshot does not match the final evidence run.",
            )
    else:
        startup_path = None
        startup_sha = None
    if cutover_lease is not None:
        try:
            from backend.app.application.final_window import (
                FinalWindowError,
                load_cutover_lease_binding,
            )

            current_lease = load_cutover_lease_binding(cutover_lease)
        except (FinalWindowError, OSError, ValueError) as error:
            raise CompatibilityGateError(
                "COMPATIBILITY_IDENTITY_MISMATCH",
                "The current cutover lease could not be revalidated.",
            ) from error
        lease_path = current_lease.path
        lease_sha = current_lease.file_sha256
        current_capability = (
            str(lease_path),
            str(current_lease.token_file_path),
            current_lease.token_sha256,
        )
        if (
            lease_binding != current_capability
            or current_lease.run_id != run_id
            or current_lease.startup_snapshot_path != startup_path
            or current_lease.startup_snapshot_sha256 != startup_sha
        ):
            raise CompatibilityGateError(
                "COMPATIBILITY_IDENTITY_MISMATCH",
                "The cutover lease does not match the final evidence run.",
            )
    else:
        lease_path = None
        lease_sha = None
    bindings = {
        "runId": run_id,
        "runManifestPath": manifest_path,
        "runManifestSha256": manifest_sha,
        "startupSnapshotPath": startup_path,
        "startupSnapshotSha256": startup_sha,
        "cutoverLeasePath": lease_path,
        "cutoverLeaseSha256": lease_sha,
    }
    if phase == "shutdown":
        bindings.update(
            _shutdown_authorization_bindings(
                run_document=run_document,
                indexed=indexed,
                record_paths=record_paths,
                build_binding=build_binding,
                database_binding=database_binding,
                origin_binding=origin_binding,
                startup_path=startup_path,
                lease_path=lease_path,
            )
        )
    return bindings


def _same_binding(
    expected: tuple[object, ...] | None,
    current: tuple[object, ...],
    label: str,
) -> tuple[object, ...]:
    if expected is not None and expected != current:
        raise CompatibilityGateError(
            "COMPATIBILITY_IDENTITY_MISMATCH",
            f"Final evidence does not share one {label} identity.",
        )
    return current


def _verify_artifact_pairs(
    document: dict[str, object],
    *,
    run_root: Path,
) -> None:
    for prefix in ("summaryArtifact", "stdout", "stderr"):
        path_value = document.get(prefix + "Path")
        sha_value = document.get(prefix + "Sha256")
        if path_value is None and sha_value is None:
            continue
        if not isinstance(path_value, str) or not isinstance(sha_value, str):
            raise CompatibilityGateError("COMPATIBILITY_EVIDENCE_INVALID", "An artifact binding is incomplete.")
        resolved = Path(path_value).resolve(strict=True)
        if not _is_below(resolved, run_root) or _file_sha256(resolved) != sha_value:
            raise CompatibilityGateError("COMPATIBILITY_EVIDENCE_INVALID", "An evidence artifact hash changed.")
    artifacts = document.get("artifacts")
    if artifacts is None:
        return
    if not isinstance(artifacts, list):
        raise CompatibilityGateError(
            "COMPATIBILITY_EVIDENCE_INVALID",
            "The evidence artifact inventory is invalid.",
        )
    names: set[str] = set()
    for item in artifacts:
        if (
            not isinstance(item, dict)
            or set(item) != {"name", "path", "sha256"}
            or not isinstance(item["name"], str)
            or not item["name"]
            or item["name"] in names
            or not isinstance(item["path"], str)
            or not isinstance(item["sha256"], str)
            or not _is_below(Path(item["path"]).resolve(strict=True), run_root)
            or _file_sha256(Path(item["path"]).resolve(strict=True)) != item["sha256"]
        ):
            raise CompatibilityGateError(
                "COMPATIBILITY_EVIDENCE_INVALID",
                "An evidence artifact binding is invalid.",
            )
        names.add(item["name"])


def _verify_native_windows_applicability(
    document: dict[str, object],
    *,
    key: str = "backend-suite",
    run_root: Path,
    build: BuildIdentityManifest,
) -> None:
    if key not in {"backend-suite", "node-suite"}:
        raise CompatibilityGateError(
            "COMPATIBILITY_APPLICABILITY_INVALID",
            "The native-Windows applicability suite key is invalid.",
        )
    artifacts = document.get("artifacts")
    matching = (
        [
            item
            for item in artifacts
            if isinstance(item, dict) and item.get("name") == "applicability"
        ]
        if isinstance(artifacts, list)
        else []
    )
    if len(matching) != 1:
        raise CompatibilityGateError(
            "COMPATIBILITY_APPLICABILITY_INVALID",
            "Native-Windows suite evidence requires one applicability artifact.",
        )
    item = matching[0]
    path_value = item.get("path")
    sha_value = item.get("sha256")
    if not isinstance(path_value, str) or not isinstance(sha_value, str):
        raise CompatibilityGateError(
            "COMPATIBILITY_APPLICABILITY_INVALID",
            "The native-Windows applicability artifact binding is incomplete.",
        )
    try:
        path = Path(path_value).resolve(strict=True)
        report = (
            load_suite_applicability_report(path)
            if key == "backend-suite"
            else load_node_suite_applicability_report(path)
        )
    except (OSError, SuiteApplicabilityError) as error:
        raise CompatibilityGateError(
            "COMPATIBILITY_APPLICABILITY_INVALID",
            "The native-Windows applicability report could not be revalidated.",
        ) from error
    expected_build = (
        str(build.manifest_path),
        build.manifest_file_sha256,
        build.build_id,
    )
    record_build = (
        document.get("buildIdentityManifestPath"),
        document.get("buildIdentityManifestSha256"),
        document.get("buildId"),
    )
    report_build = (
        str(report.build_identity_manifest_path),
        report.build_identity_manifest_sha256,
        report.build_id,
    )
    if (
        not _is_below(path, run_root)
        or report.manifest_file_sha256 != sha_value
        or expected_build != record_build
        or expected_build != report_build
        or document.get("totals") != report.selected_tests
    ):
        raise CompatibilityGateError(
            "COMPATIBILITY_APPLICABILITY_INVALID",
            "The native-Windows applicability report is not bound to this suite and build.",
        )


def _verify_isolation_binding(
    document: dict[str, object],
    *,
    key: str,
    run_root: Path,
) -> None:
    required = (
        "isolationManifestPath",
        "isolationManifestSha256",
        "isolationSuiteKey",
        "isolationSandboxRoot",
        "isolationDatabasePath",
        "isolationSettingsPath",
        "isolationPdfRoot",
        "isolationVaultRoot",
        "isolationKeyringRoot",
        "liveAccessCount",
    )
    if any(field not in document for field in required):
        raise CompatibilityGateError(
            "COMPATIBILITY_ISOLATION_INVALID",
            f"Evidence {key} is missing its suite isolation binding.",
        )
    path = Path(str(document["isolationManifestPath"])).resolve(strict=True)
    if not _is_below(path, run_root) or _file_sha256(path) != document["isolationManifestSha256"]:
        raise CompatibilityGateError(
            "COMPATIBILITY_ISOLATION_INVALID",
            f"Evidence {key} has a drifted isolation manifest.",
        )
    isolation = _json_document(path, "suite isolation manifest")
    if (
        isolation.get("suiteKey") != key
        or document["isolationSuiteKey"] != key
        or isolation.get("liveAccessCount") != 0
        or document["liveAccessCount"] != 0
        or isolation.get("runManifestPath") != document.get("runManifestPath")
        or isolation.get("runManifestSha256") != document.get("runManifestFileSha256")
    ):
        raise CompatibilityGateError(
            "COMPATIBILITY_ISOLATION_INVALID",
            f"Evidence {key} is not bound to a zero-access run isolation.",
        )
    field_map = {
        "sandboxRoot": "isolationSandboxRoot",
        "databasePath": "isolationDatabasePath",
        "settingsPath": "isolationSettingsPath",
        "pdfRoot": "isolationPdfRoot",
        "vaultRoot": "isolationVaultRoot",
        "keyringRoot": "isolationKeyringRoot",
    }
    for source, target in field_map.items():
        value = isolation.get(source)
        if (
            not isinstance(value, str)
            or document[target] != value
            or not _is_below(Path(value).resolve(strict=False), run_root)
        ):
            raise CompatibilityGateError(
                "COMPATIBILITY_ISOLATION_INVALID",
                f"Evidence {key} has an invalid isolated {source}.",
            )


def _verify_descendant_binding(
    document: dict[str, object],
    *,
    live_binding: tuple[object, ...],
) -> None:
    expected_parent = {
        "parentDatabaseIdentityManifestPath": live_binding[0],
        "parentIdentityManifestFileSha256": live_binding[1],
        "parentSubjectDatabaseId": live_binding[3],
    }
    if any(document.get(key) != value for key, value in expected_parent.items()):
        raise CompatibilityGateError(
            "COMPATIBILITY_IDENTITY_MISMATCH",
            "A descendant evidence record is not anchored to the exact Live identity.",
        )
    path_value = document.get("databaseIdentityManifestPath")
    sha_value = document.get("databaseIdentityManifestSha256")
    if not isinstance(path_value, str) or not isinstance(sha_value, str):
        raise CompatibilityGateError(
            "COMPATIBILITY_IDENTITY_MISMATCH",
            "A descendant database identity binding is incomplete.",
        )
    path = Path(path_value).resolve(strict=True)
    if _file_sha256(path) != sha_value or document.get("subjectKind") == "live":
        raise CompatibilityGateError(
            "COMPATIBILITY_IDENTITY_MISMATCH",
            "A descendant database identity file changed or is marked Live.",
        )


def _is_below(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _shutdown_authorization_bindings(
    *,
    run_document: dict[str, object],
    indexed: dict[str, dict[str, object]],
    record_paths: dict[str, Path],
    build_binding: tuple[object, ...] | None,
    database_binding: tuple[object, ...] | None,
    origin_binding: tuple[object, ...] | None,
    startup_path: Path | None,
    lease_path: Path | None,
) -> dict[str, object]:
    if (
        startup_path is None
        or lease_path is None
        or build_binding is None
        or database_binding is None
        or origin_binding is None
        or any(value is None for value in (*build_binding, *database_binding, *origin_binding))
        or database_binding[4] != "live"
    ):
        raise CompatibilityGateError(
            "COMPATIBILITY_IDENTITY_MISMATCH",
            "Shutdown authorization requires complete Build, Live database, and origin bindings.",
        )
    startup = _json_document(startup_path, "startup snapshot")
    lease = _json_document(lease_path, "cutover lease")
    startup_expected = {
        "runId": run_document.get("runId"),
        "buildIdentityManifestPath": build_binding[0],
        "buildIdentityManifestSha256": build_binding[1],
        "buildId": build_binding[2],
        "databaseIdentityManifestPath": database_binding[0],
        "databaseIdentityManifestSha256": database_binding[1],
        "databaseLineageId": database_binding[2],
        "liveSubjectDatabaseId": database_binding[3],
        "originReceiptPath": origin_binding[0],
        "originReceiptFileSha256": origin_binding[1],
        "runtimeNamespace": "production",
    }
    if any(startup.get(key) != value for key, value in startup_expected.items()):
        raise CompatibilityGateError(
            "COMPATIBILITY_IDENTITY_MISMATCH",
            "The startup snapshot does not match the shutdown evidence identities.",
        )
    if (
        lease.get("runId") != run_document.get("runId")
        or lease.get("runtimeNamespace") != "production"
        or not isinstance(lease.get("ownerMarkerPath"), str)
    ):
        raise CompatibilityGateError(
            "COMPATIBILITY_IDENTITY_MISMATCH",
            "The cutover lease does not bind the production owner.",
        )
    owner_marker_path = Path(str(lease["ownerMarkerPath"])).resolve(strict=True)
    owner_marker_version = lease.get("ownerMarkerVersion")
    if owner_marker_version is None:
        try:
            original_owner = json.loads(
                base64.b64decode(
                    str(lease["nodeActiveOwnerPayloadBase64"]),
                    validate=True,
                ).decode("utf-8")
            )
            owner_marker_version = original_owner["schemaVersion"]
        except (KeyError, TypeError, ValueError, UnicodeError, json.JSONDecodeError) as error:
            raise CompatibilityGateError(
                "COMPATIBILITY_IDENTITY_MISMATCH",
                "The cutover lease owner marker version is invalid.",
            ) from error
    if (
        not isinstance(owner_marker_version, int)
        or isinstance(owner_marker_version, bool)
        or owner_marker_version < 1
    ):
        raise CompatibilityGateError(
            "COMPATIBILITY_IDENTITY_MISMATCH",
            "The cutover lease owner marker version is invalid.",
        )
    backup_path, backup_sha = _named_artifact(
        indexed["cutover-backup-create"], "cutoverBackup"
    )
    manifest_path, manifest_sha = _named_artifact(
        indexed["cutover-backup-create"], "cutoverManifest"
    )
    node_record = record_paths["node-quiesce"]
    return {
        "buildIdentityManifestPath": str(build_binding[0]),
        "buildIdentityManifestSha256": build_binding[1],
        "buildId": build_binding[2],
        "databaseIdentityManifestPath": str(database_binding[0]),
        "databaseIdentityManifestSha256": database_binding[1],
        "databaseLineageId": database_binding[2],
        "liveSubjectDatabaseId": database_binding[3],
        "originReceiptPath": str(origin_binding[0]),
        "originReceiptFileSha256": origin_binding[1],
        "cutoverBackupPath": str(backup_path),
        "cutoverBackupSha256": backup_sha,
        "cutoverManifestPath": str(manifest_path),
        "cutoverManifestSha256": manifest_sha,
        "runtimeNamespace": "production",
        "roles": ["api", "worker", "scheduler", "mcp"],
        "nodeOwnerMarkerPath": str(owner_marker_path),
        "nodeOwnerMarkerVersion": owner_marker_version,
        "nodeZeroResourceEvidenceSha256": _file_sha256(node_record),
    }


def _named_artifact(document: dict[str, object], name: str) -> tuple[Path, str]:
    artifacts = document.get("artifacts")
    if not isinstance(artifacts, list):
        raise CompatibilityGateError(
            "COMPATIBILITY_EVIDENCE_INVALID",
            f"The {name} artifact is missing.",
        )
    aliases = {
        "cutoverBackup": {"cutoverBackup", "backupPath"},
        "cutoverManifest": {"cutoverManifest", "manifestPath"},
    }.get(name, {name})
    matches = [
        item
        for item in artifacts
        if isinstance(item, dict) and item.get("name") in aliases
    ]
    if len(matches) != 1:
        raise CompatibilityGateError(
            "COMPATIBILITY_EVIDENCE_INVALID",
            f"The {name} artifact binding is invalid.",
        )
    item = matches[0]
    path = Path(str(item["path"])).resolve(strict=True)
    sha = str(item["sha256"])
    if _file_sha256(path) != sha:
        raise CompatibilityGateError(
            "COMPATIBILITY_EVIDENCE_INVALID",
            f"The {name} artifact changed.",
        )
    return path, sha


def _json_document(path: Path, label: str) -> dict[str, object]:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise CompatibilityGateError(
            "COMPATIBILITY_EVIDENCE_INVALID",
            f"The {label} is invalid.",
        ) from error
    if not isinstance(document, dict):
        raise CompatibilityGateError(
            "COMPATIBILITY_EVIDENCE_INVALID",
            f"The {label} is invalid.",
        )
    return document


def _verify_shutdown_topology(records: dict[str, dict[str, object]]) -> None:
    try:
        boundary = max(
            _timestamp(str(records["node-quiesce"]["finishedAt"])),
            _timestamp(str(records["cutover-backup-restore-check"]["finishedAt"])),
        )
    except (KeyError, ValueError) as error:
        raise CompatibilityGateError(
            "COMPATIBILITY_EVIDENCE_TOPOLOGY_INVALID",
            "The shutdown boundary timestamps are invalid.",
        ) from error
    boundary_keys = {
        "node-quiesce",
        "cutover-backup-create",
        "cutover-backup-verify",
        "cutover-backup-restore-check",
    }
    for key, document in records.items():
        if key in boundary_keys:
            continue
        try:
            started = _timestamp(str(document["startedAt"]))
        except (KeyError, ValueError) as error:
            raise CompatibilityGateError(
                "COMPATIBILITY_EVIDENCE_TOPOLOGY_INVALID",
                f"Evidence {key} has an invalid start timestamp.",
            ) from error
        if started <= boundary:
            raise CompatibilityGateError(
                "COMPATIBILITY_EVIDENCE_TOPOLOGY_INVALID",
                f"Evidence {key} predates quiesce or cutover restore-check.",
            )


def _issue_authorization(
    root: Path,
    bindings: dict[str, object],
    *,
    output: str | os.PathLike[str],
    ttl_seconds: int,
    clock: Callable[[], datetime] | None,
) -> dict[str, object]:
    required = ("startupSnapshotPath", "startupSnapshotSha256", "cutoverLeasePath", "cutoverLeaseSha256")
    if any(bindings.get(field) is None for field in required):
        raise CompatibilityGateError(
            "COMPATIBILITY_IDENTITY_MISMATCH",
            "Authorization requires exact final startup and lease identities.",
        )
    if not isinstance(ttl_seconds, int) or isinstance(ttl_seconds, bool) or not 1 <= ttl_seconds <= 900:
        raise CompatibilityGateError("COMPATIBILITY_AUTHORIZATION_INVALID", "Authorization TTL is invalid.")
    output_path = Path(output).resolve(strict=False)
    if output_path.parent.resolve(strict=True) != root or output_path.exists():
        raise CompatibilityGateError("COMPATIBILITY_AUTHORIZATION_INVALID", "Authorization output must be new and run-local.")
    instant = (clock or (lambda: datetime.now(timezone.utc)))()
    if instant.tzinfo is None or instant.utcoffset() is None:
        raise CompatibilityGateError("COMPATIBILITY_AUTHORIZATION_INVALID", "Authorization time must be aware.")
    issued = instant.astimezone(timezone.utc)
    unsigned = {
        "schemaVersion": 1,
        "manifestKind": "promotion-authorization",
        "authorizationId": secrets.token_hex(16),
        "runId": bindings["runId"],
        "finalEvidenceRunManifestPath": str(bindings["runManifestPath"]),
        "finalEvidenceRunManifestSha256": bindings["runManifestSha256"],
        "startupSnapshotPath": str(bindings["startupSnapshotPath"]),
        "startupSnapshotSha256": bindings["startupSnapshotSha256"],
        "cutoverLeasePath": str(bindings["cutoverLeasePath"]),
        "cutoverLeaseSha256": bindings["cutoverLeaseSha256"],
        **{
            field: bindings[field]
            for field in (
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
            )
            if field in bindings
        },
        "issuedAt": issued.isoformat().replace("+00:00", "Z"),
        "expiresAt": (issued + timedelta(seconds=ttl_seconds)).isoformat().replace("+00:00", "Z"),
    }
    payload = canonical_json_bytes(
        {**unsigned, "authorizationSha256": hashlib.sha256(canonical_json_bytes(unsigned)).hexdigest()}
    )
    try:
        exclusive_write_bytes(output_path, payload)
    except DatabaseIdentityError as error:
        raise CompatibilityGateError(error.code, str(error)) from error
    resolved = output_path.resolve(strict=True)
    return {
        "authorizationPath": str(resolved),
        "authorizationSha256": hashlib.sha256(payload).hexdigest(),
        "runId": bindings["runId"],
        "finalEvidenceRunManifestSha256": bindings["runManifestSha256"],
        "startupSnapshotSha256": bindings["startupSnapshotSha256"],
    }


def _timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError
    return parsed.astimezone(timezone.utc)


def _is_sha256(value: str) -> bool:
    return len(value) == 64 and all(
        character in "0123456789abcdef" for character in value
    )


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


__all__ = [
    "CompatibilityGateError",
    "SHUTDOWN_EVIDENCE_KEYS",
    "evaluate_gate",
]
