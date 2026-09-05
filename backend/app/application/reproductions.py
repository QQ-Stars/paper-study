from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import datetime, timezone
import hashlib
import json
import logging
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import tempfile
from urllib.parse import urlparse
from uuid import uuid4

from backend.app.domain import (
    ARTICLE_DOCUMENT,
    DEFAULT_DOCUMENT,
    PublicationNotFoundError,
    PublicationValidationError,
    ReproductionArchivedError,
    ReproductionConflictError,
    ReproductionNotFoundError,
    ReproductionValidationError,
    ShowcaseExportError,
    validate_publication_decision,
    validate_project_kind,
    validate_project_status,
    validate_result_status,
    validate_run_status,
    validate_sha256,
)
from backend.app.application.showcase_export import (
    PUBLICATION_CONCLUSIONS,
    ShowcaseExporter,
    normalize_slug,
    validate_publication_snapshot,
)


LOGGER = logging.getLogger(__name__)


class ReproductionWorkspace:
    """Deep application seam for project/document/run/artifact lifecycle."""

    def __init__(
        self,
        work_factory: Callable[[], object],
        *,
        clock: Callable[[], datetime] | None = None,
        artifact_root: Path | None = None,
        showcase_root: Path | None = None,
    ) -> None:
        self._work_factory = work_factory
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._artifact_root = (
            artifact_root.expanduser().resolve()
            if artifact_root is not None
            else Path.cwd().joinpath("data", "reproduction-artifacts").resolve()
        )
        self._showcase_root = (
            showcase_root.expanduser().resolve()
            if showcase_root is not None
            else Path(__file__).resolve().parents[3].joinpath("paper-showcase").resolve()
        )
        self._showcase_exporter = ShowcaseExporter(
            self._showcase_root,
            artifact_resolver=lambda storage_key, project_id: self.artifact_path(
                storage_key,
                project_id=project_id,
            ),
            clock=self._clock,
        )

    def artifact_path(self, storage_key: str, *, project_id: str | None = None) -> Path:
        """Resolve a persisted artifact key without allowing filesystem escape."""
        if not isinstance(storage_key, str) or not storage_key:
            raise ReproductionValidationError()
        if project_id is not None:
            _validate_project_id(project_id)
        relative = PurePosixPath(storage_key)
        if relative.is_absolute() or any(part in {"", ".", ".."} for part in relative.parts):
            raise ReproductionValidationError()
        target_root = self._artifact_root
        target = (target_root / Path(*relative.parts)).resolve()
        if target_root not in target.parents:
            raise ReproductionValidationError()
        if project_id is not None:
            project_root = (target_root / "projects" / project_id).resolve()
            try:
                target.relative_to(project_root)
            except ValueError as error:
                raise ReproductionValidationError() from error
        return target

    async def list_projects(
        self,
        *,
        query: str | None,
        status: str | None,
        tag: str | None,
        paper_id: str | None = None,
        project_kind: str | None = None,
        sort: str = "updated",
        limit: int,
        offset: int,
    ) -> dict[str, object]:
        if status is not None:
            try:
                validate_project_status(status)
            except ValueError as error:
                raise ReproductionValidationError() from error
        if project_kind is not None:
            try:
                validate_project_kind(project_kind)
            except ValueError as error:
                raise ReproductionValidationError() from error
        if paper_id is not None:
            paper_id = paper_id.strip()
            if not paper_id or len(paper_id) > 200:
                raise ReproductionValidationError()
        async with self._work_factory() as work:
            items, total = await work.reproductions.list_projects(
                query=query.strip() if isinstance(query, str) and query.strip() else None,
                status=status, tag=tag.strip() if isinstance(tag, str) and tag.strip() else None,
                paper_id=paper_id,
                project_kind=project_kind,
                sort=sort, limit=limit, offset=offset,
            )
        return {"items": items, "total": total, "limit": limit, "offset": offset}

    async def get_project(self, project_id: str) -> dict[str, object]:
        project = await self._load_project(project_id)
        if not self._workspace_is_current(project):
            self._try_write_project_workspace(project)
        return project

    async def _load_project(self, project_id: str) -> dict[str, object]:
        _validate_project_id(project_id)
        async with self._work_factory() as work:
            project = await work.reproductions.get_project(project_id)
            if project is None:
                raise ReproductionNotFoundError(project_id=project_id)
            project["runs"] = await work.reproductions.list_runs(project_id)
            project["artifacts"] = await work.reproductions.list_artifacts(project_id)
            project["notes"] = await work.reproductions.list_notes(project_id)
            project["results"] = await work.reproductions.list_results(project_id)
        return project

    async def _sync_project_workspace(self, project_id: str) -> dict[str, object]:
        project = await self._load_project(project_id)
        self._try_write_project_workspace(project)
        return project

    def _try_write_project_workspace(self, project: Mapping[str, object]) -> None:
        try:
            self._write_project_workspace(project)
        except OSError as error:
            LOGGER.warning(
                "reproduction workspace mirror sync deferred for %s: %s",
                project.get("id"),
                type(error).__name__,
            )

    async def create_project(
        self,
        *,
        paper_id: str | None,
        name: str,
        tags: list[str],
        project_kind: str = "reproduction",
    ) -> dict[str, object]:
        name = name.strip()
        if not name or len(name) > 200:
            raise ReproductionValidationError()
        try:
            validate_project_kind(project_kind)
        except ValueError as error:
            raise ReproductionValidationError() from error
        paper_id = paper_id.strip() if isinstance(paper_id, str) and paper_id.strip() else None
        if project_kind == "reproduction" and paper_id is None:
            raise ReproductionValidationError()
        cleaned_tags = _tags(tags)
        now = _timestamp(self._clock())
        project_id = f"repro_{uuid4().hex}"
        document_id = f"rdoc_{uuid4().hex}"
        async with self._work_factory() as work:
            paper = await work.papers.get_legacy(paper_id) if paper_id is not None else None
            if paper_id is not None and paper is None:
                from backend.app.domain import MissingPaperError
                raise MissingPaperError(paper_id=paper_id)
            await work.reproductions.add_project(
                {
                    "id": project_id,
                    "project_kind": project_kind,
                    "paper_id": paper_id,
                    "paper_title": str(paper.get("title") or "Untitled paper") if paper is not None else "独立文章",
                    "name": name, "status": "planned", "tags_json": json.dumps(cleaned_tags, ensure_ascii=False),
                    "revision": 1, "created_at": now, "updated_at": now,
                },
                {
                    "id": document_id,
                    "project_id": project_id,
                    "content": DEFAULT_DOCUMENT if project_kind == "reproduction" else ARTICLE_DOCUMENT,
                    "revision": 1, "save_status": "saved", "created_at": now, "updated_at": now,
                },
            )
            await work.commit()
        return await self._sync_project_workspace(project_id)

    async def copy_project(
        self,
        project_id: str,
        *,
        name: str | None = None,
        content: str | None = None,
    ) -> dict[str, object]:
        """Create an independent project snapshot with project-owned artifacts."""
        source = await self.get_project(project_id)
        copy_name = name.strip() if isinstance(name, str) else f"{source['name']} 副本"
        if not copy_name or len(copy_name) > 200:
            raise ReproductionValidationError()
        source_document = source.get("document")
        if not isinstance(source_document, dict):
            raise ReproductionValidationError()
        document_content = content if content is not None else source_document.get("content")
        if not isinstance(document_content, str) or len(document_content) > 2_000_000:
            raise ReproductionValidationError()

        now = _timestamp(self._clock())
        copy_id = f"repro_{uuid4().hex}"
        document_id = f"rdoc_{uuid4().hex}"
        copy_dir: Path | None = None
        copied_artifacts: list[dict[str, object]] = []
        try:
            copy_dir = self._artifact_project_dir(copy_id, create=True)
            artifacts_dir = self._artifact_assets_dir(copy_id, create=True)
            for artifact in source.get("artifacts", []):
                if not isinstance(artifact, Mapping):
                    raise ReproductionValidationError()
                source_artifact_id = _required_text(artifact.get("id"), 100)
                source_storage_key = _safe_storage_key(
                    _required_text(artifact.get("storageKey"), 500)
                )
                source_path = self.artifact_path(source_storage_key, project_id=project_id)
                if not source_path.is_file() or source_path.is_symlink():
                    raise ReproductionValidationError()
                artifact_id = f"artifact_{uuid4().hex}"
                relative = PurePosixPath(
                    "projects",
                    copy_id,
                    "artifacts",
                    artifact_id + source_path.suffix.lower(),
                )
                target = self.artifact_path(relative.as_posix(), project_id=copy_id)
                if target.parent != artifacts_dir.resolve():
                    raise ReproductionValidationError()
                size, digest = _copy_verified_file(
                    source_path,
                    target,
                    expected_size=_optional_int(artifact.get("sizeBytes")),
                    expected_sha256=_required_text(artifact.get("sha256"), 64),
                )
                copied_artifacts.append(
                    {
                        "id": artifact_id,
                        "project_id": copy_id,
                        "run_id": None,
                        "kind": _required_text(artifact.get("kind"), 80),
                        "filename": _required_text(artifact.get("filename"), 255),
                        "storage_key": relative.as_posix(),
                        "mime_type": _required_text(artifact.get("mimeType"), 120),
                        "size_bytes": size,
                        "sha256": digest,
                        "created_at": now,
                    }
                )
                document_content = document_content.replace(
                    _artifact_download_url(project_id, source_artifact_id),
                    _artifact_download_url(copy_id, artifact_id),
                )

            async with self._work_factory() as work:
                current = await work.reproductions.get_project(project_id)
                if current is None:
                    raise ReproductionNotFoundError(project_id=project_id)
                await work.reproductions.add_project(
                    {
                        "id": copy_id,
                        "project_kind": source.get("projectKind") or "reproduction",
                        "paper_id": source.get("paperId"),
                        "paper_title": _required_text(source.get("paperTitle"), 10_000),
                        "name": copy_name,
                        "status": "planned",
                        "tags_json": json.dumps(source.get("tags", []), ensure_ascii=False),
                        "revision": 1,
                        "created_at": now,
                        "updated_at": now,
                    },
                    {
                        "id": document_id,
                        "project_id": copy_id,
                        "content": document_content,
                        "revision": 1,
                        "save_status": "saved",
                        "created_at": now,
                        "updated_at": now,
                    },
                )
                for artifact in copied_artifacts:
                    await work.reproductions.add_artifact(artifact)
                await work.commit()
        except Exception:
            if copy_dir is not None and copy_dir.is_dir() and not copy_dir.is_symlink():
                shutil.rmtree(copy_dir)
            raise
        return await self._sync_project_workspace(copy_id)

    async def update_project(self, project_id: str, *, expected_revision: int, name: str | None, status: str | None, tags: list[str] | None) -> dict[str, object]:
        if expected_revision < 1:
            raise ReproductionValidationError()
        if status is not None:
            try:
                validate_project_status(status)
            except ValueError as error:
                raise ReproductionValidationError() from error
        values: dict[str, object] = {"updated_at": _timestamp(self._clock())}
        if name is not None:
            if not name.strip() or len(name.strip()) > 200:
                raise ReproductionValidationError()
            values["name"] = name.strip()
        if status is not None:
            values["status"] = status
        if tags is not None:
            values["tags_json"] = json.dumps(_tags(tags), ensure_ascii=False)
        if status is not None and status != "completed":
            # Check the project version before any filesystem side effect. A
            # stale client must not revoke a still-live public snapshot.
            should_revoke = False
            async with self._work_factory() as work:
                current = await work.reproductions.get_project(project_id)
                if current is None:
                    raise ReproductionNotFoundError(project_id=project_id)
                if current["status"] != "archived":
                    if int(current["revision"]) != expected_revision:
                        raise ReproductionConflictError(
                            project_id=project_id,
                            expected_revision=expected_revision,
                        )
                    should_revoke = True
            if should_revoke:
                await self._revoke_if_public(
                    project_id,
                    expected_revision=expected_revision,
                )
        async with self._work_factory() as work:
            current = await work.reproductions.get_project(project_id)
            if current is None:
                raise ReproductionNotFoundError(project_id=project_id)
            if current["status"] == "archived":
                # Archived projects are immutable. Keep an idempotent PATCH
                # with no edits (or status=archived) harmless for clients that
                # retry a request after the archive transition.
                if name is not None or tags is not None or (status is not None and status != "archived"):
                    raise ReproductionArchivedError(project_id=project_id)
            else:
                if not await work.reproductions.update_project(project_id, values=values, expected_revision=expected_revision):
                    raise ReproductionConflictError(project_id=project_id, expected_revision=expected_revision)
                await work.reproductions.mark_publication_stale(
                    project_id,
                    updated_at=str(values["updated_at"]),
                )
                await work.commit()
        return await self._sync_project_workspace(project_id)

    async def archive_project(self, project_id: str, *, expected_revision: int) -> dict[str, object]:
        already_archived = False
        async with self._work_factory() as work:
            current = await work.reproductions.get_project(project_id)
            if current is None:
                raise ReproductionNotFoundError(project_id=project_id)
            if current["status"] == "archived":
                already_archived = True
            elif int(current["revision"]) != expected_revision:
                raise ReproductionConflictError(
                    project_id=project_id,
                    expected_revision=expected_revision,
                )
        if already_archived:
            return await self._sync_project_workspace(project_id)
        await self._revoke_if_public(
            project_id,
            expected_revision=expected_revision,
        )
        async with self._work_factory() as work:
            current = await work.reproductions.get_project(project_id)
            if current is None:
                raise ReproductionNotFoundError(project_id=project_id)
            if current["status"] != "archived":
                if not await work.reproductions.archive(project_id, expected_revision=expected_revision):
                    raise ReproductionConflictError(project_id=project_id, expected_revision=expected_revision)
                await work.commit()
        return await self._sync_project_workspace(project_id)

    async def delete_project(self, project_id: str, *, expected_revision: int) -> None:
        _validate_project_id(project_id)
        missing_from_database = False
        async with self._work_factory() as work:
            current = await work.reproductions.get_project(project_id)
            if current is None:
                missing_from_database = True
            else:
                if current["status"] != "archived":
                    raise ReproductionValidationError()
                if current["revision"] != expected_revision:
                    raise ReproductionConflictError(
                        project_id=project_id,
                        expected_revision=expected_revision,
                    )
                await work.reproductions.delete_project(project_id)
                await work.commit()
        project_dir = self._artifact_project_dir(project_id, create=False)
        if project_dir.is_dir() and not project_dir.is_symlink():
            try:
                shutil.rmtree(project_dir)
            except FileNotFoundError:
                pass
            return
        if missing_from_database:
            raise ReproductionNotFoundError(project_id=project_id)

    async def save_document(self, project_id: str, *, content: str, expected_revision: int) -> dict[str, object]:
        if not isinstance(content, str) or len(content) > 2_000_000 or expected_revision < 1:
            raise ReproductionValidationError()
        now = _timestamp(self._clock())
        async with self._work_factory() as work:
            current = await work.reproductions.get_project(project_id)
            if current is None:
                raise ReproductionNotFoundError(project_id=project_id)
            if current["status"] == "archived":
                raise ReproductionArchivedError(project_id=project_id)
            if not await work.reproductions.save_document(project_id, content=content, expected_revision=expected_revision, updated_at=now):
                raise ReproductionConflictError(project_id=project_id, expected_revision=expected_revision)
            await work.reproductions.mark_publication_stale(project_id, updated_at=now)
            await work.commit()
        result = await self._sync_project_workspace(project_id)
        document = result.get("document")
        if not isinstance(document, dict):
            raise ReproductionValidationError()
        saved_document = dict(document)
        saved_document["projectRevision"] = result.get("revision")
        return saved_document

    async def get_publication(self, project_id: str) -> dict[str, object]:
        _, _, publication = await self._load_publication_snapshot(project_id, create=True)
        if publication is None:
            raise PublicationNotFoundError(project_id=project_id)
        return publication

    async def save_publication(
        self,
        project_id: str,
        body: Mapping[str, object],
    ) -> dict[str, object]:
        project, paper, current = await self._load_publication_snapshot(project_id, create=True)
        if current is None:
            raise PublicationNotFoundError(project_id=project_id)
        expected_revision = body.get("expectedRevision")
        if expected_revision is not None:
            if isinstance(expected_revision, bool) or not isinstance(expected_revision, int):
                raise ReproductionValidationError()
            if expected_revision != current["revision"]:
                raise ReproductionConflictError(
                    project_id=project_id,
                    expected_revision=expected_revision,
                )
        updated = _publication_patch(
            current,
            body,
            project=project,
            paper=paper,
            now=_timestamp(self._clock()),
        )
        if _has_managed_export(current) and updated.get("decision") != "approved":
            try:
                self._showcase_exporter.revoke(project_id=project_id)
            except (OSError, UnicodeError, ValueError) as error:
                raise ShowcaseExportError(project_id=project_id) from error
            now = _timestamp(self._clock())
            updated.update(
                {
                    "status": "revoked",
                    "revokedAt": now,
                    "contentHash": None,
                    "validationPassed": False,
                    "validationErrors": [],
                    "exportError": None,
                    "updatedAt": now,
                }
            )
        return await self._persist_publication(updated)

    async def validate_publication(self, project_id: str) -> dict[str, object]:
        project, paper, publication = await self._load_publication_snapshot(project_id, create=True)
        if publication is None:
            raise PublicationNotFoundError(project_id=project_id)
        validation = validate_publication_snapshot(
            project,
            paper,
            publication,
            artifact_resolver=lambda storage_key, scoped_project: self.artifact_path(
                storage_key,
                project_id=scoped_project,
            ),
        )
        now = _timestamp(self._clock())
        has_managed_export = _has_managed_export(publication)
        updated = {
            **publication,
            "validationPassed": validation.ok,
            "validationErrors": list(validation.errors),
            "status": (
                "published"
                if publication["status"] == "published" and validation.ok
                else "stale"
                if has_managed_export
                else "draft"
                if validation.ok
                else "failed"
            ),
            "exportError": None if validation.ok else "PUBLICATION_VALIDATION_FAILED",
            "revision": int(publication["revision"]) + 1,
            "updatedAt": now,
        }
        saved = await self._persist_publication(updated)
        return {**validation.as_dict(), "publication": saved}

    async def publish_publication(
        self,
        project_id: str,
        *,
        expected_revision: int | None = None,
    ) -> dict[str, object]:
        project, paper, publication = await self._load_publication_snapshot(project_id, create=True)
        if publication is None:
            raise PublicationNotFoundError(project_id=project_id)
        _require_publication_revision(project_id, publication, expected_revision)
        validation = validate_publication_snapshot(
            project,
            paper,
            publication,
            artifact_resolver=lambda storage_key, scoped_project: self.artifact_path(
                storage_key,
                project_id=scoped_project,
            ),
        )
        if not validation.ok:
            await self._persist_publication(
                {
                    **publication,
                    "status": "stale" if _has_managed_export(publication) else "failed",
                    "validationPassed": False,
                    "validationErrors": list(validation.errors),
                    "exportError": "PUBLICATION_VALIDATION_FAILED",
                    "revision": int(publication["revision"]) + 1,
                    "updatedAt": _timestamp(self._clock()),
                }
            )
            raise PublicationValidationError(project_id=project_id)
        try:
            exported = self._showcase_exporter.export(
                project=project,
                paper=paper,
                publication=publication,
            )
        except PublicationValidationError:
            raise
        except (OSError, UnicodeError, ValueError) as error:
            await self._persist_publication(
                {
                    **publication,
                    "status": "stale" if _has_managed_export(publication) else "failed",
                    "validationPassed": False,
                    "validationErrors": [],
                    "exportError": type(error).__name__,
                    "revision": int(publication["revision"]) + 1,
                    "updatedAt": _timestamp(self._clock()),
                }
            )
            raise ShowcaseExportError(project_id=project_id) from error
        saved = await self._persist_publication(
            {
                **publication,
                "decision": "approved",
                "status": "published",
                "validationPassed": True,
                "validationErrors": [],
                "approvedAt": publication.get("approvedAt") or exported.exported_at,
                "revokedAt": None,
                "contentHash": exported.content_hash,
                "lastExportedAt": exported.exported_at,
                "exportError": None,
                "revision": int(publication["revision"]) + 1,
                "updatedAt": exported.exported_at,
            }
        )
        return {
            "publication": saved,
            "url": exported.url,
            "files": list(exported.files),
        }

    async def revoke_publication(
        self,
        project_id: str,
        *,
        expected_revision: int | None = None,
    ) -> dict[str, object]:
        _, _, publication = await self._load_publication_snapshot(project_id, create=False)
        if publication is None:
            raise PublicationNotFoundError(project_id=project_id)
        _require_publication_revision(project_id, publication, expected_revision)
        try:
            removed = self._showcase_exporter.revoke(project_id=project_id)
        except (OSError, UnicodeError, ValueError) as error:
            raise ShowcaseExportError(project_id=project_id) from error
        now = _timestamp(self._clock())
        saved = await self._persist_publication(
            {
                **publication,
                "decision": "revoked",
                "status": "revoked",
                "validationPassed": False,
                "validationErrors": [],
                "revokedAt": now,
                "contentHash": None,
                "exportError": None,
                "revision": int(publication["revision"]) + 1,
                "updatedAt": now,
            }
        )
        return {"publication": saved, "removedFiles": list(removed)}

    async def _revoke_if_public(
        self,
        project_id: str,
        *,
        expected_revision: int | None = None,
    ) -> None:
        """Withdraw static output before a project becomes immutable/archived."""
        _validate_project_id(project_id)
        async with self._work_factory() as work:
            current = await work.reproductions.get_project(project_id)
            if current is None:
                raise ReproductionNotFoundError(project_id=project_id)
            if expected_revision is not None and int(current["revision"]) != expected_revision:
                raise ReproductionConflictError(
                    project_id=project_id,
                    expected_revision=expected_revision,
                )
            publication = await work.reproductions.get_publication(project_id)
        if publication is None or not _has_managed_export(publication):
            return
        await self.revoke_publication(project_id)

    async def _load_publication_snapshot(
        self,
        project_id: str,
        *,
        create: bool,
    ) -> tuple[dict[str, object], dict[str, object] | None, dict[str, object] | None]:
        _validate_project_id(project_id)
        async with self._work_factory() as work:
            project = await work.reproductions.get_project(project_id)
            if project is None:
                raise ReproductionNotFoundError(project_id=project_id)
            project["runs"] = await work.reproductions.list_runs(project_id)
            project["artifacts"] = await work.reproductions.list_artifacts(project_id)
            project["notes"] = await work.reproductions.list_notes(project_id)
            project["results"] = await work.reproductions.list_results(project_id)
            paper_id = project.get("paperId")
            paper = await work.papers.get_legacy(str(paper_id)) if paper_id else None
            publication = await work.reproductions.get_publication(project_id)
            if publication is None and create:
                values = _new_publication_values(
                    project,
                    paper,
                    now=_timestamp(self._clock()),
                )
                publication = await work.reproductions.save_publication(values)
            await work.commit()
        return project, paper, publication

    async def _persist_publication(
        self,
        publication: Mapping[str, object],
    ) -> dict[str, object]:
        project_id = _required_text(publication.get("projectId"), 100)
        async with self._work_factory() as work:
            if await work.reproductions.get_project(project_id) is None:
                raise ReproductionNotFoundError(project_id=project_id)
            saved = await work.reproductions.save_publication(
                _publication_storage_values(publication)
            )
            await work.commit()
        return saved

    async def add_run(self, project_id: str, body: Mapping[str, object]) -> dict[str, object]:
        status = str(body.get("status") or "planned")
        try:
            validate_run_status(status)
        except ValueError as error:
            raise ReproductionValidationError() from error
        async with self._work_factory() as work:
            current = await work.reproductions.get_project(project_id)
            if current is None:
                raise ReproductionNotFoundError(project_id=project_id)
            _ensure_experiment_project(current)
            if current["status"] == "archived":
                raise ReproductionArchivedError(project_id=project_id)
            now = _timestamp(self._clock())
            run_id = f"run_{uuid4().hex}"
            await work.reproductions.add_run({
                "id": run_id, "project_id": project_id,
                "name": _optional_text(body.get("name")) or "实验运行",
                "environment": _optional_text(body.get("environment")),
                "command": _optional_text(body.get("command")), "parameters_json": _json_dict(body.get("parameters")),
                "data_version": _optional_text(body.get("dataVersion")), "code_revision": _optional_text(body.get("codeRevision")),
                "seed": _optional_int(body.get("seed")), "status": status, "metrics_json": _json_dict(body.get("metrics")),
                "result_summary": _optional_text(body.get("resultSummary")),
                "started_at": _optional_text(body.get("startedAt")), "finished_at": _optional_text(body.get("finishedAt")),
                "runtime_versions": _optional_text(body.get("runtimeVersions")), "dataset": _optional_text(body.get("dataset")),
                "preprocessing": _optional_text(body.get("preprocessing")), "repository_url": _optional_text(body.get("repositoryUrl")),
                "config": _optional_text(body.get("config")), "issues": _optional_text(body.get("issues")),
                "created_at": now, "updated_at": now,
            })
            await work.reproductions.mark_publication_stale(project_id, updated_at=now)
            await work.commit()
        project = await self._sync_project_workspace(project_id)
        return next(row for row in project["runs"] if row["id"] == run_id)

    async def update_run(self, project_id: str, run_id: str, body: Mapping[str, object]) -> dict[str, object]:
        values = _run_update_values(body, _timestamp(self._clock()))
        async with self._work_factory() as work:
            current = await work.reproductions.get_project(project_id)
            if current is None:
                raise ReproductionNotFoundError(project_id=project_id)
            _ensure_experiment_project(current)
            if current["status"] == "archived":
                raise ReproductionArchivedError(project_id=project_id)
            if not values or not await work.reproductions.update_run(project_id, run_id, values):
                raise ReproductionNotFoundError(project_id=project_id)
            await work.reproductions.mark_publication_stale(
                project_id,
                updated_at=str(values["updated_at"]),
            )
            await work.commit()
        project = await self._sync_project_workspace(project_id)
        return next(row for row in project["runs"] if row["id"] == run_id)

    async def delete_run(self, project_id: str, run_id: str) -> None:
        async with self._work_factory() as work:
            current = await work.reproductions.get_project(project_id)
            if current is None:
                raise ReproductionNotFoundError(project_id=project_id)
            _ensure_experiment_project(current)
            if current["status"] == "archived":
                raise ReproductionArchivedError(project_id=project_id)
            if not await work.reproductions.delete_run(project_id, run_id):
                raise ReproductionNotFoundError(project_id=project_id)
            await work.reproductions.mark_publication_stale(
                project_id,
                updated_at=_timestamp(self._clock()),
            )
            await work.commit()
        await self._sync_project_workspace(project_id)

    async def add_result(self, project_id: str, body: Mapping[str, object]) -> dict[str, object]:
        values = _result_values(project_id, body, _timestamp(self._clock()))
        async with self._work_factory() as work:
            current = await work.reproductions.get_project(project_id)
            if current is None:
                raise ReproductionNotFoundError(project_id=project_id)
            _ensure_experiment_project(current)
            if current["status"] == "archived":
                raise ReproductionArchivedError(project_id=project_id)
            await work.reproductions.add_result(values)
            await work.reproductions.mark_publication_stale(
                project_id,
                updated_at=str(values["updated_at"]),
            )
            await work.commit()
        await self._sync_project_workspace(project_id)
        return _result_public(values)

    async def update_result(self, project_id: str, result_id: str, body: Mapping[str, object]) -> dict[str, object]:
        now = _timestamp(self._clock())
        values = _result_update_values(body, now)
        async with self._work_factory() as work:
            current = await work.reproductions.get_project(project_id)
            if current is None:
                raise ReproductionNotFoundError(project_id=project_id)
            _ensure_experiment_project(current)
            if current["status"] == "archived":
                raise ReproductionArchivedError(project_id=project_id)
            if not values or not await work.reproductions.update_result(project_id, result_id, values):
                raise ReproductionNotFoundError(project_id=project_id)
            await work.reproductions.mark_publication_stale(
                project_id,
                updated_at=str(values["updated_at"]),
            )
            await work.commit()
        project = await self._sync_project_workspace(project_id)
        return next(item for item in project["results"] if item["id"] == result_id)

    async def delete_result(self, project_id: str, result_id: str) -> None:
        async with self._work_factory() as work:
            current = await work.reproductions.get_project(project_id)
            if current is None:
                raise ReproductionNotFoundError(project_id=project_id)
            _ensure_experiment_project(current)
            if current["status"] == "archived":
                raise ReproductionArchivedError(project_id=project_id)
            if not await work.reproductions.delete_result(project_id, result_id):
                raise ReproductionNotFoundError(project_id=project_id)
            await work.reproductions.mark_publication_stale(
                project_id,
                updated_at=_timestamp(self._clock()),
            )
            await work.commit()
        await self._sync_project_workspace(project_id)

    async def add_artifact(self, project_id: str, body: Mapping[str, object]) -> dict[str, object]:
        kind = _required_text(body.get("kind"), 80)
        filename = _required_text(body.get("filename"), 255)
        storage_key = _safe_storage_key(_required_text(body.get("storageKey"), 500))
        # Metadata-only artifact records still must be scoped to this project.
        # Reuse the same containment check as downloads so a caller cannot
        # register a key belonging to another project or outside the store.
        self.artifact_path(storage_key, project_id=project_id)
        mime_type = _required_text(body.get("mimeType"), 120)
        size_bytes = _optional_int(body.get("sizeBytes"))
        if size_bytes is None or size_bytes < 0:
            raise ReproductionValidationError()
        try:
            sha256 = validate_sha256(_required_text(body.get("sha256"), 64))
        except ValueError as error:
            raise ReproductionValidationError() from error
        async with self._work_factory() as work:
            current = await work.reproductions.get_project(project_id)
            if current is None:
                raise ReproductionNotFoundError(project_id=project_id)
            if current["status"] == "archived":
                raise ReproductionArchivedError(project_id=project_id)
            run_id = _optional_text(body.get("runId"))
            if run_id is not None and not await work.reproductions.run_exists(project_id, run_id):
                raise ReproductionValidationError()
            now = _timestamp(self._clock())
            values = {"id": f"artifact_{uuid4().hex}", "project_id": project_id, "run_id": run_id, "kind": kind, "filename": filename, "storage_key": storage_key, "mime_type": mime_type, "size_bytes": size_bytes, "sha256": sha256, "created_at": now}
            await work.reproductions.add_artifact(values)
            await work.reproductions.mark_publication_stale(project_id, updated_at=now)
            await work.commit()
        await self._sync_project_workspace(project_id)
        return {"id": values["id"], "projectId": project_id, "runId": run_id, "kind": kind, "filename": filename, "storageKey": storage_key, "mimeType": mime_type, "sizeBytes": size_bytes, "sha256": sha256, "createdAt": now}

    async def upload_artifact(
        self,
        project_id: str,
        *,
        filename: str,
        mime_type: str,
        stream: object,
        kind: str | None = None,
        run_id: str | None = None,
    ) -> dict[str, object]:
        """Stream one bounded attachment to an opaque server-owned path."""
        _validate_project_id(project_id)
        normalized_name, inferred_kind = _validate_upload_name(filename, mime_type)
        kind = (kind or inferred_kind).strip().lower() if isinstance(kind or inferred_kind, str) else inferred_kind
        if kind not in {"log", "markdown", "table", "data", "image", "document", "attachment"}:
            raise ReproductionValidationError()
        if run_id is not None and not re.fullmatch(r"run_[a-f0-9]{32}", run_id):
            raise ReproductionValidationError()
        async with self._work_factory() as work:
            current = await work.reproductions.get_project(project_id)
            if current is None:
                raise ReproductionNotFoundError(project_id=project_id)
            if current["status"] == "archived":
                raise ReproductionArchivedError(project_id=project_id)
            if run_id is not None and not await work.reproductions.run_exists(project_id, run_id):
                raise ReproductionValidationError()

        artifact_id = f"artifact_{uuid4().hex}"
        relative = PurePosixPath(
            "projects",
            project_id,
            "artifacts",
            artifact_id + Path(normalized_name).suffix.lower(),
        )
        target_root = self._artifact_root
        self._artifact_project_dir(project_id, create=True)
        artifacts_dir = self._artifact_assets_dir(project_id, create=True)
        target = (target_root / relative).resolve()
        if target_root not in target.parents or artifacts_dir.resolve() != target.parent:
            raise ReproductionValidationError()
        temp_path: Path | None = None
        size = 0
        digest = hashlib.sha256()
        signature = bytearray()
        persisted = False
        try:
            with tempfile.NamedTemporaryFile(
                mode="wb", prefix=f".{artifact_id}.", suffix=".tmp", dir=artifacts_dir, delete=False
            ) as handle:
                temp_path = Path(handle.name)
                while True:
                    chunk = await _read_upload_chunk(stream)
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > MAX_ARTIFACT_BYTES:
                        raise ReproductionValidationError()
                    if len(signature) < 32:
                        signature.extend(chunk[: 32 - len(signature)])
                    digest.update(chunk)
                    handle.write(chunk)
                handle.flush()
                os.fsync(handle.fileno())
            _validate_upload_signature(mime_type, bytes(signature))
            os.replace(temp_path, target)
            temp_path = None
            now = _timestamp(self._clock())
            values = {
                "id": artifact_id, "project_id": project_id, "run_id": run_id,
                "kind": kind, "filename": normalized_name, "storage_key": relative.as_posix(),
                "mime_type": mime_type, "size_bytes": size, "sha256": digest.hexdigest(),
                "created_at": now,
            }
            async with self._work_factory() as work:
                current = await work.reproductions.get_project(project_id)
                if current is None:
                    raise ReproductionNotFoundError(project_id=project_id)
                if current["status"] == "archived":
                    raise ReproductionArchivedError(project_id=project_id)
                await work.reproductions.add_artifact(values)
                await work.reproductions.mark_publication_stale(project_id, updated_at=now)
                await work.commit()
            persisted = True
        except Exception:
            if temp_path is not None:
                temp_path.unlink(missing_ok=True)
            if not persisted:
                target.unlink(missing_ok=True)
            raise
        await self._sync_project_workspace(project_id)
        return {
            "id": artifact_id, "projectId": project_id, "runId": run_id, "kind": kind,
            "filename": normalized_name, "storageKey": relative.as_posix(),
            "mimeType": mime_type, "sizeBytes": size, "sha256": digest.hexdigest(),
            "createdAt": now,
        }

    def _artifact_project_dir(self, project_id: str, *, create: bool) -> Path:
        _validate_project_id(project_id)
        target_root = self._artifact_root
        projects_root = target_root / "projects"
        if create:
            projects_root.mkdir(parents=True, exist_ok=True)
        if projects_root.is_symlink():
            raise ReproductionValidationError()
        try:
            projects_root_resolved = projects_root.resolve(strict=False)
            projects_root_resolved.relative_to(target_root)
        except ValueError as error:
            raise ReproductionValidationError() from error
        project_dir = projects_root / project_id
        if create:
            project_dir.mkdir(parents=True, exist_ok=True)
        if project_dir.is_symlink():
            raise ReproductionValidationError()
        try:
            project_dir.resolve(strict=False).relative_to(projects_root_resolved)
        except ValueError as error:
            raise ReproductionValidationError() from error
        return project_dir

    def _artifact_assets_dir(self, project_id: str, *, create: bool) -> Path:
        project_dir = self._artifact_project_dir(project_id, create=create)
        artifacts_dir = project_dir / "artifacts"
        if create:
            artifacts_dir.mkdir(parents=True, exist_ok=True)
        if artifacts_dir.is_symlink():
            raise ReproductionValidationError()
        try:
            artifacts_dir.resolve(strict=False).relative_to(project_dir.resolve(strict=False))
        except ValueError as error:
            raise ReproductionValidationError() from error
        return artifacts_dir

    def _workspace_is_current(self, project: Mapping[str, object]) -> bool:
        project_id = _required_text(project.get("id"), 100)
        project_dir = self._artifact_project_dir(project_id, create=False)
        if not project_dir.is_dir() or project_dir.is_symlink():
            return False
        artifacts_dir = self._artifact_assets_dir(project_id, create=False)
        if not artifacts_dir.is_dir() or artifacts_dir.is_symlink():
            return False
        files_are_complete = all(
            path.is_file() and not path.is_symlink()
            for path in (
                project_dir / "project.json",
                project_dir / "document.md",
                project_dir / "runs.json",
                project_dir / "results.json",
                project_dir / "notes.json",
            )
        )
        if not files_are_complete:
            return False
        try:
            manifest = json.loads((project_dir / "project.json").read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            return False
        return (
            isinstance(manifest, dict)
            and manifest.get("workspaceFingerprint") == _workspace_fingerprint(project)
        )

    def _write_project_workspace(self, project: Mapping[str, object]) -> None:
        project_id = _required_text(project.get("id"), 100)
        project_dir = self._artifact_project_dir(project_id, create=True)
        self._artifact_assets_dir(project_id, create=True)
        document = project.get("document")
        if not isinstance(document, Mapping):
            raise ReproductionValidationError()
        content = document.get("content")
        if not isinstance(content, str):
            raise ReproductionValidationError()
        runs = _list_of_mappings(project.get("runs"))
        results = _list_of_mappings(project.get("results"))
        notes = _list_of_mappings(project.get("notes"))
        artifacts = _list_of_mappings(project.get("artifacts"))
        manifest_artifacts: list[dict[str, object]] = []
        for artifact in artifacts:
            artifact_path = self.artifact_path(
                _required_text(artifact.get("storageKey"), 500),
                project_id=project_id,
            )
            try:
                relative_path = artifact_path.relative_to(project_dir.resolve()).as_posix()
            except ValueError as error:
                raise ReproductionValidationError() from error
            manifest_artifacts.append(
                {
                    "id": artifact.get("id"),
                    "filename": artifact.get("filename"),
                    "path": relative_path,
                    "kind": artifact.get("kind"),
                    "mimeType": artifact.get("mimeType"),
                    "sizeBytes": artifact.get("sizeBytes"),
                    "sha256": artifact.get("sha256"),
                    "runId": artifact.get("runId"),
                    "createdAt": artifact.get("createdAt"),
                }
            )
        manifest = {
            "schemaVersion": 1,
            "workspaceFingerprint": _workspace_fingerprint(project),
            "id": project_id,
            "projectKind": project.get("projectKind") or "reproduction",
            "name": project.get("name"),
            "paperId": project.get("paperId"),
            "paperTitle": project.get("paperTitle"),
            "status": project.get("status"),
            "tags": project.get("tags", []),
            "revision": project.get("revision"),
            "createdAt": project.get("createdAt"),
            "updatedAt": project.get("updatedAt"),
            "files": {
                "document": "document.md",
                "runs": "runs.json",
                "results": "results.json",
                "notes": "notes.json",
                "artifacts": "artifacts",
            },
            "document": {
                "id": document.get("id"),
                "revision": document.get("revision"),
                "saveStatus": document.get("saveStatus"),
                "updatedAt": document.get("updatedAt"),
            },
            "artifacts": manifest_artifacts,
        }
        _atomic_write_text(project_dir / "document.md", content)
        _atomic_write_json(project_dir / "runs.json", runs)
        _atomic_write_json(project_dir / "results.json", results)
        _atomic_write_json(project_dir / "notes.json", notes)
        # The manifest is the completion marker for one workspace sync.
        _atomic_write_json(project_dir / "project.json", manifest)

    async def add_note(self, project_id: str, content: str) -> dict[str, object]:
        if not isinstance(content, str) or not content.strip() or len(content) > 100_000:
            raise ReproductionValidationError()
        async with self._work_factory() as work:
            current = await work.reproductions.get_project(project_id)
            if current is None:
                raise ReproductionNotFoundError(project_id=project_id)
            if current["status"] == "archived":
                raise ReproductionArchivedError(project_id=project_id)
            now = _timestamp(self._clock())
            values = {"id": f"note_{uuid4().hex}", "project_id": project_id, "content": content, "created_at": now, "updated_at": now}
            await work.reproductions.add_note(values)
            await work.commit()
        await self._sync_project_workspace(project_id)
        return {"id": values["id"], "projectId": project_id, "content": content, "createdAt": now, "updatedAt": now}


_PUBLICATION_EDITABLE_FIELDS = {
    "decision",
    "stableSlug",
    "publicTitle",
    "publicSummary",
    "aggregateConclusion",
    "paperUrl",
    "codeUrl",
    "datasetUrls",
    "publicArtifactIds",
    "expectedRevision",
}


def _new_publication_values(
    project: Mapping[str, object],
    paper: Mapping[str, object] | None,
    *,
    now: str,
) -> dict[str, object]:
    project_id = _required_text(project.get("id"), 100)
    project_kind = str(project.get("projectKind") or "reproduction")
    runs = _list_of_mappings(project.get("runs"))
    code_url = next(
        (
            str(run.get("repositoryUrl"))
            for run in runs
            if isinstance(run.get("repositoryUrl"), str)
            and _valid_public_url(str(run.get("repositoryUrl")))
        ),
        None,
    )
    paper_url = None
    if paper is not None:
        paper_url = next(
            (
                str(paper.get(field))
                for field in ("url", "pdf_url")
                if isinstance(paper.get(field), str)
                and _valid_public_url(str(paper.get(field)))
            ),
            None,
        )
    public = {
        "projectId": project_id,
        "decision": "draft",
        "status": "draft",
        "stableSlug": normalize_slug(
            project.get("name"),
            fallback=f"{'article' if project_kind == 'article' else 'reproduction'}-{project_id[-8:]}",
        ),
        "publicTitle": project.get("name") or project.get("paperTitle"),
        "publicSummary": None,
        "aggregateConclusion": None,
        "paperUrl": paper_url,
        "codeUrl": code_url,
        "datasetUrls": [],
        "publicArtifactIds": [],
        "validationPassed": False,
        "validationErrors": [],
        "approvedAt": None,
        "revokedAt": None,
        "contentHash": None,
        "lastExportedAt": None,
        "exportError": None,
        "revision": 1,
        "createdAt": now,
        "updatedAt": now,
    }
    return _publication_storage_values(public)


def _publication_patch(
    current: Mapping[str, object],
    body: Mapping[str, object],
    *,
    project: Mapping[str, object],
    paper: Mapping[str, object] | None,
    now: str,
) -> dict[str, object]:
    del paper
    if any(key not in _PUBLICATION_EDITABLE_FIELDS for key in body):
        raise ReproductionValidationError()
    updated = dict(current)
    if "decision" in body:
        decision = str(body.get("decision") or "")
        try:
            validate_publication_decision(decision)
        except ValueError as error:
            raise ReproductionValidationError() from error
        updated["decision"] = decision
    if "publicTitle" in body:
        updated["publicTitle"] = _optional_public_text(body.get("publicTitle"), 300)
    if "publicSummary" in body:
        updated["publicSummary"] = _optional_public_text(body.get("publicSummary"), 20_000)
    if "aggregateConclusion" in body:
        conclusion = _optional_public_text(body.get("aggregateConclusion"), 40)
        if conclusion is not None and conclusion not in PUBLICATION_CONCLUSIONS:
            raise ReproductionValidationError()
        updated["aggregateConclusion"] = conclusion
    for field in ("paperUrl", "codeUrl"):
        if field in body:
            value = _optional_public_text(body.get(field), 2_000)
            if value is not None and not _valid_public_url(value):
                raise ReproductionValidationError()
            updated[field] = value
    if "datasetUrls" in body:
        updated["datasetUrls"] = _public_string_list(
            body.get("datasetUrls"),
            maximum=30,
            item_maximum=2_000,
            urls=True,
        )
    if "publicArtifactIds" in body:
        updated["publicArtifactIds"] = _public_string_list(
            body.get("publicArtifactIds"),
            maximum=200,
            item_maximum=100,
            urls=False,
        )
    if "stableSlug" in body:
        requested = normalize_slug(
            body.get("stableSlug"),
            fallback=(
                f"{'article' if project.get('projectKind') == 'article' else 'reproduction'}-"
                f"{str(project.get('id') or '')[-8:]}"
            ),
        )
        if current.get("lastExportedAt") and requested != current.get("stableSlug"):
            raise ReproductionValidationError()
        updated["stableSlug"] = requested
    elif not updated.get("stableSlug"):
        updated["stableSlug"] = normalize_slug(
            updated.get("publicTitle") or project.get("name"),
            fallback=(
                f"{'article' if project.get('projectKind') == 'article' else 'reproduction'}-"
                f"{str(project.get('id') or '')[-8:]}"
            ),
        )

    changed = any(
        updated.get(field) != current.get(field)
        for field in _PUBLICATION_EDITABLE_FIELDS
        if field != "expectedRevision"
    )
    decision = str(updated.get("decision") or "draft")
    if decision == "approved":
        updated["approvedAt"] = updated.get("approvedAt") or now
        updated["revokedAt"] = None
        if current.get("status") in {"revoked", "failed"}:
            updated["status"] = "draft"
    elif decision == "revoked":
        updated["status"] = "revoked"
        updated["revokedAt"] = now
    else:
        updated["status"] = "draft"
        updated["approvedAt"] = None
        updated["revokedAt"] = None
    if changed and _has_managed_export(current) and decision == "approved":
        updated["status"] = "stale"
    if changed:
        updated["validationPassed"] = False
        updated["validationErrors"] = []
        updated["exportError"] = None
    updated["revision"] = int(current.get("revision") or 0) + 1
    updated["updatedAt"] = now
    return updated


def _has_managed_export(publication: Mapping[str, object]) -> bool:
    """Return whether the showcase manifest still owns live files."""

    return bool(publication.get("contentHash"))


def _publication_storage_values(publication: Mapping[str, object]) -> dict[str, object]:
    return {
        "project_id": _required_text(publication.get("projectId"), 100),
        "decision": _required_text(publication.get("decision"), 20),
        "status": _required_text(publication.get("status"), 20),
        "stable_slug": _optional_public_text(publication.get("stableSlug"), 80),
        "public_title": _optional_public_text(publication.get("publicTitle"), 300),
        "public_summary": _optional_public_text(publication.get("publicSummary"), 20_000),
        "aggregate_conclusion": _optional_public_text(publication.get("aggregateConclusion"), 40),
        "paper_url": _optional_public_text(publication.get("paperUrl"), 2_000),
        "code_url": _optional_public_text(publication.get("codeUrl"), 2_000),
        "dataset_urls_json": json.dumps(
            _public_string_list(publication.get("datasetUrls"), maximum=30, item_maximum=2_000, urls=True),
            ensure_ascii=False,
        ),
        "public_artifact_ids_json": json.dumps(
            _public_string_list(publication.get("publicArtifactIds"), maximum=200, item_maximum=100, urls=False),
            ensure_ascii=False,
        ),
        "validation_passed": 1 if publication.get("validationPassed") is True else 0,
        "validation_errors_json": json.dumps(
            _public_string_list(publication.get("validationErrors"), maximum=200, item_maximum=500, urls=False),
            ensure_ascii=False,
        ),
        "approved_at": _optional_public_text(publication.get("approvedAt"), 100),
        "revoked_at": _optional_public_text(publication.get("revokedAt"), 100),
        "content_hash": _optional_public_text(publication.get("contentHash"), 64),
        "last_exported_at": _optional_public_text(publication.get("lastExportedAt"), 100),
        "export_error": _optional_public_text(publication.get("exportError"), 500),
        "revision": _optional_int(publication.get("revision")) or 1,
        "created_at": _required_text(publication.get("createdAt"), 100),
        "updated_at": _required_text(publication.get("updatedAt"), 100),
    }


def _optional_public_text(value: object, maximum: int) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or len(value) > maximum:
        raise ReproductionValidationError()
    return value.strip() or None


def _public_string_list(
    value: object,
    *,
    maximum: int,
    item_maximum: int,
    urls: bool,
) -> list[str]:
    if not isinstance(value, (list, tuple)) or len(value) > maximum:
        raise ReproductionValidationError()
    result: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip() or len(item.strip()) > item_maximum:
            raise ReproductionValidationError()
        clean = item.strip()
        if urls and not _valid_public_url(clean):
            raise ReproductionValidationError()
        if clean not in result:
            result.append(clean)
    return result


def _valid_public_url(value: str) -> bool:
    parsed = urlparse(value)
    return (
        parsed.scheme in {"http", "https"}
        and bool(parsed.netloc)
        and parsed.username is None
        and parsed.password is None
    )


def _require_publication_revision(
    project_id: str,
    publication: Mapping[str, object],
    expected_revision: int | None,
) -> None:
    if expected_revision is None:
        return
    if isinstance(expected_revision, bool) or not isinstance(expected_revision, int):
        raise ReproductionValidationError()
    if int(publication.get("revision") or 0) != expected_revision:
        raise ReproductionConflictError(
            project_id=project_id,
            expected_revision=expected_revision,
        )


def _tags(values: object) -> list[str]:
    if not isinstance(values, list) or len(values) > 30:
        raise ReproductionValidationError()
    result = []
    for value in values:
        if not isinstance(value, str) or not value.strip() or len(value.strip()) > 50:
            raise ReproductionValidationError()
        if value.strip() not in result:
            result.append(value.strip())
    return result


def _ensure_experiment_project(project: Mapping[str, object]) -> None:
    """Keep experiment-only records out of article/blog projects."""
    if str(project.get("projectKind") or "reproduction") != "reproduction":
        raise ReproductionValidationError()


_RUN_TEXT_FIELDS = {
    "name": "name",
    "environment": "environment",
    "command": "command",
    "dataVersion": "data_version",
    "codeRevision": "code_revision",
    "resultSummary": "result_summary",
    "startedAt": "started_at",
    "finishedAt": "finished_at",
    "runtimeVersions": "runtime_versions",
    "dataset": "dataset",
    "preprocessing": "preprocessing",
    "repositoryUrl": "repository_url",
    "config": "config",
    "issues": "issues",
}


def _run_update_values(body: Mapping[str, object], now: str) -> dict[str, object]:
    values: dict[str, object] = {}
    for public, stored in _RUN_TEXT_FIELDS.items():
        if public in body:
            values[stored] = _optional_text(body.get(public))
    if "seed" in body:
        values["seed"] = _optional_int(body.get("seed"))
    if "parameters" in body:
        values["parameters_json"] = _json_dict(body.get("parameters"))
    if "metrics" in body:
        values["metrics_json"] = _json_dict(body.get("metrics"))
    if "status" in body:
        status = str(body.get("status") or "")
        try:
            validate_run_status(status)
        except ValueError as error:
            raise ReproductionValidationError() from error
        values["status"] = status
    if values:
        values["updated_at"] = now
    return values


_RESULT_TEXT_FIELDS = {
    "metricName": "metric_name",
    "paperValue": "paper_value",
    "reproductionValue": "reproduction_value",
    "difference": "difference",
    "differencePercent": "difference_percent",
    "datasetSettings": "dataset_settings",
    "source": "source",
    "notes": "notes",
}


def _result_update_values(body: Mapping[str, object], now: str) -> dict[str, object]:
    values: dict[str, object] = {}
    for public, stored in _RESULT_TEXT_FIELDS.items():
        if public in body:
            value = _optional_text(body.get(public))
            if public == "metricName" and value is None:
                raise ReproductionValidationError()
            values[stored] = value
    if "status" in body:
        status = str(body.get("status") or "")
        try:
            validate_result_status(status)
        except ValueError as error:
            raise ReproductionValidationError() from error
        values["status"] = status
    if values:
        values["updated_at"] = now
    return values


def _result_values(project_id: str, body: Mapping[str, object], now: str) -> dict[str, object]:
    values = _result_update_values(body, now)
    metric_name = values.get("metric_name")
    if not isinstance(metric_name, str) or not metric_name:
        raise ReproductionValidationError()
    values.update(
        {
            "id": f"result_{uuid4().hex}",
            "project_id": project_id,
            "status": values.get("status") or "not_reproduced",
            "created_at": now,
            "updated_at": now,
        }
    )
    for stored in _RESULT_TEXT_FIELDS.values():
        values.setdefault(stored, None)
    return values


def _result_public(values: Mapping[str, object]) -> dict[str, object]:
    return {
        "id": values["id"], "projectId": values["project_id"],
        "metricName": values["metric_name"], "paperValue": values.get("paper_value"),
        "reproductionValue": values.get("reproduction_value"), "difference": values.get("difference"),
        "differencePercent": values.get("difference_percent"), "datasetSettings": values.get("dataset_settings"),
        "source": values.get("source"), "status": values["status"], "notes": values.get("notes"),
        "createdAt": values["created_at"], "updatedAt": values["updated_at"],
    }


def _required_text(value: object, maximum: int) -> str:
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > maximum:
        raise ReproductionValidationError()
    return value.strip()


def _safe_storage_key(value: str) -> str:
    if "\x00" in value or "\\" in value or value.startswith("/"):
        raise ReproductionValidationError()
    relative = PurePosixPath(value)
    if relative.is_absolute() or len(relative.parts) < 2:
        raise ReproductionValidationError()
    if any(part in {"", ".", ".."} for part in relative.parts):
        raise ReproductionValidationError()
    return relative.as_posix()


def _optional_text(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or len(value) > 10_000:
        raise ReproductionValidationError()
    return value.strip() or None


def _optional_int(value: object) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise ReproductionValidationError()
    return value


def _json_dict(value: object) -> str:
    if value is None:
        return "{}"
    if not isinstance(value, dict):
        raise ReproductionValidationError()
    try:
        encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    except (TypeError, ValueError) as error:
        raise ReproductionValidationError() from error
    if len(encoded) > 100_000:
        raise ReproductionValidationError()
    return encoded


def _timestamp(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("clock must return a timezone-aware datetime")
    return value.astimezone(timezone.utc).isoformat()


MAX_ARTIFACT_BYTES = 25 * 1024 * 1024
_ALLOWED_UPLOADS = {
    "text/plain": ("log", {".log", ".txt"}),
    "text/markdown": ("markdown", {".md", ".markdown"}),
    "text/csv": ("table", {".csv"}),
    "application/json": ("data", {".json"}),
    "image/png": ("image", {".png"}),
    "image/jpeg": ("image", {".jpg", ".jpeg"}),
    "image/webp": ("image", {".webp"}),
    "application/pdf": ("document", {".pdf"}),
    "text/html": ("document", {".html", ".htm"}),
}


def _validate_project_id(value: str) -> None:
    if re.fullmatch(r"repro_[a-f0-9]{32}", value) is None:
        raise ReproductionValidationError()


def _validate_upload_name(filename: str, mime_type: str) -> tuple[str, str]:
    if not isinstance(filename, str) or not filename or len(filename) > 255:
        raise ReproductionValidationError()
    if "\x00" in filename or "/" in filename or "\\" in filename:
        raise ReproductionValidationError()
    normalized = filename.strip()
    if normalized in {"", ".", ".."} or Path(normalized).name != normalized:
        raise ReproductionValidationError()
    if not isinstance(mime_type, str) or mime_type not in _ALLOWED_UPLOADS:
        raise ReproductionValidationError()
    kind, extensions = _ALLOWED_UPLOADS[mime_type]
    if Path(normalized).suffix.lower() not in extensions:
        raise ReproductionValidationError()
    return normalized, kind


def _validate_upload_signature(mime_type: str, signature: bytes) -> None:
    if mime_type == "image/png" and not signature.startswith(b"\x89PNG\r\n\x1a\n"):
        raise ReproductionValidationError()
    if mime_type == "image/jpeg" and not signature.startswith(b"\xff\xd8\xff"):
        raise ReproductionValidationError()
    if mime_type == "image/webp" and not (
        len(signature) >= 12
        and signature.startswith(b"RIFF")
        and signature[8:12] == b"WEBP"
    ):
        raise ReproductionValidationError()
    if mime_type == "application/pdf" and not signature.startswith(b"%PDF-"):
        raise ReproductionValidationError()


def _list_of_mappings(value: object) -> list[dict[str, object]]:
    if not isinstance(value, list):
        raise ReproductionValidationError()
    result: list[dict[str, object]] = []
    for item in value:
        if not isinstance(item, Mapping):
            raise ReproductionValidationError()
        result.append(dict(item))
    return result


def _workspace_fingerprint(project: Mapping[str, object]) -> str:
    try:
        payload = json.dumps(
            project,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError) as error:
        raise ReproductionValidationError() from error
    return hashlib.sha256(payload).hexdigest()


def _atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            prefix=f".{path.name}.",
            suffix=".tmp",
            dir=path.parent,
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _atomic_write_json(path: Path, value: object) -> None:
    _atomic_write_text(
        path,
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    )


def _copy_verified_file(
    source: Path,
    target: Path,
    *,
    expected_size: int | None,
    expected_sha256: str,
) -> tuple[int, str]:
    try:
        validate_sha256(expected_sha256)
    except ValueError as error:
        raise ReproductionValidationError() from error
    temporary: Path | None = None
    size = 0
    digest = hashlib.sha256()
    try:
        with source.open("rb") as reader, tempfile.NamedTemporaryFile(
            mode="wb",
            prefix=f".{target.name}.",
            suffix=".tmp",
            dir=target.parent,
            delete=False,
        ) as writer:
            temporary = Path(writer.name)
            while True:
                chunk = reader.read(1024 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                digest.update(chunk)
                writer.write(chunk)
            writer.flush()
            os.fsync(writer.fileno())
        actual_sha256 = digest.hexdigest()
        if expected_size is None or size != expected_size or actual_sha256 != expected_sha256:
            raise ReproductionValidationError()
        os.replace(temporary, target)
        temporary = None
        return size, actual_sha256
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _artifact_download_url(project_id: str, artifact_id: str) -> str:
    return f"/api/v2/reproductions/{project_id}/artifacts/{artifact_id}/download"


async def _read_upload_chunk(stream: object) -> bytes:
    read = getattr(stream, "read", None)
    if not callable(read):
        raise ReproductionValidationError()
    chunk = read(1024 * 1024)
    if hasattr(chunk, "__await__"):
        chunk = await chunk
    if not isinstance(chunk, bytes):
        raise ReproductionValidationError()
    return chunk
