from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest

from backend.app.api.compat.database_identity import exclusive_write_bytes

from backend.app.api.compat.machine_summary import (
    MachineSummaryError,
    MachineSummaryFailure,
    create_machine_summary,
)


class _DirectTextRunner(unittest.TextTestRunner):
    def __init__(self, *args: object, **kwargs: object) -> None:
        kwargs["stream"] = sys.stderr
        kwargs["verbosity"] = 2
        super().__init__(*args, **kwargs)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="machine-summary-runner")
    parser.add_argument(
        "--adapter",
        required=True,
        choices=("unittest", "node-test", "vitest", "playwright", "check"),
    )
    parser.add_argument("--summary-output", required=True)
    parser.add_argument("--result-artifact")
    parser.add_argument("--isolation-manifest")
    parser.add_argument("child_argv", nargs=argparse.REMAINDER)
    arguments = parser.parse_args(argv)
    child = arguments.child_argv
    if child and child[0] == "--":
        child = child[1:]
    if not child:
        parser.error("a child command is required after --")
    isolation_snapshot = None
    isolation_environment: dict[str, str] = {}
    if arguments.isolation_manifest is not None:
        isolation_snapshot, isolation_environment = _load_isolation(
            Path(arguments.isolation_manifest)
        )
    previous_environment = {
        key: os.environ.get(key)
        for key in isolation_environment
    }
    os.environ.update(isolation_environment)
    try:
        return _run_with_isolation(
            arguments,
            child,
            isolation_snapshot=isolation_snapshot,
        )
    finally:
        for key, value in previous_environment.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def _run_with_isolation(
    arguments: argparse.Namespace,
    child: list[str],
    *,
    isolation_snapshot: tuple[Path, bytes, dict[str, object]] | None,
) -> int:
    if arguments.adapter == "unittest" and arguments.result_artifact is None:
        result = _run_unittest_direct(
            child,
            summary_output=Path(arguments.summary_output),
        )
    else:
        completed = subprocess.run(
            child,
            capture_output=True,
            check=False,
            shell=False,
        )
        sys.stdout.buffer.write(completed.stdout)
        sys.stderr.buffer.write(completed.stderr)
        try:
            create_machine_summary(
                adapter=arguments.adapter,
                raw_exit=completed.returncode,
                result_artifact=(
                    Path(arguments.result_artifact)
                    if arguments.result_artifact is not None
                    else None
                ),
                summary_output=Path(arguments.summary_output),
                console_stdout=completed.stdout,
                console_stderr=completed.stderr,
            )
        except MachineSummaryFailure:
            result = completed.returncode or 1
        except MachineSummaryError as error:
            print(f"{error.code}: {error}", file=sys.stderr)
            result = completed.returncode or 2
        else:
            result = 0
    if isolation_snapshot is not None:
        _verify_isolation_unchanged(isolation_snapshot)
    return result


