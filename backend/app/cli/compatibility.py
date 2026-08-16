from __future__ import annotations

import argparse
import asyncio
import hashlib
import importlib
import json
from pathlib import Path
import sys
from typing import Any, Callable, Mapping, Sequence

from backend.app.api.compat.data_fingerprint import (
    DataFingerprintError,
    capture_fingerprint,
    compare_fingerprints,
    load_fingerprint_document,
)
from backend.app.api.compat.legacy_reconciliation import (
    LegacyReconciliationError,
    capture_legacy_reconciliation,
)
from backend.app.api.compat.build_identity import (
    BuildIdentityError,
    freeze_build_identity,
    load_build_identity_manifest,
    verify_build_identity,
)
from backend.app.api.compat.database_identity import (
    DatabaseIdentityError,
    canonical_json_bytes,
    exclusive_write_bytes,
    load_database_evidence_identity_manifest,
    verify_database_evidence_identity_subject,
)
from backend.app.api.compat.evidence_capture import (
    EvidenceCaptureError,
    EvidenceChildFailure,
    capture_evidence,
    create_evidence_run,
    load_evidence_run_manifest,
)
from backend.app.api.compat.gates import (
    CompatibilityGateError,
    evaluate_gate,
)
from backend.app.api.compat.suite_isolation import (
    SuiteIsolationError,
    create_suite_isolation,
)
from backend.app.api.compat.static_contract import (
    LegacyRuntimeContractError,
    StaticContractError,
    verify_legacy_runtime_contract,
    verify_static_runbook,
)
from backend.app.application.final_window import (
    CutoverLease,
    FinalWindowError,
    FrozenNodeRecovery,
    FinalWindowCoordinator,
    create_production_startup_snapshot,
)
from backend.app.application.production_candidate import (
    CandidateWriteSmokeError,
    CandidateWriteSmokeService,
)
from backend.app.application.compatibility_rehearsal import (
    CompatibilityRehearsalError,
    RecoverySmokeService,
    RestoreInstallRehearsalService,
    RollbackSmokeService,
)
from backend.app.application.production_rollback import (
    ProductionRecovery,
    ProductionRollbackCoordinator,
    ProductionRollbackError,
)
from backend.app.application.runtime_handoff import (
    HandoffReceipt,
    ProductionPromotionCoordinator,
    RuntimeHandoffError,
)


class CompatibilityCliError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


class _ArgumentParser(argparse.ArgumentParser):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        kwargs.setdefault("add_help", False)
        super().__init__(*args, **kwargs)

    def error(self, message: str) -> None:
        raise CompatibilityCliError("COMPATIBILITY_ARGUMENT_INVALID", message)

    def exit(self, status: int = 0, message: str | None = None) -> None:
        raise CompatibilityCliError(
            "COMPATIBILITY_ARGUMENT_INVALID",
            message or "The compatibility arguments are invalid.",
        )


