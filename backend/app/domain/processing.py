from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
import hashlib
import json
import re
from types import MappingProxyType
from typing import Mapping, TypeAlias


MAX_JOB_SPEC_BYTES = 4 * 1024 * 1024
MAX_JOB_PROGRESS_BYTES = 512
_TOP_LEVEL_KEYS = frozenset(
    {"arguments", "jobType", "paperId", "schemaVersion", "sourceMode", "target"}
)
_TARGET_KEYS = frozenset({"artifactId", "sourceDocumentId"})
_SENSITIVE_KEYS = frozenset(
    {
        "apikey", "authorization", "cookie", "credential", "credentials", "headers",
        "markdown", "pdf", "prompt", "rawrequest", "rawresponse", "leasetoken",
    }
)
_JOB_PROGRESS_KEYS = frozenset(
    {"phase", "stage", "completed", "total", "pagesCompleted", "pagesTotal"}
)
_JOB_PROGRESS_LABEL_KEYS = frozenset({"phase", "stage"})
_JOB_PROGRESS_COUNT_KEYS = _JOB_PROGRESS_KEYS - _JOB_PROGRESS_LABEL_KEYS
_JOB_PROGRESS_LABEL = re.compile(r"[A-Za-z][A-Za-z0-9_-]{0,63}\Z")
_OCR_OPTION_KEYS = frozenset({"language", "pageBatchSize", "maxConcurrency"})
_OCR_LANGUAGE = re.compile(r"[A-Za-z]{2,8}(?:-[A-Za-z0-9]{1,8}){0,3}\Z")
_MAX_OCR_OPTIONS_BYTES = 256
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_OBSIDIAN_SETTINGS_KEYS = frozenset(
    {
        "vaultPath",
        "rootFolder",
        "pdfMode",
        "enabled",
        "exportSource",
        "exportExplainer",
        "exportTranslation",
        "autoExport",
    }
)
_OBSIDIAN_LIBRARY_KEYS = frozenset({"items", "sha256"})
_OBSIDIAN_LIBRARY_ITEM_KEYS = frozenset(
    {
        "artifactHeads",
        "noteSha256",
        "paperId",
        "pdfSha256",
        "sourceContentSha256",
        "sourceDocumentId",
    }
)
_OBSIDIAN_ARTIFACT_HEAD_KEYS = frozenset(
    {"artifactId", "contentSha256", "kind"}
)


class JobSpecValidationError(ValueError):
    code = "JOB_SPEC_INVALID"

    def __init__(self, detail: str) -> None:
        super().__init__(f"{self.code}: {detail}")


