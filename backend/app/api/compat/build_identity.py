from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
from typing import Callable, Mapping, Sequence

from backend.app.api.compat.database_identity import (
    DatabaseIdentityError,
    canonical_json_bytes,
    exclusive_write_bytes,
)


_MANIFEST_FIELDS = (
    "schemaVersion",
    "manifestKind",
    "buildId",
    "gitRevision",
    "dirty",
    "sourceTreeHash",
    "sourceEntries",
    "buildArtifactHash",
    "pythonArtifacts",
    "frontendArtifacts",
    "resolvedComposeSha256",
    "imageDigests",
    "generatedAt",
)


class BuildIdentityError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class BuildIdentityManifest:
    build_id: str
    manifest_path: Path
    manifest_file_sha256: str
    git_revision: str
    dirty: bool
    source_tree_hash: str
    build_artifact_hash: str
    canonical_bytes: bytes


def compute_source_tree_hash(repository: str | os.PathLike[str]) -> str:
    document = {"schemaVersion": 1, "sourceEntries": _source_entries(Path(repository))}
    return hashlib.sha256(canonical_json_bytes(document)).hexdigest()


def compute_build_artifact_hash(
    *,
    python_artifacts: Sequence[str | os.PathLike[str]],
    frontend_root: str | os.PathLike[str],
    frontend_manifest: str | os.PathLike[str],
    resolved_compose: str | os.PathLike[str],
    image_digests: Mapping[str, str],
) -> str:
    document = _build_artifact_document(
        python_artifacts=python_artifacts,
        frontend_root=frontend_root,
        frontend_manifest=frontend_manifest,
        resolved_compose=resolved_compose,
        image_digests=image_digests,
    )
    return hashlib.sha256(canonical_json_bytes(document)).hexdigest()


def freeze_build_identity(
    *,
    repository: str | os.PathLike[str],
    build_identity_directory: str | os.PathLike[str],
    python_artifacts: Sequence[str | os.PathLike[str]],
    frontend_root: str | os.PathLike[str],
    frontend_manifest: str | os.PathLike[str],
    resolved_compose: str | os.PathLike[str],
    image_digests: Mapping[str, str],
    clock: Callable[[], datetime] | None = None,
) -> BuildIdentityManifest:
    root = Path(repository).resolve(strict=True)
    source_entries = _source_entries(root)
    source_document = {"schemaVersion": 1, "sourceEntries": source_entries}
    source_tree_hash = hashlib.sha256(canonical_json_bytes(source_document)).hexdigest()
    artifact_document = _build_artifact_document(
        python_artifacts=python_artifacts,
        frontend_root=frontend_root,
        frontend_manifest=frontend_manifest,
        resolved_compose=resolved_compose,
        image_digests=image_digests,
    )
    build_artifact_hash = hashlib.sha256(
        canonical_json_bytes(artifact_document)
    ).hexdigest()
    git_revision = _git_revision(root)
    dirty = _git_dirty(root)
    unsigned = {
        "schemaVersion": 1,
        "manifestKind": "build",
        "gitRevision": git_revision,
        "dirty": dirty,
        "sourceTreeHash": source_tree_hash,
        "sourceEntries": source_entries,
        "buildArtifactHash": build_artifact_hash,
        "pythonArtifacts": artifact_document["pythonArtifacts"],
        "frontendArtifacts": artifact_document["frontendArtifacts"],
        "resolvedComposeSha256": artifact_document["resolvedComposeSha256"],
        "imageDigests": artifact_document["imageDigests"],
    }
    build_id = hashlib.sha256(canonical_json_bytes(unsigned)).hexdigest()
    instant = (clock or (lambda: datetime.now(timezone.utc)))()
    if instant.tzinfo is None or instant.utcoffset() is None:
        raise BuildIdentityError(
            "BUILD_TIMESTAMP_INVALID",
            "The build identity timestamp must be timezone-aware.",
        )
    document = {
        "schemaVersion": 1,
        "manifestKind": "build",
        "buildId": build_id,
        "gitRevision": git_revision,
        "dirty": dirty,
        "sourceTreeHash": source_tree_hash,
        "sourceEntries": source_entries,
        "buildArtifactHash": build_artifact_hash,
        "pythonArtifacts": artifact_document["pythonArtifacts"],
        "frontendArtifacts": artifact_document["frontendArtifacts"],
        "resolvedComposeSha256": artifact_document["resolvedComposeSha256"],
        "imageDigests": artifact_document["imageDigests"],
        "generatedAt": instant.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
    }
    payload = canonical_json_bytes(document)
    directory = Path(build_identity_directory).resolve(strict=True)
    if not directory.is_dir():
        raise BuildIdentityError(
            "BUILD_IDENTITY_DIRECTORY_INVALID",
            "The build identity directory is not a directory.",
        )
    output = directory / f"frozen-build-identity-{build_id}.json"
    try:
        exclusive_write_bytes(output, payload)
    except DatabaseIdentityError as error:
        if error.code != "EVIDENCE_OUTPUT_EXISTS":
            raise BuildIdentityError(error.code, str(error)) from error
        existing = load_build_identity_manifest(output)
        if _identity_document(existing.canonical_bytes) != unsigned:
            raise BuildIdentityError(
                "BUILD_IDENTITY_CONFLICT",
                "The content-addressed build identity path contains another payload.",
            )
        return existing
    return load_build_identity_manifest(output)


