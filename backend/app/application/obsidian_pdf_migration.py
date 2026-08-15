from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import base64
import hashlib
import json
import os
from pathlib import Path
import stat
from typing import Any, Callable

from backend.app.application.obsidian_projection import project_paths
from backend.app.domain import VaultProjection
from backend.app.infrastructure.bound_vault_root import (
    BoundTargetIdentity,
    BoundTargetSnapshot,
    BoundVaultRoot,
    ObsidianVaultError,
    VaultRelativePath,
)
from backend.app.providers.obsidian_vault import (
    ManifestEntry,
    ObsidianManifest,
    merge_manifest,
    parse_manifest,
    serialize_manifest,
)
from backend.app.providers.pdf_files import PdfFiles


_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


class PdfMigrationError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class PdfMigrationPlan:
    document: dict[str, object]
    canonical_bytes: bytes
    sha256: str


@dataclass(frozen=True, slots=True)
class PdfMigrationCommandResult:
    intent_path: Path
    intent_sha256: str
    state: str


@dataclass(frozen=True, slots=True)
class _IntentSnapshot:
    path: Path
    document: dict[str, object]
    canonical_bytes: bytes
    sha256: str
    device: int
    inode: int
    size: int


def _intent_snapshot(
    path: Path,
    document: dict[str, object],
    canonical: bytes,
    identity: BoundTargetIdentity,
) -> _IntentSnapshot:
    return _IntentSnapshot(
        path=path,
        document=document,
        canonical_bytes=canonical,
        sha256=identity.sha256,
        device=identity.device,
        inode=identity.inode,
        size=identity.size,
    )


class MigrationIntentStore:
    def __init__(
        self,
        *,
        barrier: Callable[[str, dict[str, object]], None] | None = None,
    ) -> None:
        self._barrier = barrier

    def publish_new(
        self,
        path: str | Path,
        document: dict[str, object],
    ) -> _IntentSnapshot:
        target = _exact_new_path(path)
        canonical = _canonical_json(document)

        def before_publish(_path: Path) -> None:
            for name in (
                "intent_before_open",
                "intent_before_write",
                "intent_before_flush",
                "intent_before_file_fsync",
                "intent_before_parent_fsync",
            ):
                self._call(name, target)

        try:
            with BoundVaultRoot.open(
                target.parent,
                before_publish=before_publish,
            ) as root:
                relative = VaultRelativePath(target.name)
                published = None
                try:
                    published = root.publish_new(relative, canonical)
                    root.verify()
                except Exception:
                    if published is not None:
                        try:
                            root.delete_managed(relative, published.identity)
                        except Exception:
                            pass
                    raise
        except PdfMigrationError:
            raise
        except ObsidianVaultError as error:
            if error.code == "OBSIDIAN_TARGET_EXISTS":
                raise PdfMigrationError(
                    "OBSIDIAN_INTENT_EXISTS",
                    "The exact MigrationIntent output path already exists.",
                ) from error
            raise PdfMigrationError(
                "OBSIDIAN_INTENT_PARENT_CHANGED",
                "The bound MigrationIntent parent changed during publication.",
            ) from error
        return _intent_snapshot(
            target,
            document,
            canonical,
            published.identity,
        )

    def load(self, path: str | Path, expected_sha256: str) -> _IntentSnapshot:
        if not _is_sha256(expected_sha256):
            raise PdfMigrationError(
                "OBSIDIAN_INTENT_SHA_INVALID",
                "The confirmed MigrationIntent SHA-256 is invalid.",
            )
        target = _exact_existing_path(path)
        try:
            with BoundVaultRoot.open(target.parent) as root:
                current = root.inspect_target(
                    VaultRelativePath(target.name),
                    create_parent=False,
                )
                root.verify()
        except ObsidianVaultError as error:
            raise PdfMigrationError(
                "OBSIDIAN_INTENT_UNAVAILABLE",
                "The exact MigrationIntent path is unavailable.",
            ) from error
        if current is None:
            raise PdfMigrationError(
                "OBSIDIAN_INTENT_UNAVAILABLE",
                "The exact MigrationIntent path is unavailable.",
            )
        canonical = current.data
        actual_sha = hashlib.sha256(canonical).hexdigest()
        if actual_sha != expected_sha256:
            raise PdfMigrationError(
                "OBSIDIAN_INTENT_SHA_MISMATCH",
                "The current MigrationIntent SHA-256 does not match confirmation.",
            )
        document = _decode_canonical_intent(canonical)
        return _intent_snapshot(
            target,
            document,
            canonical,
            current.identity,
        )

    def checkpoint(
        self,
        snapshot: _IntentSnapshot,
        document: dict[str, object],
    ) -> _IntentSnapshot:
        canonical = _canonical_json(document)
        with BoundVaultRoot.open(snapshot.path.parent) as root:
            relative = VaultRelativePath(snapshot.path.name)
            current = root.inspect_target(relative)
            if current is None or (
                current.identity.device,
                current.identity.inode,
                current.identity.size,
                current.identity.sha256,
            ) != (
                snapshot.device,
                snapshot.inode,
                snapshot.size,
                snapshot.sha256,
            ):
                raise PdfMigrationError(
                    "OBSIDIAN_INTENT_IDENTITY_CHANGED",
                    "The MigrationIntent changed before its checkpoint.",
                )
            published = root.replace_managed(relative, canonical, current.identity)
            root.verify()
        return _intent_snapshot(
            snapshot.path,
            document,
            canonical,
            published.identity,
        )

    def _call(self, name: str, path: Path) -> None:
        if self._barrier is not None:
            self._barrier(name, {"intentPath": str(path)})


