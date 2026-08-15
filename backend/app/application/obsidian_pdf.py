from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import Path
from typing import Iterable, Literal

from backend.app.application.obsidian_projection import (
    ObsidianProjectionError,
    ProjectionFile,
    project_paths,
    validate_paper_file_id,
)
from backend.app.infrastructure.bound_vault_root import (
    BoundVaultRoot,
    ObsidianVaultError,
)
from backend.app.providers.obsidian_vault import (
    ManifestEntry,
    ObsidianProjectionPublisher,
    VaultWriteResult,
    VaultWriter,
)
from backend.app.providers.pdf_files import PdfFiles


PdfMode = Literal["none", "reference", "copy"]
_PDF_MODES = frozenset({"none", "reference", "copy"})
_COPY_SUCCEEDED = frozenset({"exported", "unchanged"})
_EXPECTED_SHA_UNSET = object()


@dataclass(frozen=True, slots=True)
class PdfProjectionResult:
    status: str
    link: str | None
    projection_file: ProjectionFile | None
    error_code: str | None = None
    issues: tuple[str, ...] = ()


class _PdfVaultWriter(VaultWriter):
    @staticmethod
    def _entry(item: ProjectionFile, exported_hash: str) -> ManifestEntry:
        if item.kind != "pdf-copy":
            return VaultWriter._entry(item, exported_hash)
        paper_id = _paper_id_from_copy_path(item.path)
        if item.ownership != "managed" or item.artifact_id is not None:
            raise ObsidianVaultError(
                "OBSIDIAN_PDF_PROJECTION_INVALID",
                "A PDF copy must be a managed paper projection.",
            )
        return ManifestEntry(
            path=item.path,
            kind=item.kind,
            paper_id=paper_id,
            artifact_id=None,
            ownership="managed",
            source_hash=item.source_hash,
            exported_hash=exported_hash,
        )

    def publish_stream(
        self,
        item: ProjectionFile,
        stream: object,
        *,
        expected_size: int,
        expected_sha256: str,
        prior: ManifestEntry | None,
        ledger: object | None,
    ) -> VaultWriteResult:
        relative = self._relative(item.path)
        target = self._root.inspect_target_identity(
            relative,
            create_parent=True,
        )
        desired_entry = self._entry(item, expected_sha256)
        if target is None:
            try:
                published = self._root.publish_new_stream(
                    relative,
                    stream,
                    expected_size=expected_size,
                    expected_sha256=expected_sha256,
                )
            except ObsidianVaultError as error:
                if error.code != "OBSIDIAN_TARGET_EXISTS":
                    raise
                raced = self._root.inspect_target_identity(relative)
                if raced is None:
                    raise
                return VaultWriteResult(
                    "conflict",
                    self._entry(item, raced.sha256),
                    raced,
                    error.code,
                )
            return VaultWriteResult(
                "exported",
                desired_entry,
                published.identity,
            )

        effective_prior = prior
        if target.sha256 == expected_sha256:
            if prior is None and ledger is None:
                return VaultWriteResult(
                    "exported",
                    desired_entry,
                    target,
                )
            if prior is None and _pdf_ledger_matches_entry(ledger, desired_entry):
                effective_prior = desired_entry
            if prior == desired_entry and ledger is None:
                return VaultWriteResult(
                    "conflict",
                    desired_entry,
                    target,
                    "OBSIDIAN_LIVE_LEDGER_MISSING",
                )

        if not self._managed_proof_matches(
            item_path=item.path,
            data=b"",
            target=target,
            prior=effective_prior,
            ledger=ledger,
        ):
            return VaultWriteResult(
                "conflict",
                prior or self._entry(item, target.sha256),
                target,
                "OBSIDIAN_MANAGED_PROOF_INVALID",
            )

        if (
            getattr(ledger, "source_hash", None) == item.source_hash
            and target.sha256 == expected_sha256
        ):
            assert effective_prior is not None
            return VaultWriteResult("unchanged", effective_prior, target)

        published = self._root.replace_managed_stream(
            relative,
            stream,
            target,
            expected_size=expected_size,
            expected_sha256=expected_sha256,
        )
        return VaultWriteResult(
            "exported",
            desired_entry,
            published.identity,
        )


