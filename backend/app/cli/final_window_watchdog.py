from __future__ import annotations

import argparse
import importlib
import json
from pathlib import Path
import sys
import time
from typing import Callable

from backend.app.application.final_window import (
    FinalWindowError,
    FinalWindowWatchdog,
    WatchdogResult,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="final-window-watchdog")
    parser.add_argument("--cutover-lease", required=True)
    parser.add_argument("--cutover-token-file", required=True)
    parser.add_argument("--recovery-output", required=True)
    parser.add_argument("--operations-factory", required=True)
    parser.add_argument("--poll-interval-seconds", type=float, default=1.0)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args(argv)
    if not 0.01 <= args.poll_interval_seconds <= 5.0:
        parser.error("--poll-interval-seconds must be between 0.01 and 5")
    try:
        operations = _load_factory(args.operations_factory)()
        watchdog = FinalWindowWatchdog(operations=operations)
        while True:
            result = watchdog.run_once(
                cutover_lease=Path(args.cutover_lease),
                cutover_token_file=Path(args.cutover_token_file),
                recovery_output=Path(args.recovery_output),
            )
            if args.once or result.action != "monitoring":
                print(json.dumps(_result_document(result), separators=(",", ":")))
                return 0
            time.sleep(args.poll_interval_seconds)
    except (
        FinalWindowError,
        ImportError,
        AttributeError,
        OSError,
        TypeError,
        ValueError,
    ) as error:
        code = getattr(error, "code", "CUTOVER_WATCHDOG_INVALID")
        print(json.dumps({"ok": False, "code": code}), file=sys.stderr)
        return 2


def _load_factory(value: str) -> Callable[[], object]:
    module_name, separator, attribute_path = value.partition(":")
    if not separator or not module_name or not attribute_path:
        raise ValueError("operations factory must be module:attribute")
    target: object = importlib.import_module(module_name)
    for segment in attribute_path.split("."):
        if not segment or segment.startswith("_"):
            raise ValueError("operations factory attribute is invalid")
        target = getattr(target, segment)
    if not callable(target):
        raise TypeError("operations factory is not callable")
    return target


def _result_document(result: WatchdogResult) -> dict[str, object]:
    document: dict[str, object] = {
        "ok": True,
        "action": result.action,
        "reasonCode": result.reason_code,
    }
    if result.recovery is not None:
        document.update(
            {
                "runId": result.recovery.run_id,
                "recoveryPath": str(result.recovery.path),
                "recoveryFileSha256": result.recovery.file_sha256,
            }
        )
    return document


if __name__ == "__main__":
    raise SystemExit(main())
