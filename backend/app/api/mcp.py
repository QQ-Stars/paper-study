from __future__ import annotations

from datetime import date, datetime
import json
from pathlib import Path
import re
import sqlite3
from typing import Callable, Literal

from backend.app.repositories.read_only import ArtifactKind, McpReadRepository


DEFAULT_TEXT_CHARS = 12000
MAX_TEXT_CHARS = 20000
MAX_RESULT_LIMIT = 50
REVIEW_TOTAL_STEPS = 7
ArtifactReadMode = Literal["legacy", "prefer_new"]
_SAFE_ERROR_CODE = re.compile(r"[A-Z][A-Z0-9_]{0,63}\Z")
_SORT = {
    "relevance": "relevance DESC",
    "year": "year DESC",
    "citations": "citations DESC",
    "recent": "created_at DESC",
}


def _ok(**data: object) -> dict[str, object]:
    return {"ok": True, **data}


def _err(message: str, **data: object) -> dict[str, object]:
    return {"ok": False, "error": message, **data}


def _clamp_int(value: object, default: int, low: int, high: int) -> int:
    try:
        number = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        number = default
    return max(low, min(number, high))


def _chunk_text(
    paper_id: str,
    content: str | None,
    *,
    offset: object = 0,
    max_chars: object = DEFAULT_TEXT_CHARS,
) -> dict[str, object]:
    text = content or ""
    start = _clamp_int(offset, 0, 0, len(text))
    size = _clamp_int(max_chars, DEFAULT_TEXT_CHARS, 1, MAX_TEXT_CHARS)
    end = min(len(text), start + size)
    next_offset = end if end < len(text) else None
    return _ok(
        id=paper_id,
        content=text[start:end],
        offset=start,
        next_offset=next_offset,
        total_chars=len(text),
        truncated=next_offset is not None,
    )


def _jload(value: object) -> list[object]:
    if not value:
        return []
    try:
        decoded = json.loads(str(value))
        return decoded if isinstance(decoded, list) else [decoded]
    except (TypeError, ValueError):
        return [item.strip() for item in str(value).split(",") if item.strip()]


def _date_only(value: object = "") -> str:
    if not value:
        return date.today().isoformat()
    text = str(value).strip()
    try:
        if len(text) >= 10 and text[4] == "-" and text[7] == "-":
            return date.fromisoformat(text[:10]).isoformat()
        return datetime.fromisoformat(text).date().isoformat()
    except ValueError:
        return date.today().isoformat()


def _review_state(
    next_due_at: str,
    completed_at: str | None,
    today: str,
) -> str:
    if completed_at:
        return "completed"
    if next_due_at < today:
        return "overdue"
    if next_due_at == today:
        return "dueToday"
    return "upcoming"


