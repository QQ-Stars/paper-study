"""arXiv 数据源（时效兜底）：抓最新预印本；从 comment 识别 venue。"""
import re
import feedparser
from ..models import PaperStub
from .. import util

ENDPOINT = "http://export.arxiv.org/api/query"
VENUE_RE = re.compile(r"(CVPR|ICCV|ECCV|NeurIPS|ICML|ICLR|ACL|EMNLP|AAAI|WACV)\s*'?\s*(20\d\d)", re.I)


class Arxiv:
    name = "arxiv"

    def search(self, query, years, limit):
        terms = [w for w in query.split() if w]
        sq = " AND ".join(f"all:{w}" for w in terms) if terms else f"all:{query}"
        params = {
            "search_query": sq, "start": 0, "max_results": limit,
            "sortBy": "relevance", "sortOrder": "descending",
        }
        r = util.get(ENDPOINT, params=params)
        feed = feedparser.parse(r.text)
        for e in feed.entries:
            aid = e.id.split("/abs/")[-1]
            aid = re.sub(r"v\d+$", "", aid)
            pdf = next((l.href for l in getattr(e, "links", []) if l.get("type") == "application/pdf"), None)
            comment = getattr(e, "arxiv_comment", "") or ""
            m = VENUE_RE.search(comment)
            venue = m.group(1).upper() if m else "arXiv"
            year = (getattr(e, "published", "") or "")[:4] or None
            if years and year and not (str(years[0]) <= year <= str(years[1])):
                continue
            yield PaperStub(
                source="arxiv", source_id=aid, arxiv_id=aid,
                title=re.sub(r"\s+", " ", e.title).strip(),
                authors=[a.name for a in getattr(e, "authors", [])],
                venue=venue, year=year,
                abstract=re.sub(r"\s+", " ", getattr(e, "summary", "")).strip(),
                url=e.id, pdf_url=pdf,
            )
