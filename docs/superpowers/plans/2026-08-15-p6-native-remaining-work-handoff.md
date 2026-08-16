# study-app 后端迁移 —— 剩余工作交接提示词

> 把本文件整份内容作为提示词交给接手的 AI。它是自包含的：不读本文件以外的会话历史也能开工。

---

## 0. 你的角色

你是 study-app 项目的接手工程师。后端迁移（Node → Python/FastAPI）的**代码实现已基本完成并已提交推送**，但**实际迁移一步都没有执行**。你的任务是：先补完验证，再按 P6 门禁顺序执行真正的迁移。

不要从头重做任何已完成的实现。

---

## 1. 仓库与环境

- **仓库根目录**：`F:\paper\研究方向细化\study-app`
- **分支**：`main`（唯一分支，已无其他分支和工作树）
- **HEAD**：`242121e feat(p6): add native Windows runtime adapter and harden identity gates`
- **远程**：`https://github.com/QQ-Stars/paper-study.git`，`main` 与 `origin/main` 同步，工作树干净
- **Python**：`.\.venv\Scripts\python.exe`（Python 3.10，基于 Anaconda）
- **平台**：Windows 11，PowerShell 5.1

### 每条 Python 命令必须先设置的环境变量

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
$env:P3_SQLITE_DLL_DIR='D:\Programming\Environment\Anaconda\pkgs\sqlite-3.51.2-hee5a0db_0\Library\bin'
```

`P3_SQLITE_DLL_DIR` 不设会导致 SQLite 扩展相关测试失败。

---

## 2. 硬性约束

- **不使用 Docker**。Docker 只是另一种部署方式，用户已明确要求先完成 native Windows 部署路径。禁止 `docker build` / `docker compose up`。本机也确实没装 Docker CLI。
- **不修改 `public/`**，不做无关 UI / CSS / React / 文案改动。
- **不使用 Live `data/app.db` 作为测试夹具**，不调用真实外部 API，不下载模型，不使用真实凭据。
- **P6 门禁全部通过前，禁止执行 Live takeover、迁移或任何 destructive 操作。**
- **不得**用 latest / glob / 重新计算 / 路径猜测 / 临时 backup pair 替代冻结的 identity。
- 不因计划文档 checkbox 未同步就判定实现缺失；**始终以当前代码、数据库证据和测试结果为准**。
- 修 bug 用 TDD：先写红测，确认红，再实现，再确认绿。
- 不要放宽或删除任何门禁来让测试变绿。

---

## 3. ⚠️ 本机环境陷阱（会浪费你几小时，务必先读）

1. **后台运行的命令会被会话 teardown 杀掉。** 不要用 `run_in_background` 跑长测试，会在中途被杀且没有完成记录。**用前台分批运行**，每批约 6 个测试模块（1–2 分钟）。

2. **`.venv\Scripts\python.exe` 是一个 stub。** 它会再 spawn 一个 `D:\Programming\Environment\Anaconda\python.exe` 子进程。所以每个运行中的角色实际上是**两个进程**，而且**监听 socket 属于子进程**。用 `Get-NetTCPConnection` 找端口时要按子进程 PID 找。

3. **机器上有 6 个用户真实的 MCP 服务进程**，命令行形如 `python F:\...\study-app\agent\mcp_server.py`（注意是**文件路径形式**，不是 `-m agent.mcp_server`）。它们持有**真实的 `data/app.db`**。**绝对不要杀这些进程。** 清理测试残留时只能匹配 `-m backend.app.cli.candidate_runtime` 或 `-m agent.mcp_server --supervisor`（模块形式）。

4. **PowerShell 5.1**：不要对 native exe 用 `2>&1` 重定向（会产生 `NativeCommandError` 并让 `$?` 变 false）。用 `| Select-String -Pattern '^Ran |^OK|^FAILED|^ERROR:|^FAIL:|skipped'` 过滤输出即可。

5. **系统代理**：`HTTP_PROXY=http://127.0.0.1:7890`，但 `NO_PROXY` 已覆盖 `127.0.0.1`，Python 的 `urlopen` 会正确 bypass。不是问题来源，别往这个方向查。

