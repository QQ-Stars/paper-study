from __future__ import annotations

import argparse
import asyncio
import json
import os
from collections.abc import Mapping, Sequence
from pathlib import Path
import signal
import sys
from typing import Any, TextIO

import uvicorn

from backend.app.api.app import create_app
from backend.app.api.compat.database_identity import (
    ContainerDatabaseIdentityService,
    load_database_evidence_identity_manifest,
)
from backend.app.api.dependencies import ApiDependencies
from backend.app.bootstrap import (
    RolloutSettings,
    bootstrap,
    bootstrap_processing_worker,
    verify_schema_revision,
)
from backend.app.cli.runtime_owner import read_node_active_owner_marker
from backend.app.config import DatabaseSettings
from backend.app.domain.context import EmbeddingProfile
from backend.app.providers.embeddings import Model2VecEmbeddingProvider
from backend.app.providers.legacy_p3 import legacy_p3_provider_factories
from backend.app.providers.runtime_lease import (
    ApiRuntimePresence,
    RoleScopedRuntimeLease,
    candidate_runtime_drain_request,
)
from backend.app.runtime import (
    CandidateRuntimeGuard,
    ProcessRuntimeSettings,
    ProductionRuntimeGuard,
    RuntimeRoleError,
    parse_process_role,
)
from backend.app.rollout import parse_rollout_settings


P4_SCHEMA_REVISION = "20260807_03"


