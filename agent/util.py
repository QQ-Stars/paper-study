"""工具：slug 生成、带重试的 HTTP、PDF 下载。"""
import re
import httpx
from tenacity import retry, wait_exponential, stop_after_attempt

UA = {"User-Agent": "paper-study/0.1 (research use)"}


def slugify(s: str, n: int = 50) -> str:
    s = re.sub(r"[^A-Za-z0-9]+", "-", (s or "")).strip("-")
    return s[:n] or "paper"


def make_slug(stub) -> str:
    base = stub.arxiv_id or stub.s2_id or f"{stub.source}-{stub.source_id}" or "paper"
    base = str(base).replace("/", "-")
    return f"{base}_{slugify(stub.title)}"


def _field(obj, name):
    if isinstance(obj, dict):
        return obj.get(name)
    return getattr(obj, name, None)


def infer_pdf_url(obj) -> str:
    """Return an explicit PDF URL or infer an arXiv PDF URL from metadata."""
    pdf_url = (_field(obj, "pdf_url") or "").strip()
    if pdf_url:
        return pdf_url
    arxiv_id = (_field(obj, "arxiv_id") or "").strip()
    url = (_field(obj, "url") or "").strip()
    source_id = (_field(obj, "source_id") or "").strip()
    if not arxiv_id:
        for val in (url, source_id, _field(obj, "id") or ""):
            m = re.search(r"(\d{4}\.\d{4,5}(?:v\d+)?)", str(val or ""))
            if m:
                arxiv_id = m.group(1)
                break
    if arxiv_id:
        return f"https://arxiv.org/pdf/{arxiv_id}.pdf"
    m = re.search(r"arxiv\.org/(?:abs|pdf)/([^/?#]+)", url)
    if m:
        return f"https://arxiv.org/pdf/{m.group(1).replace('.pdf', '')}.pdf"
    return ""


def unpaywall_pdf_url(doi, email="") -> str:
    """用 Unpaywall 按 DOI 找开放获取(OA) PDF 直链——命中开放版才返回，否则空。
    覆盖 ACL Anthology / AAAI(ojs) / OpenReview / 开放的 ACM 等会议论文。"""
    doi = str(doi or "").strip()
    if not doi:
        return ""
    try:
        r = httpx.get(f"https://api.unpaywall.org/v2/{doi}",
                      params={"email": email or "paper-study@users.noreply.github.com"},
                      timeout=25, follow_redirects=True, headers=UA)
        if r.status_code != 200:
            return ""
        loc = (r.json().get("best_oa_location") or {})
        return (loc.get("url_for_pdf") or "").strip()
    except Exception:
        return ""


def s2_open_pdf(s2_id, api_key="") -> str:
    """用 Semantic Scholar(带 key 更稳)按 paperId 查 openAccessPdf 直链。"""
    s2_id = str(s2_id or "").strip()
    if not s2_id:
        return ""
    try:
        hdr = {**UA, "x-api-key": api_key} if api_key else UA
        r = httpx.get(f"https://api.semanticscholar.org/graph/v1/paper/{s2_id}",
                      params={"fields": "openAccessPdf"}, headers=hdr, timeout=25, follow_redirects=True)
        if r.status_code != 200:
            return ""
        return ((r.json().get("openAccessPdf") or {}).get("url") or "").strip()
    except Exception:
        return ""


def _norm_title(s) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(s or "").lower())


def openreview_pdf(title) -> str:
    """按标题在 OpenReview(ICLR/NeurIPS 等)检索，标题严格匹配才返回 PDF 直链。"""
    title = str(title or "").strip()
    if len(title) < 8:
        return ""
    want = _norm_title(title)
    for base in ("https://api2.openreview.net", "https://api.openreview.net"):
        try:
            r = httpx.get(f"{base}/notes/search", params={"term": title, "limit": 8}, timeout=25, headers=UA)
            if r.status_code != 200:
                continue
            for n in r.json().get("notes", []):
                c = n.get("content") or {}
                t = c.get("title"); t = t.get("value") if isinstance(t, dict) else t
                if not t or _norm_title(t) != want:
                    continue
                pdf = c.get("pdf"); pdf = pdf.get("value") if isinstance(pdf, dict) else pdf
                if not pdf:                    # 没有真正的 PDF 附件就跳过（避免 ?id= 拿到 404）
                    continue
                if n.get("id"):
                    return f"https://openreview.net/pdf?id={n['id']}"
                return f"https://openreview.net{pdf}" if str(pdf).startswith("/") else str(pdf)
        except Exception:
            continue
    return ""


def resolve_pdf_url(obj, email="", s2_key="") -> str:
    """综合解析 PDF 直链：arXiv/元数据 → Unpaywall(DOI) → S2 openAccessPdf → OpenReview(标题)。"""
    url = infer_pdf_url(obj)
    if url:
        return url
    url = unpaywall_pdf_url(_field(obj, "doi"), email)
    if url:
        return url
    url = s2_open_pdf(_field(obj, "s2_id"), s2_key)
    if url:
        return url
    return openreview_pdf(_field(obj, "title"))


def _looks_like_pdf(path) -> bool:
    try:
        with open(path, "rb") as f:
            head = f.read(1024)
        return b"%PDF-" in head
    except Exception:
        return False


@retry(wait=wait_exponential(min=2, max=30), stop=stop_after_attempt(6), reraise=True)
def get(url, headers=None, **kw):
    h = {**UA, **(headers or {})}
    r = httpx.get(url, timeout=30, headers=h, follow_redirects=True, **kw)
    r.raise_for_status()   # 429/5xx 会抛出 -> 被 tenacity 退避重试
    return r


def download_pdf(url, dest, progress=None):
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists() and dest.stat().st_size > 0 and _looks_like_pdf(dest):
        return dest
    if dest.exists():
        try:
            dest.unlink()
        except Exception:
            pass
    tmp = dest.with_suffix(dest.suffix + ".part")
    if tmp.exists():
        try:
            tmp.unlink()
        except Exception:
            pass
    with httpx.stream("GET", url, timeout=90, follow_redirects=True, headers=UA) as r:
        r.raise_for_status()
        total = int(r.headers.get("content-length") or 0)
        seen = 0
        last_emit = 0
        with open(tmp, "wb") as f:
            for chunk in r.iter_bytes(chunk_size=128 * 1024):
                if not chunk:
                    continue
                f.write(chunk)
                seen += len(chunk)
                if progress and (seen - last_emit >= 512 * 1024 or (total and seen >= total)):
                    progress(seen, total)
                    last_emit = seen
    if not _looks_like_pdf(tmp):
        try:
            tmp.unlink()
        except Exception:
            pass
        raise ValueError("downloaded file is not a valid PDF")
    tmp.replace(dest)
    if progress:
        progress(dest.stat().st_size, dest.stat().st_size)
    return dest
