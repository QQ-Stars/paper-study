from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path, PurePosixPath
import stat
from typing import Any, Callable
from uuid import uuid4


class ObsidianVaultError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class VaultRelativePath:
    value: str

    def __post_init__(self) -> None:
        if not isinstance(self.value, str) or not self.value:
            raise ObsidianVaultError(
                "OBSIDIAN_PATH_ESCAPE",
                "A non-empty relative Vault path is required.",
            )
        if (
            "\\" in self.value
            or self.value.startswith("/")
            or "\x00" in self.value
            or any(ord(character) < 32 for character in self.value)
        ):
            raise ObsidianVaultError(
                "OBSIDIAN_PATH_ESCAPE",
                "The Vault path is not a safe relative POSIX path.",
            )
        path = PurePosixPath(self.value)
        if path.is_absolute() or any(segment in {"", ".", ".."} for segment in path.parts):
            raise ObsidianVaultError(
                "OBSIDIAN_PATH_ESCAPE",
                "The Vault path may not escape its bound root.",
            )

    @property
    def segments(self) -> tuple[str, ...]:
        return PurePosixPath(self.value).parts


@dataclass(frozen=True, slots=True)
class BoundDirectoryIdentity:
    relative_path: str
    device: int
    inode: int


@dataclass(frozen=True, slots=True)
class BoundTargetIdentity:
    relative_path: str
    device: int
    inode: int
    size: int
    sha256: str

    @property
    def opaque_id(self) -> str:
        return f"{self.device:016x}:{self.inode:016x}"


@dataclass(frozen=True, slots=True)
class PublishedFile:
    relative_path: str
    size: int
    sha256: str
    identity: BoundTargetIdentity


@dataclass(frozen=True, slots=True)
class BoundTargetSnapshot:
    identity: BoundTargetIdentity
    data: bytes


@dataclass(slots=True)
class _BoundDirectory:
    path: Path
    relative_path: str
    handle: Any
    device: int
    inode: int


