from __future__ import annotations

import asyncio
import math
from datetime import datetime, timezone
from typing import Any

from backend.app.domain import (
    Credential,
    CredentialKind,
    EmbeddingRequestFailedError,
    EmbeddingResponseInvalidError,
)
from backend.app.domain.context import (
    EmbeddingBatch,
    EmbeddingProfile,
    EmbeddingRequest,
)
from backend.app.providers.ocr.retry_after import normalize_retry_after


class OpenAiCompatibleEmbeddingProvider:
    """Strict `/embeddings` adapter with credential-free public identity."""

    def __init__(
        self,
        profile: EmbeddingProfile,
        credential: Credential,
        *,
        transport: Any,
        timeout_seconds: float = 60.0,
        clock=None,
    ) -> None:
        if not isinstance(profile, EmbeddingProfile):
            raise ValueError("profile must be EmbeddingProfile")
        if profile.provider != "openai-compatible":
            raise ValueError("OpenAI-compatible profile provider is invalid")
        if not isinstance(credential, Credential) or credential.kind is not CredentialKind.EMBEDDING:
            raise ValueError("embedding credential is required")
        if (
            not isinstance(timeout_seconds, (int, float))
            or isinstance(timeout_seconds, bool)
            or not math.isfinite(float(timeout_seconds))
            or timeout_seconds <= 0
        ):
            raise ValueError("timeout_seconds must be finite and positive")
        if transport is None or not callable(getattr(transport, "post", None)):
            raise ValueError("transport must provide async post")
        self.profile = profile
        self._credential = credential
        self._transport = transport
        self._timeout_seconds = float(timeout_seconds)
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    @property
    def provider_id(self) -> str:
        return self.profile.provider

    def __repr__(self) -> str:
        return (
            "OpenAiCompatibleEmbeddingProvider("
            f"provider={self.profile.provider!r}, model={self.profile.model!r}, "
            "credential=<redacted>)"
        )

    async def embed(self, request: EmbeddingRequest) -> EmbeddingBatch:
        if not isinstance(request, EmbeddingRequest) or request.profile != self.profile:
            raise ValueError("embedding request profile mismatch")
        try:
            response = await self._transport.post(
                "/embeddings",
                headers={
                    "Authorization": f"Bearer {self._credential.value}",
                    "Content-Type": "application/json",
                },
                json={"model": self.profile.model, "input": list(request.texts)},
                timeout=self._timeout_seconds,
            )
        except (asyncio.TimeoutError, TimeoutError, OSError):
            raise EmbeddingRequestFailedError(retryable=True) from None
        status = getattr(response, "status_code", None)
        if status != 200:
            retryable = status == 429 or (
                isinstance(status, int) and 500 <= status <= 599
            )
            headers = getattr(response, "headers", {})
            retry_after = None
            if status == 429 and hasattr(headers, "get"):
                retry_after = normalize_retry_after(
                    headers.get("Retry-After"),
                    now=self._clock(),
                )
            raise EmbeddingRequestFailedError(
                retryable=retryable,
                retry_after_seconds=retry_after,
            )
        try:
            payload = response.json()
            if not isinstance(payload, dict) or set(payload) != {"data"}:
                raise ValueError
            data = payload["data"]
            if not isinstance(data, list) or len(data) != len(request.chunk_ids):
                raise ValueError
            ordered: list[tuple[float, ...] | None] = [None] * len(data)
            for item in data:
                if not isinstance(item, dict) or set(item) != {"index", "embedding"}:
                    raise ValueError
                index = item["index"]
                vector = item["embedding"]
                if (
                    not isinstance(index, int)
                    or isinstance(index, bool)
                    or not 0 <= index < len(ordered)
                    or ordered[index] is not None
                    or not isinstance(vector, list)
                ):
                    raise ValueError
                ordered[index] = tuple(float(value) for value in vector)
            if any(vector is None for vector in ordered):
                raise ValueError
            return EmbeddingBatch(
                profile=self.profile,
                vectors=tuple(vector for vector in ordered if vector is not None),
                chunk_ids=request.chunk_ids,
            )
        except Exception:
            raise EmbeddingResponseInvalidError() from None


__all__ = ["OpenAiCompatibleEmbeddingProvider"]
