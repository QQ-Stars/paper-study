from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from backend.app.application.ports.structured_artifact_provider import (
    StructuredArtifactRequest,
)
from backend.app.application.ports.translation_provider import TranslationRequest
from backend.app.domain import TranslationProviderRequestError, WorkerConfigurationError


@dataclass(frozen=True, slots=True)
class LegacyChunkTranslationProvider:
    """Adapt the configured legacy LLM at the P3 chunk boundary."""

    provider_id: str
    model_id: str
    prompt_version: str = "translation-chunk-v1"

    async def translate(self, request: TranslationRequest) -> str:
        if not isinstance(request, TranslationRequest):
            raise TypeError("translation request must be TranslationRequest")
        try:
            translated = await asyncio.to_thread(
                _translate_markdown,
                request.markdown,
                self.provider_id,
                self.model_id,
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


@dataclass(frozen=True, slots=True)
class LegacyStructuredArtifactProvider:
    """Render only the audited P3 request into a structured LLM call."""

    provider_id: str
    model_id: str

    async def generate(self, request: StructuredArtifactRequest) -> str:
        if not isinstance(request, StructuredArtifactRequest):
            raise TypeError("structured request must be StructuredArtifactRequest")
        system_prompt, payload = _structured_prompt(request)
        try:
            output = await asyncio.to_thread(
                _generate_structured_json,
                system_prompt,
                payload,
                self.provider_id,
                self.model_id,
            )
        except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
            raise
        if not isinstance(output, str) or not output.strip():
            raise RuntimeError("structured provider returned empty output")
        return output


def legacy_p3_provider_factories(
    environment: Mapping[str, str],
) -> tuple[Any, Any]:
    provider_id = _nonblank(
        environment.get("LLM_PROVIDER", "deepseek"),
        "LLM_PROVIDER",
    ).lower()
    model_id = _nonblank(
        environment.get("LLM_MODEL", "deepseek-v4-flash"),
        "LLM_MODEL",
    )
    return (
        lambda: LegacyChunkTranslationProvider(provider_id, model_id),
        lambda: LegacyStructuredArtifactProvider(provider_id, model_id),
    )


def _translate_markdown(markdown: str, provider_id: str, model_id: str) -> str:
    # Importing the legacy client is delayed until a claimed provider call.
    from agent import llm

    _validate_legacy_identity(llm, provider_id=provider_id, model_id=model_id)
    source_english = sum(
        1 for character in markdown if character.isascii() and character.isalpha()
    )
    user_content = markdown
    for attempt in range(3):
        try:
            response = llm.client().chat.completions.create(
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
) -> str:
    # Startup, health, lexical search, and idle workers never construct a client.
    from agent import llm

    _validate_legacy_identity(llm, provider_id=provider_id, model_id=model_id)
    response = llm.client().chat.completions.create(
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
