from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
import re
import sqlite3
import os
from pathlib import Path
from typing import Any
from uuid import uuid4

from backend.app.application.generated_artifacts import (
    ArtifactGenerator,
    ArtifactReader,
    GenerationPipeline,
)
from backend.app.application.context_builder import ContextBuilder
from backend.app.application.document_artifacts import DocumentArtifactService
from backend.app.application.document_search import DocumentSearch, EmbeddingJobHandler
from backend.app.application.obsidian_auto_export import ObsidianAutoExportPolicy
from backend.app.application.obsidian_exporter import ObsidianSpecExporter
from backend.app.api.routes.document_processing import ProcessingApiService
from backend.app.application.credentials import CredentialService
from backend.app.application.settings import SettingsService
from backend.app.application.legacy_ingest import LegacyIngestService
from backend.app.application.paper_library import PaperLibrary
from backend.app.application.library_queries import LibraryQueries
from backend.app.application.legacy_processing_streams import LegacyProcessingStreams
from backend.app.application.review_scheduler import ReviewScheduler
from backend.app.application.artifact_store import ArtifactStore
from backend.app.application.search_coordinator import SearchCoordinator
from backend.app.application.reproductions import ReproductionWorkspace
from backend.app.application.source_documents import (
    DocumentSourcePipeline,
    SourceDocumentProcessor,
    build_native_source_processor,
)
from backend.app.config import DatabaseSettings
from backend.app.domain import (
    CredentialKind,
    OcrProviderContractUnverifiedError,
    OcrUnavailableError,
    SchemaRevisionMismatchError,
)
from backend.app.domain.processing import (
    ExplainJobSpecV1,
    OcrJobSpecV1,
    SourceMaterializeJobSpecV1,
    TranslateJobSpecV1,
)
from backend.app.domain.context import ChunkingSpec, EmbeddingProfile
from backend.app.infrastructure.database import create_async_session_factory
from backend.app.providers import (
    CompositeCredentialStore,
    EnvironmentCredentialStore,
    KeyringCredentialStore,
    LegacyGenerationProvider,
    LegacySettingsCredentialStore,
    NativeExtractor,
    SafeCredentialProbe,
)
from backend.app.repositories.unit_of_work import SqlAlchemyUnitOfWork
from backend.app.repositories.document_search import SqlAlchemyDocumentSearchRepository
from backend.app.repositories.translation_checkpoints import (
    SqlAlchemyTranslationCheckpointRepository,
)
from backend.app.providers.ocr.registry import compose_ocr_gate, create_production_ocr_registry
from backend.app.providers.legacy_agent import LegacyAgentProvider
from backend.app.providers.legacy_p3 import legacy_p3_runtime_config_resolver
from backend.app.providers.pdf_files import PdfFiles
from backend.app.workers.scheduler import LegacyScheduler
from backend.app.workers.processing_worker import (
    ProcessingHandlerOutcome,
    ProcessingWorker,
)
from backend.app.workers.obsidian import ObsidianJobHandler, ObsidianJobService
from backend.app.workers.runtime import ObsidianStartupReconciler


_REVISION = re.compile(r"(?:20260807_0[123]|20260825_04|20260826_01|20260829_01|20260830_01)\Z")
_P3_REVISIONS = frozenset({"20260807_03", "20260825_04", "20260826_01", "20260829_01", "20260830_01"})
_PROCESSING_CURSOR_SECRET_VARIABLE = "PROCESSING_CURSOR_SECRET"


@dataclass(frozen=True, slots=True)
class RolloutSettings:
    api_backend_mode: str = "legacy"
    document_pipeline_mode: str = "legacy"
    generation_pipeline_mode: str = "legacy"
    artifact_read_mode: str = "legacy"
    artifact_write_mode: str = "legacy"
    ocr_enabled: bool = False
    obsidian_enabled: bool = False
    processing_cursor_secret: str | None = None

    def __post_init__(self) -> None:
        accepted = {
            "api_backend_mode": {"legacy", "shadow", "python"},
            "document_pipeline_mode": {"legacy", "p1"},
            "generation_pipeline_mode": {"legacy", "p1"},
            "artifact_read_mode": {"legacy", "prefer_new"},
            "artifact_write_mode": {"legacy", "dual"},
        }
        for field, values in accepted.items():
            if getattr(self, field) not in values:
                raise ValueError(f"invalid rollout value for {field}")


