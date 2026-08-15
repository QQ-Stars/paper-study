from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

from backend.app.domain.context import (
    EmbeddingBatch,
    EmbeddingProfile,
    EmbeddingRequest,
)
from backend.app.domain import (
    EmbeddingRequestFailedError,
    EmbeddingResponseInvalidError,
)


class Model2VecEmbeddingProvider:
    """Lazy local adapter; import and construction never load model bytes."""

    def __init__(
        self,
        profile: EmbeddingProfile,
        *,
        model_loader: Callable[[str], Any] | None = None,
    ) -> None:
        if not isinstance(profile, EmbeddingProfile):
            raise ValueError("profile must be EmbeddingProfile")
        if profile.provider != "model2vec":
            raise ValueError("model2vec profile provider is invalid")
        self.profile = profile
        self._model_loader = model_loader or _load_static_model
        self._model: Any | None = None
        self._load_lock = asyncio.Lock()

    @property
    def provider_id(self) -> str:
        return self.profile.provider

    async def embed(self, request: EmbeddingRequest) -> EmbeddingBatch:
        if not isinstance(request, EmbeddingRequest) or request.profile != self.profile:
            raise ValueError("embedding request profile mismatch")
        try:
            model = await self._get_model()
            raw_vectors = await asyncio.to_thread(model.encode, list(request.texts))
        except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
            raise
        except Exception:
            raise EmbeddingRequestFailedError(retryable=True) from None
        try:
            vectors = tuple(
                tuple(float(value) for value in vector) for vector in raw_vectors
            )
            return EmbeddingBatch(
                profile=self.profile,
                vectors=vectors,
                chunk_ids=request.chunk_ids,
            )
        except (TypeError, ValueError, OverflowError) as error:
            raise EmbeddingResponseInvalidError() from error

    async def _get_model(self) -> Any:
        if self._model is not None:
            return self._model
        async with self._load_lock:
            if self._model is None:
                self._model = await asyncio.to_thread(
                    self._model_loader,
                    self.profile.model,
                )
        return self._model


def _load_static_model(model_name: str) -> Any:
    # Delayed import is part of the adapter contract: application startup and
    # lexical/query-only paths must not initialize model2vec or its cache.
    from model2vec import StaticModel

    return StaticModel.from_pretrained(model_name)


__all__ = ["Model2VecEmbeddingProvider"]
