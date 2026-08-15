# P1 Domain and Data Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Execute every checkbox in order. Every production change begins with the named failing test, confirms the intended failure, applies the smallest implementation, and reruns the fully listed target command to green. Work on the independently authorized `codex/` branch created before P0; do not stage files, create a commit, or push.

**Goal:** Add an additive, reversible SQLite domain-data foundation for traceable source material and generated study content while preserving every legacy table, Paper ID, Node workflow, Python Agent contract, React contract, and legacy artifact fallback.

**Architecture:** Domain entities and Application ports contain no ORM types. SQLAlchemy 2 repository adapters and an async Unit of Work live under <code>backend/app/repositories/</code>; SQLite engine/session and WAL policy live under <code>backend/app/infrastructure/</code>; native and generation adapters live under <code>backend/app/providers/</code>. Alembic revision <code>20260807_01</code> creates five empty additive tables. <code>DocumentSourcePipeline</code> materializes canonical Markdown once per cache identity. <code>GenerationPipeline</code> consumes a proven SourceDocument, publishes a versioned GeneratedArtifact, and updates the matching legacy projection in the same SQLite transaction. Reads prefer a ready new artifact only when configured, then fall back to the untouched legacy field when no eligible new row exists.

**Tech Stack:** Python 3.10-compatible code, SQLAlchemy 2.0.43, Alembic 1.16.5, aiosqlite 0.21.0, FastAPI 0.116.1 test-app foundation, keyring 25.6.0 with Windows Credential Manager, Pydantic v2, SQLite WAL, Python standard-library <code>unittest</code>, Node.js 22 compatibility tests, and the existing verified SQLite backup tooling.

**Depends on:** P0.0 must be fully green and P0.1 must satisfy its versioned baseline policy. A fresh Live backup must pass independent <code>verify</code> and isolated <code>restore-check</code>. The P0 legacy HTTP, Python Agent, React Gateway, disabled-OCR, and rollout-default contracts must remain green. The complete frontend suite may be non-zero only when the exact P0.1 verifier returns <code>baselineMatched=true</code> with unchanged IDs/signatures/related hashes, no touched related path, the raw non-zero exit preserved, and <code>overallGreen=false</code>; any drift stops P1.

**Workspace constraints:** Do not modify <code>AGENTS.md</code>, <code>.agents/</code>, <code>public/</code>, React layout/styles/routes, or user artifact files. Tests and migration drills use process-owned temporary databases. The user has authorized the Live additive Alembic upgrade after the isolated rehearsal, fresh verified backup, writer-stop check, legacy compatibility suite, and pre-upgrade fingerprint are all green; no further user confirmation is required, and any failed gate stops the upgrade. Do not delete or recreate <code>.venv</code>. The current Python 3.10.9 environment is a supported P1 baseline; a Python runtime upgrade is a separate operations decision.

---

## Current Evidence This Plan Builds On

- <code>db/schema.sql:5-152</code> creates the legacy tables and must remain the Node-owned legacy schema source.
- <code>db.js:7-27</code> opens <code>DB_PATH</code>, enables WAL, foreign keys, and a 5,000 ms busy timeout, then applies legacy startup schema work.
- <code>db.js:66-171</code> reads and writes Paper metadata, explainers, translations, notes, progress, and favorites.
- <code>agent/db.py:8-27,79-148</code> uses synchronous sqlite3 and performs current legacy artifact writes.
- <code>agent/extract.py:41-94</code> performs native pymupdf4llm extraction with PyMuPDF plain-text fallback and no OCR call.
- <code>agent/explain.py:31-129</code> writes explainers to <code>papers.explainer</code>.
- <code>agent/translate.py:125-183</code> writes translations to <code>translations.content</code>.
- <code>agent/importer.py:40-143</code> establishes the existing local PDF path rules.
- <code>lib/settings.js:48-57,63-100</code> currently reads plaintext <code>settings.json.apiKey</code>, <code>s2ApiKey</code>, and <code>embedApiKey</code>, falls back to <code>LLM_API_KEY</code> for the LLM key, and exposes only masked key status to the UI; the cross-phase canonical store also reserves <code>ocrApiKey</code> for the OCR kind.
- <code>lib/settings.js:103-118</code> already treats a blank credential update as preserve-existing rather than clear.
- <code>agent/config.py:6-41</code> currently loads <code>data/settings.json</code> before process environment for the LLM key; P1 CredentialStore reverses that priority without changing the P1 Node owner.
- <code>backend/app/infrastructure/database_backup.py:602-683,806-827</code> already fingerprints table counts, table hashes, integrity, foreign keys, and the single Alembic head.
- <code>requirements.txt:1-11</code> does not yet install SQLAlchemy, Alembic, aiosqlite, or FastAPI.
- The current <code>.venv</code> reports Python 3.10.9. The dependency versions in this plan support Python 3.10.

---

## Ubiquitous Language and Public Interfaces

Use these names in domain, Application, repository, provider, API, test, and documentation code:

| Term | P1 meaning |
|---|---|
| <code>Paper</code> | Existing paper metadata and stable <code>papers.id</code>; P1 never rekeys it. |
| <code>SourceDocument</code> | Canonical Markdown materialized from one Paper PDF in explicit native or OCR mode. |
| <code>GeneratedArtifact</code> | Versioned generated content derived from one proven SourceDocument. |
| <code>ProcessingJob</code> | Durable paper-scoped or global background-work identity whose public status is exactly <code>queued</code>, <code>running</code>, <code>succeeded</code>, <code>failed</code>, or <code>cancelled</code>. P1 stores the schema; P2 implements queue execution. |
| <code>VaultProjection</code> | One-way managed projection represented by <code>obsidian_exports</code>. |
| <code>ProviderProfile</code> | Non-secret provider configuration. |
| <code>Credential</code> | Secret authentication material whose value is redacted from repr, logs, DTOs, and error details. |

<code>sourceMode</code> and domain <code>SourceMode</code> accept exactly <code>native</code> or <code>ocr</code>. P1 implements only the native provider. An OCR request in P1 fails before any OCR factory, transport, database insert, or retry is constructed.

The two required Application interfaces are:

~~~python
materialize_source(paper_id, source_mode, purpose) -> SourceDocument
generate_artifact(paper_id, artifact_kind, source_mode) -> GeneratedArtifact
~~~

The concrete methods are asynchronous because their repositories use aiosqlite:

~~~python
class DocumentSourcePipeline:
    async def materialize_source(
        self,
        paper_id: str,
        source_mode: SourceMode,
        purpose: str,
    ) -> SourceDocument:
        raise NotImplementedError


class GenerationPipeline:
    async def generate_artifact(
        self,
        paper_id: str,
        artifact_kind: ArtifactKind,
        source_mode: SourceMode,
    ) -> GeneratedArtifact:
        raise NotImplementedError
~~~

<code>purpose</code> is a required nonblank caller reason used for diagnostics. It never changes SourceDocument content and is not part of the cache identity. Canonical ArtifactKind values are exactly <code>explainer|translation|summary|outline|study_card|classification|metadata</code>. The initial generation adapter supports the existing explainer/translation behaviors and returns a typed unsupported-kind error for the other five values; P3 implements classification/metadata as provenance-bearing GeneratedArtifacts.

### CredentialStore contract

Credential kinds are exactly <code>llm|ocr|embedding|semantic_scholar</code>. The internal Application port is:

~~~python
class CredentialStore(Protocol):
    async def get(self, kind: CredentialKind) -> Credential | None:
        raise NotImplementedError

    async def is_configured(self, kind: CredentialKind) -> bool:
        raise NotImplementedError

    async def key_tail(self, kind: CredentialKind) -> str | None:
        raise NotImplementedError

    async def update(
        self,
        kind: CredentialKind,
        submitted_value: str,
    ) -> CredentialStatus:
        raise NotImplementedError

    async def clear(self, kind: CredentialKind) -> CredentialStatus:
        raise NotImplementedError
~~~

Only provider adapters may call the internal <code>get</code> method. HTTP/application status DTOs contain <code>kind</code>, <code>hasKey</code>, <code>keyTail</code>, and <code>environmentManaged</code>; they never contain Credential, secret value, plaintext length, hash, or reversible ciphertext. <code>keyTail</code> is null when absent, contains exactly <code>****</code> when the configured secret has fewer than eight characters, and otherwise contains exactly <code>****</code> plus the last four characters.

Effective read priority is fixed:

| Kind | Process environment | Keyring username under service <code>study-app</code> | Legacy <code>data/settings.json</code> field |
|---|---|---|---|
| <code>llm</code> | <code>LLM_API_KEY</code> | <code>credential:llm</code> | <code>apiKey</code> |
| <code>ocr</code> | <code>OCR_API_KEY</code> | <code>credential:ocr</code> | <code>ocrApiKey</code> |
| <code>embedding</code> | <code>EMBED_API_KEY</code> | <code>credential:embedding</code> | <code>embedApiKey</code> |
| <code>semantic_scholar</code> | <code>S2_API_KEY</code> | <code>credential:semantic_scholar</code> | <code>s2ApiKey</code> |

For every kind, effective priority is process environment → Keyring → legacy field → not configured.

The environment snapshot is immutable for the process lifetime. Environment credentials are read-only and remain authoritative after <code>update</code> or <code>clear</code>. A blank or whitespace-only submitted value is an intentional no-op that preserves the existing credential and performs zero writes. A nonblank update writes the secure Keyring tier, verifies read-back, and during P1 compatibility also keeps the legacy field synchronized for the Node owner. Explicit <code>clear</code> removes both writable tiers; an environment credential remains effective and returns <code>environmentManaged=true</code>.

On first P1 CredentialStore use for a kind, its legacy value is imported into Keyring and verified by constant-time comparison. P1 deliberately retains all four legacy plaintext fields because the frozen Node rollback path has no Keyring reader. This is a documented compatibility-period security debt, not the steady state. <code>finalize_legacy_migration</code> is a separate future operation allowed only after the Node rollback window is formally retired; no P0-P6 plan calls it. That future operation must atomically remove <code>apiKey|ocrApiKey|embedApiKey|s2ApiKey</code> while preserving all non-secret and unknown settings. P1 does not create a Node-to-Keyring bridge and does not expose a secret-returning CLI.

Credential connection tests accept only <code>CredentialKind</code>; they accept no Paper ID, PDF path, PDF bytes, prompt, or user payload. The LLM probe reads a packaged fixed text fixture. The OCR probe is structurally limited to a packaged synthetic PNG. Because P1 has no verified OCR provider contract, production OCR probing returns <code>OCR_PROVIDER_CONTRACT_UNVERIFIED</code> with zero transport calls. P1 exposes no embedding or Semantic Scholar network probe contract, so those two kinds return <code>CREDENTIAL_PROBE_UNSUPPORTED</code> before transport construction. Injected fakes prove that no probe can consume user content.

### Historical provenance rule

- Revision <code>20260807_01</code> performs no data backfill.
- It never invents <code>source_document_id</code> for <code>papers.explainer</code>, <code>translations.content</code>, notes, or files.
- A new-first read returns a ready GeneratedArtifact only when its real SourceDocument relationship exists.
- If no eligible ready row exists, the reader returns the legacy value with explicit legacy provenance and a null GeneratedArtifact/SourceDocument identity.
- Historical explainers and translations stay solely in legacy storage until a later, separately audited migration can prove their SourceDocument provenance.

---

## Authoritative SQLite Schema Contract

Revision metadata is fixed:

~~~python
revision = "20260807_01"
down_revision = None
~~~

P1 creates exactly five domain tables. Alembic also manages its own <code>alembic_version</code> table. P1 does not delete or rename any legacy table or column.

### document_sources

Required columns, in migration order:

~~~text
id
paper_id
mode
status
provider
model
pdf_sha256
options_hash
content_sha256
markdown
page_count
processing_version
error_code
error_message
created_at
updated_at
~~~

Required constraints:

- <code>id</code> is a TEXT primary key.
- <code>paper_id</code> is non-null and references <code>papers.id</code> with <code>ON DELETE CASCADE</code>.
- <code>mode</code> is non-null and checked against exactly <code>native|ocr</code>.
- <code>status</code> is non-null and checked against exactly <code>queued|running|ready|failed|stale|cancelled</code>.
- <code>provider</code>, <code>model</code>, and <code>processing_version</code> are non-null and nonblank. Native rows use the stable identities <code>local</code>, <code>pymupdf4llm-pymupdf</code>, and <code>native-v1</code>, so nullable uniqueness cannot weaken the cache constraint.
- <code>pdf_sha256</code> and <code>options_hash</code> are non-null.
- SHA-256 fields contain lowercase 64-character hexadecimal strings when non-null.
- <code>page_count</code> is null or nonnegative.
- A ready row requires nonblank Markdown and a content hash; a failed row requires a nonblank error code.
- The exact cache UNIQUE key is <code>(paper_id,pdf_sha256,mode,provider,model,options_hash,processing_version)</code>.
- Index <code>ix_document_sources_paper_status</code> covers <code>(paper_id,status,updated_at)</code>.

### generated_artifacts

Required columns, in migration order:

~~~text
id
paper_id
kind
source_document_id
status
content
content_sha256
generator_provider
generator_model
prompt_version
error_code
error_message
created_at
updated_at
~~~

Required constraints:

- <code>id</code> is a TEXT primary key.
- <code>paper_id</code> is non-null and references <code>papers.id</code> with <code>ON DELETE CASCADE</code>.
- <code>source_document_id</code> is non-null and references <code>document_sources.id</code> with <code>ON DELETE CASCADE</code>.
- <code>status</code> is non-null and checked against <code>queued|running|ready|failed|stale|cancelled</code>.
- <code>kind</code>, <code>generator_provider</code>, <code>generator_model</code>, and <code>prompt_version</code> are non-null and nonblank.
- <code>kind</code> remains TEXT without a database enum CHECK so compatibility evolution does not require rebuilding the table; the domain ArtifactKind enum enforces the seven canonical values.
- <code>content_sha256</code> is null or a lowercase 64-character hexadecimal string.
- A ready row requires nonblank content and a content hash; a failed row requires a nonblank error code.
- UNIQUE <code>(source_document_id,kind,generator_provider,generator_model,prompt_version)</code> is the source/kind/version identity.
- Index <code>ix_generated_artifacts_paper_kind_status</code> covers <code>(paper_id,kind,status,updated_at)</code>.
- Index <code>ix_generated_artifacts_source</code> covers <code>source_document_id</code>.
- Repository compare-and-set rules make a ready row immutable. A failed result may never update a ready row's status, content, hash, provider metadata, timestamps, or error fields.

### processing_jobs

Required columns, in migration order:

~~~text
id
paper_id
job_type
source_mode
status
progress_json
attempt
max_attempts
idempotency_key
error_code
error_message
created_at
started_at
finished_at
cancelled_at
~~~

Required constraints:

