from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
from typing import Protocol, Sequence

from backend.app.api.compat.build_identity import BuildIdentityManifest
from backend.app.api.compat.database_identity import (
    DatabaseIdentityError,
    canonical_json_bytes,
    exclusive_write_bytes,
)


NATIVE_WINDOWS_CONTAINER_ONLY_TEST_IDS = (
    "test_candidate_container_contract.CandidateContainerContractTests."
    "test_resolved_candidate_build_targets_exist_and_match_role_commands",
    "test_candidate_container_contract.CandidateContainerContractTests."
    "test_resolved_candidate_commands_start_real_isolated_roles",
    "test_candidate_container_contract.CandidateContainerContractTests."
    "test_resolved_default_compose_keeps_node_as_live_owner",
    "test_candidate_container_contract.CandidateContainerContractTests."
    "test_resolved_p4_candidate_profile_is_isolated_role_scoped_and_loopback_only",
)
NATIVE_WINDOWS_SYMLINK_TEST_IDS = {
    "directory": (
        "test_database_backup.DatabaseBackupTests."
        "test_restore_rejects_symlinked_output_root"
    ),
    "file": (
        "test_database_backup.DatabaseBackupTests."
        "test_windows_rename_rejects_a_final_component_symlink"
    ),
}
NATIVE_WINDOWS_NODE_CONTAINER_ONLY_FILE = "test/docker-react-build.test.js"
NATIVE_WINDOWS_NODE_CONTAINER_ONLY_FILE_SHA256 = (
    "bddc4bd8fe49c0e710a81253523e0a1932a8545eed658c2bd340c3534b08fa73"
)
NATIVE_WINDOWS_NODE_CONTAINER_ONLY_TEST_NAMES = (
    "the runtime image receives a production React build from an isolated frontend stage",
    "the Docker context cannot substitute host frontend artifacts for the image build",
    "the production Docker context excludes tests, fixtures, and fake provider credentials",
    "Docker Compose exposes the startup-only UI entry rollback switch",
    "containers default backend rollout to legacy and OCR off with pass-through overrides",
)
_DISCOVERY_START_DIRECTORY = "backend/tests"
_DISCOVERY_PATTERN = "test_*.py"
_NODE_DISCOVERY_PATTERN = "test/**/*.js"
NATIVE_WINDOWS_SYMLINK_UNAVAILABLE_WIN_ERRORS = frozenset({1, 1314})
_REPORT_FIELDS = (
    "schemaVersion",
    "manifestKind",
    "suiteKey",
    "deploymentKind",
    "buildIdentityManifestPath",
    "buildIdentityManifestSha256",
    "buildId",
    "discoveryStartDirectory",
    "discoveryPattern",
    "discoveredTests",
    "selectedTests",
    "discoveredTestIds",
    "selectedTestIds",
    "excludedTests",
    "symlinkCapabilities",
)
_NODE_REPORT_FIELDS = (
    "schemaVersion",
    "manifestKind",
    "suiteKey",
    "deploymentKind",
    "buildIdentityManifestPath",
    "buildIdentityManifestSha256",
    "buildId",
    "repositoryPath",
    "discoveryPattern",
    "discoveredTests",
    "selectedTests",
    "discoveredFiles",
    "selectedFiles",
    "excludedFiles",
)


class SuiteApplicabilityError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


class _IdentifiedTest(Protocol):
    def id(self) -> str: ...


@dataclass(frozen=True, slots=True)
class SymlinkCapability:
    kind: str
    available: bool
    win_error: int | None


@dataclass(frozen=True, slots=True)
class ExcludedTest:
    test_id: str
    reason_code: str


@dataclass(frozen=True, slots=True)
class NativeWindowsSuiteSelection:
    discovered_tests: tuple[_IdentifiedTest, ...]
    selected_tests: tuple[_IdentifiedTest, ...]
    discovered_test_ids: tuple[str, ...]
    selected_test_ids: tuple[str, ...]
    excluded_tests: tuple[ExcludedTest, ...]
    symlink_capabilities: tuple[SymlinkCapability, ...]


