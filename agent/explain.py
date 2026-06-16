"""为已入库论文生成「讲解」markdown（LLM）。
进度 → stderr（STAGE::/PDFERR::/DONE:: 等），最终 markdown → stdout，并写入 papers.explainer。"""
import sys
import json
from pathlib import Path
from . import db, llm, config, extract


def _p(msg):
    print(msg, file=sys.stderr, flush=True)


def _find_pdf(r: dict):
    """按 id 找本地 PDF：DB.pdf_path → data/pdfs/<id>.pdf → 种子 ../paper/<id>.pdf。"""
    sp = r.get("pdf_path")
    if sp:
        p = Path(sp)
        if not p.is_absolute():
            p = config.ROOT / sp
        if p.exists():
            return p
    cand = config.PDF_DIR / f"{r.get('id')}.pdf"
    if cand.exists():
        return cand
    seed = config.ROOT.parent / "paper" / f"{r.get('id')}.pdf"
    if seed.exists():
        return seed
    return None


def _explain_core(con, r: dict, deep: bool) -> str:
    """单篇讲解核心逻辑（单篇 / 批量共用，确保两边逻辑完全一致）：
    装载作者串 →（deep 时）读取本地 PDF 全文并去参考文献 → 大模型撰写 → set_explainer 落库。
    进度 → stderr；返回 markdown。不写 stdout、不 SystemExit；模型空返回时返回 ""。"""
    # authors 在 DB 里是 JSON 数组字符串，转成可读串
    authors = r.get("authors")
    try:
        a = json.loads(authors) if authors else []
        r["authors_str"] = ", ".join(a) if isinstance(a, list) else str(authors or "")
    except Exception:
        r["authors_str"] = str(authors or "")

    fulltext = None
    if deep:
        pdf = _find_pdf(r)
        if pdf:
            try:
                pages = extract.page_count(pdf)
            except Exception:
                pages = "?"
            _p(f"STAGE::pdf::读取 PDF 全文（共 {pages} 页）…")
            try:
                fulltext = extract.full_text(pdf, r.get("abstract"), config.EXPLAIN_MAX_CHARS)
                fulltext, cut = extract.strip_references(fulltext)
                _p(f"PDFOK::已读取全文 {len(fulltext)} 字" + ("（已跳过参考文献）" if cut else ""))
            except Exception as e:
                _p(f"PDFERR::{e}（改用摘要生成）")
        else:
            _p("PDFMISS::未找到本地PDF，改用摘要 / TLDR 生成")

    _p("STAGE::generate::调用大模型撰写讲解…")
    md = llm.generate_explainer(r, fulltext)
    if not (md or "").strip():
        return ""
    db.set_explainer(con, r["id"], md)
    return md


def explain_paper(pid: str, deep: bool = False) -> str:
    con = db.connect()
    row = con.execute("SELECT * FROM papers WHERE id=?", (pid,)).fetchone()
    if not row:
        con.close()
        _p(f"ERR::论文不存在: {pid}")
        raise SystemExit(2)
    r = dict(row)
    _p(f"STAGE::load::{(r.get('title') or '')[:48]}")
    md = _explain_core(con, r, deep)
    con.close()
    if not md:
        _p("ERR::模型返回为空")
        raise SystemExit(3)
    _p(f"DONE::{len(md)}")
    sys.stdout.write(md)
    sys.stdout.flush()
    return md


def explain_batch(limit: int = 0, deep: bool = True) -> dict:
    """批量为「还没有讲解」的论文生成讲解。默认 deep=True：通读本地 PDF 全文，与单篇「读PDF全文」逻辑一致。
    为满足「必须通读全文」，只处理有本地 PDF 的论文；无本地 PDF 的跳过并计入 skipped_no_pdf。
    每篇成功即 set_explainer 独立提交落库 → 可随时中断、重复点击续跑（只补仍缺讲解的）。
    进度 → stderr：BATCH::total::N::skip::M / ITEM::i::N::(start|done|fail)::id::info / 及核心 STAGE:: 行；
    汇总 JSON → stdout。"""
    con = db.connect()
    rows = con.execute(
        "SELECT * FROM papers WHERE explainer IS NULL OR TRIM(explainer)='' "
        "ORDER BY datetime(created_at) DESC").fetchall()
    targets, skipped = [], []
    for row in rows:
        r = dict(row)
        if deep and not _find_pdf(r):
            skipped.append(r["id"])              # 无本地 PDF → 无法通读全文 → 跳过
        else:
            targets.append(r)
    if limit and limit > 0:
        targets = targets[:limit]
    total = len(targets)
    _p(f"BATCH::total::{total}::skip::{len(skipped)}")
    done, failed = 0, []
    for i, r in enumerate(targets, 1):
        title = (r.get("title") or "")[:60]
        _p(f"ITEM::{i}::{total}::start::{r['id']}::{title}")
        try:
            md = _explain_core(con, r, deep)
            if md:
                done += 1
                _p(f"ITEM::{i}::{total}::done::{r['id']}::{len(md)}")
            else:
                failed.append(r["id"])
                _p(f"ITEM::{i}::{total}::fail::{r['id']}::模型返回为空")
        except Exception as e:
            failed.append(r["id"])
            _p(f"ITEM::{i}::{total}::fail::{r['id']}::{str(e)[:120]}")
    con.close()
    _p(f"BATCH::finish::done={done}::fail={len(failed)}::skip={len(skipped)}")
    out = {"ok": True, "total": total, "done": done, "failed": failed, "skipped_no_pdf": skipped}
    sys.stdout.write(json.dumps(out, ensure_ascii=False))
    sys.stdout.flush()
    return out
