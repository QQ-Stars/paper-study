from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from typing import Any, Mapping

from backend.app.domain import DomainError, MissingPaperError, ObsidianDisabledError
from backend.app.domain.processing import (
    JobResult,
    NewProcessingJob,
    ObsidianExportJobSpecV1,
    ObsidianSyncJobSpecV1,
    build_obsidian_job_key,
    encode_job_spec_v1,
    hash_canonical_json,
    hash_job_spec,
)
from backend.app.providers.obsidian_vault import (
    VaultAccessResult,
    probe_obsidian_vault,
)


_SUMMARY_KEYS = (
    "exported",
    "unchanged",
    "conflicts",
    "errors",
    "skipped",
    "userManaged",
    "orphaned",
    "deleted",
)


class ObsidianSnapshotChangedError(DomainError):
    code = "OBSIDIAN_SNAPSHOT_CHANGED"
    public_message = "The library changed after this Obsidian job was queued."
    retryable = False


class ObsidianAllItemsFailedError(DomainError):
    code = "OBSIDIAN_EXPORT_FAILED"
    public_message = "Every item in the Obsidian projection failed."
    retryable = False

    def __init__(self, result: JobResult) -> None:
        self.result = result
        super().__init__()


class ObsidianVaultAccessError(DomainError):
    public_message = "The configured Obsidian Vault is unavailable."
    retryable = False
    http_status = 409

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__()


@dataclass(frozen=True, slots=True)
class ObsidianEnqueueResult:
    job: Any
    deduplicated: bool


class ObsidianJobService:
    """Build immutable, content-safe Obsidian requests on the shared P2 queue."""

    def __init__(
        self,
        unit_of_work_factory: Any,
        *,
        settings_service: Any,
        library_queries: Any,
        clock: Any = None,
        access_tester: Any = None,
    ) -> None:
        self._work_factory = unit_of_work_factory
        self._settings = settings_service
        self._library_queries = library_queries
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self.access_tester = access_tester

    async def status(self) -> dict[str, object]:
        settings = await self._settings.obsidian()
        latest: Any = None
        async with self._work_factory() as work:
            for job_type in ("obsidian_export", "obsidian_sync"):
                rows, _cursor = await work.jobs.list_page(
                    paper_id=None,
                    status=None,
                    job_type=job_type,
                    limit=1,
                    cursor=None,
                )
                if rows and (latest is None or rows[0].created_at > latest.created_at):
                    latest = rows[0]
        return {
            "enabled": settings.enabled,
            "vaultConfigured": bool(settings.vault_path),
            "writable": False,
            "rootFolder": settings.root_folder,
            "pdfMode": settings.pdf_mode,
            "lastJob": _safe_job_summary(latest) if latest is not None else None,
            "aggregate": {key: 0 for key in _SUMMARY_KEYS},
        }

    async def test_access(self) -> bool:
        await self._require_vault_access()
        return True

    async def enqueue_export(self, paper_id: str, *, dry_run: bool) -> ObsidianEnqueueResult:
        await self._require_vault_access()
        library_snapshot = await self._library_snapshot((paper_id,))
        if not library_snapshot["items"]:
            raise MissingPaperError(paper_id=paper_id)
        settings_snapshot, settings_fingerprint = await self._settings_snapshot()
        spec = ObsidianExportJobSpecV1(
            paper_id=paper_id,
            dry_run=dry_run,
            settings_fingerprint=settings_fingerprint,
            settings_snapshot=settings_snapshot,
            library_snapshot=library_snapshot,
        )
        return await self._enqueue(spec)

    async def enqueue_sync(
        self,
        *,
        dry_run: bool,
        apply_cleanup: bool,
        cleanup_plan_sha: str | None,
    ) -> ObsidianEnqueueResult:
        await self._require_vault_access()
        settings_snapshot, settings_fingerprint = await self._settings_snapshot()
        spec = ObsidianSyncJobSpecV1(
            dry_run=dry_run,
            apply_cleanup=apply_cleanup,
            cleanup_plan_sha=cleanup_plan_sha,
            settings_fingerprint=settings_fingerprint,
            settings_snapshot=settings_snapshot,
            library_snapshot=await self._library_snapshot(None),
        )
        return await self._enqueue(spec)

    async def _enqueue(self, spec: Any) -> ObsidianEnqueueResult:
        raw_spec = encode_job_spec_v1(spec)
        spec_sha = hash_job_spec(raw_spec)
        job_key = build_obsidian_job_key(spec_sha)
        job = NewProcessingJob(
            id=f"job_{job_key[:24]}",
            spec=spec,
            idempotency_key=job_key,
            created_at=self._clock(),
            max_attempts=3,
        )
        async with self._work_factory() as work:
            result = await work.jobs.insert_with_spec(
                job,
                spec_json=raw_spec,
                spec_sha256=spec_sha,
            )
            stored = await work.jobs.get(result.job.id)
            await work.commit()
        if stored is None:
            raise RuntimeError("enqueued Obsidian job is missing")
        return ObsidianEnqueueResult(stored, result.deduplicated)

    async def _settings_snapshot(self) -> tuple[dict[str, object], str]:
        settings = await self._settings.obsidian()
        snapshot: dict[str, object] = {
            "vaultPath": settings.vault_path,
            "rootFolder": settings.root_folder,
            "pdfMode": settings.pdf_mode,
            "enabled": settings.enabled,
            "exportSource": settings.export_source,
            "exportExplainer": settings.export_explainer,
            "exportTranslation": settings.export_translation,
            "autoExport": settings.auto_export,
        }
        return snapshot, hash_canonical_json(snapshot)

    async def _require_vault_access(self) -> None:
        settings = await self._settings.obsidian()
        if not settings.enabled:
            raise ObsidianDisabledError()
        if self.access_tester is None:
            result: object = probe_obsidian_vault(settings.vault_path)
        else:
            result = self.access_tester()
            if hasattr(result, "__await__"):
                result = await result
        if isinstance(result, VaultAccessResult):
            if result.ok:
                return
            raise ObsidianVaultAccessError(
                result.code or "OBSIDIAN_VAULT_NOT_WRITABLE"
            )
        if not isinstance(result, bool):
            raise TypeError("Obsidian access tester returned an invalid result")
        if not result:
            raise ObsidianVaultAccessError("OBSIDIAN_VAULT_NOT_WRITABLE")

    async def _library_snapshot(
        self, paper_ids: tuple[str, ...] | None
    ) -> dict[str, object]:
        requested = set(paper_ids) if paper_ids is not None else None
        rows = await self._library_queries.list_papers()
        items: list[dict[str, object]] = []
        async with self._work_factory() as work:
            for row in rows:
                paper_id = row.get("id")
                if not isinstance(paper_id, str) or (requested is not None and paper_id not in requested):
                    continue
                sources, _cursor = await work.sources.list_page(
                    paper_id=paper_id,
                    limit=100,
                    cursor=None,
                )
                ready_source = next(
                    (
                        source
                        for source in sources
                        if getattr(source.status, "value", source.status) == "ready"
                        and source.content_sha256 is not None
                    ),
                    None,
                )
                artifact_heads: list[dict[str, str]] = []
                for kind in ("explainer", "translation"):
                    artifact_id = await work.artifacts.get_head_artifact_id(
                        paper_id=paper_id,
                        kind=kind,
                    )
                    artifact = (
                        await work.artifacts.get(artifact_id)
                        if artifact_id is not None
                        else None
                    )
                    if (
                        artifact is not None
                        and artifact.paper_id == paper_id
                        and getattr(artifact.status, "value", artifact.status) == "ready"
                        and artifact.content_sha256 is not None
                    ):
                        artifact_heads.append(
                            {
                                "artifactId": artifact.id,
                                "contentSha256": artifact.content_sha256,
                                "kind": kind,
                            }
                        )
                note = await work.papers.get_note(paper_id)
                items.append(
                    {
                        "artifactHeads": artifact_heads,
                        "noteSha256": (
                            hashlib.sha256(note.encode("utf-8")).hexdigest()
                            if isinstance(note, str) and note
                            else None
                        ),
                        "paperId": paper_id,
                        "pdfSha256": self._library_queries.pdf_sha256(row),
                        "sourceContentSha256": (
                            ready_source.content_sha256 if ready_source is not None else None
                        ),
                        "sourceDocumentId": ready_source.id if ready_source is not None else None,
                    }
                )
        items.sort(key=lambda item: str(item["paperId"]))
        identity = {"items": items}
        return {**identity, "sha256": hash_canonical_json(identity)}

    async def validate_for_execution(self, spec: object) -> None:
        current = await self._settings.obsidian()
        if not current.enabled:
            raise ObsidianDisabledError()
        expected_ids = tuple(
            str(item["paperId"])
            for item in spec.library_snapshot["items"]
        )
        current_snapshot = await self._library_snapshot(
            expected_ids if spec.job_type == "obsidian_export" else None
        )
        if current_snapshot != _thaw(spec.library_snapshot):
            raise ObsidianSnapshotChangedError()


