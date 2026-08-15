from __future__ import annotations

from logging.config import fileConfig
import os
from pathlib import Path

import backend  # noqa: F401 -- bootstrap the selected SQLite runtime before the dialect
from alembic import context
from sqlalchemy import engine_from_config, event, pool


configuration = context.config
if configuration.config_file_name is not None:
    fileConfig(configuration.config_file_name)


def _database_url() -> str:
    raw_path = os.environ.get("DB_PATH")
    if raw_path is None or not raw_path.strip():
        raise RuntimeError("DB_PATH is required for Alembic")
    database_path = Path(raw_path).expanduser().resolve(strict=True)
    if not database_path.is_file():
        raise RuntimeError("DB_PATH must name an existing regular SQLite file")
    if not database_path.parent.is_dir():
        raise RuntimeError("DB_PATH parent must be an existing directory")
    return f"sqlite+pysqlite:///{database_path.as_posix()}"


def run_migrations_offline() -> None:
    context.configure(
        url=_database_url(),
        target_metadata=None,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        transactional_ddl=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    section = configuration.get_section(configuration.config_ini_section, {})
    section["sqlalchemy.url"] = _database_url()
    connectable = engine_from_config(
        section,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    @event.listens_for(connectable, "connect")
    def configure_sqlite(dbapi_connection: object, _connection_record: object) -> None:
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA busy_timeout=5000")
        finally:
            cursor.close()

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=None,
            transactional_ddl=True,
        )
        with context.begin_transaction():
            context.run_migrations()
    connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