6. **Python 3.10**：`asyncio.TimeoutError` 与内置 `TimeoutError` 不是同一个类。已修过一次，注意别写回去。

7. **子进程 stdout 是块缓冲**：角色日志文件为空**不代表**进程没输出，只代表 stdout 还在缓冲区里。stderr 是行缓冲，所以真报错会出现在日志里。**不要把空日志当成"进程卡住"的证据。**

---

## 4. 当前真实状态（证据，非推测）

| 证据 | 值 | 含义 |
|---|---|---|
| `data/compatibility/runtime/production-owner.json` → `ownerState` | `node_active` | Node 仍是 Live owner，Python 从未接管 |
| `data/compatibility/runtime/build-identities/` | **不存在** | 从未冻结过 BuildIdentityManifest |
| `data/compatibility/evidence/` | **不存在** | 从未创建过 final EvidenceRunManifest |
| 全仓库 `native-runtime-v1.json` | **不存在** | `native_runtime configure` 从未运行 |
| `data/compatibility/preflight/p6-native-candidate-smoke/` | 只有空 `leases/` | 只做过零星试探 |

`data/compatibility/runtime/` 现有且**只有**三个文件，它们是 P4 产物，**只能验证/消费，禁止重新初始化或重算**：

- `live-database-identity-v1.json`
- `p0-origin-receipt-v1.json`
- `production-owner.json`

---

## 5. 已完成的部分（不要重做）

- P0–P5 实现与阶段门禁
- P6 代码实现：`FinalWindowCoordinator`、`ProductionPromotionCoordinator`、`production_rollback`、BuildIdentity v2、DatabaseIdentity、StartupSnapshot、HandoffReceipt
- **native Windows adapter**：`backend/app/cli/native_runtime.py` + `backend/app/providers/native_runtime.py` + `backend/tests/test_native_runtime_operations.py`
- schema 为 additive 的 `20260807_03`，Alembic 唯一 head，Node 可读扩展 schema

### 已验证通过的测试（不必重跑）

| 范围 | 结果 |
|---|---|
| `backend.tests.test_native_runtime_operations` | Ran 12, OK, raw exit 0 |
| `backend.tests.test_build_identity` | Ran 6, OK, raw exit 0 |
| 批次1：`test_000_p3_sqlite_runtime` `test_api_foundation` `test_api_health` `test_api_jobs_schedules` `test_api_legacy_json` `test_api_ndjson` `test_api_pdf_static` `test_api_v2` `test_bound_vault_root` `test_build_identity` `test_candidate_container_contract` `test_chunk_embeddings` | Ran 68, **4 failures（全部是 Docker 未安装）** |
| 批次2：`test_compatibility_cli` `test_compatibility_gate` `test_context_builder` `test_credentials` `test_data_fingerprint` `test_database_backup` | Ran 143, OK, **2 skips（symlink 权限）** |
| `python -m compileall -q backend agent` | exit 0 |
| `git diff --check` | exit 0 |
| `compatibility verify-static-runbook` | `"ok":true`, exit 0 |

---

## 第一层任务：补完代码验证

### 任务 1.1 —— 跑完剩余 52 个测试模块

用下面的模板，**前台**逐批运行。每批把模块名替换掉：

```powershell
Set-Location 'F:\paper\研究方向细化\study-app'
$env:PYTHONDONTWRITEBYTECODE='1'
$env:P3_SQLITE_DLL_DIR='D:\Programming\Environment\Anaconda\pkgs\sqlite-3.51.2-hee5a0db_0\Library\bin'
$mods = @('模块1','模块2','模块3','模块4','模块5','模块6') | ForEach-Object { "backend.tests.$_" }
.\.venv\Scripts\python.exe -B -m unittest @mods 2>&1 | Select-String -Pattern '^Ran |^OK|^FAILED|^ERROR:|^FAIL:|skipped'
Write-Output "RAW_EXIT=$LASTEXITCODE"
```

**9 个批次：**

