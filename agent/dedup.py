"""Read-only suspected-duplicate scan for Papers in the local library."""

from collections.abc import Iterable, Mapping
from difflib import SequenceMatcher

from . import db


def _as_mapping(paper) -> Mapping:
    mapping = getattr(paper, "_mapping", None)
    if mapping is not None:
        return mapping
    if isinstance(paper, Mapping):
        return paper
    return dict(paper)


def _brief(paper: Mapping) -> dict:
    return {
        "id": paper.get("id"),
        "title": paper.get("title") or "",
        "year": paper.get("year"),
        "venue": paper.get("venue"),
    }


def find_duplicate_pairs(papers: Iterable) -> list[dict]:
    """Return each matching Paper pair once, in input order."""
    prepared = []
    for value in papers:
        paper = _as_mapping(value)
        normalized = str(paper.get("title_norm") or "").strip()
        if not normalized:
            normalized = db.title_norm(paper.get("title") or "")
        if normalized:
            prepared.append((paper, normalized))

    pairs = []
    for index, (left, left_title) in enumerate(prepared):
        for right, right_title in prepared[index + 1 :]:
            similarity = SequenceMatcher(None, left_title, right_title).ratio()
            is_contained = left_title in right_title or right_title in left_title
            if not is_contained and similarity < 0.9:
                continue
            pairs.append(
                {
                    "left": _brief(left),
                    "right": _brief(right),
                    "similarity": round(similarity, 4),
                }
            )
    return pairs


def scan_duplicates() -> list[dict]:
    """Scan the configured application database without opening a write handle."""
    connection = db.connect_readonly()
    try:
        papers = connection.execute(
            "SELECT id, title, title_norm, year, venue FROM papers ORDER BY id"
        ).fetchall()
        return find_duplicate_pairs(papers)
    finally:
        connection.close()


__all__ = ["find_duplicate_pairs", "scan_duplicates"]
