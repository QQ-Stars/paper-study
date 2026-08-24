"""Fill missing Paper metadata from Semantic Scholar without changing legacy tables."""

import json
import sys
import time
from urllib.parse import quote

import httpx

from . import config, db, util


PAPER_URL = "https://api.semanticscholar.org/graph/v1/paper"
SEARCH_URL = f"{PAPER_URL}/search"
FIELDS = "paperId,title,year,venue,authors,externalIds"
MAX_ATTEMPTS = 4


def _p(message):
    print(message, file=sys.stderr, flush=True)


def _headers():
    headers = dict(util.UA)
    if getattr(config, "S2_API_KEY", ""):
        headers["x-api-key"] = config.S2_API_KEY
    return headers


def _retry_delay(attempt, response=None):
    retry_after = response.headers.get("retry-after") if response is not None else None
    try:
        delay = float(retry_after) if retry_after is not None else 2.0**attempt
    except (TypeError, ValueError):
        delay = 2.0**attempt
    return min(max(2.0, delay), 30.0)


def _get(url, **kwargs):
    for attempt in range(MAX_ATTEMPTS):
        try:
            response = httpx.get(url, **kwargs)
        except httpx.RequestError:
            if attempt + 1 >= MAX_ATTEMPTS:
                raise
            time.sleep(_retry_delay(attempt))
            continue
        if response.status_code == 429 or response.status_code >= 500:
            if attempt + 1 >= MAX_ATTEMPTS:
                response.raise_for_status()
            time.sleep(_retry_delay(attempt, response))
            continue
        return response
    raise RuntimeError("Semantic Scholar rate limit persisted after retries")


def _request_paper(identifier):
    response = _get(
        f"{PAPER_URL}/{quote(identifier, safe='')}",
        params={"fields": FIELDS},
        headers=_headers(),
        timeout=45,
        follow_redirects=True,
    )
    if response.status_code == 404:
        return None
    response.raise_for_status()
    payload = response.json()
    return payload if isinstance(payload, dict) else None


def _search_title(title):
    response = _get(
        SEARCH_URL,
        params={"query": title, "fields": FIELDS, "limit": 5},
        headers=_headers(),
        timeout=45,
        follow_redirects=True,
    )
    response.raise_for_status()
    payload = response.json()
    rows = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        return None
    candidates = [row for row in rows if isinstance(row, dict)]
    wanted = db.title_norm(title)
    exact = next(
        (
            row
            for row in candidates
            if wanted and db.title_norm(row.get("title") or "") == wanted
        ),
        None,
    )
    return exact or next(iter(candidates), None)


def _fetch_metadata(paper):
    identifiers = []
    if paper.get("arxiv_id"):
        identifiers.append(f"ARXIV:{paper['arxiv_id']}")
    if paper.get("doi"):
        identifiers.append(f"DOI:{paper['doi']}")
    for identifier in identifiers:
        payload = _request_paper(identifier)
        if payload:
            return payload
    return _search_title(paper["title"])


def _ensure_authors_table(connection):
    connection.execute(
        """CREATE TABLE IF NOT EXISTS paper_authors (
            paper_id  TEXT PRIMARY KEY REFERENCES papers(id) ON DELETE CASCADE,
            authors   TEXT NOT NULL DEFAULT '[]',
            updated_at TEXT DEFAULT (datetime('now'))
        )"""
    )
    connection.commit()


def _author_names(payload):
    names = []
    for author in payload.get("authors") or []:
        name = author.get("name") if isinstance(author, dict) else None
        name = str(name or "").strip()
        if name and name not in names:
            names.append(name)
    return names


def _offline_metadata(paper):
    """Recover metadata that is already derivable from local paper identity."""
    assignments = []
    parameters = []
    arxiv_id = str(paper.get("arxiv_id") or "").strip()
    if not str(paper.get("year") or "").strip() and len(arxiv_id) >= 4:
        prefix = arxiv_id[:4]
        if prefix.isdigit():
            assignments.append("year=?")
            parameters.append(prefix)
    if not str(paper.get("venue") or "").strip() and arxiv_id:
        assignments.append("venue=?")
        parameters.append("arXiv")
    authors = []
    raw_authors = paper.get("authors")
    if raw_authors:
        try:
            decoded = json.loads(raw_authors) if isinstance(raw_authors, str) else raw_authors
        except (TypeError, ValueError):
            decoded = []
        if isinstance(decoded, list):
            authors = [str(item).strip() for item in decoded if str(item).strip()]
    return assignments, parameters, authors


