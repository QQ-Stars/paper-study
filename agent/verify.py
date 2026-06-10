"""会议核实：查权威学术 API（Semantic Scholar / DBLP）拿论文真实发表会议，而非 LLM 臆测。
- 优先 S2 按 arxiv_id/doi/s2_id 精确查 → 拿 publicationVenue / venue。
- 标题精确匹配兜底（S2 search）。
- DBLP 按标题兜底（CS 会议名最干净）。
- 查不到正式发表记录 → 保留原值并标注"未找到"，绝不编造。
"""
import sys
from . import util, config
from .sources.dblp import DBLP

S2_PAPER = "https://api.semanticscholar.org/graph/v1/paper/"
S2_SEARCH = "https://api.semanticscholar.org/graph/v1/paper/search"
S2_FIELDS = "title,venue,year,publicationVenue,publicationDate,externalIds"

ACR = ["CVPR", "ICCV", "ECCV", "NeurIPS", "ICML", "ICLR", "ACL", "EMNLP", "NAACL",
       "COLING", "AAAI", "IJCAI", "WACV", "BMVC", "SIGGRAPH", "KDD", "TPAMI", "TMLR", "JMLR"]


def _p(msg):
    print(msg, file=sys.stderr, flush=True)


def _s2_headers():
    k = getattr(config, "S2_API_KEY", "") or ""
    return {"x-api-key": k} if k else None


def _norm(s):
    return "".join(ch.lower() for ch in (s or "") if ch.isalnum())


def _abbrev(name):
    """把冗长的会议全称规整成常见缩写，便于显示。"""
    if not name:
        return name
    up = name.upper().replace(".", "")
    for a in ACR:
        if a.upper() in up.replace(" ", "") or (" " + a.upper() + " ") in (" " + up + " "):
            return a
    table = [
        ("NEURAL INFORMATION PROCESSING", "NeurIPS"), ("COMPUTER VISION AND PATTERN", "CVPR"),
        ("INTERNATIONAL CONFERENCE ON COMPUTER VISION", "ICCV"), ("EUROPEAN CONFERENCE ON COMPUTER VISION", "ECCV"),
        ("INTERNATIONAL CONFERENCE ON MACHINE LEARNING", "ICML"), ("LEARNING REPRESENTATIONS", "ICLR"),
        ("EMPIRICAL METHODS", "EMNLP"), ("NORTH AMERICAN CHAPTER", "NAACL"),
        ("WINTER CONFERENCE ON APPLICATIONS", "WACV"), ("ARTIFICIAL INTELLIGENCE", "AAAI"),
    ]
    for key, ab in table:
        if key in up:
            return ab
    if "ASSOCIATION FOR COMPUTATIONAL LINGUISTICS" in up and "EMPIRICAL" not in up and "NORTH" not in up:
        return "ACL"
    return name.strip()


def _is_preprint(v):
    n = _norm(v)
    return (not n) or ("arxiv" in n) or (n == "corr")


def _s2_get(pid):
    try:
        return util.get(S2_PAPER + pid, params={"fields": S2_FIELDS}, headers=_s2_headers()).json()
    except Exception:
        return None


def _venue_from_s2(j):
    pv = (j.get("publicationVenue") or {})
    name = pv.get("name") or j.get("venue") or ""
    return name.strip()


def verify_one(c):
    title = c.get("title", "")
    orig = c.get("venue")
    venue, year, src, matched = orig, c.get("year"), "none", False

    # 1) DBLP 优先（CS 会议人工策展：会把 arXiv 条目与会议条目分开，跳过预印本条目即得准确「会议+发表年」）
    if title:
        try:
            for stub in DBLP().search(title, None, 5):
                if _norm(stub.title) == _norm(title) and stub.venue and not _is_preprint(stub.venue):
                    venue, year, src, matched = _abbrev(stub.venue), (stub.year or year), "dblp", True
                    break
        except Exception:
            pass

    # 2) Semantic Scholar 兜底（按 arxiv_id/doi/s2_id 精确，再标题精确匹配）
    if not matched:
        j = None
        if c.get("arxiv_id"):
            j = _s2_get("arXiv:" + str(c["arxiv_id"]))
        if not j and c.get("doi"):
            j = _s2_get("DOI:" + str(c["doi"]))
        if not j and c.get("s2_id"):
            j = _s2_get(str(c["s2_id"]))
        if not j and title:
            try:
                data = util.get(S2_SEARCH, params={"query": title, "fields": S2_FIELDS, "limit": 3},
                                headers=_s2_headers()).json().get("data") or []
                for cand in data:
                    if _norm(cand.get("title")) == _norm(title):
                        j = cand
                        break
            except Exception:
                pass
        if j:
            v = _venue_from_s2(j)
            if v and not _is_preprint(v):
                venue, src, matched = _abbrev(v), "semanticscholar", True
                pd = str(j.get("publicationDate") or "")
                if pd[:4].isdigit():
                    year = pd[:4]
                elif j.get("year"):
                    year = str(j.get("year"))

    changed = matched and _norm(venue) != _norm(orig)
    return {
        "venue": venue, "year": year, "matched": matched, "source_of_truth": src,
        "changed": changed, "orig_venue": orig,
        "note": "" if matched else "权威库未找到正式发表记录（可能确为预印本）",
    }


def verify_venues(cands):
    """对候选逐个核实真实发表会议。进度→stderr，结果(与输入同序)→stdout。"""
    _p("STAGE::verify")
    _p(f"TOTAL::{len(cands)}")
    out = []
    for i, c in enumerate(cands):
        try:
            res = verify_one(c)
        except Exception as e:
            res = {"venue": c.get("venue"), "year": c.get("year"), "matched": False,
                   "source_of_truth": "none", "changed": False, "orig_venue": c.get("venue"),
                   "note": f"核实出错: {e}", "error": True}
        out.append(res)
        tag = res["source_of_truth"] if res["matched"] else "miss"
        _p(f"VERIFIED::{i + 1}::{res['venue'] or '?'}::{tag}")
    _p(f"DONE::{len(out)}")
    return out
