from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime
import hashlib
from pathlib import Path
import tempfile
from typing import Any, AsyncIterator, Callable, Mapping

from sqlalchemy import text

from backend.app.config import DatabaseSettings
from backend.app.application.context_builder import ContextBuilder
from backend.app.domain import SourceDocument
from backend.app.domain.context import ChunkSet, ChunkingSpec
from backend.app.infrastructure.database import create_async_session_factory
from backend.app.repositories.unit_of_work import SqlAlchemyUnitOfWork
from backend.tests.support.p1_database import create_legacy_database, run_alembic


@dataclass(frozen=True, slots=True)
class P3DatabaseFixture:
    database_path: Path
    session_factory: Any


@dataclass(frozen=True, slots=True)
class P3ContextFixture:
    database_path: Path
    session_factory: Any
    unit_of_work_factory: Callable[[], SqlAlchemyUnitOfWork]
    builder: ContextBuilder
    chunk_set: ChunkSet


@dataclass(frozen=True, slots=True)
class P3FreshnessFixture:
    database_path: Path
    session_factory: Any
    unit_of_work_factory: Callable[[], SqlAlchemyUnitOfWork]
    source_id: str
    artifact_ids: tuple[str, ...]
    chunk_ids: tuple[str, ...]
    embedding_ids: tuple[str, ...]
    queued_job_id: str
    running_job_id: str
    terminal_job_id: str


@dataclass(frozen=True, slots=True)
class P3TranslationFixture:
    database_path: Path
    session_factory: Any
    unit_of_work_factory: Callable[[], SqlAlchemyUnitOfWork]
    builder: ContextBuilder
    chunk_set: ChunkSet
    artifact_id: str
    job_id: str
    lease: Any
    provider: str
    model: str
    prompt_version: str


@asynccontextmanager
async def p3_database_fixture(
    *,
    prefix: str,
    prepare_p2: Callable[[Path], None] | None = None,
) -> AsyncIterator[P3DatabaseFixture]:
    """Create one disposable P3 database and always dispose its engine."""

    with tempfile.TemporaryDirectory(prefix=prefix) as temp_dir:
        database_path = Path(temp_dir) / "database" / "app.db"
        create_legacy_database(database_path)
        run_alembic(database_path, "20260807_02")
        if prepare_p2 is not None:
            prepare_p2(database_path)
        run_alembic(database_path, "20260807_03")
        session_factory = create_async_session_factory(DatabaseSettings(database_path))
        try:
            yield P3DatabaseFixture(database_path, session_factory)
        finally:
            await session_factory.kw["bind"].dispose()


@asynccontextmanager
async def p3_context_fixture(
    *,
    prefix: str,
    source_id: str,
    markdown: str,
    spec: ChunkingSpec,
    now: datetime,
    pdf_sha256: str,
    options_hash: str,
) -> AsyncIterator[P3ContextFixture]:
    """Create one ready SourceDocument and its deterministic P3 chunks."""

    async with p3_database_fixture(prefix=prefix) as fixture:
        factory = lambda: SqlAlchemyUnitOfWork(fixture.session_factory)
        source_sha = hashlib.sha256(markdown.encode("utf-8")).hexdigest()
        async with factory() as work:
            await work.sources.add(
                SourceDocument(
                    id=source_id,
                    paper_id="paper-1",
                    mode="native",
                    status="ready",
                    provider="local",
                    model="pymupdf4llm-pymupdf",
                    pdf_sha256=pdf_sha256,
                    options_hash=options_hash,
                    processing_version="native-v1",
                    created_at=now,
                    updated_at=now,
                    markdown=markdown,
                    content_sha256=source_sha,
                    page_count=1,
                )
            )
            await work.commit()
        builder = ContextBuilder(factory)
        chunk_set = await builder.materialize_chunks(source_id, spec, now=now)
        yield P3ContextFixture(
            database_path=fixture.database_path,
            session_factory=fixture.session_factory,
            unit_of_work_factory=factory,
            builder=builder,
            chunk_set=chunk_set,
        )


