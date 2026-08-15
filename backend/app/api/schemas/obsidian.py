from __future__ import annotations

from pydantic import BaseModel, ConfigDict, StrictBool, StrictStr, model_validator


class ObsidianExportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dryRun: StrictBool = False


class ObsidianSyncRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dryRun: StrictBool = False
    applyCleanup: StrictBool = False
    cleanupPlanSha: StrictStr | None = None

    @model_validator(mode="after")
    def validate_cleanup(self) -> "ObsidianSyncRequest":
        if self.applyCleanup:
            if (
                self.cleanupPlanSha is None
                or len(self.cleanupPlanSha) != 64
                or any(character not in "0123456789abcdef" for character in self.cleanupPlanSha)
            ):
                raise ValueError("cleanupPlanSha must be a lowercase SHA-256")
        elif self.cleanupPlanSha is not None:
            raise ValueError("cleanupPlanSha requires applyCleanup")
        return self


__all__ = ["ObsidianExportRequest", "ObsidianSyncRequest"]
