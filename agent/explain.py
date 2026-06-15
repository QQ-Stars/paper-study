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


def explain_paper(pid: str, deep: bool = False) -> str:
    con = db.connect()
    row = con.execute("SELECT * FROM papers WHERE id=?", (pid,)).fetchone()
    if not row:
        con.close()
        _p(f"ERR::论文不存在: {pid}")
        raise SystemExit(2)
    r = dict(row)
    _p(f"STAGE::load::{(r.get('title') or '')[:48]}")

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
    if not md:
        con.close()
        _p("ERR::模型返回为空")
        raise SystemExit(3)
    db.set_explainer(con, pid, md)
    con.close()
    _p(f"DONE::{len(md)}")
    sys.stdout.write(md)
    sys.stdout.flush()
    return md
