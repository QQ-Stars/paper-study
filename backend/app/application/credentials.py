from __future__ import annotations

from backend.app.application.ports.credential_probe import CredentialProbe, CredentialProbeResult
from backend.app.application.ports.credential_store import CredentialStore
from backend.app.domain import CredentialKind, CredentialStatus


class CredentialService:
    def __init__(self, store: CredentialStore, probe: CredentialProbe) -> None:
        self._store = store
        self._probe = probe

    async def status(self, kind: CredentialKind) -> CredentialStatus:
        return await self._store.status(CredentialKind(kind))

    async def credential(self, kind: CredentialKind):
        """Return the effective credential to an application-level probe.

        The value is intentionally only exposed to another application service;
        HTTP adapters must use :meth:`status` and never serialize this object.
        """

        return await self._store.get(CredentialKind(kind))

    async def update(self, kind: CredentialKind, submitted_value: str) -> CredentialStatus:
        return await self._store.update(CredentialKind(kind), submitted_value)

    async def clear(self, kind: CredentialKind) -> CredentialStatus:
        return await self._store.clear(CredentialKind(kind))

    async def test_connection(
        self,
        kind: CredentialKind,
        *,
        probe: CredentialProbe | None = None,
    ) -> CredentialProbeResult:
        normalized = CredentialKind(kind)
        selected_probe = self._probe if probe is None else probe
        return await selected_probe.test(normalized, await self._store.get(normalized))
