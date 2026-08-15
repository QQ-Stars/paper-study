from __future__ import annotations

from collections.abc import Collection
from datetime import datetime
from typing import Protocol

from backend.app.domain.processing import (
    EnqueueResult,
    JobEventListQuery,
    JobFailure,
    JobLease,
    JobListQuery,
    JobProgress,
    JobResult,
    JobSpecV1,
    NewProcessingJob,
    Page,
    ProcessingJobEvent,
    StoredJobSpec,
)


class ProcessingQueue(Protocol):
    async def enqueue(self, spec: JobSpecV1, *, now: datetime) -> EnqueueResult: ...
    async def get(self, job_id: str) -> NewProcessingJob | None: ...
    async def cancel(self, job_id: str, *, now: datetime) -> NewProcessingJob: ...
    async def retry(self, job_id: str, *, now: datetime) -> EnqueueResult: ...
    async def list(self, query: JobListQuery) -> Page: ...
    async def list_events(self, job_id: str, query: JobEventListQuery) -> Page: ...
    async def claim_next(
        self,
        *,
        worker_id: str,
        now: datetime,
        lease_seconds: int,
        job_types: Collection[str] | None = None,
    ) -> JobLease | None: ...
    async def check_active(self, lease: JobLease, *, now: datetime) -> bool: ...
    async def renew(self, lease: JobLease, *, now: datetime, lease_seconds: int) -> JobLease: ...
    async def report_progress(self, lease: JobLease, progress: JobProgress, *, now: datetime) -> None: ...
    async def complete(self, lease: JobLease, result: JobResult, *, now: datetime) -> NewProcessingJob: ...
    async def fail(
        self,
        lease: JobLease,
        failure: JobFailure,
        *,
        now: datetime,
        result: JobResult | None = None,
    ) -> NewProcessingJob: ...


class ProcessingJobRepository(Protocol):
    async def insert_with_spec(self, job: NewProcessingJob, *, spec_json: str, spec_sha256: str) -> EnqueueResult: ...
    async def load_spec(self, job_id: str) -> StoredJobSpec: ...
    async def copy_spec_for_retry(self, parent_job_id: str, descendant: NewProcessingJob) -> EnqueueResult: ...
