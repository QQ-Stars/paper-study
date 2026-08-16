from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping
import copy
from datetime import datetime, timezone
import hashlib
import os
from pathlib import Path
import sqlite3
import tempfile
from types import SimpleNamespace
from typing import cast
import urllib.request
import uuid

import uvicorn

from backend.app.api.app import create_app
from backend.app.api.compat.build_identity import verify_native_runtime_spec
from backend.app.api.compat.database_identity import (
    load_database_evidence_identity_manifest,
    verify_descendant_database_evidence_identity,
)
from backend.app.api.dependencies import ApiDependencies
from backend.app.api.mcp import ApplicationMcpAdapter
from backend.app.api.routes.document_processing import ProcessingApiService
from backend.app.application.compatibility_rehearsal import (
    RECOVERY_SMOKE_EVENTS,
    CompatibilitySmokeObservation,
    CompatibilitySmokeRequest,
)
from backend.app.application.library_queries import LibraryQueries
from backend.app.application.production_candidate import (
    CandidateSmokeObservation,
    CandidateSmokeRequest,
    CandidateWriteMutation,
)
from backend.app.application.production_rollback import ROLLBACK_TAIL_EVENTS
from backend.app.config import DatabaseSettings
from backend.app.domain import SourceDocument
from backend.app.domain.processing import (
    NewProcessingJob,
    SourceMaterializeJobSpecV1,
    build_source_job_key,
    build_source_key,
    encode_job_spec_v1,
    hash_job_spec,
)
from backend.app.infrastructure.database import create_async_session_factory
from backend.app.providers.native_runtime import (
    NativeRuntimeConfiguration,
    NativeWindowsRuntimeOperations,
    load_native_runtime_configuration,
)
from backend.app.providers.runtime_lease import ApiRuntimePresence, RoleScopedRuntimeLease
from backend.app.repositories.unit_of_work import SqlAlchemyUnitOfWork
from backend.app.runtime import ApiSettings, CandidateRuntimeGuard


_SCHEMA_REVISION = "20260807_03"
_MCP_TOOL_NAMES = (
    "get_explainer",
    "get_paper",
    "get_translation",
    "library_overview",
    "list_categories",
    "list_due_reviews",
    "related_papers",
    "search_papers",
    "semantic_search",
)


class NativeSmokeError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(f"{code}: {message}")


class _FixedLocalSmokeProvider:
    """One deterministic provider call with no credential, file, or network access."""

    provider = "p6-fixed-local"
    model = "deterministic-v1"
    processing_version = "p6-native-smoke-v1"

    def __init__(self) -> None:
        self.calls = 0

    def describe(self) -> tuple[str, str, str]:
        self.calls += 1
        return self.provider, self.model, self.processing_version


class _NoPdfFiles:
    def has_pdf(self, _paper: object) -> bool:
        return False


class _SmokeApplication:
    schema_revision = _SCHEMA_REVISION

    def __init__(self, database_path: Path) -> None:
        self.session_factory = create_async_session_factory(DatabaseSettings(database_path))
        work_factory = lambda: SqlAlchemyUnitOfWork(self.session_factory)
        self.processing_api = ProcessingApiService(
            work_factory,
            None,
            None,
            cursor_secret=b"p6-native-smoke-cursor-secret-0001",
        )
        self.legacy = SimpleNamespace(
            library_queries=LibraryQueries(work_factory, pdf_files=_NoPdfFiles())
        )
        self._disposed = False

    async def dispose(self) -> None:
        if self._disposed:
            return
        await self.session_factory.kw["bind"].dispose()
        self._disposed = True


