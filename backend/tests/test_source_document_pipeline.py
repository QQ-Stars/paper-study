from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path
import tempfile
import unittest


NOW = datetime(2026, 8, 9, 12, 0, tzinfo=timezone.utc)


class _NativeDouble:
    provider = "local"
    model = "native-test-v1"
    processing_version = "native-test-v1"

    def __init__(self, value: str = "# native\n") -> None:
        self.calls = 0
        self.value = value

    def extract(self, path: Path):
        self.calls += 1
        from backend.app.application.ports import ExtractedSource

        return ExtractedSource(
            markdown=self.value,
            content_sha256="0" * 64,
            page_count=1,
            provider=self.provider,
            model=self.model,
            processing_version=self.processing_version,
        )


class _PanicDouble:
    def __init__(self, message: str) -> None:
        self.calls = 0
        self.message = message

    def __getattr__(self, name: str):
        def panic(*_args, **_kwargs):
            self.calls += 1
            raise AssertionError(self.message)

        return panic


class _OcrDouble:
    provider_id = "fake"
    processing_version = "fake-ocr-v1"

    def __init__(self, pages: dict[int, str]) -> None:
        self.pages = pages
        self.calls = []

    async def extract_batch(self, request):
        self.calls.append(request)
        from backend.app.application.ports.ocr_provider import OcrPageResult, OcrResult
        import hashlib

        return OcrResult(
            pages=tuple(
                OcrPageResult(
                    page_number=page,
                    markdown=self.pages[page],
                    content_sha256=hashlib.sha256(self.pages[page].encode()).hexdigest(),
                    provider_page_id=f"page-{page}",
                )
                for page in request.page_numbers
            ),
            provider=self.provider_id,
            model=request.model,
            processing_version=self.processing_version,
            provider_request_id=None,
        )


class _OcrRegistry:
    def __init__(self, provider) -> None:
        self.provider = provider
        self.resolved = []

    def resolve(self, provider_id: str):
        self.resolved.append(provider_id)
        return self.provider


class _PageReader:
    def __init__(self, page_count: int) -> None:
        self.page_count_value = page_count
        self.calls = 0

    def page_count(self, pdf_bytes: bytes) -> int:
        self.calls += 1
        return self.page_count_value


class _InvalidPageReader:
    def page_count(self, pdf_bytes: bytes):
        return 0


class _EncryptedPageReader:
    def page_count(self, pdf_bytes: bytes) -> int:
        raise RuntimeError("encrypted PDF cannot be opened")


class _CheckpointRepo:
    def __init__(self) -> None:
        self.rows: dict[tuple[str, int], object] = {}
        self.writes = []
        self.leases = []

    async def list_succeeded(self, source_id: str):
        return {page for (sid, page), row in self.rows.items() if sid == source_id and row.status == "succeeded"}

    async def save_success(self, source_id: str, page, *, lease=None):
        self.writes.append(page)
        self.leases.append(lease)
        row = type("Checkpoint", (), {"status": "succeeded", "page_number": page, "markdown": page.markdown})()
        self.rows[(source_id, page.page_number)] = row

    async def read(self, source_id: str, page_number: int):
        return self.rows.get((source_id, page_number))


class _SourceRow:
    def __init__(self, *, mode: str, pdf_path: Path, source_id: str = "source-1") -> None:
        self.id = source_id
        self.paper_id = "paper-1"
        self.mode = mode
        self.pdf_path = pdf_path
        self.pdf_sha256 = ""  # populated by the processor's first read
        self.provider = "local" if mode == "native" else "fake"
        self.model = "native-test-v1" if mode == "native" else "fake-ocr-v1"
        self.options = {"pageBatchSize": 1, "maxConcurrency": 1}
        self.status = "queued"
        self.markdown = None
        self.page_count = None
        self.content_sha256 = None


class _SourceRepository:
    def __init__(self, source: _SourceRow) -> None:
        self.source = source

    async def get(self, source_id: str):
        return self.source if source_id == self.source.id else None

    async def publish_ready(self, *_args, **_kwargs):
        self.source.status = "ready"
        return True

    async def publish_stale(self, *_args, **_kwargs):
        self.source.status = "stale"
        return True


class _JobRepository:
    def __init__(self) -> None:
        self.completions = []

    async def complete(self, lease, result, *, now):
        self.completions.append(result)
        return lease.job


class _Work:
    def __init__(self, source_repo: _SourceRepository, jobs: _JobRepository) -> None:
        self.sources = source_repo
        self.jobs = jobs

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def commit(self):
        return None


class _Lease:
    def __init__(self) -> None:
        self.job = type("Job", (), {"id": "job-1"})()
        self.worker_id = "worker-1"
        self.token = "token-1"


class SourceModeDispatchTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_processors_reject_expired_or_cancelled_lease_before_writes(self) -> None:
        from contextlib import closing
        from datetime import timedelta
        import hashlib
        import sqlite3

        from backend.app.application.source_documents import (
            SourceDocumentProcessor,
            build_native_source_processor,
            build_ocr_source_processor,
        )
        from backend.app.domain import JobLeaseLostError, SourceDocument
        from backend.app.domain.processing import (
            NewProcessingJob,
            OcrJobSpecV1,
            SourceMaterializeJobSpecV1,
            build_source_job_key,
            build_source_key,
            encode_job_spec_v1,
            hash_canonical_json,
            hash_job_spec,
        )
        from backend.app.repositories.ocr_checkpoints import SqlAlchemyOcrCheckpointRepository
        from backend.app.repositories.unit_of_work import SqlAlchemyUnitOfWork
        from backend.tests.support.p2_database import p2_database_fixture

        for mode in ("native", "ocr"):
            for cancellation in (False, True):
                case = f"{mode}-{'cancelled' if cancellation else 'expired'}"
                with self.subTest(case=case):
                    async with p2_database_fixture(prefix=f"study-app-source-active-{case}-") as database:
                        pdf_bytes = b"active lease fixture"
                        pdf_path = database.database_path.parent / "paper.pdf"
                        pdf_path.write_bytes(pdf_bytes)
                        with closing(sqlite3.connect(database.database_path)) as connection:
                            connection.execute(
                                "UPDATE papers SET pdf_path=? WHERE id='paper-1'",
                                (str(pdf_path),),
                            )
                            connection.commit()

                        provider = "fake" if mode == "ocr" else "local"
                        model = "fake-ocr-v1" if mode == "ocr" else "pymupdf"
                        options_hash = (
                            hash_canonical_json({"pageBatchSize": 1, "maxConcurrency": 1})
                            if mode == "ocr"
                            else "c" * 64
                        )
                        source = SourceDocument(
                            id=f"source-active-{case}",
                            paper_id="paper-1",
                            mode=mode,
                            status="queued",
                            provider=provider,
                            model=model,
                            pdf_sha256=hashlib.sha256(pdf_bytes).hexdigest(),
                            options_hash=options_hash,
                            processing_version="native-v1" if mode == "native" else "fake-v1",
                            created_at=NOW,
                            updated_at=NOW,
                        )
                        spec = (
                            OcrJobSpecV1(
                                paper_id=source.paper_id,
                                source_document_id=source.id,
                                provider=source.provider,
                                model=source.model,
                            )
                            if mode == "ocr"
                            else SourceMaterializeJobSpecV1(
                                paper_id=source.paper_id,
                                source_document_id=source.id,
                                processing_version=source.processing_version,
                            )
                        )
                        raw = encode_job_spec_v1(spec)
                        job = NewProcessingJob(
                            id=f"job-active-{case}",
                            spec=spec,
                            idempotency_key=build_source_job_key(
                                build_source_key(
                                    paper_id=source.paper_id,
                                    mode=source.mode,
                                    provider=source.provider,
                                    model=source.model,
                                    pdf_sha256=source.pdf_sha256,
                                    options_hash=source.options_hash,
                                    processing_version=source.processing_version,
                                ),
                                hash_job_spec(raw),
                            ),
                            created_at=NOW,
                        )
                        async with SqlAlchemyUnitOfWork(database.session_factory) as work:
                            await work.sources.add(source)
                            await work.jobs.insert_with_spec(
                                job,
                                spec_json=raw,
                                spec_sha256=hash_job_spec(raw),
                            )
                            await work.commit()
                        async with SqlAlchemyUnitOfWork(database.session_factory) as work:
                            lease = await work.jobs.claim_next(
                                worker_id=f"worker-active-{case}",
                                now=NOW,
                                lease_seconds=30,
                            )
                            await work.commit()
                        assert lease is not None
                        check_at = NOW + timedelta(seconds=2 if cancellation else 31)
                        if cancellation:
                            async with SqlAlchemyUnitOfWork(database.session_factory) as work:
                                await work.jobs.cancel(job.id, now=NOW + timedelta(seconds=1))
                                await work.commit()

                        native = _NativeDouble()
                        ocr = _OcrDouble({1: "# page\n"})
                        checkpoints = SqlAlchemyOcrCheckpointRepository(
                            database.session_factory,
                            clock=lambda: check_at,
                        )
                        processor = SourceDocumentProcessor(
                            lambda: SqlAlchemyUnitOfWork(database.session_factory),
                            native_factory=lambda: build_native_source_processor(
                                native,
                                clock=lambda: check_at,
                            ),
                            ocr_factory=lambda: build_ocr_source_processor(
                                _OcrRegistry(ocr),
                                page_reader=_PageReader(1),
                                checkpoint_repository=checkpoints,
                                clock=lambda: check_at,
                            ),
                            clock=lambda: check_at,
                        )

                        with self.assertRaises(JobLeaseLostError) as caught:
                            await processor.process(lease, source.id)
                        self.assertEqual("JOB_LEASE_LOST", caught.exception.code)

                        with closing(sqlite3.connect(database.database_path)) as connection:
                            source_row = connection.execute(
                                "SELECT status,markdown FROM document_sources WHERE id=?",
                                (source.id,),
                            ).fetchone()
                            job_row = connection.execute(
                                "SELECT status,result_json FROM processing_jobs WHERE id=?",
                                (job.id,),
                            ).fetchone()
                            checkpoint_count = connection.execute(
                                "SELECT count(*) FROM ocr_page_checkpoints WHERE source_document_id=?",
                                (source.id,),
                            ).fetchone()[0]
                        self.assertEqual(("running", None), source_row)
                        self.assertEqual(("running", None), job_row)
                        self.assertEqual(0, checkpoint_count)
                        self.assertEqual(0, native.calls)
                        self.assertEqual([], ocr.calls)

    async def test_ocr_option_hash_drift_rejects_before_provider_or_checkpoint(self) -> None:
        from backend.app.application.source_documents import (
            SourceDocumentProcessor,
            build_ocr_source_processor,
        )
        from backend.app.domain.processing import JobSpecValidationError, OcrJobSpecV1

        with tempfile.TemporaryDirectory(prefix="study-app-task7-option-drift-") as temp:
            pdf = Path(temp) / "paper.pdf"
            pdf.write_bytes(b"ocr-pdf")
            source = _SourceRow(mode="ocr", pdf_path=pdf)
            source.options = None
            source.options_hash = "f" * 64
            spec = OcrJobSpecV1(
                paper_id=source.paper_id,
                source_document_id=source.id,
                provider=source.provider,
                model=source.model,
                page_batch_size=2,
                max_concurrency=2,
            )
            lease = _Lease()
            lease.spec = type("StoredSpec", (), {"value": spec})()
            provider = _OcrDouble({1: "# one\n"})
            registry = _OcrRegistry(provider)
            checkpoints = _CheckpointRepo()
            work = _Work(_SourceRepository(source), _JobRepository())
            processor = SourceDocumentProcessor(
                lambda: work,
                native_factory=lambda: (_ for _ in ()).throw(AssertionError("native")),
                ocr_factory=lambda: build_ocr_source_processor(
                    registry,
                    page_reader=_PageReader(1),
                    checkpoint_repository=checkpoints,
                ),
                clock=lambda: NOW,
            )
            with self.assertRaises(JobSpecValidationError):
                await processor.process(lease, source.id)

        self.assertEqual([], registry.resolved)
        self.assertEqual([], provider.calls)
        self.assertEqual([], checkpoints.writes)

    async def test_ocr_uses_immutable_lease_options_bound_to_persisted_source_hash(self) -> None:
        from backend.app.application.source_documents import (
            SourceDocumentProcessor,
            build_ocr_source_processor,
        )
        from backend.app.domain.processing import OcrJobSpecV1, hash_canonical_json

        with tempfile.TemporaryDirectory(prefix="study-app-task7-spec-options-") as temp:
            pdf = Path(temp) / "paper.pdf"
            pdf.write_bytes(b"ocr-pdf")
            source = _SourceRow(mode="ocr", pdf_path=pdf)
            source.options = None
            effective_options = {
                "language": "en",
                "pageBatchSize": 2,
                "maxConcurrency": 2,
            }
            source.options_hash = hash_canonical_json(effective_options)
            spec = OcrJobSpecV1(
                paper_id=source.paper_id,
                source_document_id=source.id,
                provider=source.provider,
                model=source.model,
                options={"language": "en"},
                page_batch_size=2,
                max_concurrency=2,
            )
            lease = _Lease()
            lease.spec = type("StoredSpec", (), {"value": spec})()
            ocr = _OcrDouble({1: "# one\n", 2: "# two\n"})
            checkpoints = _CheckpointRepo()
            work = _Work(_SourceRepository(source), _JobRepository())
            processor = SourceDocumentProcessor(
                lambda: work,
                native_factory=lambda: (_ for _ in ()).throw(AssertionError("native")),
                ocr_factory=lambda: build_ocr_source_processor(
                    _OcrRegistry(ocr),
                    page_reader=_PageReader(2),
                    checkpoint_repository=checkpoints,
                ),
                clock=lambda: NOW,
            )
            await processor.process(lease, source.id)

        self.assertEqual([[1, 2]], [list(request.page_numbers) for request in ocr.calls])
        self.assertEqual(effective_options, dict(ocr.calls[0].options))
        self.assertEqual([lease, lease], checkpoints.leases)

    async def test_ocr_retryable_provider_failures_are_typed_without_fallback(self) -> None:
        from backend.app.application.source_documents import (
            SourceDocumentProcessor,
            build_ocr_source_processor,
        )

        class ProviderHttpError(RuntimeError):
            def __init__(self, status_code: int, retry_after_seconds: int | None = None) -> None:
                super().__init__("raw provider body must not escape")
                self.status_code = status_code
                self.retry_after_seconds = retry_after_seconds

        cases = (
            (asyncio.TimeoutError("raw timeout"), "OCR_TIMEOUT", None),
            (ProviderHttpError(429, 120), "OCR_RATE_LIMITED", 120),
            (ProviderHttpError(500), "OCR_SERVER_ERROR", None),
        )
        for failure, expected_code, expected_retry_after in cases:
            with self.subTest(code=expected_code), tempfile.TemporaryDirectory(
                prefix=f"study-app-task7-{expected_code.lower()}-"
            ) as temp:
                pdf = Path(temp) / "paper.pdf"
                pdf.write_bytes(b"ocr-pdf")
                source = _SourceRow(mode="ocr", pdf_path=pdf)
                checkpoints = _CheckpointRepo()
                native_factory_calls = []

                class FailingProvider:
                    async def extract_batch(self, _request):
                        raise failure

                work = _Work(_SourceRepository(source), _JobRepository())
                processor = SourceDocumentProcessor(
                    lambda: work,
                    native_factory=lambda: native_factory_calls.append(True),
                    ocr_factory=lambda: build_ocr_source_processor(
                        _OcrRegistry(FailingProvider()),
                        page_reader=_PageReader(1),
                        checkpoint_repository=checkpoints,
                    ),
                    clock=lambda: NOW,
                )
                with self.assertRaises(Exception) as raised:
                    await processor.process(_Lease(), source.id)

                error = raised.exception
                self.assertEqual(expected_code, getattr(error, "code", None))
                self.assertTrue(getattr(error, "retryable", False))
                self.assertEqual(expected_retry_after, getattr(error, "retry_after_seconds", None))
                self.assertNotIn("raw provider body", str(error))
                self.assertEqual([], checkpoints.writes)
                self.assertEqual([], native_factory_calls)

    async def test_ocr_invalid_provider_pages_are_nonretryable_and_write_no_checkpoint(self) -> None:
        from backend.app.application.ports.ocr_provider import OcrPageResult, OcrResult
        from backend.app.application.source_documents import (
            SourceDocumentProcessor,
            build_ocr_source_processor,
        )

        def page(number: int, markdown: str = "# page\n") -> OcrPageResult:
            return OcrPageResult(number, markdown, "0" * 64, None)

        cases = {
            "empty": (),
            "duplicate": (page(1), page(1)),
            "out_of_order": (page(2), page(1)),
            "out_of_range": (page(1), page(3)),
        }
        for name, returned_pages in cases.items():
            with self.subTest(name=name), tempfile.TemporaryDirectory(
                prefix=f"study-app-task7-invalid-{name}-"
            ) as temp:
                pdf = Path(temp) / "paper.pdf"
                pdf.write_bytes(b"ocr-pdf")
                source = _SourceRow(mode="ocr", pdf_path=pdf)
                checkpoints = _CheckpointRepo()
                native_factory_calls = []

                class InvalidProvider:
                    async def extract_batch(self, request):
                        return OcrResult(
                            pages=returned_pages,
                            provider="fake",
                            model=request.model,
                            processing_version="fake-v1",
                            provider_request_id=None,
                        )

                work = _Work(_SourceRepository(source), _JobRepository())
                processor = SourceDocumentProcessor(
                    lambda: work,
                    native_factory=lambda: native_factory_calls.append(True),
                    ocr_factory=lambda: build_ocr_source_processor(
                        _OcrRegistry(InvalidProvider()),
                        page_reader=_PageReader(2),
                        checkpoint_repository=checkpoints,
                    ),
                    clock=lambda: NOW,
                )
                with self.assertRaises(Exception) as raised:
                    await processor.process(_Lease(), source.id)
                self.assertEqual("OCR_RESPONSE_INVALID", getattr(raised.exception, "code", None))
                self.assertEqual([], checkpoints.writes)
                self.assertEqual([], native_factory_calls)

    async def test_ocr_pdf_drift_after_provider_marks_stale_before_checkpoint(self) -> None:
        from backend.app.application.source_documents import (
            SourceDocumentProcessor,
            build_ocr_source_processor,
        )
        from backend.app.domain import SourcePdfChangedError

        with tempfile.TemporaryDirectory(prefix="study-app-task7-drift-") as temp:
            pdf = Path(temp) / "paper.pdf"
            pdf.write_bytes(b"ocr-pdf-v1")
            source = _SourceRow(mode="ocr", pdf_path=pdf)
            checkpoints = _CheckpointRepo()

            class MutatingOcr(_OcrDouble):
                async def extract_batch(self, request):
                    result = await super().extract_batch(request)
                    pdf.write_bytes(b"ocr-pdf-v2")
                    return result

            work = _Work(_SourceRepository(source), _JobRepository())
            processor = SourceDocumentProcessor(
                lambda: work,
                native_factory=lambda: (_ for _ in ()).throw(AssertionError("native")),
                ocr_factory=lambda: build_ocr_source_processor(
                    _OcrRegistry(MutatingOcr({1: "# page\n"})),
                    page_reader=_PageReader(1),
                    checkpoint_repository=checkpoints,
                ),
                clock=lambda: NOW,
            )
            with self.assertRaises(SourcePdfChangedError):
                await processor.process(_Lease(), source.id)

        self.assertEqual("stale", source.status)
        self.assertEqual([], checkpoints.writes)

    async def test_ocr_resume_skips_succeeded_pages_and_assembles_in_order(self) -> None:
        from backend.app.application.source_documents import (
            SourceDocumentProcessor,
            build_ocr_source_processor,
        )

        with tempfile.TemporaryDirectory(prefix="study-app-task7-resume-") as temp:
            pdf = Path(temp) / "paper.pdf"
            pdf.write_bytes(b"ocr-pdf")
            source = _SourceRow(mode="ocr", pdf_path=pdf)
            checkpoints = _CheckpointRepo()
            checkpoints.rows[(source.id, 1)] = type(
                "Checkpoint",
                (),
                {"status": "succeeded", "page_number": 1, "markdown": "# existing\n"},
            )()
            class ResumeOcr(_OcrDouble):
                async def extract_batch(self, request):
                    if 1 in request.page_numbers:
                        raise AssertionError("succeeded page 1 must not be requested again")
                    return await super().extract_batch(request)

            ocr = ResumeOcr({2: "# two\n", 3: "# three\n"})
            work = _Work(_SourceRepository(source), _JobRepository())
            processor = SourceDocumentProcessor(
                lambda: work,
                native_factory=lambda: (_ for _ in ()).throw(AssertionError("native")),
                ocr_factory=lambda: build_ocr_source_processor(
                    _OcrRegistry(ocr),
                    page_reader=_PageReader(3),
                    checkpoint_repository=checkpoints,
                ),
                clock=lambda: NOW,
            )
            lease = _Lease()
            result = await processor.process(lease, source.id)

        self.assertEqual("ready", result.status)
        self.assertEqual([[2], [3]], [list(request.page_numbers) for request in ocr.calls])
        self.assertEqual([2, 3], [page.page_number for page in checkpoints.writes])

    async def test_ocr_scheduler_honors_persisted_concurrency_bound(self) -> None:
        from backend.app.application.source_documents import (
            SourceDocumentProcessor,
            build_ocr_source_processor,
        )

        class ConcurrentOcr(_OcrDouble):
            def __init__(self, pages: dict[int, str]) -> None:
                super().__init__(pages)
                self.active = 0
                self.max_active = 0
                self.two_started = asyncio.Event()
                self.release = asyncio.Event()

            async def extract_batch(self, request):
                self.active += 1
                self.max_active = max(self.max_active, self.active)
                if self.active == 2:
                    self.two_started.set()
                await self.release.wait()
                try:
                    return await super().extract_batch(request)
                finally:
                    self.active -= 1

        with tempfile.TemporaryDirectory(prefix="study-app-task7-concurrency-") as temp:
            pdf = Path(temp) / "paper.pdf"
            pdf.write_bytes(b"ocr-pdf")
            source = _SourceRow(mode="ocr", pdf_path=pdf)
            source.options = {"pageBatchSize": 1, "maxConcurrency": 2}
            ocr = ConcurrentOcr({1: "# one\n", 2: "# two\n", 3: "# three\n"})
            work = _Work(_SourceRepository(source), _JobRepository())
            processor = SourceDocumentProcessor(
                lambda: work,
                native_factory=lambda: (_ for _ in ()).throw(AssertionError("native")),
                ocr_factory=lambda: build_ocr_source_processor(
                    _OcrRegistry(ocr),
                    page_reader=_PageReader(3),
                    checkpoint_repository=_CheckpointRepo(),
                ),
                clock=lambda: NOW,
            )
            processing = asyncio.create_task(processor.process(_Lease(), source.id))
            try:
                await asyncio.wait_for(ocr.two_started.wait(), timeout=0.25)
            except asyncio.TimeoutError:
                ocr.release.set()
                await processing
                self.fail("OCR scheduler never reached the persisted concurrency bound")
            ocr.release.set()
            result = await processing

        self.assertEqual("ready", result.status)
        self.assertEqual(2, ocr.max_active)
        self.assertTrue(all(request.options == source.options for request in ocr.calls))

    async def test_native_encrypted_pdf_is_typed_without_constructing_ocr(self) -> None:
        from backend.app.application.source_documents import SourceDocumentProcessor, build_native_source_processor

        class EncryptedNative:
            provider = "local"
            model = "native-test-v1"
            processing_version = "native-test-v1"

            def extract(self, _path):
                raise RuntimeError("encrypted PDF cannot be opened")

        with tempfile.TemporaryDirectory(prefix="study-app-task7-native-encrypted-") as temp:
            pdf = Path(temp) / "encrypted.pdf"
            pdf.write_bytes(b"encrypted-pdf")
            source = _SourceRow(mode="native", pdf_path=pdf)
            work = _Work(_SourceRepository(source), _JobRepository())
            ocr_factory_calls = []
            processor = SourceDocumentProcessor(
                lambda: work,
                native_factory=lambda: build_native_source_processor(EncryptedNative()),
                ocr_factory=lambda: ocr_factory_calls.append(True),
                clock=lambda: NOW,
            )
            with self.assertRaises(Exception) as raised:
                await processor.process(_Lease(), source.id)

        self.assertEqual("PDF_ENCRYPTED", getattr(raised.exception, "code", None))
        self.assertEqual([], ocr_factory_calls)

    async def test_ocr_encrypted_pdf_is_typed_and_does_not_fallback_to_native(self) -> None:
        from backend.app.application.source_documents import (
            SourceDocumentProcessor,
            build_ocr_source_processor,
        )

        with tempfile.TemporaryDirectory(prefix="study-app-task7-encrypted-") as temp:
            pdf = Path(temp) / "encrypted.pdf"
            pdf.write_bytes(b"encrypted-pdf")
            source = _SourceRow(mode="ocr", pdf_path=pdf)
            work = _Work(_SourceRepository(source), _JobRepository())
            native_factory_calls = []
            processor = SourceDocumentProcessor(
                lambda: work,
                native_factory=lambda: native_factory_calls.append(True),
                ocr_factory=lambda: build_ocr_source_processor(
                    _OcrRegistry(_OcrDouble({1: "unused"})),
                    page_reader=_EncryptedPageReader(),
                    checkpoint_repository=_CheckpointRepo(),
                ),
                clock=lambda: NOW,
            )
            with self.assertRaises(Exception) as raised:
                await processor.process(_Lease(), source.id)

        self.assertEqual("PDF_ENCRYPTED", getattr(raised.exception, "code", None))
        self.assertEqual([], native_factory_calls)


    async def test_checkpoint_write_is_fenced_by_current_lease_and_cancel_state(self) -> None:
        from contextlib import closing
        import hashlib
        import sqlite3

        from backend.app.application.ports.ocr_provider import OcrPageResult
        from backend.app.domain import JobLeaseLostError, SourceDocument
        from backend.app.domain.processing import (
            JobLease,
            NewProcessingJob,
            OcrJobSpecV1,
            build_source_job_key,
            build_source_key,
            encode_job_spec_v1,
            hash_job_spec,
        )
        from backend.app.repositories.ocr_checkpoints import SqlAlchemyOcrCheckpointRepository
        from backend.app.repositories.unit_of_work import SqlAlchemyUnitOfWork
        from backend.tests.support.p2_database import p2_database_fixture

        async with p2_database_fixture(prefix="study-app-task7-checkpoints-") as database:
            options_hash = hashlib.sha256(b"{}").hexdigest()
            source = SourceDocument(
                id="source-ocr-1",
                paper_id="paper-1",
                mode="ocr",
                status="queued",
                provider="fake",
                model="fake-ocr-v1",
                pdf_sha256=hashlib.sha256(b"ocr-pdf").hexdigest(),
                options_hash=options_hash,
                processing_version="fake-v1",
                created_at=NOW,
                updated_at=NOW,
            )
            spec = OcrJobSpecV1(
                paper_id=source.paper_id,
                source_document_id=source.id,
                provider=source.provider,
                model=source.model,
            )
            raw_spec = encode_job_spec_v1(spec)
            source_key = build_source_key(
                paper_id=source.paper_id,
                mode="ocr",
                provider=source.provider,
                model=source.model,
                pdf_sha256=source.pdf_sha256,
                options_hash=source.options_hash,
                processing_version=source.processing_version,
            )
            job = NewProcessingJob(
                id="job-ocr-1",
                spec=spec,
                idempotency_key=build_source_job_key(source_key, hash_job_spec(raw_spec)),
                created_at=NOW,
            )
            async with SqlAlchemyUnitOfWork(database.session_factory) as work:
                await work.sources.add(source)
                await work.jobs.insert_with_spec(
                    job,
                    spec_json=raw_spec,
                    spec_sha256=hash_job_spec(raw_spec),
                )
                await work.commit()
            async with SqlAlchemyUnitOfWork(database.session_factory) as work:
                lease = await work.jobs.claim_next(worker_id="worker-1", now=NOW, lease_seconds=30)
                await work.commit()
            assert lease is not None

            repository = SqlAlchemyOcrCheckpointRepository(database.session_factory, clock=lambda: NOW)
            first = OcrPageResult(1, "# page one\n", hashlib.sha256(b"# page one\n").hexdigest(), "p-1")
            await repository.save_success(source.id, first, lease=lease)
            self.assertEqual({1}, await repository.list_succeeded(source.id))
            stored_page = await repository.read(source.id, 1)
            self.assertIsNotNone(stored_page)
            self.assertEqual("# page one\n", stored_page.markdown)
            self.assertEqual({1: "# page one\n"}, await repository.read_all_succeeded(source.id))

            second = OcrPageResult(2, "# page two\n", hashlib.sha256(b"# page two\n").hexdigest(), "p-2")
            concurrent = await asyncio.gather(
                repository.save_success(source.id, second, lease=lease),
                repository.save_success(source.id, second, lease=lease),
                return_exceptions=True,
            )
            self.assertFalse(any(isinstance(value, BaseException) for value in concurrent))
            self.assertEqual([False, True], sorted(concurrent))

            conflict = OcrPageResult(
                2,
                "# conflicting\n",
                hashlib.sha256(b"# conflicting\n").hexdigest(),
                "p-conflict",
            )
            from backend.app.domain import PersistenceConflictError
            with self.assertRaises(PersistenceConflictError):
                await repository.save_success(source.id, conflict, lease=lease)

            stale = JobLease(
                job=lease.job,
                spec=lease.spec,
                worker_id=lease.worker_id,
                token="stale-token",
                expires_at=lease.expires_at,
            )
            third = OcrPageResult(3, "# page three\n", hashlib.sha256(b"# page three\n").hexdigest(), "p-3")
            with self.assertRaises(JobLeaseLostError):
                await repository.save_success(source.id, third, lease=stale)

            with closing(sqlite3.connect(database.database_path)) as connection:
                connection.execute(
                    "UPDATE processing_jobs SET cancel_requested_at=? WHERE id=?",
                    (NOW.isoformat(), lease.job.id),
                )
                connection.commit()
            with self.assertRaises(JobLeaseLostError):
                await repository.save_success(source.id, third, lease=lease)

            with closing(sqlite3.connect(database.database_path)) as connection:
                rows = connection.execute(
                    "SELECT page_number,status,markdown FROM ocr_page_checkpoints "
                    "WHERE source_document_id=? ORDER BY page_number",
                    (source.id,),
                ).fetchall()
                stored_spec = connection.execute(
                    "SELECT spec_json FROM processing_jobs WHERE id=?",
                    (lease.job.id,),
                ).fetchone()[0]
            self.assertEqual(
                [(1, "succeeded", "# page one\n"), (2, "succeeded", "# page two\n")],
                rows,
            )
            self.assertEqual(raw_spec, stored_spec)

    async def test_ocr_invalid_page_reader_result_is_typed_and_does_not_fallback_to_native(self) -> None:
        from backend.app.application.source_documents import SourceDocumentProcessor, build_ocr_source_processor

        with tempfile.TemporaryDirectory(prefix="study-app-task7-invalid-pages-") as temp:
            pdf = Path(temp) / "paper.pdf"
            pdf.write_bytes(b"ocr-pdf")
            source = _SourceRow(mode="ocr", pdf_path=pdf)
            work = _Work(_SourceRepository(source), _JobRepository())
            native_factory_calls = []
            processor = SourceDocumentProcessor(
                lambda: work,
                native_factory=lambda: native_factory_calls.append(True),
                ocr_factory=lambda: build_ocr_source_processor(
                    _OcrRegistry(_PanicDouble("OCR provider must not run")),
                    page_reader=_InvalidPageReader(), checkpoint_repository=_CheckpointRepo(),
                ),
                clock=lambda: NOW,
            )
            with self.assertRaises(Exception) as raised:
                await processor.process(_Lease(), source.id)
        self.assertEqual("NATIVE_TEXT_EMPTY", getattr(raised.exception, "code", None))
        self.assertEqual([], native_factory_calls)

    async def test_ocr_cancelled_after_inflight_batch_writes_no_checkpoint(self) -> None:
        from backend.app.application.source_documents import (
            SourceDocumentProcessor, build_ocr_source_processor,
        )
        from backend.app.domain import JobLeaseLostError

        with tempfile.TemporaryDirectory(prefix="study-app-task7-") as temp:
            pdf = Path(temp) / "paper.pdf"
            pdf.write_bytes(b"ocr-pdf")
            source = _SourceRow(mode="ocr", pdf_path=pdf)
            sources = _SourceRepository(source)
            ocr = _OcrDouble({1: "# page one\n"})
            checkpoints = _CheckpointRepo()
            class Jobs(_JobRepository):
                def __init__(self):
                    super().__init__()
                    self.checks = 0

                def check_active(self, lease, *, now):
                    del now
                    self.checks += 1
                    return self.checks == 1
            jobs = Jobs()
            work = _Work(sources, jobs)
            processor = SourceDocumentProcessor(
                lambda: work,
                native_factory=lambda: (_ for _ in ()).throw(AssertionError("native")),
                ocr_factory=lambda: build_ocr_source_processor(
                    _OcrRegistry(ocr), page_reader=_PageReader(1), checkpoint_repository=checkpoints
                ),
                clock=lambda: NOW,
            )
            with self.assertRaises(JobLeaseLostError):
                await processor.process(_Lease(), source.id)
        self.assertEqual([], checkpoints.writes)

    async def test_native_mode_never_constructs_or_calls_ocr(self) -> None:
        from backend.app.application.source_documents import (
            SourceDocumentProcessor,
            build_native_source_processor,
        )

        with tempfile.TemporaryDirectory(prefix="study-app-task7-") as temp:
            pdf = Path(temp) / "paper.pdf"
            pdf.write_bytes(b"native-pdf")
            source = _SourceRow(mode="native", pdf_path=pdf)
            sources = _SourceRepository(source)
            jobs = _JobRepository()
            native = _NativeDouble()
            ocr_registry = _PanicDouble("OCR must not be constructed for native source")
            work = _Work(sources, jobs)

            processor = SourceDocumentProcessor(
                lambda: work,
                native_factory=lambda: build_native_source_processor(native),
                ocr_factory=lambda: (_ for _ in ()).throw(
                    AssertionError("OCR factory must not be selected for native source")
                ),
                clock=lambda: NOW,
            )
            result = await processor.process(_Lease(), source.id)

        self.assertEqual("ready", result.status.value if hasattr(result.status, "value") else result.status)
        self.assertEqual(1, native.calls)
        self.assertEqual(0, ocr_registry.calls)

    async def test_ocr_mode_only_calls_selected_provider_and_never_native(self) -> None:
        from backend.app.application.source_documents import (
            SourceDocumentProcessor,
            build_ocr_source_processor,
        )

        with tempfile.TemporaryDirectory(prefix="study-app-task7-") as temp:
            pdf = Path(temp) / "paper.pdf"
            pdf.write_bytes(b"ocr-pdf")
            source = _SourceRow(mode="ocr", pdf_path=pdf)
            sources = _SourceRepository(source)
            jobs = _JobRepository()
            ocr = _OcrDouble({1: "# page one\n", 2: "# page two\n"})
            registry = _OcrRegistry(ocr)
            reader = _PageReader(2)
            checkpoints = _CheckpointRepo()
            work = _Work(sources, jobs)
            native_factory_calls = []
            processor = SourceDocumentProcessor(
                lambda: work,
                native_factory=lambda: native_factory_calls.append(True),
                ocr_factory=lambda: build_ocr_source_processor(
                    registry, page_reader=reader, checkpoint_repository=checkpoints
                ),
                clock=lambda: NOW,
            )
            lease = _Lease()
            result = await processor.process(lease, source.id)

        self.assertEqual("ready", result.status)
        self.assertEqual(["fake"], registry.resolved)
        self.assertEqual(1, reader.calls)
        self.assertEqual([[1], [2]], [list(request.page_numbers) for request in ocr.calls])
        self.assertEqual([lease, lease], checkpoints.leases)
        self.assertEqual([], native_factory_calls)


if __name__ == "__main__":
    unittest.main()