def _write_result(payload):
    sys.stdout.write(json.dumps(payload, ensure_ascii=False))
    sys.stdout.flush()
    return payload


def run(limit: int = 0) -> dict:
    """Enrich Papers missing year, venue, or a row in ``paper_authors``."""
    connection = db.connect()
    try:
        _ensure_authors_table(connection)
        paper_columns = {
            str(row[1])
            for row in connection.execute("PRAGMA table_info(papers)").fetchall()
        }
        # Older seed/fixture databases predate the denormalized authors column;
        # author enrichment is already persisted in paper_authors, so the
        # absence of that legacy column must not make the whole command fail.
        authors_expression = "p.authors" if "authors" in paper_columns else "NULL"
        rows = connection.execute(
            f"""SELECT p.id, p.title, p.arxiv_id, p.doi, p.year, p.venue, {authors_expression} AS authors,
                      CASE WHEN pa.paper_id IS NULL THEN 1 ELSE 0 END AS needs_authors
               FROM papers p
               LEFT JOIN paper_authors pa ON pa.paper_id = p.id
               WHERE p.year IS NULL OR TRIM(p.year) = ''
                  OR p.venue IS NULL OR TRIM(p.venue) = ''
                  OR pa.paper_id IS NULL
               ORDER BY p.id"""
        ).fetchall()
        if limit and limit > 0:
            rows = rows[:limit]

        total = len(rows)
        done = 0
        failed = []
        skipped = []
        _p(f"BATCH::total::{total}")
        for index, row in enumerate(rows, 1):
            paper = dict(row)
            paper_id = paper["id"]
            _p(f"ITEM::{index}::{total}::start::{paper_id}::{paper['title'][:60]}")
            try:
                payload = _fetch_metadata(paper)
                offline = not payload
                if offline:
                    assignments, parameters, authors = _offline_metadata(paper)
                    if not assignments and not authors:
                        skipped.append(paper_id)
                        _p(f"ITEM::{index}::{total}::skip::{paper_id}::未找到元数据")
                        continue
                    payload = {}

                assignments = []
                parameters = []
                if offline:
                    assignments, parameters, authors = _offline_metadata(paper)
                elif not str(paper.get("year") or "").strip() and payload.get("year"):
                    assignments.append("year=?")
                    parameters.append(str(payload["year"]))
                if not offline and not str(paper.get("venue") or "").strip() and payload.get("venue"):
                    assignments.append("venue=?")
                    parameters.append(db.norm_venue(payload["venue"]))

                if not offline:
                    authors = _author_names(payload) if paper["needs_authors"] else []
                if not assignments and not authors:
                    skipped.append(paper_id)
                    _p(
                        f"ITEM::{index}::{total}::skip::{paper_id}::无可写元数据"
                    )
                    continue
                with connection:
                    if assignments:
                        connection.execute(
                            f"UPDATE papers SET {', '.join(assignments)} WHERE id=?",
                            (*parameters, paper_id),
                        )
                    if authors:
                        connection.execute(
                            """INSERT INTO paper_authors(paper_id, authors, updated_at)
                               VALUES(?, ?, datetime('now'))
                               ON CONFLICT(paper_id) DO UPDATE SET
                                   authors=excluded.authors,
                                   updated_at=datetime('now')""",
                            (paper_id, json.dumps(authors, ensure_ascii=False)),
                        )
                done += 1
                suffix = "（本地身份信息）" if offline else ""
                _p(f"ITEM::{index}::{total}::done::{paper_id}{suffix}")
            except Exception as error:
                failed.append(paper_id)
                _p(f"ITEM::{index}::{total}::fail::{paper_id}::{str(error)[:120]}")

        _p(
            f"BATCH::finish::done={done}::fail={len(failed)}::skip={len(skipped)}"
        )
        result = {
            "ok": not bool(failed),
            "total": total,
            "done": done,
            "failed": failed,
            "skipped": skipped,
        }
        if failed:
            result["error"] = "部分元数据补全失败，请检查 Semantic Scholar 网络或密钥"
        return _write_result(result)
    finally:
        connection.close()


__all__ = ["run"]