def build_parser() -> argparse.ArgumentParser:
    parser = _ArgumentParser(prog="study-app-compatibility")
    commands = parser.add_subparsers(dest="command", required=True)

    fingerprint = commands.add_parser("fingerprint")
    fingerprint.add_argument("--database", required=True)
    fingerprint.add_argument("--database-identity-manifest")
    fingerprint.add_argument("--subject-kind")
    fingerprint.add_argument("--output", required=True)

    compare = commands.add_parser("compare")
    compare.add_argument(
        "--mode",
        required=True,
        choices=("strict-readonly", "explained-write"),
    )
    compare.add_argument("--before", required=True)
    compare.add_argument("--after", required=True)
    compare.add_argument("--delta-ledger")

    reconcile = commands.add_parser("reconcile-legacy")
    reconcile.add_argument("--database", required=True)
    reconcile.add_argument("--database-identity-manifest", required=True)
    reconcile.add_argument("--output", required=True)

    candidate = commands.add_parser("candidate-write-smoke")
    candidate.add_argument("--backup", required=True)
    candidate.add_argument("--manifest", required=True)
    candidate.add_argument("--restore-root", required=True)
    candidate.add_argument("--build-identity-manifest", required=True)
    candidate.add_argument("--parent-database-identity-manifest", required=True)
    candidate.add_argument(
        "--descendant-database-identity-output", required=True
    )
    candidate.add_argument(
        "--evidence-mode", required=True, choices=("provisional", "final")
    )
    candidate.add_argument("--evidence-dir", required=True)
    candidate.add_argument("--runner-factory", required=True)

    rollback_smoke = commands.add_parser("rollback-smoke")
    rollback_smoke.add_argument("--database", required=True)
    rollback_smoke.add_argument("--build-identity-manifest", required=True)
    rollback_smoke.add_argument("--database-identity-manifest", required=True)
    rollback_smoke.add_argument(
        "--rollback-profile", required=True, choices=("frozen-node",)
    )
    rollback_smoke.add_argument("--evidence-output", required=True)
    rollback_smoke.add_argument("--runner-factory", required=True)

    recovery_smoke = commands.add_parser("recovery-smoke")
    recovery_smoke.add_argument("--database", required=True)
    recovery_smoke.add_argument("--build-identity-manifest", required=True)
    recovery_smoke.add_argument("--database-identity-manifest", required=True)
    recovery_smoke.add_argument(
        "--python-profile", required=True, choices=("production",)
    )
    recovery_smoke.add_argument("--evidence-output", required=True)
    recovery_smoke.add_argument("--runner-factory", required=True)

    restore_rehearsal = commands.add_parser("restore-install-rehearsal")
    restore_rehearsal.add_argument("--backup", required=True)
    restore_rehearsal.add_argument("--manifest", required=True)
    restore_rehearsal.add_argument("--target-database", required=True)
    restore_rehearsal.add_argument(
        "--expected-target-sha256", type=_sha256, required=True
    )
    restore_rehearsal.add_argument("--rehearsal-root", required=True)
    restore_rehearsal.add_argument("--build-identity-manifest", required=True)
    restore_rehearsal.add_argument("--parent-database-identity-manifest", required=True)
    restore_rehearsal.add_argument(
        "--installed-database-identity-output", required=True
    )
    restore_rehearsal.add_argument("--evidence-output", required=True)

    freeze = commands.add_parser("freeze-identity")
    freeze.add_argument("--source-root", required=True)
    freeze.add_argument("--build-identity-directory", required=True)
    freeze.add_argument("--python-artifact", action="append", required=True)
    freeze.add_argument("--frontend-root", required=True)
    freeze.add_argument("--frontend-manifest", required=True)
    freeze.add_argument("--deployment-kind", choices=("container", "native-windows"), default="container")
    freeze.add_argument("--resolved-compose", "--compose-file")
    freeze.add_argument("--image-digest", action="append", default=[])
    freeze.add_argument("--native-runtime-spec")

    verify = commands.add_parser("verify-identity")
    verify.add_argument("--build-identity-manifest", required=True)
    verify.add_argument("--source-root", required=True)
    verify.add_argument("--python-artifact", action="append", required=True)
    verify.add_argument("--frontend-root", required=True)
    verify.add_argument("--frontend-manifest", required=True)
    verify.add_argument("--deployment-kind", choices=("container", "native-windows"), default="container")
    verify.add_argument("--resolved-compose", "--compose-file")
    verify.add_argument("--image-digest", action="append", default=[])
    verify.add_argument("--native-runtime-spec")

    evidence_run = commands.add_parser("create-evidence-run")
    evidence_run.add_argument("--evidence-root", required=True)
    evidence_run.add_argument("--run-id", required=True)
    evidence_run.add_argument(
        "--phase", required=True, choices=("provisional", "final")
    )
    evidence_run.add_argument("--build-identity-manifest", required=True)
    evidence_run.add_argument("--database-identity-manifest", required=True)
    evidence_run.add_argument("--expected-key", action="append", required=True)

    capture = commands.add_parser("capture-evidence")
    capture.add_argument("--key", required=True)
    capture.add_argument(
        "--phase", required=True, choices=("provisional", "final")
    )
    capture.add_argument(
        "--result-kind", required=True, choices=("machine-summary", "json-cli")
    )
    capture.add_argument("--run-manifest", required=True)
    capture.add_argument(
        "--expected-run-manifest-sha256", type=_sha256, required=True
    )
    capture.add_argument("--database-identity-manifest")
    capture.add_argument("--isolation-manifest")
    capture.add_argument("--artifact", action="append", default=[])
    capture.add_argument("--artifact-from-json", action="append", default=[])
    # Kept as a separate field because this is resolved from the child JSON
    # result by the evidence adapter in a later validation step.
    capture.add_argument("--database-identity-from-json")
    capture.add_argument("--cutover-lease")
    capture.add_argument("--cutover-token-file")
    capture.add_argument("--startup-snapshot")
    capture.add_argument("--expected-startup-snapshot-sha256", type=_sha256)
    capture.add_argument("--build-identity-manifest", required=True)
    capture.add_argument("--output", required=True)
    capture.add_argument("--summary-artifact")
    capture.add_argument("--cwd")
    capture.add_argument("argv", nargs=argparse.REMAINDER)

    isolation = commands.add_parser("create-suite-isolation")
    isolation.add_argument("--run-manifest", required=True)
    isolation.add_argument(
        "--expected-run-manifest-sha256", type=_sha256, required=True
    )
    isolation.add_argument("--suite-key", required=True)
    isolation.add_argument("--deny-live-database", required=True)
    isolation.add_argument("--deny-live-settings", required=True)
    isolation.add_argument("--deny-live-pdf-root", required=True)
    isolation.add_argument("--deny-live-vault-root")
    isolation.add_argument("--deny-live-path", action="append", default=[])
    isolation.add_argument("--deny-live-keyring", type=_binary_flag, required=True)
    isolation.add_argument("--deny-network", type=_binary_flag, required=True)
    isolation.add_argument("--deny-providers", type=_binary_flag)
    isolation.add_argument("--output", required=True)

    gate = commands.add_parser("gate")
    gate.add_argument(
        "--phase", required=True, choices=("preflight", "convergence", "shutdown")
    )
    gate.add_argument("--evidence-dir", required=True)
    gate.add_argument("--run-manifest")
    gate.add_argument("--expected-run-manifest-sha256", type=_sha256)
    gate.add_argument("--final-evidence-run-manifest")
    gate.add_argument(
        "--expected-final-evidence-run-manifest-sha256", type=_sha256
    )
    gate.add_argument("--startup-snapshot")
    gate.add_argument("--expected-startup-snapshot-sha256", type=_sha256)
    gate.add_argument("--cutover-lease")
    gate.add_argument("--build-identity-manifest", required=True)
    gate.add_argument("--database-identity-manifest", required=True)
    gate.add_argument("--authorization-output")
    gate.add_argument("--authorization-ttl-seconds", type=int, default=900)

    startup = commands.add_parser("create-startup-snapshot")
    startup.add_argument("--final-evidence-run-manifest", required=True)
    startup.add_argument(
        "--expected-final-evidence-run-manifest-sha256",
        type=_sha256,
        required=True,
    )
    startup.add_argument("--build-identity-manifest", required=True)
    startup.add_argument("--database-identity-manifest", required=True)
    startup.add_argument("--frozen-node-rollback-map", required=True)
    startup.add_argument(
        "--production-profile", required=True, choices=("production",)
    )
    startup.add_argument("--output", required=True)

    begin_window = commands.add_parser("begin-final-window")
    begin_window.add_argument("--final-evidence-run-manifest", required=True)
    begin_window.add_argument(
        "--expected-final-evidence-run-manifest-sha256",
        type=_sha256,
        required=True,
    )
    begin_window.add_argument("--startup-snapshot", required=True)
    begin_window.add_argument(
        "--expected-startup-snapshot-sha256", type=_sha256, required=True
    )
    begin_window.add_argument("--owner-marker", required=True)
    begin_window.add_argument("--runtime-namespace", required=True)
    begin_window.add_argument("--coordinator-pid", type=int, required=True)
    begin_window.add_argument("--operator-pid", type=int, required=True)
    begin_window.add_argument(
        "--heartbeat-timeout-seconds", type=int, required=True
    )
    begin_window.add_argument("--lease-output", required=True)
    begin_window.add_argument("--token-file-output", required=True)
    begin_window.add_argument("--operations-factory", required=True)
    begin_window.add_argument("--watchdog-factory", required=True)
    begin_window.add_argument(
        "--rollback-profile", choices=("frozen-node",), default="frozen-node"
    )

    quiesce = commands.add_parser("quiesce-live")
    quiesce.add_argument("--cutover-lease", required=True)
    quiesce.add_argument("--cutover-token-file", required=True)
    quiesce.add_argument("--operations-factory", required=True)
    quiesce.add_argument("--watchdog-factory", required=True)

    abort = commands.add_parser("abort-cutover")
    abort.add_argument("--cutover-lease", required=True)
    abort.add_argument("--cutover-token-file", required=True)
    abort.add_argument("--reason-code", required=True)
    abort.add_argument("--recovery-output", required=True)
    abort.add_argument("--operations-factory", required=True)
    abort.add_argument("--watchdog-factory", required=True)

    promote = commands.add_parser("promote")
    promote.add_argument("--authorization", required=True)
    promote.add_argument(
        "--expected-authorization-sha256", type=_sha256, required=True
    )
    promote.add_argument("--final-evidence-run-manifest", required=True)
    promote.add_argument(
        "--expected-final-evidence-run-manifest-sha256",
        type=_sha256,
        required=True,
    )
    promote.add_argument("--cutover-lease", required=True)
    promote.add_argument("--cutover-token-file", required=True)
    promote.add_argument("--startup-snapshot", required=True)
    promote.add_argument(
        "--expected-startup-snapshot-sha256", type=_sha256, required=True
    )
    promote.add_argument("--build-identity-manifest", required=True)
    promote.add_argument("--database-identity-manifest", required=True)
    promote.add_argument("--owner-marker", required=True)
    promote.add_argument(
        "--python-profile", required=True, choices=("production",)
    )
    promote.add_argument(
        "--rollback-profile", required=True, choices=("frozen-node",)
    )
    promote.add_argument("--handoff-receipt-output", required=True)
    promote.add_argument("--evidence-output", required=True)
    promote.add_argument("--operations-factory", required=True)
    promote.add_argument("--smoke-evidence")

    rollback_production = commands.add_parser("rollback-production")
    rollback_production.add_argument("--handoff-receipt", required=True)
    rollback_production.add_argument(
        "--expected-handoff-receipt-sha256", type=_sha256, required=True
    )
    rollback_production.add_argument("--startup-snapshot", required=True)
    rollback_production.add_argument(
        "--expected-startup-snapshot-sha256", type=_sha256, required=True
    )
    rollback_production.add_argument("--build-identity-manifest", required=True)
    rollback_production.add_argument("--database-identity-manifest", required=True)
    rollback_production.add_argument("--p0-origin-receipt", required=True)
    rollback_production.add_argument(
        "--expected-p0-origin-receipt-sha256", type=_sha256, required=True
    )
    rollback_production.add_argument("--owner-marker", required=True)
    rollback_production.add_argument("--recovery-lease-output", required=True)
    rollback_production.add_argument("--recovery-output", required=True)
    rollback_production.add_argument("--operations-factory", required=True)

    static_runbook = commands.add_parser("verify-static-runbook")
    static_runbook.add_argument("--readme", required=True)
    static_runbook.add_argument("--database-doc", required=True)

    legacy_contract = commands.add_parser("verify-legacy-runtime")
    legacy_contract.add_argument("--repository-root", required=True)
    legacy_contract.add_argument("--database", required=True)
    return parser


