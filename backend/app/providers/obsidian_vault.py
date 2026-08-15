from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import stat
from typing import Any, Callable, Iterable, Mapping
from uuid import uuid4

from backend.app.application.obsidian_projection import ProjectionFile
from backend.app.domain import VaultProjection
from backend.app.infrastructure.bound_vault_root import (
    BoundTargetIdentity,
    BoundVaultRoot,
    ObsidianVaultError,
    VaultRelativePath,
)


_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_MAX_FRONTMATTER_BYTES = 64 * 1024


@dataclass(frozen=True, slots=True)
class ManagedMarker:
    paper_id: str
    kind: str
    source_hash: str
    artifact_id: str | None


@dataclass(frozen=True, slots=True)
class NoteSeedMarker:
    paper_id: str
    source_hash: str


@dataclass(frozen=True, slots=True)
class ManifestEntry:
    path: str
    kind: str
    paper_id: str
    artifact_id: str | None
    ownership: str
    source_hash: str
    exported_hash: str

    def __post_init__(self) -> None:
        if (
            not self.path
            or self.path.startswith(("/", "\\"))
            or "\\" in self.path
            or any(part in {"", ".", ".."} for part in self.path.split("/"))
        ):
            raise ValueError("manifest path must be a safe relative POSIX path")
        if self.ownership not in {"managed", "user"}:
            raise ValueError("manifest ownership is invalid")
        if _SHA256.fullmatch(self.source_hash) is None:
            raise ValueError("manifest source hash is invalid")
        if _SHA256.fullmatch(self.exported_hash) is None:
            raise ValueError("manifest exported hash is invalid")

    def to_dict(self) -> dict[str, object]:
        return {
            "path": self.path,
            "kind": self.kind,
            "paperId": self.paper_id,
            "artifactId": self.artifact_id,
            "ownership": self.ownership,
            "sourceHash": self.source_hash,
            "exportedHash": self.exported_hash,
        }


@dataclass(frozen=True, slots=True)
class ObsidianManifest:
    generated_at: datetime
    entries: tuple[ManifestEntry, ...]
    schema_version: int = 1
    exporter_version: str = "obsidian-projection-v1"

    def __post_init__(self) -> None:
        if self.generated_at.tzinfo is None or self.generated_at.utcoffset() is None:
            raise ValueError("manifest timestamp must be timezone-aware")
        ordered = tuple(sorted(self.entries, key=lambda entry: entry.path))
        if len({entry.path.casefold() for entry in ordered}) != len(ordered):
            raise ValueError("manifest paths collide")
        object.__setattr__(self, "entries", ordered)
        object.__setattr__(
            self, "generated_at", self.generated_at.astimezone(timezone.utc)
        )


def build_manifest(
    files: Iterable[ProjectionFile], *, generated_at: datetime
) -> ObsidianManifest:
    entries = tuple(
        ManifestEntry(
            path=item.path,
            kind=item.kind,
            paper_id=_marker_paper_id(item),
            artifact_id=item.artifact_id,
            ownership=item.ownership,
            source_hash=item.source_hash,
            exported_hash=hashlib.sha256(item.data).hexdigest(),
        )
        for item in files
    )
    return ObsidianManifest(generated_at=generated_at, entries=entries)


def merge_manifest(
    prior: ObsidianManifest,
    published: Iterable[ManifestEntry],
    *,
    generated_at: datetime,
) -> ObsidianManifest:
    merged = {entry.path: entry for entry in prior.entries}
    seen_published: set[str] = set()
    for entry in published:
        if entry.path in seen_published:
            raise ValueError("manifest publish set contains a duplicate path")
        seen_published.add(entry.path)
        folded = entry.path.casefold()
        collision = next(
            (path for path in merged if path.casefold() == folded and path != entry.path),
            None,
        )
        if collision is not None:
            raise ValueError("manifest path collides with a prior entry")
        previous = merged.get(entry.path)
        if previous is not None:
            if previous.ownership == "user":
                continue
            if (previous.paper_id, previous.kind) != (entry.paper_id, entry.kind):
                raise ValueError("manifest path ownership identity changed")
        merged[entry.path] = entry
    return ObsidianManifest(generated_at=generated_at, entries=tuple(merged.values()))


