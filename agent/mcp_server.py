"""Paper-Study MCP 服务：把本地论文库(SQLite)以 MCP 工具暴露给 Claude 等客户端，
用于对话式检索、读讲解/属性、做研究综述与找研究空白。

运行（stdio）：
    cd study-app && .venv/Scripts/python.exe -m agent.mcp_server          # 开发自测（需在 study-app 目录）
    .venv/Scripts/python.exe F:/.../study-app/agent/mcp_server.py         # 绝对文件路径启动（与工作目录无关，注册用这个）

注册到 Claude Code：
    claude mcp add paper-study -- <venv-python 绝对路径> <agent/mcp_server.py 绝对路径>
（或写进 claude_desktop_config.json，见 README「MCP 服务」）

设计：所有工具只读库，复用 agent.db / agent.embed；语义检索调用 embed.rank。
注意：MCP stdio 用 stdout 传协议，故模型加载等任何输出都重定向到 stderr，避免污染。
"""
import os
import sys
import json
import contextlib
from datetime import date, datetime
from pathlib import Path

# 让 `python -m agent.mcp_server` 与直接运行都能找到包
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from agent import config, db, embed
else:
    from . import config, db, embed

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("paper-study")

MAX_RESULT_LIMIT = 50
DEFAULT_TEXT_CHARS = 12000
MAX_TEXT_CHARS = 20000
REVIEW_TOTAL_STEPS = 7


# ---------- 小工具 ----------
def _jload(s):
    """把 JSON 数组字符串安全解析成 list（authors/models/datasets/tags/s2_fields）。"""
    if not s:
        return []
    try:
        v = json.loads(s)
        return v if isinstance(v, list) else [v]
    except Exception:
        return [x.strip() for x in str(s).split(",") if x.strip()]


def _has_pdf(row) -> bool:
    """pdf_path(绝对/相对 ROOT) → data/pdfs/<id>.pdf → 种子 ../paper/<id>.pdf 任一存在即真。"""
    sp = row["pdf_path"] if "pdf_path" in row.keys() else None
    if sp:
        p = Path(sp)
        if not p.is_absolute():
            p = config.ROOT / sp
        if p.exists():
            return True
    pid = row["id"]
    if (config.PDF_DIR / f"{pid}.pdf").exists():
        return True
    if (config.ROOT.parent / "paper" / f"{pid}.pdf").exists():
        return True
    return False


def _one(con, sql, args=()):
    try:
        r = con.execute(sql, args).fetchone()
        return r[0] if r else None
    except Exception:
        return None


def _connect_readonly():
    return db.connect_readonly()


def _ok(**data):
    return {"ok": True, **data}


def _err(message, **data):
    return {"ok": False, "error": message, **data}


def _clamp_int(value, default, low, high):
    try:
        n = int(value)
    except (TypeError, ValueError):
        n = default
    return max(low, min(n, high))


def _date_only(value=""):
    if not value:
        return date.today().isoformat()
    text = str(value).strip()
    try:
        if len(text) >= 10 and text[4] == "-" and text[7] == "-":
            return date.fromisoformat(text[:10]).isoformat()
        return datetime.fromisoformat(text).date().isoformat()
    except ValueError:
        return date.today().isoformat()


def _chunk_text(id, content, *, offset=0, max_chars=DEFAULT_TEXT_CHARS):
    text = content or ""
    start = _clamp_int(offset, 0, 0, len(text))
    size = _clamp_int(max_chars, DEFAULT_TEXT_CHARS, 1, MAX_TEXT_CHARS)
    end = min(len(text), start + size)
    next_offset = end if end < len(text) else None
    return _ok(
        id=id,
        content=text[start:end],
        offset=start,
        next_offset=next_offset,
        total_chars=len(text),
        truncated=next_offset is not None,
    )


def _review_state(next_due_at, completed_at, today):
    if completed_at:
        return "completed"
    if next_due_at < today:
        return "overdue"
    if next_due_at == today:
        return "dueToday"
    return "upcoming"


