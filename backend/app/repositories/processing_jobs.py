from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
import secrets

from sqlalchemy import and_, func, or_, select, text, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.domain import (
    JobLeaseLostError,
    JobNotCancellableError,
    JobNotRetryableError,
    PersistenceConflictError,
    ProcessingJob,
)
from backend.app.domain.processing import (
    EnqueueResult,
    JobSpecValidationError,
    JobSpecV1,
    NewProcessingJob,
    StoredJobSpec,
    build_artifact_job_key,
    build_artifact_key,
    build_index_job_key,
    build_source_job_key,
    build_source_key,
    decode_job_spec_v1,
    hash_job_spec,
)
from backend.app.repositories.models import (
    GeneratedArtifactModel,
    ProcessingJobEventModel,
    ProcessingJobModel,
    SourceDocumentModel,
)


_NON_RETRYABLE_FAILURE_CODES = frozenset(
    {
        "NATIVE_TEXT_EMPTY",
        "OCR_RESPONSE_INVALID",
        "EXPLAINER_EMPTY",
        "PDF_ENCRYPTED",
        "ARTIFACT_PUBLICATION_CONFLICT",
        "MARKDOWN_STRUCTURE_INVALID",
        "TRANSLATION_CHECKPOINT_CONFLICT",
        "JOB_SPEC_UNRECOVERABLE",
    }
)


def _timestamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _json(value: dict[str, object]) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


@dataclass(frozen=True, slots=True)
class StoredProcessingJobEvent:
    sequence: int
    event_type: str
    progress_json: str
    error_code: str | None
    created_at: str