class _PdfProjectionPublisher(ObsidianProjectionPublisher):
    def __init__(
        self,
        root: BoundVaultRoot,
        repository: object,
        *,
        root_folder: str,
        now: object | None = None,
    ) -> None:
        kwargs = {"root_folder": root_folder}
        if now is not None:
            kwargs["now"] = now
        super().__init__(root, repository, **kwargs)
        self._writer = _PdfVaultWriter(root, root_folder=root_folder)

    async def publish_stream(
        self,
        item: ProjectionFile,
        stream: object,
        *,
        expected_size: int,
        expected_sha256: str,
    ) -> VaultWriteResult:
        manifest_snapshot, manifest = self._read_manifest()
        prior = next(
            (entry for entry in manifest.entries if entry.path == item.path),
            None,
        )
        ledger = await self._repository.find_by_target_path(item.path)
        result = self._writer.publish_stream(
            item,
            stream,
            expected_size=expected_size,
            expected_sha256=expected_sha256,
            prior=prior,
            ledger=ledger,
        )
        if result.status == "conflict":
            await self._repository.upsert(
                self._projection(item, result, status="conflict")
            )
            return result

        status = (
            "unchanged"
            if result.status in {"unchanged", "user_managed"}
            else "exported"
        )
        if not _pdf_ledger_matches_entry(ledger, result.entry):
            await self._repository.upsert(
                self._projection(item, result, status=status)
            )
        if prior != result.entry:
            from backend.app.providers.obsidian_vault import merge_manifest, serialize_manifest

            updated = merge_manifest(
                manifest,
                (result.entry,),
                generated_at=self._utc_now(),
            )
            self._publish_manifest(
                manifest_snapshot,
                serialize_manifest(updated),
            )
        return result


class ObsidianPdfProjector:
    """Apply one explicit PDF policy without touching source-generation seams."""

    def __init__(
        self,
        *,
        pdf_files: PdfFiles,
        root: BoundVaultRoot,
        repository: object,
        root_folder: str,
        now: object | None = None,
    ) -> None:
        self._pdf_files = pdf_files
        self._publisher = _PdfProjectionPublisher(
            root,
            repository,
            root_folder=root_folder,
            now=now,
        )

    async def project(
        self,
        *,
        paper_id: str,
        mode: PdfMode | str,
        stored_path: str | Path | None = None,
        existing_paths: Iterable[str] = (),
        source_available: bool = True,
        expected_sha256: str | None | object = _EXPECTED_SHA_UNSET,
    ) -> PdfProjectionResult:
        if mode not in _PDF_MODES:
            raise ObsidianProjectionError(
                "OBSIDIAN_PDF_MODE_INVALID",
                "Obsidian PDF mode must be none, reference, or copy.",
            )
        target_path = _validated_target_path(paper_id, existing_paths)
        issues = () if source_available else ("source_unavailable",)

        if mode == "none":
            return PdfProjectionResult("none", None, None, issues=issues)

        if mode == "reference":
            resolved = self._pdf_files.resolve_for_id(
                paper_id,
                stored_path=stored_path,
            )
            if resolved is None:
                return PdfProjectionResult(
                    "pdf_missing",
                    None,
                    None,
                    error_code="OBSIDIAN_PDF_MISSING",
                    issues=issues,
                )
            return PdfProjectionResult(
                "reference",
                resolved.path.as_uri(),
                None,
                issues=issues,
            )

        if expected_sha256 is None:
            return PdfProjectionResult(
                "pdf_missing",
                None,
                None,
                error_code="OBSIDIAN_PDF_MISSING",
                issues=issues,
            )
        if expected_sha256 is not _EXPECTED_SHA_UNSET and (
            not isinstance(expected_sha256, str)
            or len(expected_sha256) != 64
            or any(character not in "0123456789abcdef" for character in expected_sha256)
        ):
            raise ObsidianProjectionError(
                "OBSIDIAN_PDF_IDENTITY_INVALID",
                "The frozen PDF SHA-256 is invalid.",
            )

        opened = self._pdf_files.open_for_id(
            paper_id,
            stored_path=stored_path,
        )
        if opened is None:
            return PdfProjectionResult(
                "pdf_missing",
                None,
                None,
                error_code="OBSIDIAN_PDF_MISSING",
                issues=issues,
            )
        if expected_sha256 is not _EXPECTED_SHA_UNSET:
            item = ProjectionFile(
                path=target_path,
                kind="pdf-copy",
                data=b"",
                source_hash=expected_sha256,
            )
            with opened.stream:
                if not _verify_pdf_stream(
                    opened.stream,
                    expected_size=opened.size,
                    expected_sha256=expected_sha256,
                ):
                    return PdfProjectionResult(
                        "conflict",
                        None,
                        None,
                        error_code="OBSIDIAN_PDF_SOURCE_CHANGED",
                        issues=issues,
                    )
                try:
                    published = await self._publisher.publish_stream(
                        item,
                        opened.stream,
                        expected_size=opened.size,
                        expected_sha256=expected_sha256,
                    )
                except ObsidianVaultError as error:
                    if error.code != "OBSIDIAN_STREAM_SOURCE_CHANGED":
                        raise
                    return PdfProjectionResult(
                        "conflict",
                        None,
                        None,
                        error_code="OBSIDIAN_PDF_SOURCE_CHANGED",
                        issues=issues,
                    )
            return PdfProjectionResult(
                published.status,
                f"../{target_path}" if published.status in _COPY_SUCCEEDED else None,
                item if published.status in _COPY_SUCCEEDED else None,
                error_code=published.error_code,
                issues=issues,
            )

        with opened.stream:
            data = _read_pdf(opened.stream, expected_size=opened.size)
        source_hash = hashlib.sha256(data).hexdigest()
        item = ProjectionFile(
            path=target_path,
            kind="pdf-copy",
            data=data,
            source_hash=source_hash,
        )
        published = await self._publisher.publish(item)
        return PdfProjectionResult(
            published.status,
            f"../{target_path}" if published.status in _COPY_SUCCEEDED else None,
            item,
            error_code=published.error_code,
            issues=issues,
        )


