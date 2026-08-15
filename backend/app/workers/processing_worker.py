from __future__ import annotations

import asyncio
from collections.abc import Callable, Collection, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from backend.app.domain import DomainError, JobLeaseLostError
from backend.app.domain.processing import JobFailure, JobProgress, JobResult


@dataclass(frozen=True, slots=True)
class ProcessingHandlerOutcome:
    """Explicitly tells the loop that a handler settled atomically itself."""

    job_already_settled: bool

    @classmethod
    def settled(cls) -> "ProcessingHandlerOutcome":
        return cls(job_already_settled=True)


class ProcessingWorker:
    """Single-consumer processing queue adapter."""

    def __init__(
        self,
        unit_of_work_factory: Callable[[], Any],
        *,
        handlers: Mapping[str, Any],
        worker_id: str,
        clock: Callable[[], datetime] | None = None,
        lease_seconds: int = 30,
        heartbeat_interval_seconds: float | None = None,
        heartbeat_waiter: Callable[[float], Any] | None = None,
        logger: Callable[[Mapping[str, object]], None] | None = None,
        claim_job_types: Collection[str] | None = None,
    ) -> None:
        if not isinstance(worker_id, str) or not worker_id.strip():
            raise ValueError("worker_id must be nonblank")
        if not isinstance(lease_seconds, int) or isinstance(lease_seconds, bool) or lease_seconds < 1:
            raise ValueError("lease_seconds must be positive")
        heartbeat_interval = lease_seconds / 3 if heartbeat_interval_seconds is None else heartbeat_interval_seconds
        if (
            not isinstance(heartbeat_interval, (int, float))
            or isinstance(heartbeat_interval, bool)
            or not 0 < heartbeat_interval < lease_seconds
        ):
            raise ValueError("heartbeat_interval_seconds must be positive and shorter than the lease")
        self._unit_of_work_factory = unit_of_work_factory
        self._handlers = dict(handlers)
        self._worker_id = worker_id
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._lease_seconds = lease_seconds
        self._heartbeat_interval_seconds = float(heartbeat_interval)
        self._heartbeat_waiter = heartbeat_waiter or asyncio.sleep
        self._logger = logger or (lambda _record: None)
        self._claim_job_types = (
            None if claim_job_types is None else frozenset(claim_job_types)
        )

    @property
    def handler_types(self) -> frozenset[str]:
        return frozenset(self._handlers)

    async def run_once(self) -> bool:
        async with self._unit_of_work_factory() as work:
            lease = await work.jobs.claim_next(
                worker_id=self._worker_id,
                now=self._clock(),
                lease_seconds=self._lease_seconds,
                job_types=self._claim_job_types,
            )
            await work.commit()
        if lease is None:
            return False

        dispatch_error_code = getattr(lease.spec.value, "dispatch_error_code", None)
        if dispatch_error_code is not None:
            async with self._unit_of_work_factory() as work:
                await work.jobs.fail(
                    lease,
                    JobFailure(code=dispatch_error_code, retryable=False),
                    now=self._clock(),
                )
                await work.commit()
            self._log_settlement(lease, stage="failed", code=dispatch_error_code)
            return True

        handler = self._handlers.get(lease.spec.value.job_type)
        if handler is None:
            async with self._unit_of_work_factory() as work:
                await work.jobs.fail(
                    lease,
                    JobFailure(code="JOB_TYPE_UNSUPPORTED", retryable=False),
                    now=self._clock(),
                )
                await work.commit()
            self._log_settlement(
                lease,
                stage="failed",
                code="JOB_TYPE_UNSUPPORTED",
            )
            return True
        try:
            result = await self._run_handler_with_heartbeat(handler, lease)
        except JobLeaseLostError:
            # A replacement owner may already be running this job.  The stale
            # token cannot mutate; a still-current cancel request is settled by
            # the queue's fenced checkpoint path.
            await self._checkpoint_cancellation(lease)
            return True
        except DomainError as error:
            failure = JobFailure(
                code=error.code,
                retryable=bool(getattr(error, "retryable", False)),
            )
            try:
                async with self._unit_of_work_factory() as work:
                    failure_result = getattr(error, "result", None)
                    fail_kwargs = {
                        "now": self._clock(),
                        "retry_after_seconds": getattr(error, "retry_after_seconds", None),
                    }
                    if isinstance(failure_result, JobResult):
                        fail_kwargs["result"] = failure_result
                    await work.jobs.fail(lease, failure, **fail_kwargs)
                    await work.commit()
            except JobLeaseLostError:
                return True
            will_retry = failure.retryable and lease.job.attempt < lease.job.max_attempts
            self._log_settlement(
                lease,
                stage="retry_scheduled" if will_retry else "failed",
                code=failure.code,
            )
            return True
        if isinstance(result, ProcessingHandlerOutcome):
            if not result.job_already_settled:
                raise TypeError("processing handler outcome must be settled")
            return True
        if not isinstance(result, JobResult):
            raise TypeError("processing handlers must return JobResult")
        try:
            async with self._unit_of_work_factory() as work:
                await work.jobs.complete(lease, result, now=self._clock())
                await work.commit()
        except JobLeaseLostError:
            # The handler outlived its lease.  Completion is the worker's final
            # checkpoint, so an expired owner must leave recovery to a future claim.
            return True
        return True

    async def _checkpoint_cancellation(self, lease: Any) -> None:
        try:
            async with self._unit_of_work_factory() as work:
                await work.jobs.report_progress(
                    lease,
                    JobProgress({}),
                    now=self._clock(),
                )
                await work.commit()
        except JobLeaseLostError:
            return

    async def _run_handler_with_heartbeat(self, handler: Any, lease: Any) -> Any:
        handler_task = asyncio.create_task(handler(lease))
        heartbeat_task = asyncio.create_task(self._heartbeat(lease))
        done, _pending = await asyncio.wait(
            (handler_task, heartbeat_task),
            return_when=asyncio.FIRST_COMPLETED,
        )
        if handler_task in done:
            heartbeat_task.cancel()
            await asyncio.gather(heartbeat_task, return_exceptions=True)
            return handler_task.result()

        handler_task.cancel()
        await asyncio.gather(handler_task, return_exceptions=True)
        return heartbeat_task.result()

    async def _heartbeat(self, lease: Any) -> None:
        while True:
            await self._heartbeat_waiter(self._heartbeat_interval_seconds)
            async with self._unit_of_work_factory() as work:
                await work.jobs.renew(
                    lease,
                    now=self._clock(),
                    lease_seconds=self._lease_seconds,
                )
                await work.commit()

    def _log_settlement(self, lease: Any, *, stage: str, code: str) -> None:
        self._logger(
            {
                "jobId": lease.job.id,
                "stage": stage,
                "attempt": lease.job.attempt,
                "code": code,
            }
        )

    async def run_forever(
        self,
        *,
        stop_event: asyncio.Event,
        waiter: Callable[[asyncio.Event, float], Any] | None = None,
        iteration_hook: Callable[[], Any] | None = None,
    ) -> None:
        wait_when_idle = waiter or _wait_for_stop
        while not stop_event.is_set():
            if iteration_hook is not None:
                await iteration_hook()
            processed = await self.run_once()
            if not processed and not stop_event.is_set():
                await wait_when_idle(stop_event, 1.0)


async def _wait_for_stop(stop_event: asyncio.Event, timeout_seconds: float) -> None:
    try:
        await asyncio.wait_for(stop_event.wait(), timeout=timeout_seconds)
    except asyncio.TimeoutError:
        return
