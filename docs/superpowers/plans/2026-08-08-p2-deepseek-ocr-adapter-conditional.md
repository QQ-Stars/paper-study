# P2 DeepSeek OCR Adapter 条件实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans. This plan is default-gated. Do not execute implementation tasks until Task 0 produces a complete verified provider-contract manifest. Every behavior follows RED → minimal implementation → identical GREEN command.

**Goal:** 仅在本地保存的官方 DeepSeek OCR 协议资料完整且可验证时，以 fixture-driven TDD 实现 `DeepSeekOcrApiAdapter`；资料不完整时稳定返回 `OCR_PROVIDER_CONTRACT_UNVERIFIED`/HTTP 503，并证明 transport construction、网络、数据库写入与文件上传均为 0。

**Architecture:** `OcrProviderContractLoader` 把 `docs/provider-contracts/deepseek-ocr/` 的官方资料、脱敏 fixture 与 SHA-256 清单验证成 immutable `VerifiedDeepSeekOcrContract`。Provider registry 必须先验证 contract，再创建 `httpx.AsyncClient` 或 Adapter。`DeepSeekOcrApiAdapter` 只解释 verified contract，不内置猜测的 base URL、endpoint、method、auth、model、上传字段、响应字段、轮询或限额。P2 ProcessingQueue/Worker 继续拥有 retry、cancel、lease 与 page checkpoint；Adapter 只做该 contract 明确规定的 transport/protocol 转换。

**Tech Stack:** Python 3、httpx.AsyncClient/MockTransport、Pydantic v2、hashlib、unittest.IsolatedAsyncioTestCase、P2 ProcessingQueue 与 ocr_page_checkpoints、Alembic fixed revision `20260807_02`。

**Execution status:** 默认 `BLOCKED_BY_PROVIDER_CONTRACT`。P2 Fake OCR 纵向切片可独立完成并进入 P3。若用户在项目完成前提供了下述真实官方资料，则本条件计划变为必执行 gate；未提供时项目报告必须明确“真实 DeepSeek OCR Adapter blocked/未交付”，不能声称真实 DeepSeek 能力完成。

---

## Hard gate：资料目录必须完整且逐字节可验证

`docs/provider-contracts/deepseek-ocr/contract-manifest-v1.json` 必须以 repo-relative path + lowercase SHA-256 列出所有资料，并包含：

- 官方文档 URL、文档版本或抓取日期、每份保存资料的 content SHA-256；
- 脱敏 request fixture，以及 success、429、其他 4xx、5xx response fixtures；
- auth scheme/header/token prefix 与 credential placement；不得含完整 key；
- model identifier rules、base URL、endpoint path、HTTP method、content type；
- PDF 与 image 的允许编码/上传方式，支持的 mime/type，以及 byte size、page count、pixel dimensions/resolution 限制；
- 同步或异步语义；若异步，须包含 submit response、job/request ID、poll endpoint/method/auth、terminal/pending/failure/cancel states；若同步，manifest 明确 `mode=sync` 且不得发明 poll；
- `Retry-After` 的 seconds/date 支持、请求频率、并发/配额/免费额度、数据留存与删除规则；
- Markdown/text 位于响应中的精确 JSON path、页序与空结果语义；
- 官方 timeout/retry/cancel guidance；没有官方值时 manifest 标记“unspecified”，实现使用本计划有界客户端安全默认值但不得称为供应商保证。

任何字段、fixture、文件或 hash 缺失/不一致都使 gate 失败。普通 DeepSeek chat-completions 文档、代码中已有 LLM base/model、用户 PDF、完整 API key、博客推断与抓包猜测均不能补足 gate。

## 文件职责