@dataclass(frozen=True, slots=True)
class SuiteApplicabilityReport:
    manifest_path: Path
    manifest_file_sha256: str
    build_identity_manifest_path: Path
    build_identity_manifest_sha256: str
    build_id: str
    deployment_kind: str
    discovered_tests: int
    selected_tests: int
    discovered_test_ids: tuple[str, ...]
    selected_test_ids: tuple[str, ...]
    excluded_tests: tuple[ExcludedTest, ...]
    symlink_capabilities: tuple[SymlinkCapability, ...]
    canonical_bytes: bytes


@dataclass(frozen=True, slots=True)
class NativeWindowsNodeSuiteSelection:
    repository_path: Path
    discovered_files: tuple[str, ...]
    selected_files: tuple[str, ...]
    excluded_file_sha256: str
    excluded_test_names: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class NodeSuiteApplicabilityReport:
    suite_key: str
    manifest_path: Path
    manifest_file_sha256: str
    build_identity_manifest_path: Path
    build_identity_manifest_sha256: str
    build_id: str
    deployment_kind: str
    repository_path: Path
    discovered_tests: int
    selected_tests: int
    discovered_files: tuple[str, ...]
    selected_files: tuple[str, ...]
    excluded_file_sha256: str
    excluded_test_names: tuple[str, ...]
    canonical_bytes: bytes


def select_native_windows_tests(
    tests: Sequence[_IdentifiedTest],
    *,
    symlink_capabilities: Sequence[SymlinkCapability],
) -> NativeWindowsSuiteSelection:
    capabilities = _validated_capabilities(symlink_capabilities)
    discovered = tuple(tests)
    discovered_ids = tuple(test.id() for test in discovered)
    if (
        any(not isinstance(test_id, str) or not test_id for test_id in discovered_ids)
        or len(discovered_ids) != len(set(discovered_ids))
    ):
        raise SuiteApplicabilityError(
            "SUITE_APPLICABILITY_DISCOVERY_INVALID",
            "Backend test discovery returned an invalid or duplicate test ID.",
        )
    required = set(NATIVE_WINDOWS_CONTAINER_ONLY_TEST_IDS) | set(
        NATIVE_WINDOWS_SYMLINK_TEST_IDS.values()
    )
    missing = sorted(required - set(discovered_ids))
    if missing:
        raise SuiteApplicabilityError(
            "SUITE_APPLICABILITY_POLICY_STALE",
            "A frozen native-Windows applicability test ID is missing: "
            + ", ".join(missing),
        )

    exclusions = {
        test_id: "container-deployment-only"
        for test_id in NATIVE_WINDOWS_CONTAINER_ONLY_TEST_IDS
    }
    for capability in capabilities:
        if not capability.available:
            exclusions[NATIVE_WINDOWS_SYMLINK_TEST_IDS[capability.kind]] = (
                f"windows-{capability.kind}-symlink-privilege-unavailable"
            )
    selected = tuple(test for test in discovered if test.id() not in exclusions)
    selected_ids = tuple(sorted(test.id() for test in selected))
    excluded = tuple(
        ExcludedTest(test_id=test_id, reason_code=reason)
        for test_id, reason in sorted(exclusions.items())
    )
    return NativeWindowsSuiteSelection(
        discovered_tests=discovered,
        selected_tests=selected,
        discovered_test_ids=tuple(sorted(discovered_ids)),
        selected_test_ids=selected_ids,
        excluded_tests=excluded,
        symlink_capabilities=capabilities,
    )


