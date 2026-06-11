"""Semantic Scholar 数据源（主力）：一个 API 覆盖所有顶会，附带 TLDR/领域/引用/开放PDF链接。"""
from ..models import PaperStub
from .. import util, config

FIELDS = ("title,authors,venue,year,abstract,tldr,s2FieldsOfStudy,"
          "citationCount,externalIds,openAccessPdf,url")
ENDPOINT = "https://api.semanticscholar.org/graph/v1/paper/search"


def stub_from_s2(p: dict) -> PaperStub:
    """把 S2 一条论文 JSON 转成 PaperStub（search / recommendations 共用）。"""
    ext = p.get("externalIds") or {}
    oa = p.get("openAccessPdf") or {}
    tldr = p.get("tldr") or {}
    return PaperStub(
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


class SemanticScholar:
    name = "semanticscholar"

    def search(self, query, years, limit):
        params = {"query": query, "fields": FIELDS, "limit": min(limit, 100)}
        if years:
            params["year"] = f"{years[0]}-{years[1]}"
        headers = {"x-api-key": config.S2_API_KEY} if getattr(config, "S2_API_KEY", "") else None
        r = util.get(ENDPOINT, params=params, headers=headers)
        for p in (r.json().get("data") or [])[:limit]:
            yield stub_from_s2(p)
