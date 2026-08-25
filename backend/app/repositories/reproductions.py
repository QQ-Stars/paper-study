from __future__ import annotations

import json
from typing import Any

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.domain import PersistenceConflictError, PersistenceReadError


class SqlAlchemyReproductionRepository:
    """Persistence adapter for the reproduction workspace aggregate and children."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_projects(
        self,
        *,
        query: str | None,
        status: str | None,
        tag: str | None,
        sort: str,
        limit: int,
        offset: int,
    ) -> tuple[list[dict[str, object]], int]:
        clauses: list[str] = []
        params: dict[str, object] = {"limit": limit, "offset": offset}
        if query:
            clauses.append("(lower(p.name) LIKE :query OR lower(p.paper_title) LIKE :query)")
            params["query"] = f"%{query.lower()}%"
        if status:
            clauses.append("p.status = :status")
            params["status"] = status
        if tag:
            clauses.append("EXISTS (SELECT 1 FROM json_each(p.tags_json) WHERE value = :tag)")
            params["tag"] = tag
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        try:
            total = int((await self._session.execute(
                text(f"SELECT count(*) FROM reproduction_projects p{where}"), params
            )).scalar_one())
            order_by = "p.name COLLATE NOCASE ASC,p.id ASC" if sort == "name" else "p.updated_at DESC,p.id DESC"
            rows = (await self._session.execute(text(
                "SELECT p.id,p.paper_id,p.paper_title,p.name,p.status,p.tags_json,p.revision,"
                "p.created_at,p.updated_at,d.id AS document_id,d.content,d.revision AS document_revision,"
                "d.save_status AS document_save_status,d.created_at AS document_created_at,d.updated_at AS document_updated_at "
                f"FROM reproduction_projects p LEFT JOIN reproduction_documents d ON d.project_id=p.id{where} "
                f"ORDER BY {order_by} LIMIT :limit OFFSET :offset"
            ), params)).mappings().all()
        except SQLAlchemyError as error:
            raise PersistenceReadError(operation="list_reproductions") from error
        return [_project_row(row) for row in rows], total

    async def get_project(self, project_id: str) -> dict[str, object] | None:
        row = (await self._session.execute(text(
            "SELECT p.id,p.paper_id,p.paper_title,p.name,p.status,p.tags_json,p.revision,"
            "p.created_at,p.updated_at,d.id AS document_id,d.content,d.revision AS document_revision,"
            "d.save_status AS document_save_status,d.created_at AS document_created_at,d.updated_at AS document_updated_at "
            "FROM reproduction_projects p LEFT JOIN reproduction_documents d ON d.project_id=p.id "
            "WHERE p.id=:id"
        ), {"id": project_id})).mappings().one_or_none()
        return _project_row(row) if row is not None else None

    async def add_project(self, values: dict[str, object], document: dict[str, object]) -> None:
        try:
            await self._session.execute(text(
                "INSERT INTO reproduction_projects(id,paper_id,paper_title,name,status,tags_json,revision,created_at,updated_at) "
                "VALUES(:id,:paper_id,:paper_title,:name,:status,:tags_json,:revision,:created_at,:updated_at)"
            ), values)
            await self._session.execute(text(
                "INSERT INTO reproduction_documents(id,project_id,content,revision,save_status,created_at,updated_at) "
                "VALUES(:id,:project_id,:content,:revision,:save_status,:created_at,:updated_at)"
            ), document)
        except SQLAlchemyError as error:
            raise PersistenceConflictError(operation="create_reproduction") from error

    async def update_project(
        self, project_id: str, *, values: dict[str, object], expected_revision: int
    ) -> bool:
        assignments = ",".join(f"{name}=:{name}" for name in values)
        params = {**values, "id": project_id, "expected_revision": expected_revision}
        result = await self._session.execute(text(
            f"UPDATE reproduction_projects SET {assignments},revision=revision+1,updated_at=:updated_at "
            "WHERE id=:id AND revision=:expected_revision"
        ), params)
        return bool(result.rowcount == 1)

    async def archive(self, project_id: str, *, expected_revision: int) -> bool:
        result = await self._session.execute(text(
            "UPDATE reproduction_projects SET status='archived',revision=revision+1,updated_at=:updated_at "
            "WHERE id=:id AND status <> 'archived' AND revision=:expected_revision"
        ), {"id": project_id, "updated_at": _now(), "expected_revision": expected_revision})
        return bool(result.rowcount == 1)

    async def delete_project(self, project_id: str) -> bool:
        result = await self._session.execute(
            text("DELETE FROM reproduction_projects WHERE id=:id"), {"id": project_id}
        )
        return bool(result.rowcount == 1)

    async def save_document(
        self, project_id: str, *, content: str, expected_revision: int, updated_at: str
    ) -> bool:
        result = await self._session.execute(text(
            "UPDATE reproduction_documents SET content=:content,revision=revision+1,save_status='saved',updated_at=:updated_at "
            "WHERE project_id=:project_id AND revision=:expected_revision"
        ), {"project_id": project_id, "content": content, "updated_at": updated_at, "expected_revision": expected_revision})
        if result.rowcount == 1:
            await self._session.execute(text(
                "UPDATE reproduction_projects SET revision=revision+1,updated_at=:updated_at WHERE id=:project_id"
            ), {"project_id": project_id, "updated_at": updated_at})
        return bool(result.rowcount == 1)

    async def add_run(self, values: dict[str, object]) -> None:
        await self._session.execute(text(
            "INSERT INTO experiment_runs(id,project_id,environment,command,parameters_json,data_version,code_revision,seed,status,metrics_json,result_summary,created_at,updated_at) "
            "VALUES(:id,:project_id,:environment,:command,:parameters_json,:data_version,:code_revision,:seed,:status,:metrics_json,:result_summary,:created_at,:updated_at)"
        ), values)

    async def list_runs(self, project_id: str) -> list[dict[str, object]]:
        rows = (await self._session.execute(text(
            "SELECT * FROM experiment_runs WHERE project_id=:project_id ORDER BY created_at DESC,id DESC"
        ), {"project_id": project_id})).mappings().all()
        return [_run_row(row) for row in rows]

    async def run_exists(self, project_id: str, run_id: str) -> bool:
        value = await self._session.execute(
            text("SELECT 1 FROM experiment_runs WHERE id=:run_id AND project_id=:project_id"),
            {"run_id": run_id, "project_id": project_id},
        )
        return value.scalar_one_or_none() is not None

    async def add_artifact(self, values: dict[str, object]) -> None:
        await self._session.execute(text(
            "INSERT INTO reproduction_artifacts(id,project_id,run_id,kind,filename,storage_key,mime_type,size_bytes,sha256,created_at) "
            "VALUES(:id,:project_id,:run_id,:kind,:filename,:storage_key,:mime_type,:size_bytes,:sha256,:created_at)"
        ), values)

    async def list_artifacts(self, project_id: str) -> list[dict[str, object]]:
        rows = (await self._session.execute(text(
            "SELECT * FROM reproduction_artifacts WHERE project_id=:project_id ORDER BY created_at DESC,id DESC"
        ), {"project_id": project_id})).mappings().all()
        return [
            {
                "id": row["id"],
                "projectId": row["project_id"],
                "runId": row["run_id"],
                "kind": row["kind"],
                "filename": row["filename"],
                "storageKey": row["storage_key"],
                "mimeType": row["mime_type"],
                "sizeBytes": row["size_bytes"],
                "sha256": row["sha256"],
                "createdAt": row["created_at"],
            }
            for row in rows
        ]

    async def add_note(self, values: dict[str, object]) -> None:
        await self._session.execute(text(
            "INSERT INTO reproduction_notes(id,project_id,content,created_at,updated_at) VALUES(:id,:project_id,:content,:created_at,:updated_at)"
        ), values)

    async def list_notes(self, project_id: str) -> list[dict[str, object]]:
        rows = (await self._session.execute(text(
            "SELECT * FROM reproduction_notes WHERE project_id=:project_id ORDER BY updated_at DESC,id DESC"
        ), {"project_id": project_id})).mappings().all()
        return [
            {
                "id": row["id"],
                "projectId": row["project_id"],
                "content": row["content"],
                "createdAt": row["created_at"],
                "updatedAt": row["updated_at"],
            }
            for row in rows
        ]


def _project_row(row: Any) -> dict[str, object]:
    raw_tags = row.get("tags_json") or "[]"
    try:
        tags = json.loads(raw_tags)
    except (TypeError, ValueError):
        tags = []
    return {
        "id": row["id"], "paperId": row["paper_id"], "paperTitle": row["paper_title"],
        "name": row["name"], "status": row["status"], "tags": tags if isinstance(tags, list) else [],
        "revision": row["revision"], "createdAt": row["created_at"], "updatedAt": row["updated_at"],
        "document": {
            "id": row.get("document_id"), "content": row.get("content") or "",
            "revision": row.get("document_revision") or 1,
            "saveStatus": row.get("document_save_status") or "saved",
            "createdAt": row.get("document_created_at"), "updatedAt": row.get("document_updated_at"),
        },
    }


def _run_row(row: Any) -> dict[str, object]:
    return {
        "id": row["id"], "projectId": row["project_id"], "environment": row["environment"],
        "command": row["command"], "parameters": _json_object(row["parameters_json"]),
        "dataVersion": row["data_version"], "codeRevision": row["code_revision"], "seed": row["seed"],
        "status": row["status"], "metrics": _json_object(row["metrics_json"]),
        "resultSummary": row["result_summary"], "createdAt": row["created_at"], "updatedAt": row["updated_at"],
    }


def _json_object(raw: object) -> dict[str, object]:
    try:
        value = json.loads(str(raw))
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _now() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()