- Create: `backend/app/providers/ocr/deepseek_contract.py` — manifest schema、hash/fixture验证与 immutable verified contract。
- Modify: `backend/app/providers/ocr/registry.py` — contract-first registry；验证前不得构造 transport。
- Create: `backend/app/providers/ocr/deepseek_api.py` — verified protocol Adapter。
- Create: `backend/tests/test_deepseek_ocr_contract_gate.py` — 缺资料/篡改/零 transport/503。
- Create: `backend/tests/test_deepseek_ocr_api_contract.py` — MockTransport fixture contract tests。
- Modify: `backend/tests/test_ocr_provider_gate.py` — P2 default fail-closed 与 verified override。
- Modify: `backend/app/workers/ocr.py` — 只接入 Adapter 返回的 normalized page result/error/retry metadata；不复制 HTTP 协议。
- Verify only: `backend/migrations/versions/20260807_02_processing_queue_ocr.py` — 本计划不新增 revision/schema。

## Task 0：验证 provider contract，未通过即零网络停止

- [ ] **RED：写 complete/missing/tampered gate tests**

新增 `test_contract_requires_every_official_metadata_and_limit_field`、`test_contract_requires_redacted_success_429_4xx_5xx_fixtures`、`test_contract_rejects_file_hash_or_json_path_drift` 与 `test_unverified_registry_returns_503_before_transport_construction`。fixture matrix 每次删除一个必填字段/文件并断言 exact missing path；transport/client factory、PDF reader、DB UoW spies 都为 0。

- [ ] **运行 RED**

Run: `.\.venv\Scripts\python.exe -B -m unittest backend.tests.test_deepseek_ocr_contract_gate -v`

Expected RED: loader/manifest validator 不存在；不得出现 DNS、HTTP、真实 credential 或用户文件访问。

- [ ] **最小实现**

实现 strict Pydantic schema、repo-root containment、regular-file/no-symlink 检查、逐字节 SHA-256、fixture JSON parse 与 secret scanner。Registry 顺序固定为 feature enabled → provider name → contract verify → credential lookup → transport construction；contract 失败在 credential/transport/PDF/DB 前返回 safe `OCR_PROVIDER_CONTRACT_UNVERIFIED`，FastAPI 映射 503。

- [ ] **相同 GREEN 命令**

Run: `.\.venv\Scripts\python.exe -B -m unittest backend.tests.test_deepseek_ocr_contract_gate -v`

Expected GREEN: 所有缺失/篡改 case 分类通过，零 transport construction、零 network、零 PDF read、零 DB write。

**Gate:** 只有 manifest 与全部保存资料通过 validator，Task 1–4 才能执行；否则在此停止并报告 blocked。

## Task 1：逐行为实现 auth、request、upload-or-render 与 Markdown response

- [ ] **RED：按 verified fixtures 写 exact request/response tests**

新增 `test_auth_model_base_endpoint_method_and_headers_match_verified_contract`、`test_pdf_or_image_encoding_and_page_limits_match_verified_contract`、`test_success_response_extracts_ordered_markdown_from_verified_json_path`、`test_empty_or_malformed_success_maps_to_safe_typed_error`。测试仅使用仓库内最小合成 PDF/image bytes 与脱敏 key sentinel；不使用用户 PDF、完整 key或真实网络。

- [ ] **运行 RED**

Run: `.\.venv\Scripts\python.exe -B -m unittest backend.tests.test_deepseek_ocr_api_contract.DeepSeekOcrApiContractTests.test_auth_model_base_endpoint_method_and_headers_match_verified_contract backend.tests.test_deepseek_ocr_api_contract.DeepSeekOcrApiContractTests.test_pdf_or_image_encoding_and_page_limits_match_verified_contract backend.tests.test_deepseek_ocr_api_contract.DeepSeekOcrApiContractTests.test_success_response_extracts_ordered_markdown_from_verified_json_path backend.tests.test_deepseek_ocr_api_contract.DeepSeekOcrApiContractTests.test_empty_or_malformed_success_maps_to_safe_typed_error -v`

Expected RED: Adapter 不存在。

- [ ] **最小实现与相同 GREEN**

