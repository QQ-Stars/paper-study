from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from backend.app.domain import Credential, CredentialKind


@dataclass(frozen=True, slots=True)
class CredentialProbeResult:
    kind: CredentialKind
    ok: bool
    code: str | None = None
    message: str | None = None


class CredentialProbe(Protocol):
    async def test(self, kind: CredentialKind, credential: Credential | None) -> CredentialProbeResult: ...