class BoundVaultRoot:
    """Bind a Vault directory chain before any mutation is attempted."""

    def __init__(
        self,
        path: Path,
        directories: list[_BoundDirectory],
        *,
        before_publish: Callable[[Path], None] | None = None,
    ) -> None:
        self.path = path
        self._directories = directories
        self._closed = False
        self._before_publish = before_publish

    @classmethod
    def open(
        cls,
        root: str | os.PathLike[str],
        *,
        before_publish: Callable[[Path], None] | None = None,
    ) -> "BoundVaultRoot":
        path = Path(root).expanduser().absolute()
        if not path.is_dir():
            raise ObsidianVaultError(
                "OBSIDIAN_VAULT_NOT_DIRECTORY",
                "The configured Vault root is not a directory.",
            )
        try:
            if os.name == "nt":
                bound = _open_windows_root(path)
            else:
                bound = _open_posix_root(path)
            bound._before_publish = before_publish
            return bound
        except ObsidianVaultError:
            raise
        except OSError as error:
            raise ObsidianVaultError(
                "OBSIDIAN_ATOMIC_PRIMITIVE_UNAVAILABLE",
                "The Vault root could not be bound safely.",
            ) from error

    def __enter__(self) -> "BoundVaultRoot":
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def ensure_directory(
        self,
        relative_path: VaultRelativePath,
    ) -> BoundDirectoryIdentity:
        self._require_open()
        known = {item.relative_path: item for item in self._directories}
        current = self._directories[0]
        parts: list[str] = []
        for segment in relative_path.segments:
            parts.append(segment)
            key = "/".join(parts)
            existing = known.get(key)
            if existing is not None:
                current = existing
                continue
            current = (
                _bind_windows_child(current, segment, key)
                if os.name == "nt"
                else _bind_posix_child(current, segment, key)
            )
            self._directories.append(current)
            known[key] = current
        return BoundDirectoryIdentity(
            relative_path=current.relative_path,
            device=current.device,
            inode=current.inode,
        )

    def verify(self) -> None:
        self._require_open()
        for index, directory in enumerate(self._directories):
            try:
                if os.name == "nt":
                    device, inode = _probe_windows_path(directory.path)
                else:
                    metadata = os.stat(directory.path, follow_symlinks=False)
                    if not stat.S_ISDIR(metadata.st_mode):
                        raise OSError("bound directory became a non-directory")
                    device, inode = int(metadata.st_dev), int(metadata.st_ino)
            except OSError as error:
                raise _changed_directory_error(index) from error
            if (device, inode) != (directory.device, directory.inode):
                raise _changed_directory_error(index)

    def publish_new(
        self,
        relative_path: VaultRelativePath,
        data: bytes,
    ) -> PublishedFile:
        if not isinstance(data, bytes):
            raise TypeError("Vault payload must be bytes")
        parent, final_name = self._bound_parent(relative_path)
        return (
            self._publish_new_windows(
                parent,
                relative_path,
                final_name,
                lambda handle: _write_handle(handle, data),
            )
            if os.name == "nt"
            else self._publish_new_posix(
                parent,
                relative_path,
                final_name,
                lambda descriptor: _write_descriptor(descriptor, data),
            )
        )

    def publish_new_stream(
        self,
        relative_path: VaultRelativePath,
        stream: Any,
        *,
        expected_size: int,
        expected_sha256: str,
    ) -> PublishedFile:
        _validate_stream_identity(expected_size, expected_sha256)
        parent, final_name = self._bound_parent(relative_path)
        return (
            self._publish_new_windows(
                parent,
                relative_path,
                final_name,
                lambda handle: _copy_stream_to_handle(
                    stream,
                    handle,
                    expected_size=expected_size,
                    expected_sha256=expected_sha256,
                ),
            )
            if os.name == "nt"
            else self._publish_new_posix(
                parent,
                relative_path,
                final_name,
                lambda descriptor: _copy_stream_to_descriptor(
                    stream,
                    descriptor,
                    expected_size=expected_size,
                    expected_sha256=expected_sha256,
                ),
            )
        )

    def bind_target(self, relative_path: VaultRelativePath) -> BoundTargetIdentity:
        parent, final_name = self._bound_parent(relative_path, create=False)
        self.verify()
        try:
            if os.name == "nt":
                from backend.app.infrastructure.database_backup import (
                    _open_windows_file_for_bound_rename,
                )

                handle, _identity = _open_windows_file_for_bound_rename(
                    parent.path / final_name
                )
                try:
                    result = _target_from_descriptor(
                        relative_path.value, handle.fileno()
                    )
                finally:
                    handle.close()
            else:
                flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
                descriptor = os.open(final_name, flags, dir_fd=int(parent.handle))
                try:
                    result = _target_from_descriptor(relative_path.value, descriptor)
                finally:
                    os.close(descriptor)
        except FileNotFoundError as error:
            raise ObsidianVaultError(
                "OBSIDIAN_TARGET_CHANGED", "The managed Vault target is missing."
            ) from error
        except OSError as error:
            raise ObsidianVaultError(
                "OBSIDIAN_TARGET_CHANGED", "The managed Vault target could not be bound."
            ) from error
        self.verify()
        return result

    def inspect_target(
        self,
        relative_path: VaultRelativePath,
        *,
        create_parent: bool = False,
    ) -> BoundTargetSnapshot | None:
        parent, final_name = self._bound_parent(relative_path, create=create_parent)
        self.verify()
        try:
            if os.name == "nt":
                from backend.app.infrastructure.database_backup import (
                    _open_windows_file_for_bound_rename,
                )

                handle, _identity = _open_windows_file_for_bound_rename(
                    parent.path / final_name
                )
                try:
                    result = _snapshot_from_descriptor(
                        relative_path.value, handle.fileno()
                    )
                finally:
                    handle.close()
            else:
                descriptor = os.open(
                    final_name,
                    os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
                    dir_fd=int(parent.handle),
                )
                try:
                    result = _snapshot_from_descriptor(relative_path.value, descriptor)
                finally:
                    os.close(descriptor)
        except FileNotFoundError:
            self.verify()
            return None
        except OSError as error:
            raise ObsidianVaultError(
                "OBSIDIAN_TARGET_CHANGED", "The Vault target could not be inspected safely."
            ) from error
        self.verify()
        return result

    def inspect_target_identity(
        self,
        relative_path: VaultRelativePath,
        *,
        create_parent: bool = False,
    ) -> BoundTargetIdentity | None:
        parent, final_name = self._bound_parent(
            relative_path,
            create=create_parent,
        )
        self.verify()
        try:
            if os.name == "nt":
                from backend.app.infrastructure.database_backup import (
                    _open_windows_file_for_bound_rename,
                )

                handle, _identity = _open_windows_file_for_bound_rename(
                    parent.path / final_name
                )
                try:
                    result = _target_from_descriptor(
                        relative_path.value,
                        handle.fileno(),
                    )
                finally:
                    handle.close()
            else:
                descriptor = os.open(
                    final_name,
                    os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
                    dir_fd=int(parent.handle),
                )
                try:
                    result = _target_from_descriptor(
                        relative_path.value,
                        descriptor,
                    )
                finally:
                    os.close(descriptor)
        except FileNotFoundError:
            self.verify()
            return None
        except OSError as error:
            raise ObsidianVaultError(
                "OBSIDIAN_TARGET_CHANGED",
                "The Vault target identity could not be inspected safely.",
            ) from error
        self.verify()
        return result

    def replace_managed(
        self,
        relative_path: VaultRelativePath,
        data: bytes,
        expected: BoundTargetIdentity,
    ) -> PublishedFile:
        current = self.bind_target(relative_path)
        if not _same_target(current, expected):
            raise ObsidianVaultError(
                "OBSIDIAN_TARGET_CHANGED",
                "The managed Vault target no longer matches its proof.",
            )
        parent, final_name = self._bound_parent(relative_path, create=False)
        return (
            self._replace_windows(
                parent,
                relative_path,
                final_name,
                lambda handle: _write_handle(handle, data),
                expected,
            )
            if os.name == "nt"
            else self._replace_posix(
                parent,
                relative_path,
                final_name,
                lambda descriptor: _write_descriptor(descriptor, data),
                expected,
            )
        )

    def replace_managed_stream(
        self,
        relative_path: VaultRelativePath,
        stream: Any,
        expected: BoundTargetIdentity,
        *,
        expected_size: int,
        expected_sha256: str,
    ) -> PublishedFile:
        _validate_stream_identity(expected_size, expected_sha256)
        current = self.bind_target(relative_path)
        if not _same_target(current, expected):
            raise ObsidianVaultError(
                "OBSIDIAN_TARGET_CHANGED",
                "The managed Vault target no longer matches its proof.",
            )
        parent, final_name = self._bound_parent(relative_path, create=False)
        return (
            self._replace_windows(
                parent,
                relative_path,
                final_name,
                lambda handle: _copy_stream_to_handle(
                    stream,
                    handle,
                    expected_size=expected_size,
                    expected_sha256=expected_sha256,
                ),
                expected,
            )
            if os.name == "nt"
            else self._replace_posix(
                parent,
                relative_path,
                final_name,
                lambda descriptor: _copy_stream_to_descriptor(
                    stream,
                    descriptor,
                    expected_size=expected_size,
                    expected_sha256=expected_sha256,
                ),
                expected,
            )
        )

    def delete_managed(
        self,
        relative_path: VaultRelativePath,
        expected: BoundTargetIdentity,
    ) -> None:
        current = self.bind_target(relative_path)
        if not _same_target(current, expected):
            raise ObsidianVaultError(
                "OBSIDIAN_TARGET_CHANGED",
                "The managed Vault target no longer matches its proof.",
            )
        parent, final_name = self._bound_parent(relative_path, create=False)
        self.verify()
        if os.name == "nt":
            self._delete_windows(parent.path / final_name, expected)
        else:
            descriptor = os.open(
                final_name,
                os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
                dir_fd=int(parent.handle),
            )
            try:
                rebound = _target_from_descriptor(relative_path.value, descriptor)
                if not _same_target(rebound, expected):
                    raise ObsidianVaultError(
                        "OBSIDIAN_TARGET_CHANGED",
                        "The managed Vault target changed before deletion.",
                    )
                os.unlink(final_name, dir_fd=int(parent.handle))
                os.fsync(int(parent.handle))
            finally:
                os.close(descriptor)
        self.verify()

    def _bound_parent(
        self,
        relative_path: VaultRelativePath,
        *,
        create: bool = True,
    ) -> tuple[_BoundDirectory, str]:
        self._require_open()
        segments = relative_path.segments
        if len(segments) == 1:
            return self._directories[0], segments[0]
        parent_value = "/".join(segments[:-1])
        if create:
            self.ensure_directory(VaultRelativePath(parent_value))
        parent = next(
            (item for item in self._directories if item.relative_path == parent_value),
            None,
        )
        if parent is None:
            raise ObsidianVaultError(
                "OBSIDIAN_PARENT_CHANGED", "The Vault parent directory is unavailable."
            )
        return parent, segments[-1]

    def _publish_new_windows(
        self,
        parent: _BoundDirectory,
        relative_path: VaultRelativePath,
        final_name: str,
        writer: Callable[[Any], None],
    ) -> PublishedFile:
        from backend.app.infrastructure.database_backup import (
            DatabaseBackupError,
            _create_windows_owned_exclusive_file,
            _mark_windows_file_handle_for_deletion,
            _rename_windows_handle_no_replace,
        )

        temporary_name = f".{final_name}.{uuid4().hex}.tmp"
        handle = None
        renamed = False
        identity = None
        try:
            handle, identity = _create_windows_owned_exclusive_file(
                int(parent.handle), temporary_name
            )
            writer(handle)
            self.verify()
            final_path = parent.path / final_name
            if self._before_publish is not None:
                self._before_publish(final_path)
            self.verify()
            _rename_windows_handle_no_replace(handle, final_path)
            renamed = True
            target = _target_from_descriptor(relative_path.value, handle.fileno())
            return PublishedFile(
                relative_path.value, target.size, target.sha256, target
            )
        except FileExistsError as error:
            raise ObsidianVaultError(
                "OBSIDIAN_TARGET_EXISTS",
                "The Vault target appeared during exclusive publication.",
            ) from error
        except ObsidianVaultError:
            raise
        except (OSError, DatabaseBackupError) as error:
            raise ObsidianVaultError(
                "OBSIDIAN_ATOMIC_PRIMITIVE_UNAVAILABLE",
                "The Vault file could not be published atomically.",
            ) from error
        finally:
            if handle is not None:
                if not renamed and identity is not None:
                    try:
                        _mark_windows_file_handle_for_deletion(
                            handle, expected_identity=identity, strict=False
                        )
                    except (OSError, DatabaseBackupError):
                        pass
                handle.close()

    def _publish_new_posix(
        self,
        parent: _BoundDirectory,
        relative_path: VaultRelativePath,
        final_name: str,
        writer: Callable[[int], None],
    ) -> PublishedFile:
        temporary_name = f".{final_name}.{uuid4().hex}.tmp"
        descriptor = os.open(
            temporary_name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
            0o600,
            dir_fd=int(parent.handle),
        )
        linked = False
        try:
            writer(descriptor)
            self.verify()
            if self._before_publish is not None:
                self._before_publish(parent.path / final_name)
            self.verify()
            try:
                os.link(
                    temporary_name,
                    final_name,
                    src_dir_fd=int(parent.handle),
                    dst_dir_fd=int(parent.handle),
                    follow_symlinks=False,
                )
            except FileExistsError as error:
                raise ObsidianVaultError(
                    "OBSIDIAN_TARGET_EXISTS",
                    "The Vault target appeared during exclusive publication.",
                ) from error
            linked = True
            target = _target_from_descriptor(relative_path.value, descriptor)
            os.unlink(temporary_name, dir_fd=int(parent.handle))
            os.fsync(int(parent.handle))
            return PublishedFile(
                relative_path.value, target.size, target.sha256, target
            )
        finally:
            os.close(descriptor)
            if not linked:
                try:
                    os.unlink(temporary_name, dir_fd=int(parent.handle))
                except FileNotFoundError:
                    pass

    def _replace_windows(
        self,
        parent: _BoundDirectory,
        relative_path: VaultRelativePath,
        final_name: str,
        writer: Callable[[Any], None],
        expected: BoundTargetIdentity,
    ) -> PublishedFile:
        from backend.app.infrastructure.database_backup import (
            DatabaseBackupError,
            _create_windows_owned_exclusive_file,
            _mark_windows_file_handle_for_deletion,
        )

        temporary_name = f".{final_name}.{uuid4().hex}.tmp"
        backup_name = f".{final_name}.{uuid4().hex}.bak"
        handle, identity = _create_windows_owned_exclusive_file(
            int(parent.handle), temporary_name
        )
        try:
            writer(handle)
        finally:
            handle.close()
        final_path = parent.path / final_name
        temporary_path = parent.path / temporary_name
        backup_path = parent.path / backup_name
        try:
            self.verify()
            _replace_windows_file(final_path, temporary_path, backup_path)
            backup = _read_windows_target(backup_path, relative_path.value)
            if not _same_target(backup, expected):
                _replace_windows_file(final_path, backup_path, None)
                raise ObsidianVaultError(
                    "OBSIDIAN_TARGET_CHANGED",
                    "The managed Vault target changed during replacement.",
                )
            self._delete_windows(backup_path, backup)
            self.verify()
            target = self.bind_target(relative_path)
            return PublishedFile(
                relative_path.value, target.size, target.sha256, target
            )
        except ObsidianVaultError:
            raise
        except OSError as error:
            raise ObsidianVaultError(
                "OBSIDIAN_TARGET_CHANGED",
                "The managed Vault target could not be replaced safely.",
            ) from error
        finally:
            if temporary_path.exists():
                try:
                    from backend.app.infrastructure.database_backup import (
                        _open_windows_file_for_bound_rename,
                    )

                    cleanup_handle, cleanup_identity = _open_windows_file_for_bound_rename(
                        temporary_path
                    )
                    try:
                        _mark_windows_file_handle_for_deletion(
                            cleanup_handle,
                            expected_identity=cleanup_identity,
                            strict=False,
                        )
                    finally:
                        cleanup_handle.close()
                except (OSError, DatabaseBackupError):
                    pass

    def _replace_posix(
        self,
        parent: _BoundDirectory,
        relative_path: VaultRelativePath,
        final_name: str,
        writer: Callable[[int], None],
        expected: BoundTargetIdentity,
    ) -> PublishedFile:
        temporary_name = f".{final_name}.{uuid4().hex}.tmp"
        descriptor = os.open(
            temporary_name,
            os.O_RDWR | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
            0o600,
            dir_fd=int(parent.handle),
        )
        try:
            writer(descriptor)
            _rename_exchange(int(parent.handle), temporary_name, final_name)
            prior_descriptor = os.open(
                temporary_name,
                os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
                dir_fd=int(parent.handle),
            )
            try:
                prior = _target_from_descriptor(relative_path.value, prior_descriptor)
            finally:
                os.close(prior_descriptor)
            if not _same_target(prior, expected):
                _rename_exchange(int(parent.handle), temporary_name, final_name)
                raise ObsidianVaultError(
                    "OBSIDIAN_TARGET_CHANGED",
                    "The managed Vault target changed during replacement.",
                )
            os.unlink(temporary_name, dir_fd=int(parent.handle))
            os.fsync(int(parent.handle))
            target = self.bind_target(relative_path)
            return PublishedFile(
                relative_path.value, target.size, target.sha256, target
            )
        finally:
            os.close(descriptor)
            try:
                os.unlink(temporary_name, dir_fd=int(parent.handle))
            except FileNotFoundError:
                pass

    @staticmethod
    def _delete_windows(path: Path, expected: BoundTargetIdentity) -> None:
        from backend.app.infrastructure.database_backup import (
            DatabaseBackupError,
            _mark_windows_file_handle_for_deletion,
            _open_windows_file_for_bound_rename,
        )

        try:
            handle, identity = _open_windows_file_for_bound_rename(path)
            try:
                rebound = _target_from_descriptor(expected.relative_path, handle.fileno())
                if not _same_target(rebound, expected):
                    raise ObsidianVaultError(
                        "OBSIDIAN_TARGET_CHANGED",
                        "The managed Vault target changed before deletion.",
                    )
                _mark_windows_file_handle_for_deletion(
                    handle, expected_identity=identity, strict=True
                )
            finally:
                handle.close()
        except ObsidianVaultError:
            raise
        except (OSError, DatabaseBackupError) as error:
            raise ObsidianVaultError(
                "OBSIDIAN_TARGET_CHANGED",
                "The managed Vault target could not be deleted safely.",
            ) from error

    def close(self) -> None:
        if self._closed:
            return
        for directory in reversed(self._directories):
            if os.name == "nt":
                from backend.app.infrastructure.database_backup import (
                    _close_windows_handle,
                )

                _close_windows_handle(int(directory.handle))
            else:
                os.close(int(directory.handle))
        self._directories.clear()
        self._closed = True

    def _require_open(self) -> None:
        if self._closed:
            raise ObsidianVaultError(
                "OBSIDIAN_ROOT_CHANGED",
                "The bound Vault root is already closed.",
            )


