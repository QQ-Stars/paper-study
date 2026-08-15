from dataclasses import dataclass
from datetime import datetime, timezone
import base64
import hashlib
import hmac
import json
from pathlib import Path
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Query, Request, status
from pydantic import BaseModel, ConfigDict, Field, StrictStr

from backend.app.domain import (
    DomainError,
    MissingPaperError,
    MissingPdfError,
    SourceModeMismatchError,
    SourceNotFoundError,
    SourceNotReadyError,
    SourceDocument,
    SourceDocumentStatus,
    ProcessingJobType,
    StaleSourceError,
)
from backend.app.domain.processing import (
    MAX_JOB_PROGRESS_BYTES,
    JobProgress,
    JobSpecValidationError,
    NewProcessingJob,
    OcrJobSpecV1,
    SourceMaterializeJobSpecV1,
    build_source_job_key,
    build_source_key,
    encode_job_spec_v1,
    hash_canonical_json,
    hash_job_spec,
    ProcessingStatus,
)


class _StrictWireModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SourceOptionsRequest(_StrictWireModel):

    pageBatchSize: Annotated[int, Field(strict=True, ge=1, le=16)] = 1
    maxConcurrency: Annotated[int, Field(strict=True, ge=1, le=4)] = 1


class SourceEnqueueRequest(_StrictWireModel):

    sourceMode: Literal["native", "ocr"]
    ocrProvider: StrictStr | None = None
    ocrModel: StrictStr | None = None
    options: SourceOptionsRequest | None = None


class ExplainerEnqueueRequest(_StrictWireModel):
    sourceMode: Literal["native", "ocr"]
    sourceDocumentId: Annotated[StrictStr, Field(min_length=1)]
    profile: Literal["standard", "deep"] = "standard"


SourceStaleError = StaleSourceError


class InvalidCursorError(DomainError):
    code = "INVALID_CURSOR"
    public_message = "The pagination cursor is invalid."
    http_status = 422


class JobNotFoundError(DomainError):
    code = "JOB_NOT_FOUND"
    public_message = "The requested processing job does not exist."
    http_status = 404


class InvalidRequestError(DomainError):
    code = "INVALID_REQUEST"
    public_message = "Request validation failed."
    http_status = 422


@dataclass(frozen=True, slots=True)
class SourceEnqueueResult:
    source: SourceDocument
    job: Any
    deduplicated: bool


@dataclass(frozen=True, slots=True)
class ArtifactEnqueueApiResult:
    artifact: Any
    job: Any
    deduplicated: bool


