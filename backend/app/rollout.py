from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Mapping


class RolloutConfigurationError(ValueError):
    """Raised when a startup-only rollout setting is invalid or unavailable."""

    def __init__(self, code: str, variable: str, value: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.variable = variable
        self.value = value


@dataclass(frozen=True)
class RolloutSettings:
    api_backend_mode: str = "legacy"
    document_pipeline_mode: str = "legacy"
    generation_pipeline_mode: str = "legacy"
    artifact_read_mode: str = "legacy"
    artifact_write_mode: str = "legacy"
    ocr_enabled: bool = False
    obsidian_enabled: bool = False
    processing_cursor_secret: str | None = None


P0_1_ROLLOUT_VOCABULARY = "p0.1"
P5_ROLLOUT_VOCABULARY = "p5"

_SPECIFICATION = (
    ("API_BACKEND_MODE", "api_backend_mode", ("legacy", "shadow", "python"), "legacy"),
    ("DOCUMENT_PIPELINE_MODE", "document_pipeline_mode", ("legacy", "p1"), "legacy"),
    ("GENERATION_PIPELINE_MODE", "generation_pipeline_mode", ("legacy", "p1"), "legacy"),
    ("ARTIFACT_READ_MODE", "artifact_read_mode", ("legacy", "prefer_new"), "legacy"),
    ("ARTIFACT_WRITE_MODE", "artifact_write_mode", ("legacy", "dual"), "legacy"),
    ("OCR_ENABLED", "ocr_enabled", ("0", "1"), "0"),
)

_P5_EXTENSION = (
    ("OBSIDIAN_ENABLED", "obsidian_enabled", ("0", "1"), "0"),
)

_BOOLEAN_VARIABLES = frozenset({"OCR_ENABLED", "OBSIDIAN_ENABLED"})


def _specification(
    vocabulary: str,
) -> tuple[tuple[str, str, tuple[str, ...], str], ...]:
    if vocabulary == P0_1_ROLLOUT_VOCABULARY:
        return _SPECIFICATION
    if vocabulary == P5_ROLLOUT_VOCABULARY:
        return _SPECIFICATION + _P5_EXTENSION
    raise RolloutConfigurationError(
        "INVALID_ROLLOUT_VOCABULARY",
        "ROLLOUT_VOCABULARY",
        vocabulary,
        "ROLLOUT_VOCABULARY must be exactly one of p0.1, p5",
    )


def parse_rollout_settings(
    environment: Mapping[str, str] | None = None,
    *,
    vocabulary: str = P0_1_ROLLOUT_VOCABULARY,
) -> RolloutSettings:
    values = os.environ if environment is None else environment
    parsed: dict[str, object] = {}
    for variable, field, accepted, fallback in _specification(vocabulary):
        value = values.get(variable, fallback)
        if value not in accepted:
            raise RolloutConfigurationError(
                "INVALID_ROLLOUT_VALUE",
                variable,
                value,
                f"{variable} must be exactly one of {', '.join(accepted)}, received {value!r}",
            )
        parsed[field] = value == "1" if variable in _BOOLEAN_VARIABLES else value
    parsed["processing_cursor_secret"] = values.get("PROCESSING_CURSOR_SECRET")
    return RolloutSettings(**parsed)


def load_rollout_settings(
    environment: Mapping[str, str] | None = None,
    *,
    vocabulary: str = P0_1_ROLLOUT_VOCABULARY,
) -> RolloutSettings:
    settings = parse_rollout_settings(environment, vocabulary=vocabulary)
    effective = rollout_to_environment(settings, vocabulary=vocabulary)
    for variable, _field, _accepted, fallback in _SPECIFICATION:
        if effective[variable] != fallback:
            raise RolloutConfigurationError(
                "ROLLOUT_ADAPTER_UNAVAILABLE",
                variable,
                effective[variable],
                f"{variable}={effective[variable]} has no registered P0 adapter",
            )
    return settings


def is_shadow_read_only(settings: RolloutSettings) -> bool:
    return settings.api_backend_mode == "shadow"


def assert_shadow_request_allowed(settings: RolloutSettings, method: str) -> None:
    normalized_method = str(method).upper()
    if is_shadow_read_only(settings) and normalized_method not in {"GET", "HEAD", "OPTIONS"}:
        raise RolloutConfigurationError(
            "SHADOW_MUTATION_FORBIDDEN",
            "API_BACKEND_MODE",
            settings.api_backend_mode,
            f"shadow mode cannot execute {normalized_method} requests",
        )


def rollout_to_environment(
    settings: RolloutSettings,
    *,
    vocabulary: str = P0_1_ROLLOUT_VOCABULARY,
) -> dict[str, str]:
    environment: dict[str, str] = {}
    for variable, field, _accepted, _fallback in _specification(vocabulary):
        value = getattr(settings, field)
        environment[variable] = (
            "1" if value else "0"
        ) if variable in _BOOLEAN_VARIABLES else str(value)
    return environment
