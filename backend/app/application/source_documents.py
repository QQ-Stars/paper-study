from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import datetime, timezone
import hashlib
import inspect
from pathlib import Path

from backend.app.application.ports.source_extractor import SourceExtractor
from backend.app.application.ports.unit_of_work import UnitOfWork
from backend.app.domain import (
    DomainError,
    MissingPaperError,
    MissingPdfError,
    OcrUnavailableError,
    PersistenceConflictError,
    SourceCacheIdentity,
    SourceDocument,
    SourceMode,
    SourcePdfChangedError,
    OcrRateLimitedError,
    OcrRequestInvalidError,
    OcrResponseInvalidError,
    OcrServerError,
    OcrTimeoutError,
    JobLeaseLostError,
    NativeTextEmptyError,
    PdfEncryptedError,
)
from backend.app.canonical_text import normalize_canonical_text
from backend.app.application.ports.ocr_provider import OcrPageResult, OcrRequest
from backend.app.domain.processing import JobSpecValidationError, hash_canonical_json


EMPTY_OPTIONS_SHA256 = "44136fa355b3678a1146ad16f7e8649e94fb4fc21fe77e8310c060f61caaff8a"


class DocumentSourcePipeline:
    def __init__(
        self,
        unit_of_work_factory: Callable[[], UnitOfWork],
        native_extractor: SourceExtractor,
        *,
        clock: Callable[[], datetime],
        id_factory: Callable[[], str],
    ) -> None:
        self._unit_of_work_factory = unit_of_work_factory
        self._native_extractor = native_extractor
        self._clock = clock
        self._id_factory = id_factory

    async def materialize_source(
        self,
        paper_id: str,
        source_mode: SourceMode | str,
        purpose: str,
    ) -> SourceDocument:
        if not isinstance(paper_id, str) or not paper_id:
            raise ValueError("paper_id must be a nonempty string")
        if not isinstance(purpose, str) or not purpose.strip():
            raise ValueError("purpose must be nonblank")
        mode = SourceMode(source_mode)
        if mode is SourceMode.OCR:
            raise OcrUnavailableError(source_mode=mode.value)

        async with self._unit_of_work_factory() as work:
            paper = await work.papers.get(paper_id)
        if paper is None:
            raise MissingPaperError(paper_id=paper_id)
        pdf_path = _resolve_pdf_path(paper.pdf_path)
        before_bytes = _read_pdf(pdf_path)
        before_sha256 = hashlib.sha256(before_bytes).hexdigest()
        identity = SourceCacheIdentity(
            paper_id=paper.id,
            pdf_sha256=before_sha256,
            mode=mode,
            provider=self._native_extractor.provider,
            model=self._native_extractor.model,
            options_hash=EMPTY_OPTIONS_SHA256,
            processing_version=self._native_extractor.processing_version,
        )

        async with self._unit_of_work_factory() as work:
            cached = await work.sources.find_by_cache_identity(identity)
        if cached is not None and cached.status.value == "ready":
            return cached

        try:
            extracted = self._native_extractor.extract(pdf_path)
        except DomainError as error:
            await self._persist_failure(identity, error)
            raise

        after_bytes = _read_pdf(pdf_path)
        if after_bytes != before_bytes:
            error = SourcePdfChangedError(paper_id=paper_id)
            await self._persist_failure(identity, error)
            raise error

        now = _utc_now(self._clock())
        document = SourceDocument(
            id=self._id_factory(),
            paper_id=paper.id,
            mode=mode,
            status="ready",
            provider=identity.provider,
            model=identity.model,
            pdf_sha256=identity.pdf_sha256,
            options_hash=identity.options_hash,
            processing_version=identity.processing_version,
            created_at=now,
            updated_at=now,
            markdown=extracted.markdown,
            content_sha256=extracted.content_sha256,
            page_count=extracted.page_count,
        )
        return await self._insert_or_winner(identity, document)

    async def _persist_failure(
        self,
        identity: SourceCacheIdentity,
        error: DomainError,
    ) -> None:
        now = _utc_now(self._clock())
        failed = SourceDocument(
            id=self._id_factory(),
            paper_id=identity.paper_id,
            mode=identity.mode,
            status="failed",
            provider=identity.provider,
            model=identity.model,
            pdf_sha256=identity.pdf_sha256,
            options_hash=identity.options_hash,
            processing_version=identity.processing_version,
            created_at=now,
            updated_at=now,
            error_code=error.code,
            error_message=error.public_message,
        )
        try:
            async with self._unit_of_work_factory() as work:
                await work.sources.add(failed)
                await work.commit()
        except PersistenceConflictError:
            return

    async def _insert_or_winner(
        self,
        identity: SourceCacheIdentity,
        document: SourceDocument,
    ) -> SourceDocument:
        try:
            async with self._unit_of_work_factory() as work:
                await work.sources.add(document)
                await work.commit()
            return document
        except PersistenceConflictError:
            async with self._unit_of_work_factory() as work:
                winner = await work.sources.find_by_cache_identity(identity)
            if winner is not None and winner.status.value == "ready":
                return winner
            raise


