from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest

from backend.app.api.compat.build_identity import (
    BuildIdentityError,
    load_build_identity_manifest,
)
from backend.app.api.compat.database_identity import (
    DatabaseIdentityError,
    canonical_json_bytes,
    exclusive_write_bytes,
)
from backend.app.api.compat.suite_applicability import (
    NATIVE_WINDOWS_SYMLINK_UNAVAILABLE_WIN_ERRORS,
    SuiteApplicabilityError,
    SymlinkCapability,
    create_suite_applicability_report,
    select_native_windows_tests,
)


_DISCOVERY_START_DIRECTORY = "backend/tests"
_DISCOVERY_PATTERN = "test_*.py"
_EVIDENCE_PROCESS_SNAPSHOT_PREFIXES = ("P6_FINAL_", "P6_PROVISIONAL_")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="native-windows-suite")
    parser.add_argument("--repository", required=True)
    parser.add_argument("--build-identity-manifest", required=True)
    parser.add_argument("--isolation-manifest", required=True)
    parser.add_argument("--result-output", required=True)
    parser.add_argument("--applicability-output", required=True)
    return parser


def run(arguments: list[str]) -> int:
    options = build_parser().parse_args(arguments)
    if os.name != "nt":
        raise SuiteApplicabilityError(
            "SUITE_APPLICABILITY_PLATFORM_INVALID",
            "The native-Windows suite runner requires Windows.",
        )
    repository = Path(options.repository).resolve(strict=True)
    start_directory = repository / _DISCOVERY_START_DIRECTORY
    if not start_directory.is_dir():
        raise SuiteApplicabilityError(
            "SUITE_APPLICABILITY_DISCOVERY_INVALID",
            "The frozen backend discovery directory does not exist.",
        )
    build = load_build_identity_manifest(options.build_identity_manifest)
    if build.deployment_kind != "native-windows":
        raise SuiteApplicabilityError(
            "SUITE_APPLICABILITY_DEPLOYMENT_INVALID",
            "The suite runner is not bound to a native-Windows BuildIdentity.",
        )
    run_root, sandbox_root = _load_isolation(Path(options.isolation_manifest))
    result_output = _new_run_file(Path(options.result_output), run_root, "result output")
    applicability_output = _new_run_file(
        Path(options.applicability_output), run_root, "applicability output"
    )
    if result_output == applicability_output:
        raise SuiteApplicabilityError(
            "SUITE_APPLICABILITY_OUTPUT_INVALID",
            "The result and applicability outputs must be different files.",
        )

    discovered_suite = unittest.TestLoader().discover(
        str(start_directory),
        pattern=_DISCOVERY_PATTERN,
    )
    discovered_tests = tuple(_flatten_tests(discovered_suite))
    selection = select_native_windows_tests(
        discovered_tests,
        symlink_capabilities=_probe_symlink_capabilities(sandbox_root),
    )
    create_suite_applicability_report(
        selection=selection,
        build_identity=build,
        discovery_start_directory=_DISCOVERY_START_DIRECTORY,
        discovery_pattern=_DISCOVERY_PATTERN,
        output=applicability_output,
    )

    result = _run_selected_tests(selection.selected_tests)
    failures = [test.id() for test, _traceback in result.failures]
    failures.extend(test.id() for test in result.unexpectedSuccesses)
    errors = [test.id() for test, _traceback in result.errors]
    skipped = [test.id() for test, _reason in result.skipped]
    skipped.extend(test.id() for test, _traceback in result.expectedFailures)
    result_document = {
        "testsRun": result.testsRun,
        "failures": sorted(failures),
        "errors": sorted(errors),
        "skipped": sorted(skipped),
    }
    try:
        exclusive_write_bytes(result_output, canonical_json_bytes(result_document))
    except DatabaseIdentityError as error:
        raise SuiteApplicabilityError(error.code, str(error)) from error
    return 0 if result.wasSuccessful() else 1


