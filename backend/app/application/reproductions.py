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
from uuid import uuid4

from backend.app.domain import (
    DEFAULT_DOCUMENT,
    ReproductionArchivedError,
    ReproductionConflictError,
    ReproductionNotFoundError,
    ReproductionValidationError,
    validate_project_status,
    validate_result_status,
    validate_run_status,
    validate_sha256,
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
    ) -> None:
        self._work_factory = work_factory
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._artifact_root = (
            artifact_root.expanduser().resolve()
            if artifact_root is not None
            else Path.cwd().joinpath("data", "reproduction-artifacts").resolve()
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

    async def list_projects(self, *, query: str | None, status: str | None, tag: str | None, sort: str = "updated", limit: int, offset: int) -> dict[str, object]:
        if status is not None:
            try:
                validate_project_status(status)
            except ValueError as error:
                raise ReproductionValidationError() from error
        async with self._work_factory() as work:
            items, total = await work.reproductions.list_projects(
                query=query.strip() if isinstance(query, str) and query.strip() else None,
                status=status, tag=tag.strip() if isinstance(tag, str) and tag.strip() else None,
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

    async def create_project(self, *, paper_id: str, name: str, tags: list[str]) -> dict[str, object]:
        name = name.strip()
        if not name or len(name) > 200:
            raise ReproductionValidationError()
        cleaned_tags = _tags(tags)
        now = _timestamp(self._clock())
        project_id = f"repro_{uuid4().hex}"
        document_id = f"rdoc_{uuid4().hex}"
        async with self._work_factory() as work:
            paper = await work.papers.get_legacy(paper_id)
            if paper is None:
                from backend.app.domain import MissingPaperError
                raise MissingPaperError(paper_id=paper_id)
            await work.reproductions.add_project(
                {
                    "id": project_id, "paper_id": paper_id,
                    "paper_title": str(paper.get("title") or "Untitled paper"),
                    "name": name, "status": "planned", "tags_json": json.dumps(cleaned_tags, ensure_ascii=False),
                    "revision": 1, "created_at": now, "updated_at": now,
                },
                {
                    "id": document_id, "project_id": project_id, "content": DEFAULT_DOCUMENT,
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
                await work.commit()
        return await self._sync_project_workspace(project_id)

    async def archive_project(self, project_id: str, *, expected_revision: int) -> dict[str, object]:
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
            await work.commit()
        result = await self._sync_project_workspace(project_id)
        document = result.get("document")
        if not isinstance(document, dict):
            raise ReproductionValidationError()
        saved_document = dict(document)
        saved_document["projectRevision"] = result.get("revision")
        return saved_document

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
            await work.commit()
        project = await self._sync_project_workspace(project_id)
        return next(row for row in project["runs"] if row["id"] == run_id)

    async def update_run(self, project_id: str, run_id: str, body: Mapping[str, object]) -> dict[str, object]:
        values = _run_update_values(body, _timestamp(self._clock()))
        async with self._work_factory() as work:
            current = await work.reproductions.get_project(project_id)
            if current is None:
                raise ReproductionNotFoundError(project_id=project_id)
            if current["status"] == "archived":
                raise ReproductionArchivedError(project_id=project_id)
            if not values or not await work.reproductions.update_run(project_id, run_id, values):
                raise ReproductionNotFoundError(project_id=project_id)
            await work.commit()
        project = await self._sync_project_workspace(project_id)
        return next(row for row in project["runs"] if row["id"] == run_id)

    async def delete_run(self, project_id: str, run_id: str) -> None:
        async with self._work_factory() as work:
            current = await work.reproductions.get_project(project_id)
            if current is None:
                raise ReproductionNotFoundError(project_id=project_id)
            if current["status"] == "archived":
                raise ReproductionArchivedError(project_id=project_id)
            if not await work.reproductions.delete_run(project_id, run_id):
                raise ReproductionNotFoundError(project_id=project_id)
            await work.commit()
        await self._sync_project_workspace(project_id)

    async def add_result(self, project_id: str, body: Mapping[str, object]) -> dict[str, object]:
        values = _result_values(project_id, body, _timestamp(self._clock()))
        async with self._work_factory() as work:
            current = await work.reproductions.get_project(project_id)
            if current is None:
                raise ReproductionNotFoundError(project_id=project_id)
            if current["status"] == "archived":
                raise ReproductionArchivedError(project_id=project_id)
            await work.reproductions.add_result(values)
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
            if current["status"] == "archived":
                raise ReproductionArchivedError(project_id=project_id)
            if not values or not await work.reproductions.update_result(project_id, result_id, values):
                raise ReproductionNotFoundError(project_id=project_id)
            await work.commit()
        project = await self._sync_project_workspace(project_id)
        return next(item for item in project["results"] if item["id"] == result_id)

    async def delete_result(self, project_id: str, result_id: str) -> None:
        async with self._work_factory() as work:
            current = await work.reproductions.get_project(project_id)
            if current is None:
                raise ReproductionNotFoundError(project_id=project_id)
            if current["status"] == "archived":
                raise ReproductionArchivedError(project_id=project_id)
            if not await work.reproductions.delete_result(project_id, result_id):
                raise ReproductionNotFoundError(project_id=project_id)
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
