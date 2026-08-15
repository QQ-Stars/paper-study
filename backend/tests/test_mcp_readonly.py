from __future__ import annotations

import hashlib
from pathlib import Path
import sqlite3
import unittest
from unittest.mock import patch

import numpy as np

from agent import db, embed, mcp_server
from backend.app.api.mcp import ApplicationMcpAdapter
from backend.app.repositories.read_only import McpReadRepository
from backend.tests.test_mcp_contract import mcp_fixture_database


def _file_snapshot(database_path: Path) -> dict[str, object]:
    stat = database_path.stat()
    sidecars: dict[str, tuple[int, str]] = {}
    for suffix in ("-wal", "-shm", "-journal"):
        path = Path(str(database_path) + suffix)
        if path.exists():
            sidecars[suffix] = (
                path.stat().st_size,
                hashlib.sha256(path.read_bytes()).hexdigest(),
            )
    return {
        "sha256": hashlib.sha256(database_path.read_bytes()).hexdigest(),
        "size": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
        "sidecars": sidecars,
    }


def _call_all_nine(target: object) -> None:
    target.search_papers(query="paper", limit=50)
    target.semantic_search("query", k=5)
    target.related_papers("p1", k=5)
    target.get_paper("p1")
    target.get_explainer("p1", max_chars=8)
    target.get_translation("p1", max_chars=8)
    target.list_due_reviews(today="2026-07-03")
    target.list_categories()
    target.library_overview()


class McpReadonlyTests(unittest.TestCase):
    def test_all_nine_tools_leave_database_bytes_mtime_and_sidecars_unchanged(
        self,
    ) -> None:
        with mcp_fixture_database() as database_path:
            before = _file_snapshot(database_path)
            adapter = ApplicationMcpAdapter(
                database_path,
                artifact_read_mode="prefer_new",
                ranker=embed.rank,
                has_pdf=lambda _row: False,
            )
            with (
                patch.object(
                    db,
                    "connect",
                    side_effect=AssertionError("writable MCP connection"),
                ) as writable_connect,
                patch.object(
                    db,
                    "ensure_vectors_table",
                    side_effect=AssertionError("MCP schema bootstrap"),
                ) as schema_bootstrap,
                patch.object(
                    embed,
                    "model",
                    side_effect=AssertionError("MCP model download"),
                ) as model_download,
                patch.object(
                    embed,
                    "_readonly_embed_texts",
                    return_value=np.asarray([[1.0, 0.0]], dtype="float32"),
                ),
            ):
                _call_all_nine(mcp_server)
                _call_all_nine(adapter)

            self.assertEqual(0, writable_connect.call_count)
            self.assertEqual(0, schema_bootstrap.call_count)
            self.assertEqual(0, model_download.call_count)
            self.assertEqual(before, _file_snapshot(database_path))

    def test_mcp_connection_rejects_write_and_reports_zero_total_changes(self) -> None:
        with mcp_fixture_database() as database_path:
            before = _file_snapshot(database_path)
            repository = McpReadRepository(database_path)
            with repository.connect() as connection:
                self.assertEqual(0, connection.total_changes)
                with self.assertRaises(sqlite3.OperationalError):
                    connection.execute(
                        "UPDATE papers SET title='changed' WHERE id='p1'"
                    )
                self.assertEqual(0, connection.total_changes)
            self.assertEqual(before, _file_snapshot(database_path))


if __name__ == "__main__":
    unittest.main()