@asynccontextmanager
async def p3_translation_fixture(
    *,
    prefix: str,
    markdown: str,
    spec: ChunkingSpec,
    now: datetime,
    succeeded_checkpoints: Mapping[int, str] | None = None,
    provider: str = "fake-translation",
    model: str = "fake-translation-model",
    prompt_version: str = "translation-chunk-v1",
    translation_mode: str = "chunked",
) -> AsyncIterator[P3TranslationFixture]:
    """Create one real P3 translation target, job, lease, and optional resume state."""

    from backend.app.domain import GeneratedArtifact
    from backend.app.domain.processing import (
        NewProcessingJob,
        TranslateJobSpecV1,
        build_artifact_job_key,
        build_artifact_key,
        encode_job_spec_v1,
        hash_job_spec,
    )

    source_id = "src_translation"
    artifact_id = "artifact_translation"
    job_id = "job_translation"
    async with p3_context_fixture(
        prefix=prefix,
        source_id=source_id,
        markdown=markdown,
        spec=spec,
        now=now,
        pdf_sha256="a" * 64,
        options_hash="b" * 64,
    ) as context:
        artifact = GeneratedArtifact(
            id=artifact_id,
            paper_id="paper-1",
            kind="translation",
            source_document_id=source_id,
            status="queued",
            generator_provider=provider,
            generator_model=model,
            prompt_version=prompt_version,
            created_at=now,
            updated_at=now,
        )
        job_spec = TranslateJobSpecV1(
            paper_id="paper-1",
            source_document_id=source_id,
            artifact_id=artifact_id,
            mode=translation_mode,
        )
        raw_spec = encode_job_spec_v1(job_spec)
        translation_options = {
            "targetLanguage": "zh-CN",
            "chunkingVersion": spec.chunking_version,
            "contextVersion": "context-plan-v1",
            "promptSchemaVersion": "translation-chunk-v1",
        }
        if translation_mode != "chunked":
            translation_options["translationMode"] = translation_mode
        artifact_key = build_artifact_key(
            kind="translation",
            source_document_id=source_id,
            source_content_sha256=context.chunk_set.source_content_sha256,
            generator_provider=provider,
            generator_model=model,
            prompt_version=prompt_version,
            kind_specific_options=translation_options,
        )
        job = NewProcessingJob(
            id=job_id,
            spec=job_spec,
            idempotency_key=build_artifact_job_key(
                artifact_key,
                hash_job_spec(raw_spec),
            ),
            created_at=now,
            max_attempts=3,
        )
        async with context.unit_of_work_factory() as work:
            await work.artifacts.enqueue_with_job(
                artifact,
                job,
                spec_json=raw_spec,
                spec_sha256=hash_job_spec(raw_spec),
                kind_specific_options=translation_options,
            )
            await work.commit()
        async with context.unit_of_work_factory() as work:
            lease = await work.jobs.claim_next(
                worker_id="translation-worker",
                now=now,
                lease_seconds=3600,
            )
            await work.commit()
        if lease is None:
            raise AssertionError("translation fixture failed to claim its job")

        seeded = dict(succeeded_checkpoints or {})
        if seeded:
            async with context.session_factory() as session:
                for sequence, translated_markdown in sorted(seeded.items()):
                    chunk = context.chunk_set.chunks[sequence]
                    await session.execute(
                        text(
                            "INSERT INTO artifact_translation_checkpoints("
                            "artifact_id,chunk_id,sequence,source_content_sha256,provider,model,"
                            "prompt_version,status,translated_markdown,content_sha256,attempt,"
                            "error_code,error_message,created_at,updated_at) VALUES("
                            ":artifact_id,:chunk_id,:sequence,:source_sha,:provider,:model,"
                            ":prompt_version,'succeeded',:translated_markdown,:content_sha,1,"
                            "NULL,NULL,:created_at,:updated_at)"
                        ),
                        {
                            "artifact_id": artifact_id,
                            "chunk_id": chunk.id,
                            "sequence": sequence,
                            "source_sha": context.chunk_set.source_content_sha256,
                            "provider": provider,
                            "model": model,
                            "prompt_version": prompt_version,
                            "translated_markdown": translated_markdown,
                            "content_sha": hashlib.sha256(
                                translated_markdown.encode("utf-8")
                            ).hexdigest(),
                            "created_at": _timestamp(now),
                            "updated_at": _timestamp(now),
                        },
                    )
                await session.commit()

        yield P3TranslationFixture(
            database_path=context.database_path,
            session_factory=context.session_factory,
            unit_of_work_factory=context.unit_of_work_factory,
            builder=context.builder,
            chunk_set=context.chunk_set,
            artifact_id=artifact_id,
            job_id=job_id,
            lease=lease,
            provider=provider,
            model=model,
            prompt_version=prompt_version,
        )


