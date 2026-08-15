from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import delete, select, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import SQLAlchemyError

from backend.app.domain import (
    PersistenceConflictError,
    PersistenceReadError,
    VaultProjection,
)
from backend.app.repositories.models import PaperModel, VaultProjectionModel


_SUCCESS_STATUSES = frozenset({"exported", "unchanged"})


class SqlAlchemyObsidianExportsRepository:
    """Short-transaction adapter for the fixed P1 Obsidian export ledger."""

    def __init__(self, session_factory: Any) -> None:
        self._session_factory = session_factory

    async def list_papers_for_pdf_migration(self) -> list[dict[str, object]]:
        try:
            async with self._session_factory() as session:
                rows = (
                    await session.execute(
                        select(PaperModel.id, PaperModel.pdf_path).order_by(
                            PaperModel.id
                        )
                    )
                ).all()
        except SQLAlchemyError as error:
            raise PersistenceReadError(operation="list_papers_for_pdf_migration") from error
        return [
            {"id": str(row.id), "pdf_path": row.pdf_path}
            for row in rows
        ]

    async def get_paper_pdf_path(self, paper_id: str) -> str | None:
        try:
            async with self._session_factory() as session:
                row = (
                    await session.execute(
                        select(PaperModel.pdf_path).where(PaperModel.id == paper_id)
                    )
                ).one_or_none()
        except SQLAlchemyError as error:
            raise PersistenceReadError(operation="read_paper_pdf_path") from error
        return row.pdf_path if row is not None else None

    async def compare_and_set_paper_pdf_path(
        self,
        paper_id: str,
        *,
        expected: str | None,
        replacement: str | None,
    ) -> bool:
        try:
            async with self._session_factory() as session:
                result = await session.execute(
                    update(PaperModel)
                    .where(
                        PaperModel.id == paper_id,
                        PaperModel.pdf_path == expected,
                    )
                    .values(pdf_path=replacement)
                )
                await session.commit()
                return result.rowcount == 1
        except SQLAlchemyError as error:
            raise PersistenceConflictError(operation="update_paper_pdf_path") from error

    async def restore_projection(
        self,
        *,
        expected: VaultProjection,
        prior: VaultProjection | None,
    ) -> bool:
        predicates = _projection_predicates(expected)
        statement = (
            delete(VaultProjectionModel).where(*predicates)
            if prior is None
            else update(VaultProjectionModel)
            .where(*predicates)
            .values(**_projection_values(prior))
        )
        try:
            async with self._session_factory() as session:
                result = await session.execute(statement)
                await session.commit()
                return result.rowcount == 1
        except SQLAlchemyError as error:
            raise PersistenceConflictError(operation="restore_obsidian_export") from error

    async def upsert(self, projection: VaultProjection) -> VaultProjection:
        successful = projection.status in _SUCCESS_STATUSES
        values = {
            "id": projection.id,
            "paper_id": projection.paper_id,
            "artifact_id": projection.artifact_id,
            "target_path": projection.target_path,
            "source_hash": projection.source_hash,
            "exported_hash": projection.exported_hash if successful else None,
            "status": projection.status,
            "exported_at": _timestamp(projection.exported_at) if successful else None,
            "error_message": projection.error_message,
        }
        statement = sqlite_insert(VaultProjectionModel).values(**values)
        updates: dict[str, object] = {
            "paper_id": statement.excluded.paper_id,
            "artifact_id": statement.excluded.artifact_id,
            "source_hash": statement.excluded.source_hash,
            "status": statement.excluded.status,
            "error_message": statement.excluded.error_message,
        }
        if successful:
            updates.update(
                {
                    "exported_hash": statement.excluded.exported_hash,
                    "exported_at": statement.excluded.exported_at,
                }
            )
        try:
            async with self._session_factory() as session:
                await session.execute(
                    statement.on_conflict_do_update(
                        index_elements=[VaultProjectionModel.target_path],
                        set_=updates,
                    )
                )
                await session.commit()
        except SQLAlchemyError as error:
            raise PersistenceConflictError(operation="upsert_obsidian_export") from error
        persisted = await self.find_by_target_path(projection.target_path)
        if persisted is None:
            raise PersistenceConflictError(operation="read_after_obsidian_export_upsert")
        return persisted

    async def find_by_target_path(self, target_path: str) -> VaultProjection | None:
        try:
            async with self._session_factory() as session:
                row = (
                    await session.execute(
                        select(VaultProjectionModel).where(
                            VaultProjectionModel.target_path == target_path
                        )
                    )
                ).scalar_one_or_none()
        except SQLAlchemyError as error:
            raise PersistenceReadError(operation="read_obsidian_export") from error
        return _projection(row)

    async def get(self, identifier: str) -> VaultProjection | None:
        try:
            async with self._session_factory() as session:
                row = await session.get(VaultProjectionModel, identifier)
        except SQLAlchemyError as error:
            raise PersistenceReadError(operation="read_obsidian_export") from error
        return _projection(row)

    async def find_cleanup_projection(
        self,
        *,
        paper_id: str,
        target_path: str,
        source_hash: str,
        exported_hash: str,
    ) -> VaultProjection | None:
        try:
            async with self._session_factory() as session:
                row = (
                    await session.execute(
                        select(VaultProjectionModel)
                        .join(PaperModel, PaperModel.id == VaultProjectionModel.paper_id)
                        .where(
                            VaultProjectionModel.paper_id == paper_id,
                            VaultProjectionModel.target_path == target_path,
                            VaultProjectionModel.source_hash == source_hash,
                            VaultProjectionModel.exported_hash == exported_hash,
                            VaultProjectionModel.status.in_(("exported", "unchanged")),
                        )
                    )
                ).scalar_one_or_none()
        except SQLAlchemyError as error:
            raise PersistenceReadError(operation="read_obsidian_cleanup_proof") from error
        return _projection(row)

    async def delete_if_matches(self, projection: VaultProjection) -> bool:
        try:
            async with self._session_factory() as session:
                result = await session.execute(
                    delete(VaultProjectionModel).where(
                        VaultProjectionModel.id == projection.id,
                        VaultProjectionModel.paper_id == projection.paper_id,
                        VaultProjectionModel.target_path == projection.target_path,
                        VaultProjectionModel.source_hash == projection.source_hash,
                        VaultProjectionModel.exported_hash == projection.exported_hash,
                        VaultProjectionModel.status == projection.status,
                    )
                )
                await session.commit()
                return result.rowcount == 1
        except SQLAlchemyError as error:
            raise PersistenceConflictError(operation="delete_obsidian_export") from error


def _projection(row: VaultProjectionModel | None) -> VaultProjection | None:
    if row is None:
        return None
    return VaultProjection(
        id=row.id,
        paper_id=row.paper_id,
        artifact_id=row.artifact_id,
        target_path=row.target_path,
        source_hash=row.source_hash,
        exported_hash=row.exported_hash,
        status=row.status,
        exported_at=_datetime(row.exported_at),
        error_message=row.error_message,
    )


def _projection_values(projection: VaultProjection) -> dict[str, object]:
    return {
        "id": projection.id,
        "paper_id": projection.paper_id,
        "artifact_id": projection.artifact_id,
        "target_path": projection.target_path,
        "source_hash": projection.source_hash,
        "exported_hash": projection.exported_hash,
        "status": projection.status,
        "exported_at": _timestamp(projection.exported_at),
        "error_message": projection.error_message,
    }


def _projection_predicates(projection: VaultProjection) -> tuple[object, ...]:
    values = _projection_values(projection)
    return tuple(
        getattr(VaultProjectionModel, column) == value
        for column, value in values.items()
    )


def _timestamp(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _datetime(value: str | None) -> datetime | None:
    if value is None:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


__all__ = ["SqlAlchemyObsidianExportsRepository"]