class NativeCandidateWriteSmokeRunner:
    def __init__(
        self,
        *,
        configuration: object,
        build_identity: object,
        state_directory: str | os.PathLike[str] | None = None,
    ) -> None:
        self._configuration = configuration
        self._build_identity = build_identity
        self._live_database_path = _configuration_live_database(configuration)
        self._state_directory = Path(
            state_directory or self._live_database_path.parent / ".native-smoke-state"
        ).expanduser().resolve(strict=False)

    async def run(self, request: CandidateSmokeRequest) -> CandidateSmokeObservation:
        database, identity_path = self._validate_request(request)
        lease_root = self._state_directory / f"candidate-{uuid.uuid4().hex}" / "leases"
        lease_root.mkdir(parents=True, exist_ok=False)
        leases = _acquire_candidate_roles(
            lease_root,
            identity_path,
            runtime_namespace=request.runtime_namespace,
        )
        application = _SmokeApplication(database)
        provider = _FixedLocalSmokeProvider()
        try:
            identifiers = await _write_explained_candidate_rows(
                application.session_factory,
                database,
                provider,
            )
            paper_id, source_id, job_id, artifact_id, request_id = identifiers
            paths = (
                "/health/ready",
                "/api/papers",
                "/api/v2/jobs",
                f"/api/v2/papers/{paper_id}/sources",
                f"/api/v2/jobs/{job_id}/events",
                "/workspace/",
                "/legacy/",
            )
            port = await _exercise_python_http(
                application,
                paths,
                on_started=lambda: _verify_mcp_contract(database),
            )
        finally:
            await application.dispose()
            _release_leases(leases)

        return CandidateSmokeObservation(
            database_path=database,
            request_id=request_id,
            paper_id=paper_id,
            job_id=job_id,
            source_document_id=source_id,
            artifact_id=artifact_id,
            roles=("api", "worker", "scheduler"),
            loopback_bindings=(("api", "127.0.0.1", port),),
            endpoints=(*paths, "mcp:tools/list"),
            mutations=(
                CandidateWriteMutation("document_sources", "insert", (source_id,)),
                CandidateWriteMutation("processing_jobs", "insert", (job_id,)),
                CandidateWriteMutation(
                    "processing_job_events",
                    "insert",
                    (f"{job_id}:1",),
                ),
            ),
            fake_provider_calls=provider.calls,
            real_provider_calls=0,
            real_network_calls=0,
            live_path_access_count=0,
            owner_marker_write_count=0,
            user_pdf_access_count=0,
            stopped=True,
        )

    def _validate_request(self, request: CandidateSmokeRequest) -> tuple[Path, Path]:
        database = Path(request.database_path).expanduser().resolve(strict=True)
        expected_build_id = str(getattr(self._build_identity, "build_id", ""))
        if database == self._live_database_path or request.build_id != expected_build_id:
            raise NativeSmokeError(
                "NATIVE_SMOKE_IDENTITY_INVALID",
                "Candidate smoke requires an isolated descendant database and exact build.",
            )
        identity_path = Path(request.database_identity_manifest_path).expanduser().resolve(
            strict=True
        )
        try:
            identity = load_database_evidence_identity_manifest(identity_path)
            verify_descendant_database_evidence_identity(database=database, identity=identity)
        except Exception as error:
            raise NativeSmokeError(
                "NATIVE_SMOKE_IDENTITY_INVALID",
                "Candidate smoke requires an isolated descendant database and exact build.",
            ) from error
        if identity.subject_kind != "write_smoke" or identity.manifest_path != identity_path:
            raise NativeSmokeError(
                "NATIVE_SMOKE_IDENTITY_INVALID",
                "Candidate smoke requires an isolated descendant database and exact build.",
            )
        return database, identity_path


