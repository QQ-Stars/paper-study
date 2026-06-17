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


def _is_delim(line: str) -> bool:
    """Markdown 表格的分隔行，如 |:---|:---|。"""
    s = line.strip()
    return bool(s) and set(s) <= set("|:- ") and "-" in s and "|" in s


def _strip_tables(md: str):
    """删除 Markdown 表格（表格翻译又乱又占地方，用户要求跳过），各替换为一行占位。"""
    lines = md.split("\n")
    out, i, n, removed = [], 0, len(lines), 0
    while i < n:
        if lines[i].count("|") >= 2 and i + 1 < n and _is_delim(lines[i + 1]):
            j = i + 2                                   # 标准表：表头 + 分隔 + 数据行
            while j < n and lines[j].count("|") >= 2:
                j += 1
        elif (lines[i].count("|") >= 3 and i + 2 < n
              and lines[i + 1].count("|") >= 3 and lines[i + 2].count("|") >= 3):
            j = i                                       # 无分隔行的“管道密集”块兜底
            while j < n and lines[j].count("|") >= 3:
                j += 1
        else:
            out.append(lines[i]); i += 1; continue
        out.append("> 📊 *（此处为表格，未翻译，详见原文）*")
        removed += 1
        i = j
    return "\n".join(out), removed


# 行首“图表标题”：Figure 3: / Fig. 2. / Table 1： / Algorithm 1. / 图 3: / 表 2.
# 要求编号后带 : 或 .，避免误伤正文里“Figure 3 shows…”这类引用句。
_CAP_RE = re.compile(
    r'^[\s>*_#]*\**\s*(figure|fig\.?|table|algorithm|图|表|算法)\s*\.?\s*\d+[a-z]?\s*[:.：]',
    re.I)

# 行首“噪声”整行：纯页码 / arXiv 行 / preprint·under review 横幅 / 独立 URL·DOI / 版权行
_NOISE_RE = re.compile(
    r'^[\s>*_#]*('
    r'\d{1,3}'
    r'|arxiv:\s*\d\S*.*'
    r'|preprint\.?(\s+under\s+review\.?)?'
    r'|under\s+review\.?|to\s+appear\b.*'
    r'|https?://\S+|doi:\s*\S+'
    r'|©.*|copyright\b.*'
    r')\s*$', re.I)


def _clean_body(md: str):
    """移除页眉页脚/页码/arXiv·preprint 等整行噪声；把“图表标题段”整段移出正文，集中到文末附录。
    返回 (清洗后正文, [图表标题段...], 移除噪声行数)。目的：正文读起来连贯，不被图表说明插断。"""
    lines = md.split("\n")
    body, caps = [], []
    i, n, n_noise = 0, len(lines), 0
    while i < n:
        ln = lines[i]
        if _CAP_RE.match(ln):                       # 图表标题：本行 + 紧随非空行算一整段（设上限防吞正文）
            blk = [ln.strip()]; i += 1
            while i < n and lines[i].strip() and len(blk) < 6 and sum(len(x) for x in blk) < 600:
                blk.append(lines[i].strip()); i += 1
            caps.append(" ".join(blk)); continue
        if _NOISE_RE.match(ln):
            n_noise += 1; i += 1; continue
        body.append(ln); i += 1
    return "\n".join(body), caps, n_noise


def _dehyphenate(md: str) -> str:
    """合并 PDF 抽取造成的词内换行连字符：represen-\\ntation → representation
    （只在小写字母间合并，尽量不误伤 state-of-the-art 这类复合词）。"""
    return re.sub(r'([a-z])-\n([a-z])', r'\1\2', md)


def _chunk(text: str, size: int = 3800):
    """按段落(空行)聚合成不超过 size 的块；优先在 Markdown 标题处断块（每块是一个连贯小节，译文更通顺）；
    尽量不切断段落，异常超长段落兜底硬切。"""
    blocks = re.split(r'\n\s*\n', text)
    chunks, cur = [], ""
    for b in blocks:
        b = b.strip("\n")
        if not b.strip():
            continue
        is_heading = b.lstrip().startswith("#")
        if cur and (len(cur) + len(b) + 2 > size or (is_heading and len(cur) > 600)):
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
        con.close()
        _p("PDFMISS::未找到本地PDF，无法进行全文翻译")
        raise SystemExit(5)

    body, stripped = extract.strip_references(body)
    if stripped:
        _p("STRIP::已跳过参考文献/致谢部分")
    body, caps, n_noise = _clean_body(body)
    if n_noise:
        _p(f"STRIP::已移除 {n_noise} 行页眉页脚/页码/arXiv 等噪声")
    body = _dehyphenate(body)
    body, n_tbl = _strip_tables(body)
    if n_tbl:
        _p(f"STRIP::已跳过 {n_tbl} 个表格（未翻译）")
    if caps:
        _p(f"STRIP::已把 {len(caps)} 处图表标题移到文末附录")
        body = body.rstrip() + "\n\n## 图表标题（原文图表说明）\n\n" + "\n".join(f"- {c}" for c in caps)

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
