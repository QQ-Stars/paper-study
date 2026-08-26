from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

from backend.app.domain import (
    OcrDisabledError,
    OcrUnavailableError,
    OcrProviderContractUnverifiedError,
    OcrProviderUnknownError,
    OcrRequestInvalidError,
)


@dataclass(slots=True)
class OcrProviderGate:
    enabled: bool
    registry: Any = None
    registry_factory: Callable[[], Any] | None = None
    settings_resolver: Callable[[], Mapping[str, object]] | None = None

    def select(
        self,
        *,
        source_mode: str,
        provider_id: str | None,
        model: str | None,
        options: Mapping[str, object] | None,
    ) -> OcrProviderSelection | None:
        if source_mode == "native":
            if provider_id is not None or model is not None or options is not None:
                raise OcrRequestInvalidError(source_mode=source_mode)
            return None
        if source_mode != "ocr":
            raise OcrRequestInvalidError(source_mode=source_mode)
        runtime = self._runtime_settings()
        enabled = self.enabled
        if runtime is not None:
            enabled = _runtime_bool(runtime, "ocrEnabled", enabled)
        if not enabled:
            raise OcrDisabledError(source_mode=source_mode)
        if runtime is not None:
            # The UI normally omits these fields and lets the saved profile
            # supply them.  Explicit request values remain supported for
            # controlled callers/tests and preserve the v2 request contract.
            provider_id = provider_id or _runtime_text(runtime, "ocrProvider")
            model = model or _runtime_text(runtime, "ocrModel")
        if not isinstance(provider_id, str) or not provider_id.strip():
            raise OcrRequestInvalidError(source_mode=source_mode)
        if not isinstance(model, str) or not model.strip():
            raise OcrRequestInvalidError(source_mode=source_mode)
        defaults = (
            {
                "pageBatchSize": runtime.get("ocrPageBatchSize", 4),
                "maxConcurrency": runtime.get("ocrMaxConcurrency", 2),
            }
            if runtime is not None
            else None
        )
        normalized_options = _normalize_options(options, defaults=defaults)
        registry = self._registry_for(enabled)
        provider = registry.resolve(provider_id)
        return OcrProviderSelection(
            provider=provider,
            provider_id=provider_id,
            model=model,
            options=MappingProxyType(normalized_options),
            page_batch_size=normalized_options["pageBatchSize"],
            max_concurrency=normalized_options["maxConcurrency"],
        )

    def _runtime_settings(self) -> Mapping[str, object] | None:
        if self.settings_resolver is None:
            return None
        try:
            value = self.settings_resolver()
        except Exception:
            raise OcrUnavailableError() from None
        if not isinstance(value, Mapping):
            raise OcrUnavailableError()
        return value

    def _registry_for(self, enabled: bool) -> Any:
        if self.registry is None:
            if not enabled or self.registry_factory is None:
                raise OcrUnavailableError()
            try:
                self.registry = self.registry_factory()
            except Exception:
                raise OcrUnavailableError() from None
        return self.registry


@dataclass(frozen=True, slots=True)
class OcrProviderSelection:
    provider: Any
    provider_id: str
    model: str
    options: Mapping[str, object]
    page_batch_size: int
    max_concurrency: int


def _normalize_options(
    options: Mapping[str, object] | None,
    *,
    defaults: Mapping[str, object] | None = None,
) -> dict[str, int]:
    if options is None:
        normalized: dict[str, object] = {}
    elif isinstance(options, Mapping):
        normalized = dict(options)
    else:
        raise OcrRequestInvalidError(source_mode="ocr")
    if not set(normalized).issubset({"pageBatchSize", "maxConcurrency"}):
        raise OcrRequestInvalidError(source_mode="ocr")
    fallback = defaults if isinstance(defaults, Mapping) else {}
    normalized.setdefault("pageBatchSize", fallback.get("pageBatchSize", 1))
    normalized.setdefault("maxConcurrency", fallback.get("maxConcurrency", 1))
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
    settings_resolver: Callable[[], Mapping[str, object]] | None = None,
) -> OcrProviderGate:
    factory = registry_factory or create_production_ocr_registry
    # A runtime resolver may override the startup enablement, so defer registry
    # construction until the first enabled OCR request in that mode.
    registry = factory() if enabled and settings_resolver is None else None
    return OcrProviderGate(
        enabled=enabled,
        registry=registry,
        registry_factory=factory,
        settings_resolver=settings_resolver,
    )


def _runtime_bool(runtime: Mapping[str, object], key: str, fallback: bool) -> bool:
    value = runtime.get(key)
    return value if isinstance(value, bool) else fallback


def _runtime_text(runtime: Mapping[str, object], key: str) -> str | None:
    value = runtime.get(key)
    return value.strip() if isinstance(value, str) and value.strip() else None


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
