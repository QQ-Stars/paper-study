from __future__ import annotations

import asyncio
import hashlib
from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest import mock

from fastapi.testclient import TestClient

from backend.app.api.app import create_app
from backend.app.application.ports.artifact_generator import GeneratorIdentity
from backend.app.application.ports.source_extractor import ExtractedSource
from backend.app.bootstrap import RolloutSettings, bootstrap
from backend.app.config import DatabaseSettings
from backend.app.domain import GenerationFailureError, SchemaRevisionMismatchError
from backend.tests.support.p1_database import create_legacy_database, run_alembic


class ApiFoundationTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory(prefix="study-app-api-foundation-")
        self.root = Path(self._temp.name)
        self.database_path = self.root / "legacy" / "app.db"
        create_legacy_database(self.database_path)

    def tearDown(self) -> None:
        self._temp.cleanup()

    def test_health_uses_injected_container_and_never_binds_a_port(self) -> None:
        run_alembic(self.database_path, "20260807_01")
        container = bootstrap(
            RolloutSettings(document_pipeline_mode="p1"),
            DatabaseSettings(self.database_path),
            required_schema_revision="20260807_01",
        )
        app = create_app(
            settings=container,
            dependencies=container.session_factory,
            required_schema_revision="20260807_01",
        )
        with TestClient(app) as client:
            response = client.get("/api/v2/health")
        self.assertEqual(200, response.status_code)
        self.assertEqual(
            {"ok": True, "schemaRevision": "20260807_01"},
            response.json(),
        )
        self.assertFalse(hasattr(app.state, "server"))

    def test_typed_error_serialization_is_exact_and_sanitized(self) -> None:
        run_alembic(self.database_path, "20260807_01")
        container = bootstrap(
            RolloutSettings(document_pipeline_mode="p1"),
            DatabaseSettings(self.database_path),
            required_schema_revision="20260807_01",
        )
        app = create_app(
            container,
            container.session_factory,
            required_schema_revision="20260807_01",
        )

        @app.get("/raise-domain-error")
        async def raise_domain_error() -> None:
            raise GenerationFailureError(
                paper_id="paper-1",
                provider_body="raw-provider-body",
                credential="TOP-SECRET",
                authorization="Bearer TOP-SECRET",
                sql="SELECT secret",
                pdf_text="private PDF text",
            )

        with TestClient(app, raise_server_exceptions=False) as client:
            response = client.get("/raise-domain-error")
        self.assertEqual(
            {
                "error": {
                    "code": "GENERATION_FAILED",
                    "message": "The artifact could not be generated.",
                    "details": {"paper_id": "paper-1"},
                }
            },
            response.json(),
        )
        rendered = response.text
        for secret in ("TOP-SECRET", "raw-provider-body", "SELECT secret", "private PDF text"):
            self.assertNotIn(secret, rendered)

    def test_all_legacy_bootstrap_does_not_inspect_schema_or_construct_providers(self) -> None:
        before = self.database_path.read_bytes()
        counters = {"native": 0, "generation": 0}

        def native_factory():
            counters["native"] += 1
            raise AssertionError("native provider must not be constructed")

        def generation_factory():
            counters["generation"] += 1
            raise AssertionError("generation provider must not be constructed")

        container = bootstrap(
            RolloutSettings(),
            DatabaseSettings(self.database_path),
            required_schema_revision="20260807_01",
            native_provider_factory=native_factory,
            generation_provider_factory=generation_factory,
        )
        self.assertIsNone(container.schema_revision)
        self.assertIsNone(container.session_factory)
        self.assertEqual({"native": 0, "generation": 0}, counters)
        self.assertEqual(before, self.database_path.read_bytes())

    def test_p1_revision_gate_rejects_missing_wrong_multiple_and_symbolic_heads(self) -> None:
        settings = DatabaseSettings(self.database_path)
        with self.assertRaises(SchemaRevisionMismatchError):
            bootstrap(
                RolloutSettings(document_pipeline_mode="p1"),
                settings,
                required_schema_revision="20260807_01",
            )
        run_alembic(self.database_path, "20260807_01")
        with self.assertRaises(SchemaRevisionMismatchError):
            bootstrap(
                RolloutSettings(document_pipeline_mode="p1"),
                settings,
                required_schema_revision="20260807_02",
            )
        with self.assertRaises(ValueError):
            bootstrap(
                RolloutSettings(document_pipeline_mode="p1"),
                settings,
                required_schema_revision="head",
            )
        with closing(sqlite3.connect(self.database_path)) as connection:
            connection.execute("INSERT INTO alembic_version(version_num) VALUES('another_head')")
            connection.commit()
        with self.assertRaises(SchemaRevisionMismatchError):
            bootstrap(
                RolloutSettings(document_pipeline_mode="p1"),
                settings,
                required_schema_revision="20260807_01",
            )

    def test_p3_bootstrap_rejects_missing_provider_configuration_before_engine_creation(
        self,
    ) -> None:
        """A P3 API composition error must not allocate an undisposable engine."""
        from backend.app import bootstrap as bootstrap_module

        run_alembic(self.database_path, "20260807_03")
        with mock.patch.object(
            bootstrap_module,
            "create_async_session_factory",
            side_effect=AssertionError(
                "P3 configuration must be checked before engine creation"
            ),
        ) as create_session_factory:
            with self.assertRaisesRegex(ValueError, "P3 composition requires"):
                bootstrap(
                    RolloutSettings(
                        document_pipeline_mode="p1",
                        processing_cursor_secret="x" * 32,
                    ),
                    DatabaseSettings(self.database_path),
                    required_schema_revision="20260807_03",
                )
        create_session_factory.assert_not_called()

    def test_p1_bootstrap_is_read_only_and_has_zero_ocr_object_graph(self) -> None:
        run_alembic(self.database_path, "20260807_01")
        before = (
            self.database_path.read_bytes(),
            self.database_path.stat().st_size,
            self.database_path.stat().st_mtime_ns,
        )
        counters = {"native": 0, "generation": 0, "ocr": 0}

        class Native:
            provider = "local"
            model = "native-model"
            processing_version = "native-v1"

        class Generator:
            pass

        def native_factory():
            counters["native"] += 1
            return Native()

        def generation_factory():
            counters["generation"] += 1
            return Generator()

        container = bootstrap(
            RolloutSettings(
                document_pipeline_mode="p1",
                generation_pipeline_mode="p1",
                artifact_read_mode="prefer_new",
                artifact_write_mode="dual",
                ocr_enabled=False,
            ),
            DatabaseSettings(self.database_path),
            required_schema_revision="20260807_01",
            native_provider_factory=native_factory,
            generation_provider_factory=generation_factory,
        )
        after = (
            self.database_path.read_bytes(),
            self.database_path.stat().st_size,
            self.database_path.stat().st_mtime_ns,
        )
        self.assertEqual(before, after)
        self.assertEqual({"native": 1, "generation": 1, "ocr": 0}, counters)
        self.assertFalse(hasattr(container, "ocr"))
        self.assertEqual("20260807_01", container.schema_revision)

    def test_p1_modes_require_head_and_runtime_rollback_uses_only_legacy_adapters(self) -> None:
        with closing(sqlite3.connect(self.database_path)) as connection:
            connection.execute(
                "UPDATE papers SET explainer='legacy before canary' WHERE id='paper-1'"
            )
            connection.commit()

        provider_calls = {"native": 0, "generation": 0}

        def forbidden_native():
            provider_calls["native"] += 1
            raise AssertionError("legacy startup constructed the native provider")

        def forbidden_generation():
            provider_calls["generation"] += 1
            raise AssertionError("legacy startup constructed the generation provider")

        legacy_container = bootstrap(
            RolloutSettings(),
            DatabaseSettings(self.database_path),
            required_schema_revision="20260807_01",
            native_provider_factory=forbidden_native,
            generation_provider_factory=forbidden_generation,
        )
        self.assertIsNone(legacy_container.session_factory)
        self.assertEqual({"native": 0, "generation": 0}, provider_calls)
        with closing(sqlite3.connect(self.database_path)) as connection:
            self.assertEqual(
                "legacy before canary",
                connection.execute(
                    "SELECT explainer FROM papers WHERE id='paper-1'"
                ).fetchone()[0],
            )

        run_alembic(self.database_path, "20260807_02")
        pdf_path = self.root / "paper.pdf"
        pdf_path.write_bytes(b"stable fixture bytes")
        with closing(sqlite3.connect(self.database_path)) as connection:
            connection.execute(
                "UPDATE papers SET pdf_path=? WHERE id='paper-1'",
                (str(pdf_path),),
            )
            connection.commit()

        class Native:
            provider = "local"
            model = "fixture-native"
            processing_version = "native-v1"

            def extract(self, _path: Path) -> ExtractedSource:
                markdown = "# proven source\n"
                return ExtractedSource(
                    markdown=markdown,
                    content_sha256=hashlib.sha256(markdown.encode("utf-8")).hexdigest(),
                    page_count=1,
                    provider=self.provider,
                    model=self.model,
                    processing_version=self.processing_version,
                )

        class Generator:
            def identity(self, kind: str) -> GeneratorIdentity:
                return GeneratorIdentity("fixture", "fixture-model", f"{kind}-v1")

            def generate(self, _kind: str, _paper: object, _markdown: str) -> str:
                return "# canary explainer\n"

        canary_container = bootstrap(
            RolloutSettings(
                api_backend_mode="legacy",
                document_pipeline_mode="p1",
                generation_pipeline_mode="p1",
                artifact_read_mode="prefer_new",
                artifact_write_mode="dual",
                ocr_enabled=False,
            ),
            DatabaseSettings(self.database_path),
            required_schema_revision="20260807_02",
            native_provider_factory=Native,
            generation_provider_factory=Generator,
            environment_snapshot={
                "PROCESSING_CURSOR_SECRET": "api-foundation-test-cursor-secret-at-least-32-bytes"
            },
        )

        async def exercise_canary() -> None:
            self.assertIsNotNone(canary_container.source_pipeline)
            self.assertIsNotNone(canary_container.generation_pipeline)
            self.assertIsNotNone(canary_container.artifact_reader)
            generated = await canary_container.generation_pipeline.generate_artifact(
                "paper-1",
                "explainer",
                "native",
            )
            preferred = await canary_container.artifact_reader.read(
                "paper-1",
                "explainer",
            )
            self.assertEqual(generated.id, preferred.artifact_id)
            self.assertEqual("new", preferred.provenance)
            self.assertEqual("# canary explainer\n", preferred.content)
            await canary_container.dispose()

        asyncio.run(exercise_canary())
        with closing(sqlite3.connect(self.database_path)) as connection:
            self.assertEqual(
                ("# canary explainer\n", 1),
                (
                    connection.execute(
                        "SELECT explainer FROM papers WHERE id='paper-1'"
                    ).fetchone()[0],
                    connection.execute(
                        "SELECT count(*) FROM generated_artifacts WHERE status='ready'"
                    ).fetchone()[0],
                ),
            )

        rollback_container = bootstrap(
            RolloutSettings(),
            DatabaseSettings(self.database_path),
            required_schema_revision="20260807_01",
            native_provider_factory=forbidden_native,
            generation_provider_factory=forbidden_generation,
        )
        self.assertIsNone(rollback_container.session_factory)
        self.assertIsNone(rollback_container.source_pipeline)
        self.assertIsNone(rollback_container.generation_pipeline)
        self.assertIsNone(rollback_container.artifact_reader)
        self.assertEqual({"native": 0, "generation": 0}, provider_calls)
        with closing(sqlite3.connect(self.database_path)) as connection:
            self.assertEqual(
                "# canary explainer\n",
                connection.execute(
                    "SELECT explainer FROM papers WHERE id='paper-1'"
                ).fetchone()[0],
            )
            self.assertEqual(
                1,
                connection.execute(
                    "SELECT count(*) FROM generated_artifacts WHERE status='ready'"
                ).fetchone()[0],
            )


if __name__ == "__main__":
    unittest.main()