def _load_isolation(path: Path) -> tuple[Path, Path]:
    resolved = path.resolve(strict=True)
    try:
        document = json.loads(resolved.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise SuiteApplicabilityError(
            "SUITE_APPLICABILITY_ISOLATION_INVALID",
            "The backend suite isolation manifest is invalid.",
        ) from error
    expected_environment = os.environ.get("P6_SUITE_ISOLATION_MANIFEST")
    sandbox_value = document.get("sandboxRoot") if isinstance(document, dict) else None
    if (
        not isinstance(document, dict)
        or document.get("manifestKind") != "suite-isolation"
        or document.get("suiteKey") != "backend-suite"
        or expected_environment != str(resolved)
        or not isinstance(sandbox_value, str)
    ):
        raise SuiteApplicabilityError(
            "SUITE_APPLICABILITY_ISOLATION_INVALID",
            "The backend suite is not bound to its exact isolation manifest.",
        )
    sandbox = Path(sandbox_value).resolve(strict=True)
    run_root = resolved.parent.resolve(strict=True)
    try:
        sandbox.relative_to(run_root)
    except ValueError as error:
        raise SuiteApplicabilityError(
            "SUITE_APPLICABILITY_ISOLATION_INVALID",
            "The backend suite sandbox escaped the evidence run.",
        ) from error
    if not sandbox.is_dir():
        raise SuiteApplicabilityError(
            "SUITE_APPLICABILITY_ISOLATION_INVALID",
            "The backend suite sandbox is not a directory.",
        )
    return run_root, sandbox


def _new_run_file(path: Path, run_root: Path, label: str) -> Path:
    resolved = path.resolve(strict=False)
    if resolved.parent.resolve(strict=True) != run_root or resolved.exists():
        raise SuiteApplicabilityError(
            "SUITE_APPLICABILITY_OUTPUT_INVALID",
            f"The {label} must be a new direct child of the evidence run.",
        )
    return resolved


def _flatten_tests(suite: unittest.TestSuite):
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            yield from _flatten_tests(item)
        else:
            yield item


def _run_selected_tests(
    tests: tuple[unittest.TestCase, ...],
) -> unittest.TestResult:
    snapshot = {
        name: value
        for name, value in os.environ.items()
        if name.startswith(_EVIDENCE_PROCESS_SNAPSHOT_PREFIXES)
    }
    for name in snapshot:
        os.environ.pop(name, None)
    try:
        return unittest.TextTestRunner(verbosity=2).run(unittest.TestSuite(tests))
    finally:
        for name in tuple(os.environ):
            if name.startswith(_EVIDENCE_PROCESS_SNAPSHOT_PREFIXES):
                os.environ.pop(name, None)
        os.environ.update(snapshot)


def _probe_symlink_capabilities(root: Path) -> tuple[SymlinkCapability, ...]:
    return tuple(_probe_symlink(root, kind) for kind in ("directory", "file"))


def _probe_symlink(root: Path, kind: str) -> SymlinkCapability:
    probe_root = Path(tempfile.mkdtemp(prefix=f"symlink-{kind}-", dir=root))
    target = probe_root / "target"
    link = probe_root / "link"
    try:
        if kind == "directory":
            target.mkdir()
        else:
            target.write_bytes(b"native-windows-suite-probe")
        try:
            os.symlink(target, link, target_is_directory=(kind == "directory"))
        except OSError as error:
            win_error = getattr(error, "winerror", None)
            if win_error not in NATIVE_WINDOWS_SYMLINK_UNAVAILABLE_WIN_ERRORS:
                raise SuiteApplicabilityError(
                    "SUITE_APPLICABILITY_SYMLINK_PROBE_FAILED",
                    f"The {kind} symlink capability probe failed unexpectedly.",
                ) from error
            return SymlinkCapability(kind=kind, available=False, win_error=win_error)
        link.unlink()
        return SymlinkCapability(kind=kind, available=True, win_error=None)
    finally:
        shutil.rmtree(probe_root)


def main(arguments: list[str] | None = None) -> int:
    try:
        return run(sys.argv[1:] if arguments is None else arguments)
    except (BuildIdentityError, DatabaseIdentityError, SuiteApplicabilityError) as error:
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": {
                        "code": getattr(error, "code", "SUITE_APPLICABILITY_FAILED"),
                        "message": str(error),
                    },
                },
                ensure_ascii=False,
                separators=(",", ":"),
            ),
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
