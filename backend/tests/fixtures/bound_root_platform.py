from __future__ import annotations

import os
import stat
import uuid
from pathlib import Path
from typing import Any, Literal

import backend.app.infrastructure.database_backup as database_backup


class DeterministicBoundRootPlatform:
    """Host-independent model of the two P0 bound-root contracts."""

    def __init__(
        self,
        kind: Literal["windows", "posix"],
        *,
        root_is_private: bool = True,
    ) -> None:
        self.kind = kind
        self._root_is_private = root_is_private
        self.root_swap_attempted = False
        self.root_swap_succeeded = False
        self.destination_swap_attempted = False
        self.destination_swap_succeeded = False
        self.mkdirat_calls = 0
        self.openat_nofollow_calls = 0
        self.validation_parent: Path | None = None
        self.fingerprint_path: Path | None = None
        self.detached_root: Path | None = None
        self.replacement_root: Path | None = None
        self.hostile_sentinel: Path | None = None
        self._protected_destinations: set[Path] = set()

    def is_supported(self) -> bool:
        return True

    def open_restore_root(self, path: Path) -> database_backup.BoundRestoreRoot:
        identity = database_backup._identity_from_stat(path.stat(follow_symlinks=False))
        bound_path = path

        if self.kind == "windows":
            token_path, token_handle = self._open_binding_token(path, "windows-handle")
            root = _FixtureBoundRestoreRoot(
                self,
                path,
                bound_path,
                identity,
                token_path,
                token_handle,
            )
            self._attempt_windows_root_swap(root)
            return root

        if not self._root_is_private:
            raise database_backup.DatabaseBackupError(
                "RESTORE_OUTPUT_DIRECTORY_PRIVATE_REQUIRED",
                "POSIX restore validation requires a private owner-only output directory.",
            )
        self.root_swap_attempted = True
        detached_root = path.with_name(f"{path.name}-bound-original")
        path.rename(detached_root)
        path.mkdir(mode=0o700)
        sentinel = path / "hostile-parent-sentinel.bin"
        sentinel.write_bytes(b"hostile replacement root\n")
        self.root_swap_succeeded = True
        self.detached_root = detached_root
        self.replacement_root = path
        self.hostile_sentinel = sentinel
        token_path, token_handle = self._open_binding_token(detached_root, "posix-dirfd")
        return _FixtureBoundRestoreRoot(
            self,
            path,
            detached_root,
            identity,
            token_path,
            token_handle,
        )

    def fingerprint_bound_restore(
        self,
        destination: Any,
        manifest: Any,
    ) -> Any:
        self.fingerprint_path = destination.bound_path
        if self.kind == "windows":
            return manifest.database
        return database_backup._fingerprint_database(
            destination.bound_path,
            format_version=manifest.format_version,
        )

    def _open_binding_token(self, root: Path, label: str) -> tuple[Path, Any]:
        token_path = root / f".{label}-{uuid.uuid4().hex}.lock"
        descriptor = os.open(
            token_path,
            os.O_CREAT | os.O_EXCL | os.O_RDWR | getattr(os, "O_BINARY", 0),
            0o600,
        )
        return token_path, os.fdopen(descriptor, "w+b")

    def _attempt_windows_root_swap(self, root: "_FixtureBoundRestoreRoot") -> None:
        self.root_swap_attempted = True
        hostile_root = root.path.with_name(f"{root.path.name}-hostile-target")
        hostile_root.mkdir(mode=0o700)
        sentinel = hostile_root / "sentinel.bin"
        sentinel.write_bytes(b"windows hostile target\n")
        self.hostile_sentinel = sentinel
        try:
            root.rename_while_bound(hostile_root)
        except PermissionError:
            return
        self.root_swap_succeeded = True

    def _protect_destination(self, path: Path) -> None:
        self._protected_destinations.add(path)

    def _release_destination(self, path: Path) -> None:
        self._protected_destinations.discard(path)

    def _attempt_destination_swap(self, path: Path) -> None:
        if self.kind != "windows" or self.destination_swap_attempted:
            return
        self.destination_swap_attempted = True
        if path in self._protected_destinations:
            return
        path.unlink()
        path.write_bytes(b"hostile destination replacement\n")
        self.destination_swap_succeeded = True


