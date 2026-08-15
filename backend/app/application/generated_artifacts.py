from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
from pathlib import Path

from backend.app.application.ports.artifact_generator import (
    ArtifactGenerator as ArtifactGenerationProvider,
    PaperMetadata,
)
from backend.app.application.ports.unit_of_work import UnitOfWork
from backend.app.application.obsidian_auto_export import notify_artifact_ready
from backend.app.canonical_text import normalize_canonical_text
from backend.app.domain import (
    ArtifactKind,
    ArtifactKindUnsupportedError,
    ArtifactVersionIdentity,
    DomainError,
    EmptyArtifactError,
    GeneratedArtifact,
    GenerationFailureError,
    PersistenceConflictError,
    SourceDocumentStatus,
    SourceMode,
)
from backend.app.domain.processing import (
    ExplainJobSpecV1,
    JobLease,
    JobSpecValidationError,
    NewProcessingJob,
    build_artifact_job_key,
    build_artifact_key,
    encode_job_spec_v1,
    hash_job_spec,
)


@dataclass(frozen=True, slots=True)
class ArtifactReadResult:
    kind: ArtifactKind
    content: str | None
    provenance: str
    artifact_id: str | None
    source_document_id: str | None


@dataclass(frozen=True, slots=True)
class ExplainerEnqueueResult:
    artifact: GeneratedArtifact
    job: NewProcessingJob
    deduplicated: bool


class GenerationPipeline:
    def __init__(
        self,
        unit_of_work_factory: Callable[[], UnitOfWork],
        source_pipeline: object,
        generator: ArtifactGenerationProvider,
        *,
        clock: Callable[[], datetime],
        id_factory: Callable[[], str],
        mirror: Callable[[str, str, str], None] | None = None,
    ) -> None:
        self._unit_of_work_factory = unit_of_work_factory
        self._source_pipeline = source_pipeline
        self._generator = generator
        self._clock = clock
        self._id_factory = id_factory
        self._mirror = mirror

    async def generate_artifact(
        self,
        paper_id: str,
        artifact_kind: ArtifactKind | str,
        source_mode: SourceMode | str,
    ) -> GeneratedArtifact:
        kind = ArtifactKind(artifact_kind)
        if kind not in {ArtifactKind.EXPLAINER, ArtifactKind.TRANSLATION}:
            raise ArtifactKindUnsupportedError(artifact_kind=kind.value)
        mode = SourceMode(source_mode)
        source = await self._source_pipeline.materialize_source(
            paper_id,
            mode.value,
            f"artifact:{kind.value}",
        )
        identity_value = self._generator.identity(kind.value)
        identity = ArtifactVersionIdentity(
            source_document_id=source.id,
            kind=kind,
            generator_provider=identity_value.provider,
            generator_model=identity_value.model,
            prompt_version=identity_value.prompt_version,
        )
        async with self._unit_of_work_factory() as work:
            existing = await work.artifacts.find_by_version_identity(identity)
            paper = await work.papers.get(paper_id)
        if existing is not None and existing.status.value == "ready":
            return existing
        if paper is None:
            raise GenerationFailureError(paper_id=paper_id)
        metadata = PaperMetadata(
            id=paper.id,
            title=paper.title,
            authors=paper.authors,
            abstract=paper.abstract,
        )

        try:
            content = self._generator.generate(kind.value, metadata, source.markdown or "")
        except Exception:
            error = GenerationFailureError(paper_id=paper_id, artifact_kind=kind.value)
            await self._persist_failure(identity, paper_id, source.id, existing, error)
            raise error from None
        if not isinstance(content, str) or not content.strip():
            error = EmptyArtifactError(paper_id=paper_id, artifact_kind=kind.value)
            await self._persist_failure(identity, paper_id, source.id, existing, error)
            raise error
        content = normalize_canonical_text(content)

        now = _utc(self._clock())
        artifact = GeneratedArtifact(
            id=existing.id if existing is not None else self._id_factory(),
            paper_id=paper_id,
            kind=kind,
            source_document_id=source.id,
            status="ready",
            generator_provider=identity.generator_provider,
            generator_model=identity.generator_model,
            prompt_version=identity.prompt_version,
            created_at=existing.created_at if existing is not None else now,
            updated_at=now,
            content=content,
            content_sha256=hashlib.sha256(content.encode("utf-8")).hexdigest(),
        )
        published = await self._publish_success(identity, artifact, existing)
        if self._mirror is not None:
            try:
                self._mirror(kind.value, paper_id, published.content or "")
            except Exception:
                pass
        return published

    async def _publish_success(
        self,
        identity: ArtifactVersionIdentity,
        artifact: GeneratedArtifact,
        existing: GeneratedArtifact | None,
    ) -> GeneratedArtifact:
        try:
            async with self._unit_of_work_factory() as work:
                if existing is None:
                    await work.artifacts.add(artifact)
                else:
                    changed = await work.artifacts.publish_ready(
                        existing.id,
                        "failed",
                        artifact.content or "",
                        artifact.content_sha256 or "",
                        artifact.updated_at,
                    )
                    if not changed:
                        raise PersistenceConflictError(operation="publish_generated_artifact")
                await _write_legacy(work, artifact)
                await work.commit()
            return artifact
        except PersistenceConflictError:
            async with self._unit_of_work_factory() as work:
                winner = await work.artifacts.find_by_version_identity(identity)
            if winner is not None and winner.status.value == "ready":
                return winner
            if winner is not None and winner.status.value == "failed" and existing is None:
                retry = GeneratedArtifact(
                    id=winner.id,
                    paper_id=artifact.paper_id,
                    kind=artifact.kind,
                    source_document_id=artifact.source_document_id,
                    status="ready",
                    generator_provider=artifact.generator_provider,
                    generator_model=artifact.generator_model,
                    prompt_version=artifact.prompt_version,
                    created_at=winner.created_at,
                    updated_at=artifact.updated_at,
                    content=artifact.content,
                    content_sha256=artifact.content_sha256,
                )
                return await self._publish_success(identity, retry, winner)
            raise

    async def _persist_failure(
        self,
        identity: ArtifactVersionIdentity,
        paper_id: str,
        source_document_id: str,
        existing: GeneratedArtifact | None,
        error: DomainError,
    ) -> None:
        if existing is not None:
            return
        now = _utc(self._clock())
        failed = GeneratedArtifact(
            id=self._id_factory(),
            paper_id=paper_id,
            kind=identity.kind,
            source_document_id=source_document_id,
            status="failed",
            generator_provider=identity.generator_provider,
            generator_model=identity.generator_model,
            prompt_version=identity.prompt_version,
            created_at=now,
            updated_at=now,
            error_code=error.code,
            error_message=error.public_message,
        )
        try:
            async with self._unit_of_work_factory() as work:
                current = await work.artifacts.find_by_version_identity(identity)
                if current is None:
                    await work.artifacts.add(failed)
                    await work.commit()
        except PersistenceConflictError:
            return


