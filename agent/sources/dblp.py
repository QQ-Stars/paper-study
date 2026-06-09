"""DBLP 数据源：CS 顶会元数据最干净准确（venue/年份）。注意：无摘要/无PDF。"""
import re
from ..models import PaperStub
from .. import util

ENDPOINT = "https://dblp.org/search/publ/api"


class DBLP:
    name = "dblp"

    def search(self, query, years, limit):
        params = {"q": query, "format": "json", "h": min(limit, 60)}
        r = util.get(ENDPOINT, params=params)
        hits = (((r.json() or {}).get("result") or {}).get("hits") or {}).get("hit") or []
        for h in hits[:limit]:
            info = h.get("info") or {}
            yr = info.get("year")
            if years and yr and not (str(years[0]) <= str(yr) <= str(years[1])):
                continue
            au = (info.get("authors") or {}).get("author")
            if isinstance(au, dict):
                au = [au]
            names = [(a.get("text") if isinstance(a, dict) else a) for a in (au or [])]
            ee = info.get("ee") or ""
            doi = ee.split("doi.org/")[-1] if "doi.org/" in ee else None
            yield PaperStub(
                source="dblp", source_id=info.get("key", ""),
                title=re.sub(r"\s+", " ", info.get("title") or "").strip().rstrip("."),
                authors=names,
                venue=info.get("venue"),
                year=str(yr) if yr else None,
                abstract=None,            # DBLP 不提供摘要
                url=info.get("url") or ee or None,
                pdf_url=None,
                doi=doi,
            )
