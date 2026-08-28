from __future__ import annotations

import json
from types import SimpleNamespace
import unittest

from fastapi import FastAPI
from starlette.requests import Request

from backend.app.api.routes.legacy import create_legacy_router


class _Settings:
    def __init__(self) -> None:
        self.test_patch: dict[str, object] | None = None

    async def test_llm(self, patch: dict[str, object]) -> dict[str, object]:
        self.test_patch = patch
        return {"ok": True}


class SettingsRouteTests(unittest.IsolatedAsyncioTestCase):
    async def test_llm_route_forwards_current_form_values(self) -> None:
        settings = _Settings()
        app = FastAPI()
        app.state.container = SimpleNamespace(
            legacy=SimpleNamespace(settings=settings)
        )
        router = create_legacy_router()
        endpoint = next(
            route.endpoint
            for route in router.routes
            if getattr(route, "path", None) == "/api/test-llm"
        )
        payload = {
            "provider": "other",
            "baseUrl": "https://form.example/v1",
            "model": "form-model",
            "llmTimeout": 4200,
            "apiKey": "form-secret",
        }
        raw = json.dumps(payload).encode("utf-8")
        delivered = False

        async def receive() -> dict[str, object]:
            nonlocal delivered
            if delivered:
                return {"type": "http.request", "body": b"", "more_body": False}
            delivered = True
            return {"type": "http.request", "body": raw, "more_body": False}

        request = Request(
            {
                "type": "http",
                "http_version": "1.1",
                "method": "POST",
                "scheme": "http",
                "path": "/api/test-llm",
                "raw_path": b"/api/test-llm",
                "query_string": b"",
                "headers": [(b"content-type", b"application/json")],
                "client": ("127.0.0.1", 10000),
                "server": ("127.0.0.1", 5173),
                "app": app,
            },
            receive,
        )

        response = await endpoint(request)

        self.assertEqual(200, response.status_code)
        self.assertEqual(payload, settings.test_patch)


if __name__ == "__main__":
    unittest.main()