class ArtifactGenerator:
    """Lease-aware P2 explainer application service."""

    def __init__(
        self,
        unit_of_work_factory: Callable[[], UnitOfWork],
        provider: ArtifactGenerationProvider,
        *,
        clock: Callable[[], datetime],
        artifact_id_factory: Callable[[], str],
        job_id_factory: Callable[[], str],
        auto_export: object | None = None,
        auto_export_logger: Callable[[dict[str, object]], None] | None = None,
    ) -> None:
        self._unit_of_work_factory = unit_of_work_factory
        self._provider = provider
        self._clock = clock
        self._artifact_id_factory = artifact_id_factory
        self._job_id_factory = job_id_factory
        self._auto_export = auto_export
        self._auto_export_logger = auto_export_logger

    async def enqueue_explainer(
        self,
        paper_id: str,
        *,
        sourceMode: str,
        sourceDocumentId: str,
        profile: str = "standard",
    ) -> ExplainerEnqueueResult:
        async with self._unit_of_work_factory() as work:
            source = await work.sources.get(sourceDocumentId)
            paper = await work.papers.get(paper_id)
        if source is None or source.paper_id != paper_id:
            raise JobSpecValidationError(
                "explainer source must belong to the requested paper"
            )
        if source.mode.value != sourceMode:
            raise JobSpecValidationError(
                "sourceMode must match the persisted source document"
            )
        if (
            source.status is not SourceDocumentStatus.READY
            or source.markdown is None
            or source.content_sha256 is None
        ):
            raise JobSpecValidationError("explainer source must be ready")
        if paper is None or paper.pdf_path is None:
            raise JobSpecValidationError("explainer source PDF is unavailable")
        try:
            current_pdf_sha256 = hashlib.sha256(Path(paper.pdf_path).read_bytes()).hexdigest()
        except OSError:
            raise JobSpecValidationError("explainer source PDF is unavailable") from None
        if current_pdf_sha256 != source.pdf_sha256:
            raise JobSpecValidationError("explainer source PDF is stale")
        identity = self._provider.identity("explainer", profile)
        version_identity = ArtifactVersionIdentity(
            source_document_id=source.id,
            kind="explainer",
            generator_provider=identity.provider,
            generator_model=identity.model,
            prompt_version=identity.prompt_version,
        )
        async with self._unit_of_work_factory() as work:
            existing_artifact = await work.artifacts.find_by_version_identity(
                version_identity
            )
        now = _utc(self._clock())
        artifact = GeneratedArtifact(
            id=(
                existing_artifact.id
                if existing_artifact is not None
                else self._artifact_id_factory()
            ),
            paper_id=paper_id,
            kind="explainer",
            source_document_id=source.id,
            status="queued",
            generator_provider=identity.provider,
            generator_model=identity.model,
            prompt_version=identity.prompt_version,
            created_at=(
                existing_artifact.created_at
                if existing_artifact is not None
                else now
            ),
            updated_at=now,
        )
        spec = ExplainJobSpecV1(
            paper_id=paper_id,
            source_document_id=source.id,
            artifact_id=artifact.id,
            profile=profile,
            provider=identity.provider,
            model=identity.model,
            prompt_version=identity.prompt_version,
            source_mode=sourceMode,
        )
        spec_json = encode_job_spec_v1(spec)
        spec_sha256 = hash_job_spec(spec_json)
        artifact_key = build_artifact_key(
            kind="explainer",
            source_document_id=source.id,
            source_content_sha256=source.content_sha256,
            generator_provider=identity.provider,
            generator_model=identity.model,
            prompt_version=identity.prompt_version,
            kind_specific_options={"profile": profile},
        )
        job = NewProcessingJob(
            id=self._job_id_factory(),
            spec=spec,
            idempotency_key=build_artifact_job_key(artifact_key, spec_sha256),
            created_at=now,
        )
        async with self._unit_of_work_factory() as work:
            enqueue = await work.artifacts.enqueue_with_job(
                artifact,
                job,
                spec_json=spec_json,
                spec_sha256=spec_sha256,
            )
            try:
                commit_pdf_sha256 = hashlib.sha256(
                    Path(paper.pdf_path).read_bytes()
                ).hexdigest()
            except OSError:
                raise JobSpecValidationError(
                    "explainer source PDF is unavailable"
                ) from None
            if commit_pdf_sha256 != source.pdf_sha256:
                raise JobSpecValidationError("explainer source PDF is stale")
            await work.commit()
        return ExplainerEnqueueResult(
            artifact=existing_artifact or artifact,
            job=enqueue.job,
            deduplicated=enqueue.deduplicated,
        )

    async def generate_explainer(
        self,
        lease: JobLease,
        source_id: str,
    ) -> GeneratedArtifact:
        spec = lease.spec.value
        if not isinstance(spec, ExplainJobSpecV1) or spec.source_document_id != source_id:
            raise JobSpecValidationError("explainer lease does not bind the source")
        async with self._unit_of_work_factory() as work:
            source = await work.sources.get(source_id)
            paper = await work.papers.get(spec.paper_id)
            artifact = await work.artifacts.get(spec.artifact_id)
            expected_head_artifact_id = await work.artifacts.get_head_artifact_id(
                paper_id=spec.paper_id,
                kind="explainer",
            )
        if (
            source is None
            or source.status is not SourceDocumentStatus.READY
            or source.paper_id != spec.paper_id
            or source.mode.value != spec.source_mode
            or source.markdown is None
            or source.content_sha256 is None
            or paper is None
            or paper.pdf_path is None
            or artifact is None
            or artifact.status.value != "running"
            or artifact.paper_id != spec.paper_id
            or artifact.source_document_id != source_id
        ):
            raise JobSpecValidationError("explainer lease target is not publishable")
        try:
            current_pdf_sha256 = hashlib.sha256(Path(paper.pdf_path).read_bytes()).hexdigest()
        except OSError:
            raise JobSpecValidationError("explainer source PDF is unavailable") from None
        if current_pdf_sha256 != source.pdf_sha256:
            raise JobSpecValidationError("explainer source PDF is stale")

        metadata = PaperMetadata(
            id=paper.id,
            title=paper.title,
            authors=paper.authors,
            abstract=paper.abstract,
        )
        try:
            content = self._provider.generate(
                "explainer",
                metadata,
                source.markdown,
                spec.profile,
            )
        except Exception:
            raise GenerationFailureError(
                paper_id=spec.paper_id,
                artifact_kind="explainer",
            ) from None
        if not isinstance(content, str) or not content.strip():
            raise EmptyArtifactError(
                paper_id=spec.paper_id,
                artifact_kind="explainer",
            )
        content = normalize_canonical_text(content)
        content_sha256 = hashlib.sha256(content.encode("utf-8")).hexdigest()
        try:
            publish_pdf_sha256 = hashlib.sha256(Path(paper.pdf_path).read_bytes()).hexdigest()
        except OSError:
            raise JobSpecValidationError("explainer source PDF is unavailable") from None
        if publish_pdf_sha256 != source.pdf_sha256:
            raise JobSpecValidationError("explainer source PDF is stale")
        now = _utc(self._clock())
        async with self._unit_of_work_factory() as work:
            published = await work.artifacts.publish_explainer(
                lease=lease,
                source_document_id=source.id,
                expected_source_mode=source.mode.value,
                expected_source_pdf_sha256=source.pdf_sha256,
                expected_source_content_sha256=source.content_sha256,
                expected_source_processing_version=source.processing_version,
                artifact_id=artifact.id,
                expected_head_artifact_id=expected_head_artifact_id,
                content=content,
                content_sha256=content_sha256,
                updated_at=now,
            )
            await work.commit()
        await notify_artifact_ready(
            self._auto_export,
            paper_id=published.paper_id,
            artifact_id=published.id,
            committed_at=now,
            logger=self._auto_export_logger,
        )
        return published


