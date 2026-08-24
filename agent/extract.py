"""PDF -> 文本（PyMuPDF / pymupdf4llm）。
- first_pages: 取前几页，pymupdf4llm 转 Markdown（保留标题层级、双栏阅读顺序），
               失败回退 PyMuPDF 纯文本。用于采集分类 / 本地导入抽元数据。
- full_text:   写讲解 / 翻译时通读全文，同样 pymupdf4llm 优先、纯文本回退。
               可选 OCR 模式（settings.json: pdfTextProvider=ocr）：把页面渲染成
               图片交给 OCR 模型 API 转录；OCR 失败时直接报错，不混用本地解析。
- strip_references: 裁掉文末「参考文献 / 致谢」段（讲解、翻译共用，避免把书目灌给模型）。
所有输出都过 _tidy 压掉 pymupdf4llm 的多余空行。
"""
import base64
import json
import re
import sys
import urllib.request

from . import config

# 文末标题：参考文献 / 致谢（整行就是这个词，允许前缀 #、数字、加粗 / 引用标记）
_TAIL_RE = re.compile(
    r'^[\s#>*_\-.0-9]*\**\s*'
    r'(references?|bibliography|参\s*考\s*文\s*献|acknowledge?ments?|致\s*谢)'
    r'\s*\**\s*:?\s*$', re.I)

# 附录/补充材料可能出现在参考文献之后。翻译时跳过参考文献内容，
# 但遇到同级（或更高层级）的附录标题应恢复正文，避免把附录一并裁掉。
_APPENDIX_TITLE_RE = re.compile(
    r'^(?:appendix(?:es)?|supplement(?:ary|al)?|'
    r'supporting(?:\s+(?:information|material|data))?|'
    r'附录(?:\s*[A-Za-z0-9一二三四五六七八九十百]+)?|'
    r'补充(?:材料|信息|数据)?)'
    r'(?:\s|$|[:：.\-])',
    re.I,
)

_BLANK_RUN = re.compile(r'\n{3,}')
_TRAIL_WS = re.compile(r'[ \t]+\n')

# OCR 转录指令：DeepSeek-OCR 系列模型必须用厂商官方提示词激活解码模式，
# 通用指令会让模型停留在“视觉压缩 token”输出态（返回大量 } 乱码，实测验证）。
# 官方 Markdown 提示词输出 Markdown + grounding 标记（<|ref|>/<|det|> 边界框），
# 由 _ocr_clean_grounding 剔除；其他 OpenAI 兼容 vision 模型同样适用该提示词。
_OCR_GROUND_PROMPT = "<|grounding|>Convert the document to markdown."

_OCR_REF_RE = re.compile(r"<\|ref\|>.*?<\|/ref\|>")
_OCR_DET_RE = re.compile(r"<\|det\|>.*?<\|/det\|>")


def _ocr_clean_grounding(md: str) -> str:
    """剔除 DeepSeek-OCR grounding 标记（<|ref|>标签<|/ref|> 与 <|det|>[[x,y,w,h]]<|/det|> 边界框）。"""
    md = _OCR_REF_RE.sub("", md)
    md = _OCR_DET_RE.sub("", md)
    return md


def _tidy(md: str) -> str:
    """去行尾空白、把 3+ 连续空行压成一个空行（pymupdf4llm 常留大段空行）。"""
    if not md:
        return ""
    return _BLANK_RUN.sub('\n\n', _TRAIL_WS.sub('\n', md)).strip()


def _heading_level(line: str):
    """返回 Markdown ATX 标题层级；普通标题（或 OCR 纯文本标题）返回 None。"""
    match = re.match(r'^\s*(#{1,6})\s*', line)
    return len(match.group(1)) if match else None