def _compact(row) -> dict:
    """列表结果用的精简字段。"""
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
        "has_explainer": bool((row["explainer"] or "").strip()),
        "has_pdf": _has_pdf(row),
    }


_SORT = {
    "relevance": "relevance DESC",
    "year": "year DESC",
    "citations": "citations DESC",
    "recent": "created_at DESC",
}


# ---------- 检索 ----------
@mcp.tool()
def search_papers(query: str = "", type: str = "", topic: str = "", venue: str = "",
                  year_from: int = 0, year_to: int = 0, min_relevance: float = 0.0,
                  has_explainer: bool = False, only_favorites: bool = False,
                  sort: str = "relevance", limit: int = 20) -> dict:
    """按关键词和结构化属性只读检索论文库。参数：query 模糊匹配题录与抽取字段；type 和 topic 使用 list_categories 返回的实际值；venue 为会议或期刊；year_from/year_to 为含端点年份；min_relevance 为最低相关度；has_explainer 和 only_favorites 为布尔过滤；sort 默认 relevance 且仅支持 relevance|year|citations|recent；limit 默认 20 并限制为 1-50。返回 ok: true、count 和紧凑 results；无结果仍为 ok: true。紧凑结果仅用于发现，论文级主张前调用 get_paper。"""
    con = _connect_readonly()
    where, args = [], []
    if query.strip():
        like = f"%{query.strip()}%"
        where.append("(title LIKE ? OR title_zh LIKE ? OR abstract LIKE ? OR tldr LIKE ? OR contribution LIKE ? "
                     "OR topic LIKE ? OR task LIKE ? OR tags LIKE ?)")
        args += [like] * 8
    if type.strip():
        where.append("type = ?"); args.append(type.strip())
    if topic.strip():
        where.append("topic LIKE ?"); args.append(f"%{topic.strip()}%")
    if venue.strip():
        where.append("venue LIKE ?"); args.append(f"%{venue.strip()}%")
    if year_from:
        where.append("CAST(year AS INTEGER) >= ?"); args.append(int(year_from))
    if year_to:
        where.append("CAST(year AS INTEGER) <= ?"); args.append(int(year_to))
    if min_relevance:
        where.append("relevance >= ?"); args.append(float(min_relevance))
    if has_explainer:
        where.append("explainer IS NOT NULL AND TRIM(explainer) != ''")
    if only_favorites:
        where.append("id IN (SELECT paper_id FROM favorites)")
    sql = "SELECT * FROM papers"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY " + _SORT.get(sort, _SORT["relevance"])
    sql += " LIMIT ?"; args.append(_clamp_int(limit, 20, 1, MAX_RESULT_LIMIT))
    rows = con.execute(sql, args).fetchall()
    con.close()
    return _ok(count=len(rows), results=[_compact(r) for r in rows])


@mcp.tool()
def semantic_search(query: str, k: int = 15) -> dict:
    """按自然语言含义只读检索。参数：必填 query；k 默认 15 并限制为 1-50。返回 ok: true、count、indexed、total 和带余弦相似度 score 的紧凑 results；索引未覆盖全部论文时附 note。"""
    capped_k = _clamp_int(k, 15, 1, MAX_RESULT_LIMIT)
    with contextlib.redirect_stdout(sys.stderr):     # 防本地嵌入模型(下载/进度)污染 stdio 协议
        ranked = embed.rank(query, capped_k, reindex_stale=False)   # 只读：服务进程不做同步重嵌(交给网页端自愈)
    con = _connect_readonly()
    n_vec = _one(con, "SELECT COUNT(*) FROM paper_vectors") or 0
    n_pap = _one(con, "SELECT COUNT(*) FROM papers") or 0
    out = []
    for it in ranked:
        row = con.execute("SELECT * FROM papers WHERE id=?", (it["id"],)).fetchone()
        if row:
            d = _compact(row); d["score"] = it["score"]; out.append(d)
    con.close()
    res = _ok(count=len(out), indexed=n_vec, total=n_pap, results=out)
    if n_vec < n_pap:
        res["note"] = (f"语义索引覆盖 {n_vec}/{n_pap} 篇；未入索引的论文不会出现在语义结果里"
                       "（可在网页端做一次语义检索或重建索引以补全）。")
    return res


