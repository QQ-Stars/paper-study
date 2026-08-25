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


class ExperimentRunStatus(str, Enum):
    PLANNED = "planned"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    BLOCKED = "blocked"


_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_PROJECT_STATUSES = frozenset(status.value for status in ReproductionProjectStatus)
_RUN_STATUSES = frozenset(status.value for status in ExperimentRunStatus)


def validate_project_status(value: str) -> str:
    if value not in _PROJECT_STATUSES:
        raise ValueError("status is invalid")
    return value


def validate_run_status(value: str) -> str:
    if value not in _RUN_STATUSES:
        raise ValueError("status is invalid")
    return value


def validate_sha256(value: str) -> str:
    if _SHA256.fullmatch(value) is None:
        raise ValueError("sha256 must be a lowercase SHA-256 digest")
    return value


@dataclass(frozen=True, slots=True)
class ReproductionProject:
    id: str
    name: str
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


__all__ = [
    "DEFAULT_DOCUMENT",
    "ExperimentRun",
    "ExperimentRunStatus",
    "ReproductionArtifact",
    "ReproductionDocument",
    "ReproductionNote",
    "ReproductionProject",
    "ReproductionProjectStatus",
    "validate_project_status",
    "validate_run_status",
    "validate_sha256",
]