class ProcessingApiService:
    """P2 request use cases; handlers are deliberately absent from this object."""

    def __init__(
        self,
        unit_of_work_factory: Any,
        native_provider: Any,
        ocr_gate: Any,
        artifact_generator: Any = None,
        *,
        clock: Any = None,
        cursor_secret: bytes,
        document_artifacts: Any = None,
    ) -> None:
        self._unit_of_work_factory = unit_of_work_factory
        self._native_provider = native_provider
        self._ocr_gate = ocr_gate
        self._artifact_generator = artifact_generator
        self._document_artifacts = document_artifacts
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        if not isinstance(cursor_secret, bytes) or len(cursor_secret) < 32:
            raise ValueError("processing cursor secret must contain at least 32 bytes")
        self._cursor_secret = cursor_secret

    async def enqueue_source(
        self,
        paper_id: str,
        *,
        source_mode: str,
        ocr_provider: str | None,
        ocr_model: str | None,
        options: dict[str, object] | None,
    ) -> SourceEnqueueResult:
        selection = self._ocr_gate.select(
            source_mode=source_mode,
            provider_id=ocr_provider,
            model=ocr_model,
            options=options,
        )
        async with self._unit_of_work_factory() as work:
            paper = await work.papers.get(paper_id)
        if paper is None:
            raise MissingPaperError(paper_id=paper_id)
        if paper.pdf_path is None:
            raise MissingPdfError(paper_id=paper_id)
        try:
            pdf_sha256 = hashlib.sha256(Path(paper.pdf_path).read_bytes()).hexdigest()
        except OSError:
            raise MissingPdfError(paper_id=paper_id) from None

        if selection is None:
            provider = self._native_provider.provider
            model = self._native_provider.model
            processing_version = self._native_provider.processing_version
            normalized_options: dict[str, object] = {}
            spec_type = SourceMaterializeJobSpecV1
        else:
            provider = selection.provider_id
            model = selection.model
            processing_version = selection.provider.processing_version
            normalized_options = dict(selection.options)
            spec_type = OcrJobSpecV1

        options_hash = hash_canonical_json(normalized_options)
        source_key = build_source_key(
            paper_id=paper_id,
            mode=source_mode,
            provider=provider,
            model=model,
            pdf_sha256=pdf_sha256,
            options_hash=options_hash,
            processing_version=processing_version,
        )
        source_id = f"src_{source_key[:24]}"
        if spec_type is SourceMaterializeJobSpecV1:
            spec = SourceMaterializeJobSpecV1(
                paper_id=paper_id,
                source_document_id=source_id,
                processing_version=processing_version,
            )
        else:
            spec = OcrJobSpecV1(
                paper_id=paper_id,
                source_document_id=source_id,
                provider=provider,
                model=model,
                options=normalized_options,
                page_batch_size=selection.page_batch_size,
                max_concurrency=selection.max_concurrency,
            )
        spec_json = encode_job_spec_v1(spec)
        spec_sha256 = hash_job_spec(spec_json)
        job_key = build_source_job_key(source_key, spec_sha256)
        now = self._clock().astimezone(timezone.utc)
        source = SourceDocument(
            id=source_id,
            paper_id=paper_id,
            mode=source_mode,
            status="queued",
            provider=provider,
            model=model,
            pdf_sha256=pdf_sha256,
            options_hash=options_hash,
            processing_version=processing_version,
            created_at=now,
            updated_at=now,
        )
        job = NewProcessingJob(
            id=f"job_{job_key[:24]}",
            spec=spec,
            idempotency_key=job_key,
            created_at=now,
            max_attempts=3,
        )
        async with self._unit_of_work_factory() as work:
            winner, enqueue = await work.sources.enqueue_with_job(
                source,
                job,
                spec_json=spec_json,
                spec_sha256=spec_sha256,
            )
            stored_job = await work.jobs.get(enqueue.job.id)
            if stored_job is None:
                raise JobNotFoundError()
            await work.commit()
        return SourceEnqueueResult(winner, stored_job, enqueue.deduplicated)

    async def enqueue_explainer(
        self,
        paper_id: str,
        *,
        source_mode: str,
        source_document_id: str,
        profile: str,
    ) -> Any:
        if self._document_artifacts is not None:
            result = await self._document_artifacts.enqueue(
                paper_id,
                source_document_id,
                source_mode,
                "explainer",
                profile=profile,
                now=self._clock(),
            )
            async with self._unit_of_work_factory() as work:
                stored_job = await work.jobs.get(result.job.id)
            if stored_job is None:
                raise JobNotFoundError()
            return ArtifactEnqueueApiResult(
                artifact=result.artifact,
                job=stored_job,
                deduplicated=result.deduplicated,
            )
        async with self._unit_of_work_factory() as work:
            source = await work.sources.get(source_document_id)
        if source is None or source.paper_id != paper_id:
            raise SourceNotFoundError(paper_id=paper_id)
        if source.mode.value != source_mode:
            raise SourceModeMismatchError(paper_id=paper_id, source_mode=source_mode)
        if source.status is SourceDocumentStatus.STALE:
            raise SourceStaleError(paper_id=paper_id)
        if source.status is not SourceDocumentStatus.READY:
            raise SourceNotReadyError(paper_id=paper_id)
        try:
            result = await self._artifact_generator.enqueue_explainer(
                paper_id,
                sourceMode=source_mode,
                sourceDocumentId=source_document_id,
                profile=profile,
            )
        except JobSpecValidationError as error:
            message = str(error).lower()
            if "stale" in message:
                raise SourceStaleError(paper_id=paper_id) from None
            if "unavailable" in message:
                raise MissingPdfError(paper_id=paper_id) from None
            raise SourceNotReadyError(paper_id=paper_id) from None
        async with self._unit_of_work_factory() as work:
            stored_job = await work.jobs.get(result.job.id)
        if stored_job is None:
            raise JobNotFoundError()
        return ArtifactEnqueueApiResult(
            artifact=result.artifact,
            job=stored_job,
            deduplicated=result.deduplicated,
        )

    async def list_sources(
        self,
        paper_id: str,
        *,
        limit: int,
        cursor: str | None,
    ) -> tuple[tuple[SourceDocument, ...], str | None]:
        position = self._decode_cursor("sources", cursor) if cursor is not None else None
        async with self._unit_of_work_factory() as work:
            if await work.papers.get(paper_id) is None:
                raise MissingPaperError(paper_id=paper_id)
            items, next_position = await work.sources.list_page(
                paper_id=paper_id,
                limit=limit,
                cursor=position,
            )
        next_cursor = (
            self._encode_cursor("sources", next_position)
            if next_position is not None
            else None
        )
        return items, next_cursor

    async def list_artifacts(
        self,
        paper_id: str,
        *,
        kind: str | None,
        limit: int,
        cursor: str | None,
    ) -> tuple[tuple[Any, ...], str | None]:
        position = self._decode_cursor("artifacts", cursor) if cursor is not None else None
        async with self._unit_of_work_factory() as work:
            if await work.papers.get(paper_id) is None:
                raise MissingPaperError(paper_id=paper_id)
            items, next_position = await work.artifacts.list_page(
                paper_id=paper_id,
                kind=kind,
                limit=limit,
                cursor=position,
            )
        next_cursor = (
            self._encode_cursor("artifacts", next_position)
            if next_position is not None
            else None
        )
        return items, next_cursor

    async def get_job(self, job_id: str) -> Any:
        async with self._unit_of_work_factory() as work:
            row = await work.jobs.get_api_row(job_id)
        if row is None:
            raise JobNotFoundError()
        return row

    async def list_job_events(
        self,
        job_id: str,
        *,
        after_sequence: int,
        limit: int,
    ) -> tuple[Any, ...]:
        async with self._unit_of_work_factory() as work:
            job = await work.jobs.get_api_row(job_id)
            if job is None:
                raise JobNotFoundError()
            return await work.jobs.list_events_after(
                job_id,
                after_sequence=after_sequence,
                limit=limit,
            )

    async def list_jobs(
        self,
        *,
        paper_id: str | None,
        status: str | None,
        job_type: str | None,
        limit: int,
        cursor: str | None,
    ) -> tuple[tuple[Any, ...], str | None]:
        position = self._decode_cursor("jobs", cursor) if cursor is not None else None
        async with self._unit_of_work_factory() as work:
            items, next_position = await work.jobs.list_page(
                paper_id=paper_id,
                status=status,
                job_type=job_type,
                limit=limit,
                cursor=position,
            )
        return items, (
            self._encode_cursor("jobs", next_position)
            if next_position is not None
            else None
        )

    async def cancel_job(self, job_id: str) -> Any:
        now = self._clock().astimezone(timezone.utc)
        async with self._unit_of_work_factory() as work:
            if await work.jobs.get_api_row(job_id) is None:
                raise JobNotFoundError()
            job = await work.jobs.cancel(job_id, now=now)
            await work.commit()
        return job

    async def retry_job(self, job_id: str) -> Any:
        now = self._clock().astimezone(timezone.utc)
        async with self._unit_of_work_factory() as work:
            if await work.jobs.get_api_row(job_id) is None:
                raise JobNotFoundError()
            result = await work.jobs.retry(job_id, now=now)
            await work.commit()
        return result

    def _encode_cursor(self, kind: str, position: tuple[str, str]) -> str:
        payload = json.dumps(
            ["v1", kind, position[0], position[1]],
            ensure_ascii=True,
            separators=(",", ":"),
        ).encode("utf-8")
        signature = hmac.digest(self._cursor_secret, payload, "sha256")
        return base64.urlsafe_b64encode(payload).rstrip(b"=").decode() + "." + base64.urlsafe_b64encode(signature).rstrip(b"=").decode()

    def _decode_cursor(self, kind: str, cursor: str) -> tuple[str, str]:
        try:
            payload_part, signature_part = cursor.split(".")
            payload = base64.urlsafe_b64decode(payload_part + "=" * (-len(payload_part) % 4))
            signature = base64.urlsafe_b64decode(signature_part + "=" * (-len(signature_part) % 4))
            expected = hmac.digest(self._cursor_secret, payload, "sha256")
            decoded = json.loads(payload)
            if (
                not hmac.compare_digest(signature, expected)
                or not isinstance(decoded, list)
                or len(decoded) != 4
                or decoded[0] != "v1"
                or decoded[1] != kind
                or not all(isinstance(value, str) for value in decoded)
            ):
                raise ValueError
            return decoded[2], decoded[3]
        except (ValueError, TypeError, json.JSONDecodeError, UnicodeDecodeError):
            raise InvalidCursorError() from None


