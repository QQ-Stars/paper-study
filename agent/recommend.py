"""相似论文推荐：基于 Semantic Scholar Recommendations API。

给定库中一篇论文 → S2 按内容相似度找一批相近论文 → 标注是否已在库 →
作为「候选」返回（与采集向导同款结构，可直接勾选入库；分类在入库时现做）。
进度 → stderr，结果 JSON → stdout。"""
import json
import sys
from . import db, util, config
from .sources.semanticscholar import stub_from_s2

# 推荐接口支持的字段比 graph 少：不接受 tldr（会 400），改用 abstract 兜底摘要。
REC_FIELDS = ("title,authors,venue,year,abstract,s2FieldsOfStudy,"
              "citationCount,externalIds,openAccessPdf,url")

REC_ENDPOINT = "https://api.semanticscholar.org/recommendations/v1/papers/forpaper/{pid}"
S2_SEARCH = "https://api.semanticscholar.org/graph/v1/paper/search"


def _p(msg):
    print(msg, file=sys.stderr, flush=True)


def _headers():
    return {"x-api-key": config.S2_API_KEY} if getattr(config, "S2_API_KEY", "") else None


def _resolve_s2_id(row):
    """把库内论文解析成推荐接口能识别的 id（直接接受 paperId / ARXIV: / DOI:，无需额外请求，
    省一次调用以避开无 key 时的限流）；都没有才按标题搜一次拿 paperId。"""
    if row["s2_id"]:
        return row["s2_id"]
    if row["arxiv_id"]:
        return f"ARXIV:{row['arxiv_id']}"
    if row["doi"]:
        return f"DOI:{row['doi']}"
    if row["title"]:
        try:
            r = util.get(S2_SEARCH, params={"query": row["title"], "fields": "title", "limit": 1},
                         headers=_headers())
            data = r.json().get("data") or []
            if data and data[0].get("paperId"):
                return data[0]["paperId"]
        except Exception as e:
            _p(f"RESOLVEERR::{e}")
    return None


def _result(obj):
    sys.stdout.write(json.dumps(obj, ensure_ascii=False))
    sys.stdout.flush()


def recommend_paper(pid: str, limit: int = 14):
    con = db.connect()
    row = con.execute("SELECT id, s2_id, arxiv_id, doi, title FROM papers WHERE id=?", (pid,)).fetchone()
    if not row:
        _p("NOPAPER::")
        _result({"ok": False, "error": "论文不存在", "candidates": []})
        con.close(); return

    _p("STAGE::resolve")
    s2id = _resolve_s2_id(row)
    if not s2id:
        _p("NOID::")
        _result({"ok": False, "error": "no_s2_id", "candidates": []})
        con.close(); return
    _p(f"SEED::{(row['title'] or '')[:60]}")

    _p("STAGE::recommend")
    try:
        r = util.get(REC_ENDPOINT.format(pid=s2id),
                     params={"fields": REC_FIELDS, "limit": min(max(int(limit), 1), 100), "from": "all-cs"},
                     headers=_headers())
        recs = r.json().get("recommendedPapers") or []
    except Exception as e:
        _p(f"RECERR::{e}")
        _result({"ok": False, "error": str(e), "candidates": []})
        con.close(); return
    _p(f"FOUND::{len(recs)}")

    cands, seen = [], set()
    for p in recs:
        stub = stub_from_s2(p)
        if not stub.title:
            continue
        tn = db.title_norm(stub.title)
        if tn in seen:
            continue
        seen.add(tn)
        in_lib = db.exists(con, arxiv_id=stub.arxiv_id, title_norm_v=tn)
        cands.append({
            **stub.model_dump(),
            # 分类留空——入库时再现做（保持库内研究方向/会议分类一致）；此处先快速展示
            "type": "", "topic": "", "task": None,
            "models": [], "datasets": [], "contribution": "",
            "llm_tldr": None, "tags": [], "relevance": None,
            "in_library": in_lib,
        })
    con.close()
    _p(f"DONE::{len(cands)}")
    _result({"ok": True, "candidates": cands})
