from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
from uuid import uuid4

from backend.app.application.context_builder import ContextBuilder
from backend.app.application.obsidian_auto_export import notify_artifact_ready
from backend.app.application.ports.translation_provider import (
    TranslationProvider,
    TranslationRequest,
)
from backend.app.application.ports.structured_artifact_provider import (
    StructuredArtifactInput,
    StructuredArtifactProvider,
    StructuredArtifactRequest,
)
from backend.app.domain import (
    ArtifactOutputInvalidError,
    ContextCoverageInvalidError,
    ArtifactKind,
    ArtifactKindUnsupportedError,
    GeneratedArtifact,
    GenerationFailureError,
    JobLeaseLostError,
    MarkdownStructureInvalidError,
    MissingPdfError,
    PersistenceConflictError,
    SourceModeMismatchError,
    SourceNotFoundError,
    SourceNotReadyError,
    SourceDocumentStatus,
    StaleSourceError,
    TranslationProviderRequestError,
    TranslationCheckpointConflictError,
    WorkerConfigurationError,
    has_frozen_native_source_identity,
)
from backend.app.domain.context import ContextRequest
from backend.app.domain.processing import ExplainJobSpecV1, JobLease, TranslateJobSpecV1
from backend.app.domain.processing import (
    JobProgress,
    NewProcessingJob,
    build_artifact_job_key,
    build_artifact_key,
    encode_job_spec_v1,
    hash_job_spec,
)
from backend.app.repositories.translation_checkpoints import (
    SqlAlchemyTranslationCheckpointRepository,
    TranslationCheckpoint,
)


_STRUCTURED_REDUCE_FAN_IN = 8