def _changed_directory_error(index: int) -> ObsidianVaultError:
    return ObsidianVaultError(
        "OBSIDIAN_ROOT_CHANGED" if index == 0 else "OBSIDIAN_PARENT_CHANGED",
        "The bound Vault directory identity changed during the operation.",
    )


def _open_windows_root(path: Path) -> BoundVaultRoot:
    from backend.app.infrastructure.database_backup import (
        _close_windows_handle,
        _open_windows_directory_handle,
    )

    handle: int | None = None
    try:
        handle, identity = _open_windows_directory_handle(path, protect_delete=True)
        device, inode = _probe_windows_path(path)
        if (device, inode) != (int(identity.device), int(identity.inode)):
            raise ObsidianVaultError(
                "OBSIDIAN_ROOT_CHANGED",
                "The Vault root changed while it was being bound.",
            )
        return BoundVaultRoot(
            path,
            [
                _BoundDirectory(
                    path=path,
                    relative_path="",
                    handle=handle,
                    device=device,
                    inode=inode,
                )
            ],
        )
    except Exception:
        if handle is not None:
            _close_windows_handle(handle)
        raise


def _probe_windows_path(path: Path) -> tuple[int, int]:
    from backend.app.infrastructure.database_backup import (
        _close_windows_handle,
        _open_windows_directory_handle,
    )

    handle, identity = _open_windows_directory_handle(
        path,
        compatible_with_protected_handle=True,
    )
    try:
        return int(identity.device), int(identity.inode)
    finally:
        _close_windows_handle(handle)


