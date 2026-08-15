from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path

from sqlalchemy import delete, select, text, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.domain import (
    ArtifactKind,
    ArtifactVersionIdentity,
    GeneratedArtifact,
    Paper,
    PersistenceConflictError,
    PersistenceReadError,
    ProcessingJob,
    SourceCacheIdentity,
    SourceDocument,
    VaultProjection,
)
from backend.app.repositories.models import (
    GeneratedArtifactModel,
    PaperModel,
    PaperVectorModel,
    ProcessingJobModel,
    SourceDocumentModel,
    TranslationModel,
    VaultProjectionModel,
)


def _timestamp(value: datetime | None) -> str | None:
    return value.astimezone(timezone.utc).isoformat() if value is not None else None


def _datetime(value: str | None) -> datetime | None:
    if value is None:
        return None
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


async def _flush_or_conflict(session: AsyncSession, operation: str) -> None:
    try:
        await session.flush()
    except IntegrityError as error:
        await session.rollback()
        raise PersistenceConflictError(operation=operation) from error


class SqlAlchemyPaperRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, paper_id: str) -> Paper | None:
        row = await self._session.get(PaperModel, paper_id)
        if row is None:
            return None
        authors: tuple[str, ...] = ()
        if row.authors:
            try:
                decoded = json.loads(row.authors)
                if isinstance(decoded, list):
                    authors = tuple(str(value) for value in decoded)
            except (TypeError, ValueError):
                authors = ()
        return Paper(
            id=row.id,
            title=row.title,
            authors=authors,
            abstract=row.abstract,
            pdf_path=Path(row.pdf_path) if row.pdf_path else None,
        )

    async def exists(self, paper_id: str) -> bool:
        statement = text("SELECT 1 FROM papers WHERE id=:paper_id")
        return (await self._session.execute(statement, {"paper_id": paper_id})).first() is not None

    async def list_legacy(self) -> list[dict[str, object]]:
        statement = text(
            "SELECT p.id,p.id || '.pdf' AS file,p.title,p.title_zh,p.venue,p.year,"
            "p.type,p.topic,p.pdf_url,p.pdf_path,p.url,p.tldr,p.contribution,"
            "p.citations,p.created_at,p.source,p.arxiv_id,p.doi,p.s2_id,p.openalex_id,"
            "p.relevance,p.order_no AS \"order\","
            "COALESCE(g.status,'未开始') AS status,"
            "CASE WHEN n.content IS NOT NULL AND length(n.content)>0 THEN 1 ELSE 0 END AS hasNote,"
            "CASE WHEN f.paper_id IS NOT NULL THEN 1 ELSE 0 END AS favorite "
            "FROM papers p LEFT JOIN progress g ON g.paper_id=p.id "
            "LEFT JOIN notes n ON n.paper_id=p.id "
            "LEFT JOIN favorites f ON f.paper_id=p.id "
            "ORDER BY p.year,COALESCE(p.order_no,999),p.venue"
        )
        try:
            rows = (await self._session.execute(statement)).mappings().all()
        except SQLAlchemyError as error:
            raise PersistenceReadError(operation="list_legacy_papers") from error
        return [dict(row) for row in rows]

    async def get_legacy(self, paper_id: str) -> dict[str, object] | None:
        try:
            row = (
                await self._session.execute(
                    text("SELECT * FROM papers WHERE id=:paper_id"),
                    {"paper_id": paper_id},
                )
            ).mappings().one_or_none()
        except SQLAlchemyError as error:
            raise PersistenceReadError(operation="get_legacy_paper") from error
        return dict(row) if row is not None else None

    async def citation_graph_records(
        self,
    ) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
        try:
            papers = (
                await self._session.execute(
                    text(
                        "SELECT id,title,venue,year,type,topic,citations FROM papers"
                    )
                )
            ).mappings().all()
            edges = (
                await self._session.execute(
                    text("SELECT src_id,dst_id FROM cite_edges")
                )
            ).mappings().all()
        except SQLAlchemyError as error:
            raise PersistenceReadError(operation="read_citation_graph") from error
        return [dict(row) for row in papers], [dict(row) for row in edges]

    async def add_legacy(self, values: dict[str, object]) -> None:
        statement = text(
            "INSERT INTO papers("
            "id,source,title,title_zh,venue,year,abstract,tldr,url,pdf_url,pdf_path,"
            "type,topic,contribution,authors,created_at,updated_at"
            ") VALUES("
            ":id,'manual',:title,:title_zh,:venue,:year,:abstract,:tldr,:url,:pdf_url,"
            ":pdf_path,:type,:topic,:contribution,:authors,:created_at,:updated_at)"
        )
        try:
            await self._session.execute(statement, values)
        except IntegrityError as error:
            raise PersistenceConflictError(operation="add_legacy_paper") from error

    async def update_legacy(
        self,
        paper_id: str,
        fields: dict[str, object],
        *,
        updated_at: str,
    ) -> int:
        if not fields:
            return 0
        assignments = ",".join(f'"{name}"=:{name}' for name in fields)
        values = {"paper_id": paper_id, "updated_at": updated_at, **fields}
        try:
            result = await self._session.execute(
                text(
                    f"UPDATE papers SET {assignments},updated_at=:updated_at "
                    "WHERE id=:paper_id"
                ),
                values,
            )
        except IntegrityError as error:
            raise PersistenceConflictError(operation="update_legacy_paper") from error
        return int(result.rowcount or 0)

    async def set_status(self, paper_id: str, status: object, *, updated_at: str) -> None:
        await self._session.execute(
            text(
                "INSERT INTO progress(paper_id,status,updated_at) "
                "VALUES(:paper_id,:status,:updated_at) "
                "ON CONFLICT(paper_id) DO UPDATE SET "
                "status=excluded.status,updated_at=excluded.updated_at"
            ),
            {"paper_id": paper_id, "status": status, "updated_at": updated_at},
        )

    async def ensure_review_plan(
        self,
        paper_id: str,
        *,
        started_at: str,
        next_due_at: str,
        updated_at: str,
    ) -> None:
        await self._session.execute(
            text(
                "INSERT OR IGNORE INTO paper_reviews("
                "paper_id,started_at,current_step,completed_steps,next_due_at,updated_at"
                ") VALUES(:paper_id,:started_at,1,0,:next_due_at,:updated_at)"
            ),
            {
                "paper_id": paper_id,
                "started_at": started_at,
                "next_due_at": next_due_at,
                "updated_at": updated_at,
            },
        )

    async def get_review_plan(self, paper_id: str) -> dict[str, object] | None:
        try:
            row = (
                await self._session.execute(
                    text("SELECT * FROM paper_reviews WHERE paper_id=:paper_id"),
                    {"paper_id": paper_id},
                )
            ).mappings().one_or_none()
        except SQLAlchemyError as error:
            raise PersistenceReadError(operation="get_review_plan") from error
        return dict(row) if row is not None else None

    async def complete_review_step(
        self,
        paper_id: str,
        *,
        completed_steps: int,
        current_step: int,
        next_due_at: str,
        completed_at: str | None,
        updated_at: str,
    ) -> dict[str, object] | None:
        await self._session.execute(
            text(
                "UPDATE paper_reviews SET completed_steps=:completed_steps, "
                "current_step=:current_step, next_due_at=:next_due_at, "
                "completed_at=:completed_at, updated_at=:updated_at "
                "WHERE paper_id=:paper_id"
            ),
            {
                "paper_id": paper_id,
                "completed_steps": completed_steps,
                "current_step": current_step,
                "next_due_at": next_due_at,
                "completed_at": completed_at,
                "updated_at": updated_at,
            },
        )
        return await self.get_review_plan(paper_id)

    async def list_review_items(self) -> list[dict[str, object]]:
        try:
            rows = (
                await self._session.execute(
                    text(
                        "SELECT r.paper_id,r.started_at,r.current_step,r.completed_steps,"
                        "r.next_due_at,r.completed_at,r.updated_at,p.title,p.title_zh,"
                        "p.venue,p.year,COALESCE(NULLIF(TRIM(g.status),''),'未开始') AS status "
                        "FROM paper_reviews r JOIN papers p ON p.id=r.paper_id "
                        "LEFT JOIN progress g ON g.paper_id=r.paper_id "
                        "ORDER BY r.next_due_at ASC,p.title COLLATE NOCASE ASC,r.paper_id ASC"
                    )
                )
            ).mappings().all()
        except SQLAlchemyError as error:
            raise PersistenceReadError(operation="list_review_items") from error
        return [dict(row) for row in rows]

    async def get_note(self, paper_id: str) -> str | None:
        try:
            return (
                await self._session.execute(
                    text("SELECT content FROM notes WHERE paper_id=:paper_id"),
                    {"paper_id": paper_id},
                )
            ).scalar_one_or_none()
        except SQLAlchemyError as error:
            raise PersistenceReadError(operation="get_legacy_note") from error

    async def set_note(self, paper_id: str, content: str, *, updated_at: str) -> None:
        try:
            await self._session.execute(
                text(
                    "INSERT INTO notes(paper_id,content,updated_at) "
                    "VALUES(:paper_id,:content,:updated_at) "
                    "ON CONFLICT(paper_id) DO UPDATE SET "
                    "content=excluded.content,updated_at=excluded.updated_at"
                ),
                {"paper_id": paper_id, "content": content, "updated_at": updated_at},
            )
        except SQLAlchemyError as error:
            raise PersistenceConflictError(operation="write_legacy_note") from error

    async def list_missing_title_translations(self) -> list[dict[str, object]]:
        try:
            rows = (
                await self._session.execute(
                    text(
                        "SELECT id,title FROM papers "
                        "WHERE title_zh IS NULL OR TRIM(title_zh)='' "
                        "ORDER BY id"
                    )
                )
            ).mappings().all()
        except SQLAlchemyError as error:
            raise PersistenceReadError(operation="list_missing_title_translations") from error
        return [dict(row) for row in rows]

    async def list_missing_explainers(self) -> list[dict[str, object]]:
        try:
            rows = (
                await self._session.execute(
                    text(
                        "SELECT id FROM papers "
                        "WHERE explainer IS NULL OR TRIM(explainer)='' ORDER BY id"
                    )
                )
            ).mappings().all()
        except SQLAlchemyError as error:
            raise PersistenceReadError(operation="list_missing_explainers") from error
        return [dict(row) for row in rows]

    async def set_favorite(self, paper_id: str, favorite: bool, *, created_at: str) -> None:
        if favorite:
            await self._session.execute(
                text(
                    "INSERT INTO favorites(paper_id,created_at) VALUES(:paper_id,:created_at) "
                    "ON CONFLICT(paper_id) DO NOTHING"
                ),
                {"paper_id": paper_id, "created_at": created_at},
            )
        else:
            await self._session.execute(
                text("DELETE FROM favorites WHERE paper_id=:paper_id"),
                {"paper_id": paper_id},
            )

    async def delete_legacy(self, paper_id: str) -> int:
        result = await self._session.execute(
            text("DELETE FROM papers WHERE id=:paper_id"),
            {"paper_id": paper_id},
        )
        return int(result.rowcount or 0)


class SqlAlchemySourceDocumentRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, identifier: str) -> SourceDocument | None:
        return _source(await self._session.get(SourceDocumentModel, identifier))

    async def find_by_cache_identity(
        self, identity: SourceCacheIdentity,
    ) -> SourceDocument | None:
        statement = select(SourceDocumentModel).where(
            SourceDocumentModel.paper_id == identity.paper_id,
            SourceDocumentModel.pdf_sha256 == identity.pdf_sha256,
            SourceDocumentModel.mode == identity.mode.value,
            SourceDocumentModel.provider == identity.provider,
            SourceDocumentModel.model == identity.model,
            SourceDocumentModel.options_hash == identity.options_hash,
            SourceDocumentModel.processing_version == identity.processing_version,
        )
        return _source((await self._session.execute(statement)).scalar_one_or_none())

    async def add(self, document: SourceDocument) -> None:
        self._session.add(
            SourceDocumentModel(
                id=document.id,
                paper_id=document.paper_id,
                mode=document.mode.value,
                status=document.status.value,
                provider=document.provider,
                model=document.model,
                pdf_sha256=document.pdf_sha256,
                options_hash=document.options_hash,
                content_sha256=document.content_sha256,
                markdown=document.markdown,
                page_count=document.page_count,
                processing_version=document.processing_version,
                error_code=document.error_code,
                error_message=document.error_message,
                created_at=_timestamp(document.created_at),
                updated_at=_timestamp(document.updated_at),
            )
        )
        await _flush_or_conflict(self._session, "add_source_document")

    async def publish_ready(
        self, identifier: str, expected_status: str, markdown: str,
        content_sha256: str, page_count: int, updated_at: datetime,
    ) -> bool:
        return await self._compare_and_set(
            identifier,
            expected_status,
            status="ready",
            markdown=markdown,
            content_sha256=content_sha256,
            page_count=page_count,
            error_code=None,
            error_message=None,
            updated_at=_timestamp(updated_at),
        )

    async def publish_failed(
        self, identifier: str, expected_status: str, error_code: str,
        error_message: str | None, updated_at: datetime,
    ) -> bool:
        return await self._compare_and_set(
            identifier,
            expected_status,
            status="failed",
            error_code=error_code,
            error_message=error_message,
            updated_at=_timestamp(updated_at),
        )

    async def publish_stale(
        self, identifier: str, expected_status: str, error_code: str, updated_at: datetime,
    ) -> bool:
        return await self._compare_and_set(
            identifier,
            expected_status,
            status="stale",
            error_code=error_code,
            error_message=None,
            stale_at=_timestamp(updated_at),
            updated_at=_timestamp(updated_at),
        )

    async def _compare_and_set(self, identifier: str, expected_status: str, **values: object) -> bool:
        result = await self._session.execute(
            update(SourceDocumentModel)
            .where(
                SourceDocumentModel.id == identifier,
                SourceDocumentModel.status == expected_status,
            )
            .values(**values)
        )
        await _flush_or_conflict(self._session, "publish_source_document")
        return result.rowcount == 1


class SqlAlchemyGeneratedArtifactRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, identifier: str) -> GeneratedArtifact | None:
        return _artifact(await self._session.get(GeneratedArtifactModel, identifier))

    async def find_by_version_identity(
        self, identity: ArtifactVersionIdentity,
    ) -> GeneratedArtifact | None:
        statement = select(GeneratedArtifactModel).where(
            GeneratedArtifactModel.source_document_id == identity.source_document_id,
            GeneratedArtifactModel.kind == identity.kind.value,
            GeneratedArtifactModel.generator_provider == identity.generator_provider,
            GeneratedArtifactModel.generator_model == identity.generator_model,
            GeneratedArtifactModel.prompt_version == identity.prompt_version,
        )
        return _artifact((await self._session.execute(statement)).scalar_one_or_none())

    async def find_ready_for_paper(
        self, paper_id: str, kind: ArtifactKind,
    ) -> GeneratedArtifact | None:
        statement = (
            select(GeneratedArtifactModel)
            .join(
                SourceDocumentModel,
                SourceDocumentModel.id == GeneratedArtifactModel.source_document_id,
            )
            .where(
                GeneratedArtifactModel.paper_id == paper_id,
                GeneratedArtifactModel.kind == kind.value,
                GeneratedArtifactModel.status == "ready",
                SourceDocumentModel.status == "ready",
            )
            .order_by(GeneratedArtifactModel.updated_at.desc(), GeneratedArtifactModel.id.desc())
            .limit(1)
        )
        try:
            row = (await self._session.execute(statement)).scalar_one_or_none()
        except SQLAlchemyError as error:
            raise PersistenceReadError(operation="read_ready_artifact") from error
        return _artifact(row)

    async def add(self, artifact: GeneratedArtifact) -> None:
        self._session.add(
            GeneratedArtifactModel(
                id=artifact.id,
                paper_id=artifact.paper_id,
                kind=artifact.kind.value,
                source_document_id=artifact.source_document_id,
                status=artifact.status.value,
                content=artifact.content,
                content_sha256=artifact.content_sha256,
                generator_provider=artifact.generator_provider,
                generator_model=artifact.generator_model,
                prompt_version=artifact.prompt_version,
                error_code=artifact.error_code,
                error_message=artifact.error_message,
                created_at=_timestamp(artifact.created_at),
                updated_at=_timestamp(artifact.updated_at),
            )
        )
        await _flush_or_conflict(self._session, "add_generated_artifact")

    async def publish_ready(
        self, identifier: str, expected_status: str, content: str,
        content_sha256: str, updated_at: datetime,
    ) -> bool:
        return await self._compare_and_set(
            identifier, expected_status, status="ready", content=content,
            content_sha256=content_sha256, error_code=None, error_message=None,
            updated_at=_timestamp(updated_at),
        )

    async def publish_failed(
        self, identifier: str, expected_status: str, error_code: str,
        error_message: str | None, updated_at: datetime,
    ) -> bool:
        return await self._compare_and_set(
            identifier, expected_status, status="failed", error_code=error_code,
            error_message=error_message, updated_at=_timestamp(updated_at),
        )

    async def _compare_and_set(self, identifier: str, expected_status: str, **values: object) -> bool:
        result = await self._session.execute(
            update(GeneratedArtifactModel)
            .where(
                GeneratedArtifactModel.id == identifier,
                GeneratedArtifactModel.status == expected_status,
            )
            .values(**values)
        )
        await _flush_or_conflict(self._session, "publish_generated_artifact")
        return result.rowcount == 1

    async def write_legacy_explainer(
        self,
        paper_id: str,
        content: str,
        updated_at: datetime,
    ) -> None:
        result = await self._session.execute(
            update(PaperModel)
            .where(PaperModel.id == paper_id)
            .values(explainer=content, updated_at=_timestamp(updated_at))
        )
        if result.rowcount != 1:
            raise PersistenceConflictError(operation="write_legacy_explainer")
        await self._session.execute(
            delete(PaperVectorModel).where(PaperVectorModel.paper_id == paper_id)
        )
        await _flush_or_conflict(self._session, "write_legacy_explainer")

    async def write_legacy_translation(
        self,
        paper_id: str,
        content: str,
        updated_at: datetime,
    ) -> None:
        statement = sqlite_insert(TranslationModel).values(
            paper_id=paper_id,
            content=content,
            updated_at=_timestamp(updated_at),
        )
        await self._session.execute(
            statement.on_conflict_do_update(
                index_elements=[TranslationModel.paper_id],
                set_={"content": content, "updated_at": _timestamp(updated_at)},
            )
        )
        await _flush_or_conflict(self._session, "write_legacy_translation")

    async def read_legacy(self, paper_id: str, kind: ArtifactKind) -> str | None:
        try:
            if kind is ArtifactKind.EXPLAINER:
                return (
                    await self._session.execute(
                        select(PaperModel.explainer).where(PaperModel.id == paper_id)
                    )
                ).scalar_one_or_none()
            if kind is ArtifactKind.TRANSLATION:
                return (
                    await self._session.execute(
                        select(TranslationModel.content).where(
                            TranslationModel.paper_id == paper_id
                        )
                    )
                ).scalar_one_or_none()
        except SQLAlchemyError as error:
            raise PersistenceReadError(operation="read_legacy_artifact") from error
        return None


class SqlAlchemyVaultProjectionRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, identifier: str) -> VaultProjection | None:
        return _projection(await self._session.get(VaultProjectionModel, identifier))

    async def find_by_target_path(self, path: str) -> VaultProjection | None:
        statement = select(VaultProjectionModel).where(VaultProjectionModel.target_path == path)
        return _projection((await self._session.execute(statement)).scalar_one_or_none())

    async def add(self, projection: VaultProjection) -> None:
        self._session.add(
            VaultProjectionModel(
                id=projection.id,
                paper_id=projection.paper_id,
                artifact_id=projection.artifact_id,
                target_path=projection.target_path,
                source_hash=projection.source_hash,
                exported_hash=projection.exported_hash,
                status=projection.status,
                exported_at=_timestamp(projection.exported_at),
                error_message=projection.error_message,
            )
        )
        await _flush_or_conflict(self._session, "add_vault_projection")


def _source(row: SourceDocumentModel | None) -> SourceDocument | None:
    if row is None:
        return None
    return SourceDocument(
        id=row.id, paper_id=row.paper_id, mode=row.mode, status=row.status,
        provider=row.provider, model=row.model, pdf_sha256=row.pdf_sha256,
        options_hash=row.options_hash, content_sha256=row.content_sha256,
        markdown=row.markdown, page_count=row.page_count,
        processing_version=row.processing_version, error_code=row.error_code,
        error_message=row.error_message, created_at=_datetime(row.created_at),
        updated_at=_datetime(row.updated_at),
    )


