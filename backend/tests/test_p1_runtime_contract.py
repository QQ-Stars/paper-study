from __future__ import annotations

import contextlib
import dataclasses
import importlib.metadata
import io
import os
from pathlib import Path
import re
import sys
import tempfile
import unittest
from unittest import mock


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
LOCK_PATH = REPOSITORY_ROOT / "requirements.txt"

DIRECT_DEPENDENCIES = {
    "httpx": "0.28.1",
    "feedparser": "6.0.12",
    "openai": "2.41.0",
    "anthropic": "0.107.1",
    "pydantic": "2.13.4",
    "python-dotenv": "1.2.2",
    "tenacity": "9.1.4",
    "pymupdf": "1.27.2.3",
    "pymupdf4llm": "1.27.2.3",
    "model2vec": "0.8.2",
    "mcp": "1.27.2",
    "sqlalchemy": "2.0.43",
    "alembic": "1.16.5",
    "aiosqlite": "0.21.0",
    "fastapi": "0.116.1",
    "uvicorn": "0.49.0",
    "anyio": "4.13.0",
    "keyring": "25.6.0",
}


def _normalized_distribution_name(value: str) -> str:
    return re.sub(r"[-_.]+", "-", value).lower()


def _locked_requirement_stanzas(lock_text: str) -> dict[str, str]:
    stanzas: dict[str, str] = {}
    lines = lock_text.splitlines()
    index = 0
    while index < len(lines):
        stripped = lines[index].strip()
        if not stripped or stripped.startswith("#") or lines[index][0].isspace():
            index += 1
            continue

        stanza_lines = [lines[index]]
        while stanza_lines[-1].rstrip().endswith("\\"):
            index += 1
            if index >= len(lines):
                raise AssertionError("requirements.txt ends inside a continuation stanza")
            stanza_lines.append(lines[index])

        stanza = "\n".join(stanza_lines)
        match = re.match(r"([A-Za-z0-9_.-]+)==([^\s\\;]+)", stripped)
        if match:
            name = _normalized_distribution_name(match.group(1))
            if name in stanzas:
                raise AssertionError(f"duplicate locked distribution: {name}")
            stanzas[name] = stanza
        index += 1
    return stanzas


class P1RuntimeContractTests(unittest.TestCase):
    def test_supported_runtime_and_exact_data_dependency_versions(self) -> None:
        import aiosqlite
        import alembic
        import fastapi
        import keyring
        import sqlalchemy

        self.assertGreaterEqual(sys.version_info, (3, 10))
        self.assertTrue(sqlalchemy.__version__)
        self.assertTrue(alembic.__version__)
        self.assertTrue(aiosqlite.__version__)
        self.assertTrue(fastapi.__version__)
        self.assertIsNotNone(keyring.get_keyring())

        for distribution, expected_version in DIRECT_DEPENDENCIES.items():
            with self.subTest(distribution=distribution):
                self.assertEqual(
                    expected_version,
                    importlib.metadata.version(distribution),
                )

        lock_text = LOCK_PATH.read_text(encoding="utf-8")
        self.assertNotRegex(lock_text, r"(?m)^\s*[^#\r\n]*(?:>=|\*|\s@\s|git\+|-e\s)")
        stanzas = _locked_requirement_stanzas(lock_text)
        for distribution, expected_version in DIRECT_DEPENDENCIES.items():
            normalized = _normalized_distribution_name(distribution)
            with self.subTest(locked_distribution=normalized):
                self.assertIn(normalized, stanzas)
                self.assertRegex(
                    stanzas[normalized],
                    rf"(?m)^{re.escape(distribution)}=={re.escape(expected_version)}(?:\s|\\|$)",
                )

        digest_pattern = re.compile(r"--hash=sha256:[0-9a-f]{64}(?:\s|\\|$)")
        self.assertTrue(stanzas, "requirements.txt must contain locked distributions")
        for distribution, stanza in stanzas.items():
            with self.subTest(has_artifact_digest=distribution):
                self.assertRegex(stanza, digest_pattern)

    def test_database_settings_resolve_one_sqlite_file_without_creating_it(self) -> None:
        from backend.app.config import DatabaseSettings

        with tempfile.TemporaryDirectory(prefix="study-app-settings-") as temp_dir:
            root = Path(temp_dir)
            database_path = root / "existing.sqlite3"
            database_path.write_bytes(b"SQLite format 3\x00")
            before = {path.name for path in root.iterdir()}
            stdout = io.StringIO()
            stderr = io.StringIO()

            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                injected = DatabaseSettings(database_path)
                with mock.patch.dict(os.environ, {"DB_PATH": str(database_path)}):
                    from_environment = DatabaseSettings()

            self.assertEqual(database_path.resolve(), injected.database_path)
            self.assertEqual(injected, from_environment)
            self.assertEqual(before, {path.name for path in root.iterdir()})
            self.assertEqual("", stdout.getvalue())
            self.assertEqual("", stderr.getvalue())
            for suffix in ("-wal", "-shm", "-journal"):
                self.assertFalse(Path(f"{database_path}{suffix}").exists())

    def test_database_settings_reject_directory_missing_parent_and_non_file_target(self) -> None:
        from backend.app.config import DatabaseSettings

        with tempfile.TemporaryDirectory(prefix="study-app-settings-invalid-") as temp_dir:
            root = Path(temp_dir)
            cases = {
                "directory target": root,
                "missing file": root / "missing.sqlite3",
                "missing parent": root / "missing-parent" / "app.sqlite3",
            }
            for label, target in cases.items():
                with self.subTest(label=label), self.assertRaises((ValueError, OSError)):
                    DatabaseSettings(target)

        with mock.patch.dict(os.environ, {}, clear=True):
            with self.assertRaises((ValueError, OSError)):
                DatabaseSettings()

    def test_database_settings_are_immutable_and_secret_free(self) -> None:
        from backend.app.config import DatabaseSettings

        with tempfile.TemporaryDirectory(prefix="study-app-settings-frozen-") as temp_dir:
            database_path = Path(temp_dir) / "app.sqlite3"
            database_path.write_bytes(b"SQLite format 3\x00")
            settings = DatabaseSettings(database_path)

        self.assertTrue(dataclasses.is_dataclass(settings))
        self.assertEqual(["database_path"], [field.name for field in dataclasses.fields(settings)])
        with self.assertRaises(dataclasses.FrozenInstanceError):
            settings.database_path = Path("replacement.sqlite3")
        rendered = repr(settings).lower()
        for forbidden in ("password", "secret", "token", "credential", "keyring"):
            self.assertNotIn(forbidden, rendered)


if __name__ == "__main__":
    unittest.main()