- <code>id</code> is a TEXT primary key.
- A non-null <code>paper_id</code> references <code>papers.id</code> with <code>ON DELETE CASCADE</code>.
- <code>job_type</code> is non-null and checked against exactly <code>source_materialize|ocr|explain|translate|embed|obsidian_export|obsidian_sync</code>. Native source work uses <code>source_materialize</code>; only explicit OCR source work uses <code>ocr</code>.
- <code>source_mode</code> is null or checked against exactly <code>native|ocr</code>.
- <code>source_materialize</code>, <code>ocr</code>, <code>explain</code>, <code>translate</code>, and <code>embed</code> require both non-null <code>paper_id</code> and non-null <code>source_mode</code>.
- <code>obsidian_export</code> requires non-null <code>paper_id</code> and permits null <code>source_mode</code>.
- <code>obsidian_sync</code> permits null <code>paper_id</code> and null <code>source_mode</code> so one job can represent a global VaultProjection sync.
- Cross-field CHECK constraints require <code>job_type='source_materialize'</code> to use <code>source_mode='native'</code> and <code>job_type='ocr'</code> to use <code>source_mode='ocr'</code>. A native source job cannot be labeled as OCR.
- <code>status</code> is non-null and checked against exactly <code>queued|running|succeeded|failed|cancelled</code>.
- <code>progress_json</code> is non-null, <code>attempt</code> is nonnegative, and <code>max_attempts</code> is at least one.
- <code>idempotency_key</code> is non-null and globally UNIQUE.
- Index <code>ix_processing_jobs_status_created</code> covers <code>(status,created_at)</code>.
- Index <code>ix_processing_jobs_paper_created</code> covers <code>(paper_id,created_at)</code>.

The migration expresses job scope with these exact CHECK predicates:

~~~sql
CHECK (source_mode IS NULL OR source_mode IN ('native','ocr'))
CHECK (
  (
    job_type IN ('source_materialize','ocr','explain','translate','embed')
    AND paper_id IS NOT NULL
    AND source_mode IS NOT NULL
  )
  OR (job_type = 'obsidian_export' AND paper_id IS NOT NULL)
  OR job_type = 'obsidian_sync'
)
CHECK (job_type <> 'source_materialize' OR source_mode = 'native')
CHECK (job_type <> 'ocr' OR source_mode = 'ocr')
~~~

### document_chunks

Required columns, in migration order:

~~~text
id
source_document_id
sequence
heading_path
page_start
page_end
content
content_sha256
token_count
~~~

Required constraints:

- <code>id</code> is a TEXT primary key.
- <code>source_document_id</code> is non-null and references <code>document_sources.id</code> with <code>ON DELETE CASCADE</code>.
- <code>sequence</code> and <code>token_count</code> are nonnegative.
- Page values are null or nonnegative, and <code>page_end</code> cannot precede <code>page_start</code>.
- <code>content</code> is non-null; <code>content_sha256</code> is a lowercase 64-character hexadecimal value.
- UNIQUE <code>(source_document_id,sequence)</code>.
- Index <code>ix_document_chunks_source</code> covers <code>source_document_id</code>.
- P1 creates and validates this empty table; P3 is its first consumer.

### obsidian_exports

Required columns, in migration order:

~~~text
id
paper_id
artifact_id
target_path
source_hash
exported_hash
status
exported_at
error_message
~~~

Required constraints:

- <code>id</code> is a TEXT primary key.
- <code>paper_id</code> is non-null and references <code>papers.id</code> with <code>ON DELETE CASCADE</code>, preserving the existing Paper hard-delete behavior.
- A non-null <code>artifact_id</code> references <code>generated_artifacts.id</code> with <code>ON DELETE SET NULL</code>.
- <code>target_path</code> is non-null and globally UNIQUE.
- Hash fields are null or lowercase 64-character hexadecimal values.
- <code>status</code> is non-null. P5 owns its projection state machine and reuses this table rather than creating a replacement.
- Index <code>ix_obsidian_exports_paper_status</code> covers <code>(paper_id,status)</code>.
- Index <code>ix_obsidian_exports_artifact</code> covers <code>artifact_id</code>.
- Paper deletion may cascade only the application database ledger row. It must never invoke a Vault provider or delete/move a managed Vault file. P5 keeps the manifest entry as an orphan/tombstone containing Paper ID, target path, source hash, and exported hash; automatic cleanup requires live ledger plus manifest plus file marker/hash, so an orphan is reported for manual review and never auto-cleaned.

All timestamps are UTC ISO-8601 strings. Repositories set them explicitly from an injected clock. Migration defaults exist only to protect direct inserts; domain code never depends on local time.

### Identity and hashing rules

- PDF hashes use SHA-256 over the exact file bytes.
- Markdown/content hashes use SHA-256 over normalized UTF-8 bytes after CRLF-to-LF conversion, removal of trailing horizontal whitespace, and one final newline.
- Options use UTF-8 canonical JSON with sorted keys and separators <code>(",",":")</code>. P1 native options are the empty object, whose SHA-256 is <code>44136fa355b3678a1146ad16f7e8649e94fb4fc21fe77e8310c060f61caaff8a</code>.
- Source cache identity is the exact seven-column UNIQUE tuple already specified; purpose and timestamps are excluded.
- GeneratedArtifact version identity is the exact five-column UNIQUE tuple already specified; content and timestamps are excluded.
- New row IDs use a lowercase UUID4 hex payload with fixed prefixes <code>src_</code>, <code>art_</code>, <code>job_</code>, <code>chk_</code>, and <code>exp_</code>. Paper IDs are never transformed.

---

## File Responsibilities

### Existing files modified by this plan

- <code>requirements.in</code>: preserve the existing direct dependencies and add the exact Python 3.10-compatible SQLAlchemy, Alembic, aiosqlite, FastAPI, and keyring pins.
- <code>requirements.txt</code>: generated transitive lock with exact versions and concrete SHA-256 artifact hashes for every distribution; this is the only production/test install input.
- <code>backend/app/infrastructure/database_backup.py</code>: expose its existing read-only database fingerprint through a public inspection function without changing create, verify, or restore semantics.
- <code>backend/app/cli/database_backup.py</code>: add a read-only <code>inspect</code> command that emits the existing fingerprint schema.
- <code>backend/tests/test_database_backup.py</code>: cover the read-only inspection command and ensure it does not create sidecars or mutate the inspected database.
- <code>docs/DATABASE.md</code>: document P1 schema, provenance, migration rehearsal, rollout, runtime rollback, and guarded downgrade.

### New files created by this plan

- <code>backend/alembic.ini</code>: Alembic configuration rooted with <code>%(here)s</code>.
- <code>backend/migrations/__init__.py</code> and <code>backend/migrations/versions/__init__.py</code>: migration package markers used by contract tests.
- <code>backend/migrations/env.py</code>: synchronous Alembic environment reading only the resolved <code>DB_PATH</code> SQLite file.
- <code>backend/migrations/script.py.mako</code>: deterministic revision template.
- <code>backend/migrations/versions/20260807_01_domain_data_foundation.py</code>: the five-table additive migration and guarded downgrade.
- <code>backend/app/config.py</code>: immutable backend database settings and path validation.
- <code>backend/app/domain/__init__.py</code>: domain package exports.
- <code>backend/app/domain/entities.py</code>: immutable domain entities, enums, hashes, timestamps, and state invariants.
- <code>backend/app/domain/errors.py</code>: typed domain/application errors with stable safe codes.
- <code>backend/app/application/ports/__init__.py</code>: Application-port package exports.
- <code>backend/app/application/ports/repositories.py</code>: Paper, SourceDocument, GeneratedArtifact, ProcessingJob, and VaultProjection repository Protocols.
- <code>backend/app/application/ports/unit_of_work.py</code>: async UnitOfWork Protocol.
- <code>backend/app/application/ports/source_extractor.py</code>: Native extraction request/result Protocol.
- <code>backend/app/application/ports/artifact_generator.py</code>: generation request/result Protocol.
- <code>backend/app/application/ports/credential_store.py</code>: internal four-kind CredentialStore Protocol.
- <code>backend/app/application/ports/credential_probe.py</code>: fixed-fixture connection-probe Protocol.
- <code>backend/app/application/credentials.py</code>: credential status, blank-preserving update, explicit clear, compatibility migration, and connection-test use cases.
- <code>backend/app/application/source_documents.py</code>: DocumentSourcePipeline.
- <code>backend/app/application/generated_artifacts.py</code>: GenerationPipeline and new-first/legacy-fallback ArtifactReader.
- <code>backend/app/infrastructure/database.py</code>: async SQLite engine/session factory and connection PRAGMAs.
- <code>backend/app/repositories/__init__.py</code>: repository-adapter package exports.
- <code>backend/app/repositories/models.py</code>: SQLAlchemy 2 mappings for the five new tables plus the legacy projection columns used by dual write.
- <code>backend/app/repositories/sqlalchemy.py</code>: concrete SQLAlchemy repository adapters.
- <code>backend/app/repositories/unit_of_work.py</code>: SqlAlchemyUnitOfWork.
- <code>backend/app/providers/__init__.py</code>: provider-adapter package exports.
- <code>backend/app/providers/native.py</code>: NativeExtractor with pymupdf4llm then PyMuPDF fallback and no OCR dependency.
- <code>backend/app/providers/generation.py</code>: adapter around existing explainer and translation model behavior; it consumes SourceDocument Markdown only.
- <code>backend/app/providers/credentials/__init__.py</code>: credential-provider package exports.
- <code>backend/app/providers/credentials/environment.py</code>: immutable read-only <code>LLM_API_KEY|OCR_API_KEY|EMBED_API_KEY|S2_API_KEY</code> adapter.
- <code>backend/app/providers/credentials/keyring.py</code>: keyring adapter using service <code>study-app</code> and usernames <code>credential:llm|credential:ocr|credential:embedding|credential:semantic_scholar</code>.
- <code>backend/app/providers/credentials/legacy_settings.py</code>: hash-guarded atomic compatibility reader/writer for <code>data/settings.json</code>.
- <code>backend/app/providers/credentials/composite.py</code>: environment-first selection, verified Keyring import, legacy compatibility, update, and clear orchestration.
- <code>backend/app/providers/credentials/probe.py</code>: fixed-fixture LLM/OCR connectivity adapter, unverified-OCR transport gate, and zero-transport unsupported result for embedding/Semantic Scholar.
- <code>backend/app/providers/credentials/fixtures/llm-probe.txt</code>: non-sensitive fixed LLM connectivity prompt.
- <code>backend/app/providers/credentials/fixtures/ocr-probe.png.base64</code>: fixed 68-byte non-sensitive PNG encoded as text; it contains no Paper/user data.
- <code>backend/app/api/__init__.py</code>: API package exports.
- <code>backend/app/api/errors.py</code>: one safe error DTO <code>{error:{code,message,details}}</code>; P4 must modify this file in place and must not replace it with a same-named package directory under <code>backend/app/api</code>.
- <code>backend/app/api/router.py</code>: empty <code>/api/v2</code> extension seam plus health/schema contract.
- <code>backend/app/api/app.py</code>: dependency-injected FastAPI test application factory; it does not bind a production port.
- <code>backend/app/bootstrap.py</code>: composition root for settings, session factory, repositories, providers, pipelines, and rollout mode validation.
- <code>backend/tests/support/__init__.py</code>: backend test-support package marker.
- <code>backend/tests/support/p1_database.py</code>: deterministic legacy SQLite fixture, Alembic runner, schema inspector, and canonical row hashing for tests only.
- <code>backend/tests/test_p1_runtime_contract.py</code>: Python/dependency/config compatibility.
- <code>backend/tests/test_p1_domain.py</code>: entity and invariant tests.
- <code>backend/tests/test_p1_migration.py</code>: exact DDL, upgrade, downgrade, re-upgrade, count/hash, and constraint tests.
- <code>backend/tests/test_p1_repositories.py</code>: async repository and Unit of Work tests.
- <code>backend/tests/test_native_extractor.py</code>: native extraction and zero-OCR tests.
- <code>backend/tests/test_document_source_pipeline.py</code>: materialization, caching, hashing, failure, and concurrency tests.
- <code>backend/tests/test_generation_pipeline.py</code>: generation, provenance, new-first fallback, transactional dual write, and ready-row immutability tests.
- <code>backend/tests/test_credentials.py</code>: credential priority, Keyring, migration, redaction, update/clear, and fixed-fixture probe tests.
- <code>backend/tests/test_api_foundation.py</code>: test-app factory, error DTO, schema-head, and rollout composition tests.
- <code>backend/tests/test_p1_documentation_contract.py</code>: operational documentation guard.

No P1 worker file is created. Durable worker behavior begins under plural <code>backend/app/workers/</code> in P2.

---

## Task 0: Protect the Workspace and Satisfy P1 Entry Gates

**Files:**

- Verify: P0 implementation and tests
- Verify: <code>data/app.db</code>
- Verify: <code>.venv</code>

- [ ] **Step 1: Record the existing worktree without changing it (2-5 minutes)**

Run:

~~~powershell
git status --short --branch
~~~

Expected: record every existing user-owned modification and untracked file. Stop if either P1 target plan file overlaps an unexpected concurrent edit. Do not clean, restore, stage, or rewrite unrelated paths.

- [ ] **Step 2: Confirm the P0 backup and compatibility suites are green (2-5 minutes per command)**

Run:

~~~powershell
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
function Invoke-CheckedNative {
  param([Parameter(Mandatory = $true)][string]$Label, [Parameter(Mandatory = $true)][scriptblock]$Command)
  $output = & $Command
  $exitCode = $LASTEXITCODE
  if ($exitCode -ne 0) { throw "$Label failed with exit code $exitCode." }
  $output
}
$env:PYTHONDONTWRITEBYTECODE = '1'
Invoke-CheckedNative 'P0 backend tests' { .\.venv\Scripts\python.exe -B -m unittest discover -s backend/tests -p "test_*.py" -v }
Invoke-CheckedNative 'P0 legacy Python tests' { .\.venv\Scripts\python.exe -B -m unittest discover -s test -p "test_*.py" -v }
Invoke-CheckedNative 'P0 Node compatibility tests' { node --test test/legacy-api-contract.test.js test/backend-rollout.test.js }
Invoke-CheckedNative 'P0 React Gateway guard' { npm.cmd run test:run --prefix frontend -- build/legacyGatewayGuard.test.ts }
$p1BaselineJson = Invoke-CheckedNative 'P1 entry full frontend baseline verification' { node scripts/pre-existing-failure-baseline.mjs verify --baseline contracts/pre-existing-test-failures-v1.json }
$p1Baseline = $p1BaselineJson | ConvertFrom-Json
$p1BaselineRequiredFields = @('baselineMatched','observedSuiteExitCode','overallGreen')
foreach ($p1BaselineField in $p1BaselineRequiredFields) {
  if (-not ($p1Baseline.PSObject.Properties.Name -contains $p1BaselineField)) { throw "P1 entry baseline verifier omitted required field $p1BaselineField." }
}
if ($p1Baseline.baselineMatched -isnot [bool] -or $p1Baseline.baselineMatched -ne $true) { throw 'P1 entry baseline verifier did not report boolean baselineMatched=true.' }
if ($p1Baseline.observedSuiteExitCode -isnot [int] -and $p1Baseline.observedSuiteExitCode -isnot [long]) { throw 'P1 entry baseline verifier did not report an integer observedSuiteExitCode.' }
if ($p1Baseline.overallGreen -isnot [bool]) { throw 'P1 entry baseline verifier did not report boolean overallGreen.' }
$p1ObservedSuiteExitCode = [long]$p1Baseline.observedSuiteExitCode
if (($p1ObservedSuiteExitCode -eq 0) -ne $p1Baseline.overallGreen) { throw 'P1 entry baseline verifier reported inconsistent observedSuiteExitCode and overallGreen.' }
~~~

