from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import json
import os
from pathlib import Path
from pathlib import PurePosixPath
import re
from typing import Any, Callable
import urllib.request
from uuid import uuid4

from backend.app.application.credentials import CredentialService
from backend.app.domain import (
    CredentialKind,
    CredentialStatus,
    EmbeddingProfile,
    ProviderProfile,
    ProviderRuntimeConfig,
)
from backend.app.providers.credentials.probe import SafeCredentialProbe


_SECRET_FIELDS: dict[CredentialKind, str] = {
    CredentialKind.LLM: "apiKey",
    CredentialKind.OCR: "ocrApiKey",
    CredentialKind.EMBEDDING: "embedApiKey",
    CredentialKind.SEMANTIC_SCHOLAR: "s2ApiKey",
}
_CLEAR_ALIASES: dict[str, CredentialKind] = {
    **{kind.value: kind for kind in CredentialKind},
    **{field: kind for kind, field in _SECRET_FIELDS.items()},
    "llmApiKey": CredentialKind.LLM,
    "ocrApiKey": CredentialKind.OCR,
    "embeddingApiKey": CredentialKind.EMBEDDING,
    "semanticScholarApiKey": CredentialKind.SEMANTIC_SCHOLAR,
}
_STRING_FIELDS = frozenset(
    {
        "provider",
        "baseUrl",
        "model",
        "ocrProvider",
        "ocrBaseUrl",
        "ocrApiBase",
        "ocrModel",
        "pdfTextProvider",
        "embedProvider",
        "embedApiBase",
        "embedApiModel",
        "s2Provider",
        "s2Endpoint",
        "s2ApiBase",
        "pdfDir",
        "explainerDir",
        "translationDir",
        "ocrMarkdownDir",
        "reproductionDir",
        "researchTheme",
        "translateMode",
    }
)
_INTEGER_FIELDS = frozenset(
    {"timeout", "llmTimeout", "ocrTimeout", "ocrPageBatchSize", "ocrMaxConcurrency", "explainMaxChars", "translateChunkSize", "translateMaxChars", "translateWorkers"}
)
_BOOLEAN_FIELDS = frozenset({"ocrEnabled", "translateSkipReferences"})
_OBSIDIAN_STRING_FIELDS = frozenset(
    {"obsidianVaultPath", "obsidianRootFolder", "obsidianPdfMode"}
)
_OBSIDIAN_BOOLEAN_FIELDS = frozenset(
    {
        "obsidianEnabled",
        "obsidianExportSource",
        "obsidianExportExplainer",
        "obsidianExportTranslation",
        "obsidianAutoExport",
    }
)
_STRING_FIELDS = _STRING_FIELDS | _OBSIDIAN_STRING_FIELDS
_BOOLEAN_FIELDS = _BOOLEAN_FIELDS | _OBSIDIAN_BOOLEAN_FIELDS
_KNOWN_NONSECRET_FIELDS = _STRING_FIELDS | _INTEGER_FIELDS | _BOOLEAN_FIELDS
_DIRECTORY_ENV_FIELDS = {
    "pdfDir": "PDF_DIR",
    "explainerDir": "EXPLAINER_DIR",
    "translationDir": "TRANSLATION_DIR",
    "ocrMarkdownDir": "OCR_MARKDOWN_DIR",
    "reproductionDir": "REPRODUCTION_DIR",
}
_LLM_PRESETS = {
    "deepseek": ("https://api.deepseek.com", "deepseek-v4-flash"),
    "qwen": ("https://dashscope.aliyuncs.com/compatible-mode/v1", "qwen-plus"),
    "openai": ("https://api.openai.com/v1", "gpt-4o-mini"),
    "anthropic": ("https://api.anthropic.com", "claude-3-5-sonnet-latest"),
}


@dataclass(frozen=True, slots=True)
class ObsidianSettings:
    enabled: bool
    vault_path: str
    root_folder: str
    pdf_mode: str
    export_source: bool
    export_explainer: bool
    export_translation: bool
    auto_export: bool

    def to_view(self) -> dict[str, object]:
        return {
            "obsidianEnabled": self.enabled,
            "obsidianVaultPath": self.vault_path,
            "obsidianRootFolder": self.root_folder,
            "obsidianPdfMode": self.pdf_mode,
            "obsidianExportSource": self.export_source,
            "obsidianExportExplainer": self.export_explainer,
            "obsidianExportTranslation": self.export_translation,
            "obsidianAutoExport": self.auto_export,
        }


