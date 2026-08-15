from __future__ import annotations

from dataclasses import dataclass
import inspect
from typing import Any


@dataclass(slots=True)
class ApiDependencies:
    application: Any
    session_factory: Any

    @property
    def schema_revision(self) -> Any:
        return getattr(self.application, "schema_revision", None)

    async def dispose(self) -> None:
        dispose = getattr(self.application, "dispose", None)
        if dispose is None:
            return
        result = dispose()
        if inspect.isawaitable(result):
            await result
