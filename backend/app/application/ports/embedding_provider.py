from __future__ import annotations

from typing import Protocol

from backend.app.domain.context import EmbeddingBatch, EmbeddingRequest


class EmbeddingProvider(Protocol):
    @property
    def provider_id(self) -> str: ...

    async def embed(self, request: EmbeddingRequest) -> EmbeddingBatch: ...


__all__ = ["EmbeddingProvider"]