def _bind_windows_child(
    parent: _BoundDirectory,
    segment: str,
    relative_path: str,
) -> _BoundDirectory:
    from backend.app.infrastructure.database_backup import (
        DatabaseBackupError,
        _create_windows_bound_directory,
        _open_windows_directory_handle,
    )

    child_path = parent.path / segment
    try:
        handle, identity = _open_windows_directory_handle(
            child_path,
            protect_delete=True,
        )
    except OSError as open_error:
        if child_path.exists():
            raise ObsidianVaultError(
                "OBSIDIAN_PATH_ESCAPE",
                "A Vault parent could not be bound safely.",
            ) from open_error
        try:
            handle, identity = _create_windows_bound_directory(
                int(parent.handle),
                segment,
            )
        except (OSError, DatabaseBackupError) as create_error:
            raise ObsidianVaultError(
                "OBSIDIAN_PARENT_CHANGED",
                "A Vault parent changed while it was being created.",
            ) from create_error
    device, inode = _probe_windows_path(child_path)
    if (device, inode) != (int(identity.device), int(identity.inode)):
        from backend.app.infrastructure.database_backup import _close_windows_handle

        _close_windows_handle(handle)
        raise ObsidianVaultError(
            "OBSIDIAN_PARENT_CHANGED",
            "A Vault parent changed while it was being bound.",
        )
    return _BoundDirectory(
        path=child_path,
        relative_path=relative_path,
        handle=handle,
        device=device,
        inode=inode,
    )


