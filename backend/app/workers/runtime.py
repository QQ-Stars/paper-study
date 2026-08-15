from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any


class ObsidianStartupReconciler:
    """Restore missed auto-export enqueue notifications after a restart."""

    def __init__(
        self,
        unit_of_work_factory: Callable[[], Any],
        *,
        settings_service: Any,
        library_queries: Any,
        job_service: Any,
        batch_size: int = 100,
        logger: Callable[[Mapping[str, object]], None] | None = None,
    ) -> None:
        if batch_size < 1:
            raise ValueError("batch_size must be positive")
        self._unit_of_work_factory = unit_of_work_factory
        self._settings_service = settings_service
        self._library_queries = library_queries
        self._job_service = job_service
        self._batch_size = batch_size
        self._logger = logger

    async def run(self) -> int:
        settings = await self._settings_service.obsidian()
        if not settings.enabled or not settings.auto_export:
            return 0

        rows = await self._library_queries.list_papers()
        paper_ids = sorted(
            row["id"]
            for row in rows
            if isinstance(row.get("id"), str) and row["id"]
        )
        candidates: list[str] = []
        for offset in range(0, len(paper_ids), self._batch_size):
            batch = paper_ids[offset : offset + self._batch_size]
            async with self._unit_of_work_factory() as work:
                for paper_id in batch:
                    artifact = None
                    for kind in ("explainer", "translation"):
                        artifact_id = await work.artifacts.get_head_artifact_id(
                            paper_id=paper_id,
                            kind=kind,
                        )
                        if artifact_id is None:
                            continue
                        candidate = await work.artifacts.get(artifact_id)
                        if (
                            candidate is not None
                            and candidate.status.value == "ready"
                            and candidate.paper_id == paper_id
                        ):
                            artifact = candidate
                            break
                    if artifact is None:
                        continue
                    source = await work.sources.get(artifact.source_document_id)
                    if (
                        source is not None
                        and source.status.value == "ready"
                        and source.paper_id == paper_id
                    ):
                        candidates.append(paper_id)

        created = 0
        for paper_id in candidates:
            try:
                result = await self._job_service.enqueue_export(
                    paper_id,
                    dry_run=False,
                )
                if not result.deduplicated:
                    created += 1
            except Exception:
                if self._logger is not None:
                    self._logger(
                        {
                            "event": "obsidian_startup_reconciliation_failed",
                            "paperId": paper_id,
                        }
                    )
        return created


__all__ = ["ObsidianStartupReconciler"]
