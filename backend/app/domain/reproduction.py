from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import re


class ReproductionProjectStatus(str, Enum):
    PLANNED = "planned"
    PREPARING = "preparing"
    RUNNING = "running"
    COMPLETED = "completed"
    BLOCKED = "blocked"
    ARCHIVED = "archived"


class ReproductionProjectKind(str, Enum):
    REPRODUCTION = "reproduction"
    ARTICLE = "article"


class ExperimentRunStatus(str, Enum):
    PLANNED = "planned"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    BLOCKED = "blocked"


class ReproductionResultStatus(str, Enum):
    REPRODUCED = "reproduced"
    PARTIAL = "partial"
    NOT_REPRODUCED = "not_reproduced"
    INCONSISTENT = "inconsistent"


class PublicationDecision(str, Enum):
    """Human decision controlling whether a completed project may be exported."""

    DRAFT = "draft"
    APPROVED = "approved"
    REVOKED = "revoked"


class PublicationStatus(str, Enum):
    DRAFT = "draft"
    PUBLISHED = "published"
    STALE = "stale"
    FAILED = "failed"
    REVOKED = "revoked"


_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_PROJECT_KINDS = frozenset(kind.value for kind in ReproductionProjectKind)
_PROJECT_STATUSES = frozenset(status.value for status in ReproductionProjectStatus)
_RUN_STATUSES = frozenset(status.value for status in ExperimentRunStatus)
_RESULT_STATUSES = frozenset(status.value for status in ReproductionResultStatus)
_PUBLICATION_DECISIONS = frozenset(status.value for status in PublicationDecision)
_PUBLICATION_STATUSES = frozenset(status.value for status in PublicationStatus)


def validate_project_kind(value: str) -> str:
    if value not in _PROJECT_KINDS:
        raise ValueError("project kind is invalid")
    return value


def validate_project_status(value: str) -> str:
    if value not in _PROJECT_STATUSES:
        raise ValueError("status is invalid")
    return value


def validate_run_status(value: str) -> str:
    if value not in _RUN_STATUSES:
        raise ValueError("status is invalid")
    return value


def validate_result_status(value: str) -> str:
    if value not in _RESULT_STATUSES:
        raise ValueError("result status is invalid")
    return value


def validate_publication_decision(value: str) -> str:
    if value not in _PUBLICATION_DECISIONS:
        raise ValueError("publication decision is invalid")
    return value


def validate_publication_status(value: str) -> str:
    if value not in _PUBLICATION_STATUSES:
        raise ValueError("publication status is invalid")
    return value


def validate_sha256(value: str) -> str:
    if _SHA256.fullmatch(value) is None:
        raise ValueError("sha256 must be a lowercase SHA-256 digest")
    return value


@dataclass(frozen=True, slots=True)
class ReproductionProject:
    id: str
    name: str
    project_kind: ReproductionProjectKind | str
    paper_id: str | None
    paper_title: str
    status: ReproductionProjectStatus | str
    tags: tuple[str, ...]
    revision: int
    created_at: str
    updated_at: str

    def __post_init__(self) -> None:
        if not self.id.strip() or not self.name.strip():
            raise ValueError("project id and name must be nonblank")
        if not self.paper_title.strip():
            raise ValueError("paper_title must be nonblank")
        object.__setattr__(self, "project_kind", ReproductionProjectKind(self.project_kind))
        object.__setattr__(self, "status", ReproductionProjectStatus(self.status))
        if self.revision < 1:
            raise ValueError("revision must be positive")


@dataclass(frozen=True, slots=True)
class ReproductionDocument:
    id: str
    project_id: str
    content: str
    revision: int
    save_status: str
    created_at: str
    updated_at: str


@dataclass(frozen=True, slots=True)
class ExperimentRun:
    id: str
    project_id: str
    environment: str | None
    command: str | None
    parameters: dict[str, object]
    data_version: str | None
    code_revision: str | None
    seed: int | None
    status: ExperimentRunStatus | str
    metrics: dict[str, object]
    result_summary: str | None
    created_at: str
    updated_at: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "status", ExperimentRunStatus(self.status))


@dataclass(frozen=True, slots=True)
class ReproductionResult:
    id: str
    project_id: str
    metric_name: str
    paper_value: str | None
    reproduction_value: str | None
    difference: str | None
    difference_percent: str | None
    dataset_settings: str | None
    source: str | None
    status: ReproductionResultStatus | str
    notes: str | None
    created_at: str
    updated_at: str

    def __post_init__(self) -> None:
        if not self.id.strip() or not self.project_id.strip() or not self.metric_name.strip():
            raise ValueError("result identity and metric_name must be nonblank")
        object.__setattr__(self, "status", ReproductionResultStatus(self.status))


@dataclass(frozen=True, slots=True)
class ReproductionArtifact:
    id: str
    project_id: str
    run_id: str | None
    kind: str
    filename: str
    storage_key: str
    mime_type: str
    size_bytes: int
    sha256: str
    created_at: str


@dataclass(frozen=True, slots=True)
class ReproductionNote:
    id: str
    project_id: str
    content: str
    created_at: str
    updated_at: str


DEFAULT_DOCUMENT = """# 复现目标

记录希望验证的研究结论、指标与成功标准。

# 原论文方法

## 环境与依赖

## 数据集与预处理

## 实验配置

# 执行记录

# 结果对照

# 偏差与问题

# 结论与下一步
"""


ARTICLE_DOCUMENT = """# 文章标题

用一段话写清这篇文章要回答的问题，以及读者可以获得什么。

## 背景与动机

交代问题背景、写作缘由和必要上下文。

## 核心观点

展开主要论点、方法、案例或教程步骤。

## 讨论与延伸

记录限制、不同观点、可继续探索的方向。

## 结语

总结文章结论，并补充参考资料或后续计划。
"""


__all__ = [
    "ARTICLE_DOCUMENT",
    "DEFAULT_DOCUMENT",
    "ExperimentRun",
    "ExperimentRunStatus",
    "PublicationDecision",
    "PublicationStatus",
    "ReproductionResult",
    "ReproductionResultStatus",
    "ReproductionArtifact",
    "ReproductionDocument",
    "ReproductionNote",
    "ReproductionProject",
    "ReproductionProjectKind",
    "ReproductionProjectStatus",
    "validate_project_kind",
    "validate_project_status",
    "validate_run_status",
    "validate_result_status",
    "validate_publication_decision",
    "validate_publication_status",
    "validate_sha256",
]
