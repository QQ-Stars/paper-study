"""Runtime repair for the P3 FTS5 trigram index.

The P3 migration creates ``document_chunks_fts`` with the richest trigram
tokenizer the *migrating* SQLite runtime supports (``remove_diacritics 1``
needs SQLite >= 3.45).  A database migrated under a newer runtime therefore
cannot be opened by an older bundled runtime: any statement that compiles
the FTS maintenance triggers -- including the ``DELETE FROM papers``
foreign-key cascade -- aborts with ``error in tokenizer constructor`` and
surfaced as HTTP 500 on ``POST /api/delete``.

This module detects the mismatch and rebuilds the virtual table with the
best tokenizer the *current* runtime can construct, preserving the
external-content contract (``content='document_chunks'``) and the three
maintenance triggers.  It is a no-op when the stored tokenizer already works
or when the FTS table does not exist yet (pre-P3 databases).
"""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path

FTS_TABLE = "document_chunks_fts"
CONTENT_TABLE = "document_chunks"

# Sanctioned by migration 20260807_03: preferred first, fallback second.
SANCTIONED_TOKENIZERS = (
    "trigram case_sensitive 0 remove_diacritics 1",
    "trigram case_sensitive 0",
)
_TRIGGER_NAMES = (
    "document_chunks_fts_ai",
    "document_chunks_fts_ad",
    "document_chunks_fts_au",
)
# Shadow tables are plain tables; the virtual table itself cannot be
# DROPped while its tokenizer is unconstructible, so the repair purges the
# sqlite_master entries directly.
_SHADOW_TABLES = (
    "document_chunks_fts_config",
    "document_chunks_fts_data",
    "document_chunks_fts_docsize",
    "document_chunks_fts_idx",
)
_TOKENIZE_OPTION = re.compile(r"tokenize\s*=\s*'([^']*)'", re.IGNORECASE)


def _probe(connection: sqlite3.Connection, tokenizer: str) -> bool:
    """Return True when the runtime can construct the given tokenizer."""
    try:
        connection.execute(
            "CREATE VIRTUAL TABLE temp.fts_runtime_probe USING fts5("
            f"heading_path, content, tokenize='{tokenizer}')"
        )
        return True
    except sqlite3.Error:
        return False
    finally:
        try:
            connection.execute("DROP TABLE IF EXISTS temp.fts_runtime_probe")
        except sqlite3.Error:
            pass


def supported_tokenizer(connection: sqlite3.Connection) -> str | None:
    for tokenizer in SANCTIONED_TOKENIZERS:
        if _probe(connection, tokenizer):
            return tokenizer
    return None


def stored_tokenizer(connection: sqlite3.Connection) -> str | None:
    row = connection.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name=?",
        (FTS_TABLE,),
    ).fetchone()
    if row is None or not row[0]:
        return None
    match = _TOKENIZE_OPTION.search(row[0])
    return match.group(1).strip() if match else None


def ensure_fts_runtime(database: Path) -> bool:
    """Rebuild ``document_chunks_fts`` when its tokenizer is not constructible.

    Returns True when a repair was performed.  The function manages its own
    connections: after purging the broken virtual table via
    ``PRAGMA writable_schema`` the schema must be reloaded on a fresh
    connection before the replacement table can be created.
    """

    connection = sqlite3.connect(database)
    try:
        stored = stored_tokenizer(connection)
        if stored is None:
            return False
        if _probe(connection, stored):
            return False
        replacement = supported_tokenizer(connection)
        if replacement is None:
            raise sqlite3.OperationalError(
                "FTS5_TRIGRAM_UNAVAILABLE: current SQLite runtime provides no "
                "supported trigram tokenizer"
            )
        trigger_sql = [
            str(row[0])
            for row in connection.execute(
                "SELECT sql FROM sqlite_master WHERE type='trigger' AND name IN (?,?,?)",
                _TRIGGER_NAMES,
            ).fetchall()
            if row[0]
        ]
        for name in _TRIGGER_NAMES:
            connection.execute(f"DROP TRIGGER IF EXISTS {name}")
        _purge_fts_objects(connection)
        connection.commit()
    finally:
        connection.close()

    connection = sqlite3.connect(database)
    try:
        connection.execute(
            f"CREATE VIRTUAL TABLE {FTS_TABLE} USING fts5("
            f"heading_path, content, content='{CONTENT_TABLE}', "
            f"content_rowid='rowid', tokenize='{replacement}')"
        )
        connection.execute(f"INSERT INTO {FTS_TABLE}({FTS_TABLE}) VALUES('rebuild')")
        for sql in trigger_sql:
            connection.execute(sql)
        connection.commit()
    finally:
        connection.close()
    return True


def _purge_fts_objects(connection: sqlite3.Connection) -> None:
    """Remove the broken virtual table and its shadows from sqlite_master.

    ``DROP TABLE`` on an FTS5 virtual table constructs its tokenizer, which
    is exactly what fails on a mismatched runtime; purging the catalog rows
    (shadow tables are plain tables) bypasses the module.
    """

    connection.execute("PRAGMA writable_schema=ON")
    try:
        connection.execute(
            "DELETE FROM sqlite_master WHERE type='table' AND name IN (?,?,?,?,?)",
            (FTS_TABLE, *_SHADOW_TABLES),
        )
    finally:
        connection.execute("PRAGMA writable_schema=OFF")
