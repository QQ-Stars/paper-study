from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime, timedelta
from typing import Any


REVIEW_INTERVALS_DAYS = (0, 1, 2, 4, 7, 15, 30)
TOTAL_REVIEW_STEPS = len(REVIEW_INTERVALS_DAYS)


class ReviewScheduler:
    """Application adapter for the existing Ebbinghaus review plan rules."""

    def __init__(
        self,
        work_factory: Callable[[], Any],
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._work_factory = work_factory
        self._clock = clock or (lambda: datetime.now().astimezone())

    async def get_plan(self, paper_id: str) -> dict[str, object] | None:
        async with self._work_factory() as work:
            return await work.papers.get_review_plan(paper_id)

    async def ensure_plan(self, paper_id: str) -> dict[str, object] | None:
        today = _today(self._clock())
        async with self._work_factory() as work:
            if not await work.papers.exists(paper_id):
                return None
            existing = await work.papers.get_review_plan(paper_id)
            if existing is None:
                await work.papers.ensure_review_plan(
                    paper_id,
                    started_at=today,
                    next_due_at=_schedule_for_step(today, 1),
                    updated_at=today,
                )
                await work.commit()
            return await work.papers.get_review_plan(paper_id)

    async def start(self, paper_id: str) -> dict[str, object] | None:
        return await self.ensure_plan(paper_id)

    async def complete(self, paper_id: str) -> dict[str, object] | None:
        today = _today(self._clock())
        async with self._work_factory() as work:
            existing = await work.papers.get_review_plan(paper_id)
            if existing is None:
                return None
            if existing.get("completed_at"):
                return existing
            completed_steps = min(
                TOTAL_REVIEW_STEPS,
                max(0, _as_int(existing.get("completed_steps"))) + 1,
            )
            current_step = min(
                TOTAL_REVIEW_STEPS,
                max(1, _as_int(existing.get("current_step"))) + 1,
            )
            completed_at = today if completed_steps >= TOTAL_REVIEW_STEPS else None
            updated = await work.papers.complete_review_step(
                paper_id,
                completed_steps=completed_steps,
                current_step=current_step,
                next_due_at=_schedule_for_step(
                    str(existing.get("started_at") or today), current_step
                ),
                completed_at=completed_at,
                updated_at=today,
            )
            await work.commit()
            return updated

    async def complete_review_step(self, paper_id: str) -> dict[str, object] | None:
        return await self.complete(paper_id)

    async def list_snapshot(self) -> dict[str, object]:
        today = _today(self._clock())
        async with self._work_factory() as work:
            rows = await work.papers.list_review_items()
        groups: dict[str, list[dict[str, object]]] = {
            "overdue": [],
            "dueToday": [],
            "upcoming": [],
            "completed": [],
        }
        for row in rows:
            item = dict(row)
            state = _review_state(item, today)
            item["review_state"] = state
            item["total_steps"] = TOTAL_REVIEW_STEPS
            item["status"] = item.get("status") or "未开始"
            groups[state].append(item)
        return {
            "today": today,
            "counts": {
                "overdue": len(groups["overdue"]),
                "dueToday": len(groups["dueToday"]),
                "upcoming": len(groups["upcoming"]),
                "completed": len(groups["completed"]),
            },
            **groups,
        }

    async def list_review_items(self) -> dict[str, object]:
        return await self.list_snapshot()


def _today(value: datetime | date | str) -> str:
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    rendered = str(value).strip()
    if len(rendered) >= 10:
        rendered = rendered[:10]
    date.fromisoformat(rendered)
    return rendered


def _schedule_for_step(started_at: str, step: int) -> str:
    start = date.fromisoformat(str(started_at)[:10])
    normalized = max(1, min(TOTAL_REVIEW_STEPS, _as_int(step)))
    return (start + timedelta(days=REVIEW_INTERVALS_DAYS[normalized - 1])).isoformat()


def _review_state(row: dict[str, object], today: str) -> str:
    if row.get("completed_at"):
        return "completed"
    due = str(row.get("next_due_at") or "")[:10]
    if due < today:
        return "overdue"
    if due == today:
        return "dueToday"
    return "upcoming"


def _as_int(value: object) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


__all__ = [
    "REVIEW_INTERVALS_DAYS",
    "ReviewScheduler",
    "TOTAL_REVIEW_STEPS",
]