@dataclass(slots=True)
class ApplicationContainer:
    schema_revision: str | None
    session_factory: Any = None
    native_provider: Any = None
    generation_provider: Any = None
    source_pipeline: Any = None
    generation_pipeline: Any = None
    artifact_reader: Any = None
    credential_store: Any = None
    credential_service: Any = None
    processing_worker: Any = None
    source_processor: Any = None
    artifact_generator: Any = None
    processing_api: Any = None
    document_artifacts: Any = None
    document_search: Any = None
    embedding_profile: EmbeddingProfile | None = None
    embedding_profile_resolver: Any = None
    p3_translation_provider: Any = None
    p3_structured_provider: Any = None
    obsidian_jobs: Any = None
    obsidian_auto_export: Any = None
    obsidian_startup_reconciler: Any = None
    legacy: Any = None
    pdf_files: Any = None
    reproduction_workspace: Any = None
    _disposed: bool = field(default=False, init=False, repr=False)

    async def dispose(self) -> None:
        if self._disposed:
            return
        if self.session_factory is not None:
            await self.session_factory.kw["bind"].dispose()
        self._disposed = True


@dataclass(slots=True)
class LegacyApplicationServices:
    """Dependency bundle consumed by the legacy HTTP adapters."""

    settings: Any
    legacy_ingest: Any = None
    scheduler: Any = None
    paper_library: Any = None
    library_queries: Any = None
    review_scheduler: Any = None
    artifact_store: Any = None
    search_coordinator: Any = None
    agent: Any = None
    pdf_files: Any = None
    processing_streams: Any = None