Expected: backend/legacy/Node/Gateway guard commands exit 0; the exact full frontend verifier exits 0 only for an exact v1 match. A raw frontend 0 reports <code>overallGreen=true</code>; an accepted raw non-zero remains visible as <code>observedSuiteExitCode</code> with <code>overallGreen=false</code>. The legacy API ledger is complete, OCR-disabled construction/call counters are zero, and rollout values default to legacy/off. Stop on any command failure or baseline drift because P1 must not redefine a failing baseline.

- [ ] **Step 3: Verify the current Python runtime is supported without rebuilding it (2-5 minutes)**

Run:

~~~powershell
.\.venv\Scripts\python.exe -c "import sys; print(sys.version); assert sys.version_info >= (3, 10), sys.version"
~~~

Expected on this workspace: Python 3.10.9 and exit code 0. Do not require a newer runtime, delete <code>.venv</code>, or create a replacement environment.

- [ ] **Steps 4–6: Create, independently verify, and restore-check one fresh pre-P1 Live backup (6–15 minutes)**

Run this as one self-contained block. Do not split it into separate shells or type a backup path from memory:

~~~powershell
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
function Invoke-CheckedNative {
  param(
    [Parameter(Mandatory = $true)][string]$Label,
    [Parameter(Mandatory = $true)][scriptblock]$Command
  )
  $output = & $Command
  $exitCode = $LASTEXITCODE
  if ($exitCode -ne 0) { throw "$Label failed with exit code $exitCode." }
  $output
}

$p1CreateJson = Invoke-CheckedNative 'Pre-P1 backup create' {
  .\.venv\Scripts\python.exe -m backend.app.cli.database_backup create --database data/app.db --output-directory data/backups --label pre-p1-domain-data
}
$p1Create = $p1CreateJson | ConvertFrom-Json
if (-not $p1Create.ok) { throw 'Pre-P1 backup creation did not return ok=true.' }

$p1VerifyJson = Invoke-CheckedNative 'Pre-P1 independent backup verify' {
  .\.venv\Scripts\python.exe -m backend.app.cli.database_backup verify --backup $p1Create.backupPath --manifest $p1Create.manifestPath
}
$p1Verify = $p1VerifyJson | ConvertFrom-Json
if (-not $p1Verify.ok) { throw 'Pre-P1 independent verification did not return ok=true.' }
if ($p1Create.logicalSha256 -ne $p1Verify.logicalSha256) { throw 'Create and verify logical SHA-256 values differ.' }

$p1RestoreRoot = New-Item -ItemType Directory -Path (Join-Path ([System.IO.Path]::GetTempPath()) ("study-app-p1-" + [Guid]::NewGuid().ToString("N")))
$p1RestoreJson = Invoke-CheckedNative 'Pre-P1 isolated restore-check' {
  .\.venv\Scripts\python.exe -m backend.app.cli.database_backup restore-check --backup $p1Create.backupPath --manifest $p1Create.manifestPath --output-directory $p1RestoreRoot.FullName
}
$p1Restore = $p1RestoreJson | ConvertFrom-Json
if (-not $p1Restore.ok) { throw 'Pre-P1 restore-check did not return ok=true.' }
if ($p1Verify.logicalSha256 -ne $p1Restore.logicalSha256) { throw 'Verify and restore-check logical SHA-256 values differ.' }
$p1RestorePath = (Resolve-Path -LiteralPath $p1Restore.restoredPath).Path
$p1RestorePrefix = $p1RestoreRoot.FullName.TrimEnd([IO.Path]::DirectorySeparatorChar, [IO.Path]::AltDirectorySeparatorChar) + [IO.Path]::DirectorySeparatorChar
if (-not $p1RestorePath.StartsWith($p1RestorePrefix, [StringComparison]::OrdinalIgnoreCase)) { throw 'Pre-P1 restore-check escaped its process-owned temporary root.' }
~~~

Expected: create, independent verify, and isolated restore-check all report <code>ok=true</code> and identical logical SHA-256 values; verification reports <code>quickCheck=ok</code>, <code>integrityCheck=ok</code>, and zero foreign-key violations. The restored copy is contained by <code>$p1RestoreRoot</code> and has no WAL/SHM sidecar. Preserve <code>$p1Create</code>, <code>$p1Verify</code>, <code>$p1Restore</code>, and <code>$p1RestoreRoot</code> in this operator session for Task 11. If the session is lost, repeat the entire block rather than typing paths from memory.

---

## Task 1: Pin a Python 3.10-Compatible Data Runtime

**Files:**

- Create: <code>backend/tests/test_p1_runtime_contract.py</code>
- Create: <code>backend/app/config.py</code>
- Create: <code>requirements.in</code> from the current direct dependency declarations
- Modify: <code>requirements.txt</code> to the hash-locked compiled output

- [ ] **Step 1: Write the dependency and DB_PATH configuration RED test (2-5 minutes)**

Create tests named:

- <code>test_supported_runtime_and_exact_data_dependency_versions</code>
- <code>test_database_settings_resolve_one_sqlite_file_without_creating_it</code>
- <code>test_database_settings_reject_directory_missing_parent_and_non_file_target</code>
- <code>test_database_settings_are_immutable_and_secret_free</code>

The test must import SQLAlchemy, Alembic, aiosqlite, FastAPI, keyring, and <code>backend.app.config.DatabaseSettings</code>; assert Python 3.10 or newer; assert all exact direct versions listed below; parse <code>requirements.txt</code> and require exact versions plus at least one concrete SHA-256 artifact digest for every non-local distribution; resolve an injected <code>DB_PATH</code>; and prove constructing settings creates no directory, database, sidecar, or log output.

- [ ] **Step 2: Run the test and confirm intended RED (2-5 minutes)**

Run:

~~~powershell
$env:PYTHONDONTWRITEBYTECODE = '1'
.\.venv\Scripts\python.exe -B -m unittest backend.tests.test_p1_runtime_contract -v
~~~

Expected: FAIL because at least one pinned data dependency or <code>backend.app.config</code> is missing. A Python-version assertion, malformed test, or import failure from an unrelated project module is not the intended RED.

- [ ] **Step 3: Add the smallest compatible dependency and settings implementation (2-5 minutes per edit)**

Create <code>requirements.in</code> with this complete exact direct set; comments may be retained but no range specifier is permitted:

~~~text
httpx==0.28.1
feedparser==6.0.12
openai==2.41.0
anthropic==0.107.1
pydantic==2.13.4
python-dotenv==1.2.2
tenacity==9.1.4
pymupdf==1.27.2.3
pymupdf4llm==1.27.2.3
model2vec==0.8.2
mcp==1.27.2
SQLAlchemy==2.0.43
alembic==1.16.5
aiosqlite==0.21.0
fastapi==0.116.1
uvicorn==0.49.0
anyio==4.13.0
keyring==25.6.0
~~~

Compile the complete environment with <code>pip-tools==7.5.1</code> by running <code>python -m piptools compile --generate-hashes --resolver=backtracking --output-file requirements.txt requirements.in</code>. The compiled <code>requirements.txt</code> must contain no <code>&gt;=</code>, wildcard, editable URL, VCS URL, or unhashed distribution. Generate it once during this task, review the full diff, and commit neither an environment snapshot nor wheel files.

Install only the exact compile tool version before generating the lock: <code>.\.venv\Scripts\python.exe -m pip install --require-virtualenv pip-tools==7.5.1</code>. Record its version in the lock-generation evidence; a different resolver/tool version requires a full recompile and lock diff review.

Implement a frozen <code>DatabaseSettings</code> that accepts an injected path or reads <code>DB_PATH</code>, resolves it once, requires an existing regular SQLite file and parent directory, rejects a missing path or directory target, exposes no credentials, and does not open or create SQLite during construction.

Install into the existing environment from the exact lock only:

~~~powershell
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
function Invoke-CheckedNative {
  param([Parameter(Mandatory = $true)][string]$Label, [Parameter(Mandatory = $true)][scriptblock]$Command)
  $output = & $Command
  $exitCode = $LASTEXITCODE
  if ($exitCode -ne 0) { throw "$Label failed with exit code $exitCode." }
  $output
}
Invoke-CheckedNative 'locked dependency install' { .\.venv\Scripts\python.exe -m pip install --require-virtualenv --require-hashes -r requirements.txt }
Invoke-CheckedNative 'Python dependency check' { .\.venv\Scripts\python.exe -m pip check }
~~~

Expected: installation completes in the existing <code>.venv</code> using only lockfile-declared artifacts; <code>pip check</code> exits 0. Do not run a venv creation command, install from <code>requirements.in</code>, or manually edit one transitive dependency without recompiling the lock.

- [ ] **Step 4: Rerun the target test as GREEN evidence (2-5 minutes)**

Run:

~~~powershell
$env:PYTHONDONTWRITEBYTECODE = '1'
.\.venv\Scripts\python.exe -B -m unittest backend.tests.test_p1_runtime_contract -v
~~~

Expected: all runtime/config tests pass on Python 3.10.9, settings construction performs zero filesystem writes, and exact dependency versions match.

---

## Task 2: Define the Domain Without ORM Leakage

**Files:**

- Create: <code>backend/tests/test_p1_domain.py</code>
- Create: <code>backend/app/domain/__init__.py</code>
- Create: <code>backend/app/domain/entities.py</code>
- Create: <code>backend/app/domain/errors.py</code>

- [ ] **Step 1: Write domain entity and invariant RED tests (2-5 minutes per entity family)**

Cover:

- the seven required domain terms and no SQLAlchemy import in domain modules;
- <code>SourceMode</code> accepts only native/ocr;
- SourceDocument statuses accept only queued/running/ready/failed/stale/cancelled;
- ProcessingJob statuses accept only queued/running/succeeded/failed/cancelled;
- ArtifactKind accepts exactly explainer/translation/summary/outline/study_card/classification/metadata;
- ProcessingJob <code>paper_id</code> and <code>source_mode</code> are optional at the DTO level, then job-type invariants require both for source_materialize/ocr/explain/translate/embed, require Paper only for obsidian_export, and allow both null for global obsidian_sync;
- source_materialize/native and ocr/ocr are the only valid source-job pairings;
- ready SourceDocument content/hash and failed SourceDocument error-code invariants;
- ready GeneratedArtifact content/hash/source identity and failed artifact error-code invariants;
- nonnegative page, attempt, sequence, and token counts;
- immutable dataclasses and UTC-aware timestamp normalization;
- lowercase SHA-256 validation;
- ProviderProfile contains no secret field;
- CredentialKind accepts only llm/ocr/embedding/semantic_scholar and CredentialStatus serializes only kind/hasKey/keyTail/environmentManaged;
- Credential repr and error formatting never reveal its value;
- Paper ID is preserved byte-for-byte as a string.

- [ ] **Step 2: Run and confirm intended RED (2-5 minutes)**

Run:

~~~powershell
$env:PYTHONDONTWRITEBYTECODE = '1'
.\.venv\Scripts\python.exe -B -m unittest backend.tests.test_p1_domain -v
~~~

Expected: FAIL because <code>backend.app.domain.entities</code> and <code>backend.app.domain.errors</code> do not exist. A dependency error is not the intended RED.

- [ ] **Step 3: Implement the smallest immutable domain model (2-5 minutes per entity family)**

Use frozen dataclasses and string enums. Domain code may import only the Python standard library. Define typed errors for missing Paper/PDF, invalid source mode, OCR unavailable, extraction failure, empty source, generation failure, empty artifact, artifact kind unsupported, stale source, persistence conflict, and schema revision mismatch. Error objects contain stable codes and sanitized public messages; raw provider bodies, PDF content, file bytes, and credentials never enter error details.

- [ ] **Step 4: Rerun the target test as GREEN evidence (2-5 minutes)**

Run:

~~~powershell
$env:PYTHONDONTWRITEBYTECODE = '1'
.\.venv\Scripts\python.exe -B -m unittest backend.tests.test_p1_domain -v
~~~

Expected: every entity/invariant test passes and importing <code>backend.app.domain</code> does not import SQLAlchemy, aiosqlite, FastAPI, <code>agent.config</code>, or any network client.

---

## Task 3: Create Alembic Revision 20260807_01 and the Exact Five Tables

**Files:**

- Create: <code>backend/tests/support/p1_database.py</code>
- Create: <code>backend/tests/support/__init__.py</code>
- Create: <code>backend/tests/test_p1_migration.py</code>
- Create: <code>backend/alembic.ini</code>
- Create: <code>backend/migrations/__init__.py</code>
- Create: <code>backend/migrations/versions/__init__.py</code>
- Create: <code>backend/migrations/env.py</code>
- Create: <code>backend/migrations/script.py.mako</code>
- Create: <code>backend/migrations/versions/20260807_01_domain_data_foundation.py</code>

- [ ] **Step 1: Write the isolated migration RED tests (2-5 minutes per schema family)**

The fixture must create a temporary legacy database from the checked-in <code>db/schema.sql</code>, apply the current startup mutations represented by <code>db.js</code>, seed deterministic rows in every legacy table, and compute canonical count/hash pairs without opening Live SQLite.

The same module must define <code>P1RestoredCopyValidationTests(unittest.TestCase)</code>; P2 calls this class after its guarded downgrade, so it is not an implied future name. It contains exactly two public test methods: <code>test_db_path_is_bound_restore_at_exact_p1_revision</code> validates path binding and the unique <code>20260807_01</code> current revision, while <code>test_p1_schema_health_and_required_objects_are_read_only</code> validates the five required tables, hard schema, quick/integrity/FK health, and byte/metadata stability.

Both methods require an explicit process <code>DB_PATH</code>. Before opening SQLite they resolve it, reject the resolved workspace <code>data/app.db</code>, require the parent directory name to begin <code>restore-validation-</code>, and require containment beneath the operator-supplied restore root in <code>MIGRATION_RESTORE_ROOT</code>. Missing either environment value, sibling-prefix containment, symlink/junction escape, multiple/missing Alembic rows, or any revision other than <code>20260807_01</code> fails before validation. The class opens SQLite read-only, asserts the five P1 tables and hard schema/health invariants, records bytes/size/mtime/sidecars before and after, and fails if validation writes anything. Legacy count/hash equality is deliberately enforced by the PowerShell phase snapshots below, not hidden inside process-local test constants.

