from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET

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
    SuiteApplicabilityError,
    create_node_suite_applicability_report,
    select_native_windows_node_files,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="native-windows-node-suite")
    parser.add_argument("--repository", required=True)
    parser.add_argument("--node-executable", required=True)
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
            "The native-Windows Node suite runner requires Windows.",
        )
    repository = Path(options.repository).resolve(strict=True)
    node = Path(options.node_executable).resolve(strict=True)
    if not repository.is_dir() or not node.is_file() or node.is_symlink():
        raise SuiteApplicabilityError(
            "SUITE_APPLICABILITY_RUNTIME_INVALID",
            "The native-Windows Node suite runtime is invalid.",
        )
    build = load_build_identity_manifest(options.build_identity_manifest)
    if build.deployment_kind != "native-windows":
        raise SuiteApplicabilityError(
            "SUITE_APPLICABILITY_DEPLOYMENT_INVALID",
            "The Node suite runner is not bound to a native-Windows BuildIdentity.",
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

    selection = select_native_windows_node_files(repository)
    with tempfile.TemporaryDirectory(prefix="node-junit-", dir=sandbox_root) as raw:
        junit_path = Path(raw) / "node-result.xml"
        try:
            completed = subprocess.run(
                [
                    str(node),
                    "--test",
                    "--test-reporter=junit",
                    f"--test-reporter-destination={junit_path}",
                    *selection.selected_files,
                ],
                cwd=repository,
                capture_output=True,
                check=False,
                shell=False,
            )
        except OSError as error:
            raise SuiteApplicabilityError(
                "SUITE_APPLICABILITY_RUNTIME_FAILED",
                "The native-Windows Node test process could not be started.",
            ) from error
        sys.stdout.buffer.write(completed.stdout)
        sys.stderr.buffer.write(completed.stderr)
        result_document = _read_node_junit_result(junit_path)

    try:
        exclusive_write_bytes(result_output, canonical_json_bytes(result_document))
    except DatabaseIdentityError as error:
        raise SuiteApplicabilityError(error.code, str(error)) from error
    create_node_suite_applicability_report(
        selection=selection,
        build_identity=build,
        selected_tests=result_document["tests"],
        output=applicability_output,
    )
    clean = result_document["fail"] == 0 and result_document["skipped"] == 0
    if completed.returncode != 0:
        return completed.returncode
    return 0 if clean else 1


def _read_node_junit_result(path: Path) -> dict[str, int]:
    try:
        root = ET.fromstring(path.read_bytes())
    except (OSError, ET.ParseError) as error:
        raise SuiteApplicabilityError(
            "SUITE_APPLICABILITY_RESULT_INVALID",
            "The native-Windows Node JUnit result is missing or malformed.",
        ) from error
    cases = [element for element in root.iter() if _local_name(element.tag) == "testcase"]
    if not cases:
        raise SuiteApplicabilityError(
            "SUITE_APPLICABILITY_RESULT_INVALID",
            "The native-Windows Node JUnit result contains no tests.",
        )
    failures = 0
    skipped = 0
    for case in cases:
        children = {_local_name(child.tag) for child in case}
        failed = bool({"failure", "error"} & children) or any(
            field in case.attrib for field in ("failure", "error")
        )
        was_skipped = "skipped" in children or "skipped" in case.attrib
        if failed and was_skipped:
            raise SuiteApplicabilityError(
                "SUITE_APPLICABILITY_RESULT_INVALID",
                "A Node JUnit test cannot be both failed and skipped.",
            )
        failures += int(failed)
        skipped += int(was_skipped)
    total = len(cases)
    return {
        "tests": total,
        "pass": total - failures - skipped,
        "fail": failures,
        "skipped": skipped,
    }


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _load_isolation(path: Path) -> tuple[Path, Path]:
    resolved = path.resolve(strict=True)
    try:
        document = json.loads(resolved.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise SuiteApplicabilityError(
            "SUITE_APPLICABILITY_ISOLATION_INVALID",
            "The Node suite isolation manifest is invalid.",
        ) from error
    expected_environment = os.environ.get("P6_SUITE_ISOLATION_MANIFEST")
    sandbox_value = document.get("sandboxRoot") if isinstance(document, dict) else None
    if (
        not isinstance(document, dict)
        or document.get("manifestKind") != "suite-isolation"
        or document.get("suiteKey") != "node-suite"
        or expected_environment != str(resolved)
        or not isinstance(sandbox_value, str)
    ):
        raise SuiteApplicabilityError(
            "SUITE_APPLICABILITY_ISOLATION_INVALID",
            "The Node suite is not bound to its exact isolation manifest.",
        )
    sandbox = Path(sandbox_value).resolve(strict=True)
    run_root = resolved.parent.resolve(strict=True)
    try:
        sandbox.relative_to(run_root)
    except ValueError as error:
        raise SuiteApplicabilityError(
            "SUITE_APPLICABILITY_ISOLATION_INVALID",
            "The Node suite sandbox escaped the evidence run.",
        ) from error
    if not sandbox.is_dir():
        raise SuiteApplicabilityError(
            "SUITE_APPLICABILITY_ISOLATION_INVALID",
            "The Node suite sandbox is not a directory.",
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
