from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from collections.abc import Mapping
from typing import Any, Callable, Iterable

from backend.app.application.obsidian_pdf import ObsidianPdfProjector
from backend.app.application.obsidian_projection import (
    ExportOptions,
    ProjectionArtifact,
    ProjectionPlan,
    ProjectionSnapshot,
    build_projection_plans,
)
from backend.app.infrastructure.bound_vault_root import BoundVaultRoot, ObsidianVaultError
from backend.app.domain.processing import hash_canonical_json
from backend.app.providers.obsidian_vault import (
    CleanupInventory,
    CleanupProof,
    ObsidianCleanupPlanner,
    ObsidianManifest,
    ObsidianProjectionPublisher,
    VaultWriter,
    serialize_manifest,
)
from backend.app.repositories.obsidian_exports import SqlAlchemyObsidianExportsRepository


class CleanupPlanMismatchError(ValueError):
    code = "OBSIDIAN_CLEANUP_PLAN_MISMATCH"


@dataclass(frozen=True, slots=True)
class CleanupPlanItem:
    path: str
    kind: str
    paper_id: str
    source_hash: str
    exported_hash: str
    target_id: str
    target_hash: str

    def to_dict(self) -> dict[str, str]:
        return {
            "path": self.path,
            "kind": self.kind,
            "paperId": self.paper_id,
            "sourceHash": self.source_hash,
            "exportedHash": self.exported_hash,
            "targetId": self.target_id,
            "targetHash": self.target_hash,
        }


@dataclass(frozen=True, slots=True)
class CleanupPreview:
    items: tuple[CleanupPlanItem, ...]
    canonical_bytes: bytes
    sha256: str
    orphaned: int
    conflicts: int
    user_managed: int
    _proofs: tuple[CleanupProof, ...] = field(repr=False, compare=False)
    _manifest_snapshot: Any = field(repr=False, compare=False)
    _manifest: ObsidianManifest = field(repr=False, compare=False)


@dataclass(frozen=True, slots=True)
class CleanupApplyResult:
    deleted: int
    conflicts: int
    orphaned: int
    user_managed: int


@dataclass(frozen=True, slots=True)
class BatchExportResult:
    counts: dict[str, int]
    cleanup: CleanupPreview