class _FixtureBoundRestoreRoot(database_backup.BoundRestoreRoot):
    def __init__(
        self,
        platform: DeterministicBoundRootPlatform,
        path: Path,
        bound_path: Path,
        identity: Any,
        token_path: Path,
        token_handle: Any,
    ) -> None:
        self._platform = platform
        self.path = path
        self._bound_path = bound_path
        self._identity = identity
        self._token_path = token_path
        self._token_handle = token_handle
        self._children: list[_FixtureBoundValidationDirectory] = []
        self._closed = False

    def rename_while_bound(self, _hostile_root: Path) -> None:
        if self._token_handle is not None:
            raise PermissionError("simulated Windows no-delete-share root handle")
        raise AssertionError("hostile root swap was attempted without a bound handle")

    def create_validation_directory(
        self,
        prefix: str,
    ) -> database_backup.BoundValidationDirectory:
        name = database_backup._validate_bound_child_name(f"{prefix}{uuid.uuid4().hex}")
        child_path = self._bound_path / name
        child_path.mkdir(mode=0o700)
        self._platform.mkdirat_calls += 1
        self._platform.validation_parent = self._bound_path
        identity = database_backup._identity_from_stat(
            child_path.stat(follow_symlinks=False)
        )
        child = _FixtureBoundValidationDirectory(self, name, child_path, identity)
        self._children.append(child)
        return child

    def _assert_current_path(self) -> None:
        try:
            current = database_backup._identity_from_stat(
                self.path.stat(follow_symlinks=False)
            )
        except OSError as error:
            raise database_backup.DatabaseBackupError(
                "RESTORE_OUTPUT_DIRECTORY_CHANGED",
                "Restore validation output ownership changed during validation.",
            ) from error
        if not database_backup._same_file_object(current, self._identity):
            raise database_backup.DatabaseBackupError(
                "RESTORE_OUTPUT_DIRECTORY_CHANGED",
                "Restore validation output ownership changed during validation.",
            )

    def close(self) -> None:
        if self._closed:
            return
        try:
            self._assert_current_path()
        except database_backup.DatabaseBackupError:
            for child in self._children:
                child._abort()
            self._release_token()
            self._closed = True
            raise
        for child in self._children:
            child._close_bound_handles()
        self._release_token()
        self._closed = True

    def _abort(self) -> None:
        if self._closed:
            return
        for child in self._children:
            child._abort()
        self._release_token()
        self._closed = True

    def _release_token(self) -> None:
        if self._token_handle is not None:
            self._token_handle.close()
            self._token_handle = None
        try:
            self._token_path.unlink()
        except FileNotFoundError:
            pass


class _FixtureBoundValidationDirectory(database_backup.BoundValidationDirectory):
    def __init__(
        self,
        root: _FixtureBoundRestoreRoot,
        name: str,
        path: Path,
        identity: Any,
    ) -> None:
        self._root = root
        self._name = name
        self.path = path
        self._identity = identity
        self._destination_handles: dict[str, Any] = {}
        self._destination_identities: dict[str, Any] = {}
        self._closed = False

    def copy_verified_database(self, source: Path, name: str) -> None:
        destination_name = database_backup._validate_bound_child_name(name)
        destination_path = self.path / destination_name
        flags = os.O_CREAT | os.O_EXCL | os.O_RDWR | getattr(os, "O_BINARY", 0)
        descriptor = os.open(destination_path, flags, 0o600)
        handle = os.fdopen(descriptor, "w+b")
        try:
            with source.open("rb") as source_handle:
                while True:
                    chunk = source_handle.read(1024 * 1024)
                    if not chunk:
                        break
                    handle.write(chunk)
            handle.flush()
            os.fsync(handle.fileno())
            identity = database_backup._identity_from_stat(os.fstat(handle.fileno()))
            self._destination_handles[destination_name] = handle
            self._destination_identities[destination_name] = identity
            self._root._platform._protect_destination(destination_path)
            self._root._platform.openat_nofollow_calls += 1
        except Exception:
            handle.close()
            try:
                destination_path.unlink()
            except FileNotFoundError:
                pass
            raise

    def verify_destination(self, name: str) -> Any:
        destination_name = database_backup._validate_bound_child_name(name)
        destination_path = self.path / destination_name
        self._root._platform._attempt_destination_swap(destination_path)
        handle = self._destination_handles[destination_name]
        expected = self._destination_identities[destination_name]
        handle_identity = database_backup._identity_from_stat(os.fstat(handle.fileno()))
        path_identity = database_backup._identity_from_stat(
            destination_path.stat(follow_symlinks=False)
        )
        if not (
            database_backup._same_file_object(handle_identity, expected)
            and database_backup._same_file_object(path_identity, expected)
        ):
            raise database_backup.DatabaseBackupError(
                "RESTORE_PUBLISH_OWNERSHIP_CHANGED",
                "The restored database was replaced during validation.",
            )
        for suffix in ("-wal", "-shm", "-journal"):
            if destination_path.with_name(f"{destination_name}{suffix}").exists():
                raise database_backup.DatabaseBackupError(
                    "BACKUP_SIDECAR_PRESENT",
                    "Generated SQLite database unexpectedly retained a sidecar.",
                )
        return database_backup._BoundDestination(
            bound_path=destination_path,
            report_path=destination_path,
            identity=handle_identity,
            size_bytes=int(os.fstat(handle.fileno()).st_size),
            sha256=database_backup._sha256_descriptor(handle.fileno()),
        )

    def close(self) -> None:
        for name, handle in list(self._destination_handles.items()):
            self._root._platform._release_destination(self.path / name)
            handle.close()
        self._destination_handles.clear()

    def _close_bound_handles(self) -> None:
        if self._closed:
            return
        self.close()
        self._closed = True

    def _abort(self) -> None:
        if self._closed:
            return
        self.close()
        for name, expected in self._destination_identities.items():
            destination_path = self.path / name
            try:
                current = database_backup._identity_from_stat(
                    destination_path.stat(follow_symlinks=False)
                )
            except FileNotFoundError:
                continue
            if database_backup._same_file_object(current, expected):
                destination_path.unlink()
        try:
            current_directory = database_backup._identity_from_stat(
                self.path.stat(follow_symlinks=False)
            )
            if (
                stat.S_ISDIR(self.path.stat(follow_symlinks=False).st_mode)
                and database_backup._same_file_object(current_directory, self._identity)
            ):
                self.path.rmdir()
        except (FileNotFoundError, OSError):
            pass
        self._closed = True