class NativeSourceProcessor:
    """Process a persisted native source without an OCR seam in its object graph."""

    def __init__(self, extractor: SourceExtractor, *, clock: Callable[[], datetime] | None = None) -> None:
        self._extractor = extractor
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    async def process(self, lease: object, source_id: str, *, work_factory: Callable[[], UnitOfWork]) -> SourceDocument:
        async with work_factory() as work:
            source = await work.sources.get(source_id)
            if source is None:
                raise MissingPdfError()
            path = await _source_pdf_path(work, source)
            await _ensure_processing_active(work, lease, now=self._clock())
        before = _read_pdf(path)
        before_sha = hashlib.sha256(before).hexdigest()
        expected_sha = getattr(source, "pdf_sha256", "")
        if expected_sha and expected_sha != before_sha:
            await _mark_source_stale(work_factory, lease, source.id, self._clock)
            raise SourcePdfChangedError(paper_id=source.paper_id)
        try:
            extracted = await asyncio.to_thread(self._extractor.extract, path)
            if inspect.isawaitable(extracted):
                extracted = await extracted
        except Exception as error:
            if "encrypt" in str(error).lower():
                raise PdfEncryptedError() from error
            raise
        after = _read_pdf(path)
        if after != before:
            await _mark_source_stale(work_factory, lease, source.id, self._clock)
            raise SourcePdfChangedError(paper_id=source.paper_id)
        markdown = normalize_canonical_text(extracted.markdown)
        if not markdown.strip():
            raise NativeTextEmptyError()
        content_sha = hashlib.sha256(markdown.encode("utf-8")).hexdigest()
        return await _publish_source_ready(
            work_factory,
            lease,
            source,
            expected_path=path,
            expected_pdf_sha=before_sha,
            markdown=markdown,
            content_sha=content_sha,
            page_count=extracted.page_count,
            clock=self._clock,
        )


def build_native_source_processor(
    native_extractor: SourceExtractor,
    *,
    clock: Callable[[], datetime] | None = None,
) -> NativeSourceProcessor:
    """Build the native-only processor; OCR dependencies are intentionally absent."""
    if native_extractor is None or not hasattr(native_extractor, "extract"):
        raise TypeError("native_extractor must provide extract")
    return NativeSourceProcessor(native_extractor, clock=clock)