def _open_posix_root(path: Path) -> BoundVaultRoot:
    required = getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    if not required or os.open not in os.supports_dir_fd:
        raise ObsidianVaultError(
            "OBSIDIAN_ATOMIC_PRIMITIVE_UNAVAILABLE",
            "This POSIX platform cannot bind Vault directories safely.",
        )
    descriptor = os.open(path, os.O_RDONLY | required)
    metadata = os.fstat(descriptor)
    if not stat.S_ISDIR(metadata.st_mode):
        os.close(descriptor)
        raise ObsidianVaultError(
            "OBSIDIAN_VAULT_NOT_DIRECTORY",
            "The configured Vault root is not a physical directory.",
        )
    return BoundVaultRoot(
        path,
        [
            _BoundDirectory(
                path=path,
                relative_path="",
                handle=descriptor,
                device=int(metadata.st_dev),
                inode=int(metadata.st_ino),
            )
        ],
    )


def _bind_posix_child(
    parent: _BoundDirectory,
    segment: str,
    relative_path: str,
) -> _BoundDirectory:
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    try:
        descriptor = os.open(segment, flags, dir_fd=int(parent.handle))
    except FileNotFoundError:
        try:
            os.mkdir(segment, mode=0o700, dir_fd=int(parent.handle))
            descriptor = os.open(segment, flags, dir_fd=int(parent.handle))
        except OSError as error:
            raise ObsidianVaultError(
                "OBSIDIAN_PARENT_CHANGED",
                "A Vault parent changed while it was being created.",
            ) from error
    except OSError as error:
        raise ObsidianVaultError(
            "OBSIDIAN_PATH_ESCAPE",
            "A Vault parent could not be bound safely.",
        ) from error
    metadata = os.fstat(descriptor)
    if not stat.S_ISDIR(metadata.st_mode):
        os.close(descriptor)
        raise ObsidianVaultError(
            "OBSIDIAN_PATH_ESCAPE",
            "A Vault parent is not a physical directory.",
        )
    return _BoundDirectory(
        path=parent.path / segment,
        relative_path=relative_path,
        handle=descriptor,
        device=int(metadata.st_dev),
        inode=int(metadata.st_ino),
    )


