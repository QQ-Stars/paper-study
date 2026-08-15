from __future__ import annotations

import asyncio
from contextlib import closing
import hashlib
import importlib
import inspect
import json
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import text

from backend.app.domain import processing
from backend.app.domain.processing import (
    ExplainJobSpecV1,
    JobFailure,
    JobProgress,
    JobResult,
    JobSpecValidationError,
    NewProcessingJob,
    OcrJobSpecV1,
    SourceMaterializeJobSpecV1,
    build_artifact_job_key,
    build_artifact_key,
    build_source_job_key,
    build_source_key,
    decode_job_spec_v1,
    encode_job_spec_v1,
    hash_job_spec,
)
from backend.app.config import DatabaseSettings
from backend.app.domain import GeneratedArtifact, JobLeaseLostError, PersistenceConflictError, SourceDocument
from backend.app.infrastructure.database import create_async_session_factory
from backend.app.repositories.unit_of_work import SqlAlchemyUnitOfWork
from backend.tests.support.p1_database import create_legacy_database, run_alembic


class ProcessingDomainTests(unittest.TestCase):
    def test_job_spec_v1_is_canonical_strict_content_safe_and_hash_stable(self) -> None:
        spec = SourceMaterializeJobSpecV1(
            paper_id="paper-1",
            source_document_id="source-1",
            processing_version="native-v1",
        )
        expected = (
            '{"arguments":{"processingVersion":"native-v1"},'
            '"jobType":"source_materialize","paperId":"paper-1",'
            '"schemaVersion":1,"sourceMode":"native",'
            '"target":{"artifactId":null,"sourceDocumentId":"source-1"}}'
        )

        encoded = encode_job_spec_v1(spec)

        self.assertEqual(expected, encoded)
        self.assertEqual(spec, decode_job_spec_v1(encoded))
        self.assertEqual(hashlib.sha256(expected.encode("utf-8")).hexdigest(), hash_job_spec(encoded))
        for raw in (
            expected.replace('"schemaVersion":1', '"schemaVersion":2'),
            expected.replace('"processingVersion":"native-v1"', '"apiKey":"secret"'),
            expected.replace('"processingVersion":"native-v1"', '"markdown":"secret"'),
            expected.replace('"processingVersion":"native-v1"', '"processingVersion":"native-v1","x":1'),
            expected.replace('{"arguments"', '{ "arguments"'),
        ):
            with self.subTest(raw=raw):
                with self.assertRaises(JobSpecValidationError):
                    decode_job_spec_v1(raw)

    def test_job_spec_v1_rejects_sensitive_values_under_safe_keys_without_echoing_them(self) -> None:
        safe = OcrJobSpecV1(
            paper_id="paper-1",
            source_document_id="source-1",
            provider="ocr-test",
            model="ocr-v1",
            options={"language": "en"},
        )
        self.assertEqual(safe, decode_job_spec_v1(encode_job_spec_v1(safe)))

        sensitive_values = (
            "Authorization: Bearer TOP-SECRET",
            "Cookie: session=TOP-SECRET",
            "Bearer TOP-SECRET",
            "api_key=TOP-SECRET",
            "%PDF-1.7\nTOP-SECRET",
            "# Markdown TOP-SECRET\nraw body",
            "prompt: summarize TOP-SECRET",
            "raw provider response: TOP-SECRET",
        )
        for sensitive_value in sensitive_values:
            with self.subTest(sensitive_value=sensitive_value):
                unsafe = SourceMaterializeJobSpecV1(
                    paper_id="paper-1",
                    source_document_id="source-1",
                    processing_version=sensitive_value,
                )
                with self.assertRaises(JobSpecValidationError) as encode_error:
                    encode_job_spec_v1(unsafe)
                self.assertEqual("JOB_SPEC_INVALID", encode_error.exception.code)
                self.assertNotIn("TOP-SECRET", str(encode_error.exception))

                payload = json.loads(encode_job_spec_v1(safe))
                payload["arguments"]["options"]["language"] = sensitive_value
                raw = json.dumps(
                    payload,
                    ensure_ascii=False,
                    separators=(",", ":"),
                    sort_keys=True,
                )
                with self.assertRaises(JobSpecValidationError) as decode_error:
                    decode_job_spec_v1(raw)
                self.assertEqual("JOB_SPEC_INVALID", decode_error.exception.code)
                self.assertNotIn("TOP-SECRET", str(decode_error.exception))

        payload = json.loads(encode_job_spec_v1(safe))
        payload["arguments"]["options"] = {"TOP-SECRET": "en"}
        raw = json.dumps(payload, separators=(",", ":"), sort_keys=True)
        with self.assertRaises(JobSpecValidationError) as key_error:
            decode_job_spec_v1(raw)
        self.assertEqual("JOB_SPEC_INVALID", key_error.exception.code)
        self.assertNotIn("TOP-SECRET", str(key_error.exception))

    def test_ocr_job_spec_options_are_explicit_flat_and_bounded(self) -> None:
        for options in (
            {},
            {"language": "en"},
            {"pageBatchSize": 1, "maxConcurrency": 1},
            {"language": "zh-CN", "pageBatchSize": 16, "maxConcurrency": 4},
        ):
            with self.subTest(valid=options):
                value = OcrJobSpecV1(
                    paper_id="paper-1",
                    source_document_id="source-1",
                    provider="ocr-test",
                    model="ocr-v1",
                    options=options,
                )
                self.assertEqual(value, decode_job_spec_v1(encode_job_spec_v1(value)))

        for options in (
            {"temperature": 0},
            {"language": {"nested": "en"}},
            {"language": "e"},
            {"language": "en-"},
            {"language": "a" * 36},
            {"pageBatchSize": 0},
            {"pageBatchSize": True},
            {"maxConcurrency": 5},
            {"maxConcurrency": 1.0},
        ):
            with self.subTest(invalid=options):
                with self.assertRaises(JobSpecValidationError):
                    OcrJobSpecV1(
                        paper_id="paper-1",
                        source_document_id="source-1",
                        provider="ocr-test",
                        model="ocr-v1",
                        options=options,
                    )

    def test_all_job_types_have_explicit_frozen_variants_and_legacy_cannot_enqueue(self) -> None:
        variants = (
            processing.OcrJobSpecV1(
                paper_id="paper-1", source_document_id="source-1", provider="ocr-test", model="ocr-v1",
            ),
            processing.ExplainJobSpecV1(
                paper_id="paper-1", source_document_id="source-1", artifact_id="artifact-1",
                profile="deep", provider="llm-test", model="llm-v1", prompt_version="explain-v1",
            ),
            processing.TranslateJobSpecV1(
                paper_id="paper-1", source_document_id="source-1", artifact_id="artifact-1",
            ),
            processing.EmbedJobSpecV1(paper_id="paper-1", source_document_id="source-1"),
            processing.ObsidianExportJobSpecV1(paper_id="paper-1", artifact_id="artifact-1"),
            processing.ObsidianSyncJobSpecV1(),
        )
        for value in variants:
            with self.subTest(job_type=value.job_type):
                self.assertEqual(value, decode_job_spec_v1(encode_job_spec_v1(value)))
        ocr = variants[0]
        self.assertEqual(1, ocr.page_batch_size)
        self.assertEqual(1, ocr.max_concurrency)
        self.assertEqual("JOB_SPEC_UNRECOVERABLE", processing.LegacyImportedJobSpecV1(
            job_type="ocr", paper_id="paper-1", source_mode="ocr",
        ).dispatch_error_code)
        with self.assertRaises(processing.JobSpecValidationError):
            processing.ensure_application_job_spec(processing.LegacyImportedJobSpecV1(
                job_type="ocr", paper_id="paper-1", source_mode="ocr",
            ))
        with self.assertRaises(processing.JobSpecValidationError):
            processing.OcrJobSpecV1(
                paper_id="paper-1", source_document_id="source-1", provider="ocr-test", model="ocr-v1",
                page_batch_size=17,
            )

    def test_domain_dtos_enforce_public_states_transitions_utc_and_safe_json(self) -> None:
        now = datetime(2026, 8, 9, 8, 0, tzinfo=timezone.utc)
        job = processing.NewProcessingJob(
            id="job-1", spec=SourceMaterializeJobSpecV1(
                paper_id="paper-1", source_document_id="source-1", processing_version="native-v1",
            ), idempotency_key="key-1", created_at=now,
        )
        running = processing.transition_job_status(job.status, "running")
        self.assertEqual("running", running.value)
        self.assertEqual(
            {"id": "job-1", "status": "queued", "attempt": 0, "maxAttempts": 1,
             "paperId": "paper-1", "sourceMode": "native"},
            job.to_api_dict(),
        )
        self.assertEqual({"phase": "extract", "completed": 1}, processing.safe_json_object({
            "phase": "extract", "completed": 1,
        }))
        with self.assertRaises(ValueError):
            processing.NewProcessingJob(
                id="job-2", spec=job.spec, idempotency_key="key-2",
                created_at=datetime(2026, 8, 9, 8, 0),
            )
        with self.assertRaises(ValueError):
            processing.safe_json_object({"n": float("nan")})
        with self.assertRaises(ValueError):
            processing.safe_json_object({1: "not a string key"})
        with self.assertRaises(ValueError):
            processing.transition_job_status("succeeded", "running")

    def test_job_progress_has_a_small_flat_frozen_schema_and_byte_bound(self) -> None:
        valid_values = (
            {},
            {"phase": "one", "completed": 1},
            {"phase": "old"},
            {"stage": "pdf_loaded", "pagesCompleted": 2},
            {
                "phase": "extract-1",
                "stage": "pdf_loaded",
                "completed": 0,
                "total": 3,
                "pagesCompleted": 2,
                "pagesTotal": 4,
            },
        )
        for value in valid_values:
            with self.subTest(valid=value):
                progress = JobProgress(value)
                self.assertEqual(value, dict(progress.value))

        invalid_values = (
            {"unknown": "one"},
            {"message": "TOP-SECRET"},
            {"body": "raw body"},
            {"content": "# markdown"},
            {"markdown": "private"},
            {"prompt": "private"},
            {"response": "private"},
            {"header": "Authorization"},
            {"token": "Bearer TOP-SECRET"},
            {"credential": "api_key=TOP-SECRET"},
            {"phase": {"nested": "one"}},
            {"phase": "Bearer TOP-SECRET"},
            {"stage": "raw body"},
            {"phase": "a" * 65},
            {"phase": ""},
            {"phase": 1},
            {"completed": -1},
            {"completed": True},
            {"completed": 1.0},
            {"total": float("nan")},
            {"pagesCompleted": [1]},
            {"pagesTotal": int("9" * 600)},
        )
        for value in invalid_values:
            with self.subTest(invalid=value):
                with self.assertRaises(ValueError) as caught:
                    JobProgress(value)
                self.assertNotIn("TOP-SECRET", str(caught.exception))
                self.assertNotIn("raw body", str(caught.exception))

    def test_cache_key_builders_match_literal_goldens_and_are_cross_process_stable(self) -> None:
        options_hash = processing.hash_canonical_json({"maxConcurrency": 1, "pageBatchSize": 1})
        source_key = processing.build_source_key(
            paper_id="paper-1", mode="native", provider="native", model="pymupdf",
            pdf_sha256="a" * 64, options_hash=options_hash, processing_version="native-v1",
        )
        spec_sha = hash_job_spec(encode_job_spec_v1(SourceMaterializeJobSpecV1(
            paper_id="paper-1", source_document_id="source-1", processing_version="native-v1",
        )))
        self.assertEqual("944f65c4dfcd0aec81b3648a90b131d159b5436925968d44b08390f968ada3b0", options_hash)
        self.assertEqual("ba1f53bf5febd413ca90ebf40dfc89d41bbc25b1ec8c954d5202d575c082eaf5", source_key)
        self.assertEqual("f070bbd1040cb631436ad7ea513dc939d9b2d20d04052a710a81e3fb53abb608",
                         processing.build_source_job_key(source_key, spec_sha))
        artifact_key = processing.build_artifact_key(
            kind="explainer", source_document_id="source-1", source_content_sha256="b" * 64,
            generator_provider="llm-test", generator_model="llm-v1", prompt_version="explain-v1",
            kind_specific_options={"profile": "standard"},
        )
        self.assertEqual("f0ba9276bca148bb95055fad968230ab750bb83614c1ad91f057e459bb415bb8", artifact_key)
        self.assertEqual("8424ccc64a5053685c042ec607020b5331c3f3daad610f03cf2c0be125a1ebba",
                         processing.build_artifact_job_key(artifact_key, spec_sha))
        self.assertNotEqual(
            artifact_key,
            processing.build_index_job_key(source_document_id="source-1", source_content_sha256="b" * 64,
                                           embedding_model="embed-v1", chunking_version="chunks-v1"),
        )

    def test_queue_and_ocr_ports_are_protocols_without_concrete_adapters(self) -> None:
        queue_port = importlib.import_module("backend.app.application.ports.processing_queue")
        ocr_port = importlib.import_module("backend.app.application.ports.ocr_provider")
        self.assertTrue(getattr(queue_port.ProcessingQueue, "_is_protocol", False))
        self.assertEqual(
            {"enqueue", "get", "cancel", "retry", "list", "list_events", "claim_next",
             "check_active", "renew", "report_progress", "complete", "fail"},
            {name for name, value in inspect.getmembers(queue_port.ProcessingQueue)
             if inspect.isfunction(value) and not name.startswith("__")},
        )
        self.assertTrue(getattr(ocr_port.OcrProvider, "_is_protocol", False))
        request = ocr_port.OcrRequest(
            source_id="source-1", paper_id="paper-1", pdf_bytes=b"%PDF", pdf_sha256="a" * 64,
            media_type="application/pdf", model="ocr-v1", options={"pageBatchSize": 1},
            page_numbers=(1,), total_pages=1,
        )
        self.assertEqual((1,), request.page_numbers)


class ProcessingEnqueueTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory(prefix="study-app-processing-enqueue-")
        self.database_path = Path(self._temp.name) / "queue" / "app.db"
        create_legacy_database(self.database_path)
        run_alembic(self.database_path, "20260807_02")
        self.session_factory = create_async_session_factory(DatabaseSettings(self.database_path))
        self.engine = self.session_factory.kw["bind"]
        self.now = datetime(2026, 8, 9, 12, 0, tzinfo=timezone.utc)
        self.native = self._source("source-native", mode="native", provider="local", model="pymupdf")
        self.ocr = self._source("source-ocr", mode="ocr", provider="ocr-test", model="ocr-v1")
        self.artifact = GeneratedArtifact(
            id="artifact-explainer",
            paper_id="paper-1",
            kind="explainer",
            source_document_id=self.native.id,
            status="queued",
            generator_provider="llm-test",
            generator_model="llm-v1",
            prompt_version="explain-v1",
            created_at=self.now,
            updated_at=self.now,
        )
        async with SqlAlchemyUnitOfWork(self.session_factory) as work:
            await work.sources.add(self.native)
            await work.sources.add(self.ocr)
            await work.artifacts.add(self.artifact)
            await work.commit()

    async def asyncTearDown(self) -> None:
        await self.engine.dispose()
        self._temp.cleanup()

    async def test_enqueue_persists_canonical_spec_and_binds_idempotency_to_spec_hash(self) -> None:
        native_spec = SourceMaterializeJobSpecV1(
            paper_id="paper-1", source_document_id=self.native.id, processing_version="native-v1",
        )
        ocr_spec = OcrJobSpecV1(
            paper_id="paper-1", source_document_id=self.ocr.id,
            provider="ocr-test", model="ocr-v1", options={"language": "en"},
        )
        explain_spec = ExplainJobSpecV1(
            paper_id="paper-1", source_document_id=self.native.id, artifact_id=self.artifact.id,
            profile="deep", provider="llm-test", model="llm-v1", prompt_version="explain-v1",
        )
        specs = (native_spec, ocr_spec, explain_spec)
        native_key = build_source_key(
            paper_id="paper-1", mode="native", provider="local", model="pymupdf",
            pdf_sha256=self.native.pdf_sha256, options_hash=self.native.options_hash,
            processing_version="native-v1",
        )
        ocr_key = build_source_key(
            paper_id="paper-1", mode="ocr", provider="ocr-test", model="ocr-v1",
            pdf_sha256=self.ocr.pdf_sha256, options_hash=self.ocr.options_hash,
            processing_version="ocr-v1",
        )
        artifact_key = build_artifact_key(
            kind="explainer", source_document_id=self.native.id,
            source_content_sha256=self.native.content_sha256 or "",
            generator_provider="llm-test", generator_model="llm-v1", prompt_version="explain-v1",
            kind_specific_options={"profile": "deep"},
        )
        expected_keys = (
            build_source_job_key(native_key, hash_job_spec(encode_job_spec_v1(native_spec))),
            build_source_job_key(ocr_key, hash_job_spec(encode_job_spec_v1(ocr_spec))),
            build_artifact_job_key(artifact_key, hash_job_spec(encode_job_spec_v1(explain_spec))),
        )
        jobs = tuple(
            NewProcessingJob(
                id=f"job-{index}", spec=spec, idempotency_key=key, created_at=self.now,
            )
            for index, (spec, key) in enumerate(zip(specs, expected_keys), start=1)
        )

        async with SqlAlchemyUnitOfWork(self.session_factory) as work:
            results = []
            for job, spec in zip(jobs, specs):
                encoded = encode_job_spec_v1(spec)
                results.append(await work.jobs.insert_with_spec(
                    job, spec_json=encoded, spec_sha256=hash_job_spec(encoded),
                ))
            await work.commit()

        self.assertEqual([False, False, False], [result.deduplicated for result in results])
        with sqlite3.connect(self.database_path) as connection:
            rows = connection.execute(
                "SELECT id,spec_json,idempotency_key,progress_json FROM processing_jobs ORDER BY id"
            ).fetchall()
        self.assertEqual(
            [
                (job.id, encode_job_spec_v1(spec), key, "{}")
                for job, spec, key in zip(jobs, specs, expected_keys)
            ],
            rows,
        )
        for _, raw_json, _, progress_json in rows:
            self.assertEqual(raw_json, encode_job_spec_v1(decode_job_spec_v1(raw_json)))
            self.assertEqual({}, json.loads(progress_json))

        duplicate = NewProcessingJob(
            id="job-duplicate", spec=native_spec, idempotency_key=expected_keys[0], created_at=self.now,
        )
        async with SqlAlchemyUnitOfWork(self.session_factory) as work:
            deduplicated = await work.jobs.insert_with_spec(
                duplicate,
                spec_json=encode_job_spec_v1(native_spec),
                spec_sha256=hash_job_spec(encode_job_spec_v1(native_spec)),
            )
            await work.commit()
        self.assertTrue(deduplicated.deduplicated)
        self.assertEqual(jobs[0].id, deduplicated.job.id)

        changed_spec = SourceMaterializeJobSpecV1(
            paper_id="paper-1", source_document_id=self.native.id, processing_version="native-v2",
        )
        self.assertNotEqual(hash_job_spec(encode_job_spec_v1(native_spec)), hash_job_spec(encode_job_spec_v1(changed_spec)))
        self.assertNotEqual(
            expected_keys[0],
            build_source_job_key(native_key, hash_job_spec(encode_job_spec_v1(changed_spec))),
        )

        before = self._counts()
        for raw_json in (
            encode_job_spec_v1(native_spec).replace('"processingVersion":"native-v1"', '"apiKey":"TOP-SECRET"'),
            "{ " + encode_job_spec_v1(native_spec)[1:],
        ):
            with self.subTest(raw_json=raw_json):
                async with SqlAlchemyUnitOfWork(self.session_factory) as work:
                    with self.assertRaises(JobSpecValidationError):
                        await work.jobs.insert_with_spec(
                            NewProcessingJob(
                                id="job-invalid", spec=native_spec,
                                idempotency_key=expected_keys[0], created_at=self.now,
                            ),
                            spec_json=raw_json,
                            spec_sha256=hash_job_spec(raw_json),
                        )
        self.assertEqual(before, self._counts())

    async def test_source_enqueue_is_atomic_and_idempotent_across_native_and_ocr(self) -> None:
        native = self._source("source-enqueue-native", mode="native", provider="local", model="pymupdf", pdf_sha="d" * 64, status="queued")
        ocr = self._source("source-enqueue-ocr", mode="ocr", provider="ocr-test", model="ocr-v1", pdf_sha="e" * 64, status="queued")
        native_spec = SourceMaterializeJobSpecV1(
            paper_id="paper-1", source_document_id=native.id, processing_version="native-v1",
        )
        ocr_spec = OcrJobSpecV1(
            paper_id="paper-1", source_document_id=ocr.id, provider="ocr-test", model="ocr-v1",
        )
        requests = ((native, native_spec), (ocr, ocr_spec))
        jobs = []
        for index, (source, spec) in enumerate(requests, start=1):
            source_key = build_source_key(
                paper_id=source.paper_id, mode=source.mode.value, provider=source.provider,
                model=source.model, pdf_sha256=source.pdf_sha256, options_hash=source.options_hash,
                processing_version=source.processing_version,
            )
            encoded = encode_job_spec_v1(spec)
            jobs.append((
                NewProcessingJob(
                    id=f"source-enqueue-job-{index}", spec=spec,
                    idempotency_key=build_source_job_key(source_key, hash_job_spec(encoded)),
                    created_at=self.now,
                ),
                encoded,
            ))

        async with SqlAlchemyUnitOfWork(self.session_factory) as work:
            results = [
                await work.sources.enqueue_with_job(source, job, spec_json=encoded,
                                                    spec_sha256=hash_job_spec(encoded))
                for (source, _), (job, encoded) in zip(requests, jobs)
            ]
            await work.commit()

        self.assertEqual([False, False], [result.deduplicated for _, result in results])
        with sqlite3.connect(self.database_path) as connection:
            source_rows = connection.execute(
                "SELECT id,mode,status,source_key FROM document_sources "
                "WHERE id LIKE 'source-enqueue-%' ORDER BY id"
            ).fetchall()
            job_rows = connection.execute(
                "SELECT id,job_type,source_mode,status FROM processing_jobs "
                "WHERE id LIKE 'source-enqueue-%' ORDER BY id"
            ).fetchall()
            events = connection.execute(
                "SELECT job_id,sequence,event_type,progress_json FROM processing_job_events "
                "WHERE job_id LIKE 'source-enqueue-%' ORDER BY job_id,sequence"
            ).fetchall()
        self.assertEqual(
            [("source-enqueue-native", "native", "queued"), ("source-enqueue-ocr", "ocr", "queued")],
            [(identifier, mode, status) for identifier, mode, status, _ in source_rows],
        )
        self.assertTrue(all(source_key for *_, source_key in source_rows))
        self.assertEqual(
            [
                ("source-enqueue-job-1", "source_materialize", "native", "queued"),
                ("source-enqueue-job-2", "ocr", "ocr", "queued"),
            ],
            job_rows,
        )
        self.assertEqual(
            [
                ("source-enqueue-job-1", 1, "enqueued", "{}"),
                ("source-enqueue-job-2", 1, "enqueued", "{}"),
            ],
            events,
        )

        duplicate = NewProcessingJob(
            id="source-enqueue-duplicate", spec=native_spec,
            idempotency_key=jobs[0][0].idempotency_key, created_at=self.now,
        )
        async with SqlAlchemyUnitOfWork(self.session_factory) as work:
            source, result = await work.sources.enqueue_with_job(
                native, duplicate, spec_json=jobs[0][1], spec_sha256=hash_job_spec(jobs[0][1]),
            )
            await work.commit()
        self.assertEqual(native.id, source.id)
        self.assertTrue(result.deduplicated)
        self.assertEqual(jobs[0][0].id, result.job.id)

        invalid = self._source("source-enqueue-invalid", mode="native", provider="local", model="pymupdf", pdf_sha="f" * 64, status="queued")
        invalid_spec = SourceMaterializeJobSpecV1(
            paper_id="paper-1", source_document_id=invalid.id, processing_version="native-v1",
        )
        invalid_raw = encode_job_spec_v1(invalid_spec).replace(
            '"processingVersion":"native-v1"', '"apiKey":"TOP-SECRET"',
        )
        before = self._counts()
        async with SqlAlchemyUnitOfWork(self.session_factory) as work:
            with self.assertRaises(JobSpecValidationError):
                await work.sources.enqueue_with_job(
                    invalid,
                    NewProcessingJob(
                        id="source-enqueue-invalid-job", spec=invalid_spec,
                        idempotency_key="invalid-key", created_at=self.now,
                    ),
                    spec_json=invalid_raw,
                    spec_sha256=hash_job_spec(invalid_raw),
                )
        self.assertEqual(before, self._counts())

    async def test_artifact_enqueue_requires_a_ready_same_paper_source_and_is_atomic(self) -> None:
        source = self._source("source-artifact-enqueue", mode="native", provider="local", model="pymupdf", pdf_sha="d" * 64)
        artifact = GeneratedArtifact(
            id="artifact-enqueue",
            paper_id="paper-1",
            kind="explainer",
            source_document_id=source.id,
            status="queued",
            generator_provider="llm-test",
            generator_model="llm-v1",
            prompt_version="explain-v1",
            created_at=self.now,
            updated_at=self.now,
        )
        spec = ExplainJobSpecV1(
            paper_id="paper-1", source_document_id=source.id, artifact_id=artifact.id,
            profile="deep", provider="llm-test", model="llm-v1", prompt_version="explain-v1",
        )
        encoded = encode_job_spec_v1(spec)
        artifact_key = build_artifact_key(
            kind="explainer", source_document_id=source.id,
            source_content_sha256=source.content_sha256 or "",
            generator_provider="llm-test", generator_model="llm-v1", prompt_version="explain-v1",
            kind_specific_options={"profile": "deep"},
        )
        job = NewProcessingJob(
            id="artifact-enqueue-job", spec=spec,
            idempotency_key=build_artifact_job_key(artifact_key, hash_job_spec(encoded)),
            created_at=self.now,
        )

        async with SqlAlchemyUnitOfWork(self.session_factory) as work:
            await work.sources.add(source)
            result = await work.artifacts.enqueue_with_job(
                artifact, job, spec_json=encoded, spec_sha256=hash_job_spec(encoded),
            )
            await work.commit()
        self.assertFalse(result.deduplicated)
        with sqlite3.connect(self.database_path) as connection:
            self.assertEqual(
                (artifact.id, "explainer", "queued"),
                connection.execute(
                    "SELECT id,kind,status FROM generated_artifacts WHERE id=?", (artifact.id,)
                ).fetchone(),
            )
            self.assertEqual(
                (job.id, artifact.id, "explain"),
                connection.execute(
                    "SELECT id,artifact_id,job_type FROM processing_jobs WHERE id=?", (job.id,)
                ).fetchone(),
            )
            self.assertEqual(
                (job.id, 1, "enqueued"),
                connection.execute(
                    "SELECT job_id,sequence,event_type FROM processing_job_events WHERE job_id=?", (job.id,)
                ).fetchone(),
            )

        not_ready = self._source("source-not-ready", mode="native", provider="local", model="pymupdf", pdf_sha="e" * 64)
        not_ready = SourceDocument(
            **{field: getattr(not_ready, field) for field in (
                "id", "paper_id", "mode", "provider", "model", "pdf_sha256", "options_hash",
                "processing_version", "created_at", "updated_at", "content_sha256", "markdown", "page_count",
            )},
            status="queued",
        )
        async with SqlAlchemyUnitOfWork(self.session_factory) as work:
            await work.sources.add(not_ready)
            await work.commit()
        blocked_artifact = GeneratedArtifact(
            id="artifact-not-ready", paper_id="paper-1", kind="explainer",
            source_document_id=not_ready.id, status="queued", generator_provider="llm-test",
            generator_model="llm-v1", prompt_version="explain-v1", created_at=self.now, updated_at=self.now,
        )
        blocked_spec = ExplainJobSpecV1(
            paper_id="paper-1", source_document_id=not_ready.id, artifact_id=blocked_artifact.id,
            profile="deep", provider="llm-test", model="llm-v1", prompt_version="explain-v1",
        )
        before = self._counts()
        async with SqlAlchemyUnitOfWork(self.session_factory) as work:
            with self.assertRaises(JobSpecValidationError):
                await work.artifacts.enqueue_with_job(
                    blocked_artifact,
                    NewProcessingJob(
                        id="artifact-not-ready-job", spec=blocked_spec, idempotency_key="not-ready-key",
                        created_at=self.now,
                    ),
                    spec_json=encode_job_spec_v1(blocked_spec),
                    spec_sha256=hash_job_spec(encode_job_spec_v1(blocked_spec)),
                )
        self.assertEqual(before, self._counts())

    async def test_artifact_head_compare_and_set_has_one_winner_and_cascades_on_delete(self) -> None:
        source = self._source("source-head", mode="native", provider="local", model="pymupdf", pdf_sha="f" * 64)
        first = GeneratedArtifact(
            id="artifact-head-first", paper_id="paper-1", kind="explainer", source_document_id=source.id,
            status="ready", generator_provider="llm-test", generator_model="llm-v1", prompt_version="v1",
            content="first\n", content_sha256="1" * 64, created_at=self.now, updated_at=self.now,
        )
        second = GeneratedArtifact(
            id="artifact-head-second", paper_id="paper-1", kind="explainer", source_document_id=source.id,
            status="ready", generator_provider="llm-test", generator_model="llm-v1", prompt_version="v2",
            content="second\n", content_sha256="2" * 64, created_at=self.now, updated_at=self.now,
        )
        third = GeneratedArtifact(
            id="artifact-head-third", paper_id="paper-1", kind="explainer", source_document_id=source.id,
            status="ready", generator_provider="llm-test", generator_model="llm-v1", prompt_version="v3",
            content="third\n", content_sha256="3" * 64, created_at=self.now, updated_at=self.now,
        )
        async with SqlAlchemyUnitOfWork(self.session_factory) as work:
            await work.sources.add(source)
            await work.artifacts.add(first)
            await work.artifacts.add(second)
            await work.artifacts.add(third)
            self.assertTrue(await work.artifacts.publish_head(
                paper_id="paper-1", kind="explainer", artifact_id=first.id,
                expected_artifact_id=None, updated_at=self.now,
            ))
            await work.commit()

        async def publish(artifact_id: str) -> bool:
            async with SqlAlchemyUnitOfWork(self.session_factory) as work:
                changed = await work.artifacts.publish_head(
                    paper_id="paper-1", kind="explainer", artifact_id=artifact_id,
                    expected_artifact_id=first.id, updated_at=self.now,
                )
                await work.commit()
                return changed

        outcomes = await asyncio.gather(publish(second.id), publish(third.id))
        self.assertEqual([False, True], sorted(outcomes))
        with sqlite3.connect(self.database_path) as connection:
            head = connection.execute(
                "SELECT paper_id,kind,artifact_id FROM paper_artifact_heads"
            ).fetchone()
        self.assertIn(head, (("paper-1", "explainer", second.id), ("paper-1", "explainer", third.id)))

        async with SqlAlchemyUnitOfWork(self.session_factory) as work:
            self.assertFalse(await work.artifacts.publish_head(
                paper_id="paper-1", kind="explainer", artifact_id=first.id,
                expected_artifact_id=first.id, updated_at=self.now,
            ))
            await work.commit()
        with sqlite3.connect(self.database_path) as connection:
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("DELETE FROM generated_artifacts WHERE id=?", (head[2],))
            connection.commit()
            self.assertIsNone(connection.execute("SELECT 1 FROM paper_artifact_heads").fetchone())

    def _counts(self) -> tuple[int, int, int, int]:
        with sqlite3.connect(self.database_path) as connection:
            return tuple(
                connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
                for table in ("document_sources", "generated_artifacts", "processing_jobs", "processing_job_events")
            )

    def _source(
        self, identifier: str, *, mode: str, provider: str, model: str, pdf_sha: str = "a" * 64,
        status: str = "ready",
    ) -> SourceDocument:
        return SourceDocument(
            id=identifier,
            paper_id="paper-1",
            mode=mode,
            status=status,
            provider=provider,
            model=model,
            pdf_sha256=pdf_sha,
            options_hash="b" * 64,
            processing_version="native-v1" if mode == "native" else "ocr-v1",
            content_sha256="c" * 64,
            markdown="source content\n",
            page_count=1,
            created_at=self.now,
            updated_at=self.now,
        )


class ProcessingLeaseTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory(prefix="study-app-processing-lease-")
        self.database_path = Path(self._temp.name) / "queue" / "app.db"
        create_legacy_database(self.database_path)
        run_alembic(self.database_path, "20260807_02")
        self.session_factory = create_async_session_factory(DatabaseSettings(self.database_path))
        self.engine = self.session_factory.kw["bind"]
        self.now = datetime(2026, 8, 9, 12, 0, tzinfo=timezone.utc)

    async def asyncTearDown(self) -> None:
        await self.engine.dispose()
        self._temp.cleanup()

    async def test_claim_strictly_decodes_spec_before_any_transition(self) -> None:
        valid = await self._enqueue_source_job("job-valid", "source-valid", self.now)
        invalid = await self._enqueue_source_job(
            "job-invalid", "source-invalid", self.now + timedelta(seconds=1),
        )
        with closing(sqlite3.connect(self.database_path)) as connection:
            # Deliberately bypass the database guard: this is a storage-corruption fixture,
            # not an application-path write.
            connection.execute("DROP TRIGGER processing_jobs_spec_guard_update")
            connection.execute(
                "UPDATE processing_jobs SET spec_json=?, progress_json=? WHERE id=?",
                ('{"arguments":{"processingVersion":"native-v1"}', '{"rebuild":"never"}', invalid.id),
            )
            connection.commit()
            invalid_before = connection.execute(
                "SELECT status,attempt,progress_json,lease_owner,lease_token,lease_expires_at "
                "FROM processing_jobs WHERE id=?", (invalid.id,)
            ).fetchone()
            invalid_events_before = connection.execute(
                "SELECT count(*) FROM processing_job_events WHERE job_id=?", (invalid.id,)
            ).fetchone()[0]

        async with SqlAlchemyUnitOfWork(self.session_factory) as work:
            lease = await work.jobs.claim_next(worker_id="worker-1", now=self.now, lease_seconds=30)
            await work.commit()
        self.assertIsNotNone(lease)
        assert lease is not None
        self.assertEqual(valid.id, lease.job.id)
        self.assertEqual(valid.spec, lease.spec.value)
        self.assertEqual(encode_job_spec_v1(valid.spec), lease.spec.raw_json)
        self.assertEqual(hash_job_spec(lease.spec.raw_json), lease.spec.sha256)

        async with SqlAlchemyUnitOfWork(self.session_factory) as work:
            with self.assertRaises(JobSpecValidationError) as caught:
                await work.jobs.claim_next(
                    worker_id="worker-2", now=self.now + timedelta(seconds=1), lease_seconds=30,
                )
        self.assertEqual("JOB_SPEC_INVALID", caught.exception.code)
        with closing(sqlite3.connect(self.database_path)) as connection:
            self.assertEqual(
                invalid_before,
                connection.execute(
                    "SELECT status,attempt,progress_json,lease_owner,lease_token,lease_expires_at "
                    "FROM processing_jobs WHERE id=?", (invalid.id,)
                ).fetchone(),
            )
            self.assertEqual(
                invalid_events_before,
                connection.execute(
                    "SELECT count(*) FROM processing_job_events WHERE job_id=?", (invalid.id,)
                ).fetchone()[0],
            )

    async def test_explicit_retry_and_orphan_recovery_preserve_exact_spec_bytes(self) -> None:
        parent = await self._enqueue_source_job("job-parent", "source-parent", self.now)
        parent_raw = encode_job_spec_v1(parent.spec)
        async with SqlAlchemyUnitOfWork(self.session_factory) as work:
            lease = await work.jobs.claim_next(worker_id="worker-1", now=self.now, lease_seconds=10)
            await work.commit()
        assert lease is not None
        async with SqlAlchemyUnitOfWork(self.session_factory) as work:
            await work.jobs.fail(
                lease, JobFailure(code="TEST_FAILURE", retryable=False), now=self.now + timedelta(seconds=1),
            )
            await work.commit()
        async def retry_once():
            async with SqlAlchemyUnitOfWork(self.session_factory) as work:
                result = await work.jobs.retry(parent.id, now=self.now + timedelta(seconds=2))
                await work.commit()
                return result

        first_retry, second_retry = await asyncio.gather(retry_once(), retry_once())
        self.assertEqual(first_retry.job.id, second_retry.job.id)
        self.assertEqual([False, True], sorted((first_retry.deduplicated, second_retry.deduplicated)))
        retry = first_retry
        self.assertNotEqual(parent.id, retry.job.id)
        with closing(sqlite3.connect(self.database_path)) as connection:
            self.assertEqual("failed", connection.execute(
                "SELECT status FROM processing_jobs WHERE id=?", (parent.id,),
            ).fetchone()[0])
            self.assertEqual(parent_raw, connection.execute(
                "SELECT spec_json FROM processing_jobs WHERE id=?", (parent.id,),
            ).fetchone()[0])
            self.assertEqual(parent_raw, connection.execute(
                "SELECT spec_json FROM processing_jobs WHERE id=?", (retry.job.id,),
            ).fetchone()[0])
            self.assertEqual(hash_job_spec(parent_raw), hash_job_spec(connection.execute(
                "SELECT spec_json FROM processing_jobs WHERE id=?", (retry.job.id,),
            ).fetchone()[0]))

        async with SqlAlchemyUnitOfWork(self.session_factory) as work:
            child_lease = await work.jobs.claim_next(
                worker_id="worker-2", now=self.now + timedelta(seconds=2), lease_seconds=10,
            )
            await work.commit()
        assert child_lease is not None
        self.assertEqual(retry.job.id, child_lease.job.id)
        async with SqlAlchemyUnitOfWork(self.session_factory) as work:
            recovered = await work.jobs.claim_next(
                worker_id="worker-3", now=self.now + timedelta(seconds=13), lease_seconds=10,
            )
            await work.commit()
        assert recovered is not None
        self.assertEqual(retry.job.id, recovered.job.id)
        with closing(sqlite3.connect(self.database_path)) as connection:
            self.assertEqual(parent_raw, connection.execute(
                "SELECT spec_json FROM processing_jobs WHERE id=?", (retry.job.id,),
            ).fetchone()[0])

        tampered = await self._enqueue_source_job("job-tampered", "source-tampered", self.now)
        async with SqlAlchemyUnitOfWork(self.session_factory) as work:
            tampered_lease = await work.jobs.claim_next(
                worker_id="worker-4", now=self.now, lease_seconds=10,
            )
            await work.commit()
        assert tampered_lease is not None
        async with SqlAlchemyUnitOfWork(self.session_factory) as work:
            await work.jobs.fail(
                tampered_lease, JobFailure(code="TEST_FAILURE", retryable=False), now=self.now + timedelta(seconds=1),
            )
            await work.commit()
        with closing(sqlite3.connect(self.database_path)) as connection:
            connection.execute("DROP TRIGGER processing_jobs_spec_guard_update")
            connection.execute("UPDATE processing_jobs SET spec_json=? WHERE id=?", ("{", tampered.id))
            connection.commit()
            before_tamper_retry = connection.execute(
                "SELECT status FROM document_sources WHERE id=?", ("source-tampered",)
            ).fetchone()[0]
            before_descendants = connection.execute(
                "SELECT count(*) FROM processing_jobs WHERE retry_of_job_id=?", (tampered.id,)
            ).fetchone()[0]
            before_events = connection.execute(
                "SELECT count(*) FROM processing_job_events WHERE job_id=?", (tampered.id,)
            ).fetchone()[0]
        async with SqlAlchemyUnitOfWork(self.session_factory) as work:
            with self.assertRaises(JobSpecValidationError):
                await work.jobs.retry(tampered.id, now=self.now + timedelta(seconds=2))
        with closing(sqlite3.connect(self.database_path)) as connection:
            self.assertEqual(before_tamper_retry, connection.execute(
                "SELECT status FROM document_sources WHERE id=?", ("source-tampered",)
            ).fetchone()[0])
            self.assertEqual(before_descendants, connection.execute(
                "SELECT count(*) FROM processing_jobs WHERE retry_of_job_id=?", (tampered.id,)
            ).fetchone()[0])
            self.assertEqual(before_events, connection.execute(
                "SELECT count(*) FROM processing_job_events WHERE job_id=?", (tampered.id,)
            ).fetchone()[0])

    async def test_claim_uses_due_order_and_one_lease_wins_across_connections(self) -> None:
        first = await self._enqueue_source_job("job-first", "source-first", self.now)
        second = await self._enqueue_source_job(
            "job-second", "source-second", self.now + timedelta(seconds=1),
        )
        async with SqlAlchemyUnitOfWork(self.session_factory) as work:
            initial = await work.jobs.claim_next(worker_id="initial", now=self.now, lease_seconds=30)
            await work.commit()
        assert initial is not None
        self.assertEqual(first.id, initial.job.id)

        async def claim(worker_id: str):
            async with SqlAlchemyUnitOfWork(self.session_factory) as work:
                claimed = await work.jobs.claim_next(
                    worker_id=worker_id, now=self.now + timedelta(seconds=1), lease_seconds=30,
                )
                await work.commit()
                return claimed

        winner, loser = await asyncio.gather(claim("worker-a"), claim("worker-b"))
        self.assertEqual(1, sum(lease is not None for lease in (winner, loser)))
        self.assertEqual(second.id, next(lease for lease in (winner, loser) if lease is not None).job.id)

    async def test_lease_token_and_owner_fence_progress_complete_and_fail(self) -> None:
        job = await self._enqueue_source_job("job-fenced", "source-fenced", self.now)
        async with SqlAlchemyUnitOfWork(self.session_factory) as work:
            lease = await work.jobs.claim_next(worker_id="worker-1", now=self.now, lease_seconds=30)
            await work.commit()
        assert lease is not None
        async with SqlAlchemyUnitOfWork(self.session_factory) as work:
            await work.jobs.report_progress(
                lease, JobProgress({"phase": "one", "completed": 1}), now=self.now + timedelta(seconds=1),
            )
            await work.commit()
        stale = processing.JobLease(
            job=lease.job, spec=lease.spec, worker_id="worker-1", token="stale-token",
            expires_at=lease.expires_at,
        )
        with closing(sqlite3.connect(self.database_path)) as connection:
            before = connection.execute(
                "SELECT status,progress_json,lease_owner,lease_token FROM processing_jobs WHERE id=?", (job.id,)
            ).fetchone()
        async with SqlAlchemyUnitOfWork(self.session_factory) as work:
            with self.assertRaises(JobLeaseLostError):
                await work.jobs.report_progress(stale, JobProgress({"phase": "old"}), now=self.now + timedelta(seconds=2))
        async with SqlAlchemyUnitOfWork(self.session_factory) as work:
            await work.jobs.complete(lease, JobResult({"outcome": "ok"}), now=self.now + timedelta(seconds=3))
            await work.commit()
        async with SqlAlchemyUnitOfWork(self.session_factory) as work:
            with self.assertRaises(JobLeaseLostError):
                await work.jobs.fail(stale, JobFailure(code="OLD", retryable=False), now=self.now + timedelta(seconds=4))
        with closing(sqlite3.connect(self.database_path)) as connection:
            after = connection.execute(
                "SELECT status,progress_json,lease_owner,lease_token,result_json FROM processing_jobs WHERE id=?", (job.id,)
            ).fetchone()
        self.assertEqual("running", before[0])
        self.assertEqual("succeeded", after[0])
        self.assertEqual(before[1], after[1])
        self.assertEqual((None, None), after[2:4])
        self.assertEqual('{"outcome":"ok"}', after[4])

    async def test_retry_backoff_is_deterministic_bounded_and_terminal_at_max_attempts(self) -> None:
        job = await self._enqueue_source_job("job-backoff", "source-backoff", self.now, max_attempts=3)
        async with SqlAlchemyUnitOfWork(self.session_factory) as work:
            first = await work.jobs.claim_next(worker_id="worker-1", now=self.now, lease_seconds=30)
            await work.commit()
        assert first is not None
        async with SqlAlchemyUnitOfWork(self.session_factory) as work:
            await work.jobs.fail(first, JobFailure(code="TIMEOUT", retryable=True), now=self.now)
            await work.commit()
        self.assertEqual(self.now + timedelta(seconds=5), self._available_at(job.id))
        with closing(sqlite3.connect(self.database_path)) as connection:
            self.assertEqual(("queued", 1), connection.execute(
                "SELECT status,attempt FROM processing_jobs WHERE id=?", (job.id,)
            ).fetchone())
        async with SqlAlchemyUnitOfWork(self.session_factory) as work:
            second = await work.jobs.claim_next(
                worker_id="worker-2", now=self.now + timedelta(seconds=5), lease_seconds=30,
            )
            await work.commit()
        assert second is not None
        self.assertEqual(2, second.job.attempt)
        async with SqlAlchemyUnitOfWork(self.session_factory) as work:
            await work.jobs.fail(
                second, JobFailure(code="RATE_LIMIT", retryable=True),
                now=self.now + timedelta(seconds=5), retry_after_seconds=901,
            )
            await work.commit()
        self.assertEqual(self.now + timedelta(seconds=905), self._available_at(job.id))
        async with SqlAlchemyUnitOfWork(self.session_factory) as work:
            third = await work.jobs.claim_next(
                worker_id="worker-3", now=self.now + timedelta(seconds=905), lease_seconds=30,
            )
            await work.commit()
        assert third is not None
        self.assertEqual(3, third.job.attempt)
        async with SqlAlchemyUnitOfWork(self.session_factory) as work:
            await work.jobs.fail(third, JobFailure(code="TIMEOUT", retryable=True), now=self.now + timedelta(seconds=905))
            await work.commit()
        with closing(sqlite3.connect(self.database_path)) as connection:
            self.assertEqual("failed", connection.execute(
                "SELECT status FROM processing_jobs WHERE id=?", (job.id,)
            ).fetchone()[0])

    def _available_at(self, job_id: str) -> datetime:
        with closing(sqlite3.connect(self.database_path)) as connection:
            raw = connection.execute(
                "SELECT available_at FROM processing_jobs WHERE id=?", (job_id,)
            ).fetchone()[0]
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))

    async def _enqueue_source_job(
        self, job_id: str, source_id: str, created_at: datetime, *, max_attempts: int = 3,
    ) -> NewProcessingJob:
        source = SourceDocument(
            id=source_id, paper_id="paper-1", mode="native", status="queued", provider="local",
            model="pymupdf", pdf_sha256=hashlib.sha256(source_id.encode("utf-8")).hexdigest(),
            options_hash="c" * 64, processing_version="native-v1", created_at=created_at, updated_at=created_at,
        )
        spec = SourceMaterializeJobSpecV1(
            paper_id="paper-1", source_document_id=source.id, processing_version="native-v1",
        )
        raw = encode_job_spec_v1(spec)
        job = NewProcessingJob(
            id=job_id, spec=spec,
            idempotency_key=build_source_job_key(
                build_source_key(
                    paper_id=source.paper_id, mode="native", provider=source.provider, model=source.model,
                    pdf_sha256=source.pdf_sha256, options_hash=source.options_hash,
                    processing_version=source.processing_version,
                ),
                hash_job_spec(raw),
            ),
            created_at=created_at, max_attempts=max_attempts,
        )
        async with SqlAlchemyUnitOfWork(self.session_factory) as work:
            await work.sources.add(source)
            await work.jobs.insert_with_spec(job, spec_json=raw, spec_sha256=hash_job_spec(raw))
            await work.commit()
        return job
