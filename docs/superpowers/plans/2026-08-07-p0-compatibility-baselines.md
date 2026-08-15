# P0 Compatibility Baselines and Rollback Controls Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Execute each task in order. Every production change starts with the named failing test, confirms the expected failure, applies the smallest implementation, and reruns the fully listed target command to green. Work on the independently authorized `codex/` branch created before P0; do not stage files, create commits, or push.

**Goal:** Re-verify and record the already completed SQLite backup slice, capture a versioned pre-existing-test-failure baseline, freeze the current Node HTTP/NDJSON, unchanged Python Agent CLI, and React Gateway behavior as executable compatibility contracts, prove that disabled OCR performs zero provider calls, and install startup-only rollback controls before any P1 data or pipeline implementation is enabled.

**Architecture:** The current `server.js` remains the production owner and retains every route body in place. `contracts/legacy-api-v1.json` is the checked-in compatibility ledger; contract tests spawn the real `server.js` as a child on an OS-assigned loopback port with `DB_PATH` pointing to a process-owned temporary SQLite database, then exercise it only through HTTP/NDJSON. One narrow listen bootstrap seam may report the actual port to the child runner, but it cannot own routing, body parsing, response shaping, Agent translation, or application dependencies. Python tests characterize the current `python -m agent` dispatch without changing `agent/__main__.py`; rollout settings are immutable process-start values and default to legacy behavior with OCR disabled. Route-family extraction into Node compatibility Adapters begins only in P4.

**Tech Stack:** Node.js 22 built-in test runner, better-sqlite3, Python standard-library `unittest`, SQLite Online Backup and WAL, React/Vitest contract tests, existing Pydantic and PyMuPDF dependencies.

**Hard dependency:** Do not start Task 0A until `docs/superpowers/plans/2026-08-07-p0-sqlite-backup-rollback-slice.md` is fully implemented, all backend backup tests pass, both independent safety reviews report Important=0, and a fresh Live backup has passed independent `verify` plus isolated `restore-check`.

**Workspace constraints:** Protect the user's `AGENTS.md` and `.agents/` changes. Do not modify React components, CSS, routes, `public/`, Live `data/app.db*`, or user artifact files. Tests may create only process-owned temporary files. No task contains a Git commit step.

---

## Current Evidence That This Plan Freezes

- `server.js:122-604` is the current Node request router.
- `server.js:128-243` serves papers, reviews, notes, legacy artifacts, progress, favorites, and paper CRUD.
- `server.js:318-376` translates Python Agent stderr/stdout into explain and translate NDJSON.
- `server.js:434-483` scans, imports, downloads, and resolves PDFs.
- `db.js:7-27` opens the shared SQLite database, applies WAL/FK/busy-timeout settings, executes `db/schema.sql`, and performs startup schema mutations.
- `db.js:66-171` defines the current paper/list/artifact/notes/progress CRUD behavior.
- `agent/db.py:8-27,79-148` is the current Python sqlite3 access path and legacy artifact writer.
- `agent/extract.py:41-94` uses pymupdf4llm with PyMuPDF plain-text fallback and contains no OCR call.
- `agent/explain.py:31-129` writes successful explainers immediately and batch-commits each paper independently.
- `agent/translate.py:125-183` translates chunks concurrently and currently persists a marker for a failed chunk.
- `frontend/src/lib/api/paperApi.ts:61-152` and the seven `*Gateway.ts` factories are the React transport seam.
- `frontend/src/lib/api/decoders.ts:179-258,467-574` freezes JSON wire-field normalization.
- `frontend/src/lib/streaming/contracts.ts:213-370` freezes each NDJSON terminal family.
- `frontend/src/lib/api/endpoints.test.ts:9-57` currently exercises only a small subset of Gateway calls.
- `server.js:25-35` shows the existing startup-only `UI_ENTRY` rollback pattern; no backend, pipeline, artifact-read/write, or OCR rollout settings exist yet.

---

## File Responsibilities

### Existing files modified by this plan

- `backend/app/infrastructure/database_backup.py`: verify only; P0.1 does not alter the P0.0 backup implementation.
- `backend/tests/test_database_backup.py`: verify only; retain the completed P0.0 backup safety tests as the P0.1 entry gate.
- `server.js`: retain every existing route callback/body and read immutable rollout settings once at startup; its only structural change is replacing the final listen invocation with the narrow listen bootstrap seam described below.
- `agent/__main__.py`: verify only; its command dispatch, command names, arguments, stdout, stderr, and exit codes are frozen and this plan does not edit it.
- `frontend/src/lib/api/endpoints.test.ts`: retain focused transport behavior tests; the exhaustive contract lives in a new guard test.
- `docs/DATABASE.md`: document the P0 compatibility baseline, startup-only rollback values, restart requirement, and Live backup evidence.
- `Dockerfile`: expose only safe legacy/off defaults for the new rollout environment variables.
- `docker-compose.yml`: pass through the same startup-only rollout values with legacy/off defaults.

### New files created by this plan

