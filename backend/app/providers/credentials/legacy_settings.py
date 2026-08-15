from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import threading
from uuid import uuid4

from backend.app.domain import Credential, CredentialBackendError, CredentialKind
from backend.app.providers.credentials.mappings import LEGACY_FIELDS
from backend.app.providers.credentials.threaded import run_thread_to_completion


_LOCK_GUARD = threading.Lock()
_PATH_LOCKS: dict[Path, threading.RLock] = {}


def _path_lock(path: Path) -> threading.RLock:
    with _LOCK_GUARD:
        return _PATH_LOCKS.setdefault(path, threading.RLock())


class LegacySettingsCredentialStore:
    def __init__(self, settings_path: Path) -> None:
        self.path = Path(settings_path).expanduser().resolve()
        self._lock = _path_lock(self.path)

    async def get(self, kind: CredentialKind) -> Credential | None:
        return await run_thread_to_completion(self._get_sync, CredentialKind(kind))

    async def set(self, kind: CredentialKind, value: str) -> None:
        await run_thread_to_completion(self._mutate_sync, CredentialKind(kind), value)

    async def delete(self, kind: CredentialKind) -> None:
        await run_thread_to_completion(self._mutate_sync, CredentialKind(kind), None)

    def _get_sync(self, kind: CredentialKind) -> Credential | None:
        with self._lock:
            _raw, document = self._read()
        value = document.get(LEGACY_FIELDS[kind])
        if not isinstance(value, str) or not value.strip():
            return None
        return Credential(kind, value)

    def _read(self) -> tuple[bytes, dict[str, object]]:
        if not self.path.exists():
            return b"", {}
        try:
            raw = self.path.read_bytes()
            decoded = json.loads(raw.decode("utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            raise CredentialBackendError(operation="legacy_settings_read") from None
        if not isinstance(decoded, dict):
            raise CredentialBackendError(operation="legacy_settings_shape")
        return raw, decoded

    def _mutate_sync(self, kind: CredentialKind, value: str | None) -> None:
        with self._lock:
            original, document = self._read()
            expected_sha = hashlib.sha256(original).digest()
            field = LEGACY_FIELDS[kind]
            if value is None:
                document.pop(field, None)
            else:
                document[field] = value
            payload = (
                json.dumps(document, ensure_ascii=False, indent=2, sort_keys=False) + "\n"
            ).encode("utf-8")
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.parent / f".{self.path.name}.{uuid4().hex}.tmp"
            descriptor = None
            try:
                descriptor = os.open(
                    temporary,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                    0o600,
                )
                with os.fdopen(descriptor, "wb") as handle:
                    descriptor = None
                    handle.write(payload)
                    handle.flush()
                    os.fsync(handle.fileno())
                current = self.path.read_bytes() if self.path.exists() else b""
                if hashlib.sha256(current).digest() != expected_sha:
                    raise CredentialBackendError(operation="legacy_settings_conflict")
                os.replace(temporary, self.path)
            except CredentialBackendError:
                raise
            except OSError:
                raise CredentialBackendError(operation="legacy_settings_write") from None
            finally:
                if descriptor is not None:
                    os.close(descriptor)
                try:
                    temporary.unlink()
                except FileNotFoundError:
                    pass
