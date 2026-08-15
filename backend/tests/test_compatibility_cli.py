from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

from backend.tests.support.p4_identity import p4_identity_fixture


_CLI_TRACER_RUNNER = object()


def cli_tracer_runner_factory() -> object:
    return _CLI_TRACER_RUNNER


class _CliTracerCandidateResult:
    def to_dict(self) -> dict[str, object]:
        return {
            "ok": True,
            "restoredDatabasePath": "restored.db",
            "descendantDatabaseIdentityManifestPath": "descendant.json",
            "beforePath": "before.json",
            "afterPath": "after.json",
            "deltaLedgerPath": "delta.json",
            "requestId": "request-1",
            "jobId": "job-1",
            "sourceDocumentId": "source-1",
            "artifactId": "artifact-1",
            "databaseLineageId": "lineage-1",
            "subjectDatabaseId": "subject-1",
            "parentSubjectDatabaseId": "parent-1",
            "buildId": "build-1",
        }


class _CliTracerCandidateService:
    async def run(self, **kwargs: object) -> _CliTracerCandidateResult:
        if kwargs.get("runner") is not _CLI_TRACER_RUNNER:
            raise AssertionError("the explicit runner factory was not used")
        return _CliTracerCandidateResult()


def _git(root: Path, *arguments: str) -> None:
    completed = subprocess.run(
        ["git", "-C", str(root), *arguments],
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        raise AssertionError(completed.stderr.decode("utf-8", errors="replace"))


def _invoke(*arguments: str) -> tuple[int, str, str]:
    from backend.app.cli.compatibility import main

    stdout = StringIO()
    stderr = StringIO()
    with redirect_stdout(stdout), redirect_stderr(stderr):
        exit_code = main(arguments)
    return exit_code, stdout.getvalue(), stderr.getvalue()


def _create_build_inputs(root: Path) -> dict[str, Path]:
    repository = root / "repository"
    repository.mkdir()
    _git(repository, "init", "--quiet")
    _git(repository, "config", "user.name", "Compatibility CLI Test")
    _git(repository, "config", "user.email", "compat@example.test")
    source = repository / "app.py"
    source.write_text("VALUE = 1\n", encoding="utf-8")
    _git(repository, "add", "--", "app.py")
    _git(repository, "commit", "--quiet", "-m", "fixture")
    bundle = root / "study_app.whl"
    bundle.write_bytes(b"wheel-v1")
    frontend = root / "frontend"
    frontend.mkdir()
    (frontend / "index.js").write_bytes(b"frontend-v1")
    frontend_manifest = frontend / "manifest.json"
    frontend_manifest.write_text(
        json.dumps({"index.html": {"file": "index.js"}}),
        encoding="utf-8",
    )
    compose = root / "compose.json"
    compose.write_text("{}", encoding="utf-8")
    identities = root / "identities"
    identities.mkdir()
    return {
        "repository": repository,
        "source": source,
        "bundle": bundle,
        "frontend": frontend,
        "frontend_manifest": frontend_manifest,
        "compose": compose,
        "identities": identities,
    }


def _build_arguments(inputs: dict[str, Path]) -> tuple[str, ...]:
    return (
        "--source-root",
        str(inputs["repository"]),
        "--python-artifact",
        str(inputs["bundle"]),
        "--frontend-root",
        str(inputs["frontend"]),
        "--frontend-manifest",
        str(inputs["frontend_manifest"]),
        "--resolved-compose",
        str(inputs["compose"]),
        "--image-digest",
        f"candidate=sha256:{'a' * 64}",
    )


def _rollback_map(root: Path, database: Path) -> dict[str, object]:
    return {
        "imageDigest": f"sha256:{'c' * 64}",
        "entrypointPath": str(root / "server.js"),
        "cwd": str(root),
        "host": "127.0.0.1",
        "ports": {"api": 5173},
        "databasePath": str(database),
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


class CompatibilityCliTests(unittest.TestCase):
    def test_capture_evidence_cli_forwards_json_database_identity(self) -> None:
        import backend.app.cli.compatibility as compatibility

        captured: dict[str, object] = {}

        def fake_capture(**kwargs: object) -> SimpleNamespace:
            captured.update(kwargs)
            return SimpleNamespace(
                evidence_key="build-identity-verify",
                record_path=Path("record.json"),
                exit_code=0,
                totals=1,
                failures=0,
                skips=0,
                stdout_path=Path("stdout.bin"),
                stderr_path=Path("stderr.bin"),
            )

        with mock.patch.object(compatibility, "capture_evidence", fake_capture):
            exit_code, stdout, stderr = _invoke(
                "capture-evidence",
                "--key",
                "build-identity-verify",
                "--phase",
                "provisional",
                "--result-kind",
                "json-cli",
                "--run-manifest",
                "run.json",
                "--expected-run-manifest-sha256",
                "a" * 64,
                "--database-identity-from-json",
                "descendantDatabaseIdentityManifestPath",
                "--build-identity-manifest",
                "build.json",
                "--output",
                "capture.json",
                "--",
                sys.executable,
                "-c",
                "print('{}')",
            )

        self.assertEqual(0, exit_code, stderr)
        self.assertEqual("", stderr)
        self.assertEqual("descendantDatabaseIdentityManifestPath", captured["database_identity_from_json"])
        result = json.loads(stdout)
        self.assertEqual("build-identity-verify", result["evidenceKey"])

    def test_candidate_write_smoke_uses_explicit_runner_factory(self) -> None:
        import backend.app.cli.compatibility as compatibility

        with mock.patch.object(
            compatibility,
            "CandidateWriteSmokeService",
            _CliTracerCandidateService,
            create=True,
        ):
            exit_code, stdout, stderr = _invoke(
                "candidate-write-smoke",
                "--backup",
                "backup.sqlite3",
                "--manifest",
                "backup-manifest.json",
                "--restore-root",
                "restore-root",
                "--build-identity-manifest",
                "build-identity.json",
                "--parent-database-identity-manifest",
                "live-database-identity.json",
                "--descendant-database-identity-output",
                "descendant-database-identity.json",
                "--evidence-mode",
                "provisional",
                "--evidence-dir",
                "evidence",
                "--runner-factory",
                "backend.tests.test_compatibility_cli:cli_tracer_runner_factory",
            )

        self.assertEqual(0, exit_code, stderr)
        self.assertEqual("", stderr)
        result = json.loads(stdout)
        self.assertEqual(True, result["ok"])
        self.assertEqual("request-1", result["requestId"])

    def test_freeze_identity_emits_one_typed_json_result(self) -> None:
        with tempfile.TemporaryDirectory(prefix="study-app-p6-compat-cli-") as raw:
            root = Path(raw)
            inputs = _create_build_inputs(root)

            exit_code, stdout, stderr = _invoke(
                "freeze-identity",
                "--build-identity-directory",
                str(inputs["identities"]),
                *_build_arguments(inputs),
            )

            self.assertEqual(0, exit_code, stderr)
            self.assertEqual("", stderr)
            self.assertEqual(1, len(stdout.splitlines()))
            result = json.loads(stdout)
            self.assertEqual(True, result["ok"])
            self.assertEqual("freeze-identity", result["operation"])
            self.assertRegex(result["buildId"], r"^[0-9a-f]{64}$")
            self.assertRegex(result["manifestFileSha256"], r"^[0-9a-f]{64}$")
            self.assertEqual(
                inputs["identities"]
                / f"frozen-build-identity-{result['buildId']}.json",
                Path(result["manifestPath"]),
            )

    def test_verify_identity_reports_success_and_drift_as_json(self) -> None:
        with tempfile.TemporaryDirectory(prefix="study-app-p6-compat-verify-") as raw:
            inputs = _create_build_inputs(Path(raw))
            frozen_code, frozen_stdout, frozen_stderr = _invoke(
                "freeze-identity",
                "--build-identity-directory",
                str(inputs["identities"]),
                *_build_arguments(inputs),
            )
            self.assertEqual(0, frozen_code, frozen_stderr)
            manifest_path = json.loads(frozen_stdout)["manifestPath"]
            verify_arguments = (
                "verify-identity",
                "--build-identity-manifest",
                manifest_path,
                *_build_arguments(inputs),
            )

            exit_code, stdout, stderr = _invoke(*verify_arguments)
            self.assertEqual(0, exit_code, stderr)
            self.assertEqual("", stderr)
            result = json.loads(stdout)
            self.assertEqual("verify-identity", result["operation"])
            self.assertEqual(manifest_path, result["manifestPath"])

            inputs["source"].write_text("VALUE = 2\n", encoding="utf-8")
            exit_code, stdout, stderr = _invoke(*verify_arguments)
            self.assertEqual(2, exit_code)
            self.assertEqual("", stdout)
            error = json.loads(stderr)
            self.assertEqual(False, error["ok"])
            self.assertEqual("BUILD_IDENTITY_DRIFT", error["error"]["code"])

    def test_evidence_workflow_uses_typed_run_and_emits_json_only(self) -> None:
        from backend.app.api.compat.database_identity import (
            DatabaseEvidenceIdentityService,
        )

        with p4_identity_fixture() as fixture:
            inputs = _create_build_inputs(fixture.root)
            frozen_code, frozen_stdout, frozen_stderr = _invoke(
                "freeze-identity",
                "--build-identity-directory",
                str(inputs["identities"]),
                *_build_arguments(inputs),
            )
            self.assertEqual(0, frozen_code, frozen_stderr)
            build_manifest = json.loads(frozen_stdout)["manifestPath"]
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

            exit_code, stdout, stderr = _invoke(
                "create-evidence-run",
                "--evidence-root",
                str(evidence_root),
                "--run-id",
                "b" * 32,
                "--phase",
                "provisional",
                "--build-identity-manifest",
                build_manifest,
                "--database-identity-manifest",
                str(database.manifest_path),
                "--expected-key",
                "build-identity-verify",
                "--expected-key",
                "suite-isolation",
            )
            self.assertEqual(0, exit_code, stderr)
            self.assertEqual("", stderr)
            run_result = json.loads(stdout)
            run_directory = Path(run_result["runDirectory"])
            run_manifest = Path(run_result["runManifestPath"])
            run_sha = run_result["runManifestFileSha256"]
            self.assertEqual(evidence_root / f"run-{'b' * 32}", run_directory)

            live_settings = fixture.root / "live-settings.json"
            live_settings.write_text("{}", encoding="utf-8")
            live_pdf_root = fixture.root / "live-pdfs"
            live_pdf_root.mkdir()
            isolation_output = run_directory / "suite-isolation.json"
            exit_code, stdout, stderr = _invoke(
                "create-suite-isolation",
                "--run-manifest",
                str(run_manifest),
                "--expected-run-manifest-sha256",
                run_sha,
                "--suite-key",
                "backend-suite",
                "--deny-live-database",
                str(fixture.database_path),
                "--deny-live-settings",
                str(live_settings),
                "--deny-live-pdf-root",
                str(live_pdf_root),
                "--deny-live-keyring",
                "1",
                "--deny-network",
                "1",
                "--output",
                str(isolation_output),
            )
            self.assertEqual(0, exit_code, stderr)
            isolation_result = json.loads(stdout)
            self.assertEqual(0, isolation_result["liveAccessCount"])
            self.assertEqual(str(isolation_output), isolation_result["manifestPath"])

            capture_output = run_directory / "build-identity-verify.capture.json"
            environment = {
                "P6_PROVISIONAL_EVIDENCE_RUN_MANIFEST_PATH": str(run_manifest),
                "P6_PROVISIONAL_EVIDENCE_RUN_MANIFEST_SHA256": run_sha,
                "P6_PROVISIONAL_EVIDENCE_RUN_ID": "b" * 32,
            }
            child_code = "import json;print(json.dumps({'ok':True},separators=(',',':')))"
            with mock.patch.dict(os.environ, environment, clear=False):
                exit_code, stdout, stderr = _invoke(
                    "capture-evidence",
                    "--key",
                    "build-identity-verify",
                    "--phase",
                    "provisional",
                    "--result-kind",
                    "json-cli",
                    "--run-manifest",
                    str(run_manifest),
                    "--expected-run-manifest-sha256",
                    run_sha,
                    "--build-identity-manifest",
                    build_manifest,
                    "--output",
                    str(capture_output),
                    "--",
                    sys.executable,
                    "-B",
                    "-c",
                    child_code,
                )
            self.assertEqual(0, exit_code, stderr)
            capture_result = json.loads(stdout)
            self.assertEqual(0, capture_result["exitCode"])
            self.assertEqual(0, capture_result["failures"])
            self.assertEqual(str(capture_output), capture_result["recordPath"])
            capture_document = json.loads(capture_output.read_text(encoding="utf-8"))
            for prefix in ("stdout", "stderr"):
                artifact_path = Path(capture_document[prefix + "Path"])
                self.assertEqual(
                    capture_document[prefix + "Sha256"],
                    hashlib.sha256(artifact_path.read_bytes()).hexdigest(),
                )

            exit_code, stdout, stderr = _invoke(
                "gate",
                "--phase",
                "preflight",
                "--evidence-dir",
                str(run_directory),
                "--run-manifest",
                str(run_manifest),
                "--expected-run-manifest-sha256",
                run_sha,
                "--build-identity-manifest",
                build_manifest,
                "--database-identity-manifest",
                str(database.manifest_path),
            )
            self.assertEqual(0, exit_code, stderr)
            self.assertEqual("", stderr)
            gate_result = json.loads(stdout)
            self.assertEqual(True, gate_result["preflightReady"])
            self.assertEqual(False, gate_result["nodeShutdownAllowed"])

    def test_create_startup_snapshot_binds_exact_final_run_and_identities(self) -> None:
        from backend.app.api.compat.database_identity import (
            DatabaseEvidenceIdentityService,
        )

        with p4_identity_fixture() as fixture:
            inputs = _create_build_inputs(fixture.root)
            frozen_code, frozen_stdout, frozen_stderr = _invoke(
                "freeze-identity",
                "--build-identity-directory",
                str(inputs["identities"]),
                *_build_arguments(inputs),
            )
            self.assertEqual(0, frozen_code, frozen_stderr)
            build_result = json.loads(frozen_stdout)
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
            run_code, run_stdout, run_stderr = _invoke(
                "create-evidence-run",
                "--evidence-root",
                str(evidence_root),
                "--run-id",
                "d" * 32,
                "--phase",
                "final",
                "--build-identity-manifest",
                build_result["manifestPath"],
                "--database-identity-manifest",
                str(database.manifest_path),
                "--expected-key",
                "build-identity-verify",
            )
            self.assertEqual(0, run_code, run_stderr)
            run = json.loads(run_stdout)
            rollback_map = Path(run["runDirectory"]) / "frozen-node-map.json"
            rollback_map.write_text(
                json.dumps(_rollback_map(fixture.root, fixture.database_path)),
                encoding="utf-8",
            )
            output = Path(run["runDirectory"]) / "production-startup-snapshot-v1.json"

            exit_code, stdout, stderr = _invoke(
                "create-startup-snapshot",
                "--final-evidence-run-manifest",
                run["runManifestPath"],
                "--expected-final-evidence-run-manifest-sha256",
                run["runManifestFileSha256"],
                "--build-identity-manifest",
                build_result["manifestPath"],
                "--database-identity-manifest",
                str(database.manifest_path),
                "--frozen-node-rollback-map",
                str(rollback_map),
                "--production-profile",
                "production",
                "--output",
                str(output),
            )

            self.assertEqual(0, exit_code, stderr)
            self.assertEqual("", stderr)
            result = json.loads(stdout)
            self.assertEqual("create-startup-snapshot", result["operation"])
            self.assertEqual(str(output), result["startupSnapshotPath"])
            self.assertEqual(
                hashlib.sha256(output.read_bytes()).hexdigest(),
                result["startupSnapshotFileSha256"],
            )
            self.assertEqual("d" * 32, result["runId"])
            self.assertEqual(build_result["buildId"], result["buildId"])

    def test_argument_errors_are_single_json_documents(self) -> None:
        exit_code, stdout, stderr = _invoke(
            "create-suite-isolation",
            "--run-manifest",
            "missing.json",
            "--expected-run-manifest-sha256",
            "not-a-sha",
            "--suite-key",
            "backend-suite",
            "--deny-live-database",
            "missing.db",
            "--deny-live-settings",
            "missing-settings.json",
            "--deny-live-pdf-root",
            "missing-pdfs",
            "--deny-live-keyring",
            "1",
            "--deny-network",
            "1",
            "--output",
            "output.json",
        )

        self.assertEqual(2, exit_code)
        self.assertEqual("", stdout)
        self.assertEqual(1, len(stderr.splitlines()))
        error = json.loads(stderr)
        self.assertEqual(False, error["ok"])
        self.assertEqual("COMPATIBILITY_ARGUMENT_INVALID", error["error"]["code"])
        self.assertNotIn("usage:", stderr)

    def test_verify_static_runbook_emits_json_result(self) -> None:
        root = Path(__file__).resolve().parents[2]
        exit_code, stdout, stderr = _invoke(
            "verify-static-runbook",
            "--readme",
            str(root / "README.md"),
            "--database-doc",
            str(root / "docs" / "DATABASE.md"),
        )

        self.assertEqual(0, exit_code, stderr)
        self.assertEqual("", stderr)
        result = json.loads(stdout)
        self.assertEqual("verify-static-runbook", result["operation"])
        self.assertEqual(True, result["ok"])
        self.assertEqual(
            str(root / "data" / "compatibility" / "runtime" / "production-owner.json"),
            result["runtimeOwnerMarker"],
        )


if __name__ == "__main__":
    unittest.main()
