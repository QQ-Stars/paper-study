from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr

from backend.app.api.routes.document_processing import InvalidRequestError
from backend.app.domain.context import SearchRequest


class SearchChunksRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: StrictStr
    mode: Literal["lexical", "semantic", "hybrid"]
    paperIds: tuple[StrictStr, ...] = ()
    limit: Annotated[StrictInt, Field(ge=1, le=50)] = 20


def _search_item(item) -> dict[str, object]:
    return {
        "paperId": item.paper_id,
        "sourceDocumentId": item.source_document_id,
        "chunkId": item.chunk_id,
        "sequence": item.sequence,
        "headingPath": list(item.heading_path),
        "pageStart": item.page_start,
        "pageEnd": item.page_end,
        "excerpt": item.excerpt,
        "score": item.score,
        "lexicalScore": item.lexical_score,
        "semanticScore": item.semantic_score,
    }


def _coverage(value) -> dict[str, object]:
    return {
        "readyChunks": value.ready_chunks,
        "embeddedChunks": value.embedded_chunks,
        "staleChunks": value.stale_chunks,
        "failedEmbeddings": value.failed_embeddings,
    }


def create_document_search_router() -> APIRouter:
    router = APIRouter()

    @router.post("/search/chunks")
    async def search_chunks(
        body: SearchChunksRequest,
        request: Request,
    ) -> dict[str, object]:
        service = getattr(request.app.state.container, "document_search", None)
        if service is None:
            raise InvalidRequestError()
        try:
            result = await service.search(
                SearchRequest(
                    query=body.query,
                    mode=body.mode,
                    paper_ids=tuple(body.paperIds),
                    limit=body.limit,
                )
            )
        except ValueError as error:
            raise InvalidRequestError() from error
        return {
            "items": [_search_item(item) for item in result.items],
            "coverage": _coverage(result.coverage),
        }

    return router


__all__ = ["SearchChunksRequest", "create_document_search_router"]