def run(arguments: Sequence[str]) -> dict[str, Any]:
    options = build_parser().parse_args(list(arguments))
    if options.command == "fingerprint":
        if (options.database_identity_manifest is None) != (options.subject_kind is None):
            raise CompatibilityCliError(
                "FINGERPRINT_IDENTITY_ARGUMENT_INVALID",
                "Database identity and subject kind must be supplied together.",
            )
        if options.database_identity_manifest is not None:
            identity = load_database_evidence_identity_manifest(
                options.database_identity_manifest
            )
            if identity.subject_kind != options.subject_kind:
                raise DatabaseIdentityError(
                    "DATABASE_IDENTITY_SUBJECT_MISMATCH",
                    "The fingerprint subject kind does not match its identity manifest.",
                )
            verify_database_evidence_identity_subject(
                database=options.database,
                identity=identity,
            )
        report = capture_fingerprint(
            database=options.database,
            output=options.output,
        )
        return {
            "ok": True,
            "operation": "fingerprint",
            "output": str(options.output),
            "canonicalDataSha256": report["canonicalDataSha256"],
        }
    if options.command == "compare":
        before = load_fingerprint_document(options.before)
        after = load_fingerprint_document(options.after)
        ledger = None
        if options.delta_ledger is not None:
            try:
                with open(options.delta_ledger, "rb") as handle:
                    ledger = json.loads(handle.read().decode("utf-8"))
            except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as error:
                raise DataFingerprintError(
                    "FINGERPRINT_DELTA_LEDGER_INVALID",
                    "The delta ledger is not valid JSON.",
                ) from error
        compare_fingerprints(
            before,
            after,
            mode=options.mode,
            delta_ledger=ledger,
        )
        return {
            "ok": True,
            "operation": "compare",
            "mode": options.mode,
        }
    if options.command == "reconcile-legacy":
        ledger = capture_legacy_reconciliation(
            database=options.database,
            database_identity_manifest=options.database_identity_manifest,
            output=options.output,
        )
        return {
            "ok": True,
            "operation": "reconcile-legacy",
            "output": str(options.output),
            "databaseLineageId": ledger["databaseLineageId"],
            "subjectDatabaseId": ledger["subjectDatabaseId"],
            "itemCount": ledger["itemCount"],
            "classificationCounts": ledger["classificationCounts"],
        }
    if options.command == "candidate-write-smoke":
        runner = _load_factory(options.runner_factory, label="runner")()
        result = _run_async(
            CandidateWriteSmokeService().run(
                backup=Path(options.backup),
                manifest=Path(options.manifest),
                restore_root=Path(options.restore_root),
                build_identity_manifest=Path(options.build_identity_manifest),
                parent_database_identity_manifest=Path(
                    options.parent_database_identity_manifest
                ),
                descendant_database_identity_output=Path(
                    options.descendant_database_identity_output
                ),
                evidence_mode=options.evidence_mode,
                evidence_dir=Path(options.evidence_dir),
                runner=runner,
            )
        )
        return {"operation": "candidate-write-smoke", **result.to_dict()}
    if options.command in {"rollback-smoke", "recovery-smoke"}:
        runner = _load_factory(options.runner_factory, label="runner")()
        if options.command == "rollback-smoke":
            result = RollbackSmokeService(runner).run(
                database=Path(options.database),
                build_identity_manifest=Path(options.build_identity_manifest),
                database_identity_manifest=Path(options.database_identity_manifest),
                rollback_profile=options.rollback_profile,
                evidence_output=Path(options.evidence_output),
            )
        else:
            result = RecoverySmokeService(runner).run(
                database=Path(options.database),
                build_identity_manifest=Path(options.build_identity_manifest),
                database_identity_manifest=Path(options.database_identity_manifest),
                python_profile=options.python_profile,
                evidence_output=Path(options.evidence_output),
            )
        return {"operation": options.command, **result.to_dict()}
    if options.command == "restore-install-rehearsal":
        result = RestoreInstallRehearsalService().run(
            backup=Path(options.backup),
            manifest=Path(options.manifest),
            target_database=Path(options.target_database),
            expected_target_sha256=options.expected_target_sha256,
            rehearsal_root=Path(options.rehearsal_root),
            build_identity_manifest=Path(options.build_identity_manifest),
            parent_database_identity_manifest=Path(
                options.parent_database_identity_manifest
            ),
            installed_database_identity_output=Path(
                options.installed_database_identity_output
            ),
            evidence_output=Path(options.evidence_output),
        )
        return {"operation": "restore-install-rehearsal", **result.to_dict()}
    if options.command == "freeze-identity":
        identity_inputs = _build_identity_adapter_inputs(options)
        manifest = freeze_build_identity(
            repository=options.source_root,
            build_identity_directory=options.build_identity_directory,
            python_artifacts=tuple(options.python_artifact),
            frontend_root=options.frontend_root,
            frontend_manifest=options.frontend_manifest,
            **identity_inputs,
        )
        return {
            "ok": True,
            "operation": "freeze-identity",
            "buildId": manifest.build_id,
            "manifestPath": str(manifest.manifest_path),
            "manifestFileSha256": manifest.manifest_file_sha256,
            "deploymentKind": manifest.deployment_kind,
        }
    if options.command == "verify-identity":
        identity_inputs = _build_identity_adapter_inputs(options)
        manifest = verify_build_identity(
            build_identity_manifest=options.build_identity_manifest,
            repository=options.source_root,
            python_artifacts=tuple(options.python_artifact),
            frontend_root=options.frontend_root,
            frontend_manifest=options.frontend_manifest,
            **identity_inputs,
        )
        return {
            "ok": True,
            "operation": "verify-identity",
            "buildId": manifest.build_id,
            "manifestPath": str(manifest.manifest_path),
            "manifestFileSha256": manifest.manifest_file_sha256,
            "deploymentKind": manifest.deployment_kind,
        }
    if options.command == "create-evidence-run":
        run_manifest = create_evidence_run(
            evidence_root=options.evidence_root,
            run_id=options.run_id,
            phase=options.phase,
            build_identity_manifest=options.build_identity_manifest,
            database_identity_manifest=options.database_identity_manifest,
            expected_keys=tuple(options.expected_key),
        )
        return {
            "ok": True,
            "operation": "create-evidence-run",
            "runId": run_manifest.run_id,
            "phase": run_manifest.phase,
            "runDirectory": str(run_manifest.run_directory),
            "runManifestPath": str(run_manifest.manifest_path),
            "runManifestFileSha256": run_manifest.manifest_file_sha256,
            "runManifestSha256": run_manifest.run_manifest_sha256,
        }
    if options.command == "capture-evidence":
        argv = list(options.argv)
        if argv and argv[0] == "--":
            argv.pop(0)
        artifacts = tuple(_parse_artifact(value) for value in options.artifact)
        record = capture_evidence(
            key=options.key,
            phase=options.phase,
            result_kind=options.result_kind,
            run_manifest=options.run_manifest,
            expected_run_manifest_sha256=options.expected_run_manifest_sha256,
            database_identity_manifest=options.database_identity_manifest,
            database_identity_from_json=options.database_identity_from_json,
            isolation_manifest=options.isolation_manifest,
            artifacts=artifacts,
            artifact_from_json=tuple(options.artifact_from_json),
            cutover_lease=options.cutover_lease,
            cutover_token_file=options.cutover_token_file,
            startup_snapshot=options.startup_snapshot,
            expected_startup_snapshot_sha256=(
                options.expected_startup_snapshot_sha256
            ),
            build_identity_manifest=options.build_identity_manifest,
            output=options.output,
            summary_artifact=options.summary_artifact,
            argv=tuple(argv),
            cwd=options.cwd,
        )
        return {
            "ok": True,
            "operation": "capture-evidence",
            "evidenceKey": record.evidence_key,
            "recordPath": str(record.record_path),
            "exitCode": record.exit_code,
            "totals": record.totals,
            "failures": record.failures,
            "skips": record.skips,
            "stdoutPath": str(record.stdout_path),
            "stderrPath": str(record.stderr_path),
        }
    if options.command == "create-suite-isolation":
        denied = [
            options.deny_live_database,
            options.deny_live_settings,
            options.deny_live_pdf_root,
            *options.deny_live_path,
        ]
        if options.deny_live_vault_root is not None:
            denied.append(options.deny_live_vault_root)
        suite = create_suite_isolation(
            run_manifest=options.run_manifest,
            expected_run_manifest_sha256=options.expected_run_manifest_sha256,
            suite_key=options.suite_key,
            output=options.output,
            deny_live_paths=tuple(denied),
            deny_network=options.deny_network,
            deny_providers=(
                options.deny_live_keyring
                if options.deny_providers is None
                else options.deny_providers
            ),
        )
        return {
            "ok": True,
            "operation": "create-suite-isolation",
            "suiteKey": suite.suite_key,
            "manifestPath": str(suite.manifest_path),
            "sandboxRoot": str(suite.sandbox_root),
            "databasePath": str(suite.database_path),
            "settingsPath": str(suite.settings_path),
            "pdfRoot": str(suite.pdf_root),
            "vaultRoot": str(suite.vault_root),
            "keyringRoot": str(suite.keyring_root),
            "liveAccessCount": suite.live_access_count,
        }
    if options.command == "gate":
        run_path, run_sha = _gate_run_identity(options)
        run_manifest = load_evidence_run_manifest(
            run_path,
            expected_file_sha256=run_sha,
        )
        build = _verify_gate_identity_binding(
            run_manifest,
            options.build_identity_manifest,
            options.database_identity_manifest,
        )
        result = evaluate_gate(
            options.evidence_dir,
            phase=options.phase,
            final_evidence_run_manifest=run_path,
            expected_final_evidence_run_manifest_sha256=run_sha,
            startup_snapshot=options.startup_snapshot,
            expected_startup_snapshot_sha256=(
                options.expected_startup_snapshot_sha256
            ),
            cutover_lease=options.cutover_lease,
            authorization_output=options.authorization_output,
            authorization_ttl_seconds=options.authorization_ttl_seconds,
        )
        return {"operation": "gate", "buildId": build.build_id, **result}
    if options.command == "create-startup-snapshot":
        rollback_map = _strict_json_object(
            options.frozen_node_rollback_map,
            code="STARTUP_ROLLBACK_MAP_INVALID",
            label="frozen Node rollback map",
        )
        snapshot = create_production_startup_snapshot(
            final_evidence_run_manifest=options.final_evidence_run_manifest,
            expected_final_evidence_run_manifest_sha256=(
                options.expected_final_evidence_run_manifest_sha256
            ),
            build_identity_manifest=options.build_identity_manifest,
            database_identity_manifest=options.database_identity_manifest,
            frozen_node_rollback_map=rollback_map,
            output=options.output,
        )
        return {
            "ok": True,
            "operation": "create-startup-snapshot",
            "startupSnapshotPath": str(snapshot.path),
            "startupSnapshotFileSha256": snapshot.file_sha256,
            "runId": snapshot.run_id,
            "buildId": snapshot.build_id,
            "databaseLineageId": snapshot.database_lineage_id,
            "liveSubjectDatabaseId": snapshot.live_subject_database_id,
            "frozenNodeRollbackMapSha256": snapshot.rollback_map_sha256,
        }
    if options.command == "begin-final-window":
        operations = _load_factory(options.operations_factory, label="operations")()
        watchdog = _load_factory(options.watchdog_factory, label="watchdog")()
        coordinator = FinalWindowCoordinator(
            operations=operations,
            watchdog=watchdog,
            coordinator_pid=options.coordinator_pid,
        )
        lease = coordinator.begin_final_window(
            final_evidence_run_manifest=options.final_evidence_run_manifest,
            expected_final_evidence_run_manifest_sha256=(
                options.expected_final_evidence_run_manifest_sha256
            ),
            startup_snapshot=options.startup_snapshot,
            expected_startup_snapshot_sha256=options.expected_startup_snapshot_sha256,
            owner_marker=options.owner_marker,
            runtime_namespace=options.runtime_namespace,
            operator_pid=options.operator_pid,
            heartbeat_timeout_seconds=options.heartbeat_timeout_seconds,
            lease_output=options.lease_output,
            token_file_output=options.token_file_output,
        )
        return _lease_result("begin-final-window", lease)
    if options.command == "quiesce-live":
        operations = _load_factory(options.operations_factory, label="operations")()
        watchdog = _load_factory(options.watchdog_factory, label="watchdog")()
        coordinator = FinalWindowCoordinator(operations=operations, watchdog=watchdog)
        lease = coordinator.quiesce_live(
            cutover_lease=options.cutover_lease,
            cutover_token_file=options.cutover_token_file,
        )
        return _lease_result("quiesce-live", lease)
    if options.command == "abort-cutover":
        operations = _load_factory(options.operations_factory, label="operations")()
        watchdog = _load_factory(options.watchdog_factory, label="watchdog")()
        coordinator = FinalWindowCoordinator(operations=operations, watchdog=watchdog)
        recovery = coordinator.abort_cutover(
            cutover_lease=options.cutover_lease,
            cutover_token_file=options.cutover_token_file,
            reason=options.reason_code,
            recovery_output=options.recovery_output,
        )
        return _recovery_result("abort-cutover", recovery)
    if options.command == "promote":
        operations = _load_factory(options.operations_factory, label="operations")()
        coordinator = ProductionPromotionCoordinator(operations=operations)
        handoff = coordinator.begin_handoff(
            authorization=options.authorization,
            expected_authorization_sha256=options.expected_authorization_sha256,
            cutover_lease=options.cutover_lease,
            cutover_token_file=options.cutover_token_file,
            startup_snapshot=options.startup_snapshot,
            expected_startup_snapshot_sha256=options.expected_startup_snapshot_sha256,
            owner_marker=options.owner_marker,
        )
        try:
            smoke_evidence = _promotion_smoke_evidence(
                operations,
                python_profile=options.python_profile,
                rollback_profile=options.rollback_profile,
                explicit_path=options.smoke_evidence,
            )
        except BaseException:
            coordinator.rollback_to_frozen_node(
                handoff,
                reason="promotion_smoke_failed",
            )
            raise
        try:
            receipt = coordinator.commit_python_owner(
                handoff,
                smoke_evidence=smoke_evidence,
                handoff_receipt_output=options.handoff_receipt_output,
            )
        except Exception:
            # The coordinator owns the rollback tail after handoff takeover;
            # preserve the original domain error while ensuring a best-effort
            # recovery attempt for custom coordinator implementations.
            raise
        evidence_document = {
            "schemaVersion": 1,
            "evidenceKind": "production-promotion",
            "ok": True,
            "pythonProfile": options.python_profile,
            "rollbackProfile": options.rollback_profile,
            "smokeEvidence": smoke_evidence,
            "handoffReceiptPath": str(receipt.path),
            "handoffReceiptFileSha256": receipt.file_sha256,
            "runId": receipt.run_id,
        }
        evidence_path = Path(options.evidence_output).resolve(strict=False)
        _exclusive_json_output(evidence_path, evidence_document)
        return {
            "ok": True,
            "operation": "promote",
            "ownerState": "python_active",
            "handoffReceiptPath": str(receipt.path),
            "handoffReceiptSha256": receipt.file_sha256,
            "evidencePath": str(evidence_path),
            "evidenceFileSha256": _file_sha256(evidence_path),
            "runId": receipt.run_id,
        }
    if options.command == "rollback-production":
        operations = _load_factory(options.operations_factory, label="operations")()
        coordinator = ProductionRollbackCoordinator(operations=operations)
        recovery = coordinator.rollback_production(
            handoff_receipt=options.handoff_receipt,
            expected_handoff_receipt_sha256=options.expected_handoff_receipt_sha256,
            startup_snapshot=options.startup_snapshot,
            expected_startup_snapshot_sha256=options.expected_startup_snapshot_sha256,
            build_identity_manifest=options.build_identity_manifest,
            database_identity_manifest=options.database_identity_manifest,
            p0_origin_receipt=options.p0_origin_receipt,
            expected_p0_origin_receipt_sha256=options.expected_p0_origin_receipt_sha256,
            owner_marker=options.owner_marker,
            recovery_lease_output=options.recovery_lease_output,
            recovery_output=options.recovery_output,
        )
        return _production_recovery_result("rollback-production", recovery)
    if options.command == "verify-static-runbook":
        result = verify_static_runbook(
            readme=options.readme,
            database_doc=options.database_doc,
        )
        return {
            "ok": True,
            "operation": "verify-static-runbook",
            "readme": str(result["readme"]),
            "databaseDoc": str(result["databaseDoc"]),
            "runtimeOwnerMarker": str(result["runtimeOwnerMarker"]),
            "stateNeutral": result["stateNeutral"],
            "deletionBoundaryPreserved": result["deletionBoundaryPreserved"],
        }
    if options.command == "verify-legacy-runtime":
        result = verify_legacy_runtime_contract(
            repository_root=options.repository_root,
            database=options.database,
        )
        return {
            "ok": True,
            "operation": "verify-legacy-runtime",
            "alembicRevision": result["alembicRevision"],
            "legacyCredentialFields": result["legacyCredentialFields"],
            "legacyRouteCount": result["legacyRouteCount"],
            "triggerNames": result["triggerNames"],
            "processingJobs": result["processingJobs"],
        }
    raise CompatibilityCliError(
        "COMPATIBILITY_COMMAND_INVALID",
        "The compatibility command is unsupported.",
    )


