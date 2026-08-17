from __future__ import annotations

"""Application seam for the pre-P2 ``ingest_jobs`` compatibility API.

The old collector jobs intentionally remain a separate state machine from
``processing_jobs``.  This module owns the small amount of SQL needed by the
legacy adapter; HTTP routes only validate/serialize the resulting values.
"""

import asyncio
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
import json
from typing import Any, Callable

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError


_SOURCES = frozenset({"semanticscholar", "arxiv", "openalex", "dblp"})


class LegacyIngestError(Exception):
    code = "LEGACY_INGEST_ERROR"
    http_status = 500
    public_message = "Legacy ingest request failed."


class LegacyIngestValidationError(LegacyIngestError):
    code = "LEGACY_INGEST_INVALID"
    http_status = 400
    public_message = "Missing search query or data source."


class LegacyIngestNotFoundError(LegacyIngestError):
    code = "LEGACY_JOB_NOT_FOUND"
    http_status = 404
    public_message = "任务不存在"


class LegacyScheduleNotFoundError(LegacyIngestError):
    code = "LEGACY_SCHEDULE_NOT_FOUND"
    http_status = 404
    public_message = "Schedule not found."


class LegacyIngestService:
    """Repository-backed compatibility use cases for ingest jobs/schedules."""

    def __init__(
        self,
        session_factory: Any,
        *,
        provider: Any = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._provider = provider
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._background: set[asyncio.Task[Any]] = set()

    async def create_job(self, payload: Mapping[str, object]) -> int:
        query, sources = _validate_search_payload(payload)
        years = _years(payload.get("years"))
        max_papers = _bounded_int(payload.get("max"), default=12, upper=50)
        min_relevance = _bounded_float(payload.get("minRelevance"), default=0.5)
        only_a = 1 if bool(payload.get("onlyA")) else 0
        schedule_id = _optional_int(payload.get("scheduleId"))
        queries = payload.get("queries")
        if not isinstance(queries, str):
            queries = json.dumps([query], ensure_ascii=False, separators=(",", ":"))
        async with self._session_factory() as session:
            try:
                result = await session.execute(
                    text(
                        "INSERT INTO ingest_jobs("
                        "query,venues,year_from,year_to,max_papers,min_relevance,"
                        "only_a,schedule_id,queries,status) VALUES("
                        ":query,:venues,:year_from,:year_to,:max_papers,:min_relevance,"
                        ":only_a,:schedule_id,:queries,'pending')"
                    ),
                    {
                        "query": query,
                        "venues": ",".join(sources),
                        "year_from": years[0],
                        "year_to": years[1],
                        "max_papers": max_papers,
                        "min_relevance": min_relevance,
                        "only_a": only_a,
                        "schedule_id": schedule_id,
                        "queries": queries,
                    },
                )
                await session.commit()
            except SQLAlchemyError as error:
                await session.rollback()
                raise LegacyIngestError() from error
            identifier = result.lastrowid
        if identifier is None:
            raise LegacyIngestError()
        self._start_background(int(identifier))
        return int(identifier)

    async def list_jobs(self) -> list[dict[str, object]]:
        async with self._session_factory() as session:
            rows = (
                await session.execute(
                    text(
                        "SELECT id,query,venues,year_from,year_to,max_papers,min_relevance,"
                        "only_a,schedule_id,status,found,added,skipped,created_at,finished_at,"
                        "(SELECT count(*) FROM job_candidates c WHERE c.job_id=ingest_jobs.id "
                        "AND c.status='pending') AS pending "
                        "FROM ingest_jobs ORDER BY id DESC"
                    )
                )
            ).mappings().all()
        return [dict(row) for row in rows]

    async def get_job(self, identifier: int) -> dict[str, object] | None:
        async with self._session_factory() as session:
            row = (
                await session.execute(
                    text("SELECT * FROM ingest_jobs WHERE id=:id"), {"id": identifier}
                )
            ).mappings().one_or_none()
        return dict(row) if row is not None else None

    async def get_job_detail(self, identifier: int) -> dict[str, object]:
        job = await self.get_job(identifier)
        if job is None:
            raise LegacyIngestNotFoundError()
        async with self._session_factory() as session:
            rows = (
                await session.execute(
                    text(
                        "SELECT id,data FROM job_candidates "
                        "WHERE job_id=:job_id AND status='pending' ORDER BY id"
                    ),
                    {"job_id": identifier},
                )
            ).mappings().all()
        candidates: list[dict[str, object]] = []
        for row in rows:
            try:
                value = json.loads(str(row["data"] or "{}"))
            except (TypeError, ValueError):
                continue
            if isinstance(value, dict):
                value["_cid"] = row["id"]
                candidates.append(value)
        return {"ok": True, "job": job, "candidates": candidates}

    async def delete_job(self, identifier: int) -> int:
        async with self._session_factory() as session:
            await session.execute(
                text("DELETE FROM job_candidates WHERE job_id=:id"), {"id": identifier}
            )
            result = await session.execute(
                text("DELETE FROM ingest_jobs WHERE id=:id"), {"id": identifier}
            )
            await session.commit()
        return int(result.rowcount or 0)

    async def ingest_selected_events(
        self,
        candidates: Sequence[Mapping[str, object]],
        *,
        deep: bool = False,
        download_pdf: bool = True,
    ):
        normalized = [dict(item) for item in candidates if isinstance(item, Mapping)]
        provider = self._provider
        stream = getattr(provider, "stream_events", None) if provider is not None else None
        if not callable(stream):
            runner = (
                getattr(provider, "confirm_candidates", None)
                if provider is not None
                else None
            )
            if not callable(runner):
                yield {
                    "type": "done",
                    "ok": False,
                    "added": 0,
                    "error": "provider unavailable",
                }
                return
            result = runner(
                0,
                normalized,
                deep=deep,
                download_pdf=download_pdf,
            )
            if asyncio.iscoroutine(result):
                result = await result
            payload = dict(result) if isinstance(result, Mapping) else {}
            payload.pop("jobId", None)
            payload.setdefault("ok", False)
            payload.setdefault("added", 0)
            payload.setdefault("error", "" if payload["ok"] else "legacy agent failed")
            yield {"type": "done", **payload}
            return

        args: list[str] = []
        if deep:
            args.append("--deep")
        if not download_pdf:
            args.append("--no-pdf")
        added: int | None = None
        async for event in stream(
            "ingest-selected",
            args,
            terminal_type="done",
            terminal_fields={"added": 0},
            stdin=json.dumps(normalized, ensure_ascii=False),
        ):
            rendered = dict(event)
            if rendered.get("type") == "progress":
                line = str(rendered.get("line") or "")
                if line.startswith("INGESTED::"):
                    try:
                        added = max(0, int(line.split("::", 1)[1]))
                    except ValueError:
                        pass
            elif rendered.get("type") == "done" and added is not None:
                rendered["added"] = added
            yield rendered

    async def confirm(
        self,
        identifier: int,
        candidates: Sequence[Mapping[str, object]],
        *,
        deep: bool = False,
        download_pdf: bool = True,
    ) -> dict[str, object]:
        if not candidates:
            raise LegacyIngestValidationError()
        job = await self.get_job(identifier)
        if job is None:
            raise LegacyIngestNotFoundError()
        candidate_ids = _candidate_ids(candidates)
        added = 0
        if self._provider is not None:
            runner = getattr(self._provider, "confirm_candidates", None)
            if callable(runner):
                result = runner(
                    identifier,
                    list(candidates),
                    deep=deep,
                    download_pdf=download_pdf,
                )
                if asyncio.iscoroutine(result):
                    result = await result
                if isinstance(result, Mapping):
                    added = _bounded_int(result.get("added"), default=0, upper=100000)
        await self._record_confirmation(identifier, candidate_ids, added)
        return {"type": "done", "ok": True, "added": added}

    async def confirm_events(
        self,
        identifier: int,
        candidates: Sequence[Mapping[str, object]],
        *,
        deep: bool = False,
        download_pdf: bool = True,
    ):
        if not candidates:
            raise LegacyIngestValidationError()
        if await self.get_job(identifier) is None:
            raise LegacyIngestNotFoundError()
        candidate_ids = _candidate_ids(candidates)
        async for event in self.ingest_selected_events(
            candidates,
            deep=deep,
            download_pdf=download_pdf,
        ):
            rendered = dict(event)
            if rendered.get("type") != "done":
                yield rendered
                continue
            if rendered.get("ok"):
                added = _bounded_int(
                    rendered.get("added"),
                    default=0,
                    upper=100000,
                )
                await self._record_confirmation(identifier, candidate_ids, added)
                rendered["added"] = added
            yield rendered
            return
        yield {
            "type": "done",
            "ok": False,
            "added": 0,
            "error": "legacy agent failed",
        }

    async def _record_confirmation(
        self,
        identifier: int,
        candidate_ids: Sequence[int],
        added: int,
    ) -> None:
        async with self._session_factory() as session:
            if candidate_ids:
                # The status update is deliberately scoped to this job and the
                # selected IDs; candidates from another job can never be marked.
                await session.execute(
                    text(
                        "UPDATE job_candidates SET status='added' "
                        "WHERE job_id=:job_id AND id IN ("
                        + ",".join(f":cid_{i}" for i in range(len(candidate_ids)))
                        + ")"
                    ),
                    {"job_id": identifier, **{
                        f"cid_{i}": value for i, value in enumerate(candidate_ids)
                    }},
                )
            await session.execute(
                text("UPDATE ingest_jobs SET added=coalesce(added,0)+:added WHERE id=:id"),
                {"id": identifier, "added": added},
            )
            await session.execute(
                text(
                    "UPDATE ingest_jobs SET status='done',finished_at=:finished "
                    "WHERE id=:id AND NOT EXISTS ("
                    "SELECT 1 FROM job_candidates WHERE job_id=:id AND status='pending')"
                ),
                {"id": identifier, "finished": _timestamp(self._clock())},
            )
            await session.commit()

    async def list_schedules(self) -> list[dict[str, object]]:
        async with self._session_factory() as session:
            rows = (
                await session.execute(text("SELECT * FROM job_schedules ORDER BY id DESC"))
            ).mappings().all()
        return [dict(row) for row in rows]

    async def create_schedule(self, payload: Mapping[str, object]) -> int:
        query, sources = _validate_search_payload(payload)
        every_days = max(1, _bounded_int(payload.get("everyDays"), default=7, upper=3650))
        max_papers = _bounded_int(payload.get("max"), default=12, upper=50)
        min_relevance = _bounded_float(payload.get("minRelevance"), default=0.5)
        async with self._session_factory() as session:
            result = await session.execute(
                text(
                    "INSERT INTO job_schedules(query,sources,years,max_papers,min_relevance,"
                    "only_a,every_days,enabled,next_run) VALUES(:query,:sources,:years,"
                    ":max_papers,:min_relevance,:only_a,:every_days,1,:next_run)"
                ),
                {
                    "query": query,
                    "sources": json.dumps(sources, ensure_ascii=False, separators=(",", ":")),
                    "years": str(payload.get("years") or "2024-2026"),
                    "max_papers": max_papers,
                    "min_relevance": min_relevance,
                    "only_a": 1 if bool(payload.get("onlyA")) else 0,
                    "every_days": every_days,
                    "next_run": _timestamp(self._clock()),
                },
            )
            await session.commit()
        if result.lastrowid is None:
            raise LegacyIngestError()
        return int(result.lastrowid)

    async def toggle_schedule(self, identifier: int, enabled: bool) -> int:
        async with self._session_factory() as session:
            result = await session.execute(
                text("UPDATE job_schedules SET enabled=:enabled WHERE id=:id"),
                {"id": identifier, "enabled": 1 if enabled else 0},
            )
            await session.commit()
        return int(result.rowcount or 0)

    async def delete_schedule(self, identifier: int) -> int:
        async with self._session_factory() as session:
            result = await session.execute(
                text("DELETE FROM job_schedules WHERE id=:id"), {"id": identifier}
            )
            await session.commit()
        return int(result.rowcount or 0)

    async def tick(self) -> tuple[int, ...]:
        """Claim due schedules atomically and enqueue at most one job each."""

        created: list[int] = []
        now = _timestamp(self._clock())
        async with self._session_factory() as session:
            # SQLite serializes this short transaction, which is the lease/CAS
            # fence for competing scheduler ticks.
            await session.execute(text("BEGIN IMMEDIATE"))
            rows = (
                await session.execute(
                    text(
                        "SELECT * FROM job_schedules WHERE enabled=1 AND "
                        "(next_run IS NULL OR next_run<=:now) ORDER BY id"
                    ),
                    {"now": now},
                )
            ).mappings().all()
            for row in rows:
                sources = _parse_sources(row.get("sources"))
                result = await session.execute(
                    text(
                        "INSERT INTO ingest_jobs(query,venues,year_from,year_to,max_papers,"
                        "min_relevance,only_a,schedule_id,status) VALUES(:query,:venues,"
                        ":year_from,:year_to,:max_papers,:min_relevance,:only_a,:schedule_id,'pending')"
                    ),
                    {
                        "query": row["query"],
                        "venues": ",".join(sources),
                        "year_from": _years(row.get("years"))[0],
                        "year_to": _years(row.get("years"))[1],
                        "max_papers": row.get("max_papers") or 12,
                        "min_relevance": row.get("min_relevance") or 0.5,
                        "only_a": row.get("only_a") or 0,
                        "schedule_id": row["id"],
                    },
                )
                if result.lastrowid is not None:
                    created.append(int(result.lastrowid))
                every_days = max(1, int(row.get("every_days") or 7))
                await session.execute(
                    text(
                        "UPDATE job_schedules SET last_run=:now,next_run="
                        "datetime(:now, :modifier) WHERE id=:id"
                    ),
                    {"now": now, "modifier": f"+{every_days} days", "id": row["id"]},
                )
            await session.commit()
        for identifier in created:
            self._start_background(identifier)
        return tuple(created)

    def _start_background(self, identifier: int) -> None:
        runner = getattr(self._provider, "run_job", None) if self._provider is not None else None
        if not callable(runner):
            return
        task = asyncio.create_task(self._run_background(identifier, runner))
        self._background.add(task)
        task.add_done_callback(self._background.discard)

    async def _run_background(self, identifier: int, runner: Any) -> None:
        result: Any = None
        try:
            result = runner(identifier)
            if asyncio.iscoroutine(result):
                result = await result
        except asyncio.CancelledError:
            raise
        except Exception:
            # The provider owns detailed failure persistence.  A route response
            # must never expose child-process or credential text.
            return
        # 对齐旧 Node runJobBackground：子进程的 stderr 进度写回
        # ingest_jobs.log，用户回到任务页时能看到服务端真实进度；
        # 子进程异常退出而 agent 未及写状态时，兼容置 failed。
        stderr_text = str(getattr(result, "stderr", "") or "")
        returncode = getattr(result, "returncode", None)
        if stderr_text.strip() or (returncode is not None and int(returncode) != 0):
            await self._persist_background_result(identifier, stderr_text, returncode)

    async def _persist_background_result(
        self, identifier: int, stderr_text: str, returncode: object
    ) -> None:
        async with self._session_factory() as session:
            if stderr_text.strip():
                chunk = stderr_text if stderr_text.endswith("\n") else stderr_text + "\n"
                await session.execute(
                    text(
                        "UPDATE ingest_jobs SET log=coalesce(log,'')||:chunk WHERE id=:id"
                    ),
                    {"id": identifier, "chunk": chunk},
                )
            if returncode is not None and int(returncode) != 0:
                await session.execute(
                    text(
                        "UPDATE ingest_jobs SET status='failed', finished_at=:finished "
                        "WHERE id=:id AND status IN ('pending','running')"
                    ),
                    {"id": identifier, "finished": _timestamp(self._clock())},
                )
            await session.commit()


def _validate_search_payload(payload: Mapping[str, object]) -> tuple[str, tuple[str, ...]]:
    query = str(payload.get("query") or "").strip()
    raw_sources = payload.get("sources")
    if not isinstance(raw_sources, Sequence) or isinstance(raw_sources, (str, bytes)):
        raw_sources = ()
    sources = tuple(dict.fromkeys(str(value) for value in raw_sources if str(value) in _SOURCES))
    if not query or not sources:
        raise LegacyIngestValidationError()
    return query, sources


def _parse_sources(value: object) -> tuple[str, ...]:
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except ValueError:
            decoded = value.split(",")
    else:
        decoded = value
    if not isinstance(decoded, Sequence) or isinstance(decoded, (str, bytes)):
        decoded = ()
    return tuple(dict.fromkeys(str(item) for item in decoded if str(item) in _SOURCES))


def _years(value: object) -> tuple[int | None, int | None]:
    if value is None:
        return None, None
    rendered = str(value).strip()
    pieces = rendered.split("-", 1)
    try:
        first = int(pieces[0])
        second = int(pieces[1]) if len(pieces) == 2 and pieces[1] else first
    except ValueError:
        return None, None
    return first, second


def _bounded_int(value: object, *, default: int, upper: int) -> int:
    try:
        number = int(value) if value is not None else default
    except (TypeError, ValueError):
        number = default
    return max(0, min(number, upper))


def _bounded_float(value: object, *, default: float) -> float:
    try:
        number = float(value) if value is not None else default
    except (TypeError, ValueError):
        number = default
    return number if number == number else default


def _candidate_ids(candidates: Sequence[Mapping[str, object]]) -> list[int]:
    return [
        value
        for item in candidates
        for value in (_optional_int(item.get("_cid")),)
        if value is not None
    ]


def _optional_int(value: object) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _timestamp(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("clock must return a timezone-aware datetime")
    return value.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


__all__ = [
    "LegacyIngestError",
    "LegacyIngestNotFoundError",
    "LegacyIngestService",
    "LegacyIngestValidationError",
    "LegacyScheduleNotFoundError",
]
