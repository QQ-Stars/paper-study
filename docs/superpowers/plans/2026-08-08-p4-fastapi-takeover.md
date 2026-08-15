# P4 FastAPI 逐路由接管实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Every production change uses superpowers:test-driven-development, and every completion claim uses superpowers:verification-before-completion. Steps use checkbox syntax and are sized for 2–5 minutes.

**Goal:** 在不改变现有 React、legacy UI、Paper ID、旧 /api wire contract、NDJSON 事件协议、PDF URL、静态入口或 Live Node ownership 的前提下，在隔离数据库与随机 loopback 端口交付 FastAPI HTTP/Worker/Scheduler candidate、完整 parity evidence、每进程单角色与 Worker/Scheduler role-scoped ownership、drain 与 rollback rehearsal；正式生产接管只允许由 P6 shutdown gate 授权。

**Architecture:** backend/app/api 只负责请求解析、application interface 调用、wire adapter 与响应序列化。P4 直接挂载 P2 的 `document_processing` 和 P3 的 `document_consumers`/`document_search` routers，绝不复制它们的 DTO、enqueue、状态转换或重试实现。PaperLibrary、LibraryQueries、Settings、ReviewScheduler、SearchCoordinator 与 ArtifactStore 隐藏其余业务规则；repositories 隐藏 SQLite；providers 隐藏文件、子进程与外部服务；workers 消费 P2/P3 的 ProcessingJob。P4 candidate 只连接 P0 restore copy 或专用临时 DB、只监听隔离 loopback 端口，并在独立 runtime namespace 演练角色；Node 在 P4 前后始终是 Live HTTP/Worker/Scheduler production owner。P4 不停止 Live Node、不让任何 Python production role 连接 Live DB、不执行 promotion；正式 Node shutdown、Python production startup 与失败回滚只在 P6 shutdown gate。

**Tech Stack:** Python 3、FastAPI、Pydantic v2、SQLAlchemy 2、Alembic、SQLite WAL、httpx、anyio、unittest、Node test runner、React、Vitest、Playwright。

**Prerequisite gate:** P0 的 verified backup/restore rehearsal 与 fixed-path `OriginReceipt` seal/verify、P1 repositories/UoW、P2 ProcessingJob/Worker、P3 SourceDocument/GeneratedArtifact/document_chunks/search 全绿，且 Alembic 唯一 head 为 P3 revision 20260807_03 后才开始。P4 依赖 document_sources、generated_artifacts、processing_jobs、document_chunks、obsidian_exports；其中 `processing_jobs.spec_json`、`processingJobs`/`processingJobSpecs` fingerprints、`processing_jobs_spec_guard_insert|processing_jobs_spec_guard_update` 由 P2 revision `20260807_02` 冻结，P3 revision `20260807_03` 再增加 `document_chunks_fts_ai|document_chunks_fts_ad|document_chunks_fts_au`，因此 P4 的固定 schema 必须恰有五个 exact triggers。P4 不得重建这些表、列、trigger 或复制其状态机。

**Scope guardrails:** 不删除 server.js、db.js、旧表或旧字段；不改 React 布局/CSS/路由；不让 FastAPI import 执行 DDL、backfill、provider 调用、Worker 或 Scheduler；不让 legacy 与 Python scheduler 同时拥有同一 runtime namespace 的调度权；不把 ingest_jobs 与 processing_jobs 合并；不创建第二份 v2 schema、router、ProcessingQueue facade、error mapper 或 job 状态机。所有 P4 Python role 证据必须标记 `environment=candidate`、隔离 DB identity 与随机 loopback port；任何 Live Python role 或 Live Node stop action 都是越界并立即失败。P4 唯一允许的 Live runtime metadata 写入是 Task 9 经 verified lineage origin 和平台进程/文件证明后依次 exclusive-create typed Live database identity 与 `node_active` owner marker；它不停止、重启、切换或接管 Node，也不打开可写 Live SQLite 连接。任何新增生产行为或候选部署配置 mutation 都必须在同一自包含切片中依次出现“命名行为测试 → 明确命令 RED 与缺失行为 → 最小实现/mutation → 完全相同 test target GREEN”；环境、fixture、import 或解析错误不是有效 RED，任何 GREEN 不得位于对应生产 mutation 之前。

---

## 文件职责

- requirements.in/requirements.txt：沿用 P1 hash-locked dependency set，并明确锁定 FastAPI==0.116.1、Uvicorn==0.49.0、httpx==0.28.1、anyio==4.13.0；不得以兼容范围或未重编译的 transitive version 运行 P4。
- backend/app/api/app.py：FastAPI application factory、lifespan 与 router 装配；import 无副作用。
- backend/app/api/dependencies.py：UoW、repositories、application services 和 request-scoped dependency wiring。
- backend/app/api/errors.py：P1 创建的唯一领域错误 seam；P4 只追加 legacy/v2 HTTP 映射，不创建同名目录。
- backend/app/api/middleware/ndjson.py：唯一 NDJSON encoder、flush、终止事件和断开取消规则。
- backend/app/api/routes/legacy.py：旧 /api、/pdfbytes、/papers compatibility adapters；不含 SQL、文件写入或 provider 细节。
- backend/app/api/router.py：组合 P2/P3 已有 routers 与 P4 legacy/static routers；P5 后续再挂载四条明确 Obsidian 路由。
- backend/app/api/routes/document_processing.py：P2 已交付的 sources、explainer 与 read/action-only jobs 路由；P4 只挂载和做 parity，不重写。
- backend/app/api/routes/document_consumers.py：P3 已交付的 translation/classification/metadata/summary 与 index 路由；P4 只挂载和做 parity，不重写。
- backend/app/api/routes/document_search.py：P3 已交付的 chunk search 路由；P4 只挂载和做 parity，不重写。
- backend/app/api/static/frontend_assets.py：/workspace/、/legacy/、静态文件与 SPA fallback 的安全适配。
- backend/app/application/paper_library.py：Paper 写操作、旧字段双写和关联删除策略。
- backend/app/application/library_queries.py：HTTP、MCP、Obsidian 共用只读 read model。
- backend/app/application/settings.py：组合 P1 CredentialStore 与 LLM/OCR/Embedding/Semantic Scholar ProviderProfile，负责设置读取、脱敏、验证、目录准备和原子写。
- backend/app/application/review_scheduler.py：复用既有 review plan 规则。
- backend/app/application/legacy_ingest.py：只封装 legacy ingest_jobs/schedules；P2 ProcessingQueue 与其 application use cases 由既有 v2 routers 直接复用。
- backend/app/application/search_coordinator.py：search、recommend、semantic search、citation 查询协调。
- backend/app/application/artifact_store.py：SourceDocument/GeneratedArtifact 查询、当前版本选择和旧字段回退。
- backend/app/providers/legacy_agent.py：迁移期对子进程 agent 命令的窄接口；Route 不直接 spawn。
- backend/app/providers/pdf_files.py：PDF 定位、realpath containment、状态与安全读取。
- backend/app/providers/runtime_lease.py：不改 schema 的跨进程 owner lock、heartbeat、stale-owner 校验与显式释放。
- backend/app/api/compat/__init__.py：P4 首次建立、P6 继续扩展的 compatibility package；不得再建第二个 identity package。
- backend/app/api/compat/database_identity.py：唯一 `DatabaseEvidenceIdentityManifest` v1 计算/验证；强制消费 P0 `OriginReceipt` exact path/file SHA，分离 lineage 与具体 subject，P4/P6/runtime lease 共用。
- backend/app/api/compat/schema_inventory.py：P4/P5 固定对象 inventory fingerprint 与 strict before/after compare；显式冻结含 `spec_json` 的 `processing_jobs` ordered columns/SQL hash、`processingJobs`/`processingJobSpecs` count/hash/strict decode，以及两个 spec guards + 三个 FTS guards 共五个 exact trigger；不替代 P6 row-level canonical data fingerprint。
- backend/app/cli/runtime_owner.py：`create-live-database-identity|verify-live-database-identity|create-descendant-database-identity|initialize-node-owner|verify-node-owner|candidate-rollback-smoke` 六个 fail-closed 命令；两个 `verify-*` 命令严格只读，其余命令的副作用受各自 Interface 限定；全部只输出 JSON。
- backend/app/cli/schema_inventory.py：对显式 DB/DatabaseEvidenceIdentityManifest capture/compare 固定 inventory；只输出 JSON。
- backend/app/api/middleware/local_access.py：默认 loopback bind 与 Host/Origin fail-closed 本地访问策略；不信任转发头。
- backend/app/workers/scheduler.py：带显式所有权和 lease 的 Python scheduler。
- backend/app/runtime.py：HTTP、Worker、Scheduler 启用开关的 fail-fast 解析。
- backend/tests/fixtures/http/legacy_route_inventory.json：旧方法、路径、content type、wire shape 与 terminal event 清单。
- backend/tests/support/node_contract_server.py：隔离端口启动/停止冻结 Node 基线。
- backend/tests/test_http_contract_inventory.py：server.js route inventory 完整性。
- backend/tests/test_api_health.py：factory、health、readiness 与 import side-effect。
- backend/tests/test_api_legacy_json.py：旧 JSON/Markdown API compatibility。
- backend/tests/test_api_v2.py：typed v2 paper/source/artifact/job contracts。
- backend/tests/test_api_ndjson.py：NDJSON 字节级与取消语义。
- backend/tests/test_api_pdf_static.py：PDF、workspace、legacy、static 安全行为。
- backend/tests/test_runtime_ownership.py：每进程单角色、API/Worker/Scheduler 并存、Worker/Scheduler role-scoped singleton ownership 和 drain。
- frontend/e2e/fastapi-parity.spec.ts：React workspace 与 legacy UI 使用真实 FastAPI 的关键流程。
- Dockerfile：FastAPI candidate command 与冻结 Node rollback candidate stage；不改变当前 Live Node entrypoint。
- docker-compose.yml：默认仍由 Node 拥有 Live；FastAPI api、worker、scheduler 仅位于显式 candidate profile，使用隔离 DB/port/runtime namespace。
- docs/DATABASE.md：P4 candidate migration、ownership/drain/rollback rehearsal；正式 cutover 明确链接 P6 shutdown gate。

## 固定 legacy 路由清单