class NativeCompatibilitySmokeRunner:
    def __init__(
        self,
        *,
        configuration: object,
        build_identity: object,
        state_directory: str | os.PathLike[str],
        operations_factory: Callable[..., object] = NativeWindowsRuntimeOperations,
        rollback_map: Mapping[str, object] | None = None,
    ) -> None:
        self._configuration = configuration
        self._build_identity = build_identity
        self._state_directory = Path(state_directory).expanduser().resolve(strict=False)
        self._state_directory.mkdir(parents=True, exist_ok=True)
        self._operations_factory = operations_factory
        self._rollback_map = dict(
            rollback_map or _rollback_map_from_configuration(configuration)
        )
        self._live_database_path = _configuration_live_database(configuration)

    def run(self, request: CompatibilitySmokeRequest) -> CompatibilitySmokeObservation:
        database = self._validate_request(request)
        if request.operation == "rollback-smoke" and request.profile == "frozen-node":
            events = self._run_rollback(database)
        elif request.operation == "recovery-smoke" and request.profile == "production":
            events = asyncio.run(self._run_recovery(database, request))
        else:
            raise NativeSmokeError(
                "NATIVE_SMOKE_OPERATION_INVALID",
                "Native compatibility smoke received an unsupported operation or profile.",
            )
        return CompatibilitySmokeObservation(
            database_path=database,
            events=events,
            stopped=True,
            live_path_access_count=0,
            live_owner_write_count=0,
            real_network_call_count=0,
        )

    def _validate_request(self, request: CompatibilitySmokeRequest) -> Path:
        database = Path(request.database_path).expanduser().resolve(strict=True)
        build_path = Path(request.build_identity_manifest_path).expanduser().resolve(strict=True)
        expected_build_path = Path(
            getattr(self._build_identity, "manifest_path")
        ).expanduser().resolve(strict=True)
        identity_path = Path(request.database_identity_manifest_path).expanduser().resolve(
            strict=True
        )
        if (
            database == self._live_database_path
            or build_path != expected_build_path
            or request.build_id != str(getattr(self._build_identity, "build_id", ""))
        ):
            raise NativeSmokeError(
                "NATIVE_SMOKE_IDENTITY_INVALID",
                "Compatibility smoke requires the exact build and isolated descendant.",
            )
        try:
            identity = load_database_evidence_identity_manifest(identity_path)
            verify_descendant_database_evidence_identity(database=database, identity=identity)
        except Exception as error:
            raise NativeSmokeError(
                "NATIVE_SMOKE_IDENTITY_INVALID",
                "Compatibility smoke requires the exact build and isolated descendant.",
            ) from error
        if (
            identity.manifest_path != identity_path
            or identity.database_lineage_id != request.database_lineage_id
            or identity.subject_database_id != request.subject_database_id
        ):
            raise NativeSmokeError(
                "NATIVE_SMOKE_IDENTITY_INVALID",
                "Compatibility smoke requires the exact build and isolated descendant.",
            )
        return database

    def _run_rollback(self, database: Path) -> tuple[str, ...]:
        rollback_map = _isolated_rollback_map(
            self._rollback_map,
            database=database,
            port=_unused_loopback_port(),
        )
        with tempfile.TemporaryDirectory(
            prefix="rollback-",
            dir=self._state_directory,
        ) as raw_state:
            operations = self._operations_factory(
                native_runtime_spec=getattr(
                    self._configuration,
                    "spec_path",
                    Path("native-runtime-v1.json"),
                ),
                build_identity_manifest=getattr(self._build_identity, "manifest_path"),
                state_directory=Path(raw_state),
            )
            handle = operations.start_frozen_node(rollback_map)
            try:
                smoke = operations.smoke_legacy(handle)
                if not isinstance(smoke, Mapping) or smoke.get("ok") is not True:
                    raise NativeSmokeError(
                        "NATIVE_ROLLBACK_SMOKE_FAILED",
                        "The isolated frozen Node smoke did not pass.",
                    )
            finally:
                operations.stop_frozen_node(handle)
        return ROLLBACK_TAIL_EVENTS

    async def _run_recovery(
        self,
        database: Path,
        request: CompatibilitySmokeRequest,
    ) -> tuple[str, ...]:
        lease_root = self._state_directory / f"recovery-{uuid.uuid4().hex}" / "leases"
        lease_root.mkdir(parents=True, exist_ok=False)
        leases: tuple[object, ...] = ()
        application = _SmokeApplication(database)

        def runtime_started() -> None:
            nonlocal leases
            leases = _acquire_candidate_roles(
                lease_root,
                request.database_identity_manifest_path,
                runtime_namespace=f"p6-recovery-{uuid.uuid4().hex}",
            )
            _verify_mcp_contract(database)

        try:
            await _exercise_python_http(
                application,
                (
                    "/health/ready",
                    "/api/papers",
                    "/api/v2/jobs",
                    "/workspace/",
                    "/legacy/",
                ),
                on_started=runtime_started,
            )
        finally:
            await application.dispose()
            _release_leases(leases)
        return RECOVERY_SMOKE_EVENTS


async def _write_explained_candidate_rows(
    session_factory: object,
    database: Path,
    provider: _FixedLocalSmokeProvider,
) -> tuple[str, str, str, str, str]:
    with sqlite3.connect(database) as connection:
        row = connection.execute("SELECT id FROM papers ORDER BY id LIMIT 1").fetchone()
    if row is None:
        raise NativeSmokeError(
            "NATIVE_SMOKE_FIXTURE_INVALID",
            "The isolated database must contain at least one Paper.",
        )
    paper_id = str(row[0])
    nonce = uuid.uuid4().hex
    source_id = f"p6-smoke-source-{nonce}"
    job_id = f"p6-smoke-job-{nonce}"
    artifact_id = f"p6-smoke-artifact-{nonce}"
    request_id = f"p6-smoke-request-{nonce}"
    provider_name, model, processing_version = provider.describe()
    now = datetime.now(timezone.utc)
    source = SourceDocument(
        id=source_id,
        paper_id=paper_id,
        mode="native",
        status="queued",
        provider=provider_name,
        model=model,
        pdf_sha256=hashlib.sha256(b"p6-fixed-pdf-identity").hexdigest(),
        options_hash=hashlib.sha256(b"{}").hexdigest(),
        processing_version=processing_version,
        created_at=now,
        updated_at=now,
    )
    spec = SourceMaterializeJobSpecV1(
        paper_id=paper_id,
        source_document_id=source_id,
        processing_version=processing_version,
    )
    spec_json = encode_job_spec_v1(spec)
    source_key = build_source_key(
        paper_id=paper_id,
        mode="native",
        provider=provider_name,
        model=model,
        pdf_sha256=source.pdf_sha256,
        options_hash=source.options_hash,
        processing_version=processing_version,
    )
    job = NewProcessingJob(
        id=job_id,
        spec=spec,
        idempotency_key=build_source_job_key(source_key, hash_job_spec(spec_json)),
        created_at=now,
    )
    async with SqlAlchemyUnitOfWork(session_factory) as work:
        await work.sources.enqueue_with_job(
            source,
            job,
            spec_json=spec_json,
            spec_sha256=hash_job_spec(spec_json),
        )
        await work.commit()
    return paper_id, source_id, job_id, artifact_id, request_id


