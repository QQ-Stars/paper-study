from __future__ import annotations

import argparse
import asyncio
from collections.abc import Callable, Mapping, Sequence
import json
import os
import signal
import sys
from typing import Any, TextIO

from backend.app.bootstrap import bootstrap_processing_worker, verify_schema_revision
from backend.app.config import DatabaseSettings
from backend.app.domain import DomainError, WorkerConfigurationError
from backend.app.domain.context import EmbeddingProfile


P3_SCHEMA_REVISION = "20260807_03"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="study-processing-worker")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--once", action="store_true")
    mode.add_argument("--forever", action="store_true")
    return parser


async def run(
    arguments: Sequence[str] | None = None,
    *,
    environment: Mapping[str, str] | None = None,
    stderr: TextIO | None = None,
    worker_factory: Callable[[DatabaseSettings], Any] | None = None,
    waiter: Callable[[asyncio.Event, float], Any] | None = None,
    signal_registrar: Callable[[asyncio.Event], Callable[[], None]] | None = None,
    translation_provider_factory: Callable[[], Any] | None = None,
    structured_provider_factory: Callable[[], Any] | None = None,
    embedding_profile: EmbeddingProfile | None = None,
    embedding_provider_factory: Callable[[EmbeddingProfile, Any], Any] | None = None,
    credential_store: Any = None,
) -> int:
    target_stderr = stderr or sys.stderr
    options = _parser().parse_args(arguments)
    values = os.environ if environment is None else environment
    try:
        settings = DatabaseSettings(values.get("DB_PATH"))
        verify_schema_revision(settings, P3_SCHEMA_REVISION)
    except (DomainError, ValueError) as error:
        _write_error(target_stderr, error)
        return 2

    container = None
    if worker_factory is None:
        try:
            container = bootstrap_processing_worker(
                settings,
                required_schema_revision=P3_SCHEMA_REVISION,
                worker_id=f"processing-worker-{os.getpid()}",
                translation_provider_factory=translation_provider_factory,
                structured_provider_factory=structured_provider_factory,
                embedding_profile=embedding_profile,
                embedding_provider_factory=embedding_provider_factory,
                credential_store=credential_store,
            )
        except (DomainError, RuntimeError, ValueError):
            _write_error(target_stderr, WorkerConfigurationError())
            return 2
        worker = container.processing_worker
    else:
        worker = worker_factory(settings)
    try:
        if options.once:
            await worker.run_once()
            return 0
        stop_event = asyncio.Event()
        cleanup = (signal_registrar or install_signal_handlers)(stop_event)
        try:
            await worker.run_forever(stop_event=stop_event, waiter=waiter)
        finally:
            cleanup()
        return 0
    finally:
        if container is not None:
            await container.dispose()


def install_signal_handlers(
    stop_event: asyncio.Event,
    *,
    signal_api: Any = signal,
) -> Callable[[], None]:
    previous: dict[Any, Any] = {}

    def request_stop(_signum: object, _frame: object) -> None:
        stop_event.set()

    for name in ("SIGINT", "SIGTERM"):
        signum = getattr(signal_api, name, None)
        if signum is None:
            continue
        previous[signum] = signal_api.getsignal(signum)
        signal_api.signal(signum, request_stop)

    def cleanup() -> None:
        for signum, handler in previous.items():
            signal_api.signal(signum, handler)

    return cleanup


def _write_error(stderr: TextIO, error: Exception) -> None:
    code = getattr(error, "code", "WORKER_CONFIGURATION_INVALID")
    details = dict(getattr(error, "details", {}))
    payload = {
        "error": {
            "code": code,
            "message": str(error),
            "details": details,
        }
    }
    stderr.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n")


def main(arguments: Sequence[str] | None = None) -> int:
    return asyncio.run(run(arguments))


if __name__ == "__main__":
    raise SystemExit(main())