@mcp.tool()
def related_papers(id: str, k: int = 8) -> dict:
    """按标题和摘要向量查找库内相关论文。参数：必填稳定论文 id；k 默认 8 并限制为 1-50。返回 ok: true、seed、count 和带关系 score 的紧凑 results；种子不存在时返回 ok: false、error 和 id。"""
    capped_k = _clamp_int(k, 8, 1, MAX_RESULT_LIMIT)
    con = _connect_readonly()
    row = con.execute("SELECT id,title,tldr,abstract FROM papers WHERE id=?", (id,)).fetchone()
    if not row:
        con.close()
        return _err(f"未找到论文 id={id}", id=id)
    seed_text = (row["title"] or "") + ". " + (row["tldr"] or row["abstract"] or "")
    con.close()
    with contextlib.redirect_stdout(sys.stderr):
        ranked = embed.rank(seed_text, capped_k, exclude=id, reindex_stale=False)   # 只读，理由同 semantic_search
    con = _connect_readonly()
    out = []
    for it in ranked:
        r = con.execute("SELECT * FROM papers WHERE id=?", (it["id"],)).fetchone()
        if r:
            d = _compact(r); d["score"] = it["score"]; out.append(d)
    con.close()
    return _ok(seed=id, count=len(out), results=out)


# ---------- 单篇详情 ----------
@mcp.tool()
def get_paper(id: str) -> dict:
    """按稳定 id 读取完整论文元数据与学习状态。返回 ok: true、题录、摘要、TLDR、分类、任务、模型、数据集、贡献、标签、相关度、笔记、进度、收藏状态，以及 has_explainer、has_translation、has_pdf；论文不存在时返回 ok: false、error 和 id。讲解正文使用 get_explainer。"""
    con = _connect_readonly()
    row = con.execute("SELECT * FROM papers WHERE id=?", (id,)).fetchone()
    if not row:
        con.close()
        return _err(f"未找到论文 id={id}", id=id)
    note = _one(con, "SELECT content FROM notes WHERE paper_id=?", (id,))
    status = _one(con, "SELECT status FROM progress WHERE paper_id=?", (id,))
    fav = _one(con, "SELECT 1 FROM favorites WHERE paper_id=?", (id,))
    has_tr = _one(con, "SELECT 1 FROM translations WHERE paper_id=? AND TRIM(content)!=''", (id,))
    con.close()
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
        # AI 抽取的研究属性
        type=row["type"],
        topic=row["topic"],
        task=row["task"],
        models=_jload(row["models"]),
        datasets=_jload(row["datasets"]),
        contribution=row["contribution"],
        tags=_jload(row["tags"]),
        fields=_jload(row["s2_fields"]),
        relevance=row["relevance"],
        # 状态
        note=note or "",
        progress=status or "未开始",
        favorite=bool(fav),
        has_explainer=bool((row["explainer"] or "").strip()),
        has_translation=bool(has_tr),
        has_pdf=_has_pdf(row),
    )


@mcp.tool()
def get_explainer(id: str, offset: int = 0, max_chars: int = DEFAULT_TEXT_CHARS) -> dict:
    """分页读取论文讲解 Markdown。参数：必填 id；offset 默认 0；max_chars 默认 12000 并限制为 1-20000。成功返回 ok: true、content、offset、next_offset、total_chars、truncated；truncated 为 true 时把返回的 next_offset 原样用于下一次调用。论文或讲解不存在时返回 ok: false、error 和 id。"""
    con = _connect_readonly()
    md = _one(con, "SELECT explainer FROM papers WHERE id=?", (id,))
    exists = con.execute("SELECT 1 FROM papers WHERE id=?", (id,)).fetchone()
    con.close()
    if not exists:
        return _err(f"未找到论文 id={id}", id=id)
    if not (md or "").strip():
        return _err(f"该论文暂无讲解（可在网页阅读页点「✨ 生成讲解」生成）。id={id}", id=id)
    return _chunk_text(id, md, offset=offset, max_chars=max_chars)


