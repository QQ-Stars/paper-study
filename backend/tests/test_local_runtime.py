from __future__ import annotations

from contextlib import closing
from pathlib import Path
import shutil
import sqlite3
import tempfile
import unittest

from backend.app.cli.local_runtime import ensure_database


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
SCHEMA_REVISION = "20260826_01"


class LocalRuntimeDatabaseTests(unittest.TestCase):
    def test_fresh_clone_database_is_initialized_and_seeded(self) -> None:
        with tempfile.TemporaryDirectory(prefix="study-app-local-runtime-") as raw:
            root = Path(raw)
            (root / "data").mkdir()
            (root / "notes").mkdir()
            (root / "paper").mkdir()
            (root / "db").mkdir()
            (root / "backend").mkdir()
            shutil.copy2(REPOSITORY_ROOT / "data" / "papers.json", root / "data" / "papers.json")
            shutil.copy2(REPOSITORY_ROOT / "data" / "progress.json", root / "data" / "progress.json")
            shutil.copy2(REPOSITORY_ROOT / "db" / "schema.sql", root / "db" / "schema.sql")
            shutil.copytree(REPOSITORY_ROOT / "backend" / "migrations", root / "backend" / "migrations")
            shutil.copy2(REPOSITORY_ROOT / "backend" / "alembic.ini", root / "backend" / "alembic.ini")
            database = root / "data" / "app.db"

            result = ensure_database(root, database)

            self.assertTrue(result.created)
            self.assertEqual(SCHEMA_REVISION, result.schema_revision)
            with closing(sqlite3.connect(database)) as connection:
                self.assertGreater(connection.execute("SELECT COUNT(*) FROM papers").fetchone()[0], 0)
                self.assertEqual(
                    [(SCHEMA_REVISION,)],
                    connection.execute("SELECT version_num FROM alembic_version").fetchall(),
                )

    def test_existing_database_is_never_reseeded(self) -> None:
        with tempfile.TemporaryDirectory(prefix="study-app-existing-runtime-") as raw:
            root = Path(raw)
            (root / "data").mkdir()
            database = root / "data" / "app.db"
            shutil.copy2(REPOSITORY_ROOT / "data" / "papers.json", root / "data" / "papers.json")
            shutil.copy2(REPOSITORY_ROOT / "data" / "progress.json", root / "data" / "progress.json")
            (root / "db").mkdir()
            (root / "backend").mkdir()
            shutil.copy2(REPOSITORY_ROOT / "db" / "schema.sql", root / "db" / "schema.sql")
            shutil.copytree(REPOSITORY_ROOT / "backend" / "migrations", root / "backend" / "migrations")
            shutil.copy2(REPOSITORY_ROOT / "backend" / "alembic.ini", root / "backend" / "alembic.ini")
            ensure_database(root, database)
            with closing(sqlite3.connect(database)) as connection:
                connection.execute(
                    "INSERT INTO papers(id,source,title,title_norm) VALUES(?,?,?,?)",
                    ("sentinel", "test", "Sentinel", "sentinel"),
                )
                connection.commit()
                before = connection.execute("SELECT COUNT(*) FROM papers").fetchone()[0]

            result = ensure_database(root, database)

            self.assertFalse(result.created)
            with closing(sqlite3.connect(database)) as connection:
                self.assertEqual(before, connection.execute("SELECT COUNT(*) FROM papers").fetchone()[0])


if __name__ == "__main__":
    unittest.main()
