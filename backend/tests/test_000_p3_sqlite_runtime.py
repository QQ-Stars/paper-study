from __future__ import annotations

from contextlib import closing
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


_SQLITE_DLL_HANDLE = None
if (
    os.name == "nt"
    and hasattr(os, "add_dll_directory")
    and os.environ.get("P3_SQLITE_DLL_DIR")
):
    _SQLITE_DLL_HANDLE = os.add_dll_directory(os.environ["P3_SQLITE_DLL_DIR"])

import sqlite3

from backend.tests.support.p1_database import (
    ALEMBIC_CONFIG_PATH,
    REPOSITORY_ROOT,
    create_legacy_database,
    run_alembic,
)


class P3SqliteRuntimeTests(unittest.TestCase):
    def test_frozen_trigram_tokenizer_is_available_before_full_suite(self) -> None:
        with closing(sqlite3.connect(":memory:")) as connection:
            connection.execute(
                "CREATE VIRTUAL TABLE p3_tokenizer_probe USING fts5("
                "content, tokenize='trigram case_sensitive 0 remove_diacritics 1')"
            )
            connection.execute(
                "INSERT INTO p3_tokenizer_probe(content) VALUES(?)",
                ("这是一个机器学习模型",),
            )
            count = connection.execute(
                "SELECT count(*) FROM p3_tokenizer_probe "
                "WHERE p3_tokenizer_probe MATCH ?",
                ("机器学习",),
            ).fetchone()[0]
        self.assertEqual(1, count)

    def test_alembic_cli_loads_the_frozen_sqlite_runtime_before_migration(self) -> None:
        with tempfile.TemporaryDirectory(prefix="study-app-p3-alembic-runtime-") as temp_dir:
            database_path = Path(temp_dir) / "database" / "app.db"
            create_legacy_database(database_path)
            run_alembic(database_path, "20260807_02")
            environment = os.environ.copy()
            environment["DB_PATH"] = str(database_path.resolve(strict=True))

            completed = subprocess.run(
                [
                    sys.executable,
                    "-B",
                    "-m",
                    "alembic",
                    "-c",
                    str(ALEMBIC_CONFIG_PATH),
                    "upgrade",
                    "20260807_03",
                ],
                cwd=REPOSITORY_ROOT,
                env=environment,
                capture_output=True,
                text=True,
                check=False,
            )

            self.assertEqual(
                0,
                completed.returncode,
                msg=f"stdout:\n{completed.stdout}\nstderr:\n{completed.stderr}",
            )
            with closing(sqlite3.connect(database_path)) as connection:
                self.assertEqual(
                    [("20260807_03",)],
                    connection.execute(
                        "SELECT version_num FROM alembic_version"
                    ).fetchall(),
                )


if __name__ == "__main__":
    unittest.main()
