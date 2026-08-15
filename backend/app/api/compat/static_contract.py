from __future__ import annotations

from pathlib import Path
import json
import re
import sqlite3

from backend.app.domain.entities import (
    ArtifactKind,
    CredentialKind,
    ProcessingJobStatus,
    ProcessingJobType,
    SourceDocumentStatus,
    SourceMode,
)


class StaticContractError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


class LegacyRuntimeContractError(StaticContractError):
    """Raised when a retained legacy runtime/schema contract drifts."""


def verify_legacy_runtime_contract(
    *,
    repository_root: str | Path,
    database: str | Path,
) -> dict[str, object]:
    """Verify retained Node/legacy contracts without reading credential values."""

    root = Path(repository_root).expanduser().resolve(strict=True)
    database_path = Path(database).expanduser().resolve(strict=True)
    required_files = (
        "server.js",
        "db.js",
        "Dockerfile",
        "docker-compose.yml",
        "db/schema.sql",
        "contracts/legacy-api-v1.json",
        "contracts/legacy_route_inventory.json",
        "backend/app/providers/credentials/mappings.py",
    )
    texts: dict[str, str] = {}
    for relative in required_files:
        path = root / relative
        try:
            texts[relative] = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as error:
            raise LegacyRuntimeContractError(
                "LEGACY_RUNTIME_FILE_MISSING",
                f"The retained legacy contract file {relative!r} is unavailable.",
            ) from error

    source_tokens = {
        "server.js": ("/api/papers", "/api/explainer", "/api/translation"),
        "db.js": ("explainer", "pdf_path", "translations", "content"),
        "db/schema.sql": ("explainer", "pdf_path", "translations", "content"),
        "Dockerfile": ("frozen-node", "server.js"),
        "docker-compose.yml": ("frozen-node", "rollback", "server.js"),
        "backend/app/providers/credentials/mappings.py": (
            "apiKey",
            "ocrApiKey",
            "embedApiKey",
            "s2ApiKey",
        ),
    }
    for relative, tokens in source_tokens.items():
        if any(token not in texts[relative] for token in tokens):
            raise LegacyRuntimeContractError(
                "LEGACY_RUNTIME_FIELD_MISSING",
                f"The retained legacy contract changed in {relative!r}.",
            )
    if any(
        "finalize_legacy_migration" in texts[relative]
        for relative in ("server.js", "db.js", "Dockerfile", "docker-compose.yml")
    ):
        raise LegacyRuntimeContractError(
            "LEGACY_RUNTIME_FINALIZATION_FORBIDDEN",
            "P6 must not execute legacy finalization.",
        )

    try:
        route_ledger = json.loads(texts["contracts/legacy_route_inventory.json"])
        legacy_contract = json.loads(texts["contracts/legacy-api-v1.json"])
    except (ValueError, json.JSONDecodeError) as error:
        raise LegacyRuntimeContractError(
            "LEGACY_RUNTIME_ROUTE_INVENTORY_INVALID",
            "The retained legacy route inventory is invalid JSON.",
        ) from error
    routes = route_ledger.get("routes") if isinstance(route_ledger, dict) else None
    if not isinstance(routes, list) or not routes:
        raise LegacyRuntimeContractError(
            "LEGACY_RUNTIME_ROUTE_INVENTORY_INVALID",
            "The retained legacy route inventory is empty or malformed.",
        )
    for route in routes:
        if (
            not isinstance(route, dict)
            or not isinstance(route.get("method"), str)
            or not isinstance(route.get("path"), str)
            or route["path"] not in texts["server.js"]
        ):
            raise LegacyRuntimeContractError(
                "LEGACY_RUNTIME_ROUTE_MISSING",
                "A retained legacy method/path is absent from server.js.",
            )
    if not isinstance(legacy_contract, dict) or legacy_contract.get("version") != "legacy-api-v1":
        raise LegacyRuntimeContractError(
            "LEGACY_RUNTIME_ROUTE_INVENTORY_INVALID",
            "The legacy API contract version changed.",
        )

    credential_fields = ("apiKey", "ocrApiKey", "embedApiKey", "s2ApiKey")
    try:
        connection = sqlite3.connect(
            f"file:{database_path.as_posix()}?mode=ro",
            uri=True,
        )
    except sqlite3.Error as error:
        raise LegacyRuntimeContractError(
            "LEGACY_RUNTIME_DATABASE_INVALID",
            "The explicit database cannot be opened read-only.",
        ) from error
    try:
        connection.execute("PRAGMA query_only=ON")
        _verify_legacy_database_contract(connection)
        processing = _processing_projection(connection)
        trigger_names = tuple(
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='trigger' ORDER BY name"
            )
        )
    finally:
        connection.close()
    return {
        "ok": True,
        "repositoryRoot": root,
        "database": database_path,
        "alembicRevision": "20260807_03",
        "legacyCredentialFields": list(credential_fields),
        "legacyRouteCount": len(routes),
        "triggerNames": list(trigger_names),
        "processingJobs": processing,
        "legacyFinalizationExecuted": False,
    }