- A：`test_database_identity` `test_document_artifacts` `test_document_search_api` `test_document_source_pipeline` `test_evidence_capture` `test_final_window_watchdog`
- B：`test_fts_search` `test_generation_pipeline` `test_http_contract_inventory` `test_legacy_agent_contract` `test_legacy_agent_provider` `test_legacy_p3_provider`
- C：`test_legacy_processing_streams` `test_legacy_reconciliation` `test_machine_summary` `test_mcp_contract` `test_mcp_readonly` `test_mcp_shadow`
- D：`test_native_extractor` `test_obsidian_auto_export` `test_obsidian_exports_repository` `test_obsidian_jobs_api` `test_obsidian_layout` `test_obsidian_ownership`
- E：`test_obsidian_paper_delete` `test_obsidian_pdf_migration` `test_obsidian_pdf_modes` `test_obsidian_rebuild` `test_obsidian_runtime_exporter` `test_obsidian_settings`
- F：`test_ocr_disabled_baseline` `test_ocr_explainer_slice` `test_ocr_provider_gate` `test_p1_documentation_contract` `test_p1_domain` `test_p1_migration`
- G：`test_p1_repositories` `test_p1_runtime_contract` `test_p2_migration` `test_p3_migration` `test_processing_jobs_api` `test_processing_queue`
- H：`test_processing_worker` `test_production_candidate_e2e` `test_production_rollback` `test_rollout_defaults` `test_runtime_ownership` `test_schema_inventory`
- I：`test_source_document_pipeline` `test_source_freshness` `test_suite_isolation` `test_translation_resume`

每批失败就先诊断修复（TDD），修完只重跑该精确测试方法，行为稳定后再跑该文件，最后再继续下一批。**不要重复运行已通过且未受影响的测试。**

跑完每批后清点残留进程（**只匹配模块形式，别碰用户的真实 MCP 进程**）：

```powershell
$rows = Get-CimInstance Win32_Process -Filter "Name='python.exe'" | Where-Object { $_.CommandLine -match '-m backend\.app\.cli\.candidate_runtime|-m agent\.mcp_server --supervisor' }
if ($rows) { $rows | Select-Object ProcessId, CommandLine | Format-List } else { Write-Output 'NO_ORPHAN_TEST_ROLES' }
```

### 任务 1.2 —— 消除 2 个 skip

`backend/tests/test_database_backup.py` 有两个 symlink 测试因 `WinError 1314`（客户端没有所需的特权）被 skip：
`test_restore_rejects_symlinked_output_root`、`test_windows_rename_rejects_a_final_component_symlink`

P6 要求 **0 skip**。解决办法是让进程拿到创建符号链接的权限：开启 Windows 开发者模式，或在提升权限的终端运行。**这是系统设置变更，必须先问用户，不要自己改。**

### 任务 1.3 —— 处理一个已知未修的 review 发现

`backend/app/providers/runtime_lease.py:572` —— `_windows_processes_using_file()` 把结果过滤到 `candidate_pids`（只有匹配冻结 entrypoint 的 node 进程 + 带 `--study-app-role` 标记的 python 角色）。因此 `quiesce_node()` 的 `zeroPidPortDatabaseHandles` 证据**看不到候选集合以外持有 Live 数据库的进程**。

具体风险：本仓库自带的 legacy MCP server（`agent/mcp_server.py`，以文件路径形式被编辑器启动）在最终窗口期间持有 `data/app.db`，而 quiesce 会照样返回"零句柄"，于是 cutover 在还有第二个进程握着 Live 数据库的情况下继续。

**这是 P6 "零 PID/端口/DB handle" 门禁的完整性问题，在执行第二层之前必须处理。** 修法会改变门禁语义（可能让 quiesce 因无关进程而失败），属于产品决策 —— **先向用户说明再动手**。

### 任务 1.4 —— 第一层收尾

```powershell
.\.venv\Scripts\python.exe -m compileall -q backend agent   # 期望 exit 0
git diff --check                                            # 期望 exit 0
git status --short                                          # 确认没有意外增删
```

---

## 第二层任务：执行 P6 迁移（15 步，目前一步都没做）

> **前提**：第一层必须全绿（完整后端套件 raw exit 0 / failures=0 / skips=0）才能开始。
> 中途任何一步失败：abort 当前窗口、换一个全新 runId 重来，**禁止覆盖、复制或复用已有的 run 目录**。

### 可用的 CLI（已核实存在）