def select_native_windows_node_files(
    repository: str | os.PathLike[str],
) -> NativeWindowsNodeSuiteSelection:
    try:
        root = Path(repository).resolve(strict=True)
    except OSError as error:
        raise SuiteApplicabilityError(
            "SUITE_APPLICABILITY_DISCOVERY_INVALID",
            "The native-Windows Node repository does not exist.",
        ) from error
    test_root = root / "test"
    if not root.is_dir() or not test_root.is_dir():
        raise SuiteApplicabilityError(
            "SUITE_APPLICABILITY_DISCOVERY_INVALID",
            "The native-Windows Node discovery root is invalid.",
        )
    candidates = tuple(sorted(test_root.rglob("*.js")))
    if any(not path.is_file() or path.is_symlink() for path in candidates):
        raise SuiteApplicabilityError(
            "SUITE_APPLICABILITY_DISCOVERY_INVALID",
            "Node test discovery encountered a non-physical test file.",
        )
    discovered = tuple(path.relative_to(root).as_posix() for path in candidates)
    if (
        not discovered
        or len(discovered) != len(set(discovered))
        or NATIVE_WINDOWS_NODE_CONTAINER_ONLY_FILE not in discovered
    ):
        raise SuiteApplicabilityError(
            "SUITE_APPLICABILITY_POLICY_STALE",
            "The frozen native-Windows Node applicability file is missing.",
        )
    excluded_path = root / NATIVE_WINDOWS_NODE_CONTAINER_ONLY_FILE
    try:
        excluded_sha256 = hashlib.sha256(excluded_path.read_bytes()).hexdigest()
    except OSError as error:
        raise SuiteApplicabilityError(
            "SUITE_APPLICABILITY_POLICY_STALE",
            "The frozen native-Windows Node applicability file cannot be read.",
        ) from error
    if excluded_sha256 != NATIVE_WINDOWS_NODE_CONTAINER_ONLY_FILE_SHA256:
        raise SuiteApplicabilityError(
            "SUITE_APPLICABILITY_POLICY_STALE",
            "The frozen native-Windows Node applicability file changed.",
        )
    selected = tuple(
        path for path in discovered if path != NATIVE_WINDOWS_NODE_CONTAINER_ONLY_FILE
    )
    if not selected:
        raise SuiteApplicabilityError(
            "SUITE_APPLICABILITY_DISCOVERY_INVALID",
            "The native-Windows Node suite selected no applicable test files.",
        )
    return NativeWindowsNodeSuiteSelection(
        repository_path=root,
        discovered_files=discovered,
        selected_files=selected,
        excluded_file_sha256=excluded_sha256,
        excluded_test_names=NATIVE_WINDOWS_NODE_CONTAINER_ONLY_TEST_NAMES,
    )


def create_node_suite_applicability_report(
    *,
    selection: NativeWindowsNodeSuiteSelection,
    build_identity: BuildIdentityManifest,
    selected_tests: int,
    output: str | os.PathLike[str],
) -> NodeSuiteApplicabilityReport:
    if build_identity.deployment_kind != "native-windows":
        raise SuiteApplicabilityError(
            "SUITE_APPLICABILITY_DEPLOYMENT_INVALID",
            "The native-Windows Node suite requires a native-Windows BuildIdentity.",
        )
    if not _is_count(selected_tests) or selected_tests < 1:
        raise SuiteApplicabilityError(
            "SUITE_APPLICABILITY_RESULT_INVALID",
            "The native-Windows Node suite test count is invalid.",
        )
    current = select_native_windows_node_files(selection.repository_path)
    if current != selection:
        raise SuiteApplicabilityError(
            "SUITE_APPLICABILITY_POLICY_STALE",
            "Node test discovery changed before the applicability report was written.",
        )
    excluded_tests = len(selection.excluded_test_names)
    document = {
        "schemaVersion": 1,
        "manifestKind": "suite-applicability",
        "suiteKey": "node-suite",
        "deploymentKind": "native-windows",
        "buildIdentityManifestPath": str(build_identity.manifest_path),
        "buildIdentityManifestSha256": build_identity.manifest_file_sha256,
        "buildId": build_identity.build_id,
        "repositoryPath": str(selection.repository_path),
        "discoveryPattern": _NODE_DISCOVERY_PATTERN,
        "discoveredTests": selected_tests + excluded_tests,
        "selectedTests": selected_tests,
        "discoveredFiles": list(selection.discovered_files),
        "selectedFiles": list(selection.selected_files),
        "excludedFiles": [
            {
                "path": NATIVE_WINDOWS_NODE_CONTAINER_ONLY_FILE,
                "reasonCode": "container-deployment-only",
                "sha256": selection.excluded_file_sha256,
                "testNames": list(selection.excluded_test_names),
            }
        ],
    }
    payload = canonical_json_bytes(document)
    path = Path(output).resolve(strict=False)
    try:
        exclusive_write_bytes(path, payload)
    except DatabaseIdentityError as error:
        raise SuiteApplicabilityError(error.code, str(error)) from error
    return load_node_suite_applicability_report(path)


