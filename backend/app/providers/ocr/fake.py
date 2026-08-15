from __future__ import annotations

import hashlib
from types import MappingProxyType
from typing import Mapping

from backend.app.application.ports.ocr_provider import (
    OcrPageResult,
    OcrRequest,
    OcrResult,
)


class FakeOcrProvider:
    provider_id = "fake"
    processing_version = "fake-ocr-v1"

    def __init__(
        self,
        *,
        pages: Mapping[int, str],
        failures: Mapping[int, Exception] | None = None,
    ) -> None:
        self._pages = MappingProxyType(dict(pages))
        self._failures = MappingProxyType(dict(failures or {}))
        self.calls: list[OcrRequest] = []

    async def extract_batch(self, request: OcrRequest) -> OcrResult:
        self.calls.append(request)
        for page_number in request.page_numbers:
            failure = self._failures.get(page_number)
            if failure is not None:
                raise failure

        pages = tuple(
            self._page_result(page_number)
            for page_number in request.page_numbers
        )
        return OcrResult(
            pages=pages,
            provider=self.provider_id,
            model=request.model,
            processing_version=self.processing_version,
            provider_request_id=None,
        )

    def _page_result(self, page_number: int) -> OcrPageResult:
        markdown = self._pages[page_number]
        return OcrPageResult(
            page_number=page_number,
            markdown=markdown,
            content_sha256=hashlib.sha256(markdown.encode("utf-8")).hexdigest(),
            provider_page_id=f"fake-page-{page_number}",
        )