def bootstrap(
    rollout: RolloutSettings,
    database_settings: DatabaseSettings,
    *,
    required_schema_revision: str,
    native_provider_factory: Callable[[], Any] = NativeExtractor,
    generation_provider_factory: Callable[[], Any] = LegacyGenerationProvider,
    environment_snapshot: dict[str, str] | None = None,
    keyring_adapter: Any = None,
    allow_legacy_credential_fallback: bool = False,
    legacy_settings_path: Path | None = None,
    credential_probe: Any = None,
    ocr_registry_factory: Callable[[], Any] | None = None,
    translation_provider_factory: Callable[[], Any] | None = None,
    structured_provider_factory: Callable[[], Any] | None = None,
    embedding_profile: EmbeddingProfile | None = None,
    embedding_provider_factory: Callable[[EmbeddingProfile, Any], Any] | None = None,
    query_embedding_provider_factory: Callable[[EmbeddingProfile, Any], Any] | None = None,
) -> ApplicationContainer:
    if _REVISION.fullmatch(required_schema_revision) is None:
        raise ValueError("required_schema_revision must be a frozen stage revision")
    p1_selected = any(
        (
            rollout.document_pipeline_mode == "p1",
            rollout.generation_pipeline_mode == "p1",
            rollout.artifact_read_mode == "prefer_new",
            rollout.artifact_write_mode == "dual",
        )
    )
    if not p1_selected:
        return ApplicationContainer(schema_revision=None)
    if rollout.ocr_enabled and required_schema_revision == "20260807_01":
        raise OcrUnavailableError()

    verify_schema_revision(database_settings, required_schema_revision)
    cursor_secret = (
        _processing_cursor_secret(rollout, environment_snapshot)
        if required_schema_revision != "20260807_01"
        else None
    )
    if required_schema_revision in _P3_REVISIONS:
        _validate_p3_api_configuration(
            translation_provider_factory=translation_provider_factory,
            structured_provider_factory=structured_provider_factory,
        )
    session_factory = create_async_session_factory(database_settings)
    unit_of_work_factory = lambda: SqlAlchemyUnitOfWork(session_factory)
    native_provider = (
        native_provider_factory()
        if rollout.document_pipeline_mode == "p1"
        else None
    )
    generation_provider = (
        generation_provider_factory()
        if rollout.generation_pipeline_mode == "p1" or rollout.artifact_write_mode == "dual"
        else None
    )
    source_pipeline = (
        DocumentSourcePipeline(
            unit_of_work_factory,
            native_provider,
            clock=lambda: datetime.now(timezone.utc),
            id_factory=lambda: f"src_{uuid4().hex}",
        )
        if native_provider is not None
        else None
    )
    generation_pipeline = (
        GenerationPipeline(
            unit_of_work_factory,
            source_pipeline,
            generation_provider,
            clock=lambda: datetime.now(timezone.utc),
            id_factory=lambda: f"art_{uuid4().hex}",
        )
        if source_pipeline is not None and generation_provider is not None
        else None
    )
    artifact_reader = (
        ArtifactReader(unit_of_work_factory, rollout.artifact_read_mode)
        if rollout.artifact_read_mode == "prefer_new"
        else None
    )
    effective_environment = (
        dict(os.environ) if environment_snapshot is None else dict(environment_snapshot)
    )
    resolved_settings_path = (
        legacy_settings_path
        if legacy_settings_path is not None
        else Path(__file__).resolve().parents[2] / "data" / "settings.json"
    )
    effective_environment["PAPER_STUDY_SETTINGS_PATH"] = str(resolved_settings_path)
    credential_store = CompositeCredentialStore(
        EnvironmentCredentialStore(effective_environment),
        KeyringCredentialStore(keyring_adapter),
        LegacySettingsCredentialStore(resolved_settings_path),
        allow_legacy_fallback=allow_legacy_credential_fallback,
    )
    credential_service = CredentialService(
        credential_store,
        credential_probe or SafeCredentialProbe(),
    )
    repository_root = Path(__file__).resolve().parents[2]
    settings_service = SettingsService(
        settings_path=resolved_settings_path,
        root=repository_root,
        credential_service=credential_service,
        environment_snapshot=effective_environment,
        rollout_snapshot=rollout,
        default_dirs={
            "pdfDir": repository_root / "data" / "pdfs",
            "explainerDir": repository_root / "data" / "explainers",
            "translationDir": repository_root / "data" / "translations",
            "ocrMarkdownDir": repository_root / "data" / "ocr_markdown",
            "reproductionDir": repository_root / "data" / "reproduction-artifacts",
        },
    )
    translation_provider_factory = _bind_p3_credential_resolver(
        translation_provider_factory,
        credential_store,
        runtime_config_resolver=settings_service.llm_runtime_settings,
    )
    structured_provider_factory = _bind_p3_credential_resolver(
        structured_provider_factory,
        credential_store,
        runtime_config_resolver=settings_service.llm_runtime_settings,
    )

    async def resolve_embedding_profile() -> EmbeddingProfile | None:
        # Read the settings document for each new request/job.  Worker leases
        # remain immutable; only the profile used to enqueue the next job is
        # resolved dynamically.
        return await settings_service.embedding_profile(embedding_profile)

    def resolve_ocr_runtime_settings() -> dict[str, object]:
        runtime = settings_service.ocr_runtime_settings(
            fallback_enabled=rollout.ocr_enabled,
        )
        # Rollout composition is a hard safety gate; a saved UI toggle cannot
        # enable OCR when this process was started without OCR support.
        if not rollout.ocr_enabled:
            runtime["ocrEnabled"] = False
        return runtime
    # Keep compatibility commands inside the current runtime.  Spawning a
    # second Python process for every legacy button is blocked by Windows
    # socket policy and surfaces as the misleading "legacy agent failed".
    legacy_agent = LegacyAgentProvider(
        cwd=repository_root,
        environment=effective_environment,
        in_process=True,
        timeout_seconds=120.0,
        environment_provider=lambda: _legacy_agent_environment(
            effective_environment,
            credential_store,
        ),
    )
    legacy_ingest = LegacyIngestService(
        session_factory,
        provider=legacy_agent,
    )
    pdf_files = PdfFiles(
        root=repository_root,
        default_directory=repository_root / "data" / "pdfs",
        seed_directory=repository_root / "paper",
    )
    paper_library = PaperLibrary(
        unit_of_work_factory,
        pdf_files=pdf_files,
    )
    library_queries = LibraryQueries(
        unit_of_work_factory,
        pdf_files=pdf_files,
    )
    review_scheduler = ReviewScheduler(unit_of_work_factory)

    async def _paper_has_pdf(paper_id: str) -> bool:
        # 必须回查 DB 的 pdf_path（旧标题命名文件）再解析，否则仅按 <id>.pdf
        # 命名查找会把大量存量论文误判为“缺少 PDF”（与 pdfbytes 修复同源）。
        try:
            row = await library_queries.get_paper(paper_id)
        except Exception:
            row = None
        stored = row.get("pdf_path") if isinstance(row, dict) else None
        return pdf_files.resolve_for_id(paper_id, stored_path=stored) is not None

    artifact_store = ArtifactStore(
        unit_of_work_factory,
        read_mode=rollout.artifact_read_mode,
        legacy_markdown_root=repository_root / "paper",
        has_pdf=_paper_has_pdf,
    )
    search_coordinator = SearchCoordinator(legacy_agent, unit_of_work_factory)
    legacy_services = LegacyApplicationServices(
        settings=settings_service,
        legacy_ingest=legacy_ingest,
        scheduler=LegacyScheduler(legacy_ingest),
        paper_library=paper_library,
        library_queries=library_queries,
        review_scheduler=review_scheduler,
        artifact_store=artifact_store,
        search_coordinator=search_coordinator,
        agent=legacy_agent,
        pdf_files=pdf_files,
    )
    processing_api = None
    artifact_generator = None
    document_artifacts = None
    document_search = None
    p3_translation_provider = None
    p3_structured_provider = None
    if required_schema_revision != "20260807_01":
        assert cursor_secret is not None
        artifact_generator = ArtifactGenerator(
            unit_of_work_factory,
            generation_provider,
            clock=lambda: datetime.now(timezone.utc),
            artifact_id_factory=lambda: f"art_{uuid4().hex}",
            job_id_factory=lambda: f"job_{uuid4().hex}",
        )
        processing_api = ProcessingApiService(
            unit_of_work_factory,
            native_provider,
            compose_ocr_gate(
                enabled=rollout.ocr_enabled,
                registry_factory=ocr_registry_factory,
                settings_resolver=resolve_ocr_runtime_settings,
            ),
            artifact_generator,
            cursor_secret=cursor_secret,
        )
    if required_schema_revision in _P3_REVISIONS:
        (
            document_artifacts,
            document_search,
            p3_translation_provider,
            p3_structured_provider,
        ) = _compose_p3_services(
            unit_of_work_factory,
            session_factory,
            credential_store,
            clock=lambda: datetime.now(timezone.utc),
            translation_provider_factory=translation_provider_factory,
            structured_provider_factory=structured_provider_factory,
            embedding_profile=embedding_profile,
            embedding_provider_factory=embedding_provider_factory,
            query_embedding_provider_factory=query_embedding_provider_factory,
            translation_mode_resolver=settings_service.translation_mode,
        )
        processing_api = ProcessingApiService(
            unit_of_work_factory,
            native_provider,
            compose_ocr_gate(
                enabled=rollout.ocr_enabled,
                registry_factory=ocr_registry_factory,
                settings_resolver=resolve_ocr_runtime_settings,
            ),
            artifact_generator,
            cursor_secret=cursor_secret,
            document_artifacts=document_artifacts,
        )
        legacy_services.processing_streams = LegacyProcessingStreams(
            unit_of_work_factory,
            artifact_service=document_artifacts,
            document_search=document_search,
            embedding_profile=embedding_profile,
            embedding_profile_resolver=resolve_embedding_profile,
            clock=lambda: datetime.now(timezone.utc),
        )
    obsidian_jobs = (
        ObsidianJobService(
            unit_of_work_factory,
            settings_service=settings_service,
            library_queries=library_queries,
        )
        if required_schema_revision in _P3_REVISIONS
        else None
    )
    return ApplicationContainer(
        schema_revision=required_schema_revision,
        session_factory=session_factory,
        native_provider=native_provider,
        generation_provider=generation_provider,
        source_pipeline=source_pipeline,
        generation_pipeline=generation_pipeline,
        artifact_reader=artifact_reader,
        credential_store=credential_store,
        credential_service=credential_service,
        artifact_generator=artifact_generator,
        processing_api=processing_api,
        document_artifacts=document_artifacts,
        document_search=document_search,
        embedding_profile=embedding_profile,
        embedding_profile_resolver=resolve_embedding_profile,
        p3_translation_provider=p3_translation_provider,
        p3_structured_provider=p3_structured_provider,
        obsidian_jobs=obsidian_jobs,
        legacy=legacy_services,
        pdf_files=pdf_files,
        reproduction_workspace=ReproductionWorkspace(
            unit_of_work_factory,
            artifact_root=settings_service.resolve_directory("reproductionDir"),
        ),
    )


