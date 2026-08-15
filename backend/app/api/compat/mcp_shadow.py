from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
from threading import Lock
from typing import Callable


_MISSING = object()
_SOURCE_STATUSES = {"queued", "running", "ready", "failed", "stale", "cancelled"}
_SAFE_CODE = re.compile(r"[A-Z][A-Z0-9_]{0,63}\Z")
_WRITE_LOCK = Lock()


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _length(value: object) -> int:
    return len(_canonical_bytes(value))


def _source_document_addition_is_approved(value: object) -> bool:
    if not isinstance(value, dict) or set(value) != {"native", "ocr"}:
        return False
    for mode in ("native", "ocr"):
        view = value[mode]
        if view is None:
            continue
        if not isinstance(view, dict) or set(view) != {
            "currentId", "status", "updatedAt", "error"
        }:
            return False
        if (
            not isinstance(view["currentId"], str)
            or not 0 < len(view["currentId"]) <= 256
            or view["status"] not in _SOURCE_STATUSES
            or not isinstance(view["updatedAt"], str)
            or not 0 < len(view["updatedAt"]) <= 64
        ):
            return False
        error = view["error"]
        if error is None:
            continue
        if (
            not isinstance(error, dict)
            or set(error) != {"code", "message"}
            or not isinstance(error["code"], str)
            or _SAFE_CODE.fullmatch(error["code"]) is None
            or error["message"] != "Processing failed."
        ):
            return False
    return True


def canonical_diffs(
    tool: str,
    legacy: object,
    application: object,
) -> list[dict[str, object]]:
    diffs: list[dict[str, object]] = []

    def add(path: str, category: str, left: object, right: object) -> None:
        diffs.append(
            {
                "path": path,
                "category": category,
                "legacyHash": None if left is _MISSING else _digest(left),
                "legacyLength": None if left is _MISSING else _length(left),
                "applicationHash": None if right is _MISSING else _digest(right),
                "applicationLength": None if right is _MISSING else _length(right),
            }
        )

    def walk(path: str, left: object, right: object) -> None:
        if left is _MISSING:
            approved = (
                tool == "get_paper"
                and path == "$.sourceDocument"
                and _source_document_addition_is_approved(right)
            )
            add(
                path,
                "approved_additive_optional" if approved else "unexpected_addition",
                left,
                right,
            )
            return
        if right is _MISSING:
            add(path, "missing_in_application", left, right)
            return
        if type(left) is not type(right):
            add(path, "type_mismatch", left, right)
            return
        if isinstance(left, dict):
            for key in sorted(set(left) | set(right)):
                walk(
                    f"{path}.{key}",
                    left.get(key, _MISSING),
                    right.get(key, _MISSING),
                )
            return
        if isinstance(left, list):
            for index in range(max(len(left), len(right))):
                walk(
                    f"{path}[{index}]",
                    left[index] if index < len(left) else _MISSING,
                    right[index] if index < len(right) else _MISSING,
                )
            return
        if left != right:
            add(path, "value_mismatch", left, right)

    walk("$", legacy, application)
    diffs.sort(
        key=lambda item: (
            item["category"] == "approved_additive_optional",
            str(item["path"]),
        )
    )
    return diffs


class ShadowRecorder:
    def __init__(self, path: str | Path, *, allowed_root: str | Path) -> None:
        root = Path(allowed_root).expanduser().resolve()
        target = Path(path).expanduser().resolve()
        try:
            target.relative_to(root)
        except ValueError as error:
            raise ValueError("MCP shadow path must stay inside the configured root") from error
        self._path = target
        self._root = root

    @property
    def path(self) -> Path:
        return self._path

    def record(
        self,
        *,
        tool: str,
        fixture: str,
        legacy: object,
        application: object,
        source_identity: str,
        build_identity: str,
    ) -> dict[str, object]:
        observation = {
            "schemaVersion": 1,
            "tool": tool,
            "fixture": fixture,
            "sourceIdentity": source_identity,
            "buildIdentity": build_identity,
            "legacyHash": _digest(legacy),
            "legacyLength": _length(legacy),
            "applicationHash": _digest(application),
            "applicationLength": _length(application),
            "diffs": canonical_diffs(tool, legacy, application),
        }
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with _WRITE_LOCK:
            with self._path.open("a", encoding="utf-8", newline="\n") as stream:
                stream.write(
                    json.dumps(
                        observation,
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    )
                    + "\n"
                )
        return observation