- `contracts/pre-existing-test-failures-v1.json`: reviewed baseline containing the exact full-suite command, raw exit code, failed test IDs, normalized stack signatures, related-file SHA-256 values, capture time, and source-tree hash; an empty failure set is valid and preferred.
- `scripts/pre-existing-failure-baseline.mjs`: capture/compare CLI that runs the complete frontend Vitest command, normalizes only unstable time/address fragments, and never edits frontend files.
- `test/pre-existing-failure-baseline.test.js`: ledger schema, two-run stability, exact-match, drift-stop, and related-path-change guard tests.
- `contracts/legacy-api-v1.json`: complete machine-readable ledger of every supported current `server.js` HTTP/NDJSON method/path, with the exact count derived from the ledger rather than guessed in prose.
- `lib/server-listen.js`: the single permitted bootstrap seam; it receives the existing `http.Server`, preserves production listen defaults, and reports an OS-assigned test port over IPC without containing or importing any route body.
- `lib/backend-rollout.js`: strict immutable Node rollout parser.
- `test/support/legacy-server-process.js`: child-process owner that creates a temporary database, starts the unchanged route server with `HOST=127.0.0.1` and `PORT=0`, waits for the IPC-ready port, performs bounded shutdown, and proves no Live path was opened.
- `test/fixtures/legacy-server-preload.js`: child-only deterministic boundary doubles for Agent/provider calls, loaded before `server.js`; it cannot import or replace the route callback.
- `test/legacy-api-contract.test.js`: Node request/response and NDJSON compatibility contract tests.
- `test/backend-rollout.test.js`: Node rollout default, invalid-value, and unavailable-adapter tests.
- `backend/app/rollout.py`: strict immutable Python rollout parser with the same environment vocabulary as Node.
- `backend/tests/test_legacy_agent_contract.py`: characterization tests that invoke or patch only the existing `agent` CLI boundaries and assert `agent/__main__.py` remains unchanged; no production compatibility Adapter is created.
- `backend/tests/test_ocr_disabled_baseline.py`: disabled-OCR zero-construction and zero-call tests.
- `backend/tests/test_rollout_defaults.py`: Python rollout default and validation tests.
- `frontend/build/legacyGatewayGuard.ts`: build-time comparison between Gateway calls and the compatibility ledger.
- `frontend/build/legacyGatewayGuard.test.ts`: exhaustive Gateway coverage and request-shape tests without changing UI code.

---

## Task 0A: Capture and Guard the Versioned Pre-Existing Failure Baseline

**Files:**

- Create: `contracts/pre-existing-test-failures-v1.json`
- Create: `scripts/pre-existing-failure-baseline.mjs`
- Create: `test/pre-existing-failure-baseline.test.js`
- Verify only: `frontend/src/components/workspace-shell/WorkspaceShell.test.tsx`

- [ ] **Step 1: Write ledger and guard RED tests (2–5 minutes)**

Add exact tests named `baseline requires two identical full-suite captures before acceptance`, `baseline preserves the raw non-zero suite exit code`, `guard rejects failed-test-id or normalized-stack drift`, `guard rejects a changed related-file hash or a slice touching that path`, and `zero-failure capture stores empty arrays`. The v1 schema requires `version`, exact `command`, `exitCode`, sorted `failedTestIds`, sorted `normalizedStackSignatures`, sorted `relatedFileSha256`, `capturedAt`, and `sourceTreeHash`. Normalization may remove durations, worker IDs, temporary absolute prefixes, and line/column numbers only; it must preserve test names, assertion text, source-relative stack paths, expected/received values, and error class.

- [ ] **Step 2: Run the guard tests and confirm RED (2–5 minutes)**

Run: `node --test test/pre-existing-failure-baseline.test.js`

Expected: FAIL because the capture/compare CLI and ledger do not exist. A frontend test failure is not the intended RED because this step uses synthetic captured output.

- [ ] **Step 3: Implement the minimal capture/compare CLI (2–5 minutes per behavior)**

The capture command runs exactly `npm.cmd run test:run --prefix frontend`, retains its raw exit code, extracts complete Vitest test IDs and normalized signatures, hashes every repo-relative file named by those signatures plus the failing test file, and records the current source-tree hash. It writes only an operator-supplied candidate path by exclusive create. `accept` requires two independently produced candidates with identical command, exitCode, failedTestIds, normalizedStackSignatures, and relatedFileSha256; a difference is `PRE_EXISTING_FAILURE_BASELINE_UNSTABLE` and no ledger is written. A zero exit code is stored with empty arrays. Earlier observations of a fluctuating five-failure WorkspaceShell run are not hard-coded: only the two fresh slice-entry captures are authoritative.

- [ ] **Step 4: Rerun the guard tests as GREEN evidence (2–5 minutes)**

Run: `node --test test/pre-existing-failure-baseline.test.js`

Expected: all five named tests pass; synthetic non-zero results remain explicitly non-zero in the JSON and drift cannot be normalized away.

- [ ] **Step 5: Capture twice and accept only a stable fresh baseline (2–5 minutes per run)**

Run:

~~~powershell
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
node scripts/pre-existing-failure-baseline.mjs capture --output data/compatibility/pre-existing-run-1.json
if ($LASTEXITCODE -ne 0) { throw 'Baseline capture CLI run 1 failed before producing a candidate.' }
$preExistingRun1 = Get-Content -Raw -LiteralPath data/compatibility/pre-existing-run-1.json | ConvertFrom-Json
if ($null -eq $preExistingRun1.exitCode) { throw 'Run 1 candidate omitted nested Vitest exitCode.' }
node scripts/pre-existing-failure-baseline.mjs capture --output data/compatibility/pre-existing-run-2.json
if ($LASTEXITCODE -ne 0) { throw 'Baseline capture CLI run 2 failed before producing a candidate.' }
$preExistingRun2 = Get-Content -Raw -LiteralPath data/compatibility/pre-existing-run-2.json | ConvertFrom-Json
if ($null -eq $preExistingRun2.exitCode) { throw 'Run 2 candidate omitted nested Vitest exitCode.' }
node scripts/pre-existing-failure-baseline.mjs accept --first data/compatibility/pre-existing-run-1.json --second data/compatibility/pre-existing-run-2.json --output contracts/pre-existing-test-failures-v1.json
if ($LASTEXITCODE -ne 0) { throw 'Baseline accept CLI rejected the two candidates.' }
$acceptedBaseline = Get-Content -Raw -LiteralPath contracts/pre-existing-test-failures-v1.json | ConvertFrom-Json
if ($acceptedBaseline.exitCode -ne $preExistingRun1.exitCode -or $acceptedBaseline.exitCode -ne $preExistingRun2.exitCode) { throw 'Accepted baseline did not preserve the nested Vitest raw exit code.' }
~~~

