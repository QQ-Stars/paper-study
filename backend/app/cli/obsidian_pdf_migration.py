from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path
import sys
from collections.abc import Mapping, Sequence
from typing import TextIO

from backend.app.application.obsidian_pdf_migration import ObsidianPdfMigration
from backend.app.application.obsidian_pdf_migration import PdfMigrationError
from backend.app.application.settings import SettingsError, SettingsService
from backend.app.bootstrap import verify_schema_revision
from backend.app.config import DatabaseSettings
from backend.app.infrastructure.bound_vault_root import (
    BoundVaultRoot,
    ObsidianVaultError,
)
from backend.app.infrastructure.database import create_async_session_factory
from backend.app.providers.pdf_files import PdfFiles
from backend.app.repositories.obsidian_exports import (
    SqlAlchemyObsidianExportsRepository,
)


P3_SCHEMA_REVISION = "20260807_03"
REPOSITORY_ROOT = Path(__file__).resolve().parents[3]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="study-obsidian-pdf-migration")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("plan")
    prepare = commands.add_parser("prepare")
    prepare.add_argument("--confirm-plan-sha", required=True)
    prepare.add_argument("--intent-output", required=True)
    apply = commands.add_parser("apply")
    apply.add_argument("--intent", required=True)
    apply.add_argument("--confirm-intent-sha", required=True)
    rollback = commands.add_parser("rollback")
    rollback.add_argument("--intent", required=True)
    rollback.add_argument("--confirm-intent-sha", required=True)
    return parser


