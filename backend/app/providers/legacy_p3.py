from __future__ import annotations

import asyncio
import json
from pathlib import Path
from collections.abc import Mapping
from typing import Any, Awaitable, Callable

from backend.app.application.ports.structured_artifact_provider import (
    StructuredArtifactRequest,
)
from backend.app.application.ports.translation_provider import TranslationRequest
from backend.app.domain import (
    Credential,
    CredentialKind,
    ProviderRuntimeConfig,
    TranslationProviderRequestError,
    WorkerConfigurationError,
)


CredentialResolver = Callable[
    [CredentialKind], Awaitable[Credential | None]
]
RuntimeConfigResolver = Callable[[], ProviderRuntimeConfig]


class LegacyChunkTranslationProvider:
    """Adapt the configured legacy LLM at the P3 chunk boundary."""

    def __init__(
        self,
        provider_id: str | Callable[[], tuple[str, str]],
        model_id: str | None = None,
        prompt_version: str = "translation-chunk-v1",
        *,
        credential_resolver: CredentialResolver | None = None,
        runtime_config_resolver: RuntimeConfigResolver | None = None,
    ) -> None:
        self._identity = _identity_resolver(provider_id, model_id)
        self._credential_resolver = credential_resolver
        self._runtime_config_resolver = runtime_config_resolver
        self.prompt_version = prompt_version

    def bind_credential_resolver(self, resolver: CredentialResolver) -> None:
        self._credential_resolver = resolver

    def bind_runtime_config_resolver(self, resolver: RuntimeConfigResolver) -> None:
        self._runtime_config_resolver = resolver

    @property
    def provider_id(self) -> str:
        return self._identity()[0]

    @property
    def model_id(self) -> str:
        return self._identity()[1]

    async def translate(self, request: TranslationRequest) -> str:
        if not isinstance(request, TranslationRequest):
            raise TypeError("translation request must be TranslationRequest")
        try:
            runtime = self._runtime_config_resolver() if self._runtime_config_resolver else None
            provider_id, model_id = (
                (runtime.provider, runtime.model) if runtime is not None else self._identity()
            )
            credential = (
                await self._credential_resolver(CredentialKind.LLM)
                if self._credential_resolver is not None
                else None
            )
            translated = await asyncio.to_thread(
                _translate_markdown,
                request.markdown,
                provider_id,
                model_id,
                api_key=credential.value if credential is not None else None,
                base_url=runtime.base_url if runtime is not None else None,
                timeout_seconds=runtime.timeout_seconds if runtime is not None else None,
                validate_identity=runtime is None,
            )
        except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
            raise
        except WorkerConfigurationError:
            raise TranslationProviderRequestError(retryable=False) from None
        except Exception:
            raise TranslationProviderRequestError(retryable=True) from None
        if not isinstance(translated, str) or not translated.strip():
            raise TranslationProviderRequestError(retryable=True)
        return translated


class LegacyStructuredArtifactProvider:
    """Render only the audited P3 request into a structured LLM call."""

    def __init__(
        self,
        provider_id: str | Callable[[], tuple[str, str]],
        model_id: str | None = None,
        *,
        credential_resolver: CredentialResolver | None = None,
        runtime_config_resolver: RuntimeConfigResolver | None = None,
    ) -> None:
        self._identity = _identity_resolver(provider_id, model_id)
        self._credential_resolver = credential_resolver
        self._runtime_config_resolver = runtime_config_resolver

    def bind_credential_resolver(self, resolver: CredentialResolver) -> None:
        self._credential_resolver = resolver

    def bind_runtime_config_resolver(self, resolver: RuntimeConfigResolver) -> None:
        self._runtime_config_resolver = resolver

    @property
    def provider_id(self) -> str:
        return self._identity()[0]

    @property
    def model_id(self) -> str:
        return self._identity()[1]

    async def generate(self, request: StructuredArtifactRequest) -> str:
        if not isinstance(request, StructuredArtifactRequest):
            raise TypeError("structured request must be StructuredArtifactRequest")
        system_prompt, payload = _structured_prompt(request)
        try:
            runtime = self._runtime_config_resolver() if self._runtime_config_resolver else None
            provider_id, model_id = (
                (runtime.provider, runtime.model) if runtime is not None else self._identity()
            )
            credential = (
                await self._credential_resolver(CredentialKind.LLM)
                if self._credential_resolver is not None
                else None
            )
            output = await asyncio.to_thread(
                _generate_structured_json,
                system_prompt,
                payload,
                provider_id,
                model_id,
                api_key=credential.value if credential is not None else None,
                base_url=runtime.base_url if runtime is not None else None,
                timeout_seconds=runtime.timeout_seconds if runtime is not None else None,
                validate_identity=runtime is None,
            )
        except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
            raise
        if not isinstance(output, str) or not output.strip():
            raise RuntimeError("structured provider returned empty output")
        return output