Adapter 从 verified contract 读取所有 wire 值；若协议要求 PDF upload 就按 fixture 构造 multipart/body，若要求 page images 就按 verified format/dimensions/batch limit deterministic render，绝不同时实现猜测分支。响应只从 verified JSON path 提取并按页序组合 Markdown。运行与 RED 完全相同命令，Expected GREEN 为四项 OK，MockTransport 捕获 request 与脱敏 fixture 全等。

## Task 2：实现 error mapping、httpx timeout 与 429 有界重试

- [ ] **RED：写 429/4xx/5xx/timeout/network matrix**

新增 `test_429_honors_verified_retry_after_seconds_or_date`、`test_429_missing_or_invalid_retry_after_uses_bounded_exponential_policy`、`test_retry_is_capped_by_attempts_elapsed_time_and_cancel_event`、`test_4xx_5xx_timeout_and_network_errors_map_without_body_or_secret_leak`。Clock/sleep/cancel 注入；断言最大 attempts、最大 elapsed、delay clamp 与是否 retry 均来自 contract 或明确客户端安全上限。

- [ ] **运行 RED、最小实现、相同 GREEN**

Run: `.\.venv\Scripts\python.exe -B -m unittest backend.tests.test_deepseek_ocr_api_contract.DeepSeekOcrApiContractTests.test_429_honors_verified_retry_after_seconds_or_date backend.tests.test_deepseek_ocr_api_contract.DeepSeekOcrApiContractTests.test_429_missing_or_invalid_retry_after_uses_bounded_exponential_policy backend.tests.test_deepseek_ocr_api_contract.DeepSeekOcrApiContractTests.test_retry_is_capped_by_attempts_elapsed_time_and_cancel_event backend.tests.test_deepseek_ocr_api_contract.DeepSeekOcrApiContractTests.test_4xx_5xx_timeout_and_network_errors_map_without_body_or_secret_leak -v`

Expected RED: retry/error policy 缺失。最小实现使用 explicit httpx connect/read/write/pool timeouts、解析 verified Retry-After forms、有界 exponential backoff，并在每次 sleep/request 前检查 cancel。相同命令 GREEN 时四项 OK；日志/异常无 Authorization、PDF bytes、Markdown 或 raw response body。

## Task 3：实现 page batches、checkpoint recovery 与 cancel

- [ ] **RED：写多批、部分成功、重启恢复与 drift tests**

新增 `test_page_batches_follow_verified_limits_and_preserve_order`、`test_completed_pages_resume_without_duplicate_request`、`test_failed_page_batch_keeps_recoverable_checkpoints`、`test_pdf_or_contract_hash_drift_invalidates_resume`、`test_cancel_stops_poll_or_next_batch_and_never_marks_ready`。

- [ ] **运行 RED、最小实现、相同 GREEN**

Run: `.\.venv\Scripts\python.exe -B -m unittest backend.tests.test_deepseek_ocr_api_contract.DeepSeekOcrApiContractTests.test_page_batches_follow_verified_limits_and_preserve_order backend.tests.test_deepseek_ocr_api_contract.DeepSeekOcrApiContractTests.test_completed_pages_resume_without_duplicate_request backend.tests.test_deepseek_ocr_api_contract.DeepSeekOcrApiContractTests.test_failed_page_batch_keeps_recoverable_checkpoints backend.tests.test_deepseek_ocr_api_contract.DeepSeekOcrApiContractTests.test_pdf_or_contract_hash_drift_invalidates_resume backend.tests.test_deepseek_ocr_api_contract.DeepSeekOcrApiContractTests.test_cancel_stops_poll_or_next_batch_and_never_marks_ready -v`

Expected RED: batch/checkpoint integration 不存在。最小实现让 Worker 按 verified page/batch limits 调 Adapter，成功页 transactionally 写 P2 checkpoint，resume 以 PDF hash + contract hash + page range fencing，cancel 在下一 request/poll 前生效。相同命令 GREEN 时五项 OK，失败不跨 mode 回退 native。