def _processing_cursor_secret(
    rollout: RolloutSettings,
    environment_snapshot: dict[str, str] | None,
) -> bytes:
    configured = rollout.processing_cursor_secret
    if configured is None:
        environment = os.environ if environment_snapshot is None else environment_snapshot
        configured = environment.get(_PROCESSING_CURSOR_SECRET_VARIABLE)
    if not isinstance(configured, str):
        raise ValueError(
            "PROCESSING_CURSOR_SECRET is required when P2 API routes are composed"
        )
    try:
        encoded = configured.encode("utf-8")
    except UnicodeEncodeError:
        encoded = b""
    if len(encoded) < 32:
        raise ValueError(
            "PROCESSING_CURSOR_SECRET must contain at least 32 UTF-8 bytes"
        )
    return encoded


async def _legacy_agent_environment(
    base: Mapping[str, str],
    credential_store: Any,
) -> dict[str, str]:
    """Resolve one task-scoped environment from the backend credential seam."""
    environment = dict(base)
    mappings = {
        CredentialKind.LLM: ("LLM_API_KEY", "PAPER_STUDY_LLM_API_KEY"),
        CredentialKind.OCR: ("OCR_API_KEY", "PAPER_STUDY_OCR_API_KEY"),
        CredentialKind.EMBEDDING: ("EMBED_API_KEY", "PAPER_STUDY_EMBED_API_KEY"),
        CredentialKind.SEMANTIC_SCHOLAR: ("S2_API_KEY", "PAPER_STUDY_S2_API_KEY"),
    }
    for kind, (variable, snapshot_variable) in mappings.items():
        credential = await credential_store.get(kind)
        if credential is not None:
            environment[variable] = credential.value
            environment[snapshot_variable] = credential.value
    return environment