async def run(
    arguments: Sequence[str] | None = None,
    *,
    environment: Mapping[str, str] | None = None,
    stderr: TextIO | None = None,
) -> int:
    target_stderr = stderr or sys.stderr
    values = dict(os.environ if environment is None else environment)
    lease = None
    api_presence = None
    container = None
    try:
        options = _parser().parse_args(arguments)
        option_role = options.role
        role = parse_process_role(values)
        if option_role != role:
            raise RuntimeRoleError(
                "PROCESS_ROLE_MISMATCH",
                "The command role must equal API_PROCESS_ROLE.",
            )
        if options.study_app_role is not None and options.study_app_role != role:
            raise RuntimeRoleError(
                "PROCESS_MARKER_MISMATCH",
                "The process marker role must equal API_PROCESS_ROLE.",
            )
        if (
            options.study_app_environment is not None
            and options.study_app_environment != values.get("RUNTIME_ENVIRONMENT")
        ):
            raise RuntimeRoleError(
                "PROCESS_MARKER_MISMATCH",
                "The process marker environment must equal RUNTIME_ENVIRONMENT.",
            )
        database = DatabaseSettings(values.get("DB_PATH"))
        runtime = ProcessRuntimeSettings.from_environment(database, values)
        required_revision = _required_revision(values)
        runtime_environment = values.get("RUNTIME_ENVIRONMENT", "")
        production_admission = None
        if runtime_environment == "candidate":
            parent_backup = _required_path(values, "CANDIDATE_PARENT_BACKUP")
            parent_manifest = _required_path(values, "CANDIDATE_PARENT_MANIFEST")
            owner = read_node_active_owner_marker(
                _required_path(values, "PRODUCTION_OWNER_MARKER")
            )
            if values.get("CANDIDATE_HOST_IDENTITY_MANIFEST", "").strip():
                runtime_identity = ContainerDatabaseIdentityService().ensure_runtime_identity(
                    database=database.database_path,
                    host_database_identity_manifest=_required_path(
                        values,
                        "CANDIDATE_HOST_IDENTITY_MANIFEST",
                    ),
                    parent_database_identity_manifest=_required_path(
                        values,
                        "CANDIDATE_PARENT_IDENTITY_MANIFEST",
                    ),
                    origin_receipt=_required_path(values, "CANDIDATE_ORIGIN_RECEIPT"),
                    parent_backup=parent_backup,
                    parent_manifest=parent_manifest,
                    owner=owner,
                    output=_required_output_path(values, "DATABASE_IDENTITY_MANIFEST"),
                )
                identity_path = runtime_identity.manifest_path
                identity_input: object = runtime_identity
            else:
                identity_path = _required_path(values, "DATABASE_IDENTITY_MANIFEST")
                identity_input = identity_path
            identity = CandidateRuntimeGuard().validate_role(
                identity_input,
                database=database,
                environment=runtime_environment,
                runtime_namespace=values.get("RUNTIME_NAMESPACE", ""),
                role=role,
                parent_backup=parent_backup,
                parent_manifest=parent_manifest,
            )
            if (
                owner.database_lineage_id != identity.database_lineage_id
                or owner.origin_receipt_file_sha256
                != identity.origin_receipt_file_sha256
                or owner.subject_database_id != identity.parent_subject_database_id
            ):
                raise RuntimeRoleError(
                    "OWNER_MARKER_IDENTITY_MISMATCH",
                    "Candidate parent and production owner evidence must identify one Live subject.",
                )
        elif runtime_environment == "live":
            identity_path = _required_path(values, "DATABASE_IDENTITY_MANIFEST")
            guard = ProductionRuntimeGuard()
            if values.get("P6_HANDOFF_RECEIPT", "").strip():
                production_admission = guard.validate_active_owner(
                    handoff_receipt=_required_path(values, "P6_HANDOFF_RECEIPT"),
                    expected_handoff_receipt_sha256=_required_sha256(
                        values, "P6_HANDOFF_RECEIPT_SHA256"
                    ),
                    owner_marker=_required_path(values, "PRODUCTION_OWNER_MARKER"),
                    database=database,
                    environment=runtime_environment,
                    runtime_namespace=values.get("RUNTIME_NAMESPACE", ""),
                    role=role,
                )
            else:
                production_admission = guard.validate_pending_handoff(
                    authorization=_required_path(values, "P6_PROMOTION_AUTHORIZATION"),
                    expected_authorization_sha256=_required_sha256(
                        values, "P6_PROMOTION_AUTHORIZATION_SHA256"
                    ),
                    final_evidence_run_manifest=_required_path(
                        values, "P6_FINAL_EVIDENCE_RUN_MANIFEST"
                    ),
                    expected_final_evidence_run_manifest_sha256=_required_sha256(
                        values, "P6_FINAL_EVIDENCE_RUN_MANIFEST_SHA256"
                    ),
                    cutover_lease=_required_path(values, "P6_CUTOVER_LEASE"),
                    startup_snapshot=_required_path(
                        values, "P6_PRODUCTION_STARTUP_SNAPSHOT"
                    ),
                    expected_startup_snapshot_sha256=_required_sha256(
                        values, "P6_PRODUCTION_STARTUP_SNAPSHOT_SHA256"
                    ),
                    build_identity_manifest=_required_path(
                        values, "P6_BUILD_IDENTITY_MANIFEST"
                    ),
                    expected_build_identity_manifest_sha256=_required_sha256(
                        values, "P6_BUILD_IDENTITY_MANIFEST_SHA256"
                    ),
                    database_identity_manifest=identity_path,
                    expected_database_identity_manifest_sha256=_required_sha256(
                        values, "P6_DATABASE_IDENTITY_MANIFEST_SHA256"
                    ),
                    owner_marker=_required_path(values, "PRODUCTION_OWNER_MARKER"),
                    database=database,
                    environment=runtime_environment,
                    runtime_namespace=values.get("RUNTIME_NAMESPACE", ""),
                    role=role,
                )
            identity = load_database_evidence_identity_manifest(identity_path)
        else:
            raise RuntimeRoleError(
                "RUNTIME_ENVIRONMENT_INVALID",
                "Runtime startup requires candidate or Live environment.",
            )
        verify_schema_revision(database, required_revision)

        lease_root = _required_directory(values, "RUNTIME_LEASE_DIR")
        if role == "api" and runtime_environment == "candidate":
            api_presence = ApiRuntimePresence(lease_root).acquire(
                identity_path,
                environment="candidate",
                runtime_namespace=values["RUNTIME_NAMESPACE"],
                owner_id=f"candidate-api-{os.getpid()}-{os.urandom(8).hex()}",
                pid=os.getpid(),
            )
        elif role != "api":
            lease = RoleScopedRuntimeLease(lease_root).acquire(
                identity_path,
                environment=runtime_environment,
                runtime_namespace=values["RUNTIME_NAMESPACE"],
                role=role,
                owner_id=f"{runtime_environment}-{role}-{os.getpid()}",
                pid=os.getpid(),
                production_admission=production_admission,
            )
        drain_request = candidate_runtime_drain_request(
            lease_root,
            identity,
            runtime_namespace=values["RUNTIME_NAMESPACE"],
            role=role,
        )

        translation_factory, structured_factory = legacy_p3_provider_factories(values)
        embedding_profile = _embedding_profile(values)
        embedding_factory = lambda profile, _credential: Model2VecEmbeddingProvider(profile)
        control_stdin = values.get("CANDIDATE_CONTROL_STDIN") == "1"
        if role == "worker":
            rollout = _rollout(values)
            container = bootstrap_processing_worker(
                database,
                required_schema_revision=required_revision,
                worker_id=f"candidate-worker-{os.getpid()}",
                translation_provider_factory=translation_factory,
                structured_provider_factory=structured_factory,
                embedding_profile=embedding_profile,
                embedding_provider_factory=embedding_factory,
                obsidian_enabled=rollout.obsidian_enabled,
                environment_snapshot=values,
            )
            reconciler = getattr(container, "obsidian_startup_reconciler", None)
            if reconciler is not None:
                await reconciler.run()
            await _run_worker(
                container.processing_worker,
                control_stdin=control_stdin,
                drain_request=drain_request,
                auto_export_policy=getattr(container, "obsidian_auto_export", None),
            )
        else:
            container = bootstrap(
                _rollout(values),
                database,
                required_schema_revision=required_revision,
                environment_snapshot=values,
                translation_provider_factory=translation_factory,
                structured_provider_factory=structured_factory,
                embedding_profile=embedding_profile,
                embedding_provider_factory=embedding_factory,
                query_embedding_provider_factory=embedding_factory,
            )
            if role == "api":
                app = create_app(
                    runtime.api_settings(),
                    ApiDependencies(container, container.session_factory),
                    required_schema_revision=required_revision,
                )
                await _run_api(
                    app,
                    runtime,
                    control_stdin=control_stdin,
                    drain_request=drain_request,
                )
            else:
                await _run_scheduler(
                    container.legacy.scheduler,
                    control_stdin=control_stdin,
                    drain_request=drain_request,
                )
        return 0
    except (KeyboardInterrupt, asyncio.CancelledError):
        return 130
    except Exception as error:
        traceback = error.__traceback__
        while traceback is not None and traceback.tb_next is not None:
            traceback = traceback.tb_next
        exception_origin = (
            None
            if traceback is None
            else (
                f"{Path(traceback.tb_frame.f_code.co_filename).name}:"
                f"{traceback.tb_lineno}:{traceback.tb_frame.f_code.co_name}"
            )
        )
        _write_error(
            target_stderr,
            str(getattr(error, "code", "CANDIDATE_STARTUP_FAILED")),
            str(error),
            exception_type=type(error).__name__,
            exception_origin=exception_origin,
        )
        return 2
    finally:
        try:
            if container is not None:
                await container.dispose()
        finally:
            if lease is not None:
                lease.release()
            if api_presence is not None:
                api_presence.release()