以下方法/路径必须继续存在，未列出的 /api 与 /api/* 返回 404 文本 API not found：

| 类别 | 方法与路径 |
|---|---|
| Paper/read model | GET /api/papers；GET /api/paper/get；POST /api/paper/add；POST /api/paper/update；POST /api/progress；POST /api/favorite；POST /api/delete |
| Artifact/review | GET/POST /api/note；GET /api/explainer；GET /api/translation；GET /api/reviews；POST /api/reviews/start；POST /api/reviews/complete |
| Discovery JSON | POST /api/ingest；POST /api/expand；POST /api/translate-text；GET /api/scan-pdfs；GET /api/pdf/status；GET /api/citegraph；POST /api/test-llm |
| Jobs/schedules JSON | GET/POST /api/jobs；GET /api/jobs/detail；POST /api/jobs/delete；GET/POST /api/schedules；POST /api/schedules/toggle；POST /api/schedules/delete |
| NDJSON | POST /api/title-translations；POST /api/search；POST /api/ingest-selected；POST /api/verify-venue；POST /api/explain；POST /api/explain-batch；POST /api/translate；POST /api/recommend；POST /api/embed；POST /api/semsearch；POST /api/import-pdfs；POST /api/download-pdfs；POST /api/norm-venues；POST /api/cite-build；POST /api/jobs/confirm |
| Other GET | GET /api/title-translations；GET /api/explain-batch |
| Settings | GET/POST /api/settings |
| PDF/static | GET /pdfbytes；GET /papers/{paper-id-or-pdf-name}；GET/HEAD /workspace/ 及子路径；GET/HEAD /legacy/ 及子路径；既有静态资源路径 |

旧 route adapter 必须保持 server.js 当前状态码、字段名、空字符串/null、Markdown content type、列表顺序、错误文本、NDJSON type 和 terminal payload。兼容测试以 frontend/src/lib/api/decoders.ts 与 frontend/src/lib/streaming/contracts.ts 为额外消费者门禁。

上述 48 项是现有 UI 使用的 /api method/path contract；characterization suite 还必须记录 server.js 中未检查 req.method 的 /api/papers、/pdfbytes 与 /papers family 对非 GET method 的现状，以及未知 /api/* 的 404 文本，FastAPI 在兼容期不得擅自收紧。

## 固定 /api/v2 路径

- POST /api/v2/papers/{paper_id}/sources
- GET /api/v2/papers/{paper_id}/sources
- POST /api/v2/papers/{paper_id}/artifacts/explainer
- POST /api/v2/papers/{paper_id}/artifacts/translation
- POST /api/v2/papers/{paper_id}/artifacts/classification
- POST /api/v2/papers/{paper_id}/artifacts/metadata
- POST /api/v2/papers/{paper_id}/artifacts/summary
- GET /api/v2/papers/{paper_id}/artifacts
- POST /api/v2/papers/{paper_id}/index
- GET /api/v2/papers/{paper_id}/index-status
- POST /api/v2/search/chunks
- GET /api/v2/jobs
- GET /api/v2/jobs/{job_id}
- GET /api/v2/jobs/{job_id}/events
- POST /api/v2/jobs/{job_id}/cancel
- POST /api/v2/jobs/{job_id}/retry
- POST /api/v2/papers/{paper_id}/exports/obsidian 由 P5 实现。
- POST /api/v2/obsidian/sync 由 P5 实现。
- GET /api/v2/obsidian/status 由 P5 实现。
- POST /api/v2/obsidian/test 由 P5 实现。

所有 JSON request/response/query 字段沿用 P2/P3 已冻结的 camelCase wire contract，特别是 `sourceMode`、`sourceDocumentId`、`paperId`、`jobType`、`afterSequence` 与 `includeEmbeddings`；Python 内部可使用 snake_case，但只在 schema/adapter seam 转换。不得另设 GET/POST /api/v2/obsidian、POST /api/v2/jobs、DELETE /api/v2/jobs/{job_id}、generic POST /api/v2/papers/{paper_id}/artifacts、顶层 /api/v2/sources、顶层 /api/v2/artifacts 或顶层 /api/v2/exports。

## Task 0：在首个 P4 文件 mutation 前保护工作区并重放 P0–P3 入口门禁

**Files:**
- Verify only: Git worktree/index
- Verify only: data/compatibility/runtime/p0-origin-receipt-v1.json
- Verify only: P0 receipt 命名的 exact backup/Manifest 与 data/app.db
- Verify only: contracts/pre-existing-test-failures-v1.json
- Verify only: backend/tests/、test/、test/*.test.js

- [ ] **Step 1（2–5 分钟）：记录并保护用户已有改动**

在创建或修改任何 P4 source/test/fixture/doc 文件前执行；dirty worktree 不是自动失败，但完整 porcelain 集合必须保留在本次 operator session 与实施报告中。若任一计划目标已有用户改动，先逐文件审阅并采用增量 patch，禁止覆盖、还原、stash、clean 或 reset：

~~~powershell
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$p4EntryStatus = @(git status --porcelain=v1 --untracked-files=all)
if ($LASTEXITCODE -ne 0) { throw 'P4 entry git status failed.' }
$p4EntryDiffNames = @(git diff --name-only)
if ($LASTEXITCODE -ne 0) { throw 'P4 entry unstaged diff inventory failed.' }
$p4EntryCachedNames = @(git diff --cached --name-only)
if ($LASTEXITCODE -ne 0) { throw 'P4 entry staged diff inventory failed.' }
$p4EntryStatus | ForEach-Object { Write-Output ("P4_PREEXISTING_CHANGE " + $_) }
~~~

Expected: 三条 Git 命令 raw exit 0；已有 tracked/untracked/staged 文件逐项可见。该输出只是保护清单，不授权 Git 写操作，也不得把 dirty tree 误报为 P4 产生。

- [ ] **Step 2（按实际时长）：重放 exact P0 OriginReceipt、P0.1 compatibility 与 P1–P3 fixed-head evidence**

以下 block 必须在 Step 1 后、Task 1 Step 3 首次写 fixture 前完整执行。它只读取 Live DB；唯一新文件是 P0 `restore-check` 在既有 ignored rehearsal root 中创建的隔离副本。任何命令 non-zero、typed field 缺失、P0 receipt/backup 漂移、baseline 漂移、suite failure、missing/multiple head 或 Live revision 非 `20260807_03` 都在 P4 mutation 前停止：

~~~powershell
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
function Invoke-P4EntryCheckedNative {
  param([Parameter(Mandatory = $true)][string]$Label, [Parameter(Mandatory = $true)][scriptblock]$Command)
  $output = & $Command
  $exitCode = $LASTEXITCODE
  if ($exitCode -ne 0) { throw "$Label failed with exit code $exitCode." }
  $output
}
$p4EntryReceiptPath = (Resolve-Path -LiteralPath 'data/compatibility/runtime/p0-origin-receipt-v1.json').Path
$p4EntryReceiptFileSha256 = [Environment]::GetEnvironmentVariable('P0_ORIGIN_RECEIPT_SHA256', 'Process')
if ([string]::IsNullOrWhiteSpace($p4EntryReceiptFileSha256) -or $p4EntryReceiptFileSha256 -notmatch '^[0-9a-f]{64}$') { throw 'P4 entry requires the exact lowercase P0 OriginReceipt file SHA-256 from P0 evidence.' }
$p4EntryReceiptJson = Invoke-P4EntryCheckedNative 'P4 entry P0 OriginReceipt verification' { .\.venv\Scripts\python.exe -B -m backend.app.cli.database_backup verify-origin-receipt --receipt $p4EntryReceiptPath --expected-receipt-file-sha256 $p4EntryReceiptFileSha256 }
$p4EntryReceipt = $p4EntryReceiptJson | ConvertFrom-Json
foreach ($field in @('ok','backupPath','manifestPath','backupId','logicalSha256','databaseLineageId')) {
  if (-not ($p4EntryReceipt.PSObject.Properties.Name -contains $field)) { throw "P4 entry OriginReceipt verification omitted $field." }
}
if ($p4EntryReceipt.ok -isnot [bool] -or -not $p4EntryReceipt.ok) { throw 'P4 entry OriginReceipt verification did not return boolean ok=true.' }
$p4EntryOriginBackup = (Resolve-Path -LiteralPath ([string]$p4EntryReceipt.backupPath)).Path
$p4EntryOriginManifest = (Resolve-Path -LiteralPath ([string]$p4EntryReceipt.manifestPath)).Path
$p4EntryVerifyJson = Invoke-P4EntryCheckedNative 'P4 entry exact P0 backup verification' { .\.venv\Scripts\python.exe -B -m backend.app.cli.database_backup verify --backup $p4EntryOriginBackup --manifest $p4EntryOriginManifest }
$p4EntryVerify = $p4EntryVerifyJson | ConvertFrom-Json
$p4EntryRestoreJson = Invoke-P4EntryCheckedNative 'P4 entry exact P0 restore-check' { .\.venv\Scripts\python.exe -B -m backend.app.cli.database_backup restore-check --backup $p4EntryOriginBackup --manifest $p4EntryOriginManifest --output-directory data/backups/restore-checks }
$p4EntryRestore = $p4EntryRestoreJson | ConvertFrom-Json
if (-not $p4EntryVerify.ok -or -not $p4EntryRestore.ok -or $p4EntryVerify.logicalSha256 -ne $p4EntryReceipt.logicalSha256 -or $p4EntryRestore.logicalSha256 -ne $p4EntryReceipt.logicalSha256) { throw 'P4 entry P0 backup/restore logical evidence drifted from the sealed OriginReceipt.' }
Invoke-P4EntryCheckedNative 'P4 entry backend P0-P3 regression' { .\.venv\Scripts\python.exe -B -m unittest discover -s backend/tests -p 'test_*.py' -v }
Invoke-P4EntryCheckedNative 'P4 entry legacy Python regression' { .\.venv\Scripts\python.exe -B -m unittest discover -s test -p 'test_*.py' -v }
Invoke-P4EntryCheckedNative 'P4 entry Node regression' { npm.cmd test }
$p4EntryBaselineJson = Invoke-P4EntryCheckedNative 'P4 entry exact frontend baseline verification' { node scripts/pre-existing-failure-baseline.mjs verify --baseline contracts/pre-existing-test-failures-v1.json }
$p4EntryBaseline = $p4EntryBaselineJson | ConvertFrom-Json
foreach ($field in @('baselineMatched','observedSuiteExitCode','overallGreen')) {
  if (-not ($p4EntryBaseline.PSObject.Properties.Name -contains $field)) { throw "P4 entry baseline verifier omitted $field." }
}
if ($p4EntryBaseline.baselineMatched -isnot [bool] -or -not $p4EntryBaseline.baselineMatched) { throw 'P4 entry baselineMatched must be boolean true.' }
if ($p4EntryBaseline.observedSuiteExitCode -isnot [int] -and $p4EntryBaseline.observedSuiteExitCode -isnot [long]) { throw 'P4 entry observedSuiteExitCode must be an integer.' }
if ($p4EntryBaseline.overallGreen -isnot [bool] -or (($p4EntryBaseline.observedSuiteExitCode -eq 0) -ne $p4EntryBaseline.overallGreen)) { throw 'P4 entry baseline authorization fields are semantically inconsistent.' }
$p4EntryHeadsRaw = @(Invoke-P4EntryCheckedNative 'P4 entry Alembic heads' { .\.venv\Scripts\python.exe -B -m alembic -c backend/alembic.ini heads })
$p4EntryHeads = @($p4EntryHeadsRaw | ForEach-Object { ([string]$_).Trim() } | Where-Object { $_ -ne '' })
if ($p4EntryHeads.Count -ne 1 -or $p4EntryHeads[0] -ne '20260807_03 (head)') { throw "P4 entry requires exactly 20260807_03 (head); observed: $($p4EntryHeads -join ' | ')." }
$p4EntryLiveInspectJson = Invoke-P4EntryCheckedNative 'P4 entry Live fixed-revision inspection' { .\.venv\Scripts\python.exe -B -m backend.app.cli.database_backup inspect --database data/app.db }
$p4EntryLiveInspect = $p4EntryLiveInspectJson | ConvertFrom-Json
if (-not $p4EntryLiveInspect.ok -or $p4EntryLiveInspect.database.alembicVersion -ne '20260807_03') { throw 'P4 entry Live database is not the exact P3 revision 20260807_03.' }
~~~

Expected: P0 fixed receipt、其命名的 exact backup/Manifest、独立 restore-check、P0.1 exact baseline verifier、P1–P3 backend/legacy/Node regression、唯一 migration head 与 Live current revision 全部通过。已审核 frontend non-zero 仍以整数 raw code 和 `overallGreen=false` 留痕，不能称为绿色；任何漂移发生时 P4 尚未修改 source/test/fixture/doc。

## Task 1：锁定旧 HTTP/NDJSON 契约清单

**Files:**
- Create: backend/tests/fixtures/http/legacy_route_inventory.json
- Create: backend/tests/support/node_contract_server.py
- Create: backend/tests/test_http_contract_inventory.py
- Reference: server.js
- Reference: frontend/src/lib/api/decoders.ts
- Reference: frontend/src/lib/streaming/contracts.ts

- [ ] **Step 1（2–5 分钟）：写 inventory 缺失红测**

在 LegacyContractInventoryTests.test_inventory_covers_every_server_route 中解析冻结的 route inventory，断言上述每个 method/path 恰好出现一次，并断言 15 个 NDJSON 路由均声明 progress 与唯一 terminal type。

- [ ] **Step 2（2–5 分钟）：运行红测并确认失败原因**

Run:

~~~powershell
.\.venv\Scripts\python.exe -B -m unittest backend.tests.test_http_contract_inventory.LegacyContractInventoryTests.test_inventory_covers_every_server_route -v
~~~

Expected RED: ImportError 或 FileNotFoundError 指向 legacy_route_inventory.json/support helper 尚不存在；不得接受 JSON 拼写错误或 fixture 启动失败作为有效红灯。

- [ ] **Step 3（2–5 分钟）：建立最小 route inventory**

逐项写入 method、path、response_kind、success_status、content_type、terminal_type；对动态 /papers 路径使用 literal family /papers/{paper-id-or-pdf-name}，不使用未受约束通配符。

- [ ] **Step 4（2–5 分钟）：加入 Node 隔离启动 helper**

helper 使用显式临时 DB、随机监听端口和受控环境变量启动 node server.js；terminate 后等待退出并关闭 stdout/stderr pipe，不读取或写入 data/app.db。

- [ ] **Step 5（2–5 分钟）：重新运行 route inventory 定向测试并确认 GREEN**

Run:

~~~powershell
.\.venv\Scripts\python.exe -B -m unittest backend.tests.test_http_contract_inventory.LegacyContractInventoryTests.test_inventory_covers_every_server_route -v
~~~

Expected GREEN: unittest summary reports 1 test and OK；inventory 报告 48 个 /api method/path contract，其中 15 个为 NDJSON，并另外覆盖 PDF/static families。

- [ ] **Step 6（2–5 分钟）：加入 Node golden capture 有效性测试**

新增 test_node_golden_capture_uses_isolated_database，断言 capture 前后仓库 data/app.db 的 size/mtime 不变，临时 DB 在 cleanup 后不存在。

- [ ] **Step 7（2–5 分钟）：运行 Task 1 定向验证**

Run:

~~~powershell
.\.venv\Scripts\python.exe -B -m unittest backend.tests.test_http_contract_inventory -v
~~~

Expected: inventory 与隔离性测试全部 OK；没有后台 node.exe 残留。

## Task 2：建立无副作用 FastAPI composition root

**Files:**
- Modify: requirements.in
- Regenerate: requirements.txt with exact transitive versions and hashes
- Modify: backend/app/api/app.py
- Modify: backend/app/api/router.py
- Create: backend/app/api/dependencies.py
- Modify: backend/app/api/errors.py
- Create: backend/app/api/middleware/__init__.py
- Create: backend/app/api/middleware/local_access.py
- Create: backend/app/runtime.py
- Create: backend/tests/test_api_health.py

- [ ] **Step 1（2–5 分钟）：写 import 与 health 红测**

新增 ApiHealthTests.test_import_does_not_open_database_or_start_runtime、test_liveness_does_not_require_database、test_factory_requires_explicit_schema_revision、test_ready_on_expected_head、`test_readiness_rejects_missing_migration_head`、test_readiness_rejects_wrong_migration_head、`test_readiness_rejects_multiple_migration_heads`、`test_api_worker_scheduler_bootstrap_revision_matrix_fails_before_side_effects`、test_default_bind_and_host_policy_are_loopback_only 与 test_state_changing_requests_reject_untrusted_origin；patch sqlite connect、socket bind、Provider construction、job claim、Worker lease 与 Scheduler lease 为有序 spy。测试先提供最小可导入的 Worker/Scheduler fake port，不能因为尚未创建 production scheduler module 而把 ImportError 当作行为 RED。revision fixtures 分别真实制造 missing、exact `20260807_03`、单个 wrong 和“目标 revision + 额外 revision”四态；multiple fixture 必须真实写入两个 current rows/heads，不能用重复 mock 返回值伪造。matrix test 对 `api|worker|scheduler × missing|multiple|wrong|exact` 做十二个 named subtests：前三态在 socket/claim/lease/provider 前返回各自稳定 schema error 且所有副作用 spy 为 0，exact 态只越过 schema gate并到达注入的 role sentinel，不能用一条 API readiness 测试代替三 role bootstrap。访问测试冻结：默认 `API_BIND_HOST=127.0.0.1`；生产命令不得省略该默认；只接受 loopback/local application Host；不读取 `Forwarded`/`X-Forwarded-*` 来放宽信任；带 Origin 的 POST/PUT/PATCH/DELETE 只接受与有效 Host 同源；缺 Origin 的同机非浏览器调用保持旧 API 兼容。任何非 loopback bind 都必须同时显式设置 `ALLOW_REMOTE_ACCESS=1`，否则启动 fail-fast；P0–P6 不把该 opt-in 写入默认部署。

- [ ] **Step 2（2–5 分钟）：确认红测**

Run:

~~~powershell
.\.venv\Scripts\python.exe -B -m unittest backend.tests.test_api_health.ApiHealthTests.test_import_does_not_open_database_or_start_runtime backend.tests.test_api_health.ApiHealthTests.test_liveness_does_not_require_database backend.tests.test_api_health.ApiHealthTests.test_factory_requires_explicit_schema_revision backend.tests.test_api_health.ApiHealthTests.test_ready_on_expected_head backend.tests.test_api_health.ApiHealthTests.test_readiness_rejects_missing_migration_head backend.tests.test_api_health.ApiHealthTests.test_readiness_rejects_wrong_migration_head backend.tests.test_api_health.ApiHealthTests.test_readiness_rejects_multiple_migration_heads backend.tests.test_api_health.ApiHealthTests.test_api_worker_scheduler_bootstrap_revision_matrix_fails_before_side_effects backend.tests.test_api_health.ApiHealthTests.test_default_bind_and_host_policy_are_loopback_only backend.tests.test_api_health.ApiHealthTests.test_state_changing_requests_reject_untrusted_origin -v
~~~

Expected RED: backend.app.api.app 或 local-access policy 不存在；三个 spy 均不得因测试 fixture 自身提前触发。

- [ ] **Step 3（2–5 分钟）：加入精确依赖**

在 P1 的 requirements.in 中固定 FastAPI==0.116.1、Uvicorn==0.49.0、httpx==0.28.1、anyio==4.13.0，并重新生成带 hash 的 requirements.txt；测试解析 lock 后断言这些 exact versions 与 hashes。不得执行无 lock 的安装，不顺带升级 OCR、embedding 或前端依赖。

- [ ] **Step 4（2–5 分钟）：扩展既有 create_app 与 runtime config**

沿用 P1 test app factory 和 P2/P3 `backend/app/api/router.py`，不得创建第二个 composition root。签名精确为 `create_app(settings, dependencies, *, required_schema_revision)`，keyword-only 且没有默认值；漏传在构造阶段直接 `TypeError`，不得以 module constant、symbolic `head` 或 fallback 补齐。阶段 gate 必须把 `required_schema_revision="20260807_03"` 显式传入并与数据库全部非空 current/head 输出精确比较：总数必须为 1 且唯一值必须等于 `20260807_03`；missing、multiple、wrong 分别 fail closed，exact 才通过，禁止先过滤目标值后再计数，也禁止把 `head` 当作可变的“最新”值。GET /health/live 固定返回 {"status":"ok"}；GET /health/ready 在 request 时检查 DB 可读、PRAGMA foreign_keys、Alembic 单 head 为传入的 `20260807_03`，并通过 `backend/app/api/errors.py` 以 503 返回分类错误。local-access middleware 对 Host 使用严格解析和端口归一化，拒绝含 CR/LF、userinfo、歧义 IPv6 或未列入有效监听集合的值；Origin 校验不从 Referer 推断。健康检查同样只在绑定 socket 的本地访问面可用。

- [ ] **Step 5（2–5 分钟）：实现 lifespan 所有权开关**

解析 API_PROCESS_ROLE=api|worker|scheduler、`API_BIND_HOST` 与 `ALLOW_REMOTE_ACCESS`；非法值 fail-fast。api/worker/scheduler 三个 CLI 都把 stage-frozen `required_schema_revision="20260807_03"` 传入同一 bootstrap gate，并在打开 socket、claim、scheduler lease 或 provider 前拒绝 missing/multiple/wrong revision；不得各自复制或写死另一套 head 检查。api role 不启动 Worker/Scheduler，worker role 不监听 HTTP，scheduler role 不执行普通 job。`API_BIND_HOST` 缺失时固定为 `127.0.0.1`；非 loopback 且未显式 opt-in 时必须在打开 socket、DB、provider 或 runtime lease 前失败。

- [ ] **Step 6（2–5 分钟）：重新运行 health/local-access 定向测试并确认 GREEN**

Run:

~~~powershell
.\.venv\Scripts\python.exe -B -m unittest backend.tests.test_api_health.ApiHealthTests.test_import_does_not_open_database_or_start_runtime backend.tests.test_api_health.ApiHealthTests.test_liveness_does_not_require_database backend.tests.test_api_health.ApiHealthTests.test_factory_requires_explicit_schema_revision backend.tests.test_api_health.ApiHealthTests.test_ready_on_expected_head backend.tests.test_api_health.ApiHealthTests.test_readiness_rejects_missing_migration_head backend.tests.test_api_health.ApiHealthTests.test_readiness_rejects_wrong_migration_head backend.tests.test_api_health.ApiHealthTests.test_readiness_rejects_multiple_migration_heads backend.tests.test_api_health.ApiHealthTests.test_api_worker_scheduler_bootstrap_revision_matrix_fails_before_side_effects backend.tests.test_api_health.ApiHealthTests.test_default_bind_and_host_policy_are_loopback_only backend.tests.test_api_health.ApiHealthTests.test_state_changing_requests_reject_untrusted_origin -v
~~~

Expected GREEN: 10 个 top-level tests 与 revision matrix 的 12 个 subtests 全部 OK；missing/multiple/wrong 在 api/worker/scheduler 的 DB/provider/socket/claim/lease spy 调用数均为 0，exact 只到达注入 sentinel；默认或非法 remote 配置都不会打开非 loopback socket。

- [ ] **Step 7（2–5 分钟）：运行 Task 2 回归**

Run:

~~~powershell
.\.venv\Scripts\python.exe -B -m unittest backend.tests.test_api_health -v
~~~

Expected: 全部 OK；错误 readiness 返回 503 与稳定 code，不泄露 DB 绝对路径。

## Task 3：接管 Paper、Settings、Review 与旧 Artifact 读取

**Files:**
- Create: backend/app/application/paper_library.py
- Create: backend/app/application/library_queries.py
- Create: backend/app/application/settings.py
- Create: backend/app/application/review_scheduler.py
- Create: backend/app/application/artifact_store.py
- Create: backend/app/api/routes/__init__.py
- Create: backend/app/api/routes/legacy.py
- Create: backend/tests/test_api_legacy_json.py
- Modify: backend/app/api/app.py
- Modify: backend/app/api/dependencies.py

- [ ] **Step 1（2–5 分钟）：写 Paper wire 红测**

新增 LegacyJsonApiTests.test_paper_routes_match_node_golden，覆盖 GET /api/papers、GET /api/paper/get、add、update、progress、favorite、delete 的成功、缺 id、空标题、未知 paper；比较状态、content type 与完整 JSON。

- [ ] **Step 2（2–5 分钟）：确认 Paper 红测**

Run:

~~~powershell
.\.venv\Scripts\python.exe -B -m unittest backend.tests.test_api_legacy_json.LegacyJsonApiTests.test_paper_routes_match_node_golden -v
~~~

Expected RED: FastAPI 返回 404，首个差异为 GET /api/papers；不是 Node golden server 启动失败。

- [ ] **Step 3（2–5 分钟）：实现 PaperLibrary 最小写模型**

每个方法只通过 P1 UoW/repositories 操作；paper_id 始终是 papers.id；更新白名单字段；progress 复用 ensureReviewPlan hook；delete 在同一事务处理 DB 关联，PDF 删除委托 PdfFiles 且保持 legacy 的成功响应。

- [ ] **Step 4（2–5 分钟）：实现 Paper legacy adapter**

逐个显式注册七组 route，保留 hasPdf、changes、ok/id/error 字段；错误 mapper 区分 400/404/500，不让 FastAPI 默认 422 泄露到 legacy 路径。

- [ ] **Step 5（2–5 分钟）：重新运行 Paper parity 定向测试并确认 GREEN**

Run:

~~~powershell
.\.venv\Scripts\python.exe -B -m unittest backend.tests.test_api_legacy_json.LegacyJsonApiTests.test_paper_routes_match_node_golden -v
~~~

Expected GREEN: Node/FastAPI golden 的状态、header 与 body 全等。

- [ ] **Step 6（2–5 分钟）：写 Settings/CredentialStore 红测**

新增 `test_settings_use_provider_profiles_and_redacted_credentials`，用隔离 `settings.json`、环境变量与 Fake Keyring 覆盖四个现有 credential kind：LLM、OCR、Embedding API 与 Semantic Scholar。冻结非敏感配置分别覆盖 LLM/OCR 的 provider、base URL、model、timeout，OCR enabled/page batch/max concurrency，Embedding 的 `embedProvider/embedApiBase/embedApiModel`，以及 Semantic Scholar 的 provider/endpoint 标识；冻结 legacy wire 的 secret 输入/状态字段为 `apiKey` + `hasApiKey/apiKeyTail`、`ocrApiKey` + `hasOcrKey/ocrKeyTail`、`embedApiKey` + `hasEmbedKey/embedKeyTail`、`s2ApiKey` + `hasS2Key/s2KeyTail`。内部统一 `CredentialStatus` 只允许 `kind/hasKey/keyTail/environmentManaged`，legacy adapter 才映射这些既有字段；空白保存逐 kind 保留旧 Credential，显式 clear 才清除该 kind 的可写层。HTTP 响应、捕获日志和异常正文均不得出现完整 key、Authorization header 或可逆 hash。

- [ ] **Step 7（2–5 分钟）：确认 Settings 红测**

Run:

~~~powershell
.\.venv\Scripts\python.exe -B -m unittest backend.tests.test_api_legacy_json.LegacyJsonApiTests.test_settings_use_provider_profiles_and_redacted_credentials -v
~~~

Expected RED: legacy route 尚未接入扩展后的 CredentialStore，或四类返回 DTO/空白保存/脱敏断言不满足；fixture 使用四个彼此不同的测试 key，并断言真实 settings、Keyring 与网络均未触碰。

- [ ] **Step 8（2–5 分钟）：实现 Settings adapter**

`backend/app/application/settings.py` 组合扩展后的 CredentialStore 与四类非敏感 ProviderProfile；legacy `/api/settings` adapter 只做冻结 wire 映射，不自行读取/写入 secret。Canonical `CredentialKind` 精确为 `llm|ocr|embedding|semantic_scholar`；环境变量分别为 `LLM_API_KEY`、`OCR_API_KEY`、`EMBED_API_KEY`、`S2_API_KEY`，Keyring usernames 分别为 `credential:llm`、`credential:ocr`、`credential:embedding`、`credential:semantic_scholar`，legacy compatibility fields 分别为 `apiKey`、`ocrApiKey`、`embedApiKey`、`s2ApiKey`。保存设置先验证非敏感字段，再逐 kind 调用 CredentialStore；任何空白 credential 都触发零写。环境变量 credential 保持只读优先级，四个 legacy plaintext 字段在 Node rollback window 内按同一原子 settings 写事务保留/同步；未知和非 secret 字段必须原样保留。

- [ ] **Step 9（2–5 分钟）：实现安全连接探测并运行 Settings 定向测试确认 GREEN**

`/api/test-llm` 只使用打包固定文本；OCR 探测只能使用打包 synthetic PNG，且 provider contract 未验证时零 transport 调用并返回 `OCR_PROVIDER_CONTRACT_UNVERIFIED`。

Run:

~~~powershell
.\.venv\Scripts\python.exe -B -m unittest backend.tests.test_api_legacy_json.LegacyJsonApiTests.test_settings_use_provider_profiles_and_redacted_credentials -v
~~~

Expected GREEN: unittest summary reports 1 test and OK；四个 key 均只以各自脱敏尾号出现，用户 PDF/Paper/正文读取 spy 均为 0。

- [ ] **Step 10（2–5 分钟）：写 Review/Artifact 红测**

新增 `test_reviews_and_artifact_reads_match_node_golden`，覆盖 reviews 三路由、GET/POST note、GET explainer、GET translation、GET title-translations 与 GET explain-batch。

- [ ] **Step 11（2–5 分钟）：确认 Review/Artifact 红测**

Run:

~~~powershell
.\.venv\Scripts\python.exe -B -m unittest backend.tests.test_api_legacy_json.LegacyJsonApiTests.test_reviews_and_artifact_reads_match_node_golden -v
~~~

Expected RED: 首个未实现路径返回 404；不是 Node golden fixture 启动错误。

- [ ] **Step 12（2–5 分钟）：实现最小 Review/Artifact adapters**

ReviewScheduler 仅委托既有规则；ArtifactStore 新表优先、旧 `papers.explainer`/`translations.content`/`notes` 回退，缺 explainer 保留文件回退与既有占位文本。所有错误继续经过唯一 `backend/app/api/errors.py` seam。

- [ ] **Step 13（2–5 分钟）：重新运行 Review/Artifact parity 测试并确认 GREEN**

Run:

~~~powershell
.\.venv\Scripts\python.exe -B -m unittest backend.tests.test_api_legacy_json.LegacyJsonApiTests.test_reviews_and_artifact_reads_match_node_golden -v
~~~

Expected GREEN: 全组 OK；旧字段 fixture 可读，失败的新 artifact 不覆盖旧成功结果。

- [ ] **Step 14（2–5 分钟）：运行 Task 3 回归**

Run:

~~~powershell
.\.venv\Scripts\python.exe -B -m unittest backend.tests.test_api_legacy_json -v
~~~

Expected: 全部 legacy JSON/Markdown 测试 OK；日志捕获中没有 secret。

## Task 4：挂载并冻结 P2/P3 typed v2 routers

**Files:**
- Create: backend/tests/test_api_v2.py
- Modify: backend/app/api/app.py
- Modify: backend/app/api/router.py
- Modify: backend/app/api/dependencies.py
- Modify: backend/app/application/library_queries.py
- Verify only: backend/app/api/routes/document_processing.py
- Verify only: backend/app/api/routes/document_consumers.py
- Verify only: backend/app/api/routes/document_search.py
- Verify only: backend/app/api/errors.py

- [ ] **Step 1（2–5 分钟）：写 v2 路径与 schema 红测**

新增 `ApiV2Tests.test_p2_p3_routes_are_mounted_once_and_typed`，断言两条 sources、五条明确 artifact command、artifact GET、index/index-status、chunk search 和五条 jobs read/action 路径各恰好存在一次；OpenAPI 不出现 generic POST `/api/v2/jobs`、DELETE job、generic POST artifacts、GET/POST `/api/v2/obsidian`、顶层 sources/artifacts/exports，并验证 UUID/整数、ISO-8601 UTC、null 与分页字段类型。

- [ ] **Step 2（2–5 分钟）：确认 v2 红测**

Run:

~~~powershell
.\.venv\Scripts\python.exe -B -m unittest backend.tests.test_api_v2.ApiV2Tests.test_p2_p3_routes_are_mounted_once_and_typed -v
~~~

Expected RED: P4 composition root 尚未挂载至少一个 P2/P3 router，或 OpenAPI 出现缺失/重复 operation；P2/P3 router module 自身可独立 import。

- [ ] **Step 3（2–5 分钟）：挂载既有 routers**

在 `backend/app/api/router.py` 只 include P2 `document_processing.router` 与 P3 `document_consumers.router`/`document_search.router`，再由 P4 app 挂载一次。禁止复制 route 函数、Pydantic DTO、ProcessingQueue facade、cursor 编码、error mapping 或 worker dispatch。

- [ ] **Step 4（2–5 分钟）：冻结 camelCase wire 与 strict input**

通过现有 P2/P3 tests 和 OpenAPI 断言 request/response/query 外部字段为 `sourceMode`、`sourceDocumentId`、`paperId`、`jobType`、`afterSequence`、`includeEmbeddings`；发送 `source_mode` 或 `source_document_id` 必须 422。Python 内部 snake_case 只存在于 schema alias seam 后。

- [ ] **Step 5（2–5 分钟）：复用 read/application dependencies**

P4 dependency wiring 注入 P1/P2/P3 已有 repositories、LibraryQueries、ProcessingQueue 与 consumer use cases。source/artifact 必须属于 path 中 `paper_id`；不存在或不属于该 paper 统一返回 P2 冻结的 `SOURCE_NOT_FOUND`，mode mismatch 返回 `SOURCE_MODE_MISMATCH`。任何 request 都不在 route 内调用 LLM/OCR、执行重试或复制 job transition；新表 query/decode/repository error 不得被转换成 not-found 或旧字段回退，必须沿用统一的 fail-closed persistence error mapper。

- [ ] **Step 6（2–5 分钟）：重新运行 v2 route 定向测试并确认 GREEN**

Run:

~~~powershell
.\.venv\Scripts\python.exe -B -m unittest backend.tests.test_api_v2.ApiV2Tests.test_p2_p3_routes_are_mounted_once_and_typed -v
~~~

Expected GREEN: unittest summary reports 1 test and OK；P2/P3 固定路径各出现一次，OpenAPI 仅出现允许的 nested/action paths。

- [ ] **Step 7（2–5 分钟）：加入 legacy/v2 同源断言**

新增 test_legacy_and_v2_share_paper_identity，断言 source/artifact path 中 paper_id 始终等于 papers.id、旧字段仍存在、生成 artifact 后 legacy explainer/translation 仍可读。

- [ ] **Step 8（2–5 分钟）：运行 Task 4 与既有 P2/P3 API 回归**

Run:

~~~powershell
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
.\.venv\Scripts\python.exe -B -m unittest backend.tests.test_api_v2 -v
if ($LASTEXITCODE -ne 0) { throw 'P4 v2 API suite failed.' }
.\.venv\Scripts\python.exe -B -m unittest backend.tests.test_processing_jobs_api backend.tests.test_document_search_api -v
if ($LASTEXITCODE -ne 0) { throw 'P4 P2/P3 API regression failed.' }
~~~

Expected: 三个模块全部 OK；没有 request 内 provider 调用，没有第二套 route/status/error 实现。

## Task 5：接管 ingest_jobs、processing_jobs 与 schedules，但保持状态机分离

**Files:**
- Create: backend/app/application/legacy_ingest.py
- Create: backend/app/providers/legacy_agent.py
- Create: backend/tests/test_api_jobs_schedules.py
- Modify: backend/app/api/routes/legacy.py
- Modify: backend/app/api/dependencies.py
- Create: backend/app/workers/scheduler.py
- Verify only: backend/app/api/routes/document_processing.py
- Verify only: backend/app/application/ports/processing_queue.py

- [ ] **Step 1（2–5 分钟）：写两类 Job 不混用红测**

新增 JobApiTests.test_legacy_ingest_jobs_and_v2_processing_jobs_are_distinct，断言 POST /api/jobs 写 ingest_jobs，而 POST /api/v2/papers/paper-1/sources 写 processing_jobs；GET /api/v2/jobs 只列 processing_jobs，两类 ID/状态/删除语义独立。

- [ ] **Step 2（2–5 分钟）：确认红测**

Run:

~~~powershell
.\.venv\Scripts\python.exe -B -m unittest backend.tests.test_api_jobs_schedules.JobApiTests.test_legacy_ingest_jobs_and_v2_processing_jobs_are_distinct -v
~~~

Expected RED: 路由 404 或两类 job 被错误写入同一 repository。

- [ ] **Step 3（2–5 分钟）：实现 legacy ingest job adapter**

保持 GET/POST /api/jobs、detail、delete、confirm 的现有 wire；将 run-job/ingest-selected 封装进 LegacyAgentProvider；HTTP route 只验证输入和调用 application interface。

- [ ] **Step 4（2–5 分钟）：验证既有 v2 ProcessingJob read/action adapter**

GET `/api/v2/jobs`、GET `/api/v2/jobs/{job_id}`、GET `/events`、POST `/cancel`、POST `/retry` 继续调用 P2 ProcessingQueue/use cases；events 使用持久化 job event/progress 顺序，cancel/retry 遵守 P2 状态机，terminal cancel 返回 409 `JOB_NOT_CANCELLABLE`，状态、目标或错误类别不允许 retry 返回 409 `JOB_NOT_RETRYABLE`。P4 只验证 composition wiring，不得实现第二个 coordinator、generic POST `/api/v2/jobs` 或 DELETE cancel。

- [ ] **Step 5（2–5 分钟）：重新运行 Job API 分离测试并确认 GREEN**

Run:

~~~powershell
.\.venv\Scripts\python.exe -B -m unittest backend.tests.test_api_jobs_schedules.JobApiTests.test_legacy_ingest_jobs_and_v2_processing_jobs_are_distinct -v
~~~

Expected GREEN: 两类表的 count 按预期各增加 1，互不读取。

- [ ] **Step 6（2–5 分钟）：写 schedules parity 红测**

新增 test_schedule_routes_match_node_and_enqueue_once，覆盖 list/create/toggle/delete、due schedule 单次 claim、next_run 更新与重复 tick。

- [ ] **Step 7（2–5 分钟）：确认 schedules 红测**

Run:

~~~powershell
.\.venv\Scripts\python.exe -B -m unittest backend.tests.test_api_jobs_schedules.JobApiTests.test_schedule_routes_match_node_and_enqueue_once -v
~~~

Expected RED: legacy schedule route 404 或重复 tick 创建两个 job。

- [ ] **Step 8（2–5 分钟）：实现最小 schedule adapter 与 lease**

legacy wire 继续操作原 schedules/ingest_jobs；Python scheduler 在独立 role 中以 transaction claim 到期 schedule，创建一次 ingest job 后更新 next_run；API role 不 tick。processing_jobs 的 claim/cancel/retry 继续使用 P2 backend/app/application/ports/processing_queue.py 和 backend/app/workers/processing_worker.py，不在 P4 重建；所有 enqueue/claim/retry/recovery 继续 strict decode 或逐 byte 复制 frozen `spec_json`，不得从 `progress_json`、当前 Settings 或 target row 重建请求，也不得让 legacy adapter 回显 spec。

- [ ] **Step 9（2–5 分钟）：重新运行 schedule parity 测试并确认 GREEN**

Run:

~~~powershell
.\.venv\Scripts\python.exe -B -m unittest backend.tests.test_api_jobs_schedules.JobApiTests.test_schedule_routes_match_node_and_enqueue_once -v
~~~

Expected GREEN: Node golden 相等且并发 tick 只产生一个 ingest job。

- [ ] **Step 10（2–5 分钟）：运行 Task 5 回归**

Run:

~~~powershell
.\.venv\Scripts\python.exe -B -m unittest backend.tests.test_api_jobs_schedules -v
~~~

Expected: 全部 OK；不存在 route 直接 spawn 或直接 SQL。

## Task 6：统一 NDJSON encoder 并接管全部 15 条流

**Files:**
- Modify: backend/app/api/middleware/__init__.py
- Create: backend/app/api/middleware/ndjson.py
- Create: backend/tests/test_api_ndjson.py
- Modify: backend/app/api/routes/legacy.py
- Modify: backend/app/application/legacy_ingest.py
- Create: backend/app/application/search_coordinator.py
- Modify: backend/app/providers/legacy_agent.py
- Verify only: backend/app/application/ports/processing_queue.py

- [ ] **Step 1（2–5 分钟）：写字节协议红测**

新增 NdjsonApiTests.test_all_streams_match_node_event_contract，逐路由断言 application/x-ndjson、每行可独立 JSON.parse、progress 顺序、唯一 terminal type、末行换行及 terminal 后无字节。

- [ ] **Step 2（2–5 分钟）：确认 NDJSON 红测**

Run:

~~~powershell
.\.venv\Scripts\python.exe -B -m unittest backend.tests.test_api_ndjson.NdjsonApiTests.test_all_streams_match_node_event_contract -v
~~~

Expected RED: 首个 NDJSON 路径返回 404 或 content-type 为 application/json。

- [ ] **Step 3（2–5 分钟）：实现唯一 encoder**

encoder 接收 dict async iterator，UTF-8 紧凑编码后追加单个 LF；禁止 pretty print、多行字符串裸写和多个 terminal；headers 发出后的错误转换为该路由既有 terminal error event。

- [ ] **Step 4（2–5 分钟）：接管 discovery 流**

实现 title-translations、search、ingest-selected、verify-venue、recommend、import-pdfs、download-pdfs、norm-venues、cite-build；LegacyAgentProvider 负责受控子进程和 stdout/stderr framing，Route 不持有 Popen。

- [ ] **Step 5（2–5 分钟）：接管 artifact/search 流**

实现 explain、explain-batch、translate、embed、semsearch；写操作通过 ProcessingJob/worker 或既有 pipeline，terminal payload 继续符合旧 decoder，已持久化 job 的最终状态明确。

- [ ] **Step 6（2–5 分钟）：实现 jobs/confirm done 事件**

保持 progress 与 done 类型、added 字段和 candidate 标记顺序；事务失败时 done.ok=false，不吞掉数据库异常后伪报成功。

- [ ] **Step 7（2–5 分钟）：重新运行 NDJSON parity 测试并确认 GREEN**

Run:

~~~powershell
.\.venv\Scripts\python.exe -B -m unittest backend.tests.test_api_ndjson.NdjsonApiTests.test_all_streams_match_node_event_contract -v
~~~

Expected GREEN: 15 条路由的 golden event sequence 全等。

- [ ] **Step 8（2–5 分钟）：写持久任务 detach 与慢消费者红测**

新增 `test_disconnect_detaches_persisted_job_without_cancelling_worker` 与 `test_slow_consumer_preserves_event_order`。对 ProcessingJob-backed explain/translate/embed 流，浏览器断开只取消订阅 scope：ProcessingQueue.cancel、worker provider cancel 与 job terminal mutation spy 均为 0，job 保持 queued/running 并可在无浏览器连接时完成。对尚未持久化的 legacy request-scoped 子进程，断开仍按 Node golden 终止该子进程，但不得创建或取消 ProcessingJob。

- [ ] **Step 9（2–5 分钟）：确认红测并最小修复**

Run:

~~~powershell
.\.venv\Scripts\python.exe -B -m unittest backend.tests.test_api_ndjson.NdjsonApiTests.test_disconnect_detaches_persisted_job_without_cancelling_worker backend.tests.test_api_ndjson.NdjsonApiTests.test_slow_consumer_preserves_event_order -v
~~~

Expected RED before fix: 断开传播到 ProcessingQueue/worker provider、job 被错误标为 cancelled，或事件乱序。使用 anyio cancellation scope 隔离 subscriber 与 worker owner，并以单 producer channel 保序；不在线程间直接写 response。

- [ ] **Step 10（2–5 分钟）：运行持久任务 detach 与慢消费者测试并确认 GREEN**

Run:

~~~powershell
.\.venv\Scripts\python.exe -B -m unittest backend.tests.test_api_ndjson.NdjsonApiTests.test_disconnect_detaches_persisted_job_without_cancelling_worker backend.tests.test_api_ndjson.NdjsonApiTests.test_slow_consumer_preserves_event_order -v
~~~

Expected GREEN: unittest summary reports 2 tests and OK；持久 job 在 detach 后可完成，显式 `/cancel` 仍可取消；无 orphan legacy child process。

## Task 7：接管 PDF、workspace、legacy 与静态安全边界

**Files:**
- Create: backend/app/providers/pdf_files.py
- Create: backend/app/api/static/__init__.py
- Create: backend/app/api/static/frontend_assets.py
- Create: backend/tests/test_api_pdf_static.py
- Modify: backend/app/api/app.py
- Port contract from: lib/frontend-assets.js
- Port tests from: test/react-entry-routing.test.js

- [ ] **Step 1（2–5 分钟）：写 PDF parity 与 traversal 红测**

新增 PdfStaticApiTests.test_pdfbytes_and_papers_match_node、test_pdf_paths_cannot_escape_roots，覆盖缺失、Unicode ID、.pdf suffix、content type、content length、cache-control、编码 traversal、绝对路径与 symlink escape。

- [ ] **Step 2（2–5 分钟）：确认 PDF 红测**

Run:

~~~powershell
.\.venv\Scripts\python.exe -B -m unittest backend.tests.test_api_pdf_static.PdfStaticApiTests.test_pdfbytes_and_papers_match_node backend.tests.test_api_pdf_static.PdfStaticApiTests.test_pdf_paths_cannot_escape_roots -v
~~~

Expected RED: /pdfbytes 返回 404 且合法 fixture 也无法读取；安全 fixture 不得位于真实 PDF 目录。

- [ ] **Step 3（2–5 分钟）：实现 PdfFiles**

按 papers.pdf_path、默认 PDF 目录、自定义目录、seed 目录的既有优先级定位；每个 candidate resolve 后必须 contained in allowlisted root；打开文件描述符后再 stat/stream，拒绝目录与 symlink escape。

- [ ] **Step 4（2–5 分钟）：实现两个 PDF adapter**

/pdfbytes 保持 application/octet-stream、Content-Length、Cache-Control no-store；/papers family 保持扩展名 MIME 和既有 404 文本。HEAD 不读取 body。

- [ ] **Step 5（2–5 分钟）：重新运行 PDF parity/security 测试并确认 GREEN**

Run:

~~~powershell
.\.venv\Scripts\python.exe -B -m unittest backend.tests.test_api_pdf_static.PdfStaticApiTests.test_pdfbytes_and_papers_match_node backend.tests.test_api_pdf_static.PdfStaticApiTests.test_pdf_paths_cannot_escape_roots -v
~~~

Expected GREEN: 合法响应与 Node golden 等价，全部逃逸为 404 且未打开外部文件。

- [ ] **Step 6（2–5 分钟）：写双入口红测**

新增 test_workspace_and_legacy_entry_contract，移植 /workspace/、/legacy/、encoded path、dotfile、source map、SPA fallback、GET/HEAD 和不支持方法断言。

- [ ] **Step 7（2–5 分钟）：确认静态红测**

Run:

~~~powershell
.\.venv\Scripts\python.exe -B -m unittest backend.tests.test_api_pdf_static.PdfStaticApiTests.test_workspace_and_legacy_entry_contract -v
~~~

Expected RED: /workspace/ 或 /legacy/ 返回默认 FastAPI 404。

- [ ] **Step 8（2–5 分钟）：最小移植 FrontendAssets**

保持 lib/frontend-assets.js 的 raw pathname 选择、安全 decode、root containment、index fallback、缓存头和 MIME；不得用通用 StaticFiles 配置放宽 dotfile 或 fallback 规则。

- [ ] **Step 9（2–5 分钟）：重新运行静态入口测试并确认 GREEN**

Run:

~~~powershell
.\.venv\Scripts\python.exe -B -m unittest backend.tests.test_api_pdf_static.PdfStaticApiTests.test_workspace_and_legacy_entry_contract -v
~~~

Expected GREEN: 所有入口/header/body 与 Node contract 相等，traversal 不命中任何 SPA index。

- [ ] **Step 10（2–5 分钟）：运行 Node 原静态回归**

Run:

~~~powershell
node --test test/react-entry-routing.test.js
~~~

Expected: 原 Node 路由测试仍全部通过，证明兼容实现未破坏 rollback runtime。

## Task 8：完成剩余 JSON 路由与真实前端 parity

**Files:**
- Modify: backend/app/api/routes/legacy.py
- Modify: backend/app/application/search_coordinator.py
- Modify: backend/app/providers/legacy_agent.py
- Create: frontend/e2e/fastapi-parity.spec.ts
- Modify: backend/tests/test_api_legacy_json.py

- [ ] **Step 1（2–5 分钟）：写剩余 JSON route 红测**

新增 test_discovery_pdf_citation_and_llm_json_routes_match_node，覆盖 /api/ingest、/api/expand、/api/translate-text 的长度边界、/api/scan-pdfs 的深度/数量上限、/api/pdf/status、/api/citegraph、/api/test-llm。

- [ ] **Step 2（2–5 分钟）：确认红测**

Run:

~~~powershell
.\.venv\Scripts\python.exe -B -m unittest backend.tests.test_api_legacy_json.LegacyJsonApiTests.test_discovery_pdf_citation_and_llm_json_routes_match_node -v
~~~

Expected RED: 首个尚未接管路径返回 404；fake provider 明确禁止真实网络。

- [ ] **Step 3（2–5 分钟）：实现最小 adapters**

SearchCoordinator/LegacyAgentProvider 封装 ingest、expand、translate-text fallback、scan、citation 与 ping；scan 使用相同四层/2000 文件限制和路径错误语义；Route 只转换 wire。

- [ ] **Step 4（2–5 分钟）：重新运行 discovery/PDF/citation/LLM parity 测试并确认 GREEN**

Run:

~~~powershell
.\.venv\Scripts\python.exe -B -m unittest backend.tests.test_api_legacy_json.LegacyJsonApiTests.test_discovery_pdf_citation_and_llm_json_routes_match_node -v
~~~

Expected GREEN: 状态、字段、边界值与 Node golden 全等；provider fake 调用参数被精确断言。

- [ ] **Step 5（2–5 分钟）：写真实 FastAPI E2E 红测**

在 fastapi-parity.spec.ts 使用隔离 DB 与 fake providers 启动 Uvicorn，覆盖 workspace 论文列表/详情/note/PDF/review、一次 NDJSON 生成，以及 legacy 入口的列表/详情。

- [ ] **Step 6（2–5 分钟）：确认 E2E 红测**

Run:

~~~powershell
npm.cmd run e2e --prefix frontend -- --grep "FastAPI parity"
~~~

Expected RED: Playwright 报 FastAPI webServer/fixture 尚未接线或首个业务断言失败；不得连接 5173 上的用户进程。

- [ ] **Step 7（2–5 分钟）：接线隔离 Uvicorn fixture**

设置临时 DB、临时静态根、固定 fake provider、API_PROCESS_ROLE=api、OCR_ENABLED=0、OBSIDIAN_ENABLED=0；退出时等待 Uvicorn、fake worker 和浏览器完全结束。

- [ ] **Step 8（2–5 分钟）：重新运行 FastAPI parity E2E 并确认 GREEN**

Run:

~~~powershell
npm.cmd run e2e --prefix frontend -- --grep "FastAPI parity"
~~~

Expected GREEN: workspace 与 legacy 两套流程全部通过，React decoder 无 console error。

## Task 9：实现 candidate 进程单角色、role-scoped ownership、drain 与 rollback rehearsal

**Files:**
- Modify: backend/app/runtime.py
- Modify: backend/app/api/app.py
- Modify: backend/app/workers/processing_worker.py
- Modify: backend/app/workers/scheduler.py
- Create: backend/app/api/compat/__init__.py
- Create: backend/app/api/compat/database_identity.py
- Create: backend/app/cli/runtime_owner.py
- Create: backend/app/providers/runtime_lease.py
- Modify: Dockerfile
- Modify: docker-compose.yml
- Create: backend/tests/test_database_identity.py
- Create: backend/tests/test_runtime_ownership.py
- Create: backend/tests/test_candidate_container_contract.py
- Modify: docs/DATABASE.md

- [ ] **Step 0A（2–5 分钟）：写 database identity 与初始 Node owner marker 红测**

新增 `DatabaseIdentityTests.test_v1_lineage_is_stable_and_subject_is_file_instance_specific`、`DatabaseIdentityTests.test_p0_origin_receipt_is_exclusive_and_tamper_evident`、`DatabaseIdentityTests.test_live_identity_rejects_verified_origin_not_named_by_p0_receipt`、`RuntimeOwnershipTests.test_initialize_and_verify_owner_require_same_p0_receipt_sha`、`RuntimeOwnershipTests.test_initialize_node_owner_exclusive_creates_attested_node_active_marker`、`RuntimeOwnershipTests.test_initialize_node_owner_rejects_missing_multiple_python_live_or_existing_marker`、`RuntimeOwnershipTests.test_verify_node_owner_is_read_only_and_rejects_marker_origin_or_process_drift` 与 `RuntimeOwnershipTests.test_owner_rejects_same_basename_from_different_directory`。fixture 使用 P0 fixed receipt 及其 out-of-band file SHA、receipt 命名的 verified origin backup/Manifest、一个内容不同但同样可 verify 的新 backup、同名但位于另一目录的 `server.js`、同一 Live 文件正常行写入、文件 replacement、零/两个真实 Node process、非 loopback listener、wrong DB handle、任一 Live Python role、exact existing marker 和被篡改 marker；断言 databaseLineageId 只能由 receipt 重算，不能以 P4 新 backup 或新 receipt 重置。只有“receipt/path/file SHA 全等 + resolved absolute entrypoint/cwd/argv 全等 + 恰一 Node PID/loopback port/同一 resolved DB file handle + 无 Live Python”能 exclusive-create schema-versioned `node_active`；已存在 exact marker 只能由只读 verifier 接受，所有拒绝与 verify 路径都零 marker/lease/DB/provider 副作用。

- [ ] **Step 0B（2–5 分钟）：运行 identity/owner 初始化红测**

~~~powershell
.\.venv\Scripts\python.exe -B -m unittest backend.tests.test_database_identity.DatabaseIdentityTests.test_v1_lineage_is_stable_and_subject_is_file_instance_specific backend.tests.test_database_identity.DatabaseIdentityTests.test_p0_origin_receipt_is_exclusive_and_tamper_evident backend.tests.test_database_identity.DatabaseIdentityTests.test_live_identity_rejects_verified_origin_not_named_by_p0_receipt backend.tests.test_runtime_ownership.RuntimeOwnershipTests.test_initialize_and_verify_owner_require_same_p0_receipt_sha backend.tests.test_runtime_ownership.RuntimeOwnershipTests.test_initialize_node_owner_exclusive_creates_attested_node_active_marker backend.tests.test_runtime_ownership.RuntimeOwnershipTests.test_initialize_node_owner_rejects_missing_multiple_python_live_or_existing_marker backend.tests.test_runtime_ownership.RuntimeOwnershipTests.test_verify_node_owner_is_read_only_and_rejects_marker_origin_or_process_drift backend.tests.test_runtime_ownership.RuntimeOwnershipTests.test_owner_rejects_same_basename_from_different_directory -v
~~~

Expected RED: `database_identity`/`runtime_owner`/owner marker CAS seam 尚不存在；fixture/import/未实际制造两个 process 或 replacement 不算有效 RED。

- [ ] **Step 0C（2–5 分钟）：实现唯一 DatabaseEvidenceIdentityManifest v1 与 initialize-node-owner**

`databaseLineageId` 必须等于 P0 `OriginReceipt.databaseLineageId`，并从 receipt 的 canonical `{version,originBackupId,originManifestSha256,originLogicalSha256}` 独立重算；origin 只能是 receipt exact path/file SHA 命名且仍可独立 verify/restore-check 的 backup/Manifest，P4 禁止创建或选择另一份 receipt/origin。`subjectDatabaseId` 精确为 canonical `{version,databaseLineageId,subjectKind,resolvedPathHash,platformFileIdentity,parentBackupId,parentManifestSha256}` SHA-256。Windows file identity 使用 volume serial + file ID，POSIX 使用 device + inode；正常内容写入不改变 subject，文件 replacement 必须改变。`create-live-database-identity` 要求 `--database`、`--p0-origin-receipt`、`--expected-p0-origin-receipt-sha256`、`--origin-backup`、`--origin-manifest`、`--output` 六个 exact 参数，在任何输出/DB副作用前验证 receipt file/self hash、receipt path 与 pair 的 ID/hash/path，并独立重验 parent 后 exclusive-create typed manifest；manifest 固定记录 `originReceiptPath/originReceiptFileSha256/originReceiptSha256`，使用唯一 canonical UTF-8 bytes，禁止未知字段、BOM、替代序列化或非 canonical whitespace，使 raw manifest file SHA-256 可稳定重算和逐 byte 复验。`create-descendant-database-identity` 只沿用已验证 parent Live manifest 的同一 receipt anchor，并要求 exact database/parent backup/Manifest/output。两者都禁止 glob/latest/default DB，且新 backup 即使自身可 verify 也不能替代 receipt 命名的 origin。`initialize-node-owner` 接收 exact DatabaseEvidenceIdentityManifest、同一 receipt path/file SHA、retained pair、`runtimeNamespace=production`、owner marker path 与 resolved absolute `server.js` entrypoint path，重新验证 receipt/origin chain、Live file identity并平台级证明恰一 Node owner的 cwd/argv 确实指向该 exact script、loopback listener、同一 DB handle、无 Live Python role，再 exclusive-create marker；不连接可写 SQLite、不终止/重启进程、不接受 boolean override。

`verify-node-owner` 使用与初始化相同的 exact database identity、receipt path/file SHA、retained origin pair、namespace、resolved absolute entrypoint path 与 marker 参数，但 Interface 固定为 read-only：重新 hash marker、OriginReceipt 与两份 identity/backup manifests，重验 P0 origin、Live platform file identity、唯一 Node PID/cwd/argv/loopback port/DB handle 和零 Live Python role，返回 `verificationMode="read_only"`、ownerState、marker SHA、receipt SHA 与 identity IDs。它不得创建、覆盖、touch 或 CAS marker，不得创建 lease、打开可写 SQLite、停止/重启进程或接受“trust existing”布尔量；missing/changed marker、错误 receipt/origin、同 basename 不同目录 entrypoint、identity/file/process drift 均 exit 2。

- [ ] **Step 0D（2–5 分钟）：重新运行 identity/owner 初始化测试并确认 GREEN**

~~~powershell
.\.venv\Scripts\python.exe -B -m unittest backend.tests.test_database_identity.DatabaseIdentityTests.test_v1_lineage_is_stable_and_subject_is_file_instance_specific backend.tests.test_database_identity.DatabaseIdentityTests.test_p0_origin_receipt_is_exclusive_and_tamper_evident backend.tests.test_database_identity.DatabaseIdentityTests.test_live_identity_rejects_verified_origin_not_named_by_p0_receipt backend.tests.test_runtime_ownership.RuntimeOwnershipTests.test_initialize_and_verify_owner_require_same_p0_receipt_sha backend.tests.test_runtime_ownership.RuntimeOwnershipTests.test_initialize_node_owner_exclusive_creates_attested_node_active_marker backend.tests.test_runtime_ownership.RuntimeOwnershipTests.test_initialize_node_owner_rejects_missing_multiple_python_live_or_existing_marker backend.tests.test_runtime_ownership.RuntimeOwnershipTests.test_verify_node_owner_is_read_only_and_rejects_marker_origin_or_process_drift backend.tests.test_runtime_ownership.RuntimeOwnershipTests.test_owner_rejects_same_basename_from_different_directory -v
~~~

Expected GREEN: 8 tests OK；P0 receipt-anchored lineage、subject 语义、exclusive-create、只读 owner 复验、absolute entrypoint/cwd/argv、进程/端口/DB handle attestation 与零副作用均可证明。

- [ ] **Step 0E（2–5 分钟）：写“identity 已落盘、marker 初始化失败”安全续跑单行为红测**

只新增 `RuntimeOwnershipTests.test_exact_live_identity_without_marker_resumes_after_read_only_verification`。fixture 先让 `create-live-database-identity` exclusive-create 成功，再注入一次位于 marker exclusive-create 之前的 `initialize-node-owner` 失败，制造“exact identity 存在、marker 缺失”的 crash boundary；保存 identity 的 raw bytes、file SHA-256、platform file identity 与 mtime。第二次执行必须先经新的只读 `verify-live-database-identity` Interface 严格复验 P0 OriginReceipt path/file SHA/self hash、receipt 命名的 exact backup/Manifest、canonical manifest raw bytes/SHA、Live resolved path/platform identity、`subjectKind=live`、`databaseLineageId` 与 `subjectDatabaseId`，然后才允许复用原 identity 初始化 marker。断言第二次不调用 create、不删除/覆盖/touch identity，最终 identity bytes/SHA/file identity/mtime 全等且 marker 只创建一次；malformed、非 canonical、wrong receipt/lineage/subject/path/platform identity 或 marker-only 状态都在任何 create/delete/marker/lease/DB/provider 副作用前 fail closed。

- [ ] **Step 0F（2–5 分钟）：运行安全续跑单行为测试并确认有效 RED**

~~~powershell
.\.venv\Scripts\python.exe -B -m unittest backend.tests.test_runtime_ownership.RuntimeOwnershipTests.test_exact_live_identity_without_marker_resumes_after_read_only_verification -v
~~~

Expected RED: `verify-live-database-identity`/只读 verifier 尚不存在，或当前 bootstrap 把 exact identity + missing marker 一律当作不可恢复 partial state；测试必须已经成功制造第一次 initialization failure，且 identity 文件仍为完整 canonical manifest。fixture/import、无法 exclusive-create identity 或失败发生在 identity 落盘之前都不是有效 RED。

- [ ] **Step 0G（2–5 分钟）：最小实现只读 Live identity verifier 与 crash-safe resume 分支**

在 `backend/app/api/compat/database_identity.py` 增加唯一 `LiveDatabaseIdentityVerifier.verify_existing(...) -> VerifiedDatabaseEvidenceIdentity` Interface，并由 CLI `runtime_owner verify-live-database-identity --database <exact-live> --database-identity-manifest <exact-existing> --p0-origin-receipt <exact> --expected-p0-origin-receipt-sha256 <sha> --origin-backup <exact> --origin-manifest <exact>` 适配。它以 no-follow/read-only handle 读取既有 identity，要求 strict schema 与 canonical raw bytes 完全一致，计算并返回 `identityManifestFileSha256`，重新计算 receipt anchor、lineage、resolved absolute path hash、platform file identity 与 subject；同时比较读取前后的 handle identity/bytes/SHA，任何 drift exit 2。它不得 create、truncate、rename、delete、touch 或 CAS identity/marker，不得创建 lease、连接可写 SQLite、探测 provider 或接受 trust/force 开关。

resume 决策只允许四态：`identity missing + marker missing` 才调用 create；`exact identity + marker missing` 必须先调用上述只读 verifier，成功后用原路径调用 `initialize-node-owner`；`exact identity + exact marker` 只调用 `verify-node-owner`；`identity missing + marker present`、identity 非 exact/partial/wrong 或 marker 非 exact 一律 fail closed。失败不得自动清理任何既有文件；因此 initialize 失败后的下次运行能安全续跑，而不是重建 lineage identity。

- [ ] **Step 0H（2–5 分钟）：用完全相同目标重新运行并确认 GREEN**

~~~powershell
.\.venv\Scripts\python.exe -B -m unittest backend.tests.test_runtime_ownership.RuntimeOwnershipTests.test_exact_live_identity_without_marker_resumes_after_read_only_verification -v
~~~

Expected GREEN: 同一个 unittest target reports 1 test and OK；第一次失败后保留的 exact identity 被只读复验并复用，第二次只创建 marker，identity bytes/SHA/platform identity/mtime 不变；所有 wrong/partial 四态零副作用拒绝。

- [ ] **Step 0I（按实际时长）：在仍由 Node 拥有的 Live runtime 原子建立 lineage 与 node_active marker**

以下命令必须作为同一个 PowerShell block 原样执行。operator 只提供 P0 fixed receipt path 和 P0 evidence 中独立记录的 receipt file SHA；backup/Manifest exact path 必须从 receipt strict decode 后取得，不能再由环境变量或实施报告手抄。block 对 receipt/path/hash/type/unknown-field 漂移、相对猜测、glob/latest、独立 verify/restore-check mismatch 或任一 raw native failure 立即停止。它不创建 P4 lineage origin、不写 Live SQLite；marker 已存在时只走只读 owner verifier，identity 已存在而 marker 缺失时只读复验并复用 identity，绝不覆盖或删除：

~~~powershell
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$p4LiveDb = (Resolve-Path -LiteralPath 'data/app.db').Path
$p4ExpectedEntrypointPath = (Resolve-Path -LiteralPath 'server.js').Path
$p0OriginReceiptPath = (Resolve-Path -LiteralPath 'data/compatibility/runtime/p0-origin-receipt-v1.json').Path
$p0OriginReceiptShaInput = [Environment]::GetEnvironmentVariable('P0_ORIGIN_RECEIPT_SHA256', 'Process')
if ([string]::IsNullOrWhiteSpace($p0OriginReceiptShaInput) -or $p0OriginReceiptShaInput -notmatch '^[0-9a-f]{64}$') { throw 'P0 evidence exact lowercase origin receipt file SHA-256 is required.' }
$p4ReceiptVerifyJson = & .\.venv\Scripts\python.exe -B -m backend.app.cli.database_backup verify-origin-receipt --receipt $p0OriginReceiptPath --expected-receipt-file-sha256 $p0OriginReceiptShaInput
$p4ReceiptVerifyExit = $LASTEXITCODE
if ($p4ReceiptVerifyExit -ne 0) { throw 'P0 OriginReceipt verification failed.' }
$p4ReceiptVerify = $p4ReceiptVerifyJson | ConvertFrom-Json
$p0RetainedOriginBackupPath = (Resolve-Path -LiteralPath ([string]$p4ReceiptVerify.backupPath)).Path
$p0RetainedOriginManifestPath = (Resolve-Path -LiteralPath ([string]$p4ReceiptVerify.manifestPath)).Path
$p4OriginVerifyJson = & .\.venv\Scripts\python.exe -B -m backend.app.cli.database_backup verify --backup $p0RetainedOriginBackupPath --manifest $p0RetainedOriginManifestPath
$p4OriginVerifyExit = $LASTEXITCODE
if ($p4OriginVerifyExit -ne 0) { throw 'Retained P0 lineage origin verify failed.' }
$p4OriginVerify = $p4OriginVerifyJson | ConvertFrom-Json
if (-not $p4OriginVerify.ok) { throw 'Retained P0 lineage origin did not report ok.' }
$p4OriginRestoreJson = & .\.venv\Scripts\python.exe -B -m backend.app.cli.database_backup restore-check --backup $p0RetainedOriginBackupPath --manifest $p0RetainedOriginManifestPath --output-directory data/backups/restore-checks
$p4OriginRestoreExit = $LASTEXITCODE
if ($p4OriginRestoreExit -ne 0) { throw 'Retained P0 lineage origin restore-check failed.' }
$p4OriginRestore = $p4OriginRestoreJson | ConvertFrom-Json
if (-not $p4OriginRestore.ok -or $p4OriginRestore.logicalSha256 -ne $p4OriginVerify.logicalSha256) { throw 'Retained P0 lineage origin restore-check mismatch.' }
$p4LiveIdentityPath = 'data/compatibility/runtime/live-database-identity-v1.json'
$p4OwnerMarkerPath = 'data/compatibility/runtime/production-owner.json'
$p4IdentityExists = Test-Path -LiteralPath $p4LiveIdentityPath -PathType Leaf
$p4MarkerExists = Test-Path -LiteralPath $p4OwnerMarkerPath -PathType Leaf
if ($p4MarkerExists -and -not $p4IdentityExists) { throw 'Owner marker exists without the exact Live database identity; no file was changed.' }
if (-not $p4IdentityExists) {
  $p4LiveIdentityJson = & .\.venv\Scripts\python.exe -B -m backend.app.cli.runtime_owner create-live-database-identity --database $p4LiveDb --p0-origin-receipt $p0OriginReceiptPath --expected-p0-origin-receipt-sha256 $p0OriginReceiptShaInput --origin-backup $p0RetainedOriginBackupPath --origin-manifest $p0RetainedOriginManifestPath --output $p4LiveIdentityPath
  $p4LiveIdentityExit = $LASTEXITCODE
  if ($p4LiveIdentityExit -ne 0) { throw 'P4 Live database identity creation failed.' }
  $p4LiveIdentity = $p4LiveIdentityJson | ConvertFrom-Json
  if (-not $p4LiveIdentity.ok -or $p4LiveIdentity.subjectKind -ne 'live' -or $p4LiveIdentity.manifestPath -ne $p4LiveIdentityPath) { throw 'P4 Live database identity response mismatch.' }
  $p4IdentityExists = $true
}
if (-not $p4MarkerExists) {
  $p4IdentityVerifyJson = & .\.venv\Scripts\python.exe -B -m backend.app.cli.runtime_owner verify-live-database-identity --database $p4LiveDb --database-identity-manifest $p4LiveIdentityPath --p0-origin-receipt $p0OriginReceiptPath --expected-p0-origin-receipt-sha256 $p0OriginReceiptShaInput --origin-backup $p0RetainedOriginBackupPath --origin-manifest $p0RetainedOriginManifestPath
  $p4IdentityVerifyExit = $LASTEXITCODE
  if ($p4IdentityVerifyExit -ne 0) { throw 'Existing or newly created Live database identity did not pass read-only verification; no file was changed.' }
  $p4IdentityVerify = $p4IdentityVerifyJson | ConvertFrom-Json
  if (-not $p4IdentityVerify.ok -or $p4IdentityVerify.verificationMode -ne 'read_only' -or $p4IdentityVerify.subjectKind -ne 'live' -or ([string]$p4IdentityVerify.identityManifestFileSha256) -notmatch '^[0-9a-f]{64}$') { throw 'Live database identity verification response mismatch.' }
  $p4OwnerJson = & .\.venv\Scripts\python.exe -B -m backend.app.cli.runtime_owner initialize-node-owner --database-identity-manifest $p4LiveIdentityPath --p0-origin-receipt $p0OriginReceiptPath --expected-p0-origin-receipt-sha256 $p0OriginReceiptShaInput --origin-backup $p0RetainedOriginBackupPath --origin-manifest $p0RetainedOriginManifestPath --runtime-namespace production --expected-entrypoint-path $p4ExpectedEntrypointPath --owner-marker $p4OwnerMarkerPath
  $p4OwnerExit = $LASTEXITCODE
  if ($p4OwnerExit -ne 0) { throw 'P4 Node owner initialization failed.' }
  $p4Owner = $p4OwnerJson | ConvertFrom-Json
  if (-not $p4Owner.ok -or $p4Owner.ownerState -ne 'node_active' -or $p4Owner.ownerMarkerPath -ne $p4OwnerMarkerPath) { throw 'P4 Node owner initialization response mismatch.' }
}
$p4OwnerVerifyJson = & .\.venv\Scripts\python.exe -B -m backend.app.cli.runtime_owner verify-node-owner --database-identity-manifest $p4LiveIdentityPath --p0-origin-receipt $p0OriginReceiptPath --expected-p0-origin-receipt-sha256 $p0OriginReceiptShaInput --origin-backup $p0RetainedOriginBackupPath --origin-manifest $p0RetainedOriginManifestPath --runtime-namespace production --expected-entrypoint-path $p4ExpectedEntrypointPath --owner-marker $p4OwnerMarkerPath
$p4OwnerVerifyExit = $LASTEXITCODE
if ($p4OwnerVerifyExit -ne 0) { throw 'P4 Node owner read-only verification failed.' }
$p4OwnerVerify = $p4OwnerVerifyJson | ConvertFrom-Json
if (-not $p4OwnerVerify.ok -or $p4OwnerVerify.verificationMode -ne 'read_only' -or $p4OwnerVerify.ownerState -ne 'node_active') { throw 'P4 Node owner evidence is not an exact read-only match.' }
~~~

Expected: OriginReceipt file/self hash、strict schema 与 exact backup/Manifest chain 重新通过，databaseLineageId 可由 receipt 重算且与 P0 evidence 一致；P4 没有创建任何新 receipt/origin。fresh 路径 exclusive-create exact Live identity、只读复验后创建 `node_active` marker；exact identity + missing marker 路径复验 canonical manifest bytes/SHA、Live platform identity/resolved path/subject/lineage 后复用原 identity 完成 initialize；existing-marker 路径只读复验 marker bytes/SHA/identity/receipt/origin/absolute-entrypoint/process。wrong/non-canonical/partial identity、marker-only、receipt/origin/process drift 均 fail closed 且不覆盖、不删除、不 touch 既有文件；initialize 失败时 identity 保留供下次安全续跑，Live DB、lease、PID/port 与 Node metadata 始终不变。

- [ ] **Step 1（2–5 分钟）：写进程单角色与 singleton role 红测**

新增 `RuntimeOwnershipTests.test_candidate_process_requires_exactly_one_role`、`test_candidate_api_worker_scheduler_coexist_in_same_namespace`、`test_candidate_worker_rejects_second_owner_in_same_namespace`、`test_candidate_scheduler_rejects_second_owner_in_same_namespace`、`test_candidate_api_binds_random_loopback_only` 与 `test_p4_refuses_live_python_roles_while_node_is_production_owner`。每个 OS 进程必须且只能选择 `api|worker|scheduler` 中一个，但同一 candidate namespace 的三个独立进程必须能并存；只对重复 Worker 或重复 Scheduler owner fail-fast。检查 runtime argv 与 socket address 不会是 `0.0.0.0`/`::`，并证明任何 `environment=live` Python role 在 P4 都于 socket/DB/lease/provider 前返回 `P4_LIVE_PROMOTION_NOT_AUTHORIZED`。

- [ ] **Step 2（2–5 分钟）：确认红测**

Run:

~~~powershell
.\.venv\Scripts\python.exe -B -m unittest backend.tests.test_runtime_ownership.RuntimeOwnershipTests.test_candidate_process_requires_exactly_one_role backend.tests.test_runtime_ownership.RuntimeOwnershipTests.test_candidate_api_worker_scheduler_coexist_in_same_namespace backend.tests.test_runtime_ownership.RuntimeOwnershipTests.test_candidate_worker_rejects_second_owner_in_same_namespace backend.tests.test_runtime_ownership.RuntimeOwnershipTests.test_candidate_scheduler_rejects_second_owner_in_same_namespace backend.tests.test_runtime_ownership.RuntimeOwnershipTests.test_candidate_api_binds_random_loopback_only backend.tests.test_runtime_ownership.RuntimeOwnershipTests.test_p4_refuses_live_python_roles_while_node_is_production_owner -v
~~~

Expected RED: 当前 runtime 没有 candidate/live environment guard、稳定 subject identity、role-scoped Worker/Scheduler leases，或同进程多角色拒绝；若三种不同 role 被一把全局锁互相阻塞，也必须 RED。

- [ ] **Step 3（2–5 分钟）：实现角色 fail-fast**

`API_PROCESS_ROLE` 每个 OS 进程必须且只能选择 `api|worker|scheduler` 中一个；这是进程职责互斥，不表示同一 runtime namespace 内三种 role 彼此互斥。同一 `(environment,databaseLineageId,subjectDatabaseId,runtimeNamespace)` 中，api、worker、scheduler 独立进程必须能够同时运行。所有 role 只消费 Step 0C 的 typed `DatabaseEvidenceIdentityManifest`，不得自行用 path/current hash 重算另一种 subject ID；candidate identity 必须是 verified restore/temp descendant 且 parent chain 可重验，不能冒充 Live subject。Worker 与 Scheduler 是 singleton roles，分别以 `(environment,databaseLineageId,subjectDatabaseId,runtimeNamespace,role)` 建立 exclusive-create/OS lock；payload 记录 role、runtimeNamespace、owner_id、pid、started_at、expires_at、heartbeat。重复 Worker 返回 `WORKER_ALREADY_OWNED`，重复 Scheduler 返回 `SCHEDULER_ALREADY_OWNED`；API 不获取 singleton runtime lease，不同 role 不互相阻塞。Live `node_active` marker 是只读前置证据；P4 的 live authorization adapter 固定拒绝，P4 不释放、替换或接管 marker。只有同 role candidate pid 已不存在且 expires_at 已过期时才可回收 stale lock。

- [ ] **Step 4（2–5 分钟）：用与 Step 2 完全相同的 role targets 确认 GREEN**

Run:

~~~powershell
.\.venv\Scripts\python.exe -B -m unittest backend.tests.test_runtime_ownership.RuntimeOwnershipTests.test_candidate_process_requires_exactly_one_role backend.tests.test_runtime_ownership.RuntimeOwnershipTests.test_candidate_api_worker_scheduler_coexist_in_same_namespace backend.tests.test_runtime_ownership.RuntimeOwnershipTests.test_candidate_worker_rejects_second_owner_in_same_namespace backend.tests.test_runtime_ownership.RuntimeOwnershipTests.test_candidate_scheduler_rejects_second_owner_in_same_namespace backend.tests.test_runtime_ownership.RuntimeOwnershipTests.test_candidate_api_binds_random_loopback_only backend.tests.test_runtime_ownership.RuntimeOwnershipTests.test_p4_refuses_live_python_roles_while_node_is_production_owner -v
~~~

Expected GREEN: unittest summary reports 6 tests and OK；同一 candidate namespace 恰可并存一个 API、一个 Worker 与一个 Scheduler，Worker/Scheduler 各自最多一个 owner，candidate API 只监听 OS-assigned IPv4 loopback port，Live Node owner/port 全程不变。IPv6 `::1` 作为单独 opt-in loopback 测试，不与 dual-stack wildcard `::` 混淆。

- [ ] **Step 5（2–5 分钟）：先写 candidate drain 与 resolved container configuration 行为测试**

新增 `RuntimeOwnershipTests.test_candidate_drain_quiesces_api_worker_scheduler_and_preserves_live_node` 与 `RuntimeOwnershipTests.test_candidate_drain_timeout_cancels_only_candidate_provider_and_preserves_committed_artifact`。第一项用 barrier 保持一个 API request、一个 Worker transaction 与一个 Scheduler tick 在途，发出 drain 后断言：API readiness 先进入 `draining` 且 barrier 后的新请求不进入 handler；Worker 不再 `claim_next`，但提交在途 transaction 后才释放 worker lease；Scheduler 不再开始新 tick，持久化当前 tick 的 `next_run` 后才释放 scheduler lease；最后三个 role 都停止。第二项将 provider 卡在可取消 scope 中并预先提交一个 artifact，超时后只取消 candidate provider scope，已提交 artifact 不回滚，job 保持可恢复的持久状态。两项测试从 drain 前到结束持续读取 Live Node PID/loopback port/DB handle 与 owner-marker exact bytes/SHA/mtime，任何停止、重启、touch 或 ownership drift 都失败。

新增 `CandidateContainerContractTests.test_resolved_default_compose_keeps_node_as_live_owner`、`test_resolved_p4_candidate_profile_is_isolated_role_scoped_and_loopback_only` 与 `test_resolved_candidate_build_targets_exist_and_match_role_commands`。测试必须调用本机真实 `docker compose --profile p4-candidate config --format json` 取得 fully resolved configuration，而不是 grep YAML 或使用手写 fixture；再以 Docker/BuildKit 的 Dockerfile check 解析 resolved `build.target`。断言默认 profile 仍只有原 Node Live entrypoint；candidate profile 恰有 api/worker/scheduler 三个单角色 service，使用同一非 Live descendant DB identity 与 candidate runtime namespace，分别解析为 `environment=candidate`，不挂载 Live DB/runtime/owner marker 为可写，不出现 production owner value，API container 只监听 `127.0.0.1` 且宿主端口由 OS 分配，Worker/Scheduler 命令与 lease role 一一对应；resolved target 必须存在 `fastapi-candidate` 与 `frozen-node` stages。Docker/Compose 不可用或 config 无法 resolve 是环境 blocker，不得冒充行为 RED 或 skip 后声称 GREEN。

- [ ] **Step 6（2–5 分钟）：用完整且固定的 drain/container targets 确认有效 RED**

~~~powershell
.\.venv\Scripts\python.exe -B -m unittest backend.tests.test_runtime_ownership.RuntimeOwnershipTests.test_candidate_drain_quiesces_api_worker_scheduler_and_preserves_live_node backend.tests.test_runtime_ownership.RuntimeOwnershipTests.test_candidate_drain_timeout_cancels_only_candidate_provider_and_preserves_committed_artifact backend.tests.test_candidate_container_contract.CandidateContainerContractTests.test_resolved_default_compose_keeps_node_as_live_owner backend.tests.test_candidate_container_contract.CandidateContainerContractTests.test_resolved_p4_candidate_profile_is_isolated_role_scoped_and_loopback_only backend.tests.test_candidate_container_contract.CandidateContainerContractTests.test_resolved_candidate_build_targets_exist_and_match_role_commands -v
~~~

Expected RED: drain tests 精确因 quiesce API/Worker/Scheduler 协议或 timeout cancellation 尚未实现而失败；container tests 精确因 `p4-candidate` services/build targets/隔离 guard 尚未写入 resolved config 而失败。Docker daemon/Compose 缺失、fixture barrier 未进入、Live Node probe 未启动或任一 import/JSON parse 错误都不是有效 RED；记录首个缺失行为后才可继续。

- [ ] **Step 7（2–5 分钟）：最小实现 candidate drain，并在红灯之后修改 Dockerfile/Compose**

在 `backend/app/runtime.py` 实现单一 `CandidateDrainCoordinator.drain(deadline)`：API 先原子切换 readiness/admission gate 到 `draining`，拒绝 barrier 后的新 handler admission并等待在途请求；Worker 原子停止 `claim_next`，等待在途 transaction settle 后释放 worker lease；Scheduler 原子停止新 tick，提交已开始 tick 的 `next_run` 后释放 scheduler lease。deadline 到期只取消明确属于 candidate 的 provider cancel scope，不回滚已提交 artifact，不碰 Live Node/DB/marker。`backend/app/api/app.py`、`processing_worker.py` 与 `scheduler.py` 只适配该 Interface，不各写一套 drain 状态机。

仅在上述 RED 留证后修改 Dockerfile 与 docker-compose.yml：保留 frozen-node rollback candidate stage并增加 `fastapi-candidate` runtime；默认 Live service/entrypoint 仍为 Node，`p4-candidate` profile 恰启动 api、worker、scheduler 各一个 service，共享同一隔离 DB identity 与 candidate runtime namespace。Worker 与 Scheduler 分别取得自己的 role-scoped singleton lease，API 不取得 singleton lease并绑定 `127.0.0.1` 与随机宿主端口；禁止 `0.0.0.0`/`::`、Live DB path、Live runtime directory、可写 production owner marker与 production owner value。未来若用户明确选择 LAN 暴露，另立安全计划处理认证、TLS/反向代理与 CSRF；本阶段不提供远程或 Live promotion 示例。

- [ ] **Step 8（2–5 分钟）：用与 Step 6 完全相同的 targets 确认 GREEN**

~~~powershell
.\.venv\Scripts\python.exe -B -m unittest backend.tests.test_runtime_ownership.RuntimeOwnershipTests.test_candidate_drain_quiesces_api_worker_scheduler_and_preserves_live_node backend.tests.test_runtime_ownership.RuntimeOwnershipTests.test_candidate_drain_timeout_cancels_only_candidate_provider_and_preserves_committed_artifact backend.tests.test_candidate_container_contract.CandidateContainerContractTests.test_resolved_default_compose_keeps_node_as_live_owner backend.tests.test_candidate_container_contract.CandidateContainerContractTests.test_resolved_p4_candidate_profile_is_isolated_role_scoped_and_loopback_only backend.tests.test_candidate_container_contract.CandidateContainerContractTests.test_resolved_candidate_build_targets_exist_and_match_role_commands -v
~~~

Expected GREEN: 同一五个 unittest targets 全部 OK；admission/claim/tick 按顺序 quiesce、在途事务与 `next_run` 落盘后释放各自 lease、timeout 只取消 candidate provider；resolved default Compose 仍由 Node 拥有 Live，resolved candidate config 与 Dockerfile targets 满足单角色、隔离、loopback 和只读 owner evidence 契约，Live Node evidence 全程不变。

- [ ] **Step 9（2–5 分钟）：写切换与回滚 runbook**

docs/DATABASE.md 固定 P4 rehearsal 顺序：verified backup → restore copy 固定 revision migration/hash → 保持 Live Node HTTP/worker/scheduler 运行且记录 owner evidence → 在隔离 namespace 启动 Python candidate roles → candidate smoke → candidate drain/停止 → 启动 frozen Node rollback candidate 对同一隔离副本 smoke → 停止 rollback candidate。不得停止 Live Node、不得对 Live DB 启动 Python、不得修改 production profile；正式顺序只链接 P6 shutdown gate。

- [ ] **Step 10（2–5 分钟）：运行 Task 9 回归**

Run:

~~~powershell
.\.venv\Scripts\python.exe -B -m unittest backend.tests.test_runtime_ownership -v
if ($LASTEXITCODE -ne 0) { throw 'P4 runtime ownership regression failed.' }
.\.venv\Scripts\python.exe -B -m unittest backend.tests.test_candidate_container_contract -v
if ($LASTEXITCODE -ne 0) { throw 'P4 resolved candidate container contract regression failed.' }
~~~

Expected: 两个 modules 全部 OK；测试结束没有 lease、thread 或 child process 残留，resolved Compose/Docker config 仍符合已绿的同一契约。

## Task 10：P4 固定 revision migration rehearsal、兼容门禁与 candidate rollback

**Files:**
- Create: backend/app/api/compat/schema_inventory.py
- Create: backend/app/cli/schema_inventory.py
- Create: backend/tests/test_schema_inventory.py
- Modify: backend/app/cli/runtime_owner.py
- Modify: backend/tests/test_runtime_ownership.py
- Modify: backend/tests/test_http_contract_inventory.py
- Modify: backend/tests/test_api_v2.py
- Modify: docs/DATABASE.md
- Verify only: backend/migrations/versions/20260807_01_domain_data_foundation.py
- Verify only: backend/migrations/versions/20260807_02_processing_queue_ocr.py
- Verify only: backend/migrations/versions/20260807_03_source_consumers_search.py

- [ ] **Step 0A（2–5 分钟）：写固定 inventory 完整性与 strict compare 红测**

新增 `SchemaInventoryTests.test_inventory_requires_exact_p4_p5_object_set` 与 `SchemaInventoryTests.test_strict_compare_rejects_missing_or_changed_legacy_aux_spec_or_fts_object`。固定集合精确为 12 legacy tables `papers|progress|paper_reviews|notes|favorites|translations|paper_vectors|cite_edges|ingest_jobs|job_candidates|job_schedules|schema_migrations`、P1 五主表、P2 `paper_artifact_heads|processing_job_events|ocr_page_checkpoints`、P3 `document_chunk_embeddings|artifact_translation_checkpoints`、唯一 `alembic_version=20260807_03` 与 `document_chunks_fts` virtual SQL/logical content/external-content rowid join。`processing_jobs` expected ordered columns 必须逐项等于 `id|paper_id|job_type|source_mode|status|progress_json|attempt|max_attempts|idempotency_key|error_code|error_message|created_at|started_at|finished_at|cancelled_at|source_document_id|artifact_id|spec_json|available_at|lease_owner|lease_token|lease_expires_at|heartbeat_at|cancel_requested_at|result_json|updated_at|retry_of_job_id|retry_sequence`，其中 `spec_json` 必须是 P2 冻结的 non-null canonical JobSpec source；固定 fingerprints 同时包含全列 `processingJobs` ordered projection 与只覆盖 `(id,spec_json)` 的 `processingJobSpecs`，两者 count 都等于 `processing_jobs` row count，每条 spec 都 strict decode但正文不进入 inventory/log。

trigger 集合必须恰为五个 exact names：`processing_jobs_spec_guard_insert|processing_jobs_spec_guard_update|document_chunks_fts_ai|document_chunks_fts_ad|document_chunks_fts_au`。mutation fixtures 必须分别删除、重命名或改变任一 expected column/constraint/table/aux PK/trigger SQL，移除或重排 `spec_json` projection，逐 byte 篡改 spec、改成语义等价但 non-canonical JSON、制造 spec/row target mismatch，交换两个 spec guard 的 INSERT/UPDATE 语义，增加第六个 lookalike trigger，改变旧表一行，破坏 FTS join/hash，或传错 DatabaseEvidenceIdentityManifest；每种都必须被固定 schema/column/SQL/content hash 与 behavior oracle 拒绝。只比较 before/after、只检查“任意五个 trigger”或仍只检查三个 FTS trigger 都不能通过。

- [ ] **Step 0B（2–5 分钟）：运行 inventory 红测**

~~~powershell
.\.venv\Scripts\python.exe -B -m unittest backend.tests.test_schema_inventory.SchemaInventoryTests.test_inventory_requires_exact_p4_p5_object_set backend.tests.test_schema_inventory.SchemaInventoryTests.test_strict_compare_rejects_missing_or_changed_legacy_aux_spec_or_fts_object -v
~~~

Expected RED: schema inventory module/CLI 尚未冻结 `processing_jobs.spec_json` ordered projections/strict decode、expected column/schema SQL hash与五个 exact trigger，或缺失既有 table/FTS inventory；fixture 未真正改变 spec bytes/row binding/column/trigger SQL，或因拼写/import 失败不算有效 RED。

- [ ] **Step 0C（2–5 分钟）：实现固定 inventory capture/compare**

`schema_inventory capture` 必须接收 `--database`、`--database-identity-manifest`、`--output` 三个 exact 参数，以 mode=ro/query_only 单 transaction 产生 versioned canonical JSON：逐表 count/PK-set/row hash、固定 ordered column metadata、normalized schema SQL hash、Alembic、FTS evidence、lineage/subject/parent chain与 exclusive-created output。对 `processing_jobs` 还必须输出不含 payload 的 `processingJobs` count/hash、`processingJobSpecs=(id,spec_json)` count/hash、strict-decode count/error row id，并要求两项 count 与 table count 全等；ordered columns、`spec_json TEXT NOT NULL`/约束与 normalized table SQL hash必须匹配上述固定 revision contract，不能从被测数据库动态学习 expected 值。

trigger inventory 对五个固定对象 `processing_jobs_spec_guard_insert|processing_jobs_spec_guard_update|document_chunks_fts_ai|document_chunks_fts_ad|document_chunks_fts_au` 分别记录并校验 expected normalized SQL SHA-256。两个 spec guard 的 valid insert/update 接受、non-canonical/spec-row mismatch 拒绝且原 row 不变，以及三个 FTS trigger 的 insert/delete/update logical/join behavior oracle，只能由本切片测试在 disposable mutation clone 上执行并冻结 `behaviorContractVersion`；mode=ro/query_only 的 operational capture 只校验 target 的 exact name/SQL SHA 并记录该已冻结 contract version，绝不对目标 DB 执行 oracle DML。expected hashes 来自冻结的 `20260807_02`/`20260807_03` migration contract并由 test constants 锁定，不能由当前 `sqlite_schema` 自我批准。不能用 `COUNT(trigger)=5`、before/after 恰好相等或只认三个 FTS names 代替 exact name/SQL/behavior。`compare --before <exact> --after <exact>` 先独立验证两份 inventory 都满足固定 contract，再要求同一 databaseLineageId/subjectDatabaseId、固定对象集合、spec counts/hashes 与所有 schema/content hashes/contract versions 全等；任一缺失、重命名、额外 lookalike 或差异 exit 2，不接受 glob/latest/缺参数/手写 pass。

- [ ] **Step 0D（2–5 分钟）：重新运行 inventory 测试并确认 GREEN**

~~~powershell
.\.venv\Scripts\python.exe -B -m unittest backend.tests.test_schema_inventory.SchemaInventoryTests.test_inventory_requires_exact_p4_p5_object_set backend.tests.test_schema_inventory.SchemaInventoryTests.test_strict_compare_rejects_missing_or_changed_legacy_aux_spec_or_fts_object -v
~~~

Expected GREEN: 2 tests OK；`processing_jobs` 28-column projection 含 non-null `spec_json`，`processingJobs`/`processingJobSpecs` count/hash/strict decode 与五个 exact trigger name/SQL hash/behavior 全部固定；每个 missing/changed/spec-tampered fixture 均返回稳定 code，capture 对 DB bytes/mtime/sidecars/total_changes 为零影响。

- [ ] **Step 1（2–5 分钟）：确认 P4 不新增 schema**

运行 Alembic heads 并记录唯一 head 20260807_03；P1 20260807_01、P2 20260807_02、P3 20260807_03 是顺序 additive revisions。固定 revision projection 明确要求 `20260807_02` 为 `processing_jobs` 增加上述 P2 columns（含 non-null `spec_json`）并创建 `processing_jobs_spec_guard_insert|processing_jobs_spec_guard_update`，`20260807_03` 保留它们并增加 `document_chunks_fts_ai|document_chunks_fts_ad|document_chunks_fts_au`，所以最终不是三个而是五个 exact triggers。P4 不创建新 revision，不把 route import 变成 create_all。

Run:

~~~powershell
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$p4HeadsRaw = @(& .\.venv\Scripts\python.exe -B -m alembic -c backend/alembic.ini heads)
$p4HeadsExit = $LASTEXITCODE
if ($p4HeadsExit -ne 0) { throw "P4 alembic heads failed with exit code $p4HeadsExit." }
$p4Heads = @($p4HeadsRaw | ForEach-Object { ([string]$_).Trim() } | Where-Object { $_ -ne '' })
if ($p4Heads.Count -ne 1 -or $p4Heads[0] -ne '20260807_03 (head)') {
  throw "P4 requires exactly one Alembic head equal to 20260807_03 (head); observed: $($p4Heads -join ' | ')."
}
~~~

Expected: raw exit 0；全部非空输出总数恰为 1 且唯一值为 `20260807_03 (head)`。目标 head 外再出现任何额外 head 也必须失败，禁止先过滤目标值再计数。

- [ ] **Step 2（2–5 分钟）：在 P0 恢复副本演练 upgrade**

创建并验证 fresh P4 snapshot，再从 restore-check JSON 解析隔离 app.db 并通过 P4 Live identity/精确 backup/Manifest 创建 descendant `DatabaseEvidenceIdentityManifest`。显式验证路径位于 restore-validation 目录且不等于 Live，随后仅在 try/finally 生命周期内设置 DB_PATH；执行前必须证明 `alembic current` 已经精确为唯一 `20260807_03 (head)`，P4 不接受或补做 P1/P2 revision。migration 前后分别 capture fixed inventory 并 strict compare；两份 capture 都必须独立通过 `processing_jobs` 28-column contract、`processingJobs`/`processingJobSpecs` count/hash/strict decode、两个 spec guard + 三个 FTS trigger 的 exact name/SQL hash/behavior gate，不能因为 before/after 同样缺少 `spec_json` 或同样只有三个 triggers 而相等通过。Task 10 Step 2–8 是同一个 operator session；每个 block 使用 strict mode，未定义变量立即失败。命令不得动态解析 symbolic head：

Run:

~~~powershell
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$p4CreateJson = & .\.venv\Scripts\python.exe -B -m backend.app.cli.database_backup create --database data/app.db --output-directory data/backups --label pre-p4-fastapi
$p4CreateExit = $LASTEXITCODE
if ($p4CreateExit -ne 0) { throw "P4 backup create failed with exit code $p4CreateExit." }
$p4Create = $p4CreateJson | ConvertFrom-Json
if (-not $p4Create.ok) { throw 'P4 backup create JSON did not report success.' }
$p4VerifyJson = & .\.venv\Scripts\python.exe -B -m backend.app.cli.database_backup verify --backup $p4Create.backupPath --manifest $p4Create.manifestPath
$p4VerifyExit = $LASTEXITCODE
if ($p4VerifyExit -ne 0) { throw "P4 backup verify failed with exit code $p4VerifyExit." }
$p4Verify = $p4VerifyJson | ConvertFrom-Json
if (-not $p4Verify.ok -or $p4Verify.logicalSha256 -ne $p4Create.logicalSha256) { throw 'P4 backup verify mismatch.' }
$p4RestoreJson = & .\.venv\Scripts\python.exe -B -m backend.app.cli.database_backup restore-check --backup $p4Create.backupPath --manifest $p4Create.manifestPath --output-directory data/backups/restore-checks
$p4RestoreExit = $LASTEXITCODE
if ($p4RestoreExit -ne 0) { throw "P4 restore-check failed with exit code $p4RestoreExit." }
$p4Restore = $p4RestoreJson | ConvertFrom-Json
if (-not $p4Restore.ok -or $p4Restore.logicalSha256 -ne $p4Verify.logicalSha256) { throw 'P4 restore-check mismatch.' }
$p4DrillDb = (Resolve-Path -LiteralPath $p4Restore.restoredPath).Path
$p4LiveDb = (Resolve-Path -LiteralPath 'data/app.db').Path
if ($p4DrillDb -eq $p4LiveDb) { throw 'P4 drill database resolves to Live.' }
$p4RestoreRoot = (Resolve-Path -LiteralPath 'data/backups/restore-checks').Path
$p4RestorePrefix = $p4RestoreRoot.TrimEnd([IO.Path]::DirectorySeparatorChar, [IO.Path]::AltDirectorySeparatorChar) + [IO.Path]::DirectorySeparatorChar
$p4ValidationDir = Split-Path -Parent $p4DrillDb
if (-not $p4DrillDb.StartsWith($p4RestorePrefix, [StringComparison]::OrdinalIgnoreCase) -or -not (Split-Path -Leaf $p4ValidationDir).StartsWith('restore-validation-', [StringComparison]::Ordinal)) { throw 'P4 drill database is outside the current restore root.' }
$p4LiveIdentityPath = (Resolve-Path -LiteralPath 'data/compatibility/runtime/live-database-identity-v1.json').Path
$p4InventoryDir = New-Item -ItemType Directory -Path (Join-Path 'data/compatibility/preflight' ('p4-' + [guid]::NewGuid().ToString('N')))
$p4DrillIdentityPath = Join-Path $p4InventoryDir.FullName 'database-identity-v1.json'
$p4DrillIdentityJson = & .\.venv\Scripts\python.exe -B -m backend.app.cli.runtime_owner create-descendant-database-identity --database $p4DrillDb --subject-kind p4_rehearsal --parent-database-identity-manifest $p4LiveIdentityPath --parent-backup $p4Create.backupPath --parent-manifest $p4Create.manifestPath --output $p4DrillIdentityPath
$p4DrillIdentityExit = $LASTEXITCODE
if ($p4DrillIdentityExit -ne 0) { throw "P4 drill database identity failed with exit code $p4DrillIdentityExit." }
$p4DrillIdentity = $p4DrillIdentityJson | ConvertFrom-Json
if (-not $p4DrillIdentity.ok -or $p4DrillIdentity.subjectKind -ne 'p4_rehearsal') { throw 'P4 drill database identity JSON is invalid.' }
$p4InventoryBeforePath = Join-Path $p4InventoryDir.FullName 'inventory-before.json'
$p4InventoryAfterPath = Join-Path $p4InventoryDir.FullName 'inventory-after.json'
.\.venv\Scripts\python.exe -B -m backend.app.cli.schema_inventory capture --database $p4DrillDb --database-identity-manifest $p4DrillIdentityPath --output $p4InventoryBeforePath
if ($LASTEXITCODE -ne 0) { throw 'P4 pre-migration inventory capture failed.' }
$p4PreviousDbPath = [Environment]::GetEnvironmentVariable('DB_PATH', 'Process')
$p4HadDbPath = $null -ne $p4PreviousDbPath
$env:DB_PATH = $p4DrillDb
try {
  $p4BeforeCurrentRaw = @(& .\.venv\Scripts\python.exe -B -m alembic -c backend/alembic.ini current)
  $p4BeforeCurrentExit = $LASTEXITCODE
  if ($p4BeforeCurrentExit -ne 0) { throw "P4 pre-upgrade alembic current failed with exit code $p4BeforeCurrentExit." }
  $p4BeforeCurrent = @($p4BeforeCurrentRaw | ForEach-Object { ([string]$_).Trim() } | Where-Object { $_ -ne '' })
  if ($p4BeforeCurrent.Count -ne 1 -or $p4BeforeCurrent[0] -ne '20260807_03 (head)') { throw "P4 restored copy must already have exactly one current revision equal to 20260807_03 (head); observed: $($p4BeforeCurrent -join ' | ')." }
  .\.venv\Scripts\python.exe -B -m alembic -c backend/alembic.ini upgrade 20260807_03
  $p4UpgradeExit = $LASTEXITCODE
  if ($p4UpgradeExit -ne 0) { throw "P4 restored-copy upgrade failed with exit code $p4UpgradeExit." }
  $p4CurrentRaw = @(& .\.venv\Scripts\python.exe -B -m alembic -c backend/alembic.ini current)
  $p4CurrentExit = $LASTEXITCODE
  if ($p4CurrentExit -ne 0) { throw "P4 post-upgrade alembic current failed with exit code $p4CurrentExit." }
  $p4Current = @($p4CurrentRaw | ForEach-Object { ([string]$_).Trim() } | Where-Object { $_ -ne '' })
  if ($p4Current.Count -ne 1 -or $p4Current[0] -ne '20260807_03 (head)') { throw "P4 restored copy must finish with exactly one current revision equal to 20260807_03 (head); observed: $($p4Current -join ' | ')." }
  .\.venv\Scripts\python.exe -B -m backend.app.cli.schema_inventory capture --database $p4DrillDb --database-identity-manifest $p4DrillIdentityPath --output $p4InventoryAfterPath
  if ($LASTEXITCODE -ne 0) { throw 'P4 post-migration inventory capture failed.' }
  .\.venv\Scripts\python.exe -B -m backend.app.cli.schema_inventory compare --before $p4InventoryBeforePath --after $p4InventoryAfterPath
  if ($LASTEXITCODE -ne 0) { throw 'P4 migration inventory changed.' }
} finally {
  if ($p4HadDbPath) { $env:DB_PATH = $p4PreviousDbPath } else { Remove-Item Env:DB_PATH -ErrorAction SilentlyContinue }
}
~~~

Expected: exit 0；precheck 与 postcheck 都恰为 `20260807_03 (head)`；before/after 在同一 p4_rehearsal subject 上覆盖 12 legacy、五主表、全部 P2/P3 auxiliary、含 non-null `spec_json` 的 fixed `processing_jobs` projection、`processingJobs`/`processingJobSpecs` count/hash/strict decode、FTS logical/join，以及 exact `processing_jobs_spec_guard_insert|processing_jobs_spec_guard_update|document_chunks_fts_ai|document_chunks_fts_ad|document_chunks_fts_au` 五个 triggers 且严格全等。任何 `20260807_01|20260807_02` 副本、缺 spec projection/guard 或仍只有三个 FTS triggers 的 inventory 在执行 upgrade 前即停止，不能用本任务补齐缺失的 P2/P3 Live 阶段证据。

- [ ] **Step 3（2–5 分钟）：写完整 route gate 与 mutation oracle**

新增两个名称固定且职责不同的测试：`LegacyContractInventoryTests.test_gate_detects_missing_route` 在 mutation fixture 中删除 FastAPI `GET /api/papers`，断言 gate 精确报告该 method/path missing；`LegacyContractInventoryTests.test_fastapi_matches_every_inventory_entry` 对未变异 app 逐项调用 Node 与 FastAPI，并证明 48 routes/15 NDJSON contracts 全覆盖。两者都必须先定义再执行；不得让 RED 命令引用另一个未定义别名。

- [ ] **Step 4（2–5 分钟）：确认 mutation oracle 本身为 GREEN**

Run:

~~~powershell
.\.venv\Scripts\python.exe -B -m unittest backend.tests.test_http_contract_inventory.LegacyContractInventoryTests.test_gate_detects_missing_route -v
~~~

Expected GREEN: unittest 自身通过，因为它观察到 mutation FastAPI 被 gate 精确报告 `GET /api/papers` missing；若 unittest RED，说明 negative oracle 或 fixture 无效，不能继续。测试结束时 fixture 自动恢复，不能把 mutation 写入真实 app。

- [ ] **Step 5（2–5 分钟）：运行同一完整 gate**

Run:

~~~powershell
.\.venv\Scripts\python.exe -B -m unittest backend.tests.test_http_contract_inventory.LegacyContractInventoryTests.test_fastapi_matches_every_inventory_entry -v
~~~

Expected GREEN: 48 个 /api method/path 均通过，15 个 NDJSON terminal contract 均通过。

- [ ] **Step 6（2–5 分钟）：运行 Python 阶段套件**

Run:

~~~powershell
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
.\.venv\Scripts\python.exe -B -m unittest discover -s backend/tests -p "test_*.py" -v
if ($LASTEXITCODE -ne 0) { throw 'P4 backend suite failed.' }
.\.venv\Scripts\python.exe -B -m unittest discover -s test -p "test_*.py" -v
if ($LASTEXITCODE -ne 0) { throw 'P4 legacy Python suite failed.' }
.\.venv\Scripts\python.exe -B -m unittest discover -s test -p "test_mcp_server.py" -v
if ($LASTEXITCODE -ne 0) { throw 'P4 MCP server suite failed.' }
~~~

Expected: backend、legacy Python 与 MCP 九工具 characterization 全部 OK，无 skip P4 compatibility 测试。

- [ ] **Step 7（2–5 分钟）：运行 Node/React 门禁**

Run:

~~~powershell
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
npm.cmd test
if ($LASTEXITCODE -ne 0) { throw 'P4 Node suite failed.' }
node scripts/pre-existing-failure-baseline.mjs verify --baseline contracts/pre-existing-test-failures-v1.json
if ($LASTEXITCODE -ne 0) { throw 'P4 frontend Vitest/baseline guard failed.' }
npm.cmd run typecheck --prefix frontend
if ($LASTEXITCODE -ne 0) { throw 'P4 frontend typecheck failed.' }
npm.cmd run lint --prefix frontend
if ($LASTEXITCODE -ne 0) { throw 'P4 frontend lint failed.' }
npm.cmd run build --prefix frontend
if ($LASTEXITCODE -ne 0) { throw 'P4 frontend build failed.' }
npm.cmd run e2e --prefix frontend
if ($LASTEXITCODE -ne 0) { throw 'P4 frontend E2E failed.' }
~~~

Expected: Node、typecheck、lint、build 与 E2E exit 0。完整 frontend Vitest 若非零，必须由 P0.1 versioned guard 报告 raw non-zero 且证明 failed IDs、normalized signatures、related-file hashes 与 v1 完全一致、本切片未改相关路径；不得称为绿色。任何漂移立即停止。该临时例外只适用于 P1–P5，不能满足 P6 最终完成标准。

- [ ] **Step 8A（2–5 分钟）：写 candidate rollback public-seam 红测**

新增 `RuntimeOwnershipTests.test_candidate_rollback_smoke_isolated_and_preserves_full_inventory_and_live_owner`。fixture 必须同时准备 exact descendant identity、另一个指向 Live 的 identity、candidate/live namespace、`node_active` marker bytes/version、隔离 candidate roles 与真实 frozen Node rollback command；断言 Live DB/namespace、missing/stale/swapped identity、非 `node_active` marker、已存在 output 均在 stop/start/lease/DB 副作用前拒绝。成功 oracle 固定检查 candidate drain、role lease 清空、随机 loopback rollback process、`/api/papers|/api/reviews|/pdfbytes|/workspace/|/legacy/`、完整 inventory before/after，以及 Live Node PID/port/marker bytes/version 全程不变。rollback inventory 必须显式断言 `processing_jobs` 28-column projection仍含 non-null `spec_json`，`processingJobs`/`processingJobSpecs` count/hash/strict decode全等，五个 exact trigger names/normalized SQL hashes/behavior oracles全等；在隔离 fixture 中分别篡改 spec bytes、移除任一 spec guard、把五个 trigger 替换为三个 FTS trigger或增加第六个 lookalike 都必须令 smoke fail closed，而不能只靠 generic table count 通过。

- [ ] **Step 8B（2–5 分钟）：运行 candidate rollback 红测并确认失败原因**

~~~powershell
.\.venv\Scripts\python.exe -B -m unittest backend.tests.test_runtime_ownership.RuntimeOwnershipTests.test_candidate_rollback_smoke_isolated_and_preserves_full_inventory_and_live_owner -v
~~~

Expected RED: `candidate-rollback-smoke` Interface/CLI 尚不存在，或尚未满足 isolation/inventory/Live-owner oracle；fixture/import/端口占用或未真正启动 rollback process 不算有效 RED。

- [ ] **Step 8C（2–5 分钟）：实现最小 candidate rollback deep module 与 CLI Adapter**

实现 exact CLI `runtime_owner candidate-rollback-smoke --database <exact-isolated> --database-identity-manifest <exact-descendant> --candidate-runtime-namespace <non-production> --owner-marker <exact-existing-node-active> --rollback-profile frozen-node --evidence-output <exclusive-new-json>`。Interface 先验证隔离 DB platform identity、non-production namespace、marker state/hash 与 exclusive output，再在隔离副本上按 `drain candidate api/worker/scheduler → 证明 candidate lease 清空 → capture inventory before → 启动 frozen Node 于 OS-assigned loopback port → 五类 HTTP/static smoke → 停止 rollback candidate → capture/strict compare inventory after` 执行。before/after capture 都调用 Step 0C 的同一 fixed-contract validator，不能降级为 generic capture：必须保护 `spec_json` column/schema SQL、两项 ProcessingJob fingerprints与五个 exact trigger SQL/behavior。实现持续重验 Live Node PID/port/DB handle 与 marker bytes/version，任一变化立即失败；不得连接、停止或改写 Live DB/Node/marker。

- [ ] **Step 8D（按实际时长）：重跑同一测试为 GREEN，再执行一次隔离 operational smoke**

~~~powershell
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
.\.venv\Scripts\python.exe -B -m unittest backend.tests.test_runtime_ownership.RuntimeOwnershipTests.test_candidate_rollback_smoke_isolated_and_preserves_full_inventory_and_live_owner -v
if ($LASTEXITCODE -ne 0) { throw 'P4 candidate rollback CLI contract failed.' }
$p4RollbackEvidencePath = Join-Path $p4InventoryDir.FullName 'candidate-rollback-smoke.json'
.\.venv\Scripts\python.exe -B -m backend.app.cli.runtime_owner candidate-rollback-smoke --database $p4DrillDb --database-identity-manifest $p4DrillIdentityPath --candidate-runtime-namespace p4-rehearsal --owner-marker data/compatibility/runtime/production-owner.json --rollback-profile frozen-node --evidence-output $p4RollbackEvidencePath
if ($LASTEXITCODE -ne 0) { throw 'P4 isolated candidate rollback smoke failed.' }
$p4RollbackInventoryPath = Join-Path $p4InventoryDir.FullName 'inventory-after-rollback.json'
.\.venv\Scripts\python.exe -B -m backend.app.cli.schema_inventory capture --database $p4DrillDb --database-identity-manifest $p4DrillIdentityPath --output $p4RollbackInventoryPath
if ($LASTEXITCODE -ne 0) { throw 'P4 rollback inventory capture failed.' }
.\.venv\Scripts\python.exe -B -m backend.app.cli.schema_inventory compare --before $p4InventoryAfterPath --after $p4RollbackInventoryPath
if ($LASTEXITCODE -ne 0) { throw 'P4 candidate rollback changed the fixed inventory.' }
~~~

Expected: 定向 test 与五类隔离 smoke 成功；12 legacy + 全部 P1/P2/P3/FTS inventory、含 `spec_json` 的 `processingJobs`、`processingJobSpecs` 与 exact `processing_jobs_spec_guard_insert|processing_jobs_spec_guard_update|document_chunks_fts_ai|document_chunks_fts_ad|document_chunks_fts_au` 五个 triggers 严格全等；每条 spec strict decode且正文未泄露，Live Node 从未停止或交出 ownership，`node_active` marker bytes/version 不变。

- [ ] **Step 9（2–5 分钟）：记录回滚固定值**

记录供 P6 使用的候选 rollback map：API_BACKEND_MODE=legacy、DOCUMENT_PIPELINE_MODE=legacy、GENERATION_PIPELINE_MODE=legacy、ARTIFACT_READ_MODE=legacy、ARTIFACT_WRITE_MODE=legacy、OCR_ENABLED=0、OBSIDIAN_ENABLED=0；P4 只在隔离副本执行它，不应用到 Live，不执行 alembic downgrade，不恢复旧备份。rollback 必须保留每条 queued/running/terminal job 的 exact `spec_json` bytes、`processingJobSpecs` hash与两个 spec guard triggers，不能从 progress/current Settings 重建或清空任务参数。P6 shutdown gate 才能把这份 rehearsal evidence 用于正式 promotion/rollback。

- [ ] **Step 10（2–5 分钟）：检查计划范围与 diff**

Run:

~~~powershell
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
git diff --check
if ($LASTEXITCODE -ne 0) { throw 'P4 whitespace validation failed.' }
git status --short
if ($LASTEXITCODE -ne 0) { throw 'P4 repository status inspection failed.' }
~~~

Expected: 无 whitespace error；实现阶段只有本计划 Files 列表内的预期文件变化，server.js/db.js 未删除，data/app.db* 未变化。

## P4 完成门禁

- P4 create-live/initialize/verify 只使用 P0 fixed `OriginReceipt` exact path + P0 evidence out-of-band receipt file SHA，并从 receipt 取得和重新验证 exact origin backup/Manifest；Live `DatabaseEvidenceIdentityManifest` 固定携带 receipt anchor、使用 canonical raw bytes且可稳定重算 file SHA，不得新建或替换 lineage origin。四态 resume 已由单行为 RED/GREEN 固定：双缺失才 exclusive-create identity；exact identity + missing marker 必须先由只读 `verify-live-database-identity` 复验 receipt、manifest bytes/SHA、Live platform identity/resolved path/subject/lineage，再复用该 identity 调用唯一 `initialize-node-owner`；exact identity + exact marker 只调用只读 `verify-node-owner`；其余 wrong/partial 状态 fail closed。任何路径都不删除、覆盖或 touch 既有 identity/marker，并重新证明 production namespace、resolved absolute `server.js` path、唯一 Node PID/cwd/argv/loopback port/DB handle 与零 Live Python role。
- `alembic heads` 与隔离副本前后两次 `alembic current` 都对全部非空 raw 输出做 exact-one 检查；readiness 明确测试 missing/wrong/multiple/exact，且 `test_api_worker_scheduler_bootstrap_revision_matrix_fails_before_side_effects` 对三 role × 四态执行十二个 subtests，证明 missing/multiple/wrong 在任何 socket/claim/lease/provider 副作用前 fail closed。
- migration rehearsal 与 candidate rollback 的 before/after inventory 都使用同一隔离 `DatabaseEvidenceIdentityManifest`，并各自先通过固定 contract，而非只做相互比较：精确覆盖 12 legacy、五张 P1 主表、三张 P2 辅助表、两张 P3 物理表、唯一 `alembic_version=20260807_03`、`processing_jobs` 28-column/schema SQL contract（含 non-null `spec_json`）、`processingJobs`/`processingJobSpecs` count/hash/strict decode、FTS virtual SQL/logical content/external-content rowid join，以及 exact `processing_jobs_spec_guard_insert|processing_jobs_spec_guard_update|document_chunks_fts_ai|document_chunks_fts_ad|document_chunks_fts_au` 五个 trigger name/SQL hash/behavior；任一 missing/rename/spec tamper/additional-lookalike/delta 都阻止完成。
- 48 个 legacy /api method/path、15 条 NDJSON 流、/pdfbytes、/papers family、/workspace/ 与 /legacy/ 全部通过 Node/FastAPI parity。
- /api/v2 复用 P2/P3 routers：两条 paper-scoped sources、explainer/translation/classification/metadata/summary 五条 artifact command、artifact GET、index/index-status、chunk search、jobs list/detail/events/cancel/retry；所有写操作返回 typed response 或 202 job，wire 字段保持 camelCase。P5 的四条 Obsidian 路径在 P5 单独挂载和验证，不能成为 P4 的完成前置条件。
- Route 中无 SQL、Popen、OCR/LLM、文件写入、retry loop 或 artifact 状态机。
- FastAPI composition root 只有一套 v2 routers、Pydantic DTO、ProcessingQueue 与 `backend/app/api/errors.py` error seam；P4 未复制 P2/P3 实现。
- Settings 复用扩展后的 CredentialStore，LLM/OCR/Embedding API/Semantic Scholar 四类 profile 与内部 `hasKey`/`keyTail`/`environmentManaged` 全绿；legacy `hasApiKey/apiKeyTail`、`hasOcrKey/ocrKeyTail`、`hasEmbedKey/embedKeyTail`、`hasS2Key/s2KeyTail` wire 保持兼容；逐 kind 空白保存保留 Credential，固定探测 fixture 不读取用户 PDF。
- ProcessingJob-backed NDJSON 断连只 detach，任务继续由 Worker 持有；只有显式 cancel route 能提出持久任务取消。所有 enqueue/claim/retry/recovery/rollback 都以 P2 canonical `spec_json` 为唯一业务请求来源，保持 exact bytes/hash并禁止 API/log/event 回显或从 progress/current Settings 重建。
- API 默认只绑定 loopback，Host/Origin 本地访问策略 fail closed 且不信任 forwarding headers；每进程恰一个 role、同 namespace 三 role 可共存、Worker/Scheduler 各自单 owner 可证明；drain 的 admission/claim/tick 顺序、在途 transaction/`next_run` settle、role lease 释放与 timeout 只取消 candidate provider 均由同目标 RED/GREEN 固定。真实 resolved default Compose 仍由 Node 拥有 Live，resolved `p4-candidate` Compose/Docker targets 明确满足隔离 DB/namespace、单角色、loopback、只读 owner evidence，Node 仍可在隔离端口使用扩展后的 schema 启动。
- P4 Python roles 只在 restore copy/temp DB、随机 loopback port 与 candidate runtime namespace 运行；Live Node HTTP/Worker/Scheduler 始终保持 production owner，P4 没有执行 Node shutdown、Live Python role startup 或 production profile promotion。
- P0 restore 副本固定 `20260807_03` migration、backend、legacy Python、MCP、root Node、frontend typecheck/lint/build/E2E 与真实 FastAPI candidate E2E 通过；完整 frontend Vitest 为 0 或严格匹配 P0.1 versioned non-zero baseline，后者明确不是全绿。
- candidate rollback 只切隔离进程与 mode，不 downgrade、不删除 document_sources/generated_artifacts/processing_jobs/document_chunks/obsidian_exports，不改写或清空 `processing_jobs.spec_json`/两个 spec guard triggers，不删除 papers.explainer、papers.pdf_path 或 translations.content。正式 Node shutdown/Python promotion 仅由 P6 shutdown gate 授权。
