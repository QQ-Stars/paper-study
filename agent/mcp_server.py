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

# 研究方向(type)受控词表，给模型当导航参考
TYPES = "检测 | 缓解·解码 | 缓解·训练 | 机制 | 评测 | 定义 | 其他"
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
    """按关键词 + 结构化属性检索论文库。用于精确过滤（指定会议/年份/方向/相关度）。
    - query: 关键词，模糊匹配 标题/摘要/TLDR/贡献/子主题/任务/标签（中英皆可）。留空=纯按属性浏览。
    - type: 研究方向，取值之一：{TYPES}。
    - topic: 子主题（用 list_categories 看库里有哪些）。
    - venue: 会议/期刊简称，如 CVPR / NeurIPS / ACL / arXiv。
    - year_from/year_to: 年份区间（含端点），0=不限。
    - min_relevance: 与研究方向的相关度下限 0~1。
    - has_explainer: 仅返回已生成讲解的论文。only_favorites: 仅收藏。
    - sort: relevance|year|citations|recent。limit: 最多返回数。
    返回 {count, results:[精简字段]}。要读全文属性用 get_paper，读讲解用 get_explainer。
    """.replace("{TYPES}", TYPES)
    con = _connect_readonly()
    where, args = [], []
    if query.strip():
        like = f"%{query.strip()}%"
        where.append("(title LIKE ? OR abstract LIKE ? OR tldr LIKE ? OR contribution LIKE ? "
                     "OR topic LIKE ? OR task LIKE ? OR tags LIKE ?)")
        args += [like] * 7
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
    """语义检索：用自然语言描述（中/英），按含义相似度找论文（比关键词更适合“关于X的工作”这类问法）。
    返回 {count, results:[精简字段 + score(余弦相似度)]}，按相似度降序。"""
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
    """找库内与某篇论文语义相近的论文（基于标题+摘要向量），用于聚类、顺藤摸瓜、发现同质工作。
    返回 {seed, count, results:[精简字段 + score]}。"""
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
    """取一篇论文的全部属性：题录(作者/会议/年份/DOI/arXiv)、AI 抽取的分类(方向/子主题/任务/模型/数据集/贡献/标签/相关度)、
    引用数、摘要、TLDR，以及笔记、学习进度、是否收藏、有无讲解/翻译/本地PDF。读讲解正文请用 get_explainer。"""
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
    """取某篇论文的「讲解」Markdown 全文（LLM 撰写的科学方法论精读：研究问题/方法/动机/实验等）。
    这是研究分析最有价值的内容。若该篇尚无讲解，返回提示。"""
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
    """取某篇论文的中文全文翻译 Markdown（若已生成）。"""
    con = _connect_readonly()
    md = _one(con, "SELECT content FROM translations WHERE paper_id=?", (id,))
    con.close()
    if not (md or "").strip():
        return _err(f"该论文暂无中文翻译（可在网页阅读页生成）。id={id}", id=id)
    return _chunk_text(id, md, offset=offset, max_chars=max_chars)


@mcp.tool()
def list_due_reviews(today: str = "", include_upcoming: bool = False, limit: int = 20) -> dict:
    """列出艾宾浩斯复习队列。默认只返回今天及以前应复习的论文；include_upcoming=true 时包含未完成的未来计划。"""
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
    """列出库中实际在用的分类词表及计数：研究方向(type)/子主题(topic)/任务(task)。
    用于了解库的版图、导航、以及发现“某方向论文很少”这类潜在空白。"""
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
    """库的整体画像：总数、讲解/翻译/收藏/本地PDF 覆盖、按方向/会议/年份分布、相关度分桶、年份范围。
    适合开题前快速了解“这个方向已有哪些、密集在哪、哪里稀疏(潜在空白)”。"""
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