`backend.app.cli.compatibility` 子命令：
`abort-cutover` `begin-final-window` `candidate-write-smoke` `capture-evidence` `compare` `create-evidence-run` `create-startup-snapshot` `create-suite-isolation` `fingerprint` `freeze-identity` `gate` `promote` `reconcile-legacy` `recovery-smoke` `restore-install-rehearsal` `rollback-production` `rollback-smoke` `verify-identity` `verify-legacy-runtime` `verify-static-runbook`

`backend.app.cli.native_runtime` 子命令：
`configure` `start` `status` `stop` `recover-stale-node-owner`

`backend.app.cli.runtime_owner` 子命令：
`create-live-database-identity` `verify-live-database-identity` `create-descendant-database-identity` `initialize-node-owner` `verify-node-owner` `reattest-stale-node-owner` `candidate-rollback-smoke`

### native adapter 的注入方式（关键）

`begin-final-window` / `quiesce-live` / `promote` / `abort-cutover` 通过 `--operations-factory` 和 `--watchdog-factory` 注入实现，格式是 **`module:callable`**（**恰好一个冒号**，属性不能以 `_` 开头）：

```
--operations-factory backend.app.providers.native_runtime:create_operations
--watchdog-factory   backend.app.providers.native_runtime:create_watchdog
```

这两个工厂**从环境变量读配置**，调用前必须设置：

```powershell
$env:STUDY_APP_NATIVE_RUNTIME_SPEC      = '<native-runtime-v1.json 的精确路径>'
$env:P6_BUILD_IDENTITY_MANIFEST         = '<frozen-build-identity-<buildId>.json 的精确路径>'
$env:STUDY_APP_NATIVE_RUNTIME_STATE_DIR = '<native 运行时状态目录>'
```

### 任务 2.0 —— 🔴 先补一个缺失的实现（阻塞第 7 步）

P6 计划文档在 Task 10 Step 10 里写了这条命令：

```
compatibility export-frozen-node-rollback-map --build-identity-manifest ... --database-identity-manifest ... --owner-marker ... --rollback-profile frozen-node --output ...
```

**但它在代码里根本不存在。** 已核实：

- `backend/app/cli/compatibility.py` 的全部 20 个子命令里没有它（`grep -rn "export-frozen-node-rollback-map" backend` 零命中）
- 唯一能产出 native rollback map 的是 `NativeWindowsRuntimeOperations.frozen_node_rollback_map_from_owner()`（`backend/app/providers/native_runtime.py:870`），而它目前只被 `native_runtime.py:147` 的 `recover-stale-node-owner` 分支内部调用，**没有对外的 CLI 出口**

后果：第 8 步 `create-startup-snapshot` 需要 `--frozen-node-rollback-map <json 路径>`，而**没有任何命令能生成这个 JSON 文件**。整条 native 迁移链在第 7 步断掉。

**要做的事（TDD）**：给 `backend/app/cli/native_runtime.py` 增加一个子命令，例如

```
native_runtime export-rollback-map --native-runtime-spec <...> --build-identity-manifest <...> --state-directory <...> --owner-marker <...> --output <exclusive-new-json>
```

内部调用已有的 `frozen_node_rollback_map_from_owner(owner_marker)`，用 canonical JSON + O_EXCL 写出，并返回 `{"ok":true,...,"rollbackMapPath":...,"rollbackMapSha256":...}`。

注意 `frozen_node_rollback_map_from_owner` 现有的前置校验（**不要放宽**）：owner 必须是 `node_active`、`runtimeNamespace=production`、executable/entrypoint/cwd/argv 与冻结 spec 完全一致、listenerHost 为 `127.0.0.1`、恰好一个 databasePath、**且记录的 processId 必须已经不存活**。

⚠️ 最后一条意味着：**它只能在 Node 已经停掉之后才调用得动**，而第 8 步 `create-startup-snapshot` 又必须在第 10 步 quiesce 之前完成。请先确认这个先后顺序矛盾怎么解 —— 可能需要一个不要求 PID 已死的导出路径（例如从冻结 spec + owner marker 直接构造，不做 staleness 检查），或者调整步骤顺序。**这是个需要和用户确认的设计决策，不要自行放宽 staleness 校验。**

### 执行顺序

