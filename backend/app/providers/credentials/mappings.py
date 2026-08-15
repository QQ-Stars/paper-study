from backend.app.domain import CredentialKind

ENVIRONMENT_NAMES = {
    CredentialKind.LLM: "LLM_API_KEY",
    CredentialKind.OCR: "OCR_API_KEY",
    CredentialKind.EMBEDDING: "EMBED_API_KEY",
    CredentialKind.SEMANTIC_SCHOLAR: "S2_API_KEY",
}
LEGACY_FIELDS = {
    CredentialKind.LLM: "apiKey",
    CredentialKind.OCR: "ocrApiKey",
    CredentialKind.EMBEDDING: "embedApiKey",
    CredentialKind.SEMANTIC_SCHOLAR: "s2ApiKey",
}
KEYRING_USERNAMES = {kind: f"credential:{kind.value}" for kind in CredentialKind}
