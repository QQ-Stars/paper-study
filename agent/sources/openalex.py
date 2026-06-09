"""OpenAlex 数据源：免费、覆盖全、含摘要(倒排索引)/主题/引用/开放PDF。"""
from ..models import PaperStub
from .. import util

ENDPOINT = "https://api.openalex.org/works"


def _abstract(inv):
    if not inv:
        return None
    pos = {}
    for w, idxs in inv.items():
        for i in idxs:
            pos[i] = w
    return (" ".join(pos[i] for i in sorted(pos)))[:4000] or None


class OpenAlex:
    name = "openalex"

    def search(self, query, years, limit):
        params = {"search": query, "per-page": min(limit, 50), "mailto": "paper-study@example.com"}
        if years:
            params["filter"] = f"from_publication_date:{years[0]}-01-01,to_publication_date:{years[1]}-12-31"
        r = util.get(ENDPOINT, params=params)
        for w in (r.json().get("results") or [])[:limit]:
            ids = w.get("ids") or {}
            doi = (ids.get("doi") or "").replace("https://doi.org/", "") or None
            loc = w.get("primary_location") or {}
            src = (loc.get("source") or {}) if isinstance(loc, dict) else {}
            best = w.get("best_oa_location") or {}
            arxiv = None
            for v in ids.values():
                if v and "arxiv.org/abs/" in str(v):
                    arxiv = str(v).split("/abs/")[-1]
            yield PaperStub(
                source="openalex", source_id=(w.get("id") or "").split("/")[-1],
                title=w.get("title") or w.get("display_name") or "",
                authors=[a.get("author", {}).get("display_name") for a in (w.get("authorships") or []) if a.get("author")],
                venue=(src.get("display_name") if src else None),
                year=str(w.get("publication_year")) if w.get("publication_year") else None,
                abstract=_abstract(w.get("abstract_inverted_index")),
                citations=w.get("cited_by_count"),
                url=(loc.get("landing_page_url") if isinstance(loc, dict) else None) or w.get("id"),
                pdf_url=(best.get("pdf_url") if isinstance(best, dict) else None),
                arxiv_id=arxiv, doi=doi,
            )