Expected: the first two commands each report the real nested Vitest exit code; `accept` succeeds only when the two complete normalized results and related hashes are identical. If both runs have zero failures, v1 records `exitCode: 0` with empty sets. If both have the same non-zero result, the ledger retains that non-zero value and exact evidence; it is not described as green. Any inconsistent pair is treated as flaky and stops P0.1 before compatibility implementation.

- [ ] **Step 6: Establish the progression guard used by P0–P5 (2–5 minutes)**

`verify` reruns the same full command and compares exact failedTestIds/signatures/related hashes while also checking `git diff --name-only` did not touch a related path. Its only accepted invocation is:

~~~powershell
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$baselineVerifyJson = node scripts/pre-existing-failure-baseline.mjs verify --baseline contracts/pre-existing-test-failures-v1.json
$baselineVerifyExit = $LASTEXITCODE
if ($baselineVerifyExit -ne 0) { throw "Pre-existing failure baseline verification failed with exit code $baselineVerifyExit." }
$baselineVerify = $baselineVerifyJson | ConvertFrom-Json
$baselineRequiredFields = @('baselineMatched','observedSuiteExitCode','overallGreen')
foreach ($baselineField in $baselineRequiredFields) {
  if (-not ($baselineVerify.PSObject.Properties.Name -contains $baselineField)) { throw "Baseline verifier omitted required field $baselineField." }
}
if ($baselineVerify.baselineMatched -isnot [bool] -or $baselineVerify.baselineMatched -ne $true) { throw 'Baseline verifier did not report boolean baselineMatched=true.' }
if ($baselineVerify.observedSuiteExitCode -isnot [int] -and $baselineVerify.observedSuiteExitCode -isnot [long]) { throw 'Baseline verifier did not report an integer observedSuiteExitCode.' }
if ($baselineVerify.overallGreen -isnot [bool]) { throw 'Baseline verifier did not report boolean overallGreen.' }
$baselineObservedSuiteExitCode = [long]$baselineVerify.observedSuiteExitCode
if (($baselineObservedSuiteExitCode -eq 0) -ne $baselineVerify.overallGreen) { throw 'Baseline verifier reported inconsistent observedSuiteExitCode and overallGreen.' }
~~~

A stable non-zero match may authorize only the current unrelated slice and emits `baselineMatched=true`, `observedSuiteExitCode=<raw non-zero>`, `overallGreen=false`; any new, removed, renamed, reordered-to-different-ID, signature-drifted, hash-drifted, or slice-touched failure exits 2 and stops. P1–P5 run this exact command at both entry and exit. P6 never uses this exception for completion: its final full suite must have raw exit code 0 unless the user explicitly approves a changed completion standard.

---

## Task 0: Re-verify the Completed SQLite Backup Entry Gate

**Files:**

- Verify only: `backend/app/infrastructure/database_backup.py`
- Verify only: `backend/tests/test_database_backup.py`
- Follow: `docs/superpowers/plans/2026-08-07-p0-sqlite-backup-rollback-slice.md`

- [ ] **Step 1: Re-run the completed P0.0 safety gate as GREEN entry evidence (2–5 minutes)**

Run:

~~~powershell
$env:PYTHONDONTWRITEBYTECODE = '1'
.\.venv\Scripts\python.exe -m unittest `
  backend.tests.test_database_backup.DatabaseBackupTests.test_backup_rejects_multiple_alembic_heads_without_publishing_files `
  backend.tests.test_database_backup.DatabaseBackupTests.test_restore_rejects_sidecar_like_output_directory_before_creating_it `
  backend.tests.test_database_backup.DatabaseBackupTests.test_restore_collision_never_cleans_files_it_does_not_own `
  backend.tests.test_database_backup.DatabaseBackupTests.test_verify_rejects_backup_sidecars_without_deleting_them `
  backend.tests.test_database_backup.DatabaseBackupTests.test_backup_target_collision_preserves_existing_file_and_sidecars `
  -v
~~~

Expected: `Ran 5 tests` and `OK`; multiple-head failure remains classified, existing collision and sidecar bytes are unchanged, invalid restore output is rejected before creation, and no generated temporary file remains. Any failure stops P0.1 and returns ownership to the P0.0 plan; P0.1 must not patch the backup module.

- [ ] **Step 2: Run the complete backend backup suite (2–5 minutes)**

Run:

~~~powershell
$env:PYTHONDONTWRITEBYTECODE = '1'
.\.venv\Scripts\python.exe -m unittest discover -s backend/tests -p "test_*.py" -v
~~~

Expected: unittest 实际收集到的完整 `backend/tests` suite 全部通过；报告 collected、passed、skipped 与 failed 数量，不用历史硬编码数量代替本次输出。CLI create/verify/restore、WAL capture、migration fingerprinting、tamper detection、sidecar rejection、collision ownership 与 malicious backup ID rejection 均必须在 collected tests 中可见。

- [ ] **Step 3: Record Live file metadata without writing Live SQLite (2–5 minutes)**

Run:

