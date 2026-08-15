from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import re


_SHA256 = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True, slots=True)
class StaleResult:
    source_ids: tuple[str, ...] = ()
    artifact_ids: tuple[str, ...] = ()
    chunk_ids: tuple[str, ...] = ()
    embedding_ids: tuple[str, ...] = ()
    removed_head_keys: tuple[str, ...] = ()
    cancelled_job_ids: tuple[str, ...] = ()
    cancel_requested_job_ids: tuple[str, ...] = ()


class SourceFreshnessService:
    """Explicit command seam for atomically cascading stale source identity."""

    def __init__(self, unit_of_work_factory) -> None:
        self._work_factory = unit_of_work_factory

    async def reconcile_pdf(
        self,
        paper_id: str,
        current_pdf_sha256: str,
        *,
        now: datetime,
    ) -> StaleResult:
        if not isinstance(paper_id, str) or not paper_id.strip():
            raise ValueError("paper_id must be nonblank")
        if not isinstance(current_pdf_sha256, str) or not _SHA256.fullmatch(
            current_pdf_sha256
        ):
            raise ValueError("current_pdf_sha256 must be lowercase SHA-256")
        timestamp = _utc(now)
        async with self._work_factory() as work:
            result = await work.sources.stale_for_pdf_change(
                paper_id,
                current_pdf_sha256,
                now=timestamp,
            )
            await work.commit()
        return result

    async def activate_source(
        self,
        source_document_id: str,
        *,
        now: datetime,
    ) -> StaleResult:
        if not isinstance(source_document_id, str) or not source_document_id.strip():
            raise ValueError("source_document_id must be nonblank")
        timestamp = _utc(now)
        async with self._work_factory() as work:
            result = await work.sources.stale_for_active_source(
                source_document_id,
                now=timestamp,
            )
            await work.commit()
        return result


def _utc(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    return value.astimezone(timezone.utc)


__all__ = ["SourceFreshnessService", "StaleResult"]
