# Database and data

Paper-Study uses one local SQLite database. The Python runtime creates a new
database when a clone has no `data/app.db`, then applies the Alembic migrations
through revision `20260807_03` before starting FastAPI.

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

The database layer enables WAL mode, foreign keys, and a busy timeout for local
concurrent API, worker, and scheduler access.

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
