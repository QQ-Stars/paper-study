from __future__ import annotations

import argparse
import asyncio
from contextlib import closing
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import sys
from typing import Mapping, Sequence

import uvicorn
from alembic import command
from alembic.config import Config
from dotenv import load_dotenv

from backend.app.api.app import create_app
from backend.app.api.dependencies import ApiDependencies
from backend.app.bootstrap import (
    RolloutSettings,
    bootstrap,
    bootstrap_processing_worker,
)
from backend.app.config import DatabaseSettings
from backend.app.domain.context import EmbeddingProfile
from backend.app.providers.embeddings import Model2VecEmbeddingProvider
from backend.app.providers.legacy_p3 import legacy_p3_provider_factories
from backend.app.runtime import ApiSettings


SCHEMA_REVISION = "20260826_01"
REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
_TITLE_SEPARATOR = re.compile(r"[^a-z0-9\u4e00-\u9fa5]+")
_ARXIV_ID = re.compile(r"^(\d{4}\.\d{4,5})")


@dataclass(frozen=True, slots=True)
class DatabaseSetupResult:
    path: Path
    created: bool
    schema_revision: str
    backup_path: Path | None = None


def ensure_database(root: Path, database_path: Path) -> DatabaseSetupResult:
    """Create a fresh local database or migrate an existing one in place.

    Existing databases are never seeded. Before an actual schema upgrade, a
    SQLite online backup is written under ``data/backups``.
    """

    root = Path(root).resolve()
    database = Path(database_path).expanduser().resolve()
    database.parent.mkdir(parents=True, exist_ok=True)
    created = not database.exists()
    backup_path: Path | None = None
    if created:
        _create_seed_database(root, database)
    else:
        current = _read_schema_revision(database)
        if current != SCHEMA_REVISION:
            backup_path = _backup_database(database, root / "data" / "backups")
    try:
        _upgrade_database(root, database)
    except BaseException:
        if created:
            for suffix in ("", "-wal", "-shm", "-journal"):
                Path(f"{database}{suffix}").unlink(missing_ok=True)
        raise
    revision = _read_schema_revision(database)
    if revision != SCHEMA_REVISION:
        raise RuntimeError(
            f"database migration finished at {revision or 'missing'}, expected {SCHEMA_REVISION}"
        )
    return DatabaseSetupResult(database, created, revision, backup_path)


def _create_seed_database(root: Path, database: Path) -> None:
    schema = root / "db" / "schema.sql"
    if not schema.is_file():
        raise FileNotFoundError(f"database schema is missing: {schema}")
    with closing(sqlite3.connect(database)) as connection:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=5000")
        connection.executescript(schema.read_text(encoding="utf-8"))
        _seed_tracked_library(connection, root)
        connection.commit()