def load_node_suite_applicability_report(
    path: str | os.PathLike[str],
) -> NodeSuiteApplicabilityReport:
    resolved = Path(path).resolve(strict=True)
    payload = resolved.read_bytes()
    document = _strict_node_document(payload)
    repository_value = document["repositoryPath"]
    build_path_value = document["buildIdentityManifestPath"]
    if not isinstance(repository_value, str) or not isinstance(build_path_value, str):
        raise _invalid_report()
    try:
        repository = Path(repository_value).resolve(strict=True)
        build_path = Path(build_path_value).resolve(strict=True)
        selection = select_native_windows_node_files(repository)
    except (OSError, SuiteApplicabilityError) as error:
        raise _invalid_report() from error
    excluded_files = document["excludedFiles"]
    expected_excluded = [
        {
            "path": NATIVE_WINDOWS_NODE_CONTAINER_ONLY_FILE,
            "reasonCode": "container-deployment-only",
            "sha256": selection.excluded_file_sha256,
            "testNames": list(selection.excluded_test_names),
        }
    ]
    discovered_files = _sorted_unique_ids(document["discoveredFiles"])
    selected_files = _sorted_unique_ids(document["selectedFiles"])
    selected_tests = document["selectedTests"]
    discovered_tests = document["discoveredTests"]
    if (
        document["schemaVersion"] != 1
        or document["manifestKind"] != "suite-applicability"
        or document["suiteKey"] != "node-suite"
        or document["deploymentKind"] != "native-windows"
        or document["discoveryPattern"] != _NODE_DISCOVERY_PATTERN
        or str(repository) != repository_value
        or str(build_path) != build_path_value
        or discovered_files != selection.discovered_files
        or selected_files != selection.selected_files
        or excluded_files != expected_excluded
        or not _is_count(selected_tests)
        or selected_tests < 1
        or not _is_count(discovered_tests)
        or discovered_tests
        != selected_tests + len(NATIVE_WINDOWS_NODE_CONTAINER_ONLY_TEST_NAMES)
    ):
        raise _invalid_report()
    build_sha = _required_hex(document["buildIdentityManifestSha256"], 64)
    build_id = _required_hex(document["buildId"], 64)
    return NodeSuiteApplicabilityReport(
        suite_key="node-suite",
        manifest_path=resolved,
        manifest_file_sha256=hashlib.sha256(payload).hexdigest(),
        build_identity_manifest_path=build_path,
        build_identity_manifest_sha256=build_sha,
        build_id=build_id,
        deployment_kind="native-windows",
        repository_path=repository,
        discovered_tests=discovered_tests,
        selected_tests=selected_tests,
        discovered_files=discovered_files,
        selected_files=selected_files,
        excluded_file_sha256=selection.excluded_file_sha256,
        excluded_test_names=selection.excluded_test_names,
        canonical_bytes=payload,
    )


def create_suite_applicability_report(
    *,
    selection: NativeWindowsSuiteSelection,
    build_identity: BuildIdentityManifest,
    discovery_start_directory: str,
    discovery_pattern: str,
    output: str | os.PathLike[str],
) -> SuiteApplicabilityReport:
    if build_identity.deployment_kind != "native-windows":
        raise SuiteApplicabilityError(
            "SUITE_APPLICABILITY_DEPLOYMENT_INVALID",
            "The native-Windows suite requires a native-Windows BuildIdentity.",
        )
    if (
        discovery_start_directory != _DISCOVERY_START_DIRECTORY
        or discovery_pattern != _DISCOVERY_PATTERN
    ):
        raise SuiteApplicabilityError(
            "SUITE_APPLICABILITY_DISCOVERY_INVALID",
            "The backend discovery root or pattern differs from the frozen policy.",
        )
    document = {
        "schemaVersion": 1,
        "manifestKind": "suite-applicability",
        "suiteKey": "backend-suite",
        "deploymentKind": "native-windows",
        "buildIdentityManifestPath": str(build_identity.manifest_path),
        "buildIdentityManifestSha256": build_identity.manifest_file_sha256,
        "buildId": build_identity.build_id,
        "discoveryStartDirectory": discovery_start_directory,
        "discoveryPattern": discovery_pattern,
        "discoveredTests": len(selection.discovered_test_ids),
        "selectedTests": len(selection.selected_test_ids),
        "discoveredTestIds": list(selection.discovered_test_ids),
        "selectedTestIds": list(selection.selected_test_ids),
        "excludedTests": [
            {"testId": item.test_id, "reasonCode": item.reason_code}
            for item in selection.excluded_tests
        ],
        "symlinkCapabilities": [
            {
                "kind": item.kind,
                "available": item.available,
                "winError": item.win_error,
            }
            for item in selection.symlink_capabilities
        ],
    }
    payload = canonical_json_bytes(document)
    path = Path(output).resolve(strict=False)
    try:
        exclusive_write_bytes(path, payload)
    except DatabaseIdentityError as error:
        raise SuiteApplicabilityError(error.code, str(error)) from error
    return load_suite_applicability_report(path)


