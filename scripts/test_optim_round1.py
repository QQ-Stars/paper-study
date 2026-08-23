# -*- coding: utf-8 -*-
"""本轮优化的 agent/SQL 层直测：并发配置、批量筛选逻辑、cite-context SQL。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent import config, db  # noqa: E402

print(f"[1] OCR_BATCH_WORKERS = {config.OCR_BATCH_WORKERS}（预期 2，可经 ocrMaxConcurrency 调整）")
assert 1 <= config.OCR_BATCH_WORKERS <= 8

con = db.connect()

# [2] 批量筛选逻辑（不实际调 OCR API）
from agent.explain import _find_pdf  # noqa: E402

rows = con.execute("SELECT * FROM papers").fetchall()
targets, skipped, have = 0, 0, 0
for row in rows:
    r = dict(row)
    if not _find_pdf(r):
        skipped += 1
        continue
    cached = db.get_ocr_markdown(con, r["id"])
    if cached and cached.strip():
        have += 1
    else:
        targets += 1
print(f"[2] 批量筛选: 总 {len(rows)} · 待转换 {targets} · 已有 OCR {have} · 无 PDF {skipped}")

# [3] cite-context SQL（与后端端点同款查询）
hub = con.execute(
    "SELECT dst_id, COUNT(*) c FROM cite_edges GROUP BY dst_id ORDER BY c DESC LIMIT 1"
).fetchone()
if hub:
    pid, cnt = hub[0], hub[1]
    cites = con.execute(
        "SELECT p.title FROM cite_edges e JOIN papers p ON p.id = e.dst_id WHERE e.src_id = ?",
        (pid,),
    ).fetchall()
    cited_by = con.execute(
        "SELECT p.title FROM cite_edges e JOIN papers p ON p.id = e.src_id WHERE e.dst_id = ?",
        (pid,),
    ).fetchall()
    title = con.execute("SELECT title FROM papers WHERE id=?", (pid,)).fetchone()[0]
    print(f"[3] cite-context 被引最多论文: {title[:44]}…")
    print(f"    它引用库内 {len(cites)} 篇 · 被库内 {len(cited_by)} 篇引用（预期被引={cnt}）")
    assert len(cited_by) == cnt
else:
    print("[3] cite_edges 为空")

con.close()
print("\nagent 层直测通过 ✓")
