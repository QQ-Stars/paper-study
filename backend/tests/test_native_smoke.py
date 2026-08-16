from __future__ import annotations

import asyncio
from contextlib import closing
import os
from pathlib import Path
from types import SimpleNamespace
import sqlite3
import tempfile
from unittest.mock import patch
import unittest

from backend.app.api.compat.build_identity import load_build_identity_manifest
from backend.app.api.compat.database_identity import DatabaseEvidenceIdentityService
from backend.app.application.compatibility_rehearsal import (
    RECOVERY_SMOKE_EVENTS,
    CompatibilitySmokeRequest,
)
from backend.app.application.production_rollback import ROLLBACK_TAIL_EVENTS
from backend.app.application.production_candidate import (
    CandidateSmokeRequest,
    CandidateWriteSmokeService,
)
from backend.app.infrastructure.database_backup import (
    create_verified_backup,
    restore_backup_for_validation,
)
from backend.tests.support.p4_identity import p4_identity_fixture
from backend.tests.test_production_candidate_e2e import _write_build_identity


class NativeSmokeRunnerTests(unittest.TestCase):
    def test_candidate_runner_exercises_isolated_database_with_fixed_provider(self) -> None:
        from backend.app.providers.native_smoke import NativeCandidateWriteSmokeRunner

        with p4_identity_fixture() as fixture:
            live_identity_path = fixture.root / "live-database-identity-v1.json"
            live_identity = (
                DatabaseEvidenceIdentityService().create_live_database_identity(
                    database=fixture.database_path,
                    p0_origin_receipt=fixture.receipt_path,
                    expected_p0_origin_receipt_sha256=fixture.receipt_file_sha256,
                    origin_backup=fixture.origin_backup_path,
                    origin_manifest=fixture.origin_manifest_path,
                    output=live_identity_path,
                )
            )
            backup = create_verified_backup(
                fixture.database_path,
                fixture.root / "candidate-backup",
                label="native-smoke",
            )
            restored = restore_backup_for_validation(
                backup.backup_path,
                backup.manifest_path,
                fixture.root / "candidate-restore",
            )
            assert restored.restored_path is not None
            descendant_path = fixture.root / "write-smoke-identity.json"
            descendant = (
                DatabaseEvidenceIdentityService().create_descendant_database_identity(
                    database=restored.restored_path,
                    subject_kind="write_smoke",
                    parent_database_identity_manifest=live_identity_path,
                    parent_backup=backup.backup_path,
                    parent_manifest=backup.manifest_path,
                    output=descendant_path,
                )
            )
            build_path = fixture.root / "frozen-build.json"
            build_path.write_text("{}", encoding="utf-8")
            build_id = "a" * 64
            runner = NativeCandidateWriteSmokeRunner(
                configuration=SimpleNamespace(
                    live_database_path=fixture.database_path.resolve(),
                ),
                build_identity=SimpleNamespace(
                    manifest_path=build_path.resolve(),
                    build_id=build_id,
                ),
            )

            observation = asyncio.run(
                runner.run(
                    CandidateSmokeRequest(
                        database_path=restored.restored_path,
                        database_identity_manifest_path=descendant.manifest_path,
                        build_id=build_id,
                        runtime_namespace="p6-native-smoke-test",
                    )
                )
            )

            self.assertEqual(("api", "worker", "scheduler"), observation.roles)
            self.assertTrue(observation.stopped)
            self.assertEqual(1, observation.fake_provider_calls)
            self.assertEqual(0, observation.real_provider_calls)
            self.assertEqual(0, observation.real_network_calls)
            self.assertEqual(0, observation.live_path_access_count)
            self.assertEqual(0, observation.user_pdf_access_count)
            self.assertEqual(
                {"document_sources", "processing_jobs", "processing_job_events"},
                {mutation.table for mutation in observation.mutations},
            )
            with closing(sqlite3.connect(restored.restored_path)) as connection:
                self.assertEqual(
                    1,
                    connection.execute(
                        "SELECT count(*) FROM document_sources WHERE id=?",
                        (observation.source_document_id,),
                    ).fetchone()[0],
                )
                self.assertEqual(
                    1,
                    connection.execute(
                        "SELECT count(*) FROM processing_jobs WHERE id=?",
                        (observation.job_id,),
                    ).fetchone()[0],
                )
            self.assertEqual(
                live_identity.database_lineage_id,
                descendant.database_lineage_id,
            )

    def test_candidate_runner_rejects_live_database_and_wrong_build(self) -> None:
        from backend.app.providers.native_smoke import NativeCandidateWriteSmokeRunner

        with tempfile.TemporaryDirectory(prefix="study-app-native-smoke-reject-") as raw:
            root = Path(raw)
            database = root / "app.db"
            database.write_bytes(b"sqlite")
            build = root / "frozen-build.json"
            build.write_text("{}", encoding="utf-8")
            runner = NativeCandidateWriteSmokeRunner(
                configuration=SimpleNamespace(live_database_path=database.resolve()),
                build_identity=SimpleNamespace(
                    manifest_path=build.resolve(),
                    build_id="a" * 64,
                ),
            )
            request = CandidateSmokeRequest(
                database_path=database,
                database_identity_manifest_path=root / "identity.json",
                build_id="b" * 64,
                runtime_namespace="p6-native-smoke-reject",
            )

            with self.assertRaisesRegex(Exception, "isolated descendant"):
                asyncio.run(runner.run(request))

    def test_candidate_service_accepts_native_explained_write_observation(self) -> None:
        from backend.app.providers.native_smoke import NativeCandidateWriteSmokeRunner

        with p4_identity_fixture() as fixture:
            evidence = fixture.root / "native-candidate-evidence"
            evidence.mkdir()
            live_identity_path = fixture.root / "live-database-identity-v1.json"
            DatabaseEvidenceIdentityService().create_live_database_identity(
                database=fixture.database_path,
                p0_origin_receipt=fixture.receipt_path,
                expected_p0_origin_receipt_sha256=fixture.receipt_file_sha256,
                origin_backup=fixture.origin_backup_path,
                origin_manifest=fixture.origin_manifest_path,
                output=live_identity_path,
            )
            backup = create_verified_backup(
                fixture.database_path,
                evidence / "backups",
                label="native-candidate-service",
            )
            build_path = _write_build_identity(fixture.root / "build-identities")
            build = load_build_identity_manifest(build_path)
            runner = NativeCandidateWriteSmokeRunner(
                configuration=SimpleNamespace(
                    live_database_path=fixture.database_path.resolve(),
                ),
                build_identity=build,
                state_directory=fixture.root / "native-smoke-state",
            )

            result = asyncio.run(
                CandidateWriteSmokeService().run(
                    backup=backup.backup_path,
                    manifest=backup.manifest_path,
                    restore_root=evidence / "write-smoke-descendants",
                    build_identity_manifest=build_path,
                    parent_database_identity_manifest=live_identity_path,
                    descendant_database_identity_output=(
                        evidence / "write-smoke-database-identity-v1.json"
                    ),
                    evidence_mode="provisional",
                    evidence_dir=evidence,
                    runner=runner,
                )
            )

            self.assertTrue(result.delta_ledger_path.is_file())
            self.assertNotEqual(
                fixture.database_path.resolve(),
                result.restored_database_path,
            )

    def test_compatibility_runner_uses_frozen_node_and_releases_python_roles(self) -> None:
        from backend.app.providers import native_smoke
        from backend.app.providers.native_smoke import NativeCompatibilitySmokeRunner

        class _Handle:
            pass

        class _Operations:
            def __init__(self) -> None:
                self.events: list[str] = []
                self.handle = _Handle()

            def start_frozen_node(self, rollback_map: object) -> object:
                self.events.append("start-node")
                self.rollback_map = rollback_map
                return self.handle

            def smoke_legacy(self, handle: object) -> dict[str, object]:
                self.assert_handle(handle)
                self.events.append("smoke-node")
                return {"ok": True}

            def stop_frozen_node(self, handle: object) -> None:
                self.assert_handle(handle)
                self.events.append("stop-node")

            def assert_handle(self, handle: object) -> None:
                if handle is not self.handle:
                    raise AssertionError("unexpected frozen Node handle")

        with p4_identity_fixture() as fixture:
            root = fixture.root
            live = fixture.database_path
            live_identity_path = root / "live-database-identity-v1.json"
            DatabaseEvidenceIdentityService().create_live_database_identity(
                database=live,
                p0_origin_receipt=fixture.receipt_path,
                expected_p0_origin_receipt_sha256=fixture.receipt_file_sha256,
                origin_backup=fixture.origin_backup_path,
                origin_manifest=fixture.origin_manifest_path,
                output=live_identity_path,
            )
            backup = create_verified_backup(
                live,
                root / "compatibility-backup",
                label="native-compatibility-smoke",
            )
            restored = restore_backup_for_validation(
                backup.backup_path,
                backup.manifest_path,
                root / "compatibility-restore",
            )
            assert restored.restored_path is not None
            database = restored.restored_path
            build = root / "frozen-build.json"
            build.write_text("{}", encoding="utf-8")
            identity = root / "descendant-identity.json"
            descendant = DatabaseEvidenceIdentityService().create_descendant_database_identity(
                database=database,
                subject_kind="write_smoke",
                parent_database_identity_manifest=live_identity_path,
                parent_backup=backup.backup_path,
                parent_manifest=backup.manifest_path,
                output=identity,
            )
            state = root / "state"
            state.mkdir()
            rollback_map = {
                "deploymentKind": "native-windows",
                "host": "127.0.0.1",
                "ports": {"api": 49152},
                "databasePath": str(live.resolve()),
            }
            operations = _Operations()
            runner = NativeCompatibilitySmokeRunner(
                configuration=SimpleNamespace(live_database_path=live.resolve()),
                build_identity=SimpleNamespace(
                    manifest_path=build.resolve(),
                    build_id="a" * 64,
                ),
                state_directory=state,
                operations_factory=lambda **_kwargs: operations,
                rollback_map=rollback_map,
            )
            common = dict(
                database_path=database.resolve(),
                build_identity_manifest_path=build.resolve(),
                build_id="a" * 64,
                database_identity_manifest_path=identity.resolve(),
                database_lineage_id=descendant.database_lineage_id,
                subject_database_id=descendant.subject_database_id,
            )

            rollback = runner.run(
                CompatibilitySmokeRequest(
                    operation="rollback-smoke",
                    profile="frozen-node",
                    **common,
                )
            )
            self.assertEqual(ROLLBACK_TAIL_EVENTS, rollback.events)
            self.assertEqual(
                ["start-node", "smoke-node", "stop-node"],
                operations.events,
            )
            self.assertEqual(str(database.resolve()), operations.rollback_map["databasePath"])
            self.assertNotEqual(49152, operations.rollback_map["ports"]["api"])

            recovery_order: list[str] = []

            class _Lease:
                def __init__(self, role: str) -> None:
                    self.role = role

                def release(self) -> None:
                    recovery_order.append(f"release-{self.role}")

            async def exercise(_application: object, _paths: object, *, on_started=None):
                recovery_order.append("api-started")
                if on_started is not None:
                    on_started()
                recovery_order.append("api-stopped")
                return 49154

            def acquire(*_args: object, **_kwargs: object) -> tuple[object, ...]:
                recovery_order.append("role-locks-acquired")
                return (_Lease("api"), _Lease("worker"), _Lease("scheduler"))

            with patch.object(
                native_smoke,
                "_exercise_python_http",
                side_effect=exercise,
            ), patch.object(
                native_smoke,
                "_acquire_candidate_roles",
                side_effect=acquire,
            ), patch.object(
                native_smoke,
                "_verify_mcp_contract",
                side_effect=lambda _database: recovery_order.append("mcp-ready"),
            ):
                recovery = runner.run(
                    CompatibilitySmokeRequest(
                        operation="recovery-smoke",
                        profile="production",
                        **common,
                    )
                )
            self.assertEqual(RECOVERY_SMOKE_EVENTS, recovery.events)
            self.assertTrue(recovery.stopped)
            self.assertEqual(
                [
                    "api-started",
                    "role-locks-acquired",
                    "mcp-ready",
                    "api-stopped",
                    "release-scheduler",
                    "release-worker",
                    "release-api",
                ],
                recovery_order,
            )
            self.assertEqual([], list(state.glob("*.json")))

    def test_factories_bind_exact_native_environment_inputs(self) -> None:
        from backend.app.providers import native_smoke

        with tempfile.TemporaryDirectory(prefix="study-app-native-smoke-factory-") as raw:
            root = Path(raw)
            spec = root / "native-runtime-v1.json"
            build = root / "frozen-build.json"
            state = root / "state"
            spec.write_text("{}", encoding="utf-8")
            build.write_text("{}", encoding="utf-8")
            state.mkdir()
            environment = {
                "STUDY_APP_NATIVE_RUNTIME_SPEC": str(spec),
                "P6_BUILD_IDENTITY_MANIFEST": str(build),
                "STUDY_APP_NATIVE_RUNTIME_STATE_DIR": str(state),
            }
            sentinel = object()

            with patch.dict(os.environ, environment, clear=False), patch.object(
                native_smoke,
                "_create_runner",
                return_value=sentinel,
            ) as create:
                self.assertIs(
                    sentinel,
                    native_smoke.create_candidate_write_smoke_runner(),
                )
                create.assert_called_once_with("candidate")
                create.reset_mock()
                self.assertIs(
                    sentinel,
                    native_smoke.create_compatibility_smoke_runner(),
                )
                create.assert_called_once_with("compatibility")


if __name__ == "__main__":
    unittest.main()