async def _run_api(
    app: Any,
    runtime: ProcessRuntimeSettings,
    *,
    control_stdin: bool,
    drain_request: tuple[Path, bytes],
) -> None:
    stop_event = asyncio.Event()
    cleanup = _install_signal_handlers(stop_event)
    control_task = _runtime_control_task(
        stop_event,
        stdin_enabled=control_stdin,
        drain_request=drain_request,
    )
    server = uvicorn.Server(
        uvicorn.Config(
            app,
            host=runtime.bind_host,
            port=runtime.bind_port,
            access_log=False,
            log_level="warning",
        )
    )
    serve_task = asyncio.create_task(server.serve())
    stop_task = asyncio.create_task(stop_event.wait())
    try:
        done, _pending = await asyncio.wait(
            (serve_task, stop_task),
            return_when=asyncio.FIRST_COMPLETED,
        )
        if stop_task in done:
            server.should_exit = True
        await serve_task
    finally:
        stop_task.cancel()
        await asyncio.gather(stop_task, return_exceptions=True)
        await _cancel_control_task(control_task)
        cleanup()


async def _run_worker(
    worker: Any,
    *,
    control_stdin: bool,
    drain_request: tuple[Path, bytes],
    auto_export_policy: Any = None,
) -> None:
    stop_event = asyncio.Event()
    cleanup = _install_signal_handlers(stop_event)
    control_task = _runtime_control_task(
        stop_event,
        stdin_enabled=control_stdin,
        drain_request=drain_request,
    )
    try:
        await worker.run_forever(
            stop_event=stop_event,
            iteration_hook=(
                auto_export_policy.flush_due
                if auto_export_policy is not None
                else None
            ),
        )
    finally:
        await _cancel_control_task(control_task)
        cleanup()