async def run(
    arguments: Sequence[str],
    *,
    migration: ObsidianPdfMigration | None = None,
    stdout: TextIO | None = None,
    environment: Mapping[str, str] | None = None,
) -> int:
    if migration is None:
        return await _run_configured(
            arguments,
            stdout=stdout,
            environment=environment,
        )
    options = build_parser().parse_args(list(arguments))
    target = stdout or sys.stdout
    if options.command == "plan":
        result = await migration.plan()
        target.write(result.canonical_bytes.decode("utf-8"))
        return 0
    if options.command == "prepare":
        result = await migration.prepare(
            confirm_plan_sha=options.confirm_plan_sha,
            intent_output=options.intent_output,
        )
        target.write(
            json.dumps(
                {
                    "intentPath": str(result.intent_path),
                    "intentSha256": result.intent_sha256,
                    "state": result.state,
                },
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
        )
        return 0
    if options.command == "apply":
        result = await migration.apply(
            intent=options.intent,
            confirm_intent_sha=options.confirm_intent_sha,
        )
        target.write(
            json.dumps(
                {
                    "intentPath": str(result.intent_path),
                    "intentSha256": result.intent_sha256,
                    "state": result.state,
                },
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
        )
        return 0
    if options.command == "rollback":
        result = await migration.rollback(
            intent=options.intent,
            confirm_intent_sha=options.confirm_intent_sha,
        )
        target.write(
            json.dumps(
                {
                    "intentPath": str(result.intent_path),
                    "intentSha256": result.intent_sha256,
                    "state": result.state,
                },
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
        )
        return 0
    raise RuntimeError(f"PDF migration command is not implemented: {options.command}")


async def _run_configured(
    arguments: Sequence[str],
    *,
    stdout: TextIO | None,
    environment: Mapping[str, str] | None,
) -> int:
    values = dict(os.environ if environment is None else environment)
    settings_path = Path(
        values.get("SETTINGS_PATH", str(REPOSITORY_ROOT / "data" / "settings.json"))
    ).expanduser().resolve()
    document, settings_bytes = _read_settings_document(settings_path)
    settings_service = SettingsService(
        settings_path=settings_path,
        root=REPOSITORY_ROOT,
        credential_service=object(),
        environment_snapshot=values,
    )
    obsidian = await settings_service.obsidian()
    if _read_optional_bytes(settings_path) != settings_bytes:
        raise PdfMigrationError(
            "OBSIDIAN_MIGRATION_SETTINGS_CHANGED",
            "Settings changed while the PDF migration command was starting.",
        )

    pdf_directory = _existing_directory(
        _configured_directory(document, "pdfDir", REPOSITORY_ROOT / "data" / "pdfs"),
        code="OBSIDIAN_MIGRATION_PDF_DIR_INVALID",
        label="The configured PDF directory",
    )
    if not obsidian.vault_path:
        raise PdfMigrationError(
            "OBSIDIAN_MIGRATION_VAULT_NOT_CONFIGURED",
            "An absolute Obsidian Vault path must be configured.",
        )
    vault_path = _existing_directory(
        Path(obsidian.vault_path),
        code="OBSIDIAN_MIGRATION_VAULT_INVALID",
        label="The configured Obsidian Vault",
    )
    database_settings = DatabaseSettings(values.get("DB_PATH"))
    verify_schema_revision(database_settings, P3_SCHEMA_REVISION)
    session_factory = create_async_session_factory(database_settings)
    settings_fingerprint = hashlib.sha256(
        json.dumps(
            {
                "pdfDir": str(pdf_directory),
                "rootFolder": obsidian.root_folder,
                "vaultPath": str(vault_path),
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()
    try:
        repository = SqlAlchemyObsidianExportsRepository(session_factory)
        pdf_files = PdfFiles(
            root=REPOSITORY_ROOT,
            default_directory=pdf_directory,
        )
        with BoundVaultRoot.open(vault_path) as root:
            configured = ObsidianPdfMigration(
                pdf_files=pdf_files,
                root=root,
                repository=repository,
                root_folder=obsidian.root_folder,
                settings_fingerprint=settings_fingerprint,
            )
            return await run(arguments, migration=configured, stdout=stdout)
    finally:
        await session_factory.kw["bind"].dispose()


def _read_settings_document(path: Path) -> tuple[dict[str, object], bytes]:
    raw = _read_optional_bytes(path)
    if not raw:
        return {}, raw
    try:
        document = json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as error:
        raise PdfMigrationError(
            "OBSIDIAN_MIGRATION_SETTINGS_INVALID",
            "The settings document is not valid UTF-8 JSON.",
        ) from error
    if not isinstance(document, dict):
        raise PdfMigrationError(
            "OBSIDIAN_MIGRATION_SETTINGS_INVALID",
            "The settings document must be a JSON object.",
        )
    return dict(document), raw


def _read_optional_bytes(path: Path) -> bytes:
    try:
        return path.read_bytes()
    except FileNotFoundError:
        return b""
    except OSError as error:
        raise PdfMigrationError(
            "OBSIDIAN_MIGRATION_SETTINGS_UNAVAILABLE",
            "The settings document is unavailable.",
        ) from error


def _configured_directory(
    document: Mapping[str, object],
    key: str,
    default: Path,
) -> Path:
    raw = document.get(key)
    if raw is None or raw == "":
        return default
    if not isinstance(raw, str):
        raise PdfMigrationError(
            "OBSIDIAN_MIGRATION_SETTINGS_INVALID",
            "The configured PDF directory is invalid.",
        )
    path = Path(raw).expanduser()
    return path if path.is_absolute() else REPOSITORY_ROOT / path


def _existing_directory(path: Path, *, code: str, label: str) -> Path:
    try:
        resolved = path.expanduser().resolve(strict=True)
    except (OSError, RuntimeError) as error:
        raise PdfMigrationError(code, f"{label} does not exist.") from error
    if not resolved.is_dir():
        raise PdfMigrationError(code, f"{label} must be an existing directory.")
    return resolved


def main(arguments: Sequence[str] | None = None) -> int:
    try:
        return asyncio.run(run(sys.argv[1:] if arguments is None else arguments))
    except (PdfMigrationError, ObsidianVaultError, SettingsError) as error:
        code = str(getattr(error, "code", "OBSIDIAN_PDF_MIGRATION_ERROR"))
        message = str(error)
    except (OSError, RuntimeError, ValueError):
        code = "OBSIDIAN_PDF_MIGRATION_CONFIGURATION_INVALID"
        message = "The PDF migration command could not validate its configuration."
    except Exception:
        code = "OBSIDIAN_PDF_MIGRATION_UNEXPECTED_ERROR"
        message = "The PDF migration command failed unexpectedly."
    print(
        json.dumps(
            {"ok": False, "error": {"code": code, "message": message}},
            ensure_ascii=False,
            separators=(",", ":"),
        ),
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