@dataclass(frozen=True, slots=True)
class VaultWriteResult:
    status: str
    entry: ManifestEntry
    target: BoundTargetIdentity
    error_code: str | None = None


@dataclass(frozen=True, slots=True)
class CleanupProof:
    entry: ManifestEntry
    ledger: VaultProjection
    target: BoundTargetIdentity


@dataclass(frozen=True, slots=True)
class CleanupInventory:
    deletable: tuple[CleanupProof, ...]
    orphaned: tuple[ManifestEntry, ...]
    conflicts: tuple[ManifestEntry, ...]
    user_managed: tuple[ManifestEntry, ...]


@dataclass(frozen=True, slots=True)
class VaultAccessResult:
    ok: bool
    code: str | None


class VaultWriter:
    """Own all mutation decisions for one already-bound Vault root."""

    def __init__(self, root: BoundVaultRoot, *, root_folder: str) -> None:
        self._root = root
        self._root_folder = root_folder
        VaultRelativePath(f"{root_folder}/.paper-study/root-check")

    def publish(
        self,
        item: ProjectionFile,
        *,
        prior: ManifestEntry | None = None,
        ledger: VaultProjection | None = None,
    ) -> VaultWriteResult:
        relative = self._relative(item.path)
        snapshot = self._root.inspect_target(relative, create_parent=True)
        desired_hash = hashlib.sha256(item.data).hexdigest()

        if snapshot is None:
            try:
                published = self._root.publish_new(relative, item.data)
            except ObsidianVaultError as error:
                if error.code != "OBSIDIAN_TARGET_EXISTS":
                    raise
                raced = self._root.inspect_target(relative)
                if raced is None:
                    raise
                entry = self._entry(item, raced.identity.sha256)
                status = "user_managed" if item.ownership == "user" else "conflict"
                return VaultWriteResult(status, entry, raced.identity, error.code)
            return VaultWriteResult(
                "exported",
                self._entry(item, published.sha256),
                published.identity,
            )

        if item.ownership == "user":
            return VaultWriteResult(
                "user_managed",
                prior or self._entry(item, snapshot.identity.sha256),
                snapshot.identity,
            )

        if not self._managed_proof_matches(
            item_path=item.path,
            data=snapshot.data,
            target=snapshot.identity,
            prior=prior,
            ledger=ledger,
        ):
            return VaultWriteResult(
                "conflict",
                prior or self._entry(item, snapshot.identity.sha256),
                snapshot.identity,
                "OBSIDIAN_MANAGED_PROOF_INVALID",
            )

        assert prior is not None
        assert ledger is not None
        if (
            item.source_hash == ledger.source_hash
            and desired_hash == snapshot.identity.sha256
        ):
            return VaultWriteResult("unchanged", prior, snapshot.identity)

        published = self._root.replace_managed(relative, item.data, snapshot.identity)
        return VaultWriteResult(
            "exported",
            self._entry(item, published.sha256),
            published.identity,
        )

    def delete_with_proof(self, proof: CleanupProof) -> None:
        if not isinstance(proof, CleanupProof):
            raise TypeError("cleanup requires a typed three-party proof")
        relative = self._relative(proof.entry.path)
        snapshot = self._root.inspect_target(relative)
        if (
            snapshot is None
            or snapshot.identity != proof.target
            or proof.entry.ownership != "managed"
            or not self._managed_proof_matches(
                item_path=proof.entry.path,
                data=snapshot.data,
                target=snapshot.identity,
                prior=proof.entry,
                ledger=proof.ledger,
            )
        ):
            raise ObsidianVaultError(
                "OBSIDIAN_CLEANUP_PROOF_INVALID",
                "The stale managed target is not authorized for deletion.",
            )
        self._root.delete_managed(relative, proof.target)

    def _relative(self, path: str) -> VaultRelativePath:
        return VaultRelativePath(f"{self._root_folder}/{path}")

    @staticmethod
    def _entry(item: ProjectionFile, exported_hash: str) -> ManifestEntry:
        marker = (
            parse_note_seed_marker(item.data)
            if item.ownership == "user"
            else parse_managed_marker(item.data)
        )
        if marker is None:
            raise ObsidianVaultError(
                "OBSIDIAN_MARKER_INVALID",
                "Projection bytes do not contain the required ownership marker.",
            )
        return ManifestEntry(
            path=item.path,
            kind=item.kind,
            paper_id=marker.paper_id,
            artifact_id=item.artifact_id,
            ownership=item.ownership,
            source_hash=item.source_hash,
            exported_hash=exported_hash,
        )

    @staticmethod
    def _managed_proof_matches(
        *,
        item_path: str,
        data: bytes,
        target: BoundTargetIdentity,
        prior: ManifestEntry | None,
        ledger: VaultProjection | None,
    ) -> bool:
        if prior is None or ledger is None or prior.ownership != "managed":
            return False
        if target.sha256 != prior.exported_hash or target.sha256 != ledger.exported_hash:
            return False
        if (
            ledger.target_path != item_path
            or ledger.paper_id != prior.paper_id
            or ledger.artifact_id != prior.artifact_id
            or ledger.source_hash != prior.source_hash
            or ledger.status not in {"exported", "unchanged"}
        ):
            return False
        if prior.kind == "pdf-copy":
            return True
        marker = parse_managed_marker(data)
        return marker is not None and (
            marker.paper_id,
            marker.kind,
            marker.artifact_id,
            marker.source_hash,
        ) == (
            prior.paper_id,
            prior.kind,
            prior.artifact_id,
            prior.source_hash,
        )


