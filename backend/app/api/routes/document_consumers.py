from __future__ import annotations

from datetime import datetime, timezone
import inspect
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Query, Request, status
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictStr

from backend.app.api.routes.document_processing import (
    InvalidRequestError,
    SourceModeMismatchError,
    _artifact_summary,
    _new_job_summary,
)
from backend.app.domain import (
    ArtifactKindUnsupportedError,
    EmbeddingProfileUnavailableError,
)


class _StrictWireModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ArtifactConsumerRequest(_StrictWireModel):
    sourceMode: Literal["native", "ocr"]
    sourceDocumentId: Annotated[StrictStr, Field(min_length=1)]


class IndexEnqueueRequest(_StrictWireModel):
    sourceMode: Literal["native", "ocr"]
    sourceDocumentId: Annotated[StrictStr, Field(min_length=1)]
    includeEmbeddings: StrictBool


def _clock_now() -> datetime:
    return datetime.now(timezone.utc)


def _service(container: Any, name: str) -> Any:
    value = getattr(container, name, None)
    if value is None:
        raise InvalidRequestError()
    return value


def _status_payload(value: Any) -> dict[str, object]:
    to_api_dict = getattr(value, "to_api_dict", None)
    if callable(to_api_dict):
        payload = to_api_dict()
        if isinstance(payload, dict):
            return payload
        raise InvalidRequestError()
    if isinstance(value, dict):
        raw = value
    else:
        raw = {
            "total_chunks": getattr(value, "total_chunks", None),
            "ready_chunks": getattr(value, "ready_chunks", None),
            "embedded_chunks": getattr(value, "embedded_chunks", None),
            "stale_chunks": getattr(value, "stale_chunks", None),
            "failed_embeddings": getattr(value, "failed_embeddings", None),
            "provider": getattr(value, "provider", None),
            "model": getattr(value, "model", None),
            "version": getattr(value, "version", None),
            "coverage": getattr(value, "coverage", None),
        }
    return {
        "totalChunks": raw.get("total_chunks", raw.get("totalChunks")),
        "readyChunks": raw.get("ready_chunks", raw.get("readyChunks")),
        "embeddedChunks": raw.get("embedded_chunks", raw.get("embeddedChunks")),
        "staleChunks": raw.get("stale_chunks", raw.get("staleChunks")),
        "failedEmbeddings": raw.get("failed_embeddings", raw.get("failedEmbeddings")),
        "provider": raw.get("provider"),
        "model": raw.get("model"),
        "version": raw.get("version", raw.get("embedding_version")),
        "coverage": raw.get("coverage"),
    }


def create_document_consumer_router() -> APIRouter:
    router = APIRouter()

    async def enqueue_artifact_kind(
        paper_id: str,
        body: ArtifactConsumerRequest,
        request: Request,
        kind: Literal["translation", "classification", "metadata", "summary"],
    ) -> dict[str, object]:
        service = _service(request.app.state.container, "document_artifacts")
        result = await service.enqueue(
            paper_id,
            body.sourceDocumentId,
            body.sourceMode,
            kind,
            now=_clock_now(),
        )
        return {
            "artifact": _artifact_summary(result.artifact),
            "job": _new_job_summary(result.job),
            "deduplicated": result.deduplicated,
        }

    @router.post(
        "/papers/{paper_id}/artifacts/translation",
        status_code=status.HTTP_202_ACCEPTED,
    )
    async def enqueue_translation(
        paper_id: str,
        body: ArtifactConsumerRequest,
        request: Request,
    ) -> dict[str, object]:
        return await enqueue_artifact_kind(
            paper_id, body, request, "translation"
        )

    @router.post(
        "/papers/{paper_id}/artifacts/classification",
        status_code=status.HTTP_202_ACCEPTED,
    )
    async def enqueue_classification(
        paper_id: str,
        body: ArtifactConsumerRequest,
        request: Request,
    ) -> dict[str, object]:
        return await enqueue_artifact_kind(
            paper_id, body, request, "classification"
        )

    @router.post(
        "/papers/{paper_id}/artifacts/metadata",
        status_code=status.HTTP_202_ACCEPTED,
    )
    async def enqueue_metadata(
        paper_id: str,
        body: ArtifactConsumerRequest,
        request: Request,
    ) -> dict[str, object]:
        return await enqueue_artifact_kind(paper_id, body, request, "metadata")

    @router.post(
        "/papers/{paper_id}/artifacts/summary",
        status_code=status.HTTP_202_ACCEPTED,
    )
    async def enqueue_summary(
        paper_id: str,
        body: ArtifactConsumerRequest,
        request: Request,
    ) -> dict[str, object]:
        return await enqueue_artifact_kind(paper_id, body, request, "summary")

    @router.post(
        "/papers/{paper_id}/artifacts/{kind}",
        include_in_schema=False,
    )
    async def reject_unknown_artifact_kind(
        paper_id: str,
        kind: str,
        body: ArtifactConsumerRequest,
    ) -> None:
        del paper_id, body
        raise ArtifactKindUnsupportedError(artifact_kind=kind)

    @router.post(
        "/papers/{paper_id}/index",
        status_code=status.HTTP_202_ACCEPTED,
    )
    async def enqueue_index(
        paper_id: str,
        body: IndexEnqueueRequest,
        request: Request,
    ) -> dict[str, object]:
        service = _service(request.app.state.container, "document_search")
        profile = getattr(request.app.state.container, "embedding_profile", None)
        try:
            if body.includeEmbeddings:
                resolver = getattr(
                    request.app.state.container,
                    "embedding_profile_resolver",
                    None,
                )
                if callable(resolver):
                    resolved = resolver()
                    profile = (
                        await resolved if inspect.isawaitable(resolved) else resolved
                    )
            result = await service.enqueue_index(
                paper_id=paper_id,
                source_mode=body.sourceMode,
                source_document_id=body.sourceDocumentId,
                include_embeddings=body.includeEmbeddings,
                profile=profile if body.includeEmbeddings else None,
            )
        except EmbeddingProfileUnavailableError:
            raise
        except ValueError as error:
            raise InvalidRequestError() from error
        return {
            "job": _new_job_summary(result.job),
            "deduplicated": result.deduplicated,
        }

    @router.get("/papers/{paper_id}/index-status")
    async def index_status(
        paper_id: str,
        request: Request,
        sourceDocumentId: Annotated[StrictStr, Query(min_length=1)],
    ) -> dict[str, object]:
        unknown = sorted(set(request.query_params) - {"sourceDocumentId"})
        if unknown:
            raise InvalidRequestError()
        service = _service(request.app.state.container, "document_search")
        result = await service.status(sourceDocumentId, paper_id=paper_id)
        return _status_payload(result)

    return router


__all__ = [
    "ArtifactConsumerRequest",
    "IndexEnqueueRequest",
    "create_document_consumer_router",
]
