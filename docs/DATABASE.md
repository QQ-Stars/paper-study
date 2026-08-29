# Database and data

Paper-Study uses one local SQLite database. The Python runtime creates a new
database when a clone has no `data/app.db`, then applies the Alembic migrations
through revision `20260826_01` before starting FastAPI.

## Files

- `data/app.db` is the live database (usually ignored by Git).
- `data/backups/` stores automatic SQLite backups made before an upgrade.
- `data/papers.json` and `data/progress.json` are seed/portable metadata.
- `paper/` contains saved explainers and source notes used by the seed import.
- `notes/` contains user notes.
- `data/pdfs/`, `data/explainers/`, `data/translations/`, and
  `data/ocr_markdown/` contain generated or imported artifacts.

The startup migration only seeds a newly created database. It never reseeds an
existing database, and it does not remove PDFs, notes, or generated artifacts.

## Schema

Alembic files in `backend/migrations/versions/` are the authoritative schema
history. `db/schema.sql` is the base schema used for a fresh SQLite file before
Alembic finishes the migration. The main groups are:

- `papers`, `progress`, and `notes` for the local library;
- `document_sources`, `generated_artifacts`, and `document_chunks` for the
  migrated document pipeline;
- `processing_jobs` and related event tables for background work;
- source-consumer, full-text-search, embedding, and Obsidian export tables.
- `reproduction_projects`, `reproduction_documents`, `experiment_runs`,
  `reproduction_artifacts`, `reproduction_notes`, and `reproduction_results`
  for the paper reproduction
  workspace. These tables are separate from ordinary paper notes, generated
  artifacts, and processing jobs. The paper relationship is nullable and uses
  `SET NULL`, retaining the project title snapshot when a paper is removed.

Each reproduction project also owns an isolated maintenance directory keyed by
its stable opaque project ID:

```text
data/reproduction-artifacts/projects/<project-id>/
├─ project.json
├─ document.md
├─ runs.json
├─ results.json
├─ notes.json
└─ artifacts/
```

SQLite remains the transactional source of truth. The Markdown and JSON files
are atomically written, human-readable mirrors for backup, inspection, and
project-level maintenance; editing them by hand does not import changes into
SQLite, and a later API sync can overwrite those edits. `project.json` carries
an aggregate fingerprint, so an incomplete or stale mirror is retried on a
later detail read. A temporary mirror-write failure does not roll back an
already committed SQLite mutation. Existing projects are backfilled the first
time their detail endpoint is read. Legacy attachment keys
directly below `projects/<project-id>/` remain readable, while new uploads use
server-owned opaque names below the project's `artifacts/` directory. Copying a
project creates new physical attachment files and rewrites document attachment
URLs, so deleting the source project does not break its copy.

The API enforces project-directory containment before attachment registration,
copy, or download. HTML files are stored as non-executable download attachments;
image and PDF uploads receive basic file-signature validation. The first release
records experiment commands and parameters but never executes arbitrary shell
commands.

The database layer enables WAL mode, foreign keys, and a busy timeout for local
concurrent API, worker, and scheduler access.

## 13. P3 source consumers、search 与回滚门禁

The P3 migration contract remains additive: `20260807_02 → 20260807_03`, then
the reproduction workspace migration `20260825_04 → 20260826_01`. Before a
production upgrade, capture the `pre-p3-source-consumers-search` backup and
verify the following stable snapshots:

- `papers`, `progress`, `paper_reviews`, `notes`, `favorites`, `translations`,
  `paper_vectors`, `cite_edges`, `ingest_jobs`, `job_candidates`,
  `job_schedules`, and `schema_migrations` tableCounts/tableSha256;
For deterministic checks use `papers` tableCounts/tableSha256, `progress` tableCounts/tableSha256,
`paper_reviews` tableCounts/tableSha256, `notes` tableCounts/tableSha256, `favorites` tableCounts/tableSha256,
`translations` tableCounts/tableSha256, `paper_vectors` tableCounts/tableSha256, `cite_edges` tableCounts/tableSha256,
`ingest_jobs` tableCounts/tableSha256, `job_candidates` tableCounts/tableSha256, `job_schedules` tableCounts/tableSha256,
and `schema_migrations` tableCounts/tableSha256.
- `paperIds`, `explainers`, `translations`, `notes`, `paperVectors`,
  `documentSources`, `generatedArtifacts`, and `processingJobs`
  contentCounts/contentSha256;
- P3 search settings: `trigram case_sensitive 0 remove_diacritics 1`, plus
  `documentChunks`, `chunkEmbeddings`, `translationCheckpoints`,
  `documentChunksFtsCoverage`, and `documentChunksFtsIntegrity`.

The FTS checks use `INSERT INTO document_chunks_fts(document_chunks_fts,rank) VALUES('integrity-check',1)` and
`INSERT INTO document_chunks_fts(document_chunks_fts) VALUES('rebuild')`.
Snapshot output keeps each key in the form `paperIds` contentCounts/contentSha256,
`explainers` contentCounts/contentSha256, `translations` contentCounts/contentSha256,
`notes` contentCounts/contentSha256, `paperVectors` contentCounts/contentSha256,
`documentSources` contentCounts/contentSha256, `generatedArtifacts` contentCounts/contentSha256,
and `processingJobs` contentCounts/contentSha256.

The rollback runbook is: stop new enqueue → stop worker claim → wait for or
cancel running jobs → stop API writers. The query path never re-embeds; source stale cascade
is verified before downgrade. Validate with `upgrade 20260807_03`,
The exact shutdown order is 停止新 enqueue → 停止 worker claim → 等待/取消 running jobs → 停止 API writer.
and only downgrade to
`downgrade 20260807_02` on a disposable restore. A guarded rollback may use
`-x allow_p3_data_loss=true downgrade 20260807_02` only when the database is
empty; otherwise return `P3_DOWNGRADE_BLOCKED_NONEMPTY`. Keep
`API_BACKEND_MODE=legacy`, `DOCUMENT_PIPELINE_MODE=legacy`,
`GENERATION_PIPELINE_MODE=legacy`, `ARTIFACT_READ_MODE=legacy`,
`ARTIFACT_WRITE_MODE=legacy`, and `OCR_ENABLED=0` available as rollback flags.
The map-presence guard and before/after equality guard must pass. The
allow-data-loss flag is only on restore-validation-*/app.db.
不得在 Live 使用 allow_p3_data_loss=true.

## Commands

From the repository root on Windows:

```powershell
./start.ps1
./stop.ps1
```

The launcher uses `.venv/Scripts/python.exe -m backend.app.cli.local_runtime`
and serves the built `ui-redesign` application at
`http://127.0.0.1:5173/workspace/`. Docker and the removed Node server are not
part of the current runtime.

For a disposable test database, pass `--db` to the local runtime module. Do not
point test commands at the live `data/app.db` when testing migrations or
destructive maintenance.