async def _run_scheduler(
    scheduler: Any,
    *,
    control_stdin: bool,
    drain_request: tuple[Path, bytes],
) -> None:
    stop_event = asyncio.Event()
    cleanup = _install_signal_handlers(stop_event)
    control_task = _runtime_control_task(
        stop_event,
        stdin_enabled=control_stdin,
        drain_request=drain_request,
    )
    scheduler_task = asyncio.create_task(scheduler.run())
    stop_task = asyncio.create_task(stop_event.wait())
    try:
        done, _pending = await asyncio.wait(
            (scheduler_task, stop_task),
            return_when=asyncio.FIRST_COMPLETED,
        )
        if stop_task in done:
            scheduler.stop()
        await scheduler_task
    finally:
        stop_task.cancel()
        await asyncio.gather(stop_task, return_exceptions=True)
        await _cancel_control_task(control_task)
        cleanup()


def _runtime_control_task(
    stop_event: asyncio.Event,
    *,
    stdin_enabled: bool,
    drain_request: tuple[Path, bytes],
) -> asyncio.Task[None]:
    request_path, expected_payload = drain_request

    async def watch() -> None:
        stdin_task = (
            asyncio.create_task(asyncio.to_thread(sys.stdin.buffer.readline))
            if stdin_enabled
            else None
        )
        try:
            while True:
                try:
                    payload = request_path.read_bytes()
                except FileNotFoundError:
                    payload = None
                except OSError as error:
                    stop_event.set()
                    raise RuntimeRoleError(
                        "CANDIDATE_RUNTIME_DRAIN_REQUEST_INVALID",
                        "The candidate drain request could not be read.",
                    ) from error
                if payload is not None:
                    stop_event.set()
                    if payload != expected_payload:
                        raise RuntimeRoleError(
                            "CANDIDATE_RUNTIME_DRAIN_REQUEST_INVALID",
                            "The candidate drain request failed its identity fence.",
                        )
                    return
                if stdin_task is not None and stdin_task.done():
                    await stdin_task
                    stop_event.set()
                    return
                await asyncio.sleep(0.05)
        finally:
            if stdin_task is not None and not stdin_task.done():
                stdin_task.cancel()
                await asyncio.gather(stdin_task, return_exceptions=True)

    return asyncio.create_task(watch())


async def _cancel_control_task(task: asyncio.Task[None] | None) -> None:
    if task is None:
        return
    if not task.done():
        task.cancel()
    results = await asyncio.gather(task, return_exceptions=True)
    for result in results:
        if isinstance(result, Exception):
            raise result


def _install_signal_handlers(stop_event: asyncio.Event):
    previous: dict[Any, Any] = {}

    def request_stop(_signum: object, _frame: object) -> None:
        stop_event.set()

    for name in ("SIGINT", "SIGTERM", "SIGBREAK"):
        signum = getattr(signal, name, None)
        if signum is None:
            continue
        previous[signum] = signal.getsignal(signum)
        signal.signal(signum, request_stop)

    def cleanup() -> None:
        for signum, handler in previous.items():
            signal.signal(signum, handler)

    return cleanup


def _rollout(environment: Mapping[str, str]) -> RolloutSettings:
    parsed = parse_rollout_settings(environment, vocabulary="p5")
    return RolloutSettings(
        api_backend_mode="python",
        document_pipeline_mode="p1",
        generation_pipeline_mode="p1",
        artifact_read_mode="prefer_new",
        artifact_write_mode="dual",
        ocr_enabled=parsed.ocr_enabled,
        obsidian_enabled=parsed.obsidian_enabled,
        processing_cursor_secret=environment.get("PROCESSING_CURSOR_SECRET"),
    )


