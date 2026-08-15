from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Sequence

from backend.app.infrastructure.database_backup import (
    DatabaseBackupError,
    OriginReceiptReport,
    VerificationReport,
    create_verified_backup,
    inspect_database,
    restore_backup_for_validation,
    seal_origin_receipt,
    verify_backup,
    verify_origin_receipt,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
ORIGIN_RECEIPT_PATH = (
    REPOSITORY_ROOT
    / "data"
    / "compatibility"
    / "runtime"
    / "p0-origin-receipt-v1.json"
)


def _verification_payload(report: VerificationReport) -> dict[str, Any]:
    return {
        "formatVersion": report.format_version,
        "backupId": report.backup_id,
        "backupSha256": report.backup_sha256,
        "manifestSha256": report.manifest_sha256,
        "manifestFileSha256": report.manifest_file_sha256,
        "logicalSha256": report.logical_sha256,
        "tableCounts": dict(report.table_counts),
        "tableSha256": dict(report.table_sha256),
        "contentCounts": dict(report.content_counts),
        "contentSha256": dict(report.content_sha256),
    }


def _origin_receipt_payload(report: OriginReceiptReport) -> dict[str, Any]:
    return {
        "schemaVersion": report.schema_version,
        "manifestKind": report.manifest_kind,
        "backupId": report.backup_id,
        "backupPath": str(report.backup_path),
        "backupSha256": report.backup_sha256,
        "manifestPath": str(report.manifest_path),
        "manifestSha256": report.manifest_sha256,
        "logicalSha256": report.logical_sha256,
        "databaseLineageId": report.database_lineage_id,
        "receiptPath": str(report.receipt_path),
        "createdAt": report.created_at,
        "receiptSha256": report.receipt_sha256,
        "originReceiptFileSha256": report.origin_receipt_file_sha256,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="study-app-database-backup",
        description="Create and verify consistent SQLite backups without importing the legacy DB module.",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    create = commands.add_parser("create", help="Create and verify a new SQLite backup.")
    create.add_argument(
        "--database",
        default=os.environ.get("DB_PATH", str(REPOSITORY_ROOT / "data" / "app.db")),
        help="Source SQLite path. Defaults to DB_PATH or data/app.db.",
    )
    create.add_argument(
        "--output-directory",
        default=str(REPOSITORY_ROOT / "data" / "backups"),
        help="Directory for the SQLite snapshot and JSON manifest.",
    )
    create.add_argument(
        "--label",
        default="manual",
        help="Audit label containing letters, digits, dot, underscore, or hyphen.",
    )

    inspect = commands.add_parser(
        "inspect",
        help="Inspect an existing SQLite database without modifying it.",
    )
    inspect.add_argument(
        "--database",
        required=True,
        help="Existing SQLite database path.",
    )

    verify = commands.add_parser("verify", help="Verify a backup against its manifest.")
    verify.add_argument("--backup", required=True, help="SQLite backup path.")
    verify.add_argument("--manifest", required=True, help="Backup manifest path.")

    restore = commands.add_parser(
        "restore-check",
        help="Restore a verified backup into a generated isolation directory.",
    )
    restore.add_argument("--backup", required=True, help="SQLite backup path.")
    restore.add_argument("--manifest", required=True, help="Backup manifest path.")
    restore.add_argument(
        "--output-directory",
        default=str(REPOSITORY_ROOT / "data" / "backups"),
        help="Parent for a newly generated restore-validation directory.",
    )

    seal_origin = commands.add_parser(
        "seal-origin",
        help="Exclusively seal one verified backup as the fixed P0 origin receipt.",
    )
    seal_origin.add_argument("--backup", required=True, help="SQLite backup path.")
    seal_origin.add_argument("--manifest", required=True, help="Backup manifest path.")

    verify_origin = commands.add_parser(
        "verify-origin-receipt",
        help="Verify a P0 origin receipt against an out-of-band file SHA-256.",
    )
    verify_origin.add_argument("--receipt", required=True, help="Origin receipt path.")
    verify_origin.add_argument(
        "--expected-receipt-file-sha256",
        required=True,
        help="Expected SHA-256 of the complete receipt file bytes.",
    )
    return parser


def run(arguments: Sequence[str]) -> dict[str, Any]:
    options = build_parser().parse_args(list(arguments))
    if options.command == "inspect":
        report = inspect_database(options.database)
        return {
            "ok": True,
            "operation": "inspect",
            **report.to_dict(),
        }
    if options.command == "create":
        result = create_verified_backup(
            options.database,
            options.output_directory,
            label=options.label,
        )
        return {
            "ok": True,
            "operation": "create",
            "backupPath": str(result.backup_path),
            "manifestPath": str(result.manifest_path),
            **_verification_payload(result.verification),
        }
    if options.command == "verify":
        report = verify_backup(options.backup, options.manifest)
        return {
            "ok": report.valid,
            "operation": "verify",
            **_verification_payload(report),
        }
    if options.command == "restore-check":
        report = restore_backup_for_validation(
            options.backup,
            options.manifest,
            options.output_directory,
        )
        return {
            "ok": report.valid,
            "operation": "restoreCheck",
            **_verification_payload(report),
            "restoredPath": str(report.restored_path),
        }
    if options.command == "seal-origin":
        report = seal_origin_receipt(
            options.backup,
            options.manifest,
            ORIGIN_RECEIPT_PATH,
        )
        return {
            "ok": report.valid,
            "operation": "sealOrigin",
            **_origin_receipt_payload(report),
        }
    if options.command == "verify-origin-receipt":
        report = verify_origin_receipt(
            options.receipt,
            options.expected_receipt_file_sha256,
        )
        return {
            "ok": report.valid,
            "operation": "verifyOriginReceipt",
            **_origin_receipt_payload(report),
        }
    raise DatabaseBackupError(
        "BACKUP_COMMAND_INVALID",
        f"Unsupported backup command: {options.command}",
    )


def main(arguments: Sequence[str] | None = None) -> int:
    try:
        payload = run(sys.argv[1:] if arguments is None else arguments)
    except DatabaseBackupError as error:
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": {
                        "code": error.code,
                        "message": str(error),
                    },
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 1
    except Exception:
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": {
                        "code": "BACKUP_UNEXPECTED_ERROR",
                        "message": "The database backup command failed unexpectedly.",
                    },
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 1
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
