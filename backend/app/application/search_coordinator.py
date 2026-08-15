from __future__ import annotations

"""Legacy discovery coordination without exposing subprocess or SQL details."""

from collections.abc import Awaitable, Callable, Mapping, Sequence
import inspect
import json
import re
from typing import Any


_SOURCES = frozenset({"semanticscholar", "arxiv", "openalex", "dblp"})
_INTEGER_PREFIX = re.compile(r"^[+-]?\d+")


class SearchCoordinator:
    def __init__(
        self,
        agent: Any,
        unit_of_work_factory: Callable[[], Any],
        *,
        translate_text_direct: Callable[[str], str | Awaitable[str]] | None = None,
    ) -> None:
        self._agent = agent
        self._work_factory = unit_of_work_factory
        self._translate_text_direct = translate_text_direct

    async def ingest(self, payload: Mapping[str, object]) -> dict[str, object]:
        sources = _sources(payload.get("sources"))
        maximum = min(_parse_integer(payload.get("max")) or 10, 50)
        args = [
            "--query",
            _js_string(payload.get("query")),
            "--sources",
            ",".join(sources),
            "--years",
            _js_string(payload.get("years") or "2024-2026"),
            "--max",
            str(maximum),
            "--min-relevance",
            _js_string(
                0.5 if payload.get("minRelevance") is None else payload.get("minRelevance")
            ),
        ]
        if payload.get("deep"):
            args.append("--deep")
        if payload.get("expand"):
            args.append("--expand")
        if payload.get("downloadPdf") is False:
            args.append("--no-pdf")
        result = await self._agent.run("ingest", args)
        code = int(getattr(result, "returncode", 1))
        output = f"{getattr(result, 'stdout', '')}{getattr(result, 'stderr', '')}"
        return {"ok": code == 0, "code": code, "output": output}

    async def expand(self, payload: Mapping[str, object]) -> dict[str, object]:
        args = (
            "--query",
            _js_string(payload.get("query") or ""),
            "--expand-n",
            _js_string(payload.get("expandN") or 6),
        )
        result = await self._agent.run("expand", args)
        queries: list[object] = []
        try:
            decoded = json.loads(str(getattr(result, "stdout", "") or ""))
            if isinstance(decoded, list):
                queries = decoded
        except (TypeError, ValueError):
            pass
        return {"ok": True, "queries": queries}

    async def translate_text(self, text: str) -> dict[str, object]:
        if self._translate_text_direct is not None:
            try:
                translated = self._translate_text_direct(text)
                if inspect.isawaitable(translated):
                    translated = await translated
                rendered = str(translated or "")
                if rendered:
                    return {"ok": True, "text": rendered}
            except Exception:
                pass

        result = await self._agent.run("translate-text", (), stdin=text)
        code = int(getattr(result, "returncode", 1))
        translated = str(getattr(result, "stdout", "") or "").strip()
        stderr = str(getattr(result, "stderr", "") or "").strip()
        error = "" if code == 0 else ((stderr.splitlines()[-1] if stderr else "翻译失败"))
        return {
            "ok": code == 0 and bool(translated),
            "text": translated,
            "error": error,
        }

    def scan_pdfs(self, pdf_files: Any, directory: str) -> dict[str, object]:
        return pdf_files.scan(directory, max_depth=4, limit=2000)

    async def citation_graph(self) -> dict[str, object]:
        async with self._work_factory() as work:
            papers, edges = await work.papers.citation_graph_records()
        indegree: dict[str, int] = {}
        outdegree: dict[str, int] = {}
        for edge in edges:
            source = str(edge["src_id"])
            target = str(edge["dst_id"])
            outdegree[source] = outdegree.get(source, 0) + 1
            indegree[target] = indegree.get(target, 0) + 1
        nodes = [
            {
                **paper,
                "indeg": indegree.get(str(paper["id"]), 0),
                "outdeg": outdegree.get(str(paper["id"]), 0),
            }
            for paper in papers
        ]
        links = [
            {"source": edge["src_id"], "target": edge["dst_id"]}
            for edge in edges
        ]
        return {"nodes": nodes, "links": links, "edgeCount": len(edges)}


def valid_sources(value: object) -> tuple[str, ...]:
    return _sources(value)


def _sources(value: object) -> tuple[str, ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return ()
    return tuple(str(item) for item in value if str(item) in _SOURCES)


def _parse_integer(value: object) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    match = _INTEGER_PREFIX.match(str(value).lstrip())
    return int(match.group(0)) if match is not None else None


def _js_string(value: object) -> str:
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    return str(value)


__all__ = ["SearchCoordinator", "valid_sources"]
