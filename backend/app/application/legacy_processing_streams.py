from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timezone
import inspect
import json
from typing import Any

from backend.app.domain.context import EmbeddingProfile


_ARTIFACT_KINDS = frozenset({"explainer", "translation"})
_ARTIFACT_PROFILES = frozenset({"standard", "deep"})
_EMBEDDING_SCOPES = frozenset({"missing", "all"})
_TERMINAL_JOB_STATUSES = frozenset({"succeeded", "failed", "cancelled"})


class LegacyProcessingStreamError(RuntimeError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class _PollItem:
    event: dict[str, object] | None = None
    terminal_job: object | None = None


class LegacyProcessingStreams:
    """Project durable P2/P3 jobs into the legacy ordered event protocol."""

    def __init__(
        self,
        unit_of_work_factory: Callable[[], Any],
        *,
        artifact_service: Any,
        document_search: Any,
        embedding_profile: EmbeddingProfile | None,
        embedding_profile_resolver: Callable[
            [], EmbeddingProfile | None | Awaitable[EmbeddingProfile | None]
        ]
        | None = None,
        clock: Callable[[], datetime] | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        poll_interval: float = 0.05,
    ) -> None:
        if not callable(unit_of_work_factory):
            raise TypeError("unit_of_work_factory must be callable")
        if not callable(sleep):
            raise TypeError("sleep must be callable")
        if embedding_profile_resolver is not None and not callable(
            embedding_profile_resolver
        ):
            raise TypeError("embedding_profile_resolver must be callable")
        if (
            not isinstance(poll_interval, (int, float))
            or isinstance(poll_interval, bool)
            or poll_interval < 0
        ):
            raise ValueError("poll_interval must be nonnegative")
        self._work_factory = unit_of_work_factory
        self._artifact_service = artifact_service
        self._document_search = document_search
        self._embedding_profile = embedding_profile
        self._embedding_profile_resolver = embedding_profile_resolver
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._sleep = sleep
        self._poll_interval = float(poll_interval)

    async def artifact_events(
        self,
        paper_id: str,
        kind: str = "explainer",
        profile: str = "standard",
    ) -> AsyncIterator[dict[str, object]]:
        try:
            if not isinstance(paper_id, str) or not paper_id.strip():
                raise LegacyProcessingStreamError("PAPER_ID_INVALID")
            if kind not in _ARTIFACT_KINDS:
                raise LegacyProcessingStreamError("ARTIFACT_KIND_UNSUPPORTED")
            if profile not in _ARTIFACT_PROFILES:
                raise LegacyProcessingStreamError("ARTIFACT_PROFILE_INVALID")
            if kind == "translation" and profile != "standard":
                raise LegacyProcessingStreamError("ARTIFACT_PROFILE_INVALID")
            source = await self._unique_ready_source(paper_id)
            enqueue = await self._artifact_service.enqueue(
                paper_id,
                source.id,
                _value(source.mode),
                kind,
                profile=profile,
                now=self._clock(),
            )
            async for item in self._poll_job(enqueue.job.id):
                if item.event is not None:
                    yield item.event
                    continue
                job = item.terminal_job
                if job is None:
                    raise LegacyProcessingStreamError("PROCESSING_JOB_MISSING")
                if getattr(job, "status", None) != "succeeded":
                    yield _failed_result(
                        getattr(job, "error_code", None) or "PROCESSING_JOB_FAILED"
                    )
                    return
                async with self._work_factory() as work:
                    artifact = await work.artifacts.get(enqueue.artifact.id)
                if (
                    artifact is None
                    or _value(getattr(artifact, "status", None)) != "ready"
                    or not isinstance(getattr(artifact, "content", None), str)
                    or not artifact.content.strip()
                ):
                    raise LegacyProcessingStreamError("ARTIFACT_RESULT_MISSING")
                yield {
                    "type": "result",
                    "ok": True,
                    "markdown": artifact.content,
                    "artifactId": artifact.id,
                    "jobId": enqueue.job.id,
                }
                return
        except Exception as error:
            yield _failed_result(_error_code(error))

    async def embedding_events(
        self,
        scope: str = "missing",
    ) -> AsyncIterator[dict[str, object]]:
        try:
            if scope not in _EMBEDDING_SCOPES:
                raise LegacyProcessingStreamError("EMBEDDING_SCOPE_INVALID")
            profile = self._embedding_profile
            if self._embedding_profile_resolver is not None:
                try:
                    resolved = self._embedding_profile_resolver()
                    profile = (
                        await resolved
                        if inspect.isawaitable(resolved)
                        else resolved
                    )
                except Exception:
                    raise LegacyProcessingStreamError(
                        "EMBEDDING_PROFILE_UNAVAILABLE"
                    ) from None
            if not isinstance(profile, EmbeddingProfile):
                raise LegacyProcessingStreamError("EMBEDDING_PROFILE_UNAVAILABLE")
            paper_ids = await self._paper_ids()
            resolved_sources: list[tuple[str, object]] = []
            for paper_id in paper_ids:
                resolved_sources.append(
                    (paper_id, await self._unique_ready_source(paper_id))
                )
            sources = tuple(resolved_sources)
            enqueued: list[object] = []
            for paper_id, source in sources:
                if scope == "missing":
                    status = await self._document_search.status(
                        source.id,
                        paper_id=paper_id,
                    )
                    if status.coverage == "complete":
                        continue
                result = await self._document_search.enqueue_index(
                    paper_id=paper_id,
                    source_mode=_value(source.mode),
                    source_document_id=source.id,
                    include_embeddings=True,
                    profile=profile,
                )
                enqueued.append(result.job)

            indexed = 0
            failures: list[str] = []
            for queued_job in enqueued:
                async for item in self._poll_job(queued_job.id):
                    if item.event is not None:
                        yield item.event
                        continue
                    job = item.terminal_job
                    if job is None:
                        failures.append("PROCESSING_JOB_MISSING")
                    elif getattr(job, "status", None) == "succeeded":
                        indexed += 1
                    else:
                        failures.append(
                            getattr(job, "error_code", None)
                            or "PROCESSING_JOB_FAILED"
                        )
            result: dict[str, object] = {
                "type": "result",
                "ok": not failures,
                "indexed": indexed,
                "total": len(sources),
            }
            if failures:
                result["error"] = failures[0]
            yield result
        except Exception as error:
            yield {
                **_failed_result(_error_code(error)),
                "indexed": 0,
                "total": 0,
            }

    async def _unique_ready_source(self, paper_id: str) -> object:
        cursor: tuple[str, str] | None = None
        seen_cursors: set[tuple[str, str]] = set()
        ready: list[object] = []
        while True:
            async with self._work_factory() as work:
                page, next_cursor = await work.sources.list_page(
                    paper_id=paper_id,
                    limit=100,
                    cursor=cursor,
                )
            ready.extend(
                source
                for source in page
                if _value(getattr(source, "status", None)) == "ready"
            )
            if len(ready) > 1:
                raise LegacyProcessingStreamError("SOURCE_IDENTITY_AMBIGUOUS")
            if next_cursor is None:
                break
            if next_cursor in seen_cursors:
                raise LegacyProcessingStreamError("SOURCE_IDENTITY_INVALID")
            seen_cursors.add(next_cursor)
            cursor = next_cursor
        if len(ready) != 1:
            raise LegacyProcessingStreamError("SOURCE_IDENTITY_MISSING")
        return ready[0]

    async def _paper_ids(self) -> tuple[str, ...]:
        async with self._work_factory() as work:
            rows = await work.papers.list_legacy()
        identifiers = [
            row.get("id")
            for row in rows
            if isinstance(row, dict)
        ]
        if any(not isinstance(value, str) or not value.strip() for value in identifiers):
            raise LegacyProcessingStreamError("PAPER_ID_INVALID")
        if len(identifiers) != len(set(identifiers)):
            raise LegacyProcessingStreamError("PAPER_ID_AMBIGUOUS")
        return tuple(sorted(identifiers))

    async def _poll_job(self, job_id: str) -> AsyncIterator[_PollItem]:
        after_sequence = -1
        batch_size = 100
        while True:
            async with self._work_factory() as work:
                events = await work.jobs.list_events_after(
                    job_id,
                    after_sequence=after_sequence,
                    limit=batch_size,
                )
                job = await work.jobs.get(job_id)
            if job is None:
                raise LegacyProcessingStreamError("PROCESSING_JOB_MISSING")
            for event in events:
                if event.sequence <= after_sequence:
                    raise LegacyProcessingStreamError("PROCESSING_EVENT_ORDER_INVALID")
                after_sequence = event.sequence
                yield _PollItem(event=_progress_event(job_id, event))
            if job.status in _TERMINAL_JOB_STATUSES and len(events) < batch_size:
                yield _PollItem(terminal_job=job)
                return
            await self._sleep(self._poll_interval)


def _progress_event(job_id: str, event: object) -> dict[str, object]:
    try:
        progress = json.loads(event.progress_json)
    except (TypeError, ValueError, json.JSONDecodeError) as error:
        raise LegacyProcessingStreamError("PROCESSING_EVENT_INVALID") from error
    if not isinstance(progress, dict):
        raise LegacyProcessingStreamError("PROCESSING_EVENT_INVALID")
    result: dict[str, object] = {
        "type": "progress",
        "line": str(event.event_type),
        "jobId": job_id,
        "sequence": event.sequence,
        "event": event.event_type,
        "progress": progress,
    }
    if event.error_code is not None:
        result["errorCode"] = event.error_code
    return result


def _value(value: object) -> str:
    raw = getattr(value, "value", value)
    return raw if isinstance(raw, str) else ""


def _error_code(error: Exception) -> str:
    code = getattr(error, "code", None)
    if isinstance(code, str) and code:
        return code
    return "LEGACY_PROCESSING_STREAM_FAILED"


def _failed_result(code: str) -> dict[str, object]:
    return {"type": "result", "ok": False, "error": code}


__all__ = ["LegacyProcessingStreamError", "LegacyProcessingStreams"]