class SettingsError(Exception):
    """A safe settings failure; it never carries input values or file bytes."""

    code = "SETTINGS_ERROR"
    public_message = "Settings could not be processed."
    http_status = 500

    def __init__(self, *, code: str | None = None, status: int | None = None) -> None:
        if code is not None:
            self.code = code
        if status is not None:
            self.http_status = status
        super().__init__(self.public_message)


class SettingsValidationError(SettingsError):
    code = "SETTINGS_INVALID"
    public_message = "The settings update is invalid."
    http_status = 422


class SettingsBackendError(SettingsError):
    code = "SETTINGS_BACKEND_ERROR"
    public_message = "The settings store is unavailable."
    http_status = 503


class SettingsService:
    """Application seam for provider profiles and legacy settings compatibility.

    The service owns JSON/profile handling while ``CredentialService`` owns all
    secret storage.  Only redacted status fields leave this boundary.
    """

    def __init__(
        self,
        *,
        settings_path: Path | str,
        credential_service: CredentialService,
        root: Path | str | None = None,
        environment_snapshot: Mapping[str, str] | None = None,
        rollout_snapshot: object | None = None,
        default_dirs: Mapping[str, Path | str] | None = None,
        llm_transport: Callable[[object, str], Any] | None = None,
    ) -> None:
        self.settings_path = Path(settings_path).expanduser().resolve()
        self.root = Path(root or self.settings_path.parent).expanduser().resolve()
        self.credential_service = credential_service
        self.environment = dict(environment_snapshot or {})
        self._obsidian_enabled_snapshot = (
            bool(getattr(rollout_snapshot, "obsidian_enabled"))
            if rollout_snapshot is not None
            else None
        )
        self.default_dirs = {
            "pdfDir": Path(
                (default_dirs or {}).get("pdfDir", self.root / "data" / "pdfs")
            ).expanduser(),
            "explainerDir": Path(
                (default_dirs or {}).get(
                    "explainerDir", self.root / "data" / "explainers"
                )
            ).expanduser(),
            "translationDir": Path(
                (default_dirs or {}).get(
                    "translationDir", self.root / "data" / "translations"
                )
            ).expanduser(),
            "ocrMarkdownDir": Path(
                (default_dirs or {}).get(
                    "ocrMarkdownDir", self.root / "data" / "ocr_markdown"
                )
            ).expanduser(),
            "reproductionDir": Path(
                (default_dirs or {}).get(
                    "reproductionDir", self.root / "data" / "reproduction-artifacts"
                )
            ).expanduser(),
        }
        self._llm_transport = llm_transport
        self._lock = asyncio.Lock()

    async def view(self) -> dict[str, object]:
        async with self._lock:
            return await self._view_unlocked()

    async def read(self) -> dict[str, object]:
        return await self.view()

    async def obsidian(self) -> ObsidianSettings:
        async with self._lock:
            return self._obsidian_from_document(self._read_document())

    async def update(self, patch: Mapping[str, object]) -> dict[str, object]:
        if not isinstance(patch, Mapping):
            raise SettingsValidationError()
        async with self._lock:
            document = self._read_document()
            normalized = self._validate_patch(patch)
            clear_kinds = _clear_kinds(patch)

            # Validate all values before touching either storage tier.
            secret_updates = self._secret_updates(patch)
            for kind, value in secret_updates.items():
                if value is not None and not isinstance(value, str):
                    raise SettingsValidationError()
                if value is not None and value.strip() and len(value) > 8192:
                    raise SettingsValidationError()
            for kind in clear_kinds:
                if not isinstance(kind, CredentialKind):
                    raise SettingsValidationError()

            # CredentialStore writes preserve the current JSON document.  Do
            # them first, then re-read before committing profile fields so a
            # credential update cannot be overwritten by a stale snapshot.
            for kind, value in secret_updates.items():
                if isinstance(value, str) and value.strip():
                    await self.credential_service.update(kind, value.strip())
            for kind in clear_kinds:
                await self.credential_service.clear(kind)

            current = self._read_document()
            current.update(normalized)
            self._obsidian_from_document(current)
            self._prepare_directories(current)
            self._write_document(current)
            return await self._view_unlocked()

    async def test_llm(self) -> dict[str, object]:
        result = await self.credential_service.test_connection(
            CredentialKind.LLM,
            probe=self._llm_probe(),
        )
        return _probe_payload(result)

    async def test_connection(self, kind: CredentialKind | str) -> dict[str, object]:
        normalized = CredentialKind(kind)
        probe = self._llm_probe() if normalized is CredentialKind.LLM else None
        return _probe_payload(
            await self.credential_service.test_connection(normalized, probe=probe)
        )

    def _llm_probe(self) -> SafeCredentialProbe | None:
        if self._llm_transport is not None:
            return SafeCredentialProbe(llm_transport=self._llm_transport)
        # Native runtime does not inject a transport; derive one from the current
        # LLM profile so "测试模型连接" works the same as the legacy Node flow.
        runtime = self.llm_runtime_settings()
        base_url = str(runtime.base_url or "").strip()
        if not base_url:
            return None
        return SafeCredentialProbe(
            llm_transport=_openai_compat_probe_transport(
                base_url,
                runtime.model,
                timeout_seconds=runtime.timeout_seconds,
            )
        )

    async def profile(self, kind: CredentialKind | str) -> ProviderProfile:
        normalized = CredentialKind(kind)
        async with self._lock:
            document = self._read_document()
            return self._profile_from_document(normalized, document)

    def llm_runtime_settings(self) -> ProviderRuntimeConfig:
        """Resolve the current non-secret LLM configuration for one request.

        The atomically replaced settings document is read on every call so a
        saved settings update applies to the next provider request without a
        process restart.  Blank saved fields are treated as unset, preserving
        the established saved -> environment -> provider-default precedence.
        Credentials deliberately stay behind ``CredentialService``.
        """

        return self._llm_runtime_from_document(self._read_document())

    def translation_mode(self) -> str:
        mode = _value(
            self._read_document(),
            "translateMode",
            self.environment,
            "TRANSLATE_MODE",
            "chunked",
        ).lower()
        return mode if mode in {"chunked", "full"} else "chunked"

    def _llm_runtime_from_document(
        self,
        document: Mapping[str, object],
    ) -> ProviderRuntimeConfig:
        provider = _value(
            document,
            "provider",
            self.environment,
            "LLM_PROVIDER",
            "deepseek",
        ).lower()
        default_base_url, default_model = _LLM_PRESETS.get(
            provider,
            _LLM_PRESETS["deepseek"],
        )
        model = _value(
            document,
            "model",
            self.environment,
            "LLM_MODEL",
            default_model,
        )
        base_url = _value(
            document,
            "baseUrl",
            self.environment,
            "LLM_BASE_URL",
            default_base_url,
        )
        timeout_ms = _aliased_integer_value(
            document,
            ("llmTimeout", "timeout"),
            self.environment,
            ("LLM_TIMEOUT",),
            0,
            allow_zero=True,
        )
        return ProviderRuntimeConfig(
            provider=provider,
            model=model,
            base_url=base_url,
            timeout_seconds=timeout_ms / 1000.0 if timeout_ms > 0 else None,
        )

    async def embedding_profile(
        self,
        baseline: EmbeddingProfile | None = None,
    ) -> EmbeddingProfile | None:
        """Resolve the current durable embedding identity from saved settings.

        Durable jobs persist their complete profile in the job spec, so this
        resolver is only used when creating the next job.  The runtime currently
        supports the local model2vec adapter; an ``api`` setting therefore
        fails closed instead of silently reusing the startup profile.
        """

        configured = await self.profile(CredentialKind.EMBEDDING)
        provider = configured.provider.strip().lower()
        if provider == "local":
            provider = "model2vec"
        if provider != "model2vec":
            return None

        reference = baseline
        model = configured.model.strip()
        if not model or model == "default":
            model = reference.model if reference is not None else "minishlab/potion-multilingual-128M"
        embedding_version = (
            reference.embedding_version if reference is not None else "model2vec-0.8.2"
        )
        dimensions = reference.dimensions if reference is not None else 256
        options = dict(reference.options) if reference is not None else {}
        try:
            return EmbeddingProfile(
                provider=provider,
                model=model,
                embedding_version=embedding_version,
                dimensions=dimensions,
                options=options,
            )
        except (TypeError, ValueError):
            return None

    def ocr_runtime_settings(
        self,
        *,
        fallback_enabled: bool | None = None,
    ) -> dict[str, object]:
        """Return the current non-secret OCR settings for request-time gates.

        The gate is synchronous because source enqueue validation runs before a
        job is persisted.  This method reads the atomically replaced settings
        document and never exposes credentials.
        """
        document = self._read_document()
        profile = self._profile_view(document)
        result = {
            key: profile[key]
            for key in (
                "ocrEnabled",
                "ocrProvider",
                "ocrModel",
                "ocrPageBatchSize",
                "ocrMaxConcurrency",
            )
        }
        if (
            fallback_enabled is not None
            and "ocrEnabled" not in document
            and "OCR_ENABLED" not in self.environment
        ):
            result["ocrEnabled"] = bool(fallback_enabled)
        return result

    async def credential_status(self, kind: CredentialKind | str) -> CredentialStatus:
        return await self.credential_service.status(CredentialKind(kind))

    async def _view_unlocked(self) -> dict[str, object]:
        document = self._read_document()
        statuses = {
            kind: await self.credential_service.status(kind) for kind in CredentialKind
        }
        view: dict[str, object] = {}
        view.update(self._profile_view(document))
        view.update(self._directory_view(document))
        view.update(self._obsidian_from_document(document).to_view())
        for kind, status in statuses.items():
            field = _SECRET_FIELDS[kind]
            status_names = {
                "apiKey": ("hasApiKey", "apiKeyTail"),
                "ocrApiKey": ("hasOcrKey", "ocrKeyTail"),
                "embedApiKey": ("hasEmbedKey", "embedKeyTail"),
                "s2ApiKey": ("hasS2Key", "s2KeyTail"),
            }[field]
            view[status_names[0]] = bool(status.has_key)
            view[status_names[1]] = status.key_tail or ""
            # Canonical status is useful to non-legacy callers but contains no
            # secret; the legacy adapter may omit it from its frozen DTO.
            view.setdefault("credentialStatus", {})
            assert isinstance(view["credentialStatus"], dict)
            view["credentialStatus"][kind.value] = status.to_dict()
        return view

    def _profile_view(self, document: Mapping[str, object]) -> dict[str, object]:
        env = self.environment
        llm_runtime = self._llm_runtime_from_document(document)
        return {
            "provider": llm_runtime.provider,
            "baseUrl": llm_runtime.base_url or "",
            "model": llm_runtime.model,
            "timeout": _integer_value(document, "timeout", env, "LLM_TIMEOUT", 60000),
            "ocrProvider": _value(document, "ocrProvider", env, "OCR_PROVIDER", ""),
            "ocrBaseUrl": _aliased_value(
                document,
                ("ocrBaseUrl", "ocrApiBase"),
                env,
                ("OCR_BASE_URL",),
                "",
            ),
            "ocrModel": _value(document, "ocrModel", env, "OCR_MODEL", ""),
            # PDF 文本提取方式：default=本地 pymupdf4llm（默认），ocr=OCR 模型 API。
            "pdfTextProvider": _value(document, "pdfTextProvider", env, "PDF_TEXT_PROVIDER", "default"),
            "ocrTimeout": _integer_value(document, "ocrTimeout", env, "OCR_TIMEOUT", 60000),
            "ocrEnabled": _boolean_value(document, "ocrEnabled", env, "OCR_ENABLED", False),
            "ocrPageBatchSize": _integer_value(
                document, "ocrPageBatchSize", env, "OCR_PAGE_BATCH_SIZE", 4
            ),
            "ocrMaxConcurrency": _integer_value(
                document, "ocrMaxConcurrency", env, "OCR_MAX_CONCURRENCY", 2
            ),
            "embedProvider": _value(document, "embedProvider", env, "EMBED_PROVIDER", "local"),
            "embedApiBase": _value(document, "embedApiBase", env, "EMBED_API_BASE", ""),
            "embedApiModel": _value(document, "embedApiModel", env, "EMBED_API_MODEL", ""),
            "s2Provider": _value(document, "s2Provider", env, "S2_PROVIDER", "semantic-scholar"),
            "explainMaxChars": _integer_value(
                document, "explainMaxChars", env, "EXPLAIN_MAX_CHARS", 120000
            ),
            "llmTimeout": (
                int(round(llm_runtime.timeout_seconds * 1000))
                if llm_runtime.timeout_seconds is not None
                else 0
            ),
            "translateMode": _value(document, "translateMode", env, "TRANSLATE_MODE", "chunked"),
            "translateChunkSize": _integer_value(
                document, "translateChunkSize", env, "TRANSLATE_CHUNK_SIZE", 3800
            ),
            "translateMaxChars": _integer_value(
                document, "translateMaxChars", env, "TRANSLATE_MAX_CHARS", 120000
            ),
            "translateWorkers": _integer_value(
                document, "translateWorkers", env, "TRANSLATE_WORKERS", 4
            ),
            "translateSkipReferences": _boolean_value(
                document, "translateSkipReferences", env, "TRANSLATE_SKIP_REFERENCES", True
            ),
            "s2Endpoint": _aliased_value(
                document,
                ("s2Endpoint", "s2ApiBase"),
                env,
                ("S2_ENDPOINT", "S2_API_BASE"),
                "https://api.semanticscholar.org/graph/v1",
            ),
        }

    def _directory_view(self, document: Mapping[str, object]) -> dict[str, object]:
        result: dict[str, object] = {}
        for field, environment_field in _DIRECTORY_ENV_FIELDS.items():
            rendered = _value(
                document,
                field,
                self.environment,
                environment_field,
                "",
            )
            default = self.default_dirs[field]
            result[field] = rendered
            result[f"default{field[0].upper()}{field[1:]}"] = _relative_to_root(
                self.root, default
            )
            resolved = _resolve_dir(self.root, rendered or default)
            result[f"resolved{field[0].upper()}{field[1:]}"] = str(resolved)
        result["researchTheme"] = (
            document.get("researchTheme")
            if isinstance(document.get("researchTheme"), str)
            else ""
        )
        return result

    def resolve_directory(self, field: str) -> Path:
        """Resolve a configured data directory for services initialized at startup."""
        if field not in _DIRECTORY_ENV_FIELDS:
            raise ValueError(f"unknown directory setting: {field}")
        document = self._read_document()
        return _resolve_dir(
            self.root,
            _value(document, field, self.environment, _DIRECTORY_ENV_FIELDS[field], "")
            or self.default_dirs[field],
        )

    def _obsidian_from_document(
        self, document: Mapping[str, object]
    ) -> ObsidianSettings:
        environment = self.environment
        enabled = (
            self._obsidian_enabled_snapshot
            if self._obsidian_enabled_snapshot is not None
            else _obsidian_boolean(
                document, environment, "obsidianEnabled", "OBSIDIAN_ENABLED", False
            )
        )
        vault_path = _obsidian_string(
            document, environment, "obsidianVaultPath", "OBSIDIAN_VAULT_PATH", ""
        )
        root_folder = _obsidian_string(
            document,
            environment,
            "obsidianRootFolder",
            "OBSIDIAN_ROOT_FOLDER",
            "Research",
        )
        pdf_mode = _obsidian_string(
            document, environment, "obsidianPdfMode", "OBSIDIAN_PDF_MODE", "none"
        )
        if vault_path and not Path(vault_path).expanduser().is_absolute():
            raise SettingsValidationError()
        _validate_obsidian_root_folder(root_folder)
        if pdf_mode not in {"none", "reference", "copy"}:
            raise SettingsValidationError()
        return ObsidianSettings(
            enabled=enabled,
            vault_path=vault_path,
            root_folder=root_folder,
            pdf_mode=pdf_mode,
            export_source=_obsidian_boolean(
                document,
                environment,
                "obsidianExportSource",
                "OBSIDIAN_EXPORT_SOURCE",
                True,
            ),
            export_explainer=_obsidian_boolean(
                document,
                environment,
                "obsidianExportExplainer",
                "OBSIDIAN_EXPORT_EXPLAINER",
                True,
            ),
            export_translation=_obsidian_boolean(
                document,
                environment,
                "obsidianExportTranslation",
                "OBSIDIAN_EXPORT_TRANSLATION",
                True,
            ),
            auto_export=_obsidian_boolean(
                document,
                environment,
                "obsidianAutoExport",
                "OBSIDIAN_AUTO_EXPORT",
                False,
            ),
        )

    def _profile_from_document(
        self, kind: CredentialKind, document: Mapping[str, object]
    ) -> ProviderProfile:
        if kind is CredentialKind.LLM:
            runtime = self._llm_runtime_from_document(document)
            return ProviderProfile(
                provider=runtime.provider,
                model=runtime.model,
                base_url=runtime.base_url,
            )
        if kind is CredentialKind.OCR:
            return ProviderProfile(
                provider=str(_value(document, "ocrProvider", self.environment, "OCR_PROVIDER", "default")),
                model=str(_value(document, "ocrModel", self.environment, "OCR_MODEL", "default")),
                base_url=_aliased_value(
                    document,
                    ("ocrBaseUrl", "ocrApiBase"),
                    self.environment,
                    ("OCR_BASE_URL",),
                    "",
                )
                or None,
            )
        if kind is CredentialKind.EMBEDDING:
            return ProviderProfile(
                provider=str(_value(document, "embedProvider", self.environment, "EMBED_PROVIDER", "local")),
                model=str(_value(document, "embedApiModel", self.environment, "EMBED_API_MODEL", "default")),
                base_url=str(_value(document, "embedApiBase", self.environment, "EMBED_API_BASE", "")) or None,
            )
        return ProviderProfile(
            provider=str(_value(document, "s2Provider", self.environment, "S2_PROVIDER", "semantic-scholar")),
            model=str(_value(document, "s2Endpoint", self.environment, "S2_ENDPOINT", "default")),
            base_url=str(_value(document, "s2ApiBase", self.environment, "S2_API_BASE", "")) or None,
        )

    def _validate_patch(self, patch: Mapping[str, object]) -> dict[str, object]:
        normalized: dict[str, object] = {}
        for key in _KNOWN_NONSECRET_FIELDS:
            if key not in patch:
                continue
            value = patch[key]
            if key in _STRING_FIELDS:
                if not isinstance(value, str):
                    raise SettingsValidationError()
                normalized[key] = value.strip()
            elif key in _INTEGER_FIELDS:
                if not isinstance(value, int) or isinstance(value, bool):
                    raise SettingsValidationError()
                minimum = 0 if key == "llmTimeout" else 1
                if value < minimum:
                    raise SettingsValidationError()
                normalized[key] = value
            else:
                if not isinstance(value, bool):
                    raise SettingsValidationError()
                normalized[key] = value
        # Keep the legacy wire's editable aliases in one canonical field.
        if "ocrApiBase" in normalized and "ocrBaseUrl" not in normalized:
            normalized["ocrBaseUrl"] = normalized.pop("ocrApiBase")
        if "s2ApiBase" in normalized and "s2Endpoint" not in normalized:
            normalized["s2Endpoint"] = normalized["s2ApiBase"]
        vault_path = normalized.get("obsidianVaultPath")
        if isinstance(vault_path, str) and vault_path:
            if not Path(vault_path).expanduser().is_absolute():
                raise SettingsValidationError()
        root_folder = normalized.get("obsidianRootFolder")
        if isinstance(root_folder, str):
            _validate_obsidian_root_folder(root_folder)
        pdf_mode = normalized.get("obsidianPdfMode")
        if isinstance(pdf_mode, str) and pdf_mode not in {
            "none",
            "reference",
            "copy",
        }:
            raise SettingsValidationError()
        translate_mode = normalized.get("translateMode")
        if isinstance(translate_mode, str) and translate_mode not in {
            "chunked",
            "full",
        }:
            raise SettingsValidationError()
        return normalized

    @staticmethod
    def _secret_updates(patch: Mapping[str, object]) -> dict[CredentialKind, object]:
        updates: dict[CredentialKind, object] = {}
        for kind, field in _SECRET_FIELDS.items():
            if field in patch:
                updates[kind] = patch[field]
        return updates

    def _read_document(self) -> dict[str, object]:
        if not self.settings_path.exists():
            return {}
        try:
            raw = self.settings_path.read_bytes()
            decoded = json.loads(raw.decode("utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            raise SettingsBackendError() from None
        if not isinstance(decoded, dict):
            raise SettingsBackendError()
        return dict(decoded)

    def _write_document(self, document: Mapping[str, object]) -> None:
        try:
            payload = (
                json.dumps(document, ensure_ascii=False, indent=2, sort_keys=False) + "\n"
            ).encode("utf-8")
            self.settings_path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.settings_path.parent / f".{self.settings_path.name}.{uuid4().hex}.tmp"
            descriptor = os.open(
                temporary,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o600,
            )
            try:
                with os.fdopen(descriptor, "wb") as handle:
                    descriptor = -1
                    handle.write(payload)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temporary, self.settings_path)
            finally:
                if descriptor >= 0:
                    os.close(descriptor)
                try:
                    temporary.unlink()
                except FileNotFoundError:
                    pass
        except SettingsError:
            raise
        except (OSError, TypeError, ValueError):
            raise SettingsBackendError() from None

    def _prepare_directories(self, document: Mapping[str, object]) -> None:
        try:
            for field in (
                "pdfDir",
                "explainerDir",
                "translationDir",
                "ocrMarkdownDir",
                "reproductionDir",
            ):
                value = document.get(field)
                if isinstance(value, str) and value.strip():
                    _resolve_dir(self.root, value).mkdir(parents=True, exist_ok=True)
        except OSError:
            raise SettingsBackendError() from None


def _clear_kinds(patch: Mapping[str, object]) -> tuple[CredentialKind, ...]:
    raw = patch.get("clearCredentials", patch.get("clear"))
    if raw is None:
        raw_values: list[object] = []
    elif isinstance(raw, Mapping):
        raw_values = [key for key, enabled in raw.items() if enabled is True]
    elif isinstance(raw, Sequence) and not isinstance(raw, (str, bytes, bytearray)):
        raw_values = list(raw)
    else:
        raise SettingsValidationError()
    for kind, field in _SECRET_FIELDS.items():
        if patch.get(f"clear{field[0].upper()}{field[1:]}") is True:
            raw_values.append(kind.value)
    resolved: list[CredentialKind] = []
    for value in raw_values:
        if not isinstance(value, str) or value not in _CLEAR_ALIASES:
            raise SettingsValidationError()
        kind = _CLEAR_ALIASES[value]
        if kind not in resolved:
            resolved.append(kind)
    return tuple(resolved)


def _value(
    document: Mapping[str, object],
    key: str,
    environment: Mapping[str, str],
    env_key: str,
    default: str,
) -> str:
    value = document.get(key)
    if isinstance(value, str) and value.strip():
        return value.strip()
    env_value = environment.get(env_key)
    if isinstance(env_value, str) and env_value.strip():
        return env_value.strip()
    return default


def _aliased_value(
    document: Mapping[str, object],
    keys: Sequence[str],
    environment: Mapping[str, str],
    env_keys: Sequence[str],
    default: str,
) -> str:
    """Resolve renamed settings without letting env outrank a saved alias."""
    for key in keys:
        value = document.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    for env_key in env_keys:
        value = environment.get(env_key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return default


def _integer_value(
    document: Mapping[str, object],
    key: str,
    environment: Mapping[str, str],
    env_key: str,
    default: int,
) -> int:
    allow_zero = key == "llmTimeout"
    value = document.get(key)
    if (
        isinstance(value, int)
        and not isinstance(value, bool)
        and (value > 0 or (allow_zero and value == 0))
    ):
        return value
    env_value = environment.get(env_key)
    if isinstance(env_value, str) and env_value.strip():
        try:
            parsed = int(env_value)
            if parsed > 0 or (allow_zero and parsed == 0):
                return parsed
        except ValueError:
            pass
    return default


def _aliased_integer_value(
    document: Mapping[str, object],
    keys: Sequence[str],
    environment: Mapping[str, str],
    env_keys: Sequence[str],
    default: int,
    *,
    allow_zero: bool = False,
) -> int:
    """Resolve integer aliases with saved > environment > default priority."""

    def accepted(value: object) -> int | None:
        if isinstance(value, bool):
            return None
        if isinstance(value, int):
            parsed = value
        elif isinstance(value, str) and value.strip():
            try:
                parsed = int(value)
            except ValueError:
                return None
        else:
            return None
        if parsed > 0 or (allow_zero and parsed == 0):
            return parsed
        return None

    for key in keys:
        parsed = accepted(document.get(key))
        if parsed is not None:
            return parsed
    for env_key in env_keys:
        parsed = accepted(environment.get(env_key))
        if parsed is not None:
            return parsed
    return default


def _boolean_value(
    document: Mapping[str, object],
    key: str,
    environment: Mapping[str, str],
    env_key: str,
    default: bool,
) -> bool:
    value = document.get(key)
    if isinstance(value, bool):
        return value
    env_value = environment.get(env_key)
    if isinstance(env_value, str):
        normalized = env_value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
    return default


def _resolve_dir(root: Path, value: Path | str) -> Path:
    path = Path(value).expanduser()
    return path if path.is_absolute() else (root / path).resolve()


def _obsidian_string(
    document: Mapping[str, object],
    environment: Mapping[str, str],
    key: str,
    env_key: str,
    default: str,
) -> str:
    if env_key in environment:
        value = environment[env_key]
        if not isinstance(value, str):
            raise SettingsValidationError()
        return value.strip()
    if key in document:
        value = document[key]
        if not isinstance(value, str):
            raise SettingsValidationError()
        return value.strip()
    return default


def _obsidian_boolean(
    document: Mapping[str, object],
    environment: Mapping[str, str],
    key: str,
    env_key: str,
    default: bool,
) -> bool:
    if env_key in environment:
        value = environment[env_key]
        if value == "1":
            return True
        if value == "0":
            return False
        raise SettingsValidationError()
    if key in document:
        value = document[key]
        if not isinstance(value, bool):
            raise SettingsValidationError()
        return value
    return default


def _validate_obsidian_root_folder(value: str) -> None:
    if (
        not value
        or value in {".", ".."}
        or "\\" in value
        or ":" in value
        or value.startswith("/")
        or any(part == "" for part in value.split("/"))
        or re.search(r"[\x00-\x1f\x7f]", value)
    ):
        raise SettingsValidationError()
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise SettingsValidationError()


def _relative_to_root(root: Path, value: Path) -> str:
    try:
        return str(value.resolve().relative_to(root.resolve()))
    except ValueError:
        return str(value.resolve())


def _openai_compat_probe_transport(
    base_url: str,
    model: str,
    *,
    timeout_seconds: float | None = None,
) -> Callable[[Any, str], Any]:
    """Probe transport: send the fixture prompt to an OpenAI-compatible
    /chat/completions endpoint using the stored credential.  Truthy return
    means the connection works; any exception is mapped to probe failure."""
    endpoint = base_url.rstrip("/") + "/chat/completions"

    async def transport(credential: Any, prompt: str) -> bool:
        def call() -> bool:
            payload = {
                "model": model,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0,
                "max_tokens": 64,
            }
            request = urllib.request.Request(
                endpoint,
                data=json.dumps(payload).encode("utf-8"),
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {credential.value}",
                },
                method="POST",
            )
            if timeout_seconds is None:
                response_context = urllib.request.urlopen(request)
            else:
                response_context = urllib.request.urlopen(
                    request,
                    timeout=max(0.001, float(timeout_seconds)),
                )
            with response_context as response:
                document = json.loads(response.read().decode("utf-8"))
            return bool(document.get("choices"))

        return await asyncio.to_thread(call)

    return transport


def _probe_payload(result: Any) -> dict[str, object]:
    if bool(getattr(result, "ok", False)):
        return {"ok": True, "output": "STUDY_APP_CREDENTIAL_OK."}
    message = getattr(result, "message", None)
    return {"ok": False, "output": str(message) if isinstance(message, str) else ""}


__all__ = [
    "ObsidianSettings",
    "SettingsBackendError",
    "SettingsError",
    "SettingsService",
    "SettingsValidationError",
]
