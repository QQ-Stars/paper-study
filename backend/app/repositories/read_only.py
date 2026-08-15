from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
import sqlite3
from typing import Iterator, Literal


ArtifactKind = Literal["explainer", "translation"]
SourceMode = Literal["native", "ocr"]


@dataclass(frozen=True, slots=True)
class SelectedArtifact:
    content: str


@dataclass(frozen=True, slots=True)
class SourceDocumentSummary:
    identifier: str
    status: str
    updated_at: str
    error_code: str | None


class McpReadRepository:
    """Small read-only query boundary used by the application MCP adapter."""

    def __init__(self, database_path: str | Path) -> None:
        self._database_path = Path(database_path).expanduser().resolve()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(
            self._database_path.as_uri() + "?mode=ro",
            uri=True,
        )
        try:
            connection.execute("PRAGMA query_only=ON")
            connection.row_factory = sqlite3.Row
            yield connection
        finally:
            connection.close()

    def paper_exists(self, paper_id: str) -> bool:
        with self.connect() as connection:
            return (
                connection.execute(
                    "SELECT 1 FROM papers WHERE id=?",
                    (paper_id,),
                ).fetchone()
                is not None
            )

    def legacy_artifact_content(
        self,
        paper_id: str,
        kind: ArtifactKind,
    ) -> str | None:
        with self.connect() as connection:
            if kind == "explainer":
                row = connection.execute(
                    "SELECT explainer AS content FROM papers WHERE id=?",
                    (paper_id,),
                ).fetchone()
            else:
                row = connection.execute(
                    "SELECT content FROM translations WHERE paper_id=?",
                    (paper_id,),
                ).fetchone()
        return None if row is None else row["content"]

    def selected_artifact(
        self,
        paper_id: str,
        kind: ArtifactKind,
    ) -> SelectedArtifact | None:
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT artifact.content
                FROM generated_artifacts AS artifact
                JOIN document_sources AS source
                  ON source.id = artifact.source_document_id
                WHERE artifact.paper_id = ?
                  AND artifact.kind = ?
                  AND artifact.status = 'ready'
                  AND artifact.stale_at IS NULL
                  AND source.status = 'ready'
                  AND source.stale_at IS NULL
                ORDER BY artifact.updated_at DESC, artifact.created_at DESC,
                         artifact.id DESC
                LIMIT 1
                """,
                (paper_id, kind),
            ).fetchone()
        if row is None:
            return None
        content = row["content"]
        if not isinstance(content, str):
            raise ValueError("ready generated artifact content must be text")
        return SelectedArtifact(content=content)

    def source_document_summaries(
        self,
        paper_id: str,
    ) -> dict[SourceMode, SourceDocumentSummary | None]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT id, mode, status, updated_at, error_code
                FROM document_sources
                WHERE paper_id = ? AND mode IN ('native', 'ocr')
                ORDER BY mode ASC, updated_at DESC, id DESC
                """,
                (paper_id,),
            ).fetchall()
        result: dict[SourceMode, SourceDocumentSummary | None] = {
            "native": None,
            "ocr": None,
        }
        allowed_statuses = {
            "queued", "running", "ready", "failed", "stale", "cancelled"
        }
        for row in rows:
            mode = row["mode"]
            if mode not in result or result[mode] is not None:
                continue
            identifier = row["id"]
            status = row["status"]
            updated_at = row["updated_at"]
            error_code = row["error_code"]
            if (
                not isinstance(identifier, str)
                or not identifier
                or status not in allowed_statuses
                or not isinstance(updated_at, str)
                or not updated_at
                or (error_code is not None and not isinstance(error_code, str))
            ):
                raise ValueError("invalid source document summary")
            result[mode] = SourceDocumentSummary(
                identifier=identifier,
                status=status,
                updated_at=updated_at,
                error_code=error_code,
            )
        return result
