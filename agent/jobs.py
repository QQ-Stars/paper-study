"""后台采集任务执行器。

  python -m agent run-job --id <jobId>

读 ingest_jobs 一行 → 跑 pipeline.search（多样化扩展检索词）→ 把库中没有的「新」候选
暂存进 job_candidates(status=pending) → 任务置 review（待用户确认入库）。进度照旧打到
stderr（STAGE/SRC/DOING/KEPT…），由 Node 收进 ingest_jobs.log。"""
import json
import sys
from . import db, pipeline


def _p(msg):
    print(msg, file=sys.stderr, flush=True)


def run_job(job_id: int):
    con = db.connect()
    row = con.execute("SELECT * FROM ingest_jobs WHERE id=?", (job_id,)).fetchone()
    if not row:
        _p(f"JOBERR::任务 {job_id} 不存在"); con.close(); return
    con.execute("UPDATE ingest_jobs SET status='running' WHERE id=?", (job_id,))
    con.commit()

    direction = row["query"] or ""
    sources = [s.strip() for s in (row["venues"] or "").split(",") if s.strip()] or ["semanticscholar"]
    yf, yt = row["year_from"], row["year_to"]
    years = (yf, yt) if yf and yt else None
    limit = row["max_papers"] or 12
    min_rel = row["min_relevance"] if row["min_relevance"] is not None else 0.5
    only_a = bool(row["only_a"])

    try:
        cands = pipeline.search(direction, sources, years, limit, min_rel,
                                expand=True, expand_n=12, only_a=only_a)
    except Exception as e:
        _p(f"JOBERR::{e}")
        con.execute("UPDATE ingest_jobs SET status='failed', finished_at=datetime('now') WHERE id=?", (job_id,))
        con.commit(); con.close(); return

    # 只暂存「新」候选（库里没有的），按标题归一去重
    seen, new_cands = set(), []
    for c in cands:
        if c.get("in_library"):
            continue
        tn = db.title_norm(c.get("title", ""))
        if not tn or tn in seen:
            continue
        seen.add(tn); new_cands.append((tn, c))

    for tn, c in new_cands:
        con.execute("INSERT INTO job_candidates(job_id, title_norm, data, status) VALUES(?,?,?,'pending')",
                    (job_id, tn, json.dumps(c, ensure_ascii=False)))
    status = "review" if new_cands else "done"
    con.execute("UPDATE ingest_jobs SET found=?, skipped=?, status=?, finished_at=datetime('now') WHERE id=?",
                (len(cands), len(cands) - len(new_cands), status, job_id))
    con.commit(); con.close()
    _p(f"JOBDONE::{len(new_cands)}::{len(cands)}")    # 新候选数 :: 总命中数
