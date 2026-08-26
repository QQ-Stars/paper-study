from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import datetime, timezone
import json
import re
import secrets
import time
from typing import Any

from backend.app.domain import MissingPaperError


_EDITABLE_FIELDS = (
    "title",
    "title_zh",
    "venue",
    "year",
    "type",
    "topic",
    "url",
    "pdf_url",
    "pdf_path",
    "tldr",
    "abstract",
    "contribution",
    "authors",
    "relevance",
    "order_no",
)

_VENUE_CANON = {
    "neurips": "NeurIPS",
    "nips": "NeurIPS",
    "cvpr": "CVPR",
    "iccv": "ICCV",
    "eccv": "ECCV",
    "wacv": "WACV",
    "icml": "ICML",
    "iclr": "ICLR",
    "aaai": "AAAI",
    "ijcai": "IJCAI",
    "acl": "ACL",
    "emnlp": "EMNLP",
    "naacl": "NAACL",
    "coling": "COLING",
    "tmlr": "TMLR",
    "tpami": "TPAMI",
    "corr": "arXiv",
}
_VENUE_FULL = (
    ("empirical methods in natural language", "EMNLP"),
    ("north american chapter", "NAACL"),
    ("findings of the association for computational linguistics", "ACL Findings"),
    ("association for computational linguistics", "ACL"),
    ("computer vision and pattern recognition", "CVPR"),
    ("european conference on computer vision", "ECCV"),
    ("winter conference on applications of computer vision", "WACV"),
    ("international conference on computer vision", "ICCV"),
    ("learning representations", "ICLR"),
    ("international conference on machine learning", "ICML"),
    ("neural information processing systems", "NeurIPS"),
    ("international joint conference on artificial intelligence", "IJCAI"),
    ("aaai conference on artificial intelligence", "AAAI"),
    ("advancement of artificial intelligence", "AAAI"),
    ("acm multimedia", "ACM MM"),
    ("international conference on multimedia", "ACM MM"),
)


class PaperLibrary:
    def __init__(
        self,
        unit_of_work_factory: Callable[[], Any],
        *,
        pdf_files: Any,
        id_factory: Callable[[str], str] | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._work_factory = unit_of_work_factory
        self._pdf_files = pdf_files
        self._id_factory = id_factory or _manual_id
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    async def add(self, fields: Mapping[str, object]) -> str:
        title = str(fields.get("title") or "").strip()
        if not title:
            raise ValueError("标题不能为空")
        now = _timestamp(self._clock())
        identifier = self._id_factory(title)
        authors = fields.get("authors")
        values: dict[str, object] = {
            "id": identifier,
            "title": title,
            "title_zh": (
                str(fields["title_zh"]).strip() if fields.get("title_zh") else None
            ),
            "venue": _normalize_venue(fields.get("venue")) or None,
            "year": str(fields["year"]) if fields.get("year") else None,
            "abstract": fields.get("abstract") or None,
            "tldr": fields.get("tldr") or None,
            "url": fields.get("url") or None,
            "pdf_url": fields.get("pdf_url") or None,
            "pdf_path": fields.get("pdf_path") or None,
            "type": fields.get("type") or "其他",
            "topic": fields.get("topic") or "其他",
            "contribution": fields.get("contribution") or None,
            "authors": (
                json.dumps(authors, ensure_ascii=False, separators=(",", ":"))
                if isinstance(authors, list)
                else authors or None
            ),
            "created_at": now,
            "updated_at": now,
        }
        async with self._work_factory() as work:
            await work.papers.add_legacy(values)
            await work.commit()
        return identifier

    async def update(self, paper_id: str, fields: Mapping[str, object]) -> int:
        normalized: dict[str, object] = {}
        for name in _EDITABLE_FIELDS:
            if name not in fields:
                continue
            value = fields[name]
            if name == "authors" and isinstance(value, list):
                value = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
            if value == "":
                value = None
            if name == "venue":
                value = _normalize_venue(value)
            normalized[name] = value
        async with self._work_factory() as work:
            changes = await work.papers.update_legacy(
                paper_id,
                normalized,
                updated_at=_timestamp(self._clock()),
            )
            await work.commit()
        return changes

    async def set_status(self, paper_id: str, status: object) -> None:
        now = self._clock()
        async with self._work_factory() as work:
            if not await work.papers.exists(paper_id):
                raise MissingPaperError(paper_id=paper_id)
            await work.papers.set_status(
                paper_id,
                status,
                updated_at=_timestamp(now),
            )
            if status == "已理解":
                day = now.astimezone().date().isoformat()
                await work.papers.ensure_review_plan(
                    paper_id,
                    started_at=day,
                    next_due_at=day,
                    updated_at=day,
                )
            await work.commit()

    async def set_favorite(self, paper_id: str, favorite: bool) -> None:
        async with self._work_factory() as work:
            if favorite and not await work.papers.exists(paper_id):
                raise MissingPaperError(paper_id=paper_id)
            await work.papers.set_favorite(
                paper_id,
                favorite,
                created_at=_timestamp(self._clock()),
            )
            await work.commit()

    async def delete(self, paper_id: str) -> None:
        async with self._work_factory() as work:
            if not await work.papers.exists(paper_id):
                raise MissingPaperError(paper_id=paper_id)
            await work.papers.delete_legacy(paper_id)
            await work.commit()
        try:
            await self._pdf_files.delete_for_paper(paper_id)
        except OSError:
            pass


def _normalize_venue(value: object) -> object:
    if not value:
        return value
    rendered = str(value).strip()
    lowered = rendered.lower()
    if lowered in _VENUE_CANON:
        return _VENUE_CANON[lowered]
    if lowered.startswith("arxiv"):
        return "arXiv"
    for fragment, abbreviation in _VENUE_FULL:
        if fragment in lowered:
            return abbreviation
    return rendered


def _timestamp(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("clock must return a timezone-aware datetime")
    return value.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _manual_id(title: str) -> str:
    slug = re.sub(r"[^a-z0-9一-龥]+", "-", title.lower()).strip("-")[:40]
    return f"manual-{slug or 'paper'}-{int(time.time() * 1000):x}{secrets.token_hex(2)[:3]}"


__all__ = ["PaperLibrary"]