def shadow_call(
    *,
    tool: str,
    fixture: str,
    legacy_call: Callable[[], object],
    application_call: Callable[[], object],
    recorder: ShadowRecorder,
    source_identity: str,
    build_identity: str,
) -> object:
    legacy = legacy_call()
    try:
        application = application_call()
    except Exception:
        application = {
            "ok": False,
            "error": "MCP application shadow failed",
            "code": "MCP_APPLICATION_READ_FAILED",
        }
    recorder.record(
        tool=tool,
        fixture=fixture,
        legacy=legacy,
        application=application,
        source_identity=source_identity,
        build_identity=build_identity,
    )
    return legacy


@dataclass(frozen=True, slots=True)
class ShadowConvergenceResult:
    ok: bool
    code: str | None
    reasons: tuple[str, ...]


def evaluate_shadow_window(
    observations: list[dict[str, object]],
    *,
    evidence: dict[str, object],
    required_tools: set[str],
    required_fixtures: set[str],
) -> ShadowConvergenceResult:
    reasons: set[str] = set()
    expected_coverage = {
        (tool, fixture)
        for tool in required_tools
        for fixture in required_fixtures
    }
    observed_coverage: set[tuple[str, str]] = set()

    source_identity = evidence.get("sourceIdentity")
    build_identity = evidence.get("buildIdentity")
    if not isinstance(source_identity, str) or not source_identity:
        reasons.add("source_identity_missing")
    if not isinstance(build_identity, str) or not build_identity:
        reasons.add("build_identity_missing")
    for key in ("readOnly", "zeroEnqueue", "zeroOcr"):
        if evidence.get(key) is not True:
            reasons.add(f"evidence_{key}_not_true")

    approved_normal_seen = False
    for index, observation in enumerate(observations):
        tool = observation.get("tool")
        fixture = observation.get("fixture")
        if isinstance(tool, str) and isinstance(fixture, str):
            observed_coverage.add((tool, fixture))
        else:
            reasons.add(f"observation_{index}_identity_invalid")
            continue
        if tool not in required_tools or fixture not in required_fixtures:
            reasons.add(f"unexpected_coverage:{tool}:{fixture}")
        if observation.get("sourceIdentity") != source_identity:
            reasons.add(f"source_identity_mismatch:{tool}:{fixture}")
        if observation.get("buildIdentity") != build_identity:
            reasons.add(f"build_identity_mismatch:{tool}:{fixture}")
        diffs = observation.get("diffs")
        if not isinstance(diffs, list):
            reasons.add(f"diffs_invalid:{tool}:{fixture}")
            continue
        for diff in diffs:
            if not isinstance(diff, dict):
                reasons.add(f"diff_invalid:{tool}:{fixture}")
                continue
            approved = (
                tool == "get_paper"
                and fixture == "normal"
                and diff.get("path") == "$.sourceDocument"
                and diff.get("category") == "approved_additive_optional"
                and diff.get("legacyHash") is None
                and isinstance(diff.get("applicationHash"), str)
                and re.fullmatch(r"[0-9a-f]{64}", str(diff["applicationHash"]))
                is not None
            )
            if approved:
                if approved_normal_seen:
                    reasons.add("approved_addition_duplicate")
                approved_normal_seen = True
            else:
                reasons.add(f"unexplained_diff:{tool}:{fixture}")

    missing = expected_coverage - observed_coverage
    for tool, fixture in sorted(missing):
        reasons.add(f"coverage_missing:{tool}:{fixture}")
    if not approved_normal_seen:
        reasons.add("approved_addition_missing")

    if reasons:
        return ShadowConvergenceResult(
            ok=False,
            code="MCP_SHADOW_NOT_CONVERGED",
            reasons=tuple(sorted(reasons)),
        )
    return ShadowConvergenceResult(ok=True, code=None, reasons=())