class ObsidianPdfMigration:
    def __init__(
        self,
        *,
        pdf_files: PdfFiles,
        root: BoundVaultRoot,
        repository: object,
        root_folder: str,
        settings_fingerprint: str,
        clock: Callable[[], datetime] | None = None,
        barrier: Callable[[str, dict[str, object]], None] | None = None,
    ) -> None:
        if not _is_sha256(settings_fingerprint):
            raise ValueError("settings_fingerprint must be a lowercase SHA-256")
        self._pdf_files = pdf_files
        self._root = root
        self._repository = repository
        self._root_folder = root_folder
        self._settings_fingerprint = settings_fingerprint
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._barrier = barrier
        self._intent_store = MigrationIntentStore(barrier=barrier)
        VaultRelativePath(f"{root_folder}/.paper-study/root-check")

    async def plan(self) -> PdfMigrationPlan:
        papers = await self._repository.list_papers_for_pdf_migration()
        manifest_snapshot, manifest = self._read_manifest()
        manifest_exists = manifest_snapshot is not None
        simulated_manifest = manifest
        items: list[dict[str, object]] = []

        for row in sorted(papers, key=lambda value: str(value.get("id"))):
            paper_id = str(row.get("id") or "")
            target_path = project_paths(paper_id).pdf
            old_pdf_path = row.get("pdf_path")
            opened = self._pdf_files.open_for_id(
                paper_id,
                stored_path=old_pdf_path if isinstance(old_pdf_path, (str, Path)) else None,
            )
            if opened is None:
                raise PdfMigrationError(
                    "OBSIDIAN_PDF_SOURCE_MISSING",
                    "A planned PDF source is missing or outside configured roots.",
                )
            with opened.stream:
                if opened.path is None:
                    raise PdfMigrationError(
                        "OBSIDIAN_PDF_SOURCE_IDENTITY_MISSING",
                        "The PDF provider did not return its validated source path.",
                    )
                source_path = opened.path.resolve()
                source_bytes = _read_stream(opened.stream, opened.size)
            source_sha = hashlib.sha256(source_bytes).hexdigest()

            target_snapshot = self._inspect_optional(target_path)
            prior_entry = next(
                (entry for entry in manifest.entries if entry.path == target_path),
                None,
            )
            prior_ledger = await self._repository.find_by_target_path(target_path)
            prior_ledger_document = _projection_document(prior_ledger)
            prior_entry_document = _manifest_entry_document(prior_entry)
            ownership = _target_ownership(
                target_snapshot,
                prior_entry,
                prior_ledger_document,
            )
            prior_manifest_bytes = (
                serialize_manifest(simulated_manifest)
                if manifest_exists or items
                else None
            )
            expected_entry = ManifestEntry(
                path=target_path,
                kind="pdf-copy",
                paper_id=paper_id,
                artifact_id=None,
                ownership="managed",
                source_hash=source_sha,
                exported_hash=source_sha,
            )
            simulated_manifest = merge_manifest(
                simulated_manifest,
                (expected_entry,),
                generated_at=simulated_manifest.generated_at,
            )
            expected_manifest_bytes = serialize_manifest(simulated_manifest)

            item = {
                "oldPdfPath": old_pdf_path,
                "paperId": paper_id,
                "priorLedger": prior_ledger_document,
                "priorLedgerHash": _document_hash(prior_ledger_document),
                "priorManifestEntry": prior_entry_document,
                "priorManifestEntryHash": _document_hash(prior_entry_document),
                "priorManifestHash": _bytes_hash(prior_manifest_bytes),
                "sourcePath": str(source_path),
                "sourceSha256": source_sha,
                "sourceSize": len(source_bytes),
                "targetPath": target_path,
                "targetPrior": _target_document(target_snapshot, ownership),
                "expectedManifestEntry": _manifest_entry_document(expected_entry),
                "expectedManifestHash": hashlib.sha256(expected_manifest_bytes).hexdigest(),
            }
            items.append(item)

        document: dict[str, object] = {
            "items": items,
            "schemaVersion": 1,
            "settingsFingerprint": self._settings_fingerprint,
        }
        canonical = _canonical_json(document)
        return PdfMigrationPlan(
            document=document,
            canonical_bytes=canonical,
            sha256=hashlib.sha256(canonical).hexdigest(),
        )

    async def prepare(
        self,
        *,
        confirm_plan_sha: str,
        intent_output: str | Path,
    ) -> PdfMigrationCommandResult:
        plan = await self.plan()
        if plan.sha256 != confirm_plan_sha:
            raise PdfMigrationError(
                "OBSIDIAN_MIGRATION_PLAN_SHA_MISMATCH",
                "The current canonical plan does not match operator confirmation.",
            )
        created_at = _timestamp(_utc_now(self._clock()))
        assert created_at is not None
        intent_items: list[dict[str, object]] = []
        for sequence, item in enumerate(plan.document["items"]):
            assert isinstance(item, dict)
            target_path = str(item["targetPath"])
            current_target = self._inspect_optional(target_path)
            current_target_document = _target_document(
                current_target,
                str(item["targetPrior"]["ownership"]),
            )
            if current_target_document != item["targetPrior"]:
                raise PdfMigrationError(
                    "OBSIDIAN_MIGRATION_PLAN_STALE",
                    "Vault target evidence changed after the canonical plan.",
                )
            prior_target = dict(current_target_document)
            prior_target["contentBase64"] = (
                base64.b64encode(current_target.data).decode("ascii")
                if current_target is not None
                else None
            )
            expected_ledger = {
                "artifactId": None,
                "errorMessage": None,
                "exportedAt": created_at,
                "exportedHash": item["sourceSha256"],
                "id": "obsidian-"
                + hashlib.sha256(target_path.encode("utf-8")).hexdigest()[:32],
                "paperId": item["paperId"],
                "sourceHash": item["sourceSha256"],
                "status": "exported",
                "targetPath": target_path,
            }
            intent_items.append(
                {
                    "checkpoints": {
                        "dbUpdated": False,
                        "itemSealed": False,
                        "ledgerUpdated": False,
                        "manifestUpdated": False,
                        "targetPublished": False,
                    },
                    "expectedPost": {
                        "dbValueKind": "vault-target",
                        "ledger": expected_ledger,
                        "ledgerHash": _document_hash(expected_ledger),
                        "manifestEntry": item["expectedManifestEntry"],
                        "manifestHash": item["expectedManifestHash"],
                        "target": {
                            "exists": True,
                            "identity": None,
                            "ownership": "managed",
                            "sha256": item["sourceSha256"],
                            "size": item["sourceSize"],
                        },
                    },
                    "paperId": item["paperId"],
                    "phase": "prepared",
                    "prior": {
                        "dbValue": item["oldPdfPath"],
                        "ledger": item["priorLedger"],
                        "ledgerHash": item["priorLedgerHash"],
                        "manifestEntry": item["priorManifestEntry"],
                        "manifestEntryHash": item["priorManifestEntryHash"],
                        "manifestHash": item["priorManifestHash"],
                        "target": prior_target,
                    },
                    "sequence": sequence,
                    "source": {
                        "path": item["sourcePath"],
                        "sha256": item["sourceSha256"],
                        "size": item["sourceSize"],
                    },
                    "target": {"relativePath": target_path},
                }
            )
        document: dict[str, object] = {
            "createdAt": created_at,
            "items": intent_items,
            "planSha256": plan.sha256,
            "receipt": None,
            "schemaVersion": 1,
            "settingsFingerprint": self._settings_fingerprint,
            "state": "prepared",
            "updatedAt": created_at,
        }
        snapshot = self._intent_store.publish_new(intent_output, document)
        return PdfMigrationCommandResult(
            snapshot.path,
            snapshot.sha256,
            "prepared",
        )

    async def apply(
        self,
        *,
        intent: str | Path,
        confirm_intent_sha: str,
    ) -> PdfMigrationCommandResult:
        snapshot = self._intent_store.load(intent, confirm_intent_sha)
        document = snapshot.document
        self._validate_intent_binding(document)
        if document["state"] in {"sealed", "rolled_back"}:
            return PdfMigrationCommandResult(snapshot.path, snapshot.sha256, str(document["state"]))
        source_bytes = await self._preflight_apply(document)
        document["state"] = "applying"

        for raw_item in document["items"]:
            item = raw_item
            if item["phase"] == "item_sealed":
                continue
            snapshot = await self._recover_item(snapshot, document, item)
            data = source_bytes[str(item["paperId"])]
            target_path = str(item["target"]["relativePath"])
            vault_relative = VaultRelativePath(f"{self._root_folder}/{target_path}")

            if item["phase"] == "prepared":
                prior_target = item["prior"]["target"]
                current = self._inspect_optional(target_path)
                if current is None:
                    published = self._root.publish_new(vault_relative, data)
                else:
                    if prior_target["ownership"] != "managed":
                        raise PdfMigrationError(
                            "OBSIDIAN_MIGRATION_TARGET_UNOWNED",
                            "An existing PDF target lacks managed replacement proof.",
                        )
                    published = self._root.replace_managed(
                        vault_relative,
                        data,
                        current.identity,
                    )
                item["expectedPost"]["target"]["identity"] = {
                    "device": published.identity.device,
                    "inode": published.identity.inode,
                    "opaqueId": published.identity.opaque_id,
                }
                self._call_barrier("apply_target_published", item)
                snapshot = self._checkpoint_phase(
                    snapshot,
                    document,
                    item,
                    "target_published",
                    "targetPublished",
                )

            if item["phase"] == "target_published":
                replacement = self._target_absolute_path(target_path)
                changed = await self._repository.compare_and_set_paper_pdf_path(
                    str(item["paperId"]),
                    expected=item["prior"]["dbValue"],
                    replacement=replacement,
                )
                if not changed:
                    raise PdfMigrationError(
                        "OBSIDIAN_MIGRATION_DB_CONFLICT",
                        "papers.pdf_path changed during migration.",
                    )
                self._call_barrier("after_db_update_before_checkpoint", item)
                snapshot = self._checkpoint_phase(
                    snapshot,
                    document,
                    item,
                    "db_updated",
                    "dbUpdated",
                )

            if item["phase"] == "db_updated":
                projection = _projection_from_document(item["expectedPost"]["ledger"])
                await self._repository.upsert(projection)
                self._call_barrier("after_ledger_update_before_checkpoint", item)
                snapshot = self._checkpoint_phase(
                    snapshot,
                    document,
                    item,
                    "ledger_updated",
                    "ledgerUpdated",
                )

            if item["phase"] == "ledger_updated":
                self._publish_item_manifest(item)
                self._call_barrier("apply_manifest_updated", item)
                snapshot = self._checkpoint_phase(
                    snapshot,
                    document,
                    item,
                    "manifest_updated",
                    "manifestUpdated",
                )

            if item["phase"] == "manifest_updated":
                snapshot = self._checkpoint_phase(
                    snapshot,
                    document,
                    item,
                    "item_sealed",
                    "itemSealed",
                )
            self._call_barrier("between_items", item)

        if all(item["phase"] == "item_sealed" for item in document["items"]):
            snapshot = await self._seal(snapshot, document)
        return PdfMigrationCommandResult(snapshot.path, snapshot.sha256, str(document["state"]))

    async def rollback(
        self,
        *,
        intent: str | Path,
        confirm_intent_sha: str,
    ) -> PdfMigrationCommandResult:
        snapshot = self._intent_store.load(intent, confirm_intent_sha)
        document = snapshot.document
        self._validate_intent_binding(document)
        if document["state"] == "rolled_back":
            return PdfMigrationCommandResult(snapshot.path, snapshot.sha256, "rolled_back")
        await self._preflight_apply(document)

        if document["state"] != "rolling_back":
            for item in document["items"]:
                if item["phase"] != "item_sealed":
                    snapshot = await self._recover_item(snapshot, document, item)
            self._preflight_rollback_targets(document)
            document["state"] = "rolling_back"
            document["updatedAt"] = _timestamp(_utc_now(self._clock()))
            snapshot = self._intent_store.checkpoint(snapshot, document)
        else:
            self._preflight_rollback_targets(document)

        for item in reversed(document["items"]):
            if item["phase"] in {"prepared", "rolled_back_item"}:
                continue
            snapshot = await self._rollback_item(snapshot, document, item)

        rolled_back_at = _timestamp(_utc_now(self._clock()))
        prior_db = [
            {"paperId": item["paperId"], "pdfPath": item["prior"]["dbValue"]}
            for item in document["items"]
        ]
        prior_ledgers = [item["prior"]["ledger"] for item in document["items"]]
        document["receipt"] = {
            "planSha256": document["planSha256"],
            "priorDbHash": hashlib.sha256(_canonical_json(prior_db)).hexdigest(),
            "priorLedgerHash": hashlib.sha256(_canonical_json(prior_ledgers)).hexdigest(),
            "priorManifestHash": document["items"][0]["prior"]["manifestHash"]
            if document["items"]
            else None,
            "rolledBackAt": rolled_back_at,
            "sourcePreserved": True,
        }
        document["state"] = "rolled_back"
        document["updatedAt"] = rolled_back_at
        snapshot = self._intent_store.checkpoint(snapshot, document)
        return PdfMigrationCommandResult(snapshot.path, snapshot.sha256, "rolled_back")

    def _preflight_rollback_targets(self, document: dict[str, object]) -> None:
        for item in document["items"]:
            if item["phase"] in {"prepared", "rolled_back_item"}:
                continue
            target = self._inspect_optional(str(item["target"]["relativePath"]))
            if not (
                _target_matches(target, item["expectedPost"]["target"])
                or _target_matches(target, item["prior"]["target"])
            ):
                raise PdfMigrationError(
                    "OBSIDIAN_MIGRATION_ROLLBACK_CONFLICT",
                    "A PDF target changed after migration and cannot be rolled back.",
                )

    async def _rollback_item(
        self,
        snapshot: _IntentSnapshot,
        document: dict[str, object],
        item: dict[str, object],
    ) -> _IntentSnapshot:
        target_path = str(item["target"]["relativePath"])
        prior = item["prior"]
        expected = item["expectedPost"]

        manifest_snapshot, _manifest = self._read_manifest()
        manifest_hash = manifest_snapshot.identity.sha256 if manifest_snapshot else None
        if manifest_hash == expected["manifestHash"]:
            self._restore_item_manifest(item, manifest_snapshot)
            self._call_barrier("after_rollback_manifest_before_checkpoint", item)
        elif manifest_hash != prior["manifestHash"]:
            raise PdfMigrationError(
                "OBSIDIAN_MIGRATION_ROLLBACK_CONFLICT",
                "Manifest evidence changed after migration.",
            )
        snapshot = self._checkpoint_rollback(
            snapshot, document, item, "rollback_manifest_restored", "rollbackManifest"
        )

        current_ledger = _projection_document(
            await self._repository.find_by_target_path(target_path)
        )
        if current_ledger == expected["ledger"]:
            restored = await self._repository.restore_projection(
                expected=_projection_from_document(expected["ledger"]),
                prior=_projection_from_document(prior["ledger"])
                if prior["ledger"] is not None
                else None,
            )
            if not restored:
                raise PdfMigrationError(
                    "OBSIDIAN_MIGRATION_ROLLBACK_CONFLICT",
                    "Ledger evidence changed during rollback.",
                )
            self._call_barrier("after_rollback_ledger_before_checkpoint", item)
        elif current_ledger != prior["ledger"]:
            raise PdfMigrationError(
                "OBSIDIAN_MIGRATION_ROLLBACK_CONFLICT",
                "Ledger evidence changed after migration.",
            )
        snapshot = self._checkpoint_rollback(
            snapshot, document, item, "rollback_ledger_restored", "rollbackLedger"
        )

        current_db = await self._repository.get_paper_pdf_path(str(item["paperId"]))
        expected_db = self._target_absolute_path(target_path)
        if current_db == expected_db:
            restored = await self._repository.compare_and_set_paper_pdf_path(
                str(item["paperId"]),
                expected=expected_db,
                replacement=prior["dbValue"],
            )
            if not restored:
                raise PdfMigrationError(
                    "OBSIDIAN_MIGRATION_ROLLBACK_CONFLICT",
                    "papers.pdf_path changed during rollback.",
                )
            self._call_barrier("after_rollback_db_before_checkpoint", item)
        elif current_db != prior["dbValue"]:
            raise PdfMigrationError(
                "OBSIDIAN_MIGRATION_ROLLBACK_CONFLICT",
                "papers.pdf_path changed after migration.",
            )
        snapshot = self._checkpoint_rollback(
            snapshot, document, item, "rollback_db_restored", "rollbackDb"
        )

        current_target = self._inspect_optional(target_path)
        if _target_matches(current_target, expected["target"]):
            assert current_target is not None
            relative = VaultRelativePath(f"{self._root_folder}/{target_path}")
            if prior["target"]["exists"] is False:
                self._root.delete_managed(relative, current_target.identity)
            else:
                prior_bytes = base64.b64decode(
                    str(prior["target"]["contentBase64"]), validate=True
                )
                self._root.replace_managed(relative, prior_bytes, current_target.identity)
            self._call_barrier("after_rollback_target_before_checkpoint", item)
        elif not _target_matches(current_target, prior["target"]):
            raise PdfMigrationError(
                "OBSIDIAN_MIGRATION_ROLLBACK_CONFLICT",
                "A PDF target changed after migration.",
            )
        snapshot = self._checkpoint_rollback(
            snapshot, document, item, "rolled_back_item", "rollbackTarget"
        )
        return snapshot

    def _checkpoint_rollback(
        self,
        snapshot: _IntentSnapshot,
        document: dict[str, object],
        item: dict[str, object],
        phase: str,
        checkpoint: str,
    ) -> _IntentSnapshot:
        item["phase"] = phase
        item["checkpoints"][checkpoint] = True
        document["updatedAt"] = _timestamp(_utc_now(self._clock()))
        updated = self._intent_store.checkpoint(snapshot, document)
        self._call_barrier("intent_checkpoint", {"phase": phase, "paperId": item["paperId"]})
        return updated

    def _restore_item_manifest(
        self,
        item: dict[str, object],
        snapshot: BoundTargetSnapshot | None,
    ) -> None:
        if snapshot is None:
            raise PdfMigrationError(
                "OBSIDIAN_MIGRATION_ROLLBACK_CONFLICT",
                "The managed manifest is missing during rollback.",
            )
        manifest = parse_manifest(snapshot.data)
        target_path = str(item["target"]["relativePath"])
        entries = [entry for entry in manifest.entries if entry.path != target_path]
        prior_entry = item["prior"]["manifestEntry"]
        if prior_entry is not None:
            entries.append(_entry_from_document(prior_entry))
        relative = VaultRelativePath(f"{self._root_folder}/.paper-study/manifest.json")
        if item["prior"]["manifestHash"] is None:
            if entries:
                raise PdfMigrationError(
                    "OBSIDIAN_MIGRATION_ROLLBACK_CONFLICT",
                    "The prior manifest absence cannot be restored safely.",
                )
            self._root.delete_managed(relative, snapshot.identity)
            return
        restored = ObsidianManifest(
            generated_at=manifest.generated_at,
            entries=tuple(entries),
        )
        data = serialize_manifest(restored)
        if hashlib.sha256(data).hexdigest() != item["prior"]["manifestHash"]:
            raise PdfMigrationError(
                "OBSIDIAN_MIGRATION_ROLLBACK_CONFLICT",
                "The prior manifest hash cannot be reconstructed.",
            )
        self._root.replace_managed(relative, data, snapshot.identity)

    def _validate_intent_binding(self, document: dict[str, object]) -> None:
        if document.get("settingsFingerprint") != self._settings_fingerprint:
            raise PdfMigrationError(
                "OBSIDIAN_INTENT_SETTINGS_MISMATCH",
                "The MigrationIntent settings fingerprint is not current.",
            )
        if not _is_sha256(document.get("planSha256")):
            raise PdfMigrationError(
                "OBSIDIAN_INTENT_INVALID",
                "The MigrationIntent plan identity is invalid.",
            )

    async def _preflight_apply(
        self,
        document: dict[str, object],
    ) -> dict[str, bytes]:
        result: dict[str, bytes] = {}
        for item in document["items"]:
            paper_id = str(item["paperId"])
            source = item["source"]
            opened = self._pdf_files.open_for_id(
                paper_id,
                stored_path=source["path"],
            )
            if opened is None or opened.path is None or opened.path.resolve() != Path(source["path"]):
                raise PdfMigrationError(
                    "OBSIDIAN_MIGRATION_SOURCE_CHANGED",
                    "A frozen PDF source path is no longer valid.",
                )
            with opened.stream:
                data = _read_stream(opened.stream, opened.size)
            if (
                len(data) != source["size"]
                or hashlib.sha256(data).hexdigest() != source["sha256"]
            ):
                raise PdfMigrationError(
                    "OBSIDIAN_MIGRATION_SOURCE_CHANGED",
                    "A frozen PDF source changed before apply.",
                )
            result[paper_id] = data
        return result

    async def _recover_item(
        self,
        snapshot: _IntentSnapshot,
        document: dict[str, object],
        item: dict[str, object],
    ) -> _IntentSnapshot:
        prior = item["prior"]
        expected = item["expectedPost"]
        target_path = str(item["target"]["relativePath"])
        target = self._inspect_optional(target_path)
        target_prior = _target_matches(target, prior["target"])
        target_post = _target_matches(target, expected["target"])
        if target_post and target is not None and expected["target"]["identity"] is None:
            expected["target"]["identity"] = {
                "device": target.identity.device,
                "inode": target.identity.inode,
                "opaqueId": target.identity.opaque_id,
            }

        db_value = await self._repository.get_paper_pdf_path(str(item["paperId"]))
        db_prior = db_value == prior["dbValue"]
        db_post = db_value == self._target_absolute_path(target_path)
        ledger = _projection_document(
            await self._repository.find_by_target_path(target_path)
        )
        ledger_prior = ledger == prior["ledger"]
        ledger_post = ledger == expected["ledger"]
        manifest_snapshot, _manifest = self._read_manifest()
        manifest_hash = (
            manifest_snapshot.identity.sha256 if manifest_snapshot is not None else None
        )
        manifest_prior = manifest_hash == prior["manifestHash"]
        manifest_post = manifest_hash == expected["manifestHash"]

        if target_post and db_post and ledger_post and manifest_post:
            evidence_rank = 4
        elif target_post and db_post and ledger_post and manifest_prior:
            evidence_rank = 3
        elif target_post and db_post and ledger_prior and manifest_prior:
            evidence_rank = 2
        elif target_post and db_prior and ledger_prior and manifest_prior:
            evidence_rank = 1
        elif target_prior and db_prior and ledger_prior and manifest_prior:
            evidence_rank = 0
        else:
            raise PdfMigrationError(
                "OBSIDIAN_MIGRATION_EVIDENCE_DIVERGED",
                "Migration evidence is neither the frozen prior nor expected post state.",
            )

        phase_order = {
            "prepared": 0,
            "target_published": 1,
            "db_updated": 2,
            "ledger_updated": 3,
            "manifest_updated": 4,
            "item_sealed": 5,
        }
        current_rank = phase_order.get(str(item["phase"]), -1)
        if current_rank < 0 or evidence_rank < min(current_rank, 4):
            raise PdfMigrationError(
                "OBSIDIAN_MIGRATION_EVIDENCE_DIVERGED",
                "Migration evidence regressed behind its durable checkpoint.",
            )
        phases = (
            (1, "target_published", "targetPublished"),
            (2, "db_updated", "dbUpdated"),
            (3, "ledger_updated", "ledgerUpdated"),
            (4, "manifest_updated", "manifestUpdated"),
        )
        for rank, phase, checkpoint in phases:
            if current_rank < rank <= evidence_rank:
                snapshot = self._checkpoint_phase(
                    snapshot,
                    document,
                    item,
                    phase,
                    checkpoint,
                )
                current_rank = rank
        if evidence_rank == 4 and current_rank < 5:
            snapshot = self._checkpoint_phase(
                snapshot,
                document,
                item,
                "item_sealed",
                "itemSealed",
            )
        return snapshot

    def _checkpoint_phase(
        self,
        snapshot: _IntentSnapshot,
        document: dict[str, object],
        item: dict[str, object],
        phase: str,
        checkpoint: str,
    ) -> _IntentSnapshot:
        item["phase"] = phase
        item["checkpoints"][checkpoint] = True
        document["updatedAt"] = _timestamp(_utc_now(self._clock()))
        updated = self._intent_store.checkpoint(snapshot, document)
        self._call_barrier("intent_checkpoint", {"phase": phase, "paperId": item["paperId"]})
        return updated

    def _publish_item_manifest(self, item: dict[str, object]) -> None:
        snapshot, manifest = self._read_manifest()
        current_hash = snapshot.identity.sha256 if snapshot is not None else None
        if current_hash != item["prior"]["manifestHash"]:
            raise PdfMigrationError(
                "OBSIDIAN_MIGRATION_MANIFEST_CONFLICT",
                "The managed manifest changed during migration.",
            )
        entry = _entry_from_document(item["expectedPost"]["manifestEntry"])
        updated = merge_manifest(
            manifest,
            (entry,),
            generated_at=manifest.generated_at,
        )
        data = serialize_manifest(updated)
        if hashlib.sha256(data).hexdigest() != item["expectedPost"]["manifestHash"]:
            raise PdfMigrationError(
                "OBSIDIAN_MIGRATION_MANIFEST_CONFLICT",
                "The expected manifest identity is invalid.",
            )
        relative = VaultRelativePath(f"{self._root_folder}/.paper-study/manifest.json")
        if snapshot is None:
            self._root.publish_new(relative, data)
        else:
            self._root.replace_managed(relative, data, snapshot.identity)

    def _target_absolute_path(self, target_path: str) -> str:
        return str((self._root.path / self._root_folder / target_path).absolute())

    def _call_barrier(self, name: str, context: dict[str, object]) -> None:
        if self._barrier is not None:
            self._barrier(name, context)

    async def _seal(
        self,
        snapshot: _IntentSnapshot,
        document: dict[str, object],
    ) -> _IntentSnapshot:
        if document["state"] == "sealed":
            return snapshot
        db_rows: list[dict[str, object]] = []
        ledger_rows: list[dict[str, object]] = []
        item_hashes: list[dict[str, str]] = []
        for item in document["items"]:
            paper_id = str(item["paperId"])
            source = item["source"]
            opened = self._pdf_files.open_for_id(paper_id, stored_path=source["path"])
            if opened is None or opened.path is None or opened.path.resolve() != Path(source["path"]):
                raise PdfMigrationError(
                    "OBSIDIAN_MIGRATION_SOURCE_CHANGED",
                    "A source PDF was not preserved through migration.",
                )
            with opened.stream:
                data = _read_stream(opened.stream, opened.size)
            if hashlib.sha256(data).hexdigest() != source["sha256"]:
                raise PdfMigrationError(
                    "OBSIDIAN_MIGRATION_SOURCE_CHANGED",
                    "A source PDF was not preserved through migration.",
                )
            target_path = str(item["target"]["relativePath"])
            db_rows.append(
                {
                    "paperId": paper_id,
                    "pdfPath": await self._repository.get_paper_pdf_path(paper_id),
                }
            )
            ledger = _projection_document(
                await self._repository.find_by_target_path(target_path)
            )
            if ledger != item["expectedPost"]["ledger"]:
                raise PdfMigrationError(
                    "OBSIDIAN_MIGRATION_EVIDENCE_DIVERGED",
                    "Ledger evidence changed before sealing.",
                )
            ledger_rows.append(ledger)
            item_hashes.append(
                {
                    "paperId": paper_id,
                    "sha256": hashlib.sha256(
                        _canonical_json(item["expectedPost"])
                    ).hexdigest(),
                }
            )
        manifest_snapshot, _manifest = self._read_manifest()
        if manifest_snapshot is None:
            raise PdfMigrationError(
                "OBSIDIAN_MIGRATION_EVIDENCE_DIVERGED",
                "The final managed manifest is missing.",
            )
        sealed_at = _timestamp(_utc_now(self._clock()))
        document["receipt"] = {
            "finalDbHash": hashlib.sha256(_canonical_json(db_rows)).hexdigest(),
            "finalItemHashes": item_hashes,
            "finalLedgerHash": hashlib.sha256(_canonical_json(ledger_rows)).hexdigest(),
            "finalManifestHash": manifest_snapshot.identity.sha256,
            "planSha256": document["planSha256"],
            "sealedAt": sealed_at,
            "sourcePreserved": True,
        }
        document["state"] = "sealed"
        document["updatedAt"] = sealed_at
        updated = self._intent_store.checkpoint(snapshot, document)
        self._call_barrier("intent_sealed", {"phase": "sealed"})
        return updated

    def _read_manifest(self) -> tuple[BoundTargetSnapshot | None, ObsidianManifest]:
        snapshot = self._inspect_optional(".paper-study/manifest.json")
        if snapshot is None:
            return None, ObsidianManifest(generated_at=_EPOCH, entries=())
        return snapshot, parse_manifest(snapshot.data)

    def _inspect_optional(self, target_path: str) -> BoundTargetSnapshot | None:
        relative = VaultRelativePath(f"{self._root_folder}/{target_path}")
        try:
            return self._root.inspect_target(relative, create_parent=False)
        except ObsidianVaultError as error:
            if error.code == "OBSIDIAN_PARENT_CHANGED":
                return None
            raise


