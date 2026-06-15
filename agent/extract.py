"""PDF -> 文本（PyMuPDF / pymupdf4llm）。
- first_pages: 取前几页，pymupdf4llm 转 Markdown（保留标题层级、双栏阅读顺序），
               失败回退 PyMuPDF 纯文本。用于采集分类 / 本地导入抽元数据。
- full_text:   写讲解 / 翻译时通读全文，同样 pymupdf4llm 优先、纯文本回退。
- strip_references: 裁掉文末「参考文献 / 致谢」段（讲解、翻译共用，避免把书目灌给模型）。
所有输出都过 _tidy 压掉 pymupdf4llm 的多余空行。
"""
import re

# 文末标题：参考文献 / 致谢（整行就是这个词，允许前缀 #、数字、加粗 / 引用标记）
_TAIL_RE = re.compile(
    r'^[\s#>*_\-.0-9]*\**\s*'
    r'(references?|bibliography|参\s*考\s*文\s*献|acknowledge?ments?|致\s*谢)'
    r'\s*\**\s*:?\s*$', re.I)

_BLANK_RUN = re.compile(r'\n{3,}')
_TRAIL_WS = re.compile(r'[ \t]+\n')


def _tidy(md: str) -> str:
    """去行尾空白、把 3+ 连续空行压成一个空行（pymupdf4llm 常留大段空行）。"""
    if not md:
        return ""
    return _BLANK_RUN.sub('\n\n', _TRAIL_WS.sub('\n', md)).strip()


def strip_references(md: str):
    """裁掉「参考文献 / 致谢」标题及其后全部内容（含其后的附录），返回 (裁剪后文本, 是否裁剪)。
    _TAIL_RE 只匹配“整行就是该词”的标题行（如 **References**），正文里对 references 的提及不会命中；
    故只设 15% 下限挡掉首页/目录的极端误命中——附录很长把参考文献顶到前半段的论文(如 30%)也能正确裁剪。"""
    if not md:
        return md, False
    lines = md.split("\n")
    n = len(lines)
    for i, ln in enumerate(lines):
        if i > n * 0.15 and _TAIL_RE.match(ln.strip()):
            return "\n".join(lines[:i]).rstrip(), True
    return md, False


def _plain_pages(path, n: int = 0) -> str:
    """PyMuPDF 逐页纯文本回退；n>0 仅取前 n 页，n=0 取全部。"""
    import fitz  # PyMuPDF
    doc = fitz.open(path)
    try:
        last = doc.page_count if n <= 0 else min(n, doc.page_count)
        return "\n".join(doc[i].get_text() for i in range(last))
    finally:
        doc.close()


def first_pages(path, n: int = 8, abstract: str = None) -> str:
    """前 n 页 → Markdown（pymupdf4llm 修双栏顺序+加节标题，利分类/抽元数据），失败回退纯文本。"""
    text = ""
    try:
        import fitz
        doc = fitz.open(path)
        pc = doc.page_count
        doc.close()
        import pymupdf4llm
        # show_progress=False 关键：否则进度会打到 stdout，污染 agent 输出
        text = pymupdf4llm.to_markdown(
            str(path), pages=list(range(min(n, pc))), show_progress=False) or ""
    except Exception:
        text = ""
    if len(text.strip()) < 100:        # 失败 / 扫描件 → 回退纯文本
        try:
            text = _plain_pages(path, n)
        except Exception:
            text = text or ""
    text = _tidy(text)
    if abstract:
        text = f"摘要:{abstract}\n\n{text}"
    return text[:24000]


def full_text(path, abstract: str = None, max_chars: int = 120000) -> str:
    """整篇 PDF → Markdown（pymupdf4llm 优先，保留版面结构）。
    max_chars 仅为防超长论文撑爆模型上下文的安全上限。"""
    text = ""
    try:
        import pymupdf4llm
        text = pymupdf4llm.to_markdown(str(path), show_progress=False) or ""
    except Exception:
        text = ""
    if len(text.strip()) < 200:        # pymupdf4llm 失败/几乎为空（如扫描件）→ 回退纯文本
        try:
            text = _plain_pages(path)
        except Exception:
            text = text or ""
    text = _tidy(text)
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
