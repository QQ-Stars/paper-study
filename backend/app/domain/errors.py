from __future__ import annotations

from types import MappingProxyType
from typing import ClassVar


_SAFE_DETAIL_KEYS = frozenset(
    {
        "actual_revision",
        "artifact_kind",
        "expected_revision",
        "operation",
        "paper_id",
        "project_id",
        "revision",
        "source_mode",
    }
)


class DomainError(Exception):
    code: ClassVar[str] = "DOMAIN_ERROR"
    public_message: ClassVar[str] = "The requested operation could not be completed."

    def __init__(self, **details: object) -> None:
        safe_details = {
            key: str(value)[:256]
            for key, value in details.items()
            if key in _SAFE_DETAIL_KEYS and value is not None
        }
        self.details = MappingProxyType(safe_details)
        super().__init__(f"{self.code}: {self.public_message}")


class ChunkCoverageInvalidError(DomainError, ValueError):
    code = "CHUNK_COVERAGE_INVALID"
    public_message = "The source chunks do not cover the source document exactly."
    retryable = False


class ChunkAtomicBlockTooLargeError(DomainError, ValueError):
    code = "CHUNK_ATOMIC_BLOCK_TOO_LARGE"
    public_message = "An atomic Markdown block exceeds the supported chunk size."
    retryable = False


class SourceChunksNotReadyError(DomainError, ValueError):
    code = "SOURCE_CHUNKS_NOT_READY"
    public_message = "The source chunks are not ready."
    http_status = 409
    retryable = False


class ChunkingVersionMismatchError(DomainError, ValueError):
    code = "CHUNKING_VERSION_MISMATCH"
    public_message = "The source chunks use a different chunking version."
    http_status = 409
    retryable = False


class ContextBudgetInvalidError(DomainError, ValueError):
    code = "CONTEXT_BUDGET_INVALID"
    public_message = "The context budget cannot contain the required source unit."
    http_status = 422
    retryable = False


class ContextCoverageInvalidError(DomainError, ValueError):
    code = "CONTEXT_COVERAGE_INVALID"
    public_message = "The context plan does not cover its required source chunks."
    retryable = False


class MissingPaperError(DomainError):
    code = "PAPER_NOT_FOUND"
    public_message = "The requested paper does not exist."


class MissingPdfError(DomainError):
    code = "PDF_NOT_FOUND"
    public_message = "The paper has no available PDF."


class SourceNotFoundError(DomainError):
    code = "SOURCE_NOT_FOUND"
    public_message = "The requested source document does not exist."
    http_status = 404


class SourceModeMismatchError(DomainError):
    code = "SOURCE_MODE_MISMATCH"
    public_message = "The source mode does not match the source document."
    http_status = 422


class SourceNotReadyError(DomainError):
    code = "SOURCE_NOT_READY"
    public_message = "The source document is not ready."
    http_status = 409


class InvalidSourceModeError(DomainError):
    code = "INVALID_SOURCE_MODE"
    public_message = "The requested source mode is invalid."


class OcrUnavailableError(DomainError):
    code = "OCR_UNAVAILABLE"
    public_message = "OCR is unavailable in this rollout stage."


class OcrDisabledError(DomainError):
    code = "OCR_DISABLED"
    public_message = "OCR is disabled."
    http_status = 409


class ObsidianDisabledError(DomainError):
    code = "OBSIDIAN_DISABLED"
    public_message = "Obsidian projection is disabled."
    http_status = 409


class OcrProviderUnknownError(DomainError):
    code = "OCR_PROVIDER_UNKNOWN"
    public_message = "The requested OCR provider is unknown."
    http_status = 422


class OcrProviderContractUnverifiedError(DomainError):
    code = "OCR_PROVIDER_CONTRACT_UNVERIFIED"
    public_message = "The OCR provider contract has not been verified."
    http_status = 503


class OcrRequestInvalidError(DomainError):
    code = "OCR_REQUEST_INVALID"
    public_message = "The OCR request is invalid."
    http_status = 422


class OcrResponseInvalidError(DomainError):
    code = "OCR_RESPONSE_INVALID"
    public_message = "The OCR provider returned an invalid response."


class _OcrRetryableError(DomainError):
    retryable = True
    retry_after_seconds: int | None = None


class OcrRateLimitedError(_OcrRetryableError):
    code = "OCR_RATE_LIMITED"
    public_message = "The OCR provider rate limit was reached."

    def __init__(self, *, retry_after_seconds: int | None = None) -> None:
        if isinstance(retry_after_seconds, int) and not isinstance(retry_after_seconds, bool):
            self.retry_after_seconds = min(900, max(0, retry_after_seconds))
        else:
            self.retry_after_seconds = None
        super().__init__()


class OcrTimeoutError(_OcrRetryableError):
    code = "OCR_TIMEOUT"
    public_message = "The OCR provider request timed out."


class OcrServerError(_OcrRetryableError):
    code = "OCR_SERVER_ERROR"
    public_message = "The OCR provider is temporarily unavailable."


class ExtractionFailureError(DomainError):
    code = "EXTRACTION_FAILED"
    public_message = "The document source could not be extracted."


class EmptySourceError(DomainError):
    code = "EMPTY_SOURCE"
    public_message = "The extracted document source is empty."


class GenerationFailureError(DomainError):
    code = "GENERATION_FAILED"
    public_message = "The artifact could not be generated."
    retryable = True


class WorkerConfigurationError(DomainError):
    code = "WORKER_CONFIGURATION_INVALID"
    public_message = "The processing worker configuration is invalid."


