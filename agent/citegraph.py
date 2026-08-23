"""引用关系图：批量抓取 Semantic Scholar 参考文献并原子重建库内引用边。

进度写入 stderr（TOTAL/PROG/DONE），统计结果写入 stdout。
"""

import json
import sys
import time

import httpx

from . import config, db, util


BATCH_URL = "https://api.semanticscholar.org/graph/v1/paper/batch"
BATCH_SIZE = 50
BATCH_FIELDS = "paperId,references.paperId,references.title,references.externalIds"
MAX_BATCH_ATTEMPTS = 4


def _p(msg):
    print(msg, file=sys.stderr, flush=True)


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


def _request_reference_batch(ids):
    for attempt in range(MAX_BATCH_ATTEMPTS):
        try:
            response = httpx.post(
                BATCH_URL,
                params={"fields": BATCH_FIELDS},
                json={"ids": ids},
                headers=_headers(),
                timeout=45,
                follow_redirects=True,
            )
        except httpx.RequestError:
            if attempt + 1 >= MAX_BATCH_ATTEMPTS:
                raise
            time.sleep(_retry_delay(attempt))
            continue
        if response.status_code == 429 or response.status_code >= 500:
            if attempt + 1 >= MAX_BATCH_ATTEMPTS:
                response.raise_for_status()
            time.sleep(_retry_delay(attempt, response))
            continue
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, list) or len(payload) != len(ids):
            raise ValueError("Semantic Scholar batch response length mismatch")
        return payload
    raise RuntimeError("Semantic Scholar rate limit persisted after retries")


def _write_result(payload):
    sys.stdout.write(json.dumps(payload, ensure_ascii=False))
    sys.stdout.flush()
    return payload


def build_edges():
    con = db.connect()
    try:
        db.ensure_edges_table(con)
        con.commit()
        papers = con.execute(
            "SELECT id, s2_id, arxiv_id, doi, title, title_norm FROM papers"
        ).fetchall()
        by_s2 = {paper["s2_id"]: paper["id"] for paper in papers if paper["s2_id"]}
        by_arxiv = {
            paper["arxiv_id"]: paper["id"] for paper in papers if paper["arxiv_id"]
        }
        by_doi = {
            (paper["doi"] or "").lower(): paper["id"]
            for paper in papers
            if paper["doi"]
        }
        by_title = {
            paper["title_norm"]: paper["id"] for paper in papers if paper["title_norm"]
        }

        def library_id(external_ids, s2_id, title):
            """把一条参考文献解析成库内论文 id（命中则返回，否则 None）。"""
            if s2_id and s2_id in by_s2:
                return by_s2[s2_id]
            arxiv_id = external_ids.get("ArXiv")
            if arxiv_id and arxiv_id in by_arxiv:
                return by_arxiv[arxiv_id]
            doi = (external_ids.get("DOI") or "").lower()
            if doi and doi in by_doi:
                return by_doi[doi]
            normalized_title = db.title_norm(title or "")
            if normalized_title and normalized_title in by_title:
                return by_title[normalized_title]
            return None

        targets = []
        for paper in papers:
            identifier = paper["s2_id"]
            if not identifier and paper["arxiv_id"]:
                identifier = f"ARXIV:{paper['arxiv_id']}"
            if not identifier and paper["doi"]:
                identifier = f"DOI:{paper['doi']}"
            if identifier:
                targets.append((paper, identifier))

        total = len(targets)
        node_count = len(papers)
        previous_edge_count = con.execute("SELECT COUNT(*) FROM cite_edges").fetchone()[0]
        _p(f"TOTAL::{total}")

        edges = set()
        resolved = 0
        failed = 0
        done = 0
        for offset in range(0, total, BATCH_SIZE):
            batch = targets[offset : offset + BATCH_SIZE]
            identifiers = [identifier for _, identifier in batch]
            try:
                results = _request_reference_batch(identifiers)
            except Exception as error:
                for paper, _ in batch:
                    _p(f"REFERR::{paper['id'][:28]}::{error}")
                _p("FAILED::引用数据获取失败，旧图谱已保留")
                return _write_result(
                    {
                        "ok": False,
                        "edges": previous_edge_count,
                        "nodes": node_count,
                        "error": f"引用数据获取失败，旧图谱已保留：{error}",
                    }
                )

            for (paper, _), result in zip(batch, results):
                if not isinstance(result, dict):
                    failed += 1
                    _p(f"REFERR::{paper['id'][:28]}::paper not found")
                else:
                    resolved += 1
                    references = result.get("references") or []
                    for reference in references:
                        if not isinstance(reference, dict):
                            continue
                        destination = library_id(
                            reference.get("externalIds") or {},
                            reference.get("paperId"),
                            reference.get("title"),
                        )
                        if destination and destination != paper["id"]:
                            edges.add((paper["id"], destination))
                done += 1
                _p(f"PROG::{done}::{total}")

        if total and resolved == 0:
            _p("FAILED::未获取到任何有效引用数据，旧图谱已保留")
            return _write_result(
                {
                    "ok": False,
                    "edges": previous_edge_count,
                    "nodes": node_count,
                    "error": "未获取到任何有效引用数据，旧图谱已保留",
                }
            )

        with con:
            con.execute("DELETE FROM cite_edges")
            con.executemany(
                "INSERT OR IGNORE INTO cite_edges(src_id, dst_id) VALUES(?, ?)",
                sorted(edges),
            )

        _p(f"DONE::{len(edges)}")
        return _write_result(
            {
                "ok": True,
                "edges": len(edges),
                "nodes": node_count,
                "processed": resolved,
                "failed": failed,
            }
        )
    finally:
        con.close()
