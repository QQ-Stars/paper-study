from __future__ import annotations

from collections.abc import Callable
from typing import Any

from backend.app.rollout import RolloutConfigurationError, RolloutSettings


def extract_with_ocr_gate(
    source: Any,
    *,
    native_extract: Callable[[Any], str],
    ocr_provider_factory: Callable[[], Any],
    rollout: RolloutSettings,
) -> str:
    native_result = native_extract(source)
    if not rollout.ocr_enabled:
        return native_result
    raise RolloutConfigurationError(
        "ROLLOUT_ADAPTER_UNAVAILABLE",
        "OCR_ENABLED",
        "1",
        "OCR_ENABLED=1 has no registered P0 adapter",
    )
