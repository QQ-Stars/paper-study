from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import datetime, timezone
import hashlib
import json
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
        async with self._work_factory() as work:
            project = await work.reproductions.get_project(project_id)
            if project is None:
                raise ReproductionNotFoundError(project_id=project_id)
            project["runs"] = await work.reproductions.list_runs(project_id)
            project["artifacts"] = await work.reproductions.list_artifacts(project_id)
            project["notes"] = await work.reproductions.list_notes(project_id)
            project["results"] = await work.reproductions.list_results(project_id)
        return project

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
            result = await work.reproductions.get_project(project_id)
        assert result is not None
        return result

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
                return current
            if not await work.reproductions.update_project(project_id, values=values, expected_revision=expected_revision):
                raise ReproductionConflictError(project_id=project_id, expected_revision=expected_revision)
            await work.commit()
            result = await work.reproductions.get_project(project_id)
        assert result is not None
        return result

    async def archive_project(self, project_id: str, *, expected_revision: int) -> dict[str, object]:
        async with self._work_factory() as work:
            current = await work.reproductions.get_project(project_id)
            if current is None:
                raise ReproductionNotFoundError(project_id=project_id)
            if current["status"] == "archived":
                return current
            if not await work.reproductions.archive(project_id, expected_revision=expected_revision):
                raise ReproductionConflictError(project_id=project_id, expected_revision=expected_revision)
            await work.commit()
            result = await work.reproductions.get_project(project_id)
        assert result is not None
        return result

    async def delete_project(self, project_id: str, *, expected_revision: int) -> None:
        _validate_project_id(project_id)
        async with self._work_factory() as work:
            current = await work.reproductions.get_project(project_id)
            if current is None:
                raise ReproductionNotFoundError(project_id=project_id)
            if current["status"] != "archived":
                raise ReproductionValidationError()
            if current["revision"] != expected_revision:
                raise ReproductionConflictError(project_id=project_id, expected_revision=expected_revision)
            await work.reproductions.delete_project(project_id)
            await work.commit()
        project_dir = self._artifact_project_dir(project_id, create=False)
        if project_dir.is_dir() and not project_dir.is_symlink():
            shutil.rmtree(project_dir)

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
            result = await work.reproductions.get_project(project_id)
        assert result is not None
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
            runs = await work.reproductions.list_runs(project_id)
        return next(row for row in runs if row["id"] == run_id)

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
            runs = await work.reproductions.list_runs(project_id)
        return next(row for row in runs if row["id"] == run_id)

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
            results = await work.reproductions.list_results(project_id)
        return next(item for item in results if item["id"] == result_id)

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
        relative = PurePosixPath("projects", project_id, artifact_id + Path(normalized_name).suffix.lower())
        target_root = self._artifact_root
        project_dir = self._artifact_project_dir(project_id, create=True)
        target = (target_root / relative).resolve()
        if target_root not in target.parents or project_dir.resolve() != target.parent:
            raise ReproductionValidationError()
        temp_path: Path | None = None
        size = 0
        digest = hashlib.sha256()
        try:
            with tempfile.NamedTemporaryFile(
                mode="wb", prefix=f".{artifact_id}.", suffix=".tmp", dir=project_dir, delete=False
            ) as handle:
                temp_path = Path(handle.name)
                while True:
                    chunk = await _read_upload_chunk(stream)
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > MAX_ARTIFACT_BYTES:
                        raise ReproductionValidationError()
                    digest.update(chunk)
                    handle.write(chunk)
                handle.flush()
                os.fsync(handle.fileno())
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
            return {
                "id": artifact_id, "projectId": project_id, "runId": run_id, "kind": kind,
                "filename": normalized_name, "storageKey": relative.as_posix(),
                "mimeType": mime_type, "sizeBytes": size, "sha256": digest.hexdigest(),
                "createdAt": now,
            }
        except Exception:
            if temp_path is not None:
                temp_path.unlink(missing_ok=True)
            target.unlink(missing_ok=True)
            raise

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
