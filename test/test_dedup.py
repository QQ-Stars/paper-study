import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from agent import db, dedup


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


class DuplicateScanTest(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tempdir.name) / "dedup.db"
        connection = sqlite3.connect(self.db_path)
        connection.executescript(
            """
            CREATE TABLE papers (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                title_norm TEXT,
                year TEXT,
                venue TEXT
            );
            INSERT INTO papers(id, title, title_norm, year, venue) VALUES
                ('paper-a', 'Vision Transformer', 'visiontransformer', '2021', 'ICLR'),
                ('paper-b', 'Vision-Transformer', 'visiontransformer', '2022', 'CVPR'),
                ('paper-c', 'Unrelated Work', 'unrelatedwork', '2020', 'ACL');
            """
        )
        connection.commit()
        connection.close()

    def tearDown(self):
        self.tempdir.cleanup()

    def test_scan_returns_one_unique_pair_for_identical_normalized_titles(self):
        with mock.patch.object(db.config, "DB_PATH", str(self.db_path)):
            pairs = dedup.scan_duplicates()

        self.assertEqual(
            pairs,
            [
                {
                    "left": {
                        "id": "paper-a",
                        "title": "Vision Transformer",
                        "year": "2021",
                        "venue": "ICLR",
                    },
                    "right": {
                        "id": "paper-b",
                        "title": "Vision-Transformer",
                        "year": "2022",
                        "venue": "CVPR",
                    },
                    "similarity": 1.0,
                }
            ],
        )

    def test_scan_includes_a_normalized_title_contained_by_another(self):
        pairs = dedup.find_duplicate_pairs(
            [
                {
                    "id": "short",
                    "title": "Graph Neural Networks",
                    "year": "2019",
                    "venue": "NeurIPS",
                },
                {
                    "id": "survey",
                    "title": "Graph Neural Networks: A Survey",
                    "year": "2020",
                    "venue": "ACM Computing Surveys",
                },
                {
                    "id": "other",
                    "title": "Database Systems",
                    "year": None,
                    "venue": None,
                },
            ]
        )

        self.assertEqual(len(pairs), 1)
        self.assertEqual((pairs[0]["left"]["id"], pairs[0]["right"]["id"]), ("short", "survey"))
        self.assertEqual(pairs[0]["similarity"], 0.8444)

    def test_scan_includes_sequence_similarity_at_threshold_and_excludes_lower_scores(self):
        pairs = dedup.find_duplicate_pairs(
            [
                {"id": "base", "title": "Base", "title_norm": "abcdefghij"},
                {"id": "near", "title": "Near", "title_norm": "abcdefghix"},
                {"id": "far", "title": "Far", "title_norm": "abcdefgxyz"},
            ]
        )

        self.assertEqual(len(pairs), 1)
        self.assertEqual((pairs[0]["left"]["id"], pairs[0]["right"]["id"]), ("base", "near"))
        self.assertEqual(pairs[0]["similarity"], 0.9)

    def test_dup_scan_cli_writes_the_pair_list_as_json(self):
        completed = subprocess.run(
            [sys.executable, "-m", "agent", "dup-scan"],
            cwd=REPOSITORY_ROOT,
            env={
                **os.environ,
                "DB_PATH": str(self.db_path),
                "PYTHONIOENCODING": "utf-8",
            },
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        payload = json.loads(completed.stdout)
        self.assertEqual(len(payload), 1)
        self.assertEqual(payload[0]["left"]["id"], "paper-a")
        self.assertEqual(payload[0]["right"]["id"], "paper-b")


if __name__ == "__main__":
    unittest.main()