~~~powershell
$livePaths = @(
  (Resolve-Path -LiteralPath 'data/app.db').Path,
  (Join-Path (Resolve-Path -LiteralPath 'data').Path 'app.db-wal'),
  (Join-Path (Resolve-Path -LiteralPath 'data').Path 'app.db-shm')
)
$before = foreach ($livePath in $livePaths) {
  if (Test-Path -LiteralPath $livePath) {
    Get-Item -LiteralPath $livePath | Select-Object FullName, Length, LastWriteTimeUtc
  } else {
    [pscustomobject]@{ FullName = $livePath; Length = $null; LastWriteTimeUtc = $null }
  }
}
$before | Format-Table -AutoSize
~~~

Expected: the command only reports existence, length, and UTC mtime. It must not checkpoint SQLite or delete sidecars.

- [ ] **Step 4: Create, independently verify, and restore-check one fresh Live snapshot (2–5 minutes per command)**

Run:

~~~powershell
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$createPayload = .\.venv\Scripts\python.exe -m backend.app.cli.database_backup create `
  --database data/app.db `
  --output-directory data/backups `
  --label pre-p0-compatibility | ConvertFrom-Json
if ($LASTEXITCODE -ne 0 -or -not $createPayload.ok) { throw 'Live backup creation failed or did not return ok=true.' }

$verifyPayload = .\.venv\Scripts\python.exe -m backend.app.cli.database_backup verify `
  --backup $createPayload.backupPath `
  --manifest $createPayload.manifestPath | ConvertFrom-Json
if ($LASTEXITCODE -ne 0 -or -not $verifyPayload.ok) { throw 'Independent backup verification failed or did not return ok=true.' }

$restorePayload = .\.venv\Scripts\python.exe -m backend.app.cli.database_backup restore-check `
  --backup $createPayload.backupPath `
  --manifest $createPayload.manifestPath `
  --output-directory data/backups/restore-checks | ConvertFrom-Json
if ($LASTEXITCODE -ne 0 -or -not $restorePayload.ok) { throw 'Isolated restore-check failed or did not return ok=true.' }

if ($createPayload.logicalSha256 -ne $verifyPayload.logicalSha256) {
  throw 'Create and verify logical SHA-256 values differ.'
}
if ($verifyPayload.logicalSha256 -ne $restorePayload.logicalSha256) {
  throw 'Verify and restore-check logical SHA-256 values differ.'
}
~~~

Expected: all three JSON payloads contain `ok=true`; create, verify, and restored copy share the same logical SHA-256 and table counts; backup and restored database have no sidecar.

- [ ] **Step 5: Prove the backup workflow did not write Live SQLite (2–5 minutes)**

Repeat Step 3 into `$after`, compare the `data/app.db` entry from `$before` and `$after`, and record any sidecar change caused by an independently running application rather than treating it as backup-owned cleanup.

Exit gate: do not start Task 1 unless the fresh snapshot ID, paths, file SHA-256, logical SHA-256, table counts, critical-content counts/hashes, and isolated restored path are recorded in the implementation report.

---

## Task 1: Create the Authoritative Legacy HTTP Contract Ledger

**Files:**

- Create: `contracts/legacy-api-v1.json`
- Create: `test/legacy-api-contract.test.js`

- [ ] **Step 1: Write the ledger-presence and schema test (2–5 minutes)**

Add a Node test named `legacy contract ledger is versioned, complete, and internally consistent`. It must load `contracts/legacy-api-v1.json` and require:

- `version` equals `legacy-api-v1`.
- Every record has `method`, `path`, `responseKind`, and `successShape`.
- JSON commands declare required request keys.
- NDJSON commands declare terminal type `result` or `done` and the terminal success keys.
- Duplicate `(method,path)` records are rejected, except paths that intentionally support both GET and POST as separate records.

- [ ] **Step 2: Run the test and confirm RED (2–5 minutes)**

Run:

~~~powershell
node --test test/legacy-api-contract.test.js
~~~

Expected: FAIL with `ENOENT` for `contracts/legacy-api-v1.json`. A syntax or Node module error is not the intended RED.

- [ ] **Step 3: Add the complete ledger with no inferred endpoints (2–5 minutes per endpoint family)**

Record exactly the React-consumed endpoints present in the current Gateway sources:

- Paper reads: `GET /api/papers`, `GET /api/paper/get`, `GET /api/note`, `GET /api/explainer`, `GET /api/translation`, `GET /pdfbytes`, `GET /api/reviews`.
- Paper writes: `POST /api/reviews/start`, `POST /api/reviews/complete`, `POST /api/note`, `POST /api/progress`, `POST /api/favorite`, `POST /api/delete`, `POST /api/paper/add`, `POST /api/paper/update`.
- Acquisition: `POST /api/expand`, `POST /api/ingest`, `POST /api/search`, `POST /api/verify-venue`, `POST /api/ingest-selected`.
- Generated content: `GET /api/title-translations`, `POST /api/title-translations`, `POST /api/explain`, `GET /api/explain-batch`, `POST /api/explain-batch`, `POST /api/translate`, `POST /api/translate-text`.
- PDFs and insights: `GET /api/scan-pdfs`, `POST /api/import-pdfs`, `POST /api/download-pdfs`, `GET /api/pdf/status`, `POST /api/recommend`, `POST /api/embed`, `POST /api/semsearch`, `GET /api/citegraph`, `POST /api/norm-venues`, `POST /api/cite-build`.
- Settings and background work: `GET /api/settings`, `POST /api/settings`, `POST /api/test-llm`, `GET /api/jobs`, `POST /api/jobs`, `GET /api/jobs/detail`, `POST /api/jobs/delete`, `POST /api/jobs/confirm`, `GET /api/schedules`, `POST /api/schedules`, `POST /api/schedules/toggle`, `POST /api/schedules/delete`.

