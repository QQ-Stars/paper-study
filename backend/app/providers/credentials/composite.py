from __future__ import annotations

import asyncio
import hmac

from backend.app.domain import (
    Credential,
    CredentialBackendError,
    CredentialKind,
    CredentialStatus,
    CredentialUpdateIndeterminateError,
)
from backend.app.providers.credentials.environment import EnvironmentCredentialStore
from backend.app.providers.credentials.keyring import KeyringCredentialStore
from backend.app.providers.credentials.legacy_settings import LegacySettingsCredentialStore


class CompositeCredentialStore:
    def __init__(
        self,
        environment: EnvironmentCredentialStore,
        keyring: KeyringCredentialStore,
        legacy: LegacySettingsCredentialStore,
        *,
        allow_legacy_fallback: bool = False,
    ) -> None:
        self._environment = environment
        self._keyring = keyring
        self._legacy = legacy
        self._allow_legacy_fallback = allow_legacy_fallback
        self._locks = {kind: asyncio.Lock() for kind in CredentialKind}
        self._migrated: set[CredentialKind] = set()
        self._keyring_unavailable: set[CredentialKind] = set()

    async def get(self, kind: CredentialKind) -> Credential | None:
        normalized = CredentialKind(kind)
        async with self._locks[normalized]:
            await self._ensure_migrated(normalized)
            return await self._effective(normalized)

    async def is_configured(self, kind: CredentialKind) -> bool:
        return await self.get(kind) is not None

    async def key_tail(self, kind: CredentialKind) -> str | None:
        credential = await self.get(kind)
        return _tail(credential.value) if credential is not None else None

    async def status(self, kind: CredentialKind) -> CredentialStatus:
        normalized = CredentialKind(kind)
        async with self._locks[normalized]:
            await self._ensure_migrated(normalized)
            return await self._status_unlocked(normalized)

    async def update(
        self,
        kind: CredentialKind,
        submitted_value: str,
    ) -> CredentialStatus:
        normalized = CredentialKind(kind)
        if not isinstance(submitted_value, str):
            raise ValueError("submitted credential must be a string")
        async with self._locks[normalized]:
            if not submitted_value.strip():
                return await self._status_unlocked(normalized)
            await self._ensure_migrated(normalized)
            previous_keyring = await self._keyring_get(normalized)
            previous_legacy = await self._legacy.get(normalized)
            if normalized in self._keyring_unavailable:
                await self._legacy.set(normalized, submitted_value)
                return await self._status_unlocked(normalized)
            legacy_mutation_started = False
            try:
                await self._keyring.set(normalized, submitted_value)
                verified = await self._keyring_get(normalized)
                if verified is None or not hmac.compare_digest(verified.value, submitted_value):
                    raise CredentialBackendError(operation="credential_keyring_verify")
                legacy_mutation_started = True
                await self._legacy.set(normalized, submitted_value)
            except asyncio.CancelledError:
                await self._compensate(
                    normalized,
                    previous_keyring,
                    previous_legacy,
                    restore_legacy=legacy_mutation_started,
                )
                raise
            except Exception as error:
                if self._allow_legacy_fallback and isinstance(error, CredentialBackendError):
                    self._keyring_unavailable.add(normalized)
                    await self._legacy.set(normalized, submitted_value)
                    return await self._status_unlocked(normalized)
                await self._compensate(normalized, previous_keyring)
                if isinstance(error, CredentialBackendError):
                    raise CredentialBackendError(
                        operation=error.details.get("operation", "credential_update")
                    ) from None
                raise CredentialBackendError(operation="credential_update") from None
            return await self._status_unlocked(normalized)

    async def clear(self, kind: CredentialKind) -> CredentialStatus:
        normalized = CredentialKind(kind)
        async with self._locks[normalized]:
            await self._ensure_migrated(normalized)
            previous_keyring = await self._keyring_get(normalized)
            previous_legacy = await self._legacy.get(normalized)
            if normalized in self._keyring_unavailable:
                await self._legacy.delete(normalized)
                return await self._status_unlocked(normalized)
            legacy_mutation_started = False
            try:
                await self._keyring.delete(normalized)
                legacy_mutation_started = True
                await self._legacy.delete(normalized)
            except asyncio.CancelledError:
                await self._compensate(
                    normalized,
                    previous_keyring,
                    previous_legacy,
                    restore_legacy=legacy_mutation_started,
                )
                raise
            except Exception as error:
                if self._allow_legacy_fallback and isinstance(error, CredentialBackendError):
                    self._keyring_unavailable.add(normalized)
                    await self._legacy.delete(normalized)
                    return await self._status_unlocked(normalized)
                await self._compensate(normalized, previous_keyring)
                if isinstance(error, CredentialBackendError):
                    raise CredentialBackendError(
                        operation=error.details.get("operation", "credential_clear")
                    ) from None
                raise CredentialBackendError(operation="credential_clear") from None
            return await self._status_unlocked(normalized)

    async def _ensure_migrated(self, kind: CredentialKind) -> None:
        if kind in self._migrated:
            return
        keyring_value = await self._keyring_get(kind)
        legacy_value = await self._legacy.get(kind)
        if keyring_value is None and legacy_value is not None:
            if kind not in self._keyring_unavailable:
                try:
                    await self._keyring.set(kind, legacy_value.value)
                    verified = await self._keyring_get(kind)
                    if verified is None or not hmac.compare_digest(
                        verified.value,
                        legacy_value.value,
                    ):
                        raise CredentialBackendError(
                            operation="credential_migration_verify"
                        )
                except CredentialBackendError:
                    if not self._allow_legacy_fallback:
                        raise
                    self._keyring_unavailable.add(kind)
        self._migrated.add(kind)

    async def _effective(self, kind: CredentialKind) -> Credential | None:
        # Keyring and the synchronized legacy settings field are both saved
        # configuration.  They outrank process-level environment defaults.
        keyring = await self._keyring_get(kind)
        if keyring is not None:
            return keyring
        legacy = await self._legacy.get(kind)
        if legacy is not None:
            return legacy
        return await self._environment.get(kind)

    async def _status_unlocked(self, kind: CredentialKind) -> CredentialStatus:
        environment = await self._environment.get(kind)
        keyring = await self._keyring_get(kind)
        legacy = await self._legacy.get(kind)
        effective = keyring
        if effective is None:
            effective = legacy
        if effective is None:
            effective = environment
        return CredentialStatus(
            kind=kind,
            has_key=effective is not None,
            key_tail=_tail(effective.value) if effective is not None else None,
            environment_managed=(
                environment is not None and keyring is None and legacy is None
            ),
        )

    async def _keyring_get(self, kind: CredentialKind) -> Credential | None:
        if kind in self._keyring_unavailable:
            return None
        try:
            return await self._keyring.get(kind)
        except CredentialBackendError:
            if not self._allow_legacy_fallback:
                raise
            self._keyring_unavailable.add(kind)
            return None

    async def _compensate(
        self,
        kind: CredentialKind,
        previous_keyring: Credential | None,
        previous_legacy: Credential | None = None,
        *,
        restore_legacy: bool = False,
    ) -> None:
        indeterminate = False
        if restore_legacy:
            try:
                indeterminate = not await self._restore_and_verify(
                    self._legacy,
                    kind,
                    previous_legacy,
                )
            except Exception:
                indeterminate = True
        try:
            if not await self._restore_and_verify(
                self._keyring,
                kind,
                previous_keyring,
            ):
                indeterminate = True
        except Exception:
            indeterminate = True
        if indeterminate:
            raise CredentialUpdateIndeterminateError() from None

    @staticmethod
    async def _restore_and_verify(
        store: object,
        kind: CredentialKind,
        previous: Credential | None,
    ) -> bool:
        if previous is None:
            await store.delete(kind)
        else:
            await store.set(kind, previous.value)
        observed = await store.get(kind)
        if previous is None:
            return observed is None
        return observed is not None and hmac.compare_digest(
            observed.value,
            previous.value,
        )


def _tail(value: str) -> str:
    return "****" if len(value) < 8 else f"****{value[-4:]}"