def load_suite_applicability_report(
    path: str | os.PathLike[str],
) -> SuiteApplicabilityReport:
    resolved = Path(path).resolve(strict=True)
    payload = resolved.read_bytes()
    document = _strict_document(payload)
    capability_values = document["symlinkCapabilities"]
    if any(
        not isinstance(item, dict)
        or tuple(item) != ("kind", "available", "winError")
        for item in capability_values
    ):
        raise _invalid_report()
    capabilities = _validated_capabilities(
        tuple(
            SymlinkCapability(
                kind=item["kind"],
                available=item["available"],
                win_error=item["winError"],
            )
            for item in capability_values
        )
    )
    discovered_ids = _sorted_unique_ids(document["discoveredTestIds"])
    selected_ids = _sorted_unique_ids(document["selectedTestIds"])
    excluded = _validated_exclusions(document["excludedTests"], capabilities)
    excluded_ids = {item.test_id for item in excluded}
    if (
        document["schemaVersion"] != 1
        or document["manifestKind"] != "suite-applicability"
        or document["suiteKey"] != "backend-suite"
        or document["deploymentKind"] != "native-windows"
        or document["discoveryStartDirectory"] != _DISCOVERY_START_DIRECTORY
        or document["discoveryPattern"] != _DISCOVERY_PATTERN
        or not _is_count(document["discoveredTests"])
        or not _is_count(document["selectedTests"])
        or document["discoveredTests"] != len(discovered_ids)
        or document["selectedTests"] != len(selected_ids)
        or not selected_ids
        or set(discovered_ids) != set(selected_ids) | excluded_ids
        or set(selected_ids) & excluded_ids
        or len(discovered_ids) != len(selected_ids) + len(excluded_ids)
    ):
        raise _invalid_report()
    build_path_value = document["buildIdentityManifestPath"]
    if not isinstance(build_path_value, str):
        raise _invalid_report()
    try:
        build_path = Path(build_path_value).resolve(strict=True)
    except OSError as error:
        raise _invalid_report() from error
    if str(build_path) != build_path_value:
        raise _invalid_report()
    build_sha = _required_hex(document["buildIdentityManifestSha256"], 64)
    build_id = _required_hex(document["buildId"], 64)
    return SuiteApplicabilityReport(
        manifest_path=resolved,
        manifest_file_sha256=hashlib.sha256(payload).hexdigest(),
        build_identity_manifest_path=build_path,
        build_identity_manifest_sha256=build_sha,
        build_id=build_id,
        deployment_kind="native-windows",
        discovered_tests=len(discovered_ids),
        selected_tests=len(selected_ids),
        discovered_test_ids=discovered_ids,
        selected_test_ids=selected_ids,
        excluded_tests=excluded,
        symlink_capabilities=capabilities,
        canonical_bytes=payload,
    )


def _strict_document(payload: bytes) -> dict[str, object]:
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
        raise _invalid_report() from error
    if (
        duplicates
        or not isinstance(document, dict)
        or tuple(document) != _REPORT_FIELDS
        or canonical_json_bytes(document) != payload
        or not isinstance(document.get("excludedTests"), list)
        or not isinstance(document.get("symlinkCapabilities"), list)
    ):
        raise _invalid_report()
    return document