def verify_schema_revision(
    database_settings: DatabaseSettings,
    required_schema_revision: str,
) -> None:
    if _REVISION.fullmatch(required_schema_revision) is None:
        raise ValueError("required_schema_revision must be a frozen stage revision")
    uri = database_settings.database_path.as_uri() + "?mode=ro&immutable=1"
    try:
        connection = sqlite3.connect(uri, uri=True)
        try:
            connection.execute("PRAGMA query_only=ON")
            versions = connection.execute(
                "SELECT version_num FROM alembic_version ORDER BY version_num"
            ).fetchall()
        finally:
            connection.close()
    except sqlite3.Error as error:
        raise SchemaRevisionMismatchError(
            expected_revision=required_schema_revision,
        ) from error
    if versions != [(required_schema_revision,)]:
        actual = ",".join(str(row[0]) for row in versions) if versions else "missing"
        raise SchemaRevisionMismatchError(
            expected_revision=required_schema_revision,
            actual_revision=actual,
        )


_verify_revision_read_only = verify_schema_revision


def bootstrap_processing_worker(
    database_settings: DatabaseSettings,
    *,
    required_schema_revision: str,
    worker_id: str,
    native_provider_factory: Callable[[], Any] = NativeExtractor,
    generation_provider_factory: Callable[[], Any] = LegacyGenerationProvider,
    clock: Callable[[], datetime] | None = None,
    logger: Callable[[dict[str, object]], None] | None = None,
    translation_provider_factory: Callable[[], Any] | None = None,
    structured_provider_factory: Callable[[], Any] | None = None,
    embedding_profile: EmbeddingProfile | None = None,
    embedding_provider_factory: Callable[[EmbeddingProfile, Any], Any] | None = None,
    credential_store: Any = None,
    obsidian_job_service: Any = None,
    obsidian_exporter: Any = None,
    obsidian_enabled: bool = False,
    environment_snapshot: dict[str, str] | None = None,
    legacy_settings_path: Path | None = None,
    allow_legacy_credential_fallback: bool = False,
    obsidian_auto_export_debounce_seconds: float = 1.0,
) -> ApplicationContainer:
    """Compose, but never start, the single stage-specific processing worker."""
    verify_schema_revision(database_settings, required_schema_revision)
    if required_schema_revision in _P3_REVISIONS:
        _validate_p3_worker_configuration(
            translation_provider_factory=translation_provider_factory,
            structured_provider_factory=structured_provider_factory,
            embedding_profile=embedding_profile,
            embedding_provider_factory=embedding_provider_factory,
        )
    current_time = clock or (lambda: datetime.now(timezone.utc))
    if not obsidian_enabled and (
        obsidian_job_service is not None or obsidian_exporter is not None
    ):
        raise ValueError("Obsidian worker dependencies require obsidian_enabled")
    session_factory = create_async_session_factory(database_settings)
    work_factory = lambda: SqlAlchemyUnitOfWork(session_factory)
    native_provider = native_provider_factory()
    generation_provider = (
        generation_provider_factory()
        if required_schema_revision not in _P3_REVISIONS
        else None
    )
    production_ocr_registry = create_production_ocr_registry()
    source_processor = SourceDocumentProcessor(
        work_factory,
        native_factory=lambda: build_native_source_processor(
            native_provider,
            clock=current_time,
        ),
        ocr_factory=lambda: _ContractGatedOcrProcessor(production_ocr_registry),
        clock=current_time,
    )
    artifact_generator = None
    document_artifacts = None
    document_search = None
    p3_translation_provider = None
    p3_structured_provider = None
    p3_embedding_handler = None
    p3_context_builder = None
    obsidian_auto_export = None
    obsidian_startup_reconciler = None
    if required_schema_revision not in _P3_REVISIONS:
        artifact_generator = ArtifactGenerator(
            work_factory,
            generation_provider,
            clock=current_time,
            artifact_id_factory=lambda: f"art_{uuid4().hex}",
            job_id_factory=lambda: f"job_{uuid4().hex}",
        )
    else:
        effective_environment = (
            dict(os.environ)
            if environment_snapshot is None
            else dict(environment_snapshot)
        )
        repository_root = Path(__file__).resolve().parents[2]
        resolved_settings_path = (
            legacy_settings_path
            if legacy_settings_path is not None
            else repository_root / "data" / "settings.json"
        )
        effective_environment["PAPER_STUDY_SETTINGS_PATH"] = str(resolved_settings_path)
        p3_credential_store = credential_store or CompositeCredentialStore(
            EnvironmentCredentialStore(effective_environment),
            KeyringCredentialStore(None),
            LegacySettingsCredentialStore(resolved_settings_path),
            allow_legacy_fallback=allow_legacy_credential_fallback,
        )
        p3_settings_service = SettingsService(
            settings_path=resolved_settings_path,
            root=repository_root,
            credential_service=CredentialService(
                p3_credential_store,
                SafeCredentialProbe(),
            ),
            environment_snapshot=effective_environment,
            rollout_snapshot=RolloutSettings(obsidian_enabled=obsidian_enabled),
            default_dirs={
                "pdfDir": repository_root / "data" / "pdfs",
                "explainerDir": repository_root / "data" / "explainers",
                "translationDir": repository_root / "data" / "translations",
                "ocrMarkdownDir": repository_root / "data" / "ocr_markdown",
                "reproductionDir": repository_root / "data" / "reproduction-artifacts",
            },
        )
        settings_service = p3_settings_service
        translation_provider_factory = _bind_p3_credential_resolver(
            translation_provider_factory,
            p3_credential_store,
            runtime_config_resolver=p3_settings_service.llm_runtime_settings,
        )
        structured_provider_factory = _bind_p3_credential_resolver(
            structured_provider_factory,
            p3_credential_store,
            runtime_config_resolver=p3_settings_service.llm_runtime_settings,
        )
        if obsidian_enabled:
            if (obsidian_job_service is None) != (obsidian_exporter is None):
                raise ValueError(
                    "Obsidian worker composition requires both job service and exporter"
                )
            if obsidian_job_service is None:
                credential_service = p3_settings_service.credential_service
                pdf_files = PdfFiles(
                    root=repository_root,
                    default_directory=repository_root / "data" / "pdfs",
                    seed_directory=repository_root / "paper",
                )
                library_queries = LibraryQueries(
                    work_factory,
                    pdf_files=pdf_files,
                )
                obsidian_job_service = ObsidianJobService(
                    work_factory,
                    settings_service=settings_service,
                    library_queries=library_queries,
                    clock=current_time,
                )
                obsidian_exporter = ObsidianSpecExporter(
                    work_factory,
                    session_factory,
                    pdf_files=pdf_files,
                    clock=current_time,
                )
                obsidian_auto_export = ObsidianAutoExportPolicy(
                    work_factory,
                    settings_service=settings_service,
                    job_service=obsidian_job_service,
                    clock=current_time,
                    debounce_seconds=obsidian_auto_export_debounce_seconds,
                    logger=logger,
                )
                obsidian_startup_reconciler = ObsidianStartupReconciler(
                    work_factory,
                    settings_service=settings_service,
                    library_queries=library_queries,
                    job_service=obsidian_job_service,
                    logger=logger,
                )
        (
            document_artifacts,
            document_search,
            p3_translation_provider,
            p3_structured_provider,
        ) = _compose_p3_services(
            work_factory,
            session_factory,
            p3_credential_store,
            clock=current_time,
            translation_provider_factory=translation_provider_factory,
            structured_provider_factory=structured_provider_factory,
            embedding_profile=embedding_profile,
            embedding_provider_factory=embedding_provider_factory,
            auto_export=obsidian_auto_export,
            auto_export_logger=logger,
            translation_mode_resolver=p3_settings_service.translation_mode,
        )
        assert embedding_provider_factory is not None
        p3_context_builder = ContextBuilder(work_factory)
        p3_embedding_handler = EmbeddingJobHandler(
            SqlAlchemyDocumentSearchRepository(session_factory),
            context_builder=p3_context_builder,
            credential_store=p3_credential_store,
            provider_factory=embedding_provider_factory,
            clock=current_time,
        )

    async def process_source(lease: Any) -> ProcessingHandlerOutcome:
        spec = lease.spec.value
        if not isinstance(spec, (SourceMaterializeJobSpecV1, OcrJobSpecV1)):
            raise TypeError("source handler received a non-source job spec")
        await source_processor.process(lease, spec.source_document_id)
        return ProcessingHandlerOutcome.settled()

    async def process_explainer(lease: Any) -> ProcessingHandlerOutcome:
        spec = lease.spec.value
        if not isinstance(spec, ExplainJobSpecV1):
            raise TypeError("explainer handler received a non-explainer job spec")
        await artifact_generator.generate_explainer(
            lease,
            spec.source_document_id,
        )
        return ProcessingHandlerOutcome.settled()

    async def process_p3_artifact(lease: Any) -> ProcessingHandlerOutcome:
        spec = lease.spec.value
        if not isinstance(spec, (TranslateJobSpecV1, ExplainJobSpecV1)):
            raise TypeError("P3 artifact handler received an invalid job spec")
        if document_artifacts is None:
            raise RuntimeError("P3 artifact service is not configured")
        if p3_context_builder is None:
            raise RuntimeError("P3 context builder is not configured")
        await p3_context_builder.materialize_chunks(
            spec.source_document_id,
            ChunkingSpec(),
            now=current_time(),
        )
        await document_artifacts.run(lease, spec.artifact_id)
        return ProcessingHandlerOutcome.settled()

    handlers: dict[str, Any] = {
        "source_materialize": process_source,
        "ocr": process_source,
    }
    if required_schema_revision in _P3_REVISIONS:
        if p3_embedding_handler is None:
            raise RuntimeError("P3 embedding handler is not configured")
        handlers.update(
            {
                "translate": process_p3_artifact,
                "explain": process_p3_artifact,
                "embed": p3_embedding_handler,
            }
        )
        if obsidian_job_service is not None or obsidian_exporter is not None:
            if obsidian_job_service is None or obsidian_exporter is None:
                raise ValueError(
                    "Obsidian worker composition requires both job service and exporter"
                )
            obsidian_handler = ObsidianJobHandler(
                obsidian_job_service,
                exporter=obsidian_exporter,
            )
            handlers.update(
                {
                    "obsidian_export": obsidian_handler,
                    "obsidian_sync": obsidian_handler,
                }
            )
    else:
        handlers["explain"] = process_explainer

    processing_worker = ProcessingWorker(
        work_factory,
        handlers=handlers,
        worker_id=worker_id,
        clock=current_time,
        logger=logger,
        claim_job_types=(
            frozenset(handlers)
            if required_schema_revision in _P3_REVISIONS and not obsidian_enabled
            else None
        ),
    )
    return ApplicationContainer(
        schema_revision=required_schema_revision,
        session_factory=session_factory,
        native_provider=native_provider,
        generation_provider=generation_provider,
        source_processor=source_processor,
        artifact_generator=artifact_generator,
        processing_worker=processing_worker,
        document_artifacts=document_artifacts,
        document_search=document_search,
        embedding_profile=embedding_profile,
        p3_translation_provider=p3_translation_provider,
        p3_structured_provider=p3_structured_provider,
        obsidian_jobs=obsidian_job_service,
        obsidian_auto_export=obsidian_auto_export,
        obsidian_startup_reconciler=obsidian_startup_reconciler,
    )