class ArtifactReader:
    def __init__(
        self,
        unit_of_work_factory: Callable[[], UnitOfWork],
        read_mode: str,
    ) -> None:
        if read_mode not in {"legacy", "prefer_new"}:
            raise ValueError("read_mode must be legacy or prefer_new")
        self._unit_of_work_factory = unit_of_work_factory
        self._read_mode = read_mode

    async def read(
        self,
        paper_id: str,
        artifact_kind: ArtifactKind | str,
    ) -> ArtifactReadResult:
        kind = ArtifactKind(artifact_kind)
        if kind not in {ArtifactKind.EXPLAINER, ArtifactKind.TRANSLATION}:
            raise ArtifactKindUnsupportedError(artifact_kind=kind.value)
        async with self._unit_of_work_factory() as work:
            if self._read_mode == "prefer_new":
                artifact = await work.artifacts.find_ready_for_paper(paper_id, kind)
                if artifact is not None:
                    return ArtifactReadResult(
                        kind=kind,
                        content=artifact.content,
                        provenance="new",
                        artifact_id=artifact.id,
                        source_document_id=artifact.source_document_id,
                    )
            legacy = await work.artifacts.read_legacy(paper_id, kind)
        return ArtifactReadResult(
            kind=kind,
            content=legacy,
            provenance="legacy",
            artifact_id=None,
            source_document_id=None,
        )


async def _write_legacy(work: UnitOfWork, artifact: GeneratedArtifact) -> None:
    if artifact.kind is ArtifactKind.EXPLAINER:
        await work.artifacts.write_legacy_explainer(
            artifact.paper_id,
            artifact.content or "",
            artifact.updated_at,
        )
    else:
        await work.artifacts.write_legacy_translation(
            artifact.paper_id,
            artifact.content or "",
            artifact.updated_at,
        )


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("clock must return a timezone-aware datetime")
    return value.astimezone(timezone.utc)