| # | 步骤 | 命令 / 说明 | 状态 |
|---|---|---|---|
| 1 | 静态 runbook 校验 | `compatibility verify-static-runbook --readme README.md --database-doc docs/DATABASE.md` | ✅ 已通过 |
| 2 | 构建前端产物 | `npm.cmd run build --prefix frontend` | ⬜ |
| 3 | 生成 native runtime spec | `native_runtime configure --repository --python-executable --requirements-lock --node-executable --node-entrypoint --database --database-identity-manifest --owner-marker --runtime-lease-directory --processing-cursor-secret-file --api-port --output` | ⬜ |
| 4 | 冻结 BuildIdentityManifest | `compatibility freeze-identity --source-root . --python-artifact <...> --frontend-root frontend/dist --frontend-manifest <...> --deployment-kind native-windows --native-runtime-spec <step3 输出> --build-identity-directory data/compatibility/runtime/build-identities` | ⬜ |
| 5 | 独立复验 identity | `compatibility verify-identity --build-identity-manifest <...> --deployment-kind native-windows --native-runtime-spec <...>` | ⬜ |
| 6 | 创建 final EvidenceRunManifest | `compatibility create-evidence-run --evidence-root data/compatibility/evidence --run-id <新 GUID> --phase final --build-identity-manifest <...> --database-identity-manifest <live 的> --expected-key <33 个，见下>` | ⬜ |
| 7 | 导出 frozen Node rollback map | **⚠️ 存在实现缺口，见下方「任务 2.0」** | 🔴 阻塞 |
| 8 | 创建 canonical startup snapshot | `compatibility create-startup-snapshot --final-evidence-run-manifest --expected-final-evidence-run-manifest-sha256 --build-identity-manifest --database-identity-manifest --frozen-node-rollback-map --production-profile production --output` | ⬜ |
| 9 | arm cutover lease + watchdog | `compatibility begin-final-window --final-evidence-run-manifest --expected-... --startup-snapshot --expected-startup-snapshot-sha256 --owner-marker --runtime-namespace production --operator-pid <pid> --heartbeat-timeout-seconds <n> --lease-output --token-file-output --operations-factory --watchdog-factory --rollback-profile frozen-node` | ⬜ |
| 10 | quiesce Node（停 Live writer，证明零 PID/端口/DB handle） | `compatibility quiesce-live --cutover-lease --cutover-token-file --operations-factory --watchdog-factory` | ⬜ |
| 11 | cutover backup：create → 独立 verify → restore-check | P0 backup CLI + `restore-install-rehearsal --backup --manifest --target-database --expected-target-sha256 --rehearsal-root --build-identity-manifest --parent-database-identity-manifest --installed-database-identity-output --evidence-output` | ⬜ |
| 12 | Live 写入前后 fingerprint + 严格对比 | `compatibility fingerprint --database --database-identity-manifest --subject-kind --output`，再 `compatibility compare --mode --before --after --delta-ledger`；同时要求 `liveAccessCount=0` | ⬜ |
| 13 | 在同一 run root 内补齐全部 evidence key | `compatibility capture-evidence --key <key> --phase final --result-kind ... --run-manifest --expected-run-manifest-sha256 ...`；含 zero-skip 全套件、strict convergence、explained write smoke、frozen Node rollback（`rollback-smoke`）、Python recovery（`recovery-smoke`）、隔离 restore-install rehearsal | ⬜ |
| 14 | shutdown gate 发放单次 authorization（TTL ≤ 15 分钟） | `compatibility gate --phase final --evidence-dir --run-manifest --expected-run-manifest-sha256 --final-evidence-run-manifest --expected-... --startup-snapshot --expected-startup-snapshot-sha256 --cutover-lease --build-identity-manifest --database-identity-manifest --authorization-output --authorization-ttl-seconds 900` | ⬜ |
| 15 | promote：Python 四角色接管并落盘 HandoffReceipt | `compatibility promote --authorization --expected-authorization-sha256 --final-evidence-run-manifest --expected-... --cutover-lease --cutover-token-file --startup-snapshot --expected-startup-snapshot-sha256 --build-identity-manifest --database-identity-manifest --owner-marker --python-profile production --rollback-profile frozen-node --handoff-receipt-output --evidence-output --operations-factory` | ⬜ |