def _default_credential_store() -> Any:
    return CompositeCredentialStore(
        EnvironmentCredentialStore(dict(os.environ)),
        KeyringCredentialStore(None),
        LegacySettingsCredentialStore(Path(__file__).resolve().parents[2] / "data" / "settings.json"),
    )


def _validate_p3_api_configuration(
    *,
    translation_provider_factory: Callable[[], Any] | None,
    structured_provider_factory: Callable[[], Any] | None,
) -> None:
    """Reject incomplete P3 API composition before allocating an async engine."""

    if (
        not callable(translation_provider_factory)
        or not callable(structured_provider_factory)
    ):
        raise ValueError(
            "P3 composition requires explicit translation_provider_factory and "
            "structured_provider_factory"
        )


def _validate_p3_worker_configuration(
    *,
    translation_provider_factory: Callable[[], Any] | None,
    structured_provider_factory: Callable[[], Any] | None,
    embedding_profile: EmbeddingProfile | None,
    embedding_provider_factory: Callable[[EmbeddingProfile, Any], Any] | None,
) -> None:
    """Reject incomplete P3 worker composition before allocating an async engine."""

    if (
        not callable(translation_provider_factory)
        or not callable(structured_provider_factory)
    ):
        raise ValueError(
            "P3 composition requires explicit translation_provider_factory and "
            "structured_provider_factory"
        )
    if not isinstance(embedding_profile, EmbeddingProfile):
        raise ValueError("P3 worker requires an explicit embedding_profile")
    if not callable(embedding_provider_factory):
        raise ValueError("P3 worker requires an explicit embedding_provider_factory")