class SqlAlchemyProcessingJobRepository:
    """The only persistence boundary for immutable processing-job specs."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, identifier: str) -> ProcessingJob | None:
        row = await self._session.get(ProcessingJobModel, identifier)
        if row is None:
            return None
        return ProcessingJob(
            id=row.id,
            paper_id=row.paper_id,
            job_type=row.job_type,
            source_mode=row.source_mode,
            status=row.status,
            progress_json=row.progress_json,
            attempt=row.attempt,
            max_attempts=row.max_attempts,
            idempotency_key=row.idempotency_key,
            error_code=row.error_code,
            error_message=row.error_message,
            created_at=_datetime(row.created_at),
            started_at=_datetime(row.started_at),
            finished_at=_datetime(row.finished_at),
            cancelled_at=_datetime(row.cancelled_at),
        )

    async def get_api_row(self, identifier: str) -> ProcessingJob | None:
        row = await self._session.get(ProcessingJobModel, identifier)
        if row is not None:
            self._stored_from_row(row)
        return await self.get(identifier)

    async def list_events_after(
        self,
        job_id: str,
        *,
        after_sequence: int,
        limit: int,
    ) -> tuple[StoredProcessingJobEvent, ...]:
        if await self._session.get(ProcessingJobModel, job_id) is None:
            return ()
        rows = (
            await self._session.execute(
                select(ProcessingJobEventModel)
                .where(
                    ProcessingJobEventModel.job_id == job_id,
                    ProcessingJobEventModel.sequence > after_sequence,
                )
                .order_by(ProcessingJobEventModel.sequence.asc())
                .limit(limit)
            )
        ).scalars().all()
        return tuple(
            StoredProcessingJobEvent(
                sequence=row.sequence,
                event_type=row.event_type,
                progress_json=row.progress_json,
                error_code=row.error_code,
                created_at=row.created_at,
            )
            for row in rows
        )

    async def list_page(
        self,
        *,
        paper_id: str | None,
        status: str | None,
        job_type: str | None,
        limit: int,
        cursor: tuple[str, str] | None,
    ) -> tuple[tuple[ProcessingJob, ...], tuple[str, str] | None]:
        statement = select(ProcessingJobModel)
        if paper_id is not None:
            statement = statement.where(ProcessingJobModel.paper_id == paper_id)
        if status is not None:
            statement = statement.where(ProcessingJobModel.status == status)
        if job_type is not None:
            statement = statement.where(ProcessingJobModel.job_type == job_type)
        if cursor is not None:
            created_at, identifier = cursor
            statement = statement.where(
                or_(
                    ProcessingJobModel.created_at < created_at,
                    and_(
                        ProcessingJobModel.created_at == created_at,
                        ProcessingJobModel.id < identifier,
                    ),
                )
            )
        rows = (
            await self._session.execute(
                statement.order_by(
                    ProcessingJobModel.created_at.desc(),
                    ProcessingJobModel.id.desc(),
                ).limit(limit + 1)
            )
        ).scalars().all()
        visible = rows[:limit]
        items: list[ProcessingJob] = []
        for row in visible:
            self._stored_from_row(row)
            job = await self.get(row.id)
            if job is not None:
                items.append(job)
        next_cursor = (
            (visible[-1].created_at, visible[-1].id)
            if len(rows) > limit and visible
            else None
        )
        return tuple(items), next_cursor

    async def add(self, job: ProcessingJob) -> None:
        del job
        raise JobSpecValidationError(
            "legacy imported specs are migration-only; use insert_with_spec for application jobs"
        )

    async def insert_with_spec(
        self,
        job: NewProcessingJob,
        *,
        spec_json: str,
        spec_sha256: str,
    ) -> EnqueueResult:
        spec = self._validate_spec(job, spec_json, spec_sha256)
        await self._assert_idempotency_key(job, spec, spec_sha256)
        statement = sqlite_insert(ProcessingJobModel).values(
            id=job.id,
            paper_id=spec.paper_id,
            job_type=spec.job_type,
            source_mode=spec.source_mode,
            status=job.status.value,
            progress_json="{}",
            attempt=job.attempt,
            max_attempts=job.max_attempts,
            idempotency_key=job.idempotency_key,
            error_code=None,
            error_message=None,
            created_at=_timestamp(job.created_at),
            started_at=None,
            finished_at=None,
            cancelled_at=None,
            source_document_id=spec.source_document_id,
            artifact_id=spec.artifact_id,
            spec_json=spec_json,
            available_at=_timestamp(job.created_at),
            lease_owner=None,
            lease_token=None,
            lease_expires_at=None,
            heartbeat_at=None,
            cancel_requested_at=None,
            result_json=None,
            updated_at=_timestamp(job.created_at),
            retry_of_job_id=None,
            retry_sequence=0,
        ).on_conflict_do_nothing(index_elements=[ProcessingJobModel.idempotency_key])
        try:
            result = await self._session.execute(statement)
        except IntegrityError as error:
            raise PersistenceConflictError(operation="insert_processing_job") from error
        if result.rowcount == 1:
            self._session.add(
                ProcessingJobEventModel(
                    job_id=job.id,
                    sequence=1,
                    event_type="enqueued",
                    progress_json="{}",
                    error_code=None,
                    created_at=_timestamp(job.created_at),
                )
            )
            return EnqueueResult(job=job)
        winner = (
            await self._session.execute(
                select(ProcessingJobModel).where(
                    ProcessingJobModel.idempotency_key == job.idempotency_key
                )
            )
        ).scalar_one_or_none()
        if winner is None:
            raise PersistenceConflictError(operation="insert_processing_job")
        stored = self._stored_from_row(winner)
        if stored.sha256 != spec_sha256 or stored.value != spec:
            raise PersistenceConflictError(operation="processing_job_idempotency_mismatch")
        return EnqueueResult(job=_new_job_from_row(winner, stored.value), deduplicated=True)

    async def load_spec(self, job_id: str) -> StoredJobSpec:
        row = await self._session.get(ProcessingJobModel, job_id)
        if row is None:
            raise PersistenceConflictError(operation="load_processing_job_spec")
        return self._stored_from_row(row)

    async def claim_next(
        self,
        *,
        worker_id: str,
        now: datetime,
        lease_seconds: int,
        job_types: Collection[str] | None = None,
    ):
        """Claim one due job after validating its immutable persisted spec.

        The caller owns a short-lived unit of work.  This method deliberately
        starts its SQLite write transaction before selecting a candidate, so
        another connection cannot race a target transition between validation
        and the job lease CAS.
        """
        if not isinstance(worker_id, str) or not worker_id.strip():
            raise ValueError("worker_id must be nonblank")
        if not isinstance(lease_seconds, int) or isinstance(lease_seconds, bool) or lease_seconds < 1:
            raise ValueError("lease_seconds must be positive")
        normalized_job_types: tuple[str, ...] | None = None
        if job_types is not None:
            if any(not isinstance(value, str) or not value.strip() for value in job_types):
                raise ValueError("job_types must contain nonblank strings")
            normalized_job_types = tuple(sorted(set(job_types)))
            if not normalized_job_types:
                return None
        now_text = _timestamp(now)
        expires_at = now.astimezone(timezone.utc) + timedelta(seconds=lease_seconds)
        expires_text = _timestamp(expires_at)
        await self._session.execute(text("BEGIN IMMEDIATE"))
        await self._recover_expired_leases(
            now_text,
            job_types=normalized_job_types,
        )
        conditions = [
            ProcessingJobModel.status == "queued",
            ProcessingJobModel.available_at <= now_text,
        ]
        if normalized_job_types is not None:
            conditions.append(ProcessingJobModel.job_type.in_(normalized_job_types))
        row = (
            await self._session.execute(
                select(ProcessingJobModel)
                .where(*conditions)
                .order_by(
                    ProcessingJobModel.available_at.asc(),
                    ProcessingJobModel.created_at.asc(),
                    ProcessingJobModel.id.asc(),
                )
                .limit(1)
            )
        ).scalar_one_or_none()
        if row is None:
            return None

        # This is intentionally before every job/target/event write.  A
        # corrupt stored request is never reconstructed from progress/settings.
        stored = self._stored_from_row(row)
        token = secrets.token_urlsafe(32)
        next_attempt = row.attempt + 1
        target_changed = await self._claim_target(row, now_text)
        if not target_changed:
            raise PersistenceConflictError(operation="claim_processing_job_target")
        claimed = await self._session.execute(
            update(ProcessingJobModel)
            .where(
                ProcessingJobModel.id == row.id,
                ProcessingJobModel.status == "queued",
                ProcessingJobModel.attempt == row.attempt,
                ProcessingJobModel.available_at <= now_text,
            )
            .values(
                status="running",
                attempt=next_attempt,
                started_at=now_text,
                lease_owner=worker_id,
                lease_token=token,
                lease_expires_at=expires_text,
                heartbeat_at=now_text,
                updated_at=now_text,
            )
        )
        if claimed.rowcount != 1:
            raise PersistenceConflictError(operation="claim_processing_job")
        await self._append_event(row.id, "claimed", {}, None, now_text)
        from backend.app.domain.processing import JobLease
        if getattr(stored.value, "dispatch_error_code", None) is not None:
            leased_job = ProcessingJob(
                id=row.id,
                paper_id=row.paper_id,
                job_type=row.job_type,
                source_mode=row.source_mode,
                status="running",
                progress_json=row.progress_json,
                attempt=next_attempt,
                max_attempts=row.max_attempts,
                idempotency_key=row.idempotency_key,
                error_code=row.error_code,
                error_message=row.error_message,
                created_at=_datetime(row.created_at) or now,
                started_at=now,
            )
        else:
            leased_job = NewProcessingJob(
                id=row.id,
                spec=stored.value,
                idempotency_key=row.idempotency_key,
                created_at=_datetime(row.created_at) or now,
                max_attempts=row.max_attempts,
                attempt=next_attempt,
            )
        return JobLease(
            job=leased_job,
            spec=stored,
            worker_id=worker_id,
            token=token,
            expires_at=expires_at,
        )

    async def _claim_target(self, row: ProcessingJobModel, now_text: str) -> bool:
        stored = self._stored_from_row(row)
        if getattr(stored.value, "dispatch_error_code", None) is not None:
            return True
        if row.job_type in {"source_materialize", "ocr"}:
            statement = (
                update(SourceDocumentModel)
                .where(SourceDocumentModel.id == row.source_document_id, SourceDocumentModel.status == "queued")
                .values(status="running", updated_at=now_text)
            )
        elif row.job_type in {"explain", "translate"}:
            statement = (
                update(GeneratedArtifactModel)
                .where(GeneratedArtifactModel.id == row.artifact_id, GeneratedArtifactModel.status == "queued")
                .values(status="running", updated_at=now_text)
            )
        else:
            # P2's queue slice owns only source and explainer targets.
            return True
        return (await self._session.execute(statement)).rowcount == 1

    async def _recover_expired_leases(
        self,
        now_text: str,
        *,
        job_types: tuple[str, ...] | None = None,
    ) -> None:
        conditions = [
            ProcessingJobModel.status == "running",
            ProcessingJobModel.lease_expires_at.is_not(None),
            ProcessingJobModel.lease_expires_at <= now_text,
        ]
        if job_types is not None:
            conditions.append(ProcessingJobModel.job_type.in_(job_types))
        rows = (
            await self._session.execute(
                select(ProcessingJobModel).where(*conditions)
            )
        ).scalars().all()
        for row in rows:
            # Validate before recovery as well: corrupted rows are never
            # transitioned or rewritten by an automated recovery path.
            self._stored_from_row(row)
            if row.attempt >= row.max_attempts:
                terminal = await self._session.execute(
                    update(ProcessingJobModel)
                    .where(
                        ProcessingJobModel.id == row.id,
                        ProcessingJobModel.status == "running",
                        ProcessingJobModel.lease_token == row.lease_token,
                        ProcessingJobModel.lease_expires_at == row.lease_expires_at,
                    )
                    .values(
                        status="failed",
                        finished_at=now_text,
                        lease_owner=None,
                        lease_token=None,
                        lease_expires_at=None,
                        heartbeat_at=None,
                        error_code="PROCESSING_LEASE_EXPIRED",
                        error_message=None,
                        updated_at=now_text,
                    )
                )
                if terminal.rowcount == 1:
                    await self._set_target_status(
                        row,
                        "failed",
                        now_text,
                        error_code="PROCESSING_LEASE_EXPIRED",
                    )
                    await self._append_event(
                        row.id,
                        "failed",
                        {},
                        "PROCESSING_LEASE_EXPIRED",
                        now_text,
                    )
                continue
            recovered = await self._session.execute(
                update(ProcessingJobModel)
                .where(
                    ProcessingJobModel.id == row.id,
                    ProcessingJobModel.status == "running",
                    ProcessingJobModel.lease_token == row.lease_token,
                    ProcessingJobModel.lease_expires_at == row.lease_expires_at,
                )
                .values(
                    status="queued",
                    lease_owner=None,
                    lease_token=None,
                    lease_expires_at=None,
                    heartbeat_at=None,
                    available_at=now_text,
                    updated_at=now_text,
                )
            )
            if recovered.rowcount == 1:
                await self._restore_target_to_queued(row, now_text)
                await self._append_event(row.id, "lease_recovered", {}, None, now_text)

    async def _restore_target_to_queued(self, row: ProcessingJobModel, now_text: str) -> None:
        if row.job_type in {"source_materialize", "ocr"}:
            await self._session.execute(
                update(SourceDocumentModel)
                .where(SourceDocumentModel.id == row.source_document_id, SourceDocumentModel.status == "running")
                .values(status="queued", updated_at=now_text)
            )
        elif row.job_type in {"explain", "translate"}:
            await self._session.execute(
                update(GeneratedArtifactModel)
                .where(GeneratedArtifactModel.id == row.artifact_id, GeneratedArtifactModel.status == "running")
                .values(status="queued", updated_at=now_text)
            )

    async def _append_event(
        self, job_id: str, event_type: str, progress: dict[str, object], error_code: str | None, created_at: str,
    ) -> None:
        sequence = (
            await self._session.execute(
                select(func.coalesce(func.max(ProcessingJobEventModel.sequence), 0)).where(
                    ProcessingJobEventModel.job_id == job_id
                )
            )
        ).scalar_one() + 1
        self._session.add(
            ProcessingJobEventModel(
                job_id=job_id,
                sequence=sequence,
                event_type=event_type,
                progress_json=_json(progress),
                error_code=error_code,
                created_at=created_at,
            )
        )
        await self._session.flush()

    async def check_active(self, lease, *, now: datetime) -> bool:
        """Return whether this owner still holds a live, uncancelled lease."""
        now_text = _timestamp(now)
        current = (
            await self._session.execute(
                select(ProcessingJobModel.id).where(
                    ProcessingJobModel.id == lease.job.id,
                    ProcessingJobModel.status == "running",
                    ProcessingJobModel.lease_owner == lease.worker_id,
                    ProcessingJobModel.lease_token == lease.token,
                    ProcessingJobModel.lease_expires_at > now_text,
                    ProcessingJobModel.cancel_requested_at.is_(None),
                )
            )
        ).scalar_one_or_none()
        return current is not None

    async def renew(self, lease, *, now: datetime, lease_seconds: int):
        """Extend one still-active lease without changing its ownership token."""
        if not isinstance(lease_seconds, int) or isinstance(lease_seconds, bool) or lease_seconds < 1:
            raise ValueError("lease_seconds must be positive")
        now_text = _timestamp(now)
        expires_at = now.astimezone(timezone.utc) + timedelta(seconds=lease_seconds)
        expires_text = _timestamp(expires_at)
        await self._session.execute(text("BEGIN IMMEDIATE"))
        renewed = await self._session.execute(
            update(ProcessingJobModel)
            .where(
                ProcessingJobModel.id == lease.job.id,
                ProcessingJobModel.status == "running",
                ProcessingJobModel.lease_owner == lease.worker_id,
                ProcessingJobModel.lease_token == lease.token,
                ProcessingJobModel.lease_expires_at > now_text,
                ProcessingJobModel.cancel_requested_at.is_(None),
            )
            .values(
                heartbeat_at=now_text,
                lease_expires_at=expires_text,
                updated_at=now_text,
            )
        )
        if renewed.rowcount != 1:
            raise JobLeaseLostError(operation="renew_processing_job_lease_lost")
        from backend.app.domain.processing import JobLease

        return JobLease(
            job=lease.job,
            spec=lease.spec,
            worker_id=lease.worker_id,
            token=lease.token,
            expires_at=expires_at,
        )

    async def fail(
        self,
        lease,
        failure,
        *,
        now: datetime,
        retry_after_seconds: int | None = None,
        result=None,
    ):
        """Settle a current lease, preserving its stored request bytes exactly."""
        from backend.app.domain.processing import JobFailure
        from backend.app.domain.processing import JobResult
        if not isinstance(failure, JobFailure):
            raise ValueError("failure must be JobFailure")
        if result is not None and not isinstance(result, JobResult):
            raise ValueError("failure result must be JobResult or null")
        if retry_after_seconds is not None and (
            not isinstance(retry_after_seconds, int) or isinstance(retry_after_seconds, bool)
        ):
            raise ValueError("retry_after_seconds must be an integer or null")
        now_text = _timestamp(now)
        await self._session.execute(text("BEGIN IMMEDIATE"))
        row = await self._session.get(ProcessingJobModel, lease.job.id)
        if row is None:
            raise PersistenceConflictError(operation="fail_processing_job_missing")
        self._stored_from_row(row)
        current = (
            row.status == "running"
            and row.lease_owner == lease.worker_id
            and row.lease_token == lease.token
            and row.lease_expires_at is not None
            and row.lease_expires_at > now_text
            and row.cancel_requested_at is None
        )
        if not current:
            raise JobLeaseLostError(operation="fail_processing_job_lease_lost")
        retry = failure.retryable and row.attempt < row.max_attempts
        if retry:
            base_delay = min(900, 5 * 2 ** (row.attempt - 1))
            normalized_retry_after = max(0, retry_after_seconds or 0)
            delay = min(900, max(base_delay, normalized_retry_after))
            values = {
                "status": "queued", "available_at": _timestamp(now + timedelta(seconds=delay)),
                "lease_owner": None, "lease_token": None, "lease_expires_at": None,
                "heartbeat_at": None, "error_code": failure.code, "error_message": failure.message,
                "updated_at": now_text,
            }
            event_type = "retry_scheduled"
        else:
            values = {
                "status": "failed", "finished_at": now_text, "lease_owner": None,
                "lease_token": None, "lease_expires_at": None, "heartbeat_at": None,
                "error_code": failure.code, "error_message": failure.message, "updated_at": now_text,
            }
            if result is not None:
                values["result_json"] = _json(dict(result.value))
            event_type = "failed"
        settled = await self._session.execute(
            update(ProcessingJobModel)
            .where(
                ProcessingJobModel.id == row.id,
                ProcessingJobModel.status == "running",
                ProcessingJobModel.lease_owner == lease.worker_id,
                ProcessingJobModel.lease_token == lease.token,
                ProcessingJobModel.lease_expires_at > now_text,
                ProcessingJobModel.cancel_requested_at.is_(None),
            )
            .values(**values)
        )
        if settled.rowcount != 1:
            raise JobLeaseLostError(operation="fail_processing_job_lease_lost")
        await self._set_target_status(
            row, "queued" if retry else "failed", now_text,
            error_code=None if retry else failure.code,
            error_message=None if retry else failure.message,
        )
        await self._append_event(row.id, event_type, {}, failure.code, now_text)
        return lease.job

    async def report_progress(self, lease, progress, *, now: datetime) -> None:
        await self.checkpoint(lease, progress, now=now)

    async def checkpoint(self, lease, progress, *, now: datetime) -> str:
        from backend.app.domain.processing import JobProgress
        if not isinstance(progress, JobProgress):
            raise ValueError("progress must be JobProgress")
        now_text = _timestamp(now)
        await self._session.execute(text("BEGIN IMMEDIATE"))
        row = await self._session.get(ProcessingJobModel, lease.job.id)
        if row is None:
            raise PersistenceConflictError(operation="progress_processing_job_missing")
        self._stored_from_row(row)
        current = (
            row.status == "running"
            and row.lease_owner == lease.worker_id
            and row.lease_token == lease.token
            and row.lease_expires_at is not None
            and row.lease_expires_at > now_text
        )
        if not current:
            raise JobLeaseLostError(operation="progress_processing_job_lease_lost")
        progress_value = dict(progress.value)
        payload = _json(progress_value)
        if row.cancel_requested_at is not None:
            target_changed = await self._set_target_status_if_current(
                row, expected_status="running", status="cancelled", now_text=now_text,
            )
            if not target_changed:
                raise PersistenceConflictError(operation="checkpoint_processing_job_target")
            cancelled = await self._session.execute(
                update(ProcessingJobModel)
                .where(
                    ProcessingJobModel.id == row.id,
                    ProcessingJobModel.status == "running",
                    ProcessingJobModel.lease_owner == lease.worker_id,
                    ProcessingJobModel.lease_token == lease.token,
                    ProcessingJobModel.lease_expires_at > now_text,
                    ProcessingJobModel.cancel_requested_at.is_not(None),
                )
                .values(
                    status="cancelled", progress_json=payload, finished_at=now_text,
                    cancelled_at=now_text, lease_owner=None, lease_token=None,
                    lease_expires_at=None, heartbeat_at=None, updated_at=now_text,
                )
            )
            if cancelled.rowcount != 1:
                raise JobLeaseLostError(operation="checkpoint_processing_job_lease_lost")
            await self._append_event(row.id, "cancelled", progress_value, None, now_text)
            return "cancelled"
        updated = await self._session.execute(
            update(ProcessingJobModel)
            .where(
                ProcessingJobModel.id == row.id,
                ProcessingJobModel.status == "running",
                ProcessingJobModel.lease_owner == lease.worker_id,
                ProcessingJobModel.lease_token == lease.token,
                ProcessingJobModel.lease_expires_at > now_text,
                ProcessingJobModel.cancel_requested_at.is_(None),
            )
            .values(progress_json=payload, heartbeat_at=now_text, updated_at=now_text)
        )
        if updated.rowcount != 1:
            raise JobLeaseLostError(operation="progress_processing_job_lease_lost")
        await self._append_event(row.id, "progress", progress_value, None, now_text)
        return "continue"

    async def complete(self, lease, result, *, now: datetime):
        from backend.app.domain.processing import JobResult
        if not isinstance(result, JobResult):
            raise ValueError("result must be JobResult")
        now_text = _timestamp(now)
        if not self._session.in_transaction():
            await self._session.execute(text("BEGIN IMMEDIATE"))
        row = await self._session.get(ProcessingJobModel, lease.job.id)
        if row is None:
            raise PersistenceConflictError(operation="complete_processing_job_missing")
        self._stored_from_row(row)
        current = (
            row.status == "running"
            and row.lease_owner == lease.worker_id
            and row.lease_token == lease.token
            and row.lease_expires_at is not None
            and row.lease_expires_at > now_text
        )
        if not current:
            raise JobLeaseLostError(operation="complete_processing_job_lease_lost")
        if row.cancel_requested_at is not None:
            target_changed = await self._set_target_status_if_current(
                row, expected_status="running", status="cancelled", now_text=now_text,
            )
            if not target_changed:
                raise JobLeaseLostError(operation="complete_processing_job_lease_lost")
            cancelled = await self._session.execute(
                update(ProcessingJobModel)
                .where(
                    ProcessingJobModel.id == row.id,
                    ProcessingJobModel.status == "running",
                    ProcessingJobModel.lease_owner == lease.worker_id,
                    ProcessingJobModel.lease_token == lease.token,
                    ProcessingJobModel.lease_expires_at > now_text,
                    ProcessingJobModel.cancel_requested_at.is_not(None),
                )
                .values(
                    status="cancelled", finished_at=now_text, cancelled_at=now_text,
                    lease_owner=None, lease_token=None, lease_expires_at=None,
                    heartbeat_at=None, updated_at=now_text,
                )
            )
            if cancelled.rowcount != 1:
                raise JobLeaseLostError(operation="complete_processing_job_lease_lost")
            await self._append_event(row.id, "cancelled", {}, None, now_text)
            result = await self.get(row.id)
            if result is None:
                raise PersistenceConflictError(operation="complete_processing_job_missing")
            return result
        updated = await self._session.execute(
            update(ProcessingJobModel)
            .where(
                ProcessingJobModel.id == row.id,
                ProcessingJobModel.status == "running",
                ProcessingJobModel.lease_owner == lease.worker_id,
                ProcessingJobModel.lease_token == lease.token,
                ProcessingJobModel.lease_expires_at > now_text,
                ProcessingJobModel.cancel_requested_at.is_(None),
            )
            .values(
                status="succeeded", result_json=_json(dict(result.value)), finished_at=now_text,
                lease_owner=None, lease_token=None, lease_expires_at=None, heartbeat_at=None,
                updated_at=now_text,
            )
        )
        if updated.rowcount != 1:
            raise JobLeaseLostError(operation="complete_processing_job_lease_lost")
        await self._append_event(row.id, "succeeded", {}, None, now_text)
        return lease.job

    async def cancel(self, job_id: str, *, now: datetime):
        """Cancel queued work immediately in the job target's transaction."""
        now_text = _timestamp(now)
        await self._session.execute(text("BEGIN IMMEDIATE"))
        row = await self._session.get(ProcessingJobModel, job_id)
        if row is None:
            raise PersistenceConflictError(operation="cancel_processing_job_missing")
        self._stored_from_row(row)
        if row.status == "running":
            if row.cancel_requested_at is None:
                requested = await self._session.execute(
                    update(ProcessingJobModel)
                    .where(
                        ProcessingJobModel.id == row.id,
                        ProcessingJobModel.status == "running",
                        ProcessingJobModel.cancel_requested_at.is_(None),
                    )
                    .values(cancel_requested_at=now_text, updated_at=now_text)
                )
                if requested.rowcount != 1:
                    raise PersistenceConflictError(operation="cancel_processing_job_conflict")
                await self._append_event(row.id, "cancel_requested", {}, None, now_text)
            result = await self.get(row.id)
            if result is None:
                raise PersistenceConflictError(operation="cancel_processing_job_missing")
            return result
        if row.status != "queued":
            raise JobNotCancellableError(operation="cancel_processing_job_not_cancellable")
        target_changed = await self._set_target_status_if_current(
            row, expected_status="queued", status="cancelled", now_text=now_text,
        )
        if not target_changed:
            raise PersistenceConflictError(operation="cancel_processing_job_target")
        cancelled = await self._session.execute(
            update(ProcessingJobModel)
            .where(
                ProcessingJobModel.id == row.id,
                ProcessingJobModel.status == "queued",
            )
            .values(
                status="cancelled",
                finished_at=now_text,
                cancelled_at=now_text,
                updated_at=now_text,
            )
        )
        if cancelled.rowcount != 1:
            raise PersistenceConflictError(operation="cancel_processing_job_conflict")
        await self._append_event(row.id, "cancelled", {}, None, now_text)
        result = await self.get(row.id)
        if result is None:
            raise PersistenceConflictError(operation="cancel_processing_job_missing")
        return result

    async def retry(self, job_id: str, *, now: datetime):
        """Create (or return) one active descendant by copying parent bytes verbatim."""
        now_text = _timestamp(now)
        if not self._session.in_transaction():
            await self._session.execute(text("BEGIN IMMEDIATE"))
        parent = await self._session.get(ProcessingJobModel, job_id)
        if parent is None:
            raise PersistenceConflictError(operation="retry_processing_job_missing")
        stored = self._stored_from_row(parent)
        if parent.status not in {"failed", "cancelled"}:
            raise JobNotRetryableError(operation="retry_processing_job_not_terminal")
        if parent.error_code in _NON_RETRYABLE_FAILURE_CODES:
            raise JobNotRetryableError(operation="retry_processing_job_nonretryable")
        existing = (
            await self._session.execute(
                select(ProcessingJobModel)
                .where(
                    ProcessingJobModel.retry_of_job_id == parent.id,
                    ProcessingJobModel.status.in_(("queued", "running")),
                )
                .order_by(ProcessingJobModel.retry_sequence.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if existing is not None:
            return EnqueueResult(job=_new_job_from_row(existing, self._stored_from_row(existing).value), deduplicated=True)
        if not await self._target_has_status(parent, parent.status):
            raise JobNotRetryableError(operation="retry_processing_job_target")
        latest_retry_sequence = (
            await self._session.execute(
                select(ProcessingJobModel.retry_sequence)
                .where(ProcessingJobModel.retry_of_job_id == parent.id)
                .order_by(ProcessingJobModel.retry_sequence.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        next_retry_sequence = max(
            parent.retry_sequence + 1,
            (latest_retry_sequence + 1) if latest_retry_sequence is not None else 0,
        )
        descendant = NewProcessingJob(
            id=f"retry-{secrets.token_urlsafe(18)}",
            spec=stored.value,
            idempotency_key=f"{parent.idempotency_key}:retry:{next_retry_sequence}",
            created_at=now,
            max_attempts=parent.max_attempts,
        )
        result = await self.copy_spec_for_retry(
            parent.id,
            descendant,
            retry_sequence=next_retry_sequence,
        )
        await self._set_target_status(parent, "queued", now_text)
        await self._append_event(parent.id, "retry_scheduled", {}, None, now_text)
        return result

    async def _target_has_status(self, row: ProcessingJobModel, expected_status: str) -> bool:
        if row.job_type in {"source_materialize", "ocr"}:
            target = await self._session.get(SourceDocumentModel, row.source_document_id)
        elif row.job_type in {"explain", "translate"}:
            target = await self._session.get(GeneratedArtifactModel, row.artifact_id)
        elif row.job_type in {"obsidian_export", "obsidian_sync"}:
            return True
        else:
            return False
        return target is not None and target.status == expected_status

    async def copy_spec_for_retry(
        self,
        parent_job_id: str,
        descendant: NewProcessingJob,
        *,
        retry_sequence: int | None = None,
    ) -> EnqueueResult:
        parent = await self._session.get(ProcessingJobModel, parent_job_id)
        if parent is None:
            raise PersistenceConflictError(operation="copy_processing_job_spec_missing")
        stored = self._stored_from_row(parent)
        if stored.value != descendant.spec:
            raise JobSpecValidationError("retry descendant spec must equal the parent spec")
        statement = sqlite_insert(ProcessingJobModel).values(
            id=descendant.id,
            paper_id=parent.paper_id,
            job_type=parent.job_type,
            source_mode=parent.source_mode,
            status="queued",
            progress_json="{}",
            attempt=0,
            max_attempts=parent.max_attempts,
            idempotency_key=descendant.idempotency_key,
            error_code=None,
            error_message=None,
            created_at=_timestamp(descendant.created_at),
            started_at=None,
            finished_at=None,
            cancelled_at=None,
            source_document_id=parent.source_document_id,
            artifact_id=parent.artifact_id,
            spec_json=stored.raw_json,
            available_at=_timestamp(descendant.created_at),
            lease_owner=None,
            lease_token=None,
            lease_expires_at=None,
            heartbeat_at=None,
            cancel_requested_at=None,
            result_json=None,
            updated_at=_timestamp(descendant.created_at),
            retry_of_job_id=parent.id,
            retry_sequence=(
                parent.retry_sequence + 1
                if retry_sequence is None
                else retry_sequence
            ),
        )
        try:
            inserted = await self._session.execute(statement)
        except IntegrityError as error:
            raise PersistenceConflictError(operation="copy_processing_job_spec") from error
        if inserted.rowcount != 1:
            raise PersistenceConflictError(operation="copy_processing_job_spec")
        copied = await self._session.get(ProcessingJobModel, descendant.id)
        if copied is None or copied.spec_json != stored.raw_json:
            raise PersistenceConflictError(operation="copy_processing_job_spec")
        copied_stored = self._stored_from_row(copied)
        if copied_stored.sha256 != stored.sha256:
            raise PersistenceConflictError(operation="copy_processing_job_spec")
        await self._append_event(descendant.id, "enqueued", {}, None, _timestamp(descendant.created_at))
        return EnqueueResult(job=descendant)

    async def _set_target_status(
        self,
        row: ProcessingJobModel,
        status: str,
        now_text: str,
        *,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> None:
        values = {"status": status, "updated_at": now_text}
        if status in {"queued", "running"}:
            values.update(error_code=None, error_message=None)
        elif status == "failed":
            values.update(error_code=error_code, error_message=error_message)
        if row.job_type in {"source_materialize", "ocr"}:
            await self._session.execute(
                update(SourceDocumentModel)
                .where(SourceDocumentModel.id == row.source_document_id)
                .values(**values)
            )
        elif row.job_type in {"explain", "translate"}:
            await self._session.execute(
                update(GeneratedArtifactModel)
                .where(GeneratedArtifactModel.id == row.artifact_id)
                .values(**values)
            )

    async def _set_target_status_if_current(
        self,
        row: ProcessingJobModel,
        *,
        expected_status: str,
        status: str,
        now_text: str,
    ) -> bool:
        values = {"status": status, "updated_at": now_text}
        if row.job_type in {"source_materialize", "ocr"}:
            statement = (
                update(SourceDocumentModel)
                .where(
                    SourceDocumentModel.id == row.source_document_id,
                    SourceDocumentModel.status == expected_status,
                )
                .values(**values)
            )
        elif row.job_type in {"explain", "translate"}:
            statement = (
                update(GeneratedArtifactModel)
                .where(
                    GeneratedArtifactModel.id == row.artifact_id,
                    GeneratedArtifactModel.status == expected_status,
                )
                .values(**values)
            )
        else:
            return True
        return (await self._session.execute(statement)).rowcount == 1

    def _validate_spec(
        self,
        job: NewProcessingJob,
        spec_json: str,
        spec_sha256: str,
    ) -> JobSpecV1:
        spec = decode_job_spec_v1(spec_json, expected_row={
            "job_type": job.spec.job_type,
            "paper_id": job.spec.paper_id,
            "source_mode": job.spec.source_mode,
            "source_document_id": job.spec.source_document_id,
            "artifact_id": job.spec.artifact_id,
        })
        if spec != job.spec or hash_job_spec(spec_json) != spec_sha256:
            raise JobSpecValidationError("job spec does not match its enqueue request")
        return spec

    async def _assert_idempotency_key(
        self,
        job: NewProcessingJob,
        spec: JobSpecV1,
        spec_sha256: str,
    ) -> None:
        if spec.job_type in {"source_materialize", "ocr"}:
            source = await self._session.get(SourceDocumentModel, spec.source_document_id)
            if source is None:
                raise PersistenceConflictError(operation="processing_job_source_missing")
            source_key = source.source_key or build_source_key(
                paper_id=source.paper_id,
                mode=source.mode,
                provider=source.provider,
                model=source.model,
                pdf_sha256=source.pdf_sha256,
                options_hash=source.options_hash,
                processing_version=source.processing_version,
            )
            expected = build_source_job_key(source_key, spec_sha256)
        elif spec.job_type in {"explain", "translate"}:
            artifact = await self._session.get(GeneratedArtifactModel, spec.artifact_id)
            source = await self._session.get(SourceDocumentModel, spec.source_document_id)
            if artifact is None or source is None or source.content_sha256 is None:
                raise PersistenceConflictError(operation="processing_job_artifact_missing")
            artifact_key = artifact.artifact_key or build_artifact_key(
                kind=artifact.kind,
                source_document_id=artifact.source_document_id,
                source_content_sha256=source.content_sha256,
                generator_provider=artifact.generator_provider,
                generator_model=artifact.generator_model,
                prompt_version=artifact.prompt_version,
                kind_specific_options={"profile": getattr(spec, "profile", "")},
            )
            expected = build_artifact_job_key(artifact_key, spec_sha256)
        elif spec.job_type == "embed" and getattr(spec, "include_embeddings", None) is not None:
            source = await self._session.get(SourceDocumentModel, spec.source_document_id)
            if (
                source is None
                or source.paper_id != spec.paper_id
                or source.mode != spec.source_mode
                or source.status != "ready"
                or source.content_sha256 is None
            ):
                raise PersistenceConflictError(operation="processing_job_index_source_missing")
            expected = build_index_job_key(
                source_document_id=source.id,
                source_content_sha256=source.content_sha256,
                chunking_version=spec.chunking_version,
                embedding_provider=spec.provider,
                embedding_model=spec.model,
                embedding_version=spec.embedding_version,
                include_embeddings=spec.include_embeddings,
                embedding_options=dict(spec.options or {}),
            )
        elif (
            spec.job_type in {"obsidian_export", "obsidian_sync"}
            and getattr(spec, "settings_fingerprint", "")
        ):
            from backend.app.domain.processing import build_obsidian_job_key

            expected = build_obsidian_job_key(spec_sha256)
        else:
            return
        if expected != job.idempotency_key:
            raise JobSpecValidationError("job idempotency key does not bind the canonical spec")

    def _stored_from_row(self, row: ProcessingJobModel) -> StoredJobSpec:
        raw_json = row.spec_json
        value = decode_job_spec_v1(raw_json, expected_row={
            "job_type": row.job_type,
            "paper_id": row.paper_id,
            "source_mode": row.source_mode,
            "source_document_id": row.source_document_id,
            "artifact_id": row.artifact_id,
        })
        sha256 = hash_job_spec(raw_json)
        if (
            value.job_type in {"obsidian_export", "obsidian_sync"}
            and getattr(value, "settings_fingerprint", "")
        ):
            from backend.app.domain.processing import build_obsidian_job_key

            expected_key = build_obsidian_job_key(sha256)
            if row.retry_of_job_id is not None:
                expected_key = f"{expected_key}:retry:{row.retry_sequence}"
            if row.idempotency_key != expected_key:
                raise JobSpecValidationError(
                    "obsidian job idempotency key does not bind stored spec bytes"
                )
        return StoredJobSpec(value=value, raw_json=raw_json, sha256=sha256)


def _datetime(value: str | None) -> datetime | None:
    if value is None:
        return None
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("stored timestamp must be timezone-aware")
    return parsed.astimezone(timezone.utc)


def _new_job_from_row(row: ProcessingJobModel, spec: JobSpecV1) -> NewProcessingJob:
    return NewProcessingJob(
        id=row.id,
        spec=spec,
        idempotency_key=row.idempotency_key,
        created_at=_datetime(row.created_at) or datetime.now(timezone.utc),
        max_attempts=row.max_attempts,
        attempt=row.attempt,
    )