def _source_summary(source: SourceDocument) -> dict[str, object]:
    return {
        "id": source.id,
        "paperId": source.paper_id,
        "mode": source.mode.value,
        "status": source.status.value,
    }


def _new_job_summary(job: Any) -> dict[str, object]:
    if isinstance(job, NewProcessingJob):
        paper_id = job.spec.paper_id
        job_type = job.spec.job_type
        source_mode = job.spec.source_mode
        status_value = job.status.value
    else:
        paper_id = job.paper_id
        job_type = job.job_type.value
        source_mode = job.source_mode.value if job.source_mode is not None else None
        status_value = job.status.value
    return {
        "id": job.id,
        "paperId": paper_id,
        "jobType": job_type,
        "sourceMode": source_mode,
        "status": status_value,
    }


def _artifact_summary(artifact: Any) -> dict[str, object]:
    return {
        "id": artifact.id,
        "paperId": artifact.paper_id,
        "kind": artifact.kind.value,
        "sourceDocumentId": artifact.source_document_id,
        "status": artifact.status.value,
    }


def _timestamp_api(value: str | datetime | None) -> str | None:
    if value is None:
        return None
    parsed = value if isinstance(value, datetime) else datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _public_progress(raw_json: object) -> dict[str, object]:
    if not isinstance(raw_json, str):
        return {}
    try:
        if len(raw_json.encode("utf-8")) > MAX_JOB_PROGRESS_BYTES:
            return {}
        decoded = json.loads(raw_json)
        if not isinstance(decoded, dict):
            return {}
        return dict(JobProgress(decoded).value)
    except (TypeError, ValueError, UnicodeError, RecursionError):
        return {}