def _verify_legacy_database_contract(connection: sqlite3.Connection) -> None:
    required_tables = ("papers", "translations", "processing_jobs", "document_chunks")
    table_names = {
        str(row[0])
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        )
    }
    if any(table not in table_names for table in required_tables):
        raise LegacyRuntimeContractError(
            "LEGACY_RUNTIME_SCHEMA_MISSING",
            "A retained legacy or additive table is missing.",
        )
    columns = {
        table: tuple(
            str(row[1]) for row in connection.execute(f"PRAGMA table_info({table})")
        )
        for table in required_tables
    }
    if not {"explainer", "pdf_path"}.issubset(columns["papers"]):
        raise LegacyRuntimeContractError(
            "LEGACY_RUNTIME_FIELD_MISSING",
            "papers.explainer or papers.pdf_path is missing.",
        )
    if "content" not in columns["translations"]:
        raise LegacyRuntimeContractError(
            "LEGACY_RUNTIME_FIELD_MISSING",
            "translations.content is missing.",
        )
    from backend.app.api.compat.schema_inventory import (
        PROCESSING_JOB_COLUMNS,
        _EXPECTED_TRIGGER_SQL_SHA256,
        _EXPECTED_TABLE_SQL_SHA256,
        _normalize_sql,
        _sql_sha256,
        _row_bytes,
        _sequence_sha256,
    )
    if columns["processing_jobs"] != PROCESSING_JOB_COLUMNS:
        raise LegacyRuntimeContractError(
            "LEGACY_RUNTIME_SCHEMA_MISSING",
            "processing_jobs no longer has the frozen ordered columns.",
        )
    metadata = connection.execute("PRAGMA table_info(processing_jobs)").fetchall()
    if int(metadata[17][3]) != 1:
        raise LegacyRuntimeContractError(
            "LEGACY_RUNTIME_SCHEMA_MISSING",
            "processing_jobs.spec_json must remain non-null.",
        )
    trigger_rows = connection.execute(
        "SELECT name,sql FROM sqlite_master WHERE type='trigger' ORDER BY name"
    ).fetchall()
    if tuple(str(row[0]) for row in trigger_rows) != tuple(sorted(_EXPECTED_TRIGGER_SQL_SHA256)):
        raise LegacyRuntimeContractError(
            "LEGACY_RUNTIME_TRIGGER_INVALID",
            "The retained trigger inventory changed.",
        )
    for name, sql in trigger_rows:
        if _sql_sha256(_normalize_sql(str(sql))) != _EXPECTED_TRIGGER_SQL_SHA256[name]:
            raise LegacyRuntimeContractError(
                "LEGACY_RUNTIME_TRIGGER_INVALID",
                f"Trigger {name!r} SQL changed.",
            )
    fts_sql = connection.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='document_chunks_fts'"
    ).fetchone()
    if not fts_sql or _sql_sha256(_normalize_sql(str(fts_sql[0]))) != _EXPECTED_TABLE_SQL_SHA256["document_chunks_fts"]:
        raise LegacyRuntimeContractError(
            "LEGACY_RUNTIME_FTS_INVALID",
            "The retained FTS table contract changed.",
        )

    revisions = tuple(
        str(row[0])
        for row in connection.execute("SELECT version_num FROM alembic_version ORDER BY version_num")
    )
    if revisions != ("20260807_03",):
        raise LegacyRuntimeContractError(
            "LEGACY_RUNTIME_REVISION_INVALID",
            "The database must have exactly Alembic revision 20260807_03.",
        )


