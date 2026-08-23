import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from agent import citegraph


def _connect(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    return connection


class CitationGraphTest(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tempdir.name) / "citegraph.db"
        connection = _connect(self.db_path)
        connection.executescript(
            """
            CREATE TABLE papers (
                id TEXT PRIMARY KEY,
                s2_id TEXT,
                arxiv_id TEXT,
                doi TEXT,
                title TEXT NOT NULL,
                title_norm TEXT
            );
            CREATE TABLE cite_edges (
                src_id TEXT NOT NULL REFERENCES papers(id) ON DELETE CASCADE,
                dst_id TEXT NOT NULL REFERENCES papers(id) ON DELETE CASCADE,
                PRIMARY KEY (src_id, dst_id)
            );
            INSERT INTO papers(id, s2_id, title, title_norm)
            VALUES
                ('paper-1', 'S2-1', 'Paper One', 'paperone'),
                ('paper-2', 'S2-2', 'Paper Two', 'papertwo');
            INSERT INTO cite_edges(src_id, dst_id) VALUES('paper-1', 'paper-2');
            """
        )
        connection.commit()
        connection.close()

    def tearDown(self):
        self.tempdir.cleanup()

    def test_failed_rebuild_preserves_existing_edges(self):
        def connect():
            return _connect(self.db_path)

        with (
            mock.patch.object(citegraph.db, "connect", side_effect=connect),
            mock.patch.object(
                citegraph,
                "_request_reference_batch",
                side_effect=RuntimeError("network down"),
            ),
        ):
            result = citegraph.build_edges()

        connection = _connect(self.db_path)
        edges = connection.execute(
            "SELECT src_id, dst_id FROM cite_edges ORDER BY src_id, dst_id"
        ).fetchall()
        connection.close()
        self.assertFalse(result["ok"])
        self.assertIn("旧图谱已保留", result["error"])
        self.assertEqual([tuple(row) for row in edges], [("paper-1", "paper-2")])

    def test_rebuild_fetches_papers_in_batches_and_replaces_edges_atomically(self):
        connection = _connect(self.db_path)
        connection.execute("DELETE FROM cite_edges")
        connection.execute(
            "INSERT INTO cite_edges(src_id, dst_id) VALUES('paper-2', 'paper-1')"
        )
        connection.commit()
        connection.close()

        def connect():
            return _connect(self.db_path)

        batch_response = [
            {
                "paperId": "S2-1",
                "references": [
                    {
                        "paperId": "S2-2",
                        "title": "Paper Two",
                        "externalIds": {},
                    }
                ],
            },
            {"paperId": "S2-2", "references": []},
        ]
        with (
            mock.patch.object(citegraph.db, "connect", side_effect=connect),
            mock.patch.object(
                citegraph,
                "_request_reference_batch",
                return_value=batch_response,
            ) as request_batch,
            mock.patch.object(
                citegraph.util,
                "get",
                side_effect=AssertionError("per-paper requests must not be used"),
            ),
        ):
            citegraph.build_edges()

        connection = _connect(self.db_path)
        edges = connection.execute(
            "SELECT src_id, dst_id FROM cite_edges ORDER BY src_id, dst_id"
        ).fetchall()
        connection.close()

        request_batch.assert_called_once_with(["S2-1", "S2-2"])
        self.assertEqual([tuple(row) for row in edges], [("paper-1", "paper-2")])

    def test_later_batch_failure_does_not_commit_partial_edges(self):
        def connect():
            return _connect(self.db_path)

        first_batch = [
            {
                "paperId": "S2-1",
                "references": [
                    {
                        "paperId": "S2-2",
                        "title": "Paper Two",
                        "externalIds": {},
                    }
                ],
            }
        ]
        with (
            mock.patch.object(citegraph.db, "connect", side_effect=connect),
            mock.patch.object(citegraph, "BATCH_SIZE", 1),
            mock.patch.object(
                citegraph,
                "_request_reference_batch",
                side_effect=[first_batch, RuntimeError("second batch down")],
            ),
        ):
            result = citegraph.build_edges()

        connection = _connect(self.db_path)
        edges = connection.execute(
            "SELECT src_id, dst_id FROM cite_edges ORDER BY src_id, dst_id"
        ).fetchall()
        connection.close()

        self.assertFalse(result["ok"])
        self.assertEqual([tuple(row) for row in edges], [("paper-1", "paper-2")])

    def test_batch_request_retries_rate_limit_before_returning_payload(self):
        class Response:
            def __init__(self, status_code, payload=None, headers=None):
                self.status_code = status_code
                self._payload = payload
                self.headers = headers or {}

            def raise_for_status(self):
                if self.status_code >= 400:
                    raise RuntimeError(f"HTTP {self.status_code}")

            def json(self):
                return self._payload

        responses = [
            Response(429, headers={"retry-after": "0"}),
            Response(200, payload=[{"paperId": "S2-1", "references": []}]),
        ]
        with (
            mock.patch.object(citegraph.httpx, "post", side_effect=responses),
            mock.patch.object(citegraph.time, "sleep") as sleep,
        ):
            result = citegraph._request_reference_batch(["S2-1"])

        self.assertEqual(result, [{"paperId": "S2-1", "references": []}])
        sleep.assert_called_once_with(2.0)


if __name__ == "__main__":
    unittest.main()