def _nonblank_or_none(value: object, name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise JobSpecValidationError(f"{name} must be null or nonblank text")
    return value


def _nonblank(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise JobSpecValidationError(f"{name} must be nonblank text")
    return value


@dataclass(frozen=True, slots=True)
class LegacyImportedJobSpecV1:
    job_type: str
    paper_id: str | None
    source_mode: str | None
    source_document_id: str | None = None
    artifact_id: str | None = None

    def __post_init__(self) -> None:
        _nonblank(self.job_type, "job_type")
        for name in ("paper_id", "source_mode", "source_document_id", "artifact_id"):
            _nonblank_or_none(getattr(self, name), name)

    @property
    def dispatch_error_code(self) -> str:
        """Legacy P1 imports have no recoverable worker request details."""
        return "JOB_SPEC_UNRECOVERABLE"


@dataclass(frozen=True, slots=True)
class SourceMaterializeJobSpecV1:
    paper_id: str
    source_document_id: str
    processing_version: str
    source_mode: str = "native"
    artifact_id: None = None
    job_type: str = "source_materialize"

    def __post_init__(self) -> None:
        _nonblank(self.paper_id, "paper_id")
        _nonblank(self.source_document_id, "source_document_id")
        _nonblank(self.processing_version, "processing_version")
        if self.source_mode != "native" or self.job_type != "source_materialize":
            raise JobSpecValidationError("source materialize binding is invalid")


JsonScalar: TypeAlias = type(None) | bool | int | float | str
JsonValue: TypeAlias = JsonScalar | tuple["JsonValue", ...] | Mapping[str, "JsonValue"]


def _safe_json(value: object) -> JsonValue:
    if value is None or isinstance(value, (bool, str)):
        return value
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):
            raise ValueError("JSON values cannot be NaN or Infinity")
        return value
    if isinstance(value, Mapping):
        result: dict[str, JsonValue] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError("JSON object keys must be strings")
            result[key] = _safe_json(item)
        _reject_sensitive_keys(result)
        return MappingProxyType(result)
    if isinstance(value, (tuple, list)):
        return tuple(_safe_json(item) for item in value)
    raise ValueError("value is not JSON-safe")


def safe_json_object(value: Mapping[str, object]) -> dict[str, JsonValue]:
    normalized = _safe_json(value)
    if not isinstance(normalized, Mapping):
        raise ValueError("safe JSON object must be an object")
    return dict(normalized)


def _positive_int(value: object, name: str, *, minimum: int, maximum: int) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or not minimum <= value <= maximum:
        raise JobSpecValidationError(f"{name} must be an integer from {minimum} to {maximum}")
    return value


def _safe_ocr_options(value: Mapping[str, object]) -> dict[str, JsonValue]:
    normalized = safe_json_object(value)
    if not set(normalized).issubset(_OCR_OPTION_KEYS):
        raise JobSpecValidationError("OCR option fields are invalid")
    language = normalized.get("language")
    if language is not None and (
        not isinstance(language, str) or _OCR_LANGUAGE.fullmatch(language) is None
    ):
        raise JobSpecValidationError("OCR language option is invalid")
    if "pageBatchSize" in normalized:
        _positive_int(normalized["pageBatchSize"], "pageBatchSize", minimum=1, maximum=16)
    if "maxConcurrency" in normalized:
        _positive_int(normalized["maxConcurrency"], "maxConcurrency", minimum=1, maximum=4)
    encoded = json.dumps(
        normalized,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
        allow_nan=False,
    )
    if len(encoded.encode("utf-8")) > _MAX_OCR_OPTIONS_BYTES:
        raise JobSpecValidationError("OCR options exceed the supported size")
    return normalized


@dataclass(frozen=True, slots=True)
class OcrJobSpecV1:
    paper_id: str
    source_document_id: str
    provider: str
    model: str
    options: Mapping[str, JsonValue] | None = None
    page_batch_size: int = 1
    max_concurrency: int = 1
    source_mode: str = "ocr"
    artifact_id: None = None
    job_type: str = "ocr"

    def __post_init__(self) -> None:
        for name in ("paper_id", "source_document_id", "provider", "model"):
            _nonblank(getattr(self, name), name)
        _positive_int(self.page_batch_size, "page_batch_size", minimum=1, maximum=16)
        _positive_int(self.max_concurrency, "max_concurrency", minimum=1, maximum=4)
        if self.source_mode != "ocr" or self.job_type != "ocr":
            raise JobSpecValidationError("OCR job binding is invalid")
        object.__setattr__(self, "options", MappingProxyType(_safe_ocr_options(self.options or {})))


@dataclass(frozen=True, slots=True)
class ExplainJobSpecV1:
    paper_id: str
    source_document_id: str
    artifact_id: str
    profile: str
    provider: str
    model: str
    prompt_version: str
    source_mode: str = "native"
    job_type: str = "explain"

    def __post_init__(self) -> None:
        for name in ("paper_id", "source_document_id", "artifact_id", "provider", "model", "prompt_version"):
            _nonblank(getattr(self, name), name)
        if self.profile not in {"standard", "deep"}:
            raise JobSpecValidationError("profile must be standard or deep")
        if self.source_mode not in {"native", "ocr"} or self.job_type != "explain":
            raise JobSpecValidationError("explain job binding is invalid")


@dataclass(frozen=True, slots=True)
class TranslateJobSpecV1:
    paper_id: str
    source_document_id: str
    artifact_id: str
    source_mode: str = "native"
    job_type: str = "translate"
    # Appended after the legacy fields so positional construction from older
    # callers keeps its source_mode/job_type meaning during rollout.
    mode: str = "chunked"

    def __post_init__(self) -> None:
        for name in ("paper_id", "source_document_id", "artifact_id"):
            _nonblank(getattr(self, name), name)
        if self.mode not in {"chunked", "full"}:
            raise JobSpecValidationError("translate mode is invalid")
        if self.source_mode not in {"native", "ocr"} or self.job_type != "translate":
            raise JobSpecValidationError("translate job binding is invalid")


@dataclass(frozen=True, slots=True)
class EmbedJobSpecV1:
    paper_id: str
    source_document_id: str
    include_embeddings: bool | None = None
    provider: str | None = None
    model: str | None = None
    embedding_version: str | None = None
    dimensions: int | None = None
    chunking_version: str | None = None
    options: Mapping[str, JsonValue] | None = None
    source_mode: str = "native"
    artifact_id: None = None
    job_type: str = "embed"

    def __post_init__(self) -> None:
        for name in ("paper_id", "source_document_id"):
            _nonblank(getattr(self, name), name)
        if self.source_mode not in {"native", "ocr"} or self.job_type != "embed":
            raise JobSpecValidationError("embed job binding is invalid")
        normalized_options = safe_json_object(self.options or {})
        object.__setattr__(self, "options", MappingProxyType(normalized_options))
        if self.include_embeddings is None:
            if any(
                value is not None
                for value in (
                    self.provider,
                    self.model,
                    self.embedding_version,
                    self.dimensions,
                    self.chunking_version,
                )
            ) or normalized_options:
                raise JobSpecValidationError("legacy embed spec cannot carry P3 identity")
            return
        if not isinstance(self.include_embeddings, bool):
            raise JobSpecValidationError("include_embeddings must be boolean or null")
        chunking_version = _nonblank(self.chunking_version, "chunking_version")
        object.__setattr__(self, "chunking_version", chunking_version)
        provider = _nonblank(self.provider, "provider")
        model = _nonblank(self.model, "model")
        embedding_version = _nonblank(self.embedding_version, "embedding_version")
        if self.include_embeddings:
            if "none" in {provider, model, embedding_version}:
                raise JobSpecValidationError("enabled embeddings require a real profile")
            _positive_int(self.dimensions, "dimensions", minimum=1, maximum=65536)
        elif (
            (provider, model, embedding_version) != ("none", "none", "none")
            or self.dimensions is not None
            or normalized_options
        ):
            raise JobSpecValidationError("disabled embeddings require the none profile")


@dataclass(frozen=True, slots=True)
class ObsidianExportJobSpecV1:
    paper_id: str
    dry_run: bool = False
    settings_fingerprint: str = ""
    settings_snapshot: Mapping[str, JsonValue] | None = None
    library_snapshot: Mapping[str, JsonValue] | None = None
    artifact_id: str | None = None
    apply_cleanup: bool = False
    cleanup_plan_sha: str | None = None
    source_mode: None = None
    source_document_id: None = None
    job_type: str = "obsidian_export"

    def __post_init__(self) -> None:
        _nonblank(self.paper_id, "paper_id")
        _nonblank_or_none(self.artifact_id, "artifact_id")
        if self.job_type != "obsidian_export":
            raise JobSpecValidationError("obsidian export binding is invalid")
        if _is_legacy_obsidian_placeholder(self):
            return
        _validate_obsidian_job_arguments(self)
        if self.apply_cleanup or self.cleanup_plan_sha is not None:
            raise JobSpecValidationError("obsidian export cannot apply cleanup")


@dataclass(frozen=True, slots=True)
class ObsidianSyncJobSpecV1:
    dry_run: bool = False
    apply_cleanup: bool = False
    cleanup_plan_sha: str | None = None
    settings_fingerprint: str = ""
    settings_snapshot: Mapping[str, JsonValue] | None = None
    library_snapshot: Mapping[str, JsonValue] | None = None
    paper_id: None = None
    source_mode: None = None
    source_document_id: None = None
    artifact_id: None = None
    job_type: str = "obsidian_sync"

    def __post_init__(self) -> None:
        if self.job_type != "obsidian_sync":
            raise JobSpecValidationError("obsidian sync binding is invalid")
        if _is_legacy_obsidian_placeholder(self):
            return
        _validate_obsidian_job_arguments(self)


def _is_legacy_obsidian_placeholder(value: object) -> bool:
    return (
        getattr(value, "dry_run") is False
        and getattr(value, "apply_cleanup") is False
        and getattr(value, "cleanup_plan_sha") is None
        and getattr(value, "settings_fingerprint") == ""
        and getattr(value, "settings_snapshot") is None
        and getattr(value, "library_snapshot") is None
    )


def _validate_obsidian_job_arguments(value: object) -> None:
    dry_run = getattr(value, "dry_run")
    apply_cleanup = getattr(value, "apply_cleanup")
    cleanup_plan_sha = getattr(value, "cleanup_plan_sha")
    if not isinstance(dry_run, bool) or not isinstance(apply_cleanup, bool):
        raise JobSpecValidationError("obsidian flags must be boolean")
    if apply_cleanup:
        if not isinstance(cleanup_plan_sha, str) or _SHA256.fullmatch(cleanup_plan_sha) is None:
            raise JobSpecValidationError("cleanupPlanSha must bind cleanup application")
    elif cleanup_plan_sha is not None:
        raise JobSpecValidationError("cleanupPlanSha requires applyCleanup")
    fingerprint = getattr(value, "settings_fingerprint")
    if not isinstance(fingerprint, str) or _SHA256.fullmatch(fingerprint) is None:
        raise JobSpecValidationError("settingsFingerprint must be lowercase SHA-256")
    settings = safe_json_object(getattr(value, "settings_snapshot") or {})
    if frozenset(settings) != _OBSIDIAN_SETTINGS_KEYS:
        raise JobSpecValidationError("settingsSnapshot fields are invalid")
    if (
        not isinstance(settings["vaultPath"], str)
        or not settings["vaultPath"].strip()
        or not isinstance(settings["rootFolder"], str)
        or not settings["rootFolder"].strip()
        or settings["pdfMode"] not in {"none", "reference", "copy"}
        or any(
            not isinstance(settings[field], bool)
            for field in (
                "enabled",
                "exportSource",
                "exportExplainer",
                "exportTranslation",
                "autoExport",
            )
        )
    ):
        raise JobSpecValidationError("settingsSnapshot values are invalid")
    if hash_canonical_json(settings) != fingerprint:
        raise JobSpecValidationError("settingsFingerprint does not match settingsSnapshot")
    library = safe_json_object(getattr(value, "library_snapshot") or {})
    if (
        frozenset(library) != _OBSIDIAN_LIBRARY_KEYS
        or not isinstance(library["items"], tuple)
        or not isinstance(library["sha256"], str)
        or _SHA256.fullmatch(library["sha256"]) is None
    ):
        raise JobSpecValidationError("librarySnapshot fields are invalid")
    normalized_items: list[Mapping[str, JsonValue]] = []
    previous_id: str | None = None
    for raw_item in library["items"]:
        if not isinstance(raw_item, Mapping) or frozenset(raw_item) != _OBSIDIAN_LIBRARY_ITEM_KEYS:
            raise JobSpecValidationError("librarySnapshot item fields are invalid")
        paper_id = _nonblank(raw_item["paperId"], "librarySnapshot.paperId")
        source_id = raw_item["sourceDocumentId"]
        source_hash = raw_item["sourceContentSha256"]
        if (source_id is None) != (source_hash is None) or (
            source_id is not None
            and (
                not isinstance(source_id, str)
                or not source_id.strip()
                or not isinstance(source_hash, str)
                or _SHA256.fullmatch(source_hash) is None
            )
        ):
            raise JobSpecValidationError("librarySnapshot source identity is invalid")
        for field in ("noteSha256", "pdfSha256"):
            content_hash = raw_item[field]
            if content_hash is not None and (
                not isinstance(content_hash, str)
                or _SHA256.fullmatch(content_hash) is None
            ):
                raise JobSpecValidationError(f"librarySnapshot {field} is invalid")
        heads = raw_item["artifactHeads"]
        if not isinstance(heads, tuple):
            raise JobSpecValidationError("librarySnapshot artifactHeads is invalid")
        normalized_heads: list[Mapping[str, JsonValue]] = []
        previous_kind: str | None = None
        for raw_head in heads:
            if (
                not isinstance(raw_head, Mapping)
                or frozenset(raw_head) != _OBSIDIAN_ARTIFACT_HEAD_KEYS
            ):
                raise JobSpecValidationError("librarySnapshot artifact head fields are invalid")
            kind = _nonblank(raw_head["kind"], "librarySnapshot.artifactHeads.kind")
            _nonblank(raw_head["artifactId"], "librarySnapshot.artifactHeads.artifactId")
            content_hash = raw_head["contentSha256"]
            if not isinstance(content_hash, str) or _SHA256.fullmatch(content_hash) is None:
                raise JobSpecValidationError("librarySnapshot artifact head hash is invalid")
            if previous_kind is not None and kind <= previous_kind:
                raise JobSpecValidationError("librarySnapshot artifactHeads must be strictly sorted")
            previous_kind = kind
            normalized_heads.append(MappingProxyType(dict(raw_head)))
        if previous_id is not None and paper_id <= previous_id:
            raise JobSpecValidationError("librarySnapshot items must be strictly sorted")
        previous_id = paper_id
        normalized_items.append(
            MappingProxyType({**dict(raw_item), "artifactHeads": tuple(normalized_heads)})
        )
    canonical_items = {"items": [_plain_json(item) for item in normalized_items]}
    if hash_canonical_json(canonical_items) != library["sha256"]:
        raise JobSpecValidationError("librarySnapshot sha256 does not match items")
    if isinstance(value, ObsidianExportJobSpecV1) and (
        len(normalized_items) != 1
        or normalized_items[0]["paperId"] != value.paper_id
    ):
        raise JobSpecValidationError("obsidian export snapshot must bind exactly one paper")
    object.__setattr__(value, "settings_snapshot", MappingProxyType(settings))
    object.__setattr__(
        value,
        "library_snapshot",
        MappingProxyType(
            {"items": tuple(normalized_items), "sha256": library["sha256"]}
        ),
    )


JobSpecV1: TypeAlias = (
    LegacyImportedJobSpecV1 | SourceMaterializeJobSpecV1 | OcrJobSpecV1 |
    ExplainJobSpecV1 | TranslateJobSpecV1 | EmbedJobSpecV1 |
    ObsidianExportJobSpecV1 | ObsidianSyncJobSpecV1
)


def ensure_application_job_spec(value: JobSpecV1) -> JobSpecV1:
    if isinstance(value, LegacyImportedJobSpecV1):
        raise JobSpecValidationError("legacy imported specs cannot be application-enqueued")
    return value


def _canonical(payload: Mapping[str, object]) -> str:
    try:
        encoded = json.dumps(
            payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True,
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise JobSpecValidationError("job spec is not JSON-safe") from exc
    if len(encoded.encode("utf-8")) > MAX_JOB_SPEC_BYTES:
        raise JobSpecValidationError("job spec exceeds 4 MiB")
    return encoded


def _plain_json(value: object) -> object:
    if isinstance(value, Mapping):
        return {key: _plain_json(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_plain_json(item) for item in value]
    return value


def _payload(value: JobSpecV1) -> dict[str, object]:
    if isinstance(value, LegacyImportedJobSpecV1):
        arguments: dict[str, object] = {"legacyImported": True}
    elif isinstance(value, SourceMaterializeJobSpecV1):
        arguments = {"processingVersion": value.processing_version}
    elif isinstance(value, OcrJobSpecV1):
        arguments = {
            "maxConcurrency": value.max_concurrency,
            "model": value.model,
            "options": dict(value.options or {}),
            "pageBatchSize": value.page_batch_size,
            "provider": value.provider,
        }
    elif isinstance(value, ExplainJobSpecV1):
        arguments = {
            "model": value.model, "profile": value.profile,
            "promptVersion": value.prompt_version, "provider": value.provider,
        }
    elif isinstance(value, EmbedJobSpecV1):
        arguments = (
            {}
            if value.include_embeddings is None
            else {
                "chunkingVersion": value.chunking_version,
                "dimensions": value.dimensions,
                "embeddingVersion": value.embedding_version,
                "includeEmbeddings": value.include_embeddings,
                "model": value.model,
                "options": dict(value.options or {}),
                "provider": value.provider,
            }
        )
    elif isinstance(value, TranslateJobSpecV1):
        # Preserve the original chunked encoding so existing queued jobs and
        # idempotency keys remain valid across this rollout.
        arguments = {} if value.mode == "chunked" else {"mode": value.mode}
    elif isinstance(value, (ObsidianExportJobSpecV1, ObsidianSyncJobSpecV1)):
        arguments = (
            {}
            if _is_legacy_obsidian_placeholder(value)
            else {
                "applyCleanup": value.apply_cleanup,
                "cleanupPlanSha": value.cleanup_plan_sha,
                "dryRun": value.dry_run,
                "librarySnapshot": _plain_json(value.library_snapshot or {}),
                "settingsFingerprint": value.settings_fingerprint,
                "settingsSnapshot": _plain_json(value.settings_snapshot or {}),
            }
        )
    else:
        raise JobSpecValidationError("unsupported v1 job spec variant")
    return {
        "arguments": arguments,
        "jobType": value.job_type,
        "paperId": value.paper_id,
        "schemaVersion": 1,
        "sourceMode": value.source_mode,
        "target": {
            "artifactId": value.artifact_id,
            "sourceDocumentId": value.source_document_id,
        },
    }


def encode_job_spec_v1(value: JobSpecV1) -> str:
    payload = _payload(value)
    _reject_sensitive_keys(payload)
    return _canonical(payload)


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, item in pairs:
        if key in result:
            raise JobSpecValidationError(f"duplicate key: {key}")
        result[key] = item
    return result


def _reject_nonfinite(value: str) -> object:
    raise JobSpecValidationError(f"non-finite JSON value: {value}")


def _reject_sensitive_keys(value: object) -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            normalized = key.lower().replace("_", "").replace("-", "")
            if normalized in _SENSITIVE_KEYS or normalized.endswith("secret"):
                raise JobSpecValidationError("sensitive key is forbidden")
            _reject_sensitive_keys(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _reject_sensitive_keys(item)
    elif isinstance(value, str):
        lowered = value.casefold().strip()
        is_version_identifier = bool(
            re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,127}-v\d+", lowered)
        )
        contains_sensitive_marker = (
            lowered.startswith("%pdf-")
            or lowered.startswith("#")
            or lowered.startswith("```")
            or "raw provider response" in lowered
            or ("markdown" in lowered and not is_version_identifier)
            or "prompt:" in lowered
            or re.search(r"\b(?:authorization|cookie|bearer|api[ _-]?key)\b", lowered)
            or re.search(r"(?:^|[\s:=_-])secret(?:$|[\s:=_-])", lowered)
        )
        if contains_sensitive_marker:
            raise JobSpecValidationError("sensitive string value is forbidden")


def decode_job_spec_v1(
    raw_json: str,
    *,
    expected_row: Mapping[str, object] | None = None,
) -> JobSpecV1:
    if not isinstance(raw_json, str):
        raise JobSpecValidationError("job spec must be text")
    byte_length = len(raw_json.encode("utf-8"))
    if byte_length < 2 or byte_length > MAX_JOB_SPEC_BYTES:
        raise JobSpecValidationError("job spec size is outside the supported range")
    try:
        payload = json.loads(
            raw_json, object_pairs_hook=_reject_duplicate_keys, parse_constant=_reject_nonfinite,
        )
    except JobSpecValidationError:
        raise
    except (TypeError, ValueError) as exc:
        raise JobSpecValidationError("job spec is not valid JSON") from exc
    if not isinstance(payload, dict) or frozenset(payload) != _TOP_LEVEL_KEYS:
        raise JobSpecValidationError("job spec envelope keys are invalid")
    if type(payload["schemaVersion"]) is not int or payload["schemaVersion"] != 1:
        raise JobSpecValidationError("unsupported schemaVersion")
    if not isinstance(payload["arguments"], dict):
        raise JobSpecValidationError("arguments must be an object")
    target = payload["target"]
    if not isinstance(target, dict) or frozenset(target) != _TARGET_KEYS:
        raise JobSpecValidationError("target keys are invalid")
    _reject_sensitive_keys(payload)
    job_type = _nonblank(payload["jobType"], "jobType")
    paper_id = _nonblank_or_none(payload["paperId"], "paperId")
    source_mode = _nonblank_or_none(payload["sourceMode"], "sourceMode")
    source_document_id = _nonblank_or_none(target["sourceDocumentId"], "sourceDocumentId")
    artifact_id = _nonblank_or_none(target["artifactId"], "artifactId")
    arguments = payload["arguments"]
    if arguments == {"legacyImported": True}:
        value: JobSpecV1 = LegacyImportedJobSpecV1(
            job_type=job_type, paper_id=paper_id, source_mode=source_mode,
            source_document_id=source_document_id, artifact_id=artifact_id,
        )
    elif job_type == "source_materialize":
        if frozenset(arguments) != {"processingVersion"}:
            raise JobSpecValidationError("source materialize arguments are invalid")
        if artifact_id is not None:
            raise JobSpecValidationError("source materialize artifact target must be null")
        value = SourceMaterializeJobSpecV1(
            paper_id=_nonblank(paper_id, "paperId"),
            source_document_id=_nonblank(source_document_id, "sourceDocumentId"),
            processing_version=_nonblank(arguments["processingVersion"], "processingVersion"),
            source_mode=_nonblank(source_mode, "sourceMode"),
            job_type=job_type,
        )
    elif job_type == "ocr":
        if frozenset(arguments) != {"maxConcurrency", "model", "options", "pageBatchSize", "provider"}:
            raise JobSpecValidationError("OCR arguments are invalid")
        if artifact_id is not None or not isinstance(arguments["options"], dict):
            raise JobSpecValidationError("OCR target or options are invalid")
        value = OcrJobSpecV1(
            paper_id=_nonblank(paper_id, "paperId"), source_document_id=_nonblank(source_document_id, "sourceDocumentId"),
            provider=_nonblank(arguments["provider"], "provider"), model=_nonblank(arguments["model"], "model"),
            options=safe_json_object(arguments["options"]),
            page_batch_size=_positive_int(arguments["pageBatchSize"], "pageBatchSize", minimum=1, maximum=16),
            max_concurrency=_positive_int(arguments["maxConcurrency"], "maxConcurrency", minimum=1, maximum=4),
            source_mode=_nonblank(source_mode, "sourceMode"), job_type=job_type,
        )
    elif job_type == "explain":
        if frozenset(arguments) != {"model", "profile", "promptVersion", "provider"}:
            raise JobSpecValidationError("explain arguments are invalid")
        value = ExplainJobSpecV1(
            paper_id=_nonblank(paper_id, "paperId"), source_document_id=_nonblank(source_document_id, "sourceDocumentId"),
            artifact_id=_nonblank(artifact_id, "artifactId"), profile=_nonblank(arguments["profile"], "profile"),
            provider=_nonblank(arguments["provider"], "provider"), model=_nonblank(arguments["model"], "model"),
            prompt_version=_nonblank(arguments["promptVersion"], "promptVersion"),
            source_mode=_nonblank(source_mode, "sourceMode"), job_type=job_type,
        )
    elif job_type == "translate":
        if frozenset(arguments) not in {frozenset(), frozenset({"mode"})}:
            raise JobSpecValidationError("translate arguments are invalid")
        value = TranslateJobSpecV1(
            paper_id=_nonblank(paper_id, "paperId"), source_document_id=_nonblank(source_document_id, "sourceDocumentId"),
            artifact_id=_nonblank(artifact_id, "artifactId"),
            mode=(
                _nonblank(arguments["mode"], "mode")
                if "mode" in arguments
                else "chunked"
            ),
            source_mode=_nonblank(source_mode, "sourceMode"), job_type=job_type,
        )
    elif job_type == "embed":
        if artifact_id is not None:
            raise JobSpecValidationError("embed target is invalid")
        if not arguments:
            value = EmbedJobSpecV1(
                paper_id=_nonblank(paper_id, "paperId"),
                source_document_id=_nonblank(source_document_id, "sourceDocumentId"),
                source_mode=_nonblank(source_mode, "sourceMode"),
                job_type=job_type,
            )
        else:
            expected_embed_keys = {
                "chunkingVersion", "dimensions", "embeddingVersion",
                "includeEmbeddings", "model", "options", "provider",
            }
            if set(arguments) != expected_embed_keys or not isinstance(arguments["options"], dict):
                raise JobSpecValidationError("embed arguments are invalid")
            value = EmbedJobSpecV1(
                paper_id=_nonblank(paper_id, "paperId"),
                source_document_id=_nonblank(source_document_id, "sourceDocumentId"),
                include_embeddings=arguments["includeEmbeddings"],
                provider=arguments["provider"],
                model=arguments["model"],
                embedding_version=arguments["embeddingVersion"],
                dimensions=arguments["dimensions"],
                chunking_version=arguments["chunkingVersion"],
                options=safe_json_object(arguments["options"]),
                source_mode=_nonblank(source_mode, "sourceMode"),
                job_type=job_type,
            )
    elif job_type == "obsidian_export":
        expected_obsidian_keys = {
            "applyCleanup", "cleanupPlanSha", "dryRun", "librarySnapshot",
            "settingsFingerprint", "settingsSnapshot",
        }
        if source_mode is not None or source_document_id is not None:
            raise JobSpecValidationError("obsidian export arguments or target are invalid")
        if not arguments:
            value = ObsidianExportJobSpecV1(
                paper_id=_nonblank(paper_id, "paperId"), artifact_id=artifact_id, job_type=job_type,
            )
        else:
            if set(arguments) != expected_obsidian_keys:
                raise JobSpecValidationError("obsidian export arguments are invalid")
            value = ObsidianExportJobSpecV1(
                paper_id=_nonblank(paper_id, "paperId"),
                artifact_id=artifact_id,
                dry_run=arguments["dryRun"],
                apply_cleanup=arguments["applyCleanup"],
                cleanup_plan_sha=arguments["cleanupPlanSha"],
                settings_fingerprint=arguments["settingsFingerprint"],
                settings_snapshot=arguments["settingsSnapshot"],
                library_snapshot=arguments["librarySnapshot"],
                job_type=job_type,
            )
    elif job_type == "obsidian_sync":
        expected_obsidian_keys = {
            "applyCleanup", "cleanupPlanSha", "dryRun", "librarySnapshot",
            "settingsFingerprint", "settingsSnapshot",
        }
        if any(item is not None for item in (paper_id, source_mode, source_document_id, artifact_id)):
            raise JobSpecValidationError("obsidian sync bindings are invalid")
        if not arguments:
            value = ObsidianSyncJobSpecV1(job_type=job_type)
        else:
            if set(arguments) != expected_obsidian_keys:
                raise JobSpecValidationError("obsidian sync arguments are invalid")
            value = ObsidianSyncJobSpecV1(
                dry_run=arguments["dryRun"],
                apply_cleanup=arguments["applyCleanup"],
                cleanup_plan_sha=arguments["cleanupPlanSha"],
                settings_fingerprint=arguments["settingsFingerprint"],
                settings_snapshot=arguments["settingsSnapshot"],
                library_snapshot=arguments["librarySnapshot"],
                job_type=job_type,
            )
    else:
        raise JobSpecValidationError("unsupported v1 job spec variant")
    if raw_json != encode_job_spec_v1(value):
        raise JobSpecValidationError("job spec bytes are not canonical")
    if expected_row is not None:
        for column, item in {
            "job_type": value.job_type, "paper_id": value.paper_id,
            "source_mode": value.source_mode, "source_document_id": value.source_document_id,
            "artifact_id": value.artifact_id,
        }.items():
            if expected_row.get(column) != item:
                raise JobSpecValidationError(f"job spec does not match {column}")
    return value


def hash_job_spec(raw_json: str) -> str:
    return hashlib.sha256(raw_json.encode("utf-8")).hexdigest()


def hash_canonical_json(value: Mapping[str, object]) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def build_obsidian_job_key(spec_sha256: str) -> str:
    if not isinstance(spec_sha256, str) or _SHA256.fullmatch(spec_sha256) is None:
        raise JobSpecValidationError("obsidian spec SHA must be lowercase SHA-256")
    return _hash_material("job:obsidian:v1", spec_sha256)


def _hash_material(*parts: str) -> str:
    if any(not isinstance(part, str) for part in parts):
        raise ValueError("hash material must be text")
    return hashlib.sha256("\0".join(parts).encode("utf-8")).hexdigest()


def build_source_key(*, paper_id: str, mode: str, provider: str, model: str,
                     pdf_sha256: str, options_hash: str, processing_version: str) -> str:
    for name, value in {"paper_id": paper_id, "mode": mode, "provider": provider, "model": model,
                        "pdf_sha256": pdf_sha256, "options_hash": options_hash,
                        "processing_version": processing_version}.items():
        _nonblank(value, name)
    return _hash_material("source:v1", paper_id, mode, provider, model, pdf_sha256, options_hash, processing_version)


def build_source_job_key(source_key: str, spec_sha256: str) -> str:
    return _hash_material("job:source:v2", _nonblank(source_key, "source_key"), _nonblank(spec_sha256, "spec_sha256"))


def build_artifact_key(*, kind: str, source_document_id: str, source_content_sha256: str,
                       generator_provider: str, generator_model: str, prompt_version: str,
                       kind_specific_options: Mapping[str, object]) -> str:
    options_hash = hash_canonical_json(kind_specific_options)
    return _hash_material(
        "artifact:v1", _nonblank(kind, "kind"), _nonblank(source_document_id, "source_document_id"),
        _nonblank(source_content_sha256, "source_content_sha256"),
        _nonblank(generator_provider, "generator_provider"), _nonblank(generator_model, "generator_model"),
        _nonblank(prompt_version, "prompt_version"), options_hash,
    )


def build_artifact_job_key(artifact_key: str, spec_sha256: str) -> str:
    return _hash_material("job:artifact:v2", _nonblank(artifact_key, "artifact_key"), _nonblank(spec_sha256, "spec_sha256"))


def build_index_job_key(*, source_document_id: str, source_content_sha256: str,
                        embedding_model: str, chunking_version: str,
                        embedding_provider: str | None = None,
                        embedding_version: str | None = None,
                        include_embeddings: bool = True,
                        embedding_options: Mapping[str, object] | None = None) -> str:
    """Build the P3 index key while preserving the P2 four-argument golden.

    Older P2 callers supplied only ``embedding_model`` and
    ``chunking_version``.  Their byte-level key remains unchanged.  The full
    P3 identity is selected when provider/version/options are explicit and
    includes the canonical includeEmbeddings/options payload.
    """
    source_document_id = _nonblank(source_document_id, "source_document_id")
    source_content_sha256 = _nonblank(source_content_sha256, "source_content_sha256")
    embedding_model = _nonblank(embedding_model, "embedding_model")
    chunking_version = _nonblank(chunking_version, "chunking_version")
    if embedding_provider is None and embedding_version is None and embedding_options is None and include_embeddings:
        return _hash_material(
            "job:index:v1", source_document_id, source_content_sha256,
            embedding_model, chunking_version,
        )
    if not isinstance(include_embeddings, bool):
        raise JobSpecValidationError("include_embeddings must be boolean")
    provider = _nonblank(embedding_provider or "none", "embedding_provider")
    version = _nonblank(embedding_version or "none", "embedding_version")
    if not include_embeddings and (provider != "none" or embedding_model != "none" or version != "none"):
        # A lexical-only index must not accidentally carry a provider identity.
        raise JobSpecValidationError("disabled embeddings require none provider/model/version")
    options_hash = hash_canonical_json({
        "includeEmbeddings": include_embeddings,
        "chunkingVersion": chunking_version,
        "embeddingOptions": dict(embedding_options or {}),
    })
    return _hash_material(
        "job:index:v1", source_document_id, source_content_sha256, chunking_version,
        provider, embedding_model, version, options_hash,
    )


class ProcessingStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


def _utc(value: datetime, name: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be a timezone-aware datetime")
    return value.astimezone(timezone.utc)


def _identifier(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 255:
        raise ValueError(f"{name} must be a nonblank identifier")
    return value


_TRANSITIONS: Mapping[ProcessingStatus, frozenset[ProcessingStatus]] = MappingProxyType({
    ProcessingStatus.QUEUED: frozenset({ProcessingStatus.RUNNING, ProcessingStatus.CANCELLED}),
    ProcessingStatus.RUNNING: frozenset({ProcessingStatus.QUEUED, ProcessingStatus.SUCCEEDED, ProcessingStatus.FAILED, ProcessingStatus.CANCELLED}),
    ProcessingStatus.SUCCEEDED: frozenset(), ProcessingStatus.FAILED: frozenset(), ProcessingStatus.CANCELLED: frozenset(),
})


def transition_job_status(current: ProcessingStatus | str, target: ProcessingStatus | str) -> ProcessingStatus:
    try:
        current_status, target_status = ProcessingStatus(current), ProcessingStatus(target)
    except (TypeError, ValueError) as exc:
        raise ValueError("processing status is invalid") from exc
    if target_status not in _TRANSITIONS[current_status]:
        raise ValueError(f"invalid processing job transition: {current_status.value} -> {target_status.value}")
    return target_status


@dataclass(frozen=True, slots=True)
class NewProcessingJob:
    id: str
    spec: JobSpecV1
    idempotency_key: str
    created_at: datetime
    max_attempts: int = 1
    attempt: int = 0
    status: ProcessingStatus = ProcessingStatus.QUEUED

    def __post_init__(self) -> None:
        _identifier(self.id, "id")
        _identifier(self.idempotency_key, "idempotency_key")
        ensure_application_job_spec(self.spec)
        if not isinstance(self.max_attempts, int) or isinstance(self.max_attempts, bool) or self.max_attempts < 1:
            raise ValueError("max_attempts must be at least one")
        if not isinstance(self.attempt, int) or isinstance(self.attempt, bool) or not 0 <= self.attempt <= self.max_attempts:
            raise ValueError("attempt must be between zero and max_attempts")
        if self.status is not ProcessingStatus.QUEUED:
            raise ValueError("new processing jobs must be queued")
        object.__setattr__(self, "created_at", _utc(self.created_at, "created_at"))

    def to_api_dict(self) -> dict[str, object]:
        return {
            "id": self.id, "status": self.status.value, "attempt": self.attempt,
            "maxAttempts": self.max_attempts, "paperId": self.spec.paper_id,
            "sourceMode": self.spec.source_mode,
        }


@dataclass(frozen=True, slots=True)
class StoredJobSpec:
    value: JobSpecV1
    raw_json: str
    sha256: str

    def __post_init__(self) -> None:
        if decode_job_spec_v1(self.raw_json) != self.value or hash_job_spec(self.raw_json) != self.sha256:
            raise JobSpecValidationError("stored job spec is not canonical")


@dataclass(frozen=True, slots=True)
class EnqueueResult:
    job: NewProcessingJob
    deduplicated: bool = False


@dataclass(frozen=True, slots=True)
class JobProgress:
    value: Mapping[str, JsonValue]

    def __post_init__(self) -> None:
        if not isinstance(self.value, Mapping) or not all(
            isinstance(key, str) for key in self.value
        ):
            raise ValueError("progress must be an object with text keys")
        if not set(self.value).issubset(_JOB_PROGRESS_KEYS):
            raise ValueError("progress fields are invalid")
        normalized: dict[str, JsonValue] = {}
        for key, item in self.value.items():
            if key in _JOB_PROGRESS_LABEL_KEYS:
                if not isinstance(item, str) or _JOB_PROGRESS_LABEL.fullmatch(item) is None:
                    raise ValueError("progress label is invalid")
            elif not isinstance(item, int) or isinstance(item, bool) or item < 0:
                raise ValueError("progress count is invalid")
            normalized[key] = item
        encoded = json.dumps(
            normalized,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
            allow_nan=False,
        )
        if len(encoded.encode("utf-8")) > MAX_JOB_PROGRESS_BYTES:
            raise ValueError("progress exceeds the supported size")
        object.__setattr__(self, "value", MappingProxyType(normalized))


@dataclass(frozen=True, slots=True)
class JobResult:
    value: Mapping[str, JsonValue]

    def __post_init__(self) -> None:
        object.__setattr__(self, "value", MappingProxyType(safe_json_object(self.value)))


@dataclass(frozen=True, slots=True)
class JobFailure:
    code: str
    message: str | None = None
    retryable: bool = False

    def __post_init__(self) -> None:
        _identifier(self.code, "code")
        if self.message is not None and not isinstance(self.message, str):
            raise ValueError("message must be text or null")


@dataclass(frozen=True, slots=True)
class JobLease:
    job: NewProcessingJob
    spec: StoredJobSpec
    worker_id: str
    token: str
    expires_at: datetime

    def __post_init__(self) -> None:
        _identifier(self.worker_id, "worker_id")
        _identifier(self.token, "token")
        object.__setattr__(self, "expires_at", _utc(self.expires_at, "expires_at"))


@dataclass(frozen=True, slots=True)
class JobListQuery:
    paper_id: str | None = None
    status: ProcessingStatus | None = None
    limit: int = 50
    cursor: str | None = None

    def __post_init__(self) -> None:
        if self.paper_id is not None:
            _identifier(self.paper_id, "paper_id")
        if self.status is not None:
            object.__setattr__(self, "status", ProcessingStatus(self.status))
        if not isinstance(self.limit, int) or isinstance(self.limit, bool) or not 1 <= self.limit <= 100:
            raise ValueError("limit must be from one to 100")


@dataclass(frozen=True, slots=True)
class JobEventListQuery:
    limit: int = 50
    cursor: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.limit, int) or isinstance(self.limit, bool) or not 1 <= self.limit <= 100:
            raise ValueError("limit must be from one to 100")


@dataclass(frozen=True, slots=True)
class ProcessingJobEvent:
    job_id: str
    sequence: int
    event_type: str
    created_at: datetime
    details: Mapping[str, JsonValue] | None = None

    def __post_init__(self) -> None:
        _identifier(self.job_id, "job_id")
        _identifier(self.event_type, "event_type")
        if not isinstance(self.sequence, int) or isinstance(self.sequence, bool) or self.sequence < 0:
            raise ValueError("sequence must be nonnegative")
        object.__setattr__(self, "created_at", _utc(self.created_at, "created_at"))
        if self.details is not None:
            object.__setattr__(self, "details", MappingProxyType(safe_json_object(self.details)))


@dataclass(frozen=True, slots=True)
class Page:
    items: tuple[object, ...]
    next_cursor: str | None = None