### 第 6 步需要的 33 个 expected key（顺序固定，不得重复）

```
build-identity-verify  bound-root-zero-skip  suite-isolation  backend-suite
legacy-python-suite  mcp-server-suite  node-suite
frontend-vitest  frontend-typecheck  frontend-lint  frontend-build  frontend-e2e
migration-head-ready  http-v2-ndjson-static  runtime-worker-scheduler-obsidian  mcp-credentials
legacy-reconciliation  node-quiesce  cutover-backup-create  cutover-backup-verify
cutover-backup-restore-check  live-pre-fingerprint  live-post-fingerprint  strict-readonly-compare
convergence-gate  candidate-production-profile  candidate-write-smoke  explained-write-compare
frozen-node-rollback  python-recovery  restore-install-rehearsal  final-enum-runbook  handoff-contract
```

⚠️ 注意其中包含 `frontend-vitest` `frontend-typecheck` `frontend-lint` `frontend-build` `frontend-e2e` `node-suite` `mcp-server-suite` `legacy-python-suite` —— **完成门禁不只是后端 unittest，前端与 Node 套件也必须在同一个 run 里全绿零 skip。**

### 每一步之间的强制自检

每条 native 命令后立刻检查 `$LASTEXITCODE`；用 pipeline 解析 JSON 时先保存并检查 native exit，禁止未定义变量静默变成 `$null`。多命令 PowerShell 块一律以下面两行开头：

```powershell
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
```

---

## 第三层任务：运行时配置切换

promotion 成功后，Live 运行时固定值应为（由 startup snapshot 一次性消费，**禁止热切换或逐项修改**）：

```
RUNTIME_ENVIRONMENT=live
RUNTIME_NAMESPACE=production
API_BACKEND_MODE=python
DOCUMENT_PIPELINE_MODE=p1
GENERATION_PIPELINE_MODE=p1
ARTIFACT_READ_MODE=prefer_new
ARTIFACT_WRITE_MODE=dual
OCR_ENABLED=0
OBSIDIAN_ENABLED=0
PAPER_STUDY_MCP_MODE=application
UI_ENTRY=react
```

回滚固定值（frozen Node）保持全 `legacy`，`OCR_ENABLED=0`、`OBSIDIAN_ENABLED=0`、`UI_ENTRY=react`。

`OCR_ENABLED=0` 与 `OBSIDIAN_ENABLED=0` 是最终默认值；用户显式开启属于独立的 startup choice，**不是 owner promotion 的隐式副作用**。

---

## 6. 失败恢复

任一步失败，按统一尾序恢复 frozen Node：

保持 `node_quiesced|handoff_pending` 非 active → 清除 authorization → drain Python / 停止新流量与 claim → 释放 role locks 与连接 → 按 startup snapshot 里的完整 frozen map 启动 Node → 跑 legacy smoke（`/api/papers` `/api/reviews` `/pdfbytes` `/workspace/` `/legacy/`）→ **全绿之后才** CAS owner 到 `node_active`。

- phase 为 `armed|node_quiesced|authorization_issued` → 用 `abort-cutover`
- phase 为 `handoff_pending`（已接管但无成功 receipt）→ 由 ProductionPromotionCoordinator + durable handoff lease 恢复
- 已 `python_active` 且已写 HandoffReceipt → 用 `rollback-production`

`rollback-production` 是**应用回滚**，不移动 / 不恢复 / 不 downgrade / 不写入 SQLite 内容。真正的数据恢复是另一个显式接口 `restore-production-data`，需要独立授权，promotion authorization 和 HandoffReceipt 都不能替代它。

---

## 7. 报告要求

每完成一个阶段，报告：

- 精确执行的命令
- raw exit code
- 测试数量 / failures / skips
- 相关 revision、hash、identity
- **明确列出未执行的项和原因**

不要把 non-zero 说成"全绿"。不要为了让门禁通过而放宽门禁。

---

## 8. 立即开始

1. 只读确认：`git status --short`、`git log --oneline -1`、上面第 4 节的五条状态证据
2. 从**第一层任务 1.1 批次 A** 开始
3. 遇到失败先诊断再修复，不要跳过
