from __future__ import annotations

from datetime import datetime
from typing import Protocol


class ObsidianAutoExportPort(Protocol):
    async def on_artifact_ready(
        self,
        paper_id: str,
        artifact_id: str,
        committed_at: datetime,
    ) -> None: ...


class NoopObsidianAutoExport:
    async def on_artifact_ready(
        self,
        paper_id: str,
        artifact_id: str,
        committed_at: datetime,
    ) -> None:
        del paper_id, artifact_id, committed_at


__all__ = ["NoopObsidianAutoExport", "ObsidianAutoExportPort"]
