from __future__ import annotations

import asyncio
from types import SimpleNamespace
import unittest

from fastapi.testclient import TestClient

from backend.app.api.app import create_app
from backend.tests.support.p3_database import p3_database_fixture


class ApiV2Tests(unittest.TestCase):
    def test_p2_p3_routes_are_mounted_once_and_typed(self) -> None:
        async def scenario() -> None:
            async with p3_database_fixture(prefix="study-app-p4-v2-routes-") as fixture:
                class Services:
                    schema_revision = "20260807_03"
                    # OpenAPI inspection must not construct providers or execute jobs.
                    processing_api = object()
                    document_artifacts = object()
                    document_search = object()
                    embedding_profile = None
                    legacy = SimpleNamespace()

                    async def dispose(self) -> None:
                        return None

                app = create_app(
                    Services(),
                    fixture.session_factory,
                    required_schema_revision="20260807_03",
                )
                with TestClient(app) as client:
                    paths = client.app.openapi()["paths"]

                expected = {
                    "/api/v2/papers/{paper_id}/sources": {"get", "post"},
                    "/api/v2/papers/{paper_id}/artifacts": {"get"},
                    "/api/v2/papers/{paper_id}/artifacts/explainer": {"post"},
                    "/api/v2/papers/{paper_id}/artifacts/translation": {"post"},
                    "/api/v2/papers/{paper_id}/artifacts/classification": {"post"},
                    "/api/v2/papers/{paper_id}/artifacts/metadata": {"post"},
                    "/api/v2/papers/{paper_id}/artifacts/summary": {"post"},
                    "/api/v2/papers/{paper_id}/index": {"post"},
                    "/api/v2/papers/{paper_id}/index-status": {"get"},
                    "/api/v2/search/chunks": {"post"},
                    "/api/v2/jobs": {"get"},
                    "/api/v2/jobs/{job_id}": {"get"},
                    "/api/v2/jobs/{job_id}/events": {"get"},
                    "/api/v2/jobs/{job_id}/cancel": {"post"},
                    "/api/v2/jobs/{job_id}/retry": {"post"},
                }
                for path, methods in expected.items():
                    self.assertIn(path, paths, path)
                    for method in methods:
                        self.assertIn(method, paths[path], f"{method} {path}")
                forbidden = {
                    "/api/v2/jobs/{job_id}/delete",
                    "/api/v2/obsidian",
                    "/api/v2/papers/{paper_id}/artifacts/{kind}",
                    "/api/v2/papers/{paper_id}/artifacts/{kind}/create",
                    "/api/v2/sources",
                    "/api/v2/artifacts",
                    "/api/v2/exports",
                }
                self.assertTrue(forbidden.isdisjoint(paths))

                # The wire contract is camelCase and strict: snake_case must not
                # appear as an externally accepted request property.
                source_schema = paths["/api/v2/papers/{paper_id}/sources"]["post"]
                source_ref = source_schema["requestBody"]["content"]["application/json"]["schema"]
                source_schema_name = source_ref["$ref"].rsplit("/", 1)[-1]
                schema = client.app.openapi()["components"]["schemas"][source_schema_name]
                self.assertIn("sourceMode", schema["properties"])
                self.assertNotIn("source_mode", schema["properties"])
                self.assertIn("sourceDocumentId", client.app.openapi()["components"]["schemas"]["ExplainerEnqueueRequest"]["properties"])
                self.assertNotIn("source_document_id", str(client.app.openapi()))

                # FastAPI must not synthesize an untyped generic jobs/artifacts
                # operation when the typed routers are mounted more than once.
                operation_ids = [
                    operation.get("operationId")
                    for item in paths.values()
                    for operation in item.values()
                    if isinstance(operation, dict)
                ]
                self.assertEqual(len(operation_ids), len(set(operation_ids)))

        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()
