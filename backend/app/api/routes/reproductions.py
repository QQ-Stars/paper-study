from __future__ import annotations

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Path, Query, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr


class _Body(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CreateProjectBody(_Body):
    paperId: StrictStr | None = Field(default=None, min_length=1, max_length=200)
    name: StrictStr = Field(min_length=1, max_length=200)
    tags: list[StrictStr] = Field(default_factory=list, max_length=30)


class UpdateProjectBody(_Body):
    expectedRevision: Annotated[int, Field(strict=True, ge=1)]
    name: StrictStr | None = Field(default=None, min_length=1, max_length=200)
    status: Literal["planned", "preparing", "running", "completed", "blocked", "archived"] | None = None
    tags: list[StrictStr] | None = Field(default=None, max_length=30)


class SaveDocumentBody(_Body):
    content: StrictStr = Field(max_length=2_000_000)
    expectedRevision: Annotated[int, Field(strict=True, ge=1)]


class RunBody(_Body):
    environment: StrictStr | None = Field(default=None, max_length=10_000)
    command: StrictStr | None = Field(default=None, max_length=10_000)
    parameters: dict[str, object] = Field(default_factory=dict)
    dataVersion: StrictStr | None = Field(default=None, max_length=10_000)
    codeRevision: StrictStr | None = Field(default=None, max_length=10_000)
    seed: StrictInt | None = None
    status: Literal["planned", "running", "completed", "failed", "blocked"] = "planned"
    metrics: dict[str, object] = Field(default_factory=dict)
    resultSummary: StrictStr | None = Field(default=None, max_length=10_000)


class ArtifactBody(_Body):
    runId: StrictStr | None = None
    kind: StrictStr = Field(min_length=1, max_length=80)
    filename: StrictStr = Field(min_length=1, max_length=255)
    storageKey: StrictStr = Field(min_length=1, max_length=500)
    mimeType: StrictStr = Field(min_length=1, max_length=120)
    sizeBytes: Annotated[int, Field(strict=True, ge=0)]
    sha256: StrictStr = Field(min_length=64, max_length=64)


class NoteBody(_Body):
    content: StrictStr = Field(min_length=1, max_length=100_000)


class RevisionBody(_Body):
    expectedRevision: Annotated[int, Field(strict=True, ge=1)]


def create_reproduction_router() -> APIRouter:
    router = APIRouter()

    @router.get("/reproductions")
    async def list_reproductions(
        request: Request,
        q: Annotated[str | None, Query(max_length=200)] = None,
        status_filter: Annotated[str | None, Query(alias="status", max_length=30)] = None,
        tag: Annotated[str | None, Query(max_length=50)] = None,
        sort: Literal["updated", "name"] = "updated",
        limit: Annotated[int, Query(ge=1, le=100)] = 25,
        offset: Annotated[int, Query(ge=0)] = 0,
    ) -> dict[str, object]:
        return await _workspace(request).list_projects(
            query=q, status=status_filter, tag=tag, sort=sort, limit=limit, offset=offset
        )

    @router.post("/reproductions", status_code=status.HTTP_201_CREATED)
    async def create_reproduction(request: Request, body: CreateProjectBody) -> dict[str, object]:
        return await _workspace(request).create_project(
            paper_id=body.paperId, name=body.name, tags=list(body.tags)
        )

    @router.get("/reproductions/{project_id}")
    async def get_reproduction(request: Request, project_id: str = Path(min_length=1, max_length=100)) -> dict[str, object]:
        return await _workspace(request).get_project(project_id)

    @router.patch("/reproductions/{project_id}")
    async def update_reproduction(request: Request, body: UpdateProjectBody, project_id: str = Path(min_length=1, max_length=100)) -> dict[str, object]:
        return await _workspace(request).update_project(
            project_id, expected_revision=body.expectedRevision, name=body.name,
            status=body.status, tags=list(body.tags) if body.tags is not None else None
        )

    @router.post("/reproductions/{project_id}/archive")
    async def archive_reproduction(request: Request, body: RevisionBody, project_id: str = Path(min_length=1, max_length=100)) -> dict[str, object]:
        return await _workspace(request).archive_project(project_id, expected_revision=body.expectedRevision)

    @router.delete("/reproductions/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
    async def delete_reproduction(request: Request, body: RevisionBody, project_id: str = Path(min_length=1, max_length=100)) -> Response:
        await _workspace(request).delete_project(project_id, expected_revision=body.expectedRevision)
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @router.put("/reproductions/{project_id}/document")
    async def save_reproduction_document(request: Request, body: SaveDocumentBody, project_id: str = Path(min_length=1, max_length=100)) -> dict[str, object]:
        return await _workspace(request).save_document(
            project_id, content=body.content, expected_revision=body.expectedRevision
        )

    @router.post("/reproductions/{project_id}/runs", status_code=status.HTTP_201_CREATED)
    async def add_experiment_run(request: Request, body: RunBody, project_id: str = Path(min_length=1, max_length=100)) -> dict[str, object]:
        return await _workspace(request).add_run(project_id, body.model_dump())

    @router.get("/reproductions/{project_id}/runs")
    async def list_experiment_runs(request: Request, project_id: str = Path(min_length=1, max_length=100)) -> list[dict[str, object]]:
        project = await _workspace(request).get_project(project_id)
        return list(project["runs"])

    @router.post("/reproductions/{project_id}/artifacts", status_code=status.HTTP_201_CREATED)
    async def add_reproduction_artifact(request: Request, project_id: str = Path(min_length=1, max_length=100)) -> dict[str, object]:
        content_type = request.headers.get("content-type", "").lower()
        workspace = _workspace(request)
        if content_type.startswith("multipart/form-data"):
            form = await request.form()
            upload = form.get("file")
            if upload is None or not hasattr(upload, "filename") or not hasattr(upload, "read"):
                from backend.app.domain import ReproductionValidationError
                raise ReproductionValidationError()
            run_id_value = form.get("runId")
            run_id = str(run_id_value) if run_id_value else None
            return await workspace.upload_artifact(
                project_id,
                filename=str(upload.filename or ""),
                mime_type=str(upload.content_type or ""),
                stream=upload,
                kind=str(form.get("kind") or "") or None,
                run_id=run_id,
            )
        if not content_type.startswith("application/json"):
            from backend.app.domain import ReproductionValidationError
            raise ReproductionValidationError()
        try:
            body = ArtifactBody.model_validate(await request.json())
        except (ValueError, TypeError) as error:
            from backend.app.domain import ReproductionValidationError
            raise ReproductionValidationError() from error
        return await workspace.add_artifact(project_id, body.model_dump())

    @router.get("/reproductions/{project_id}/artifacts")
    async def list_reproduction_artifacts(request: Request, project_id: str = Path(min_length=1, max_length=100)) -> list[dict[str, object]]:
        project = await _workspace(request).get_project(project_id)
        return list(project["artifacts"])

    @router.get("/reproductions/{project_id}/artifacts/{artifact_id}/download")
    async def download_reproduction_artifact(
        request: Request,
        project_id: str = Path(min_length=1, max_length=100),
        artifact_id: str = Path(min_length=1, max_length=100),
    ) -> Response:
        project = await _workspace(request).get_project(project_id)
        artifact = next((item for item in project["artifacts"] if item["id"] == artifact_id), None)
        if artifact is None:
            from backend.app.domain import ReproductionNotFoundError
            raise ReproductionNotFoundError(project_id=project_id)
        path = _workspace(request).artifact_path(
            str(artifact["storageKey"]), project_id=project_id
        )
        if not path.is_file():
            from backend.app.domain import ReproductionNotFoundError
            raise ReproductionNotFoundError(project_id=project_id)
        from fastapi.responses import FileResponse
        return FileResponse(path, media_type=str(artifact["mimeType"]), filename=str(artifact["filename"]))

    @router.post("/reproductions/{project_id}/notes", status_code=status.HTTP_201_CREATED)
    async def add_reproduction_note(request: Request, body: NoteBody, project_id: str = Path(min_length=1, max_length=100)) -> dict[str, object]:
        return await _workspace(request).add_note(project_id, body.content)

    @router.get("/reproductions/{project_id}/notes")
    async def list_reproduction_notes(request: Request, project_id: str = Path(min_length=1, max_length=100)) -> list[dict[str, object]]:
        project = await _workspace(request).get_project(project_id)
        return list(project["notes"])

    return router


def _workspace(request: Request) -> Any:
    workspace = getattr(request.app.state.container, "reproduction_workspace", None)
    if workspace is None:
        raise RuntimeError("reproduction workspace is not configured")
    return workspace
