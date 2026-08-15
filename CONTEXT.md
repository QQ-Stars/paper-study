# Study App Research Knowledge Context

This context names the durable concepts used to turn a paper's PDF into reusable study knowledge while preserving the paper's identity and provenance.

## Language

**Paper**:
A research work's metadata and stable identity. Its Paper ID remains unchanged across extraction, generation, search, export, and migration.
_Avoid_: Document, item, record

**SourceDocument**:
The canonical Markdown materialized from a Paper's PDF in either native or OCR mode. It is source material, not an explanation or note.
_Avoid_: OCR result, full text, explainer, note

**SourceMode**:
The explicit choice of `native` or `ocr` used to materialize a SourceDocument. Provider configuration alone never selects OCR.
_Avoid_: OCR enabled, extraction fallback

**GeneratedArtifact**:
Study content derived from one identified SourceDocument, such as an explainer, translation, summary, outline, or study card.
_Avoid_: SourceDocument, note, generated file

**ProcessingJob**:
A durable unit of background processing for OCR, explanation, translation, embedding, or Obsidian projection.
_Avoid_: Ingest job, HTTP stream, task row

**VaultProjection**:
A managed, one-way representation of Study App data in an Obsidian Vault. The application remains the source of truth.
_Avoid_: Vault sync record, exported note

**ProviderProfile**:
The non-secret configuration that identifies how an external model provider is used.
_Avoid_: Credential, API key, settings blob

**Credential**:
A secret used to authenticate with an external provider. A Credential is never part of a ProviderProfile returned to the UI.
_Avoid_: ProviderProfile, key tail

**LegacyProvenance**:
An explicit statement that an existing artifact predates traceable SourceDocument lineage. It preserves uncertainty instead of inventing a source relationship.
_Avoid_: Inferred source, migrated source