def _read_stream(stream: Any, expected_size: int) -> bytes:
    chunks: list[bytes] = []
    size = 0
    while True:
        chunk = stream.read(1024 * 1024)
        if not isinstance(chunk, bytes):
            raise PdfMigrationError(
                "OBSIDIAN_PDF_SOURCE_CHANGED",
                "A PDF source descriptor returned invalid data.",
            )
        if not chunk:
            break
        chunks.append(chunk)
        size += len(chunk)
    if size != expected_size:
        raise PdfMigrationError(
            "OBSIDIAN_PDF_SOURCE_CHANGED",
            "A PDF source changed while it was being inspected.",
        )
    return b"".join(chunks)


def _target_document(
    snapshot: BoundTargetSnapshot | None,
    ownership: str,
) -> dict[str, object]:
    if snapshot is None:
        return {
            "exists": False,
            "identity": None,
            "ownership": "absent",
            "sha256": None,
            "size": None,
        }
    identity = snapshot.identity
    return {
        "exists": True,
        "identity": {
            "device": identity.device,
            "inode": identity.inode,
            "opaqueId": identity.opaque_id,
        },
        "ownership": ownership,
        "sha256": identity.sha256,
        "size": identity.size,
    }


def _target_ownership(
    snapshot: BoundTargetSnapshot | None,
    entry: ManifestEntry | None,
    ledger: dict[str, object] | None,
) -> str:
    if snapshot is None:
        return "absent"
    if (
        entry is not None
        and ledger is not None
        and entry.kind == "pdf-copy"
        and entry.ownership == "managed"
        and entry.exported_hash == snapshot.identity.sha256
        and ledger.get("exportedHash") == snapshot.identity.sha256
        and ledger.get("targetPath") == entry.path
        and ledger.get("sourceHash") == entry.source_hash
        and ledger.get("status") in {"exported", "unchanged"}
    ):
        return "managed"
    return "unowned"