Tests must assert:

- one Alembic head named exactly <code>20260807_01</code> with <code>down_revision=None</code>;
- upgrade creates only the five required domain tables plus Alembic metadata;
- every required column appears exactly once with the specified name;
- all required PK, FK, UNIQUE, CHECK, and index contracts exist;
- every <code>paper_id</code>, <code>source_document_id</code>, and <code>artifact_id</code> foreign key has the required target and delete action;
- deleting a Paper cascades its obsidian_exports row without changing any external fixture/Vault byte, while deleting a GeneratedArtifact sets only the ledger artifact_id to null;
- invalid source modes, source statuses, ProcessingJob job types, ProcessingJob statuses, invalid job scope combinations, SHA strings, counts, duplicate cache identities, duplicate artifact identities, duplicate job idempotency keys, duplicate chunk sequences, and duplicate global target paths are rejected;
- generated_artifacts.kind rejects blank text but has no database enum CHECK; a nonblank compatibility kind can be stored by migration-level code while the domain rejects noncanonical input;
- all seven canonical ProcessingJob job types are accepted; <code>source_materialize</code> with native mode and <code>ocr</code> with OCR mode are represented distinctly, either reversed source-mode pairing is rejected, obsidian_export accepts Paper with null mode, and global obsidian_sync accepts null Paper/mode;
- all five new tables contain zero rows immediately after upgrade;
- no historical legacy artifact is inserted into generated_artifacts;
- upgrade preserves every legacy table count/hash;
- downgrade on empty new tables drops the five domain tables in FK-safe order and preserves every legacy count/hash;
- re-upgrade restores exactly one head and five empty tables;
- downgrade refuses when any P1 table contains data;
- <code>PRAGMA quick_check</code> and <code>integrity_check</code> return <code>ok</code>, and <code>foreign_key_check</code> returns zero rows at each phase.

- [ ] **Step 2: Run and confirm intended RED (2-5 minutes)**

Run:

~~~powershell
$env:PYTHONDONTWRITEBYTECODE = '1'
.\.venv\Scripts\python.exe -B -m unittest backend.tests.test_p1_migration -v
~~~

Expected: FAIL because <code>backend/alembic.ini</code>, migration environment, and revision are absent. A failure caused by opening <code>data/app.db</code> is a test defect; fix the fixture first.

- [ ] **Step 3: Implement deterministic Alembic configuration (2-5 minutes per file)**

Set <code>script_location = %(here)s/migrations</code>. Make <code>env.py</code> read and resolve only <code>DB_PATH</code>, convert that file path to a synchronous SQLite URL for Alembic, enable foreign keys and a 5,000 ms busy timeout on connect, and run migrations transactionally. It must not import <code>agent.config</code>, create directories, call ORM <code>create_all</code>, or silently choose another database.

- [ ] **Step 4: Implement the exact additive upgrade (2-5 minutes per table)**

Create all five tables and named indexes from the Authoritative SQLite Schema Contract. Use explicit constraint names. Keep all legacy objects untouched. Insert no Paper, SourceDocument, GeneratedArtifact, ProcessingJob, chunk, export, explainer, translation, note, or vector row.

- [ ] **Step 5: Implement the guarded downgrade (2-5 minutes)**

Before dropping, count every P1 table. If any count is nonzero, raise a classified migration error that names the nonempty tables and directs the operator to runtime rollback and verified backup recovery. When all are empty, drop in this order:

~~~text
obsidian_exports
document_chunks
processing_jobs
generated_artifacts
document_sources
~~~

Never drop or alter <code>papers</code>, <code>translations</code>, <code>notes</code>, <code>paper_vectors</code>, <code>schema_migrations</code>, or any other legacy object.

- [ ] **Step 6: Rerun the target test as GREEN evidence (2-5 minutes)**

Run:

~~~powershell
$env:PYTHONDONTWRITEBYTECODE = '1'
.\.venv\Scripts\python.exe -B -m unittest backend.tests.test_p1_migration -v
~~~

Expected: all migration assertions pass for upgrade, guarded downgrade, and re-upgrade; legacy hashes remain identical; five new tables are empty; exactly one Alembic head is present after each upgrade.

---

## Task 4: Add Async SQLite Repositories and a Transactional Unit of Work

**Files:**

- Create: <code>backend/tests/test_p1_repositories.py</code>
- Create: <code>backend/app/application/ports/__init__.py</code>
- Create: <code>backend/app/application/ports/repositories.py</code>
- Create: <code>backend/app/application/ports/unit_of_work.py</code>
- Create: <code>backend/app/infrastructure/database.py</code>
- Create: <code>backend/app/repositories/__init__.py</code>
- Create: <code>backend/app/repositories/models.py</code>
- Create: <code>backend/app/repositories/sqlalchemy.py</code>
- Create: <code>backend/app/repositories/unit_of_work.py</code>

- [ ] **Step 1: Write repository/UoW RED tests (2-5 minutes per behavior family)**

Using <code>unittest.IsolatedAsyncioTestCase</code> and a migrated temporary file, cover:

- every connection reports WAL, foreign keys ON, and busy timeout 5000;
- engine/session construction creates no schema and does not touch Live SQLite;
- PaperRepository reads existing <code>papers.id</code> without remapping it;
- SourceDocumentRepository finds the exact cache identity and inserts one row under concurrent duplicate attempts;
- GeneratedArtifactRepository finds the newest eligible ready row deterministically and enforces source/kind/version identity;
- ProcessingJobRepository preserves the exact five public statuses, unique idempotency key, paper-scoped jobs, and a global obsidian_sync row with null paper_id/source_mode;
- VaultProjectionRepository treats target_path uniqueness as global;
- SqlAlchemyUnitOfWork commits all writes together, rolls all writes back on an exception, closes sessions, and never commits implicitly from a repository;
- cancellation during <code>__aexit__</code> rolls back;
- domain-facing repository returns contain no ORM instance;
- no lazy SQL occurs after the Unit of Work closes;
- foreign-key failures and uniqueness conflicts map to stable typed errors without SQL text or row content.

- [ ] **Step 2: Run and confirm intended RED (2-5 minutes)**

Run:

~~~powershell
$env:PYTHONDONTWRITEBYTECODE = '1'
.\.venv\Scripts\python.exe -B -m unittest backend.tests.test_p1_repositories -v
~~~

Expected: FAIL because the Application ports, database factory, repository mappings, and Unit of Work do not exist. A missing migration is not the intended RED because Task 3 is green first.

- [ ] **Step 3: Define narrow Application ports (2-5 minutes per port)**

Expose only domain values and these capabilities:

- PaperRepository: <code>get(paper_id)</code>.
- SourceDocumentRepository: <code>get(id)</code>, <code>find_by_cache_identity(identity)</code>, <code>add(document)</code>, and compare-and-set failure/ready publication.
- GeneratedArtifactRepository: <code>get(id)</code>, <code>find_by_version_identity(identity)</code>, <code>find_ready_for_paper(paper_id,kind)</code>, <code>add(artifact)</code>, and compare-and-set failure/ready publication.
- ProcessingJobRepository: <code>get(id)</code> and <code>add(job)</code>, including globally scoped ProcessingJob values; P2 expands queue behavior.
- VaultProjectionRepository: <code>get(id)</code>, <code>find_by_target_path(path)</code>, and <code>add(projection)</code>.
- UnitOfWork: async context entry/exit, the five repositories, <code>commit()</code>, and <code>rollback()</code>.

Do not expose Session, Select, Row, mapped classes, SQL strings, or engine objects through a port.

- [ ] **Step 4: Implement SQLite engine/session policy (2-5 minutes)**

Create an aiosqlite engine from the resolved file path. On every physical connection set foreign keys ON and busy timeout 5000; writable runtime connections use WAL. Use <code>expire_on_commit=False</code>. Do not hold a connection at module import and do not run a migration or <code>create_all</code> at startup.

- [ ] **Step 5: Implement mappings, repositories, and SqlAlchemyUnitOfWork (2-5 minutes per repository)**

Map exact migration columns. Map only the required legacy Paper and artifact-projection columns for reads/dual writes. Convert ORM rows to frozen domain values before returning. Flush inside repositories when an ID/constraint result is required, but commit only in UnitOfWork. Use explicit rollback and close paths for success, exception, and cancellation.

- [ ] **Step 6: Rerun the target test as GREEN evidence (2-5 minutes)**

Run:

~~~powershell
$env:PYTHONDONTWRITEBYTECODE = '1'
.\.venv\Scripts\python.exe -B -m unittest backend.tests.test_p1_repositories -v
~~~

Expected: all repository/UoW tests pass, one duplicate cache/version row survives concurrency, transaction rollback leaves both new and legacy tables unchanged, and no connection/session leak remains.

---

## Task 5: Implement NativeExtractor With a Zero-OCR Object Graph

**Files:**

- Create: <code>backend/tests/test_native_extractor.py</code>
- Modify: <code>backend/app/application/ports/__init__.py</code>
- Create: <code>backend/app/application/ports/source_extractor.py</code>
- Create: <code>backend/app/providers/__init__.py</code>
- Create: <code>backend/app/providers/native.py</code>

- [ ] **Step 1: Write NativeExtractor RED tests (2-5 minutes per extraction branch)**

Use injected pymupdf4llm and PyMuPDF doubles to cover:

- pymupdf4llm Markdown success with <code>show_progress=False</code>;
- plain PyMuPDF fallback when Markdown raises or contains fewer than 200 non-whitespace characters;
- full-document page order and page_count;
- deterministic CRLF-to-LF and trailing-space normalization before hashing;
- blank result returns a typed <code>NATIVE_TEXT_EMPTY</code> failure;
- unreadable/corrupt PDF returns <code>NATIVE_EXTRACTION_FAILED</code> without file content in the error;
- provider identity <code>local</code>, model identity <code>pymupdf4llm-pymupdf</code>, and processing version <code>native-v1</code>;
- OCR constructor count zero, OCR call count zero, network call count zero, and no imported OCR module for every native success/failure branch.

- [ ] **Step 2: Run and confirm intended RED (2-5 minutes)**

Run:

~~~powershell
$env:PYTHONDONTWRITEBYTECODE = '1'
$env:OCR_ENABLED = '0'
.\.venv\Scripts\python.exe -B -m unittest backend.tests.test_native_extractor -v
~~~

Expected: FAIL because the source-extractor port and NativeExtractor do not exist. A real PDF library import error is not intended because tests inject doubles.

- [ ] **Step 3: Implement the smallest native provider (2-5 minutes per branch)**

NativeExtractor receives a resolved PDF path and injected extraction functions. It reads the complete native document, applies the established pymupdf4llm-first/plain-text-second policy, normalizes deterministically, and returns Markdown/page_count/provider/model/processing_version. It contains no OCR import, registry, factory, configuration lookup, fallback, or transport code.

- [ ] **Step 4: Rerun the target test as GREEN evidence (2-5 minutes)**

Run:

~~~powershell
$env:PYTHONDONTWRITEBYTECODE = '1'
$env:OCR_ENABLED = '0'
.\.venv\Scripts\python.exe -B -m unittest backend.tests.test_native_extractor -v
~~~

Expected: all native branches pass; OCR construction/call/network counters remain exactly zero; stdout remains empty.

---

## Task 6: Implement DocumentSourcePipeline and Cache Identity

**Files:**

- Create: <code>backend/tests/test_document_source_pipeline.py</code>
- Create: <code>backend/app/application/source_documents.py</code>

- [ ] **Step 1: Write DocumentSourcePipeline RED tests (2-5 minutes per behavior family)**

Cover:

- the exact <code>materialize_source(paper_id,source_mode,purpose)</code> signature;
- missing Paper and missing/unreadable PDF typed errors with zero source rows;
- blank purpose rejection before filesystem or repository access;
- native-only P1 composition and explicit OCR rejection with zero provider construction/calls and zero rows;
- resolved PDF SHA-256, canonical empty-options SHA-256, provider/model, processing version, content SHA-256, page_count, and UTC timestamps;
- ready cache hit returns the same SourceDocument ID and calls NativeExtractor zero additional times;
- different purpose shares the same cache identity;
- changed PDF bytes create a different cache identity and SourceDocument;
- changed processing version creates a different cache identity;
- file bytes changing between pre/post extraction checks produce <code>SOURCE_PDF_CHANGED</code> and no ready row;
- empty extraction stores only a failed source identity and never replaces an existing ready row;
- concurrent identical materializations return one persisted row;
- no write transaction is held while reading PDF bytes or extracting Markdown.

- [ ] **Step 2: Run and confirm intended RED (2-5 minutes)**

Run:

~~~powershell
$env:PYTHONDONTWRITEBYTECODE = '1'
$env:OCR_ENABLED = '0'
.\.venv\Scripts\python.exe -B -m unittest backend.tests.test_document_source_pipeline -v
~~~

Expected: FAIL because <code>backend.app.application.source_documents.DocumentSourcePipeline</code> is missing. A database schema failure is not the intended RED.

- [ ] **Step 3: Implement materialization outside long transactions (2-5 minutes per phase)**

Implement this order:

1. Validate Paper ID, SourceMode, and nonblank purpose.
2. Load Paper and resolve the existing PDF path without creating a file.
3. Hash PDF bytes and form the exact cache identity from Paper ID, PDF hash, mode, provider, model, canonical empty options hash, and processing version.
4. In a short Unit of Work, return an existing ready identity.
5. Close the Unit of Work, invoke NativeExtractor, and re-hash the PDF.
6. Build a ready or failed SourceDocument with a generated stable ID and injected UTC clock.
7. In a new short Unit of Work, insert with conflict handling; on a race, read and return the winning ready row.

The native provider/model identities are non-null. P1 does not materialize OCR. Do not truncate the canonical SourceDocument based on purpose; later consumers build bounded context.

- [ ] **Step 4: Rerun the target test as GREEN evidence (2-5 minutes)**

Run:

~~~powershell
$env:PYTHONDONTWRITEBYTECODE = '1'
$env:OCR_ENABLED = '0'
.\.venv\Scripts\python.exe -B -m unittest backend.tests.test_document_source_pipeline -v
~~~

Expected: all cache, failure, race, and zero-OCR assertions pass; a repeated call returns the same ID/content/hash without a second extraction.

---

## Task 7: Implement GenerationPipeline, Provenance-Safe Reads, and Transactional Dual Write

**Files:**

- Create: <code>backend/tests/test_generation_pipeline.py</code>
- Modify: <code>backend/app/application/ports/__init__.py</code>
- Create: <code>backend/app/application/ports/artifact_generator.py</code>
- Create: <code>backend/app/application/generated_artifacts.py</code>
- Modify: <code>backend/app/providers/__init__.py</code>
- Create: <code>backend/app/providers/generation.py</code>

