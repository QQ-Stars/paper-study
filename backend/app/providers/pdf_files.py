from __future__ import annotations

"""Safe PDF location and streaming metadata provider for compatibility routes."""

from dataclasses import dataclass
import mimetypes
import os
from pathlib import Path
import stat
from typing import BinaryIO, Iterable


@dataclass(frozen=True, slots=True)
class PdfFile:
    path: Path
    size: int


@dataclass(frozen=True, slots=True)
class OpenedPdf:
    stream: BinaryIO
    size: int
    path: Path | None = None


class PdfFiles:
    def __init__(
        self,
        *,
        root: Path | str,
        default_directory: Path | str | None = None,
        custom_directories: Iterable[Path | str] = (),
        seed_directory: Path | str | None = None,
    ) -> None:
        self.root = Path(root).expanduser().resolve()
        directories = [
            Path(default_directory or self.root / "data" / "pdfs"),
            *(Path(value) for value in custom_directories),
        ]
        if seed_directory is not None:
            directories.append(Path(seed_directory))
        self._roots = tuple(_canonical_directory(value) for value in directories)

    @property
    def roots(self) -> tuple[Path, ...]:
        return self._roots

    def resolve_for_id(self, paper_id: str, *, stored_path: str | Path | None = None) -> PdfFile | None:
        for candidate in self._candidate_paths(paper_id, stored_path=stored_path):
            resolved = _contained_realpath(candidate, self._roots)
            if resolved is None:
                continue
            try:
                if resolved.is_file():
                    return PdfFile(resolved, resolved.stat().st_size)
            except OSError:
                continue
        return None

    def open_for_id(
        self,
        paper_id: str,
        *,
        stored_path: str | Path | None = None,
    ) -> OpenedPdf | None:
        for candidate in self._candidate_paths(paper_id, stored_path=stored_path):
            resolved = _contained_realpath(candidate, self._roots)
            if resolved is None:
                continue
            descriptor: int | None = None
            try:
                flags = os.O_RDONLY | getattr(os, "O_BINARY", 0)
                flags |= getattr(os, "O_NOINHERIT", 0)
                flags |= getattr(os, "O_NOFOLLOW", 0)
                descriptor = os.open(resolved, flags)
                opened_stat = os.fstat(descriptor)
                if not stat.S_ISREG(opened_stat.st_mode):
                    continue

                current_realpath = _contained_realpath(resolved, self._roots)
                if current_realpath != resolved:
                    continue
                current_stat = os.stat(resolved, follow_symlinks=False)
                if not stat.S_ISREG(current_stat.st_mode) or not os.path.samestat(
                    opened_stat, current_stat
                ):
                    continue

                stream = os.fdopen(descriptor, "rb", closefd=True)
                descriptor = None
                return OpenedPdf(
                    stream=stream,
                    size=opened_stat.st_size,
                    path=resolved,
                )
            except (FileNotFoundError, IsADirectoryError, OSError):
                continue
            finally:
                if descriptor is not None:
                    os.close(descriptor)
        return None

    def _candidate_paths(
        self,
        paper_id: str,
        *,
        stored_path: str | Path | None,
    ) -> tuple[Path, ...]:
        identifier = _safe_identifier(paper_id)
        if not identifier:
            return ()
        candidates: list[Path] = []
        if stored_path:
            stored = Path(stored_path)
            candidates.append(stored if stored.is_absolute() else self.root / stored)
        candidates.extend(directory / f"{identifier}.pdf" for directory in self._roots)
        return tuple(candidates)

    def has_pdf(self, paper: object) -> bool:
        paper_id = getattr(paper, "id", None)
        stored_path = getattr(paper, "pdf_path", None)
        if isinstance(paper, dict):
            paper_id = paper.get("id")
            stored_path = paper.get("pdf_path")
        return self.resolve_for_id(str(paper_id or ""), stored_path=stored_path) is not None

    async def delete_for_paper(self, paper_id: str) -> None:
        identifier = _safe_identifier(paper_id)
        if not identifier:
            return
        for directory in self._roots:
            candidate = _contained_realpath(directory / f"{identifier}.pdf", self._roots)
            if candidate is None:
                continue
            try:
                if candidate.is_file():
                    candidate.unlink()
            except FileNotFoundError:
                pass

    def scan(self, directory: Path | str, *, max_depth: int = 4, limit: int = 2000) -> dict[str, object]:
        requested = Path(directory).expanduser()
        try:
            requested_root = requested.resolve()
        except (OSError, RuntimeError):
            return {"ok": False, "error": "文件夹不存在或不是目录"}
        root = _contained_realpath(requested, (requested_root,))
        if root is None or not root.is_dir():
            return {"ok": False, "error": "文件夹不存在或不是目录"}
        files: list[dict[str, object]] = []

        def walk(current: Path, depth: int) -> None:
            if depth > max_depth or len(files) >= limit:
                return
            try:
                entries = sorted(current.iterdir(), key=lambda path: str(path))
            except OSError:
                return
            for entry in entries:
                if len(files) >= limit:
                    break
                if entry.is_dir() and not entry.is_symlink():
                    if entry.name.startswith("."):
                        continue
                    walk(entry, depth + 1)
                elif (
                    not entry.is_symlink()
                    and entry.is_file()
                    and entry.suffix.lower() == ".pdf"
                ):
                    try:
                        files.append({"path": str(entry), "name": entry.name, "size": entry.stat().st_size})
                    except OSError:
                        continue

        walk(root, 0)
        files.sort(key=lambda item: str(item["path"]))
        return {
            "ok": True,
            "dir": str(requested),
            "count": len(files),
            "files": files,
        }


def _safe_identifier(value: object) -> str:
    rendered = str(value or "")
    if not rendered or rendered in {".", ".."} or "/" in rendered or "\\" in rendered:
        return ""
    return rendered[:-4] if rendered.lower().endswith(".pdf") else rendered


def _canonical_directory(value: Path) -> Path:
    try:
        value.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    return value.expanduser().resolve()


def _contained_realpath(candidate: Path, roots: Iterable[Path]) -> Path | None:
    try:
        absolute = candidate.expanduser().resolve(strict=False)
    except (OSError, RuntimeError):
        return None
    for root in roots:
        try:
            absolute.relative_to(Path(root).resolve())
            return absolute
        except (ValueError, OSError, RuntimeError):
            continue
    return None


__all__ = ["OpenedPdf", "PdfFile", "PdfFiles"]
