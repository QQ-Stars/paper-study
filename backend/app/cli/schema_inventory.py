from __future__ import annotations

import argparse
import json
import sys
from typing import Any, Sequence

from backend.app.api.compat.schema_inventory import (
    SchemaInventoryError,
    capture_inventory,
    compare_inventory,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="study-app-schema-inventory")
    commands = parser.add_subparsers(dest="command", required=True)

    capture = commands.add_parser("capture")
    capture.add_argument("--database", required=True)
    capture.add_argument("--database-identity-manifest", required=True)
    capture.add_argument("--output", required=True)

    compare = commands.add_parser("compare")
    compare.add_argument("--before", required=True)
    compare.add_argument("--after", required=True)
    return parser


def run(arguments: Sequence[str]) -> dict[str, Any]:
    options = build_parser().parse_args(list(arguments))
    if options.command == "capture":
        inventory = capture_inventory(
            database=options.database,
            database_identity_manifest=options.database_identity_manifest,
            output=options.output,
        )
        return {
            "ok": True,
            "operation": "capture",
            "output": str(options.output),
            "schemaVersion": inventory["schemaVersion"],
            "databaseLineageId": inventory["databaseIdentity"]["databaseLineageId"],
            "subjectDatabaseId": inventory["databaseIdentity"]["subjectDatabaseId"],
            "alembicRevision": inventory["alembic"]["revision"],
        }
    if options.command == "compare":
        compare_inventory(options.before, options.after)
        return {
            "ok": True,
            "operation": "compare",
            "before": str(options.before),
            "after": str(options.after),
        }
    raise SchemaInventoryError("INVENTORY_COMMAND_INVALID", "Unsupported command.")


def main(arguments: Sequence[str] | None = None) -> int:
    try:
        payload = run(sys.argv[1:] if arguments is None else arguments)
    except SchemaInventoryError as error:
        print(
            json.dumps(
                {"ok": False, "error": {"code": error.code, "message": str(error)}},
                ensure_ascii=False,
                separators=(",", ":"),
            ),
            file=sys.stderr,
        )
        return 2
    except Exception:
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": {
                        "code": "INVENTORY_UNEXPECTED_ERROR",
                        "message": "The schema inventory command failed unexpectedly.",
                    },
                },
                ensure_ascii=False,
                separators=(",", ":"),
            ),
            file=sys.stderr,
        )
        return 2
    print(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
