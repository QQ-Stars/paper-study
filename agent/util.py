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


@retry(wait=wait_exponential(min=2, max=30), stop=stop_after_attempt(6), reraise=True)
def get(url, **kw):
    r = httpx.get(url, timeout=30, headers=UA, follow_redirects=True, **kw)
    r.raise_for_status()   # 429/5xx 会抛出 -> 被 tenacity 退避重试
    return r


def download_pdf(url, dest):
    if dest.exists() and dest.stat().st_size > 0:
        return dest
    with httpx.stream("GET", url, timeout=90, follow_redirects=True, headers=UA) as r:
        r.raise_for_status()
        with open(dest, "wb") as f:
            for chunk in r.iter_bytes():
                f.write(chunk)
    return dest
