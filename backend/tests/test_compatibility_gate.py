from __future__ import annotations

from pathlib import Path
from datetime import datetime, timezone
import hashlib
import json
import tempfile
import unittest


SHUTDOWN_KEYS = (
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


def _canonical(document: dict[str, object]) -> bytes:
    return json.dumps(document, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def _gate_fixture(root: Path, *, run_id: str = "a" * 32) -> dict[str, object]:
    run_root = root / f"run-{run_id}"
    run_root.mkdir()
    build_unsigned = {
        "schemaVersion": 1,
        "manifestKind": "build",
        "gitRevision": "a" * 40,
        "dirty": False,
        "sourceTreeHash": "1" * 64,
        "sourceEntries": [],
        "buildArtifactHash": "2" * 64,
        "pythonArtifacts": [],
        "frontendArtifacts": [],
        "resolvedComposeSha256": "3" * 64,
        "imageDigests": [],
    }
    build_id = hashlib.sha256(_canonical(build_unsigned)).hexdigest()
    build = root / f"frozen-build-identity-{build_id}.json"
    build.write_bytes(
        _canonical(
            {
                "schemaVersion": 1,
                "manifestKind": "build",
                "buildId": build_id,
                **{
                    key: value
                    for key, value in build_unsigned.items()
                    if key not in {"schemaVersion", "manifestKind"}
                },
                "generatedAt": "2026-08-15T00:00:00Z",
            }
        )
    )
    build_sha = hashlib.sha256(build.read_bytes()).hexdigest()
    database = root / "database-identity.json"
    database_subject = root / "database-subject.db"
    database_subject.write_bytes(b"database-subject")
    repository_root = Path(__file__).resolve().parents[2]
    origin = repository_root / "data" / "compatibility" / "runtime" / "p0-origin-receipt-v1.json"
    origin_document = json.loads(origin.read_text(encoding="utf-8"))
    origin_sha = hashlib.sha256(origin.read_bytes()).hexdigest()
    lineage_id = origin_document["databaseLineageId"]
    database_unsigned = {
        "schemaVersion": 1,
        "manifestKind": "database-evidence-identity",
        "databaseLineageId": origin_document["databaseLineageId"],
        "subjectDatabaseId": "e" * 64,
        "subjectKind": "live",
        "databasePath": str(database_subject.resolve()),
        "resolvedPathHash": "4" * 64,
        "platformFileIdentity": {
            "platform": "posix",
            "device": "0" * 16,
            "inode": "1" * 16,
        },
        "parentBackupId": origin_document["backupId"],
        "parentManifestSha256": origin_document["manifestSha256"],
        "parentDatabaseIdentityManifestPath": None,
        "parentSubjectDatabaseId": None,
        "parentIdentityManifestFileSha256": None,
        "originReceiptPath": str(origin.resolve()),
        "originReceiptFileSha256": origin_sha,
        "originReceiptSha256": origin_document["receiptSha256"],
        "createdAt": "2026-08-15T00:00:00Z",
    }
    database_identity_sha = hashlib.sha256(_canonical(database_unsigned)).hexdigest()
    database.write_bytes(
        _canonical({**database_unsigned, "identityManifestSha256": database_identity_sha})
    )
    database_sha = hashlib.sha256(database.read_bytes()).hexdigest()
    owner = root / "production-owner.json"
    owner.write_bytes(_canonical({"ownerState": "node_quiesced", "version": 7}))
    cutover_backup = run_root / "cutover.sqlite3"
    cutover_backup.write_bytes(b"cutover-backup")
    cutover_backup_sha = hashlib.sha256(cutover_backup.read_bytes()).hexdigest()
    cutover_manifest = run_root / "cutover-manifest.json"
    cutover_manifest.write_bytes(_canonical({"backup": cutover_backup.name}))
    cutover_manifest_sha = hashlib.sha256(cutover_manifest.read_bytes()).hexdigest()
    run_manifest = run_root / "evidence-run-manifest-v1.json"
    run_unsigned = {
        "schemaVersion": 1,
        "manifestKind": "evidence-run",
        "runId": run_id,
        "phase": "final",
        "runDirectory": str(run_root.resolve()),
        "buildIdentityManifestPath": str(build.resolve()),
        "buildIdentityManifestSha256": build_sha,
        "databaseIdentityManifestPath": str(database.resolve()),
        "databaseIdentityManifestSha256": database_sha,
        "originReceiptPath": str(origin.resolve()),
        "originReceiptFileSha256": origin_sha,
        "expectedKeys": list(SHUTDOWN_KEYS),
        "createdAt": "2026-08-15T00:00:00Z",
    }
    run_manifest.write_bytes(
        _canonical(
            {
                **run_unsigned,
                "runManifestSha256": hashlib.sha256(_canonical(run_unsigned)).hexdigest(),
            }
        )
    )
    run_sha = hashlib.sha256(run_manifest.read_bytes()).hexdigest()
    startup = run_root / "production-startup-snapshot.json"
    startup.write_bytes(
        _canonical(
            {
                "schemaVersion": 1,
                "manifestKind": "production-startup",
                "runId": run_id,
                "buildIdentityManifestPath": str(build.resolve()),
                "buildIdentityManifestSha256": build_sha,
                "buildId": build_id,
                "databaseIdentityManifestPath": str(database.resolve()),
                "databaseIdentityManifestSha256": database_sha,
                "databaseLineageId": lineage_id,
                "liveSubjectDatabaseId": "e" * 64,
                "originReceiptPath": str(origin.resolve()),
                "originReceiptFileSha256": origin_sha,
                "runtimeNamespace": "production",
            }
        )
    )
    startup_sha = hashlib.sha256(startup.read_bytes()).hexdigest()
    lease = root / "cutover-lease.json"
    lease.write_bytes(
        _canonical(
            {
                "schemaVersion": 1,
                "leaseId": "lease-1",
                "runId": run_id,
                "ownerMarkerPath": str(owner.resolve()),
                "ownerMarkerVersion": 7,
                "runtimeNamespace": "production",
            }
        )
    )
    lease_sha = hashlib.sha256(lease.read_bytes()).hexdigest()
    quiesce_finished = "2026-08-15T01:00:00Z"
    restore_finished = "2026-08-15T01:01:00Z"
    for index, key in enumerate(SHUTDOWN_KEYS):
        started = "2026-08-15T01:02:00Z"
        finished = "2026-08-15T01:03:00Z"
        if key == "node-quiesce":
            started, finished = "2026-08-15T00:59:00Z", quiesce_finished
        elif key == "cutover-backup-restore-check":
            started, finished = "2026-08-15T01:00:30Z", restore_finished
        document = {
            "schemaVersion": 1,
            "producer": "compatibility.capture-evidence",
            "runId": run_id,
            "runManifestPath": str(run_manifest.resolve()),
            "runManifestFileSha256": run_sha,
            "evidenceKey": key,
            "phase": "final",
            "provisional": False,
            "startedAt": started,
            "finishedAt": finished,
            "exitCode": 0,
            "totals": 1,
            "failures": 0,
            "skips": 0,
            "buildIdentityManifestPath": str(build.resolve()),
            "buildIdentityManifestSha256": build_sha,
            "buildId": build_id,
            "databaseIdentityManifestPath": str(database.resolve()),
            "databaseIdentityManifestSha256": database_sha,
                "databaseLineageId": lineage_id,
            "subjectDatabaseId": "e" * 64,
            "subjectKind": "live",
            "originReceiptPath": str(origin.resolve()),
            "originReceiptFileSha256": origin_sha,
            "startupSnapshotPath": str(startup.resolve()),
            "startupSnapshotSha256": startup_sha,
            "cutoverLeasePath": str(lease.resolve()),
            "cutoverLeaseSha256": lease_sha,
        }
        if key == "cutover-backup-create":
            document["artifacts"] = [
                {
                    "name": "cutoverBackup",
                    "path": str(cutover_backup.resolve()),
                    "sha256": cutover_backup_sha,
                },
                {
                    "name": "cutoverManifest",
                    "path": str(cutover_manifest.resolve()),
                    "sha256": cutover_manifest_sha,
                },
            ]
        record_hash = hashlib.sha256(_canonical(document)).hexdigest()
        (run_root / f"{index:02d}-{key}.json").write_bytes(
            _canonical({**document, "recordSha256": record_hash})
        )
    return {
        "run_root": run_root,
        "run_id": run_id,
        "run_manifest": run_manifest,
        "run_sha": run_sha,
        "startup": startup,
        "startup_sha": startup_sha,
        "lease": lease,
        "lease_sha": lease_sha,
        "build": build,
        "build_sha": build_sha,
        "database": database,
        "database_sha": database_sha,
        "origin": origin,
        "origin_sha": origin_sha,
        "build_id": build_id,
        "owner": owner,
        "cutover_backup": cutover_backup,
        "cutover_backup_sha": cutover_backup_sha,
        "cutover_manifest": cutover_manifest,
        "cutover_manifest_sha": cutover_manifest_sha,
    }


class CompatibilityGateTests(unittest.TestCase):
    def test_legacy_runtime_schema_and_fields_remain_present(self) -> None:
        from backend.app.api.compat.static_contract import (
            LegacyRuntimeContractError,
            verify_legacy_runtime_contract,
        )
        from backend.tests.support.p4_identity import p4_identity_fixture

        root = Path(__file__).resolve().parents[2]
        with p4_identity_fixture() as fixture:
            result = verify_legacy_runtime_contract(
                repository_root=root,
                database=fixture.database_path,
            )
        self.assertTrue(result["ok"])
        self.assertEqual("20260807_03", result["alembicRevision"])
        self.assertEqual(
            {"apiKey", "ocrApiKey", "embedApiKey", "s2ApiKey"},
            set(result["legacyCredentialFields"]),
        )
        self.assertEqual(
            {
                "processing_jobs_spec_guard_insert",
                "processing_jobs_spec_guard_update",
                "document_chunks_fts_ai",
                "document_chunks_fts_ad",
                "document_chunks_fts_au",
            },
            set(result["triggerNames"]),
        )
        with p4_identity_fixture() as mutation_fixture:
            with tempfile.TemporaryDirectory(prefix="study-app-legacy-contract-") as raw:
                temp_root = Path(raw)
                for relative in (
                    "server.js",
                    "db.js",
                    "Dockerfile",
                    "docker-compose.yml",
                    "db/schema.sql",
                    "contracts/legacy-api-v1.json",
                    "contracts/legacy_route_inventory.json",
                    "backend/app/providers/credentials/mappings.py",
                ):
                    target = temp_root / relative
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes((root / relative).read_bytes())
                (temp_root / "db.js").write_text(
                    (temp_root / "db.js").read_text(encoding="utf-8").replace(
                        "explainer", "removed"
                    ),
                    encoding="utf-8",
                )
                with self.assertRaises(LegacyRuntimeContractError) as missing:
                    verify_legacy_runtime_contract(
                        repository_root=temp_root,
                        database=mutation_fixture.database_path,
                    )
                self.assertEqual("LEGACY_RUNTIME_FIELD_MISSING", missing.exception.code)

    def test_canonical_domain_enums_remain_exact(self) -> None:
        from backend.app.api.compat.static_contract import canonical_domain_enums

        self.assertEqual(
            {
                "sourceMode": ("native", "ocr"),
                "sourceDocumentStatus": (
                    "queued",
                    "running",
                    "ready",
                    "failed",
                    "stale",
                    "cancelled",
                ),
                "generatedArtifactStatus": (
                    "queued",
                    "running",
                    "ready",
                    "failed",
                    "stale",
                    "cancelled",
                ),
                "artifactKind": (
                    "explainer",
                    "translation",
                    "summary",
                    "outline",
                    "study_card",
                    "classification",
                    "metadata",
                ),
                "processingJobType": (
                    "source_materialize",
                    "ocr",
                    "explain",
                    "translate",
                    "embed",
                    "obsidian_export",
                    "obsidian_sync",
                ),
                "processingJobStatus": (
                    "queued",
                    "running",
                    "succeeded",
                    "failed",
                    "cancelled",
                ),
                "credentialKind": (
                    "llm",
                    "ocr",
                    "embedding",
                    "semantic_scholar",
                ),
            },
            canonical_domain_enums(),
        )

    def test_static_runbook_is_state_neutral_and_preserves_deletion_boundary(
        self,
    ) -> None:
        from backend.app.api.compat.static_contract import (
            StaticContractError,
            verify_static_runbook,
        )

        root = Path(__file__).resolve().parents[2]
        readme = root / "README.md"
        database_doc = root / "docs" / "DATABASE.md"
        result = verify_static_runbook(readme=readme, database_doc=database_doc)
        self.assertEqual(
            root / "data" / "compatibility" / "runtime" / "production-owner.json",
            result["runtimeOwnerMarker"],
        )
        with tempfile.TemporaryDirectory(prefix="study-app-static-runbook-") as raw:
            temp = Path(raw)
            stateful = temp / "README.md"
            stateful.write_text(
                readme.read_text(encoding="utf-8")
                + "\nCURRENT_PRODUCTION_OWNER=python_active\n",
                encoding="utf-8",
            )
            with self.assertRaises(StaticContractError) as hardcoded:
                verify_static_runbook(readme=stateful, database_doc=database_doc)
            self.assertEqual("STATIC_RUNBOOK_STATEFUL", hardcoded.exception.code)

            incomplete = temp / "DATABASE.md"
            incomplete.write_text(
                database_doc.read_text(encoding="utf-8").replace(
                    "finalize_legacy_migration",
                    "removed-finalization-boundary",
                ),
                encoding="utf-8",
            )
            with self.assertRaises(StaticContractError) as missing:
                verify_static_runbook(readme=readme, database_doc=incomplete)
            self.assertEqual(
                "STATIC_RUNBOOK_DELETION_BOUNDARY_MISSING",
                missing.exception.code,
            )

    def test_production_profile_has_no_node_runtime_and_keeps_frozen_rollback(
        self,
    ) -> None:
        import yaml

        root = Path(__file__).resolve().parents[2]
        document = yaml.safe_load(
            (root / "docker-compose.yml").read_text(encoding="utf-8")
        )
        services = document["services"]
        default_services = {
            name for name, service in services.items() if not service.get("profiles")
        }

        self.assertEqual({"api", "worker", "scheduler", "mcp"}, default_services)
        for name in default_services:
            service = services[name]
            self.assertEqual("python-production", service["build"]["target"])
            command = " ".join(service["command"]).lower()
            self.assertNotIn("node", command)
            self.assertNotIn("server.js", command)

        frozen_node = services["frozen-node"]
        self.assertEqual(["rollback"], frozen_node["profiles"])
        self.assertEqual("frozen-node", frozen_node["build"]["target"])
        self.assertEqual(["node", "server.js"], frozen_node["command"])

    def test_shutdown_gate_names_every_missing_evidence(self) -> None:
        from backend.app.api.compat.gates import CompatibilityGateError, evaluate_gate

        with tempfile.TemporaryDirectory(prefix="study-app-p6-empty-gate-") as raw:
            with self.assertRaises(CompatibilityGateError) as caught:
                evaluate_gate(Path(raw), phase="shutdown")

        self.assertEqual("COMPATIBILITY_EVIDENCE_MISSING", caught.exception.code)
        self.assertEqual(SHUTDOWN_KEYS, caught.exception.missing_keys)

    def test_shutdown_gate_rejects_pre_quiesce_or_pre_cutover_suite_records_and_duplicate_keys(self) -> None:
        from backend.app.api.compat.gates import CompatibilityGateError, evaluate_gate

        with tempfile.TemporaryDirectory(prefix="study-app-p6-gate-topology-") as raw:
            fixture = _gate_fixture(Path(raw))
            run_root = fixture["run_root"]
            assert isinstance(run_root, Path)
            original = next(run_root.glob("*backend-suite.json"))
            duplicate = run_root / "duplicate-backend-suite.json"
            duplicate.write_bytes(original.read_bytes())
            with self.assertRaises(CompatibilityGateError) as repeated:
                evaluate_gate(run_root, phase="shutdown")
            self.assertEqual("COMPATIBILITY_EVIDENCE_DUPLICATE", repeated.exception.code)
            duplicate.unlink()

            document = json.loads(original.read_text(encoding="utf-8"))
            unsigned = dict(document)
            unsigned.pop("recordSha256")
            unsigned["startedAt"] = "2026-08-15T00:58:00Z"
            original.write_bytes(
                _canonical({**unsigned, "recordSha256": hashlib.sha256(_canonical(unsigned)).hexdigest()})
            )
            with self.assertRaises(CompatibilityGateError) as early:
                evaluate_gate(run_root, phase="shutdown")
            self.assertEqual("COMPATIBILITY_EVIDENCE_TOPOLOGY_INVALID", early.exception.code)

    def test_gate_rejects_cross_run_or_copied_capture_records(self) -> None:
        from backend.app.api.compat.gates import CompatibilityGateError, evaluate_gate

        with tempfile.TemporaryDirectory(prefix="study-app-p6-gate-copy-") as raw:
            fixture = _gate_fixture(Path(raw))
            run_root = fixture["run_root"]
            assert isinstance(run_root, Path)
            copied = next(run_root.glob("*node-suite.json"))
            document = json.loads(copied.read_text(encoding="utf-8"))
            unsigned = dict(document)
            unsigned.pop("recordSha256")
            unsigned["runId"] = "f" * 32
            copied.write_bytes(
                _canonical({**unsigned, "recordSha256": hashlib.sha256(_canonical(unsigned)).hexdigest()})
            )
            with self.assertRaises(CompatibilityGateError) as caught:
                evaluate_gate(run_root, phase="shutdown")
            self.assertEqual("COMPATIBILITY_EVIDENCE_CROSS_RUN", caught.exception.code)

    def test_gate_revalidates_referenced_run_identity_manifests(self) -> None:
        from backend.app.api.compat.gates import CompatibilityGateError, evaluate_gate

        with tempfile.TemporaryDirectory(prefix="study-app-p6-gate-manifest-") as raw:
            fixture = _gate_fixture(Path(raw))
            build = fixture["build"]
            assert isinstance(build, Path)
            build.write_bytes(_canonical({"kind": "tampered-build"}))

            with self.assertRaises(CompatibilityGateError) as caught:
                evaluate_gate(
                    fixture["run_root"],
                    phase="shutdown",
                    final_evidence_run_manifest=fixture["run_manifest"],
                    expected_final_evidence_run_manifest_sha256=fixture["run_sha"],
                    startup_snapshot=fixture["startup"],
                    expected_startup_snapshot_sha256=fixture["startup_sha"],
                    cutover_lease=fixture["lease"],
                )

            self.assertEqual("COMPATIBILITY_IDENTITY_MISMATCH", caught.exception.code)

    def test_authorization_binds_exact_final_evidence_run(self) -> None:
        from backend.app.api.compat.gates import CompatibilityGateError, evaluate_gate

        with tempfile.TemporaryDirectory(prefix="study-app-p6-gate-authorization-") as raw:
            fixture = _gate_fixture(Path(raw))
            arguments = {
                "evidence_directory": fixture["run_root"],
                "phase": "shutdown",
                "final_evidence_run_manifest": fixture["run_manifest"],
                "expected_final_evidence_run_manifest_sha256": fixture["run_sha"],
                "startup_snapshot": fixture["startup"],
                "expected_startup_snapshot_sha256": fixture["startup_sha"],
                "cutover_lease": fixture["lease"],
                "authorization_output": fixture["run_root"] / "promotion-authorization.json",
                "authorization_ttl_seconds": 900,
                "clock": lambda: datetime(2026, 8, 15, 1, 4, tzinfo=timezone.utc),
            }
            result = evaluate_gate(**arguments)
            self.assertTrue(result["nodeShutdownAllowed"])
            authorization_path = result["authorizationPath"]
            self.assertIsInstance(authorization_path, str)
            document = json.loads(Path(authorization_path).read_text(encoding="utf-8"))
            self.assertEqual(fixture["run_id"], document["runId"])
            self.assertEqual(fixture["run_sha"], document["finalEvidenceRunManifestSha256"])
            self.assertEqual(fixture["startup_sha"], document["startupSnapshotSha256"])
            self.assertEqual(fixture["lease_sha"], document["cutoverLeaseSha256"])
            self.assertEqual(fixture["build"].resolve(), Path(document["buildIdentityManifestPath"]))
            self.assertEqual(fixture["build_sha"], document["buildIdentityManifestSha256"])
            self.assertEqual(fixture["build_id"], document["buildId"])
            self.assertEqual(
                fixture["database"].resolve(),
                Path(document["databaseIdentityManifestPath"]),
            )
            self.assertEqual(
                fixture["database_sha"],
                document["databaseIdentityManifestSha256"],
            )
            self.assertEqual(
                json.loads(fixture["origin"].read_text(encoding="utf-8"))["databaseLineageId"],
                document["databaseLineageId"],
            )
            self.assertEqual("e" * 64, document["liveSubjectDatabaseId"])
            self.assertEqual(fixture["origin"].resolve(), Path(document["originReceiptPath"]))
            self.assertEqual(fixture["origin_sha"], document["originReceiptFileSha256"])
            self.assertEqual(
                fixture["cutover_backup"].resolve(),
                Path(document["cutoverBackupPath"]),
            )
            self.assertEqual(fixture["cutover_backup_sha"], document["cutoverBackupSha256"])
            self.assertEqual(
                fixture["cutover_manifest"].resolve(),
                Path(document["cutoverManifestPath"]),
            )
            self.assertEqual(
                fixture["cutover_manifest_sha"],
                document["cutoverManifestSha256"],
            )
            self.assertEqual(fixture["owner"].resolve(), Path(document["nodeOwnerMarkerPath"]))
            self.assertEqual(7, document["nodeOwnerMarkerVersion"])
            self.assertEqual("production", document["runtimeNamespace"])
            self.assertEqual(["api", "worker", "scheduler", "mcp"], document["roles"])
            self.assertRegex(document["authorizationId"], r"^[0-9a-f]{32}$")
            self.assertRegex(document["nodeZeroResourceEvidenceSha256"], r"^[0-9a-f]{64}$")

            wrong = dict(arguments)
            wrong["authorization_output"] = fixture["run_root"] / "wrong-authorization.json"
            wrong["expected_startup_snapshot_sha256"] = "0" * 64
            with self.assertRaises(CompatibilityGateError) as mismatch:
                evaluate_gate(**wrong)
            self.assertEqual("COMPATIBILITY_IDENTITY_MISMATCH", mismatch.exception.code)


if __name__ == "__main__":
    unittest.main()