For each endpoint, copy request and response field names from the current server/Gateway/decoder source. Preserve current snake_case wire names, boolean-compatible SQLite `0|1`, nullable detail fields, text/bytes responses, and exact NDJSON terminal families. Do not record accidental unsupported-method behavior as a supported contract.

- [ ] **Step 4: Rerun the legacy API ledger test as GREEN evidence (2–5 minutes)**

Run:

~~~powershell
node --test test/legacy-api-contract.test.js
~~~

Expected: the ledger test passes and reports the exact endpoint-record count derived from the explicit list above; no duplicate method/path pair exists.

---

## Task 2: Freeze the Existing Node Server Through a Real Black-Box Process

**Files:**

- Create: `lib/server-listen.js`
- Create: `test/support/legacy-server-process.js`
- Create: `test/fixtures/legacy-server-preload.js`
- Modify only at final listen call: `server.js`
- Extend: `test/legacy-api-contract.test.js`

- [ ] **Step 1: Write the black-box bootstrap RED test (2–5 minutes)**

Add `legacy server process boots current server.js on an OS-assigned loopback port with a temporary database`. The test must spawn `node server.js` with `DB_PATH` under a process-owned temporary directory, `HOST=127.0.0.1`, `PORT=0`, an IPC channel, disabled schedules, and the deterministic child-only preload. It waits for a bounded ready message containing the actual address, calls `GET /api/papers`, then terminates the child and asserts the temporary DB was used and `data/app.db*` metadata was never opened or changed.

- [ ] **Step 2: Run and confirm RED (2–5 minutes)**

Run: `node --test test/legacy-api-contract.test.js`

Expected: FAIL because no helper can learn the OS-assigned child port. A Live-DB open, wildcard test address, native-module error, or timeout is an invalid RED and must be fixed before continuing.

- [ ] **Step 3: Add the one permitted listen seam without moving routes (2–5 minutes)**

`lib/server-listen.js` accepts the already-created `http.Server`, existing configured port, optional host, and optional IPC sender. It calls `server.listen`, then reports `server.address()`; when host is absent it preserves the existing production bind behavior, while the contract child explicitly supplies `127.0.0.1`. In `server.js`, replace only the final `server.listen` wrapper with this call. The current async `http.createServer` request callback and every route body remain in `server.js`; the seam cannot import `db.js`, Agent, settings, filesystem routes, request parsing, response serializers, or rollout selection.

- [ ] **Step 4: Implement bounded child ownership and rerun the bootstrap test (2–5 minutes)**

The helper creates one fresh temp root per test, supplies `DB_PATH`, obtains the IPC port, requests only `http://127.0.0.1:<reported-port>`, and on every success/failure sends graceful termination followed by bounded kill and handle cleanup. The preload supplies deterministic external Agent/LLM boundary results before `server.js` loads but never imports/replaces the request listener. Rerun `node --test test/legacy-api-contract.test.js`.

Expected: the bootstrap test passes, the reported port is non-zero and loopback-only, no child/port/temp sidecar remains, and source inspection proves no route body left `server.js`.

- [ ] **Step 5: Add black-box cases for every ledger record (2–5 minutes per endpoint family)**

For each ledger method/path, issue a real HTTP request to the spawned child and assert required query/body keys, exact status/content type, complete success shape, documented error shape, and side effects in the temporary DB/filesystem only. For every NDJSON contract assert byte order, progress family, exactly one terminal, no frame after terminal, and no synthesized success after cancellation. The ledger-to-test guard fails if a current `server.js` route lacks a record/case or a record has no current route. Tests must not import the server listener, call a route function directly, or use a replacement handler object.

- [ ] **Step 6: Run black-box contracts and existing server regressions (2–5 minutes)**

Run:

~~~powershell
node --test `
  test/legacy-api-contract.test.js `
  test/title-translations-api.test.js `
  test/server-modules.test.js `
  test/react-entry-routing.test.js
~~~

Expected: all ledgered HTTP/NDJSON cases and existing regressions pass; `/workspace/`, `/legacy/`, and `UI_ENTRY` behavior is unchanged. The implementation diff contains no route-family extraction; Node compatibility Adapter structure migration is explicitly deferred to P4 and then proceeds one route family at a time under this black-box baseline.

---

## Task 3: Freeze Python Agent Behavior and Prove Disabled OCR Calls Zero Providers

**Files:**

- Create: `backend/tests/test_legacy_agent_contract.py`
- Create: `backend/tests/test_ocr_disabled_baseline.py`
- Verify only: `agent/__main__.py`
- Verify only: `agent/extract.py`
- Verify only: `agent/explain.py`
- Verify only: `agent/translate.py`

- [ ] **Step 1: Write unchanged-CLI characterization tests (2–5 minutes per behavior family)**

Tests must cover:

- `first_pages`: pymupdf4llm success, plain fallback below 100 characters, abstract prefix, and 24,000-character cap.
- `full_text`: pymupdf4llm success, plain fallback below 200 characters, abstract prefix, and caller-provided cap.
- explain: missing Paper exit code 2, empty model output exit code 3 with zero write, successful output with one legacy write, deep missing-PDF metadata fallback.
- translate: missing PDF exit code 5 with zero write, empty body exit code 3, empty result exit code 4, deterministic output ordering, and current failed-chunk marker persistence.
- stdout/stderr: markdown or JSON remains on stdout, progress remains on stderr, and no extractor progress contaminates stdout.

- [ ] **Step 2: Run and confirm the characterization fails only on an unfrozen expectation (2–5 minutes)**

Run:

~~~powershell
$env:PYTHONDONTWRITEBYTECODE = '1'
.\.venv\Scripts\python.exe -m unittest backend.tests.test_legacy_agent_contract -v
~~~

Expected RED: the new dispatch-source fingerprint and at least one exact stdout/stderr/exit/write assertion are absent from the characterization fixture. A missing installed PDF dependency is not the intended RED because tests patch current boundary symbols or use local fixtures.

- [ ] **Step 3: Freeze the current implementation without adding a production Adapter (2–5 minutes per behavior family)**

Tests invoke `python -m agent` against temporary DB/PDF fixtures for process-visible cases and patch only existing `agent.extract`, `agent.explain`, and `agent.translate` boundary symbols for deterministic provider cases. Record the reviewed SHA-256 of `agent/__main__.py` and assert its command-to-function dispatch remains byte-for-byte unchanged. Do not create a compatibility package, wrapper command, alternate dispatcher, OCR branch, retry path, new persistence, status string, or exit code.

- [ ] **Step 4: Rerun the Python legacy Agent contract tests as GREEN evidence (2–5 minutes)**

Run:

~~~powershell
$env:PYTHONDONTWRITEBYTECODE = '1'
.\.venv\Scripts\python.exe -m unittest backend.tests.test_legacy_agent_contract -v
~~~

Expected: all characterization tests pass and document the current partial-translation behavior rather than silently changing it; `git diff -- agent/__main__.py` is empty and its reviewed hash matches the fixture.

- [ ] **Step 5: Write the disabled-OCR RED test (2–5 minutes)**

Create a counting OCR provider double with separate constructor and `extract` counters. Exercise scanned/empty native extraction through the P0 compatibility path with OCR disabled. Assert both counters equal zero and no module named `paddleocr` is imported.

- [ ] **Step 6: Run and confirm RED (2–5 minutes)**

Run:

~~~powershell
$env:OCR_ENABLED = '0'
$env:PYTHONDONTWRITEBYTECODE = '1'
.\.venv\Scripts\python.exe -m unittest backend.tests.test_ocr_disabled_baseline -v
~~~

Expected: FAIL because no strict rollout/OCR provider factory exists yet. A native empty result is allowed; an OCR constructor or provider call is not.

- [ ] **Step 7: Add only the disabled provider guard (2–5 minutes)**

Make the compatibility composition accept an OCR provider factory but invoke neither the factory nor provider unless rollout configuration explicitly enables OCR. Do not install PaddleOCR or implement OCR in P0.

- [ ] **Step 8: Rerun the disabled-OCR baseline as GREEN evidence (2–5 minutes)**

Run:

~~~powershell
$env:OCR_ENABLED = '0'
$env:PYTHONDONTWRITEBYTECODE = '1'
.\.venv\Scripts\python.exe -m unittest backend.tests.test_ocr_disabled_baseline -v
~~~

Expected: tests pass with constructor count `0`, call count `0`, and no `paddleocr` import.

---

## Task 4: Freeze Complete React Gateway Coverage Without Changing UI

**Files:**

- Create: `frontend/build/legacyGatewayGuard.ts`
- Create: `frontend/build/legacyGatewayGuard.test.ts`
- Verify: `frontend/src/lib/api/paperApi.ts`
- Verify: `frontend/src/lib/api/acquisitionGateway.ts`
- Verify: `frontend/src/lib/api/artifactGateway.ts`
- Verify: `frontend/src/lib/api/pdfGateway.ts`
- Verify: `frontend/src/lib/api/insightsGateway.ts`
- Verify: `frontend/src/lib/api/jobsGateway.ts`
- Verify: `frontend/src/lib/api/schedulesGateway.ts`
- Verify: `frontend/src/lib/api/settingsGateway.ts`

- [ ] **Step 1: Write the Gateway guard RED test (2–5 minutes)**

Import `auditLegacyGateways` from `frontend/build/legacyGatewayGuard.ts`. Supply the contract ledger and current Gateway source roots. Require exact method/path coverage, request-body keys, query encoding, decoder/terminal association, and no unledgered endpoint.

- [ ] **Step 2: Run and confirm RED (2–5 minutes)**

Run:

~~~powershell
npm.cmd run test:run --prefix frontend -- build/legacyGatewayGuard.test.ts
~~~

Expected: FAIL because the guard module is missing. A frontend dependency-install error is not the intended RED.

- [ ] **Step 3: Implement the build-time guard (2–5 minutes per Gateway family)**

Use the same injected `ApiClient` pattern already present in every Gateway factory. Record calls made by every public Gateway method using representative fixed input, then compare the recorded method/path/body/contract tuple with `contracts/legacy-api-v1.json`. The guard is build/test infrastructure and must not be imported by application UI code.

- [ ] **Step 4: Rerun the React Gateway guard test as GREEN evidence (2–5 minutes)**

Run:

~~~powershell
npm.cmd run test:run --prefix frontend -- build/legacyGatewayGuard.test.ts
~~~

Expected: every ledger record consumed by React has one matching Gateway call; every Gateway call has one ledger record.

- [ ] **Step 5: Run decoder and NDJSON compatibility tests (2–5 minutes)**

Run:

~~~powershell
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
npm.cmd run test:run --prefix frontend -- `
  build/legacyGatewayGuard.test.ts `
  src/lib/api/endpoints.test.ts `
  src/lib/api/decoders.test.ts `
  src/lib/api/client.test.ts `
  src/lib/streaming/ndjson.test.ts
if ($LASTEXITCODE -ne 0) { throw 'Frontend decoder/NDJSON compatibility tests failed.' }
npm.cmd run typecheck --prefix frontend
if ($LASTEXITCODE -ne 0) { throw 'Frontend typecheck failed after compatibility tests.' }
~~~

Expected: all tests and TypeScript checks pass; no React component, route, CSS, or query-key behavior changes.

---

## Task 5: Install Immutable Rollout and Rollback Controls

**Files:**

- Create: `lib/backend-rollout.js`
- Create: `backend/app/rollout.py`
- Create: `test/backend-rollout.test.js`
- Create: `backend/tests/test_rollout_defaults.py`
- Modify: `server.js`
- Modify: `Dockerfile`
- Modify: `docker-compose.yml`

The shared environment vocabulary is:

| Variable | Accepted values | P0 default | Emergency rollback |
|---|---|---|---|
| `API_BACKEND_MODE` | `legacy`, `shadow`, `python` | `legacy` | `legacy` |
| `DOCUMENT_PIPELINE_MODE` | `legacy`, `p1` | `legacy` | `legacy` |
| `GENERATION_PIPELINE_MODE` | `legacy`, `p1` | `legacy` | `legacy` |
| `ARTIFACT_READ_MODE` | `legacy`, `prefer_new` | `legacy` | `legacy` |
| `ARTIFACT_WRITE_MODE` | `legacy`, `dual` | `legacy` | `legacy` |
| `OCR_ENABLED` | `0`, `1` | `0` | `0` |

- [ ] **Step 1: Write Node and Python parser RED tests (2–5 minutes)**

Require:

- absent variables return exactly the P0 defaults;
- whitespace, mixed case, empty strings, and unknown values are rejected rather than normalized silently;
- the settings objects are immutable after construction;
- Node and Python produce the same effective values;
- selecting an unavailable `shadow`, `python`, or `p1` adapter fails startup with a named configuration error rather than falling back silently;
- `shadow` is classified read-only and cannot execute mutations.

- [ ] **Step 2: Run and confirm RED (2–5 minutes)**

Run:

~~~powershell
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
node --test test/backend-rollout.test.js
$p0NodeRolloutRedExit = $LASTEXITCODE
if ($p0NodeRolloutRedExit -eq 0) { throw 'Node rollout RED command unexpectedly passed.' }
$env:PYTHONDONTWRITEBYTECODE = '1'
.\.venv\Scripts\python.exe -m unittest backend.tests.test_rollout_defaults -v
$p0PythonRolloutRedExit = $LASTEXITCODE
if ($p0PythonRolloutRedExit -eq 0) { throw 'Python rollout RED command unexpectedly passed.' }
~~~

Expected: both saved raw exit codes are non-zero because their rollout modules are missing; either unexpected zero stops the RED step immediately.

- [ ] **Step 3: Implement strict parsers and startup wiring (2–5 minutes per runtime)**

- Read environment variables exactly once during Node/Python composition.
- Freeze the Node settings object and expose a frozen Python dataclass or Pydantic model.
- Do not expose credentials or settings through API responses.
- Keep the existing Node and Agent paths as the only registered P0 runtime behavior; unavailable modes fail before binding a port or opening a write transaction. `agent/__main__.py` remains byte-for-byte unchanged, so Python rollout availability is enforced by the future backend composition root rather than inserted into the legacy Agent dispatcher.
- Do not let `API_BACKEND_MODE=shadow` duplicate POST, PUT, PATCH, DELETE, or NDJSON side effects.

- [ ] **Step 4: Rerun the Node and Python rollout tests as GREEN evidence (2–5 minutes)**

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
Invoke-CheckedNative 'Node rollout tests' { node --test test/backend-rollout.test.js }
$env:PYTHONDONTWRITEBYTECODE = '1'
Invoke-CheckedNative 'Python rollout tests' { .\.venv\Scripts\python.exe -m unittest backend.tests.test_rollout_defaults -v }
~~~

Expected: defaults, invalid-value failures, immutability, cross-runtime parity, and unavailable-adapter failures all pass.

- [ ] **Step 5: Add container contract assertions (2–5 minutes)**

Extend the existing Docker contract tests to require the six variables above with legacy/off defaults and Compose pass-through. No secret value belongs in Dockerfile or Compose.

- [ ] **Step 6: Run container/static contract tests as GREEN evidence (2–5 minutes)**

Run:

~~~powershell
node --test test/docker-react-build.test.js test/backend-rollout.test.js
~~~

Expected: the existing React build/`UI_ENTRY` assertions and the new backend rollback defaults all pass.

---

## Task 6: Exercise Runtime Rollback and Close P0

**Files:**

- Modify: `docs/DATABASE.md`
- Verify: all P0 production and test files

- [ ] **Step 1: Document the exact emergency rollback environment (2–5 minutes)**

Record this complete startup configuration:

~~~powershell
$env:API_BACKEND_MODE = 'legacy'
$env:DOCUMENT_PIPELINE_MODE = 'legacy'
$env:GENERATION_PIPELINE_MODE = 'legacy'
$env:ARTIFACT_READ_MODE = 'legacy'
$env:ARTIFACT_WRITE_MODE = 'legacy'
$env:OCR_ENABLED = '0'
$env:UI_ENTRY = 'react'
~~~

State that all values are startup-only and require a process/container restart. `UI_ENTRY=legacy` remains an independent UI-root rollback and is not required for backend rollback.

- [ ] **Step 2: Run the complete compatibility suite (2–5 minutes per command)**

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
Invoke-CheckedNative 'backend tests' { .\.venv\Scripts\python.exe -m unittest discover -s backend/tests -p "test_*.py" -v }
Invoke-CheckedNative 'legacy Python tests' { .\.venv\Scripts\python.exe -m unittest discover -s test -p "test_*.py" -v }
Invoke-CheckedNative 'MCP characterization' { .\.venv\Scripts\python.exe -B -m unittest discover -s test -p "test_mcp_server.py" -v }
Invoke-CheckedNative 'Node compatibility tests' { node --test test/legacy-api-contract.test.js test/backend-rollout.test.js test/title-translations-api.test.js }
Invoke-CheckedNative 'root Node tests' { npm.cmd test }
$baselineVerifyJson = Invoke-CheckedNative 'full frontend baseline verification' { node scripts/pre-existing-failure-baseline.mjs verify --baseline contracts/pre-existing-test-failures-v1.json }
$baselineVerify = $baselineVerifyJson | ConvertFrom-Json
$baselineRequiredFields = @('baselineMatched','observedSuiteExitCode','overallGreen')
foreach ($baselineField in $baselineRequiredFields) {
  if (-not ($baselineVerify.PSObject.Properties.Name -contains $baselineField)) { throw "P0 exit baseline verifier omitted required field $baselineField." }
}
if ($baselineVerify.baselineMatched -isnot [bool] -or $baselineVerify.baselineMatched -ne $true) { throw 'P0 exit baseline verifier did not report boolean baselineMatched=true.' }
if ($baselineVerify.observedSuiteExitCode -isnot [int] -and $baselineVerify.observedSuiteExitCode -isnot [long]) { throw 'P0 exit baseline verifier did not report an integer observedSuiteExitCode.' }
if ($baselineVerify.overallGreen -isnot [bool]) { throw 'P0 exit baseline verifier did not report boolean overallGreen.' }
$baselineObservedSuiteExitCode = [long]$baselineVerify.observedSuiteExitCode
if (($baselineObservedSuiteExitCode -eq 0) -ne $baselineVerify.overallGreen) { throw 'P0 exit baseline verifier reported inconsistent observedSuiteExitCode and overallGreen.' }
Invoke-CheckedNative 'frontend typecheck' { npm.cmd run typecheck --prefix frontend }
Invoke-CheckedNative 'frontend lint' { npm.cmd run lint --prefix frontend }
Invoke-CheckedNative 'frontend build' { npm.cmd run build --prefix frontend }
Invoke-CheckedNative 'frontend e2e' { npm.cmd run e2e --prefix frontend }
Invoke-CheckedNative 'git diff check' { git diff --check }
~~~

Expected: backend, legacy Python, MCP, Node, typecheck, lint, build, E2E, and diff check must return 0. The complete frontend Vitest command must be reported with its raw exit code. Exit 0 requires an empty v1 baseline; a stable reviewed non-zero result may pass only `scripts/pre-existing-failure-baseline.mjs verify` with exact IDs/signatures/related hashes and no touched related path, must be labelled `overallGreen=false`, and may authorize progression through P1–P5 only. Any mismatch stops. P6 final completion requires raw frontend exit code 0.

- [ ] **Step 3: Exercise the default/rollback process state (2–5 minutes)**

Start the application with the Step 1 values and a temporary contract database. Verify:

- Node serves every ledgered legacy endpoint.
- Python commands still use the unchanged `agent/__main__.py` dispatch.
- artifact reads and writes use only legacy fields.
- OCR constructor and call counts remain zero.
- no P1 table is required for startup.

- [ ] **Step 4: Verify file scope (2–5 minutes)**

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
Invoke-CheckedNative 'P0 changed-file listing' { git diff --name-only }
Invoke-CheckedNative 'P0 workspace status' { git status --short }
~~~

Expected: no change appears under `public/`, React component/style/route directories, `AGENTS.md`, `.agents/`, or Live `data/app.db*`. Existing user-owned changes remain untouched.

---

## P0 Exit Gate

P1 may begin only when all statements are true:

- The backup suite is fully green and a fresh Live create/verify/restore-check has matching logical SHA-256 and counts.
- `contracts/pre-existing-test-failures-v1.json` came from two identical fresh full-suite captures. A non-zero baseline retains and reports its raw non-zero exit code; progression is allowed only while the full ID/signature/related-hash set is identical and the current slice did not modify any related path. Any drift stops immediately, and this exception cannot satisfy the P6 final zero-failure gate.
- The complete legacy API ledger exists and a child-process black-box test exercises every ledger record against current `server.js` on a temporary DB and OS-assigned loopback port.
- The only Node structural seam is `lib/server-listen.js`; every route body remains in `server.js`, and route-family compatibility Adapter migration is deferred to P4.
- Python extraction, explain, and translate behavior is characterized with fixed stdout/stderr/exit/write behavior; `agent/__main__.py` is unchanged and no production compatibility Adapter exists.
- MCP `tools/list` is exactly nine and the current success/empty/error/read-only behavior is frozen by `test/test_mcp_server.py`; no MCP query creates a task, invokes OCR, or writes SQLite.
- React Gateway coverage exactly matches the ledger.
- `OCR_ENABLED=0` proves zero provider construction and zero provider calls.
- All rollout settings default to legacy/off, reject invalid values, and require restart.
- Emergency rollback to legacy requires no schema downgrade and no UI modification.
- No commit, branch, staging, push, Live database mutation, or user-file cleanup occurred during implementation.

## Self-Review Checklist

- [ ] Every production edit has a named RED test, an explicit confirmation of the intended failure, a smallest implementation step, and the identical GREEN command.
- [ ] All paths, symbols, commands, status expectations, and rollback values are explicit.
- [ ] The plan contains no incomplete implementation marker or omitted endpoint family.
- [ ] P0 does not install or invoke OCR and does not introduce SQLAlchemy/Alembic schema changes.
- [ ] P0 does not change UI behavior.
- [ ] P0 contains no Git commit or push step.
