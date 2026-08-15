from __future__ import annotations

from dataclasses import dataclass
import os
from os import PathLike
from pathlib import Path


@dataclass(frozen=True, slots=True)
class DatabaseSettings:
    """Resolved, read-only location of the existing application database."""

    database_path: Path | str | PathLike[str] | None = None

    def __post_init__(self) -> None:
        raw_path = self.database_path
        if raw_path is None:
            raw_path = os.environ.get("DB_PATH")
        if raw_path is None or not str(raw_path).strip():
            raise ValueError("DB_PATH must name an existing SQLite file")

        resolved_path = Path(raw_path).expanduser().resolve(strict=True)
        if not resolved_path.parent.is_dir():
            raise ValueError("DB_PATH parent must be an existing directory")
        if not resolved_path.is_file():
            raise ValueError("DB_PATH must name an existing regular SQLite file")

        object.__setattr__(self, "database_path", resolved_path)
