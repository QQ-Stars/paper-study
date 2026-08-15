from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock
import unittest

from backend.app.api.compat.database_identity import (
    DatabaseEvidenceIdentityService,
    canonical_json_bytes,
    load_database_evidence_identity_manifest,
)
from backend.app.application.production_candidate import (
    CandidateSmokeObservation,
    CandidateSmokeRequest,
    CandidateWriteMutation,
    CandidateWriteSmokeService,
)
from backend.app.application.compatibility_rehearsal import (
    CompatibilityRehearsalError,
    CompatibilitySmokeObservation,
    CompatibilitySmokeRequest,
    RECOVERY_SMOKE_EVENTS,
    ROLLBACK_TAIL_EVENTS,
    RecoverySmokeService,
    RestoreInstallRehearsalService,
    RollbackSmokeService,
)
from backend.app.config import DatabaseSettings
from backend.app.domain import SourceDocument
from backend.app.domain.processing import (
    NewProcessingJob,
    SourceMaterializeJobSpecV1,
    build_source_job_key,
    build_source_key,
    encode_job_spec_v1,
    hash_job_spec,
)
from backend.app.infrastructure.database import create_async_session_factory
from backend.app.infrastructure.database_backup import (
    create_verified_backup,
    restore_backup_for_validation,
)
from backend.app.repositories.unit_of_work import SqlAlchemyUnitOfWork
from backend.tests.support.p4_identity import p4_identity_fixture


class _FakeCandidateRunner:
    def __init__(self, live_database: Path) -> None:
        self.live_database = live_database.resolve()
        self.calls: list[CandidateSmokeRequest] = []

    async def run(self, request: CandidateSmokeRequest) -> CandidateSmokeObservation:
        self.calls.append(request)
        if request.database_path.resolve() == self.live_database:
            raise AssertionError("candidate runner received the Live database")

        now = datetime(2026, 8, 15, 8, 0, tzinfo=timezone.utc)
        source_id = "write-smoke-source-1"
        job_id = "write-smoke-job-1"
        artifact_id = "write-smoke-artifact-1"
        with sqlite3.connect(request.database_path) as connection:
            paper_row = connection.execute(
                "SELECT id FROM papers ORDER BY id LIMIT 1"
            ).fetchone()
        if paper_row is None:
            raise AssertionError("candidate fixture requires one existing Paper")
        paper_id = str(paper_row[0])
        source = SourceDocument(
            id=source_id,
            paper_id=paper_id,
            mode="native",
            status="queued",
            provider="local",
            model="pymupdf",
            pdf_sha256="a" * 64,
            options_hash="b" * 64,
            processing_version="write-smoke-v1",
            created_at=now,
            updated_at=now,
        )
        spec = SourceMaterializeJobSpecV1(
            paper_id=paper_id,
            source_document_id=source_id,
            processing_version="write-smoke-v1",
        )
        spec_json = encode_job_spec_v1(spec)
        source_key = build_source_key(
            paper_id=paper_id,
            mode="native",
            provider="local",
            model="pymupdf",
            pdf_sha256=source.pdf_sha256,
            options_hash=source.options_hash,
            processing_version=source.processing_version,
        )
        job = NewProcessingJob(
            id=job_id,
            spec=spec,
            idempotency_key=build_source_job_key(source_key, hash_job_spec(spec_json)),
            created_at=now,
        )

        session_factory = create_async_session_factory(
            DatabaseSettings(request.database_path)
        )
        engine = session_factory.kw["bind"]
        try:
            async with SqlAlchemyUnitOfWork(session_factory) as work:
                await work.sources.enqueue_with_job(
                    source,
                    job,
                    spec_json=spec_json,
                    spec_sha256=hash_job_spec(spec_json),
                )
                await work.commit()
        finally:
            await engine.dispose()

        return CandidateSmokeObservation(
            database_path=request.database_path,
            request_id="write-smoke-request-1",
            paper_id=paper_id,
            job_id=job_id,
            source_document_id=source_id,
            artifact_id=artifact_id,
            roles=("api", "worker", "scheduler"),
            loopback_bindings=(("api", "127.0.0.1", 49152),),
            endpoints=(
                "/health/ready",
                "/api/papers",
                "/api/v2/jobs",
                f"/api/v2/papers/{paper_id}/sources",
                f"/api/v2/jobs/{job_id}/events",
                "/workspace/",
                "/legacy/",
                "mcp:tools/list",
            ),
            mutations=(
                CandidateWriteMutation("document_sources", "insert", (source_id,)),
                CandidateWriteMutation("processing_jobs", "insert", (job_id,)),
                CandidateWriteMutation(
                    "processing_job_events",
                    "insert",
                    (f"{job_id}:1",),
                ),
            ),
            fake_provider_calls=1,
            real_provider_calls=0,
            real_network_calls=0,
            live_path_access_count=0,
            owner_marker_write_count=0,
            user_pdf_access_count=0,
            stopped=True,
        )


