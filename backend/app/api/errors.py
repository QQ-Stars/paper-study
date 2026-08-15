from __future__ import annotations

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from backend.app.domain import DomainError


async def domain_error_response(_request: Request, error: DomainError) -> JSONResponse:
    status_by_code = {
        "PAPER_NOT_FOUND": 404,
        "PDF_NOT_FOUND": 404,
        "SOURCE_NOT_FOUND": 404,
        "JOB_NOT_FOUND": 404,
        "JOB_NOT_CANCELLABLE": 409,
        "JOB_NOT_RETRYABLE": 409,
        "SOURCE_NOT_READY": 409,
        "SOURCE_STALE": 409,
        "SOURCE_MODE_MISMATCH": 422,
        "INVALID_CURSOR": 422,
        "INVALID_REQUEST": 422,
    }
    return JSONResponse(
        status_code=getattr(error, "http_status", status_by_code.get(error.code, 400)),
        content={
            "error": {
                "code": error.code,
                "message": error.public_message,
                "details": dict(error.details),
            }
        },
    )


async def request_validation_error_response(
    _request: Request,
    error: RequestValidationError,
) -> JSONResponse:
    errors = error.errors()
    location = errors[0].get("loc", ()) if errors else ()
    safe_parts = [str(part)[:64] for part in location if isinstance(part, (str, int))]
    details = {"field": ".".join(safe_parts)} if safe_parts else {}
    return JSONResponse(
        status_code=422,
        content={
            "error": {
                "code": "INVALID_REQUEST",
                "message": "Request validation failed.",
                "details": details,
            }
        },
    )