def _section_title(line: str) -> str:
    """移除常见 Markdown/编号装饰，得到用于识别附录的标题文本。"""
    title = line.strip()
    title = re.sub(r'^\s*#{1,6}\s*', '', title)
    title = re.sub(r'^[>*]+\s*', '', title)
    title = re.sub(r'^(?:[-+*]\s+)', '', title)
    title = re.sub(r'^\*+\s*|\s*\*+$', '', title)
    title = re.sub(r'^\d+(?:\.\d+)*[.)]?\s+', '', title)
    return title.strip()


def _appendix_heading_level(line: str):
    """若一行是附录/补充材料标题，返回其层级（纯文本标题为 0）；否则返回 None。

    除关键词标题（appendix/supplement/附录/补充材料…）外，兼容 OCR 输出中常见的
    LaTeX 式附录节标题：ATX 标题且首个 token 为单个大写字母，如
    「# A Precision-recall tradeoffs with weaker models」——LaTeX 论文附录节以
    单字母编号、正文节标题是单词，且参考文献条目不会带 # 前缀，误判概率低。
    纯文本行不适用单字母规则（参考文献条目常以作者缩写「A. Author」开头）。"""
    title = _section_title(line)
    if _heading_level(line) is not None and re.match(r'^[A-Z](?:\s+\S|[.)])', title):
        return _heading_level(line)
    candidates = [title]
    # 常见 OCR 标题会把附录编号放在词前，例如“ A APPENDIX”。
    prefixed_title = re.sub(r'^[A-Z](?:[.)]|\s+)\s*', '', title, count=1)
    if prefixed_title != title:
        candidates.append(prefixed_title)
    if not any(_APPENDIX_TITLE_RE.match(candidate) for candidate in candidates):
        return None
    return _heading_level(line) or 0


