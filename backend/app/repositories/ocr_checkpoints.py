from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select, text

from backend.app.application.ports.ocr_provider import OcrPageResult
from backend.app.domain import JobLeaseLostError, PersistenceConflictError
from backend.app.repositories.models import OcrPageCheckpointModel, ProcessingJobModel


class SqlAlchemyOcrCheckpointRepository:
    def __init__(self, session_factory: Any, *, clock: Callable[[], datetime]) -> None:
        self._session_factory = session_factory
        self._clock = clock

    async def save_success(
        self,
        source_id: str,
        page: OcrPageResult,
        *,
        lease: object,
    ) -> bool:
        if not isinstance(source_id, str) or not source_id:
            raise ValueError("source_id must be nonblank")
        if not isinstance(page, OcrPageResult):
            raise ValueError("page must be an OcrPageResult")
        now = _utc(self._clock())
        now_text = _timestamp(now)
        async with self._session_factory() as session:
            await session.execute(text("BEGIN IMMEDIATE"))
            current = (
                await session.execute(
                    select(ProcessingJobModel).where(
                        ProcessingJobModel.id == lease.job.id,
                        ProcessingJobModel.source_document_id == source_id,
                        ProcessingJobModel.status == "running",
                        ProcessingJobModel.lease_owner == lease.worker_id,
                        ProcessingJobModel.lease_token == lease.token,
                        ProcessingJobModel.lease_expires_at > now_text,
                        ProcessingJobModel.cancel_requested_at.is_(None),
                    )
                )
            ).scalar_one_or_none()
            if current is None:
                raise JobLeaseLostError(operation="ocr_checkpoint_lease_lost")

            existing = await session.get(
                OcrPageCheckpointModel,
                {"source_document_id": source_id, "page_number": page.page_number},
            )
            if existing is not None and existing.status == "succeeded":
                same = (
                    existing.markdown == page.markdown
                    and existing.content_sha256 == page.content_sha256
                    and existing.provider_page_id == page.provider_page_id
                )
                if not same:
                    raise PersistenceConflictError(operation="ocr_checkpoint_content_conflict")
                await session.commit()
                return False

            attempt = int(getattr(lease.job, "attempt", 0))
            if existing is None:
                session.add(
                    OcrPageCheckpointModel(
                        source_document_id=source_id,
                        page_number=page.page_number,
                        status="succeeded",
                        markdown=page.markdown,
                        content_sha256=page.content_sha256,
                        provider_page_id=page.provider_page_id,
                        attempt=attempt,
                        error_code=None,
                        error_message=None,
                        created_at=now_text,
                        updated_at=now_text,
                    )
                )
            else:
                existing.status = "succeeded"
                existing.markdown = page.markdown
                existing.content_sha256 = page.content_sha256
                existing.provider_page_id = page.provider_page_id
                existing.attempt = attempt
                existing.error_code = None
                existing.error_message = None
                existing.updated_at = now_text
            await session.commit()
            return True

    async def list_succeeded(self, source_id: str) -> set[int]:
        _source_id(source_id)
        async with self._session_factory() as session:
            values = (
                await session.execute(
                    select(OcrPageCheckpointModel.page_number).where(
                        OcrPageCheckpointModel.source_document_id == source_id,
                        OcrPageCheckpointModel.status == "succeeded",
                    )
                )
            ).scalars().all()
        return {int(value) for value in values}

    async def read(self, source_id: str, page_number: int) -> OcrPageCheckpointModel | None:
        _source_id(source_id)
        if not isinstance(page_number, int) or isinstance(page_number, bool) or page_number < 1:
            raise ValueError("page_number must be a positive integer")
        async with self._session_factory() as session:
            return await session.get(
                OcrPageCheckpointModel,
                {"source_document_id": source_id, "page_number": page_number},
            )

    async def read_all_succeeded(self, source_id: str) -> dict[int, str]:
        _source_id(source_id)
        async with self._session_factory() as session:
            rows = (
                await session.execute(
                    select(
                        OcrPageCheckpointModel.page_number,
                        OcrPageCheckpointModel.markdown,
                    )
                    .where(
                        OcrPageCheckpointModel.source_document_id == source_id,
                        OcrPageCheckpointModel.status == "succeeded",
                        OcrPageCheckpointModel.markdown.is_not(None),
                    )
                    .order_by(OcrPageCheckpointModel.page_number.asc())
                )
            ).all()
        return {int(page_number): str(markdown) for page_number, markdown in rows}


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("clock must return a timezone-aware datetime")
    return value.astimezone(timezone.utc)


def _timestamp(value: datetime) -> str:
    return _utc(value).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _source_id(value: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError("source_id must be nonblank")
    return value