__all__ = [
    "BoundDirectoryIdentity",
    "BoundTargetIdentity",
    "BoundTargetSnapshot",
    "BoundVaultRoot",
    "ObsidianVaultError",
    "PublishedFile",
    "VaultRelativePath",
]


def _write_handle(handle: Any, data: bytes) -> None:
    handle.seek(0)
    handle.write(data)
    handle.flush()
    os.fsync(handle.fileno())


def _validate_stream_identity(expected_size: int, expected_sha256: str) -> None:
    if (
        not isinstance(expected_size, int)
        or isinstance(expected_size, bool)
        or expected_size < 0
        or not isinstance(expected_sha256, str)
        or len(expected_sha256) != 64
        or any(character not in "0123456789abcdef" for character in expected_sha256)
    ):
        raise ValueError("stream identity must contain a size and lowercase SHA-256")


def _copy_stream_to_handle(
    stream: Any,
    handle: Any,
    *,
    expected_size: int,
    expected_sha256: str,
) -> None:
    handle.seek(0)

    def write(chunk: bytes) -> None:
        view = memoryview(chunk)
        while view:
            written = handle.write(view)
            if not isinstance(written, int) or written <= 0:
                raise OSError("Vault stream write made no progress")
            view = view[written:]

    _copy_verified_stream(
        stream,
        write,
        expected_size=expected_size,
        expected_sha256=expected_sha256,
    )
    handle.flush()
    os.fsync(handle.fileno())