def _seed_tracked_library(connection: sqlite3.Connection, root: Path) -> None:
    papers_path = root / "data" / "papers.json"
    if not papers_path.is_file():
        return
    papers = json.loads(papers_path.read_text(encoding="utf-8"))
    if not isinstance(papers, list) or any(not isinstance(item, dict) for item in papers):
        raise ValueError("data/papers.json must contain an array of objects")
    paper_root = root / "paper"
    for item in papers:
        paper_id = str(item.get("id") or "").strip()
        title = str(item.get("title") or "").strip()
        filename = str(item.get("file") or "").strip()
        if not paper_id or not title or not filename:
            raise ValueError("tracked Paper seeds require id, title, and file")
        explainer_path = paper_root / f"{paper_id}.md"
        explainer = (
            explainer_path.read_text(encoding="utf-8")
            if explainer_path.is_file()
            else None
        )
        arxiv_match = _ARXIV_ID.match(paper_id)
        connection.execute(
            """
            INSERT INTO papers(
              id,source,arxiv_id,title,title_norm,venue,year,type,topic,
              order_no,pdf_path,explainer
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                paper_id,
                "seed",
                arxiv_match.group(1) if arxiv_match else None,
                title,
                _TITLE_SEPARATOR.sub("", title.lower()),
                item.get("venue"),
                str(item["year"]) if item.get("year") is not None else None,
                item.get("type"),
                item.get("topic") or None,
                item.get("order"),
                str(Path("paper") / filename),
                explainer,
            ),
        )

    progress_path = root / "data" / "progress.json"
    progress = (
        json.loads(progress_path.read_text(encoding="utf-8"))
        if progress_path.is_file()
        else {}
    )
    if not isinstance(progress, dict):
        raise ValueError("data/progress.json must contain an object")
    for paper_id, status in progress.items():
        connection.execute(
            "INSERT OR REPLACE INTO progress(paper_id,status) "
            "SELECT ?,? WHERE EXISTS(SELECT 1 FROM papers WHERE id=?)",
            (str(paper_id), str(status), str(paper_id)),
        )

    notes_root = root / "notes"
    if notes_root.is_dir():
        for note in notes_root.glob("*.md"):
            content = note.read_text(encoding="utf-8")
            if content.strip():
                connection.execute(
                    "INSERT OR REPLACE INTO notes(paper_id,content) "
                    "SELECT ?,? WHERE EXISTS(SELECT 1 FROM papers WHERE id=?)",
                    (note.stem, content, note.stem),
                )
    connection.execute("INSERT OR IGNORE INTO schema_migrations(version) VALUES(1)")


def _upgrade_database(root: Path, database: Path) -> None:
    config_path = root / "backend" / "alembic.ini"
    if not config_path.is_file():
        raise FileNotFoundError(f"Alembic configuration is missing: {config_path}")
    previous = os.environ.get("DB_PATH")
    os.environ["DB_PATH"] = str(database)
    try:
        configuration = Config(str(config_path))
        command.upgrade(configuration, SCHEMA_REVISION)
    finally:
        if previous is None:
            os.environ.pop("DB_PATH", None)
        else:
            os.environ["DB_PATH"] = previous


def _read_schema_revision(database: Path) -> str:
    try:
        with closing(sqlite3.connect(database)) as connection:
            row = connection.execute(
                "SELECT version_num FROM alembic_version"
            ).fetchone()
    except sqlite3.Error:
        return ""
    return str(row[0]) if row is not None else ""


def _backup_database(database: Path, backup_root: Path) -> Path:
    backup_root.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(str(database).encode("utf-8")).hexdigest()[:10]
    target = backup_root / f"pre-upgrade-{digest}-{os.getpid()}.sqlite3"
    with closing(sqlite3.connect(database)) as source, closing(
        sqlite3.connect(target)
    ) as destination:
        source.backup(destination)
    return target


def _embedding_profile(environment: Mapping[str, str]) -> EmbeddingProfile:
    provider = environment.get("EMBED_PROVIDER", "model2vec").strip().lower()
    if provider == "local":
        provider = "model2vec"
    if provider != "model2vec":
        raise ValueError("EMBED_PROVIDER must be model2vec or local")
    dimensions = int(environment.get("EMBED_DIMENSIONS", "256"))
    return EmbeddingProfile(
        provider=provider,
        model=environment.get("EMBED_MODEL", "minishlab/potion-multilingual-128M"),
        embedding_version=environment.get("EMBEDDING_VERSION", "model2vec-0.8.2"),
        dimensions=dimensions,
    )


def _rollout(environment: Mapping[str, str], root: Path) -> RolloutSettings:
    cursor_secret = environment.get("PROCESSING_CURSOR_SECRET") or hashlib.sha256(
        f"paper-study-local:{root}".encode("utf-8")
    ).hexdigest()
    return RolloutSettings(
        api_backend_mode="python",
        document_pipeline_mode="p1",
        generation_pipeline_mode="p1",
        artifact_read_mode="prefer_new",
        artifact_write_mode="dual",
        ocr_enabled=environment.get("OCR_ENABLED", "0") == "1",
        obsidian_enabled=environment.get("OBSIDIAN_ENABLED", "0") == "1",
        processing_cursor_secret=cursor_secret,
    )


async def serve(
    *,
    root: Path,
    database: Path,
    host: str,
    port: int,
    with_worker: bool = True,
    with_scheduler: bool = True,
) -> None:
    root = root.resolve()
    load_dotenv(root / ".env", override=False)
    setup = ensure_database(root, database)
    os.environ["DB_PATH"] = str(setup.path)
    environment = dict(os.environ)
    profile = _embedding_profile(environment)
    embedding_factory = lambda selected, _credential: Model2VecEmbeddingProvider(selected)
    translation_factory, structured_factory = legacy_p3_provider_factories(environment)
    rollout = _rollout(environment, root)
    settings = DatabaseSettings(setup.path)
    container = bootstrap(
        rollout,
        settings,
        required_schema_revision=SCHEMA_REVISION,
        environment_snapshot=environment,
        allow_legacy_credential_fallback=True,
        translation_provider_factory=translation_factory,
        structured_provider_factory=structured_factory,
        embedding_profile=profile,
        embedding_provider_factory=embedding_factory,
        query_embedding_provider_factory=embedding_factory,
    )
    app = create_app(
        ApiSettings(bind_host=host, bind_port=port),
        ApiDependencies(container, container.session_factory),
        required_schema_revision=SCHEMA_REVISION,
    )
    worker_container = None
    stop_event = asyncio.Event()
    background: list[asyncio.Task[object]] = []
    if with_worker:
        worker_container = bootstrap_processing_worker(
            settings,
            required_schema_revision=SCHEMA_REVISION,
            worker_id=f"local-worker-{os.getpid()}",
            translation_provider_factory=translation_factory,
            structured_provider_factory=structured_factory,
            embedding_profile=profile,
            embedding_provider_factory=embedding_factory,
            obsidian_enabled=rollout.obsidian_enabled,
            environment_snapshot=environment,
            allow_legacy_credential_fallback=True,
        )
        background.append(
            asyncio.create_task(
                worker_container.processing_worker.run_forever(stop_event=stop_event),
                name="processing-worker",
            )
        )
    if with_scheduler:
        background.append(
            asyncio.create_task(container.legacy.scheduler.run(), name="ingest-scheduler")
        )
    server = uvicorn.Server(
        uvicorn.Config(app, host=host, port=port, access_log=False, log_level="info")
    )
    try:
        await server.serve()
    finally:
        stop_event.set()
        if with_scheduler:
            container.legacy.scheduler.stop()
        if background:
            await asyncio.gather(*background, return_exceptions=True)
        if worker_container is not None:
            await worker_container.dispose()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="paper-study")
    parser.add_argument("--root", type=Path, default=REPOSITORY_ROOT)
    parser.add_argument("--db", type=Path)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=5173)
    parser.add_argument("--no-worker", action="store_true")
    parser.add_argument("--no-scheduler", action="store_true")
    return parser


def main(arguments: Sequence[str] | None = None) -> int:
    options = _parser().parse_args(arguments)
    root = options.root.resolve()
    database = (options.db or root / "data" / "app.db").expanduser()
    if not database.is_absolute():
        database = root / database
    try:
        asyncio.run(
            serve(
                root=root,
                database=database,
                host=options.host,
                port=options.port,
                with_worker=not options.no_worker,
                with_scheduler=not options.no_scheduler,
            )
        )
    except KeyboardInterrupt:
        return 130
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