@mcp.tool()
def get_translation(id: str, offset: int = 0, max_chars: int = DEFAULT_TEXT_CHARS) -> dict:
    """分页读取论文中文全文翻译 Markdown。参数：必填 id；offset 默认 0；max_chars 默认 12000 并限制为 1-20000。成功返回 ok: true、content、offset、next_offset、total_chars、truncated；truncated 为 true 时把返回的 next_offset 原样用于下一次调用。翻译不存在时返回 ok: false、error 和 id。"""
    con = _connect_readonly()
    md = _one(con, "SELECT content FROM translations WHERE paper_id=?", (id,))
    con.close()
    if not (md or "").strip():
        return _err(f"该论文暂无中文翻译（可在网页阅读页生成）。id={id}", id=id)
    return _chunk_text(id, md, offset=offset, max_chars=max_chars)


@mcp.tool()
def list_due_reviews(today: str = "", include_upcoming: bool = False, limit: int = 20) -> dict:
    """只读列出艾宾浩斯复习队列。参数：today 默认当前日期；include_upcoming 默认 false；limit 默认 20 并限制为 1-50。默认只返回 today 当日及以前到期且未完成的条目；成功返回 ok: true、today、count、include_upcoming 和 results。"""
    today_s = _date_only(today)
    capped_limit = _clamp_int(limit, 20, 1, MAX_RESULT_LIMIT)
    where = ["r.completed_at IS NULL"]
    args = []
    if not include_upcoming:
        where.append("r.next_due_at <= ?")
        args.append(today_s)
    sql = f"""
        SELECT
          r.paper_id,
          r.started_at,
          r.current_step,
          r.completed_steps,
          r.next_due_at,
          r.completed_at,
          r.updated_at,
          p.title,
          p.title_zh,
          p.venue,
          p.year,
          COALESCE(NULLIF(TRIM(progress.status), ''), '未开始') AS progress
        FROM paper_reviews r
        JOIN papers p ON p.id = r.paper_id
        LEFT JOIN progress ON progress.paper_id = r.paper_id
        WHERE {' AND '.join(where)}
        ORDER BY r.next_due_at ASC, p.title COLLATE NOCASE ASC, r.paper_id ASC
        LIMIT ?
    """
    args.append(capped_limit)
    con = _connect_readonly()
    rows = con.execute(sql, args).fetchall()
    con.close()
    results = []
    for row in rows:
        state = _review_state(row["next_due_at"], row["completed_at"], today_s)
        results.append({
            "id": row["paper_id"],
            "title": row["title"],
            "title_zh": row["title_zh"],
            "venue": row["venue"],
            "year": row["year"],
            "progress": row["progress"],
            "review_state": state,
            "started_at": row["started_at"],
            "current_step": row["current_step"],
            "completed_steps": row["completed_steps"],
            "total_steps": REVIEW_TOTAL_STEPS,
            "next_due_at": row["next_due_at"],
            "updated_at": row["updated_at"],
        })
    return _ok(today=today_s, count=len(results), include_upcoming=bool(include_upcoming), results=results)


# ---------- 综览 / 找空白 ----------
@mcp.tool()
def list_categories() -> dict:
    """只读列出库中实际使用的分类词表与计数，无参数。返回 ok: true、types、topics、tasks；使用返回的 type/topic 值构造 search_papers 过滤，tasks 仅用于理解任务版图。"""
    con = _connect_readonly()

    def counts(col):
        rows = con.execute(
            f"SELECT {col} AS k, COUNT(*) AS c FROM papers "
            f"WHERE {col} IS NOT NULL AND TRIM({col})!='' GROUP BY {col} "
            f"ORDER BY c DESC, k").fetchall()
        return [{"name": r["k"], "count": r["c"]} for r in rows]

    res = _ok(types=counts("type"), topics=counts("topic"), tasks=counts("task"))
    con.close()
    return res