class DocumentArtifactService:
    """Run P3 artifact consumers exclusively from an audited ContextPlan."""

    def __init__(
        self,
        unit_of_work_factory,
        *,
        context_builder: ContextBuilder,
        clock: Callable[[], datetime],
        translation_provider: TranslationProvider | None = None,
        checkpoint_repository: SqlAlchemyTranslationCheckpointRepository | None = None,
        structured_provider: StructuredArtifactProvider | None = None,
        translation_mode_resolver: Callable[[], str] | None = None,
        artifact_id_factory: Callable[[], str] | None = None,
        job_id_factory: Callable[[], str] | None = None,
        auto_export: object | None = None,
        auto_export_logger: Callable[[dict[str, object]], None] | None = None,
    ) -> None:
        self._work_factory = unit_of_work_factory
        self._context_builder = context_builder
        self._translation_provider = translation_provider
        self._checkpoints = checkpoint_repository
        self._structured_provider = structured_provider
        self._translation_mode_resolver = translation_mode_resolver
        self._clock = clock
        self._artifact_id_factory = artifact_id_factory or (
            lambda: f"artifact_{uuid4().hex}"
        )
        self._job_id_factory = job_id_factory or (lambda: f"job_{uuid4().hex}")
        self._auto_export = auto_export
        self._auto_export_logger = auto_export_logger

    async def enqueue(
        self,
        paper_id: str,
        source_document_id: str,
        source_mode: str,
        kind: str,
        *,
        profile: str = "standard",
        now: datetime,
    ) -> "DocumentArtifactEnqueueResult":
        try:
            artifact_kind = ArtifactKind(kind)
        except (TypeError, ValueError) as error:
            raise ArtifactKindUnsupportedError(artifact_kind=kind) from error
        if artifact_kind not in {
            ArtifactKind.EXPLAINER,
            ArtifactKind.TRANSLATION,
            ArtifactKind.CLASSIFICATION,
            ArtifactKind.METADATA,
            ArtifactKind.SUMMARY,
        }:
            raise ArtifactKindUnsupportedError(artifact_kind=artifact_kind.value)
        if profile not in {"standard", "deep"}:
            raise ValueError("profile must be standard or deep")
        if artifact_kind is not ArtifactKind.EXPLAINER and profile != "standard":
            raise ValueError("only explainer supports the deep profile")
        now = _utc(now)
        async with self._work_factory() as work:
            source = await work.sources.get(source_document_id)
            paper = await work.papers.get(paper_id)
        if source is None or source.paper_id != paper_id:
            raise SourceNotFoundError(paper_id=paper_id)
        if source.mode.value != source_mode:
            raise SourceModeMismatchError(paper_id=paper_id, source_mode=source_mode)
        if source.mode.value == "native" and not has_frozen_native_source_identity(source):
            raise StaleSourceError(paper_id=paper_id)
        if source.status is SourceDocumentStatus.STALE:
            raise StaleSourceError(paper_id=paper_id)
        if (
            source.status is not SourceDocumentStatus.READY
            or source.markdown is None
            or source.content_sha256 is None
        ):
            raise SourceNotReadyError(paper_id=paper_id)
        if paper is None:
            raise SourceNotFoundError(paper_id=paper_id)
        if paper.pdf_path is None:
            raise MissingPdfError(paper_id=paper_id)
        try:
            current_pdf_sha256 = hashlib.sha256(Path(paper.pdf_path).read_bytes()).hexdigest()
        except OSError:
            raise MissingPdfError(paper_id=paper_id) from None
        if current_pdf_sha256 != source.pdf_sha256:
            raise StaleSourceError(paper_id=paper_id)
        if artifact_kind is ArtifactKind.TRANSLATION:
            provider = self._translation_provider
            if provider is None:
                raise ValueError("translation provider is not configured")
            provider_id = provider.provider_id
            model_id = provider.model_id
            prompt_version = provider.prompt_version
            translation_mode = self._translation_mode()
            options = {
                "targetLanguage": "zh-CN",
                "chunkingVersion": "markdown-coverage-v1",
                "contextVersion": "context-plan-v1",
                "promptSchemaVersion": prompt_version,
            }
            if translation_mode != "chunked":
                options["translationMode"] = translation_mode
        else:
            provider = self._structured_provider
            if provider is None:
                raise ValueError("structured artifact provider is not configured")
            provider_id = provider.provider_id
            model_id = provider.model_id
            prompt_version = _structured_prompt_version(artifact_kind, profile)
            options = _artifact_options(artifact_kind, prompt_version, profile=profile)
        artifact_key = build_artifact_key(
            kind=artifact_kind.value,
            source_document_id=source.id,
            source_content_sha256=source.content_sha256,
            generator_provider=provider_id,
            generator_model=model_id,
            prompt_version=prompt_version,
            kind_specific_options=options,
        )
        async with self._work_factory() as work:
            existing = await work.artifacts.find_by_artifact_key(artifact_key)
        # Artifact/job identities must be reproducible before either write is
        # attempted.  A random artifact id would leak into the immutable job
        # spec and turn two simultaneous requests for the same artifact key
        # into incompatible jobs.  Existing pre-deterministic rows remain
        # readable for rollout compatibility.
        artifact_id = existing.id if existing is not None else _artifact_id_for_key(artifact_key)
        artifact = GeneratedArtifact(
            id=artifact_id,
            paper_id=paper_id,
            kind=artifact_kind,
            source_document_id=source.id,
            status="queued",
            generator_provider=provider_id,
            generator_model=model_id,
            prompt_version=prompt_version,
            created_at=existing.created_at if existing is not None else now,
            updated_at=now,
        )
        if artifact_kind is ArtifactKind.TRANSLATION:
            spec = TranslateJobSpecV1(
                paper_id=paper_id,
                source_document_id=source.id,
                artifact_id=artifact.id,
                mode=translation_mode,
                source_mode=source_mode,
            )
        else:
            spec = ExplainJobSpecV1(
                paper_id=paper_id,
                source_document_id=source.id,
                artifact_id=artifact.id,
                profile=profile,
                provider=provider_id,
                model=model_id,
                prompt_version=prompt_version,
                source_mode=source_mode,
            )
        spec_json = encode_job_spec_v1(spec)
        spec_sha256 = hash_job_spec(spec_json)
        job_key = build_artifact_job_key(artifact_key, spec_sha256)
        job = NewProcessingJob(
            id=_job_id_for_key(job_key),
            spec=spec,
            idempotency_key=job_key,
            created_at=now,
            max_attempts=3,
        )
        async with self._work_factory() as work:
            enqueue = await work.artifacts.enqueue_with_job(
                artifact,
                job,
                spec_json=spec_json,
                spec_sha256=spec_sha256,
                kind_specific_options=options,
                expected_pdf_sha256=current_pdf_sha256,
                pdf_path=Path(paper.pdf_path),
                expected_source_provider=source.provider,
                expected_source_model=source.model,
            )
            persisted = await work.artifacts.get(artifact.id)
            if persisted is None:
                raise PersistenceConflictError(operation="artifact_enqueue_missing")
            await work.commit()
        return DocumentArtifactEnqueueResult(
            artifact=persisted,
            job=enqueue.job,
            deduplicated=enqueue.deduplicated,
        )

    def _translation_mode(self) -> str:
        resolver = self._translation_mode_resolver
        if callable(resolver):
            try:
                mode = resolver()
                if mode in {"chunked", "full"}:
                    return mode
            except Exception:
                pass
        return "chunked"

    async def run(self, lease: JobLease, artifact_id: str) -> GeneratedArtifact:
        if not isinstance(lease, JobLease):
            raise ValueError("lease must be a JobLease")
        spec = lease.spec.value
        if spec.artifact_id != artifact_id:
            raise ValueError("artifact lease does not bind the artifact")
        if isinstance(spec, TranslateJobSpecV1):
            return await self._run_translation(lease, artifact_id, spec)
        if isinstance(spec, ExplainJobSpecV1):
            return await self._run_structured(lease, artifact_id, spec)
        raise ValueError("processing lease is not an artifact job")

    async def _run_translation(
        self,
        lease: JobLease,
        artifact_id: str,
        spec: TranslateJobSpecV1,
    ) -> GeneratedArtifact:
        if self._translation_provider is None or self._checkpoints is None:
            raise ValueError("translation dependencies are not configured")
        async with self._work_factory() as work:
            source = await work.sources.get(spec.source_document_id)
            artifact = await work.artifacts.get(artifact_id)
            expected_head_artifact_id = await work.artifacts.get_head_artifact_id(
                paper_id=spec.paper_id,
                kind="translation",
            )
        if (
            source is None
            or source.status is not SourceDocumentStatus.READY
            or source.markdown is None
            or source.content_sha256 is None
            or source.paper_id != spec.paper_id
            or source.mode.value != spec.source_mode
        ):
            raise PersistenceConflictError(operation="translation_source_not_ready")
        if (
            artifact is None
            or artifact.status is not SourceDocumentStatus.RUNNING
            or artifact.kind is not ArtifactKind.TRANSLATION
            or artifact.paper_id != spec.paper_id
            or artifact.source_document_id != source.id
        ):
            raise PersistenceConflictError(operation="translation_artifact_not_running")
        if (
            self._translation_provider.provider_id != artifact.generator_provider
            or self._translation_provider.model_id != artifact.generator_model
            or self._translation_provider.prompt_version != artifact.prompt_version
        ):
            raise PersistenceConflictError(operation="translation_provider_identity")

        plan = await self._context_builder.build(
            source.id,
            ContextRequest(
                source_document_id=source.id,
                consumer=ArtifactKind.TRANSLATION,
            ),
        )
        if (
            plan.all_chunk_ids != plan.eligible_chunk_ids
            or plan.all_chunk_ids != plan.selected_chunk_ids
            or len(plan.batches) != len(plan.all_chunk_ids)
            or not plan.batches
            or any(len(batch.chunks) != 1 for batch in plan.batches)
        ):
            raise PersistenceConflictError(operation="translation_context_coverage")

        if spec.mode == "full":
            return await self._run_full_translation(
                lease,
                source=source,
                artifact=artifact,
                plan=plan,
                expected_head_artifact_id=expected_head_artifact_id,
            )

        translated: list[str] = []
        await self._translation_progress_checkpoint(
            lease,
            completed=0,
            total=len(plan.batches),
        )
        for batch in plan.batches:
            chunk = batch.chunks[0]
            checkpoint = await self._checkpoints.read(artifact_id, batch.sequence)
            if checkpoint is not None:
                _validated_checkpoint_identity(
                    checkpoint,
                    artifact=artifact,
                    chunk_id=chunk.id,
                    sequence=batch.sequence,
                    source_content_sha256=plan.source_content_sha256,
                )
                if checkpoint.status == "succeeded":
                    translated.append(
                        _validated_checkpoint(
                            checkpoint,
                            artifact=artifact,
                            chunk_id=chunk.id,
                            sequence=batch.sequence,
                            source_content_sha256=plan.source_content_sha256,
                        )
                    )
                    await self._translation_progress_checkpoint(
                        lease,
                        completed=batch.sequence + 1,
                        total=len(plan.batches),
                    )
                    continue
            if chunk.content_kind == "verbatim" or not chunk.content.strip():
                # Verbatim and separator chunks are source-owned bytes.  They
                # are checkpointed under the same fence as provider results,
                # but never cross the external boundary.
                result = chunk.content
                await self._checkpoints.save_success(
                    lease=lease,
                    artifact_id=artifact_id,
                    chunk_id=chunk.id,
                    sequence=batch.sequence,
                    source_content_sha256=plan.source_content_sha256,
                    provider=artifact.generator_provider,
                    model=artifact.generator_model,
                    prompt_version=artifact.prompt_version,
                    translated_markdown=result,
                )
                translated.append(result)
                await self._translation_progress_checkpoint(
                    lease,
                    completed=batch.sequence + 1,
                    total=len(plan.batches),
                )
                continue
            request_markdown = chunk.content
            if chunk.content_kind == "structured":
                request_markdown, placeholders = _protect_structured_markdown(chunk.content)
            else:
                placeholders = None
            request = TranslationRequest(
                artifact_id=artifact_id,
                source_document_id=source.id,
                source_content_sha256=plan.source_content_sha256,
                chunk_id=chunk.id,
                sequence=batch.sequence,
                markdown=request_markdown,
                content_kind=chunk.content_kind or "text",
            )
            try:
                result = await self._translation_provider.translate(request)
            except Exception as error:
                typed_error = (
                    error
                    if isinstance(error, TranslationProviderRequestError)
                    else TranslationProviderRequestError(retryable=True)
                )
                await self._checkpoints.save_failure(
                    lease=lease,
                    artifact_id=artifact_id,
                    chunk_id=chunk.id,
                    sequence=batch.sequence,
                    source_content_sha256=plan.source_content_sha256,
                    provider=artifact.generator_provider,
                    model=artifact.generator_model,
                    prompt_version=artifact.prompt_version,
                    error_code=typed_error.code,
                )
                raise typed_error from None
            if not isinstance(result, str) or not result.strip():
                error = ArtifactOutputInvalidError(operation="translation_output")
                await self._checkpoints.save_failure(
                    lease=lease,
                    artifact_id=artifact_id,
                    chunk_id=chunk.id,
                    sequence=batch.sequence,
                    source_content_sha256=plan.source_content_sha256,
                    provider=artifact.generator_provider,
                    model=artifact.generator_model,
                    prompt_version=artifact.prompt_version,
                    error_code=error.code,
                )
                raise error
            if placeholders is not None:
                result = _restore_structured_markdown(
                    result,
                    placeholders,
                    source_markdown=chunk.content,
                )
            await self._checkpoints.save_success(
                lease=lease,
                artifact_id=artifact_id,
                chunk_id=chunk.id,
                sequence=batch.sequence,
                source_content_sha256=plan.source_content_sha256,
                provider=artifact.generator_provider,
                model=artifact.generator_model,
                prompt_version=artifact.prompt_version,
                translated_markdown=result,
            )
            translated.append(result)
            await self._translation_progress_checkpoint(
                lease,
                completed=batch.sequence + 1,
                total=len(plan.batches),
            )

        content = "".join(translated)
        if not content.strip():
            raise ValueError("translation assembled empty Markdown")
        return await self._publish_translation_result(
            lease,
            source=source,
            artifact=artifact,
            plan=plan,
            expected_head_artifact_id=expected_head_artifact_id,
            content=content,
            translation_mode="chunked",
        )

    async def _run_full_translation(
        self,
        lease: JobLease,
        *,
        source,
        artifact,
        plan,
        expected_head_artifact_id: str | None,
    ) -> GeneratedArtifact:
        await self._translation_progress_checkpoint(
            lease,
            completed=0,
            total=1,
            mode="full",
        )
        first_chunk = plan.batches[0].chunks[0]
        request = TranslationRequest(
            artifact_id=artifact.id,
            source_document_id=source.id,
            source_content_sha256=plan.source_content_sha256,
            chunk_id=first_chunk.id,
            sequence=0,
            markdown=source.markdown,
            content_kind="text",
        )
        try:
            content = await self._translation_provider.translate(request)
        except Exception as error:
            raise (
                error
                if isinstance(error, TranslationProviderRequestError)
                else TranslationProviderRequestError(retryable=True)
            ) from None
        if not isinstance(content, str) or not content.strip():
            raise ArtifactOutputInvalidError(operation="translation_output")
        await self._translation_progress_checkpoint(
            lease,
            completed=1,
            total=1,
            mode="full",
        )
        return await self._publish_translation_result(
            lease,
            source=source,
            artifact=artifact,
            plan=plan,
            expected_head_artifact_id=expected_head_artifact_id,
            content=content,
            translation_mode="full",
        )

    async def _publish_translation_result(
        self,
        lease: JobLease,
        *,
        source,
        artifact,
        plan,
        expected_head_artifact_id: str | None,
        content: str,
        translation_mode: str,
    ) -> GeneratedArtifact:
        content_sha256 = hashlib.sha256(content.encode("utf-8")).hexdigest()
        now = _utc(self._clock())
        async with self._work_factory() as work:
            published = await work.artifacts.publish_translation(
                lease=lease,
                source_document_id=source.id,
                expected_source_mode=source.mode.value,
                expected_source_pdf_sha256=source.pdf_sha256,
                expected_source_content_sha256=source.content_sha256,
                expected_source_processing_version=source.processing_version,
                expected_chunking_version=plan.chunking_version,
                expected_chunk_identities=tuple(
                    _chunk_publication_identity(batch.chunks[0])
                    for batch in plan.batches
                ),
                artifact_id=artifact.id,
                expected_head_artifact_id=expected_head_artifact_id,
                content=content,
                content_sha256=content_sha256,
                translation_mode=translation_mode,
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

    async def _translation_progress_checkpoint(
        self,
        lease: JobLease,
        *,
        completed: int,
        total: int,
        mode: str = "chunked",
    ) -> None:
        async with self._work_factory() as work:
            await work.jobs.report_progress(
                lease,
                JobProgress(
                    {
                        "stage": f"translation_{mode}",
                        "completed": completed,
                        "total": total,
                    }
                ),
                now=_utc(self._clock()),
            )
            await work.commit()
        async with self._work_factory() as work:
            if not await work.jobs.check_active(lease, now=_utc(self._clock())):
                raise JobLeaseLostError(operation="translation_progress_lease_lost")

    async def _structured_provider_checkpoint(self, lease: JobLease) -> None:
        async with self._work_factory() as work:
            active = await work.jobs.check_active(lease, now=_utc(self._clock()))
        if not active:
            raise JobLeaseLostError(operation="structured_provider_lease_lost")

    async def _run_structured(
        self,
        lease: JobLease,
        artifact_id: str,
        spec: ExplainJobSpecV1,
    ) -> GeneratedArtifact:
        provider = self._structured_provider
        if provider is None:
            raise ValueError("structured artifact provider is not configured")
        async with self._work_factory() as work:
            source = await work.sources.get(spec.source_document_id)
            artifact = await work.artifacts.get(artifact_id)
            paper = await work.papers.get(spec.paper_id)
            expected_head_artifact_id = await work.artifacts.get_head_artifact_id(
                paper_id=spec.paper_id,
                kind=artifact.kind.value if artifact is not None else "",
            )
        if (
            source is None
            or source.status is not SourceDocumentStatus.READY
            or source.markdown is None
            or source.content_sha256 is None
            or source.paper_id != spec.paper_id
            or source.mode.value != spec.source_mode
        ):
            raise PersistenceConflictError(operation="artifact_source_not_ready")
        if (
            artifact is None
            or artifact.status is not SourceDocumentStatus.RUNNING
            or artifact.kind
            not in {
                ArtifactKind.EXPLAINER,
                ArtifactKind.CLASSIFICATION,
                ArtifactKind.METADATA,
                ArtifactKind.SUMMARY,
            }
            or artifact.paper_id != spec.paper_id
            or artifact.source_document_id != source.id
            or artifact.generator_provider != spec.provider
            or artifact.generator_model != spec.model
            or artifact.prompt_version != spec.prompt_version
        ):
            raise PersistenceConflictError(operation="artifact_not_running")
        if paper is None:
            raise PersistenceConflictError(operation="artifact_paper_missing")
        if provider.provider_id != artifact.generator_provider or provider.model_id != artifact.generator_model:
            raise PersistenceConflictError(operation="artifact_provider_identity")
        if (
            (spec.profile != "standard" and artifact.kind is not ArtifactKind.EXPLAINER)
            or spec.prompt_version
            != _structured_prompt_version(artifact.kind, spec.profile)
        ):
            raise PersistenceConflictError(operation="artifact_profile_identity")

        plan = await self._context_builder.build(
            source.id,
            ContextRequest(
                source_document_id=source.id,
                consumer=artifact.kind,
            ),
        )

        async def generate(request: StructuredArtifactRequest) -> str:
            await self._structured_provider_checkpoint(lease)
            return await _generate_structured(provider, request)

        request_base = {
            "artifact_id": artifact.id,
            "kind": artifact.kind.value,
            "paper_id": paper.id,
            "paper_title": paper.title,
            "paper_authors": paper.authors,
            "prompt_version": artifact.prompt_version,
            "profile": spec.profile,
        }
        if artifact.kind in {ArtifactKind.SUMMARY, ArtifactKind.EXPLAINER}:
            map_results: list[StructuredArtifactInput] = []
            for batch in plan.batches:
                raw_map = await generate(
                    StructuredArtifactRequest(
                        **request_base,
                        stage="map",
                        batch=batch,
                    ),
                )
                if artifact.kind is ArtifactKind.SUMMARY:
                    map_results.append(
                        _summary_map_output(raw_map, batch.covered_ranges)
                    )
                else:
                    map_results.append(
                        _explainer_map_output(raw_map, batch.covered_ranges)
                    )
            if artifact.kind is ArtifactKind.SUMMARY:
                raw_output, expected_ranges = await _reduce_summary_inputs(
                    generate,
                    request_base=request_base,
                    inputs=tuple(map_results),
                )
                content, projection = _summary_output(
                    raw_output,
                    expected_ranges=expected_ranges,
                )
            else:
                section_results: list[StructuredArtifactInput] = []
                current_group: int | None = None
                current_inputs: list[StructuredArtifactInput] = []
                for batch, result in zip(plan.batches, map_results, strict=True):
                    if batch.group_sequence is None:
                        raise ContextCoverageInvalidError()
                    if current_inputs and batch.group_sequence != current_group:
                        section_results.append(
                            await _reduce_explainer_to_input(
                                generate,
                                request_base=request_base,
                                inputs=tuple(current_inputs),
                                reduce_single=False,
                            )
                        )
                        current_inputs = []
                    current_group = batch.group_sequence
                    current_inputs.append(result)
                if current_inputs:
                    section_results.append(
                        await _reduce_explainer_to_input(
                            generate,
                            request_base=request_base,
                            inputs=tuple(current_inputs),
                            reduce_single=False,
                        )
                    )
                raw_output, expected_ranges = await _reduce_explainer_inputs(
                    generate,
                    request_base=request_base,
                    inputs=tuple(section_results),
                )
                content, projection = _explainer_output(
                    raw_output,
                    expected_ranges=expected_ranges,
                )
        else:
            raw_output = await generate(
                StructuredArtifactRequest(
                    **request_base,
                    stage="direct",
                    plan=plan,
                ),
            )
        if artifact.kind is ArtifactKind.CLASSIFICATION:
            content, projection = _classification_output(raw_output)
        elif artifact.kind is ArtifactKind.METADATA:
            content, projection = _metadata_output(raw_output)
        elif artifact.kind not in {ArtifactKind.SUMMARY, ArtifactKind.EXPLAINER}:
            raise ValueError("structured consumer is not implemented")
        content_sha256 = hashlib.sha256(content.encode("utf-8")).hexdigest()
        now = _utc(self._clock())
        async with self._work_factory() as work:
            published = await work.artifacts.publish_structured(
                lease=lease,
                source_document_id=source.id,
                expected_source_mode=source.mode.value,
                expected_source_pdf_sha256=source.pdf_sha256,
                expected_source_content_sha256=source.content_sha256,
                expected_source_processing_version=source.processing_version,
                expected_chunking_version=plan.chunking_version,
                expected_selected_chunk_identities=tuple(
                    _chunk_publication_identity(chunk)
                    for batch in plan.batches
                    for chunk in batch.chunks
                ),
                artifact_id=artifact.id,
                kind=artifact.kind.value,
                expected_head_artifact_id=expected_head_artifact_id,
                content=content,
                content_sha256=content_sha256,
                projection=projection,
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


def _validated_checkpoint(
    checkpoint: TranslationCheckpoint,
    *,
    artifact: GeneratedArtifact,
    chunk_id: str,
    sequence: int,
    source_content_sha256: str,
) -> str:
    translated = checkpoint.translated_markdown
    _validated_checkpoint_identity(
        checkpoint,
        artifact=artifact,
        chunk_id=chunk_id,
        sequence=sequence,
        source_content_sha256=source_content_sha256,
    )
    if (
        translated is None
        or not translated.strip()
        or checkpoint.content_sha256
        != hashlib.sha256(translated.encode("utf-8")).hexdigest()
    ):
        raise TranslationCheckpointConflictError(operation="translation_checkpoint_identity")
    return translated


def _validated_checkpoint_identity(
    checkpoint: TranslationCheckpoint,
    *,
    artifact: GeneratedArtifact,
    chunk_id: str,
    sequence: int,
    source_content_sha256: str,
) -> None:
    if (
        checkpoint.artifact_id != artifact.id
        or checkpoint.chunk_id != chunk_id
        or checkpoint.sequence != sequence
        or checkpoint.source_content_sha256 != source_content_sha256
        or checkpoint.provider != artifact.generator_provider
        or checkpoint.model != artifact.generator_model
        or checkpoint.prompt_version != artifact.prompt_version
    ):
        raise TranslationCheckpointConflictError(operation="translation_checkpoint_identity")


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("clock must return a timezone-aware datetime")
    return value.astimezone(timezone.utc)


def _artifact_id_for_key(artifact_key: str) -> str:
    return "artifact_" + artifact_key


def _job_id_for_key(job_key: str) -> str:
    return "job_" + job_key


async def _generate_structured(
    provider: StructuredArtifactProvider,
    request: StructuredArtifactRequest,
) -> str:
    try:
        return await provider.generate(request)
    except Exception as error:
        if isinstance(
            error,
            (
                ArtifactOutputInvalidError,
                GenerationFailureError,
                WorkerConfigurationError,
            ),
        ):
            raise
        raise GenerationFailureError() from None


async def _reduce_explainer_to_input(
    generate: Callable[[StructuredArtifactRequest], Awaitable[str]],
    *,
    request_base: dict[str, object],
    inputs: tuple[StructuredArtifactInput, ...],
    reduce_single: bool = True,
) -> StructuredArtifactInput:
    if len(inputs) == 1 and not reduce_single:
        return inputs[0]
    raw_output, expected_ranges = await _reduce_explainer_inputs(
        generate,
        request_base=request_base,
        inputs=inputs,
    )
    return _explainer_map_output(raw_output, expected_ranges)


async def _reduce_explainer_inputs(
    generate: Callable[[StructuredArtifactRequest], Awaitable[str]],
    *,
    request_base: dict[str, object],
    inputs: tuple[StructuredArtifactInput, ...],
) -> tuple[str, tuple[tuple[int, int], ...]]:
    current = inputs
    while len(current) > _STRUCTURED_REDUCE_FAN_IN:
        next_level: list[StructuredArtifactInput] = []
        for start in range(0, len(current), _STRUCTURED_REDUCE_FAN_IN):
            children = current[start : start + _STRUCTURED_REDUCE_FAN_IN]
            expected_ranges = _input_ranges(children)
            raw_output = await generate(
                StructuredArtifactRequest(
                    **request_base,
                    stage="reduce",
                    inputs=children,
                ),
            )
            next_level.append(_explainer_map_output(raw_output, expected_ranges))
        current = tuple(next_level)
    expected_ranges = _input_ranges(current)
    raw_output = await generate(
        StructuredArtifactRequest(
            **request_base,
            stage="reduce",
            inputs=current,
        ),
    )
    return raw_output, expected_ranges


async def _reduce_summary_inputs(
    generate: Callable[[StructuredArtifactRequest], Awaitable[str]],
    *,
    request_base: dict[str, object],
    inputs: tuple[StructuredArtifactInput, ...],
) -> tuple[str, tuple[tuple[int, int], ...]]:
    current = inputs
    while len(current) > _STRUCTURED_REDUCE_FAN_IN:
        next_level: list[StructuredArtifactInput] = []
        for start in range(0, len(current), _STRUCTURED_REDUCE_FAN_IN):
            children = current[start : start + _STRUCTURED_REDUCE_FAN_IN]
            expected_ranges = _input_ranges(children)
            raw_output = await generate(
                StructuredArtifactRequest(
                    **request_base,
                    stage="reduce",
                    inputs=children,
                ),
            )
            next_level.append(_summary_reduce_output(raw_output, expected_ranges))
        current = tuple(next_level)
    expected_ranges = _input_ranges(current)
    raw_output = await generate(
        StructuredArtifactRequest(
            **request_base,
            stage="reduce",
            inputs=current,
        ),
    )
    return raw_output, expected_ranges


def _input_ranges(
    inputs: tuple[StructuredArtifactInput, ...],
) -> tuple[tuple[int, int], ...]:
    if not inputs:
        raise ContextCoverageInvalidError()
    return tuple(item for child in inputs for item in child.covered_ranges)


def _chunk_publication_identity(chunk: Any) -> tuple[str, int, str, str, int, int]:
    return (
        chunk.id,
        chunk.sequence,
        chunk.content_sha256,
        chunk.chunk_key or "",
        chunk.char_start if chunk.char_start is not None else -1,
        chunk.char_end if chunk.char_end is not None else -1,
    )


@dataclass(frozen=True, slots=True)
class DocumentArtifactEnqueueResult:
    artifact: GeneratedArtifact
    job: NewProcessingJob
    deduplicated: bool


_STRUCTURED_PROMPT_VERSIONS = {
    ArtifactKind.EXPLAINER: "explainer-context-v1",
    ArtifactKind.CLASSIFICATION: "classification-v1",
    ArtifactKind.METADATA: "metadata-v1",
    ArtifactKind.SUMMARY: "summary-v1",
}


def _structured_prompt_version(kind: ArtifactKind, profile: str) -> str:
    version = _STRUCTURED_PROMPT_VERSIONS[kind]
    if kind is ArtifactKind.EXPLAINER and profile == "deep":
        return version.removesuffix("-v1") + "-deep-v1"
    return version


def _artifact_options(
    kind: ArtifactKind,
    prompt_version: str,
    *,
    profile: str,
) -> dict[str, object]:
    return {
        "profile": profile,
        "contextVersion": "context-plan-v1",
        "outputSchemaVersion": prompt_version,
        "consumer": kind.value,
    }


__all__ = ["DocumentArtifactEnqueueResult", "DocumentArtifactService"]


def _classification_output(raw_output: str) -> tuple[str, dict[str, object]]:
    if not isinstance(raw_output, str) or not raw_output.strip():
        raise ArtifactOutputInvalidError(operation="classification_output")
    try:
        value = json.loads(
            raw_output,
            object_pairs_hook=_unique_json_object,
            parse_constant=_reject_json_constant,
        )
    except ArtifactOutputInvalidError:
        raise
    except (TypeError, ValueError, json.JSONDecodeError) as error:
        raise ArtifactOutputInvalidError(operation="classification_output") from error
    required = {"type", "topic", "task", "models", "datasets", "tags", "relevance"}
    if not isinstance(value, dict) or set(value) != required:
        raise ArtifactOutputInvalidError(operation="classification_output")
    if any(
        not isinstance(value[name], str) or not value[name].strip()
        for name in ("type", "topic", "task")
    ):
        raise ArtifactOutputInvalidError(operation="classification_output")
    for name in ("models", "datasets", "tags"):
        items = value[name]
        if (
            not isinstance(items, list)
            or any(not isinstance(item, str) or not item.strip() for item in items)
            or len(set(items)) != len(items)
        ):
            raise ArtifactOutputInvalidError(operation="classification_output")
    relevance = value["relevance"]
    if (
        not isinstance(relevance, (int, float))
        or isinstance(relevance, bool)
        or not 0 <= float(relevance) <= 1
    ):
        raise ArtifactOutputInvalidError(operation="classification_output")
    canonical = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return canonical, value


def _metadata_output(raw_output: str) -> tuple[str, dict[str, object]]:
    value = _strict_json_object(raw_output, operation="metadata_output")
    required = {
        "title",
        "titleZh",
        "authors",
        "venue",
        "year",
        "abstract",
        "arxivId",
        "doi",
    }
    if set(value) != required:
        raise ArtifactOutputInvalidError(operation="metadata_output")
    if not isinstance(value["title"], str) or not value["title"].strip():
        raise ArtifactOutputInvalidError(operation="metadata_output")
    for name in ("titleZh", "venue", "year", "abstract", "arxivId", "doi"):
        item = value[name]
        if item is not None and (not isinstance(item, str) or not item.strip()):
            raise ArtifactOutputInvalidError(operation="metadata_output")
    authors = value["authors"]
    if (
        not isinstance(authors, list)
        or any(not isinstance(author, str) or not author.strip() for author in authors)
        or len(set(authors)) != len(authors)
    ):
        raise ArtifactOutputInvalidError(operation="metadata_output")
    year = value["year"]
    if year is not None and (len(year) != 4 or not year.isdigit()):
        raise ArtifactOutputInvalidError(operation="metadata_output")
    canonical = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return canonical, {
        "title": value["title"],
        "title_zh": value["titleZh"],
        "authors": authors,
        "venue": value["venue"],
        "year": year,
        "abstract": value["abstract"],
        "arxiv_id": value["arxivId"],
        "doi": value["doi"],
    }


def _summary_map_output(
    raw_output: str,
    expected_ranges: tuple[tuple[int, int], ...],
) -> StructuredArtifactInput:
    value = _strict_json_object(raw_output, operation="summary_map_output")
    if set(value) != {"coveredRanges", "summary"}:
        raise ArtifactOutputInvalidError(operation="summary_map_output")
    ranges = _covered_ranges(value["coveredRanges"], operation="summary_map_output")
    summary = value["summary"]
    if ranges != expected_ranges or not isinstance(summary, str) or not summary.strip():
        raise ArtifactOutputInvalidError(operation="summary_map_output")
    return StructuredArtifactInput(content=summary, covered_ranges=ranges)


def _summary_output(
    raw_output: str,
    *,
    expected_ranges: tuple[tuple[int, int], ...],
) -> tuple[str, dict[str, object]]:
    value = _strict_json_object(raw_output, operation="summary_output")
    if set(value) != {"coveredRanges", "tldr", "contribution"}:
        raise ArtifactOutputInvalidError(operation="summary_output")
    ranges = _covered_ranges(value["coveredRanges"], operation="summary_output")
    if ranges != expected_ranges or any(
        not isinstance(value[name], str) or not value[name].strip()
        for name in ("tldr", "contribution")
    ):
        raise ArtifactOutputInvalidError(operation="summary_output")
    projection = {"tldr": value["tldr"], "contribution": value["contribution"]}
    canonical = json.dumps(
        projection,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return canonical, projection


def _summary_reduce_output(
    raw_output: str,
    expected_ranges: tuple[tuple[int, int], ...],
) -> StructuredArtifactInput:
    canonical, _projection = _summary_output(
        raw_output,
        expected_ranges=expected_ranges,
    )
    return StructuredArtifactInput(
        content=canonical,
        covered_ranges=expected_ranges,
    )


def _explainer_map_output(
    raw_output: str,
    expected_ranges: tuple[tuple[int, int], ...],
) -> StructuredArtifactInput:
    value = _strict_json_object(raw_output, operation="explainer_map_output")
    if set(value) != {"coveredRanges", "markdown"}:
        raise ArtifactOutputInvalidError(operation="explainer_map_output")
    ranges = _covered_ranges(value["coveredRanges"], operation="explainer_map_output")
    markdown = value["markdown"]
    if ranges != expected_ranges or not isinstance(markdown, str) or not markdown.strip():
        raise ArtifactOutputInvalidError(operation="explainer_map_output")
    return StructuredArtifactInput(content=markdown, covered_ranges=ranges)


def _explainer_output(
    raw_output: str,
    *,
    expected_ranges: tuple[tuple[int, int], ...],
) -> tuple[str, dict[str, object]]:
    value = _strict_json_object(raw_output, operation="explainer_output")
    if set(value) != {"coveredRanges", "markdown"}:
        raise ArtifactOutputInvalidError(operation="explainer_output")
    ranges = _covered_ranges(value["coveredRanges"], operation="explainer_output")
    markdown = value["markdown"]
    if ranges != expected_ranges or not isinstance(markdown, str) or not markdown.strip():
        raise ArtifactOutputInvalidError(operation="explainer_output")
    return markdown, {"explainer": markdown}


def _covered_ranges(value: object, *, operation: str) -> tuple[tuple[int, int], ...]:
    if not isinstance(value, list):
        raise ArtifactOutputInvalidError(operation=operation)
    ranges: list[tuple[int, int]] = []
    for item in value:
        if (
            not isinstance(item, list)
            or len(item) != 2
            or any(not isinstance(bound, int) or isinstance(bound, bool) for bound in item)
            or item[0] < 0
            or item[1] <= item[0]
        ):
            raise ArtifactOutputInvalidError(operation=operation)
        ranges.append((item[0], item[1]))
    if not ranges or len(set(ranges)) != len(ranges):
        raise ArtifactOutputInvalidError(operation=operation)
    return tuple(ranges)


def _strict_json_object(raw_output: str, *, operation: str) -> dict[str, object]:
    if not isinstance(raw_output, str) or not raw_output.strip():
        raise ArtifactOutputInvalidError(operation=operation)
    try:
        value = json.loads(
            raw_output,
            object_pairs_hook=_unique_json_object,
            parse_constant=_reject_json_constant,
        )
    except ArtifactOutputInvalidError:
        raise
    except (TypeError, ValueError, json.JSONDecodeError) as error:
        raise ArtifactOutputInvalidError(operation=operation) from error
    if not isinstance(value, dict):
        raise ArtifactOutputInvalidError(operation=operation)
    return value


def _unique_json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ArtifactOutputInvalidError(operation="artifact_output_duplicate_key")
        result[key] = value
    return result


def _reject_json_constant(_value: str) -> object:
    raise ArtifactOutputInvalidError(operation="artifact_output_nonfinite")


_INLINE_MATH = re.compile(
    r"(?<!\\)\$(?!\$)(?:\\.|[^$\r\n])+?(?<!\\)\$|\\\((?:\\.|[^\r\n])+?\\\)"
)
_DISPLAY_MATH = re.compile(
    r"(?<!\\)\$\$(?:\\.|(?!\$\$)[^\r\n])+?(?<!\\)\$\$|"
    r"\\\[(?:\\.|[^\r\n])+?\\\]"
)
_ESCAPED_DELIMITER = re.compile(r"\\[\\|$`*_{}\[\]()]" )
_PLACEHOLDER = re.compile(r"P3MD_[0-9]+_[0-9a-f]{12}")


def _protect_structured_markdown(markdown: str) -> tuple[str, tuple[tuple[str, str], ...]]:
    """Replace syntax-bearing spans with deterministic, integrity-bound tokens."""

    protected: list[tuple[str, str]] = []
    pieces: list[str] = []
    cursor = 0
    # Protect inline/display math first, then escaped delimiters and table
    # pipes.  Match order is deterministic and spans never overlap.
    pattern = re.compile(
        _DISPLAY_MATH.pattern
        + r"|"
        + _INLINE_MATH.pattern
        + r"|"
        + _ESCAPED_DELIMITER.pattern
        + r"|(?<!\\)\|"
    )
    for index, match in enumerate(pattern.finditer(markdown)):
        pieces.append(markdown[cursor : match.start()])
        original = match.group(0)
        digest = hashlib.sha256(original.encode("utf-8")).hexdigest()[:12]
        token = f"P3MD_{index}_{digest}"
        pieces.append(token)
        protected.append((token, original))
        cursor = match.end()
    pieces.append(markdown[cursor:])
    return "".join(pieces), tuple(protected)


def _restore_structured_markdown(
    translated: str,
    protected: tuple[tuple[str, str], ...],
    *,
    source_markdown: str,
) -> str:
    """Fail closed when a provider loses, duplicates, or reorders a token."""

    expected = tuple(token for token, _original in protected)
    actual = tuple(match.group(0) for match in _PLACEHOLDER.finditer(translated))
    if actual != expected or len(set(actual)) != len(actual):
        raise MarkdownStructureInvalidError(operation="translation_structure")
    source_skeleton = _structured_skeleton(source_markdown)
    restored = translated
    for token, original in protected:
        restored = restored.replace(token, original)
    # No provider-created P3 token may survive restoration.
    if _PLACEHOLDER.search(restored) or _structured_skeleton(restored) != source_skeleton:
        raise MarkdownStructureInvalidError(operation="translation_structure")
    return restored


def _structured_skeleton(
    markdown: str,
) -> tuple[tuple[tuple[int, tuple[bool, ...]], ...], tuple[str, ...]]:
    rows: list[tuple[int, tuple[bool, ...]]] = []
    for line in markdown.splitlines():
        pipe_count = sum(1 for character in line if character == "|")
        if not pipe_count:
            continue
        cells = line.split("|")[1:-1]
        alignment = tuple(
            bool(re.fullmatch(r"\s*:?-{3,}:?\s*", cell)) for cell in cells
        )
        rows.append((pipe_count, alignment))
    syntax_pattern = re.compile(
        _DISPLAY_MATH.pattern + r"|" + _INLINE_MATH.pattern + r"|" + _ESCAPED_DELIMITER.pattern
    )
    syntax = tuple(match.group(0) for match in syntax_pattern.finditer(markdown))
    return tuple(rows), syntax
