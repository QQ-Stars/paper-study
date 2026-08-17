"""PDF -> 文本（PyMuPDF / pymupdf4llm）。
- first_pages: 取前几页，pymupdf4llm 转 Markdown（保留标题层级、双栏阅读顺序），
               失败回退 PyMuPDF 纯文本。用于采集分类 / 本地导入抽元数据。
- full_text:   写讲解 / 翻译时通读全文，同样 pymupdf4llm 优先、纯文本回退。
               可选 OCR 模式（settings.json: pdfTextProvider=ocr）：把页面渲染成
               图片交给 OCR 模型 API 转录；失败 / 配置不全时自动回退本地解析。
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
    pdfTextProvider=ocr 时先尝试 OCR 模型 API，失败 / 结果过短自动回退本地解析。
    max_chars 仅为防超长论文撑爆模型上下文的安全上限。"""
    if (config.PDF_TEXT_PROVIDER or "default") == "ocr":
        try:
            ocr_text = _ocr_full_text(path)
        except Exception as e:
            print(f"OCRERR::{e}（回退本地解析）", file=sys.stderr, flush=True)
            ocr_text = ""
        if len((ocr_text or "").strip()) >= 200:
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


def _ocr_full_text(path, paper_id=None) -> str:
    """整篇 PDF → OCR 文本；进度 → stderr（OCRPG::i/n）。配置不全直接抛错由调用方回退。
    传入 paper_id 且转换成功（≥200 字阈值防乱码落库）时写入 DB ocr_markdown 表，
    供讲解/翻译管道与阅读页复用。"""
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
            print(f"OCRSAVE::已落库（{len(text)} 字）", file=sys.stderr, flush=True)
        except Exception as e:
            print(f"OCRSAVE-ERR::{e}（落库失败，不影响本次结果）", file=sys.stderr, flush=True)
    return text