def _load_factory(value: str, *, label: str) -> Callable[[], Any]:
    module_name, separator, attribute_path = value.partition(":")
    if (
        not separator
        or not module_name
        or not attribute_path
        or value.count(":") != 1
    ):
        raise CompatibilityCliError(
            "COMPATIBILITY_FACTORY_INVALID",
            f"The {label} factory must use module:callable syntax.",
        )
    try:
        target: object = _load_factory_module(module_name)
        for segment in attribute_path.split("."):
            if not segment or segment.startswith("_"):
                raise ValueError("factory attribute is private or empty")
            target = getattr(target, segment)
    except Exception as error:
        if isinstance(error, CompatibilityCliError):
            raise
        raise CompatibilityCliError(
            "COMPATIBILITY_FACTORY_INVALID",
            f"The {label} factory could not be imported.",
        ) from error
    if not callable(target):
        raise CompatibilityCliError(
            "COMPATIBILITY_FACTORY_INVALID",
            f"The {label} factory is not callable.",
        )
    return target  # type: ignore[return-value]


def _load_factory_module(module_name: str) -> object:
    """Reuse an already-loaded module when discovery gave it an alias.

    ``unittest discover`` imports files below its start directory as top-level
    modules.  A runner factory commonly names that same file by its package
    path, and importing it again would duplicate module-level sentinels and
    test doubles.  Resolve the requested module's source first and reuse the
    existing module with the identical source path when available.
    """

    loaded = sys.modules.get(module_name)
    if loaded is not None:
        return loaded
    try:
        spec = importlib.util.find_spec(module_name)
        origin = spec.origin if spec is not None else None
    except (ImportError, AttributeError, ValueError):
        origin = None
    if origin and origin not in {"built-in", "frozen"}:
        try:
            resolved_origin = Path(origin).resolve(strict=False)
        except OSError:
            resolved_origin = None
        if resolved_origin is not None:
            for candidate in tuple(sys.modules.values()):
                candidate_file = getattr(candidate, "__file__", None)
                if not candidate_file:
                    continue
                try:
                    if Path(candidate_file).resolve(strict=False) == resolved_origin:
                        return candidate
                except OSError:
                    continue
    return importlib.import_module(module_name)


