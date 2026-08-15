from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from backend.app.application.ports.obsidian_auto_export import ObsidianAutoExportPort


async def notify_artifact_ready(
    port: ObsidianAutoExportPort | None,
    *,
    paper_id: str,
    artifact_id: str,
    committed_at: datetime,
    logger: Callable[[Mapping[str, object]], None] | None = None,
) -> None:
    if port is None:
        return
    try:
        await port.on_artifact_ready(paper_id, artifact_id, committed_at)
    except Exception:
        if logger is not None:
            logger(
                {
                    "event": "obsidian_auto_export_enqueue_failed",
                    "paperId": paper_id,
                    "artifactId": artifact_id,
                }
            )


class SafeObsidianAutoExportAdapter:
    """Production isolation wrapper for an auto-export policy."""

    def __init__(
        self,
        policy: Any,
        *,
        logger: Callable[[Mapping[str, object]], None] | None = None,
    ) -> None:
        self._policy = policy
        self._logger = logger

    async def on_artifact_ready(
        self,
        paper_id: str,
        artifact_id: str,
        committed_at: datetime,
    ) -> None:
        await notify_artifact_ready(
            self._policy,
            paper_id=paper_id,
            artifact_id=artifact_id,
            committed_at=committed_at,
            logger=self._logger,
        )


@dataclass(frozen=True, slots=True)
class _PendingExport:
    artifact_id: str
    committed_at: datetime
    due_at: datetime


class ObsidianAutoExportPolicy:
    """Coalesce ready-artifact notifications onto the canonical export queue."""

    def __init__(
        self,
        unit_of_work_factory: Callable[[], Any],
        *,
        settings_service: Any,
        job_service: Any,
        clock: Callable[[], datetime],
        debounce_seconds: float,
        logger: Callable[[Mapping[str, object]], None] | None = None,
    ) -> None:
        self._unit_of_work_factory = unit_of_work_factory
        self._settings_service = settings_service
        self._job_service = job_service
        self._clock = clock
        self._debounce_seconds = debounce_seconds
        self._logger = logger
        self._pending: dict[str, _PendingExport] = {}
        self._lock = asyncio.Lock()

    @property
    def pending_count(self) -> int:
        return len(self._pending)

    async def on_artifact_ready(
        self,
        paper_id: str,
        artifact_id: str,
        committed_at: datetime,
    ) -> None:
        settings = await self._settings_service.obsidian()
        if not settings.enabled or not settings.auto_export:
            async with self._lock:
                self._pending.pop(paper_id, None)
            return

        async with self._unit_of_work_factory() as work:
            artifact = await work.artifacts.get(artifact_id)
            source = (
                await work.sources.get(artifact.source_document_id)
                if artifact is not None
                else None
            )
        if (
            artifact is None
            or artifact.paper_id != paper_id
            or artifact.status.value != "ready"
            or source is None
            or source.paper_id != paper_id
            or source.status.value != "ready"
        ):
            return

        due_at = datetime.fromtimestamp(
            committed_at.timestamp() + self._debounce_seconds,
            tz=committed_at.tzinfo,
        )
        candidate = _PendingExport(
            artifact_id=artifact_id,
            committed_at=committed_at,
            due_at=due_at,
        )
        async with self._lock:
            current = self._pending.get(paper_id)
            if current is None or committed_at >= current.committed_at:
                self._pending[paper_id] = candidate

    async def flush_due(self) -> None:
        settings = await self._settings_service.obsidian()
        if not settings.enabled or not settings.auto_export:
            async with self._lock:
                self._pending.clear()
            return

        now = self._clock()
        async with self._lock:
            due = tuple(
                (paper_id, pending)
                for paper_id, pending in sorted(self._pending.items())
                if pending.due_at <= now
            )
            for paper_id, pending in due:
                if self._pending.get(paper_id) == pending:
                    del self._pending[paper_id]

        for paper_id, pending in due:
            try:
                await self._job_service.enqueue_export(paper_id, dry_run=False)
            except Exception:
                if self._logger is not None:
                    self._logger(
                        {
                            "event": "obsidian_auto_export_enqueue_failed",
                            "paperId": paper_id,
                            "artifactId": pending.artifact_id,
                        }
                    )


__all__ = [
    "ObsidianAutoExportPolicy",
    "SafeObsidianAutoExportAdapter",
    "notify_artifact_ready",
]