def _validated_target_path(paper_id: str, existing_paths: Iterable[str]) -> str:
    validate_paper_file_id(paper_id)
    target = project_paths(paper_id).pdf
    folded_id = paper_id.casefold()
    for existing_path in existing_paths:
        existing_id = _paper_id_from_existing_path(existing_path)
        if (
            existing_id is not None
            and existing_id.casefold() == folded_id
            and existing_id != paper_id
        ):
            raise ObsidianProjectionError(
                "OBSIDIAN_PAPER_ID_CASE_COLLISION",
                "Paper id collides with an existing Vault target.",
            )
    return target


def _paper_id_from_existing_path(path: str) -> str | None:
    normalized = path.replace("\\", "/")
    directory, separator, name = normalized.rpartition("/")
    if not separator:
        return None
    suffixes = {
        "Papers": ".md",
        "Sources": ".md",
        "Explainers": ".md",
        "Translations": ".md",
        "Notes": ".md",
        "Attachments/PDF": ".pdf",
    }
    suffix = suffixes.get(directory)
    if suffix is None or not name.endswith(suffix):
        return None
    return name[: -len(suffix)]


def _paper_id_from_copy_path(path: str) -> str:
    prefix = "Attachments/PDF/"
    suffix = ".pdf"
    if not path.startswith(prefix) or not path.endswith(suffix):
        raise ObsidianVaultError(
            "OBSIDIAN_PDF_PROJECTION_INVALID",
            "A PDF copy must use the fixed attachment path.",
        )
    paper_id = path[len(prefix) : -len(suffix)]
    validate_paper_file_id(paper_id)
    if project_paths(paper_id).pdf != path:
        raise ObsidianVaultError(
            "OBSIDIAN_PDF_PROJECTION_INVALID",
            "A PDF copy must use the raw paper id.",
        )
    return paper_id


def _read_pdf(stream: object, *, expected_size: int) -> bytes:
    chunks: list[bytes] = []
    size = 0
    while True:
        chunk = stream.read(1024 * 1024)
        if not isinstance(chunk, bytes):
            raise ObsidianVaultError(
                "OBSIDIAN_PDF_SOURCE_CHANGED",
                "The PDF source descriptor returned invalid bytes.",
            )
        if not chunk:
            break
        chunks.append(chunk)
        size += len(chunk)
    if size != expected_size:
        raise ObsidianVaultError(
            "OBSIDIAN_PDF_SOURCE_CHANGED",
            "The PDF source changed while it was being copied.",
        )
    return b"".join(chunks)


def _verify_pdf_stream(
    stream: object,
    *,
    expected_size: int,
    expected_sha256: str,
) -> bool:
    digest = hashlib.sha256()
    size = 0
    while True:
        chunk = stream.read(1024 * 1024)
        if not isinstance(chunk, bytes):
            return False
        if not chunk:
            break
        size += len(chunk)
        if size > expected_size:
            return False
        digest.update(chunk)
    try:
        stream.seek(0)
    except (AttributeError, OSError):
        return False
    return size == expected_size and digest.hexdigest() == expected_sha256


def _pdf_ledger_matches_entry(ledger: object | None, entry: ManifestEntry) -> bool:
    return ledger is not None and getattr(ledger, "status", None) in {
        "exported",
        "unchanged",
    } and (
        getattr(ledger, "paper_id", None),
        getattr(ledger, "artifact_id", None),
        getattr(ledger, "target_path", None),
        getattr(ledger, "source_hash", None),
        getattr(ledger, "exported_hash", None),
    ) == (
        entry.paper_id,
        entry.artifact_id,
        entry.path,
        entry.source_hash,
        entry.exported_hash,
    )


__all__ = [
    "ObsidianPdfProjector",
    "PdfMode",
    "PdfProjectionResult",
]