- [ ] **Step 1: Write generation and artifact-read RED tests (2-5 minutes per behavior family)**

Cover:

- the exact <code>generate_artifact(paper_id,artifact_kind,source_mode)</code> signature;
- GenerationPipeline calls DocumentSourcePipeline and passes only SourceDocument Markdown plus Paper metadata to the generator;
- generator cannot receive a PDF path, file handle, or PDF bytes;
- explainer/translation provider/model/prompt versions form the exact unique artifact identity;
- same ready version returns the existing artifact with zero additional generator calls;
- summary/outline/study_card/classification/metadata return a typed unsupported-kind error in P1 with zero rows;
- empty output and provider exception create or preserve a failed row, do not touch legacy fields, and expose no provider body;
- a late failed result cannot update or clear an already-ready artifact;
- a successful retry may compare-and-set a failed identity to ready, but cannot alter a ready identity;
- ready artifact insert and <code>papers.explainer</code> update commit in one transaction;
- ready translation insert and <code>translations.content</code> upsert commit in one transaction;
- injected legacy-write failure rolls back the ready artifact;
- injected new-artifact failure leaves legacy content unchanged;
- post-commit file mirroring is best effort, happens only after DB commit, and is not presented as transactional;
- new-first reader returns the newest eligible ready artifact with new provenance;
- absent, failed, stale, or cancelled new rows fall back to the legacy explainer/translation with explicit legacy provenance and null source/artifact IDs;
- new-table query errors do not masquerade as an absent row;
- legacy-only read mode never queries a P1 table;
- migration/reader never fabricate a SourceDocument for historical legacy content.

- [ ] **Step 2: Run and confirm intended RED (2-5 minutes)**

Run:

~~~powershell
$env:PYTHONDONTWRITEBYTECODE = '1'
.\.venv\Scripts\python.exe -B -m unittest backend.tests.test_generation_pipeline -v
~~~

Expected: FAIL because the generator port, GenerationPipeline, ArtifactReader, and generation provider do not exist. A real model/network call is a test defect.

- [ ] **Step 3: Implement the provider seam (2-5 minutes per supported kind)**

Wrap current explainer and translation model functions behind a provider receiving immutable Paper metadata and SourceDocument Markdown. Expose stable provider/model/prompt-version identities. Tests inject a fake; production construction remains lazy and makes no request during import/bootstrap.

- [ ] **Step 4: Implement generation identity and failure publication (2-5 minutes per state path)**

Call <code>materialize_source</code> with purpose <code>artifact:explainer</code> or <code>artifact:translation</code>. Check for an existing ready version before generation. Invoke the model outside a write transaction. Publish success with compare-and-set from absent/failed to ready. On failure, insert a failed identity only if a ready identity does not exist. Never issue an unconditional UPDATE against generated_artifacts.

- [ ] **Step 5: Implement transactional dual write (2-5 minutes per legacy kind)**

Within one SqlAlchemyUnitOfWork:

- explainer success writes generated_artifacts and <code>papers.explainer</code>, updates <code>papers.updated_at</code>, and invalidates the existing <code>paper_vectors</code> row;
- translation success writes generated_artifacts and upserts <code>translations.content</code>/<code>updated_at</code>;
- any SQL failure rolls back both new and legacy database writes.

Do not place Markdown file writes inside the database transaction. If legacy file mirroring is retained, perform it after commit through an injected adapter and preserve the existing best-effort behavior.

- [ ] **Step 6: Implement new-first/legacy-fallback reads (2-5 minutes)**

ArtifactReader accepts the startup-only read mode. <code>legacy</code> reads only current fields. <code>prefer_new</code> selects a ready GeneratedArtifact joined to a ready SourceDocument, ordered deterministically by updated_at then ID; if no eligible row exists, it reads the legacy field. A database error is raised as a typed persistence failure so operators can explicitly restart in legacy mode.

- [ ] **Step 7: Rerun the target test as GREEN evidence (2-5 minutes)**

Run:

~~~powershell
$env:PYTHONDONTWRITEBYTECODE = '1'
.\.venv\Scripts\python.exe -B -m unittest backend.tests.test_generation_pipeline -v
~~~

Expected: all generation, failure-race, transaction, immutability, fallback, and provenance assertions pass; every fake provider count is exact; no historical artifact row is synthesized.

---

## Task 8: Add the Composition Root and API Extension Seam

**Files:**

- Create: <code>backend/tests/test_api_foundation.py</code>
- Create: <code>backend/app/api/__init__.py</code>
- Create: <code>backend/app/api/errors.py</code>
- Create: <code>backend/app/api/router.py</code>
- Create: <code>backend/app/api/app.py</code>
- Create: <code>backend/app/bootstrap.py</code>

- [ ] **Step 1: Write composition/API RED tests (2-5 minutes per behavior family)**

Cover:

- <code>create_app</code> accepts an injected container/session factory plus an explicit frozen <code>required_schema_revision</code> and never binds a port;
- <code>/api/v2/health</code> returns <code>{"ok":true,"schemaRevision":"20260807_01"}</code> when P1 passes <code>required_schema_revision="20260807_01"</code> against a migrated temporary database;
- typed errors serialize exactly as <code>{error:{code,message,details}}</code>;
- details never contain Credential values, PDF text, SQL text, Authorization values, or raw provider bodies;
- all-legacy rollout construction does not require P1 tables and constructs no native/generation provider;
- selecting a P1 document/generation/read/write mode requires exactly one Alembic current revision equal to the caller's frozen <code>required_schema_revision</code>; P1 passes <code>20260807_01</code>, P2 must later pass <code>20260807_02</code>, and P3–P6 must pass <code>20260807_03</code> without replacing this composition root;
- selecting P1 with no migration, wrong head, or multiple heads fails before any provider construction or write;
- P1 native composition has no OCR registry/provider/factory object and performs zero OCR calls;
- bootstrap does not run Alembic, <code>create_all</code>, or a schema mutation;
- engine/session disposal closes all resources.

- [ ] **Step 2: Run and confirm intended RED (2-5 minutes)**

Run:

~~~powershell
$env:PYTHONDONTWRITEBYTECODE = '1'
$env:OCR_ENABLED = '0'
.\.venv\Scripts\python.exe -B -m unittest backend.tests.test_api_foundation -v
~~~

Expected: FAIL because the API factory, error DTO, router, and bootstrap module do not exist. A real Live database open or network bind is not the intended RED.

- [ ] **Step 3: Implement safe error serialization and the /api/v2 seam (2-5 minutes per file)**

Create one router whose only P1 route is health/schema readiness. Application routes in P2 extend this router. Register typed exception handlers. Do not add a production server command, proxy, Node route, React Gateway, or UI control in P1.

Keep the error seam as the single module <code>backend/app/api/errors.py</code>. Downstream FastAPI takeover extends that module rather than creating a same-name package directory.

- [ ] **Step 4: Implement bootstrap and rollout availability checks (2-5 minutes per mode)**

Bootstrap receives frozen P0 rollout settings, DatabaseSettings, and a required schema revision supplied by the stage composition. All-legacy mode returns only legacy-compatible adapters and does not inspect P1 tables. A selected P1 path verifies read-only that <code>alembic_version</code> contains exactly one row equal to that required revision, then constructs session factory, repositories, providers, pipelines, and ArtifactReader. The revision contract rejects missing/multiple/unknown values and never resolves the symbolic Alembic <code>head</code> at runtime. OCR disabled/native composition has no OCR object. Startup performs no migration or write transaction.

- [ ] **Step 5: Rerun the target test as GREEN evidence (2-5 minutes)**

Run:

~~~powershell
$env:PYTHONDONTWRITEBYTECODE = '1'
$env:OCR_ENABLED = '0'
.\.venv\Scripts\python.exe -B -m unittest backend.tests.test_api_foundation -v
~~~

Expected: all factory/error/head/rollout/resource tests pass; no socket binds; no Live path opens; zero OCR construction/calls.

---

## Task 9: Add the Production CredentialStore Vertical Slice

**Files:**

- Create: <code>backend/tests/test_credentials.py</code>
- Modify: <code>backend/app/application/ports/__init__.py</code>
- Create: <code>backend/app/application/ports/credential_store.py</code>
- Create: <code>backend/app/application/ports/credential_probe.py</code>
- Create: <code>backend/app/application/credentials.py</code>
- Modify: <code>backend/app/providers/__init__.py</code>
- Create: <code>backend/app/providers/credentials/__init__.py</code>
- Create: <code>backend/app/providers/credentials/environment.py</code>
- Create: <code>backend/app/providers/credentials/keyring.py</code>
- Create: <code>backend/app/providers/credentials/legacy_settings.py</code>
- Create: <code>backend/app/providers/credentials/composite.py</code>
- Create: <code>backend/app/providers/credentials/probe.py</code>
- Create: <code>backend/app/providers/credentials/fixtures/llm-probe.txt</code>
- Create: <code>backend/app/providers/credentials/fixtures/ocr-probe.png.base64</code>
- Modify: <code>backend/app/bootstrap.py</code>

- [ ] **Step 1: Write the complete CredentialStore RED suite (2-5 minutes per behavior family)**

Create these named test families:

- <code>CredentialPriorityTests</code>: for all four exact mappings, frozen <code>LLM_API_KEY|OCR_API_KEY|EMBED_API_KEY|S2_API_KEY</code> environment wins over Keyring and legacy JSON; Keyring wins over <code>apiKey|ocrApiKey|embedApiKey|s2ApiKey</code>; missing all tiers returns not configured.
- <code>KeyringCredentialStoreTests</code>: service is exactly <code>study-app</code>, usernames are exactly <code>credential:llm|credential:ocr|credential:embedding|credential:semantic_scholar</code>, OS calls run through an injected blocking adapter, and Keyring unavailable/locked/delete failures become sanitized typed errors for each kind.
- <code>LegacyCredentialMigrationTests</code>: all four legacy fields import into their matching Keyring entries, read-back verification uses <code>hmac.compare_digest</code>, all fields remain during the Node rollback window, unknown/non-secret JSON fields remain unchanged, and a Keyring failure leaves the original file bytes unchanged.
- <code>CredentialMutationTests</code>: parameterized across four kinds, blank/whitespace update is a zero-readback/zero-write no-op; nonblank update synchronizes Keyring then only its mapped legacy field; explicit clear removes both writable tiers for only that kind; environment remains effective after update/clear; a failed second-tier write compensates the first-tier write or returns <code>CREDENTIAL_UPDATE_INDETERMINATE</code> without a secret.
- <code>CredentialStatusTests</code>: for all four kinds, status is exactly <code>kind,hasKey,keyTail,environmentManaged</code>; configured tails are <code>****</code> plus four characters only when the secret has at least eight characters, shorter secrets return only <code>****</code>; absent tails are null.
- <code>CredentialRedactionTests</code>: each of four distinct sentinel secrets never occurs in repr, str, exceptions, logs, stdout, stderr, status JSON, FastAPI error JSON, migration reports, or probe results.
- <code>CredentialProbeTests</code>: LLM transport receives only the fixed prompt; default OCR returns <code>OCR_PROVIDER_CONTRACT_UNVERIFIED</code> with zero transport calls; an injected verified OCR fake receives only the decoded fixed PNG; embedding/Semantic Scholar return <code>CREDENTIAL_PROBE_UNSUPPORTED</code> with zero transport; no probe accepts or reads a Paper ID, PDF path, PDF byte sequence, user prompt, or user file.
- <code>CredentialConcurrencyTests</code>: concurrent update/clear operations are serialized per credential kind and per shared settings path, the settings file uses an expected-byte-hash compare before atomic replace, cross-kind updates preserve the other three fields, and no temporary file/partial JSON remains after success or failure.

All filesystem tests use <code>TemporaryDirectory</code>. All Keyring/transport tests inject fakes and must not write the developer's Windows Credential Manager or make a network call.

- [ ] **Step 2: Run and confirm intended RED (2-5 minutes)**

Run:

~~~powershell
$env:PYTHONDONTWRITEBYTECODE = '1'
.\.venv\Scripts\python.exe -B -m unittest backend.tests.test_credentials -v
~~~

Expected: FAIL because the CredentialStore/Probe ports, use cases, provider package, and fixtures do not exist. A real Keyring write, settings mutation, or network attempt is a test defect.

- [ ] **Step 3: Implement environment and Keyring adapters (2-5 minutes per adapter)**

EnvironmentCredentialStore receives a copied mapping at bootstrap and reads only <code>LLM_API_KEY|OCR_API_KEY|EMBED_API_KEY|S2_API_KEY</code> through the fixed kind mapping. It is read-only, trims only surrounding whitespace used to detect absence, and never mutates <code>os.environ</code>.

KeyringCredentialStore wraps keyring 25.6.0 with service/usernames fixed above. Invoke blocking OS calls outside the async event loop. Never enumerate unrelated credentials. Never include a Credential in logger arguments or chained exception text. The real adapter is lazy; importing/bootstrap performs zero Credential Manager operations.

- [ ] **Step 4: Implement legacy compatibility and composite priority (2-5 minutes per state transition)**

LegacySettingsCredentialStore reads only <code>apiKey|ocrApiKey|embedApiKey|s2ApiKey</code> from an injected, resolved settings path. A write changes only the field mapped to its CredentialKind and preserves the other three plus every unknown/non-secret JSON field; it uses UTF-8, same-directory exclusive temporary creation, flush/fsync, expected-original-SHA comparison, and atomic replace. It never creates a missing production settings file during a read.

CompositeCredentialStore implements environment then Keyring then legacy reads. On first use, import a legacy value only when the corresponding Keyring entry is absent, read it back, and verify with <code>hmac.compare_digest</code>. Retain the legacy value throughout P0-P6 for frozen Node rollback. Do not implement or call <code>finalize_legacy_migration</code>.

For a nonblank update, snapshot both writable tiers, set/read-verify Keyring, then atomically synchronize the legacy field. If the legacy write fails, restore/delete the prior Keyring value and verify compensation. Explicit clear deletes the Keyring item and removes the matching legacy field; it applies the same compensation/error classification. Environment values are never changed, but clear still removes stale lower-tier values. Blank input returns current status before any writable-tier call.

- [ ] **Step 5: Implement safe status and connection-probe use cases (2-5 minutes per credential kind)**

CredentialService is the only API-facing layer. Its status mapper emits only <code>kind</code>, <code>hasKey</code>, <code>keyTail</code>, and <code>environmentManaged</code>. Provider code receives the internal Credential object directly from the store; no controller, DTO, JSON encoder, or CLI can request the value.

Create <code>llm-probe.txt</code> with exactly this 40-byte UTF-8 content including its final LF:

~~~text
Return exactly STUDY_APP_CREDENTIAL_OK.
~~~

Its SHA-256 is <code>2c7049edb25d64a1434c1e8d30dd5dfa668fb26f69db700327a0ee98eb39b6ee</code>.