def _copy_stream_to_descriptor(
    stream: Any,
    descriptor: int,
    *,
    expected_size: int,
    expected_sha256: str,
) -> None:
    _copy_verified_stream(
        stream,
        lambda chunk: _write_descriptor_chunk(descriptor, chunk),
        expected_size=expected_size,
        expected_sha256=expected_sha256,
    )
    os.fsync(descriptor)


def _copy_verified_stream(
    stream: Any,
    writer: Callable[[bytes], None],
    *,
    expected_size: int,
    expected_sha256: str,
) -> None:
    digest = hashlib.sha256()
    size = 0
    while True:
        chunk = stream.read(1024 * 1024)
        if not isinstance(chunk, bytes):
            raise ObsidianVaultError(
                "OBSIDIAN_STREAM_SOURCE_CHANGED",
                "The projection source descriptor returned invalid bytes.",
            )
        if not chunk:
            break
        size += len(chunk)
        if size > expected_size:
            raise ObsidianVaultError(
                "OBSIDIAN_STREAM_SOURCE_CHANGED",
                "The projection source size changed during publication.",
            )
        digest.update(chunk)
        writer(chunk)
    if size != expected_size or digest.hexdigest() != expected_sha256:
        raise ObsidianVaultError(
            "OBSIDIAN_STREAM_SOURCE_CHANGED",
            "The projection source identity changed during publication.",
        )