def _acquire_candidate_roles(
    lease_root: Path,
    identity_path: Path,
    *,
    runtime_namespace: str,
) -> tuple[object, ...]:
    owner = uuid.uuid4().hex
    acquired: list[object] = []
    try:
        acquired.append(
            ApiRuntimePresence(lease_root).acquire(
                identity_path,
                environment="candidate",
                runtime_namespace=runtime_namespace,
                owner_id=f"native-smoke-api-{owner}",
                pid=os.getpid(),
            )
        )
        role_leases = RoleScopedRuntimeLease(lease_root)
        for role in ("worker", "scheduler"):
            acquired.append(
                role_leases.acquire(
                    identity_path,
                    environment="candidate",
                    runtime_namespace=runtime_namespace,
                    role=role,
                    owner_id=f"native-smoke-{role}-{owner}",
                    pid=os.getpid(),
                )
            )
    except BaseException:
        _release_leases(tuple(acquired))
        raise
    return tuple(acquired)


def _release_leases(leases: tuple[object, ...]) -> None:
    first_error: BaseException | None = None
    for lease in reversed(leases):
        try:
            lease.release()
        except BaseException as error:
            if first_error is None:
                first_error = error
    if first_error is not None:
        raise first_error


async def _exercise_python_http(
    application: _SmokeApplication,
    paths: tuple[str, ...],
    *,
    on_started: Callable[[], None] | None = None,
) -> int:
    listener = CandidateRuntimeGuard().bind_loopback_socket(host="127.0.0.1", port=0)
    port = int(listener.getsockname()[1])
    app = create_app(
        ApiSettings(bind_host="127.0.0.1", bind_port=port),
        ApiDependencies(application, application.session_factory),
        required_schema_revision=_SCHEMA_REVISION,
    )
    server = uvicorn.Server(
        uvicorn.Config(
            app,
            host="127.0.0.1",
            port=port,
            access_log=False,
            log_level="warning",
        )
    )
    task = asyncio.create_task(server.serve(sockets=[listener]))
    try:
        for _attempt in range(9000):
            if server.started:
                break
            if task.done():
                await task
                raise NativeSmokeError(
                    "NATIVE_SMOKE_HTTP_FAILED",
                    "The isolated FastAPI runtime exited before readiness.",
                )
            await asyncio.sleep(0.01)
        else:
            raise NativeSmokeError(
                "NATIVE_SMOKE_HTTP_FAILED",
                "The isolated FastAPI runtime did not become ready.",
            )
        if on_started is not None:
            on_started()
        for path in paths:
            status = await asyncio.to_thread(_loopback_status, port, path)
            if status != 200:
                raise NativeSmokeError(
                    "NATIVE_SMOKE_HTTP_FAILED",
                    f"The isolated FastAPI endpoint {path} returned HTTP {status}.",
                )
    finally:
        server.should_exit = True
        try:
            await asyncio.wait_for(task, timeout=15)
        finally:
            listener.close()
    return port


def _loopback_status(port: int, path: str) -> int:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(f"http://127.0.0.1:{port}{path}", timeout=3) as response:
        response.read()
        return int(response.status)


def _verify_mcp_contract(database: Path) -> None:
    adapter = ApplicationMcpAdapter(
        database,
        artifact_read_mode="prefer_new",
        ranker=lambda *_args, **_kwargs: [],
        has_pdf=lambda _row: False,
    )
    if any(not callable(getattr(adapter, name, None)) for name in _MCP_TOOL_NAMES):
        raise NativeSmokeError(
            "NATIVE_SMOKE_MCP_FAILED",
            "The application MCP tools/list contract is incomplete.",
        )
    overview = adapter.library_overview()
    if not isinstance(overview, dict) or overview.get("ok") is not True:
        raise NativeSmokeError(
            "NATIVE_SMOKE_MCP_FAILED",
            "The application MCP adapter could not read the isolated database.",
        )