def _run_async(awaitable: Any) -> Any:
    try:
        return asyncio.run(awaitable)
    except RuntimeError as error:
        raise CompatibilityCliError(
            "COMPATIBILITY_ASYNC_CONTEXT_INVALID",
            "An async compatibility service cannot run inside an active event loop.",
        ) from error


def _parse_artifact(value: str) -> tuple[str, str]:
    name, separator, path = value.partition("=")
    if not separator or not name or not path:
        raise EvidenceCaptureError(
            "EVIDENCE_ARTIFACT_INVALID",
            "Artifacts must use name=path syntax.",
        )
    return name, path


def _lease_result(operation: str, lease: CutoverLease) -> dict[str, object]:
    return {
        "ok": True,
        "operation": operation,
        "runId": lease.run_id,
        "phase": lease.phase,
        "version": lease.version,
        "cutoverLeasePath": str(lease.path),
        "cutoverLeaseFileSha256": lease.file_sha256,
        "cutoverTokenFilePath": str(lease.token_file_path),
        "cutoverTokenFileSha256": _file_sha256(lease.token_file_path),
    }


def _recovery_result(operation: str, recovery: FrozenNodeRecovery) -> dict[str, object]:
    document = _canonical_document(recovery.canonical_bytes)
    legacy_smoke = document.get("legacySmoke")
    return {
        "ok": True,
        "operation": operation,
        "recoveryPath": str(recovery.path),
        "recoveryFileSha256": recovery.file_sha256,
        "ownerState": recovery.owner_state,
        "runId": recovery.run_id,
        "legacySmokePassed": isinstance(legacy_smoke, dict)
        and legacy_smoke.get("ok") is True,
        "events": document.get("events", []),
    }