def _load_isolation(
    path: Path,
) -> tuple[tuple[Path, bytes, dict[str, object]], dict[str, str]]:
    try:
        resolved = path.resolve(strict=True)
        payload = resolved.read_bytes()
        document = json.loads(payload.decode("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise SystemExit(f"MACHINE_ISOLATION_INVALID: {error}") from error
    if (
        not isinstance(document, dict)
        or document.get("schemaVersion") != 1
        or document.get("manifestKind") != "suite-isolation"
        or document.get("denyNetwork") is not True
        or document.get("denyProviders") is not True
        or document.get("liveAccessCount") != 0
    ):
        raise SystemExit("MACHINE_ISOLATION_INVALID: isolation manifest is not zero-access")
    run_root = resolved.parent
    roots = {
        key: document.get(key)
        for key in (
            "sandboxRoot",
            "databasePath",
            "settingsPath",
            "pdfRoot",
            "vaultRoot",
            "keyringRoot",
        )
    }
    if any(not isinstance(value, str) or not _is_below(Path(value).resolve(strict=False), run_root) for value in roots.values()):
        raise SystemExit("MACHINE_ISOLATION_INVALID: sandbox root escaped the run root")
    environment = {
        "P6_SUITE_ISOLATION_MANIFEST": str(resolved),
        "P6_SUITE_SANDBOX_ROOT": str(roots["sandboxRoot"]),
        "P6_SUITE_DATABASE_PATH": str(roots["databasePath"]),
        "P6_SUITE_SETTINGS_PATH": str(roots["settingsPath"]),
        "P6_SUITE_PDF_ROOT": str(roots["pdfRoot"]),
        "P6_SUITE_VAULT_ROOT": str(roots["vaultRoot"]),
        "P6_SUITE_KEYRING_ROOT": str(roots["keyringRoot"]),
        "P6_SUITE_DENY_NETWORK": "1",
        "P6_SUITE_DENY_PROVIDERS": "1",
    }
    return (resolved, payload, document), environment


def _verify_isolation_unchanged(
    snapshot: tuple[Path, bytes, dict[str, object]],
) -> None:
    path, payload, _document = snapshot
    try:
        current = path.read_bytes()
    except OSError as error:
        raise SystemExit(f"MACHINE_ISOLATION_DRIFT: {error}") from error
    if current != payload or hashlib.sha256(current).hexdigest() != hashlib.sha256(payload).hexdigest():
        raise SystemExit("MACHINE_ISOLATION_DRIFT: isolation manifest changed during suite")


def _is_below(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _run_unittest_direct(child: list[str], *, summary_output: Path) -> int:
    try:
        module_index = child.index("-m")
    except ValueError:
        print("MACHINE_UNITTEST_ARGV_INVALID: unittest requires python -m unittest.", file=sys.stderr)
        return 2
    if module_index + 1 >= len(child) or child[module_index + 1] != "unittest":
        print("MACHINE_UNITTEST_ARGV_INVALID: unittest requires python -m unittest.", file=sys.stderr)
        return 2
    try:
        executable = Path(child[0]).resolve(strict=True)
    except OSError:
        print("MACHINE_UNITTEST_ARGV_INVALID: the Python executable is invalid.", file=sys.stderr)
        return 2
    if executable != Path(sys.executable).resolve(strict=True):
        print("MACHINE_UNITTEST_ARGV_INVALID: the Python executable identity changed.", file=sys.stderr)
        return 2
    # The outer backend discovery leaves unittest.defaultTestLoader bound to the
    # repository root.  A direct adapter may intentionally discover a fresh
    # run-local temporary directory, so give the nested invocation an isolated
    # loader and top-level-directory state.
    program = unittest.main(
        module=None,
        argv=["unittest", *child[module_index + 2 :]],
        testRunner=_DirectTextRunner,
        testLoader=unittest.TestLoader(),
        exit=False,
    )
    result = program.result
    if result is None:
        print("MACHINE_UNITTEST_RESULT_MISSING: unittest returned no TestResult.", file=sys.stderr)
        return 2
    result_artifact = summary_output.with_suffix(".unittest-result.json")
    result_document = {
        "testsRun": result.testsRun,
        "failures": [str(test) for test, _ in result.failures],
        "errors": [str(test) for test, _ in result.errors],
        "skipped": [str(test) for test, _ in result.skipped],
    }
    try:
        exclusive_write_bytes(
            result_artifact,
            json.dumps(result_document, separators=(",", ":")).encode("utf-8"),
        )
        create_machine_summary(
            adapter="unittest",
            raw_exit=0 if result.wasSuccessful() else 1,
            result_artifact=result_artifact,
            summary_output=summary_output,
        )
    except MachineSummaryFailure:
        return 0 if result.wasSuccessful() and not result.skipped else 1
    except (MachineSummaryError, OSError) as error:
        print(f"MACHINE_UNITTEST_SUMMARY_INVALID: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