def _embedding_profile(environment: Mapping[str, str]) -> EmbeddingProfile:
    provider = environment.get("EMBED_PROVIDER", "model2vec").strip().lower()
    if provider == "local":
        provider = "model2vec"
    if provider != "model2vec":
        raise RuntimeRoleError(
            "CANDIDATE_EMBEDDING_PROVIDER_INVALID",
            "P4 candidate startup supports only the lazy model2vec adapter.",
        )
    try:
        dimensions = int(environment.get("EMBED_DIMENSIONS", "256"))
    except ValueError as error:
        raise RuntimeRoleError(
            "CANDIDATE_EMBEDDING_PROFILE_INVALID",
            "EMBED_DIMENSIONS must be an integer.",
        ) from error
    return EmbeddingProfile(
        provider=provider,
        model=environment.get(
            "EMBED_MODEL",
            "minishlab/potion-multilingual-128M",
        ),
        embedding_version=environment.get(
            "EMBEDDING_VERSION",
            "model2vec-0.8.2",
        ),
        dimensions=dimensions,
    )


def _required_revision(environment: Mapping[str, str]) -> str:
    revision = environment.get("REQUIRED_SCHEMA_REVISION")
    if revision != P4_SCHEMA_REVISION:
        raise RuntimeRoleError(
            "SCHEMA_REVISION_CONFIGURATION_INVALID",
            f"REQUIRED_SCHEMA_REVISION must equal {P4_SCHEMA_REVISION}.",
        )
    return revision


def _required_path(environment: Mapping[str, str], name: str) -> Path:
    value = environment.get(name)
    if not isinstance(value, str) or not value.strip():
        raise RuntimeRoleError(f"{name}_INVALID", f"{name} is required.")
    path = Path(value).expanduser().resolve(strict=True)
    if not path.is_file():
        raise RuntimeRoleError(f"{name}_INVALID", f"{name} must name a file.")
    return path


def _required_sha256(environment: Mapping[str, str], name: str) -> str:
    value = environment.get(name)
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise RuntimeRoleError(f"{name}_INVALID", f"{name} must be lowercase SHA-256.")
    return value


def _required_directory(environment: Mapping[str, str], name: str) -> Path:
    value = environment.get(name)
    if not isinstance(value, str) or not value.strip():
        raise RuntimeRoleError(f"{name}_INVALID", f"{name} is required.")
    path = Path(value).expanduser().resolve(strict=False)
    if path.exists() and not path.is_dir():
        raise RuntimeRoleError(f"{name}_INVALID", f"{name} must name a directory.")
    return path


def _required_output_path(environment: Mapping[str, str], name: str) -> Path:
    value = environment.get(name)
    if not isinstance(value, str) or not value.strip():
        raise RuntimeRoleError(f"{name}_INVALID", f"{name} is required.")
    path = Path(value).expanduser().resolve(strict=False)
    if path.exists() and not path.is_file():
        raise RuntimeRoleError(f"{name}_INVALID", f"{name} must name a file.")
    return path


def _write_error(
    stderr: TextIO,
    code: str,
    message: str,
    *,
    exception_type: str | None = None,
    exception_origin: str | None = None,
) -> None:
    details = {}
    if exception_type:
        details["exceptionType"] = exception_type
    if exception_origin:
        details["exceptionOrigin"] = exception_origin
    stderr.write(
        json.dumps(
            {"error": {"code": code, "message": message, "details": details}},
            ensure_ascii=False,
            separators=(",", ":"),
        )
        + "\n"
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="study-candidate-runtime")
    parser.add_argument("--role", required=True, choices=("api", "worker", "scheduler"))
    parser.add_argument(
        "--study-app-role",
        choices=("api", "worker", "scheduler"),
        help=argparse.SUPPRESS,
    )
    parser.add_argument("--study-app-environment", help=argparse.SUPPRESS)
    return parser


def main(arguments: Sequence[str] | None = None) -> int:
    return asyncio.run(run(sys.argv[1:] if arguments is None else arguments))


if __name__ == "__main__":
    raise SystemExit(main())
