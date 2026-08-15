from __future__ import annotations

import asyncio
import unittest
from datetime import datetime, timezone
import hashlib
from io import StringIO
import json
from pathlib import Path

from backend.tests.support.p4_identity import p4_identity_fixture


def _rollback_map(
    *,
    root: Path | None = None,
    database: Path | None = None,
) -> dict[str, object]:
    runtime_root = root or Path("C:/study-app")
    database_path = database or runtime_root / "data" / "app.db"
    return {
        "imageDigest": f"sha256:{'a' * 64}",
        "entrypointPath": str(runtime_root / "server.js"),
        "cwd": str(runtime_root),
        "host": "127.0.0.1",
        "ports": {"api": 3000},
        "databasePath": str(database_path),
        "environment": {
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
        },
    }


class ProductionRollbackTests(unittest.TestCase):
    def test_runtime_rollback_keeps_nonactive_owner_until_legacy_smoke(self) -> None:
        from backend.app.application.production_rollback import (
            ROLLBACK_TAIL_EVENTS,
            ProductionRollbackError,
            validate_frozen_node_rollback_map,
            validate_rollback_tail,
        )

        validate_frozen_node_rollback_map(_rollback_map())
        for initial_state in ("node_quiesced", "handoff_pending", "python_active"):
            expected = list(ROLLBACK_TAIL_EVENTS)
            if initial_state == "python_active":
                expected.insert(0, "owner_handoff_pending")
            validate_rollback_tail(initial_owner_state=initial_state, events=expected)

            for index in range(len(expected) - 1):
                wrong = expected.copy()
                wrong[index], wrong[index + 1] = wrong[index + 1], wrong[index]
                with self.subTest(initial_state=initial_state, index=index):
                    with self.assertRaises(ProductionRollbackError) as raised:
                        validate_rollback_tail(
                            initial_owner_state=initial_state,
                            events=wrong,
                        )
                    self.assertEqual("ROLLBACK_ORDER_INVALID", raised.exception.code)

        failed_before_smoke = list(ROLLBACK_TAIL_EVENTS[:5])
        validate_rollback_tail(
            initial_owner_state="handoff_pending",
            events=failed_before_smoke,
            completed=False,
        )
        with self.assertRaises(ProductionRollbackError) as early_active:
            validate_rollback_tail(
                initial_owner_state="handoff_pending",
                events=[*failed_before_smoke, "owner_node_active"],
                completed=False,
            )
        self.assertEqual("ROLLBACK_ORDER_INVALID", early_active.exception.code)

    def test_frozen_node_map_rejects_rollout_aliases_or_capabilities(self) -> None:
        from backend.app.application.production_rollback import (
            ProductionRollbackError,
            validate_frozen_node_rollback_map,
        )

        mutations = []
        invalid_mode = _rollback_map()
        invalid_mode["environment"] = {
            **invalid_mode["environment"],
            "API_BACKEND_MODE": "prefer_new",
        }
        mutations.append(invalid_mode)
        capability = _rollback_map()
        capability["environment"] = {
            **capability["environment"],
            "P6_PROMOTION_AUTHORIZATION": "must-not-survive",
        }
        mutations.append(capability)
        missing = _rollback_map()
        missing["environment"] = dict(missing["environment"])
        del missing["environment"]["PAPER_STUDY_MCP_MODE"]
        mutations.append(missing)

        for value in mutations:
            with self.subTest(value=value):
                with self.assertRaises(ProductionRollbackError) as raised:
                    validate_frozen_node_rollback_map(value)
                self.assertEqual("ROLLBACK_MAP_INVALID", raised.exception.code)

    def test_canonical_startup_snapshot_rejects_identity_drift_or_extra_fields(
        self,
    ) -> None:
        from backend.app.api.compat.database_identity import (
            DatabaseEvidenceIdentityService,
            canonical_json_bytes,
        )
        from backend.app.api.compat.evidence_capture import create_evidence_run
        from backend.app.application.final_window import (
            FinalWindowError,
            create_production_startup_snapshot,
            load_production_startup_snapshot,
        )
        from backend.tests.test_evidence_capture import _build_identity

        with p4_identity_fixture() as fixture:
            build = _build_identity(fixture.root)
            database = DatabaseEvidenceIdentityService().create_live_database_identity(
                database=fixture.database_path,
                p0_origin_receipt=fixture.receipt_path,
                expected_p0_origin_receipt_sha256=fixture.receipt_file_sha256,
                origin_backup=fixture.origin_backup_path,
                origin_manifest=fixture.origin_manifest_path,
                output=fixture.root / "live-database-identity.json",
            )
            evidence_root = fixture.root / "evidence"
            evidence_root.mkdir()
            run = create_evidence_run(
                evidence_root=evidence_root,
                run_id="c" * 32,
                phase="final",
                build_identity_manifest=build.manifest_path,
                database_identity_manifest=database.manifest_path,
                expected_keys=("build-identity-verify", "handoff-contract"),
            )
            rollback_map = _rollback_map(
                root=fixture.entrypoint_path.parent,
                database=fixture.database_path,
            )
            output = run.run_directory / "production-startup-snapshot-v1.json"
            snapshot = create_production_startup_snapshot(
                final_evidence_run_manifest=run.manifest_path,
                expected_final_evidence_run_manifest_sha256=run.manifest_file_sha256,
                build_identity_manifest=build.manifest_path,
                database_identity_manifest=database.manifest_path,
                frozen_node_rollback_map=rollback_map,
                output=output,
                clock=lambda: datetime(2026, 8, 15, tzinfo=timezone.utc),
            )
            self.assertEqual(output, snapshot.path)
            self.assertEqual(hashlib.sha256(output.read_bytes()).hexdigest(), snapshot.file_sha256)
            loaded = load_production_startup_snapshot(
                output,
                expected_file_sha256=snapshot.file_sha256,
            )
            self.assertEqual(run.run_id, loaded.run_id)
            self.assertEqual(build.build_id, loaded.build_id)
            self.assertEqual(database.subject_database_id, loaded.live_subject_database_id)

            tampered_document = json.loads(output.read_text(encoding="utf-8"))
            tampered_document["environmentOverride"] = "forbidden"
            tampered = run.run_directory / "tampered-startup.json"
            tampered.write_bytes(canonical_json_bytes(tampered_document))
            with self.assertRaises(FinalWindowError) as extra:
                load_production_startup_snapshot(tampered)
            self.assertEqual("STARTUP_SNAPSHOT_INVALID", extra.exception.code)

            wrong_output = run.run_directory / "wrong-identity-startup.json"
            with self.assertRaises(FinalWindowError) as wrong_identity:
                create_production_startup_snapshot(
                    final_evidence_run_manifest=run.manifest_path,
                    expected_final_evidence_run_manifest_sha256=run.manifest_file_sha256,
                    build_identity_manifest=database.manifest_path,
                    database_identity_manifest=database.manifest_path,
                    frozen_node_rollback_map=rollback_map,
                    output=wrong_output,
                )
            self.assertEqual("STARTUP_IDENTITY_MISMATCH", wrong_identity.exception.code)
            self.assertFalse(wrong_output.exists())

    def test_final_window_abort_is_token_bound_ordered_and_idempotent(self) -> None:
        from backend.app.api.compat.database_identity import DatabaseEvidenceIdentityService
        from backend.app.api.compat.evidence_capture import create_evidence_run
        from backend.app.application.final_window import (
            FinalWindowCoordinator,
            FinalWindowError,
            create_production_startup_snapshot,
        )
        from backend.tests.test_evidence_capture import _build_identity
        from backend.tests.test_runtime_ownership import (
            _Inspector,
            _api,
            _owner_arguments,
            _process,
            _snapshot,
        )

        class Operations:
            def __init__(self) -> None:
                self.events: list[str] = []

            def quiesce_node(self) -> dict[str, object]:
                self.events.append("quiesce")
                return {"zeroPidPortDatabaseHandles": True}

            def clear_authorization(self) -> None:
                self.events.append("authorization_cleared")

            def drain_python_ingress(self) -> None:
                self.events.append("python_ingress_drained")

            def drain_worker_claims(self) -> None:
                self.events.append("worker_claims_drained")

            def stop_scheduler_obsidian_mcp(self) -> None:
                self.events.append("scheduler_obsidian_mcp_stopped")

            def stop_fastapi(self) -> None:
                self.events.append("fastapi_stopped")

            def release_locks_connections(self) -> None:
                self.events.append("role_locks_connections_released")

            def start_frozen_node(self, _rollback_map: object) -> object:
                self.events.append("frozen_node_started")
                return object()

            def smoke_legacy(self, _handle: object) -> dict[str, object]:
                self.events.append("legacy_smoked")
                return {"ok": True}

        class Watchdog:
            def __init__(self) -> None:
                self.events: list[str] = []

            def start(self, _lease_path: Path, _token_path: Path) -> int:
                self.events.append("start")
                return 43210

            def stop(self, _lease_path: Path) -> None:
                self.events.append("stop")

        with p4_identity_fixture() as fixture:
            build = _build_identity(fixture.root)
            database = DatabaseEvidenceIdentityService().create_live_database_identity(
                database=fixture.database_path,
                p0_origin_receipt=fixture.receipt_path,
                expected_p0_origin_receipt_sha256=fixture.receipt_file_sha256,
                origin_backup=fixture.origin_backup_path,
                origin_manifest=fixture.origin_manifest_path,
                output=fixture.root / "live-database-identity.json",
            )
            evidence_root = fixture.root / "evidence"
            evidence_root.mkdir()
            run = create_evidence_run(
                evidence_root=evidence_root,
                run_id="d" * 32,
                phase="final",
                build_identity_manifest=build.manifest_path,
                database_identity_manifest=database.manifest_path,
                expected_keys=("node-quiesce", "handoff-contract"),
            )
            snapshot = create_production_startup_snapshot(
                final_evidence_run_manifest=run.manifest_path,
                expected_final_evidence_run_manifest_sha256=run.manifest_file_sha256,
                build_identity_manifest=build.manifest_path,
                database_identity_manifest=database.manifest_path,
                frozen_node_rollback_map=_rollback_map(
                    root=fixture.entrypoint_path.parent,
                    database=fixture.database_path,
                ),
                output=run.run_directory / "production-startup-snapshot-v1.json",
            )
            identity_type, owner_type, process_type, snapshot_type = _api()
            self.assertIs(identity_type, DatabaseEvidenceIdentityService)
            owner_marker = fixture.root / "production-owner.json"
            owner_type(
                _Inspector(_snapshot(snapshot_type, (_process(fixture, process_type),)))
            ).initialize_node_owner(
                **_owner_arguments(fixture, database.manifest_path, owner_marker)
            )
            original_owner = owner_marker.read_bytes()
            operations = Operations()
            watchdog = Watchdog()
            coordinator = FinalWindowCoordinator(
                operations=operations,
                watchdog=watchdog,
                token_factory=lambda: b"t" * 32,
                clock=lambda: datetime(2026, 8, 15, tzinfo=timezone.utc),
                coordinator_pid=12345,
            )
            lease = coordinator.begin_final_window(
                final_evidence_run_manifest=run.manifest_path,
                expected_final_evidence_run_manifest_sha256=run.manifest_file_sha256,
                startup_snapshot=snapshot.path,
                expected_startup_snapshot_sha256=snapshot.file_sha256,
                owner_marker=owner_marker,
                runtime_namespace="production",
                operator_pid=54321,
                heartbeat_timeout_seconds=120,
                lease_output=fixture.root / f"final-window-{run.run_id}.json",
                token_file_output=fixture.root / f"final-window-{run.run_id}.token",
            )
            token = lease.token_file_path.read_bytes()
            self.assertNotIn(token, lease.path.read_bytes())
            wrong_token = fixture.root / "wrong.token"
            wrong_token.write_bytes(b"x" * 32)
            with self.assertRaises(FinalWindowError) as wrong:
                coordinator.quiesce_live(
                    cutover_lease=lease.path,
                    cutover_token_file=wrong_token,
                )
            self.assertEqual("CUTOVER_TOKEN_MISMATCH", wrong.exception.code)
            self.assertEqual([], operations.events)

            coordinator.quiesce_live(
                cutover_lease=lease.path,
                cutover_token_file=lease.token_file_path,
            )
            self.assertNotEqual(original_owner, owner_marker.read_bytes())
            recovery_output = run.run_directory / "abort-recovery.json"
            recovery = coordinator.abort_cutover(
                cutover_lease=lease.path,
                cutover_token_file=lease.token_file_path,
                reason="step11_failure",
                recovery_output=recovery_output,
            )
            self.assertEqual("node_active", recovery.owner_state)
            self.assertEqual(original_owner, owner_marker.read_bytes())
            self.assertEqual(
                [
                    "quiesce",
                    "authorization_cleared",
                    "python_ingress_drained",
                    "worker_claims_drained",
                    "scheduler_obsidian_mcp_stopped",
                    "fastapi_stopped",
                    "role_locks_connections_released",
                    "frozen_node_started",
                    "legacy_smoked",
                ],
                operations.events,
            )
            before_retry = list(operations.events)
            repeated = coordinator.abort_cutover(
                cutover_lease=lease.path,
                cutover_token_file=lease.token_file_path,
                reason="step11_failure",
                recovery_output=recovery_output,
            )
            self.assertEqual(recovery.file_sha256, repeated.file_sha256)
            self.assertEqual(before_retry, operations.events)
            self.assertEqual(["start", "stop"], watchdog.events)

    def test_handoff_consumes_authorization_once_and_writes_durable_receipt(
        self,
    ) -> None:
        from backend.app.api.compat.database_identity import DatabaseEvidenceIdentityService
        from backend.app.api.compat.evidence_capture import create_evidence_run
        from backend.app.api.compat.gates import _issue_authorization
        from backend.app.application.final_window import (
            FinalWindowCoordinator,
            create_production_startup_snapshot,
        )
        from backend.app.application.runtime_handoff import (
            ProductionPromotionCoordinator,
            RuntimeHandoffError,
            load_handoff_receipt,
        )
        from backend.app.config import DatabaseSettings
        from backend.app.providers.runtime_lease import (
            RoleLeaseError,
            RoleScopedRuntimeLease,
        )
        from backend.app.runtime import ProductionRuntimeGuard
        from backend.tests.test_evidence_capture import _build_identity
        from backend.tests.test_runtime_ownership import (
            _Inspector,
            _api,
            _owner_arguments,
            _process,
            _snapshot,
        )

        class Operations:
            def __init__(self) -> None:
                self.events: list[str] = []

            def quiesce_node(self) -> dict[str, object]:
                self.events.append("quiesce")
                return {"zeroPidPortDatabaseHandles": True}

            def clear_authorization(self) -> None:
                self.events.append("authorization_cleared")

            def drain_python_ingress(self) -> None:
                self.events.append("python_ingress_drained")

            def drain_worker_claims(self) -> None:
                self.events.append("worker_claims_drained")

            def stop_scheduler_obsidian_mcp(self) -> None:
                self.events.append("scheduler_obsidian_mcp_stopped")

            def stop_fastapi(self) -> None:
                self.events.append("fastapi_stopped")

            def release_locks_connections(self) -> None:
                self.events.append("role_locks_connections_released")

            def start_frozen_node(self, _rollback_map: object) -> object:
                self.events.append("frozen_node_started")
                return object()

            def smoke_legacy(self, _handle: object) -> dict[str, object]:
                self.events.append("legacy_smoked")
                return {"ok": True}

        class Watchdog:
            def start(self, _lease_path: Path, _token_path: Path) -> int:
                return 43210

            def stop(self, _lease_path: Path) -> None:
                return None

        with p4_identity_fixture() as fixture:
            build = _build_identity(fixture.root)
            database = DatabaseEvidenceIdentityService().create_live_database_identity(
                database=fixture.database_path,
                p0_origin_receipt=fixture.receipt_path,
                expected_p0_origin_receipt_sha256=fixture.receipt_file_sha256,
                origin_backup=fixture.origin_backup_path,
                origin_manifest=fixture.origin_manifest_path,
                output=fixture.root / "live-database-identity.json",
            )
            evidence_root = fixture.root / "evidence"
            evidence_root.mkdir()
            run = create_evidence_run(
                evidence_root=evidence_root,
                run_id="e" * 32,
                phase="final",
                build_identity_manifest=build.manifest_path,
                database_identity_manifest=database.manifest_path,
                expected_keys=("node-quiesce", "handoff-contract"),
            )
            snapshot = create_production_startup_snapshot(
                final_evidence_run_manifest=run.manifest_path,
                expected_final_evidence_run_manifest_sha256=run.manifest_file_sha256,
                build_identity_manifest=build.manifest_path,
                database_identity_manifest=database.manifest_path,
                frozen_node_rollback_map=_rollback_map(
                    root=fixture.entrypoint_path.parent,
                    database=fixture.database_path,
                ),
                output=run.run_directory / "production-startup-snapshot-v1.json",
            )
            identity_type, owner_type, process_type, snapshot_type = _api()
            self.assertIs(identity_type, DatabaseEvidenceIdentityService)
            owner_marker = fixture.root / "production-owner.json"
            owner_type(
                _Inspector(_snapshot(snapshot_type, (_process(fixture, process_type),)))
            ).initialize_node_owner(
                **_owner_arguments(fixture, database.manifest_path, owner_marker)
            )
            operations = Operations()
            final_window = FinalWindowCoordinator(
                operations=operations,
                watchdog=Watchdog(),
                token_factory=lambda: b"u" * 32,
                clock=lambda: datetime(2026, 8, 15, 1, 0, tzinfo=timezone.utc),
                coordinator_pid=12345,
            )
            lease = final_window.begin_final_window(
                final_evidence_run_manifest=run.manifest_path,
                expected_final_evidence_run_manifest_sha256=run.manifest_file_sha256,
                startup_snapshot=snapshot.path,
                expected_startup_snapshot_sha256=snapshot.file_sha256,
                owner_marker=owner_marker,
                runtime_namespace="production",
                operator_pid=54321,
                heartbeat_timeout_seconds=120,
                lease_output=fixture.root / f"final-window-{run.run_id}.json",
                token_file_output=fixture.root / f"final-window-{run.run_id}.token",
            )
            final_window.quiesce_live(
                cutover_lease=lease.path,
                cutover_token_file=lease.token_file_path,
            )
            lease_sha = hashlib.sha256(lease.path.read_bytes()).hexdigest()
            authorization_path = run.run_directory / "promotion-authorization.json"
            authorization = _issue_authorization(
                run.run_directory,
                {
                    "runId": run.run_id,
                    "runManifestPath": run.manifest_path,
                    "runManifestSha256": run.manifest_file_sha256,
                    "startupSnapshotPath": snapshot.path,
                    "startupSnapshotSha256": snapshot.file_sha256,
                    "cutoverLeasePath": lease.path,
                    "cutoverLeaseSha256": lease_sha,
                },
                output=authorization_path,
                ttl_seconds=900,
                clock=lambda: datetime(2026, 8, 15, 1, 1, tzinfo=timezone.utc),
            )
            authorization_sha = str(authorization["authorizationSha256"])
            promoter = ProductionPromotionCoordinator(
                operations=operations,
                clock=lambda: datetime(2026, 8, 15, 1, 2, tzinfo=timezone.utc),
                receipt_id_factory=lambda: "f" * 32,
            )
            owner_before = owner_marker.read_bytes()
            with self.assertRaises(RuntimeHandoffError) as wrong:
                promoter.begin_handoff(
                    authorization=authorization_path,
                    expected_authorization_sha256="0" * 64,
                    cutover_lease=lease.path,
                    cutover_token_file=lease.token_file_path,
                    startup_snapshot=snapshot.path,
                    expected_startup_snapshot_sha256=snapshot.file_sha256,
                    owner_marker=owner_marker,
                )
            self.assertEqual("PROMOTION_AUTHORIZATION_INVALID", wrong.exception.code)
            self.assertEqual(owner_before, owner_marker.read_bytes())
            self.assertEqual(["quiesce"], operations.events)

            handoff = promoter.begin_handoff(
                authorization=authorization_path,
                expected_authorization_sha256=authorization_sha,
                cutover_lease=lease.path,
                cutover_token_file=lease.token_file_path,
                startup_snapshot=snapshot.path,
                expected_startup_snapshot_sha256=snapshot.file_sha256,
                owner_marker=owner_marker,
            )
            from backend.app.cli.candidate_runtime import run as run_runtime

            untouched_lease_root = fixture.root / "live-lease-root-sentinel"
            untouched_lease_root.write_text("sentinel\n", encoding="utf-8")
            runtime_stderr = StringIO()
            runtime_exit = asyncio.run(
                run_runtime(
                    ["--role", "worker"],
                    environment={
                        "API_PROCESS_ROLE": "worker",
                        "RUNTIME_ENVIRONMENT": "live",
                        "RUNTIME_NAMESPACE": "production",
                        "DB_PATH": str(fixture.database_path),
                        "DATABASE_IDENTITY_MANIFEST": str(database.manifest_path),
                        "PRODUCTION_OWNER_MARKER": str(owner_marker),
                        "RUNTIME_LEASE_DIR": str(untouched_lease_root),
                        "REQUIRED_SCHEMA_REVISION": "20260807_03",
                        "P6_PROMOTION_AUTHORIZATION": str(authorization_path),
                        "P6_PROMOTION_AUTHORIZATION_SHA256": authorization_sha,
                        "P6_FINAL_EVIDENCE_RUN_MANIFEST": str(run.manifest_path),
                        "P6_FINAL_EVIDENCE_RUN_MANIFEST_SHA256": (
                            run.manifest_file_sha256
                        ),
                        "P6_CUTOVER_LEASE": str(lease.path),
                        "P6_PRODUCTION_STARTUP_SNAPSHOT": str(snapshot.path),
                        "P6_PRODUCTION_STARTUP_SNAPSHOT_SHA256": snapshot.file_sha256,
                        "P6_BUILD_IDENTITY_MANIFEST": str(build.manifest_path),
                        "P6_BUILD_IDENTITY_MANIFEST_SHA256": "0" * 64,
                        "P6_DATABASE_IDENTITY_MANIFEST_SHA256": (
                            database.identity_manifest_file_sha256
                        ),
                    },
                    stderr=runtime_stderr,
                )
            )
            self.assertEqual(2, runtime_exit)
            self.assertEqual(
                "PRODUCTION_ADMISSION_INVALID",
                json.loads(runtime_stderr.getvalue())["error"]["code"],
            )
            self.assertEqual(
                "sentinel\n",
                untouched_lease_root.read_text(encoding="utf-8"),
            )
            lease_root = fixture.root / "production-role-leases"
            leases = RoleScopedRuntimeLease(lease_root, pid_probe=lambda _pid: True)
            with self.assertRaises(RoleLeaseError) as missing_admission:
                leases.acquire(
                    database.manifest_path,
                    environment="live",
                    runtime_namespace="production",
                    role="worker",
                    owner_id="python-worker",
                    pid=12345,
                )
            self.assertEqual(
                "PRODUCTION_ADMISSION_REQUIRED",
                missing_admission.exception.code,
            )
            self.assertFalse(lease_root.exists())
            runtime_guard = ProductionRuntimeGuard(
                clock=lambda: datetime(2026, 8, 15, 1, 3, tzinfo=timezone.utc)
            )
            worker_admission = runtime_guard.validate_pending_handoff(
                authorization=authorization_path,
                expected_authorization_sha256=authorization_sha,
                final_evidence_run_manifest=run.manifest_path,
                expected_final_evidence_run_manifest_sha256=run.manifest_file_sha256,
                cutover_lease=lease.path,
                startup_snapshot=snapshot.path,
                expected_startup_snapshot_sha256=snapshot.file_sha256,
                build_identity_manifest=build.manifest_path,
                expected_build_identity_manifest_sha256=build.manifest_file_sha256,
                database_identity_manifest=database.manifest_path,
                expected_database_identity_manifest_sha256=(
                    database.identity_manifest_file_sha256
                ),
                owner_marker=owner_marker,
                database=DatabaseSettings(fixture.database_path),
                environment="live",
                runtime_namespace="production",
                role="worker",
            )
            scheduler_admission = runtime_guard.validate_handoff(
                handoff,
                database=DatabaseSettings(fixture.database_path),
                environment="live",
                runtime_namespace="production",
                role="scheduler",
            )
            worker_lease = leases.acquire(
                database.manifest_path,
                environment="live",
                runtime_namespace="production",
                role="worker",
                owner_id="python-worker",
                pid=12345,
                production_admission=worker_admission,
            )
            scheduler_lease = leases.acquire(
                database.manifest_path,
                environment="live",
                runtime_namespace="production",
                role="scheduler",
                owner_id="python-scheduler",
                pid=12346,
                production_admission=scheduler_admission,
            )
            with self.assertRaises(RuntimeHandoffError) as replayed:
                promoter.begin_handoff(
                    authorization=authorization_path,
                    expected_authorization_sha256=authorization_sha,
                    cutover_lease=lease.path,
                    cutover_token_file=lease.token_file_path,
                    startup_snapshot=snapshot.path,
                    expected_startup_snapshot_sha256=snapshot.file_sha256,
                    owner_marker=owner_marker,
                )
            self.assertEqual("PROMOTION_AUTHORIZATION_REPLAYED", replayed.exception.code)
            receipt_path = fixture.root / "handoff-receipt-v1.json"
            receipt = promoter.commit_python_owner(
                handoff,
                smoke_evidence={
                    "ok": True,
                    "roleLocks": {
                        "worker": hashlib.sha256(
                            worker_lease.canonical_bytes
                        ).hexdigest(),
                        "scheduler": hashlib.sha256(
                            scheduler_lease.canonical_bytes
                        ).hexdigest(),
                    },
                },
                handoff_receipt_output=receipt_path,
            )
            loaded = load_handoff_receipt(
                receipt_path,
                expected_file_sha256=receipt.file_sha256,
            )
            self.assertEqual(run.run_id, loaded.run_id)
            self.assertEqual(snapshot.file_sha256, loaded.startup_snapshot_file_sha256)
            owner_document = json.loads(owner_marker.read_text(encoding="utf-8"))
            self.assertEqual("python_active", owner_document["ownerState"])
            self.assertEqual(str(receipt_path), owner_document["handoffReceiptPath"])
            self.assertEqual(receipt.file_sha256, owner_document["handoffReceiptFileSha256"])
            self.assertEqual(["quiesce"], operations.events)
            scheduler_lease.release()
            worker_lease.release()

    def test_rollback_production_resumes_after_crash_and_is_idempotent(self) -> None:
        from backend.app.api.compat.database_identity import (
            DatabaseEvidenceIdentityService,
            canonical_json_bytes,
            exclusive_write_bytes,
        )
        from backend.app.api.compat.evidence_capture import create_evidence_run
        from backend.app.api.compat.gates import _issue_authorization
        from backend.app.application.final_window import (
            FinalWindowCoordinator,
            _cas_replace,
            _load_lease,
            _self_hashed,
            _update_lease,
            create_production_startup_snapshot,
        )
        from backend.app.application.production_rollback import (
            ProductionRollbackCoordinator,
            ProductionRollbackError,
        )
        from backend.app.application.runtime_handoff import (
            _python_owner_payload,
            load_handoff_receipt,
        )
        from backend.tests.test_evidence_capture import _build_identity
        from backend.tests.test_runtime_ownership import (
            _Inspector,
            _api,
            _owner_arguments,
            _process,
            _snapshot,
        )

        class WindowOperations:
            def quiesce_node(self) -> dict[str, object]:
                return {"zeroPidPortDatabaseHandles": True}

        class Watchdog:
            def start(self, _lease_path: Path, _token_path: Path) -> int:
                return 43210

            def stop(self, _lease_path: Path) -> None:
                return None

        class RollbackOperations:
            def __init__(self) -> None:
                self.events: list[str] = []

            def clear_authorization(self) -> None:
                self.events.append("authorization_cleared")

            def drain_python_ingress(self) -> None:
                self.events.append("python_ingress_drained")

            def drain_worker_claims(self) -> None:
                self.events.append("worker_claims_drained")

            def stop_scheduler_obsidian_mcp(self) -> None:
                self.events.append("scheduler_obsidian_mcp_stopped")

            def stop_fastapi(self) -> None:
                self.events.append("fastapi_stopped")

            def release_locks_connections(self) -> None:
                self.events.append("role_locks_connections_released")

            def start_frozen_node(self, _rollback_map: object) -> object:
                self.events.append("frozen_node_started")
                return object()

            def attach_frozen_node(self, _rollback_map: object) -> object:
                self.events.append("frozen_node_attached")
                return object()

            def smoke_legacy(self, _handle: object) -> dict[str, object]:
                self.events.append("legacy_smoked")
                return {"ok": True}

        class InjectedCrash(RuntimeError):
            pass

        with p4_identity_fixture() as fixture:
            build = _build_identity(fixture.root)
            database = DatabaseEvidenceIdentityService().create_live_database_identity(
                database=fixture.database_path,
                p0_origin_receipt=fixture.receipt_path,
                expected_p0_origin_receipt_sha256=fixture.receipt_file_sha256,
                origin_backup=fixture.origin_backup_path,
                origin_manifest=fixture.origin_manifest_path,
                output=fixture.root / "live-database-identity.json",
            )
            evidence_root = fixture.root / "evidence"
            evidence_root.mkdir()
            run = create_evidence_run(
                evidence_root=evidence_root,
                run_id="1" * 32,
                phase="final",
                build_identity_manifest=build.manifest_path,
                database_identity_manifest=database.manifest_path,
                expected_keys=("node-quiesce", "handoff-contract"),
            )
            snapshot = create_production_startup_snapshot(
                final_evidence_run_manifest=run.manifest_path,
                expected_final_evidence_run_manifest_sha256=run.manifest_file_sha256,
                build_identity_manifest=build.manifest_path,
                database_identity_manifest=database.manifest_path,
                frozen_node_rollback_map=_rollback_map(
                    root=fixture.entrypoint_path.parent,
                    database=fixture.database_path,
                ),
                output=run.run_directory / "production-startup-snapshot-v1.json",
            )
            identity_type, owner_type, process_type, snapshot_type = _api()
            self.assertIs(identity_type, DatabaseEvidenceIdentityService)
            owner_marker = fixture.root / "production-owner.json"
            owner_type(
                _Inspector(_snapshot(snapshot_type, (_process(fixture, process_type),)))
            ).initialize_node_owner(
                **_owner_arguments(fixture, database.manifest_path, owner_marker)
            )
            original_owner = owner_marker.read_bytes()
            final_window = FinalWindowCoordinator(
                operations=WindowOperations(),
                watchdog=Watchdog(),
                token_factory=lambda: b"v" * 32,
                clock=lambda: datetime(2026, 8, 15, 2, 0, tzinfo=timezone.utc),
                coordinator_pid=12345,
            )
            lease = final_window.begin_final_window(
                final_evidence_run_manifest=run.manifest_path,
                expected_final_evidence_run_manifest_sha256=run.manifest_file_sha256,
                startup_snapshot=snapshot.path,
                expected_startup_snapshot_sha256=snapshot.file_sha256,
                owner_marker=owner_marker,
                runtime_namespace="production",
                operator_pid=54321,
                heartbeat_timeout_seconds=120,
                lease_output=fixture.root / f"final-window-{run.run_id}.json",
                token_file_output=fixture.root / f"final-window-{run.run_id}.token",
            )
            final_window.quiesce_live(
                cutover_lease=lease.path,
                cutover_token_file=lease.token_file_path,
            )
            lease_path, quiesced_lease_payload, lease_document = _load_lease(lease.path)
            handoff_lease_document = _update_lease(
                lease_path,
                expected_payload=quiesced_lease_payload,
                changes={"phase": "handoff_pending"},
                clock=lambda: datetime(2026, 8, 15, 2, 1, tzinfo=timezone.utc),
            )
            handoff_lease_payload = lease_path.read_bytes()
            authorization_path = run.run_directory / "promotion-authorization.json"
            authorization = _issue_authorization(
                run.run_directory,
                {
                    "runId": run.run_id,
                    "runManifestPath": run.manifest_path,
                    "runManifestSha256": run.manifest_file_sha256,
                    "startupSnapshotPath": snapshot.path,
                    "startupSnapshotSha256": snapshot.file_sha256,
                    "cutoverLeasePath": lease_path,
                    "cutoverLeaseSha256": hashlib.sha256(
                        handoff_lease_payload
                    ).hexdigest(),
                },
                output=authorization_path,
                ttl_seconds=900,
                clock=lambda: datetime(2026, 8, 15, 2, 1, tzinfo=timezone.utc),
            )
            receipt_path = fixture.root / "handoff-receipt-v1.json"
            receipt_unsigned = {
                "schemaVersion": 1,
                "receiptKind": "python-production-handoff",
                "receiptId": "2" * 32,
                "runId": run.run_id,
                "authorizationPath": str(authorization_path),
                "authorizationFileSha256": str(authorization["authorizationSha256"]),
                "cutoverLeasePath": str(lease_path),
                "cutoverLeaseFileSha256": hashlib.sha256(
                    handoff_lease_payload
                ).hexdigest(),
                "startupSnapshotPath": str(snapshot.path),
                "startupSnapshotFileSha256": snapshot.file_sha256,
                "buildIdentityManifestPath": str(build.manifest_path),
                "buildIdentityManifestSha256": build.manifest_file_sha256,
                "buildId": build.build_id,
                "databaseIdentityManifestPath": str(database.manifest_path),
                "databaseIdentityManifestSha256": database.identity_manifest_file_sha256,
                "databaseLineageId": database.database_lineage_id,
                "liveSubjectDatabaseId": database.subject_database_id,
                "originReceiptPath": str(database.origin_receipt_path),
                "originReceiptFileSha256": database.origin_receipt_file_sha256,
                "ownerMarkerPath": str(owner_marker),
                "runtimeNamespace": "production",
                "roleLocks": {"worker": "1" * 64, "scheduler": "2" * 64},
                "smokeEvidence": {
                    "ok": True,
                    "roleLocks": {"worker": "1" * 64, "scheduler": "2" * 64},
                },
                "committedAt": "2026-08-15T02:02:00Z",
            }
            receipt_payload = _self_hashed(
                receipt_unsigned,
                "handoffReceiptSha256",
            )
            exclusive_write_bytes(receipt_path, receipt_payload)
            receipt = load_handoff_receipt(receipt_path)
            python_owner = _python_owner_payload(
                snapshot=snapshot,
                lease=handoff_lease_document,
                receipt_path=receipt_path,
                receipt_file_sha256=receipt.file_sha256,
                clock=lambda: datetime(2026, 8, 15, 2, 2, tzinfo=timezone.utc),
            )
            current_owner = owner_marker.read_bytes()
            _cas_replace(owner_marker, current_owner, python_owner)
            _update_lease(
                lease_path,
                expected_payload=handoff_lease_payload,
                changes={"phase": "completed"},
                clock=lambda: datetime(2026, 8, 15, 2, 2, tzinfo=timezone.utc),
            )
            database_before = hashlib.sha256(fixture.database_path.read_bytes()).hexdigest()
            recovery_lease = fixture.root / "production-recovery-lease-v1.json"
            recovery_output = fixture.root / "production-recovery-v1.json"
            invalid_operations = RollbackOperations()
            invalid = ProductionRollbackCoordinator(operations=invalid_operations)
            with self.assertRaises(ProductionRollbackError) as wrong:
                invalid.rollback_production(
                    handoff_receipt=receipt_path,
                    expected_handoff_receipt_sha256="0" * 64,
                    startup_snapshot=snapshot.path,
                    expected_startup_snapshot_sha256=snapshot.file_sha256,
                    build_identity_manifest=build.manifest_path,
                    database_identity_manifest=database.manifest_path,
                    p0_origin_receipt=fixture.receipt_path,
                    expected_p0_origin_receipt_sha256=fixture.receipt_file_sha256,
                    owner_marker=owner_marker,
                    recovery_lease_output=recovery_lease,
                    recovery_output=recovery_output,
                )
            self.assertEqual("PRODUCTION_ROLLBACK_IDENTITY_MISMATCH", wrong.exception.code)
            self.assertEqual([], invalid_operations.events)

            first_operations = RollbackOperations()

            def crash_after(event: str) -> None:
                if event == "role_locks_connections_released":
                    raise InjectedCrash(event)

            first = ProductionRollbackCoordinator(
                operations=first_operations,
                crash_after_event=crash_after,
            )
            with self.assertRaises(InjectedCrash):
                first.rollback_production(
                    handoff_receipt=receipt_path,
                    expected_handoff_receipt_sha256=receipt.file_sha256,
                    startup_snapshot=snapshot.path,
                    expected_startup_snapshot_sha256=snapshot.file_sha256,
                    build_identity_manifest=build.manifest_path,
                    database_identity_manifest=database.manifest_path,
                    p0_origin_receipt=fixture.receipt_path,
                    expected_p0_origin_receipt_sha256=fixture.receipt_file_sha256,
                    owner_marker=owner_marker,
                    recovery_lease_output=recovery_lease,
                    recovery_output=recovery_output,
                )
            self.assertEqual(
                [
                    "authorization_cleared",
                    "python_ingress_drained",
                    "worker_claims_drained",
                    "scheduler_obsidian_mcp_stopped",
                    "fastapi_stopped",
                    "role_locks_connections_released",
                ],
                first_operations.events,
            )
            self.assertEqual("handoff_pending", json.loads(owner_marker.read_text())["ownerState"])

            second_operations = RollbackOperations()
            second = ProductionRollbackCoordinator(operations=second_operations)
            recovery = second.rollback_production(
                handoff_receipt=receipt_path,
                expected_handoff_receipt_sha256=receipt.file_sha256,
                startup_snapshot=snapshot.path,
                expected_startup_snapshot_sha256=snapshot.file_sha256,
                build_identity_manifest=build.manifest_path,
                database_identity_manifest=database.manifest_path,
                p0_origin_receipt=fixture.receipt_path,
                expected_p0_origin_receipt_sha256=fixture.receipt_file_sha256,
                owner_marker=owner_marker,
                recovery_lease_output=recovery_lease,
                recovery_output=recovery_output,
            )
            self.assertEqual(
                ["frozen_node_started", "legacy_smoked"],
                second_operations.events,
            )
            self.assertEqual(original_owner, owner_marker.read_bytes())
            self.assertEqual(database_before, hashlib.sha256(fixture.database_path.read_bytes()).hexdigest())

            retry_operations = RollbackOperations()
            repeated = ProductionRollbackCoordinator(
                operations=retry_operations
            ).rollback_production(
                handoff_receipt=receipt_path,
                expected_handoff_receipt_sha256=receipt.file_sha256,
                startup_snapshot=snapshot.path,
                expected_startup_snapshot_sha256=snapshot.file_sha256,
                build_identity_manifest=build.manifest_path,
                database_identity_manifest=database.manifest_path,
                p0_origin_receipt=fixture.receipt_path,
                expected_p0_origin_receipt_sha256=fixture.receipt_file_sha256,
                owner_marker=owner_marker,
                recovery_lease_output=recovery_lease,
                recovery_output=recovery_output,
            )
            self.assertEqual(recovery.file_sha256, repeated.file_sha256)
            self.assertEqual([], retry_operations.events)


if __name__ == "__main__":
    unittest.main()
