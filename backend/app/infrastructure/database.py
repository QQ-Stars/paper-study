from __future__ import annotations

from sqlalchemy import URL, event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from backend.app.config import DatabaseSettings


def create_async_session_factory(
    settings: DatabaseSettings,
) -> async_sessionmaker[AsyncSession]:
    url = URL.create("sqlite+aiosqlite", database=str(settings.database_path))
    engine = create_async_engine(url, pool_pre_ping=True)

    @event.listens_for(engine.sync_engine, "connect")
    def configure_sqlite(dbapi_connection: object, _connection_record: object) -> None:
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA busy_timeout=5000")
        finally:
            cursor.close()

    return async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