def _job_detail(row: Any) -> dict[str, object]:
    error = (
        {"code": row.error_code, "message": "Processing failed."}
        if row.error_code is not None
        else None
    )
    return {
        "id": row.id,
        "paperId": row.paper_id,
        "jobType": row.job_type.value,
        "sourceMode": row.source_mode.value if row.source_mode is not None else None,
        "status": row.status.value,
        "progress": _public_progress(row.progress_json),
        "attempt": row.attempt,
        "maxAttempts": row.max_attempts,
        "error": error,
        "createdAt": _timestamp_api(row.created_at),
        "startedAt": _timestamp_api(row.started_at),
        "finishedAt": _timestamp_api(row.finished_at),
        "cancelledAt": _timestamp_api(row.cancelled_at),
    }


def _event_summary(row: Any) -> dict[str, object]:
    return {
        "sequence": row.sequence,
        "type": row.event_type,
        "progress": _public_progress(row.progress_json),
        "error": ({"code": row.error_code} if row.error_code is not None else None),
        "createdAt": _timestamp_api(row.created_at),
    }


def _reject_unknown_query(request: Request, allowed: set[str]) -> None:
    unknown = sorted(set(request.query_params) - allowed)
    if unknown:
        raise InvalidRequestError()


async def _require_empty_body(request: Request) -> None:
    if await request.body():
        raise InvalidRequestError()


