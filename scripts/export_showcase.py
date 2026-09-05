"""Export one approved reproduction project into the isolated Hexo showcase.

This command intentionally uses the internal database only while it runs.  The
generated ``paper-showcase`` directory remains a static, database-free site.
"""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path
import sys


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from backend.app.application.reproductions import ReproductionWorkspace
from backend.app.config import DatabaseSettings
from backend.app.infrastructure.database import create_async_session_factory
from backend.app.repositories.unit_of_work import SqlAlchemyUnitOfWork


async def export_project(
    *,
    project_id: str,
    database: Path,
    artifacts: Path,
    showcase: Path,
) -> dict[str, object]:
    session_factory = create_async_session_factory(DatabaseSettings(database))
    workspace = ReproductionWorkspace(
        lambda: SqlAlchemyUnitOfWork(session_factory),
        artifact_root=artifacts,
        showcase_root=showcase,
    )
    try:
        publication = await workspace.get_publication(project_id)
        return await workspace.publish_publication(
            project_id,
            expected_revision=int(publication["revision"]),
        )
    finally:
        await session_factory.kw["bind"].dispose()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", required=True, help="reproduction project id")
    parser.add_argument(
        "--database",
        type=Path,
        default=REPOSITORY_ROOT / "data" / "app.db",
        help="private SQLite database path",
    )
    parser.add_argument(
        "--artifacts",
        type=Path,
        default=REPOSITORY_ROOT / "data" / "reproduction-artifacts",
        help="private reproduction artifact root",
    )
    parser.add_argument(
        "--showcase",
        type=Path,
        default=REPOSITORY_ROOT / "paper-showcase",
        help="static Hexo showcase root",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    result = asyncio.run(
        export_project(
            project_id=str(args.project),
            database=Path(args.database).expanduser().resolve(),
            artifacts=Path(args.artifacts).expanduser().resolve(),
            showcase=Path(args.showcase).expanduser().resolve(),
        )
    )
    print(result["url"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