def _configuration_live_database(configuration: object) -> Path:
    explicit = getattr(configuration, "live_database_path", None)
    if explicit is not None:
        return Path(explicit).expanduser().resolve(strict=True)
    values = {
        role.environment.get("DB_PATH")
        for role in getattr(configuration, "roles", ())
        if getattr(role, "role", None) in {"api", "worker", "scheduler"}
        and role.environment.get("DB_PATH")
    }
    if len(values) != 1:
        raise NativeSmokeError(
            "NATIVE_SMOKE_CONFIGURATION_INVALID",
            "The native specification must bind one exact Live database path.",
        )
    return Path(next(iter(values))).expanduser().resolve(strict=True)


def _rollback_map_from_configuration(configuration: object) -> dict[str, object]:
    rollback = getattr(configuration, "rollback", None)
    if rollback is None:
        raise NativeSmokeError(
            "NATIVE_SMOKE_CONFIGURATION_INVALID",
            "The native specification has no frozen Node rollback configuration.",
        )
    database = _configuration_live_database(configuration)
    executable = Path(rollback.executable_path).resolve(strict=True)
    entrypoint = Path(rollback.entrypoint_path).resolve(strict=True)
    return {
        "deploymentKind": "native-windows",
        "executablePath": str(executable),
        "executableSha256": _file_sha256(executable),
        "entrypointPath": str(entrypoint),
        "entrypointSha256": _file_sha256(entrypoint),
        "cwd": str(Path(rollback.cwd).resolve(strict=True)),
        "host": "127.0.0.1",
        "ports": {"api": 5173},
        "databasePath": str(database),
        "environment": dict(rollback.environment),
    }


def _isolated_rollback_map(
    rollback_map: Mapping[str, object],
    *,
    database: Path,
    port: int,
) -> dict[str, object]:
    result = copy.deepcopy(dict(rollback_map))
    result["host"] = "127.0.0.1"
    result["ports"] = {"api": port}
    result["databasePath"] = str(database)
    return result


def _unused_loopback_port() -> int:
    listener = CandidateRuntimeGuard().bind_loopback_socket(host="127.0.0.1", port=0)
    try:
        return int(listener.getsockname()[1])
    finally:
        listener.close()


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _required_environment_path(name: str, *, existing: bool) -> Path:
    value = os.environ.get(name)
    if not isinstance(value, str) or not value.strip():
        raise NativeSmokeError(
            "NATIVE_SMOKE_ENVIRONMENT_INVALID",
            f"{name} is required for native smoke runners.",
        )
    path = Path(value).expanduser().resolve(strict=existing)
    if existing and not path.exists():
        raise NativeSmokeError(
            "NATIVE_SMOKE_ENVIRONMENT_INVALID",
            f"{name} does not exist.",
        )
    return path


def _create_runner(kind: str) -> object:
    spec_path = _required_environment_path("STUDY_APP_NATIVE_RUNTIME_SPEC", existing=True)
    build_path = _required_environment_path("P6_BUILD_IDENTITY_MANIFEST", existing=True)
    state_directory = _required_environment_path(
        "STUDY_APP_NATIVE_RUNTIME_STATE_DIR",
        existing=False,
    )
    configuration: NativeRuntimeConfiguration = load_native_runtime_configuration(spec_path)
    build_identity = verify_native_runtime_spec(
        build_identity_manifest=build_path,
        native_runtime_spec=spec_path,
    )
    if kind == "candidate":
        return NativeCandidateWriteSmokeRunner(
            configuration=configuration,
            build_identity=build_identity,
            state_directory=state_directory,
        )
    if kind == "compatibility":
        return NativeCompatibilitySmokeRunner(
            configuration=configuration,
            build_identity=build_identity,
            state_directory=state_directory,
        )
    raise AssertionError(f"unsupported native smoke runner: {kind}")


def create_candidate_write_smoke_runner() -> NativeCandidateWriteSmokeRunner:
    return cast(NativeCandidateWriteSmokeRunner, _create_runner("candidate"))


def create_compatibility_smoke_runner() -> NativeCompatibilitySmokeRunner:
    return cast(NativeCompatibilitySmokeRunner, _create_runner("compatibility"))


__all__ = [
    "NativeCandidateWriteSmokeRunner",
    "NativeCompatibilitySmokeRunner",
    "NativeSmokeError",
    "create_candidate_write_smoke_runner",
    "create_compatibility_smoke_runner",
]
