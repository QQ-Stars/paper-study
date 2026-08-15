from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timezone
import inspect
from pathlib import Path
from typing import Any

from backend.app.domain import ArtifactKind


@dataclass(frozen=True, slots=True)
class LegacyArtifactRead:
    kind: ArtifactKind
    content: str
    provenance: str
    artifact_id: str | None = None
    source_document_id: str | None = None


class ArtifactStore:
    """Read and legacy-note adapter shared by the compatibility routes."""

    def __init__(
        self,
        work_factory: Callable[[], Any],
        *,
        read_mode: str = "prefer_new",
        legacy_markdown_root: Path | str | None = None,
        has_pdf: Callable[[str], bool | Awaitable[bool]] | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if read_mode not in {"legacy", "prefer_new"}:
            raise ValueError("read_mode must be legacy or prefer_new")
        self._work_factory = work_factory
        self._read_mode = read_mode
        self._legacy_markdown_root = (
            Path(legacy_markdown_root).expanduser().resolve()
            if legacy_markdown_root is not None
            else None
        )
        self._has_pdf = has_pdf or (lambda _paper_id: False)
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    async def read(
        self,
        paper_id: str,
        kind: ArtifactKind | str,
    ) -> LegacyArtifactRead:
        normalized = ArtifactKind(kind)
        if normalized not in {ArtifactKind.EXPLAINER, ArtifactKind.TRANSLATION}:
            raise ValueError("legacy route supports only explainer and translation")
        async with self._work_factory() as work:
            if self._read_mode == "prefer_new":
                artifact = await work.artifacts.find_ready_for_paper(paper_id, normalized)
                if artifact is not None and artifact.content:
                    return LegacyArtifactRead(
                        kind=normalized,
                        content=artifact.content,
                        provenance="new",
                        artifact_id=artifact.id,
                        source_document_id=artifact.source_document_id,
                    )
            legacy = await work.artifacts.read_legacy(paper_id, normalized)
        if isinstance(legacy, str) and legacy:
            return LegacyArtifactRead(normalized, legacy, "legacy")
        if normalized is ArtifactKind.EXPLAINER:
            fallback = self._read_markdown_fallback(paper_id)
            return LegacyArtifactRead(normalized, fallback, "legacy-file" if fallback != "*(暂无讲解)*" else "placeholder")
        return LegacyArtifactRead(normalized, "", "legacy")

    async def read_content(self, paper_id: str, kind: ArtifactKind | str) -> str:
        return (await self.read(paper_id, kind)).content

    async def read_note(self, paper_id: str) -> str:
        async with self._work_factory() as work:
            content = await work.papers.get_note(paper_id)
        return content or ""

    async def write_note(self, paper_id: str, content: object) -> None:
        rendered = "" if content is None else str(content)
        now = self._clock().astimezone(timezone.utc).isoformat()
        async with self._work_factory() as work:
            if not await work.papers.exists(paper_id):
                # Let SQLite's foreign-key boundary produce the same safe
                # missing-paper failure as the Node adapter.
                await work.papers.set_note(paper_id, rendered, updated_at=now)
            else:
                await work.papers.set_note(paper_id, rendered, updated_at=now)
            await work.commit()

    async def title_translation_status(self) -> dict[str, object]:
        async with self._work_factory() as work:
            rows = await work.papers.list_missing_title_translations()
        return {"pending": len(rows), "running": False}

    async def explain_batch_status(self) -> dict[str, int]:
        async with self._work_factory() as work:
            rows = await work.papers.list_missing_explainers()
        with_pdf = 0
        for row in rows:
            value = self._has_pdf(str(row.get("id") or ""))
            if inspect.isawaitable(value):
                value = await value
            if bool(value):
                with_pdf += 1
        return {
            "pending": len(rows),
            "withPdf": with_pdf,
            "noPdf": len(rows) - with_pdf,
        }

    def _read_markdown_fallback(self, paper_id: str) -> str:
        if self._legacy_markdown_root is None:
            return "*(暂无讲解)*"
        candidate = (self._legacy_markdown_root / f"{paper_id}.md").resolve()
        try:
            candidate.relative_to(self._legacy_markdown_root)
            if candidate.is_file():
                return candidate.read_text(encoding="utf-8")
        except (OSError, UnicodeError, ValueError):
            pass
        return "*(暂无讲解)*"


__all__ = ["ArtifactStore", "LegacyArtifactRead"]