class ObsidianBatchExporter:
    def __init__(
        self,
        root: BoundVaultRoot,
        repository: Any,
        *,
        root_folder: str,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._root = root
        self._repository = repository
        self._root_folder = root_folder
        self._now = now or (lambda: datetime.now(timezone.utc))
        self._publisher = ObsidianProjectionPublisher(
            root,
            repository,
            root_folder=root_folder,
            now=self._now,
        )
        self._writer = VaultWriter(root, root_folder=root_folder)

    async def export(self, plans: Iterable[ProjectionPlan]) -> BatchExportResult:
        frozen = tuple(plans)
        counts = {
            "exported": 0,
            "unchanged": 0,
            "conflicts": 0,
            "errors": 0,
            "skipped": 0,
            "userManaged": 0,
            "orphaned": 0,
            "deleted": 0,
        }
        for item in sorted(
            (item for plan in frozen for item in plan.files),
            key=lambda value: value.path,
        ):
            try:
                result = await self._publisher.publish(item)
            except ObsidianVaultError:
                counts["errors"] += 1
                continue
            key = {
                "exported": "exported",
                "unchanged": "unchanged",
                "conflict": "conflicts",
                "user_managed": "userManaged",
            }[result.status]
            counts[key] += 1
        cleanup = await self.preview_cleanup(frozen)
        counts["orphaned"] = cleanup.orphaned
        return BatchExportResult(counts, cleanup)

    async def preview_cleanup(
        self, plans: Iterable[ProjectionPlan]
    ) -> CleanupPreview:
        desired = {
            item.path for plan in tuple(plans) for item in plan.files
        }
        manifest_snapshot, manifest = self._publisher._read_manifest(create_parent=False)
        stale = tuple(entry for entry in manifest.entries if entry.path not in desired)
        planner = ObsidianCleanupPlanner(
            self._root,
            self._repository,
            root_folder=self._root_folder,
        )
        inventory = await planner.classify(stale)
        return self._preview(inventory, manifest_snapshot, manifest)

    async def apply_cleanup(
        self,
        plans: Iterable[ProjectionPlan],
        confirmation_sha256: str | None,
        *,
        before_delete: Callable[[CleanupProof], None] | None = None,
    ) -> CleanupApplyResult:
        preview = await self.preview_cleanup(tuple(plans))
        if confirmation_sha256 is None or confirmation_sha256 != preview.sha256:
            raise CleanupPlanMismatchError("cleanup plan confirmation does not match")

        deleted: list[CleanupProof] = []
        conflicts = preview.conflicts
        for proof in preview._proofs:
            try:
                if before_delete is not None:
                    before_delete(proof)
                self._writer.delete_with_proof(proof)
            except ObsidianVaultError:
                conflicts += 1
                continue
            deleted.append(proof)

        if deleted:
            removed = {proof.entry.path for proof in deleted}
            updated = ObsidianManifest(
                generated_at=self._utc_now(),
                entries=tuple(
                    entry for entry in preview._manifest.entries if entry.path not in removed
                ),
            )
            self._publisher._publish_manifest(
                preview._manifest_snapshot,
                serialize_manifest(updated),
            )
            for proof in deleted:
                if not await self._repository.delete_if_matches(proof.ledger):
                    raise ObsidianVaultError(
                        "OBSIDIAN_LEDGER_CHANGED",
                        "The cleanup ledger changed after Vault publication.",
                    )
        return CleanupApplyResult(
            deleted=len(deleted),
            conflicts=conflicts,
            orphaned=preview.orphaned,
            user_managed=preview.user_managed,
        )

    @staticmethod
    def _preview(
        inventory: CleanupInventory,
        manifest_snapshot: Any,
        manifest: ObsidianManifest,
    ) -> CleanupPreview:
        proofs = tuple(sorted(inventory.deletable, key=lambda proof: proof.entry.path))
        items = tuple(
            CleanupPlanItem(
                path=proof.entry.path,
                kind=proof.entry.kind,
                paper_id=proof.entry.paper_id,
                source_hash=proof.entry.source_hash,
                exported_hash=proof.entry.exported_hash,
                target_id=proof.target.opaque_id,
                target_hash=proof.target.sha256,
            )
            for proof in proofs
        )
        canonical = (
            json.dumps(
                {
                    "schemaVersion": 1,
                    "items": [item.to_dict() for item in items],
                },
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
                allow_nan=False,
            )
            + "\n"
        ).encode("utf-8")
        return CleanupPreview(
            items=items,
            canonical_bytes=canonical,
            sha256=hashlib.sha256(canonical).hexdigest(),
            orphaned=len(inventory.orphaned),
            conflicts=len(inventory.conflicts),
            user_managed=len(inventory.user_managed),
            _proofs=proofs,
            _manifest_snapshot=manifest_snapshot,
            _manifest=manifest,
        )

    def _utc_now(self) -> datetime:
        value = self._now()
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Obsidian exporter clock must be timezone-aware")
        return value.astimezone(timezone.utc)


class ObsidianSpecExporter:
    """Execute one immutable Obsidian JobSpec through the production adapters."""

    def __init__(
        self,
        unit_of_work_factory: Any,
        session_factory: Any,
        *,
        pdf_files: Any,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._work_factory = unit_of_work_factory
        self._session_factory = session_factory
        self._pdf_files = pdf_files
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    async def __call__(self, spec: object) -> dict[str, int]:
        settings = dict(getattr(spec, "settings_snapshot", None) or {})
        required_settings = {
            "vaultPath",
            "rootFolder",
            "pdfMode",
            "exportSource",
            "exportExplainer",
            "exportTranslation",
        }
        if not required_settings.issubset(settings):
            raise ValueError("Obsidian JobSpec settings snapshot is incomplete")
        snapshots, stored_paths = await self._load_snapshots(spec)
        options = ExportOptions(
            export_source=settings["exportSource"],
            export_explainer=settings["exportExplainer"],
            export_translation=settings["exportTranslation"],
        )
        plans = build_projection_plans(snapshots, options)
        counts = _empty_counts()
        counts["skipped"] = _missing_projection_count(snapshots, options)
        vault_path = Path(str(settings["vaultPath"])).expanduser().resolve(strict=True)
        root_folder = str(settings["rootFolder"])
        pdf_mode = str(settings["pdfMode"])

        with BoundVaultRoot.open(vault_path) as root:
            repository = SqlAlchemyObsidianExportsRepository(self._session_factory)
            batch = ObsidianBatchExporter(
                root,
                repository,
                root_folder=root_folder,
                now=self._clock,
            )
            if bool(getattr(spec, "dry_run", False)):
                preview = await batch.preview_cleanup(plans)
                counts["orphaned"] = preview.orphaned
                counts["conflicts"] = preview.conflicts
                counts["userManaged"] = preview.user_managed
                return counts

            exported = await batch.export(plans)
            for key, value in exported.counts.items():
                counts[key] += value

            pdf_files = []
            projector = ObsidianPdfProjector(
                pdf_files=self._pdf_files,
                root=root,
                repository=repository,
                root_folder=root_folder,
                now=self._clock,
            )
            existing_paths = tuple(
                item.path for plan in plans for item in plan.files
            )
            for snapshot in snapshots:
                result = await projector.project(
                    paper_id=snapshot.paper_id,
                    mode=pdf_mode,
                    stored_path=stored_paths[snapshot.paper_id],
                    existing_paths=existing_paths,
                    source_available=snapshot.source_markdown is not None,
                    expected_sha256=snapshot.pdf_sha256,
                )
                if result.projection_file is not None:
                    pdf_files.append(result.projection_file)
                if result.status == "exported":
                    counts["exported"] += 1
                elif result.status == "unchanged":
                    counts["unchanged"] += 1
                elif result.status == "conflict":
                    counts["conflicts"] += 1
                elif result.status == "pdf_missing":
                    counts["skipped"] += 1

            desired = plans
            if pdf_files:
                by_paper: dict[str, list[Any]] = {}
                for item in pdf_files:
                    paper_id = Path(item.path).stem
                    by_paper.setdefault(paper_id, []).append(item)
                desired = tuple(
                    ProjectionPlan(
                        paper_id=plan.paper_id,
                        files=plan.files + tuple(by_paper.get(plan.paper_id, ())),
                    )
                    for plan in plans
                )
            preview = await batch.preview_cleanup(desired)
            if bool(getattr(spec, "apply_cleanup", False)):
                cleanup = await batch.apply_cleanup(
                    desired,
                    getattr(spec, "cleanup_plan_sha", None),
                )
                counts["deleted"] += cleanup.deleted
                counts["conflicts"] += cleanup.conflicts
                counts["orphaned"] = cleanup.orphaned
                counts["userManaged"] += cleanup.user_managed
            else:
                counts["orphaned"] = preview.orphaned
                counts["conflicts"] += preview.conflicts
                counts["userManaged"] += preview.user_managed
            return counts

    async def _load_snapshots(
        self,
        spec: object,
    ) -> tuple[tuple[ProjectionSnapshot, ...], dict[str, str | None]]:
        library = dict(getattr(spec, "library_snapshot", None) or {})
        raw_items = library.get("items")
        if not isinstance(raw_items, (tuple, list)):
            raise ValueError("Obsidian JobSpec library snapshot is invalid")
        snapshots: list[ProjectionSnapshot] = []
        stored_paths: dict[str, str | None] = {}
        async with self._work_factory() as work:
            for raw_item in raw_items:
                item = dict(raw_item)
                paper_id = str(item["paperId"])
                paper = await work.papers.get_legacy(paper_id)
                if paper is None:
                    raise ValueError("Obsidian snapshot paper no longer exists")
                source = await _bound_source(work, item, paper_id)
                explainer = await _bound_artifact(work, item, paper_id, "explainer")
                translation = await _bound_artifact(work, item, paper_id, "translation")
                note = await work.papers.get_note(paper_id)
                note_hash = (
                    hashlib.sha256(note.encode("utf-8")).hexdigest()
                    if isinstance(note, str) and note
                    else None
                )
                if note_hash != item.get("noteSha256"):
                    raise ValueError("Obsidian note snapshot changed")
                title = str(paper.get("title") or paper_id)
                title_zh = _optional_text(paper.get("title_zh"))
                stored = paper.get("pdf_path")
                stored_path = str(stored) if stored else None
                pdf_mode = str(
                    dict(getattr(spec, "settings_snapshot", None) or {}).get(
                        "pdfMode", "none"
                    )
                )
                pdf_link = None
                if pdf_mode == "reference":
                    resolved = self._pdf_files.resolve_for_id(
                        paper_id,
                        stored_path=stored_path,
                    )
                    if resolved is not None:
                        pdf_link = resolved.path.as_uri()
                elif pdf_mode == "copy" and item.get("pdfSha256") is not None:
                    pdf_link = f"../Attachments/PDF/{paper_id}.pdf"
                snapshots.append(
                    ProjectionSnapshot(
                        paper_id=paper_id,
                        title=title,
                        title_zh=title_zh,
                        authors=_authors_text(paper.get("authors")),
                        aliases=(title_zh,) if title_zh is not None and title_zh != title else (),
                        tags=_paper_tags(paper),
                        paper_source_hash=_snapshot_item_hash(item),
                        source_markdown=source.markdown if source is not None else None,
                        source_hash=source.content_sha256 if source is not None else None,
                        explainer=explainer,
                        translation=translation,
                        note_markdown=note if isinstance(note, str) and note else None,
                        note_source_hash=note_hash,
                        pdf_link=pdf_link,
                        pdf_sha256=(
                            str(item["pdfSha256"])
                            if item.get("pdfSha256") is not None
                            else None
                        ),
                    )
                )
                stored_paths[paper_id] = stored_path
        return tuple(snapshots), stored_paths


async def _bound_source(work: Any, item: dict[str, object], paper_id: str) -> Any:
    source_id = item.get("sourceDocumentId")
    expected_hash = item.get("sourceContentSha256")
    if source_id is None and expected_hash is None:
        return None
    source = await work.sources.get(str(source_id))
    if (
        source is None
        or source.paper_id != paper_id
        or getattr(source.status, "value", source.status) != "ready"
        or source.content_sha256 != expected_hash
        or source.markdown is None
    ):
        raise ValueError("Obsidian source snapshot changed")
    return source


async def _bound_artifact(
    work: Any,
    item: dict[str, object],
    paper_id: str,
    kind: str,
) -> ProjectionArtifact | None:
    head = next(
        (
            candidate
            for candidate in item.get("artifactHeads", ())
            if isinstance(candidate, Mapping) and candidate.get("kind") == kind
        ),
        None,
    )
    if head is None:
        return None
    artifact_id = head.get("artifactId")
    expected_hash = head.get("contentSha256")
    artifact = await work.artifacts.get(str(artifact_id))
    if (
        artifact is None
        or artifact.paper_id != paper_id
        or getattr(artifact.kind, "value", artifact.kind) != kind
        or getattr(artifact.status, "value", artifact.status) != "ready"
        or artifact.content_sha256 != expected_hash
        or artifact.content is None
    ):
        raise ValueError("Obsidian artifact snapshot changed")
    return ProjectionArtifact(
        artifact_id=artifact.id,
        markdown=artifact.content,
        source_hash=artifact.content_sha256,
    )


def _empty_counts() -> dict[str, int]:
    return {
        "exported": 0,
        "unchanged": 0,
        "conflicts": 0,
        "errors": 0,
        "skipped": 0,
        "userManaged": 0,
        "orphaned": 0,
        "deleted": 0,
    }


def _snapshot_item_hash(item: Mapping[str, object]) -> str:
    def plain(value: object) -> object:
        if isinstance(value, Mapping):
            return {key: plain(nested) for key, nested in value.items()}
        if isinstance(value, (tuple, list)):
            return [plain(nested) for nested in value]
        return value

    return hash_canonical_json(plain(item))


def _missing_projection_count(
    snapshots: Iterable[ProjectionSnapshot],
    options: ExportOptions,
) -> int:
    return sum(
        int(options.export_source and snapshot.source_markdown is None)
        + int(options.export_explainer and snapshot.explainer is None)
        + int(options.export_translation and snapshot.translation is None)
        for snapshot in snapshots
    )


def _optional_text(value: object) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _authors_text(value: object) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        decoded = json.loads(value)
    except (TypeError, ValueError):
        return value.strip()
    if isinstance(decoded, list):
        authors = tuple(str(item).strip() for item in decoded if str(item).strip())
        return "; ".join(authors) or None
    return value.strip()


def _paper_tags(paper: dict[str, object]) -> tuple[str, ...]:
    return tuple(
        value
        for field in ("topic", "type", "venue")
        if (value := _optional_text(paper.get(field))) is not None
    )


__all__ = [
    "BatchExportResult",
    "CleanupApplyResult",
    "CleanupPlanItem",
    "CleanupPlanMismatchError",
    "CleanupPreview",
    "ObsidianBatchExporter",
    "ObsidianSpecExporter",
]