def _strict_node_document(payload: bytes) -> dict[str, object]:
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
        raise _invalid_report() from error
    if (
        duplicates
        or not isinstance(document, dict)
        or tuple(document) != _NODE_REPORT_FIELDS
        or canonical_json_bytes(document) != payload
        or not isinstance(document.get("discoveredFiles"), list)
        or not isinstance(document.get("selectedFiles"), list)
        or not isinstance(document.get("excludedFiles"), list)
    ):
        raise _invalid_report()
    return document


def _validated_capabilities(
    values: Sequence[SymlinkCapability],
) -> tuple[SymlinkCapability, ...]:
    capabilities = tuple(values)
    if tuple(item.kind for item in capabilities) != ("directory", "file"):
        raise _invalid_report()
    for item in capabilities:
        if (
            not isinstance(item.kind, str)
            or not isinstance(item.available, bool)
            or (item.available and item.win_error is not None)
            or (
                not item.available
                and (
                    not isinstance(item.win_error, int)
                    or isinstance(item.win_error, bool)
                    or item.win_error
                    not in NATIVE_WINDOWS_SYMLINK_UNAVAILABLE_WIN_ERRORS
                )
            )
        ):
            raise _invalid_report()
    return capabilities


def _validated_exclusions(
    value: object,
    capabilities: tuple[SymlinkCapability, ...],
) -> tuple[ExcludedTest, ...]:
    if not isinstance(value, list):
        raise _invalid_report()
    exclusions: list[ExcludedTest] = []
    for item in value:
        if (
            not isinstance(item, dict)
            or tuple(item) != ("testId", "reasonCode")
            or not isinstance(item["testId"], str)
            or not isinstance(item["reasonCode"], str)
        ):
            raise _invalid_report()
        exclusions.append(
            ExcludedTest(test_id=item["testId"], reason_code=item["reasonCode"])
        )
    expected = {
        test_id: "container-deployment-only"
        for test_id in NATIVE_WINDOWS_CONTAINER_ONLY_TEST_IDS
    }
    for capability in capabilities:
        if not capability.available:
            expected[NATIVE_WINDOWS_SYMLINK_TEST_IDS[capability.kind]] = (
                f"windows-{capability.kind}-symlink-privilege-unavailable"
            )
    actual = {item.test_id: item.reason_code for item in exclusions}
    if (
        len(actual) != len(exclusions)
        or actual != expected
        or tuple(item.test_id for item in exclusions) != tuple(sorted(expected))
    ):
        raise _invalid_report()
    return tuple(exclusions)


def _sorted_unique_ids(value: object) -> tuple[str, ...]:
    if (
        not isinstance(value, list)
        or any(not isinstance(item, str) or not item for item in value)
        or len(value) != len(set(value))
        or value != sorted(value)
    ):
        raise _invalid_report()
    return tuple(value)


def _required_hex(value: object, width: int) -> str:
    if (
        not isinstance(value, str)
        or len(value) != width
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise _invalid_report()
    return value


def _is_count(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _invalid_report() -> SuiteApplicabilityError:
    return SuiteApplicabilityError(
        "SUITE_APPLICABILITY_INVALID",
        "The native-Windows suite applicability report is invalid.",
    )


__all__ = [
    "ExcludedTest",
    "NATIVE_WINDOWS_CONTAINER_ONLY_TEST_IDS",
    "NATIVE_WINDOWS_NODE_CONTAINER_ONLY_FILE",
    "NATIVE_WINDOWS_NODE_CONTAINER_ONLY_FILE_SHA256",
    "NATIVE_WINDOWS_NODE_CONTAINER_ONLY_TEST_NAMES",
    "NATIVE_WINDOWS_SYMLINK_TEST_IDS",
    "NativeWindowsNodeSuiteSelection",
    "NativeWindowsSuiteSelection",
    "NodeSuiteApplicabilityReport",
    "SuiteApplicabilityError",
    "SuiteApplicabilityReport",
    "SymlinkCapability",
    "create_node_suite_applicability_report",
    "create_suite_applicability_report",
    "load_node_suite_applicability_report",
    "load_suite_applicability_report",
    "select_native_windows_node_files",
    "select_native_windows_tests",
]