@asynccontextmanager
async def p3_freshness_fixture(
    *,
    prefix: str,
    now: datetime,
) -> AsyncIterator[P3FreshnessFixture]:
    """Create one P3 dependency graph for source-staleness integration tests."""

    from backend.app.domain import GeneratedArtifact, SourceDocument
    from backend.app.domain.context import ChunkingSpec
    from backend.app.domain.processing import (
        ExplainJobSpecV1,
        NewProcessingJob,
        build_artifact_job_key,
        build_artifact_key,
        encode_job_spec_v1,
        hash_job_spec,
    )
    from backend.app.repositories.models import ProcessingJobModel

    source_id = "src_freshness"
    markdown = "# Abstract\n\nfreshness source.\n\n# Conclusion\n\ntail.\n"
    source_sha = hashlib.sha256(markdown.encode("utf-8")).hexdigest()
    pdf_sha = "a" * 64
    options_hash = "b" * 64
    artifact_ids = (
        "artifact_ready",
        "artifact_queued",
        "artifact_running",
        "artifact_terminal",
    )
    artifact_prompt_versions = (
        "p3-ready-v1",
        "p3-queued-v1",
        "p3-running-v1",
        "p3-terminal-v1",
    )
    queued_job_id = "job_freshness_queued"
    running_job_id = "job_freshness_running"
    terminal_job_id = "job_freshness_terminal"

    async with p3_database_fixture(prefix=prefix) as fixture:
        factory = lambda: SqlAlchemyUnitOfWork(fixture.session_factory)
        async with factory() as work:
            await work.sources.add(
                SourceDocument(
                    id=source_id,
                    paper_id="paper-1",
                    mode="native",
                    status="ready",
                    provider="local",
                    model="pymupdf4llm-pymupdf",
                    pdf_sha256=pdf_sha,
                    options_hash=options_hash,
                    processing_version="native-v1",
                    created_at=now,
                    updated_at=now,
                    markdown=markdown,
                    content_sha256=source_sha,
                    page_count=1,
                )
            )
            for artifact_id, prompt_version, status in zip(
                artifact_ids,
                artifact_prompt_versions,
                ("ready", "queued", "running", "ready"),
                strict=True,
            ):
                content = f"{artifact_id} content" if status == "ready" else None
                await work.artifacts.add(
                    GeneratedArtifact(
                        id=artifact_id,
                        paper_id="paper-1",
                        kind="explainer",
                        source_document_id=source_id,
                        status=status,
                        generator_provider="fake",
                        generator_model="fake-model",
                        prompt_version=prompt_version,
                        created_at=now,
                        updated_at=now,
                        content=content,
                        content_sha256=(
                            hashlib.sha256(content.encode("utf-8")).hexdigest()
                            if content is not None
                            else None
                        ),
                    )
                )
            await work.commit()

        chunk_set = await ContextBuilder(factory).materialize_chunks(
            source_id,
            ChunkingSpec(target_tokens=5, hard_cap_tokens=5),
            now=now,
        )
        vector = b"\x00\x00\x80?"
        vector_sha = hashlib.sha256(vector).hexdigest()
        embedding_ids = tuple(f"embedding_{index}" for index, _ in enumerate(chunk_set.chunks))
        async with fixture.session_factory() as session:
            await session.execute(
                text(
                    "INSERT INTO paper_artifact_heads(paper_id,kind,artifact_id,updated_at) "
                    "VALUES(:paper_id,:kind,:artifact_id,:updated_at)"
                ),
                {
                    "paper_id": "paper-1",
                    "kind": "explainer",
                    "artifact_id": artifact_ids[0],
                    "updated_at": _timestamp(now),
                },
            )
            for embedding_id, chunk in zip(embedding_ids, chunk_set.chunks, strict=True):
                await session.execute(
                    text(
                        "INSERT INTO document_chunk_embeddings("
                        "id,chunk_id,source_document_id,provider,model,embedding_version,"
                        "dimensions,vector,vector_sha256,chunk_content_sha256,status,"
                        "created_at,updated_at) VALUES("
                        ":id,:chunk_id,:source_id,'fake','fake-embed','v1',1,:vector,"
                        ":vector_sha,:chunk_sha,'ready',:created_at,:updated_at)"
                    ),
                    {
                        "id": embedding_id,
                        "chunk_id": chunk.id,
                        "source_id": source_id,
                        "vector": vector,
                        "vector_sha": vector_sha,
                        "chunk_sha": chunk.content_sha256,
                        "created_at": _timestamp(now),
                        "updated_at": _timestamp(now),
                    },
                )
            await session.commit()

        async with factory() as work:
            for job_id, artifact_id, prompt_version in (
                (queued_job_id, artifact_ids[1], artifact_prompt_versions[1]),
                (running_job_id, artifact_ids[2], artifact_prompt_versions[2]),
                (terminal_job_id, artifact_ids[3], artifact_prompt_versions[3]),
            ):
                spec = ExplainJobSpecV1(
                    paper_id="paper-1",
                    source_document_id=source_id,
                    artifact_id=artifact_id,
                    profile="standard",
                    provider="fake",
                    model="fake-model",
                    prompt_version=prompt_version,
                )
                raw_spec = encode_job_spec_v1(spec)
                artifact_key = build_artifact_key(
                    kind="explainer",
                    source_document_id=source_id,
                    source_content_sha256=source_sha,
                    generator_provider="fake",
                    generator_model="fake-model",
                    prompt_version=prompt_version,
                    kind_specific_options={"profile": "standard"},
                )
                job = NewProcessingJob(
                    id=job_id,
                    spec=spec,
                    idempotency_key=build_artifact_job_key(
                        artifact_key,
                        hash_job_spec(raw_spec),
                    ),
                    created_at=now,
                )
                await work.jobs.insert_with_spec(
                    job,
                    spec_json=raw_spec,
                    spec_sha256=hash_job_spec(raw_spec),
                )
            await work.commit()

        async with fixture.session_factory() as session:
            running = await session.get(ProcessingJobModel, running_job_id)
            terminal = await session.get(ProcessingJobModel, terminal_job_id)
            running.status = "running"
            running.attempt = 1
            running.started_at = _timestamp(now)
            running.lease_owner = "freshness-worker"
            running.lease_token = "freshness-token"
            running.lease_expires_at = _timestamp(now.replace(year=now.year + 1))
            running.heartbeat_at = _timestamp(now)
            terminal.status = "succeeded"
            terminal.finished_at = _timestamp(now)
            await session.commit()

        yield P3FreshnessFixture(
            database_path=fixture.database_path,
            session_factory=fixture.session_factory,
            unit_of_work_factory=factory,
            source_id=source_id,
            artifact_ids=artifact_ids,
            chunk_ids=chunk_set.chunk_ids,
            embedding_ids=embedding_ids,
            queued_job_id=queued_job_id,
            running_job_id=running_job_id,
            terminal_job_id=terminal_job_id,
        )


def _timestamp(value: datetime) -> str:
    return value.isoformat(timespec="microseconds").replace("+00:00", "Z")