class _FakeCompatibilitySmokeRunner:
    def __init__(self) -> None:
        self.calls: list[CompatibilitySmokeRequest] = []

    def run(self, request: CompatibilitySmokeRequest) -> CompatibilitySmokeObservation:
        self.calls.append(request)
        events = (
            ROLLBACK_TAIL_EVENTS
            if request.operation == "rollback-smoke"
            else RECOVERY_SMOKE_EVENTS
        )
        return CompatibilitySmokeObservation(
            database_path=request.database_path,
            events=events,
            stopped=True,
        )


class ProductionCandidateE2ETests(unittest.IsolatedAsyncioTestCase):
    async def test_restore_install_rehearsal_isolated_and_returns_installed_identity(
        self,
    ) -> None:
        with p4_identity_fixture() as fixture:
            run_dir = fixture.root / f"run-{'3' * 32}"
            run_dir.mkdir()
            backup = create_verified_backup(
                fixture.database_path,
                run_dir / "backups",
                label="restore-install-rehearsal",
            )
            parent_identity_path = fixture.root / "live-database-identity-v1.json"
            parent_identity = DatabaseEvidenceIdentityService().create_live_database_identity(
                database=fixture.database_path,
                p0_origin_receipt=fixture.receipt_path,
                expected_p0_origin_receipt_sha256=fixture.receipt_file_sha256,
                origin_backup=fixture.origin_backup_path,
                origin_manifest=fixture.origin_manifest_path,
                output=parent_identity_path,
            )
            build_identity_path = _write_build_identity(run_dir / "build-identities")
            seed = restore_backup_for_validation(
                backup.backup_path,
                backup.manifest_path,
                run_dir / "seed-restore",
            )
            self.assertIsNotNone(seed.restored_path)
            seed_path = seed.restored_path
            assert seed_path is not None
            rehearsal_root = run_dir / "restore-install-checks"
            target = rehearsal_root / "install-target" / "app.db"
            target.parent.mkdir(parents=True)
            shutil.copyfile(seed_path, target)
            expected_target_sha256 = _file_sha256(target)
            live_sha256 = _file_sha256(fixture.database_path)
            service = RestoreInstallRehearsalService()
            live_alias = rehearsal_root / "live-parent-alias.db"
            os.link(fixture.database_path, live_alias)

            invalid_cases = (
                {
                    "target_database": target,
                    "expected_target_sha256": "0" * 64,
                    "rehearsal_root": rehearsal_root,
                    "build_identity_manifest": build_identity_path,
                    "parent_database_identity_manifest": parent_identity_path,
                },
                {
                    "target_database": seed_path,
                    "expected_target_sha256": _file_sha256(seed_path),
                    "rehearsal_root": rehearsal_root,
                    "build_identity_manifest": build_identity_path,
                    "parent_database_identity_manifest": parent_identity_path,
                },
                {
                    "target_database": fixture.database_path,
                    "expected_target_sha256": live_sha256,
                    "rehearsal_root": fixture.root,
                    "build_identity_manifest": build_identity_path,
                    "parent_database_identity_manifest": parent_identity_path,
                },
                {
                    "target_database": live_alias,
                    "expected_target_sha256": live_sha256,
                    "rehearsal_root": rehearsal_root,
                    "build_identity_manifest": build_identity_path,
                    "parent_database_identity_manifest": parent_identity_path,
                },
                {
                    "target_database": target,
                    "expected_target_sha256": expected_target_sha256,
                    "rehearsal_root": rehearsal_root,
                    "build_identity_manifest": parent_identity_path,
                    "parent_database_identity_manifest": parent_identity_path,
                },
            )
            for index, invalid in enumerate(invalid_cases):
                with self.subTest(index=index), self.assertRaises(CompatibilityRehearsalError):
                    service.run(
                        backup=backup.backup_path,
                        manifest=backup.manifest_path,
                        target_database=invalid["target_database"],
                        expected_target_sha256=invalid["expected_target_sha256"],
                        rehearsal_root=invalid["rehearsal_root"],
                        build_identity_manifest=invalid["build_identity_manifest"],
                        parent_database_identity_manifest=invalid[
                            "parent_database_identity_manifest"
                        ],
                        installed_database_identity_output=(
                            run_dir / f"invalid-{index}-identity.json"
                        ),
                        evidence_output=run_dir / f"invalid-{index}-evidence.json",
                    )
                self.assertEqual(expected_target_sha256, _file_sha256(target))
                self.assertEqual(live_sha256, _file_sha256(fixture.database_path))

            installed_identity_output = run_dir / "restore-install-database-identity-v1.json"
            evidence_output = run_dir / "restore-install-rehearsal.json"
            result = service.run(
                backup=backup.backup_path,
                manifest=backup.manifest_path,
                target_database=target,
                expected_target_sha256=expected_target_sha256,
                rehearsal_root=rehearsal_root,
                build_identity_manifest=build_identity_path,
                parent_database_identity_manifest=parent_identity_path,
                installed_database_identity_output=installed_identity_output,
                evidence_output=evidence_output,
            )

            installed = load_database_evidence_identity_manifest(installed_identity_output)
            evidence = json.loads(evidence_output.read_text(encoding="utf-8"))
            self.assertEqual(target.resolve(), result.target_database_path)
            self.assertEqual("restore_install_rehearsal", installed.subject_kind)
            self.assertEqual(
                parent_identity.subject_database_id,
                installed.parent_subject_database_id,
            )
            self.assertEqual(installed.subject_database_id, result.subject_database_id)
            self.assertEqual(_file_sha256(backup.backup_path), _file_sha256(target))
            self.assertEqual(expected_target_sha256, _file_sha256(result.recovery_database_path))
            self.assertEqual("20260807_03", evidence["alembicRevision"])
            self.assertEqual(5, evidence["triggerCount"])
            self.assertEqual(
                evidence["processingJobCount"],
                evidence["processingJobSpecCount"],
            )
            self.assertTrue(result.inventory_path.is_file())
            self.assertEqual(live_sha256, _file_sha256(fixture.database_path))

            calls_before_sha = _file_sha256(target)
            with self.assertRaises(CompatibilityRehearsalError):
                service.run(
                    backup=backup.backup_path,
                    manifest=backup.manifest_path,
                    target_database=target,
                    expected_target_sha256=calls_before_sha,
                    rehearsal_root=rehearsal_root,
                    build_identity_manifest=build_identity_path,
                    parent_database_identity_manifest=installed_identity_output,
                    installed_database_identity_output=run_dir / "descendant-parent-identity.json",
                    evidence_output=run_dir / "descendant-parent-evidence.json",
                )
            self.assertEqual(calls_before_sha, _file_sha256(target))

    async def test_rollback_and_recovery_smokes_require_exact_build_and_descendant_database_identities(
        self,
    ) -> None:
        with p4_identity_fixture() as fixture:
            run_dir = fixture.root / f"run-{'2' * 32}"
            run_dir.mkdir()
            backup = create_verified_backup(
                fixture.database_path,
                run_dir / "backups",
                label="compatibility-rehearsal",
            )
            parent_identity_path = fixture.root / "live-database-identity-v1.json"
            parent_identity = DatabaseEvidenceIdentityService().create_live_database_identity(
                database=fixture.database_path,
                p0_origin_receipt=fixture.receipt_path,
                expected_p0_origin_receipt_sha256=fixture.receipt_file_sha256,
                origin_backup=fixture.origin_backup_path,
                origin_manifest=fixture.origin_manifest_path,
                output=parent_identity_path,
            )
            restored = restore_backup_for_validation(
                backup.backup_path,
                backup.manifest_path,
                run_dir / "smoke-subject",
            )
            self.assertIsNotNone(restored.restored_path)
            database = restored.restored_path
            assert database is not None
            descendant_path = run_dir / "write-smoke-database-identity-v1.json"
            descendant = DatabaseEvidenceIdentityService().create_descendant_database_identity(
                database=database,
                subject_kind="write_smoke",
                parent_database_identity_manifest=parent_identity_path,
                parent_backup=backup.backup_path,
                parent_manifest=backup.manifest_path,
                output=descendant_path,
            )
            build_identity_path = _write_build_identity(run_dir / "build-identities")
            runner = _FakeCompatibilitySmokeRunner()

            rollback = RollbackSmokeService(runner).run(
                database=database,
                build_identity_manifest=build_identity_path,
                database_identity_manifest=descendant_path,
                rollback_profile="frozen-node",
                evidence_output=run_dir / "frozen-node-rollback.json",
            )
            recovery = RecoverySmokeService(runner).run(
                database=database,
                build_identity_manifest=build_identity_path,
                database_identity_manifest=descendant_path,
                python_profile="production",
                evidence_output=run_dir / "python-recovery.json",
            )

            self.assertEqual(2, len(runner.calls))
            self.assertEqual(parent_identity.database_lineage_id, rollback.database_lineage_id)
            self.assertEqual(descendant.subject_database_id, rollback.subject_database_id)
            self.assertEqual(descendant.subject_database_id, recovery.subject_database_id)
            self.assertEqual(ROLLBACK_TAIL_EVENTS, rollback.events)
            self.assertEqual(RECOVERY_SMOKE_EVENTS, recovery.events)
            self.assertTrue(rollback.evidence_path.is_file())
            self.assertTrue(recovery.evidence_path.is_file())

            invalid_inputs = (
                {
                    "build_identity_manifest": run_dir / "missing-build.json",
                    "database_identity_manifest": descendant_path,
                },
                {
                    "build_identity_manifest": descendant_path,
                    "database_identity_manifest": descendant_path,
                },
                {
                    "build_identity_manifest": build_identity_path,
                    "database_identity_manifest": parent_identity_path,
                },
            )
            for index, invalid in enumerate(invalid_inputs):
                calls_before = len(runner.calls)
                with self.subTest(index=index), self.assertRaises(CompatibilityRehearsalError):
                    RollbackSmokeService(runner).run(
                        database=database,
                        build_identity_manifest=invalid["build_identity_manifest"],
                        database_identity_manifest=invalid["database_identity_manifest"],
                        rollback_profile="frozen-node",
                        evidence_output=run_dir / f"invalid-{index}.json",
                    )
                self.assertEqual(calls_before, len(runner.calls))

            stale_build_directory = run_dir / "stale-build"
            stale_build_directory.mkdir()
            stale_build_path = stale_build_directory / build_identity_path.name
            stale_build_document = json.loads(build_identity_path.read_text(encoding="utf-8"))
            stale_build_document["sourceTreeHash"] = "9" * 64
            stale_build_path.write_bytes(canonical_json_bytes(stale_build_document))
            calls_before = len(runner.calls)
            with self.assertRaises(CompatibilityRehearsalError):
                RecoverySmokeService(runner).run(
                    database=database,
                    build_identity_manifest=stale_build_path,
                    database_identity_manifest=descendant_path,
                    python_profile="production",
                    evidence_output=run_dir / "stale-build-evidence.json",
                )
            self.assertEqual(calls_before, len(runner.calls))

            replacement = run_dir / "replacement-database.db"
            shutil.copyfile(database, replacement)
            os.replace(replacement, database)
            calls_before = len(runner.calls)
            with self.assertRaises(CompatibilityRehearsalError):
                RecoverySmokeService(runner).run(
                    database=database,
                    build_identity_manifest=build_identity_path,
                    database_identity_manifest=descendant_path,
                    python_profile="production",
                    evidence_output=run_dir / "platform-drift-evidence.json",
                )
            self.assertEqual(calls_before, len(runner.calls))

    async def test_candidate_write_smoke_uses_verified_descendant_and_never_live(
        self,
    ) -> None:
        with p4_identity_fixture() as fixture:
            evidence_dir = fixture.root / f"run-{'1' * 32}"
            evidence_dir.mkdir()
            backup = create_verified_backup(
                fixture.database_path,
                evidence_dir / "backups",
                label="candidate-write-smoke",
            )
            parent_identity_path = fixture.root / "live-database-identity-v1.json"
            parent_identity = DatabaseEvidenceIdentityService().create_live_database_identity(
                database=fixture.database_path,
                p0_origin_receipt=fixture.receipt_path,
                expected_p0_origin_receipt_sha256=fixture.receipt_file_sha256,
                origin_backup=fixture.origin_backup_path,
                origin_manifest=fixture.origin_manifest_path,
                output=parent_identity_path,
            )
            build_identity_path = _write_build_identity(fixture.root / "build-identities")
            descendant_output = evidence_dir / "write-smoke-database-identity-v1.json"
            runner = _FakeCandidateRunner(fixture.database_path)
            live_accesses = 0
            real_connect = sqlite3.connect

            def guarded_connect(database: object, *args: object, **kwargs: object):
                nonlocal live_accesses
                if str(fixture.database_path.resolve()).lower() in str(database).lower():
                    live_accesses += 1
                    raise AssertionError("candidate write-smoke opened the Live database")
                return real_connect(database, *args, **kwargs)

            with mock.patch.object(sqlite3, "connect", side_effect=guarded_connect):
                result = await CandidateWriteSmokeService().run(
                    backup=backup.backup_path,
                    manifest=backup.manifest_path,
                    restore_root=evidence_dir / "write-smoke-descendants",
                    build_identity_manifest=build_identity_path,
                    parent_database_identity_manifest=parent_identity_path,
                    descendant_database_identity_output=descendant_output,
                    evidence_mode="provisional",
                    evidence_dir=evidence_dir,
                    runner=runner,
                )

            self.assertEqual(0, live_accesses)
            self.assertEqual(1, len(runner.calls))
            self.assertNotEqual(fixture.database_path.resolve(), result.restored_database_path)
            self.assertEqual(descendant_output.resolve(), result.descendant_database_identity_manifest_path)
            self.assertEqual(parent_identity.database_lineage_id, result.database_lineage_id)
            self.assertEqual(parent_identity.subject_database_id, result.parent_subject_database_id)
            self.assertEqual(
                {"document_sources", "processing_jobs", "processing_job_events"},
                {
                    entry["table"]
                    for entry in json.loads(result.delta_ledger_path.read_text(encoding="utf-8"))[
                        "entries"
                    ]
                },
            )
            for path in (
                result.before_path,
                result.after_path,
                result.delta_ledger_path,
                result.descendant_database_identity_manifest_path,
            ):
                self.assertEqual(evidence_dir.resolve(), path.parent)
                self.assertTrue(path.is_file())
            self.assertEqual(
                {
                    "restoredDatabasePath": str(result.restored_database_path),
                    "descendantDatabaseIdentityManifestPath": str(descendant_output.resolve()),
                },
                {
                    key: result.to_dict()[key]
                    for key in (
                        "restoredDatabasePath",
                        "descendantDatabaseIdentityManifestPath",
                    )
                },
            )


