from __future__ import annotations

from pathlib import Path
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from backend.tests.support.p4_identity import p4_identity_fixture


def _git(root: Path, *arguments: str) -> None:
    completed = subprocess.run(
        ["git", "-C", str(root), *arguments],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if completed.returncode != 0:
        raise AssertionError(completed.stderr or completed.stdout)


def _build_identity(root: Path):
    from backend.app.api.compat.build_identity import freeze_build_identity

    repository = root / "repository"
    repository.mkdir()
    _git(repository, "init", "--quiet")
    _git(repository, "config", "user.name", "Evidence Test")
    _git(repository, "config", "user.email", "evidence@example.test")
    (repository / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
    _git(repository, "add", "--", "app.py")
    _git(repository, "commit", "--quiet", "-m", "fixture")
    bundle = root / "study_app.whl"
    bundle.write_bytes(b"wheel")
    frontend = root / "frontend"
    frontend.mkdir()
    (frontend / "index.js").write_bytes(b"frontend")
    manifest = frontend / "manifest.json"
    manifest.write_text(
        json.dumps({"index.html": {"file": "index.js"}}), encoding="utf-8"
    )
    compose = root / "compose.json"
    compose.write_text("{}", encoding="utf-8")
    identities = root / "build-identities"
    identities.mkdir()
    return freeze_build_identity(
        repository=repository,
        build_identity_directory=identities,
        python_artifacts=(bundle,),
        frontend_root=frontend,
        frontend_manifest=manifest,
        resolved_compose=compose,
        image_digests={"candidate": f"sha256:{'a' * 64}"},
    )


class EvidenceCaptureTests(unittest.TestCase):
    def test_secret_argv_guard_allows_credential_names_outside_option_flags(
        self,
    ) -> None:
        from backend.app.api.compat.evidence_capture import (
            EvidenceCaptureError,
            _reject_secret_argv,
        )

        _reject_secret_argv(
            (
                sys.executable,
                "-B",
                "-m",
                "unittest",
                "backend.tests.test_credentials",
            )
        )

        for secret_flag in (
            "--api-key",
            "--password=fixture-value",
            "--credential-file",
            "--token-value",
        ):
            with self.subTest(secret_flag=secret_flag):
                with self.assertRaises(EvidenceCaptureError) as caught:
                    _reject_secret_argv((sys.executable, secret_flag))
                self.assertEqual("EVIDENCE_ARGV_SECRET_REJECTED", caught.exception.code)

    def test_create_evidence_run_rejects_hostile_root_swap(self) -> None:
        from backend.app.api.compat.database_identity import DatabaseEvidenceIdentityService
        from backend.app.api.compat.evidence_capture import (
            EvidenceCaptureError,
            create_evidence_run,
        )

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
            run_id = "8" * 32
            run_directory = evidence_root / f"run-{run_id}"
            displaced_root = fixture.root / "evidence-displaced"
            original_mkdir = os.mkdir
            swapped = False

            def hostile_mkdir(path, mode=0o777, *, dir_fd=None):
                nonlocal swapped
                is_run = (
                    Path(path) == run_directory
                    if dir_fd is None
                    else Path(path).name == run_directory.name
                )
                if is_run and not swapped:
                    if dir_fd is None:
                        original_mkdir(path, mode)
                    else:
                        original_mkdir(path, mode, dir_fd=dir_fd)
                    swapped = True
                    evidence_root.rename(displaced_root)
                    original_mkdir(evidence_root, 0o700)
                    original_mkdir(run_directory, mode)
                    return None
                if dir_fd is None:
                    return original_mkdir(path, mode)
                return original_mkdir(path, mode, dir_fd=dir_fd)

            with mock.patch(
                "backend.app.api.compat.evidence_capture.os.mkdir",
                side_effect=hostile_mkdir,
            ):
                with self.assertRaises(EvidenceCaptureError) as caught:
                    create_evidence_run(
                        evidence_root=evidence_root,
                        run_id=run_id,
                        phase="provisional",
                        build_identity_manifest=build.manifest_path,
                        database_identity_manifest=database.manifest_path,
                        expected_keys=("build-identity-verify",),
                    )

            self.assertTrue(swapped)
            self.assertIn(
                caught.exception.code,
                {"EVIDENCE_ROOT_CHANGED", "EVIDENCE_RUN_CREATE_FAILED"},
            )

    def test_capture_binds_run_local_artifacts_isolation_and_descendant_database_identity(
        self,
    ) -> None:
        from backend.app.api.compat.database_identity import DatabaseEvidenceIdentityService
        from backend.app.api.compat.evidence_capture import capture_evidence, create_evidence_run
        from backend.app.api.compat.suite_isolation import create_suite_isolation
        from backend.app.infrastructure.database_backup import create_verified_backup

        with p4_identity_fixture() as fixture:
            build = _build_identity(fixture.root)
            identities = DatabaseEvidenceIdentityService()
            live = identities.create_live_database_identity(
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
                run_id="9" * 32,
                phase="provisional",
                build_identity_manifest=build.manifest_path,
                database_identity_manifest=live.manifest_path,
                expected_keys=("candidate-write-smoke",),
            )
            cutover = create_verified_backup(
                fixture.database_path,
                run.run_directory / "cutover-backup",
                label="capture-artifacts",
            )
            candidate = run.run_directory / "candidate.db"
            shutil.copyfile(cutover.backup_path, candidate)
            descendant = identities.create_descendant_database_identity(
                database=candidate,
                subject_kind="candidate_write_smoke",
                parent_database_identity_manifest=live.manifest_path,
                parent_backup=cutover.backup_path,
                parent_manifest=cutover.manifest_path,
                output=run.run_directory / "candidate-database-identity.json",
            )
            isolation = create_suite_isolation(
                run_manifest=run.manifest_path,
                expected_run_manifest_sha256=run.manifest_file_sha256,
                suite_key="candidate-write-smoke",
                output=run.run_directory / "candidate-write-smoke.isolation.json",
                deny_live_paths=(fixture.database_path,),
                deny_network=True,
                deny_providers=True,
            )
            explicit_artifact = run.run_directory / "explicit-evidence.json"
            returned_artifact = run.run_directory / "returned-evidence.json"
            child = (
                "import json,pathlib,sys;"
                "pathlib.Path(sys.argv[1]).write_text('explicit',encoding='utf-8');"
                "pathlib.Path(sys.argv[2]).write_text('returned',encoding='utf-8');"
                "print(json.dumps({'ok':True,'resultPath':sys.argv[2]},separators=(',',':')))"
            )
            environment = {
                "P6_PROVISIONAL_EVIDENCE_RUN_MANIFEST_PATH": str(run.manifest_path),
                "P6_PROVISIONAL_EVIDENCE_RUN_MANIFEST_SHA256": run.manifest_file_sha256,
                "P6_PROVISIONAL_EVIDENCE_RUN_ID": run.run_id,
            }
            with mock.patch.dict(os.environ, environment, clear=False):
                record = capture_evidence(
                    key="candidate-write-smoke",
                    phase="provisional",
                    result_kind="json-cli",
                    run_manifest=run.manifest_path,
                    expected_run_manifest_sha256=run.manifest_file_sha256,
                    build_identity_manifest=build.manifest_path,
                    database_identity_manifest=descendant.manifest_path,
                    isolation_manifest=isolation.manifest_path,
                    artifacts=(("explicit", explicit_artifact),),
                    artifact_from_json=("resultPath",),
                    output=run.run_directory / "candidate-write-smoke.capture.json",
                    argv=(
                        sys.executable,
                        "-B",
                        "-c",
                        child,
                        str(explicit_artifact),
                        str(returned_artifact),
                    ),
                )

            document = json.loads(record.record_path.read_text(encoding="utf-8"))
            self.assertEqual(descendant.subject_database_id, document["subjectDatabaseId"])
            self.assertEqual("candidate_write_smoke", document["subjectKind"])
            self.assertEqual(str(isolation.manifest_path), document["isolationManifestPath"])
            self.assertEqual(0, document["liveAccessCount"])
            self.assertEqual(
                {"explicit", "resultPath"},
                {artifact["name"] for artifact in document["artifacts"]},
            )
            for artifact in document["artifacts"]:
                self.assertEqual(
                    hashlib.sha256(Path(artifact["path"]).read_bytes()).hexdigest(),
                    artifact["sha256"],
                )

    def test_create_evidence_run_supports_explicit_provisional_snapshot_and_run_local_artifacts(
        self,
    ) -> None:
        from backend.app.api.compat.database_identity import DatabaseEvidenceIdentityService
        from backend.app.api.compat.evidence_capture import (
            EvidenceCaptureError,
            create_evidence_run,
        )

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
            created = create_evidence_run(
                evidence_root=evidence_root,
                run_id="a" * 32,
                phase="provisional",
                build_identity_manifest=build.manifest_path,
                database_identity_manifest=database.manifest_path,
                expected_keys=("build-identity-verify", "migration-head-ready"),
            )

            self.assertEqual(evidence_root / f"run-{'a' * 32}", created.run_directory)
            self.assertEqual(
                created.run_directory / "evidence-run-manifest-v1.json",
                created.manifest_path,
            )
            payload = created.manifest_path.read_bytes()
            document = json.loads(payload.decode("utf-8"))
            self.assertEqual("evidence-run", document["manifestKind"])
            self.assertEqual("provisional", document["phase"])
            self.assertEqual(
                hashlib.sha256(payload).hexdigest(), created.manifest_file_sha256
            )
            unsigned = dict(document)
            self_hash = unsigned.pop("runManifestSha256")
            from backend.app.api.compat.database_identity import canonical_json_bytes

            self.assertEqual(hashlib.sha256(canonical_json_bytes(unsigned)).hexdigest(), self_hash)

            with self.assertRaises(EvidenceCaptureError) as repeated:
                create_evidence_run(
                    evidence_root=evidence_root,
                    run_id="a" * 32,
                    phase="provisional",
                    build_identity_manifest=build.manifest_path,
                    database_identity_manifest=database.manifest_path,
                    expected_keys=("build-identity-verify", "migration-head-ready"),
                )
            self.assertEqual("EVIDENCE_RUN_EXISTS", repeated.exception.code)

    def test_capture_evidence_exclusive_creates_allowlisted_typed_record_and_propagates_child_exit(
        self,
    ) -> None:
        from backend.app.api.compat.database_identity import DatabaseEvidenceIdentityService
        from backend.app.api.compat.evidence_capture import (
            EvidenceChildFailure,
            capture_evidence,
            create_evidence_run,
        )

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
                run_id="b" * 32,
                phase="provisional",
                build_identity_manifest=build.manifest_path,
                database_identity_manifest=database.manifest_path,
                expected_keys=("build-identity-verify", "migration-head-ready"),
            )
            environment = {
                "P6_PROVISIONAL_EVIDENCE_RUN_MANIFEST_PATH": str(run.manifest_path),
                "P6_PROVISIONAL_EVIDENCE_RUN_MANIFEST_SHA256": run.manifest_file_sha256,
                "P6_PROVISIONAL_EVIDENCE_RUN_ID": run.run_id,
            }

            success_summary = run.run_directory / "success-summary.json"
            success_code = (
                "import json,pathlib,sys;"
                "pathlib.Path(sys.argv[1]).write_text(json.dumps({"
                "'schemaVersion':1,'manifestKind':'machine-summary','adapter':'check',"
                "'rawExit':0,'totals':1,'failures':0,'skips':0,"
                "'resultArtifactPath':None,'resultArtifactFormat':'raw-exit'}),"
                "encoding='utf-8');"
                "print(json.dumps({'ok':True},separators=(',',':')))"
            )
            with mock.patch.dict(os.environ, environment, clear=False):
                success = capture_evidence(
                    key="build-identity-verify",
                    phase="provisional",
                    result_kind="machine-summary",
                    run_manifest=run.manifest_path,
                    expected_run_manifest_sha256=run.manifest_file_sha256,
                    build_identity_manifest=build.manifest_path,
                    output=run.run_directory / "build-identity-verify.json",
                    summary_artifact=success_summary,
                    argv=(sys.executable, "-B", "-c", success_code, str(success_summary)),
                )
            self.assertEqual(0, success.exit_code)
            self.assertEqual((1, 0, 0), (success.totals, success.failures, success.skips))
            success_document = json.loads(success.record_path.read_text(encoding="utf-8"))
            self.assertEqual("compatibility.capture-evidence", success_document["producer"])
            self.assertEqual(run.run_id, success_document["runId"])
            self.assertEqual("build-identity-verify", success_document["evidenceKey"])
            self.assertEqual(0, success_document["exitCode"])
            self.assertEqual(b'{"ok":true}', success.stdout_path.read_bytes().rstrip())

            failure_summary = run.run_directory / "failure-summary.json"
            failure_code = (
                "import json,pathlib,sys;"
                "pathlib.Path(sys.argv[1]).write_text(json.dumps({"
                "'schemaVersion':1,'manifestKind':'machine-summary','adapter':'check',"
                "'rawExit':7,'totals':1,'failures':1,'skips':0,"
                "'resultArtifactPath':None,'resultArtifactFormat':'raw-exit'}),"
                "encoding='utf-8');"
                "print('OK');sys.exit(7)"
            )
            with mock.patch.dict(os.environ, environment, clear=False):
                with self.assertRaises(EvidenceChildFailure) as caught:
                    capture_evidence(
                        key="migration-head-ready",
                        phase="provisional",
                        result_kind="machine-summary",
                        run_manifest=run.manifest_path,
                        expected_run_manifest_sha256=run.manifest_file_sha256,
                        build_identity_manifest=build.manifest_path,
                        output=run.run_directory / "migration-head-ready.json",
                        summary_artifact=failure_summary,
                        argv=(sys.executable, "-B", "-c", failure_code, str(failure_summary)),
                    )
            self.assertEqual(7, caught.exception.exit_code)
            failure_document = json.loads(
                caught.exception.record_path.read_text(encoding="utf-8")
            )
            self.assertEqual(7, failure_document["exitCode"])
            self.assertEqual(1, failure_document["failures"])
            self.assertTrue((run.run_directory / "failure-seal-v1.json").is_file())

    def test_capture_evidence_rejects_duplicate_phase_identity_mismatch_and_handwritten_record(
        self,
    ) -> None:
        from backend.app.api.compat.database_identity import DatabaseEvidenceIdentityService
        from backend.app.api.compat.evidence_capture import (
            EvidenceCaptureError,
            capture_evidence,
            create_evidence_run,
        )

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
                phase="provisional",
                build_identity_manifest=build.manifest_path,
                database_identity_manifest=database.manifest_path,
                expected_keys=("build-identity-verify",),
            )
            environment = {
                "P6_PROVISIONAL_EVIDENCE_RUN_MANIFEST_PATH": str(run.manifest_path),
                "P6_PROVISIONAL_EVIDENCE_RUN_MANIFEST_SHA256": run.manifest_file_sha256,
                "P6_PROVISIONAL_EVIDENCE_RUN_ID": run.run_id,
            }
            summary = run.run_directory / "summary.json"
            child = (
                "import json,pathlib,sys;"
                "pathlib.Path(sys.argv[1]).write_text(json.dumps({"
                "'schemaVersion':1,'manifestKind':'machine-summary','adapter':'check',"
                "'rawExit':0,'totals':1,'failures':0,'skips':0,"
                "'resultArtifactPath':None,'resultArtifactFormat':'raw-exit'}),"
                "encoding='utf-8')"
            )
            arguments = dict(
                key="build-identity-verify",
                phase="provisional",
                result_kind="machine-summary",
                run_manifest=run.manifest_path,
                expected_run_manifest_sha256=run.manifest_file_sha256,
                build_identity_manifest=build.manifest_path,
                output=run.run_directory / "capture.json",
                summary_artifact=summary,
                argv=(sys.executable, "-B", "-c", child, str(summary)),
            )
            with mock.patch.dict(os.environ, environment, clear=False):
                capture_evidence(**arguments)
                with self.assertRaises(EvidenceCaptureError) as duplicate:
                    capture_evidence(
                        **{
                            **arguments,
                            "output": run.run_directory / "duplicate.json",
                            "summary_artifact": run.run_directory / "duplicate-summary.json",
                        }
                    )
            self.assertEqual("EVIDENCE_DUPLICATE_KEY", duplicate.exception.code)

            copied_root = fixture.root / "copied-build"
            copied_root.mkdir()
            copied_build = copied_root / build.manifest_path.name
            copied_build.write_bytes(build.manifest_path.read_bytes())
            with mock.patch.dict(os.environ, environment, clear=False):
                with mock.patch("backend.app.api.compat.evidence_capture.subprocess.run") as spawn:
                    with self.assertRaises(EvidenceCaptureError) as wrong_build:
                        capture_evidence(
                            **{
                                **arguments,
                                "build_identity_manifest": copied_build,
                                "output": run.run_directory / "wrong-build.json",
                                "summary_artifact": run.run_directory / "wrong-build-summary.json",
                            }
                        )
            self.assertEqual("EVIDENCE_IDENTITY_MISMATCH", wrong_build.exception.code)
            spawn.assert_not_called()

            handwritten = run.run_directory / "handwritten.json"
            handwritten.write_text('{"ok":true}', encoding="utf-8")
            from backend.app.api.compat.gates import CompatibilityGateError, evaluate_gate

            with self.assertRaises(CompatibilityGateError):
                evaluate_gate(run.run_directory, phase="shutdown")

            with mock.patch.dict(os.environ, environment, clear=False):
                with mock.patch("subprocess.run") as spawn:
                    with self.assertRaises(EvidenceCaptureError) as wrong_phase:
                        capture_evidence(
                            **{
                                **arguments,
                                "phase": "final",
                                "output": run.run_directory / "wrong-phase.json",
                                "summary_artifact": run.run_directory / "wrong-phase-summary.json",
                            }
                        )
            self.assertEqual("EVIDENCE_PHASE_MISMATCH", wrong_phase.exception.code)
            spawn.assert_not_called()

    def test_final_capture_requires_explicit_matching_lease_token_and_startup_snapshot(
        self,
    ) -> None:
        from backend.app.api.compat.database_identity import DatabaseEvidenceIdentityService
        from backend.app.api.compat.evidence_capture import (
            EvidenceCaptureError,
            capture_evidence,
            create_evidence_run,
        )

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
                expected_keys=("build-identity-verify",),
            )
            lease = fixture.root / "cutover-lease.json"
            token = fixture.root / "cutover-token.bin"
            startup = run.run_directory / "production-startup-snapshot.json"
            lease.write_text('{"schemaVersion":1,"leaseId":"lease-1"}', encoding="utf-8")
            token.write_bytes(b"owner-capability")
            startup.write_text(
                json.dumps({"schemaVersion": 1, "manifestKind": "production-startup", "runId": run.run_id}),
                encoding="utf-8",
            )
            startup_sha = hashlib.sha256(startup.read_bytes()).hexdigest()
            environment = {
                "P6_FINAL_EVIDENCE_RUN_MANIFEST_PATH": str(run.manifest_path),
                "P6_FINAL_EVIDENCE_RUN_MANIFEST_SHA256": run.manifest_file_sha256,
                "P6_FINAL_EVIDENCE_RUN_ID": run.run_id,
                "P6_FINAL_WINDOW_LEASE_PATH": str(lease.resolve()),
                "P6_FINAL_WINDOW_TOKEN_FILE": str(token.resolve()),
                "P6_PRODUCTION_STARTUP_SNAPSHOT_PATH": str(startup.resolve()),
                "P6_PRODUCTION_STARTUP_SNAPSHOT_SHA256": startup_sha,
            }
            common = dict(
                key="build-identity-verify",
                phase="final",
                result_kind="machine-summary",
                run_manifest=run.manifest_path,
                expected_run_manifest_sha256=run.manifest_file_sha256,
                build_identity_manifest=build.manifest_path,
                output=run.run_directory / "capture.json",
                summary_artifact=run.run_directory / "summary.json",
                argv=(sys.executable, "-B", "-c", "raise SystemExit(0)"),
            )
            cases = (
                {},
                {"cutover_lease": lease, "cutover_token_file": token, "startup_snapshot": startup, "expected_startup_snapshot_sha256": "0" * 64},
                {"cutover_lease": fixture.root / "missing-lease.json", "cutover_token_file": token, "startup_snapshot": startup, "expected_startup_snapshot_sha256": startup_sha},
            )
            with mock.patch.dict(os.environ, environment, clear=False):
                for extra in cases:
                    with self.subTest(extra=tuple(extra)):
                        with mock.patch("backend.app.api.compat.evidence_capture.subprocess.run") as spawn:
                            with self.assertRaises(EvidenceCaptureError):
                                capture_evidence(**common, **extra)
                            spawn.assert_not_called()

    def test_failed_final_run_is_immutable_and_fresh_run_can_retry(self) -> None:
        from backend.app.api.compat.database_identity import DatabaseEvidenceIdentityService
        from backend.app.api.compat.evidence_capture import (
            EvidenceCaptureError,
            EvidenceChildFailure,
            capture_evidence,
            create_evidence_run,
        )

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

            def make_run(run_id: str):
                run = create_evidence_run(
                    evidence_root=evidence_root,
                    run_id=run_id,
                    phase="final",
                    build_identity_manifest=build.manifest_path,
                    database_identity_manifest=database.manifest_path,
                    expected_keys=("build-identity-verify",),
                )
                lease = fixture.root / f"{run_id}-lease.json"
                token = fixture.root / f"{run_id}-token.bin"
                startup = run.run_directory / "startup.json"
                lease.write_text('{"schemaVersion":1}', encoding="utf-8")
                token.write_bytes(run_id.encode("ascii"))
                startup.write_text(json.dumps({"runId": run_id}), encoding="utf-8")
                startup_sha = hashlib.sha256(startup.read_bytes()).hexdigest()
                env = {
                    "P6_FINAL_EVIDENCE_RUN_MANIFEST_PATH": str(run.manifest_path),
                    "P6_FINAL_EVIDENCE_RUN_MANIFEST_SHA256": run.manifest_file_sha256,
                    "P6_FINAL_EVIDENCE_RUN_ID": run.run_id,
                    "P6_FINAL_WINDOW_LEASE_PATH": str(lease.resolve()),
                    "P6_FINAL_WINDOW_TOKEN_FILE": str(token.resolve()),
                    "P6_PRODUCTION_STARTUP_SNAPSHOT_PATH": str(startup.resolve()),
                    "P6_PRODUCTION_STARTUP_SNAPSHOT_SHA256": startup_sha,
                }
                return run, lease, token, startup, startup_sha, env

            run_a, lease_a, token_a, startup_a, startup_sha_a, env_a = make_run("e" * 32)
            summary_a = run_a.run_directory / "summary.json"
            failure_code = (
                "import json,pathlib,sys;"
                "pathlib.Path(sys.argv[1]).write_text(json.dumps({"
                "'schemaVersion':1,'manifestKind':'machine-summary','adapter':'check',"
                "'rawExit':7,'totals':1,'failures':1,'skips':0,"
                "'resultArtifactPath':None,'resultArtifactFormat':'raw-exit'}),encoding='utf-8');"
                "raise SystemExit(7)"
            )
            with mock.patch.dict(os.environ, env_a, clear=False):
                with mock.patch(
                    "backend.app.application.final_window.heartbeat_cutover_lease"
                ):
                    with self.assertRaises(EvidenceChildFailure):
                        capture_evidence(
                            key="build-identity-verify", phase="final", result_kind="machine-summary",
                            run_manifest=run_a.manifest_path, expected_run_manifest_sha256=run_a.manifest_file_sha256,
                            cutover_lease=lease_a, cutover_token_file=token_a, startup_snapshot=startup_a,
                            expected_startup_snapshot_sha256=startup_sha_a,
                            build_identity_manifest=build.manifest_path, output=run_a.run_directory / "capture.json",
                            summary_artifact=summary_a,
                            argv=(sys.executable, "-B", "-c", failure_code, str(summary_a)),
                        )
            sealed = {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in run_a.run_directory.iterdir()}
            with mock.patch.dict(os.environ, env_a, clear=False):
                with self.assertRaises(EvidenceCaptureError) as immutable:
                    capture_evidence(
                        key="build-identity-verify", phase="final", result_kind="machine-summary",
                        run_manifest=run_a.manifest_path, expected_run_manifest_sha256=run_a.manifest_file_sha256,
                        cutover_lease=lease_a, cutover_token_file=token_a, startup_snapshot=startup_a,
                        expected_startup_snapshot_sha256=startup_sha_a,
                        build_identity_manifest=build.manifest_path, output=run_a.run_directory / "retry.json",
                        summary_artifact=run_a.run_directory / "retry-summary.json",
                        argv=(sys.executable, "-B", "-c", "raise SystemExit(0)"),
                    )
            self.assertEqual("EVIDENCE_RUN_SEALED", immutable.exception.code)
            self.assertEqual(sealed, {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in run_a.run_directory.iterdir()})

            run_b, lease_b, token_b, startup_b, startup_sha_b, env_b = make_run("f" * 32)
            summary_b = run_b.run_directory / "summary.json"
            success_code = (
                "import json,pathlib,sys;"
                "pathlib.Path(sys.argv[1]).write_text(json.dumps({"
                "'schemaVersion':1,'manifestKind':'machine-summary','adapter':'check',"
                "'rawExit':0,'totals':1,'failures':0,'skips':0,"
                "'resultArtifactPath':None,'resultArtifactFormat':'raw-exit'}),encoding='utf-8')"
            )
            with mock.patch.dict(os.environ, env_b, clear=False):
                with mock.patch(
                    "backend.app.application.final_window.heartbeat_cutover_lease"
                ):
                    result = capture_evidence(
                        key="build-identity-verify", phase="final", result_kind="machine-summary",
                        run_manifest=run_b.manifest_path, expected_run_manifest_sha256=run_b.manifest_file_sha256,
                        cutover_lease=lease_b, cutover_token_file=token_b, startup_snapshot=startup_b,
                        expected_startup_snapshot_sha256=startup_sha_b,
                        build_identity_manifest=build.manifest_path, output=run_b.run_directory / "capture.json",
                        summary_artifact=summary_b,
                        argv=(sys.executable, "-B", "-c", success_code, str(summary_b)),
                    )
            self.assertEqual(0, result.exit_code)
            self.assertNotEqual(run_a.run_id, run_b.run_id)

    def test_capture_spawn_failure_seals_reserved_run(self) -> None:
        from backend.app.api.compat.database_identity import DatabaseEvidenceIdentityService
        from backend.app.api.compat.evidence_capture import (
            EvidenceCaptureError,
            capture_evidence,
            create_evidence_run,
        )

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
                run_id="7" * 32,
                phase="provisional",
                build_identity_manifest=build.manifest_path,
                database_identity_manifest=database.manifest_path,
                expected_keys=("build-identity-verify",),
            )
            environment = {
                "P6_PROVISIONAL_EVIDENCE_RUN_MANIFEST_PATH": str(run.manifest_path),
                "P6_PROVISIONAL_EVIDENCE_RUN_MANIFEST_SHA256": run.manifest_file_sha256,
                "P6_PROVISIONAL_EVIDENCE_RUN_ID": run.run_id,
            }
            output = run.run_directory / "capture.json"
            with mock.patch.dict(os.environ, environment, clear=False):
                with self.assertRaises(EvidenceCaptureError) as caught:
                    capture_evidence(
                        key="build-identity-verify",
                        phase="provisional",
                        result_kind="json-cli",
                        run_manifest=run.manifest_path,
                        expected_run_manifest_sha256=run.manifest_file_sha256,
                        build_identity_manifest=build.manifest_path,
                        output=output,
                        argv=(str(fixture.root / "missing-child.exe"),),
                    )

            self.assertEqual("EVIDENCE_CHILD_SPAWN_FAILED", caught.exception.code)
            self.assertTrue(output.exists())
            self.assertTrue((run.run_directory / "failure-seal-v1.json").is_file())

    def test_capture_rejects_legacy_machine_summary_schema(self) -> None:
        from backend.app.api.compat.database_identity import DatabaseEvidenceIdentityService
        from backend.app.api.compat.evidence_capture import (
            EvidenceCaptureError,
            capture_evidence,
            create_evidence_run,
        )

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
                run_id="6" * 32,
                phase="provisional",
                build_identity_manifest=build.manifest_path,
                database_identity_manifest=database.manifest_path,
                expected_keys=("build-identity-verify",),
            )
            summary = run.run_directory / "legacy-summary.json"
            child = (
                "import json,pathlib,sys;"
                "pathlib.Path(sys.argv[1]).write_text(json.dumps({"
                "'schemaVersion':1,'adapter':'check','totals':1,'failures':0,'skips':0}),"
                "encoding='utf-8')"
            )
            environment = {
                "P6_PROVISIONAL_EVIDENCE_RUN_MANIFEST_PATH": str(run.manifest_path),
                "P6_PROVISIONAL_EVIDENCE_RUN_MANIFEST_SHA256": run.manifest_file_sha256,
                "P6_PROVISIONAL_EVIDENCE_RUN_ID": run.run_id,
            }
            with mock.patch.dict(os.environ, environment, clear=False):
                with self.assertRaises(EvidenceCaptureError) as caught:
                    capture_evidence(
                        key="build-identity-verify",
                        phase="provisional",
                        result_kind="machine-summary",
                        run_manifest=run.manifest_path,
                        expected_run_manifest_sha256=run.manifest_file_sha256,
                        build_identity_manifest=build.manifest_path,
                        output=run.run_directory / "capture.json",
                        summary_artifact=summary,
                        argv=(sys.executable, "-B", "-c", child, str(summary)),
                    )

            self.assertEqual("EVIDENCE_SUMMARY_INVALID", caught.exception.code)
            self.assertTrue((run.run_directory / "failure-seal-v1.json").is_file())

    def test_final_capture_heartbeats_while_child_is_running(self) -> None:
        from backend.app.api.compat.database_identity import DatabaseEvidenceIdentityService
        from backend.app.api.compat.evidence_capture import capture_evidence, create_evidence_run

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
                run_id="5" * 32,
                phase="final",
                build_identity_manifest=build.manifest_path,
                database_identity_manifest=database.manifest_path,
                expected_keys=("build-identity-verify",),
            )
            lease = fixture.root / "cutover-lease.json"
            token = fixture.root / "cutover-token.bin"
            startup = run.run_directory / "startup.json"
            lease.write_text('{"schemaVersion":1}', encoding="utf-8")
            token.write_bytes(b"owner-capability")
            startup.write_text(json.dumps({"runId": run.run_id}), encoding="utf-8")
            startup_sha = hashlib.sha256(startup.read_bytes()).hexdigest()
            summary = run.run_directory / "summary.json"
            child = (
                "import json,pathlib,sys,time;"
                "pathlib.Path(sys.argv[1]).write_text(json.dumps({"
                "'schemaVersion':1,'manifestKind':'machine-summary','adapter':'check',"
                "'rawExit':0,'totals':1,'failures':0,'skips':0,"
                "'resultArtifactPath':None,'resultArtifactFormat':'raw-exit'}),"
                "encoding='utf-8');time.sleep(0.05)"
            )
            environment = {
                "P6_FINAL_EVIDENCE_RUN_MANIFEST_PATH": str(run.manifest_path),
                "P6_FINAL_EVIDENCE_RUN_MANIFEST_SHA256": run.manifest_file_sha256,
                "P6_FINAL_EVIDENCE_RUN_ID": run.run_id,
                "P6_FINAL_WINDOW_LEASE_PATH": str(lease.resolve()),
                "P6_FINAL_WINDOW_TOKEN_FILE": str(token.resolve()),
                "P6_PRODUCTION_STARTUP_SNAPSHOT_PATH": str(startup.resolve()),
                "P6_PRODUCTION_STARTUP_SNAPSHOT_SHA256": startup_sha,
            }
            with mock.patch.dict(os.environ, environment, clear=False):
                with mock.patch(
                    "backend.app.application.final_window.heartbeat_cutover_lease"
                ) as heartbeat:
                    capture_evidence(
                        key="build-identity-verify",
                        phase="final",
                        result_kind="machine-summary",
                        run_manifest=run.manifest_path,
                        expected_run_manifest_sha256=run.manifest_file_sha256,
                        cutover_lease=lease,
                        cutover_token_file=token,
                        startup_snapshot=startup,
                        expected_startup_snapshot_sha256=startup_sha,
                        build_identity_manifest=build.manifest_path,
                        output=run.run_directory / "capture.json",
                        summary_artifact=summary,
                        argv=(sys.executable, "-B", "-c", child, str(summary)),
                    )

            self.assertGreaterEqual(heartbeat.call_count, 1)


if __name__ == "__main__":
    unittest.main()