def _write_descriptor(descriptor: int, data: bytes) -> None:
    _write_descriptor_chunk(descriptor, data)
    os.fsync(descriptor)


def _write_descriptor_chunk(descriptor: int, data: bytes) -> None:
    view = memoryview(data)
    while view:
        written = os.write(descriptor, view)
        if written <= 0:
            raise OSError("Vault write made no progress")
        view = view[written:]


def _target_from_descriptor(relative_path: str, descriptor: int) -> BoundTargetIdentity:
    metadata = os.fstat(descriptor)
    os.lseek(descriptor, 0, os.SEEK_SET)
    digest = hashlib.sha256()
    while True:
        chunk = os.read(descriptor, 1024 * 1024)
        if not chunk:
            break
        digest.update(chunk)
    os.lseek(descriptor, 0, os.SEEK_SET)
    return BoundTargetIdentity(
        relative_path=relative_path,
        device=int(metadata.st_dev),
        inode=int(metadata.st_ino),
        size=int(metadata.st_size),
        sha256=digest.hexdigest(),
    )


def _snapshot_from_descriptor(
    relative_path: str, descriptor: int
) -> BoundTargetSnapshot:
    metadata = os.fstat(descriptor)
    if not stat.S_ISREG(metadata.st_mode):
        raise OSError("Vault target is not a regular file")
    os.lseek(descriptor, 0, os.SEEK_SET)
    chunks: list[bytes] = []
    digest = hashlib.sha256()
    while True:
        chunk = os.read(descriptor, 1024 * 1024)
        if not chunk:
            break
        chunks.append(chunk)
        digest.update(chunk)
    os.lseek(descriptor, 0, os.SEEK_SET)
    identity = BoundTargetIdentity(
        relative_path=relative_path,
        device=int(metadata.st_dev),
        inode=int(metadata.st_ino),
        size=int(metadata.st_size),
        sha256=digest.hexdigest(),
    )
    return BoundTargetSnapshot(identity=identity, data=b"".join(chunks))


def _same_target(left: BoundTargetIdentity, right: BoundTargetIdentity) -> bool:
    return (
        left.relative_path == right.relative_path
        and left.device == right.device
        and left.inode == right.inode
        and left.size == right.size
        and left.sha256 == right.sha256
    )


def _read_windows_target(path: Path, relative_path: str) -> BoundTargetIdentity:
    from backend.app.infrastructure.database_backup import (
        _open_windows_file_for_bound_rename,
    )

    handle, _identity = _open_windows_file_for_bound_rename(path)
    try:
        return _target_from_descriptor(relative_path, handle.fileno())
    finally:
        handle.close()


def _replace_windows_file(
    replaced: Path,
    replacement: Path,
    backup: Path | None,
) -> None:
    import ctypes
    from ctypes import wintypes

    replace_file = ctypes.WinDLL("kernel32", use_last_error=True).ReplaceFileW
    replace_file.argtypes = (
        wintypes.LPCWSTR,
        wintypes.LPCWSTR,
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.LPVOID,
    )
    replace_file.restype = wintypes.BOOL
    if not replace_file(
        str(replaced),
        str(replacement),
        str(backup) if backup is not None else None,
        0,
        None,
        None,
    ):
        raise OSError(ctypes.get_last_error(), "ReplaceFileW failed")


def _rename_exchange(parent_descriptor: int, left: str, right: str) -> None:
    import ctypes

    libc = ctypes.CDLL(None, use_errno=True)
    renameat2 = getattr(libc, "renameat2", None)
    if renameat2 is None:
        raise ObsidianVaultError(
            "OBSIDIAN_ATOMIC_PRIMITIVE_UNAVAILABLE",
            "This platform does not provide identity-recoverable replacement.",
        )
    renameat2.argtypes = (
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_uint,
    )
    renameat2.restype = ctypes.c_int
    if renameat2(
        parent_descriptor,
        os.fsencode(left),
        parent_descriptor,
        os.fsencode(right),
        2,
    ) != 0:
        raise OSError(ctypes.get_errno(), "renameat2(RENAME_EXCHANGE) failed")