Create <code>ocr-probe.png.base64</code> with exactly this Base64 payload:

~~~text
iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=
~~~

It decodes to 68 PNG bytes with SHA-256 <code>431ced6916a2a21a156e38701afe55bbd7f88969fbbfc56d7fe099d47f265460</code>. CredentialService.test_connection accepts only CredentialKind. LLM probing sends the fixed text. OCR probing is disabled in production P1 before transport construction; verified fake tests receive only the 68 fixed bytes. Embedding and Semantic Scholar return <code>CREDENTIAL_PROBE_UNSUPPORTED</code> without constructing transport.

- [ ] **Step 6: Wire CredentialStore without changing Node ownership (2-5 minutes)**

Bootstrap constructs the lazy CompositeCredentialStore from the frozen environment snapshot, injected Keyring adapter, and <code>data/settings.json</code> compatibility adapter. It does not read/migrate a secret until a credential use case is invoked. Keep <code>API_BACKEND_MODE=legacy</code>, <code>lib/settings.js</code>, and Node settings routes unchanged in P1. Do not create a Node-to-Keyring process bridge.

- [ ] **Step 7: Rerun the target test as GREEN evidence (2-5 minutes)**

Run:

~~~powershell
$env:PYTHONDONTWRITEBYTECODE = '1'
.\.venv\Scripts\python.exe -B -m unittest backend.tests.test_credentials -v
~~~

Expected: all priority, Keyring, legacy import, blank no-op, update, clear, status, redaction, concurrency, and fixed-probe assertions pass; fake call counts are exact; no real credential/settings/network resource changes.

- [ ] **Step 8: Verify the current Windows secure backend without reading/writing a secret (2-5 minutes)**

Run:

~~~powershell
.\.venv\Scripts\python.exe -c "import keyring,sys; backend=keyring.get_keyring(); name=type(backend).__module__+'.'+type(backend).__name__; print(name); assert sys.platform!='win32' or 'Windows' in name, name"
~~~

Expected on this workspace: exit 0 and a Windows Credential Manager backend class name. The command calls no get/set/delete method and prints no credential.

---

## Task 10: Expose Read-Only Fingerprints for Migration Audits

**Files:**

- Extend: <code>backend/tests/test_database_backup.py</code>
- Modify: <code>backend/app/infrastructure/database_backup.py</code>
- Modify: <code>backend/app/cli/database_backup.py</code>

- [ ] **Step 1: Write the inspect-command RED tests (2-5 minutes)**

Add tests that invoke <code>backend.app.cli.database_backup.run(["inspect","--database",str(temporary_database)])</code> with a path created by <code>TemporaryDirectory</code>. Assert JSON <code>ok=true</code> and the existing DatabaseFingerprint fields: quick/integrity results, foreign-key count, schema/logical hash, table counts/hashes, critical-content counts/hashes, legacy schema migrations, Alembic head, and SQLite page metadata. Assert database bytes, size, mtime, and sidecar set are unchanged.

- [ ] **Step 2: Run and confirm intended RED (2-5 minutes)**

Run:

~~~powershell
$env:PYTHONDONTWRITEBYTECODE = '1'
.\.venv\Scripts\python.exe -B -m unittest backend.tests.test_database_backup.DatabaseBackupTests.test_inspect_reports_existing_fingerprint_without_writing_database backend.tests.test_database_backup.DatabaseBackupTests.test_inspect_cli_emits_json_and_preserves_file_metadata -v
~~~

Expected: FAIL because <code>inspect</code> is not a recognized command or public inspection function. A cleanup/permission error is not the intended RED.

- [ ] **Step 3: Add only a read-only inspection adapter (2-5 minutes per file)**

Expose the already-tested fingerprint operation through a public function using a readonly SQLite URI and explicit connection close. Add CLI parsing for <code>inspect --database</code>. Reuse DatabaseFingerprint serialization. Do not checkpoint WAL, normalize journal mode, create a backup, remove a sidecar, or open a writable connection.

- [ ] **Step 4: Rerun the target tests as GREEN evidence (2-5 minutes)**

Run:

~~~powershell
$env:PYTHONDONTWRITEBYTECODE = '1'
.\.venv\Scripts\python.exe -B -m unittest backend.tests.test_database_backup.DatabaseBackupTests.test_inspect_reports_existing_fingerprint_without_writing_database backend.tests.test_database_backup.DatabaseBackupTests.test_inspect_cli_emits_json_and_preserves_file_metadata -v
~~~

Expected: both tests pass; byte/hash/size/mtime/sidecars remain identical.

- [ ] **Step 5: Run the complete backup regression suite (2-5 minutes)**

Run:

~~~powershell
$env:PYTHONDONTWRITEBYTECODE = '1'
.\.venv\Scripts\python.exe -B -m unittest backend.tests.test_database_backup -v
~~~

Expected: all pre-existing create/verify/restore safety tests and the two inspect tests pass.

---

## Task 11: Rehearse Upgrade, Downgrade, and Re-Upgrade on the Verified Restore

**Files:**

- Verify: <code>$p1Restore.restoredPath</code>
- Verify: Alembic and fingerprint output

- [ ] **Step 1: Capture the restored legacy fingerprint before migration (2-5 minutes)**

Run in the same PowerShell session as Task 0:

~~~powershell
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$p1RestoreRootPath = (Resolve-Path -LiteralPath $p1RestoreRoot.FullName).Path.TrimEnd([IO.Path]::DirectorySeparatorChar, [IO.Path]::AltDirectorySeparatorChar)
$p1RestorePrefix = $p1RestoreRootPath + [IO.Path]::DirectorySeparatorChar
$p1MigrationItem = Get-Item -LiteralPath $p1Restore.restoredPath -Force
$p1MigrationDb = (Resolve-Path -LiteralPath $p1MigrationItem.FullName).Path
$p1MigrationParent = (Resolve-Path -LiteralPath $p1MigrationItem.Directory.FullName).Path
if (-not $p1MigrationDb.StartsWith($p1RestorePrefix, [StringComparison]::OrdinalIgnoreCase)) { throw 'Migration drill database escaped the process-owned restore directory.' }
if (-not ($p1MigrationParent + [IO.Path]::DirectorySeparatorChar).StartsWith($p1RestorePrefix, [StringComparison]::OrdinalIgnoreCase)) { throw 'Migration drill parent escaped the process-owned restore directory.' }
if (-not (Split-Path -Leaf $p1MigrationParent).StartsWith('restore-validation-', [StringComparison]::Ordinal)) { throw 'Migration drill is not inside a restore-validation directory.' }
$p1Before = .\.venv\Scripts\python.exe -m backend.app.cli.database_backup inspect --database $p1MigrationDb | ConvertFrom-Json
if ($LASTEXITCODE -ne 0 -or -not $p1Before.ok) { throw 'Pre-upgrade fingerprint failed.' }
$p1LegacyTables = @('papers','progress','paper_reviews','notes','favorites','translations','paper_vectors','cite_edges','ingest_jobs','job_candidates','job_schedules','schema_migrations')
foreach ($p1Table in $p1LegacyTables) {
  if (-not ($p1Before.database.tableCounts.PSObject.Properties.Name -contains $p1Table)) { throw "Pre-upgrade count map is missing legacy table $p1Table." }
  if (-not ($p1Before.database.tableSha256.PSObject.Properties.Name -contains $p1Table)) { throw "Pre-upgrade hash map is missing legacy table $p1Table." }
}
~~~

`Resolve-Path` here means the existing target after symlink/junction/reparse resolution, not lexical normalization only. The containment RED tests include a sibling-prefix directory, `..`, a symlink/junction escaping the restore root, and case-variant paths; if the platform cannot prove a final target, the drill fails closed instead of continuing.

Expected: inspect succeeds read-only; <code>alembicVersion</code> is null; no five P1 tables exist.

- [ ] **Step 2: Upgrade the isolated restore to the fixed head (2-5 minutes)**

Run:

~~~powershell
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
function Invoke-CheckedNative {
  param([Parameter(Mandatory = $true)][string]$Label, [Parameter(Mandatory = $true)][scriptblock]$Command)
  $output = & $Command
  $exitCode = $LASTEXITCODE
  if ($exitCode -ne 0) { throw "$Label failed with exit code $exitCode." }
  $output
}
$p1PreviousDbPath = [Environment]::GetEnvironmentVariable('DB_PATH', 'Process')
$p1HadDbPath = $null -ne $p1PreviousDbPath
$p1PreviousRestoreRoot = [Environment]::GetEnvironmentVariable('MIGRATION_RESTORE_ROOT', 'Process')
$p1HadRestoreRoot = $null -ne $p1PreviousRestoreRoot
$env:DB_PATH = $p1MigrationDb
$env:MIGRATION_RESTORE_ROOT = $p1RestoreRootPath
try {
  .\.venv\Scripts\python.exe -m alembic -c backend/alembic.ini upgrade 20260807_01
  if ($LASTEXITCODE -ne 0) { throw 'Isolated P1 upgrade failed.' }
  .\.venv\Scripts\python.exe -B -m unittest backend.tests.test_p1_migration.P1RestoredCopyValidationTests -v
  if ($LASTEXITCODE -ne 0) { throw 'P1 restored-copy validator failed after upgrade.' }
  $p1AfterUpgrade = .\.venv\Scripts\python.exe -m backend.app.cli.database_backup inspect --database $p1MigrationDb | ConvertFrom-Json
  if ($LASTEXITCODE -ne 0 -or -not $p1AfterUpgrade.ok -or $p1AfterUpgrade.database.alembicVersion -ne '20260807_01') { throw 'Isolated upgrade did not reach 20260807_01.' }
} finally {
  if ($p1HadDbPath) { $env:DB_PATH = $p1PreviousDbPath } else { Remove-Item Env:DB_PATH -ErrorAction SilentlyContinue }
  if ($p1HadRestoreRoot) { $env:MIGRATION_RESTORE_ROOT = $p1PreviousRestoreRoot } else { Remove-Item Env:MIGRATION_RESTORE_ROOT -ErrorAction SilentlyContinue }
}
~~~

Expected: Alembic exits 0; exactly one head is <code>20260807_01</code>.

- [ ] **Step 3: Prove upgrade preserved legacy data and created five empty tables (2-5 minutes)**

Run:

~~~powershell
foreach ($p1Table in $p1LegacyTables) { if ($p1Before.database.tableCounts.$p1Table -ne $p1AfterUpgrade.database.tableCounts.$p1Table) { throw "Legacy count changed for $p1Table." }; if ($p1Before.database.tableSha256.$p1Table -ne $p1AfterUpgrade.database.tableSha256.$p1Table) { throw "Legacy hash changed for $p1Table." } }
$p1LegacyTables | ForEach-Object { if (-not ($p1AfterUpgrade.database.tableCounts.PSObject.Properties.Name -contains $_) -or -not ($p1AfterUpgrade.database.tableSha256.PSObject.Properties.Name -contains $_)) { throw "Post-upgrade fingerprint map is missing legacy table $_." } }
$p1NewTables = @('document_sources','generated_artifacts','processing_jobs','document_chunks','obsidian_exports')
foreach ($p1Table in $p1NewTables) { if ($p1AfterUpgrade.database.tableCounts.$p1Table -ne 0) { throw "New table is not empty after upgrade: $p1Table." } }
if ($p1AfterUpgrade.database.quickCheck -ne 'ok' -or $p1AfterUpgrade.database.integrityCheck -ne 'ok' -or $p1AfterUpgrade.database.foreignKeyViolations -ne 0) { throw 'Post-upgrade SQLite health check failed.' }
~~~

Expected: all legacy count/hash pairs are identical; every new table count is zero; quick/integrity are ok; FK violations are zero.

- [ ] **Step 4: Downgrade the empty isolated schema to base (2-5 minutes)**

Run:

~~~powershell
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$p1PreviousDbPath = [Environment]::GetEnvironmentVariable('DB_PATH', 'Process')
$p1HadDbPath = $null -ne $p1PreviousDbPath
$env:DB_PATH = $p1MigrationDb
try {
  .\.venv\Scripts\python.exe -m alembic -c backend/alembic.ini downgrade base
  if ($LASTEXITCODE -ne 0) { throw 'Isolated P1 downgrade failed.' }
  $p1AfterDowngrade = .\.venv\Scripts\python.exe -m backend.app.cli.database_backup inspect --database $p1MigrationDb | ConvertFrom-Json
  if ($LASTEXITCODE -ne 0 -or -not $p1AfterDowngrade.ok) { throw 'Post-downgrade fingerprint failed.' }
  foreach ($p1Table in $p1LegacyTables) { if (-not ($p1AfterDowngrade.database.tableCounts.PSObject.Properties.Name -contains $p1Table) -or -not ($p1AfterDowngrade.database.tableSha256.PSObject.Properties.Name -contains $p1Table)) { throw "Post-downgrade fingerprint map is missing legacy table $p1Table." } }
  foreach ($p1Table in $p1NewTables) { if ($p1AfterDowngrade.database.tableCounts.PSObject.Properties.Name -contains $p1Table) { throw "P1 table remained after downgrade: $p1Table." } }
  foreach ($p1Table in $p1LegacyTables) { if ($p1Before.database.tableCounts.$p1Table -ne $p1AfterDowngrade.database.tableCounts.$p1Table) { throw "Legacy count changed during downgrade for $p1Table." }; if ($p1Before.database.tableSha256.$p1Table -ne $p1AfterDowngrade.database.tableSha256.$p1Table) { throw "Legacy hash changed during downgrade for $p1Table." } }
} finally {
  if ($p1HadDbPath) { $env:DB_PATH = $p1PreviousDbPath } else { Remove-Item Env:DB_PATH -ErrorAction SilentlyContinue }
}
~~~

Expected: downgrade exits 0 because all P1 tables are empty; five P1 tables are absent; every legacy count/hash is unchanged; SQLite health remains clean.

- [ ] **Step 5: Re-upgrade and verify one head again (2-5 minutes)**

Run:

~~~powershell
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$p1PreviousDbPath = [Environment]::GetEnvironmentVariable('DB_PATH', 'Process')
$p1HadDbPath = $null -ne $p1PreviousDbPath
$env:DB_PATH = $p1MigrationDb
try {
  $p1BeforeReupgradeCurrent = @(& .\.venv\Scripts\python.exe -m alembic -c backend/alembic.ini current | Where-Object { $_.Trim() })
  if ($LASTEXITCODE -ne 0 -or $p1BeforeReupgradeCurrent.Count -ne 0) { throw 'Isolated P1 re-upgrade must start at Alembic base with no current revision.' }
  $p1MigrationHeads = @(& .\.venv\Scripts\python.exe -m alembic -c backend/alembic.ini heads | ForEach-Object { "$_".Trim() } | Where-Object { $_ })
  if ($LASTEXITCODE -ne 0 -or $p1MigrationHeads.Count -ne 1 -or $p1MigrationHeads[0] -notmatch '^20260807_01\s+\(head\)$') { throw 'P1 migration graph does not have exactly one target head 20260807_01.' }
  .\.venv\Scripts\python.exe -m alembic -c backend/alembic.ini upgrade 20260807_01
  if ($LASTEXITCODE -ne 0) { throw 'Isolated P1 re-upgrade failed.' }
  $p1AfterReupgradeCurrent = @(& .\.venv\Scripts\python.exe -m alembic -c backend/alembic.ini current | ForEach-Object { "$_".Trim() } | Where-Object { $_ })
  if ($LASTEXITCODE -ne 0 -or $p1AfterReupgradeCurrent.Count -ne 1 -or $p1AfterReupgradeCurrent[0] -notmatch '^20260807_01\s+\(head\)$') { throw 'Isolated P1 re-upgrade did not produce exactly one current revision 20260807_01.' }
  $p1AfterReupgrade = .\.venv\Scripts\python.exe -m backend.app.cli.database_backup inspect --database $p1MigrationDb | ConvertFrom-Json
  if ($LASTEXITCODE -ne 0 -or -not $p1AfterReupgrade.ok -or $p1AfterReupgrade.database.alembicVersion -ne '20260807_01') { throw 'Re-upgrade did not return to 20260807_01.' }
  foreach ($p1Table in $p1LegacyTables) {
    if (-not ($p1AfterReupgrade.database.tableCounts.PSObject.Properties.Name -contains $p1Table) -or -not ($p1AfterReupgrade.database.tableSha256.PSObject.Properties.Name -contains $p1Table)) { throw "Post-re-upgrade fingerprint map is missing legacy table $p1Table." }
    if ($p1Before.database.tableCounts.$p1Table -ne $p1AfterReupgrade.database.tableCounts.$p1Table) { throw "Legacy count changed during re-upgrade for $p1Table." }
    if ($p1Before.database.tableSha256.$p1Table -ne $p1AfterReupgrade.database.tableSha256.$p1Table) { throw "Legacy hash changed during re-upgrade for $p1Table." }
  }
  foreach ($p1Table in $p1NewTables) { if ($p1AfterReupgrade.database.tableCounts.$p1Table -ne 0) { throw "New table is not empty after re-upgrade: $p1Table." } }
} finally {
  if ($p1HadDbPath) { $env:DB_PATH = $p1PreviousDbPath } else { Remove-Item Env:DB_PATH -ErrorAction SilentlyContinue }
}
~~~

Expected: one head <code>20260807_01</code>, five empty new tables, unchanged legacy count/hash pairs, quick/integrity ok, zero FK violations.

- [ ] **Step 6: Prove the drill never changed Live SQLite or the verified backup (2-5 minutes)**

Run:

~~~powershell
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
function Invoke-CheckedNative {
  param([Parameter(Mandatory = $true)][string]$Label, [Parameter(Mandatory = $true)][scriptblock]$Command)
  $output = & $Command
  $exitCode = $LASTEXITCODE
  if ($exitCode -ne 0) { throw "$Label failed with exit code $exitCode." }
  $output
}
$p1VerifyAgain = Invoke-CheckedNative 'post-drill backup verification' { .\.venv\Scripts\python.exe -m backend.app.cli.database_backup verify --backup $p1Create.backupPath --manifest $p1Create.manifestPath } | ConvertFrom-Json
if (-not $p1VerifyAgain.ok -or $p1VerifyAgain.logicalSha256 -ne $p1Verify.logicalSha256) { throw 'Verified backup changed during migration drill.' }
$p1LiveAfterDrill = Invoke-CheckedNative 'post-drill Live inspection' { .\.venv\Scripts\python.exe -m backend.app.cli.database_backup inspect --database data/app.db } | ConvertFrom-Json
if (-not $p1LiveAfterDrill.ok) { throw 'Post-drill Live inspection did not return ok=true.' }
if ($p1LiveAfterDrill.database.alembicVersion -eq '20260807_01') { throw 'Live database was unexpectedly migrated during isolated rehearsal.' }
~~~

Expected: backup verification remains identical; Live has not acquired the P1 head; the only migrated database is <code>$p1MigrationDb</code>.

---

## Task 12: Prove Rollout and Runtime Rollback Before Any Live Schema Change

**Files:**

- Extend: <code>backend/tests/test_api_foundation.py</code>
- Verify: <code>backend/app/bootstrap.py</code>
- Verify: P0 rollout modules

- [ ] **Step 1: Add an isolated rollout/rollback RED test (2-5 minutes)**

The test must start three fresh containers against temporary databases:

1. All P0 defaults on an unmigrated legacy database: no P1 table query, no provider construction, legacy artifact read succeeds.
2. P1 document/generation plus prefer-new/dual on a <code>20260807_01</code> database: NativeExtractor and pipelines are available, one fake generated explainer writes the new artifact and legacy field atomically, and new-first read returns it.
3. All rollback values after a simulated restart against that same additive database: only legacy reads/writes are used, no P1 provider is constructed, and the legacy projection remains readable.

- [ ] **Step 2: Run and confirm intended RED (2-5 minutes)**

Run:

~~~powershell
$env:PYTHONDONTWRITEBYTECODE = '1'
.\.venv\Scripts\python.exe -B -m unittest backend.tests.test_api_foundation.ApiFoundationTests.test_p1_modes_require_head_and_runtime_rollback_uses_only_legacy_adapters -v
~~~

Expected before the final bootstrap wiring: FAIL because P1 adapters are not registered or rollback still constructs them. A missing temporary migration is not the intended RED.

- [ ] **Step 3: Apply the smallest bootstrap registration fix (2-5 minutes per mode)**

Register P1 DocumentSourcePipeline, GenerationPipeline, ArtifactReader, and dual-write projection only when their startup modes select P1. Keep all default values unchanged. Construct no P1 provider or repository in the all-legacy branch. Keep <code>API_BACKEND_MODE=legacy</code> in P1; FastAPI production takeover is not part of this phase.

- [ ] **Step 4: Rerun the target test as GREEN evidence (2-5 minutes)**

Run:

~~~powershell
$env:PYTHONDONTWRITEBYTECODE = '1'
.\.venv\Scripts\python.exe -B -m unittest backend.tests.test_api_foundation.ApiFoundationTests.test_p1_modes_require_head_and_runtime_rollback_uses_only_legacy_adapters -v
~~~

Expected: the three-process simulation passes; rollback requires only a restart and retains additive tables/data.

- [ ] **Step 5: Record the exact P1 canary and emergency configurations (2-5 minutes)**

P1 canary, used only after schema head verification:

~~~powershell
$env:API_BACKEND_MODE = 'legacy'
$env:DOCUMENT_PIPELINE_MODE = 'p1'
$env:GENERATION_PIPELINE_MODE = 'p1'
$env:ARTIFACT_READ_MODE = 'prefer_new'
$env:ARTIFACT_WRITE_MODE = 'dual'
$env:OCR_ENABLED = '0'
~~~

Emergency runtime rollback:

~~~powershell
$env:API_BACKEND_MODE = 'legacy'
$env:DOCUMENT_PIPELINE_MODE = 'legacy'
$env:GENERATION_PIPELINE_MODE = 'legacy'
$env:ARTIFACT_READ_MODE = 'legacy'
$env:ARTIFACT_WRITE_MODE = 'legacy'
$env:OCR_ENABLED = '0'
~~~

Expected: settings are read once at process start. Changing them requires a process/container restart. Runtime rollback keeps P1 tables; it does not run Alembic downgrade.

---

## Task 13: Document Operations and Execute the Gate-Authorized Live Additive Upgrade

**Files:**

- Create: <code>backend/tests/test_p1_documentation_contract.py</code>
- Modify: <code>docs/DATABASE.md</code>

- [ ] **Step 1: Write the documentation contract RED test (2-5 minutes)**

Require <code>docs/DATABASE.md</code> to contain:

- revision <code>20260807_01</code> and all five table names;
- the exact cache and artifact identity keys;
- canonical ProcessingJob type/scope/nullability CHECKs and all seven ArtifactKind values;
- the no-historical-backfill rule;
- four-kind CredentialStore environment/Keyring/legacy priority and exact mappings, <code>study-app</code> Keyring names, hasKey/keyTail redaction, blank-preserve, explicit clear, and fixed/unsupported-probe behavior;
- the retained legacy plaintext security debt, its Node rollback reason, and the rule that no P0-P6 phase calls final legacy-field removal;
- obsidian_exports Paper cascade affects only the database ledger while the Vault manifest retains a non-auto-cleanable orphan/tombstone;
- Python 3.10 compatibility and no venv rebuild instruction;
- pre-upgrade create/verify/restore-check;
- isolated upgrade/downgrade/re-upgrade;
- legacy count/hash, five-zero-table, single-head, quick/integrity/FK gates;
- canary and emergency startup values;
- runtime rollback before schema downgrade;
- nonempty-table downgrade refusal;
- the explicit warning that restoring a pre-upgrade snapshot discards post-snapshot writes.

- [ ] **Step 2: Run and confirm intended RED (2-5 minutes)**

Run:

~~~powershell
$env:PYTHONDONTWRITEBYTECODE = '1'
.\.venv\Scripts\python.exe -B -m unittest backend.tests.test_p1_documentation_contract -v
~~~

Expected: FAIL because P1 operations are not fully documented. A text-decoding failure is not the intended RED.

- [ ] **Step 3: Add the complete operational runbook (2-5 minutes per subsection)**

Document schema, job scope, provenance, credential storage/redaction/security debt, fixed probes, backup evidence, isolated rehearsal, mandatory Live upgrade gate, rollout, restart, runtime rollback, guarded downgrade, Obsidian orphan behavior, and disaster restore. State that this task's user authorization permits the Live additive upgrade only after every isolated and writer-stop gate is recorded green; the CLI never upgrades automatically, and any failed gate stops before mutation.

- [ ] **Step 4: Rerun the target test as GREEN evidence (2-5 minutes)**

Run:

~~~powershell
$env:PYTHONDONTWRITEBYTECODE = '1'
.\.venv\Scripts\python.exe -B -m unittest backend.tests.test_p1_documentation_contract -v
~~~

Expected: the documentation contract passes with every required command/value present.

- [ ] **Step 5: Prepare a fresh Live preflight without migrating Live (2-5 minutes)**

Run:

~~~powershell
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$p1LivePreflight = .\.venv\Scripts\python.exe -m backend.app.cli.database_backup inspect --database data/app.db | ConvertFrom-Json
if ($LASTEXITCODE -ne 0 -or -not $p1LivePreflight.ok) { throw 'Live preflight fingerprint failed.' }
if ($null -ne $p1LivePreflight.database.alembicVersion) { throw 'Live P1 upgrade must start with no Alembic current revision.' }
if ($p1LivePreflight.database.quickCheck -ne 'ok' -or $p1LivePreflight.database.integrityCheck -ne 'ok' -or $p1LivePreflight.database.foreignKeyViolations -ne 0) { throw 'Live preflight health gate failed.' }
$p1LiveLegacyTables = @('papers','progress','paper_reviews','notes','favorites','translations','paper_vectors','cite_edges','ingest_jobs','job_candidates','job_schedules','schema_migrations')
foreach ($p1Table in $p1LiveLegacyTables) {
  if (-not ($p1LivePreflight.database.tableCounts.PSObject.Properties.Name -contains $p1Table)) { throw "Live preflight count map is missing legacy table $p1Table." }
  if (-not ($p1LivePreflight.database.tableSha256.PSObject.Properties.Name -contains $p1Table)) { throw "Live preflight hash map is missing legacy table $p1Table." }
}
~~~

Expected: a read-only healthy fingerprint. Record the evidence, verify all Node, Python Agent, worker, and scheduler writers are stopped, and continue to the already authorized Live additive upgrade only when every preceding gate is green. On any mismatch or active writer, stop before mutation and preserve the verified backup.

- [ ] **Step 6: Perform the gate-authorized Live additive upgrade (2-5 minutes)**

When every preceding gate is green and the writer-stop evidence is recorded, run:

~~~powershell
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$p1PreviousDbPath = [Environment]::GetEnvironmentVariable('DB_PATH', 'Process')
$p1HadDbPath = $null -ne $p1PreviousDbPath
$p1LiveDb = (Resolve-Path -LiteralPath 'data/app.db').Path
$env:DB_PATH = $p1LiveDb
try {
  .\.venv\Scripts\python.exe -m alembic -c backend/alembic.ini upgrade 20260807_01
  if ($LASTEXITCODE -ne 0) { throw 'Live P1 additive upgrade failed.' }
  $p1Current = @(& .\.venv\Scripts\python.exe -m alembic -c backend/alembic.ini current | ForEach-Object { "$_".Trim() } | Where-Object { $_ })
  if ($LASTEXITCODE -ne 0 -or $p1Current.Count -ne 1 -or $p1Current[0] -notmatch '^20260807_01\s+\(head\)$') { throw 'Live Alembic current is not uniquely 20260807_01 (head).' }
  $p1LiveUpgraded = .\.venv\Scripts\python.exe -m backend.app.cli.database_backup inspect --database $p1LiveDb | ConvertFrom-Json
  if ($LASTEXITCODE -ne 0 -or -not $p1LiveUpgraded.ok -or $p1LiveUpgraded.database.alembicVersion -ne '20260807_01') { throw 'Live upgrade did not reach 20260807_01.' }
  foreach ($p1Table in @('document_sources','generated_artifacts','processing_jobs','document_chunks','obsidian_exports')) { if ($p1LiveUpgraded.database.tableCounts.$p1Table -ne 0) { throw "Live P1 table is not initially empty: $p1Table." } }
  foreach ($p1Table in $p1LiveLegacyTables) {
    if (-not ($p1LiveUpgraded.database.tableCounts.PSObject.Properties.Name -contains $p1Table)) { throw "Live post-upgrade count map is missing legacy table $p1Table." }
    if (-not ($p1LiveUpgraded.database.tableSha256.PSObject.Properties.Name -contains $p1Table)) { throw "Live post-upgrade hash map is missing legacy table $p1Table." }
    if ($p1LivePreflight.database.tableCounts.$p1Table -ne $p1LiveUpgraded.database.tableCounts.$p1Table) { throw "Live P1 changed legacy count for $p1Table." }
    if ($p1LivePreflight.database.tableSha256.$p1Table -ne $p1LiveUpgraded.database.tableSha256.$p1Table) { throw "Live P1 changed legacy hash for $p1Table." }
  }
} finally {
  if ($p1HadDbPath) { $env:DB_PATH = $p1PreviousDbPath } else { Remove-Item Env:DB_PATH -ErrorAction SilentlyContinue }
}
~~~

Expected: one head <code>20260807_01</code>, five initially empty tables, unchanged legacy counts/hashes, quick/integrity ok, and zero FK violations. Restart first with all emergency rollback values; enable the P1 canary only as a separate startup choice.