def legacy_p3_provider_factories(
    environment: Mapping[str, str],
    *,
    credential_resolver: CredentialResolver | None = None,
) -> tuple[Any, Any]:
    runtime_resolver = legacy_p3_runtime_config_resolver(environment)
    resolver = lambda: (
        runtime_resolver().provider,
        runtime_resolver().model,
    )
    return (
        lambda: LegacyChunkTranslationProvider(
            resolver,
            credential_resolver=credential_resolver,
            runtime_config_resolver=runtime_resolver,
        ),
        lambda: LegacyStructuredArtifactProvider(
            resolver,
            credential_resolver=credential_resolver,
            runtime_config_resolver=runtime_resolver,
        ),
    )


def _identity_resolver(
    provider_id: str | Callable[[], tuple[str, str]],
    model_id: str | None,
) -> Callable[[], tuple[str, str]]:
    if callable(provider_id):
        if model_id is not None:
            raise TypeError("model_id cannot accompany an identity resolver")
        return provider_id
    return lambda: (
        _nonblank(provider_id, "LLM_PROVIDER").lower(),
        _nonblank(model_id, "LLM_MODEL"),
    )


def legacy_p3_runtime_config_resolver(
    environment: Mapping[str, str],
) -> RuntimeConfigResolver:
    presets = {
        "deepseek": ("https://api.deepseek.com", "deepseek-v4-flash"),
        "qwen": ("https://dashscope.aliyuncs.com/compatible-mode/v1", "qwen-plus"),
        "openai": ("https://api.openai.com/v1", "gpt-4o-mini"),
        "anthropic": ("https://api.anthropic.com", "claude-3-5-sonnet-latest"),
    }
    settings_path = environment.get("PAPER_STUDY_SETTINGS_PATH", "").strip()

    def resolve() -> _RuntimeConfig:
        document: dict[str, object] = {}
        if settings_path:
            try:
                decoded = json.loads(Path(settings_path).expanduser().read_text(encoding="utf-8"))
                if isinstance(decoded, dict):
                    document = decoded
            except (OSError, UnicodeError, json.JSONDecodeError):
                pass
        provider = str(document.get("provider") or environment.get("LLM_PROVIDER") or "deepseek").strip().lower()
        base, default_model = presets.get(provider, presets["deepseek"])
        model = str(document.get("model") or environment.get("LLM_MODEL") or default_model).strip()
        base_url = str(document.get("baseUrl") or environment.get("LLM_BASE_URL") or base).strip()
        raw_timeout = document.get("llmTimeout", environment.get("LLM_TIMEOUT", 0))
        try:
            timeout_ms = int(raw_timeout or 0)
        except (TypeError, ValueError):
            timeout_ms = 0
        return ProviderRuntimeConfig(
            provider=_nonblank(provider, "LLM_PROVIDER"),
            model=_nonblank(model, "LLM_MODEL"),
            base_url=_nonblank(base_url, "LLM_BASE_URL"),
            timeout_seconds=timeout_ms / 1000 if timeout_ms > 0 else None,
        )

    return resolve


def _translate_markdown(
    markdown: str,
    provider_id: str,
    model_id: str,
    *,
    api_key: str | None = None,
    base_url: str | None = None,
    timeout_seconds: float | None = None,
    validate_identity: bool = True,
) -> str:
    # Importing the legacy client is delayed until a claimed provider call.
    from agent import llm

    if validate_identity:
        _validate_legacy_identity(llm, provider_id=provider_id, model_id=model_id)
    client_options = {
        key: value
        for key, value in {
            "api_key": api_key,
            "base_url": base_url,
            "timeout": timeout_seconds,
        }.items()
        if value is not None
    }
    source_english = sum(
        1 for character in markdown if character.isascii() and character.isalpha()
    )
    user_content = markdown
    for attempt in range(3):
        try:
            response = llm.client(**client_options).chat.completions.create(
                model=model_id,
                messages=(
                    {"role": "system", "content": llm.TRANSLATE_SYSTEM},
                    {"role": "user", "content": user_content},
                ),
                temperature=0.2 if attempt == 0 else 0.4,
            )
            output = str(response.choices[0].message.content or "").strip()
        except Exception:
            continue
        if not output or (source_english and output == markdown.strip()):
            continue
        if source_english < 40 or _cjk_ratio(output) >= 0.25:
            return output
        user_content = (
            "下面是英文论文片段，请完整翻译成简体中文，不要原样返回英文：\n\n"
            + markdown
        )
    raise TranslationProviderRequestError(retryable=True)


