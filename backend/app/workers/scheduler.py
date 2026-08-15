from __future__ import annotations

"""Role-scoped scheduler facade for legacy ingest schedules."""

from dataclasses import dataclass
from datetime import datetime, timezone
import asyncio
from typing import Any, Callable


class SchedulerAlreadyOwnedError(RuntimeError):
    code = "SCHEDULER_ALREADY_OWNED"


@dataclass(frozen=True, slots=True)
class SchedulerTickResult:
    job_ids: tuple[int, ...]
    ticked_at: datetime


class LegacyScheduler:
    """Drive ``LegacyIngestService.tick`` in its own process role.

    Ownership is supplied by the runtime coordinator in P4; this class only
    owns the schedule tick interval and never touches P2 ``processing_jobs``.
    """

    def __init__(
        self,
        ingest_service: Any,
        *,
        clock: Callable[[], datetime] | None = None,
        interval_seconds: float = 600.0,
    ) -> None:
        self.ingest_service = ingest_service
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.interval_seconds = interval_seconds
        self._stop = asyncio.Event()

    async def tick(self) -> SchedulerTickResult:
        values = await self.ingest_service.tick()
        return SchedulerTickResult(tuple(int(value) for value in values), self.clock())

    async def run(self) -> None:
        while not self._stop.is_set():
            await self.tick()
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.interval_seconds)
            except asyncio.TimeoutError:
                continue

    def stop(self) -> None:
        self._stop.set()


__all__ = ["LegacyScheduler", "SchedulerAlreadyOwnedError", "SchedulerTickResult"]