def _bind_p3_credential_resolver(
    factory: Callable[[], Any] | None,
    credential_store: Any,
    *,
    runtime_config_resolver: Callable[[], Any] | None = None,
    environment: Mapping[str, str] | None = None,
) -> Callable[[], Any] | None:
    """Inject the one backend credential seam into durable P3 providers.

    Compatibility factories are intentionally allowed to remain unaware of
    credentials.  Providers that opt into the seam resolve per request, while
    test/custom providers keep their original zero-argument composition.
    """
    if not callable(factory):
        return factory

    def bound() -> Any:
        provider = factory()
        binder = getattr(provider, "bind_credential_resolver", None)
        if callable(binder):
            binder(credential_store.get)
        runtime_binder = getattr(provider, "bind_runtime_config_resolver", None)
        if callable(runtime_binder):
            resolver = runtime_config_resolver
            if resolver is None and environment is not None:
                resolver = legacy_p3_runtime_config_resolver(environment)
            if resolver is not None:
                runtime_binder(resolver)
        return provider

    return bound


def _compose_p3_services(
    work_factory: Callable[[], Any],
    session_factory: Any,
    credential_store: Any,
    *,
    clock: Callable[[], datetime],
    translation_provider_factory: Callable[[], Any] | None,
    structured_provider_factory: Callable[[], Any] | None,
    embedding_profile: EmbeddingProfile | None,
    embedding_provider_factory: Callable[[EmbeddingProfile, Any], Any] | None,
    query_embedding_provider_factory: Callable[[EmbeddingProfile, Any], Any] | None = None,
    auto_export: Any = None,
    auto_export_logger: Callable[[dict[str, object]], None] | None = None,
    translation_mode_resolver: Callable[[], str] | None = None,
) -> tuple[Any, Any, Any, Any]:
    if translation_provider_factory is None or structured_provider_factory is None:
        raise ValueError(
            "P3 composition requires explicit translation_provider_factory and "
            "structured_provider_factory"
        )
    translation_provider = translation_provider_factory()
    structured_provider = structured_provider_factory()
    context_builder = ContextBuilder(work_factory)
    checkpoints = SqlAlchemyTranslationCheckpointRepository(
        session_factory,
        clock=clock,
    )
    artifacts = DocumentArtifactService(
        work_factory,
        context_builder=context_builder,
        clock=clock,
        translation_provider=translation_provider,
        checkpoint_repository=checkpoints,
        structured_provider=structured_provider,
        translation_mode_resolver=translation_mode_resolver,
        auto_export=auto_export,
        auto_export_logger=auto_export_logger,
    )
    repository = SqlAlchemyDocumentSearchRepository(session_factory)
    query_provider = None
    if query_embedding_provider_factory is not None and embedding_profile is not None:
        credential = None
        query_provider = query_embedding_provider_factory(embedding_profile, credential)
    search = DocumentSearch(
        repository,
        context_builder=context_builder,
        index_embedding_profile=embedding_profile,
        query_embedding_profile=embedding_profile if query_provider is not None else None,
        query_embedding_provider=query_provider,
    )
    return artifacts, search, translation_provider, structured_provider


class _ContractGatedOcrProcessor:
    def __init__(self, registry: Any) -> None:
        self._registry = registry

    async def process(self, lease: Any, _source_id: str, *, work_factory: Any) -> None:
        del work_factory
        spec = lease.spec.value
        if not isinstance(spec, OcrJobSpecV1):
            raise TypeError("OCR processor received a non-OCR job spec")
        # The production registry has no concrete adapter until the external
        # provider contract is independently verified.  Resolution fails
        # before credential lookup, transport construction, PDF reads, or I/O.
        self._registry.resolve(spec.provider)
        raise OcrProviderContractUnverifiedError()