class ObsidianJobHandler:
    """P2 handler that consumes only the immutable lease request."""

    def __init__(self, service: ObsidianJobService, *, exporter: Any) -> None:
        self._service = service
        self._exporter = exporter

    async def __call__(self, lease: object) -> JobResult:
        spec = lease.spec.value
        if not isinstance(spec, (ObsidianExportJobSpecV1, ObsidianSyncJobSpecV1)):
            raise TypeError("Obsidian handler received a non-Obsidian job spec")
        await self._service.validate_for_execution(spec)
        raw_counts = await self._exporter(spec)
        if not isinstance(raw_counts, Mapping) or frozenset(raw_counts) != frozenset(_SUMMARY_KEYS):
            raise TypeError("Obsidian exporter must return the fixed terminal summary")
        counts: dict[str, int] = {}
        for key in _SUMMARY_KEYS:
            count = raw_counts[key]
            if not isinstance(count, int) or isinstance(count, bool) or count < 0:
                raise TypeError("Obsidian terminal counts must be nonnegative integers")
            counts[key] = count
        result = JobResult(counts)
        if counts["errors"] > 0 and sum(
            count for key, count in counts.items() if key != "errors"
        ) == 0:
            raise ObsidianAllItemsFailedError(result)
        return result


def _thaw(value: object) -> object:
    if isinstance(value, Mapping):
        return {key: _thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw(item) for item in value]
    return value


def _safe_job_summary(job: object) -> dict[str, object]:
    status = getattr(job, "status")
    job_type = getattr(job, "job_type")
    return {
        "id": getattr(job, "id"),
        "paperId": getattr(job, "paper_id"),
        "jobType": getattr(job_type, "value", job_type),
        "status": getattr(status, "value", status),
    }


__all__ = [
    "ObsidianEnqueueResult",
    "ObsidianAllItemsFailedError",
    "ObsidianJobHandler",
    "ObsidianJobService",
    "ObsidianSnapshotChangedError",
    "ObsidianVaultAccessError",
]