def _processing_projection(connection: sqlite3.Connection) -> dict[str, object]:
    from backend.app.api.compat.schema_inventory import (
        PROCESSING_JOB_COLUMNS,
        _row_bytes,
        _sequence_sha256,
    )
    from backend.app.domain.processing import JobSpecValidationError, decode_job_spec_v1

    projection = ",".join(f'"{column}"' for column in PROCESSING_JOB_COLUMNS)
    rows = connection.execute(
        f"SELECT {projection} FROM processing_jobs ORDER BY id"
    ).fetchall()
    spec_rows: list[bytes] = []
    all_rows: list[bytes] = []
    for row in rows:
        mapped = dict(zip(PROCESSING_JOB_COLUMNS, row, strict=True))
        try:
            decode_job_spec_v1(
                mapped["spec_json"],
                expected_row={
                    "job_type": mapped["job_type"],
                    "paper_id": mapped["paper_id"],
                    "source_mode": mapped["source_mode"],
                    "source_document_id": mapped["source_document_id"],
                    "artifact_id": mapped["artifact_id"],
                },
            )
        except JobSpecValidationError as error:
            raise LegacyRuntimeContractError(
                "LEGACY_RUNTIME_JOB_SPEC_INVALID",
                "A processing_jobs.spec_json envelope failed strict decoding.",
            ) from error
        all_rows.append(_row_bytes(row))
        spec_rows.append(_row_bytes((mapped["id"], mapped["spec_json"])))
    return {
        "count": len(rows),
        "sha256": _sequence_sha256(all_rows),
        "processingJobSpecsCount": len(spec_rows),
        "processingJobSpecsSha256": _sequence_sha256(spec_rows),
        "strictDecodeCount": len(rows),
        "strictDecodeErrorCount": 0,
    }


def canonical_domain_enums() -> dict[str, tuple[str, ...]]:
    source_statuses = tuple(item.value for item in SourceDocumentStatus)
    return {
        "sourceMode": tuple(item.value for item in SourceMode),
        "sourceDocumentStatus": source_statuses,
        "generatedArtifactStatus": source_statuses,
        "artifactKind": tuple(item.value for item in ArtifactKind),
        "processingJobType": tuple(item.value for item in ProcessingJobType),
        "processingJobStatus": tuple(item.value for item in ProcessingJobStatus),
        "credentialKind": tuple(item.value for item in CredentialKind),
    }


def verify_static_runbook(
    *,
    readme: str | Path,
    database_doc: str | Path,
) -> dict[str, object]:
    try:
        readme_path = Path(readme).expanduser().resolve(strict=True)
        database_path = Path(database_doc).expanduser().resolve(strict=True)
        readme_text = readme_path.read_text(encoding="utf-8")
        database_text = database_path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        raise StaticContractError(
            "STATIC_RUNBOOK_INVALID",
            "The explicit static runbook files could not be read as UTF-8.",
        ) from error
    stateful = re.compile(
        r"(?im)^\s*(?:CURRENT_PRODUCTION_OWNER|CURRENT_OWNER_STATE)\s*[:=]"
    )
    if stateful.search(readme_text) or stateful.search(database_text):
        raise StaticContractError(
            "STATIC_RUNBOOK_STATEFUL",
            "Static documentation must not declare the current production owner.",
        )
    marker = "data/compatibility/runtime/production-owner.json"
    if marker not in readme_text or marker not in database_text:
        raise StaticContractError(
            "STATIC_RUNBOOK_OWNER_MARKER_MISSING",
            "Static documentation must name the runtime owner marker.",
        )
    deletion_terms = (
        "finalize_legacy_migration",
        "另立版本化计划",
        "frozen Node",
        "旧 API",
        "旧表/列",
        "legacy fallback",
        "legacy credential fields",
        "Obsidian ledger",
        "P6 本身不执行",
    )
    if any(term not in database_text for term in deletion_terms):
        raise StaticContractError(
            "STATIC_RUNBOOK_DELETION_BOUNDARY_MISSING",
            "The static runbook no longer preserves the independent deletion boundary.",
        )
    return {
        "ok": True,
        "readme": readme_path,
        "databaseDoc": database_path,
        "runtimeOwnerMarker": (
            readme_path.parent
            / "data"
            / "compatibility"
            / "runtime"
            / "production-owner.json"
        ),
        "stateNeutral": True,
        "deletionBoundaryPreserved": True,
    }


__all__ = [
    "LegacyRuntimeContractError",
    "StaticContractError",
    "canonical_domain_enums",
    "verify_legacy_runtime_contract",
    "verify_static_runbook",
]