def load_build_identity_manifest(
    manifest: str | os.PathLike[str],
) -> BuildIdentityManifest:
    path = Path(manifest).resolve(strict=True)
    payload = path.read_bytes()
    document = _strict_manifest_document(payload)
    unsigned = _identity_document(payload)
    build_id = hashlib.sha256(canonical_json_bytes(unsigned)).hexdigest()
    if document["buildId"] != build_id:
        raise BuildIdentityError(
            "BUILD_IDENTITY_INVALID",
            "The build identity self hash is invalid.",
        )
    if path.name != f"frozen-build-identity-{build_id}.json":
        raise BuildIdentityError(
            "BUILD_IDENTITY_PATH_INVALID",
            "The build identity path is not content-addressed.",
        )
    return BuildIdentityManifest(
        build_id=build_id,
        manifest_path=path,
        manifest_file_sha256=hashlib.sha256(payload).hexdigest(),
        git_revision=_required_hex(document["gitRevision"], widths=(40, 64)),
        dirty=_required_bool(document["dirty"]),
        source_tree_hash=_required_hex(document["sourceTreeHash"], widths=(64,)),
        build_artifact_hash=_required_hex(
            document["buildArtifactHash"], widths=(64,)
        ),
        canonical_bytes=payload,
    )


def verify_build_identity(
    *,
    build_identity_manifest: str | os.PathLike[str],
    repository: str | os.PathLike[str],
    python_artifacts: Sequence[str | os.PathLike[str]],
    frontend_root: str | os.PathLike[str],
    frontend_manifest: str | os.PathLike[str],
    resolved_compose: str | os.PathLike[str],
    image_digests: Mapping[str, str],
) -> BuildIdentityManifest:
    frozen = load_build_identity_manifest(build_identity_manifest)
    root = Path(repository).resolve(strict=True)
    source_entries = _source_entries(root)
    source_tree_hash = hashlib.sha256(
        canonical_json_bytes({"schemaVersion": 1, "sourceEntries": source_entries})
    ).hexdigest()
    artifacts = _build_artifact_document(
        python_artifacts=python_artifacts,
        frontend_root=frontend_root,
        frontend_manifest=frontend_manifest,
        resolved_compose=resolved_compose,
        image_digests=image_digests,
    )
    current = {
        "schemaVersion": 1,
        "manifestKind": "build",
        "gitRevision": _git_revision(root),
        "dirty": _git_dirty(root),
        "sourceTreeHash": source_tree_hash,
        "sourceEntries": source_entries,
        "buildArtifactHash": hashlib.sha256(canonical_json_bytes(artifacts)).hexdigest(),
        "pythonArtifacts": artifacts["pythonArtifacts"],
        "frontendArtifacts": artifacts["frontendArtifacts"],
        "resolvedComposeSha256": artifacts["resolvedComposeSha256"],
        "imageDigests": artifacts["imageDigests"],
    }
    if current != _identity_document(frozen.canonical_bytes):
        raise BuildIdentityError(
            "BUILD_IDENTITY_DRIFT",
            "The source tree or deployed build artifacts drifted from the frozen identity.",
        )
    return frozen


def _identity_document(payload: bytes) -> dict[str, object]:
    document = _strict_manifest_document(payload)
    return {
        "schemaVersion": document["schemaVersion"],
        "manifestKind": document["manifestKind"],
        "gitRevision": document["gitRevision"],
        "dirty": document["dirty"],
        "sourceTreeHash": document["sourceTreeHash"],
        "sourceEntries": document["sourceEntries"],
        "buildArtifactHash": document["buildArtifactHash"],
        "pythonArtifacts": document["pythonArtifacts"],
        "frontendArtifacts": document["frontendArtifacts"],
        "resolvedComposeSha256": document["resolvedComposeSha256"],
        "imageDigests": document["imageDigests"],
    }


