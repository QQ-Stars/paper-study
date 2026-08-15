from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import xml.etree.ElementTree as ET

from backend.app.api.compat.database_identity import (
    DatabaseIdentityError,
    canonical_json_bytes,
    exclusive_write_bytes,
)


_ADAPTERS = {"unittest", "node-test", "vitest", "playwright", "check"}


class MachineSummaryError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class MachineSummaryFailure(MachineSummaryError):
    pass


@dataclass(frozen=True, slots=True)
class MachineSummary:
    adapter: str
    raw_exit: int
    totals: int
    failures: int
    skips: int
    manifest_path: Path
    canonical_bytes: bytes


def create_machine_summary(
    *,
    adapter: str,
    raw_exit: int,
    result_artifact: str | os.PathLike[str] | None,
    summary_output: str | os.PathLike[str],
    console_stdout: bytes = b"",
    console_stderr: bytes = b"",
) -> MachineSummary:
    del console_stdout, console_stderr
    if adapter not in _ADAPTERS:
        raise MachineSummaryError("MACHINE_ADAPTER_INVALID", "The runner adapter is invalid.")
    if not isinstance(raw_exit, int) or isinstance(raw_exit, bool) or raw_exit < 0:
        raise MachineSummaryError("MACHINE_EXIT_INVALID", "The raw exit code is invalid.")
    if adapter == "check":
        if result_artifact is not None:
            raise MachineSummaryError(
                "MACHINE_RESULT_INVALID",
                "The check adapter derives its one result only from the raw exit code.",
            )
        totals, failures, skips = 1, int(raw_exit != 0), 0
        artifact_path: Path | None = None
        artifact_format = "raw-exit"
    else:
        if result_artifact is None:
            raise MachineSummaryError(
                "MACHINE_RESULT_MISSING",
                "A structured runner result artifact is required.",
            )
        artifact_path = Path(result_artifact).resolve(strict=False)
        if not artifact_path.is_file():
            raise MachineSummaryError(
                "MACHINE_RESULT_MISSING",
                "The structured runner result artifact does not exist.",
            )
        totals, failures, skips, artifact_format = _read_counts(adapter, artifact_path)
        if (raw_exit == 0) != (failures == 0):
            raise MachineSummaryError(
                "MACHINE_RESULT_CONTRADICTORY",
                "The structured result contradicts the raw process exit.",
            )
    document = {
        "schemaVersion": 1,
        "manifestKind": "machine-summary",
        "adapter": adapter,
        "rawExit": raw_exit,
        "totals": totals,
        "failures": failures,
        "skips": skips,
        "resultArtifactPath": str(artifact_path) if artifact_path is not None else None,
        "resultArtifactFormat": artifact_format,
    }
    payload = canonical_json_bytes(document)
    output = Path(summary_output).resolve(strict=False)
    try:
        exclusive_write_bytes(output, payload)
    except DatabaseIdentityError as error:
        raise MachineSummaryError(error.code, str(error)) from error
    summary = MachineSummary(
        adapter=adapter,
        raw_exit=raw_exit,
        totals=totals,
        failures=failures,
        skips=skips,
        manifest_path=output.resolve(strict=True),
        canonical_bytes=payload,
    )
    if failures or skips or raw_exit:
        raise MachineSummaryFailure(
            "MACHINE_RESULT_NOT_CLEAN",
            "The runner did not produce a zero-exit, zero-failure, zero-skip result.",
        )
    return summary


def _read_counts(adapter: str, path: Path) -> tuple[int, int, int, str]:
    payload = path.read_bytes()
    if payload.lstrip().startswith(b"<"):
        try:
            root = ET.fromstring(payload)
        except ET.ParseError as error:
            raise MachineSummaryError("MACHINE_RESULT_INVALID", "JUnit XML is malformed.") from error
        suites = [root] if root.tag.endswith("testsuite") else list(root.findall(".//testsuite"))
        if not suites:
            raise MachineSummaryError("MACHINE_RESULT_INVALID", "JUnit XML has no test suite.")
        return (
            sum(_xml_count(suite, "tests") for suite in suites),
            sum(_xml_count(suite, "failures") + _xml_count(suite, "errors") for suite in suites),
            sum(_xml_count(suite, "skipped") for suite in suites),
            "junit-xml",
        )
    try:
        document = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise MachineSummaryError("MACHINE_RESULT_INVALID", "Runner JSON is malformed.") from error
    if not isinstance(document, dict):
        raise MachineSummaryError("MACHINE_RESULT_INVALID", "Runner JSON must be an object.")
    try:
        if adapter == "unittest":
            totals = _count(document, "testsRun")
            failures = len(_list(document, "failures")) + len(_list(document, "errors"))
            skips = len(_list(document, "skipped"))
        elif adapter == "node-test":
            totals = _count(document, "tests")
            failures = _count(document, "fail")
            skips = _count(document, "skipped")
            if _count(document, "pass") + failures + skips != totals:
                raise ValueError
        elif adapter == "vitest":
            totals = _count(document, "numTotalTests")
            failures = _count(document, "numFailedTests")
            skips = _count(document, "numPendingTests")
        else:
            stats = document.get("stats")
            if not isinstance(stats, dict):
                raise ValueError
            expected = _count(stats, "expected")
            failures = _count(stats, "unexpected")
            skips = _count(stats, "skipped")
            totals = expected + failures + skips
    except (KeyError, TypeError, ValueError) as error:
        raise MachineSummaryError(
            "MACHINE_RESULT_INVALID",
            "Runner JSON does not match the selected adapter schema.",
        ) from error
    if failures > totals or skips > totals or failures + skips > totals:
        raise MachineSummaryError("MACHINE_RESULT_INVALID", "Runner counts are inconsistent.")
    return totals, failures, skips, "json"


def _count(document: dict[str, object], field: str) -> int:
    value = document[field]
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError
    return value


def _list(document: dict[str, object], field: str) -> list[object]:
    value = document[field]
    if not isinstance(value, list):
        raise ValueError
    return value


def _xml_count(element: ET.Element, field: str) -> int:
    raw = element.attrib.get(field, "0")
    try:
        value = int(raw)
    except ValueError as error:
        raise MachineSummaryError("MACHINE_RESULT_INVALID", "JUnit count is invalid.") from error
    if value < 0:
        raise MachineSummaryError("MACHINE_RESULT_INVALID", "JUnit count is invalid.")
    return value


__all__ = [
    "MachineSummary",
    "MachineSummaryError",
    "MachineSummaryFailure",
    "create_machine_summary",
]
