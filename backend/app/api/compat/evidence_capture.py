from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
from typing import Callable, Sequence

from backend.app.api.compat.build_identity import (
    BuildIdentityError,
    load_build_identity_manifest,
)
from backend.app.api.compat.database_identity import (
    DatabaseIdentityError,
    canonical_json_bytes,
    exclusive_write_bytes,
    load_database_evidence_identity_manifest,
    verify_database_evidence_identity_subject,
    verify_descendant_database_evidence_identity,
)
from backend.app.api.compat.gates import SHUTDOWN_EVIDENCE_KEYS
from backend.app.infrastructure.database_backup import (
    DatabaseBackupError,
    open_bound_restore_root,
)


_RUN_ID = re.compile(r"^[0-9a-f]{32}$")
_FINAL_HEARTBEAT_INTERVAL_SECONDS = 30.0
_FINAL_HEARTBEAT_TIMEOUT_SECONDS = 120
_RUN_FIELDS = (
    "schemaVersion",
    "manifestKind",
    "runId",
    "phase",
    "runDirectory",
    "buildIdentityManifestPath",
    "buildIdentityManifestSha256",
    "databaseIdentityManifestPath",
    "databaseIdentityManifestSha256",
    "originReceiptPath",
    "originReceiptFileSha256",
    "expectedKeys",
    "createdAt",
    "runManifestSha256",
)


class EvidenceCaptureError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class EvidenceChildFailure(EvidenceCaptureError):
    def __init__(self, exit_code: int, record_path: Path) -> None:
        super().__init__(
            "EVIDENCE_CHILD_FAILED",
            "The evidence child command did not produce a zero-failure, zero-skip result.",
        )
        self.exit_code = exit_code
        self.record_path = record_path


@dataclass(frozen=True, slots=True)
class EvidenceRunManifest:
    run_id: str
    phase: str
    run_directory: Path
    manifest_path: Path
    manifest_file_sha256: str
    run_manifest_sha256: str
    build_identity_manifest_path: Path
    build_identity_manifest_sha256: str
    database_identity_manifest_path: Path
    database_identity_manifest_sha256: str
    expected_keys: tuple[str, ...]
    canonical_bytes: bytes


@dataclass(frozen=True, slots=True)
class EvidenceCaptureRecord:
    evidence_key: str
    record_path: Path
    exit_code: int
    totals: int
    failures: int
    skips: int
    stdout_path: Path
    stderr_path: Path
    canonical_bytes: bytes


@contextmanager
def _bound_new_run_directory(root: Path, name: str):
    """Create one exact child while keeping its root identity bound."""

    path = root / name
    if os.name == "nt":
        bound_root = open_bound_restore_root(root)
        bound_run = None
        try:
            os.mkdir(path, mode=0o700)
            bound_run = open_bound_restore_root(path)
            resolved = path.resolve(strict=True)
            if resolved.parent != root or resolved.name != name:
                raise EvidenceCaptureError(
                    "EVIDENCE_ROOT_CHANGED",
                    "The evidence run directory escaped its bound root.",
                )
            yield resolved, None
        finally:
            try:
                if bound_run is not None:
                    bound_run.close()
            finally:
                bound_root.close()
        return

    required = (os.mkdir, os.open, os.stat)
    if any(operation not in os.supports_dir_fd for operation in required):
        raise EvidenceCaptureError(
            "EVIDENCE_ROOT_CHANGED",
            "This platform cannot bind evidence creation to a directory descriptor.",
        )
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    root_descriptor = os.open(root, flags)
    run_descriptor: int | None = None
    try:
        root_identity = os.fstat(root_descriptor)
        os.mkdir(name, mode=0o700, dir_fd=root_descriptor)
        run_descriptor = os.open(name, flags, dir_fd=root_descriptor)
        run_identity = os.fstat(run_descriptor)
        current_root = os.stat(root, follow_symlinks=False)
        current_run = os.stat(name, dir_fd=root_descriptor, follow_symlinks=False)
        if (
            (root_identity.st_dev, root_identity.st_ino)
            != (current_root.st_dev, current_root.st_ino)
            or (run_identity.st_dev, run_identity.st_ino)
            != (current_run.st_dev, current_run.st_ino)
        ):
            raise EvidenceCaptureError(
                "EVIDENCE_ROOT_CHANGED",
                "The evidence root identity changed during run creation.",
            )
        resolved = path.resolve(strict=True)
        if resolved.parent != root or resolved.name != name:
            raise EvidenceCaptureError(
                "EVIDENCE_ROOT_CHANGED",
                "The evidence run directory escaped its bound root.",
            )
        yield resolved, run_descriptor
        current_root = os.stat(root, follow_symlinks=False)
        current_run = os.stat(name, dir_fd=root_descriptor, follow_symlinks=False)
        if (
            (root_identity.st_dev, root_identity.st_ino)
            != (current_root.st_dev, current_root.st_ino)
            or (run_identity.st_dev, run_identity.st_ino)
            != (current_run.st_dev, current_run.st_ino)
        ):
            raise EvidenceCaptureError(
                "EVIDENCE_ROOT_CHANGED",
                "The evidence root identity changed before manifest publication.",
            )
    finally:
        if run_descriptor is not None:
            os.close(run_descriptor)
        os.close(root_descriptor)


