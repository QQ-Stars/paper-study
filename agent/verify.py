"""会议核实：查权威学术库（可选 DBLP / Semantic Scholar / OpenAlex）拿论文真实发表会议，而非 LLM 臆测。
- 可指定核实源及优先级（sources，按先后顺序逐个查，命中即止）。
- 候选若本就来自所选权威源之一 → 已是权威数据，跳过不查（数据源==核实源就没必要核实）。
- 查不到正式发表记录 → 保留原值并标"仅预印本"，绝不编造。
"""
import sys
from . import util, config, ccf
from .sources.dblp import DBLP
from .sources.openalex import OpenAlex

S2_PAPER = "https://api.semanticscholar.org/graph/v1/paper/"
S2_SEARCH = "https://api.semanticscholar.org/graph/v1/paper/search"
S2_FIELDS = "title,venue,year,publicationVenue,publicationDate,externalIds"

AUTHORITIES = ["dblp", "semanticscholar", "openalex"]   # arxiv 不是会议权威（预印本服务器）
SRC_LABEL = {"dblp": "DBLP", "semanticscholar": "Semantic Scholar", "openalex": "OpenAlex"}

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


# ---------- 各权威源单篇查询：命中返回 (venue, year)，否则 None ----------
def _lk_dblp(c):
    title = c.get("title", "")
    if not title:
        return None
    for stub in DBLP().search(title, None, 5):
        if _norm(stub.title) == _norm(title) and stub.venue and not _is_preprint(stub.venue):
            return (_abbrev(stub.venue), stub.year)
    return None


def _lk_openalex(c):
    title = c.get("title", "")
    if not title:
        return None
    for stub in OpenAlex().search(title, None, 5):
        if _norm(stub.title) == _norm(title) and stub.venue and not _is_preprint(stub.venue):
            return (_abbrev(stub.venue), stub.year)
    return None


def _lk_s2(c):
    j = None
    if c.get("arxiv_id"):
        j = _s2_get("arXiv:" + str(c["arxiv_id"]))
    if not j and c.get("doi"):
        j = _s2_get("DOI:" + str(c["doi"]))
    if not j and c.get("s2_id"):
        j = _s2_get(str(c["s2_id"]))
    title = c.get("title", "")
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
        pv = (j.get("publicationVenue") or {})
        v = (pv.get("name") or j.get("venue") or "").strip()
        if v and not _is_preprint(v):
            pd = str(j.get("publicationDate") or "")
            yr = pd[:4] if pd[:4].isdigit() else (str(j.get("year")) if j.get("year") else None)
            return (_abbrev(v), yr)
    return None


def _s2_get(pid):
    try:
        return util.get(S2_PAPER + pid, params={"fields": S2_FIELDS}, headers=_s2_headers()).json()
    except Exception:
        return None


LOOKUPS = {"dblp": _lk_dblp, "semanticscholar": _lk_s2, "openalex": _lk_openalex}


def verify_one(c, sources):
    orig = c.get("venue")
    cand_src = (c.get("source") or "").strip()
    # 候选本就来自所选权威源之一 → 已权威，无需核实
    if cand_src in sources:
        return {"venue": orig, "year": c.get("year"), "matched": True, "skipped": True,
                "source_of_truth": cand_src, "changed": False, "orig_venue": orig, "ccf": ccf.rank(orig),
                "note": f"数据源即权威源（{SRC_LABEL.get(cand_src, cand_src)}），无需核实"}
    # 按所选源优先级逐个查，命中即止
    for s in sources:
        fn = LOOKUPS.get(s)
        if not fn:
            continue
        try:
            r = fn(c)
        except Exception:
            r = None
        if r:
            venue = r[0] or orig
            return {"venue": venue, "year": (r[1] or c.get("year")), "matched": True, "skipped": False,
                    "source_of_truth": s, "changed": (_norm(venue) != _norm(orig)), "orig_venue": orig,
                    "ccf": ccf.rank(venue), "note": ""}
    return {"venue": orig, "year": c.get("year"), "matched": False, "skipped": False,
            "source_of_truth": "none", "changed": False, "orig_venue": orig, "ccf": ccf.rank(orig),
            "note": "所选权威库未找到正式发表记录（可能确为预印本）"}


def verify_venues(cands, sources=None):
    """对候选逐个核实真实发表会议。进度→stderr，结果(与输入同序)→stdout。"""
    sources = [s for s in (sources or ["dblp", "semanticscholar"]) if s in AUTHORITIES] or ["dblp", "semanticscholar"]
    _p("STAGE::verify")
    _p("SOURCES::" + ",".join(sources))
    _p(f"TOTAL::{len(cands)}")
    out = []
    for i, c in enumerate(cands):
        try:
            res = verify_one(c, sources)
        except Exception as e:
            res = {"venue": c.get("venue"), "year": c.get("year"), "matched": False, "skipped": False,
                   "source_of_truth": "none", "changed": False, "orig_venue": c.get("venue"),
                   "note": f"核实出错: {e}", "error": True}
        out.append(res)
        tag = ("skip:" + res["source_of_truth"]) if res.get("skipped") else (res["source_of_truth"] if res["matched"] else "miss")
        _p(f"VERIFIED::{i + 1}::{res['venue'] or '?'}::{tag}")
    _p(f"DONE::{len(out)}")
    return out