def _cjk_ratio(value: str) -> float:
    cjk = sum(1 for character in value if "\u4e00" <= character <= "\u9fff")
    english = sum(
        1 for character in value if character.isascii() and character.isalpha()
    )
    total = cjk + english
    return cjk / total if total else 1.0


def _generate_structured_json(
    system_prompt: str,
    payload: str,
    provider_id: str,
    model_id: str,
    *,
    api_key: str | None = None,
    base_url: str | None = None,
    timeout_seconds: float | None = None,
    validate_identity: bool = True,
) -> str:
    # Startup, health, lexical search, and idle workers never construct a client.
    from agent import llm

    if validate_identity:
        _validate_legacy_identity(llm, provider_id=provider_id, model_id=model_id)
    client_options = {
        key: value
        for key, value in {
            "api_key": api_key,
            "base_url": base_url,
            "timeout": timeout_seconds,
        }.items()
        if value is not None
    }
    response = llm.client(**client_options).chat.completions.create(
        model=model_id,
        messages=(
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": payload},
        ),
        response_format={"type": "json_object"},
        temperature=0.2,
    )
    return str(response.choices[0].message.content or "").strip()


def _validate_legacy_identity(
    llm: Any,
    *,
    provider_id: str,
    model_id: str,
) -> None:
    if (
        str(getattr(llm.config, "PROVIDER", "")).strip().lower() != provider_id
        or str(getattr(llm.config, "MODEL", "")).strip() != model_id
    ):
        raise WorkerConfigurationError()


def _structured_prompt(request: StructuredArtifactRequest) -> tuple[str, str]:
    ranges, content = _request_content(request)
    schemas = {
        "classification": (
            '{"type":"...","topic":"...","task":"...","models":[],"datasets":[], '
            '"tags":[],"relevance":0.0}'
        ),
        "metadata": (
            '{"title":"...","titleZh":null,"authors":[],"venue":null,"year":null,'
            '"abstract":null,"arxivId":null,"doi":null}'
        ),
        "summary-map": '{"coveredRanges":[[0,1]],"summary":"..."}',
        "summary-final": (
            '{"coveredRanges":[[0,1]],"tldr":"...","contribution":"..."}'
        ),
        "explainer": '{"coveredRanges":[[0,1]],"markdown":"..."}',
    }
    if request.kind == "summary":
        schema = schemas["summary-map" if request.stage == "map" else "summary-final"]
    else:
        schema = schemas[request.kind]
    system = (
        "You produce one strict JSON object for a research-paper artifact. "
        "Return no prose or code fence. Preserve coveredRanges exactly as supplied. "
        f"The required shape is {schema}."
    )
    payload = json.dumps(
        {
            "kind": request.kind,
            "stage": request.stage,
            "profile": request.profile,
            "promptVersion": request.prompt_version,
            "paper": {
                "id": request.paper_id,
                "title": request.paper_title,
                "authors": list(request.paper_authors),
            },
            "coveredRanges": [list(item) for item in ranges],
            "content": content,
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return system, payload


def _request_content(
    request: StructuredArtifactRequest,
) -> tuple[tuple[tuple[int, int], ...], str]:
    if request.stage == "direct":
        assert request.plan is not None
        batches = request.plan.batches
        ranges = tuple(item for batch in batches for item in batch.covered_ranges)
        content = "".join(
            chunk.content for batch in batches for chunk in batch.chunks
        )
        return ranges, content
    if request.stage == "map":
        assert request.batch is not None
        return request.batch.covered_ranges, "".join(
            chunk.content for chunk in request.batch.chunks
        )
    return (
        tuple(item for child in request.inputs for item in child.covered_ranges),
        "\n\n".join(child.content for child in request.inputs),
    )


def _nonblank(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be nonblank")
    return value.strip()


__all__ = [
    "LegacyChunkTranslationProvider",
    "LegacyStructuredArtifactProvider",
    "legacy_p3_provider_factories",
]