def _exclusive_write_bound(
    path: Path,
    payload: bytes,
    *,
    run_descriptor: int | None,
) -> None:
    if run_descriptor is None:
        exclusive_write_bytes(path, payload)
        return
    descriptor: int | None = None
    try:
        descriptor = os.open(
            path.name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
            dir_fd=run_descriptor,
        )
        view = memoryview(payload)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise OSError("bound evidence write made no progress")
            view = view[written:]
        os.fsync(descriptor)
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _run_capture_child(
    *,
    argv: Sequence[str],
    cwd: Path,
    final_lease: tuple[Path, Path] | None,
) -> subprocess.CompletedProcess[bytes]:
    process = subprocess.Popen(
        list(argv),
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        shell=False,
    )
    try:
        if final_lease is None:
            stdout, stderr = process.communicate()
        else:
            from backend.app.application.final_window import heartbeat_cutover_lease

            lease_path, token_path = final_lease
            while True:
                try:
                    heartbeat_cutover_lease(
                        cutover_lease=lease_path,
                        cutover_token_file=token_path,
                        heartbeat_timeout_seconds=_FINAL_HEARTBEAT_TIMEOUT_SECONDS,
                    )
                except Exception as error:
                    _stop_capture_child(process)
                    raise EvidenceCaptureError(
                        "EVIDENCE_HEARTBEAT_FAILED",
                        "The final evidence child lost its cutover lease.",
                    ) from error
                try:
                    stdout, stderr = process.communicate(
                        timeout=_FINAL_HEARTBEAT_INTERVAL_SECONDS
                    )
                    break
                except subprocess.TimeoutExpired:
                    continue
    except BaseException:
        if process.poll() is None:
            _stop_capture_child(process)
        raise
    return subprocess.CompletedProcess(
        args=list(argv),
        returncode=int(process.returncode),
        stdout=stdout,
        stderr=stderr,
    )