def serialize_manifest(manifest: ObsidianManifest) -> bytes:
    payload = {
        "schemaVersion": manifest.schema_version,
        "exporterVersion": manifest.exporter_version,
        "generatedAt": manifest.generated_at.isoformat().replace("+00:00", "Z"),
        "entries": [entry.to_dict() for entry in manifest.entries],
    }
    return (
        json.dumps(payload, ensure_ascii=False, indent=2, separators=(",", ": "))
        + "\n"
    ).encode("utf-8")


def parse_manifest(data: bytes) -> ObsidianManifest:
    def unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("manifest contains duplicate keys")
            result[key] = value
        return result

    try:
        payload = json.loads(data.decode("utf-8"), object_pairs_hook=unique_object)
        if not isinstance(payload, dict) or set(payload) != {
            "schemaVersion",
            "exporterVersion",
            "generatedAt",
            "entries",
        }:
            raise ValueError("manifest object shape is invalid")
        if payload["schemaVersion"] != 1 or payload["exporterVersion"] != "obsidian-projection-v1":
            raise ValueError("manifest version is unsupported")
        raw_entries = payload["entries"]
        if not isinstance(raw_entries, list):
            raise ValueError("manifest entries must be an array")
        entries: list[ManifestEntry] = []
        expected_keys = {
            "path",
            "kind",
            "paperId",
            "artifactId",
            "ownership",
            "sourceHash",
            "exportedHash",
        }
        for raw in raw_entries:
            if not isinstance(raw, dict) or set(raw) != expected_keys:
                raise ValueError("manifest entry shape is invalid")
            entries.append(
                ManifestEntry(
                    path=raw["path"],
                    kind=raw["kind"],
                    paper_id=raw["paperId"],
                    artifact_id=raw["artifactId"],
                    ownership=raw["ownership"],
                    source_hash=raw["sourceHash"],
                    exported_hash=raw["exportedHash"],
                )
            )
        generated_at = datetime.fromisoformat(
            str(payload["generatedAt"]).replace("Z", "+00:00")
        )
        manifest = ObsidianManifest(generated_at=generated_at, entries=tuple(entries))
    except (AttributeError, TypeError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
        raise ObsidianVaultError(
            "OBSIDIAN_MANIFEST_INVALID", "The managed Vault manifest is invalid."
        ) from error
    if serialize_manifest(manifest) != data:
        raise ObsidianVaultError(
            "OBSIDIAN_MANIFEST_INVALID", "The managed Vault manifest is not canonical."
        )
    return manifest


class ObsidianProjectionPublisher:
    """Recoverably publish one immutable projection file and its ledger evidence."""

    def __init__(
        self,
        root: BoundVaultRoot,
        repository: Any,
        *,
        root_folder: str,
        now: Callable[[], datetime] | None = None,
        before_ledger: Callable[[], None] | None = None,
        before_manifest: Callable[[], None] | None = None,
    ) -> None:
        self._root = root
        self._repository = repository
        self._writer = VaultWriter(root, root_folder=root_folder)
        self._root_folder = root_folder
        self._now = now or (lambda: datetime.now(timezone.utc))
        self._before_ledger = before_ledger
        self._before_manifest = before_manifest

    async def publish(self, item: ProjectionFile) -> VaultWriteResult:
        manifest_snapshot, manifest = self._read_manifest()
        prior = next((entry for entry in manifest.entries if entry.path == item.path), None)
        ledger = await self._repository.find_by_target_path(item.path)
        result = self._reconcile_file(item, prior=prior, ledger=ledger)

        if result.status == "conflict":
            await self._repository.upsert(self._projection(item, result, status="conflict"))
            return result

        if self._before_ledger is not None:
            self._before_ledger()
        status = "unchanged" if result.status in {"unchanged", "user_managed"} else "exported"
        if not _ledger_matches_entry(ledger, result.entry):
            await self._repository.upsert(self._projection(item, result, status=status))

        if prior == result.entry:
            return result
        updated = merge_manifest(manifest, (result.entry,), generated_at=self._utc_now())
        self._publish_manifest(manifest_snapshot, serialize_manifest(updated))
        return result

    def _reconcile_file(
        self,
        item: ProjectionFile,
        *,
        prior: ManifestEntry | None,
        ledger: VaultProjection | None,
    ) -> VaultWriteResult:
        relative = self._writer._relative(item.path)
        snapshot = self._root.inspect_target(relative, create_parent=True)
        desired_hash = hashlib.sha256(item.data).hexdigest()
        desired_entry = self._writer._entry(item, desired_hash)
        if snapshot is not None and snapshot.identity.sha256 == desired_hash and snapshot.data == item.data:
            if prior is None and ledger is None:
                return VaultWriteResult("exported", desired_entry, snapshot.identity)
            if prior is None and _ledger_matches_entry(ledger, desired_entry):
                prior = desired_entry
            if prior == desired_entry and ledger is None:
                return VaultWriteResult(
                    "conflict",
                    prior,
                    snapshot.identity,
                    "OBSIDIAN_LIVE_LEDGER_MISSING",
                )
        return self._writer.publish(item, prior=prior, ledger=ledger)

    def _read_manifest(self, *, create_parent: bool = True):
        relative = VaultRelativePath(f"{self._root_folder}/.paper-study/manifest.json")
        try:
            snapshot = self._root.inspect_target(relative, create_parent=create_parent)
        except ObsidianVaultError as error:
            if create_parent or error.code != "OBSIDIAN_PARENT_CHANGED":
                raise
            snapshot = None
        if snapshot is None:
            return None, ObsidianManifest(generated_at=self._utc_now(), entries=())
        return snapshot, parse_manifest(snapshot.data)

    def _publish_manifest(self, prior_snapshot: Any, data: bytes) -> None:
        if self._before_manifest is not None:
            self._before_manifest()
        relative = VaultRelativePath(f"{self._root_folder}/.paper-study/manifest.json")
        if prior_snapshot is None:
            self._root.publish_new(relative, data)
        else:
            self._root.replace_managed(relative, data, prior_snapshot.identity)

    def _projection(
        self,
        item: ProjectionFile,
        result: VaultWriteResult,
        *,
        status: str,
    ) -> VaultProjection:
        identifier = "obsidian-" + hashlib.sha256(item.path.encode("utf-8")).hexdigest()[:32]
        successful = status in {"exported", "unchanged"}
        return VaultProjection(
            id=identifier,
            paper_id=result.entry.paper_id,
            artifact_id=item.artifact_id,
            target_path=item.path,
            source_hash=item.source_hash,
            exported_hash=result.entry.exported_hash if successful else None,
            status=status,
            exported_at=self._utc_now() if successful else None,
            error_message=result.error_code if not successful else None,
        )

    def _utc_now(self) -> datetime:
        value = self._now()
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("projection publisher clock must be timezone-aware")
        return value.astimezone(timezone.utc)


def _ledger_matches_entry(
    ledger: VaultProjection | None, entry: ManifestEntry
) -> bool:
    return ledger is not None and ledger.status in {"exported", "unchanged"} and (
        ledger.paper_id,
        ledger.artifact_id,
        ledger.target_path,
        ledger.source_hash,
        ledger.exported_hash,
    ) == (
        entry.paper_id,
        entry.artifact_id,
        entry.path,
        entry.source_hash,
        entry.exported_hash,
    )


class ObsidianCleanupPlanner:
    def __init__(
        self,
        root: BoundVaultRoot,
        repository: Any,
        *,
        root_folder: str,
    ) -> None:
        self._root = root
        self._repository = repository
        self._writer = VaultWriter(root, root_folder=root_folder)

    async def classify(self, entries: Iterable[ManifestEntry]) -> CleanupInventory:
        deletable: list[CleanupProof] = []
        orphaned: list[ManifestEntry] = []
        conflicts: list[ManifestEntry] = []
        user_managed: list[ManifestEntry] = []
        for entry in sorted(entries, key=lambda item: item.path):
            if entry.ownership == "user":
                user_managed.append(entry)
                continue
            ledger = await self._repository.find_cleanup_projection(
                paper_id=entry.paper_id,
                target_path=entry.path,
                source_hash=entry.source_hash,
                exported_hash=entry.exported_hash,
            )
            if ledger is None:
                orphaned.append(entry)
                continue
            snapshot = self._root.inspect_target(self._writer._relative(entry.path))
            if snapshot is None or not self._writer._managed_proof_matches(
                item_path=entry.path,
                data=snapshot.data,
                target=snapshot.identity,
                prior=entry,
                ledger=ledger,
            ):
                conflicts.append(entry)
                continue
            deletable.append(CleanupProof(entry, ledger, snapshot.identity))
        return CleanupInventory(
            tuple(deletable),
            tuple(orphaned),
            tuple(conflicts),
            tuple(user_managed),
        )


def probe_obsidian_vault(vault_path: str) -> VaultAccessResult:
    path = Path(vault_path).expanduser()
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return VaultAccessResult(False, "OBSIDIAN_VAULT_NOT_FOUND")
    except OSError:
        return VaultAccessResult(False, "OBSIDIAN_VAULT_NOT_WRITABLE")
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    if stat.S_ISLNK(metadata.st_mode) or int(
        getattr(metadata, "st_file_attributes", 0)
    ) & reparse_flag:
        return VaultAccessResult(False, "OBSIDIAN_PATH_ESCAPE")
    if not stat.S_ISDIR(metadata.st_mode):
        return VaultAccessResult(False, "OBSIDIAN_VAULT_NOT_DIRECTORY")

    relative = VaultRelativePath(f".paper-study-probe-{uuid4().hex}")
    try:
        with BoundVaultRoot.open(path) as root:
            published = root.publish_new(relative, b"paper-study-obsidian-probe\n")
            root.delete_managed(relative, published.identity)
    except PermissionError:
        return VaultAccessResult(False, "OBSIDIAN_VAULT_NOT_WRITABLE")
    except ObsidianVaultError as error:
        cause: BaseException | None = error
        while cause is not None:
            if isinstance(cause, PermissionError) or getattr(cause, "winerror", None) == 5:
                return VaultAccessResult(False, "OBSIDIAN_VAULT_NOT_WRITABLE")
            cause = cause.__cause__
        if error.code in {
            "OBSIDIAN_PATH_ESCAPE",
            "OBSIDIAN_ATOMIC_PRIMITIVE_UNAVAILABLE",
        }:
            return VaultAccessResult(False, error.code)
        return VaultAccessResult(False, "OBSIDIAN_ATOMIC_PRIMITIVE_UNAVAILABLE")
    except OSError as error:
        if isinstance(error, PermissionError) or getattr(error, "winerror", None) == 5:
            return VaultAccessResult(False, "OBSIDIAN_VAULT_NOT_WRITABLE")
        return VaultAccessResult(False, "OBSIDIAN_ATOMIC_PRIMITIVE_UNAVAILABLE")
    return VaultAccessResult(True, None)


def parse_managed_marker(data: bytes) -> ManagedMarker | None:
    fields = _frontmatter_fields(data)
    if fields is None or fields.get("paper-study-managed") is not True:
        return None
    paper_id = fields.get("paper-id")
    kind = fields.get("kind")
    source_hash = fields.get("source-hash")
    artifact_id = fields.get("artifact-id")
    if (
        not isinstance(paper_id, str)
        or not isinstance(kind, str)
        or not isinstance(source_hash, str)
        or _SHA256.fullmatch(source_hash) is None
        or (artifact_id is not None and not isinstance(artifact_id, str))
    ):
        return None
    return ManagedMarker(paper_id, kind, source_hash, artifact_id)


def parse_note_seed_marker(data: bytes) -> NoteSeedMarker | None:
    fields = _frontmatter_fields(data)
    if fields is None or fields.get("paper-study-note-seed") is not True:
        return None
    if fields.get("paper-study-managed") is True:
        return None
    paper_id = fields.get("paper-id")
    kind = fields.get("kind")
    source_hash = fields.get("source-hash")
    if (
        not isinstance(paper_id, str)
        or kind != "note"
        or not isinstance(source_hash, str)
        or _SHA256.fullmatch(source_hash) is None
    ):
        return None
    return NoteSeedMarker(paper_id, source_hash)


def _marker_paper_id(item: ProjectionFile) -> str:
    marker = (
        parse_note_seed_marker(item.data)
        if item.ownership == "user"
        else parse_managed_marker(item.data)
    )
    if marker is None:
        raise ValueError("projection bytes do not contain their required marker")
    return marker.paper_id


def _frontmatter_fields(data: bytes) -> Mapping[str, object] | None:
    if not isinstance(data, bytes) or not data.startswith(b"---\n"):
        return None
    boundary = data.find(b"\n---\n", 4, _MAX_FRONTMATTER_BYTES)
    if boundary < 0:
        return None
    try:
        lines = data[4:boundary].decode("utf-8").splitlines()
    except UnicodeDecodeError:
        return None
    fields: dict[str, object] = {}
    list_key: str | None = None
    for line in lines:
        if line.startswith("  - "):
            if list_key is None:
                return None
            try:
                item = json.loads(line[4:])
            except (TypeError, json.JSONDecodeError):
                return None
            if not isinstance(item, str):
                return None
            assert isinstance(fields[list_key], list)
            fields[list_key].append(item)
            continue
        if ":" not in line or line.startswith((" ", "\t")):
            return None
        key, raw_value = line.split(":", 1)
        if not key or key in fields:
            return None
        if raw_value == "":
            fields[key] = []
            list_key = key
            continue
        if not raw_value.startswith(" "):
            return None
        try:
            fields[key] = json.loads(raw_value[1:])
        except (TypeError, json.JSONDecodeError):
            return None
        list_key = None
    return fields


__all__ = [
    "CleanupProof",
    "CleanupInventory",
    "ManagedMarker",
    "ManifestEntry",
    "NoteSeedMarker",
    "ObsidianManifest",
    "ObsidianCleanupPlanner",
    "ObsidianProjectionPublisher",
    "VaultWriteResult",
    "VaultAccessResult",
    "VaultWriter",
    "build_manifest",
    "merge_manifest",
    "parse_managed_marker",
    "parse_manifest",
    "parse_note_seed_marker",
    "probe_obsidian_vault",
    "serialize_manifest",
]