class OcrSourceProcessor:
    """OCR-only page processor. It has no native extractor dependency."""

    def __init__(
        self,
        registry: object,
        *,
        page_reader: object,
        checkpoint_repository: object,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._registry = registry
        self._page_reader = page_reader
        self._checkpoints = checkpoint_repository
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    async def process(self, lease: object, source_id: str, *, work_factory: Callable[[], UnitOfWork]):
        async with work_factory() as work:
            source = await work.sources.get(source_id)
            if source is None:
                raise MissingPdfError()
            path = await _source_pdf_path(work, source)
            options = _ocr_options(source, lease)
            await _ensure_processing_active(work, lease, now=self._clock())
        pdf_bytes = _read_pdf(path)
        pdf_sha = hashlib.sha256(pdf_bytes).hexdigest()
        expected_sha = getattr(source, "pdf_sha256", "")
        if expected_sha and expected_sha != pdf_sha:
            await _mark_source_stale(work_factory, lease, source.id, self._clock)
            raise SourcePdfChangedError(paper_id=source.paper_id)
        page_count = _read_page_count(self._page_reader, pdf_bytes, path)
        if page_count < 1:
            raise NativeTextEmptyError()
        batch_size = _bounded_option(options, "pageBatchSize", 1, 16)
        max_concurrency = _bounded_option(options, "maxConcurrency", 1, 4)
        provider = self._registry.resolve(source.provider)
        succeeded = await _succeeded_pages(self._checkpoints, source.id)
        pending = [page for page in range(1, page_count + 1) if page not in succeeded]

        async def process_batch(pages: list[int]) -> None:
            await _check_source_active(
                work_factory, lease, source, path, pdf_sha, self._clock,
            )
            request = OcrRequest(
                source_id=source.id,
                paper_id=source.paper_id,
                pdf_bytes=pdf_bytes,
                pdf_sha256=pdf_sha,
                media_type="application/pdf",
                model=source.model,
                options=options,
                page_numbers=pages,
                total_pages=page_count,
            )
            try:
                result = await provider.extract_batch(request)
            except DomainError:
                raise
            except (TimeoutError, asyncio.TimeoutError):
                raise OcrTimeoutError() from None
            except Exception as error:
                status = getattr(error, "status_code", getattr(error, "status", None))
                if status == 429:
                    raise OcrRateLimitedError(
                        retry_after_seconds=getattr(error, "retry_after_seconds", None)
                    ) from None
                if isinstance(status, int) and not isinstance(status, bool) and status >= 500:
                    raise OcrServerError() from None
                raise
            page_results = _validated_ocr_pages(result, pages, page_count)
            for page in page_results:
                await _check_source_active(
                    work_factory, lease, source, path, pdf_sha, self._clock,
                )
                normalized = normalize_canonical_text(page.markdown)
                canonical_page = OcrPageResult(
                    page_number=page.page_number,
                    markdown=normalized,
                    content_sha256=hashlib.sha256(normalized.encode("utf-8")).hexdigest(),
                    provider_page_id=page.provider_page_id,
                )
                await _save_checkpoint(
                    self._checkpoints,
                    source.id,
                    canonical_page,
                    lease=lease,
                )

        batches = [
            pending[start : start + batch_size]
            for start in range(0, len(pending), batch_size)
        ]
        if max_concurrency == 1:
            for pages in batches:
                await process_batch(pages)
        else:
            semaphore = asyncio.Semaphore(max_concurrency)

            async def process_bounded(pages: list[int]) -> None:
                async with semaphore:
                    await process_batch(pages)

            tasks = [asyncio.create_task(process_bounded(pages)) for pages in batches]
            try:
                await asyncio.gather(*tasks)
            except BaseException:
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
                raise
        rows = await _read_succeeded_rows(self._checkpoints, source.id)
        if any(page not in rows for page in range(1, page_count + 1)):
            raise ValueError("OCR page checkpoints are incomplete")
        markdown = normalize_canonical_text("\n\n".join(rows[page] for page in range(1, page_count + 1)))
        if not markdown.strip():
            raise NativeTextEmptyError()
        content_sha = hashlib.sha256(markdown.encode("utf-8")).hexdigest()
        return await _publish_source_ready(
            work_factory,
            lease,
            source,
            expected_path=path,
            expected_pdf_sha=pdf_sha,
            markdown=markdown,
            content_sha=content_sha,
            page_count=page_count,
            clock=self._clock,
        )


def build_ocr_source_processor(
    ocr_registry: object,
    *,
    page_reader: object,
    checkpoint_repository: object,
    clock: Callable[[], datetime] | None = None,
) -> OcrSourceProcessor:
    """Build OCR path from registry, page reader and checkpoint repository only."""
    if ocr_registry is None or not hasattr(ocr_registry, "resolve"):
        raise TypeError("ocr_registry must provide resolve")
    if page_reader is None or checkpoint_repository is None:
        raise TypeError("page_reader and checkpoint_repository are required")
    return OcrSourceProcessor(
        ocr_registry,
        page_reader=page_reader,
        checkpoint_repository=checkpoint_repository,
        clock=clock,
    )


class SourceDocumentProcessor:
    """Strict mode dispatcher. It reads the persisted row before constructing a processor."""

    def __init__(
        self,
        unit_of_work_factory: Callable[[], UnitOfWork],
        *,
        native_factory: Callable[[], NativeSourceProcessor],
        ocr_factory: Callable[[], object],
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._work_factory = unit_of_work_factory
        self._native_factory = native_factory
        self._ocr_factory = ocr_factory
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    async def process(self, lease: object, source_id: str):
        async with self._work_factory() as work:
            source = await work.sources.get(source_id)
        if source is None:
            raise MissingPdfError()
        mode = getattr(getattr(source, "mode", None), "value", getattr(source, "mode", None))
        if mode == "native":
            processor = self._native_factory()
        elif mode == "ocr":
            processor = self._ocr_factory()
        else:
            raise ValueError("source mode is invalid")
        return await processor.process(lease, source_id, work_factory=self._work_factory)


async def _source_pdf_path(work: UnitOfWork, source: object) -> Path:
    path = getattr(source, "pdf_path", None)
    if path is None:
        paper = await work.papers.get(source.paper_id)
        path = getattr(paper, "pdf_path", None) if paper is not None else None
    return _resolve_pdf_path(path)


def _read_page_count(reader: object, pdf_bytes: bytes, path: Path) -> int:
    method = getattr(reader, "page_count", None)
    if method is None:
        method = getattr(reader, "count_pages", None)
    try:
        value = method(pdf_bytes) if method is not None else reader(pdf_bytes)
    except Exception as error:
        if "encrypt" in str(error).lower():
            raise PdfEncryptedError() from error
        raise
    if isinstance(value, int) and not isinstance(value, bool) and value < 1:
        raise NativeTextEmptyError()
    if not isinstance(value, int) or isinstance(value, bool):
        raise OcrRequestInvalidError(source_mode="ocr")
    return value


def _bounded_option(options: dict[str, object], name: str, default: int, maximum: int) -> int:
    value = options.get(name, default)
    if not isinstance(value, int) or isinstance(value, bool) or not 1 <= value <= maximum:
        raise ValueError(f"{name} is invalid")
    return value


def _ocr_options(source: object, lease: object) -> dict[str, object]:
    stored = getattr(lease, "spec", None)
    spec = getattr(stored, "value", None)
    if getattr(spec, "job_type", None) == "ocr":
        bindings = (
            ("paper_id", getattr(source, "paper_id", None)),
            ("source_document_id", getattr(source, "id", None)),
            ("provider", getattr(source, "provider", None)),
            ("model", getattr(source, "model", None)),
        )
        if any(getattr(spec, name, None) != expected for name, expected in bindings):
            raise JobSpecValidationError("OCR job spec does not bind the persisted source")
        options = dict(getattr(spec, "options", None) or {})
        canonical_limits = {
            "pageBatchSize": getattr(spec, "page_batch_size", None),
            "maxConcurrency": getattr(spec, "max_concurrency", None),
        }
        for name, value in canonical_limits.items():
            if name in options and options[name] != value:
                raise JobSpecValidationError(f"OCR job spec has conflicting {name}")
            options[name] = value
        expected_hash = getattr(source, "options_hash", None)
        if expected_hash and hash_canonical_json(options) != expected_hash:
            raise JobSpecValidationError("OCR job options do not match the persisted source")
        return options
    return dict(getattr(source, "options", None) or getattr(source, "ocr_options", None) or {})


def _validated_ocr_pages(result: object, expected_pages: list[int], total_pages: int) -> tuple[OcrPageResult, ...]:
    """Reject provider contract faults before any checkpoint becomes durable."""
    try:
        pages = tuple(getattr(result, "pages"))
    except (AttributeError, TypeError):
        raise OcrResponseInvalidError(source_mode="ocr") from None
    if len(pages) != len(expected_pages):
        raise OcrResponseInvalidError(source_mode="ocr")
    for page, expected in zip(pages, expected_pages):
        if (
            not isinstance(page, OcrPageResult)
            or page.page_number != expected
            or page.page_number < 1
            or page.page_number > total_pages
            or not isinstance(page.markdown, str)
            or not page.markdown.strip()
        ):
            raise OcrResponseInvalidError(source_mode="ocr")
    return pages


async def _succeeded_pages(repository: object, source_id: str) -> set[int]:
    method = getattr(repository, "list_succeeded", None)
    if method is None:
        return set()
    values = await method(source_id)
    return {int(value) for value in values}


async def _save_checkpoint(
    repository: object,
    source_id: str,
    page: OcrPageResult,
    *,
    lease: object,
) -> None:
    method = getattr(repository, "save_success", None)
    if method is None:
        return
    await method(source_id, page, lease=lease)


async def _read_succeeded_rows(repository: object, source_id: str) -> dict[int, str]:
    method = getattr(repository, "read_all_succeeded", None)
    if method is not None:
        values = await method(source_id)
        return {int(page): str(markdown) for page, markdown in values.items()}
    rows: dict[int, str] = {}
    for page in range(1, 10000):
        read = getattr(repository, "read", None)
        if read is None:
            break
        row = await read(source_id, page)
        if row is None:
            continue
        if getattr(row, "status", None) != "succeeded":
            continue
        markdown = getattr(row, "markdown", None)
        if isinstance(markdown, str) and markdown.strip():
            rows[page] = markdown
        if page > 1 and row is None:
            break
    return rows


async def _mark_stale(work: UnitOfWork, source: object, now: datetime) -> None:
    expected = getattr(source.status, "value", source.status)
    repository = work.sources
    if hasattr(repository, "publish_stale"):
        published = await repository.publish_stale(source.id, expected, "SOURCE_PDF_CHANGED", now)
    elif hasattr(repository, "mark_stale"):
        published = await repository.mark_stale(source.id, expected, now)
    elif hasattr(repository, "publish_failed"):
        # Compatibility seam for the P1 repository.  P2's concrete repository
        # implements publish_stale so drift is never represented as retryable.
        published = await repository.publish_failed(source.id, expected, "SOURCE_PDF_CHANGED", "", now)
    else:
        return
    if published is False:
        raise PersistenceConflictError(operation="mark_source_document_stale")


async def _mark_source_stale(
    work_factory: Callable[[], UnitOfWork],
    lease: object,
    source_id: str,
    clock: Callable[[], datetime],
) -> None:
    async with work_factory() as work:
        source = await work.sources.get(source_id)
        if source is None:
            raise MissingPdfError()
        now = _utc_now(clock())
        await _ensure_processing_active(work, lease, now=now)
        await _mark_stale(work, source, now)
        await work.commit()


async def _check_source_active(
    work_factory: Callable[[], UnitOfWork],
    lease: object,
    expected_source: object,
    expected_path: Path,
    expected_pdf_sha: str,
    clock: Callable[[], datetime],
) -> None:
    current_pdf_sha = hashlib.sha256(_read_pdf(expected_path)).hexdigest()
    async with work_factory() as work:
        source = await work.sources.get(expected_source.id)
        if source is None:
            raise MissingPdfError()
        current_path = await _source_pdf_path(work, source)
        persisted_sha = getattr(source, "pdf_sha256", "")
        now = _utc_now(clock())
        await _ensure_processing_active(work, lease, now=now)
        _ensure_source_configuration(source, expected_source)
        if (
            current_path != expected_path
            or (persisted_sha and persisted_sha != expected_pdf_sha)
            or current_pdf_sha != expected_pdf_sha
        ):
            await _mark_stale(work, source, now)
            await work.commit()
            raise SourcePdfChangedError(paper_id=source.paper_id)


async def _publish_source_ready(
    work_factory: Callable[[], UnitOfWork],
    lease: object,
    expected_source: object,
    *,
    expected_path: Path,
    expected_pdf_sha: str,
    markdown: str,
    content_sha: str,
    page_count: int,
    clock: Callable[[], datetime],
):
    current_pdf_sha = hashlib.sha256(_read_pdf(expected_path)).hexdigest()
    async with work_factory() as work:
        source = await work.sources.get(expected_source.id)
        if source is None:
            raise MissingPdfError()
        current_path = await _source_pdf_path(work, source)
        persisted_sha = getattr(source, "pdf_sha256", "")
        now = _utc_now(clock())
        await _ensure_processing_active(work, lease, now=now)
        _ensure_source_configuration(source, expected_source)
        if (
            current_path != expected_path
            or (persisted_sha and persisted_sha != expected_pdf_sha)
            or current_pdf_sha != expected_pdf_sha
        ):
            await _mark_stale(work, source, now)
            await work.commit()
            raise SourcePdfChangedError(paper_id=source.paper_id)
        await _publish_ready(work, source, markdown, content_sha, page_count, now)
        activate = getattr(work.sources, "stale_for_active_source", None)
        if activate is not None:
            await activate(source.id, now=now)
        result = await _reload_source(work, source)
        await _complete_job(work, lease, result, now)
        await work.commit()
        return result


def _ensure_source_configuration(current: object, expected: object) -> None:
    fields = (
        "paper_id",
        "mode",
        "provider",
        "model",
        "options_hash",
        "processing_version",
    )
    for field in fields:
        current_value = getattr(current, field, None)
        expected_value = getattr(expected, field, None)
        current_value = getattr(current_value, "value", current_value)
        expected_value = getattr(expected_value, "value", expected_value)
        if current_value != expected_value:
            raise PersistenceConflictError(operation="source_document_configuration_changed")


async def _publish_ready(work: UnitOfWork, source: object, markdown: str, content_sha: str, page_count: int, now: datetime) -> None:
    expected = getattr(source.status, "value", source.status)
    published = await work.sources.publish_ready(source.id, expected, markdown, content_sha, page_count, now)
    if published is False:
        raise PersistenceConflictError(operation="publish_source_document")


async def _reload_source(work: UnitOfWork, source: object):
    refreshed = await work.sources.get(source.id)
    return refreshed if refreshed is not None else source


async def _complete_job(work: UnitOfWork, lease: object, result: object, now: datetime) -> None:
    jobs = getattr(work, "jobs", None)
    if jobs is None or not hasattr(jobs, "complete"):
        return
    from backend.app.domain.processing import JobResult

    await jobs.complete(lease, JobResult({"sourceDocumentId": result.id}), now=now)


async def _ensure_processing_active(work: UnitOfWork, lease: object, *, now: datetime) -> None:
    """Run an optional in-memory/DB lease and cancellation guard.

    Test doubles expose one of these guards; production job repositories may
    use the same seam.  A false/failed guard must prevent checkpoint or
    publication from proceeding.
    """
    jobs = getattr(work, "jobs", None)
    if jobs is None:
        return
    for name in ("check_active", "check_lease", "ensure_active", "assert_active"):
        checker = getattr(jobs, name, None)
        if checker is None:
            continue
        value = checker(lease, now=_utc_now(now))
        if hasattr(value, "__await__"):
            value = await value
        if value is False:
            raise JobLeaseLostError(operation="processing_job_lease_lost")
        return
    cancelled = getattr(jobs, "is_cancelled", None)
    if cancelled is not None:
        value = cancelled(lease)
        if hasattr(value, "__await__"):
            value = await value
        if value:
            raise JobLeaseLostError(operation="processing_job_cancelled")


async def _assert_pdf_unchanged(work: UnitOfWork, source: object, path: Path, expected_sha: str, clock) -> None:
    actual = hashlib.sha256(_read_pdf(path)).hexdigest()
    if actual != expected_sha:
        await _mark_stale(work, source, _utc_now(clock()))
        raise SourcePdfChangedError(paper_id=source.paper_id)


def _resolve_pdf_path(value: Path | None) -> Path:
    if value is None:
        raise MissingPdfError()
    candidate = Path(value).expanduser()
    if not candidate.is_absolute():
        candidate = Path.cwd() / candidate
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as error:
        raise MissingPdfError() from error
    if not resolved.is_file():
        raise MissingPdfError()
    return resolved


def _read_pdf(path: Path) -> bytes:
    try:
        return path.read_bytes()
    except OSError as error:
        raise MissingPdfError() from error


def _utc_now(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("clock must return a timezone-aware datetime")
    return value.astimezone(timezone.utc)