## Task 4：只按 verified sync/async 协议实现 polling

- [ ] **RED：写 manifest-selected protocol tests**

若 verified manifest 为 async，测试 submit ID、poll method/path/auth、pending cadence、terminal success/failure/cancel、missing ID、poll timeout 与 quota；若为 sync，测试 `test_sync_contract_constructs_zero_poll_requests`。测试名称与分支由 checked-in manifest mode 固定，不能运行两套互斥猜测协议。

- [ ] **运行 RED、最小实现、相同 GREEN**

Run: `.\.venv\Scripts\python.exe -B -m unittest backend.tests.test_deepseek_ocr_api_contract -v`

Expected RED: verified mode 尚未完整实现。最小实现只加入 manifest 指定的 sync 或 async path；async poll 同样受 cancel、Retry-After、frequency/quota、attempt/elapsed bounds 控制。相同命令 GREEN 时全部 fixture contract tests OK，MockTransport 收到的每个 request 都在官方资料中有对应 fixture。

## Task 5：固定 revision 集成与条件出口

- [ ] **验证 schema revision 前后唯一且不迁移**

从 fresh verified P2 backup 的 `restore-check` JSON 获取本次隔离数据库；路径必须位于当前 `restore-validation-*` 目录且不等于 Live。只在 try/finally 内设置 `DB_PATH`，在完整条件套件前后分别执行 exact-one-line `alembic current`。本计划不运行 upgrade、不创建 migration、不调用 create_all：

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
$deepseekCreate = Invoke-CheckedNative 'DeepSeek contract backup create' { .\.venv\Scripts\python.exe -B -m backend.app.cli.database_backup create --database data/app.db --output-directory data/backups --label pre-deepseek-contract } | ConvertFrom-Json
if (-not $deepseekCreate.ok) { throw 'DeepSeek contract backup create failed.' }
$deepseekVerify = Invoke-CheckedNative 'DeepSeek contract backup verify' { .\.venv\Scripts\python.exe -B -m backend.app.cli.database_backup verify --backup $deepseekCreate.backupPath --manifest $deepseekCreate.manifestPath } | ConvertFrom-Json
if (-not $deepseekVerify.ok -or $deepseekVerify.logicalSha256 -ne $deepseekCreate.logicalSha256) { throw 'DeepSeek contract backup verification failed.' }
$deepseekRestore = Invoke-CheckedNative 'DeepSeek contract restore-check' { .\.venv\Scripts\python.exe -B -m backend.app.cli.database_backup restore-check --backup $deepseekCreate.backupPath --manifest $deepseekCreate.manifestPath --output-directory data/backups/restore-checks } | ConvertFrom-Json
if (-not $deepseekRestore.ok -or $deepseekRestore.logicalSha256 -ne $deepseekVerify.logicalSha256) { throw 'DeepSeek contract restore-check failed.' }

$deepseekDb = (Resolve-Path -LiteralPath $deepseekRestore.restoredPath).Path
$deepseekLiveDb = (Resolve-Path -LiteralPath 'data/app.db').Path
$deepseekRestoreRoot = (Resolve-Path -LiteralPath 'data/backups/restore-checks').Path.TrimEnd([IO.Path]::DirectorySeparatorChar, [IO.Path]::AltDirectorySeparatorChar)
$deepseekRestorePrefix = $deepseekRestoreRoot + [IO.Path]::DirectorySeparatorChar
$deepseekValidationDir = (Resolve-Path -LiteralPath (Split-Path -Parent $deepseekDb)).Path
if ($deepseekDb -eq $deepseekLiveDb -or -not $deepseekDb.StartsWith($deepseekRestorePrefix, [StringComparison]::OrdinalIgnoreCase) -or -not (Split-Path -Leaf $deepseekValidationDir).StartsWith('restore-validation-', [StringComparison]::Ordinal)) { throw 'DeepSeek contract DB is not the current isolated restore.' }