def _artifact(row: GeneratedArtifactModel | None) -> GeneratedArtifact | None:
    if row is None:
        return None
    return GeneratedArtifact(
        id=row.id, paper_id=row.paper_id, kind=row.kind,
        source_document_id=row.source_document_id, status=row.status,
        content=row.content, content_sha256=row.content_sha256,
        generator_provider=row.generator_provider, generator_model=row.generator_model,
        prompt_version=row.prompt_version, error_code=row.error_code,
        error_message=row.error_message, created_at=_datetime(row.created_at),
        updated_at=_datetime(row.updated_at),
    )


def _job(row: ProcessingJobModel | None) -> ProcessingJob | None:
    if row is None:
        return None
    return ProcessingJob(
        id=row.id, paper_id=row.paper_id, job_type=row.job_type,
        source_mode=row.source_mode, status=row.status, progress_json=row.progress_json,
        attempt=row.attempt, max_attempts=row.max_attempts,
        idempotency_key=row.idempotency_key, error_code=row.error_code,
        error_message=row.error_message, created_at=_datetime(row.created_at),
        started_at=_datetime(row.started_at), finished_at=_datetime(row.finished_at),
        cancelled_at=_datetime(row.cancelled_at),
    )


def _projection(row: VaultProjectionModel | None) -> VaultProjection | None:
    if row is None:
        return None
    return VaultProjection(
        id=row.id, paper_id=row.paper_id, artifact_id=row.artifact_id,
        target_path=row.target_path, source_hash=row.source_hash,
        exported_hash=row.exported_hash, status=row.status,
        exported_at=_datetime(row.exported_at), error_message=row.error_message,
    )
