from __future__ import annotations

from fastapi import APIRouter, Request

from backend.app.api.schemas.obsidian import (
    ObsidianExportRequest,
    ObsidianSyncRequest,
)
from backend.app.domain import ObsidianDisabledError


def create_obsidian_router() -> APIRouter:
    router = APIRouter()

    async def effective_settings(request: Request):
        service = request.app.state.container.legacy.settings
        return await service.obsidian()

    async def require_enabled(request: Request):
        settings = await effective_settings(request)
        if not settings.enabled:
            raise ObsidianDisabledError()
        return settings

    @router.get("/obsidian/status")
    async def status(request: Request) -> dict[str, object]:
        await effective_settings(request)
        service = getattr(request.app.state.container, "obsidian_jobs", None)
        if service is None:
            settings = await effective_settings(request)
            return {
                "enabled": settings.enabled,
                "vaultConfigured": bool(settings.vault_path),
                "writable": False,
                "rootFolder": settings.root_folder,
                "pdfMode": settings.pdf_mode,
                "lastJob": None,
                "aggregate": {
                    key: 0
                    for key in (
                        "exported", "unchanged", "conflicts", "errors",
                        "skipped", "userManaged", "orphaned", "deleted",
                    )
                },
            }
        return await service.status()

    @router.post("/papers/{paper_id}/exports/obsidian", status_code=202)
    async def export_paper(
        paper_id: str, body: ObsidianExportRequest, request: Request
    ) -> dict[str, object]:
        await require_enabled(request)
        result = await request.app.state.container.obsidian_jobs.enqueue_export(
            paper_id,
            dry_run=body.dryRun,
        )
        return {"job": _job_summary(result.job), "deduplicated": result.deduplicated}

    @router.post("/obsidian/sync", status_code=202)
    async def sync(
        body: ObsidianSyncRequest, request: Request
    ) -> dict[str, object]:
        await require_enabled(request)
        result = await request.app.state.container.obsidian_jobs.enqueue_sync(
            dry_run=body.dryRun,
            apply_cleanup=body.applyCleanup,
            cleanup_plan_sha=body.cleanupPlanSha,
        )
        return {"job": _job_summary(result.job), "deduplicated": result.deduplicated}

    @router.post("/obsidian/test")
    async def test(request: Request) -> dict[str, object]:
        await require_enabled(request)
        return {"ok": await request.app.state.container.obsidian_jobs.test_access()}

    return router


def _job_summary(job: object) -> dict[str, object]:
    job_type = getattr(job, "job_type")
    status = getattr(job, "status")
    source_mode = getattr(job, "source_mode")
    return {
        "id": getattr(job, "id"),
        "paperId": getattr(job, "paper_id"),
        "jobType": getattr(job_type, "value", job_type),
        "sourceMode": getattr(source_mode, "value", source_mode),
        "status": getattr(status, "value", status),
    }


__all__ = ["create_obsidian_router"]