def _write_build_identity(directory: Path) -> Path:
    directory.mkdir()
    unsigned = {
        "schemaVersion": 1,
        "manifestKind": "build",
        "gitRevision": "a" * 40,
        "dirty": True,
        "sourceTreeHash": "b" * 64,
        "sourceEntries": [
            {"path": "backend/app.py", "mode": "regular", "sha256": "c" * 64}
        ],
        "buildArtifactHash": "d" * 64,
        "pythonArtifacts": [{"name": "study_app.whl", "sha256": "e" * 64}],
        "frontendArtifacts": [{"path": "manifest.json", "sha256": "f" * 64}],
        "resolvedComposeSha256": "1" * 64,
        "imageDigests": [
            {"name": "python", "digest": f"sha256:{'2' * 64}"},
            {"name": "node", "digest": f"sha256:{'3' * 64}"},
        ],
    }
    build_id = hashlib.sha256(canonical_json_bytes(unsigned)).hexdigest()
    document = {
        "schemaVersion": 1,
        "manifestKind": "build",
        "buildId": build_id,
        **{key: value for key, value in unsigned.items() if key not in {"schemaVersion", "manifestKind"}},
        "generatedAt": "2026-08-15T08:00:00Z",
    }
    path = directory / f"frozen-build-identity-{build_id}.json"
    path.write_bytes(canonical_json_bytes(document))
    return path


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


if __name__ == "__main__":
    unittest.main()