def _projection_document(value: object | None) -> dict[str, object] | None:
    if value is None:
        return None
    exported_at = getattr(value, "exported_at", None)
    return {
        "artifactId": getattr(value, "artifact_id", None),
        "errorMessage": getattr(value, "error_message", None),
        "exportedAt": _timestamp(exported_at),
        "exportedHash": getattr(value, "exported_hash", None),
        "id": getattr(value, "id"),
        "paperId": getattr(value, "paper_id"),
        "sourceHash": getattr(value, "source_hash"),
        "status": getattr(value, "status"),
        "targetPath": getattr(value, "target_path"),
    }


def _manifest_entry_document(value: ManifestEntry | None) -> dict[str, object] | None:
    return value.to_dict() if value is not None else None


def _entry_from_document(value: object) -> ManifestEntry:
    if not isinstance(value, dict):
        raise PdfMigrationError("OBSIDIAN_INTENT_INVALID", "Manifest entry evidence is invalid.")
    return ManifestEntry(
        path=str(value["path"]),
        kind=str(value["kind"]),
        paper_id=str(value["paperId"]),
        artifact_id=value["artifactId"],
        ownership=str(value["ownership"]),
        source_hash=str(value["sourceHash"]),
        exported_hash=str(value["exportedHash"]),
    )


