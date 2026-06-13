"""用大模型把库里五花八门的会议/期刊名规整成标准简称（万能：覆盖任意 venue，不靠写死的表）。

取库内全部不同 venue → LLM 给每个标准简称 → 落库（UPDATE）。
进度 → stderr，结果(含映射) → stdout。"""
import json
import sys
from . import db, llm


def _p(msg):
    print(msg, file=sys.stderr, flush=True)


def run():
    con = db.connect()
    rows = con.execute(
        "SELECT DISTINCT venue FROM papers "
        "WHERE venue IS NOT NULL AND TRIM(venue)!='' AND venue!='—'").fetchall()
    venues = [r["venue"] for r in rows]
    _p(f"TOTAL::{len(venues)}")
    if not venues:
        sys.stdout.write(json.dumps({"ok": True, "changed": 0, "mapping": {}}, ensure_ascii=False))
        sys.stdout.flush(); con.close(); return

    _p("STAGE::llm")
    try:
        mapping = llm.canonicalize_venues(venues)
    except Exception as e:
        _p(f"LLMERR::{e}")
        sys.stdout.write(json.dumps({"ok": False, "error": str(e), "changed": 0}, ensure_ascii=False))
        sys.stdout.flush(); con.close(); return

    _p("STAGE::apply")
    changed, applied = 0, {}
    for orig, abbr in mapping.items():
        abbr = (abbr or "").strip()
        if not abbr or abbr == orig:
            continue
        n = con.execute("UPDATE papers SET venue=? WHERE venue=?", (abbr, orig)).rowcount
        if n:
            changed += n
            applied[orig] = abbr
    con.commit()
    con.close()
    _p(f"DONE::{changed}")
    sys.stdout.write(json.dumps({"ok": True, "changed": changed, "mapping": applied}, ensure_ascii=False))
    sys.stdout.flush()
