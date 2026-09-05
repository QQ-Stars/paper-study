from __future__ import annotations

from fastapi import APIRouter

from backend.app.api.routes.document_processing import create_document_processing_router
from backend.app.api.routes.document_consumers import create_document_consumer_router
from backend.app.api.routes.document_search import create_document_search_router
from backend.app.api.routes.legacy import create_legacy_router
from backend.app.api.routes.obsidian import create_obsidian_router
from backend.app.api.routes.reproductions import create_reproduction_router


def create_router(required_schema_revision: str) -> APIRouter:
    router = APIRouter()
    v2_router = APIRouter(prefix="/api/v2")

    @v2_router.get("/health")
    async def health() -> dict[str, object]:
        return {"ok": True, "schemaRevision": required_schema_revision}

    if required_schema_revision != "20260807_01":
        v2_router.include_router(create_document_processing_router())
    if required_schema_revision in {"20260807_03", "20260825_04", "20260826_01", "20260829_01", "20260830_01"}:
        v2_router.include_router(create_document_consumer_router())
        v2_router.include_router(create_document_search_router())
        v2_router.include_router(create_obsidian_router())
    if required_schema_revision in {"20260825_04", "20260826_01", "20260829_01", "20260830_01"}:
        v2_router.include_router(create_reproduction_router())

    router.include_router(v2_router)
    router.include_router(create_legacy_router())
    return router
