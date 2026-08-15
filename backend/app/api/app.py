from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.exception_handlers import http_exception_handler
from sqlalchemy import text
from starlette.exceptions import HTTPException as StarletteHttpException

from backend.app.api.errors import domain_error_response, request_validation_error_response
from backend.app.api.dependencies import ApiDependencies
from backend.app.api.middleware.local_access import LocalAccessMiddleware, LocalAccessPolicy
from backend.app.api.router import create_router
from backend.app.api.static import create_frontend_assets_handler
from backend.app.domain import DomainError, SchemaRevisionMismatchError
from backend.app.runtime import ApiSettings


def create_app(
    settings: Any,
    dependencies: Any,
    *,
    required_schema_revision: str,
) -> FastAPI:
    if not isinstance(settings, ApiSettings) or not isinstance(
        dependencies, ApiDependencies
    ):
        settings, dependencies = (
            ApiSettings.for_tests(),
            ApiDependencies(settings, dependencies),
        )
    container = dependencies.application
    session_factory = dependencies.session_factory
    if dependencies.schema_revision != required_schema_revision:
        raise ValueError("container schema revision does not match required_schema_revision")

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        try:
            yield
        finally:
            await dependencies.dispose()

    app = FastAPI(lifespan=lifespan)
    app.state.container = container
    app.state.settings = settings
    app.state.dependencies = dependencies
    app.state.session_factory = session_factory
    app.state.required_schema_revision = required_schema_revision
    app.add_exception_handler(DomainError, domain_error_response)
    app.add_exception_handler(RequestValidationError, request_validation_error_response)
    frontend_assets = create_frontend_assets_handler()

    @app.exception_handler(StarletteHttpException)
    async def frontend_or_http_error(request: Any, error: StarletteHttpException):
        path = request.url.path
        reserved = (
            path == "/api"
            or path.startswith("/api/")
            or path == "/pdfbytes"
            or path == "/papers"
            or path.startswith("/papers/")
        )
        if error.status_code in {404, 405} and not reserved:
            return await frontend_assets(request, path[1:] if path.startswith("/") else path)
        return await http_exception_handler(request, error)
    app.add_middleware(
        LocalAccessMiddleware,
        policy=LocalAccessPolicy(
            settings.bind_host,
            settings.bind_port,
            additional_hosts=settings.additional_hosts,
            loopback_port_forwarding=settings.loopback_port_forwarding,
            loopback_forwarder_hosts=settings.loopback_forwarder_hosts,
        ),
    )

    @app.get("/health/live")
    async def liveness() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/health/ready")
    async def readiness() -> dict[str, str]:
        async with session_factory() as session:
            foreign_keys = (
                await session.execute(text("PRAGMA foreign_keys"))
            ).scalar_one()
            revisions = (
                await session.execute(
                    text("SELECT version_num FROM alembic_version ORDER BY version_num")
                )
            ).scalars().all()
        if foreign_keys != 1 or revisions != [required_schema_revision]:
            actual_revision = ",".join(str(value) for value in revisions) or "missing"
            raise SchemaRevisionMismatchError(
                expected_revision=required_schema_revision,
                actual_revision=actual_revision,
            )
        return {
            "status": "ready",
            "schemaRevision": required_schema_revision,
        }

    app.include_router(create_router(required_schema_revision))
    return app
