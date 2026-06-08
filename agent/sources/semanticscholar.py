"""Semantic Scholar 数据源（主力）：一个 API 覆盖所有顶会，附带 TLDR/领域/引用/开放PDF链接。"""
from ..models import PaperStub
from .. import util

FIELDS = ("title,authors,venue,year,abstract,tldr,s2FieldsOfStudy,"
          "citationCount,externalIds,openAccessPdf,url")
ENDPOINT = "https://api.semanticscholar.org/graph/v1/paper/search"


class SemanticScholar:
    name = "semanticscholar"

    def search(self, query, years, limit):
        params = {"query": query, "fields": FIELDS, "limit": min(limit, 100)}
        if years:
            params["year"] = f"{years[0]}-{years[1]}"
        r = util.get(ENDPOINT, params=params)
        for p in (r.json().get("data") or [])[:limit]:
            ext = p.get("externalIds") or {}
            oa = p.get("openAccessPdf") or {}
            tldr = p.get("tldr") or {}
            yield PaperStub(
                source="semanticscholar",
                source_id=p.get("paperId", ""),
                s2_id=p.get("paperId"),
                title=p.get("title") or "",
                authors=[a.get("name") for a in (p.get("authors") or []) if a.get("name")],
                venue=p.get("venue") or None,
                year=str(p.get("year")) if p.get("year") else None,
                abstract=p.get("abstract"),
                tldr=tldr.get("text"),
                fields=[f.get("category") for f in (p.get("s2FieldsOfStudy") or []) if f.get("category")],
                citations=p.get("citationCount"),
                url=p.get("url"),
                pdf_url=oa.get("url"),
                arxiv_id=ext.get("ArXiv"),
                doi=ext.get("DOI"),
            )
