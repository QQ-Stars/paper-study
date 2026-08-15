from __future__ import annotations

from typing import Any

from backend.app.domain import Credential, CredentialBackendError, CredentialKind
from backend.app.providers.credentials.mappings import KEYRING_USERNAMES
from backend.app.providers.credentials.threaded import run_thread_to_completion


class _LazySystemKeyring:
    def get_password(self, service: str, username: str) -> str | None:
        import keyring
        return keyring.get_password(service, username)

    def set_password(self, service: str, username: str, value: str) -> None:
        import keyring
        keyring.set_password(service, username, value)

    def delete_password(self, service: str, username: str) -> None:
        import keyring
        keyring.delete_password(service, username)


class KeyringCredentialStore:
    SERVICE = "study-app"

    def __init__(self, adapter: Any | None = None) -> None:
        self._adapter = adapter or _LazySystemKeyring()

    async def get(self, kind: CredentialKind) -> Credential | None:
        normalized = CredentialKind(kind)
        try:
            value = await run_thread_to_completion(
                self._adapter.get_password, self.SERVICE, KEYRING_USERNAMES[normalized]
            )
        except Exception:
            raise CredentialBackendError(operation="keyring_get") from None
        if value is None or not str(value).strip():
            return None
        return Credential(normalized, str(value))

    async def set(self, kind: CredentialKind, value: str) -> None:
        normalized = CredentialKind(kind)
        try:
            await run_thread_to_completion(
                self._adapter.set_password,
                self.SERVICE,
                KEYRING_USERNAMES[normalized],
                value,
            )
        except Exception:
            raise CredentialBackendError(operation="keyring_set") from None

    async def delete(self, kind: CredentialKind) -> None:
        normalized = CredentialKind(kind)
        try:
            await run_thread_to_completion(
                self._adapter.delete_password, self.SERVICE, KEYRING_USERNAMES[normalized]
            )
        except Exception:
            raise CredentialBackendError(operation="keyring_delete") from None
