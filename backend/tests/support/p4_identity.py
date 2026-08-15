from __future__ import annotations

from contextlib import closing, contextmanager
from dataclasses import dataclass
from pathlib import Path
import hashlib
import shutil
import sqlite3
import tempfile
from typing import Iterator

from backend.app.infrastructure.database_backup import (
    create_verified_backup,
    verify_origin_receipt,
)
from backend.tests.support.p1_database import run_alembic


_REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
_P0_ORIGIN_RECEIPT = (
    _REPOSITORY_ROOT / "data" / "compatibility" / "runtime" / "p0-origin-receipt-v1.json"
)
_P0_ORIGIN_RECEIPT_SHA256 = (
    "7428474fb74bee7bbe6db97a56f08f30520f7d020ff51c149e85ea8a27be6224"
)


@dataclass(frozen=True, slots=True)
class P4IdentityFixture:
    root: Path
    database_path: Path
    candidate_database_path: Path
    receipt_path: Path
    receipt_file_sha256: str
    origin_backup_path: Path
    origin_manifest_path: Path
    alternate_backup_path: Path
    alternate_manifest_path: Path
    entrypoint_path: Path
    same_name_entrypoint_path: Path


@contextmanager
def p4_identity_fixture() -> Iterator[P4IdentityFixture]:
    with tempfile.TemporaryDirectory(prefix="study-app-p4-identity-") as temp_dir:
        root = Path(temp_dir)
        origin = verify_origin_receipt(
            _P0_ORIGIN_RECEIPT,
            _P0_ORIGIN_RECEIPT_SHA256,
        )
        database_path = root / "live" / "app.db"
        database_path.parent.mkdir(parents=True)
        shutil.copyfile(origin.backup_path, database_path)
        run_alembic(database_path, "20260807_03")

        alternate_database = root / "alternate" / "app.db"
        alternate_database.parent.mkdir()
        shutil.copyfile(database_path, alternate_database)
        with closing(sqlite3.connect(alternate_database)) as connection:
            connection.execute(
                "UPDATE notes SET content = 'alternate verified origin' WHERE paper_id = 'paper-1'"
            )
            connection.commit()
        alternate = create_verified_backup(
            alternate_database,
            root / "backups" / "alternate",
            label="alternate",
        )

        candidate_database_path = root / "candidate" / "app.db"
        candidate_database_path.parent.mkdir()

        entrypoint_path = root / "runtime-a" / "server.js"
        same_name_entrypoint_path = root / "runtime-b" / "server.js"
        entrypoint_path.parent.mkdir()
        same_name_entrypoint_path.parent.mkdir()
        entrypoint_path.write_text("setInterval(() => {}, 1000);\n", encoding="utf-8")
        same_name_entrypoint_path.write_text(
            "setInterval(() => {}, 1000);\n",
            encoding="utf-8",
        )

        yield P4IdentityFixture(
            root=root,
            database_path=database_path,
            candidate_database_path=candidate_database_path,
            receipt_path=origin.receipt_path,
            receipt_file_sha256=origin.origin_receipt_file_sha256,
            origin_backup_path=origin.backup_path,
            origin_manifest_path=origin.manifest_path,
            alternate_backup_path=alternate.backup_path,
            alternate_manifest_path=alternate.manifest_path,
            entrypoint_path=entrypoint_path,
            same_name_entrypoint_path=same_name_entrypoint_path,
        )


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()