$deepseekPreviousDbPath = [Environment]::GetEnvironmentVariable('DB_PATH', 'Process')
$deepseekHadDbPath = $null -ne $deepseekPreviousDbPath
$env:DB_PATH = $deepseekDb
try {
  $deepseekBeforeCurrentRaw = @(& .\.venv\Scripts\python.exe -B -m alembic -c backend/alembic.ini current)
  $deepseekBeforeCurrentExit = $LASTEXITCODE
  if ($deepseekBeforeCurrentExit -ne 0) { throw 'DeepSeek contract DB revision inspection failed before tests.' }
  $deepseekBeforeCurrent = @($deepseekBeforeCurrentRaw | Where-Object { -not [string]::IsNullOrWhiteSpace([string]$_) })
  if ($deepseekBeforeCurrent.Count -ne 1 -or [string]$deepseekBeforeCurrent[0] -notmatch '^20260807_02\s+\(head\)$') { throw 'DeepSeek contract DB must be uniquely at 20260807_02 (head) before tests.' }

  .\.venv\Scripts\python.exe -B -m unittest backend.tests.test_deepseek_ocr_contract_gate -v
  if ($LASTEXITCODE -ne 0) { throw 'DeepSeek contract gate suite failed.' }
  .\.venv\Scripts\python.exe -B -m unittest backend.tests.test_deepseek_ocr_api_contract -v
  if ($LASTEXITCODE -ne 0) { throw 'DeepSeek API contract suite failed.' }
  .\.venv\Scripts\python.exe -B -m unittest backend.tests.test_ocr_provider_gate backend.tests.test_processing_worker backend.tests.test_processing_jobs_api -v
  if ($LASTEXITCODE -ne 0) { throw 'DeepSeek integration contract suite failed.' }

  $deepseekAfterCurrentRaw = @(& .\.venv\Scripts\python.exe -B -m alembic -c backend/alembic.ini current)
  $deepseekAfterCurrentExit = $LASTEXITCODE
  if ($deepseekAfterCurrentExit -ne 0) { throw 'DeepSeek contract DB revision inspection failed after tests.' }
  $deepseekAfterCurrent = @($deepseekAfterCurrentRaw | Where-Object { -not [string]::IsNullOrWhiteSpace([string]$_) })
  if ($deepseekAfterCurrent.Count -ne 1 -or [string]$deepseekAfterCurrent[0] -notmatch '^20260807_02\s+\(head\)$') { throw 'DeepSeek contract tests changed the fixed P2 revision.' }
} finally {
  if ($deepseekHadDbPath) { $env:DB_PATH = $deepseekPreviousDbPath } else { Remove-Item Env:DB_PATH -ErrorAction SilentlyContinue }
}
~~~

Expected: 前后 `alembic current` 都唯一为 `20260807_02 (head)`；contract gate、request/response、auth、encoding/limits、Markdown、errors/timeouts、429 Retry-After/bounded retry、page batch/recovery/cancel 与 verified sync/async protocol 全部 OK；所有 tests 使用 fixture/MockTransport，真实 network/user PDF/full key 调用为 0，调用者原有 `DB_PATH` 精确恢复。

## 条件完成标准

- Gate 未满足：P2 Fake slice 可完成；registry 对 DeepSeek 保持 `OCR_PROVIDER_CONTRACT_UNVERIFIED`/503/零 transport，真实 Adapter 状态明确为 blocked，不能声称完成。
- Gate 满足：Task 0–5 全部执行并保存资料 hash、fixture evidence、test count、fixed `20260807_02` current 与零真实网络证据后，才可声称当前 verified contract 对应的 DeepSeek OCR Adapter 完成。
- 后续官方协议、endpoint、model、limit、retention 或 fixture hash 变化会使现有 contract hash 失效并重新关闭 registry；必须新建版本化 manifest 与重新运行本计划，禁止静默兼容。