def _production_recovery_result(
    operation: str,
    recovery: ProductionRecovery,
) -> dict[str, object]:
    return {
        "ok": True,
        "operation": operation,
        "recoveryPath": str(recovery.path),
        "recoveryFileSha256": recovery.file_sha256,
        "handoffReceiptId": recovery.receipt_id,
        "ownerState": recovery.owner_state,
        "events": list(recovery.events),
        "legacySmokePassed": recovery.owner_state == "node_active"
        and "legacy_smoked" in recovery.events,
    }


def _canonical_document(payload: bytes) -> dict[str, object]:
    try:
        document = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise CompatibilityCliError(
            "COMPATIBILITY_RESULT_INVALID",
            "A service result did not contain canonical JSON.",
        ) from error
    if not isinstance(document, dict):
        raise CompatibilityCliError(
            "COMPATIBILITY_RESULT_INVALID",
            "A service result must be a JSON object.",
        )
    return document


def _promotion_smoke_evidence(
    operations: object,
    *,
    python_profile: str,
    rollback_profile: str,
    explicit_path: str | None,
) -> dict[str, object]:
    if explicit_path is not None:
        path = Path(explicit_path).resolve(strict=True)
        document = _canonical_document(path.read_bytes())
    else:
        method: object | None = None
        for name in (
            "run_promotion_smoke",
            "promotion_smoke",
            "run_python_smoke",
            "python_smoke",
        ):
            candidate = getattr(operations, name, None)
            if callable(candidate):
                method = candidate
                break
        if method is None:
            raise CompatibilityCliError(
                "PROMOTION_SMOKE_REQUIRED",
                "The operations factory must provide promotion smoke evidence.",
            )
        try:
            document = method(
                python_profile=python_profile,
                rollback_profile=rollback_profile,
            )
        except TypeError:
            try:
                document = method(python_profile, rollback_profile)
            except Exception as error:
                raise CompatibilityCliError(
                    "PROMOTION_SMOKE_FAILED",
                    "The promotion smoke operation failed.",
                ) from error
        except Exception as error:
            raise CompatibilityCliError(
                "PROMOTION_SMOKE_FAILED",
                "The promotion smoke operation failed.",
            ) from error
    if not isinstance(document, Mapping):
        raise CompatibilityCliError(
            "PROMOTION_SMOKE_INVALID",
            "Promotion smoke evidence must be a JSON object.",
        )
    result = dict(document)
    role_locks = result.get("roleLocks")
    if (
        result.get("ok") is not True
        or not isinstance(role_locks, dict)
        or set(role_locks) != {"worker", "scheduler"}
        or any(
            not isinstance(value, str)
            or len(value) != 64
            or any(character not in "0123456789abcdef" for character in value)
            for value in role_locks.values()
        )
    ):
        raise CompatibilityCliError(
            "PROMOTION_SMOKE_INVALID",
            "Promotion smoke evidence must include successful worker and scheduler role locks.",
        )
    return result


