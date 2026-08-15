from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
import tempfile
from typing import AsyncIterator, Any, Callable

from backend.app.config import DatabaseSettings
from backend.app.infrastructure.database import create_async_session_factory
from backend.tests.support.p1_database import create_legacy_database, run_alembic


P2_TEST_PROCESSING_CURSOR_SECRET = "test-only-processing-cursor-secret-v1"


@dataclass(frozen=True, slots=True)
class P2DatabaseFixture:
    database_path: Path
    session_factory: Any


@asynccontextmanager
async def p2_database_fixture(
    *,
    prefix: str,
    prepare_legacy: Callable[[Path], None] | None = None,
) -> AsyncIterator[P2DatabaseFixture]:
    """Create one migrated temporary P2 database and always dispose its engine."""
    with tempfile.TemporaryDirectory(prefix=prefix) as temp_dir:
        database_path = Path(temp_dir) / "database" / "app.db"
        create_legacy_database(database_path)
        if prepare_legacy is not None:
            run_alembic(database_path, "20260807_01")
            prepare_legacy(database_path)
        run_alembic(database_path, "20260807_02")
        session_factory = create_async_session_factory(DatabaseSettings(database_path))
        try:
            yield P2DatabaseFixture(database_path, session_factory)
        finally:
            await session_factory.kw["bind"].dispose()
