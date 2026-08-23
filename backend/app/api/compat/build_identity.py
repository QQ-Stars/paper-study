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


_CONTAINER_MANIFEST_FIELDS = (
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
_NATIVE_MANIFEST_FIELDS = (
    "schemaVersion",
    "manifestKind",
    "buildId",
    "deploymentKind",
    "gitRevision",
    "dirty",
    "sourceTreeHash",
    "sourceEntries",
    "buildArtifactHash",
    "pythonArtifacts",
    "frontendArtifacts",
    "nativeRuntime",
    "generatedAt",
)
_NATIVE_ROLES = ("api", "worker", "scheduler", "mcp")


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
    deployment_kind: str
    canonical_bytes: bytes


def compute_source_tree_hash(repository: str | os.PathLike[str]) -> str:
    document = {"schemaVersion": 1, "sourceEntries": _source_entries(Path(repository))}
    return hashlib.sha256(canonical_json_bytes(document)).hexdigest()


def compute_build_artifact_hash(
    *,
    python_artifacts: Sequence[str | os.PathLike[str]],
    frontend_root: str | os.PathLike[str],
    frontend_manifest: str | os.PathLike[str],
    resolved_compose: str | os.PathLike[str] | None = None,
    image_digests: Mapping[str, str] | None = None,
    deployment_kind: str = "container",
    native_runtime_spec: str | os.PathLike[str] | None = None,
) -> str:
    document = _build_artifact_document(
        python_artifacts=python_artifacts,
        frontend_root=frontend_root,
        frontend_manifest=frontend_manifest,
        resolved_compose=resolved_compose,
        image_digests=image_digests,
        deployment_kind=deployment_kind,
        native_runtime_spec=native_runtime_spec,
    )
    return hashlib.sha256(canonical_json_bytes(document)).hexdigest()


def freeze_build_identity(
    *,
    repository: str | os.PathLike[str],
    build_identity_directory: str | os.PathLike[str],
    python_artifacts: Sequence[str | os.PathLike[str]],
    frontend_root: str | os.PathLike[str],
    frontend_manifest: str | os.PathLike[str],
    resolved_compose: str | os.PathLike[str] | None = None,
    image_digests: Mapping[str, str] | None = None,
    deployment_kind: str = "container",
    native_runtime_spec: str | os.PathLike[str] | None = None,
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
        deployment_kind=deployment_kind,
        native_runtime_spec=native_runtime_spec,
    )
    build_artifact_hash = hashlib.sha256(
        canonical_json_bytes(artifact_document)
    ).hexdigest()
    git_revision = _git_revision(root)
    dirty = _git_dirty(root)
    unsigned = _unsigned_build_document(
        git_revision=git_revision,
        dirty=dirty,
        source_tree_hash=source_tree_hash,
        source_entries=source_entries,
        build_artifact_hash=build_artifact_hash,
        artifact_document=artifact_document,
    )
    build_id = hashlib.sha256(canonical_json_bytes(unsigned)).hexdigest()
    instant = (clock or (lambda: datetime.now(timezone.utc)))()
    if instant.tzinfo is None or instant.utcoffset() is None:
        raise BuildIdentityError(
            "BUILD_TIMESTAMP_INVALID",
            "The build identity timestamp must be timezone-aware.",
        )
    document = {
        **{key: value for key, value in unsigned.items() if key != "manifestKind"},
    }
    document = {
        "schemaVersion": document.pop("schemaVersion"),
        "manifestKind": "build",
        "buildId": build_id,
        **document,
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
        deployment_kind=str(document.get("deploymentKind", "container")),
        canonical_bytes=payload,
    )


def verify_build_identity(
    *,
    build_identity_manifest: str | os.PathLike[str],
    repository: str | os.PathLike[str],
    python_artifacts: Sequence[str | os.PathLike[str]],
    frontend_root: str | os.PathLike[str],
    frontend_manifest: str | os.PathLike[str],
    resolved_compose: str | os.PathLike[str] | None = None,
    image_digests: Mapping[str, str] | None = None,
    deployment_kind: str = "container",
    native_runtime_spec: str | os.PathLike[str] | None = None,
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
        deployment_kind=deployment_kind,
        native_runtime_spec=native_runtime_spec,
    )
    current = _unsigned_build_document(
        git_revision=_git_revision(root),
        dirty=_git_dirty(root),
        source_tree_hash=source_tree_hash,
        source_entries=source_entries,
        build_artifact_hash=hashlib.sha256(canonical_json_bytes(artifacts)).hexdigest(),
        artifact_document=artifacts,
    )
    if current != _identity_document(frozen.canonical_bytes):
        raise BuildIdentityError(
            "BUILD_IDENTITY_DRIFT",
            "The source tree or deployed build artifacts drifted from the frozen identity.",
        )
    return frozen


def verify_native_runtime_spec(
    *,
    build_identity_manifest: str | os.PathLike[str],
    native_runtime_spec: str | os.PathLike[str],
    require_frozen_node_executable: bool = True,
) -> BuildIdentityManifest:
    frozen = load_build_identity_manifest(build_identity_manifest)
    if frozen.deployment_kind != "native-windows":
        raise BuildIdentityError(
            "BUILD_DEPLOYMENT_KIND_MISMATCH",
            "A native runtime requires a native-windows build identity.",
        )
    document = _strict_manifest_document(frozen.canonical_bytes)
    frozen_runtime = document["nativeRuntime"]
    frozen_node_executable_sha256: str | None = None
    if not require_frozen_node_executable:
        if not isinstance(frozen_runtime, dict):
            raise BuildIdentityError(
                "BUILD_IDENTITY_INVALID",
                "The frozen native runtime identity is invalid.",
            )
        frozen_node_rollback = frozen_runtime.get("frozenNodeRollback")
        if not isinstance(frozen_node_rollback, dict):
            raise BuildIdentityError(
                "BUILD_IDENTITY_INVALID",
                "The frozen native runtime identity is invalid.",
            )
        frozen_node_executable_sha256 = _required_hex(
            frozen_node_rollback.get("executableSha256"),
            widths=(64,),
        )
    if frozen_runtime != _native_runtime_document(
        native_runtime_spec,
        frozen_node_executable_sha256=frozen_node_executable_sha256,
    ):
        raise BuildIdentityError(
            "BUILD_IDENTITY_DRIFT",
            "The native runtime specification drifted from the frozen identity.",
        )
    return frozen


def _identity_document(payload: bytes) -> dict[str, object]:
    document = _strict_manifest_document(payload)
    common = {
        "schemaVersion": document["schemaVersion"],
        "manifestKind": document["manifestKind"],
    }
    if document["schemaVersion"] == 2:
        common["deploymentKind"] = document["deploymentKind"]
    common.update({
        "gitRevision": document["gitRevision"],
        "dirty": document["dirty"],
        "sourceTreeHash": document["sourceTreeHash"],
        "sourceEntries": document["sourceEntries"],
        "buildArtifactHash": document["buildArtifactHash"],
        "pythonArtifacts": document["pythonArtifacts"],
        "frontendArtifacts": document["frontendArtifacts"],
    })
    if document["schemaVersion"] == 1:
        common["resolvedComposeSha256"] = document["resolvedComposeSha256"]
        common["imageDigests"] = document["imageDigests"]
    else:
        common["nativeRuntime"] = document["nativeRuntime"]
    return common


def _unsigned_build_document(
    *,
    git_revision: str,
    dirty: bool,
    source_tree_hash: str,
    source_entries: list[dict[str, str]],
    build_artifact_hash: str,
    artifact_document: Mapping[str, object],
) -> dict[str, object]:
    common = {
        "schemaVersion": artifact_document["schemaVersion"],
        "manifestKind": "build",
    }
    if artifact_document["schemaVersion"] == 2:
        common["deploymentKind"] = artifact_document["deploymentKind"]
    common.update(
        {
            "gitRevision": git_revision,
            "dirty": dirty,
            "sourceTreeHash": source_tree_hash,
            "sourceEntries": source_entries,
            "buildArtifactHash": build_artifact_hash,
            "pythonArtifacts": artifact_document["pythonArtifacts"],
            "frontendArtifacts": artifact_document["frontendArtifacts"],
        }
    )
    if artifact_document["schemaVersion"] == 1:
        common["resolvedComposeSha256"] = artifact_document["resolvedComposeSha256"]
        common["imageDigests"] = artifact_document["imageDigests"]
    else:
        common["nativeRuntime"] = artifact_document["nativeRuntime"]
    return common


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
        or tuple(document)
        != (
            _CONTAINER_MANIFEST_FIELDS
            if document.get("schemaVersion") == 1
            else _NATIVE_MANIFEST_FIELDS
        )
        or canonical_json_bytes(document) != payload
        or document["schemaVersion"] not in {1, 2}
        or document["manifestKind"] != "build"
        or document.get("deploymentKind", "container")
        != ("container" if document["schemaVersion"] == 1 else "native-windows")
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
    resolved_compose: str | os.PathLike[str] | None,
    image_digests: Mapping[str, str] | None,
    deployment_kind: str,
    native_runtime_spec: str | os.PathLike[str] | None,
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

    if deployment_kind == "native-windows":
        if resolved_compose is not None or image_digests is not None:
            raise BuildIdentityError(
                "BUILD_DEPLOYMENT_INPUT_INVALID",
                "Native build identity cannot contain Compose or image inputs.",
            )
        if native_runtime_spec is None:
            raise BuildIdentityError(
                "BUILD_NATIVE_RUNTIME_INVALID",
                "Native build identity requires an exact runtime specification.",
            )
        return {
            "schemaVersion": 2,
            "deploymentKind": "native-windows",
            "pythonArtifacts": python_entries,
            "frontendArtifacts": frontend_entries,
            "nativeRuntime": _native_runtime_document(native_runtime_spec),
        }
    if deployment_kind != "container" or native_runtime_spec is not None:
        raise BuildIdentityError(
            "BUILD_DEPLOYMENT_INPUT_INVALID",
            "Build deployment kind or adapter inputs are invalid.",
        )
    if resolved_compose is None or image_digests is None:
        raise BuildIdentityError(
            "BUILD_CONTAINER_RUNTIME_INVALID",
            "Container build identity requires Compose and image digests.",
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


def _native_runtime_document(
    native_runtime_spec: str | os.PathLike[str],
    *,
    frozen_node_executable_sha256: str | None = None,
) -> dict[str, object]:
    spec_path = Path(native_runtime_spec).resolve(strict=True)
    try:
        document = json.loads(spec_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise BuildIdentityError(
            "BUILD_NATIVE_RUNTIME_INVALID",
            "The native runtime specification is not valid JSON.",
        ) from error
    expected = {
        "schemaVersion",
        "deploymentKind",
        "pythonExecutablePath",
        "requirementsLockPath",
        "applicationCwd",
        "roles",
        "frozenNodeRollback",
    }
    if (
        not isinstance(document, dict)
        or set(document) != expected
        or document.get("schemaVersion") != 1
        or document.get("deploymentKind") != "native-windows"
    ):
        raise BuildIdentityError(
            "BUILD_NATIVE_RUNTIME_INVALID",
            "The native runtime specification schema is invalid.",
        )
    python_path = _runtime_file(document["pythonExecutablePath"], "Python executable")
    requirements_path = _runtime_file(document["requirementsLockPath"], "requirements lock")
    application_cwd = _runtime_directory(document["applicationCwd"], "application cwd")
    roles = document["roles"]
    if not isinstance(roles, dict) or tuple(roles) != _NATIVE_ROLES:
        raise BuildIdentityError(
            "BUILD_NATIVE_RUNTIME_INVALID",
            "Native runtime roles must be ordered api, worker, scheduler, mcp.",
        )
    role_entries = [
        _native_role_document(role, roles[role], python_path=python_path)
        for role in _NATIVE_ROLES
    ]
    rollback = document["frozenNodeRollback"]
    if not isinstance(rollback, dict) or set(rollback) != {
        "executablePath",
        "entrypointPath",
        "cwd",
        "argv",
        "environment",
    }:
        raise BuildIdentityError(
            "BUILD_NATIVE_RUNTIME_INVALID",
            "The frozen Node rollback specification is invalid.",
        )
    if frozen_node_executable_sha256 is None:
        node_path = _runtime_file(rollback["executablePath"], "Node executable")
        node_sha256 = _file_sha256(node_path)
    else:
        node_path = _runtime_path(rollback["executablePath"], "Node executable")
        node_sha256 = frozen_node_executable_sha256
    entrypoint_path = _runtime_file(rollback["entrypointPath"], "Node entrypoint")
    cwd = _runtime_directory(rollback["cwd"], "Node cwd")
    node_argv = _runtime_argv(rollback["argv"])
    if (
        Path(node_argv[0]).expanduser().resolve(strict=False) != node_path
        or len(node_argv) < 2
        or Path(node_argv[1]).expanduser().resolve(strict=False) != entrypoint_path
    ):
        raise BuildIdentityError(
            "BUILD_NATIVE_RUNTIME_INVALID",
            "Frozen Node argv must name the exact executable and entrypoint.",
        )
    return {
        "specPath": str(spec_path),
        "pythonExecutable": {
            "path": str(python_path),
            "sha256": _file_sha256(python_path),
        },
        "requirementsLock": {
            "path": str(requirements_path),
            "sha256": _file_sha256(requirements_path),
        },
        "applicationCwd": str(application_cwd),
        "roles": role_entries,
        "frozenNodeRollback": {
            "executablePath": str(node_path),
            "executableSha256": node_sha256,
            "entrypointPath": str(entrypoint_path),
            "entrypointSha256": _file_sha256(entrypoint_path),
            "cwd": str(cwd),
            "argv": list(node_argv),
            "environment": _hashed_environment(rollback["environment"]),
        },
    }


def native_role_argv(python_executable: Path, role: str) -> tuple[str, ...]:
    """Return the only command a native role may ever be frozen or started with."""
    if role == "mcp":
        return (str(python_executable), "-B", "-m", "agent.mcp_server", "--supervisor")
    return (
        str(python_executable),
        "-B",
        "-m",
        "backend.app.cli.candidate_runtime",
        "--role",
        role,
    )


def _native_role_document(
    role: str,
    value: object,
    *,
    python_path: Path,
) -> dict[str, object]:
    if not isinstance(value, dict) or set(value) != {"argv", "environment"}:
        raise BuildIdentityError(
            "BUILD_NATIVE_RUNTIME_INVALID",
            f"The native {role} role specification is invalid.",
        )
    argv = _runtime_argv(value["argv"])
    if Path(argv[0]).expanduser().resolve(strict=False) != python_path:
        raise BuildIdentityError(
            "BUILD_NATIVE_RUNTIME_INVALID",
            f"The native {role} argv does not use the frozen Python executable.",
        )
    if argv[1:] != native_role_argv(python_path, role)[1:]:
        raise BuildIdentityError(
            "BUILD_NATIVE_RUNTIME_INVALID",
            f"The native {role} argv does not match the frozen role contract.",
        )
    return {
        "role": role,
        "argv": list(argv),
        "environment": _hashed_environment(value["environment"]),
    }


def _runtime_argv(value: object) -> tuple[str, ...]:
    if (
        not isinstance(value, list)
        or not value
        or any(not isinstance(item, str) or not item for item in value)
    ):
        raise BuildIdentityError(
            "BUILD_NATIVE_RUNTIME_INVALID",
            "A native runtime argv is invalid.",
        )
    return tuple(value)


def _hashed_environment(value: object) -> list[dict[str, str]]:
    if (
        not isinstance(value, dict)
        or not value
        or any(
            not isinstance(name, str)
            or not name
            or not isinstance(item, str)
            for name, item in value.items()
        )
    ):
        raise BuildIdentityError(
            "BUILD_NATIVE_RUNTIME_INVALID",
            "A native runtime environment map is invalid.",
        )
    return [
        {
            "name": name,
            "valueSha256": hashlib.sha256(item.encode("utf-8")).hexdigest(),
        }
        for name, item in sorted(value.items())
    ]


def _runtime_file(value: object, description: str) -> Path:
    if not isinstance(value, str) or not value:
        raise BuildIdentityError(
            "BUILD_NATIVE_RUNTIME_INVALID",
            f"The native {description} path is invalid.",
        )
    try:
        path = Path(value).expanduser().resolve(strict=True)
    except OSError as error:
        raise BuildIdentityError(
            "BUILD_NATIVE_RUNTIME_INVALID",
            f"The native {description} path does not exist.",
        ) from error
    if not path.is_file():
        raise BuildIdentityError(
            "BUILD_NATIVE_RUNTIME_INVALID",
            f"The native {description} path is not a file.",
        )
    return path


def _runtime_path(value: object, description: str) -> Path:
    if not isinstance(value, str) or not value:
        raise BuildIdentityError(
            "BUILD_NATIVE_RUNTIME_INVALID",
            f"The native {description} path is invalid.",
        )
    try:
        return Path(value).expanduser().resolve(strict=False)
    except OSError as error:
        raise BuildIdentityError(
            "BUILD_NATIVE_RUNTIME_INVALID",
            f"The native {description} path is invalid.",
        ) from error


def _runtime_directory(value: object, description: str) -> Path:
    if not isinstance(value, str) or not value:
        raise BuildIdentityError(
            "BUILD_NATIVE_RUNTIME_INVALID",
            f"The native {description} path is invalid.",
        )
    try:
        path = Path(value).expanduser().resolve(strict=True)
    except OSError as error:
        raise BuildIdentityError(
            "BUILD_NATIVE_RUNTIME_INVALID",
            f"The native {description} path does not exist.",
        ) from error
    if not path.is_dir():
        raise BuildIdentityError(
            "BUILD_NATIVE_RUNTIME_INVALID",
            f"The native {description} path is not a directory.",
        )
    return path


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
    "native_role_argv",
    "verify_build_identity",
    "verify_native_runtime_spec",
]