def _exclusive_json_output(path: Path, document: Mapping[str, object]) -> None:
    if path.exists() or not path.parent.is_dir():
        raise CompatibilityCliError(
            "COMPATIBILITY_OUTPUT_INVALID",
            "The compatibility output must be an exclusive new file.",
        )
    payload = canonical_json_bytes(dict(document))
    try:
        exclusive_write_bytes(path, payload)
    except DatabaseIdentityError as error:
        raise CompatibilityCliError(error.code, str(error)) from error


def _file_sha256(path: str | Path) -> str:
    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except OSError as error:
        raise CompatibilityCliError(
            "COMPATIBILITY_OUTPUT_INVALID",
            "A compatibility result path is unreadable.",
        ) from error


def _build_identity_adapter_inputs(options: argparse.Namespace) -> dict[str, object]:
    if options.deployment_kind == "native-windows":
        if (
            not isinstance(options.native_runtime_spec, str)
            or not options.native_runtime_spec
            or options.resolved_compose is not None
            or options.image_digest
        ):
            raise CompatibilityCliError(
                "BUILD_DEPLOYMENT_INPUT_INVALID",
                "Native identity requires only --native-runtime-spec adapter input.",
            )
        return {
            "deployment_kind": "native-windows",
            "native_runtime_spec": options.native_runtime_spec,
        }
    if (
        not isinstance(options.resolved_compose, str)
        or not options.resolved_compose
        or not options.image_digest
        or options.native_runtime_spec is not None
    ):
        raise CompatibilityCliError(
            "BUILD_DEPLOYMENT_INPUT_INVALID",
            "Container identity requires Compose and at least one image digest.",
        )
    return {
        "deployment_kind": "container",
        "resolved_compose": options.resolved_compose,
        "image_digests": _image_digests(options.image_digest),
    }


