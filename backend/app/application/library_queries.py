from __future__ import annotations

from collections.abc import Callable
import hashlib
import json
from pathlib import Path
from typing import Any


class LibraryQueries:
    def __init__(
        self,
        unit_of_work_factory: Callable[[], Any],
        *,
        pdf_files: Any,
        ccf_ranks: dict[str, str] | None = None,
    ) -> None:
        self._work_factory = unit_of_work_factory
        self._pdf_files = pdf_files
        self._ccf_ranks = dict(ccf_ranks) if ccf_ranks is not None else _load_ccf_ranks()

    async def list_papers(self) -> list[dict[str, object]]:
        async with self._work_factory() as work:
            rows = await work.papers.list_legacy()
        for row in rows:
            row["ccf"] = self._ccf_ranks.get(str(row.get("venue") or ""))
            row["hasPdf"] = bool(self._pdf_files.has_pdf(row))
        return rows

    async def get_paper(self, paper_id: str) -> dict[str, object] | None:
        async with self._work_factory() as work:
            return await work.papers.get_legacy(paper_id)

    def pdf_sha256(self, paper: dict[str, object]) -> str | None:
        if not self._pdf_files.has_pdf(paper):
            return None
        opened = self._pdf_files.open_for_id(
            str(paper.get("id") or ""),
            stored_path=paper.get("pdf_path"),
        )
        if opened is None:
            return None
        digest = hashlib.sha256()
        size = 0
        with opened.stream:
            while chunk := opened.stream.read(1024 * 1024):
                if not isinstance(chunk, bytes):
                    raise ValueError("PDF reader returned invalid bytes")
                digest.update(chunk)
                size += len(chunk)
        if size != opened.size:
            raise ValueError("PDF changed while its snapshot was created")
        return digest.hexdigest()


def _load_ccf_ranks() -> dict[str, str]:
    path = Path(__file__).resolve().parents[3] / "db" / "ccf_ranks.json"
    try:
        decoded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {
        str(name): str(rank)
        for name, rank in decoded.items()
        if isinstance(name, str) and isinstance(rank, str)
    }


__all__ = ["LibraryQueries"]
