"""引用关系图：抓每篇库内论文的参考文献(Semantic Scholar)，找出其中也在库里的，
建成「谁引用谁」的有向边（src 引用 dst，二者都在库内）。边存 cite_edges，全量重建。

进度 → stderr（TOTAL/PROG/DONE），统计结果 → stdout。"""
import json
import sys
from . import db, util, config

REFS = "https://api.semanticscholar.org/graph/v1/paper/{id}/references"


def _p(msg):
    print(msg, file=sys.stderr, flush=True)


def _headers():
    return {"x-api-key": config.S2_API_KEY} if getattr(config, "S2_API_KEY", "") else None


def build_edges():
    con = db.connect()
    db.ensure_edges_table(con)
    papers = con.execute("SELECT id, s2_id, arxiv_id, doi, title, title_norm FROM papers").fetchall()
    by_s2 = {p["s2_id"]: p["id"] for p in papers if p["s2_id"]}
    by_arxiv = {p["arxiv_id"]: p["id"] for p in papers if p["arxiv_id"]}
    by_doi = {(p["doi"] or "").lower(): p["id"] for p in papers if p["doi"]}
    by_tn = {p["title_norm"]: p["id"] for p in papers if p["title_norm"]}

    def lib_id(ext, s2pid, title):
        """把一条参考文献解析成库内论文 id（命中则返回，否则 None）。"""
        if s2pid and s2pid in by_s2:
            return by_s2[s2pid]
        ax = ext.get("ArXiv")
        if ax and ax in by_arxiv:
            return by_arxiv[ax]
        doi = (ext.get("DOI") or "").lower()
        if doi and doi in by_doi:
            return by_doi[doi]
        tn = db.title_norm(title or "")
        if tn and tn in by_tn:
            return by_tn[tn]
        return None

    targets = [p for p in papers if p["s2_id"] or p["arxiv_id"] or p["doi"]]
    _p(f"TOTAL::{len(targets)}")
    con.execute("DELETE FROM cite_edges")          # 全量重建
    con.commit()

    edges, done = set(), 0
    for p in targets:
        sid = p["s2_id"] or (f"ARXIV:{p['arxiv_id']}" if p["arxiv_id"] else f"DOI:{p['doi']}")
        try:
            r = util.get(REFS.format(id=sid),
                         params={"fields": "title,externalIds", "limit": 1000}, headers=_headers())
            refs = r.json().get("data") or []
        except Exception as e:
            _p(f"REFERR::{p['id'][:28]}::{e}")
            refs = []
        for it in refs:
            cp = it.get("citedPaper") or {}
            dst = lib_id(cp.get("externalIds") or {}, cp.get("paperId"), cp.get("title"))
            if dst and dst != p["id"]:
                edges.add((p["id"], dst))
        done += 1
        _p(f"PROG::{done}::{len(targets)}")

    for s, d in edges:
        con.execute("INSERT OR IGNORE INTO cite_edges(src_id, dst_id) VALUES(?,?)", (s, d))
    con.commit()
    n_nodes = con.execute("SELECT COUNT(*) FROM papers").fetchone()[0]
    con.close()
    _p(f"DONE::{len(edges)}")
    sys.stdout.write(json.dumps({"ok": True, "edges": len(edges), "nodes": n_nodes}, ensure_ascii=False))
    sys.stdout.flush()