class EmptyArtifactError(DomainError):
    code = "EMPTY_ARTIFACT"
    public_message = "The generated artifact is empty."


class ArtifactKindUnsupportedError(DomainError):
    code = "ARTIFACT_KIND_UNSUPPORTED"
    public_message = "The requested artifact kind is unsupported."
    http_status = 422


class StaleSourceError(DomainError):
    code = "SOURCE_STALE"
    public_message = "The source document is stale."
    http_status = 409


class PersistenceConflictError(DomainError):
    code = "PERSISTENCE_CONFLICT"
    public_message = "The data changed during publication."


class TranslationCheckpointConflictError(DomainError):
    """A saved translation checkpoint cannot be reused for this identity."""

    code = "TRANSLATION_CHECKPOINT_CONFLICT"
    public_message = "The translation checkpoint does not match the current source."
    retryable = False


class TranslationProviderRequestError(DomainError):
    """The chunk-level translation provider failed without exposing its payload."""

    code = "TRANSLATION_PROVIDER_REQUEST_FAILED"
    public_message = "The translation provider request failed."

    def __init__(
        self,
        *,
        retryable: bool,
        retry_after_seconds: int | None = None,
    ) -> None:
        self.retryable = bool(retryable)
        self.retry_after_seconds = (
            min(900, max(0, retry_after_seconds))
            if isinstance(retry_after_seconds, int)
            and not isinstance(retry_after_seconds, bool)
            else None
        )
        super().__init__()


class MarkdownStructureInvalidError(DomainError):
    """A structured translation changed protected Markdown syntax."""

    code = "MARKDOWN_STRUCTURE_INVALID"
    public_message = "The translated Markdown did not preserve document structure."
    retryable = False


class ArtifactOutputInvalidError(DomainError):
    """A structured consumer returned bytes outside its frozen schema."""

    code = "ARTIFACT_OUTPUT_INVALID"
    public_message = "The generated artifact did not match its required output schema."
    retryable = False


class SearchQueryTooShortError(DomainError):
    code = "SEARCH_QUERY_TOO_SHORT"
    public_message = "The search query must contain at least three Unicode code points."
    http_status = 422
    retryable = False


class EmbeddingRequestFailedError(DomainError):
    code = "EMBEDDING_REQUEST_FAILED"
    public_message = "The embedding provider request failed."

    def __init__(
        self,
        *,
        retryable: bool,
        retry_after_seconds: int | None = None,
    ) -> None:
        self.retryable = bool(retryable)
        self.retry_after_seconds = (
            min(900, max(0, retry_after_seconds))
            if isinstance(retry_after_seconds, int)
            and not isinstance(retry_after_seconds, bool)
            else None
        )
        super().__init__()


class EmbeddingResponseInvalidError(DomainError):
    code = "EMBEDDING_RESPONSE_INVALID"
    public_message = "The embedding provider returned an invalid response."
    retryable = False


class EmbeddingProfileUnavailableError(DomainError):
    code = "EMBEDDING_PROFILE_UNAVAILABLE"
    public_message = "The semantic embedding profile is unavailable."
    http_status = 409


class JobNotCancellableError(DomainError):
    code = "JOB_NOT_CANCELLABLE"
    public_message = "The processing job cannot be cancelled in its current state."


class JobNotRetryableError(DomainError):
    code = "JOB_NOT_RETRYABLE"
    public_message = "The processing job cannot be retried in its current state."


class JobLeaseLostError(DomainError):
    code = "JOB_LEASE_LOST"
    public_message = "The worker lease is no longer valid."


class SchemaRevisionMismatchError(DomainError):
    code = "SCHEMA_REVISION_MISMATCH"
    public_message = "The database schema revision is incompatible."
    http_status = 503


class NativeTextEmptyError(DomainError):
    code = "NATIVE_TEXT_EMPTY"
    public_message = "Native PDF extraction returned no usable text."


class NativeExtractionFailedError(DomainError):
    code = "NATIVE_EXTRACTION_FAILED"
    public_message = "Native PDF extraction failed."


class PdfEncryptedError(DomainError):
    code = "PDF_ENCRYPTED"
    public_message = "The PDF is encrypted and cannot be read without a password."


class SourcePdfChangedError(DomainError):
    code = "SOURCE_PDF_CHANGED"
    public_message = "The PDF changed while its source was being materialized."


class PersistenceReadError(DomainError):
    code = "PERSISTENCE_READ_FAILED"
    public_message = "The requested data could not be read safely."


class ReproductionNotFoundError(DomainError):
    code = "REPRODUCTION_NOT_FOUND"
    public_message = "The reproduction project does not exist."
    http_status = 404


class ReproductionConflictError(DomainError):
    code = "REPRODUCTION_CONFLICT"
    public_message = "The reproduction project changed before your save completed."
    http_status = 409


class ReproductionArchivedError(DomainError):
    code = "REPRODUCTION_ARCHIVED"
    public_message = "The archived reproduction project is read-only."
    http_status = 409


class ReproductionValidationError(DomainError):
    code = "REPRODUCTION_INVALID"
    public_message = "The reproduction request is invalid."
    http_status = 422


class CredentialBackendError(DomainError):
    code = "CREDENTIAL_BACKEND_ERROR"
    public_message = "The secure credential backend is unavailable."


class CredentialUpdateIndeterminateError(DomainError):
    code = "CREDENTIAL_UPDATE_INDETERMINATE"
    public_message = "Credential update compensation could not be verified."