def strip_references(md: str):
    """跳过「参考文献 / 致谢」章节，同时保留其后同级附录/补充材料。

    _TAIL_RE 只匹配“整行就是该词”的标题行（如 **References**），正文里对
    references 的提及不会命中；仍保留 15% 下限以挡掉首页/目录的极端误命中。
    参考文献若位于文末则行为与旧实现一致；若后面出现同级（或纯文本）附录，
    从附录标题开始恢复输出。
    """
    if not md:
        return md, False
    lines = md.split("\n")
    n = len(lines)
    out = []
    skipping = False
    skipped_level = None
    stripped = False

    for i, line in enumerate(lines):
        if not skipping:
            if i > n * 0.15 and _TAIL_RE.match(line.strip()):
                skipping = True
                skipped_level = _heading_level(line)
                stripped = True
                continue
            out.append(line)
            continue

        # 跳过态的恢复规则：附录/补充材料标题（任意层级），或任何同级/更高级的
        # 新标题（参考文献之后的同级标题即后续正文，如附录节、伦理声明等）。
        # 若该标题仍是参考文献/致谢类尾词（如 Acknowledgments 后接 References），
        # 则保持跳过并刷新层级。
        level = _heading_level(line)
        same_or_higher = level is not None and (
            skipped_level is None or level <= skipped_level
        )
        if _appendix_heading_level(line) is not None or same_or_higher:
            if _TAIL_RE.match(line.strip()):
                skipped_level = level
                continue
            skipping = False
            out.append(line)

    return "\n".join(out).rstrip(), stripped


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
    pdfTextProvider=ocr 时只调用 OCR 模型 API；失败 / 结果过短直接抛错，
    不回退到本地解析，避免把两种来源混在一起。
    max_chars 仅为防超长论文撑爆模型上下文的安全上限。"""
    if (config.PDF_TEXT_PROVIDER or "default") == "ocr":
        ocr_text = _ocr_full_text(path)
        if len((ocr_text or "").strip()) < 200:
            raise RuntimeError("OCR 结果为空或过短")
        text = _tidy(ocr_text)
        if abstract:
            text = f"摘要:{abstract}\n\n{text}"
        return text[:max_chars]
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


# ---------- 可选：OCR 模型 API 提取（pdfTextProvider=ocr 时启用） ----------

def _ocr_settings() -> dict:
    return {
        "base": (config.OCR_API_BASE or "").rstrip("/"),
        "key": config.OCR_API_KEY or "",
        "model": config.OCR_MODEL or "",
        "dpi": max(72, int(config.OCR_DPI or 200)),
        "batch": max(1, int(config.OCR_PAGE_BATCH or 4)),
        "max_pages": max(0, int(config.OCR_MAX_PAGES or 0)),
    }


def _ocr_page_images(path, dpi: int, max_pages: int) -> list:
    """逐页渲染为 PNG 并 base64（供 vision/OCR 接口使用）。"""
    import fitz  # PyMuPDF
    doc = fitz.open(path)
    try:
        total = doc.page_count if max_pages <= 0 else min(max_pages, doc.page_count)
        images = []
        for i in range(total):
            pix = doc[i].get_pixmap(dpi=dpi)
            images.append(base64.b64encode(pix.tobytes("png")).decode("ascii"))
        return images
    finally:
        doc.close()


def _ocr_transcribe(images_b64: list, cfg: dict) -> str:
    """把若干页图片一次交给 OpenAI 兼容 chat 接口转录为 Markdown（官方提示词）。"""
    content = []
    for b64 in images_b64:
        content.append({
            "type": "image_url",
            "image_url": {"url": f"data:image/png;base64,{b64}"},
        })
    content.append({"type": "text", "text": _OCR_GROUND_PROMPT})
    payload = {
        "model": cfg["model"],
        "temperature": 0,
        "messages": [
            {"role": "user", "content": content},
        ],
    }
    req = urllib.request.Request(
        cfg["base"] + "/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    if cfg["key"]:
        req.add_header("Authorization", "Bearer " + cfg["key"])
    with urllib.request.urlopen(req, timeout=600) as resp:
        doc = json.loads(resp.read().decode("utf-8"))
    text = (doc.get("choices") or [{}])[0].get("message", {}).get("content") or ""
    return _ocr_clean_grounding(text)


def _ocr_full_text(path, paper_id=None, quiet=False) -> str:
    """整篇 PDF → OCR 文本；进度 → stderr（OCRPG::i/n）。配置不全或请求失败直接抛错。
    传入 paper_id 且转换成功（≥200 字阈值防乱码落库）时写入 DB ocr_markdown 表，
    供讲解/翻译管道与阅读页复用。quiet=True 时不输出逐页/落库进度（篇级并发批量场景，
    避免多篇 OCRPG 行交错，批次自身有 ITEM:: 进度契约）。"""
    cfg = _ocr_settings()
    if not cfg["base"] or not cfg["model"]:
        raise RuntimeError("OCR API 未配置（需在设置中填写 OCR 地址与模型）")
    images = _ocr_page_images(path, cfg["dpi"], cfg["max_pages"])
    if not images:
        raise RuntimeError("PDF 没有可 OCR 的页面")
    parts = []
    total = len(images)
    for start in range(0, total, cfg["batch"]):
        chunk = images[start:start + cfg["batch"]]
        if not quiet:
            print(f"OCRPG::{min(start + len(chunk), total)}/{total}", file=sys.stderr, flush=True)
        parts.append(_ocr_transcribe(chunk, cfg))
    text = "\n\n".join((p or "").strip() for p in parts if (p or "").strip())
    if paper_id and len(text.strip()) >= 200:
        try:
            from . import db as _db
            con = _db.connect()
            try:
                _db.set_ocr_markdown(con, paper_id, text)
            finally:
                con.close()
            if not quiet:
                print(f"OCRSAVE::已落库（{len(text)} 字）", file=sys.stderr, flush=True)
        except Exception as e:
            print(f"OCRSAVE-ERR::{e}（落库失败，不影响本次结果）", file=sys.stderr, flush=True)
    return text
