from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from backend.app.domain.context import ContextBatch, ContextPlan


@dataclass(frozen=True, slots=True)
class StructuredArtifactInput:
    content: str
    covered_ranges: tuple[tuple[int, int], ...]

    def __post_init__(self) -> None:
        if not isinstance(self.content, str) or not self.content.strip():
            raise ValueError("content must be nonblank")
        ranges = tuple(self.covered_ranges)
        if not ranges:
            raise ValueError("covered_ranges must be nonempty")
        object.__setattr__(self, "covered_ranges", ranges)


@dataclass(frozen=True, slots=True)
class StructuredArtifactRequest:
    """Provider input that deliberately exposes only an audited ContextPlan."""

    artifact_id: str
    kind: str
    paper_id: str
    paper_title: str
    paper_authors: tuple[str, ...]
    prompt_version: str
    profile: str = "standard"
    stage: str = "direct"
    plan: ContextPlan | None = None
    batch: ContextBatch | None = None
    inputs: tuple[StructuredArtifactInput, ...] = ()

    def __post_init__(self) -> None:
        for name in ("artifact_id", "kind", "paper_id", "prompt_version"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must be nonblank")
        if self.kind not in {"explainer", "classification", "metadata", "summary"}:
            raise ValueError("kind is not a structured artifact consumer")
        if self.profile not in {"standard", "deep"}:
            raise ValueError("profile is invalid")
        if self.profile == "deep" and self.kind != "explainer":
            raise ValueError("only explainer supports the deep profile")
        if self.stage not in {"direct", "map", "reduce"}:
            raise ValueError("stage is invalid")
        inputs = tuple(self.inputs)
        object.__setattr__(self, "inputs", inputs)
        if self.stage == "direct":
            if self.plan is None or self.batch is not None or inputs:
                raise ValueError("direct request requires only a ContextPlan")
            if self.plan.request_consumer != self.kind:
                raise ValueError("context plan consumer does not match artifact kind")
        elif self.stage == "map":
            if self.plan is not None or self.batch is None or inputs:
                raise ValueError("map request requires only one ContextBatch")
        elif self.plan is not None or self.batch is not None or not inputs:
            raise ValueError("reduce request requires only typed child inputs")
        if not isinstance(self.paper_title, str):
            raise ValueError("paper_title must be text")
        authors = tuple(self.paper_authors)
        if any(not isinstance(author, str) for author in authors):
            raise ValueError("paper_authors must contain text")
        object.__setattr__(self, "paper_authors", authors)


class StructuredArtifactProvider(Protocol):
    provider_id: str
    model_id: str

    async def generate(self, request: StructuredArtifactRequest) -> str: ...


__all__ = [
    "StructuredArtifactInput",
    "StructuredArtifactProvider",
    "StructuredArtifactRequest",
]