def _projection_from_document(value: object) -> VaultProjection:
    if not isinstance(value, dict):
        raise PdfMigrationError("OBSIDIAN_INTENT_INVALID", "Ledger evidence is invalid.")
    exported_at = datetime.fromisoformat(str(value["exportedAt"]).replace("Z", "+00:00"))
    return VaultProjection(
        id=str(value["id"]),
        paper_id=str(value["paperId"]),
        artifact_id=value["artifactId"],
        target_path=str(value["targetPath"]),
        source_hash=str(value["sourceHash"]),
        exported_hash=str(value["exportedHash"]),
        status=str(value["status"]),
        exported_at=exported_at,
        error_message=value["errorMessage"],
    )


def _document_hash(value: object | None) -> str | None:
    return hashlib.sha256(_canonical_json(value)).hexdigest() if value is not None else None


def _bytes_hash(value: bytes | None) -> str | None:
    return hashlib.sha256(value).hexdigest() if value is not None else None


def _canonical_json(value: object) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def _timestamp(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _is_sha256(value: object) -> bool:
    return bool(
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _exact_new_path(value: str | Path) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        raise PdfMigrationError(
            "OBSIDIAN_INTENT_PATH_NOT_EXACT",
            "MigrationIntent paths must be absolute exact paths.",
        )
    if any(part in {".", ".."} for part in path.parts):
        raise PdfMigrationError(
            "OBSIDIAN_INTENT_PATH_NOT_EXACT",
            "MigrationIntent paths cannot contain aliases.",
        )
    absolute = Path(os.path.abspath(path))
    try:
        parent = absolute.parent.resolve(strict=True)
    except OSError as error:
        raise PdfMigrationError(
            "OBSIDIAN_INTENT_PARENT_UNAVAILABLE",
            "The exact MigrationIntent parent directory is unavailable.",
        ) from error
    if parent != absolute.parent or not parent.is_dir():
        raise PdfMigrationError(
            "OBSIDIAN_INTENT_PATH_NOT_EXACT",
            "MigrationIntent paths cannot use an aliased parent directory.",
        )
    return absolute


def _exact_existing_path(value: str | Path) -> Path:
    path = _exact_new_path(value)
    try:
        resolved = path.resolve(strict=True)
    except OSError as error:
        raise PdfMigrationError(
            "OBSIDIAN_INTENT_UNAVAILABLE",
            "The exact MigrationIntent path is unavailable.",
        ) from error
    if resolved != path:
        raise PdfMigrationError(
            "OBSIDIAN_INTENT_PATH_NOT_EXACT",
            "MigrationIntent paths cannot use aliases or links.",
        )
    metadata = path.stat(follow_symlinks=False)
    if not stat.S_ISREG(metadata.st_mode):
        raise PdfMigrationError(
            "OBSIDIAN_INTENT_UNAVAILABLE",
            "The exact MigrationIntent path is not a regular file.",
        )
    return path


def _decode_canonical_intent(data: bytes) -> dict[str, object]:
    try:
        value = json.loads(data.decode("utf-8"), object_pairs_hook=_unique_object)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
        raise PdfMigrationError(
            "OBSIDIAN_INTENT_INVALID",
            "The MigrationIntent is not strict canonical JSON.",
        ) from error
    if not isinstance(value, dict) or _canonical_json(value) != data:
        raise PdfMigrationError(
            "OBSIDIAN_INTENT_INVALID",
            "The MigrationIntent is not strict canonical JSON.",
        )
    expected = {
        "createdAt",
        "items",
        "planSha256",
        "receipt",
        "schemaVersion",
        "settingsFingerprint",
        "state",
        "updatedAt",
    }
    if set(value) != expected or value.get("schemaVersion") != 1:
        raise PdfMigrationError(
            "OBSIDIAN_INTENT_INVALID",
            "The MigrationIntent schema is invalid.",
        )
    return value


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate JSON key")
        value[key] = item
    return value


def _utc_now(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("clock must return a timezone-aware datetime")
    return value.astimezone(timezone.utc)


def _target_matches(
    snapshot: BoundTargetSnapshot | None,
    evidence: object,
) -> bool:
    if not isinstance(evidence, dict):
        return False
    if evidence.get("exists") is False:
        return snapshot is None
    if snapshot is None or evidence.get("exists") is not True:
        return False
    if (
        snapshot.identity.sha256 != evidence.get("sha256")
        or snapshot.identity.size != evidence.get("size")
    ):
        return False
    identity = evidence.get("identity")
    if identity is None:
        return True
    return bool(
        isinstance(identity, dict)
        and snapshot.identity.device == identity.get("device")
        and snapshot.identity.inode == identity.get("inode")
        and snapshot.identity.opaque_id == identity.get("opaqueId")
    )


__all__ = [
    "ObsidianPdfMigration",
    "MigrationIntentStore",
    "PdfMigrationCommandResult",
    "PdfMigrationError",
    "PdfMigrationPlan",
]
