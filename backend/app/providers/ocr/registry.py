from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

from backend.app.domain import (
    OcrDisabledError,
    OcrProviderContractUnverifiedError,
    OcrProviderUnknownError,
    OcrRequestInvalidError,
)


@dataclass(frozen=True, slots=True)
class OcrProviderGate:
    enabled: bool
    registry: Any = None

    def select(
        self,
        *,
        source_mode: str,
        provider_id: str | None,
        model: str | None,
        options: Mapping[str, object] | None,
    ) -> OcrProviderSelection | None:
        if source_mode == "ocr" and not self.enabled:
            raise OcrDisabledError(source_mode=source_mode)
        if source_mode == "native":
            if provider_id is not None or model is not None or options is not None:
                raise OcrRequestInvalidError(source_mode=source_mode)
            return None
        if source_mode != "ocr":
            raise OcrRequestInvalidError(source_mode=source_mode)
        if not isinstance(provider_id, str) or not provider_id.strip():
            raise OcrRequestInvalidError(source_mode=source_mode)
        if not isinstance(model, str) or not model.strip():
            raise OcrRequestInvalidError(source_mode=source_mode)
        normalized_options = _normalize_options(options)
        provider = self.registry.resolve(provider_id)
        return OcrProviderSelection(
            provider=provider,
            provider_id=provider_id,
            model=model,
            options=MappingProxyType(normalized_options),
            page_batch_size=normalized_options["pageBatchSize"],
            max_concurrency=normalized_options["maxConcurrency"],
        )


@dataclass(frozen=True, slots=True)
class OcrProviderSelection:
    provider: Any
    provider_id: str
    model: str
    options: Mapping[str, object]
    page_batch_size: int
    max_concurrency: int


def _normalize_options(options: Mapping[str, object] | None) -> dict[str, int]:
    if options is None:
        normalized: dict[str, object] = {}
    elif isinstance(options, Mapping):
        normalized = dict(options)
    else:
        raise OcrRequestInvalidError(source_mode="ocr")
    if not set(normalized).issubset({"pageBatchSize", "maxConcurrency"}):
        raise OcrRequestInvalidError(source_mode="ocr")
    normalized.setdefault("pageBatchSize", 1)
    normalized.setdefault("maxConcurrency", 1)
    bounds = {"pageBatchSize": (1, 16), "maxConcurrency": (1, 4)}
    for name, (minimum, maximum) in bounds.items():
        value = normalized[name]
        if not isinstance(value, int) or isinstance(value, bool) or not minimum <= value <= maximum:
            raise OcrRequestInvalidError(source_mode="ocr")
    return {name: int(normalized[name]) for name in ("pageBatchSize", "maxConcurrency")}


def compose_ocr_gate(
    *,
    enabled: bool,
    registry_factory: Callable[[], Any] | None = None,
) -> OcrProviderGate:
    registry = (registry_factory or create_production_ocr_registry)() if enabled else None
    return OcrProviderGate(enabled=enabled, registry=registry)


@dataclass(frozen=True, slots=True)
class ProductionOcrRegistry:
    provider_ids: frozenset[str] = frozenset({"deepseek"})

    def resolve(self, provider_id: str) -> Any:
        if provider_id not in self.provider_ids:
            raise OcrProviderUnknownError()
        # No provider object, credential store, or transport may be touched until
        # the separately versioned provider-contract gate is satisfied.
        raise OcrProviderContractUnverifiedError()


def create_production_ocr_registry() -> ProductionOcrRegistry:
    return ProductionOcrRegistry()


@dataclass(frozen=True, slots=True)
class TestOcrRegistry:
    providers: Mapping[str, Any]

    @property
    def provider_ids(self) -> frozenset[str]:
        return frozenset(self.providers)

    def resolve(self, provider_id: str) -> Any:
        try:
            return self.providers[provider_id]
        except KeyError as error:
            raise OcrProviderUnknownError() from error


def create_test_ocr_registry(providers: Mapping[str, Any]) -> TestOcrRegistry:
    copied = dict(providers)
    if set(copied) != {"fake"} or getattr(copied["fake"], "provider_id", None) != "fake":
        raise ValueError("test OCR registry must contain only a fake provider")
    return TestOcrRegistry(MappingProxyType(copied))