def _strict_manifest_document(payload: bytes) -> dict[str, object]:
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
        raise BuildIdentityError(
            "BUILD_IDENTITY_INVALID",
            "The build identity is not valid canonical JSON.",
        ) from error
    if (
        duplicates
        or not isinstance(document, dict)
        or tuple(document) != _MANIFEST_FIELDS
        or canonical_json_bytes(document) != payload
        or document["schemaVersion"] != 1
        or document["manifestKind"] != "build"
    ):
        raise BuildIdentityError(
            "BUILD_IDENTITY_INVALID",
            "The build identity schema or canonical serialization is invalid.",
        )
    _required_hex(document["buildId"], widths=(64,))
    return document


def _required_hex(value: object, *, widths: tuple[int, ...]) -> str:
    if (
        not isinstance(value, str)
        or len(value) not in widths
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise BuildIdentityError(
            "BUILD_IDENTITY_INVALID",
            "A build identity hexadecimal field is invalid.",
        )
    return value


def _required_bool(value: object) -> bool:
    if not isinstance(value, bool):
        raise BuildIdentityError(
            "BUILD_IDENTITY_INVALID",
            "The build identity dirty field must be boolean.",
        )
    return value


def _git_revision(repository: Path) -> str:
    revision = _git_bytes(repository, "rev-parse", "--verify", "HEAD").strip()
    try:
        return _required_hex(revision.decode("ascii"), widths=(40, 64))
    except UnicodeDecodeError as error:
        raise BuildIdentityError(
            "BUILD_GIT_REVISION_INVALID",
            "The Git revision is invalid.",
        ) from error


def _git_dirty(repository: Path) -> bool:
    return bool(
        _git_bytes(
            repository,
            "status",
            "--porcelain=v1",
            "-z",
            "--untracked-files=all",
        )
    )


def _build_artifact_document(
    *,
    python_artifacts: Sequence[str | os.PathLike[str]],
    frontend_root: str | os.PathLike[str],
    frontend_manifest: str | os.PathLike[str],
    resolved_compose: str | os.PathLike[str],
    image_digests: Mapping[str, str],
) -> dict[str, object]:
    python_entries = [
        {"name": path.name, "sha256": _file_sha256(path)}
        for path in sorted(
            (Path(value).resolve(strict=True) for value in python_artifacts),
            key=lambda item: item.name,
        )
    ]
    if not python_entries or len({entry["name"] for entry in python_entries}) != len(
        python_entries
    ):
        raise BuildIdentityError(
            "BUILD_PYTHON_ARTIFACTS_INVALID",
            "Python build artifacts must have unique names.",
        )

    root = Path(frontend_root).resolve(strict=True)
    manifest_path = Path(frontend_manifest).resolve(strict=True)
    _require_contained(manifest_path, root)
    try:
        manifest_document = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise BuildIdentityError(
            "BUILD_FRONTEND_MANIFEST_INVALID",
            "The frontend asset manifest is invalid.",
        ) from error
    if not isinstance(manifest_document, dict):
        raise BuildIdentityError(
            "BUILD_FRONTEND_MANIFEST_INVALID",
            "The frontend asset manifest must be an object.",
        )
    referenced = sorted(_frontend_references(manifest_document))
    frontend_entries = [
        {
            "path": relative,
            "sha256": _file_sha256(_contained_frontend_path(root, relative)),
        }
        for relative in referenced
    ]
    manifest_relative = manifest_path.relative_to(root).as_posix()
    frontend_entries.insert(
        0,
        {"path": manifest_relative, "sha256": _file_sha256(manifest_path)},
    )

    digest_entries: list[dict[str, str]] = []
    for name, digest in sorted(image_digests.items()):
        if (
            not isinstance(name, str)
            or not name.strip()
            or not isinstance(digest, str)
            or not digest.startswith("sha256:")
            or len(digest) != 71
            or any(character not in "0123456789abcdef" for character in digest[7:])
        ):
            raise BuildIdentityError(
                "BUILD_IMAGE_DIGEST_INVALID",
                "Container image digests must be named lowercase SHA-256 values.",
            )
        digest_entries.append({"name": name, "digest": digest})
    if not digest_entries:
        raise BuildIdentityError(
            "BUILD_IMAGE_DIGEST_INVALID",
            "At least one container image digest is required.",
        )
    compose_path = Path(resolved_compose).resolve(strict=True)
    return {
        "schemaVersion": 1,
        "pythonArtifacts": python_entries,
        "frontendArtifacts": frontend_entries,
        "resolvedComposeSha256": _file_sha256(compose_path),
        "imageDigests": digest_entries,
    }


def _frontend_references(document: object) -> set[str]:
    found: set[str] = set()
    if isinstance(document, dict):
        for key, value in document.items():
            if key == "file" and isinstance(value, str):
                found.add(value)
            elif key in {"css", "assets"} and isinstance(value, list):
                if not all(isinstance(item, str) for item in value):
                    raise BuildIdentityError(
                        "BUILD_FRONTEND_MANIFEST_INVALID",
                        "Frontend asset references must be strings.",
                    )
                found.update(value)
            else:
                found.update(_frontend_references(value))
    elif isinstance(document, list):
        for value in document:
            found.update(_frontend_references(value))
    return found


def _contained_frontend_path(root: Path, relative: str) -> Path:
    if not relative or Path(relative).is_absolute() or ".." in Path(relative).parts:
        raise BuildIdentityError(
            "BUILD_FRONTEND_PATH_INVALID",
            "A frontend asset path is outside the build root.",
        )
    candidate = root.joinpath(*relative.replace("\\", "/").split("/")).resolve(
        strict=True
    )
    _require_contained(candidate, root)
    return candidate


def _require_contained(candidate: Path, root: Path) -> None:
    try:
        candidate.relative_to(root)
    except ValueError as error:
        raise BuildIdentityError(
            "BUILD_FRONTEND_PATH_INVALID",
            "A frontend build path is outside the build root.",
        ) from error


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
    except OSError as error:
        raise BuildIdentityError(
            "BUILD_ARTIFACT_UNREADABLE",
            "A build artifact could not be read.",
        ) from error
    return digest.hexdigest()


def _source_entries(repository: Path) -> list[dict[str, str]]:
    root = repository.resolve(strict=True)
    paths = _git_bytes(
        root,
        "ls-files",
        "--cached",
        "--others",
        "--exclude-standard",
        "-z",
    ).split(b"\0")
    tracked_modes = _tracked_modes(root)
    entries: list[dict[str, str]] = []
    for relative_bytes in sorted(path for path in paths if path):
        relative = _decode_git_path(relative_bytes)
        if _excluded(relative):
            continue
        absolute = root.joinpath(*relative.split("/"))
        tracked_mode = tracked_modes.get(relative_bytes)
        if tracked_mode == "120000" or absolute.is_symlink():
            mode = "symlink"
            content = os.readlink(absolute)
            payload = os.fsencode(content)
        else:
            try:
                metadata = absolute.stat()
                payload = absolute.read_bytes()
            except FileNotFoundError as error:
                raise BuildIdentityError(
                    "BUILD_SOURCE_MISSING",
                    "A source path disappeared while the source identity was captured.",
                ) from error
            mode = (
                "executable"
                if tracked_mode == "100755" or metadata.st_mode & stat.S_IXUSR
                else "regular"
            )
        entries.append(
            {
                "path": relative,
                "mode": mode,
                "sha256": hashlib.sha256(payload).hexdigest(),
            }
        )
    return entries


def _tracked_modes(repository: Path) -> dict[bytes, str]:
    records = _git_bytes(repository, "ls-files", "--stage", "-z").split(b"\0")
    modes: dict[bytes, str] = {}
    for record in records:
        if not record:
            continue
        prefix, separator, relative = record.partition(b"\t")
        fields = prefix.split()
        if not separator or len(fields) != 3:
            raise BuildIdentityError(
                "BUILD_GIT_OUTPUT_INVALID",
                "Git returned an invalid staged source entry.",
            )
        modes[relative] = fields[0].decode("ascii")
    return modes


def _git_bytes(repository: Path, *arguments: str) -> bytes:
    completed = subprocess.run(
        ["git", "-C", str(repository), *arguments],
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        raise BuildIdentityError(
            "BUILD_GIT_COMMAND_FAILED",
            "Git could not enumerate the source tree.",
        )
    return completed.stdout


def _decode_git_path(value: bytes) -> str:
    try:
        return value.decode("utf-8").replace("\\", "/")
    except UnicodeDecodeError as error:
        raise BuildIdentityError(
            "BUILD_SOURCE_PATH_INVALID",
            "A source path is not valid UTF-8.",
        ) from error


def _excluded(relative: str) -> bool:
    parts = relative.split("/")
    if any(part in {".git", ".venv", "node_modules", "__pycache__", ".pytest_cache"} for part in parts):
        return True
    if relative.startswith(("data/compatibility/preflight/", "data/compatibility/evidence/", "data/compatibility/runtime/", "data/backups/")):
        return True
    return relative in {"data/app.db", "data/app.db-wal", "data/app.db-shm"}


__all__ = [
    "BuildIdentityManifest",
    "BuildIdentityError",
    "compute_build_artifact_hash",
    "compute_source_tree_hash",
    "freeze_build_identity",
    "load_build_identity_manifest",
    "verify_build_identity",
]