class ApplicationMcpAdapter:
    def __init__(
        self,
        database_path: str | Path,
        *,
        artifact_read_mode: ArtifactReadMode,
        ranker: Callable[..., list[dict[str, object]]],
        has_pdf: Callable[[object], bool],
    ) -> None:
        if artifact_read_mode not in {"legacy", "prefer_new"}:
            raise ValueError("artifact_read_mode must be legacy or prefer_new")
        self._repository = McpReadRepository(database_path)
        self._artifact_read_mode = artifact_read_mode
        self._ranker = ranker
        self._has_pdf = has_pdf

    def _artifact_content(self, paper_id: str, kind: ArtifactKind) -> str | None:
        if self._artifact_read_mode == "prefer_new":
            selected = self._repository.selected_artifact(paper_id, kind)
            if selected is not None:
                return selected.content
        return self._repository.legacy_artifact_content(paper_id, kind)

    def _compact(self, row: sqlite3.Row) -> dict[str, object]:
        content = self._artifact_content(str(row["id"]), "explainer")
        return {
            "id": row["id"],
            "title": row["title"],
            "title_zh": row["title_zh"],
            "venue": row["venue"],
            "year": row["year"],
            "type": row["type"],
            "topic": row["topic"],
            "relevance": row["relevance"],
            "citations": row["citations"],
            "tldr": row["tldr"],
            "has_explainer": bool((content or "").strip()),
            "has_pdf": bool(self._has_pdf(row)),
        }

    def _source_document_view(self, paper_id: str) -> dict[str, object]:
        summaries = self._repository.source_document_summaries(paper_id)
        result: dict[str, object] = {}
        for mode in ("native", "ocr"):
            summary = summaries[mode]
            if summary is None:
                result[mode] = None
                continue
            code = summary.error_code
            safe_error = None
            if code is not None:
                safe_code = code if _SAFE_ERROR_CODE.fullmatch(code) else "PROCESSING_FAILED"
                safe_error = {"code": safe_code, "message": "Processing failed."}
            result[mode] = {
                "currentId": summary.identifier,
                "status": summary.status,
                "updatedAt": summary.updated_at,
                "error": safe_error,
            }
        return result

    @staticmethod
    def _read_failed(paper_id: str) -> dict[str, object]:
        return _err(
            "MCP application read failed",
            code="MCP_APPLICATION_READ_FAILED",
            id=paper_id,
        )

    def get_explainer(
        self,
        id: str,
        offset: int = 0,
        max_chars: int = DEFAULT_TEXT_CHARS,
    ) -> dict[str, object]:
        try:
            content = self._artifact_content(id, "explainer")
            exists = self._repository.paper_exists(id)
        except Exception:
            return self._read_failed(id)
        if not exists:
            return _err(f"未找到论文 id={id}", id=id)
        if not (content or "").strip():
            return _err(
                f"该论文暂无讲解（可在网页阅读页点「✨ 生成讲解」生成）。id={id}",
                id=id,
            )
        return _chunk_text(id, content, offset=offset, max_chars=max_chars)

    def get_translation(
        self,
        id: str,
        offset: int = 0,
        max_chars: int = DEFAULT_TEXT_CHARS,
    ) -> dict[str, object]:
        try:
            content = self._artifact_content(id, "translation")
        except Exception:
            return self._read_failed(id)
        if not (content or "").strip():
            return _err(f"该论文暂无中文翻译（可在网页阅读页生成）。id={id}", id=id)
        return _chunk_text(id, content, offset=offset, max_chars=max_chars)

    def search_papers(
        self,
        query: str = "",
        type: str = "",
        topic: str = "",
        venue: str = "",
        year_from: int = 0,
        year_to: int = 0,
        min_relevance: float = 0.0,
        has_explainer: bool = False,
        only_favorites: bool = False,
        sort: str = "relevance",
        limit: int = 20,
    ) -> dict[str, object]:
        where: list[str] = []
        arguments: list[object] = []
        if query.strip():
            like = f"%{query.strip()}%"
            where.append(
                "(title LIKE ? OR title_zh LIKE ? OR abstract LIKE ? OR tldr LIKE ? "
                "OR contribution LIKE ? OR topic LIKE ? OR task LIKE ? OR tags LIKE ?)"
            )
            arguments.extend([like] * 8)
        if type.strip():
            where.append("type = ?")
            arguments.append(type.strip())
        if topic.strip():
            where.append("topic LIKE ?")
            arguments.append(f"%{topic.strip()}%")
        if venue.strip():
            where.append("venue LIKE ?")
            arguments.append(f"%{venue.strip()}%")
        if year_from:
            where.append("CAST(year AS INTEGER) >= ?")
            arguments.append(int(year_from))
        if year_to:
            where.append("CAST(year AS INTEGER) <= ?")
            arguments.append(int(year_to))
        if min_relevance:
            where.append("relevance >= ?")
            arguments.append(float(min_relevance))
        if only_favorites:
            where.append("id IN (SELECT paper_id FROM favorites)")
        sql = "SELECT * FROM papers"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY " + _SORT.get(sort, _SORT["relevance"])
        capped_limit = _clamp_int(limit, 20, 1, MAX_RESULT_LIMIT)
        if not has_explainer:
            sql += " LIMIT ?"
            arguments.append(capped_limit)
        try:
            with self._repository.connect() as connection:
                rows = connection.execute(sql, arguments).fetchall()
            results = [self._compact(row) for row in rows]
            if has_explainer:
                results = [
                    row for row in results if row["has_explainer"]
                ][:capped_limit]
            return _ok(count=len(results), results=results)
        except Exception:
            return self._read_failed("")

    def semantic_search(self, query: str, k: int = 15) -> dict[str, object]:
        capped_k = _clamp_int(k, 15, 1, MAX_RESULT_LIMIT)
        try:
            ranked = self._ranker(query, capped_k, reindex_stale=False)
            with self._repository.connect() as connection:
                indexed = connection.execute(
                    "SELECT COUNT(*) FROM paper_vectors"
                ).fetchone()[0]
                total = connection.execute("SELECT COUNT(*) FROM papers").fetchone()[0]
                rows = {
                    row["id"]: row
                    for item in ranked
                    if (
                        row := connection.execute(
                            "SELECT * FROM papers WHERE id=?", (item["id"],)
                        ).fetchone()
                    )
                }
            results = []
            for item in ranked:
                row = rows.get(item["id"])
                if row is not None:
                    compact = self._compact(row)
                    compact["score"] = item["score"]
                    results.append(compact)
            response = _ok(
                count=len(results), indexed=indexed, total=total, results=results
            )
            if indexed < total:
                response["note"] = (
                    f"语义索引覆盖 {indexed}/{total} 篇；未入索引的论文不会出现在语义结果里"
                    "（可在网页端做一次语义检索或重建索引以补全）。"
                )
            return response
        except Exception:
            return self._read_failed("")

    def related_papers(self, id: str, k: int = 8) -> dict[str, object]:
        capped_k = _clamp_int(k, 8, 1, MAX_RESULT_LIMIT)
        try:
            with self._repository.connect() as connection:
                seed = connection.execute(
                    "SELECT id,title,tldr,abstract FROM papers WHERE id=?", (id,)
                ).fetchone()
            if seed is None:
                return _err(f"未找到论文 id={id}", id=id)
            seed_text = (seed["title"] or "") + ". " + (
                seed["tldr"] or seed["abstract"] or ""
            )
            ranked = self._ranker(
                seed_text, capped_k, exclude=id, reindex_stale=False
            )
            with self._repository.connect() as connection:
                rows = {
                    row["id"]: row
                    for item in ranked
                    if (
                        row := connection.execute(
                            "SELECT * FROM papers WHERE id=?", (item["id"],)
                        ).fetchone()
                    )
                }
            results = []
            for item in ranked:
                row = rows.get(item["id"])
                if row is not None:
                    compact = self._compact(row)
                    compact["score"] = item["score"]
                    results.append(compact)
            return _ok(seed=id, count=len(results), results=results)
        except Exception:
            return self._read_failed(id)

    def get_paper(self, id: str) -> dict[str, object]:
        try:
            with self._repository.connect() as connection:
                row = connection.execute(
                    "SELECT * FROM papers WHERE id=?", (id,)
                ).fetchone()
                if row is None:
                    return _err(f"未找到论文 id={id}", id=id)
                note_row = connection.execute(
                    "SELECT content FROM notes WHERE paper_id=?", (id,)
                ).fetchone()
                status_row = connection.execute(
                    "SELECT status FROM progress WHERE paper_id=?", (id,)
                ).fetchone()
                favorite = connection.execute(
                    "SELECT 1 FROM favorites WHERE paper_id=?", (id,)
                ).fetchone()
            explainer = self._artifact_content(id, "explainer")
            translation = self._artifact_content(id, "translation")
            source_view = self._source_document_view(id)
            return _ok(
                id=row["id"],
                title=row["title"],
                title_zh=row["title_zh"],
                authors=_jload(row["authors"]),
                venue=row["venue"],
                year=row["year"],
                doi=row["doi"],
                arxiv_id=row["arxiv_id"],
                url=row["url"],
                citations=row["citations"],
                abstract=row["abstract"],
                tldr=row["tldr"],
                type=row["type"],
                topic=row["topic"],
                task=row["task"],
                models=_jload(row["models"]),
                datasets=_jload(row["datasets"]),
                contribution=row["contribution"],
                tags=_jload(row["tags"]),
                fields=_jload(row["s2_fields"]),
                relevance=row["relevance"],
                note=note_row[0] if note_row is not None else "",
                progress=status_row[0] if status_row is not None else "未开始",
                favorite=favorite is not None,
                has_explainer=bool((explainer or "").strip()),
                has_translation=bool((translation or "").strip()),
                has_pdf=bool(self._has_pdf(row)),
                sourceDocument=source_view,
            )
        except Exception:
            return self._read_failed(id)

    def list_due_reviews(
        self,
        today: str = "",
        include_upcoming: bool = False,
        limit: int = 20,
    ) -> dict[str, object]:
        today_value = _date_only(today)
        capped_limit = _clamp_int(limit, 20, 1, MAX_RESULT_LIMIT)
        where = ["r.completed_at IS NULL"]
        arguments: list[object] = []
        if not include_upcoming:
            where.append("r.next_due_at <= ?")
            arguments.append(today_value)
        arguments.append(capped_limit)
        sql = f"""
            SELECT r.paper_id, r.started_at, r.current_step, r.completed_steps,
                   r.next_due_at, r.completed_at, r.updated_at, p.title,
                   p.title_zh, p.venue, p.year,
                   COALESCE(NULLIF(TRIM(progress.status), ''), '未开始') AS progress
            FROM paper_reviews AS r
            JOIN papers AS p ON p.id = r.paper_id
            LEFT JOIN progress ON progress.paper_id = r.paper_id
            WHERE {' AND '.join(where)}
            ORDER BY r.next_due_at ASC, p.title COLLATE NOCASE ASC, r.paper_id ASC
            LIMIT ?
        """
        try:
            with self._repository.connect() as connection:
                rows = connection.execute(sql, arguments).fetchall()
            results = [
                {
                    "id": row["paper_id"],
                    "title": row["title"],
                    "title_zh": row["title_zh"],
                    "venue": row["venue"],
                    "year": row["year"],
                    "progress": row["progress"],
                    "review_state": _review_state(
                        row["next_due_at"], row["completed_at"], today_value
                    ),
                    "started_at": row["started_at"],
                    "current_step": row["current_step"],
                    "completed_steps": row["completed_steps"],
                    "total_steps": REVIEW_TOTAL_STEPS,
                    "next_due_at": row["next_due_at"],
                    "updated_at": row["updated_at"],
                }
                for row in rows
            ]
            return _ok(
                today=today_value,
                count=len(results),
                include_upcoming=bool(include_upcoming),
                results=results,
            )
        except Exception:
            return self._read_failed("")

    def list_categories(self) -> dict[str, object]:
        try:
            with self._repository.connect() as connection:
                def counts(column: str) -> list[dict[str, object]]:
                    rows = connection.execute(
                        f"SELECT {column} AS k, COUNT(*) AS c FROM papers "
                        f"WHERE {column} IS NOT NULL AND TRIM({column})!='' "
                        f"GROUP BY {column} ORDER BY c DESC, k"
                    ).fetchall()
                    return [{"name": row["k"], "count": row["c"]} for row in rows]

                return _ok(
                    types=counts("type"),
                    topics=counts("topic"),
                    tasks=counts("task"),
                )
        except Exception:
            return self._read_failed("")

    def library_overview(self) -> dict[str, object]:
        try:
            with self._repository.connect() as connection:
                def scalar(sql: str) -> object:
                    row = connection.execute(sql).fetchone()
                    return row[0] if row is not None else None

                def grouped(column: str, limit: int = 0) -> list[dict[str, object]]:
                    sql = (
                        f"SELECT {column} AS k, COUNT(*) AS c FROM papers "
                        f"WHERE {column} IS NOT NULL AND TRIM({column})!='' "
                        f"GROUP BY {column} ORDER BY c DESC, k"
                    )
                    if limit:
                        sql += f" LIMIT {limit}"
                    return [
                        {"name": row["k"], "count": row["c"]}
                        for row in connection.execute(sql).fetchall()
                    ]

                paper_ids = [
                    row[0]
                    for row in connection.execute(
                        "SELECT id FROM papers ORDER BY id"
                    ).fetchall()
                ]
                years = [
                    {"year": row["k"], "count": row["c"]}
                    for row in connection.execute(
                        "SELECT year AS k, COUNT(*) AS c FROM papers "
                        "WHERE year IS NOT NULL AND TRIM(year)!='' "
                        "GROUP BY year ORDER BY k"
                    ).fetchall()
                ]
                total = scalar("SELECT COUNT(*) FROM papers") or 0
                favorites = scalar("SELECT COUNT(*) FROM favorites") or 0
                indexed = scalar("SELECT COUNT(*) FROM paper_vectors") or 0
                review_due = scalar(
                    "SELECT COUNT(*) FROM paper_reviews WHERE completed_at IS NULL "
                    "AND next_due_at <= date('now')"
                ) or 0
                review_open = scalar(
                    "SELECT COUNT(*) FROM paper_reviews WHERE completed_at IS NULL"
                ) or 0
                high = scalar("SELECT COUNT(*) FROM papers WHERE relevance >= 0.8") or 0
                middle = scalar(
                    "SELECT COUNT(*) FROM papers WHERE relevance >= 0.6 AND relevance < 0.8"
                ) or 0
                low = scalar(
                    "SELECT COUNT(*) FROM papers WHERE relevance < 0.6 AND relevance IS NOT NULL"
                ) or 0
                average = scalar(
                    "SELECT ROUND(AVG(citations),1) FROM papers WHERE citations IS NOT NULL"
                )
                by_type = grouped("type")
                by_topic = grouped("topic", 15)
                by_venue = grouped("venue", 15)
            with_explainer = sum(
                bool((self._artifact_content(paper_id, "explainer") or "").strip())
                for paper_id in paper_ids
            )
            with_translation = sum(
                bool((self._artifact_content(paper_id, "translation") or "").strip())
                for paper_id in paper_ids
            )
            return _ok(
                total=total,
                with_explainer=with_explainer,
                with_translation=with_translation,
                favorites=favorites,
                indexed_vectors=indexed,
                review_due=review_due,
                review_open=review_open,
                by_type=by_type,
                by_topic_top15=by_topic,
                by_venue_top15=by_venue,
                by_year=years,
                relevance_buckets={">=0.8": high, "0.6-0.8": middle, "<0.6": low},
                avg_citations=average,
            )
        except Exception:
            return self._read_failed("")