@mcp.tool()
def library_overview() -> dict:
    """只读返回论文库整体画像，无参数。返回 ok: true、total、with_explainer、with_translation、favorites、indexed_vectors、review_due、review_open，以及方向、主题、会议、年份、相关度和引用统计。用于覆盖分析，不替代论文级证据。"""
    con = _connect_readonly()
    total = _one(con, "SELECT COUNT(*) FROM papers") or 0
    with_exp = _one(con, "SELECT COUNT(*) FROM papers WHERE explainer IS NOT NULL AND TRIM(explainer)!=''") or 0
    with_tr = _one(con, "SELECT COUNT(*) FROM translations WHERE TRIM(content)!=''") or 0
    favs = _one(con, "SELECT COUNT(*) FROM favorites") or 0
    indexed = _one(con, "SELECT COUNT(*) FROM paper_vectors") or 0
    review_due = _one(con, "SELECT COUNT(*) FROM paper_reviews WHERE completed_at IS NULL AND next_due_at <= date('now')") or 0
    review_open = _one(con, "SELECT COUNT(*) FROM paper_reviews WHERE completed_at IS NULL") or 0

    def grp(col, limit=0):
        sql = (f"SELECT {col} AS k, COUNT(*) AS c FROM papers "
               f"WHERE {col} IS NOT NULL AND TRIM({col})!='' GROUP BY {col} ORDER BY c DESC, k")
        if limit:
            sql += f" LIMIT {limit}"
        return [{"name": r["k"], "count": r["c"]} for r in con.execute(sql).fetchall()]

    years = [{"year": r["k"], "count": r["c"]} for r in con.execute(
        "SELECT year AS k, COUNT(*) AS c FROM papers WHERE year IS NOT NULL AND TRIM(year)!='' "
        "GROUP BY year ORDER BY k").fetchall()]
    rel_hi = _one(con, "SELECT COUNT(*) FROM papers WHERE relevance >= 0.8") or 0
    rel_mid = _one(con, "SELECT COUNT(*) FROM papers WHERE relevance >= 0.6 AND relevance < 0.8") or 0
    rel_lo = _one(con, "SELECT COUNT(*) FROM papers WHERE relevance < 0.6 AND relevance IS NOT NULL") or 0
    avg_cit = _one(con, "SELECT ROUND(AVG(citations),1) FROM papers WHERE citations IS NOT NULL")
    by_type, by_topic, by_venue = grp("type"), grp("topic", 15), grp("venue", 15)
    con.close()
    return _ok(
        total=total,
        with_explainer=with_exp,
        with_translation=with_tr,
        favorites=favs,
        indexed_vectors=indexed,
        review_due=review_due,
        review_open=review_open,
        by_type=by_type,
        by_topic_top15=by_topic,
        by_venue_top15=by_venue,
        by_year=years,
        relevance_buckets={">=0.8": rel_hi, "0.6-0.8": rel_mid, "<0.6": rel_lo},
        avg_citations=avg_cit,
    )


def _prewarm():
    """启动阶段(单线程、事件循环未起)先把含 C 扩展/较重的模块导入好。
    否则首次 semantic_search/related_papers 会在 asyncio 事件循环线程上懒加载 numpy 的
    C 扩展，在 Windows 上与运行中的事件循环死锁而整个服务挂起(已用 faulthandler 定位)。"""
    import numpy  # noqa: F401  embed 已模块级导入 numpy，这里再确保一次
    try:
        import httpx  # noqa: F401  外部嵌入 API(_api_embed)用
    except Exception:
        pass
    if config.EMBED_PROVIDER != "api":
        try:
            embed.model()          # 本地嵌入模型：启动时加载好(首次可能下载)，免得首查时在事件循环线程加载
        except Exception:
            pass


def main():
    _prewarm()
    mcp.run()      # 默认 stdio


if __name__ == "__main__":
    main()