- [ ] **Step 7: Exercise runtime rollback without schema downgrade (2-5 minutes)**

Restart with the emergency values from Task 12. Run P0 compatibility tests and one legacy artifact read against an isolated contract database.

Expected: legacy reads/writes work without querying P1; OCR construction/calls remain zero; additive tables remain available for later re-enable.

- [ ] **Step 8: Record the guarded schema-downgrade boundary without executing it in the forward path (2-5 minutes)**

The isolated upgrade → downgrade → re-upgrade rehearsal in Task 11 is the P1 rollback verification. The normal P1 forward path must leave Live at <code>20260807_01</code>; do not execute a Live downgrade while continuing to P2. A Live schema downgrade is a separate emergency operation permitted only after stopping every writer, proving all five P1 table counts are zero, recording a fresh verified backup, and explicitly deciding not to continue the forward migration. Its command is:

~~~powershell
$p1PreviousDbPath = [Environment]::GetEnvironmentVariable('DB_PATH', 'Process')
$p1HadDbPath = $null -ne $p1PreviousDbPath
$env:DB_PATH = (Resolve-Path -LiteralPath 'data/app.db').Path
try {
  .\.venv\Scripts\python.exe -m alembic -c backend/alembic.ini downgrade base
  if ($LASTEXITCODE -ne 0) { throw 'Emergency P1 downgrade failed or was guarded.' }
} finally {
  if ($p1HadDbPath) { $env:DB_PATH = $p1PreviousDbPath } else { Remove-Item Env:DB_PATH -ErrorAction SilentlyContinue }
}
~~~

Expected only during that separate emergency operation: the guarded downgrade succeeds only with five empty tables. If any count is nonzero, it fails without dropping a table; keep runtime rollback active. The P1 implementation flow does not run this command and instead verifies <code>alembic current</code> plus the read-only database fingerprint still report exactly <code>20260807_01</code> before entering Task 14. Restoring <code>$p1Create.backupPath</code> is a separate explicit recovery decision because all writes after that snapshot would be lost.

---

## Task 14: Run Full Verification and Continue Through the P1 Exit Gate

**Files:**

- Verify: all P0/P1 production, migration, test, and documentation files

- [ ] **Step 1: Run all backend tests (2-5 minutes)**

Run:

~~~powershell
$env:PYTHONDONTWRITEBYTECODE = '1'
.\.venv\Scripts\python.exe -B -m unittest discover -s backend/tests -p "test_*.py" -v
~~~

Expected: exit 0 with no skipped P1 test and no Live database access.

- [ ] **Step 2: Rerun the CredentialStore security suite explicitly (2-5 minutes)**

Run:

~~~powershell
.\.venv\Scripts\python.exe -B -m unittest backend.tests.test_credentials -v
~~~

Expected: all credential priority, migration, mutation, redaction, fixed-probe, and concurrency tests pass with zero real Keyring/settings/network mutation.

- [ ] **Step 3: Run all legacy Python tests (2-5 minutes)**

Run:

~~~powershell
.\.venv\Scripts\python.exe -B -m unittest discover -s test -p "test_*.py" -v
~~~

Expected: exit 0; P0 extract/explain/translate characterization remains unchanged.

- [ ] **Step 3a: Run the frozen MCP nine-tool characterization (2-5 minutes)**

Run:

~~~powershell
.\.venv\Scripts\python.exe -B -m unittest discover -s test -p "test_mcp_server.py" -v
~~~

Expected: exit 0; tools/list remains exactly nine and every MCP query remains read-only.

- [ ] **Step 4: Run Node compatibility tests (2-5 minutes)**

Run:

~~~powershell
node --test test/legacy-api-contract.test.js test/backend-rollout.test.js test/title-translations-api.test.js test/server-modules.test.js
~~~

Expected: exit 0; Node remains the default production owner and every legacy wire contract is unchanged.

- [ ] **Step 5: Run root Node tests (2-5 minutes)**

Run:

~~~powershell
npm.cmd test
~~~

Expected: exit 0.

- [ ] **Step 6: Run frontend tests (2-5 minutes)**

Run:

~~~powershell
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$p1ExitBaselineJson = node scripts/pre-existing-failure-baseline.mjs verify --baseline contracts/pre-existing-test-failures-v1.json
$p1ExitBaselineCode = $LASTEXITCODE
if ($p1ExitBaselineCode -ne 0) { throw "P1 exit baseline verification failed with exit code $p1ExitBaselineCode." }
$p1ExitBaseline = $p1ExitBaselineJson | ConvertFrom-Json
$p1ExitBaselineRequiredFields = @('baselineMatched','observedSuiteExitCode','overallGreen')
foreach ($p1ExitBaselineField in $p1ExitBaselineRequiredFields) {
  if (-not ($p1ExitBaseline.PSObject.Properties.Name -contains $p1ExitBaselineField)) { throw "P1 exit baseline verifier omitted required field $p1ExitBaselineField." }
}
if ($p1ExitBaseline.baselineMatched -isnot [bool] -or $p1ExitBaseline.baselineMatched -ne $true) { throw 'P1 exit baseline verifier did not report boolean baselineMatched=true.' }
if ($p1ExitBaseline.observedSuiteExitCode -isnot [int] -and $p1ExitBaseline.observedSuiteExitCode -isnot [long]) { throw 'P1 exit baseline verifier did not report an integer observedSuiteExitCode.' }
if ($p1ExitBaseline.overallGreen -isnot [bool]) { throw 'P1 exit baseline verifier did not report boolean overallGreen.' }
$p1ExitObservedSuiteExitCode = [long]$p1ExitBaseline.observedSuiteExitCode
if (($p1ExitObservedSuiteExitCode -eq 0) -ne $p1ExitBaseline.overallGreen) { throw 'P1 exit baseline verifier reported inconsistent observedSuiteExitCode and overallGreen.' }
~~~

Expected: verifier process exit 0 and <code>baselineMatched=true</code>; raw frontend exit 0 reports <code>overallGreen=true</code>, while an exact reviewed non-zero remains raw non-zero with <code>overallGreen=false</code>. No React behavior/layout/style/route changed. Any ID/signature/hash/path drift stops P1.

- [ ] **Step 7: Run frontend static verification (2-5 minutes per command)**

Run:

~~~powershell
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
function Invoke-CheckedNative {
  param([Parameter(Mandatory = $true)][string]$Label, [Parameter(Mandatory = $true)][scriptblock]$Command)
  $output = & $Command
  $exitCode = $LASTEXITCODE
  if ($exitCode -ne 0) { throw "$Label failed with exit code $exitCode." }
  $output
}
Invoke-CheckedNative 'frontend typecheck' { npm.cmd run typecheck --prefix frontend }
Invoke-CheckedNative 'frontend lint' { npm.cmd run lint --prefix frontend }
Invoke-CheckedNative 'frontend build' { npm.cmd run build --prefix frontend }
Invoke-CheckedNative 'frontend e2e' { npm.cmd run e2e --prefix frontend }
~~~

Expected: each command exits 0; the complete production E2E suite runs without a P1-specific skip.

- [ ] **Step 8: Verify migration metadata and dependency health (2-5 minutes)**

Verify the migration graph against the isolated re-upgraded database, then separately prove that Live remains at P1 head. Preserve any caller-owned DB_PATH value across both checks:

~~~powershell
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
function Invoke-CheckedNative {
  param([Parameter(Mandatory = $true)][string]$Label, [Parameter(Mandatory = $true)][scriptblock]$Command)
  $output = & $Command
  $exitCode = $LASTEXITCODE
  if ($exitCode -ne 0) { throw "$Label failed with exit code $exitCode." }
  $output
}
$p1PreviousDbPath = [Environment]::GetEnvironmentVariable('DB_PATH', 'Process')
$p1HadDbPath = $null -ne $p1PreviousDbPath
try {
  $env:DB_PATH = $p1MigrationDb
  $p1FinalHeads = @(Invoke-CheckedNative 'P1 migration heads check' { .\.venv\Scripts\python.exe -m alembic -c backend/alembic.ini heads } | ForEach-Object { "$_".Trim() } | Where-Object { $_ })
  if ($p1FinalHeads.Count -ne 1 -or $p1FinalHeads[0] -notmatch '^20260807_01\s+\(head\)$') { throw 'P1 migration graph no longer exposes exactly one 20260807_01 (head).' }
  $env:DB_PATH = (Resolve-Path -LiteralPath 'data/app.db').Path
  $p1LiveCurrent = @(Invoke-CheckedNative 'P1 Live current check' { .\.venv\Scripts\python.exe -m alembic -c backend/alembic.ini current } | ForEach-Object { "$_".Trim() } | Where-Object { $_ })
  if ($p1LiveCurrent.Count -ne 1 -or $p1LiveCurrent[0] -notmatch '^20260807_01\s+\(head\)$') { throw 'Live database no longer reports exactly one P1 head.' }
  $p1LiveFinal = Invoke-CheckedNative 'final Live fingerprint' { .\.venv\Scripts\python.exe -m backend.app.cli.database_backup inspect --database $env:DB_PATH } | ConvertFrom-Json
  if (-not $p1LiveFinal.ok -or $p1LiveFinal.database.alembicVersion -ne '20260807_01') { throw 'Live fingerprint no longer reports P1 head.' }
  .\.venv\Scripts\python.exe -m pip check
  if ($LASTEXITCODE -ne 0) { throw 'Python dependency check failed.' }
} finally {
  if ($p1HadDbPath) { $env:DB_PATH = $p1PreviousDbPath } else { Remove-Item Env:DB_PATH -ErrorAction SilentlyContinue }
}
~~~

Expected: the graph has exactly <code>20260807_01 (head)</code>, Live <code>current</code> and read-only fingerprint both report <code>20260807_01</code>, dependency check exits 0, and the caller's original DB_PATH state is restored.

- [ ] **Step 9: Verify diff quality and workspace scope (2-5 minutes)**

Run:

~~~powershell
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
function Invoke-CheckedNative {
  param([Parameter(Mandatory = $true)][string]$Label, [Parameter(Mandatory = $true)][scriptblock]$Command)
  $output = & $Command
  $exitCode = $LASTEXITCODE
  if ($exitCode -ne 0) { throw "$Label failed with exit code $exitCode." }
  $output
}
Invoke-CheckedNative 'P1 diff whitespace check' { git diff --check }
Invoke-CheckedNative 'P1 changed-file listing' { git diff --name-only }
Invoke-CheckedNative 'P1 workspace status' { git status --short }
~~~

Expected: no whitespace error; no P1 implementation change under <code>public/</code>, React component/style/route directories, <code>AGENTS.md</code>, <code>.agents/</code>, or Live <code>data/app.db*</code>. Existing user-owned changes remain untouched.

---

## P1 Exit Gate

P2 begins immediately without another approval when every statement is true. Any failed statement stops progression, preserves the verified backup and rollback settings, and must be reported before implementation continues:

- P0 backup, compatibility, React Gateway, rollout-default, and disabled-OCR contracts remain green.
- The existing Python 3.10.9 venv passes the pinned dependency tests and was not deleted/recreated.
- Alembic has exactly one head, <code>20260807_01</code>.
- A verified restore completed upgrade, validation, downgrade, validation, and re-upgrade.
- Legacy table counts and hashes are identical after every migration phase.
- document_sources, generated_artifacts, processing_jobs, document_chunks, and obsidian_exports exist and are initially empty.
- All five tables have every required column, FK, UNIQUE, CHECK, and index contract.
- Paper IDs remain unchanged.
- ProcessingJob scope CHECKs accept global obsidian_sync and Paper-scoped obsidian_export while requiring Paper/sourceMode for source_materialize/ocr/explain/translate/embed.
- ArtifactKind is exactly explainer/translation/summary/outline/study_card/classification/metadata in the domain while the database kind column remains evolvable nonblank TEXT.
- DocumentSourcePipeline uses the exact required interface and cache identity.
- Native mode constructs/calls no OCR provider and performs no network request.
- GenerationPipeline consumes only proven SourceDocument Markdown.
- Failed generation never overwrites a ready artifact.
- Ready new artifact and legacy projection writes are one SQLite transaction.
- New-first reads fall back only to legacy values when no eligible ready row exists.
- No historical explainer/translation was assigned a fabricated source_document_id.
- All four Credential kinds use environment then Keyring then their mapped legacy field; blank update preserves, explicit clear synchronizes only that kind's writable tiers, cross-kind fields remain unchanged, and environment remains authoritative.
- Credential status exposes only hasKey/keyTail/environmentManaged; no real value appears in a DTO, log, exception, stdout/stderr, probe result, or secret-returning CLI.
- The fixed LLM/OCR probe fixtures are the only probe payloads; production P1 OCR and unsupported embedding/Semantic Scholar probing perform zero transport calls and no user PDF can enter the interface.
- Legacy <code>apiKey|ocrApiKey|embedApiKey|s2ApiKey</code> remain as an explicit Node-rollback security debt throughout P0-P6; final removal is outside this roadmap and requires formal retirement of the Node rollback window.
- Paper deletion may cascade an obsidian_exports database row but cannot invoke Vault I/O; the retained manifest entry is an orphan/tombstone that is never auto-cleaned.
- Runtime rollback uses startup-only legacy values, requires a restart, and keeps additive tables.
- Schema downgrade refuses nonempty P1 tables.
- API ownership remains legacy; the P1 FastAPI object is an extension/test seam only.
- Work remains on the authorized independent `codex/` branch; no staging, commit, push, UI redesign, user-file cleanup, or migration outside the recorded green gates occurred.

## Self-Review Checklist

- [ ] Every production edit has a named RED test, intended-failure check, smallest implementation step, and fully listed GREEN command.
- [ ] Every checkbox is scoped to a 2-5 minute action or a 2-5 minute action per named unit.
- [ ] All file paths, class names, method signatures, revision IDs, table columns, constraints, commands, expected outcomes, and rollback values are explicit.
- [ ] Persistence is SQLite-only with SQLAlchemy 2, Alembic, aiosqlite, WAL, and Keyring for llm/ocr/embedding/semantic_scholar secrets.
- [ ] The plan supports the current Python 3.10.9 venv and never instructs an environment rebuild.
- [ ] The five new tables are additive; document_chunks is created for P3 and obsidian_exports is reused by P5.
- [ ] ProcessingJob status is exactly queued/running/succeeded/failed/cancelled.
- [ ] ProcessingJob paper/sourceMode nullability is constrained by canonical job type, including global obsidian_sync.
- [ ] SourceMode is exactly native/ocr.
- [ ] ArtifactKind contains all seven canonical provenance-bearing artifact kinds.
- [ ] CredentialStore has an independently runnable RED-to-GREEN suite and no secret-returning public surface.
- [ ] Historical artifact provenance is preserved as unknown legacy provenance.
- [ ] Exactly one authorized `codex/` branch is used, and no Git staging, commit, or push step exists.