def _stop_capture_child(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.communicate(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.communicate()


def create_evidence_run(
    *,
    evidence_root: str | os.PathLike[str],
    run_id: str,
    phase: str,
    build_identity_manifest: str | os.PathLike[str],
    database_identity_manifest: str | os.PathLike[str],
    expected_keys: Sequence[str],
    clock: Callable[[], datetime] | None = None,
) -> EvidenceRunManifest:
    if not isinstance(run_id, str) or not _RUN_ID.fullmatch(run_id):
        raise EvidenceCaptureError(
            "EVIDENCE_RUN_ID_INVALID",
            "The evidence run ID must be lowercase 32-hex.",
        )
    if phase not in {"provisional", "final"}:
        raise EvidenceCaptureError(
            "EVIDENCE_PHASE_INVALID",
            "The evidence phase must be provisional or final.",
        )
    keys = tuple(expected_keys)
    if (
        not keys
        or len(set(keys)) != len(keys)
        or any(not isinstance(key, str) or key not in SHUTDOWN_EVIDENCE_KEYS for key in keys)
    ):
        raise EvidenceCaptureError(
            "EVIDENCE_KEYS_INVALID",
            "Evidence keys must be unique, nonempty, and allowlisted.",
        )
    try:
        build = load_build_identity_manifest(build_identity_manifest)
        database = load_database_evidence_identity_manifest(database_identity_manifest)
    except (BuildIdentityError, DatabaseIdentityError) as error:
        raise EvidenceCaptureError(
            "EVIDENCE_IDENTITY_INVALID",
            "An evidence run identity manifest is invalid.",
        ) from error
    root = Path(evidence_root).resolve(strict=True)
    if not root.is_dir():
        raise EvidenceCaptureError(
            "EVIDENCE_ROOT_INVALID",
            "The evidence root must be a directory.",
        )
    run_name = f"run-{run_id}"
    try:
        with _bound_new_run_directory(root, run_name) as (
            resolved_run,
            run_descriptor,
        ):
            instant = (clock or (lambda: datetime.now(timezone.utc)))()
            if instant.tzinfo is None or instant.utcoffset() is None:
                raise EvidenceCaptureError(
                    "EVIDENCE_TIMESTAMP_INVALID",
                    "The evidence run timestamp must be timezone-aware.",
                )
            unsigned = {
                "schemaVersion": 1,
                "manifestKind": "evidence-run",
                "runId": run_id,
                "phase": phase,
                "runDirectory": str(resolved_run),
                "buildIdentityManifestPath": str(build.manifest_path),
                "buildIdentityManifestSha256": build.manifest_file_sha256,
                "databaseIdentityManifestPath": str(database.manifest_path),
                "databaseIdentityManifestSha256": database.identity_manifest_file_sha256,
                "originReceiptPath": str(database.origin_receipt_path),
                "originReceiptFileSha256": database.origin_receipt_file_sha256,
                "expectedKeys": list(keys),
                "createdAt": instant.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
            }
            self_hash = hashlib.sha256(canonical_json_bytes(unsigned)).hexdigest()
            document = {**unsigned, "runManifestSha256": self_hash}
            payload = canonical_json_bytes(document)
            output = resolved_run / "evidence-run-manifest-v1.json"
            _exclusive_write_bound(output, payload, run_descriptor=run_descriptor)
    except FileExistsError as error:
        raise EvidenceCaptureError(
            "EVIDENCE_RUN_EXISTS",
            "The evidence run directory already exists.",
        ) from error
    except EvidenceCaptureError:
        raise
    except (DatabaseBackupError, DatabaseIdentityError, OSError) as error:
        code = getattr(error, "code", "EVIDENCE_RUN_CREATE_FAILED")
        if code in {
            "RESTORE_OUTPUT_DIRECTORY_CHANGED",
            "RESTORE_OUTPUT_DIRECTORY_INVALID",
            "RESTORE_BOUND_DIRECTORY_UNSUPPORTED",
        }:
            code = "EVIDENCE_ROOT_CHANGED"
        elif code == "EVIDENCE_OUTPUT_EXISTS":
            code = "EVIDENCE_RUN_EXISTS"
        else:
            code = "EVIDENCE_RUN_CREATE_FAILED"
        raise EvidenceCaptureError(
            code,
            "The evidence run directory could not be created safely.",
        ) from error
    return EvidenceRunManifest(
        run_id=run_id,
        phase=phase,
        run_directory=resolved_run,
        manifest_path=output.resolve(strict=True),
        manifest_file_sha256=hashlib.sha256(payload).hexdigest(),
        run_manifest_sha256=self_hash,
        build_identity_manifest_path=build.manifest_path,
        build_identity_manifest_sha256=build.manifest_file_sha256,
        database_identity_manifest_path=database.manifest_path,
        database_identity_manifest_sha256=database.identity_manifest_file_sha256,
        expected_keys=keys,
        canonical_bytes=payload,
    )


def load_evidence_run_manifest(
    manifest: str | os.PathLike[str],
    *,
    expected_file_sha256: str | None = None,
) -> EvidenceRunManifest:
    path = Path(manifest).resolve(strict=True)
    payload = path.read_bytes()
    file_sha = hashlib.sha256(payload).hexdigest()
    if expected_file_sha256 is not None and file_sha != expected_file_sha256:
        raise EvidenceCaptureError(
            "EVIDENCE_RUN_HASH_MISMATCH",
            "The evidence run manifest file hash does not match.",
        )
    document = _strict_json_object(payload, _RUN_FIELDS, "evidence run manifest")
    if document["schemaVersion"] != 1 or document["manifestKind"] != "evidence-run":
        raise EvidenceCaptureError(
            "EVIDENCE_RUN_INVALID",
            "The evidence run manifest kind or version is invalid.",
        )
    run_id = document["runId"]
    phase = document["phase"]
    if not isinstance(run_id, str) or not _RUN_ID.fullmatch(run_id):
        raise EvidenceCaptureError("EVIDENCE_RUN_INVALID", "The run ID is invalid.")
    if phase not in {"provisional", "final"}:
        raise EvidenceCaptureError("EVIDENCE_RUN_INVALID", "The run phase is invalid.")
    run_directory = Path(_required_string(document["runDirectory"])).resolve(strict=True)
    if (
        not run_directory.is_dir()
        or run_directory.name != f"run-{run_id}"
        or path != run_directory / "evidence-run-manifest-v1.json"
    ):
        raise EvidenceCaptureError(
            "EVIDENCE_RUN_PATH_INVALID",
            "The evidence run manifest path is not canonical.",
        )
    unsigned = dict(document)
    self_hash = _required_sha256(unsigned.pop("runManifestSha256"))
    if hashlib.sha256(canonical_json_bytes(unsigned)).hexdigest() != self_hash:
        raise EvidenceCaptureError(
            "EVIDENCE_RUN_INVALID",
            "The evidence run manifest self hash is invalid.",
        )
    build = load_build_identity_manifest(
        _required_string(document["buildIdentityManifestPath"])
    )
    if build.manifest_file_sha256 != _required_sha256(
        document["buildIdentityManifestSha256"]
    ):
        raise EvidenceCaptureError(
            "EVIDENCE_IDENTITY_MISMATCH",
            "The run build identity file hash does not match.",
        )
    database = load_database_evidence_identity_manifest(
        _required_string(document["databaseIdentityManifestPath"])
    )
    if database.identity_manifest_file_sha256 != _required_sha256(
        document["databaseIdentityManifestSha256"]
    ):
        raise EvidenceCaptureError(
            "EVIDENCE_IDENTITY_MISMATCH",
            "The run database identity file hash does not match.",
        )
    if (
        str(database.origin_receipt_path) != document["originReceiptPath"]
        or database.origin_receipt_file_sha256
        != document["originReceiptFileSha256"]
    ):
        raise EvidenceCaptureError(
            "EVIDENCE_IDENTITY_MISMATCH",
            "The run OriginReceipt binding does not match.",
        )
    keys_value = document["expectedKeys"]
    if (
        not isinstance(keys_value, list)
        or not keys_value
        or not all(isinstance(key, str) for key in keys_value)
        or len(set(keys_value)) != len(keys_value)
        or any(key not in SHUTDOWN_EVIDENCE_KEYS for key in keys_value)
    ):
        raise EvidenceCaptureError(
            "EVIDENCE_RUN_INVALID",
            "The evidence run expected keys are invalid.",
        )
    return EvidenceRunManifest(
        run_id=run_id,
        phase=phase,
        run_directory=run_directory,
        manifest_path=path,
        manifest_file_sha256=file_sha,
        run_manifest_sha256=self_hash,
        build_identity_manifest_path=build.manifest_path,
        build_identity_manifest_sha256=build.manifest_file_sha256,
        database_identity_manifest_path=database.manifest_path,
        database_identity_manifest_sha256=database.identity_manifest_file_sha256,
        expected_keys=tuple(keys_value),
        canonical_bytes=payload,
    )


def capture_evidence(
    *,
    key: str,
    phase: str,
    result_kind: str,
    run_manifest: str | os.PathLike[str],
    expected_run_manifest_sha256: str,
    database_identity_manifest: str | os.PathLike[str] | None = None,
    database_identity_from_json: str | None = None,
    isolation_manifest: str | os.PathLike[str] | None = None,
    artifacts: Sequence[tuple[str, str | os.PathLike[str]]] = (),
    artifact_from_json: Sequence[str] = (),
    cutover_lease: str | os.PathLike[str] | None = None,
    cutover_token_file: str | os.PathLike[str] | None = None,
    startup_snapshot: str | os.PathLike[str] | None = None,
    expected_startup_snapshot_sha256: str | None = None,
    build_identity_manifest: str | os.PathLike[str],
    output: str | os.PathLike[str],
    summary_artifact: str | os.PathLike[str] | None = None,
    argv: Sequence[str],
    cwd: str | os.PathLike[str] | None = None,
    clock: Callable[[], datetime] | None = None,
) -> EvidenceCaptureRecord:
    run = load_evidence_run_manifest(
        run_manifest,
        expected_file_sha256=expected_run_manifest_sha256,
    )
    if phase != run.phase:
        raise EvidenceCaptureError(
            "EVIDENCE_PHASE_MISMATCH",
            "The capture phase does not match its run manifest.",
        )
    database = _resolve_capture_database_identity(
        run,
        database_identity_manifest=database_identity_manifest,
    )
    if database_identity_from_json is not None and database_identity_manifest is not None:
        raise EvidenceCaptureError(
            "EVIDENCE_ARGUMENT_INVALID",
            "Explicit and JSON-returned database identities cannot be combined.",
        )
    if database_identity_from_json is not None and result_kind != "json-cli":
        raise EvidenceCaptureError(
            "EVIDENCE_ARGUMENT_INVALID",
            "A JSON-returned database identity requires a JSON CLI child.",
        )
    isolation = _load_capture_isolation(
        run,
        key=key,
        result_kind=result_kind,
        isolation_manifest=isolation_manifest,
    )
    explicit_artifacts = _normalize_artifacts(artifacts)
    json_artifact_keys = tuple(artifact_from_json)
    if len(set(json_artifact_keys)) != len(json_artifact_keys) or any(
        not isinstance(value, str) or not value for value in json_artifact_keys
    ):
        raise EvidenceCaptureError(
            "EVIDENCE_ARTIFACT_INVALID",
            "Artifact-from-JSON keys must be unique, nonempty strings.",
        )
    final_binding = _verify_process_snapshot(
        run,
        cutover_lease=cutover_lease,
        cutover_token_file=cutover_token_file,
        startup_snapshot=startup_snapshot,
        expected_startup_snapshot_sha256=expected_startup_snapshot_sha256,
    )
    if key not in run.expected_keys or key not in SHUTDOWN_EVIDENCE_KEYS:
        raise EvidenceCaptureError(
            "EVIDENCE_KEY_INVALID",
            "The evidence key is not allowlisted for this run.",
        )
    if result_kind not in {"machine-summary", "json-cli"}:
        raise EvidenceCaptureError(
            "EVIDENCE_RESULT_KIND_INVALID",
            "The evidence result adapter is invalid.",
        )
    build = load_build_identity_manifest(build_identity_manifest)
    if (
        build.manifest_path != run.build_identity_manifest_path
        or build.manifest_file_sha256 != run.build_identity_manifest_sha256
    ):
        raise EvidenceCaptureError(
            "EVIDENCE_IDENTITY_MISMATCH",
            "The capture build identity does not match its run.",
        )
    if (run.run_directory / "failure-seal-v1.json").exists():
        raise EvidenceCaptureError(
            "EVIDENCE_RUN_SEALED",
            "The failed evidence run is immutable.",
        )
    output_path = _new_run_path(output, run, label="capture output")
    if result_kind == "machine-summary":
        if summary_artifact is None:
            raise EvidenceCaptureError(
                "EVIDENCE_SUMMARY_MISSING",
                "Machine-summary capture requires an explicit run-local artifact.",
            )
        summary_path = _new_run_path(summary_artifact, run, label="machine summary")
    else:
        if summary_artifact is not None:
            raise EvidenceCaptureError(
                "EVIDENCE_RESULT_KIND_INVALID",
                "JSON CLI capture cannot accept a machine summary artifact.",
            )
        summary_path = None
    if not argv or not all(isinstance(value, str) and value for value in argv):
        raise EvidenceCaptureError(
            "EVIDENCE_ARGV_INVALID",
            "The evidence child argv is invalid.",
        )
    _reject_secret_argv(argv)
    for candidate in run.run_directory.glob("*.json"):
        if candidate == run.manifest_path or candidate == summary_path:
            continue
        try:
            existing = json.loads(candidate.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            continue
        if (
            isinstance(existing, dict)
            and existing.get("producer") == "compatibility.capture-evidence"
            and existing.get("phase") == phase
            and existing.get("evidenceKey") == key
        ):
            raise EvidenceCaptureError(
                "EVIDENCE_DUPLICATE_KEY",
                "This evidence key already has a record in the run.",
            )
    try:
        output_descriptor = os.open(
            output_path,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o600,
        )
    except FileExistsError as error:
        raise EvidenceCaptureError(
            "EVIDENCE_OUTPUT_EXISTS",
            "The evidence capture output already exists.",
        ) from error
    try:
        started = _instant(clock)
        try:
            work_directory = Path(cwd or Path.cwd()).resolve(strict=True)
            completed = _run_capture_child(
                argv=argv,
                cwd=work_directory,
                final_lease=(
                    (Path(cutover_lease), Path(cutover_token_file))
                    if phase == "final"
                    and cutover_lease is not None
                    and cutover_token_file is not None
                    else None
                ),
            )
        except EvidenceCaptureError:
            raise
        except (OSError, ValueError, subprocess.SubprocessError) as error:
            raise EvidenceCaptureError(
                "EVIDENCE_CHILD_SPAWN_FAILED",
                "The evidence child process could not be started.",
            ) from error
        finished = _instant(clock)
        stdout_path = output_path.with_suffix(".stdout.bin")
        stderr_path = output_path.with_suffix(".stderr.bin")
        exclusive_write_bytes(stdout_path, completed.stdout)
        exclusive_write_bytes(stderr_path, completed.stderr)
        if result_kind == "machine-summary":
            assert summary_path is not None
            summary = _load_machine_summary(
                summary_path,
                completed.returncode,
                run_root=run.run_directory,
            )
            summary_artifact_path: str | None = str(summary_path)
            summary_artifact_sha: str | None = _file_sha256(summary_path)
            summary_artifact_format: str | None = summary["format"]
        else:
            if completed.returncode == 0:
                _load_json_cli_result(completed.stdout)
            summary = {
                "totals": 1,
                "failures": int(completed.returncode != 0),
                "skips": 0,
                "format": "json-cli",
            }
            summary_artifact_path = None
            summary_artifact_sha = None
            summary_artifact_format = "json-cli"
        json_result: dict[str, object] = {}
        if result_kind == "json-cli":
            json_result = _load_json_cli_result(completed.stdout)
            if database_identity_from_json is not None:
                identity_value = _json_result_value(
                    json_result,
                    database_identity_from_json,
                )
                if not isinstance(identity_value, str) or not identity_value:
                    raise EvidenceCaptureError(
                        "EVIDENCE_IDENTITY_MISMATCH",
                        "The JSON CLI result does not contain the requested database identity.",
                    )
                database = _resolve_capture_database_identity(
                    run,
                    database_identity_manifest=identity_value,
                    require_run_root=True,
                )
        artifact_documents = list(explicit_artifacts)
        for field in json_artifact_keys:
            value = _json_result_value(json_result, field)
            if not isinstance(value, str) or not value:
                raise EvidenceCaptureError(
                    "EVIDENCE_ARTIFACT_INVALID",
                    f"The JSON CLI result does not contain artifact path {field!r}.",
                )
            artifact_documents.append((field, Path(value)))
        artifacts_payload = []
        for name, value in artifact_documents:
            artifact_path = _artifact_path(value, run)
            if artifact_path in {output_path, summary_path, run.manifest_path}:
                raise EvidenceCaptureError(
                    "EVIDENCE_ARTIFACT_INVALID",
                    "An evidence artifact cannot overwrite a reserved run file.",
                )
            artifacts_payload.append(
                {
                    "name": name,
                    "path": str(artifact_path),
                    "sha256": _file_sha256(artifact_path),
                }
            )
        document = {
            "schemaVersion": 1,
            "producer": "compatibility.capture-evidence",
            "runId": run.run_id,
            "runManifestPath": str(run.manifest_path),
            "runManifestFileSha256": run.manifest_file_sha256,
            "evidenceKey": key,
            "phase": phase,
            "provisional": phase == "provisional",
            "resultKind": result_kind,
            "argv": list(argv),
            "executable": str(Path(argv[0]).resolve(strict=True)),
            "cwd": str(work_directory),
            "startedAt": started,
            "finishedAt": finished,
            "exitCode": completed.returncode,
            "totals": summary["totals"],
            "failures": summary["failures"],
            "skips": summary["skips"],
            "summaryArtifactPath": summary_artifact_path,
            "summaryArtifactFormat": summary_artifact_format,
            "summaryArtifactSha256": summary_artifact_sha,
            "stdoutPath": str(stdout_path),
            "stdoutSha256": hashlib.sha256(completed.stdout).hexdigest(),
            "stderrPath": str(stderr_path),
            "stderrSha256": hashlib.sha256(completed.stderr).hexdigest(),
            "buildIdentityManifestPath": str(build.manifest_path),
            "buildIdentityManifestSha256": build.manifest_file_sha256,
            "buildId": build.build_id,
            "gitRevision": build.git_revision,
            "sourceTreeHash": build.source_tree_hash,
            "buildArtifactHash": build.build_artifact_hash,
            "databaseIdentityManifestPath": str(database.manifest_path),
            "databaseIdentityManifestSha256": database.identity_manifest_file_sha256,
            "databaseLineageId": database.database_lineage_id,
            "subjectDatabaseId": database.subject_database_id,
            "subjectKind": database.subject_kind,
            "parentBackupId": database.parent_backup_id,
            "parentManifestSha256": database.parent_manifest_sha256,
            "parentDatabaseIdentityManifestPath": (
                str(database.parent_database_identity_manifest_path)
                if database.parent_database_identity_manifest_path is not None
                else None
            ),
            "parentSubjectDatabaseId": database.parent_subject_database_id,
            "parentIdentityManifestFileSha256": database.parent_identity_manifest_file_sha256,
            "originReceiptPath": str(database.origin_receipt_path),
            "originReceiptFileSha256": database.origin_receipt_file_sha256,
            **final_binding,
        }
        if artifacts_payload:
            document["artifacts"] = artifacts_payload
        if isolation is not None:
            document.update(isolation)
        record_hash = hashlib.sha256(canonical_json_bytes(document)).hexdigest()
        payload = canonical_json_bytes({**document, "recordSha256": record_hash})
        _write_reserved(output_descriptor, payload)
        output_descriptor = -1
    except BaseException:
        if output_descriptor >= 0:
            os.close(output_descriptor)
        _seal_failed_run(run, reason="capture-invalid", record_path=output_path)
        raise
    record = EvidenceCaptureRecord(
        evidence_key=key,
        record_path=output_path,
        exit_code=completed.returncode,
        totals=summary["totals"],
        failures=summary["failures"],
        skips=summary["skips"],
        stdout_path=stdout_path,
        stderr_path=stderr_path,
        canonical_bytes=payload,
    )
    if completed.returncode != 0 or summary["failures"] != 0 or summary["skips"] != 0:
        _seal_failed_run(run, reason="child-failed", record_path=output_path)
        raise EvidenceChildFailure(completed.returncode, output_path)
    return record


def _resolve_capture_database_identity(
    run: EvidenceRunManifest,
    *,
    database_identity_manifest: str | os.PathLike[str] | None,
    require_run_root: bool = False,
) -> Any:
    path = (
        run.database_identity_manifest_path
        if database_identity_manifest is None
        else Path(database_identity_manifest).resolve(strict=True)
    )
    if require_run_root and not _is_below(path, run.run_directory):
        raise EvidenceCaptureError(
            "EVIDENCE_IDENTITY_MISMATCH",
            "A JSON-returned database identity must be inside the exact evidence run root.",
        )
    try:
        identity = load_database_evidence_identity_manifest(path)
        run_identity = load_database_evidence_identity_manifest(
            run.database_identity_manifest_path
        )
        if (
            identity.database_lineage_id != run_identity.database_lineage_id
            or identity.origin_receipt_path != run_identity.origin_receipt_path
            or identity.origin_receipt_file_sha256
            != run_identity.origin_receipt_file_sha256
            or identity.origin_receipt_sha256 != run_identity.origin_receipt_sha256
        ):
            raise DatabaseIdentityError(
                "EVIDENCE_IDENTITY_MISMATCH",
                "The capture database identity does not share the run lineage and receipt.",
            )
        if identity.subject_kind == "live":
            if (
                identity.manifest_path != run_identity.manifest_path
                or identity.identity_manifest_file_sha256
                != run_identity.identity_manifest_file_sha256
            ):
                raise DatabaseIdentityError(
                    "EVIDENCE_IDENTITY_MISMATCH",
                    "A Live capture must use the exact database identity bound to its run.",
                )
            verify_database_evidence_identity_subject(
                database=identity.database_path,
                identity=identity,
            )
        else:
            if (
                identity.parent_database_identity_manifest_path
                != run_identity.manifest_path
                or identity.parent_subject_database_id
                != run_identity.subject_database_id
                or identity.parent_identity_manifest_file_sha256
                != run_identity.identity_manifest_file_sha256
            ):
                raise DatabaseIdentityError(
                    "EVIDENCE_IDENTITY_MISMATCH",
                    "A descendant capture must point directly to the run Live identity.",
                )
            verify_descendant_database_evidence_identity(
                database=identity.database_path,
                identity=identity,
            )
        return identity
    except DatabaseIdentityError as error:
        raise EvidenceCaptureError(error.code, str(error)) from error


def _normalize_artifacts(
    values: Sequence[tuple[str, str | os.PathLike[str]]],
) -> list[tuple[str, Path]]:
    normalized: list[tuple[str, Path]] = []
    names: set[str] = set()
    for value in values:
        if (
            not isinstance(value, tuple)
            or len(value) != 2
            or not isinstance(value[0], str)
            or not value[0]
            or value[0] in names
        ):
            raise EvidenceCaptureError(
                "EVIDENCE_ARTIFACT_INVALID",
                "Explicit evidence artifacts must have unique nonempty names.",
            )
        names.add(value[0])
        normalized.append((value[0], Path(value[1])))
    return normalized


def _artifact_path(value: str | os.PathLike[str] | Path, run: EvidenceRunManifest) -> Path:
    try:
        path = Path(value).expanduser().resolve(strict=True)
    except OSError as error:
        raise EvidenceCaptureError(
            "EVIDENCE_ARTIFACT_INVALID",
            "An evidence artifact path does not name an existing file.",
        ) from error
    if not path.is_file() or not _is_below(path, run.run_directory):
        raise EvidenceCaptureError(
            "EVIDENCE_ARTIFACT_INVALID",
            "Evidence artifacts must be existing files inside the exact run root.",
        )
    return path


def _is_below(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _json_result_value(document: dict[str, object], field: str) -> object:
    value: object = document
    for component in field.split("."):
        if not isinstance(value, dict) or component not in value:
            return None
        value = value[component]
    return value


def _load_capture_isolation(
    run: EvidenceRunManifest,
    *,
    key: str,
    result_kind: str,
    isolation_manifest: str | os.PathLike[str] | None,
) -> dict[str, object] | None:
    if isolation_manifest is None:
        return None
    path = Path(isolation_manifest).resolve(strict=True)
    if not _is_below(path, run.run_directory):
        raise EvidenceCaptureError(
            "EVIDENCE_ISOLATION_INVALID",
            "The suite isolation manifest must be inside the exact run root.",
        )
    try:
        payload = path.read_bytes()
        document = _strict_json_object(
            payload,
            (
                "schemaVersion",
                "manifestKind",
                "suiteKey",
                "runManifestPath",
                "runManifestSha256",
                "sandboxRoot",
                "databasePath",
                "settingsPath",
                "pdfRoot",
                "vaultRoot",
                "keyringRoot",
                "deniedLivePaths",
                "denyNetwork",
                "denyProviders",
                "liveAccessCount",
            ),
            "suite isolation manifest",
        )
    except (OSError, EvidenceCaptureError) as error:
        if isinstance(error, EvidenceCaptureError):
            raise EvidenceCaptureError("EVIDENCE_ISOLATION_INVALID", str(error)) from error
        raise EvidenceCaptureError("EVIDENCE_ISOLATION_INVALID", "The suite isolation manifest is unreadable.") from error
    roots = {
        "sandboxRoot": document["sandboxRoot"],
        "databasePath": document["databasePath"],
        "settingsPath": document["settingsPath"],
        "pdfRoot": document["pdfRoot"],
        "vaultRoot": document["vaultRoot"],
        "keyringRoot": document["keyringRoot"],
    }
    if (
        document["schemaVersion"] != 1
        or document["manifestKind"] != "suite-isolation"
        or document["suiteKey"] != key
        or document["runManifestPath"] != str(run.manifest_path)
        or document["runManifestSha256"] != run.manifest_file_sha256
        or document["denyNetwork"] is not True
        or document["denyProviders"] is not True
        or document["liveAccessCount"] != 0
        or any(
            not isinstance(value, str)
            or not _is_below(Path(value).resolve(strict=False), run.run_directory)
            for value in roots.values()
        )
    ):
        raise EvidenceCaptureError(
            "EVIDENCE_ISOLATION_INVALID",
            "The suite isolation manifest is not bound to this zero-access run.",
        )
    return {
        "isolationManifestPath": str(path),
        "isolationManifestSha256": hashlib.sha256(payload).hexdigest(),
        "isolationSuiteKey": document["suiteKey"],
        "isolationSandboxRoot": roots["sandboxRoot"],
        "isolationDatabasePath": roots["databasePath"],
        "isolationSettingsPath": roots["settingsPath"],
        "isolationPdfRoot": roots["pdfRoot"],
        "isolationVaultRoot": roots["vaultRoot"],
        "isolationKeyringRoot": roots["keyringRoot"],
        "liveAccessCount": document["liveAccessCount"],
    }


def _verify_process_snapshot(
    run: EvidenceRunManifest,
    *,
    cutover_lease: str | os.PathLike[str] | None,
    cutover_token_file: str | os.PathLike[str] | None,
    startup_snapshot: str | os.PathLike[str] | None,
    expected_startup_snapshot_sha256: str | None,
) -> dict[str, object]:
    prefix = "P6_PROVISIONAL" if run.phase == "provisional" else "P6_FINAL"
    expected = {
        f"{prefix}_EVIDENCE_RUN_MANIFEST_PATH": str(run.manifest_path),
        f"{prefix}_EVIDENCE_RUN_MANIFEST_SHA256": run.manifest_file_sha256,
        f"{prefix}_EVIDENCE_RUN_ID": run.run_id,
    }
    if any(os.environ.get(name) != value for name, value in expected.items()):
        raise EvidenceCaptureError(
            "EVIDENCE_PROCESS_SNAPSHOT_MISMATCH",
            "The evidence process snapshot does not match the run manifest.",
        )
    other = "P6_FINAL" if run.phase == "provisional" else "P6_PROVISIONAL"
    if any(name.startswith(other + "_") for name in os.environ):
        raise EvidenceCaptureError(
            "EVIDENCE_PROCESS_SNAPSHOT_MISMATCH",
            "Provisional and final evidence snapshots cannot coexist.",
        )
    capabilities = (
        cutover_lease,
        cutover_token_file,
        startup_snapshot,
        expected_startup_snapshot_sha256,
    )
    if run.phase == "provisional":
        if any(value is not None for value in capabilities):
            raise EvidenceCaptureError(
                "EVIDENCE_PROCESS_SNAPSHOT_MISMATCH",
                "Provisional capture cannot accept final-window capabilities.",
            )
        return {}
    if any(value is None for value in capabilities):
        raise EvidenceCaptureError(
            "EVIDENCE_PROCESS_SNAPSHOT_MISMATCH",
            "Final capture requires explicit lease, token, and startup snapshot bindings.",
        )
    assert cutover_lease is not None
    assert cutover_token_file is not None
    assert startup_snapshot is not None
    assert expected_startup_snapshot_sha256 is not None
    try:
        lease_path = Path(cutover_lease).resolve(strict=True)
        token_path = Path(cutover_token_file).resolve(strict=True)
        startup_path = Path(startup_snapshot).resolve(strict=True)
    except OSError as error:
        raise EvidenceCaptureError(
            "EVIDENCE_PROCESS_SNAPSHOT_MISMATCH",
            "A final-window capability path does not exist.",
        ) from error
    if not lease_path.is_file() or not token_path.is_file() or not startup_path.is_file():
        raise EvidenceCaptureError(
            "EVIDENCE_PROCESS_SNAPSHOT_MISMATCH",
            "Final-window capabilities must be files.",
        )
    if startup_path.parent != run.run_directory:
        raise EvidenceCaptureError(
            "EVIDENCE_RUN_PATH_INVALID",
            "The production startup snapshot must be inside the exact run root.",
        )
    startup_sha = _required_sha256(expected_startup_snapshot_sha256)
    if _file_sha256(startup_path) != startup_sha:
        raise EvidenceCaptureError(
            "EVIDENCE_PROCESS_SNAPSHOT_MISMATCH",
            "The production startup snapshot hash does not match.",
        )
    final_expected = {
        "P6_FINAL_WINDOW_LEASE_PATH": str(lease_path),
        "P6_FINAL_WINDOW_TOKEN_FILE": str(token_path),
        "P6_PRODUCTION_STARTUP_SNAPSHOT_PATH": str(startup_path),
        "P6_PRODUCTION_STARTUP_SNAPSHOT_SHA256": startup_sha,
    }
    if any(os.environ.get(name) != value for name, value in final_expected.items()):
        raise EvidenceCaptureError(
            "EVIDENCE_PROCESS_SNAPSHOT_MISMATCH",
            "The final-window capability snapshot does not match explicit arguments.",
        )
    return {
        "cutoverLeasePath": str(lease_path),
        "cutoverLeaseSha256": _file_sha256(lease_path),
        "cutoverTokenFilePath": str(token_path),
        "cutoverTokenSha256": _file_sha256(token_path),
        "startupSnapshotPath": str(startup_path),
        "startupSnapshotSha256": startup_sha,
    }


def _new_run_path(
    value: str | os.PathLike[str],
    run: EvidenceRunManifest,
    *,
    label: str,
) -> Path:
    path = Path(value).resolve(strict=False)
    if path.parent.resolve(strict=True) != run.run_directory or path.exists():
        raise EvidenceCaptureError(
            "EVIDENCE_RUN_PATH_INVALID",
            f"The {label} must be a new file directly inside the run directory.",
        )
    return path


def _load_machine_summary(
    path: Path,
    raw_exit: int,
    *,
    run_root: Path,
) -> dict[str, int | str]:
    if not path.is_file():
        raise EvidenceCaptureError(
            "EVIDENCE_SUMMARY_MISSING",
            "The child did not create its machine summary.",
        )
    payload = path.read_bytes()
    try:
        document = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise EvidenceCaptureError("EVIDENCE_SUMMARY_INVALID", "The machine summary is invalid.") from error
    if not isinstance(document, dict):
        raise EvidenceCaptureError("EVIDENCE_SUMMARY_INVALID", "The machine summary must be an object.")
    typed_fields = (
        "schemaVersion", "manifestKind", "adapter", "rawExit", "totals",
        "failures", "skips", "resultArtifactPath", "resultArtifactFormat",
    )
    fields = tuple(document)
    if fields != typed_fields:
        raise EvidenceCaptureError("EVIDENCE_SUMMARY_INVALID", "The machine summary schema is invalid.")
    if (
        document["schemaVersion"] != 1
        or not isinstance(document["adapter"], str)
        or document["adapter"] not in {
        "unittest",
        "node-test",
        "vitest",
        "playwright",
        "check",
        }
    ):
        raise EvidenceCaptureError(
            "EVIDENCE_SUMMARY_INVALID",
            "The machine summary adapter is invalid.",
        )
    if document["manifestKind"] != "machine-summary" or document["rawExit"] != raw_exit:
        raise EvidenceCaptureError(
            "EVIDENCE_SUMMARY_INVALID",
            "The machine summary contradicts the raw child exit.",
        )
    result_format = document["resultArtifactFormat"]
    if not isinstance(result_format, str):
        raise EvidenceCaptureError(
            "EVIDENCE_SUMMARY_INVALID",
            "The result artifact format is invalid.",
        )
    result_path_value = document["resultArtifactPath"]
    adapter = document["adapter"]
    if adapter == "check":
        if result_format != "raw-exit" or result_path_value is not None:
            raise EvidenceCaptureError(
                "EVIDENCE_SUMMARY_INVALID",
                "The check summary must use a null raw-exit artifact binding.",
            )
    else:
        if result_format not in {"json", "junit-xml"} or not isinstance(
            result_path_value, str
        ):
            raise EvidenceCaptureError(
                "EVIDENCE_SUMMARY_INVALID",
                "The structured result artifact binding is invalid.",
            )
        try:
            result_path = Path(result_path_value).resolve(strict=True)
        except OSError as error:
            raise EvidenceCaptureError(
                "EVIDENCE_SUMMARY_INVALID",
                "The structured result artifact does not exist.",
            ) from error
        if not result_path.is_file() or not _is_below(result_path, run_root):
            raise EvidenceCaptureError(
                "EVIDENCE_SUMMARY_INVALID",
                "The structured result artifact must be inside the exact run root.",
            )
    values: dict[str, int | str] = {}
    for field in ("totals", "failures", "skips"):
        value = document[field]
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise EvidenceCaptureError(
                "EVIDENCE_SUMMARY_INVALID",
                "Machine summary counts must be nonnegative integers.",
            )
        values[field] = value
    if values["failures"] > values["totals"] or values["skips"] > values["totals"]:
        raise EvidenceCaptureError(
            "EVIDENCE_SUMMARY_INVALID",
            "Machine summary counts are inconsistent.",
        )
    if (raw_exit == 0) != (values["failures"] == 0):
        raise EvidenceCaptureError(
            "EVIDENCE_SUMMARY_INVALID",
            "The machine summary failures contradict the raw child exit.",
        )
    values["format"] = str(result_format)
    return values


def _load_json_cli_result(payload: bytes) -> dict[str, object]:
    try:
        document = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise EvidenceCaptureError(
            "EVIDENCE_JSON_RESULT_INVALID",
            "The successful JSON CLI child did not return one JSON object.",
        ) from error
    if not isinstance(document, dict):
        raise EvidenceCaptureError(
            "EVIDENCE_JSON_RESULT_INVALID",
            "The successful JSON CLI child result must be an object.",
        )
    return document


def _strict_json_object(
    payload: bytes,
    fields: tuple[str, ...],
    label: str,
    *,
    require_canonical: bool = True,
) -> dict[str, object]:
    duplicates: list[str] = []

    def pairs(values: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in values:
            if key in result:
                duplicates.append(key)
            result[key] = value
        return result

    try:
        document = json.loads(payload.decode("utf-8"), object_pairs_hook=pairs)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise EvidenceCaptureError(
            "EVIDENCE_JSON_INVALID",
            f"The {label} is not valid JSON.",
        ) from error
    if (
        duplicates
        or not isinstance(document, dict)
        or tuple(document) != fields
        or (require_canonical and canonical_json_bytes(document) != payload)
    ):
        raise EvidenceCaptureError(
            "EVIDENCE_JSON_INVALID",
            f"The {label} schema or serialization is invalid.",
        )
    return document


def _required_string(value: object) -> str:
    if not isinstance(value, str) or not value:
        raise EvidenceCaptureError("EVIDENCE_RUN_INVALID", "A run field is invalid.")
    return value


def _required_sha256(value: object) -> str:
    text = _required_string(value)
    if len(text) != 64 or any(character not in "0123456789abcdef" for character in text):
        raise EvidenceCaptureError("EVIDENCE_RUN_INVALID", "A run hash is invalid.")
    return text


def _reject_secret_argv(argv: Sequence[str]) -> None:
    forbidden = ("api-key", "secret", "credential", "token-value", "password")
    option_names = (
        value.partition("=")[0].casefold()
        for value in argv
        if value.startswith("-")
    )
    if any(any(fragment in option for fragment in forbidden) for option in option_names):
        raise EvidenceCaptureError(
            "EVIDENCE_ARGV_SECRET_REJECTED",
            "The evidence child argv contains a secret-bearing flag.",
        )


def _instant(clock: Callable[[], datetime] | None) -> str:
    value = (clock or (lambda: datetime.now(timezone.utc)))()
    if value.tzinfo is None or value.utcoffset() is None:
        raise EvidenceCaptureError(
            "EVIDENCE_TIMESTAMP_INVALID",
            "Evidence timestamps must be timezone-aware.",
        )
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _write_reserved(descriptor: int, payload: bytes) -> None:
    try:
        view = memoryview(payload)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise OSError("reserved evidence write made no progress")
            view = view[written:]
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _seal_failed_run(
    run: EvidenceRunManifest,
    *,
    reason: str,
    record_path: Path,
) -> None:
    output = run.run_directory / "failure-seal-v1.json"
    unsigned = {
        "schemaVersion": 1,
        "sealKind": "evidence-run-failure",
        "runId": run.run_id,
        "reason": reason,
        "recordPath": str(record_path),
    }
    payload = canonical_json_bytes(
        {**unsigned, "sealSha256": hashlib.sha256(canonical_json_bytes(unsigned)).hexdigest()}
    )
    try:
        exclusive_write_bytes(output, payload)
    except DatabaseIdentityError as error:
        if error.code != "EVIDENCE_OUTPUT_EXISTS":
            raise EvidenceCaptureError(error.code, str(error)) from error


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


__all__ = [
    "EvidenceCaptureRecord",
    "EvidenceCaptureError",
    "EvidenceChildFailure",
    "EvidenceRunManifest",
    "capture_evidence",
    "create_evidence_run",
    "load_evidence_run_manifest",
]
