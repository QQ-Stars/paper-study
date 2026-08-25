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

**ReproductionProject**:
A user-owned attempt to reproduce one Paper. A Paper may have multiple independent reproduction projects; the project is not a replacement for the Paper or its SourceDocument.
_Avoid_: experiment, ProcessingJob, paper copy

**ReproductionDocument**:
The Markdown record belonging to a ReproductionProject, including its revision and save state. It describes the reproduction plan, method, environment, execution record, results, deviations, and next steps.
_Avoid_: SourceDocument, GeneratedArtifact, ordinary paper note

**ExperimentRun**:
An intentionally recorded, human-described execution of a reproduction experiment. It includes environment, command, parameters, data version, code revision, seed, status, metrics, and result summary. It is not a ProcessingJob and does not execute arbitrary shell commands in the first release.
_Avoid_: background job, task row, ProcessingJob

**ReproductionArtifact**:
An attachment produced or referenced by a ReproductionProject, such as a log, image, table, or model output. It remains distinct from GeneratedArtifact unless a later decision proves the semantics are identical.
_Avoid_: generated artifact, PDF path, SourceDocument

**ReproductionNote**:
An optional short-form note kept inside a ReproductionProject when a separate quick capture is genuinely useful. It must not silently reuse the ordinary paper-note store.
_Avoid_: paper note, ReproductionDocument section

**Reproduction retention**:
Deleting a Paper must never silently cascade-delete its reproduction materials. The production design must preserve the project, document, runs, artifacts, and notes while making the missing Paper relationship explicit (for example through a retained identity snapshot and a nullable association). This is an implementation decision to verify in the migration plan.
