"""全文翻译：PDF→Markdown(pymupdf4llm) → 去参考文献/致谢 → 分块 → 并发调 LLM 译中文 → 存 translations 表。
进度→stderr(STAGE::/PDF*/STRIP::/TOTAL::N/CHUNK::done::N/DONE::)，最终中文 Markdown→stdout。"""
import sys
import re
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from . import db, llm, config, extract


def _p(msg):
    print(msg, file=sys.stderr, flush=True)


def _find_pdf(r: dict):
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


# 参考文献 / 致谢 等“文末”标题（整行就是这个词，允许前面有 #、数字、加粗标记）
_TAIL = re.compile(
    r'^[\s#>*_\-.0-9]*\**\s*'
    r'(references?|bibliography|参\s*考\s*文\s*献|acknowledge?ments?|致\s*谢)'
    r'\s*\**\s*:?\s*$', re.I)


def _strip_tail(md: str):
    """裁掉参考文献/致谢及其后内容（仅当它出现在全文后半段时，避免误伤正文里的提及）。"""
    lines = md.split("\n")
    n = len(lines)
    for i, ln in enumerate(lines):
        if i > n * 0.45 and _TAIL.match(ln.strip()):
            return "\n".join(lines[:i]).rstrip(), True
    return md, False


def _chunk(text: str, size: int = 3800):
    """按段落(空行)聚合成不超过 size 的块，尽量不切断段落；异常超长段落兜底硬切。"""
    blocks = re.split(r'\n\s*\n', text)
    chunks, cur = [], ""
    for b in blocks:
        b = b.strip("\n")
        if not b.strip():
            continue
        if cur and len(cur) + len(b) + 2 > size:
            chunks.append(cur)
            cur = b
        else:
            cur = (cur + "\n\n" + b) if cur else b
        while len(cur) > size * 1.6:
            chunks.append(cur[:size])
            cur = cur[size:]
    if cur.strip():
        chunks.append(cur)
    return chunks


def translate_paper(pid: str, workers: int = 4) -> str:
    con = db.connect()
    row = con.execute("SELECT * FROM papers WHERE id=?", (pid,)).fetchone()
    if not row:
        con.close(); _p(f"ERR::论文不存在: {pid}"); raise SystemExit(2)
    r = dict(row)
    _p(f"STAGE::load::{(r.get('title') or '')[:48]}")

    pdf = _find_pdf(r)
    if pdf:
        _p(f"STAGE::pdf::读取 PDF 全文（共 {extract.page_count(pdf)} 页）…")
        body = extract.full_text(pdf, None, config.EXPLAIN_MAX_CHARS)
    else:
        _p("PDFMISS::未找到本地PDF，改用摘要翻译")
        body = ((r.get("title") or "") + "\n\n" + (r.get("abstract") or "")).strip()

    body, stripped = _strip_tail(body)
    if stripped:
        _p("STRIP::已跳过参考文献/致谢部分")

    chunks = _chunk(body)
    if not chunks:
        con.close(); _p("ERR::无可翻译内容"); raise SystemExit(3)
    _p(f"TOTAL::{len(chunks)}")
    _p("STAGE::translate::翻译中…")

    results = [None] * len(chunks)
    done = 0
    with ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
        futs = {ex.submit(llm.translate_md, chunks[i]): i for i in range(len(chunks))}
        for fut in as_completed(futs):
            i = futs[fut]
            try:
                results[i] = fut.result()
            except Exception as e:
                results[i] = f"> [本段翻译失败：{e}]"
            done += 1
            _p(f"CHUNK::{done}::{len(chunks)}")

    md = "\n\n".join(x for x in results if x).strip()
    if not md:
        con.close(); _p("ERR::翻译结果为空"); raise SystemExit(4)
    db.set_translation(con, pid, md)
    con.close()
    _p(f"DONE::{len(md)}")
    sys.stdout.write(md)
    sys.stdout.flush()
    return md
