from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path
import sqlite3
import unittest

from fastapi.testclient import TestClient

from backend.app.api.app import create_app
from backend.app.application.ports.artifact_generator import GeneratorIdentity
from backend.app.bootstrap import RolloutSettings, bootstrap
from backend.app.config import DatabaseSettings
from backend.app.domain import SchemaRevisionMismatchError
from backend.app.domain.processing import (
    JobProgress,
    NewProcessingJob,
    ObsidianSyncJobSpecV1,
    encode_job_spec_v1,
    hash_job_spec,
)
from backend.app.providers.ocr.fake import FakeOcrProvider
from backend.app.providers.ocr.registry import create_test_ocr_registry
from backend.app.repositories.unit_of_work import SqlAlchemyUnitOfWork
from backend.tests.support.p2_database import (
    P2_TEST_PROCESSING_CURSOR_SECRET,
    p2_database_fixture,
)


class _NativeProvider:
    provider = "local"
    model = "pymupdf4llm-pymupdf"
    processing_version = "native-v1"

    def __init__(self) -> None:
        self.calls = 0

    def extract(self, _path: Path):
        self.calls += 1
        raise AssertionError("a 202 response must not execute the handler")


class _GenerationProvider:
    def __init__(self) -> None:
        self.calls = 0

    def identity(self, _kind: str, profile: str = "standard") -> GeneratorIdentity:
        return GeneratorIdentity("fake-generator", "fake-model", f"explainer-{profile}-v1")

    def generate(self, *_args, **_kwargs):
        self.calls += 1
        raise AssertionError("a 202 response must not execute the handler")


def _p2_rollout_settings(*, ocr_enabled: bool = False) -> RolloutSettings:
    return RolloutSettings(
        document_pipeline_mode="p1",
        generation_pipeline_mode="p1",
        ocr_enabled=ocr_enabled,
        processing_cursor_secret=P2_TEST_PROCESSING_CURSOR_SECRET,
    )


class ProcessingJobsApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self._database_context = p2_database_fixture(prefix="study-app-processing-api-")
        self.database = await self._database_context.__aenter__()
        self.pdf_path = self.database.database_path.parent / "paper-1.pdf"
        self.pdf_path.write_bytes(b"%PDF-1.4\nprocessing api fixture\n")
        with sqlite3.connect(self.database.database_path) as connection:
            connection.execute(
                "UPDATE papers SET pdf_path=? WHERE id='paper-1'",
                (str(self.pdf_path),),
            )
            connection.commit()
        self.native = _NativeProvider()
        self.generator = _GenerationProvider()
        self.container = bootstrap(
            _p2_rollout_settings(),
            DatabaseSettings(self.database.database_path),
            required_schema_revision="20260807_02",
            native_provider_factory=lambda: self.native,
            generation_provider_factory=lambda: self.generator,
        )
        self.client_context = TestClient(
            create_app(
                self.container,
                self.container.session_factory,
                required_schema_revision="20260807_02",
            )
        )
        self.client = self.client_context.__enter__()

    async def asyncTearDown(self) -> None:
        self.client_context.__exit__(None, None, None)
        await self._database_context.__aexit__(None, None, None)

    async def test_native_source_enqueue_is_exact_and_does_not_run_handler(self) -> None:
        response = self.client.post(
            "/api/v2/papers/paper-1/sources",
            json={"sourceMode": "native"},
        )

        self.assertEqual(202, response.status_code)
        payload = response.json()
        self.assertEqual(
            {
                "source": {
                    "id": payload["source"]["id"],
                    "paperId": "paper-1",
                    "mode": "native",
                    "status": "queued",
                },
                "job": {
                    "id": payload["job"]["id"],
                    "paperId": "paper-1",
                    "jobType": "source_materialize",
                    "sourceMode": "native",
                    "status": "queued",
                },
                "deduplicated": False,
            },
            payload,
        )
        self.assertEqual(0, self.native.calls)
        self.assertEqual(0, self.generator.calls)

    async def test_native_source_rejects_ocr_fields_with_safe_422(self) -> None:
        response = self.client.post(
            "/api/v2/papers/paper-1/sources",
            json={"sourceMode": "native", "ocrProvider": "secret-provider"},
        )

        self.assertEqual(422, response.status_code)
        self.assertEqual(
            {
                "error": {
                    "code": "OCR_REQUEST_INVALID",
                    "message": "The OCR request is invalid.",
                    "details": {"source_mode": "native"},
                }
            },
            response.json(),
        )
        self.assertNotIn("secret-provider", response.text)

    async def test_unknown_body_fields_use_the_safe_validation_contract(self) -> None:
        response = self.client.post(
            "/api/v2/papers/paper-1/sources",
            json={"sourceMode": "native", "authorization": "Bearer TOP-SECRET"},
        )

        self.assertEqual(422, response.status_code)
        self.assertEqual(
            {
                "error": {
                    "code": "INVALID_REQUEST",
                    "message": "Request validation failed.",
                    "details": {"field": "body.authorization"},
                }
            },
            response.json(),
        )
        self.assertNotIn("TOP-SECRET", response.text)

    async def test_ocr_disabled_is_409_and_writes_nothing(self) -> None:
        response = self.client.post(
            "/api/v2/papers/paper-1/sources",
            json={
                "sourceMode": "ocr",
                "ocrProvider": "fake",
                "ocrModel": "fake-ocr-v1",
                "options": {"pageBatchSize": 1, "maxConcurrency": 1},
            },
        )

        self.assertEqual(409, response.status_code)
        self.assertEqual("OCR_DISABLED", response.json()["error"]["code"])
        self.assertEqual((0, 0, 0), self._counts("document_sources", "processing_jobs", "ocr_page_checkpoints"))

    async def test_fake_ocr_enqueue_is_exact_deduplicated_and_never_calls_provider(self) -> None:
        fake = FakeOcrProvider(pages={1: "never called"})
        container = bootstrap(
            _p2_rollout_settings(ocr_enabled=True),
            DatabaseSettings(self.database.database_path),
            required_schema_revision="20260807_02",
            native_provider_factory=lambda: self.native,
            generation_provider_factory=lambda: self.generator,
            ocr_registry_factory=lambda: create_test_ocr_registry({"fake": fake}),
        )
        body = {
            "sourceMode": "ocr",
            "ocrProvider": "fake",
            "ocrModel": "fake-ocr-v1",
            "options": {"pageBatchSize": 1, "maxConcurrency": 1},
        }
        with TestClient(
            create_app(
                container,
                container.session_factory,
                required_schema_revision="20260807_02",
            )
        ) as client:
            first = client.post("/api/v2/papers/paper-1/sources", json=body)
            second = client.post("/api/v2/papers/paper-1/sources", json=body)

        self.assertEqual((202, 202), (first.status_code, second.status_code))
        first_payload = first.json()
        second_payload = second.json()
        self.assertEqual(first_payload["source"], second_payload["source"])
        self.assertEqual(first_payload["job"], second_payload["job"])
        self.assertFalse(first_payload["deduplicated"])
        self.assertTrue(second_payload["deduplicated"])
        self.assertEqual("ocr", first_payload["job"]["jobType"])
        self.assertEqual([], fake.calls)
        self.assertEqual((1, 1), self._counts("document_sources", "processing_jobs"))

    async def test_production_deepseek_fails_before_rows_or_pdf_reads(self) -> None:
        before_mtime = self.pdf_path.stat().st_atime_ns
        container = bootstrap(
            _p2_rollout_settings(ocr_enabled=True),
            DatabaseSettings(self.database.database_path),
            required_schema_revision="20260807_02",
            native_provider_factory=lambda: self.native,
            generation_provider_factory=lambda: self.generator,
        )
        with TestClient(
            create_app(
                container,
                container.session_factory,
                required_schema_revision="20260807_02",
            )
        ) as client:
            response = client.post(
                "/api/v2/papers/paper-1/sources",
                json={
                    "sourceMode": "ocr",
                    "ocrProvider": "deepseek",
                    "ocrModel": "client-model-must-not-be-trusted",
                },
            )

        self.assertEqual(503, response.status_code)
        self.assertEqual("OCR_PROVIDER_CONTRACT_UNVERIFIED", response.json()["error"]["code"])
        self.assertEqual((0, 0, 0), self._counts("document_sources", "processing_jobs", "ocr_page_checkpoints"))
        self.assertEqual(before_mtime, self.pdf_path.stat().st_atime_ns)
        self.assertNotIn("client-model-must-not-be-trusted", response.text)

    async def test_explainer_enqueue_is_exact_deduplicated_and_does_not_generate(self) -> None:
        source_response = self.client.post(
            "/api/v2/papers/paper-1/sources", json={"sourceMode": "native"}
        )
        source_id = source_response.json()["source"]["id"]
        markdown = "ready source\n"
        with sqlite3.connect(self.database.database_path) as connection:
            connection.execute(
                "UPDATE document_sources SET status='ready',markdown=?,content_sha256=?,page_count=1,ready_at=updated_at WHERE id=?",
                (markdown, hashlib.sha256(markdown.encode()).hexdigest(), source_id),
            )
            connection.commit()

        body = {"sourceMode": "native", "sourceDocumentId": source_id, "profile": "deep"}
        first = self.client.post("/api/v2/papers/paper-1/artifacts/explainer", json=body)
        second = self.client.post("/api/v2/papers/paper-1/artifacts/explainer", json=body)

        self.assertEqual((202, 202), (first.status_code, second.status_code))
        first_payload = first.json()
        self.assertEqual(
            {
                "artifact": {
                    "id": first_payload["artifact"]["id"],
                    "paperId": "paper-1",
                    "kind": "explainer",
                    "sourceDocumentId": source_id,
                    "status": "queued",
                },
                "job": {
                    "id": first_payload["job"]["id"],
                    "paperId": "paper-1",
                    "jobType": "explain",
                    "sourceMode": "native",
                    "status": "queued",
                },
                "deduplicated": False,
            },
            first_payload,
        )
        self.assertEqual(first_payload["artifact"], second.json()["artifact"])
        self.assertEqual(first_payload["job"], second.json()["job"])
        self.assertTrue(second.json()["deduplicated"])
        self.assertEqual(0, self.generator.calls)

    async def test_explainer_requires_source_binding_and_rejects_mode_or_not_ready(self) -> None:
        source_response = self.client.post(
            "/api/v2/papers/paper-1/sources", json={"sourceMode": "native"}
        )
        source_id = source_response.json()["source"]["id"]

        missing = self.client.post(
            "/api/v2/papers/paper-1/artifacts/explainer",
            json={"sourceMode": "native"},
        )
        mismatch = self.client.post(
            "/api/v2/papers/paper-1/artifacts/explainer",
            json={"sourceMode": "ocr", "sourceDocumentId": source_id},
        )
        not_ready = self.client.post(
            "/api/v2/papers/paper-1/artifacts/explainer",
            json={"sourceMode": "native", "sourceDocumentId": source_id},
        )

        markdown = "ready then stale\n"
        with sqlite3.connect(self.database.database_path) as connection:
            connection.execute(
                "UPDATE document_sources SET status='ready',markdown=?,content_sha256=?,page_count=1 WHERE id=?",
                (markdown, hashlib.sha256(markdown.encode()).hexdigest(), source_id),
            )
            connection.commit()
        self.pdf_path.write_bytes(b"%PDF-1.4\nstale after source\n")
        stale = self.client.post(
            "/api/v2/papers/paper-1/artifacts/explainer",
            json={"sourceMode": "native", "sourceDocumentId": source_id},
        )

        self.assertEqual(
            (422, 422, 409, 409),
            (missing.status_code, mismatch.status_code, not_ready.status_code, stale.status_code),
        )
        self.assertEqual("INVALID_REQUEST", missing.json()["error"]["code"])
        self.assertEqual("SOURCE_MODE_MISMATCH", mismatch.json()["error"]["code"])
        self.assertEqual("SOURCE_NOT_READY", not_ready.json()["error"]["code"])
        self.assertEqual("SOURCE_STALE", stale.json()["error"]["code"])
        self.assertEqual((0,), self._counts("generated_artifacts"))

    async def test_source_list_uses_signed_stable_cursor_and_rejects_tampering(self) -> None:
        first = self.client.post(
            "/api/v2/papers/paper-1/sources", json={"sourceMode": "native"}
        ).json()["source"]
        self.pdf_path.write_bytes(b"%PDF-1.4\nchanged processing api fixture\n")
        second = self.client.post(
            "/api/v2/papers/paper-1/sources", json={"sourceMode": "native"}
        ).json()["source"]

        first_page = self.client.get("/api/v2/papers/paper-1/sources?limit=1")
        self.assertEqual(200, first_page.status_code)
        self.assertEqual([second], first_page.json()["items"])
        cursor = first_page.json()["nextCursor"]
        self.assertIsInstance(cursor, str)

        final_page = self.client.get(
            "/api/v2/papers/paper-1/sources", params={"limit": 1, "cursor": cursor}
        )
        self.assertEqual({"items": [first], "nextCursor": None}, final_page.json())

        tampered = self.client.get(
            "/api/v2/papers/paper-1/sources", params={"cursor": cursor + "x"}
        )
        self.assertEqual(422, tampered.status_code)
        self.assertEqual("INVALID_CURSOR", tampered.json()["error"]["code"])

    async def test_source_cursor_survives_an_app_restart_with_the_same_startup_secret(self) -> None:
        cursor_secret = P2_TEST_PROCESSING_CURSOR_SECRET
        environment = {"PROCESSING_CURSOR_SECRET": cursor_secret}
        first_container = bootstrap(
            RolloutSettings(document_pipeline_mode="p1", generation_pipeline_mode="p1"),
            DatabaseSettings(self.database.database_path),
            required_schema_revision="20260807_02",
            native_provider_factory=lambda: self.native,
            generation_provider_factory=lambda: self.generator,
            environment_snapshot=environment,
        )
        with TestClient(
            create_app(
                first_container,
                first_container.session_factory,
                required_schema_revision="20260807_02",
            )
        ) as first_client:
            oldest = first_client.post(
                "/api/v2/papers/paper-1/sources", json={"sourceMode": "native"}
            ).json()["source"]
            self.pdf_path.write_bytes(b"%PDF-1.4\nrestart-stable cursor fixture\n")
            newest = first_client.post(
                "/api/v2/papers/paper-1/sources", json={"sourceMode": "native"}
            ).json()["source"]
            first_page = first_client.get("/api/v2/papers/paper-1/sources?limit=1")
            self.assertEqual([newest], first_page.json()["items"])
            cursor = first_page.json()["nextCursor"]
            self.assertNotIn(cursor_secret, cursor)

        second_container = bootstrap(
            RolloutSettings(document_pipeline_mode="p1", generation_pipeline_mode="p1"),
            DatabaseSettings(self.database.database_path),
            required_schema_revision="20260807_02",
            native_provider_factory=lambda: self.native,
            generation_provider_factory=lambda: self.generator,
            environment_snapshot=environment,
        )
        with TestClient(
            create_app(
                second_container,
                second_container.session_factory,
                required_schema_revision="20260807_02",
            )
        ) as second_client:
            final_page = second_client.get(
                "/api/v2/papers/paper-1/sources",
                params={"limit": 1, "cursor": cursor},
            )

        self.assertEqual(200, final_page.status_code)
        self.assertEqual({"items": [oldest], "nextCursor": None}, final_page.json())

    async def test_p2_startup_rejects_missing_or_short_cursor_secret_before_composition(self) -> None:
        provider_calls = {"native": 0, "generation": 0}

        def native_factory():
            provider_calls["native"] += 1
            return self.native

        def generation_factory():
            provider_calls["generation"] += 1
            return self.generator

        for configured, environment in (
            (None, {}),
            ("short-secret-must-not-be-echoed", {}),
        ):
            with self.subTest(configured=configured is not None):
                with self.assertRaises(ValueError) as caught:
                    bootstrap(
                        RolloutSettings(
                            document_pipeline_mode="p1",
                            generation_pipeline_mode="p1",
                            processing_cursor_secret=configured,
                        ),
                        DatabaseSettings(self.database.database_path),
                        required_schema_revision="20260807_02",
                        native_provider_factory=native_factory,
                        generation_provider_factory=generation_factory,
                        environment_snapshot=environment,
                    )
                self.assertNotIn(str(configured), str(caught.exception))

        self.assertEqual({"native": 0, "generation": 0}, provider_calls)

    async def test_source_cursor_rejects_a_different_secret_and_wrong_route_context(self) -> None:
        self.client.post(
            "/api/v2/papers/paper-1/sources", json={"sourceMode": "native"}
        )
        self.pdf_path.write_bytes(b"%PDF-1.4\ncursor context fixture\n")
        self.client.post(
            "/api/v2/papers/paper-1/sources", json={"sourceMode": "native"}
        )
        cursor = self.client.get(
            "/api/v2/papers/paper-1/sources?limit=1"
        ).json()["nextCursor"]

        wrong_route = self.client.get("/api/v2/jobs", params={"cursor": cursor})
        different_secret_container = bootstrap(
            RolloutSettings(
                document_pipeline_mode="p1",
                generation_pipeline_mode="p1",
                processing_cursor_secret="different-test-processing-cursor-secret-v1",
            ),
            DatabaseSettings(self.database.database_path),
            required_schema_revision="20260807_02",
            native_provider_factory=lambda: self.native,
            generation_provider_factory=lambda: self.generator,
        )
        with TestClient(
            create_app(
                different_secret_container,
                different_secret_container.session_factory,
                required_schema_revision="20260807_02",
            )
        ) as different_secret_client:
            wrong_secret = different_secret_client.get(
                "/api/v2/papers/paper-1/sources", params={"cursor": cursor}
            )

        self.assertEqual((422, 422), (wrong_route.status_code, wrong_secret.status_code))
        self.assertEqual("INVALID_CURSOR", wrong_route.json()["error"]["code"])
        self.assertEqual("INVALID_CURSOR", wrong_secret.json()["error"]["code"])

    async def test_artifact_list_filters_kind_and_uses_safe_shape(self) -> None:
        source_id = self.client.post(
            "/api/v2/papers/paper-1/sources", json={"sourceMode": "native"}
        ).json()["source"]["id"]
        markdown = "ready source\n"
        with sqlite3.connect(self.database.database_path) as connection:
            connection.execute(
                "UPDATE document_sources SET status='ready',markdown=?,content_sha256=?,page_count=1 WHERE id=?",
                (markdown, hashlib.sha256(markdown.encode()).hexdigest(), source_id),
            )
            connection.commit()
        created = self.client.post(
            "/api/v2/papers/paper-1/artifacts/explainer",
            json={"sourceMode": "native", "sourceDocumentId": source_id},
        ).json()["artifact"]

        response = self.client.get(
            "/api/v2/papers/paper-1/artifacts?kind=explainer&limit=1"
        )
        self.assertEqual(200, response.status_code)
        self.assertEqual({"items": [created], "nextCursor": None}, response.json())
        self.assertNotIn("content", response.text)

    async def test_job_detail_and_events_are_exact_and_hide_internal_fields(self) -> None:
        created = self.client.post(
            "/api/v2/papers/paper-1/sources", json={"sourceMode": "native"}
        ).json()["job"]

        detail = self.client.get(f"/api/v2/jobs/{created['id']}")
        self.assertEqual(200, detail.status_code)
        payload = detail.json()
        self.assertEqual(
            {
                "id",
                "paperId",
                "jobType",
                "sourceMode",
                "status",
                "progress",
                "attempt",
                "maxAttempts",
                "error",
                "createdAt",
                "startedAt",
                "finishedAt",
                "cancelledAt",
            },
            set(payload),
        )
        self.assertEqual({}, payload["progress"])
        self.assertIsNone(payload["error"])
        for forbidden in ("lease", "token", "idempotency", "spec", "result"):
            self.assertNotIn(forbidden, detail.text.lower())

        events = self.client.get(
            f"/api/v2/jobs/{created['id']}/events?afterSequence=0&limit=100"
        )
        self.assertEqual(200, events.status_code)
        self.assertEqual(1, events.json()["nextAfterSequence"])
        self.assertEqual(
            {"sequence", "type", "progress", "error", "createdAt"},
            set(events.json()["items"][0]),
        )
        self.assertEqual("enqueued", events.json()["items"][0]["type"])

    async def test_job_detail_and_events_fail_closed_for_legacy_unsafe_progress(self) -> None:
        created = self.client.post(
            "/api/v2/papers/paper-1/sources", json={"sourceMode": "native"}
        ).json()["job"]
        with sqlite3.connect(self.database.database_path) as connection:
            connection.execute(
                "UPDATE processing_jobs SET progress_json=? WHERE id=?",
                ('{"phase":"TOP-SECRET raw body"}', created["id"]),
            )
            connection.execute(
                "UPDATE processing_job_events SET progress_json=? WHERE job_id=?",
                ('{"stage":"Bearer TOP-SECRET"}', created["id"]),
            )
            connection.commit()

        detail = self.client.get(f"/api/v2/jobs/{created['id']}")
        events = self.client.get(f"/api/v2/jobs/{created['id']}/events")

        self.assertEqual((200, 200), (detail.status_code, events.status_code))
        self.assertEqual({}, detail.json()["progress"])
        self.assertEqual({}, events.json()["items"][0]["progress"])
        for forbidden in ("TOP-SECRET", "raw body", "Bearer"):
            self.assertNotIn(forbidden, detail.text + events.text)

    async def test_report_progress_is_atomic_and_roundtrips_only_safe_progress(self) -> None:
        created = self.client.post(
            "/api/v2/papers/paper-1/sources", json={"sourceMode": "native"}
        ).json()["job"]
        now = datetime.now(timezone.utc)
        async with SqlAlchemyUnitOfWork(self.database.session_factory) as work:
            lease = await work.jobs.claim_next(
                worker_id="progress-test-worker",
                now=now,
                lease_seconds=30,
            )
            await work.commit()
        self.assertIsNotNone(lease)
        assert lease is not None

        with sqlite3.connect(self.database.database_path) as connection:
            job_before = connection.execute(
                "SELECT * FROM processing_jobs WHERE id=?", (created["id"],)
            ).fetchone()
            events_before = connection.execute(
                "SELECT * FROM processing_job_events WHERE job_id=? ORDER BY sequence",
                (created["id"],),
            ).fetchall()

        async with SqlAlchemyUnitOfWork(self.database.session_factory) as work:
            with self.assertRaises(ValueError) as caught:
                await work.jobs.report_progress(
                    lease,
                    {"message": "TOP-SECRET raw body"},
                    now=now,
                )
        self.assertNotIn("TOP-SECRET", str(caught.exception))
        self.assertNotIn("raw body", str(caught.exception))
        with sqlite3.connect(self.database.database_path) as connection:
            self.assertEqual(
                job_before,
                connection.execute(
                    "SELECT * FROM processing_jobs WHERE id=?", (created["id"],)
                ).fetchone(),
            )
            self.assertEqual(
                events_before,
                connection.execute(
                    "SELECT * FROM processing_job_events WHERE job_id=? ORDER BY sequence",
                    (created["id"],),
                ).fetchall(),
            )

        safe_progress = {"stage": "pdf_loaded", "pagesCompleted": 2}
        async with SqlAlchemyUnitOfWork(self.database.session_factory) as work:
            await work.jobs.report_progress(lease, JobProgress(safe_progress), now=now)
            await work.commit()

        detail = self.client.get(f"/api/v2/jobs/{created['id']}")
        events = self.client.get(f"/api/v2/jobs/{created['id']}/events")
        self.assertEqual(safe_progress, detail.json()["progress"])
        self.assertEqual(safe_progress, events.json()["items"][-1]["progress"])
        with sqlite3.connect(self.database.database_path) as connection:
            expected_json = '{"pagesCompleted":2,"stage":"pdf_loaded"}'
            self.assertEqual(
                expected_json,
                connection.execute(
                    "SELECT progress_json FROM processing_jobs WHERE id=?", (created["id"],)
                ).fetchone()[0],
            )
            self.assertEqual(
                ("progress", expected_json),
                connection.execute(
                    "SELECT event_type,progress_json FROM processing_job_events "
                    "WHERE job_id=? ORDER BY sequence DESC LIMIT 1",
                    (created["id"],),
                ).fetchone(),
            )

    async def test_job_list_filters_and_includes_safe_global_jobs(self) -> None:
        created = self.client.post(
            "/api/v2/papers/paper-1/sources", json={"sourceMode": "native"}
        ).json()["job"]
        global_spec = ObsidianSyncJobSpecV1()
        global_spec_json = encode_job_spec_v1(global_spec)
        async with SqlAlchemyUnitOfWork(self.database.session_factory) as work:
            await work.jobs.insert_with_spec(
                NewProcessingJob(
                    id="global-job",
                    spec=global_spec,
                    idempotency_key="global-idempotency-key",
                    created_at=datetime(2026, 8, 11, tzinfo=timezone.utc),
                ),
                spec_json=global_spec_json,
                spec_sha256=hash_job_spec(global_spec_json),
            )
            await work.commit()

        filtered = self.client.get(
            "/api/v2/jobs",
            params={
                "paperId": "paper-1",
                "status": "queued",
                "jobType": "source_materialize",
                "limit": 1,
            },
        )
        self.assertEqual(200, filtered.status_code)
        self.assertEqual(created["id"], filtered.json()["items"][0]["id"])

        global_list = self.client.get("/api/v2/jobs?jobType=obsidian_sync")
        self.assertEqual(200, global_list.status_code)
        global_job = global_list.json()["items"][0]
        self.assertEqual("global-job", global_job["id"])
        self.assertIsNone(global_job["paperId"])
        self.assertIsNone(global_job["sourceMode"])
        for forbidden in ("idempotency", "spec", "lease", "result"):
            self.assertNotIn(forbidden, global_list.text.lower())

    async def test_cancel_and_explicit_retry_follow_the_persistent_state_machine(self) -> None:
        original = self.client.post(
            "/api/v2/papers/paper-1/sources", json={"sourceMode": "native"}
        ).json()["job"]

        cancelled = self.client.post(f"/api/v2/jobs/{original['id']}/cancel")
        terminal_cancel = self.client.post(f"/api/v2/jobs/{original['id']}/cancel")
        first_retry = self.client.post(f"/api/v2/jobs/{original['id']}/retry")
        second_retry = self.client.post(f"/api/v2/jobs/{original['id']}/retry")

        self.assertEqual(200, cancelled.status_code)
        self.assertEqual("cancelled", cancelled.json()["status"])
        self.assertEqual(409, terminal_cancel.status_code)
        self.assertEqual("JOB_NOT_CANCELLABLE", terminal_cancel.json()["error"]["code"])
        self.assertEqual((202, 202), (first_retry.status_code, second_retry.status_code))
        self.assertEqual(original["id"], first_retry.json()["retriedFromJobId"])
        self.assertFalse(first_retry.json()["deduplicated"])
        self.assertTrue(second_retry.json()["deduplicated"])
        self.assertEqual(first_retry.json()["job"], second_retry.json()["job"])
        self.assertEqual(
            "cancelled",
            self.client.get(f"/api/v2/jobs/{original['id']}").json()["status"],
        )

    async def test_paper_scoped_lists_return_paper_not_found(self) -> None:
        sources = self.client.get("/api/v2/papers/missing-paper/sources")
        artifacts = self.client.get("/api/v2/papers/missing-paper/artifacts")

        self.assertEqual((404, 404), (sources.status_code, artifacts.status_code))
        self.assertEqual("PAPER_NOT_FOUND", sources.json()["error"]["code"])
        self.assertEqual("PAPER_NOT_FOUND", artifacts.json()["error"]["code"])

    async def test_job_list_rejects_unknown_or_snake_case_query_fields(self) -> None:
        response = self.client.get(
            "/api/v2/jobs",
            params={"paper_id": "paper-1", "authorization": "TOP-SECRET"},
        )

        self.assertEqual(422, response.status_code)
        self.assertEqual("INVALID_REQUEST", response.json()["error"]["code"])
        self.assertNotIn("TOP-SECRET", response.text)

    async def test_running_cancel_requests_checkpoint_settlement_and_nonretryable_fails(self) -> None:
        first = self.client.post(
            "/api/v2/papers/paper-1/sources", json={"sourceMode": "native"}
        ).json()
        async with SqlAlchemyUnitOfWork(self.database.session_factory) as work:
            lease = await work.jobs.claim_next(
                worker_id="test-worker",
                now=datetime.now(timezone.utc),
                lease_seconds=30,
            )
            await work.commit()
        self.assertIsNotNone(lease)

        requested = self.client.post(f"/api/v2/jobs/{first['job']['id']}/cancel")
        self.assertEqual(200, requested.status_code)
        self.assertEqual("running", requested.json()["status"])
        events = self.client.get(f"/api/v2/jobs/{first['job']['id']}/events").json()["items"]
        self.assertEqual("cancel_requested", events[-1]["type"])

        self.pdf_path.write_bytes(b"%PDF-1.4\nnonretryable identity\n")
        second = self.client.post(
            "/api/v2/papers/paper-1/sources", json={"sourceMode": "native"}
        ).json()
        with sqlite3.connect(self.database.database_path) as connection:
            connection.execute(
                "UPDATE processing_jobs SET status='failed',error_code='PDF_ENCRYPTED',finished_at=updated_at WHERE id=?",
                (second["job"]["id"],),
            )
            connection.execute(
                "UPDATE document_sources SET status='failed',error_code='PDF_ENCRYPTED' WHERE id=?",
                (second["source"]["id"],),
            )
            connection.commit()
        retry = self.client.post(f"/api/v2/jobs/{second['job']['id']}/retry")
        self.assertEqual(409, retry.status_code)
        self.assertEqual("JOB_NOT_RETRYABLE", retry.json()["error"]["code"])

    async def test_p2_app_factory_rejects_p1_revision_and_accepts_p2_revision(self) -> None:
        self.assertEqual("20260807_02", self.container.schema_revision)
        with sqlite3.connect(self.database.database_path) as connection:
            connection.execute(
                "UPDATE alembic_version SET version_num='20260807_01'"
            )
            connection.commit()

        with self.assertRaises(SchemaRevisionMismatchError) as caught:
            bootstrap(
                RolloutSettings(document_pipeline_mode="p1"),
                DatabaseSettings(self.database.database_path),
                required_schema_revision="20260807_02",
            )
        self.assertEqual("SCHEMA_REVISION_MISMATCH", caught.exception.code)

    async def test_job_error_dto_never_exposes_internal_error_message_or_content(self) -> None:
        created = self.client.post(
            "/api/v2/papers/paper-1/sources", json={"sourceMode": "native"}
        ).json()
        with sqlite3.connect(self.database.database_path) as connection:
            connection.execute(
                "UPDATE processing_jobs SET status='failed',error_code='OCR_SERVER_ERROR',error_message=? WHERE id=?",
                ("Authorization Bearer TOP-SECRET raw body private content", created["job"]["id"]),
            )
            connection.execute(
                "UPDATE document_sources SET status='failed',error_code='OCR_SERVER_ERROR',error_message=? WHERE id=?",
                ("private PDF content", created["source"]["id"]),
            )
            connection.commit()

        response = self.client.get(f"/api/v2/jobs/{created['job']['id']}")
        self.assertEqual(200, response.status_code)
        self.assertEqual(
            {"code": "OCR_SERVER_ERROR", "message": "Processing failed."},
            response.json()["error"],
        )
        for secret in ("TOP-SECRET", "Authorization", "raw body", "private content"):
            self.assertNotIn(secret, response.text)

    async def test_terminal_post_dedupe_reports_stored_status_without_reviving_job(self) -> None:
        first = self.client.post(
            "/api/v2/papers/paper-1/sources", json={"sourceMode": "native"}
        ).json()
        self.client.post(f"/api/v2/jobs/{first['job']['id']}/cancel")

        repeated = self.client.post(
            "/api/v2/papers/paper-1/sources", json={"sourceMode": "native"}
        )

        self.assertEqual(202, repeated.status_code)
        self.assertTrue(repeated.json()["deduplicated"])
        self.assertEqual("cancelled", repeated.json()["source"]["status"])
        self.assertEqual("cancelled", repeated.json()["job"]["status"])
        self.assertEqual((1, 1), self._counts("document_sources", "processing_jobs"))

    async def test_empty_source_document_id_is_a_body_validation_error(self) -> None:
        response = self.client.post(
            "/api/v2/papers/paper-1/artifacts/explainer",
            json={"sourceMode": "native", "sourceDocumentId": ""},
        )

        self.assertEqual(422, response.status_code)
        self.assertEqual("INVALID_REQUEST", response.json()["error"]["code"])

    async def test_job_read_and_actions_reject_unknown_query_or_body(self) -> None:
        created = self.client.post(
            "/api/v2/papers/paper-1/sources", json={"sourceMode": "native"}
        ).json()["job"]

        read = self.client.get(
            f"/api/v2/jobs/{created['id']}", params={"authorization": "TOP-SECRET"}
        )
        cancel = self.client.post(
            f"/api/v2/jobs/{created['id']}/cancel",
            json={"authorization": "TOP-SECRET"},
        )

        self.assertEqual((422, 422), (read.status_code, cancel.status_code))
        self.assertEqual("INVALID_REQUEST", read.json()["error"]["code"])
        self.assertEqual("INVALID_REQUEST", cancel.json()["error"]["code"])
        self.assertEqual(
            "queued", self.client.get(f"/api/v2/jobs/{created['id']}").json()["status"]
        )
        self.assertNotIn("TOP-SECRET", read.text + cancel.text)

    def _counts(self, *tables: str) -> tuple[int, ...]:
        with sqlite3.connect(self.database.database_path) as connection:
            return tuple(
                int(connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0])
                for table in tables
            )


if __name__ == "__main__":
    unittest.main()