def _image_digests(values: Sequence[str]) -> dict[str, str]:
    result: dict[str, str] = {}
    for value in values:
        name, separator, digest = value.partition("=")
        if not separator or not name or name in result:
            raise BuildIdentityError(
                "BUILD_IMAGE_DIGEST_INVALID",
                "Image digests must use unique name=sha256:<digest> values.",
            )
        result[name] = digest
    return result


def _binary_flag(value: str) -> bool:
    if value not in {"0", "1"}:
        raise argparse.ArgumentTypeError("expected 0 or 1")
    return value == "1"


def _sha256(value: str) -> str:
    if len(value) != 64 or any(
        character not in "0123456789abcdef" for character in value
    ):
        raise argparse.ArgumentTypeError("expected lowercase SHA-256")
    return value


def _gate_run_identity(options: argparse.Namespace) -> tuple[str, str]:
    provisional = (
        options.run_manifest,
        options.expected_run_manifest_sha256,
    )
    final = (
        options.final_evidence_run_manifest,
        options.expected_final_evidence_run_manifest_sha256,
    )
    selected = provisional if options.phase == "preflight" else final
    other = final if options.phase == "preflight" else provisional
    if any(value is not None for value in other) or any(value is None for value in selected):
        raise EvidenceCaptureError(
            "EVIDENCE_RUN_ARGUMENT_INVALID",
            "The gate requires exactly one phase-typed run manifest path and SHA-256.",
        )
    return str(selected[0]), str(selected[1])


def _verify_gate_identity_binding(
    run_manifest: Any,
    build_identity_manifest: str,
    database_identity_manifest: str,
) -> Any:
    build = load_build_identity_manifest(build_identity_manifest)
    database = load_database_evidence_identity_manifest(database_identity_manifest)
    if (
        build.manifest_path != run_manifest.build_identity_manifest_path
        or build.manifest_file_sha256
        != run_manifest.build_identity_manifest_sha256
        or database.manifest_path != run_manifest.database_identity_manifest_path
        or database.identity_manifest_file_sha256
        != run_manifest.database_identity_manifest_sha256
    ):
        raise EvidenceCaptureError(
            "EVIDENCE_IDENTITY_MISMATCH",
            "The gate identities do not match the evidence run.",
        )
    return build


def _strict_json_object(path: str, *, code: str, label: str) -> dict[str, object]:
    duplicates: list[str] = []

    def pairs(values: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in values:
            if key in result:
                duplicates.append(key)
            result[key] = value
        return result

    try:
        payload = Path(path).resolve(strict=True).read_bytes()
        document = json.loads(payload.decode("utf-8"), object_pairs_hook=pairs)
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise FinalWindowError(code, f"The {label} is not valid JSON.") from error
    if duplicates or not isinstance(document, dict):
        raise FinalWindowError(code, f"The {label} must be one strict JSON object.")
    return document


def main(arguments: Sequence[str] | None = None) -> int:
    try:
        payload = run(sys.argv[1:] if arguments is None else arguments)
    except EvidenceChildFailure as error:
        print(
            json.dumps(
                {"ok": False, "error": {"code": error.code, "message": str(error)}},
                ensure_ascii=False,
                separators=(",", ":"),
            ),
            file=sys.stderr,
        )
        return error.exit_code if error.exit_code else 1
    except (
        BuildIdentityError,
        CandidateWriteSmokeError,
        CompatibilityCliError,
        CompatibilityGateError,
        CandidateWriteSmokeError,
        CompatibilityRehearsalError,
        CompatibilityRehearsalError,
        DataFingerprintError,
        DatabaseIdentityError,
        EvidenceCaptureError,
        FinalWindowError,
        LegacyReconciliationError,
        ProductionRollbackError,
        RuntimeHandoffError,
        StaticContractError,
        LegacyRuntimeContractError,
        SuiteIsolationError,
        ProductionRollbackError,
        RuntimeHandoffError,
    ) as error:
        print(
            json.dumps(
                {"ok": False, "error": {"code": error.code, "message": str(error)}},
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
                        "code": "COMPATIBILITY_UNEXPECTED_ERROR",
                        "message": "The compatibility command failed unexpectedly.",
                    },
                },
                ensure_ascii=False,
                separators=(",", ":"),
            ),
            file=sys.stderr,
        )
        return 1
    print(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
