"""PDF -> 文本（PyMuPDF）。
- first_pages: 快速取前几页纯文本，用于采集时的分类（够用即可，求快）。
- full_text:   写讲解时通读全文，优先用 pymupdf4llm 转 Markdown（保留标题 / 表格 / 双栏阅读顺序，
               对大模型最友好），失败再回退 PyMuPDF 纯文本，保证任何 PDF 都能拿到文本。
"""


def first_pages(path, n: int = 8, abstract: str = None) -> str:
    import fitz  # PyMuPDF
    doc = fitz.open(path)
    try:
        text = "\n".join(doc[i].get_text() for i in range(min(n, doc.page_count)))
    finally:
        doc.close()
    if abstract:
        text = f"摘要:{abstract}\n\n{text}"
    return text[:24000]


def _plain_full(path) -> str:
    """回退方案：PyMuPDF 逐页纯文本（全部页）。"""
    import fitz  # PyMuPDF
    doc = fitz.open(path)
    try:
        return "\n".join(doc[i].get_text() for i in range(doc.page_count))
    finally:
        doc.close()


def full_text(path, abstract: str = None, max_chars: int = 120000) -> str:
    """整篇 PDF → Markdown（pymupdf4llm 优先，保留版面结构）。
    max_chars 仅为防超长论文撑爆模型上下文的安全上限。"""
    text = ""
    try:
        import pymupdf4llm
        # show_progress=False 关键：否则它会往 stdout 打印进度，污染 agent 的 stdout 输出
        text = pymupdf4llm.to_markdown(str(path), show_progress=False) or ""
    except Exception:
        text = ""
    if len(text.strip()) < 200:        # pymupdf4llm 失败/几乎为空（如扫描件）→ 回退纯文本
        try:
            text = _plain_full(path)
        except Exception:
            text = text or ""
    if abstract:
        text = f"摘要:{abstract}\n\n{text}"
    return text[:max_chars]


def page_count(path) -> int:
    import fitz  # PyMuPDF
    doc = fitz.open(path)
    try:
        return doc.page_count
    finally:
        doc.close()