def create_document_processing_router() -> APIRouter:
    router = APIRouter()

    @router.post(
        "/papers/{paper_id}/sources",
        status_code=status.HTTP_202_ACCEPTED,
    )
    async def enqueue_source(
        paper_id: str,
        body: SourceEnqueueRequest,
        request: Request,
    ) -> dict[str, object]:
        _reject_unknown_query(request, set())
        options = body.options.model_dump() if body.options is not None else None
        result = await request.app.state.container.processing_api.enqueue_source(
            paper_id,
            source_mode=body.sourceMode,
            ocr_provider=body.ocrProvider,
            ocr_model=body.ocrModel,
            options=options,
        )
        return {
            "source": _source_summary(result.source),
            "job": _new_job_summary(result.job),
            "deduplicated": result.deduplicated,
        }

    @router.post(
        "/papers/{paper_id}/artifacts/explainer",
        status_code=status.HTTP_202_ACCEPTED,
    )
    async def enqueue_explainer(
        paper_id: str,
        body: ExplainerEnqueueRequest,
        request: Request,
    ) -> dict[str, object]:
        _reject_unknown_query(request, set())
        result = await request.app.state.container.processing_api.enqueue_explainer(
            paper_id,
            source_mode=body.sourceMode,
            source_document_id=body.sourceDocumentId,
            profile=body.profile,
        )
        return {
            "artifact": _artifact_summary(result.artifact),
            "job": _new_job_summary(result.job),
            "deduplicated": result.deduplicated,
        }

    @router.get("/papers/{paper_id}/sources")
    async def list_sources(
        paper_id: str,
        request: Request,
        limit: int = Query(50, ge=1, le=100),
        cursor: str | None = Query(None),
    ) -> dict[str, object]:
        _reject_unknown_query(request, {"limit", "cursor"})
        items, next_cursor = await request.app.state.container.processing_api.list_sources(
            paper_id,
            limit=limit,
            cursor=cursor,
        )
        return {
            "items": [_source_summary(item) for item in items],
            "nextCursor": next_cursor,
        }

    @router.get("/papers/{paper_id}/artifacts")
    async def list_artifacts(
        paper_id: str,
        request: Request,
        kind: Literal["explainer"] | None = Query(None),
        limit: int = Query(50, ge=1, le=100),
        cursor: str | None = Query(None),
    ) -> dict[str, object]:
        _reject_unknown_query(request, {"kind", "limit", "cursor"})
        items, next_cursor = await request.app.state.container.processing_api.list_artifacts(
            paper_id,
            kind=kind,
            limit=limit,
            cursor=cursor,
        )
        return {
            "items": [_artifact_summary(item) for item in items],
            "nextCursor": next_cursor,
        }

    @router.get("/jobs/{job_id}")
    async def get_job(job_id: str, request: Request) -> dict[str, object]:
        _reject_unknown_query(request, set())
        row = await request.app.state.container.processing_api.get_job(job_id)
        return _job_detail(row)

    @router.get("/jobs")
    async def list_jobs(
        request: Request,
        paper_id: str | None = Query(None, alias="paperId"),
        status_filter: ProcessingStatus | None = Query(None, alias="status"),
        job_type: ProcessingJobType | None = Query(None, alias="jobType"),
        limit: int = Query(50, ge=1, le=100),
        cursor: str | None = Query(None),
    ) -> dict[str, object]:
        _reject_unknown_query(request, {"paperId", "status", "jobType", "limit", "cursor"})
        items, next_cursor = await request.app.state.container.processing_api.list_jobs(
            paper_id=paper_id,
            status=status_filter.value if status_filter is not None else None,
            job_type=job_type.value if job_type is not None else None,
            limit=limit,
            cursor=cursor,
        )
        return {
            "items": [_job_detail(item) for item in items],
            "nextCursor": next_cursor,
        }

    @router.get("/jobs/{job_id}/events")
    async def get_job_events(
        job_id: str,
        request: Request,
        after_sequence: int = Query(0, alias="afterSequence", ge=0),
        limit: int = Query(100, ge=1, le=100),
    ) -> dict[str, object]:
        _reject_unknown_query(request, {"afterSequence", "limit"})
        rows = await request.app.state.container.processing_api.list_job_events(
            job_id,
            after_sequence=after_sequence,
            limit=limit,
        )
        return {
            "items": [_event_summary(row) for row in rows],
            "nextAfterSequence": rows[-1].sequence if rows else after_sequence,
        }

    @router.post("/jobs/{job_id}/cancel")
    async def cancel_job(job_id: str, request: Request) -> dict[str, object]:
        _reject_unknown_query(request, set())
        await _require_empty_body(request)
        job = await request.app.state.container.processing_api.cancel_job(job_id)
        return _job_detail(job)

    @router.post(
        "/jobs/{job_id}/retry",
        status_code=status.HTTP_202_ACCEPTED,
    )
    async def retry_job(job_id: str, request: Request) -> dict[str, object]:
        _reject_unknown_query(request, set())
        await _require_empty_body(request)
        result = await request.app.state.container.processing_api.retry_job(job_id)
        return {
            "job": _new_job_summary(result.job),
            "retriedFromJobId": job_id,
            "deduplicated": result.deduplicated,
        }

    return router
