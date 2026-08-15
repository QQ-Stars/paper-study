from __future__ import annotations

from collections.abc import Mapping

from backend.app.domain import Credential, CredentialKind
from backend.app.providers.credentials.mappings import ENVIRONMENT_NAMES


class EnvironmentCredentialStore:
    def __init__(self, environment: Mapping[str, str]) -> None:
        self._environment = dict(environment)

    async def get(self, kind: CredentialKind) -> Credential | None:
        normalized = CredentialKind(kind)
        value = self._environment.get(ENVIRONMENT_NAMES[normalized])
        if value is None or not value.strip():
            return None
        return Credential(normalized, value)
