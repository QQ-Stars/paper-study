from __future__ import annotations

from contextlib import closing, contextmanager
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import tempfile
from typing import Iterator

from alembic import command
from alembic.config import Config


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
ALEMBIC_CONFIG_PATH = REPOSITORY_ROOT / "backend" / "alembic.ini"
P1_TABLES = (
    "document_sources",
    "generated_artifacts",
    "processing_jobs",
    "document_chunks",
    "obsidian_exports",
)


def _quote_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def create_legacy_database(database_path: Path) -> None:
    database_path.parent.mkdir(parents=True, exist_ok=False)
    with closing(sqlite3.connect(database_path)) as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.executescript(
            (REPOSITORY_ROOT / "db" / "schema.sql").read_text(encoding="utf-8")
        )
        _apply_current_startup_mutations(connection)
        _seed_every_legacy_table(connection)
        connection.commit()


def _apply_current_startup_mutations(connection: sqlite3.Connection) -> None:
    ingest_columns = {
        str(row[1]) for row in connection.execute("PRAGMA table_info(ingest_jobs)")
    }
    for name, declaration in (
        ("only_a", "INTEGER DEFAULT 0"),
        ("queries", "TEXT"),
        ("schedule_id", "INTEGER"),
    ):
        if name not in ingest_columns:
            connection.execute(f"ALTER TABLE ingest_jobs ADD COLUMN {name} {declaration}")
    paper_columns = {
        str(row[1]) for row in connection.execute("PRAGMA table_info(papers)")
    }
    if "title_zh" not in paper_columns:
        connection.execute("ALTER TABLE papers ADD COLUMN title_zh TEXT")


def _seed_every_legacy_table(connection: sqlite3.Connection) -> None:
    papers = (
        ("paper-1", "manual", "Seed One", "seedone"),
        ("paper-2", "manual", "Seed Two", "seedtwo"),
    )
    connection.executemany(
        "INSERT INTO papers(id,source,title,title_norm) VALUES(?,?,?,?)",
        papers,
    )
    connection.execute(
        "INSERT INTO progress(paper_id,status,updated_at) VALUES(?,?,?)",
        ("paper-1", "已理解", "2026-08-01T00:00:00Z"),
    )
    connection.execute(
        "INSERT INTO paper_reviews(paper_id,started_at,current_step,completed_steps,next_due_at,updated_at) "
        "VALUES(?,?,?,?,?,?)",
        (
            "paper-1",
            "2026-08-01",
            1,
            0,
            "2026-08-02",
            "2026-08-01T00:00:00Z",
        ),
    )
    connection.execute("INSERT INTO notes(paper_id,content) VALUES('paper-1','note')")
    connection.execute("INSERT INTO favorites(paper_id) VALUES('paper-1')")
    connection.execute(
        "INSERT INTO translations(paper_id,content) VALUES('paper-1','translation')"
    )
    connection.execute(
        "INSERT INTO paper_vectors(paper_id,dim,vector) VALUES(?,?,?)",
        ("paper-1", 2, b"\x00\x01"),
    )
    connection.execute("INSERT INTO cite_edges(src_id,dst_id) VALUES('paper-1','paper-2')")
    cursor = connection.execute(
        "INSERT INTO ingest_jobs(query,status,only_a,queries,schedule_id) VALUES(?,?,?,?,?)",
        ("seed", "done", 1, '["seed"]', None),
    )
    job_id = int(cursor.lastrowid)
    connection.execute(
        "INSERT INTO job_candidates(job_id,title_norm,data,status) VALUES(?,?,?,?)",
        (job_id, "candidate", "{}", "pending"),
    )
    connection.execute(
        "INSERT INTO job_schedules(query,sources,enabled) VALUES(?,?,?)",
        ("seed", '["arxiv"]', 1),
    )
    connection.execute("INSERT INTO schema_migrations(version) VALUES(1)")


def legacy_count_hashes(connection: sqlite3.Connection) -> dict[str, tuple[int, str]]:
    excluded = {*P1_TABLES, "alembic_version"}
    table_names = [
        str(row[0])
        for row in connection.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        )
        if str(row[0]) not in excluded
    ]
    result: dict[str, tuple[int, str]] = {}
    for table_name in table_names:
        quoted_table = _quote_identifier(table_name)
        columns = [
            str(row[1])
            for row in connection.execute(f"PRAGMA table_xinfo({quoted_table})")
        ]
        order = ",".join(_quote_identifier(column) for column in columns)
        rows = connection.execute(
            f"SELECT * FROM {quoted_table} ORDER BY {order}"
        ).fetchall()
        encoded_rows = [
            [_canonical_cell(value) for value in row]
            for row in rows
        ]
        payload = json.dumps(
            {"columns": columns, "rows": encoded_rows},
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        result[table_name] = (len(rows), hashlib.sha256(payload).hexdigest())
    return result


def _canonical_cell(value: object) -> dict[str, object]:
    if value is None:
        return {"type": "null", "value": None}
    if isinstance(value, bytes):
        return {"type": "bytes", "value": value.hex()}
    return {"type": type(value).__name__, "value": value}


def run_alembic(database_path: Path, revision: str) -> None:
    previous = os.environ.get("DB_PATH")
    os.environ["DB_PATH"] = str(database_path.resolve(strict=True))
    try:
        configuration = Config(str(ALEMBIC_CONFIG_PATH))
        command.upgrade(configuration, revision) if revision != "base" else command.downgrade(
            configuration, "base"
        )
    finally:
        if previous is None:
            os.environ.pop("DB_PATH", None)
        else:
            os.environ["DB_PATH"] = previous


@contextmanager
def temporary_legacy_database() -> Iterator[Path]:
    with tempfile.TemporaryDirectory(prefix="study-app-p1-migration-") as temp_dir:
        database_path = Path(temp_dir) / "legacy" / "app.db"
        create_legacy_database(database_path)
        yield database_path


@contextmanager
def temporary_restore_database() -> Iterator[tuple[Path, Path]]:
    """Create a disposable database at the migration restore-check seam."""
    with tempfile.TemporaryDirectory(prefix="study-app-restore-check-") as temp_dir:
        restore_root = Path(temp_dir) / "restore-checks"
        database_path = restore_root / "restore-validation-fixture" / "app.db"
        create_legacy_database(database_path)
        yield database_path, restore_root
