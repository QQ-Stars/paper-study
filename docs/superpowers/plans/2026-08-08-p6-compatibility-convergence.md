# P6 MCP、数据兼容收敛与 Node 停用实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Every production change uses superpowers:test-driven-development, and every completion claim uses superpowers:verification-before-completion. Steps use checkbox syntax and are sized for 2–5 minutes.

**Goal:** 以可重复的数量/canonical hash、HTTP/NDJSON/React compatibility、九工具 MCP exact contract 和完整回滚演练证明 Python runtime 已接管生产；随后停止 Node HTTP/Worker/Scheduler，但保留冻结 Node runtime、旧 API、旧字段、旧表和回滚开关，不做物理删除。

**Architecture:** LibraryQueries 是 HTTP、MCP、Obsidian 的共同 read model。backend/app/api/mcp.py 实现 application MCP adapter；agent/mcp_server.py 保持 stdio/FastMCP 入口和 legacy|shadow|application 模式选择。shadow 同时执行 legacy/application read path，只返回 legacy 并记录脱敏 canonical diff。DataFingerprint 使用 SQLite mode=ro 快照计算版本化 canonical hashes；LegacyReconciliation 在同一只读 snapshot 内逐个 legacy `(paper_id,kind)` 证明内容与 SourceDocument provenance，把严格 read-only convergence window、legacy reconciliation ledger 与可解释 write-smoke delta window 分开。Task 7–9 只做隔离 preflight；所有实现、测试、Compose 与静态文档先完成，随后以 content-addressed 唯一路径冻结 `BuildIdentityManifest`，才进入一次短暂的最终 Live convergence window。P4 的 `DatabaseEvidenceIdentityManifest` 独立描述 Live/descendant 数据库 lineage 与 subject，绝不从 build identity 推断。provisional 与 final 命令都由 `create-evidence-run` 创建 fresh immutable run root，再由 `capture-evidence` 生成 run-bound、machine-readable artifact。`FinalWindowCoordinator` 在 quiesce 前先 O_EXCL 建立 durable `CutoverLease` 并启动独立 watchdog；quiesce 后到 promotion takeover 前任何命令失败、operator 退出、heartbeat timeout、authorization 未使用或过期，都由同一个 token/identity-bound `abort_cutover` 顺序恢复 frozen Node。CompatibilityGate 聚合 migration、data、reconciliation、HTTP、NDJSON、UI、Worker/Scheduler、MCP、CredentialStore、Obsidian 和 rollback evidence；shutdown phase 以 exclusive-create 产生同时绑定 final run、cutover lease、canonical `ProductionStartupSnapshot`、build/database manifests 的短期单次 promotion authorization。ProductionPromotionCoordinator 的 `begin_handoff` 原子接管 watchdog lease，在任何 Python socket/DB/provider/role-lease 副作用前验证 authorization 与 startup snapshot，并原子移交 owner marker；接管后的任一步失败遵循统一尾序：保持 `node_quiesced|handoff_pending` 非 active、清 authorization、drain Python、释放 locks、启动 frozen Node、legacy smoke，最后才 CAS `node_active`。成功进入 `python_active` 后写 durable `HandoffReceipt`，常驻协调器与可由新进程调用的 `rollback-production` 可在服务重启后严格复验全部 identity 并幂等执行同一尾序。

**Tech Stack:** Python 3、FastMCP、FastAPI、Pydantic v2、SQLite URI mode=ro、hashlib、canonical JSON、unittest、Node test runner、React、Vitest、Playwright、Docker Compose。

**2026-08-15 deployment-adapter amendment（优先于下文 container-only 表述）：** P6 production
支持 `native-windows|container` 两个等价 adapter。Windows 默认完整运行方式是
`native-windows`，Docker 只是可选方式。所有 HTTP/NDJSON/MCP、owner、lease、receipt、rollback、
zero-skip 与 Live fingerprint 门禁不变。container BuildIdentity 继续绑定 resolved Compose 与 exact
image digests；native BuildIdentity v2 改为绑定 Python/Node executable bytes、requirements、frontend
artifacts、application cwd、API/Worker/Scheduler/MCP 与 frozen Node exact argv、环境值 hash，且不得伪造
image digest。native operator 只通过 `backend.app.cli.native_runtime configure|start|status|stop|recover-stale-node-owner`
运行；`python_active` 日常重启必须复验 exact HandoffReceipt identity chain。下文凡写“必须为 image
digest/Compose”之处，对 native adapter 均替换为上述 native artifact identity；凡写“默认 Compose
profile”之处，均理解为所选 adapter 的四 Python roles。Docker 构建与实机验证按用户要求在项目
实现及原生门禁完成后单独执行，不得阻塞原生完整运行。

**Prerequisite gate:** P0–P5 scoped gates 已通过且 Alembic 唯一 head 为 P3 revision 20260807_03；若 P0.1 v1 曾记录 non-zero pre-existing failures，它们在 P1–P5 每阶段都必须以 raw non-zero、完全相同 ID/signature/related hashes 且未改相关路径报告，不能称为全绿。FastAPI 已在隔离端口通过真实后端 E2E；Python Worker/Scheduler 单一所有权已验证；P0 verified backup 与隔离 restore/install rehearsal 可用；frozen Node image 能读取 revision 20260807_03 扩展 schema。P4 已 exclusive-create `data/compatibility/runtime/live-database-identity-v1.json` 与 `production-owner.json`，二者分别是 exact Live `DatabaseEvidenceIdentityManifest` 与 `node_active` owner marker；P6 只验证/消费，不重新初始化或用 path/current hash 重算。P6 shutdown gate 和最终完成不接受该临时例外：完整套件必须由 `capture-evidence` 报告 raw 0 failure、0 skip，除非用户明确批准改变完成标准。

**Scope guardrails:** 不删除 server.js、db.js、agent/mcp_server.py、旧 API、旧表、旧列、rollback image 或 `data/settings.json` 的 legacy `apiKey|ocrApiKey|embedApiKey|s2ApiKey` compatibility fields；不物理删除 papers.explainer、papers.pdf_path、translations.content；不在 MCP 查询中 create table、migrate、backfill、embed、enqueue、调用 OCR 或写日志到 SQLite；应用回滚不 downgrade Live DB；数据恢复必须停止所有进程并保留原 DB。P6 不调用 P1 `finalize_legacy_migration`，legacy plaintext 仅在未来独立、版本化且正式关闭 Node rollback window 的计划中才可移除。计划内所有多命令 PowerShell block 都必须以 `Set-StrictMode -Version Latest` 与 `$ErrorActionPreference = 'Stop'` 开始，并在每个 native executable 后立即检查 `$LASTEXITCODE`；通过 pipeline 解析 JSON 时也必须先保存并检查 native exit，禁止未定义 session variable 静默变为 `$null`。

---

## 文件职责

- backend/app/api/mcp.py：九个 application MCP tool handlers 与 exact legacy wire serializer。
- backend/app/application/library_queries.py：新表优先、旧字段回退的共同只读查询。
- backend/app/repositories/read_only.py：SQLite URI mode=ro、PRAGMA query_only=ON、短只读 transaction。
- backend/app/api/compat/mcp_shadow.py：legacy/application 双读、canonical diff 与脱敏观察记录。
- backend/app/api/compat/data_fingerprint.py：版本化 table counts、PK set hash、row hash、关键列 hash、`canonicalDataSha256` 与 FK/integrity 结果；绝不冒充 P0 backup-compatible `logicalSha256`。
- backend/app/api/compat/database_identity.py：P4 已创建；P6 只扩展/验证唯一 `DatabaseEvidenceIdentityManifest`，从 P0 fixed `OriginReceipt` exact path/file SHA 验证稳定 databaseLineageId，并为 Live/restore/write-smoke/install 文件实例计算 subjectDatabaseId 与 parent chain。
- backend/app/api/compat/legacy_reconciliation.py：只读生成 provenance-safe legacy-to-new 内容分类 ledger；绝不 backfill 或伪造 SourceDocument relation。
- backend/app/api/compat/build_identity.py：content-addressed `BuildIdentityManifest`；只含 canonical source manifest、dirty-aware sourceTreeHash 与实际部署 buildArtifactHash，不含数据库 lineage、subject、parent chain 或 DB path；每个 buildId 只能 exclusive-create `frozen-build-identity-<buildId>.json`。
- backend/app/api/compat/evidence_capture.py：`EvidenceRunManifest`、`capture-evidence` 的 run-bound allowlist、exclusive-create、子进程记录与 artifact digest；provisional/final gate item 都不得绕开。
- backend/app/api/compat/machine_summary.py：把 unittest/Node/Vitest/Playwright/JUnit/JSON/check runner 的 machine-readable summary 归一为严格 `RunnerSummaryV1`；不从人类文本推断 totals/failures/skips。
- backend/app/api/compat/suite_isolation.py：为每条 suite 建立 run-local 临时 DB/settings/PDF/Vault/Fake Keyring，安装 Live path-open/SQLite-connect/Provider/network deny tripwire，并汇总 `liveAccessCount=0`。
- backend/app/api/compat/gates.py：阶段 evidence 读取和 Node shutdown gate。
- backend/app/application/final_window.py：`FinalWindowCoordinator`、durable `CutoverLease`、owner-only token file、heartbeat/watchdog 与 authorization 前 abort recovery；不执行 compatibility assertions。
- backend/app/application/runtime_handoff.py：验证 promotion authorization、canonical startup snapshot、原子 owner-marker handoff、durable `HandoffReceipt`、常驻协调与 restart-safe rollback 的深模块。
- backend/app/application/production_rollback.py：`rollback-production` 的 durable recovery lease、crash resume、same-receipt idempotency 与统一 frozen Node recovery 尾序；不执行数据库恢复。
- backend/app/infrastructure/bound_root.py：复用 P0 `BoundRoot`；Windows 持有 no-delete-share root handle，POSIX 使用 dirfd/openat/renameat/O_NOFOLLOW，所有 destructive rehearsal/install 在绑定对象下解析并原子写入。
- backend/app/runtime.py：P4 live-deny seam 在 P6 接入 authorization verifier；验证前不得打开 socket、DB 或 Provider。
- backend/app/providers/runtime_lease.py：Worker/Scheduler role-scoped lease 与 Node/Python owner marker 的原子状态转换。
- backend/app/cli/compatibility.py：`fingerprint|compare|reconcile-legacy|candidate-write-smoke|rollback-smoke|recovery-smoke|restore-install-rehearsal|create-evidence-run|capture-evidence|freeze-identity|verify-identity|create-startup-snapshot|begin-final-window|quiesce-live|abort-cutover|gate|promote|rollback-production` CLI；typed identity/run/startup/receipt 参数不可互换，只输出 JSON。真实离线数据恢复另属显式授权的 `restore-production-data` Interface，P6 只实现隔离测试与 runbook，不对 Live 调用。
- agent/mcp_server.py：保留 FastMCP stdio 入口和九个公开工具，按 PAPER_STUDY_MCP_MODE 选择 legacy|shadow|application。
- agent/embed.py：semantic rank 真正只读连接；read-only 路径不建表、不更新向量。
- backend/tests/fixtures/mcp/tool_schemas.json：九工具 tools/list exact snapshot。
- backend/tests/fixtures/mcp/results/：正常、空库、旧字段、新 artifact、Unicode、错误结果 golden。
- backend/tests/test_mcp_contract.py：工具 schema、返回值和模式切换。
- backend/tests/test_mcp_readonly.py：DB bytes/mtime/sidecar/total_changes 只读证明。
- backend/tests/test_mcp_shadow.py：canonical diff、legacy return 和切换门禁。
- backend/tests/test_data_fingerprint.py：canonical 编码、counts/hash、迁移前后兼容。
- backend/tests/test_legacy_reconciliation.py：explainer/translation 的 proven、unprovable、mismatch 分类以及 notes/paper_vectors preservation。
- backend/tests/test_build_identity.py：tracked/untracked/modified source 与部署 artifact identity。
- backend/tests/test_evidence_capture.py：capture wrapper 的 allowlist、phase、exclusive-create、完整 argv/raw exit/summary/digest 与 child failure propagation。
- backend/tests/test_machine_summary.py：各 runner machine-readable summary、exit 0 + skip>0 拒绝与文本伪造拒绝。
- backend/tests/test_suite_isolation.py：每 suite fresh sandbox、Live tripwire 和 `liveAccessCount=0`。
- backend/tests/test_runtime_ownership.py：live authorization、Node→Python handoff、Worker/Scheduler locks 与失败恢复。
- backend/tests/test_compatibility_gate.py：缺 evidence 必失败、全部 evidence 才允许 shutdown。
- test/test_mcp_server.py：现有九工具 legacy 回归，不删除或放宽。
- Dockerfile：保留 frozen-node rollback stage，Python production stage 不含 Node runtime entrypoint。
- docker-compose.yml：生产 api/worker/scheduler/mcp 与 inactive frozen-node rollback profile。
- docs/DATABASE.md：数据 fingerprint、应用回滚、数据恢复和 Live 安装步骤。
- README.md：Python production 启动、MCP mode、Node inactive/rollback 状态。

## 九个 MCP 工具固定契约

| 工具 | 输入 |
|---|---|
| search_papers | query=""、type=""、topic=""、venue=""、year_from=0、year_to=0、min_relevance=0.0、has_explainer=false、only_favorites=false、sort="relevance"、limit=20 |
| semantic_search | query 必填、k=15 |
| related_papers | id 必填、k=8 |
| get_paper | id 必填 |
| get_explainer | id 必填、offset=0、max_chars=12000 |
| get_translation | id 必填、offset=0、max_chars=12000 |
| list_due_reviews | today=""、include_upcoming=false、limit=20 |
| list_categories | 无输入 |
| library_overview | 无输入 |

工具数必须恰为 9；名称、description、JSON Schema required/default/type、结果字段、null/空字符串、排序、limit clamp、分页、ok/error 和错误文本由 agent/mcp_server.py 与 test/test_mcp_server.py 当前行为冻结。P6 不增加第十个工具，不给九工具增加参数，也不增加返回长 SourceDocument 正文的新工具。

`ARTIFACT_READ_MODE` 只允许 P0 固定值 `legacy|prefer_new`。`legacy` 只读旧字段；`prefer_new` 仅在找到 eligible ready GeneratedArtifact 时返回新内容，没有 eligible row 才回退 `papers.explainer`/`translations.content`。新表 query、decode 或 repository 错误必须 fail closed 为安全分类错误，绝不能伪装成“没有新 row”后回退旧字段。

`get_paper` 的输入 schema 不变。Task 2 golden characterization 明确采用 application-only additive optional `sourceDocument` 字段，不改变 legacy/shadow caller 的冻结结果：该字段只有 `native` 与 `ocr` 两个 mode view；每个 view 为 null 或 `{currentId,status,updatedAt,error}`，测试覆盖 canonical `queued|running|ready|failed|stale|cancelled` 六状态的选择与序列化，`error` 只含 allowlisted code 与 bounded safe summary，不含 Markdown、PDF path、provider raw body、credential 或 traceback。因为 native/OCR 可以并存，不制造跨 mode 的单一“current source”。application cutover gate 将这一个 golden-approved additive field 与真正 contract regression 分开；不得借此忽略其他缺字段、排序、null 或类型差异。

`get_explainer`/`get_translation` 在 `prefer_new` 下先选择 eligible ready artifact、无 eligible row 才旧字段回退，然后对已选择的同一内容应用既有 `offset/max_chars` clamp 与分页 metadata；不得先截断再选择来源。

## v2 收敛固定路径

- POST 与 GET /api/v2/papers/{paper_id}/sources
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
- POST /api/v2/papers/{paper_id}/exports/obsidian
- POST /api/v2/obsidian/sync
- GET /api/v2/obsidian/status
- POST /api/v2/obsidian/test

OpenAPI compatibility evidence 必须证明上述 P2/P3/P5 path 各恰好出现一次，不存在 generic POST /api/v2/jobs、DELETE /api/v2/jobs/{job_id}、generic POST /api/v2/papers/{paper_id}/artifacts 或 generic GET/POST /api/v2/obsidian。所有 artifact/index request 继续要求 camelCase `sourceMode` + `sourceDocumentId`，index 另用 camelCase `includeEmbeddings`；snake_case、缺 source relation、paper/source 关联错误或 mode mismatch 均 fail closed。

## canonical data fingerprint v1

- 必选旧表：papers、progress、paper_reviews、notes、favorites、translations、paper_vectors、cite_edges、ingest_jobs、job_candidates、job_schedules、schema_migrations。
- 必选 P1 主表：document_sources、generated_artifacts、processing_jobs、document_chunks、obsidian_exports。
- 必选 P2 辅助表：paper_artifact_heads、processing_job_events、ocr_page_checkpoints。
- 必选 P3 物理表：document_chunk_embeddings、artifact_translation_checkpoints。
- `processing_jobs` 的 PRAGMA migration-order columns 必须精确为 P1 保留列 `id|paper_id|job_type|source_mode|status|progress_json|attempt|max_attempts|idempotency_key|error_code|error_message|created_at|started_at|finished_at|cancelled_at`，随后是 P2 additive 列 `source_document_id|artifact_id|spec_json|available_at|lease_owner|lease_token|lease_expires_at|heartbeat_at|cancel_requested_at|result_json|updated_at|retry_of_job_id|retry_sequence`。`spec_json` 必须为 non-null canonical v1 envelope；任何 fingerprint、备份、恢复、回滚或旧 Node smoke 都不得删列、改名、忽略该列或从 `progress_json` 重建请求。
- `alembic_version` 作为 revision metadata 单独校验唯一值 `20260807_03`。schema trigger inventory 的名称集合与总数必须精确为五个：P2 `processing_jobs_spec_guard_insert|processing_jobs_spec_guard_update`，以及 P3 `document_chunks_fts_ai|document_chunks_fts_ad|document_chunks_fts_au`；逐个固定 normalized SQL SHA，拒绝 missing、rename、额外 lookalike 或同名 SQL drift。`document_chunks_fts` virtual table、三个 FTS trigger 的 insert/delete/update behavior oracle、external-content rowid join count 与 canonical logical content hash另行校验；FTS shadow tables 是派生实现细节，不列为业务表，也不得因排除其 physical layout 而跳过 FTS 逻辑一致性。
- 每张表按 PRAGMA table_info 的 cid 顺序编码列，按声明主键列排序；无主键表按全部 canonical row bytes 排序。
- 单值编码：NULL 为 {"t":"null"}；integer 为十进制字符串；real 为 IEEE-754 十六进制；text 为 NFC UTF-8；blob 为 lowercase hex。禁止用语言默认 repr。
- tableRowHash 是每行 canonical JSON bytes 加 LF 后的流式 SHA-256；primaryKeySetHash 只编码主键；tableCount 是同一 read transaction 内 COUNT。
- papersLegacyColumnsHash 单独覆盖 id、explainer、pdf_path；translationsLegacyContentHash 覆盖 paper_id、content。
- 报告含 schemaVersion=1、databaseLineageId、subjectDatabaseId、subjectKind、parentBackupId、parentManifestSha256、alembicHeads、sqliteIntegrity、foreignKeyViolations、tables、ordered-column inventory、五 trigger 的 normalized SQL hashes 与 `canonicalDataSha256`；generatedAt 不参与 data hash。`canonicalDataSha256` 只聚合本节 22 表/Alembic/五 trigger/FTS/legacy-column canonical fields，P6 schema 明确禁止名为 `logicalSha256` 的字段。规范化路径只参与 subject identity，不进入可跨机器比较的 lineage identity。
- strict read-only compare 要求所有 22 张必选 application table 的 count、PK set hash 与 row hash全等，`processing_jobs` ordered columns（含旧列与 `spec_json`）全等，并要求 Alembic、五 trigger schema 与 FTS logical hash 全等；write-smoke compare 另用显式 new/aux-table delta ledger，不得用宽松规则替代 strict compare，也不得允许任何旧表 hash 变化。
- 所有 P6 backup/restore-check、descendant、restore-install-rehearsal 与 rollback/recovery evidence 还必须复验 P2 冻结的 `processingJobs` ordered projection显式包含 `spec_json`，`processingJobSpecs=(id,spec_json)` 的 count 等于 `processing_jobs` count、每个 envelope strict-decode且 aggregate hash可重算，并复验上述五 trigger exact inventory。运行时回滚保留这些 additive 列与 trigger；只有另行授权的数据恢复 Interface 才能安装数据库文件，但仍不得把缺失这些对象的副本报告为 revision `20260807_03` compatible。

P0 `backend.app.cli.database_backup inspect` 的既有 `logicalSha256` 是 **backupCompatibleLogicalSha256** 的唯一来源：它与 create/verify/restore-check 使用完全相同的 logical fingerprint 算法，专用于证明 quiesced Live 与 cutover backup 等价。执行记录中的本地变量和说明必须使用 `backupCompatibleLogicalSha256`；P6 `fingerprint` 的 `canonicalDataSha256` 只供 P6 strict pre/post compare。两种 hash 的输入集合和版本不同，任何比较、字段别名、fallback 或接受 P6 `logicalSha256` 都是 schema error。

## typed build/database/startup/handoff identity boundary

- `BuildIdentityManifest` 只允许字段 `{schemaVersion,manifestKind="build",buildId,gitRevision,dirty,sourceTreeHash,sourceEntries,buildArtifactHash,pythonArtifacts,frontendArtifacts,resolvedComposeSha256,imageDigests,generatedAt}`。`buildId` 是排除 `generatedAt` 后 canonical payload 的 SHA-256；manifest 只能位于调用者指定 bound directory 下的 `frozen-build-identity-<buildId>.json`，以 O_EXCL 创建且永不覆盖。失败后若修改任一 source/build byte，必须重新构建得到新 buildId/新路径，禁止覆盖或复用旧 identity。它证明 exact source/build，不含 `databaseLineageId`、`subjectDatabaseId`、backup/Manifest、SQLite path 或 platform file identity。
- `DatabaseEvidenceIdentityManifest` 只允许字段 `{schemaVersion,manifestKind="database",databaseLineageId,subjectDatabaseId,subjectKind,resolvedPathHash,platformFileIdentity,parentBackupId,parentManifestSha256,parentLiveSubjectDatabaseId,originReceiptPath,originReceiptFileSha256,originReceiptSha256,generatedAt}`。它证明由 P0 `OriginReceipt` 锚定的 lineage/subject/parent chain，不含 git revision、sourceTreeHash、buildArtifactHash、Compose 或 image digest。
- `ProductionStartupSnapshot` 只允许字段 `{schemaVersion,manifestKind="production-startup",runId,finalEvidenceRunManifestPath,finalEvidenceRunManifestSha256,buildIdentityManifestPath,buildIdentityManifestSha256,databaseIdentityManifestPath,databaseIdentityManifestSha256,originReceiptPath,originReceiptFileSha256,runtimeNamespace,environment,processRoles,modeMap,frozenNodeRollbackMapSha256,createdAt,startupSnapshotSha256}`。`modeMap` 必须与本计划 production fixed map 完全相等；缺字段、额外字段、未知值或 runtime env override 均在任何 socket/DB/provider/lock 副作用前拒绝。snapshot 位于 exact run root、O_EXCL 创建，path+file SHA 同时绑定 CutoverLease 与 promotion authorization；`promote` 必须显式接收 canonical path+SHA，禁止逐项环境变量拼出另一份配置。
- `HandoffReceipt` 只允许字段 `{schemaVersion,manifestKind="handoff-receipt",receiptId,runId,authorizationPath,authorizationSha256,cutoverLeasePath,cutoverLeaseFinalSha256,buildIdentityManifestPath,buildIdentityManifestSha256,databaseIdentityManifestPath,databaseIdentityManifestSha256,originReceiptPath,originReceiptFileSha256,startupSnapshotPath,startupSnapshotSha256,ownerMarkerPath,ownerMarkerVersion,pythonRoleLockIdentities,pythonProcessIdentities,promotionSmokePath,promotionSmokeSha256,frozenNodeRollbackMapSha256,createdAt,receiptSha256}`；`receiptId` 是 lowercase 32-hex GUID。它只在全部 Python smoke 通过且 owner 已 CAS 为 `python_active` 后 O_EXCL 写入；owner marker 原子引用 exact receipt path/SHA。receipt 不可变，rollback progress 写入另一个 durable hash-chained recovery lease。
- 本节四种 artifact 与 P0 `OriginReceipt` 合计五种 runtime identity artifact，分别使用不同 Pydantic model、`manifestKind` 与 CLI 参数。需要多种 identity 的命令必须逐项显式接收 exact path+SHA；只有创建 verified descendant/installed subject 的命令改为接收 `--parent-database-identity-manifest <exact>` 并返回新的 exact child manifest path。只需要一种 identity 的命令也必须使用其准确参数名；全计划禁止旧别名 `--parent-live-database-identity-manifest`、泛化 `--identity-manifest`、latest/glob 或从环境重建 startup snapshot。

## database evidence identity v1

- `databaseLineageId` 必须等于 P0 fixed `OriginReceipt.databaseLineageId` 并由其 `{version,originBackupId,originManifestSha256,originLogicalSha256}` 重算。P6 每次读取 database manifest 时先验证 `originReceiptPath + originReceiptFileSha256 + originReceiptSha256`，再重新验证 receipt 命名的 exact origin backup/Manifest；它标识本次迁移的数据谱系，不随后续 Live 写入或 restore copy 改变。禁止以另一份可 verify backup、latest/glob、随机运行 UUID、当前内容 hash 或新 receipt 代替。
- `subjectDatabaseId` 标识一次 gate 窗口中的具体 SQLite 文件实例，固定为 `{version,databaseLineageId,subjectKind,resolvedPathHash,platformFileIdentity,parentBackupId,parentManifestSha256}` 的 canonical SHA-256。Windows 使用 volume serial + file ID，POSIX 使用 device + inode；文件被替换后 identity 必须改变。正常行内容写入不改变该 ID。
- Live strict pre/post、Live reconciliation 与 final migration evidence 必须使用同一 `subjectKind=live` 和同一 subjectDatabaseId。write-smoke pre/post 使用同一独立 `subjectKind=write_smoke`；restore rehearsal、Node rollback/Python recovery 与 restore-install-rehearsal 可以各有独立 subjectDatabaseId。
- 每个非 Live subject 必须携带产生它的 verified `parentBackupId + parentManifestSha256`、父 Live subjectDatabaseId 与共同 databaseLineageId；gate 重新验证 manifest/hash/文件 identity 的链。所有 evidence 共享 databaseLineageId，但禁止笼统要求隔离副本与 Live 共享 subjectDatabaseId。

## provenance-safe legacy reconciliation ledger v1

- 输入 legacy 内容仅为非空 `papers.explainer` 与 `translations.content`；`notes` 与 `paper_vectors` 是独立 canonical 数据，不是 GeneratedArtifact migration source，ledger 只记录 `preserved_legacy_only` 且禁止自动导入、清空或删除。
- ledger 每项固定为 `{paperId,kind,legacyLocator,legacyContentSha256,classification,sourceDocumentId,artifactId,artifactContentSha256,provenanceEvidenceSha256,reasonCode}`，按 `(paperId,kind)` code-point 顺序；正文、PDF path、credential、provider raw body 与 traceback 不进入 ledger。
- `proven_migrated` 只允许在 ready GeneratedArtifact 的 canonical content hash 等于 legacy hash、其 `source_document_id` 指向同 paper 的 ready SourceDocument、artifact/source mode 与 provenance metadata 一致，且 provenance evidence hash 可由当前行重算时成立。它证明等价但仍不删除 legacy 字段。
- `legacy_only_unprovable` 用于不存在 eligible artifact、artifact 缺 SourceDocument relation、historical origin 无法证明、source stale/failed，或 provenance metadata 不完整；不得根据 paper_id、相同标题、相同 PDF path、时间接近、内容相等本身伪造 relation 或新 SourceDocument。该项的 `sourceDocumentId`、`artifactId`、`artifactContentSha256` 与 `provenanceEvidenceSha256` 必须为 null。
- `mismatch` 用于 provenance-complete candidate 的 canonical content hash 与 legacy 不同、多个 candidate 不能按 canonical head/ready selection 唯一决定、cross-paper/source-mode relation、FK/status/decode/repository 错误。两份内容都保留，禁止“选最新”或覆盖任一侧；`reasonCode` 精确区分 `CONTENT_HASH_MISMATCH|AMBIGUOUS_CANDIDATES|SOURCE_RELATION_INVALID|NEW_STATE_INVALID`。
- shutdown gate 要求每个非空 legacy explainer/translation 恰有一项且无重复/遗漏；允许的分类集合精确为 `proven_migrated|legacy_only_unprovable|mismatch`。任何 `mismatch` 阻止 shutdown；前两类继续保留旧字段供回滚。ledger 生成与 compare 全程 `mode=ro/query_only`，零 INSERT/UPDATE/DELETE/backfill。

## Task 1：实现可证明的 canonical data fingerprint

**Files:**
- Modify: backend/app/api/compat/__init__.py（P4 已创建）
- Create: backend/app/api/compat/data_fingerprint.py
- Modify: backend/app/api/compat/database_identity.py（P4 已创建唯一 typed manifest；P6 只增加验证/复用）
- Create: backend/app/cli/compatibility.py
- Create: backend/tests/test_data_fingerprint.py

- [ ] **Step 1（2–5 分钟）：写 canonical scalar 红测**

新增 DataFingerprintTests.test_canonical_encoding_is_stable_for_null_real_text_blob，固定 NULL、-0.0、浮点、组合 Unicode、换行与 blob，断言精确 bytes 和 SHA-256。

- [ ] **Step 2（2–5 分钟）：确认 canonical 红测**

Run:

~~~powershell
.\.venv\Scripts\python.exe -B -m unittest backend.tests.test_data_fingerprint.DataFingerprintTests.test_canonical_encoding_is_stable_for_null_real_text_blob -v
~~~

Expected RED: backend.app.api.compat.data_fingerprint 不存在。

- [ ] **Step 3（2–5 分钟）：实现 v1 scalar/row encoder**

显式类型 tag；text 先 NFC；real 使用 struct.pack 的固定大端 hex；JSON 使用 ensure_ascii=false、固定 separators 和 UTF-8；任何未支持 SQLite type 立即抛 FINGERPRINT_TYPE_UNSUPPORTED。

- [ ] **Step 4（2–5 分钟）：重新运行 canonical 定向测试并确认 GREEN**

Run:

~~~powershell
.\.venv\Scripts\python.exe -B -m unittest backend.tests.test_data_fingerprint.DataFingerprintTests.test_canonical_encoding_is_stable_for_null_real_text_blob -v
~~~

Expected GREEN: golden bytes/hash 精确相等。

- [ ] **Step 5（2–5 分钟）：写 table snapshot 红测**

新增 `test_fingerprint_reports_required_counts_pk_and_legacy_hashes`、`test_fingerprint_emits_canonical_data_sha_and_forbids_backup_logical_field` 与 `test_fingerprint_freezes_processing_job_spec_and_exact_five_trigger_inventory`，创建乱序 fixture，断言 22 张必选 application table、PK set hash、row hash、两个 legacy column hash、唯一 Alembic head、`processing_jobs` exact ordered columns、`processingJobSpecs` count/hash/strict decode、两个 spec guard + 三个 FTS trigger 的 exact name/normalized SQL SHA、FTS virtual schema/behavior/join/logical hash、integrity、FK 与可重算 `canonicalDataSha256`；strict P6 result model 必须拒绝未知 `logicalSha256`，不得给同一字段换算法。

- [ ] **Step 6（2–5 分钟）：确认 snapshot 红测**

Run:

~~~powershell
.\.venv\Scripts\python.exe -B -m unittest backend.tests.test_data_fingerprint.DataFingerprintTests.test_fingerprint_reports_required_counts_pk_and_legacy_hashes backend.tests.test_data_fingerprint.DataFingerprintTests.test_fingerprint_emits_canonical_data_sha_and_forbids_backup_logical_field backend.tests.test_data_fingerprint.DataFingerprintTests.test_fingerprint_freezes_processing_job_spec_and_exact_five_trigger_inventory -v
~~~

Expected RED: fingerprint 尚未枚举 tables/legacy hashes/canonicalDataSha256、P2 JobSpec column/projection或 exact five-trigger inventory，或仍用 `logicalSha256` 命名 P6 aggregate。

- [ ] **Step 7（2–5 分钟）：实现单 transaction snapshot**

以 mode=ro 打开，BEGIN 后运行 quick_check、foreign_key_check、counts 和 streams；finally ROLLBACK/close。缺任一必选表返回 REQUIRED_TABLE_MISSING，不默默跳过。

- [ ] **Step 8（2–5 分钟）：重新运行 snapshot 定向测试并确认 GREEN**

Run:

~~~powershell
.\.venv\Scripts\python.exe -B -m unittest backend.tests.test_data_fingerprint.DataFingerprintTests.test_fingerprint_reports_required_counts_pk_and_legacy_hashes backend.tests.test_data_fingerprint.DataFingerprintTests.test_fingerprint_emits_canonical_data_sha_and_forbids_backup_logical_field backend.tests.test_data_fingerprint.DataFingerprintTests.test_fingerprint_freezes_processing_job_spec_and_exact_five_trigger_inventory -v
~~~

Expected GREEN: count/hash 与 golden 相等，`processing_jobs.spec_json` projection及五 trigger inventory完整，`canonicalDataSha256` 可重算且插入顺序变化不改变；unknown/alias `logicalSha256` 被拒绝。

- [ ] **Step 9（2–5 分钟）：写 strict/explained compare policy 红测**

新增 `test_strict_compare_rejects_any_table_delta`、`test_explained_write_compare_requires_exact_new_table_delta_ledger_and_unchanged_legacy`、`test_cutover_backup_equality_uses_backup_compatible_logical_sha` 与 `test_canonical_data_sha_is_not_accepted_as_backup_logical_sha`。第一项逐表改一行并要求 strict 失败；第二项覆盖合法 processing/source delta、漏 row、多 row、错误 before/after hash、未知 table、旧表变化，只有精确 ledger 通过。后两项使用一对让 P0 logical fingerprint 与 P6 canonical aggregate 明确不同的 fixture，证明 cutover equality 只接受 `database_backup inspect.logicalSha256`，拒绝把 `canonicalDataSha256`、同名 alias 或缺 P0 inspect evidence 代入。

- [ ] **Step 10（2–5 分钟）：运行并确认 compare policy RED**

Run:

~~~powershell
.\.venv\Scripts\python.exe -B -m unittest backend.tests.test_data_fingerprint.DataFingerprintTests.test_strict_compare_rejects_any_table_delta backend.tests.test_data_fingerprint.DataFingerprintTests.test_explained_write_compare_requires_exact_new_table_delta_ledger_and_unchanged_legacy backend.tests.test_data_fingerprint.DataFingerprintTests.test_cutover_backup_equality_uses_backup_compatible_logical_sha backend.tests.test_data_fingerprint.DataFingerprintTests.test_canonical_data_sha_is_not_accepted_as_backup_logical_sha -v
~~~

Expected RED: CLI 尚无两个显式 mode 或 explained policy 错误接受未解释/legacy delta。

- [ ] **Step 11（2–5 分钟）：实现精确 CLI**

`fingerprint --database data/app.db --output data/compatibility/pre-convergence.json` 只读 DB，并以 exclusive create 写只含 `canonicalDataSha256` 的 P6 报告；它不实现或输出 backup-compatible `logicalSha256`。`compare --mode strict-readonly --before data/compatibility/pre-convergence.json --after data/compatibility/post-convergence.json` 比较全部 22 张 application table 的 counts/PK/row hashes、canonicalDataSha256、Alembic/FTS evidence 与 legacy hashes，任一差异 exit 2；`compare --mode explained-write --before data/compatibility/pre-write-smoke.json --after data/compatibility/post-write-smoke.json --delta-ledger data/compatibility/write-smoke-delta.json` 仍要求全部旧表 hashes 全等，并逐条验证新/aux table 允许 delta 的 table、PK、operation、jobId、source/artifact ID 与 evidence hash，未列出或数量/hash不符即 exit 2。路径/JSON/两类 hash 字段混用错误 exit 1。

- [ ] **Step 12（2–5 分钟）：重新运行 compare policy 定向测试并确认 GREEN**

Run:

~~~powershell
.\.venv\Scripts\python.exe -B -m unittest backend.tests.test_data_fingerprint.DataFingerprintTests.test_strict_compare_rejects_any_table_delta backend.tests.test_data_fingerprint.DataFingerprintTests.test_explained_write_compare_requires_exact_new_table_delta_ledger_and_unchanged_legacy backend.tests.test_data_fingerprint.DataFingerprintTests.test_cutover_backup_equality_uses_backup_compatible_logical_sha backend.tests.test_data_fingerprint.DataFingerprintTests.test_canonical_data_sha_is_not_accepted_as_backup_logical_sha -v
~~~

Expected GREEN: 四个 tests OK；strict 与 explained evidence 不可互换，backupCompatibleLogicalSha256 与 canonicalDataSha256 也不可互换。

- [ ] **Step 13（2–5 分钟）：characterize P4 已交付的 lineage/subject identity**

不为 P4 已存在的 `DatabaseEvidenceIdentityManifest` 计算/创建能力制造伪 RED。先运行 P4 characterization：`DatabaseIdentityTests.test_v1_lineage_is_stable_and_subject_is_file_instance_specific`、`test_p0_origin_receipt_is_exclusive_and_tamper_evident`、`test_live_identity_rejects_verified_origin_not_named_by_p0_receipt`。它们必须在未改 P4 行为时 GREEN，证明 P6 只消费 exact OriginReceipt、lineage、subject 与 platform file identity。

~~~powershell
.\.venv\Scripts\python.exe -B -m unittest backend.tests.test_database_identity.DatabaseIdentityTests.test_v1_lineage_is_stable_and_subject_is_file_instance_specific backend.tests.test_database_identity.DatabaseIdentityTests.test_p0_origin_receipt_is_exclusive_and_tamper_evident backend.tests.test_database_identity.DatabaseIdentityTests.test_live_identity_rejects_verified_origin_not_named_by_p0_receipt -v
~~~

- [ ] **Step 14（2–5 分钟）：写并运行 P6 evidence binding/parent-chain verifier 红测**

新增 `DataFingerprintTests.test_p6_evidence_binding_rejects_wrong_subject_parent_chain_or_origin_anchor` 与 `test_p6_evidence_binding_accepts_two_distinct_verified_descendants_in_one_lineage`。红灯只针对 P6 尚不存在的 evidence-binding verifier：交换 capture record 的 database manifest、parent backup/Manifest、OriginReceipt file SHA 或 subjectKind 必须被拒绝；两个合法 descendant 必须共享 lineage 但保持不同 subject。不得重复测试或重写 P4 create/verify capability。

~~~powershell
.\.venv\Scripts\python.exe -B -m unittest backend.tests.test_data_fingerprint.DataFingerprintTests.test_p6_evidence_binding_rejects_wrong_subject_parent_chain_or_origin_anchor backend.tests.test_data_fingerprint.DataFingerprintTests.test_p6_evidence_binding_accepts_two_distinct_verified_descendants_in_one_lineage -v
~~~

Expected RED: P4 characterization 已 GREEN，但 P6 evidence binding/parent-chain verifier 尚不存在或错误接受至少一种 cross-record mismatch；fixture/import/P4 capability 失败不算有效 RED。

- [ ] **Step 15（2–5 分钟）：实现最小 P6 evidence binding verifier**

只读取 P4 typed manifest 与调用者给出的 exact capture/parent paths；重新验证 OriginReceipt exact path/file/self hash、receipt 命名的 parent、platform file identity、lineage/subject/subjectKind 与 parent chain，再返回 `VerifiedEvidenceDatabaseBinding`。不得 create/replace database identity、访问 latest/glob/默认 Live 路径或写 SQLite；capture/gate/runtime lease 共用该 verifier，不各自重算另一种 ID。

- [ ] **Step 16（2–5 分钟）：以相同目标重新运行并确认 GREEN**

~~~powershell
.\.venv\Scripts\python.exe -B -m unittest backend.tests.test_data_fingerprint.DataFingerprintTests.test_p6_evidence_binding_rejects_wrong_subject_parent_chain_or_origin_anchor backend.tests.test_data_fingerprint.DataFingerprintTests.test_p6_evidence_binding_accepts_two_distinct_verified_descendants_in_one_lineage -v
~~~

Expected GREEN: P6 verifier 拒绝 wrong subject/parent/origin binding，接受共同 lineage 下不同且独立可验的 descendants；P4 create/verify behavior 未被复制或改变。

- [ ] **Step 17（2–5 分钟）：运行 Task 1 回归**

Run:

~~~powershell
.\.venv\Scripts\python.exe -B -m unittest backend.tests.test_data_fingerprint -v
~~~

Expected: 全部 OK，测试 DB 无 -wal/-shm 残留。

## Task 1A：以 TDD 建立 provenance-safe legacy-to-new reconciliation ledger

**Files:**
- Create: backend/app/api/compat/legacy_reconciliation.py
- Create: backend/tests/test_legacy_reconciliation.py
- Modify: backend/app/cli/compatibility.py
- Evidence output only: data/compatibility/legacy-reconciliation-v1.json

- [ ] **Step 1（2–5 分钟）：写分类、完整性与只读红测**

新增 `LegacyReconciliationTests.test_explainer_and_translation_require_proven_source_relation_and_content_hash`、`test_unprovable_history_keeps_null_source_relation_and_never_backfills`、`test_mismatch_ambiguity_or_invalid_relation_fails_gate`、`test_ledger_counts_sets_and_hashes_cover_every_legacy_item_exactly_once`、`test_notes_and_paper_vectors_are_preserved_without_claimed_migration` 与 `test_reconciliation_is_readonly`。fixture 必须同时覆盖 explainer/translation 的三类、相同 content 但无 SourceDocument relation、cross-paper relation、duplicate candidate、hash 差异、null/empty legacy、notes 与 vector bytes；记录 DB bytes/mtime/sidecars/total_changes，并注入 write/backfill spy。

- [ ] **Step 2（2–5 分钟）：运行测试并确认 RED**

Run: `.\.venv\Scripts\python.exe -B -m unittest backend.tests.test_legacy_reconciliation -v`

Expected RED: reconciliation module/CLI 不存在或把仅 content 相等误判为 proven；任何测试写 DB、使用真实 Live path 或省略 mismatch case 都不是有效 RED。

- [ ] **Step 3（2–5 分钟）：实现最小只读分类器与 canonical evidence**

在一次 `mode=ro/query_only` snapshot 内枚举所有非空 legacy explainer/translation，以规范化 UTF-8 bytes 逐内容 SHA-256；仅按上节完整 SourceDocument relation 证明 `proven_migrated`，无法证明时输出 `legacy_only_unprovable` 且 relation fields 为 null，所有 hash/relation/ambiguity/state 差异输出 `mismatch`。CLI `reconcile-legacy --database <isolated-or-quiesced-db> --database-identity-manifest <exact> --output <exclusive-new-json>` 输出 schemaVersion、databaseLineageId、subjectDatabaseId、subjectKind、parent chain、alembicRevision、itemCount、classificationCounts、按 kind/paperId 的完整 set hashes、legacy/artifact/provenance aggregate hashes、notes/paper_vectors preservation counts+PK-set+row hashes与 items；不包含正文。输入 manifest 的 resolved file identity 必须与 `--database` 一致；该 CLI 不接受 build identity，外层 `capture-evidence` 负责绑定本次 build。

- [ ] **Step 4（2–5 分钟）：以相同命令确认 GREEN**

Run: `.\.venv\Scripts\python.exe -B -m unittest backend.tests.test_legacy_reconciliation -v`

Expected GREEN: 六个测试全部 OK；每个 legacy `(paper_id,kind)` 恰有一项，counts、集合与逐内容 hash 可重算，`legacy_only_unprovable` 不产生 relation/backfill，任一 `mismatch` 返回 gate failure，notes/paper_vectors bytes 保留，数据库零变化。

- [ ] **Step 5（2–5 分钟）：把 ledger 纳入 convergence 与 shutdown evidence**

CompatibilityGate 必须验证 artifact SHA、database/source/build identity、itemCount=三类 count 之和、explainer/translation 输入集合等于 ledger 集合、aggregate hash 可重算、notes/paper_vectors preservation hashes 等于 strict fingerprint，且 `mismatch=0`。`legacy_only_unprovable` 不是“已迁移”，报告必须分别列出其数量与完整 `(paperId,kind)` set hash；不得用总数相等声称所有旧内容已迁移。

## Task 2：冻结 MCP tools/list 与九工具结果

**Files:**
- Create: backend/tests/fixtures/mcp/tool_schemas.json
- Create: backend/tests/fixtures/mcp/results/legacy_results.json
- Create: backend/tests/test_mcp_contract.py
- Modify: test/test_mcp_server.py
- Reference: agent/mcp_server.py

- [ ] **Step 1（2–5 分钟）：写工具数量/schema 红测**

新增 McpContractTests.test_exact_nine_tool_schemas_match_snapshot，调用 FastMCP tools/list，比较九个名称、description、inputSchema、required/default；fixture 故意先不创建。

- [ ] **Step 2（2–5 分钟）：确认 schema 红测**

Run:

~~~powershell
.\.venv\Scripts\python.exe -B -m unittest backend.tests.test_mcp_contract.McpContractTests.test_exact_nine_tool_schemas_match_snapshot -v
~~~

Expected RED: tool_schemas.json FileNotFoundError；tools/list 本身成功并返回 9。

- [ ] **Step 3（2–5 分钟）：从当前实现写 exact snapshot**

按 tools/list 返回顺序保存九工具完整 schema，不手工删 description；测试另以 set 断言无缺失/额外工具。

- [ ] **Step 4（2–5 分钟）：重新运行 schema 定向测试并确认 GREEN**

Run:

~~~powershell
.\.venv\Scripts\python.exe -B -m unittest backend.tests.test_mcp_contract.McpContractTests.test_exact_nine_tool_schemas_match_snapshot -v
~~~

Expected GREEN: unittest summary reports 1 test and OK；工具数量恰为 9。

- [ ] **Step 5（2–5 分钟）：写结果 golden 与 bounded SourceDocument 红测**

新增 `test_legacy_results_cover_normal_empty_unicode_and_errors`，覆盖每个工具至少一个成功结果；get/related 再覆盖 missing id；search/semantic/reviews 覆盖空结果、limit clamp 与稳定排序。新增 `test_get_paper_source_document_contract_decision_is_explicit`，读取独立 contract-decision fixture 并锁定：legacy/shadow 结果不增加字段，application 允许一个 optional `sourceDocument.native/ocr` bounded view，input schema/工具数不变，长 Markdown 永不进入 MCP。

- [ ] **Step 6（2–5 分钟）：确认 result 红测**

Run:

~~~powershell
.\.venv\Scripts\python.exe -B -m unittest backend.tests.test_mcp_contract.McpContractTests.test_legacy_results_cover_normal_empty_unicode_and_errors backend.tests.test_mcp_contract.McpContractTests.test_get_paper_source_document_contract_decision_is_explicit -v
~~~

Expected RED: legacy_results.json/application additive golden 缺失；不得连接真实 data/app.db。

- [ ] **Step 7（2–5 分钟）：保存脱敏 legacy golden**

使用隔离 fixture DB 运行当前 agent/mcp_server.py；保存完整 legacy JSON，保留中文错误文本、空值、score 类型、pagination fields 和 overview bucket keys。另保存 contract-decision fixture，规定 application `get_paper.sourceDocument` additive golden 只允许 `native|ocr` 两个 key 及各自 null/`currentId,status,updatedAt,error` bounded view，不改工具 input schema，不新增工具；具体 application golden 在 Task 3 随实现生成并验证。

- [ ] **Step 8（2–5 分钟）：重新运行 result golden 定向测试并确认 GREEN**

Run:

~~~powershell
.\.venv\Scripts\python.exe -B -m unittest backend.tests.test_mcp_contract.McpContractTests.test_legacy_results_cover_normal_empty_unicode_and_errors backend.tests.test_mcp_contract.McpContractTests.test_get_paper_source_document_contract_decision_is_explicit -v
~~~

Expected GREEN: 九工具 legacy 正常/边界结果与 snapshot 全等；application 只允许 golden 中那一个 bounded additive field，未批准的任何额外 field 仍失败。

- [ ] **Step 9（2–5 分钟）：运行原 MCP 套件**

Run:

~~~powershell
.\.venv\Scripts\python.exe -B -m unittest discover -s test -p "test_mcp_server.py" -v
~~~

Expected: 现有 schema、只读和行为测试全部 OK；没有删改断言来迁就新实现。

## Task 3：实现 application MCP adapter 与新表优先/旧字段回退

**Files:**
- Create: backend/app/api/mcp.py
- Modify: backend/app/application/library_queries.py
- Create: backend/app/repositories/read_only.py
- Modify: backend/tests/test_mcp_contract.py

- [ ] **Step 1（2–5 分钟）：写 source/artifact 解析红测**

新增 `test_application_tools_prefer_ready_new_rows_and_fallback_to_legacy_fields`；同一 paper 同时放旧 explainer/translation 与不同的新 ready artifact，断言 `ARTIFACT_READ_MODE=prefer_new` 新值优先；删除 eligible new row 后断言 papers.explainer 与 translations.content 精确回退。另覆盖 `legacy` mode 永远旧字段、new query/decode/repository error fail closed 不回退，以及 ready/stale/failed/mixed-provider rows 的 eligible selection。

- [ ] **Step 2（2–5 分钟）：确认解析红测**

Run:

~~~powershell
.\.venv\Scripts\python.exe -B -m unittest backend.tests.test_mcp_contract.McpContractTests.test_application_tools_prefer_ready_new_rows_and_fallback_to_legacy_fields -v
~~~

Expected RED: backend.app.api.mcp 不存在或仍只读旧字段。

- [ ] **Step 3（2–5 分钟）：扩展 LibraryQueries**

只接受 P0 固定 `ARTIFACT_READ_MODE=legacy|prefer_new`。`legacy` 不查询新 artifact；`prefer_new` 以 eligible ready、updated/generated timestamp、id 的固定规则选择当前 GeneratedArtifact并关联 ready SourceDocument，仅“没有 eligible row”回退旧字段。新表缺失、query/decode/repository 错误返回分类 safe error，不回退；记录脱敏 telemetry 但不改变 wire。

- [ ] **Step 4（2–5 分钟）：实现九个 handlers**

每个 handler 接收与现有函数完全相同参数，复用 clamp/date/chunk/compact 语义。`get_explainer/get_translation` 先完成 eligible selection/fallback，再对同一 selected content 应用既有 offset/max_chars 分页；不得分别截断新旧内容后再挑结果。除 Task 2 批准的 application-only bounded `get_paper.sourceDocument` 外，不把 SourceDocument/Artifact internal IDs 添加到旧 MCP 结果。

- [ ] **Step 5（2–5 分钟）：重新运行 source/artifact 解析测试并确认 GREEN**

Run:

~~~powershell
.\.venv\Scripts\python.exe -B -m unittest backend.tests.test_mcp_contract.McpContractTests.test_application_tools_prefer_ready_new_rows_and_fallback_to_legacy_fields -v
~~~

Expected GREEN: 新表优先和旧字段回退两种 fixture 均通过。

- [ ] **Step 6（2–5 分钟）：写九工具 application golden 红测**

新增 `test_application_mode_matches_all_legacy_goldens`，逐个运行 application handler 并与 Task 2 golden 比较；comparison 只允许 `get_paper.sourceDocument` 的 exact additive golden，其他字段、null、排序、分页、错误文本均要求零差异。另新增 `test_explainer_translation_pagination_uses_selected_content`，用新旧内容在 page boundary 处放不同 sentinel，证明选择发生在分页之前；新增 `test_get_paper_application_source_document_view_is_mode_specific_and_safe`，覆盖 native/ocr 的 `queued|running|ready|failed|stale|cancelled` 全六状态、mode-specific currentId/time 与 error safe summary redaction。

- [ ] **Step 7（2–5 分钟）：运行并确认 application golden RED**

Run:

~~~powershell
.\.venv\Scripts\python.exe -B -m unittest backend.tests.test_mcp_contract.McpContractTests.test_application_mode_matches_all_legacy_goldens backend.tests.test_mcp_contract.McpContractTests.test_explainer_translation_pagination_uses_selected_content backend.tests.test_mcp_contract.McpContractTests.test_get_paper_application_source_document_view_is_mode_specific_and_safe -v
~~~

Expected RED: application serializer 缺 bounded field、pagination 在 source selection 前发生，或出现未批准 wire diff；不得通过修改 legacy golden 消除失败。

- [ ] **Step 8（2–5 分钟）：修复 wire adapter 而非修改 legacy golden**

只在 backend/app/api/mcp.py 转换字段/排序/错误；LibraryQueries 保持 typed DTO，不承载中文错误文本或 MCP pagination。`get_paper.sourceDocument` 从 mode-specific typed summary 投影，不读取或返回 Markdown；legacy/shadow serializer 保持冻结字段，application serializer 只增加 Task 2 exact golden 批准的 optional field。

- [ ] **Step 9（2–5 分钟）：重新运行九工具 application golden 测试并确认 GREEN**

Run:

~~~powershell
.\.venv\Scripts\python.exe -B -m unittest backend.tests.test_mcp_contract.McpContractTests.test_application_mode_matches_all_legacy_goldens backend.tests.test_mcp_contract.McpContractTests.test_explainer_translation_pagination_uses_selected_content backend.tests.test_mcp_contract.McpContractTests.test_get_paper_application_source_document_view_is_mode_specific_and_safe -v
~~~

Expected GREEN: legacy fields 九工具 golden 零差异；application 只包含 exact approved bounded addition，分页 sentinel 与 redaction tests 全绿。

## Task 4：修复 semantic MCP 真正只读

**Files:**
- Modify: backend/app/repositories/read_only.py
- Create: backend/tests/test_mcp_readonly.py
- Modify: agent/embed.py
- Modify: backend/app/api/mcp.py
- Modify: agent/mcp_server.py

- [ ] **Step 1（2–5 分钟）：写文件级只读红测**

新增 `McpReadonlyTests.test_all_nine_tools_leave_database_bytes_mtime_and_sidecars_unchanged`；复制 fixture DB，记录 SHA-256/size/mtime/sidecars，依次调用九工具后重新比较，并注入 ProcessingQueue/enqueue、SourceDocumentProcessor、OCR registry/provider/transport、embedding write/model-download spies，断言构造与调用均为 0。

- [ ] **Step 2（2–5 分钟）：确认只读红测**

Run:

~~~powershell
.\.venv\Scripts\python.exe -B -m unittest backend.tests.test_mcp_readonly.McpReadonlyTests.test_all_nine_tools_leave_database_bytes_mtime_and_sidecars_unchanged -v
~~~

Expected RED: semantic_search 经 agent/embed.py 打开 writable connection，产生写连接 spy 或 sidecar/mtime 差异。

- [ ] **Step 3（2–5 分钟）：实现统一 read-only connection**

使用 sqlite3.connect 的 file URI、mode=ro、immutable 仅用于 immutable fixture；立即 PRAGMA query_only=ON，设置 row_factory；禁止 executescript、CREATE、INSERT、UPDATE、DELETE、REPLACE 和 schema bootstrap。

- [ ] **Step 4（2–5 分钟）：拆分 embed rank read path**

agent/embed.py 的 rank(query, capped_k, reindex_stale=False) 只加载现有 vectors/chunks；related path 使用 rank(seed_text, capped_k, exclude=id, reindex_stale=False)；缺索引返回空结果/现有 note，不调用 ensure_schema、model download、embed write 或 commit。

- [ ] **Step 5（2–5 分钟）：关闭所有连接**

legacy 与 application handlers 均使用 context manager；semantic/related 的多段查询共享一次只读 snapshot 或分别确保 close；异常路径也关闭。

- [ ] **Step 6（2–5 分钟）：重新运行文件级只读测试并确认 GREEN**

Run:

~~~powershell
.\.venv\Scripts\python.exe -B -m unittest backend.tests.test_mcp_readonly.McpReadonlyTests.test_all_nine_tools_leave_database_bytes_mtime_and_sidecars_unchanged -v
~~~

Expected GREEN: DB SHA-256、size、mtime 全等，调用前后均无 -wal/-shm/-journal；九工具零 enqueue、零持久日志、零 OCR、零 document embed/repair。

- [ ] **Step 7（2–5 分钟）：写 SQL write rejection 红测**

新增 test_mcp_connection_rejects_write_and_reports_zero_total_changes，尝试通过注入 query 执行 UPDATE，断言 OperationalError readonly/query_only，total_changes=0。

- [ ] **Step 8（2–5 分钟）：运行 write rejection 定向测试并确认 GREEN**

Run:

~~~powershell
.\.venv\Scripts\python.exe -B -m unittest backend.tests.test_mcp_readonly.McpReadonlyTests.test_mcp_connection_rejects_write_and_reports_zero_total_changes -v
~~~

Expected GREEN: write 被拒绝，DB bytes 不变。

## Task 5：建立 legacy → shadow → application MCP 切换

**Files:**
- Create: backend/app/api/compat/mcp_shadow.py
- Create: backend/tests/test_mcp_shadow.py
- Modify: agent/mcp_server.py
- Modify: backend/app/api/mcp.py

- [ ] **Step 1（2–5 分钟）：写模式解析红测**

新增 McpShadowTests.test_mode_is_strict_and_defaults_to_legacy，断言缺省 legacy，合法值仅 legacy|shadow|application，大小写或未知值在 stdio 初始化前 fail-fast。

- [ ] **Step 2（2–5 分钟）：确认模式红测**

Run:

~~~powershell
.\.venv\Scripts\python.exe -B -m unittest backend.tests.test_mcp_shadow.McpShadowTests.test_mode_is_strict_and_defaults_to_legacy -v
~~~

Expected RED: agent/mcp_server.py 尚未解析 PAPER_STUDY_MCP_MODE。

- [ ] **Step 3（2–5 分钟）：实现入口模式选择**

九个 @mcp.tool 定义保持原位/原 schema；函数体调用选定 backend。legacy 调用冻结实现，application 调用 backend/app/api/mcp.py，shadow 执行两者但返回 legacy。

- [ ] **Step 4（2–5 分钟）：重新运行模式解析测试并确认 GREEN**

Run:

~~~powershell
.\.venv\Scripts\python.exe -B -m unittest backend.tests.test_mcp_shadow.McpShadowTests.test_mode_is_strict_and_defaults_to_legacy -v
~~~

Expected GREEN: 严格模式解析通过，tools/list 三模式完全相同。

- [ ] **Step 5（2–5 分钟）：写 shadow diff 红测**

新增 `test_shadow_returns_legacy_and_records_canonical_diff`；人为让 application 少一个结果字段，断言 caller 收到完整 legacy，diff 包含 tool、field path、legacy hash、application hash、category，不含正文或 API key。另断言 exact `get_paper.$.sourceDocument` 被标记为 `approved_additive_optional`，只有与 Task 2 contract-decision/application golden 全等时才可解释；其他额外字段仍是 regression。

- [ ] **Step 6（2–5 分钟）：确认 diff 红测**

Run:

~~~powershell
.\.venv\Scripts\python.exe -B -m unittest backend.tests.test_mcp_shadow.McpShadowTests.test_shadow_returns_legacy_and_records_canonical_diff -v
~~~

Expected RED: 当前没有 shadow recorder 或错误返回 application。

- [ ] **Step 7（2–5 分钟）：实现 canonical diff**

只规范化明确非契约 tracing id；不得忽略行顺序、null/缺字段、浮点、日期、中文错误或 count。唯一 allowlist diff 是 exact application `get_paper.sourceDocument` golden；其内部任一缺失/额外/类型/状态/error redaction 差异都不能忽略。正文只写 SHA-256 与长度，观察文件采用 append-only JSONL 并限制在配置目录，绝不写 SQLite。

- [ ] **Step 8（2–5 分钟）：重新运行 shadow diff 测试并确认 GREEN**

Run:

~~~powershell
.\.venv\Scripts\python.exe -B -m unittest backend.tests.test_mcp_shadow.McpShadowTests.test_shadow_returns_legacy_and_records_canonical_diff -v
~~~

Expected GREEN: 返回 legacy 全等，差异分类精确且无正文泄露。

- [ ] **Step 9（2–5 分钟）：写零差异切换 gate**

新增 `test_application_switch_requires_complete_zero_diff_window`，固定窗口包含九工具、每工具正常/空/错误 fixture；缺任一工具、approved additive golden 不匹配或有一条 unexplained diff 都返回 MCP_SHADOW_NOT_CONVERGED。通过窗口要求除 exact approved field 外零差异，且 MCP read-only/zero-enqueue/zero-OCR evidence 同一 source/build identity。

- [ ] **Step 10（2–5 分钟）：运行并确认 switch gate RED**

Run:

~~~powershell
.\.venv\Scripts\python.exe -B -m unittest backend.tests.test_mcp_shadow.McpShadowTests.test_application_switch_requires_complete_zero_diff_window -v
~~~

Expected RED: gate 未检查 complete window、approved golden、read-only evidence 或 source/build identity 中至少一项。

- [ ] **Step 11（2–5 分钟）：实现 complete-window gate**

验证九工具/三类 fixture coverage、exact approved addition、零 unexplained diff、read-only/zero-enqueue/zero-OCR 与同一 source/build identity；只返回 typed convergence result，不在 gate 中切模式或写 DB。

- [ ] **Step 12（2–5 分钟）：重新运行 switch gate 定向测试并确认 GREEN**

Run:

~~~powershell
.\.venv\Scripts\python.exe -B -m unittest backend.tests.test_mcp_shadow.McpShadowTests.test_application_switch_requires_complete_zero_diff_window -v
~~~

Expected GREEN: complete window 通过；每个缺失/差异 fixture 均分类拒绝。

- [ ] **Step 13（2–5 分钟）：运行 Task 5 回归**

Run:

~~~powershell
.\.venv\Scripts\python.exe -B -m unittest backend.tests.test_mcp_shadow -v
~~~

Expected: 全部 OK；application 仅在完整窗口后可启用，切回 legacy 不需要迁移。

## Task 6：汇总兼容 evidence 并建立硬门禁

**Files:**
- Create: backend/app/api/compat/gates.py
- Create: backend/app/api/compat/build_identity.py
- Create: backend/app/api/compat/evidence_capture.py
- Create: backend/tests/test_compatibility_gate.py
- Create: backend/tests/test_build_identity.py
- Create: backend/tests/test_evidence_capture.py
- Modify: backend/app/cli/compatibility.py
- Modify: docs/DATABASE.md

- [ ] **Step 1（2–5 分钟）：写缺 evidence 必失败红测**

新增 CompatibilityGateTests.test_shutdown_gate_names_every_missing_evidence，空目录运行 shutdown gate，断言逐项缺 migration、strict data hash、database lineage/subject/parent chain、legacy reconciliation、HTTP/v2/NDJSON/UI、Worker/Scheduler、MCP/CredentialStore、Obsidian、P0 backup、explained write-smoke、candidate profile、frozen Node rollback、Python recovery、隔离 restore-install-rehearsal、deterministic BoundRoot zero-skip、suite isolation/liveAccessCount=0、machine summaries、canonical startup snapshot、HandoffReceipt/rollback-production contract、final enum/runbook与 final zero-failure suite。测试比较完整稳定 key集合。另新增 pre-quiesce/pre-cutover、cross-run/copied、duplicate与 authorization run/startup path/SHA mismatch tests；全部 fail closed，filename排序不能绕过。

- [ ] **Step 2（2–5 分钟）：确认 gate 红测**

Run:

~~~powershell
.\.venv\Scripts\python.exe -B -m unittest backend.tests.test_compatibility_gate.CompatibilityGateTests.test_shutdown_gate_names_every_missing_evidence backend.tests.test_compatibility_gate.CompatibilityGateTests.test_shutdown_gate_rejects_pre_quiesce_or_pre_cutover_suite_records_and_duplicate_keys backend.tests.test_compatibility_gate.CompatibilityGateTests.test_gate_rejects_cross_run_or_copied_capture_records backend.tests.test_compatibility_gate.CompatibilityGateTests.test_authorization_binds_exact_final_evidence_run -v
~~~

Expected RED: gates module 不存在。

- [ ] **Step 3（2–5 分钟）：定义 evidence manifest**

先实现 `EvidenceRunManifest` strict schema `{schemaVersion,manifestKind="evidence-run",runId,phase,runDirectory,buildIdentityManifestPath,buildIdentityManifestSha256,databaseIdentityManifestPath,databaseIdentityManifestSha256,originReceiptPath,originReceiptFileSha256,expectedKeys,createdAt,runManifestSha256}`；`phase` 只允许 `provisional|final`，`runId` 是 lowercase 32-hex GUID，`runDirectory` 必须精确为调用者给定 evidence root 下的 `run-<runId>` resolved path，self hash 排除自身字段。唯一入口 `create-evidence-run --evidence-root <exact> --run-id <exact> --phase provisional|final --build-identity-manifest <exact-content-addressed> --database-identity-manifest <exact-live> --expected-key <repeatable>...` 自己通过 `BoundRoot` atomic mkdir/O_EXCL 创建此前不存在的 matching run directory，再在仍仅含内部 staging handle 时 exclusive-create `evidence-run-manifest-v1.json`；调用者不得预建或 `-Force` 复用目录。CLI 验证 buildId/path、两种 identity 与 P0 receipt anchor 后返回 runId、runDirectory、manifest exact path/file SHA；已有目录/manifest、runId/path/phase 不一致、空 key/重复 key均拒绝。除 immutable external inputs（content-addressed BuildIdentity、P4 Live DatabaseIdentity、OriginReceipt、owner marker）外，该 run 产生的 backup/restore copy、descendant identity、settings/PDF/Vault/Keyring fixture、stdout/stderr、runner summary、fingerprint、ledger 与 smoke evidence全部必须在 exact run root，由 bound handle 创建且不可跨 root。失败 child、显式 abort 或 watchdog recovery会在该 run root exclusive-create immutable failure/recovery seal；一旦存在，manifest、records、stdout/stderr 与 artifacts 永久封存，capture/gate 不得删除、覆盖、续写或从别处复制；重试只创建另一个新 GUID/run directory。

实现唯一 capture 形状：`compatibility capture-evidence --key <allowlisted> --phase provisional|final --result-kind machine-summary|json-cli --run-manifest <exact> --expected-run-manifest-sha256 <sha> [--cutover-lease <exact> --cutover-token-file <exact-owner-only> --startup-snapshot <exact> --expected-startup-snapshot-sha256 <sha>] --build-identity-manifest <exact> [--database-identity-manifest <exact> | --database-identity-from-json <child-result-field>] --output <exclusive-new-json> [--summary-artifact <exact-run-local-json-or-junit>] [--isolation-manifest <exact-run-local-json>] [--artifact <name>=<exact-path>] [--artifact-from-json <field>] -- <完整 child argv>`。provisional 禁止四个 cutover/startup 参数；final 必须逐项显式提供，不能从 latest/glob、cwd或普通环境变量猜路径。final phase 还强制把参数与本次 operator immutable process snapshot `P6_FINAL_EVIDENCE_RUN_MANIFEST_PATH|SHA256|ID`、`P6_FINAL_WINDOW_LEASE_PATH|TOKEN_FILE`、`P6_PRODUCTION_STARTUP_SNAPSHOT_PATH|SHA256` 交叉验证；provisional phase 强制读取不同且同样完整的 `P6_PROVISIONAL_EVIDENCE_RUN_MANIFEST_PATH|SHA256|ID`。缺一、两组同时存在、phase/env/output parent/run manifest 不同均在 spawn 前拒绝。final child 存活期间凭 exact lease/token capability持续 heartbeat，wrapper/child/heartbeat 任一失败都触发同一 abort。`--database-identity-from-json` 只用于 child 在 verified backup 上创建新 descendant/installed subject 的命令；wrapper 在 child raw 0 后解析返回的 exact manifest path、验证 typed bytes/file identity/parent chain，再写入 record。wrapper 在 spawn 前以 O_EXCL 保留 output，原样保留 argv，不经过 shell 重解释；成功时把 child stdout byte-for-byte 转发给调用者以便 `ConvertFrom-Json`，stderr 只转发 stderr。它必须拒绝未知 key/phase/result adapter、已存在 output、同 run 重复 `(phase,key)`、跨 run artifact/record、manifestKind 错误、build/database/origin/run/startup/lease identity 不匹配、任何新 artifact 写入 run root 之外、存在 failure/recovery seal，以及 argv 中 secret value flag。缺 build manifest 永远拒绝；只有确实访问 SQLite 的 key 才要求且只允许独立 database manifest或 child 返回的独立 database manifest。

每个 capture record 固定含 `schemaVersion`、`producer="compatibility.capture-evidence"`、`runId`、EvidenceRunManifest exact path/file SHA、allowlisted `evidenceKey`、`phase`、`provisional`、`resultKind`、完整 argv、resolved executable/cwd、startedAt/finishedAt、原始 child `exitCode`、`totals/failures/skips`、machine summary artifact path/format/SHA、stdout/stderr artifact path 与 SHA-256、所有显式/JSON-returned artifact path+SHA-256、`BuildIdentityManifest` exact path/SHA/buildId/gitRevision/sourceTreeHash/buildArtifactHash。final record 还绑定显式传入的 canonical ProductionStartupSnapshot path/SHA 与 exact CutoverLease ID/token hash/version；涉及 SQLite 时另含 `DatabaseEvidenceIdentityManifest` exact path/SHA、OriginReceipt anchor 及 databaseLineageId/subjectDatabaseId/subjectKind/parent chain；suite record 另含 isolation manifest SHA、每个 run-local DB/settings/PDF/Vault/Keyring root 与 `liveAccessCount`。所有 unittest/Node/Vitest/Playwright/check runner 都必须通过 `machine-summary` child adapter先 exclusive-create JSON/JUnit；wrapper只从该 artifact 的严格 schema 读取 totals/failures/skips并交叉检查 raw exit，禁止解析 stdout/stderr、正则匹配“OK”或手写 pass boolean。check adapter 的 summary 也必须显式含 `totals=1`、`failures=0|1`、`skips=0`。raw exit 0 但 `failures>0` 或 `skips>0` 一律使 capture non-zero并 seal run；缺 summary、summary path 出 run root、summary 与 exit 矛盾也拒绝。record 以 canonical payload hash 自校验；gate 重新 hash run manifest、startup snapshot、lease identity、record、summary、stdout/stderr 与每个 referenced artifact。

final allowlist 精确为：`build-identity-verify|bound-root-zero-skip|suite-isolation|backend-suite|legacy-python-suite|mcp-server-suite|node-suite|frontend-vitest|frontend-typecheck|frontend-lint|frontend-build|frontend-e2e|migration-head-ready|http-v2-ndjson-static|runtime-worker-scheduler-obsidian|mcp-credentials|legacy-reconciliation|node-quiesce|cutover-backup-create|cutover-backup-verify|cutover-backup-restore-check|live-pre-fingerprint|live-post-fingerprint|strict-readonly-compare|convergence-gate|candidate-production-profile|candidate-write-smoke|explained-write-compare|frozen-node-rollback|python-recovery|restore-install-rehearsal|final-enum-runbook|handoff-contract`。preflight 使用相同业务 key 的 provisional record，但 final gate 按 phase、directory 与 exact build/database manifest SHA 拒绝复用。任何 child non-zero 或 non-zero skips 仍写完整 record，并把 failure exit 传播给 caller；wrapper 自身 schema/identity/duplicate 错误统一 exit 2，禁止把失败改写为 0。

- [ ] **Step 3A（2–5 分钟）：写 capture-evidence 红测**

新增 `EvidenceCaptureTests.test_capture_evidence_exclusive_creates_allowlisted_typed_record_and_propagates_child_exit`、`EvidenceCaptureTests.test_capture_evidence_rejects_duplicate_phase_identity_mismatch_and_handwritten_record`、`EvidenceCaptureTests.test_create_evidence_run_supports_explicit_provisional_snapshot_and_run_local_artifacts`、`EvidenceCaptureTests.test_final_capture_requires_explicit_matching_lease_token_and_startup_snapshot` 与 `EvidenceCaptureTests.test_failed_final_run_is_immutable_and_fresh_run_can_retry`。分别用 exit 0/7 的假 child、重复 key/output、交换 build/database/run manifest、错误 phase/run directory、篡改 stdout/artifact SHA 与手写 `{"ok":true}`，证明完整字段、raw exit propagation 与 fail-closed gate；provisional test 必须通过 `create-evidence-run --phase provisional` 获得 manifest path/SHA/runId，设置三项专用 process env，并拒绝 final env、run-root 外新 artifact 与未建 manifest 的 capture；final test逐项遗漏或交换显式 CutoverLease/token/startup path/SHA并证明 spawn count=0；最后一项让 run A 中途失败并尝试覆盖/补写/复制，再创建 run B 从空 key set 重试，证明 A bytes/mtime 不变且 B 获得独立 runId。fixture/import failure不算有效 RED。

- [ ] **Step 3B（2–5 分钟）：运行 capture-evidence 红测**

~~~powershell
.\.venv\Scripts\python.exe -B -m unittest backend.tests.test_evidence_capture.EvidenceCaptureTests.test_capture_evidence_exclusive_creates_allowlisted_typed_record_and_propagates_child_exit backend.tests.test_evidence_capture.EvidenceCaptureTests.test_capture_evidence_rejects_duplicate_phase_identity_mismatch_and_handwritten_record backend.tests.test_evidence_capture.EvidenceCaptureTests.test_create_evidence_run_supports_explicit_provisional_snapshot_and_run_local_artifacts backend.tests.test_evidence_capture.EvidenceCaptureTests.test_final_capture_requires_explicit_matching_lease_token_and_startup_snapshot backend.tests.test_evidence_capture.EvidenceCaptureTests.test_failed_final_run_is_immutable_and_fresh_run_can_retry -v
~~~

Expected RED: evidence_capture/capture-evidence 尚不存在，或 gate 错误接受重复、identity-mismatched/handwritten record；不是因为假 child 无法运行。

- [ ] **Step 3C（2–5 分钟）：实现 wrapper 并重新运行同一命令确认 GREEN**

~~~powershell
.\.venv\Scripts\python.exe -B -m unittest backend.tests.test_evidence_capture.EvidenceCaptureTests.test_capture_evidence_exclusive_creates_allowlisted_typed_record_and_propagates_child_exit backend.tests.test_evidence_capture.EvidenceCaptureTests.test_capture_evidence_rejects_duplicate_phase_identity_mismatch_and_handwritten_record backend.tests.test_evidence_capture.EvidenceCaptureTests.test_create_evidence_run_supports_explicit_provisional_snapshot_and_run_local_artifacts backend.tests.test_evidence_capture.EvidenceCaptureTests.test_final_capture_requires_explicit_matching_lease_token_and_startup_snapshot backend.tests.test_evidence_capture.EvidenceCaptureTests.test_failed_final_run_is_immutable_and_fresh_run_can_retry -v
~~~

Expected GREEN: 5 tests OK；provisional/final 均先有 immutable run manifest 和显式 env/path/SHA/runId，final另逐项显式绑定 lease/token/startup path/SHA；成功与失败 child 都留下 exclusive typed run-bound record，raw exit、summary、digests、artifact hashes 与 typed identity/run 边界可重验；失败 run 不可修改，新 run 可从头重试。

- [ ] **Step 3D（2–5 分钟）：写并运行 machine summary 与 suite isolation RED**

新增 `MachineSummaryTests.test_all_runner_adapters_require_json_or_junit_and_never_parse_console_text`、`test_exit_zero_with_nonzero_skip_is_failure` 与 `SuiteIsolationTests.test_suite_sandbox_denies_live_paths_network_providers_and_reports_zero_access`。每个 fixture 都准备伪造的“OK”文本、exit 0 + skip=1 summary、missing/malformed/contradictory summary，以及指向 resolved Live DB/settings/PDF/Vault/Keyring 的 path-open/sqlite-connect/provider/network attempt；测试必须先因为 P6 adapter/isolation seam 尚不存在或错误接受而 RED，不能因 runner 不可执行或平台 skip 变红。

~~~powershell
.\.venv\Scripts\python.exe -B -m unittest backend.tests.test_machine_summary.MachineSummaryTests.test_all_runner_adapters_require_json_or_junit_and_never_parse_console_text backend.tests.test_machine_summary.MachineSummaryTests.test_exit_zero_with_nonzero_skip_is_failure backend.tests.test_suite_isolation.SuiteIsolationTests.test_suite_sandbox_denies_live_paths_network_providers_and_reports_zero_access -v
~~~

- [ ] **Step 3E（2–5 分钟）：实现最小 machine summary 与 suite isolation seam**

实现 `machine-summary-runner --adapter unittest|node-test|vitest|playwright|check --summary-output <exclusive-run-local>`：优先调用 runner 原生 JSON/JUnit reporter；unittest 使用自定义 `TestResult` 直接产生 JSON，不解析 `-v` 文本；check adapter捕获 exact raw exit并写 totals=1。实现 `create-suite-isolation --run-manifest <exact> --suite-key <exact> --output <exclusive-run-local>`，为每 suite 创建独立 DB/settings/PDF/Vault/Fake Keyring roots，注入只允许这些 resolved roots 的 path-open/sqlite-connect hooks和零 transport Provider/network adapter；任何 Live attempt 计数并立即失败。fixture 不得共享 mutable DB 或 keyring。

- [ ] **Step 3F（2–5 分钟）：以相同目标重新运行并确认 GREEN**

~~~powershell
.\.venv\Scripts\python.exe -B -m unittest backend.tests.test_machine_summary.MachineSummaryTests.test_all_runner_adapters_require_json_or_junit_and_never_parse_console_text backend.tests.test_machine_summary.MachineSummaryTests.test_exit_zero_with_nonzero_skip_is_failure backend.tests.test_suite_isolation.SuiteIsolationTests.test_suite_sandbox_denies_live_paths_network_providers_and_reports_zero_access -v
~~~

Expected GREEN: 3 tests OK、0 skip；console 文本不能影响 summary，exit 0 + skip>0 被拒绝，每个 sandbox 独立且报告 `liveAccessCount=0`。

- [ ] **Step 4（2–5 分钟）：实现完整 shutdown evidence 聚合**

所有 final 项必须引用同一个 exact `BuildIdentityManifest` path/SHA，并从中得到同一 `gitRevision + sourceTreeHash + buildArtifactHash`；dirty worktree 可以通过，但只能在每项 source hash 完全相同且 manifest 明确 `dirty=true` 时通过。所有 database-related final 项另引用 exact `DatabaseEvidenceIdentityManifest` path/SHA 并共享 databaseLineageId；禁止把 build manifest 当作 database identity。Live migration/strict/reconciliation 项必须同一 live subjectDatabaseId；每组隔离 pre/post 项必须在组内同一 subjectDatabaseId，并通过 parent backup/manifest chain 追溯到该 Live lineage，不能伪称与 Live 是同一 subject。migration head 必须 20260807_03；reconciliation 必须完整覆盖 explainer/translation、逐内容 hash 可重算、`mismatch=0` 并分别报告 `proven_migrated` 与 `legacy_only_unprovable`，notes/paper_vectors preservation hashes 必须一致；MCP 必须九工具完整且除 exact approved optional field 外零 unexplained diff；CredentialStore evidence 必须完整；backup restore 必须是隔离 install rehearsal，不只 restore-check。gate 必须重算时间拓扑：全部 final suite startedAt 严格晚于 node-quiesce finishedAt 与 cutover-backup-restore-check finishedAt，并拒绝任一重复 `(phase,key)`。gate 只读取 allowlisted capture record，不把 `.log`、raw stdout 或业务 artifact 本身误当 evidence record。

- [ ] **Step 5（2–5 分钟）：重新运行 missing-evidence 定向测试并确认 GREEN**

Run:

~~~powershell
.\.venv\Scripts\python.exe -B -m unittest backend.tests.test_compatibility_gate.CompatibilityGateTests.test_shutdown_gate_names_every_missing_evidence backend.tests.test_compatibility_gate.CompatibilityGateTests.test_shutdown_gate_rejects_pre_quiesce_or_pre_cutover_suite_records_and_duplicate_keys backend.tests.test_compatibility_gate.CompatibilityGateTests.test_gate_rejects_cross_run_or_copied_capture_records backend.tests.test_compatibility_gate.CompatibilityGateTests.test_authorization_binds_exact_final_evidence_run -v
~~~

Expected GREEN: test OK，错误逐项列出 Step 1 定义的完整稳定 evidence key 集合且无遗漏、无笼统 not ready。

- [ ] **Step 6（2–5 分钟）：写 source/build identity 红测**

新增 `BuildIdentityTests.test_source_tree_hash_covers_tracked_untracked_and_modified_bytes`、`test_build_artifact_hash_covers_exact_deployed_outputs`、`test_freeze_uses_unique_content_addressed_path_and_never_overwrites` 与 `test_freeze_and_verify_identity_cli_require_typed_build_manifest_and_detect_drift`。临时 Git repo 分别修改 tracked bytes、新增 non-ignored untracked file、改变 executable/symlink mode，断言 source hash 改变；仅改变 evidence/data/cache ignored outputs 不改变。对 Python deploy bundle、frontend asset manifest、compose config 与 image digest 任一 byte/digest 改变，build hash 必须改变。CLI test 还必须证明 `freeze-identity --build-identity-directory <bound>` 只 exclusive-create `frozen-build-identity-<buildId>.json`，同 payload 返回同一已验证 immutable path而不改 bytes/mtime，失败后修改 source 得到新 buildId/新路径；`verify-identity --build-identity-manifest <exact>` 重算并拒绝 drift，database manifest 不能传入任一命令。

- [ ] **Step 7（2–5 分钟）：运行并确认 identity RED**

Run:

~~~powershell
.\.venv\Scripts\python.exe -B -m unittest backend.tests.test_build_identity.BuildIdentityTests.test_source_tree_hash_covers_tracked_untracked_and_modified_bytes backend.tests.test_build_identity.BuildIdentityTests.test_build_artifact_hash_covers_exact_deployed_outputs backend.tests.test_build_identity.BuildIdentityTests.test_freeze_uses_unique_content_addressed_path_and_never_overwrites backend.tests.test_build_identity.BuildIdentityTests.test_freeze_and_verify_identity_cli_require_typed_build_manifest_and_detect_drift -v
~~~

Expected RED: build_identity module 尚不存在；不是因为真实 workspace 有 dirty files。

- [ ] **Step 8（2–5 分钟）：实现 canonical identities**

source manifest 使用 `git ls-files --cached --others --exclude-standard -z` 的完整集合，按 POSIX relative path bytes 排序并编码 path、regular/symlink/executable mode、content SHA-256；固定排除 `.git`、`.venv`、`node_modules`、build/cache、`data/compatibility/preflight`、`data/compatibility/evidence`、`data/compatibility/runtime`、真实 DB/backup/Vault roots。排除项只能是运行时 evidence/产物，不能包含 source、tests、README、docs、Dockerfile 或 compose。不得只运行 `git diff` 或只 hash tracked files。build manifest 精确列出 Python deploy bundle/wheel、frontend asset manifest及其 files、resolved compose config、container image digest；canonical payload 产生 buildArtifactHash 与 buildId。实现 exact CLI：`freeze-identity ... --build-identity-directory <bound-root>` 返回 `buildId/manifestPath/manifestFileSha256`，路径必须精确为 `frozen-build-identity-<buildId>.json`；同 identity 只允许 read-only verify existing，绝不 truncate/replace。`verify-identity --build-identity-manifest <exact>` 重算全部字段；两者不得接受 DB path、database lineage/subject 或 generic manifest 参数。

- [ ] **Step 9（2–5 分钟）：重新运行 source/build identity 测试并确认 GREEN**

Run:

~~~powershell
.\.venv\Scripts\python.exe -B -m unittest backend.tests.test_build_identity.BuildIdentityTests.test_source_tree_hash_covers_tracked_untracked_and_modified_bytes backend.tests.test_build_identity.BuildIdentityTests.test_build_artifact_hash_covers_exact_deployed_outputs backend.tests.test_build_identity.BuildIdentityTests.test_freeze_uses_unique_content_addressed_path_and_never_overwrites backend.tests.test_build_identity.BuildIdentityTests.test_freeze_and_verify_identity_cli_require_typed_build_manifest_and_detect_drift -v
~~~

Expected GREEN: 四个 tests OK；dirty source 与 deployed artifact drift 均可证明，ignored runtime output 不污染 identity，content-addressed file 不覆盖且 changed build 获得新路径，两类 manifest 无法互换。

- [ ] **Step 10（2–5 分钟）：写伪造 evidence 拒绝红测**

新增 `test_gate_rejects_artifact_revision_source_build_database_identity_mismatch_and_skips`，分别篡改 evidence artifact、gitRevision、sourceTreeHash、buildArtifactHash、databaseLineageId、同组 subjectDatabaseId、parent backup/manifest chain 与 skipped test count；另用 verified descendant fixture 证明不同隔离 subject 在共同 lineage 下合法。

- [ ] **Step 11（2–5 分钟）：运行并确认伪造 evidence RED**

Run:

~~~powershell
.\.venv\Scripts\python.exe -B -m unittest backend.tests.test_compatibility_gate.CompatibilityGateTests.test_gate_rejects_artifact_revision_source_build_database_identity_mismatch_and_skips -v
~~~

Expected RED: gate 尚未验证至少一个 source/build/database lineage-subject-parent field，伪 evidence 被错误接受或合法 descendant 被错误要求与 Live 共用 subject。

- [ ] **Step 12（2–5 分钟）：实现验证并重新运行伪造 evidence 定向测试确认 GREEN**

Run:

~~~powershell
.\.venv\Scripts\python.exe -B -m unittest backend.tests.test_compatibility_gate.CompatibilityGateTests.test_gate_rejects_artifact_revision_source_build_database_identity_mismatch_and_skips -v
~~~

Expected GREEN: 所有伪 evidence 均被分类拒绝，verified descendant subject 被接受；错误不输出 source file content、database path 或 secret。

- [ ] **Step 13（2–5 分钟）：实现 CLI gate**

实现三个不可混用的显式 gate phase。`--phase preflight` 要求 exact run manifest path/SHA与 isolated DatabaseIdentity，只接受同一 provisional run/build、run-local artifacts和 Task 7–9 shape/parent-chain，固定输出 `{"ok":true,"preflightReady":true,"finalEvidence":false,"nodeShutdownAllowed":false}`。`--phase convergence|shutdown` 同时要求 exact BuildIdentity、Live DatabaseIdentity与 canonical startup snapshot path/SHA；convergence要求 Task 10 strict-readonly集合，shutdown另验证 post-quiesce cutover backup、explained write-smoke、candidate profile、frozen Node rollback、Python recovery、隔离 restore-install-rehearsal、BoundRoot zero-skip、suite isolation `liveAccessCount=0`、machine summaries、final enum/runbook与 HandoffReceipt/rollback-production contract。所有 suites raw 0/failures 0/skips 0后才 O_EXCL生成绑定 startup snapshot的 promotion authorization。preflight不能升级为 final；P0.1 baseline在 shutdown无效；generic/missing/unknown/failed项完整列出并 exit 2。

## Task 6A：消除 P0 BoundRoot 平台 skip，保持 final zero-skip 门禁

**Files:**
- Modify: backend/app/infrastructure/database_backup.py
- Modify: backend/tests/test_database_backup.py
- Create: backend/tests/fixtures/bound_root_platform.py

- [ ] **Step 1（2–5 分钟）：写并运行 deterministic platform fixture RED**

新增 `DatabaseBackupTests.test_bound_root_windows_contract_runs_without_platform_skip` 与 `test_bound_root_posix_contract_runs_without_platform_skip`。fixture 通过窄 `BoundRootPlatform` Port 注入 Windows no-delete-share directory-handle 语义和 POSIX dirfd/openat/renameat/O_NOFOLLOW 语义，而不是依赖宿主 OS；它必须继续覆盖 P0 的 `test_restore_holds_windows_output_root_handle_until_child_is_bound`、`test_restore_creates_validation_directory_through_bound_posix_dirfd` 和 hostile parent swap tripwire。任何 `unittest.skip*`、平台条件提前 return 或未实际触发 hostile swap 均使测试失败。

~~~powershell
.\.venv\Scripts\python.exe -B -m unittest backend.tests.test_database_backup.DatabaseBackupTests.test_bound_root_windows_contract_runs_without_platform_skip backend.tests.test_database_backup.DatabaseBackupTests.test_bound_root_posix_contract_runs_without_platform_skip -v
~~~

Expected RED: 当前平台专用测试至少一支会 skip，或 BoundRoot 直接调用 OS global而无法注入；fixture/import 错误不算有效 RED。

- [ ] **Step 2（2–5 分钟）：实现最小 BoundRootPlatform seam**

只把 P0 已有安全操作下沉到可注入 platform adapter；production adapter 仍调用真实 Windows/POSIX primitives，测试 adapter以真实临时文件/handle模拟相同 identity、share mode、dirfd 与 swap failure。不得放宽 fail-closed、path-open/sqlite-connect tripwire或把安全检查改成字符串 containment。

- [ ] **Step 3（2–5 分钟）：以相同目标确认 GREEN 并证明 backend 零 skip**

~~~powershell
.\.venv\Scripts\python.exe -B -m unittest backend.tests.test_database_backup.DatabaseBackupTests.test_bound_root_windows_contract_runs_without_platform_skip backend.tests.test_database_backup.DatabaseBackupTests.test_bound_root_posix_contract_runs_without_platform_skip -v
~~~

随后由 `machine-summary-runner --adapter unittest` 运行完整 `backend/tests`；summary 必须为 raw exit 0、failures=0、skips=0。当前基线的 `skipped=1` 必须由确定性 fixture消除，禁止修改 gate、把 skip 改名为 expected failure或在 final wrapper 中忽略。

## Task 7：在隔离副本执行 migration/data/HTTP/UI/MCP preflight

**Files:**
- Modify: backend/tests/test_compatibility_gate.py
- Modify: docs/DATABASE.md
- Evidence output only: data/compatibility/preflight/，保持 gitignored；所有 manifest 固定 `provisional=true`，不得供 Task 10 final gate 使用

**强制执行依赖：** 本文按职责把 preflight、candidate 与 rollback 分成 Task 7–9，但 operator 不得按版面顺序提前采集证据。实际顺序固定为：先完成 Task 8/9实现、测试、Compose与静态文档（到 Task 9 Step 9A，不执行 operational rehearsal）→ Task 7 Step 0用 `create-evidence-run --phase provisional` 建 fresh run → Step 1–12 → Task 8 candidate capture → Task 9 rollback/recovery/restore-install-rehearsal capture → Task 7 Step 13。除 immutable external identities外，全部 artifact必须在同一 run root并绑定同一 provisional BuildIdentity；任何 failure seal run，source/build drift产生新 buildId，retry产生新 runId。禁止裸命令输出填 gate。

- [ ] **Step 0（按实际时长）：在首个 provisional capture 前构建并冻结本次 preflight identity**

Task 8/9 的全部 source、tests、README/docs、Dockerfile/Compose 修改已经完成后，先构建 candidate/frozen-node artifacts并在专用 immutable identity root得到 content-addressed `BuildIdentityManifest`，再由 `create-evidence-run --phase provisional` 原子创建 fresh run root/manifest。Build/Live Database/OriginReceipt 是 run 外只读 immutable inputs；从第一条 capture 起产生的 backup、restore copy、descendant identity、summary、fixture 与 evidence 全在 run root。Steps 0–13 与 Task 8/9 operational capture 在同一个 operator process snapshot内连续执行；任何命令失败都 seal 整次 run，不能覆盖、续写或升级为 final identity，重试必须新 buildId（若 source/build 改变）和新 runId：

~~~powershell
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$p6Python = (Resolve-Path -LiteralPath '.\.venv\Scripts\python.exe').Path
$p6LiveDataRoot = (Resolve-Path -LiteralPath 'data').Path.TrimEnd([IO.Path]::DirectorySeparatorChar, [IO.Path]::AltDirectorySeparatorChar)
$p6LiveDb = (Resolve-Path -LiteralPath (Join-Path $p6LiveDataRoot 'app.db')).Path
$p6LiveSettingsPath = [IO.Path]::GetFullPath((Join-Path $p6LiveDataRoot 'settings.json'))
$p6LivePdfRoot = [IO.Path]::GetFullPath((Join-Path $p6LiveDataRoot 'pdfs'))
$p6PreflightRootItem = New-Item -ItemType Directory -Force -Path 'data/compatibility/preflight'
$p6PreflightRoot = (Resolve-Path -LiteralPath $p6PreflightRootItem.FullName).Path
$p6BuildIdentityRootItem = New-Item -ItemType Directory -Force -Path 'data/compatibility/runtime/build-identities'
$p6BuildIdentityRoot = (Resolve-Path -LiteralPath $p6BuildIdentityRootItem.FullName).Path
$p6LiveDatabaseIdentityPath = (Resolve-Path -LiteralPath 'data/compatibility/runtime/live-database-identity-v1.json').Path
npm.cmd run build --prefix frontend
$p6PreflightFrontendBuildExit = $LASTEXITCODE
if ($p6PreflightFrontendBuildExit -ne 0) { throw "P6 provisional frontend build failed with exit code $p6PreflightFrontendBuildExit." }
docker build --target python-production --tag study-app-python:p6-candidate .
$p6PreflightPythonImageExit = $LASTEXITCODE
if ($p6PreflightPythonImageExit -ne 0) { throw "P6 provisional Python image build failed with exit code $p6PreflightPythonImageExit." }
docker build --target frozen-node --tag study-app-node:p6-rollback .
$p6PreflightNodeImageExit = $LASTEXITCODE
if ($p6PreflightNodeImageExit -ne 0) { throw "P6 provisional frozen Node image build failed with exit code $p6PreflightNodeImageExit." }
$p6PreflightFreezeJson = & $p6Python -B -m backend.app.cli.compatibility freeze-identity --source-root . --compose-file docker-compose.yml --frontend-root frontend/dist --python-image study-app-python:p6-candidate --node-image study-app-node:p6-rollback --build-identity-directory $p6BuildIdentityRoot
$p6PreflightFreezeExit = $LASTEXITCODE
if ($p6PreflightFreezeExit -ne 0) { throw "P6 provisional BuildIdentityManifest freeze failed with exit code $p6PreflightFreezeExit." }
$p6PreflightFreeze = $p6PreflightFreezeJson | ConvertFrom-Json
$p6ProvisionalBuildIdentityPath = (Resolve-Path -LiteralPath ([string]$p6PreflightFreeze.manifestPath)).Path
$p6ProvisionalBuildIdentitySha256 = [string]$p6PreflightFreeze.manifestFileSha256
if ($p6PreflightFreeze.buildId -notmatch '^[0-9a-f]{64}$' -or (Split-Path -Leaf $p6ProvisionalBuildIdentityPath) -ne ("frozen-build-identity-" + $p6PreflightFreeze.buildId + '.json') -or $p6ProvisionalBuildIdentitySha256 -notmatch '^[0-9a-f]{64}$') { throw 'P6 provisional content-addressed BuildIdentity result is invalid.' }
& $p6Python -B -m backend.app.cli.compatibility verify-identity --build-identity-manifest $p6ProvisionalBuildIdentityPath
$p6PreflightDirectVerifyExit = $LASTEXITCODE
if ($p6PreflightDirectVerifyExit -ne 0) { throw "P6 provisional BuildIdentityManifest direct verification failed with exit code $p6PreflightDirectVerifyExit." }
$p6ExpectedProvisionalKeys = @('build-identity-verify','bound-root-zero-skip','suite-isolation','backend-suite','legacy-python-suite','mcp-server-suite','node-suite','frontend-vitest','frontend-typecheck','frontend-lint','frontend-build','frontend-e2e','migration-head-ready','http-v2-ndjson-static','runtime-worker-scheduler-obsidian','mcp-credentials','legacy-reconciliation','live-pre-fingerprint','live-post-fingerprint','strict-readonly-compare','candidate-production-profile','candidate-write-smoke','explained-write-compare','frozen-node-rollback','python-recovery','restore-install-rehearsal')
$p6PreflightRunId = [guid]::NewGuid().ToString('N')
$p6CreatePreflightArgs = @('-B','-m','backend.app.cli.compatibility','create-evidence-run','--evidence-root',$p6PreflightRoot,'--run-id',$p6PreflightRunId,'--phase','provisional','--build-identity-manifest',$p6ProvisionalBuildIdentityPath,'--database-identity-manifest',$p6LiveDatabaseIdentityPath)
foreach ($p6ExpectedProvisionalKey in $p6ExpectedProvisionalKeys) { $p6CreatePreflightArgs += @('--expected-key',$p6ExpectedProvisionalKey) }
$p6PreflightRunJson = & $p6Python @p6CreatePreflightArgs
$p6PreflightRunExit = $LASTEXITCODE
if ($p6PreflightRunExit -ne 0) { throw "P6 provisional evidence run creation failed with exit code $p6PreflightRunExit." }
$p6PreflightRun = $p6PreflightRunJson | ConvertFrom-Json
$p6PreflightDir = (Resolve-Path -LiteralPath ([string]$p6PreflightRun.runDirectory)).Path
$p6ProvisionalRunManifestPath = (Resolve-Path -LiteralPath ([string]$p6PreflightRun.runManifestPath)).Path
$p6ProvisionalRunManifestSha256 = [string]$p6PreflightRun.runManifestFileSha256
if (-not $p6PreflightRun.ok -or $p6PreflightRun.runId -ne $p6PreflightRunId -or $p6PreflightRun.phase -ne 'provisional' -or $p6ProvisionalRunManifestSha256 -notmatch '^[0-9a-f]{64}$') { throw 'P6 provisional EvidenceRunManifest result is invalid.' }
if ((Get-FileHash -Algorithm SHA256 -LiteralPath $p6ProvisionalRunManifestPath).Hash.ToLowerInvariant() -ne $p6ProvisionalRunManifestSha256) { throw 'P6 provisional EvidenceRunManifest file SHA-256 mismatch.' }
$env:P6_PROVISIONAL_EVIDENCE_RUN_ID = $p6PreflightRunId
$env:P6_PROVISIONAL_EVIDENCE_RUN_MANIFEST_PATH = $p6ProvisionalRunManifestPath
$env:P6_PROVISIONAL_EVIDENCE_RUN_MANIFEST_SHA256 = $p6ProvisionalRunManifestSha256
$env:P6_PREFLIGHT_RUN_DIR = $p6PreflightDir
$env:P6_PROVISIONAL_BUILD_IDENTITY = $p6ProvisionalBuildIdentityPath
$env:P6_PROVISIONAL_BUILD_IDENTITY_SHA256 = $p6ProvisionalBuildIdentitySha256
$p6ProvisionalCaptureRunArgs = @('--run-manifest',$p6ProvisionalRunManifestPath,'--expected-run-manifest-sha256',$p6ProvisionalRunManifestSha256)
function Invoke-P6ProvisionalMachineSuite {
  param(
    [Parameter(Mandatory = $true)][string]$Key,
    [Parameter(Mandatory = $true)][ValidateSet('unittest','node-test','vitest','playwright','check')][string]$Adapter,
    [Parameter(Mandatory = $true)][string[]]$ChildArgv
  )
  $p6SuiteIsolationPath = Join-Path $p6PreflightDir ($Key + '.isolation.json')
  & $p6Python -B -m backend.app.cli.compatibility create-suite-isolation --run-manifest $p6ProvisionalRunManifestPath --expected-run-manifest-sha256 $p6ProvisionalRunManifestSha256 --suite-key $Key --deny-live-database $p6LiveDb --deny-live-settings $p6LiveSettingsPath --deny-live-pdf-root $p6LivePdfRoot --deny-live-keyring 1 --deny-network 1 --output $p6SuiteIsolationPath
  $p6SuiteIsolationExit = $LASTEXITCODE
  if ($p6SuiteIsolationExit -ne 0) { throw "P6 provisional isolation creation for $Key failed with exit code $p6SuiteIsolationExit." }
  $p6SuiteSummaryPath = Join-Path $p6PreflightDir ($Key + '.summary.json')
  & $p6Python -B -m backend.app.cli.compatibility capture-evidence --key $Key --phase provisional --result-kind machine-summary @p6ProvisionalCaptureRunArgs --build-identity-manifest $p6ProvisionalBuildIdentityPath --summary-artifact $p6SuiteSummaryPath --isolation-manifest $p6SuiteIsolationPath --output (Join-Path $p6PreflightDir ($Key + '.capture.json')) -- $p6Python -B -m backend.app.cli.machine_summary_runner --adapter $Adapter --summary-output $p6SuiteSummaryPath --isolation-manifest $p6SuiteIsolationPath -- @ChildArgv
  $p6SuiteCaptureExit = $LASTEXITCODE
  if ($p6SuiteCaptureExit -ne 0) { throw "P6 provisional machine suite $Key failed with exit code $p6SuiteCaptureExit." }
}
& $p6Python -B -m backend.app.cli.compatibility capture-evidence --key build-identity-verify --phase provisional --result-kind json-cli @p6ProvisionalCaptureRunArgs --build-identity-manifest $p6ProvisionalBuildIdentityPath --output (Join-Path $p6PreflightDir 'build-identity-verify.capture.json') -- $p6Python -B -m backend.app.cli.compatibility verify-identity --build-identity-manifest $p6ProvisionalBuildIdentityPath
$p6PreflightIdentityCaptureExit = $LASTEXITCODE
if ($p6PreflightIdentityCaptureExit -ne 0) { throw "P6 provisional identity capture failed with exit code $p6PreflightIdentityCaptureExit." }
~~~

Expected: content-addressed build identity 在任何 provisional evidence 前存在且验证成功；`create-evidence-run --phase provisional` 独占创建唯一 `run-<runId>` 与 manifest，三项 provisional process env 和 path/SHA/runId 精确一致。首个 record 绑定 exact run/build path+SHA；后续 Task 7–9 capture 的 buildId/sourceTreeHash/buildArtifactHash 必须完全相同，所有新 artifact 都位于该 run root。failed run 被 seal 后只能 fresh retry。

- [ ] **Step 1（2–5 分钟）：创建切换前 verified backup**

沿用 Step 0 的同一个 operator session。使用 P0 CLI create，保存返回的精确 backup/manifest 路径；随后分别运行 verify 与 restore-check。不得手写最新文件名，不得指向 Live 恢复目录：

~~~powershell
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$p6LiveDb = (Resolve-Path -LiteralPath 'data/app.db').Path
$p6PreflightBackupRootItem = New-Item -ItemType Directory -Path (Join-Path $p6PreflightDir 'backups')
$p6PreflightBackupRoot = (Resolve-Path -LiteralPath $p6PreflightBackupRootItem.FullName).Path
$p6PreflightRestoreRootItem = New-Item -ItemType Directory -Path (Join-Path $p6PreflightDir 'restore-checks')
$p6PreflightRestoreRoot = (Resolve-Path -LiteralPath $p6PreflightRestoreRootItem.FullName).Path
$p6CreateJson = & $p6Python -B -m backend.app.cli.database_backup create --database $p6LiveDb --output-directory $p6PreflightBackupRoot --label ("pre-p6-" + $p6PreflightRunId)
$p6CreateExit = $LASTEXITCODE
if ($p6CreateExit -ne 0) { throw "P6 backup create failed with exit code $p6CreateExit." }
$p6Create = $p6CreateJson | ConvertFrom-Json
if (-not $p6Create.ok) { throw 'P6 backup create JSON did not report success.' }
$p6PreflightCreateBackupCompatibleLogicalSha256 = [string]$p6Create.logicalSha256
$p6VerifyJson = & $p6Python -B -m backend.app.cli.database_backup verify --backup $p6Create.backupPath --manifest $p6Create.manifestPath
$p6VerifyExit = $LASTEXITCODE
if ($p6VerifyExit -ne 0) { throw "P6 independent backup verify failed with exit code $p6VerifyExit." }
$p6Verify = $p6VerifyJson | ConvertFrom-Json
$p6PreflightVerifyBackupCompatibleLogicalSha256 = [string]$p6Verify.logicalSha256
if (-not $p6Verify.ok -or $p6PreflightVerifyBackupCompatibleLogicalSha256 -ne $p6PreflightCreateBackupCompatibleLogicalSha256) { throw 'P6 independent backup verify backup-compatible logical hash mismatch.' }
$p6RestoreJson = & $p6Python -B -m backend.app.cli.database_backup restore-check --backup $p6Create.backupPath --manifest $p6Create.manifestPath --output-directory $p6PreflightRestoreRoot
$p6RestoreExit = $LASTEXITCODE
if ($p6RestoreExit -ne 0) { throw "P6 restore-check failed with exit code $p6RestoreExit." }
$p6Restore = $p6RestoreJson | ConvertFrom-Json
$p6PreflightRestoreBackupCompatibleLogicalSha256 = [string]$p6Restore.logicalSha256
if (-not $p6Restore.ok -or $p6PreflightRestoreBackupCompatibleLogicalSha256 -ne $p6PreflightVerifyBackupCompatibleLogicalSha256) { throw 'P6 restore-check backup-compatible logical hash mismatch.' }
~~~

Expected: 三命令 exit 0，create/verify/restore-check 的 backupCompatibleLogicalSha256 一致；backup、manifest 与 restore copy 全在本次 immutable provisional run root，且该值不与 P6 canonicalDataSha256 比较。

- [ ] **Step 2（2–5 分钟）：在隔离恢复副本执行 migration rehearsal**

只对 Step 1 返回的恢复副本执行 revision-explicit rehearsal。先用 segment-safe restore-root containment 排除 Live/兄弟前缀路径，保存调用者原有 `DB_PATH`，再依次处理 P3 与 P2 的非空 downgrade guard；guard 命中时只在这个可丢弃副本显式传对应 data-loss x 参数：

~~~powershell
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$p6DrillDb = (Resolve-Path -LiteralPath $p6Restore.restoredPath).Path
$p6RestoreRoot = $p6PreflightRestoreRoot.TrimEnd([IO.Path]::DirectorySeparatorChar, [IO.Path]::AltDirectorySeparatorChar)
$p6RestorePrefix = $p6RestoreRoot + [IO.Path]::DirectorySeparatorChar
$p6DrillParent = (Resolve-Path -LiteralPath (Split-Path -Parent $p6DrillDb)).Path
if ($p6DrillDb -eq $p6LiveDb) { throw 'P6 drill resolved to Live data/app.db.' }
if (-not $p6DrillDb.StartsWith($p6RestorePrefix, [StringComparison]::OrdinalIgnoreCase)) { throw 'P6 drill escaped restore-check root.' }
if (-not (Split-Path -Leaf $p6DrillParent).StartsWith('restore-validation-', [StringComparison]::Ordinal)) { throw 'P6 drill is not in a restore-validation directory.' }
$p6BeforeJson = & $p6Python -B -m backend.app.cli.database_backup inspect --database $p6DrillDb
$p6BeforeExit = $LASTEXITCODE
if ($p6BeforeExit -ne 0) { throw "P6 pre-rehearsal inspect failed with exit code $p6BeforeExit." }
$p6Before = $p6BeforeJson | ConvertFrom-Json
if (-not $p6Before.ok) { throw 'P6 pre-rehearsal inspect JSON did not report success.' }
$p6LiveDatabaseIdentityPath = (Resolve-Path -LiteralPath 'data/compatibility/runtime/live-database-identity-v1.json').Path
$p6DrillDatabaseIdentityPath = Join-Path $p6PreflightDir 'p6-preflight-database-identity-v1.json'
$p6DrillIdentityJson = & $p6Python -B -m backend.app.cli.runtime_owner create-descendant-database-identity --database $p6DrillDb --subject-kind p6_preflight --parent-database-identity-manifest $p6LiveDatabaseIdentityPath --parent-backup $p6Create.backupPath --parent-manifest $p6Create.manifestPath --output $p6DrillDatabaseIdentityPath
$p6DrillIdentityExit = $LASTEXITCODE
if ($p6DrillIdentityExit -ne 0) { throw "P6 preflight descendant database identity creation failed with exit code $p6DrillIdentityExit." }
$p6DrillIdentity = $p6DrillIdentityJson | ConvertFrom-Json
if (-not $p6DrillIdentity.ok -or $p6DrillIdentity.manifestPath -ne $p6DrillDatabaseIdentityPath) { throw 'P6 preflight descendant database identity JSON is invalid.' }
$p6LegacyTables = @('papers','progress','paper_reviews','notes','favorites','translations','paper_vectors','cite_edges','ingest_jobs','job_candidates','job_schedules','schema_migrations')
$p6PreviousDbPath = [Environment]::GetEnvironmentVariable('DB_PATH', 'Process')
$p6HadDbPath = $null -ne $p6PreviousDbPath
$env:DB_PATH = $p6DrillDb
try {
  .\.venv\Scripts\python.exe -B -m alembic -c backend/alembic.ini upgrade 20260807_03
  if ($LASTEXITCODE -ne 0) { throw 'P6 restored-copy upgrade to 20260807_03 failed.' }

  $p6P3GuardOutput = & .\.venv\Scripts\python.exe -B -m alembic -c backend/alembic.ini downgrade 20260807_02 2>&1
  $p6P3GuardExit = $LASTEXITCODE
  if ($p6P3GuardExit -ne 0) {
    if (($p6P3GuardOutput -join "`n") -notmatch 'P3_DOWNGRADE_BLOCKED_NONEMPTY') { throw 'P6 P3 downgrade failed without the required nonempty classification.' }
    .\.venv\Scripts\python.exe -B -m alembic -c backend/alembic.ini -x allow_p3_data_loss=true downgrade 20260807_02
    if ($LASTEXITCODE -ne 0) { throw 'P6 isolated P3 explicit data-loss downgrade failed.' }
  }
  $p6AtP2Json = & $p6Python -B -m backend.app.cli.database_backup inspect --database $p6DrillDb
  $p6AtP2Exit = $LASTEXITCODE
  if ($p6AtP2Exit -ne 0) { throw "P6 P2 inspect failed with exit code $p6AtP2Exit." }
  $p6AtP2 = $p6AtP2Json | ConvertFrom-Json
  if ($p6AtP2.database.alembicVersion -ne '20260807_02') { throw 'P6 drill did not reach 20260807_02.' }

  $p6P2GuardOutput = & .\.venv\Scripts\python.exe -B -m alembic -c backend/alembic.ini downgrade 20260807_01 2>&1
  $p6P2GuardExit = $LASTEXITCODE
  if ($p6P2GuardExit -ne 0) {
    if (($p6P2GuardOutput -join "`n") -notmatch 'P2_DOWNGRADE_BLOCKED_NONEMPTY') { throw 'P6 P2 downgrade failed without the required nonempty classification.' }
    .\.venv\Scripts\python.exe -B -m alembic -c backend/alembic.ini -x allow_p2_data_loss=true downgrade 20260807_01
    if ($LASTEXITCODE -ne 0) { throw 'P6 isolated P2 explicit data-loss downgrade failed.' }
  }
  $p6AtP1Json = & $p6Python -B -m backend.app.cli.database_backup inspect --database $p6DrillDb
  $p6AtP1Exit = $LASTEXITCODE
  if ($p6AtP1Exit -ne 0) { throw "P6 P1 inspect failed with exit code $p6AtP1Exit." }
  $p6AtP1 = $p6AtP1Json | ConvertFrom-Json
  if ($p6AtP1.database.alembicVersion -ne '20260807_01') { throw 'P6 drill did not reach 20260807_01.' }
  foreach ($p6Table in $p6LegacyTables) {
    if ($p6Before.database.tableCounts.$p6Table -ne $p6AtP1.database.tableCounts.$p6Table) { throw "P6 rehearsal changed legacy count for $p6Table." }
    if ($p6Before.database.tableSha256.$p6Table -ne $p6AtP1.database.tableSha256.$p6Table) { throw "P6 rehearsal changed legacy hash for $p6Table." }
  }

  .\.venv\Scripts\python.exe -B -m alembic -c backend/alembic.ini upgrade 20260807_03
  if ($LASTEXITCODE -ne 0) { throw 'P6 restored-copy re-upgrade failed.' }
  $p6ReupgradedJson = & $p6Python -B -m backend.app.cli.database_backup inspect --database $p6DrillDb
  $p6ReupgradedExit = $LASTEXITCODE
  if ($p6ReupgradedExit -ne 0) { throw "P6 re-upgrade inspect failed with exit code $p6ReupgradedExit." }
  $p6Reupgraded = $p6ReupgradedJson | ConvertFrom-Json
  if ($p6Reupgraded.database.alembicVersion -ne '20260807_03') { throw 'P6 drill did not return to 20260807_03.' }
} finally {
  if ($p6HadDbPath) { $env:DB_PATH = $p6PreviousDbPath } else { Remove-Item Env:DB_PATH -ErrorAction SilentlyContinue }
}
$p6MigrationCaptureJson = & $p6Python -B -m backend.app.cli.compatibility capture-evidence --key migration-head-ready --phase provisional --result-kind json-cli @p6ProvisionalCaptureRunArgs --build-identity-manifest $p6ProvisionalBuildIdentityPath --database-identity-manifest $p6DrillDatabaseIdentityPath --output (Join-Path $p6PreflightDir 'migration-head-ready.capture.json') -- $p6Python -B -m backend.app.cli.database_backup inspect --database $p6DrillDb
$p6MigrationCaptureExit = $LASTEXITCODE
if ($p6MigrationCaptureExit -ne 0) { throw "P6 provisional migration-head capture failed with exit code $p6MigrationCaptureExit." }
$p6MigrationCapture = $p6MigrationCaptureJson | ConvertFrom-Json
if (-not $p6MigrationCapture.ok -or $p6MigrationCapture.database.alembicVersion -ne '20260807_03') { throw 'P6 provisional migration-head capture did not prove exact revision 20260807_03.' }
~~~

Expected: `20260807_03 → 20260807_02 → 20260807_01 → 20260807_03` 循环成功；有 P3/P2 operational rows 时先分别观察精确 guard，再只对隔离副本显式允许丢弃；旧表 counts/PK/legacy column hashes 全等，调用者原有 `DB_PATH` 被精确恢复，Live DB 此时不变。禁止对 Live 使用任一 data-loss x 参数。

- [ ] **Step 3（2–5 分钟）：在隔离 subject 建立 provisional strict-readonly preflight window**

停止所有连接 `$p6DrillDb` 的演练进程并确认该隔离 subject 无 writer；Live Node production 不 drain、不停止，继续服务 Live DB。对 `$p6DrillDb` 生成本次 `$p6PreflightDir/pre-convergence.json`，记录共同 databaseLineageId、该恢复副本的独立 subjectDatabaseId/parent chain、Step 0 已冻结的 provisional build identity 与 `provisional=true`。从此到 Step 13 只允许对该隔离 DB 做 mode=ro/query_only；所有会写入的 contract/Worker/E2E tests 继续使用各自临时 DB。任何 preflight 失败只停止演练，不改变 Live owner；这些结果仅用于在最终停机窗口前发现缺口。

~~~powershell
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$p6PreflightPrePath = Join-Path $p6PreflightDir 'pre-convergence.json'
& $p6Python -B -m backend.app.cli.compatibility capture-evidence --key live-pre-fingerprint --phase provisional --result-kind json-cli @p6ProvisionalCaptureRunArgs --build-identity-manifest $p6ProvisionalBuildIdentityPath --database-identity-manifest $p6DrillDatabaseIdentityPath --output (Join-Path $p6PreflightDir 'live-pre-fingerprint.capture.json') --artifact ("pre-convergence=" + $p6PreflightPrePath) -- $p6Python -B -m backend.app.cli.compatibility fingerprint --database $p6DrillDb --database-identity-manifest $p6DrillDatabaseIdentityPath --subject-kind p6_preflight --output $p6PreflightPrePath
$p6PreflightPreExit = $LASTEXITCODE
if ($p6PreflightPreExit -ne 0) { throw "P6 provisional pre-convergence fingerprint capture failed with exit code $p6PreflightPreExit." }
~~~

Expected: 隔离 subject integrity ok、FK violations 为空、22 张必选 application table 均有 count/hash，Alembic/FTS 与 parent-chain evidence 完整；Live Node HTTP/Worker/Scheduler owner、端口和 Live DB metadata 保持不变。

- [ ] **Step 3A（2–5 分钟）：在同一隔离只读窗口生成 provisional legacy reconciliation ledger**

Run:

~~~powershell
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$p6PreflightReconciliationPath = Join-Path $p6PreflightDir 'legacy-reconciliation-v1.json'
& $p6Python -B -m backend.app.cli.compatibility capture-evidence --key legacy-reconciliation --phase provisional --result-kind json-cli @p6ProvisionalCaptureRunArgs --build-identity-manifest $p6ProvisionalBuildIdentityPath --database-identity-manifest $p6DrillDatabaseIdentityPath --output (Join-Path $p6PreflightDir 'legacy-reconciliation.capture.json') --artifact ("legacy-reconciliation=" + $p6PreflightReconciliationPath) -- $p6Python -B -m backend.app.cli.compatibility reconcile-legacy --database $p6DrillDb --database-identity-manifest $p6DrillDatabaseIdentityPath --output $p6PreflightReconciliationPath
$p6PreflightReconciliationExit = $LASTEXITCODE
if ($p6PreflightReconciliationExit -ne 0) { throw "P6 provisional legacy reconciliation capture failed with exit code $p6PreflightReconciliationExit." }
.\.venv\Scripts\python.exe -B -m unittest backend.tests.test_legacy_reconciliation -v
$p6LegacyReconciliationSuiteExit = $LASTEXITCODE
if ($p6LegacyReconciliationSuiteExit -ne 0) { throw "P6 legacy reconciliation suite failed with exit code $p6LegacyReconciliationSuiteExit." }
~~~

Expected: ledger 的 explainer/translation item count、完整 `(paperId,kind)` sets、逐内容/aggregate hashes 可从同一隔离 subject 重算；`mismatch` 精确为 0。`proven_migrated` 与 `legacy_only_unprovable` 分开报告，后者保持 null source relation 且不得被文档称为已迁移；notes/paper_vectors preservation counts/sets/hashes 与 provisional strict fingerprint 相等，Live DB bytes/mtime/sidecars 不变。

- [ ] **Step 4（2–5 分钟）：运行 HTTP/NDJSON parity**

Run:

~~~powershell
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
Invoke-P6ProvisionalMachineSuite -Key 'http-v2-ndjson-static' -Adapter 'unittest' -ChildArgv @($p6Python,'-B','-m','unittest','backend.tests.test_http_contract_inventory','backend.tests.test_api_legacy_json','backend.tests.test_api_ndjson','backend.tests.test_api_pdf_static','backend.tests.test_api_v2','-v')
$p6PreflightHttpExit = $LASTEXITCODE
if ($p6PreflightHttpExit -ne 0) { throw "P6 provisional HTTP/v2/NDJSON/static capture failed with exit code $p6PreflightHttpExit." }
~~~

Expected: 全部 OK；48 个 legacy /api method/path 与 15 个 NDJSON contract 无差异；OpenAPI 精确包含 P2/P3/P5 fixed inventory，包括 classification/metadata/summary/index/index-status/search/chunks。artifact/index wire 只接受 camelCase `sourceMode/sourceDocumentId/includeEmbeddings`，不存在 generic/duplicate path。

- [ ] **Step 5（2–5 分钟）：运行 Worker/Scheduler/Obsidian**

Run:

~~~powershell
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
Invoke-P6ProvisionalMachineSuite -Key 'runtime-worker-scheduler-obsidian' -Adapter 'unittest' -ChildArgv @($p6Python,'-B','-m','unittest','backend.tests.test_runtime_ownership','backend.tests.test_obsidian_layout','backend.tests.test_obsidian_ownership','backend.tests.test_obsidian_pdf_modes','backend.tests.test_obsidian_jobs_api','backend.tests.test_obsidian_rebuild','-v')
$p6PreflightRuntimeExit = $LASTEXITCODE
if ($p6PreflightRuntimeExit -ne 0) { throw "P6 provisional runtime/Worker/Scheduler/Obsidian capture failed with exit code $p6PreflightRuntimeExit." }
~~~

Expected: 全部 OK；Worker/Scheduler 各自单 owner且不同 role 可共存，Obsidian OCR spy 为 0；ProcessingJob public status 只出现 `queued|running|succeeded|failed|cancelled`。Obsidian conflict/partial failure 的 terminal job 为 `succeeded`，仅在 safe `result_json` 写 `{exported,unchanged,conflicts,errors,skipped,userManaged,orphaned,deleted}` 八项非负整数 counts，不出现任何第六状态。

- [ ] **Step 6（2–5 分钟）：运行 MCP suites**

Run:

~~~powershell
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
Invoke-P6ProvisionalMachineSuite -Key 'mcp-server-suite' -Adapter 'unittest' -ChildArgv @($p6Python,'-B','-m','unittest','discover','-s','test','-p','test_mcp_server.py','-v')
$p6PreflightLegacyMcpExit = $LASTEXITCODE
if ($p6PreflightLegacyMcpExit -ne 0) { throw "P6 provisional legacy MCP capture failed with exit code $p6PreflightLegacyMcpExit." }
.\.venv\Scripts\python.exe -B -m unittest backend.tests.test_mcp_contract backend.tests.test_mcp_readonly backend.tests.test_mcp_shadow -v
$p6ApplicationMcpDiagnosticExit = $LASTEXITCODE
if ($p6ApplicationMcpDiagnosticExit -ne 0) { throw "P6 application MCP diagnostic suites failed with exit code $p6ApplicationMcpDiagnosticExit." }
~~~

Expected: 全部 OK；tools/list 恰为九个，get_paper bounded source status 与 artifact pagination/fallback goldens 全绿，read-only DB bytes 不变，queue/OCR spies 为 0。

- [ ] **Step 7（2–5 分钟）：写 CredentialStore evidence validator 红测**

新增 `CompatibilityGateTests.test_credential_evidence_requires_p1_store_contract_and_retained_legacy_fields`。对 `llm|ocr|embedding|semantic_scholar` 四种 kind，分别删掉 exact env/Keyring/legacy mapping、priority、`hasKey/keyTail/environmentManaged`、blank-preserve zero-write、explicit clear、fixed/unsupported probe、log redaction或四个 legacy-field-retained 证据，断言 gate 逐项返回 `CREDENTIAL_EVIDENCE_INCOMPLETE`；若 evidence 声称调用 `finalize_legacy_migration`，返回 `NODE_ROLLBACK_CREDENTIALS_REMOVED`。

- [ ] **Step 8（2–5 分钟）：运行并确认 CredentialStore validator RED**

Run:

~~~powershell
.\.venv\Scripts\python.exe -B -m unittest backend.tests.test_compatibility_gate.CompatibilityGateTests.test_credential_evidence_requires_p1_store_contract_and_retained_legacy_fields -v
~~~

Expected RED: gate 尚未验证 credential evidence 的至少一个字段或错误接受 finalization marker。

- [ ] **Step 9（2–5 分钟）：实现 validator 并重新运行 CredentialStore evidence 测试确认 GREEN**

Run:

~~~powershell
.\.venv\Scripts\python.exe -B -m unittest backend.tests.test_compatibility_gate.CompatibilityGateTests.test_credential_evidence_requires_p1_store_contract_and_retained_legacy_fields -v
~~~

Expected GREEN: test OK；validator 只读脱敏 status/count/hash，不读取或输出任何完整 credential。

- [ ] **Step 10（2–5 分钟）：运行 CredentialStore/settings 收敛套件**

Run:

~~~powershell
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
Invoke-P6ProvisionalMachineSuite -Key 'mcp-credentials' -Adapter 'unittest' -ChildArgv @($p6Python,'-B','-m','unittest','backend.tests.test_mcp_contract','backend.tests.test_mcp_readonly','backend.tests.test_mcp_shadow','backend.tests.test_credentials','backend.tests.test_api_legacy_json.LegacyJsonApiTests.test_settings_use_provider_profiles_and_redacted_credentials','-v')
$p6PreflightMcpCredentialsExit = $LASTEXITCODE
if ($p6PreflightMcpCredentialsExit -ne 0) { throw "P6 provisional MCP/CredentialStore capture failed with exit code $p6PreflightMcpCredentialsExit." }
~~~

Expected: 全部 OK；使用隔离 settings/Fake Keyring 与打包固定 probe fixture，四种 kind 的 environment → Keyring → legacy、hasKey/keyTail、blank preserve、clear/environment read-only 全绿；stdout/stderr/log/error 无完整 test key，四个 legacy fields 仍存在，真实 settings/Keyring/network 零写。

- [ ] **Step 11（2–5 分钟）：运行 Node/React/static gates**

Run:

~~~powershell
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
Invoke-P6ProvisionalMachineSuite -Key 'bound-root-zero-skip' -Adapter 'unittest' -ChildArgv @($p6Python,'-B','-m','unittest','backend.tests.test_database_backup.DatabaseBackupTests.test_bound_root_windows_contract_runs_without_platform_skip','backend.tests.test_database_backup.DatabaseBackupTests.test_bound_root_posix_contract_runs_without_platform_skip','-v')
$p6PreflightBoundRootExit = $LASTEXITCODE
if ($p6PreflightBoundRootExit -ne 0) { throw "P6 provisional deterministic BoundRoot suite failed with exit code $p6PreflightBoundRootExit." }
Invoke-P6ProvisionalMachineSuite -Key 'suite-isolation' -Adapter 'unittest' -ChildArgv @($p6Python,'-B','-m','unittest','backend.tests.test_suite_isolation','backend.tests.test_machine_summary','-v')
$p6PreflightIsolationExit = $LASTEXITCODE
if ($p6PreflightIsolationExit -ne 0) { throw "P6 provisional suite-isolation contract failed with exit code $p6PreflightIsolationExit." }
Invoke-P6ProvisionalMachineSuite -Key 'backend-suite' -Adapter 'unittest' -ChildArgv @($p6Python,'-B','-m','unittest','discover','-s','backend/tests','-p','test_*.py','-v')
$p6PreflightBackendExit = $LASTEXITCODE
if ($p6PreflightBackendExit -ne 0) { throw "P6 provisional backend suite failed with exit code $p6PreflightBackendExit." }
Invoke-P6ProvisionalMachineSuite -Key 'legacy-python-suite' -Adapter 'unittest' -ChildArgv @($p6Python,'-B','-m','unittest','discover','-s','test','-p','test_*.py','-v')
$p6PreflightLegacyPythonExit = $LASTEXITCODE
if ($p6PreflightLegacyPythonExit -ne 0) { throw "P6 provisional legacy Python suite failed with exit code $p6PreflightLegacyPythonExit." }
Invoke-P6ProvisionalMachineSuite -Key 'node-suite' -Adapter 'node-test' -ChildArgv @('npm.cmd','test')
$p6PreflightNodeExit = $LASTEXITCODE
if ($p6PreflightNodeExit -ne 0) { throw "P6 provisional Node suite failed with exit code $p6PreflightNodeExit." }
Invoke-P6ProvisionalMachineSuite -Key 'frontend-vitest' -Adapter 'vitest' -ChildArgv @('npm.cmd','run','test:run','--prefix','frontend')
$p6PreflightVitestExit = $LASTEXITCODE
if ($p6PreflightVitestExit -ne 0) { throw "P6 provisional frontend Vitest failed with exit code $p6PreflightVitestExit." }
Invoke-P6ProvisionalMachineSuite -Key 'frontend-typecheck' -Adapter 'check' -ChildArgv @('npm.cmd','run','typecheck','--prefix','frontend')
$p6PreflightTypecheckExit = $LASTEXITCODE
if ($p6PreflightTypecheckExit -ne 0) { throw "P6 provisional frontend typecheck failed with exit code $p6PreflightTypecheckExit." }
Invoke-P6ProvisionalMachineSuite -Key 'frontend-lint' -Adapter 'check' -ChildArgv @('npm.cmd','run','lint','--prefix','frontend')
$p6PreflightLintExit = $LASTEXITCODE
if ($p6PreflightLintExit -ne 0) { throw "P6 provisional frontend lint failed with exit code $p6PreflightLintExit." }
Invoke-P6ProvisionalMachineSuite -Key 'frontend-build' -Adapter 'check' -ChildArgv @('npm.cmd','run','build','--prefix','frontend')
$p6PreflightBuildExit = $LASTEXITCODE
if ($p6PreflightBuildExit -ne 0) { throw "P6 provisional frontend build failed with exit code $p6PreflightBuildExit." }
Invoke-P6ProvisionalMachineSuite -Key 'frontend-e2e' -Adapter 'playwright' -ChildArgv @('npm.cmd','run','e2e','--prefix','frontend','--','--grep','FastAPI parity')
$p6PreflightE2eExit = $LASTEXITCODE
if ($p6PreflightE2eExit -ne 0) { throw "P6 provisional FastAPI parity E2E failed with exit code $p6PreflightE2eExit." }
Invoke-P6ProvisionalMachineSuite -Key 'candidate-production-profile' -Adapter 'unittest' -ChildArgv @($p6Python,'-B','-m','unittest','backend.tests.test_compatibility_gate.CompatibilityGateTests.test_production_profile_has_no_node_runtime_and_keeps_frozen_rollback','-v')
$p6PreflightProfileExit = $LASTEXITCODE
if ($p6PreflightProfileExit -ne 0) { throw "P6 provisional production-profile capture failed with exit code $p6PreflightProfileExit." }
~~~

Expected: 每个 wrapper 与 child raw exit 0、skips=0；workspace 与 legacy 使用真实 FastAPI 通过，无 skipped compatibility case。任一 capture record 缺少 Step 0 exact provisional build identity，或 frontend rebuild 使 identity 漂移，都丢弃整次 preflight run。

- [ ] **Step 12（2–5 分钟）：生成 provisional strict post-convergence fingerprint 并比较**

对同一停止写入的 `$p6DrillDb` 再生成本次 `$p6PreflightDir/post-convergence.json`，运行 `compare --mode strict-readonly`；两条命令都必须由 wrapper 绑定同一 provisional build/database identity。只读验证不得改变任何一张必选表的 count/PK/row hash。该结果只证明 preflight 流程可执行，Task 10 必须在 frozen identity 下对 Live subject 重新采集 final strict evidence。

~~~powershell
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$p6PreflightPostPath = Join-Path $p6PreflightDir 'post-convergence.json'
& $p6Python -B -m backend.app.cli.compatibility capture-evidence --key live-post-fingerprint --phase provisional --result-kind json-cli @p6ProvisionalCaptureRunArgs --build-identity-manifest $p6ProvisionalBuildIdentityPath --database-identity-manifest $p6DrillDatabaseIdentityPath --output (Join-Path $p6PreflightDir 'live-post-fingerprint.capture.json') --artifact ("post-convergence=" + $p6PreflightPostPath) -- $p6Python -B -m backend.app.cli.compatibility fingerprint --database $p6DrillDb --database-identity-manifest $p6DrillDatabaseIdentityPath --subject-kind p6_preflight --output $p6PreflightPostPath
$p6PreflightPostExit = $LASTEXITCODE
if ($p6PreflightPostExit -ne 0) { throw "P6 provisional post-convergence fingerprint capture failed with exit code $p6PreflightPostExit." }
& $p6Python -B -m backend.app.cli.compatibility capture-evidence --key strict-readonly-compare --phase provisional --result-kind json-cli @p6ProvisionalCaptureRunArgs --build-identity-manifest $p6ProvisionalBuildIdentityPath --database-identity-manifest $p6DrillDatabaseIdentityPath --output (Join-Path $p6PreflightDir 'strict-readonly-compare.capture.json') -- $p6Python -B -m backend.app.cli.compatibility compare --mode strict-readonly --before $p6PreflightPrePath --after $p6PreflightPostPath
$p6PreflightCompareExit = $LASTEXITCODE
if ($p6PreflightCompareExit -ne 0) { throw "P6 provisional strict comparison capture failed with exit code $p6PreflightCompareExit." }
~~~

Expected: compare exit 0，所有 required table count/PK/row hash 全等。

- [ ] **Step 13（2–5 分钟）：Task 8/9 operational capture 完成后运行唯一 provisional preflight gate**

本步骤在版面上位于 Task 8/9 之前，但执行上必须最后运行：先完成 Task 8 candidate-write-smoke/explained compare 与 Task 9 frozen-node rollback/Python recovery/restore-install-rehearsal records。随后重新验证同一 provisional run manifest path/SHA/runId与 build identity，再运行 gate；不得在 records缺失时通过：

~~~powershell
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
& $p6Python -B -m backend.app.cli.compatibility verify-identity --build-identity-manifest $p6ProvisionalBuildIdentityPath
$p6PreflightIdentityReverifyExit = $LASTEXITCODE
if ($p6PreflightIdentityReverifyExit -ne 0) { throw "P6 provisional identity drifted before gate with exit code $p6PreflightIdentityReverifyExit." }
if ([Environment]::GetEnvironmentVariable('P6_PROVISIONAL_EVIDENCE_RUN_ID', 'Process') -ne $p6PreflightRunId -or [Environment]::GetEnvironmentVariable('P6_PROVISIONAL_EVIDENCE_RUN_MANIFEST_SHA256', 'Process') -ne $p6ProvisionalRunManifestSha256 -or (Get-FileHash -Algorithm SHA256 -LiteralPath $p6ProvisionalRunManifestPath).Hash.ToLowerInvariant() -ne $p6ProvisionalRunManifestSha256) { throw 'P6 provisional EvidenceRunManifest snapshot drifted before gate.' }
$p6PreflightGateJson = & $p6Python -B -m backend.app.cli.compatibility gate --phase preflight --evidence-dir $p6PreflightDir --run-manifest $p6ProvisionalRunManifestPath --expected-run-manifest-sha256 $p6ProvisionalRunManifestSha256 --build-identity-manifest $p6ProvisionalBuildIdentityPath --database-identity-manifest $p6DrillDatabaseIdentityPath
$p6PreflightGateExit = $LASTEXITCODE
if ($p6PreflightGateExit -ne 0) { throw "P6 provisional preflight gate failed with exit code $p6PreflightGateExit." }
$p6PreflightGate = $p6PreflightGateJson | ConvertFrom-Json
if (-not $p6PreflightGate.ok -or -not $p6PreflightGate.preflightReady -or $p6PreflightGate.finalEvidence -or $p6PreflightGate.nodeShutdownAllowed) { throw 'P6 provisional preflight gate returned an invalid state.' }
~~~

Expected: `{"ok":true,"preflightReady":true,"finalEvidence":false,"nodeShutdownAllowed":false}`，exit 0；gate 明确验证 Task 7–9 每个输入都是 wrapper-produced、`phase=provisional`、同一 build identity，并按数据库相关性绑定正确 isolated subject/parent chain。Live Node production 仍 active；结果不得复制到 final evidence directory、不得运行 shutdown phase、不得切 production profile。

## Task 8：构建并隔离验证 Python production candidate，Node production 暂不切换

**Files:**
- Modify: Dockerfile
- Modify: docker-compose.yml
- Modify: README.md
- Modify: docs/DATABASE.md
- Modify: backend/tests/test_compatibility_gate.py
- Modify: backend/app/cli/compatibility.py
- Create: backend/tests/test_production_candidate_e2e.py

- [ ] **Step 1（2–5 分钟）：写 deployment profile 红测**

新增 CompatibilityGateTests.test_production_profile_has_no_node_runtime_and_keeps_frozen_rollback，解析 compose config；断言默认 services 仅 Python api/worker/scheduler/mcp，frozen-node 只在 rollback profile。

- [ ] **Step 2（2–5 分钟）：确认 profile 红测**

Run:

~~~powershell
.\.venv\Scripts\python.exe -B -m unittest backend.tests.test_compatibility_gate.CompatibilityGateTests.test_production_profile_has_no_node_runtime_and_keeps_frozen_rollback -v
~~~

Expected RED: compose 默认仍启动 Node 或没有 rollback profile。

- [ ] **Step 3（2–5 分钟）：实现 production/rollback stages**

Python production candidate stage 运行 Uvicorn；worker/scheduler 使用独立 command；React 静态构建可由 Node build stage 产生，但候选 runtime 不运行 node。frozen-node stage 固定 package-lock、server.js、agent 与静态资源。此步骤只构建/解析候选产物，不重启或替换当前 Node production；Live Node 继续保持 active owner。

- [ ] **Step 4（2–5 分钟）：实现 compose profiles**

候选默认 api、worker、scheduler、mcp；rollback profile 只启动 frozen-node，且要求 Python services scale 0/停止后才运行。DB volume 相同，禁止自动 downgrade。compose/profile 修改在最终 shutdown gate 前只允许对隔离 DB/端口运行，不得 promotion 到 Live service owner。

- [ ] **Step 5（2–5 分钟）：重新运行 deployment profile 测试并确认 GREEN**

Run:

~~~powershell
.\.venv\Scripts\python.exe -B -m unittest backend.tests.test_compatibility_gate.CompatibilityGateTests.test_production_profile_has_no_node_runtime_and_keeps_frozen_rollback -v
~~~

Expected GREEN: 默认 config 无 node command，rollback stage 可构建且不默认启动。

- [ ] **Step 5A（2–5 分钟）：写 candidate-write-smoke CLI 红测**

新增 `ProductionCandidateE2ETests.test_candidate_write_smoke_uses_verified_descendant_and_never_live`：输入精确 backup/manifest/restore root、`BuildIdentityManifest`、父 Live `DatabaseEvidenceIdentityManifest` 与 Fake provider，断言 CLI 创建并 exclusive-write 独立 descendant database manifest、只在随机 loopback 启动 api/worker/scheduler、生成 before/after/delta/parent-chain evidence，并在 JSON 中返回 exact `restoredDatabasePath` 与 `descendantDatabaseIdentityManifestPath`；Live path、Live owner marker、用户 PDF、真实 network 与真实 credential 调用全部为 0。交换两种 manifest、缺父 identity 或返回路径与 file identity 不匹配必须失败。

- [ ] **Step 5B（2–5 分钟）：运行 candidate-write-smoke 红测**

Run: `.\.venv\Scripts\python.exe -B -m unittest backend.tests.test_production_candidate_e2e.ProductionCandidateE2ETests.test_candidate_write_smoke_uses_verified_descendant_and_never_live -v`

Expected RED: `candidate-write-smoke` command 尚不存在；fixture/import/skip 不算有效 RED。

- [ ] **Step 5C（2–5 分钟）：完成 provisional candidate 构建定义并推迟 evidence freeze**

完成 candidate/frozen-node 的 Docker/Compose 构建定义和定向测试，但此处不得创建 provisional capture 或 `BuildIdentityManifest`：Task 9 仍会修改 source/tests/docs，提前 freeze 必然产生过期身份。等 Task 8/9 所有实现与静态文件完成后，严格按 Task 7 的执行依赖返回 Task 7 Step 0，在首个 provisional capture 前构建 artifacts 并 exclusive-create 唯一 `$p6ProvisionalBuildIdentityPath`。所有 Task 8/9 operational command 显式接收该 exact path；source/build 一旦变化就丢弃整个 run，禁止覆盖或升级成 final identity。

- [ ] **Step 6A（2–5 分钟）：实现独立 write-smoke deep module 与 CLI Adapter**

实现 exact CLI：`compatibility candidate-write-smoke --backup <exact> --manifest <exact> --restore-root <exact> --build-identity-manifest <exact> --parent-database-identity-manifest <exact> --descendant-database-identity-output <exclusive-new-json> --evidence-mode provisional|final --evidence-dir <exact>`；缺任一 identity/mode/output、两种 manifest 互换或目录与 mode 不匹配时 fail closed。它先独立 verify backup/manifest 与父 Live subject，再创建 `subjectKind=write_smoke` restore copy及其 `DatabaseEvidenceIdentityManifest`，返回 exact `restoredDatabasePath`、`descendantDatabaseIdentityManifestPath`、before/after/delta paths。Task 8 固定 `--evidence-mode provisional`，在该副本所有候选 writer 停止时生成 provisional `pre-write-smoke.json`；不得覆盖 Task 7 的 provisional strict pre/post，也不得写入 final evidence directory。随后只在隔离端口和该副本启动 candidate api/worker/scheduler，验证 /health/ready、/api/papers、GET /api/v2/jobs、POST /api/v2/papers/paper-1/sources 创建的一次 fake processing job、GET /api/v2/jobs/{job_id}/events、/workspace/、/legacy/ 和 MCP tools/list。测试记录 exact request/job/source IDs、每个预期 changed new/aux table PK、operation 与 before/after row hash 到 exclusive-create provisional `write-smoke-delta.json`；不得用 count-only summary。冻结 Node runtime 和 rollback startup snapshot 必须继续可用，Live Node production 全程 active，candidate 禁止连接 Live path/runtime namespace。

- [ ] **Step 6B（2–5 分钟）：重跑同一 candidate-write-smoke 测试并确认 GREEN**

~~~powershell
.\.venv\Scripts\python.exe -B -m unittest backend.tests.test_production_candidate_e2e.ProductionCandidateE2ETests.test_candidate_write_smoke_uses_verified_descendant_and_never_live -v
~~~

Expected GREEN: 1 test OK；exact descendant/parent chain 与 before/after/delta artifacts 全部可验证，Live path/owner marker/user PDF/real network/real credential 调用数均为 0。

- [ ] **Step 6C（按实际时长）：在 frozen provisional identity 下执行一次 run-bound smoke**

Task 7 Step 12 完成后，在同一个 operator session 运行一次真实 provisional capture；wrapper 必须从 child JSON 验证并绑定新 descendant database identity：

~~~powershell
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$p6PreflightWriteIdentityPath = Join-Path $p6PreflightDir 'write-smoke-database-identity-v1.json'
$p6PreflightWriteJson = & $p6Python -B -m backend.app.cli.compatibility capture-evidence --key candidate-write-smoke --phase provisional --result-kind json-cli @p6ProvisionalCaptureRunArgs --build-identity-manifest $p6ProvisionalBuildIdentityPath --database-identity-from-json descendantDatabaseIdentityManifestPath --output (Join-Path $p6PreflightDir 'candidate-write-smoke.capture.json') --artifact-from-json beforePath --artifact-from-json afterPath --artifact-from-json deltaLedgerPath --artifact-from-json descendantDatabaseIdentityManifestPath -- $p6Python -B -m backend.app.cli.compatibility candidate-write-smoke --backup $p6Create.backupPath --manifest $p6Create.manifestPath --restore-root (Join-Path $p6PreflightDir 'write-smoke-descendants') --build-identity-manifest $p6ProvisionalBuildIdentityPath --parent-database-identity-manifest $p6LiveDatabaseIdentityPath --descendant-database-identity-output $p6PreflightWriteIdentityPath --evidence-mode provisional --evidence-dir $p6PreflightDir
$p6PreflightWriteExit = $LASTEXITCODE
if ($p6PreflightWriteExit -ne 0) { throw "P6 provisional candidate write-smoke capture failed with exit code $p6PreflightWriteExit." }
$p6PreflightWrite = $p6PreflightWriteJson | ConvertFrom-Json
if (-not $p6PreflightWrite.ok -or $p6PreflightWrite.descendantDatabaseIdentityManifestPath -ne $p6PreflightWriteIdentityPath) { throw 'P6 provisional candidate write-smoke returned an invalid descendant identity.' }
$p6PreflightWriteDatabaseIdentityPath = (Resolve-Path -LiteralPath $p6PreflightWrite.descendantDatabaseIdentityManifestPath).Path
~~~

Expected: 全部成功；隔离 candidate 的进程 namespace/端口内没有 Node HTTP/Worker/Scheduler，Live production owner/DB 未变化，冻结 Node rollback artifact 与 startup snapshot 已验证可用；job status 只来自五值 enum，delta ledger 不含正文、credential 或 provider raw body。

- [ ] **Step 7（2–5 分钟）：验证 explained write-smoke delta**

drain/停止所有隔离 candidate writer 后生成 provisional `post-write-smoke.json`，运行 `compare --mode explained-write --before pre-write-smoke.json --after post-write-smoke.json --delta-ledger write-smoke-delta.json`。全部旧表 counts/PK/row hashes、papersLegacyColumnsHash、translationsLegacyContentHash 必须全等；document_sources、processing_jobs、processing_job_events 等 new/aux table 的每个变化必须与 exact ledger row 一一对应。该 policy 不允许修改或替代 Task 7 provisional strict evidence，更不能替代 Task 10 frozen-identity final evidence。

~~~powershell
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
& $p6Python -B -m backend.app.cli.compatibility capture-evidence --key explained-write-compare --phase provisional --result-kind json-cli @p6ProvisionalCaptureRunArgs --build-identity-manifest $p6ProvisionalBuildIdentityPath --database-identity-manifest $p6PreflightWriteDatabaseIdentityPath --output (Join-Path $p6PreflightDir 'explained-write-compare.capture.json') -- $p6Python -B -m backend.app.cli.compatibility compare --mode explained-write --before $p6PreflightWrite.beforePath --after $p6PreflightWrite.afterPath --delta-ledger $p6PreflightWrite.deltaLedgerPath
$p6PreflightWriteCompareExit = $LASTEXITCODE
if ($p6PreflightWriteCompareExit -ne 0) { throw "P6 provisional explained-write comparison failed with exit code $p6PreflightWriteCompareExit." }
~~~

Expected: 旧数据 hash 全等；允许的新表 row delta 全部可追溯到唯一 smoke job；任何未解释、多余或遗漏变化阻止完成。

- [ ] **Step 8（2–5 分钟）：更新状态文档**

README 与 docs/DATABASE.md 在 source freeze 前写成状态中立的运维说明：记录 candidate/frozen image digest、revision、启动命令、保留期限，以及“权威当前 owner 读取 `data/compatibility/runtime/production-owner.json`”的规则；不硬编码 active/inactive 当前值。Task 10 promotion 只更新 gitignored runtime evidence，不在 gate 后修改 README、runbook 或其他受 sourceTreeHash 覆盖的文件。

## Task 9：分别演练应用回滚与破坏性数据恢复

**Files:**
- Modify: docs/DATABASE.md
- Modify: backend/tests/test_compatibility_gate.py
- Modify: backend/tests/test_production_candidate_e2e.py
- Modify: backend/app/cli/compatibility.py

- [ ] **Step 1（2–5 分钟）：写应用回滚顺序红测**

新增 `CompatibilityGateTests.test_runtime_rollback_keeps_nonactive_owner_until_legacy_smoke`；分别从 `node_quiesced|handoff_pending` 起步，并从 `python_active` 先 CAS 到 `handoff_pending`，对每个相邻事件注入错序，断言 `ROLLBACK_ORDER_INVALID`。统一顺序必须是：owner 保持 `node_quiesced|handoff_pending` 非 active → 清除/使 authorization 不可继承 → drain Python/停止新流量与 claim → 释放 Worker/Scheduler locks和所有连接 → 按 frozen snapshot 启动 Node → legacy smoke → 最后且仅最后 CAS `node_active`。start/smoke 失败必须保持 non-active，不得提前 active。测试同时要求版本化 rollback map：`RUNTIME_ENVIRONMENT=live`、`RUNTIME_NAMESPACE=production`；P0.1 六项 `API_BACKEND_MODE=legacy`、`DOCUMENT_PIPELINE_MODE=legacy`、`GENERATION_PIPELINE_MODE=legacy`、`ARTIFACT_READ_MODE=legacy`、`ARTIFACT_WRITE_MODE=legacy`、`OCR_ENABLED=0`；P5 `OBSIDIAN_ENABLED=0`；P6 `PAPER_STUDY_MCP_MODE=legacy`，以及 `UI_ENTRY=react`。Node environment 必须不存在 promotion authorization/startup capability；任何未知值、`python`、`prefer_new`、遗留 authorization 或缺项均拒绝。`UI_ENTRY=legacy` 仅是独立 UI-root rollback，不替代 backend map。

- [ ] **Step 2（2–5 分钟）：确认红测**

Run:

~~~powershell
.\.venv\Scripts\python.exe -B -m unittest backend.tests.test_compatibility_gate.CompatibilityGateTests.test_runtime_rollback_keeps_nonactive_owner_until_legacy_smoke -v
~~~

Expected RED: gate 尚不验证事件顺序。

- [ ] **Step 3（2–5 分钟）：实现 runtime rollback validator**

实现唯一 rollback tail validator：若起点为 `python_active`，先以 receipt-bound CAS 进入 `handoff_pending`；若已是 `node_quiesced|handoff_pending` 则保持原 non-active state。随后清 authorization capability/env → 停入口新流量与 API drain → Worker 停 claim并完成 transaction → Scheduler/Obsidian/MCP 停止 → 停 FastAPI → 释放 Worker/Scheduler role locks和所有 DB/provider connections → 以 exact startup-only frozen snapshot 启动 Node → 完整 legacy smoke → 最后 CAS `node_active`。每步带 durable sequence/time/process/lock identity；start/smoke 失败写 `recovery_failed` 并保持 non-active marker，不接受未定义 rollout value，也不运行 schema downgrade。

- [ ] **Step 4（2–5 分钟）：重新运行 runtime rollback 顺序测试并确认 GREEN**

Run:

~~~powershell
.\.venv\Scripts\python.exe -B -m unittest backend.tests.test_compatibility_gate.CompatibilityGateTests.test_runtime_rollback_keeps_nonactive_owner_until_legacy_smoke -v
~~~

Expected GREEN: 错序被拒绝，正确顺序通过。

- [ ] **Step 4A（2–5 分钟）：写 rollback-smoke/recovery-smoke typed identity 红测**

新增 `ProductionCandidateE2ETests.test_rollback_and_recovery_smokes_require_exact_build_and_descendant_database_identities`。fixture 准备 missing/swapped/stale manifest 与 valid descendant；有效 RED 必须是命令尚不存在或错误接受至少一种 typed mismatch，不得由 fixture/import/skip 造成。

- [ ] **Step 4B（2–5 分钟）：运行并确认 rollback-smoke/recovery-smoke RED**

~~~powershell
.\.venv\Scripts\python.exe -B -m unittest backend.tests.test_production_candidate_e2e.ProductionCandidateE2ETests.test_rollback_and_recovery_smokes_require_exact_build_and_descendant_database_identities -v
~~~

- [ ] **Step 4C（2–5 分钟）：实现最小 rollback-smoke/recovery-smoke CLI**

两个 exact signature 固定为 `rollback-smoke --database <exact> --build-identity-manifest <exact> --database-identity-manifest <exact-descendant> --rollback-profile frozen-node --evidence-output <exclusive-new-json>` 与 `recovery-smoke --database <exact> --build-identity-manifest <exact> --database-identity-manifest <same-exact-descendant> --python-profile production --evidence-output <exclusive-new-json>`。两命令先验证 `--database` platform file identity 等于 manifest subject，随后执行各自真实隔离 smoke；返回 artifact path/SHA、完整事件顺序与相同 lineage/subject，禁止从 path、backup 或 build manifest重算 database identity。rollback-smoke 必须沿用统一 non-active → clear authorization → drain/release → Node start → smoke → final CAS 语义的隔离等价事件，不允许提前 active。

- [ ] **Step 4D（2–5 分钟）：以相同目标确认 rollback-smoke/recovery-smoke GREEN**

~~~powershell
.\.venv\Scripts\python.exe -B -m unittest backend.tests.test_production_candidate_e2e.ProductionCandidateE2ETests.test_rollback_and_recovery_smokes_require_exact_build_and_descendant_database_identities -v
~~~

Expected GREEN: 1 test OK、0 skip；missing/swapped/stale identity 在任何 stop/start/DB/provider 副作用前拒绝，合法隔离 smoke 返回 exact typed evidence。

- [ ] **Step 5（2–5 分钟）：在扩展 schema 上执行 Node rollback smoke**

对隔离 DB 执行一次真实顺序演练：先启动 Python candidate，按 Step 3 drain 并停止 candidate；随后以完整 rollback map 启动 frozen Node，调用 /api/papers、paper update、note、reviews、PDF、workspace、legacy；用隔离 settings/Fake credential 验证 legacy `apiKey|ocrApiKey|embedApiKey|s2ApiKey` compatibility fields 仍可供冻结 Node/Python rollback consumers读取且不打印值；停止 Node 后比较新表 count/hash，并复验 `processing_jobs` exact ordered columns、`processingJobs`/`processingJobSpecs` count/hash/strict decode与五个 exact trigger 的 normalized SQL SHA。该演练的事件时间、lease/连接释放、完整 startup map 与 smoke 结果写入本次 provisional run root且固定 `provisional=true`；不得用纯 unit simulation 代替，Task 10 冻结 identity 后必须重跑并生成 final evidence。

Expected: legacy 流程成功，新表未被 Node 删除或改写；`processing_jobs.spec_json` 与两个 spec guard、三个 FTS trigger完整；papers.explainer、papers.pdf_path、translations.content 仍存在。

- [ ] **Step 6（2–5 分钟）：恢复 Python runtime**

停止 frozen Node，确认无 child process/DB writer，再仅在同一隔离 subject/runtime namespace 以完整 candidate map 启动 Python worker/scheduler/api/mcp；运行 readiness、MCP tools/list 与一个 read-only smoke，并把事件写入同一 provisional subject identity。此步骤不是 Live promotion，不接受 `environment=live`。

Expected: Python candidate 正常恢复，Worker/Scheduler 各自只有一个 owner且 API/MCP 不占用 singleton role lock。

Task 8 explained-write capture 通过后，把上述两个真实演练作为同一 preflight run 的 typed records 重跑；两者必须共享 candidate write-smoke 的 exact descendant subject，且每个 native exit 在解析前保存：

~~~powershell
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$p6PreflightRollbackPath = Join-Path $p6PreflightDir 'frozen-node-rollback.json'
& $p6Python -B -m backend.app.cli.compatibility capture-evidence --key frozen-node-rollback --phase provisional --result-kind json-cli @p6ProvisionalCaptureRunArgs --build-identity-manifest $p6ProvisionalBuildIdentityPath --database-identity-manifest $p6PreflightWriteDatabaseIdentityPath --output (Join-Path $p6PreflightDir 'frozen-node-rollback.capture.json') --artifact ("frozen-node-rollback=" + $p6PreflightRollbackPath) -- $p6Python -B -m backend.app.cli.compatibility rollback-smoke --database $p6PreflightWrite.restoredDatabasePath --build-identity-manifest $p6ProvisionalBuildIdentityPath --database-identity-manifest $p6PreflightWriteDatabaseIdentityPath --rollback-profile frozen-node --evidence-output $p6PreflightRollbackPath
$p6PreflightRollbackExit = $LASTEXITCODE
if ($p6PreflightRollbackExit -ne 0) { throw "P6 provisional frozen Node rollback capture failed with exit code $p6PreflightRollbackExit." }
$p6PreflightRecoveryPath = Join-Path $p6PreflightDir 'python-recovery.json'
& $p6Python -B -m backend.app.cli.compatibility capture-evidence --key python-recovery --phase provisional --result-kind json-cli @p6ProvisionalCaptureRunArgs --build-identity-manifest $p6ProvisionalBuildIdentityPath --database-identity-manifest $p6PreflightWriteDatabaseIdentityPath --output (Join-Path $p6PreflightDir 'python-recovery.capture.json') --artifact ("python-recovery=" + $p6PreflightRecoveryPath) -- $p6Python -B -m backend.app.cli.compatibility recovery-smoke --database $p6PreflightWrite.restoredDatabasePath --build-identity-manifest $p6ProvisionalBuildIdentityPath --database-identity-manifest $p6PreflightWriteDatabaseIdentityPath --python-profile production --evidence-output $p6PreflightRecoveryPath
$p6PreflightRecoveryExit = $LASTEXITCODE
if ($p6PreflightRecoveryExit -ne 0) { throw "P6 provisional Python recovery capture failed with exit code $p6PreflightRecoveryExit." }
~~~

Expected: 两个 wrapper/child raw exit 0；event order、locks、ports、readiness、完整 startup map 与同一 descendant database/build identity 可重验，Live owner/DB 始终不变。

- [ ] **Step 7（2–5 分钟）：写 rehearsal/production restore 分离与 BoundRoot 红测**

新增 `CompatibilityGateTests.test_data_restore_rejects_any_live_process`、`ProductionCandidateE2ETests.test_restore_install_rehearsal_isolated_and_returns_installed_identity`、`test_restore_reuses_p0_bound_root_and_rejects_hostile_root_or_parent_swap` 与 `test_restore_production_data_requires_explicit_offline_authorization`。前者分别模拟 Node、FastAPI、Worker、Scheduler、MCP、Obsidian projector 仍活跃，真实 restore 每次都返回 `RESTORE_REQUIRES_FULL_STOP`；rehearsal test 交换/遗漏 build 与父 Live database manifest、篡改 parent backup、target file identity/output path，并证明 rehearsal 永远拒绝 Live target；hostile test 在 pathname check后把 output root/parent换成 junction/reparse/symlink，path-open/sqlite-connect tripwire必须保持 0；production test要求独立 recovery authorization、全 writer stop proof、target identity/hash和现 DB retained-recovery path，且禁止接受 rehearsal flag冒充授权。

- [ ] **Step 8（2–5 分钟）：运行并确认 restore Interface RED**

~~~powershell
.\.venv\Scripts\python.exe -B -m unittest backend.tests.test_compatibility_gate.CompatibilityGateTests.test_data_restore_rejects_any_live_process backend.tests.test_production_candidate_e2e.ProductionCandidateE2ETests.test_restore_install_rehearsal_isolated_and_returns_installed_identity backend.tests.test_production_candidate_e2e.ProductionCandidateE2ETests.test_restore_reuses_p0_bound_root_and_rejects_hostile_root_or_parent_swap backend.tests.test_production_candidate_e2e.ProductionCandidateE2ETests.test_restore_production_data_requires_explicit_offline_authorization -v
~~~

Expected RED: 两个 distinct CLI/BoundRoot reuse 尚不存在或错误接受 hostile/Live target；fixture/import/skip 不算有效 RED。

- [ ] **Step 9（2–5 分钟）：实现最小且分离的 destructive restore Interfaces**

`restore-install-rehearsal` exact signature 固定为 `restore-install-rehearsal --backup <exact> --manifest <exact> --target-database <exact-isolated> --expected-target-sha256 <exact> --rehearsal-root <exact> --build-identity-manifest <exact> --parent-database-identity-manifest <exact> --installed-database-identity-output <exclusive-new-json> --evidence-output <exclusive-new-json>`；它只接受 run-local isolated root，精确拒绝 Live target/parent，不接收 authorization，也不能切 production owner。`restore-production-data` 是不同 Interface：除 exact backup/Manifest/target/build/database identity外，强制 `--recovery-authorization <exact> --expected-recovery-authorization-sha256 <sha> --full-writer-stop-proof <exact> --retained-current-output <exclusive-new> --recovery-lease-output <exclusive-new>`；authorization 必须由独立 operator action生成并绑定 target platform identity/hash、origin/lineage/backup、owner state和 TTL，不能由 shutdown promotion authorization或 rehearsal flag替代。两者均复用 P0 `BoundRoot`：Windows 持有 output-root no-delete-share handle直到 child/install/verify 完成；POSIX只通过 bound dirfd + openat/renameat + O_NOFOLLOW 创建/替换；能力不足在首次写前 fail closed。保留当前 target为 hash-named可恢复文件，再在同一 bound root原子安装并验证 integrity/FK/count/hash，创建新 `DatabaseEvidenceIdentityManifest`。安装验证除 integrity/FK/count/hash外，必须固定确认 revision `20260807_03`、完整 `processing_jobs` ordered columns、`processingJobs`/`processingJobSpecs` count/hash/strict decode，以及 trigger 名称集合精确为 `processing_jobs_spec_guard_insert|processing_jobs_spec_guard_update|document_chunks_fts_ai|document_chunks_fts_ad|document_chunks_fts_au` 且 normalized SQL SHA匹配；旧列与旧表仍存在。任何 writer/sidecar、root/parent swap、target hash漂移、schema inventory或 identity/parent mismatch都零写拒绝。P6 只在测试中对临时目录调用 `restore-production-data`，真实 offline restore仅写入 runbook，不自动执行。

- [ ] **Step 9A（2–5 分钟）：以相同目标确认 restore Interfaces GREEN**

Run:

~~~powershell
.\.venv\Scripts\python.exe -B -m unittest backend.tests.test_compatibility_gate.CompatibilityGateTests.test_data_restore_rejects_any_live_process backend.tests.test_production_candidate_e2e.ProductionCandidateE2ETests.test_restore_install_rehearsal_isolated_and_returns_installed_identity backend.tests.test_production_candidate_e2e.ProductionCandidateE2ETests.test_restore_reuses_p0_bound_root_and_rejects_hostile_root_or_parent_swap backend.tests.test_production_candidate_e2e.ProductionCandidateE2ETests.test_restore_production_data_requires_explicit_offline_authorization -v
~~~

Expected GREEN: 4 tests OK、0 skip；六类活跃进程、hostile root/parent swap及所有 typed-identity/authorization/output/schema-inventory mismatch 均在首次写前被拒绝；合法 rehearsal和仅临时目录内的 authorized production-interface fixture返回新 subject manifest及可恢复旧 target，且 JobSpec projection与五 trigger完整。真实 Live DB 未被调用。

- [ ] **Step 10（2–5 分钟）：在隔离安装路径演练数据恢复**

停止全部测试进程。本 block 不依赖 Task 7 的 `$p6Create`、`$p6Restore` 或 `$p6DrillDb` session variable：它从 Step 0 显式 process-scoped run manifest path/SHA/runId 与 build identity path/SHA 重建 immutable snapshot，自行在该 run root fresh create → verify → restore-check 一份 backup，再把 `restore-install-rehearsal` 作为 wrapper-produced provisional record安装到明确的隔离 target并启动 FastAPI smoke。target staging/install 全部经 P0 BoundRoot，不使用 `Copy-Item` 或 pathname-check-then-open。不得对 `data/app.db` 调用 rehearsal或 production restore，并必须保存/恢复调用者的 `DB_PATH`：

~~~powershell
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$p6Python = (Resolve-Path -LiteralPath '.\.venv\Scripts\python.exe').Path
$p6LiveDb = (Resolve-Path -LiteralPath 'data/app.db').Path
$p6LiveDatabaseIdentityPath = (Resolve-Path -LiteralPath 'data/compatibility/runtime/live-database-identity-v1.json').Path
$p6PreflightDirInput = [Environment]::GetEnvironmentVariable('P6_PREFLIGHT_RUN_DIR', 'Process')
$p6ProvisionalIdentityInput = [Environment]::GetEnvironmentVariable('P6_PROVISIONAL_BUILD_IDENTITY', 'Process')
$p6ProvisionalIdentityShaInput = [Environment]::GetEnvironmentVariable('P6_PROVISIONAL_BUILD_IDENTITY_SHA256', 'Process')
$p6ProvisionalRunId = [Environment]::GetEnvironmentVariable('P6_PROVISIONAL_EVIDENCE_RUN_ID', 'Process')
$p6ProvisionalRunManifestInput = [Environment]::GetEnvironmentVariable('P6_PROVISIONAL_EVIDENCE_RUN_MANIFEST_PATH', 'Process')
$p6ProvisionalRunManifestShaInput = [Environment]::GetEnvironmentVariable('P6_PROVISIONAL_EVIDENCE_RUN_MANIFEST_SHA256', 'Process')
if ([string]::IsNullOrWhiteSpace($p6PreflightDirInput) -or [string]::IsNullOrWhiteSpace($p6ProvisionalIdentityInput) -or $p6ProvisionalIdentityShaInput -notmatch '^[0-9a-f]{64}$' -or $p6ProvisionalRunId -notmatch '^[0-9a-f]{32}$' -or [string]::IsNullOrWhiteSpace($p6ProvisionalRunManifestInput) -or $p6ProvisionalRunManifestShaInput -notmatch '^[0-9a-f]{64}$') { throw 'P6 exact provisional run/build process inputs are required.' }
$p6PreflightDir = (Resolve-Path -LiteralPath $p6PreflightDirInput).Path
$p6ProvisionalBuildIdentityPath = (Resolve-Path -LiteralPath $p6ProvisionalIdentityInput).Path
$p6ProvisionalRunManifestPath = (Resolve-Path -LiteralPath $p6ProvisionalRunManifestInput).Path
if ((Split-Path -Parent $p6ProvisionalRunManifestPath) -ne $p6PreflightDir -or (Split-Path -Leaf $p6PreflightDir) -ne ('run-' + $p6ProvisionalRunId)) { throw 'P6 provisional run path does not match runId.' }
if ((Get-FileHash -Algorithm SHA256 -LiteralPath $p6ProvisionalBuildIdentityPath).Hash.ToLowerInvariant() -ne $p6ProvisionalIdentityShaInput -or (Get-FileHash -Algorithm SHA256 -LiteralPath $p6ProvisionalRunManifestPath).Hash.ToLowerInvariant() -ne $p6ProvisionalRunManifestShaInput) { throw 'P6 provisional run/build file SHA drifted.' }
$p6ProvisionalCaptureRunArgs = @('--run-manifest',$p6ProvisionalRunManifestPath,'--expected-run-manifest-sha256',$p6ProvisionalRunManifestShaInput)
& $p6Python -B -m backend.app.cli.compatibility verify-identity --build-identity-manifest $p6ProvisionalBuildIdentityPath
$p6RestoreIdentityVerifyExit = $LASTEXITCODE
if ($p6RestoreIdentityVerifyExit -ne 0) { throw "P6 restore rehearsal build identity verification failed with exit code $p6RestoreIdentityVerifyExit." }
$p6InstallBackupRootItem = New-Item -ItemType Directory -Path (Join-Path $p6PreflightDir 'restore-install-rehearsal-backup')
$p6InstallBackupRoot = (Resolve-Path -LiteralPath $p6InstallBackupRootItem.FullName).Path
$p6InstallSourceRootItem = New-Item -ItemType Directory -Path (Join-Path $p6PreflightDir 'restore-install-rehearsal-source')
$p6InstallSourceRoot = (Resolve-Path -LiteralPath $p6InstallSourceRootItem.FullName).Path
$p6InstallCreateJson = & $p6Python -B -m backend.app.cli.database_backup create --database $p6LiveDb --output-directory $p6InstallBackupRoot --label ("pre-p6-restore-install-rehearsal-" + $p6ProvisionalRunId)
$p6InstallCreateExit = $LASTEXITCODE
if ($p6InstallCreateExit -ne 0) { throw "P6 restore rehearsal backup create failed with exit code $p6InstallCreateExit." }
$p6InstallCreate = $p6InstallCreateJson | ConvertFrom-Json
if (-not $p6InstallCreate.ok) { throw 'P6 restore rehearsal backup create JSON did not report success.' }
$p6InstallCreateBackupCompatibleLogicalSha256 = [string]$p6InstallCreate.logicalSha256
$p6InstallVerifyJson = & $p6Python -B -m backend.app.cli.database_backup verify --backup $p6InstallCreate.backupPath --manifest $p6InstallCreate.manifestPath
$p6InstallVerifyExit = $LASTEXITCODE
if ($p6InstallVerifyExit -ne 0) { throw "P6 restore rehearsal backup verify failed with exit code $p6InstallVerifyExit." }
$p6InstallVerify = $p6InstallVerifyJson | ConvertFrom-Json
$p6InstallVerifyBackupCompatibleLogicalSha256 = [string]$p6InstallVerify.logicalSha256
if (-not $p6InstallVerify.ok -or $p6InstallVerifyBackupCompatibleLogicalSha256 -ne $p6InstallCreateBackupCompatibleLogicalSha256) { throw 'P6 restore rehearsal backup-compatible verify mismatch.' }
$p6InstallRestoreJson = & $p6Python -B -m backend.app.cli.database_backup restore-check --backup $p6InstallCreate.backupPath --manifest $p6InstallCreate.manifestPath --output-directory $p6InstallSourceRoot
$p6InstallRestoreExit = $LASTEXITCODE
if ($p6InstallRestoreExit -ne 0) { throw "P6 restore rehearsal restore-check failed with exit code $p6InstallRestoreExit." }
$p6InstallRestore = $p6InstallRestoreJson | ConvertFrom-Json
$p6InstallRestoreBackupCompatibleLogicalSha256 = [string]$p6InstallRestore.logicalSha256
if (-not $p6InstallRestore.ok -or $p6InstallRestoreBackupCompatibleLogicalSha256 -ne $p6InstallVerifyBackupCompatibleLogicalSha256) { throw 'P6 restore rehearsal backup-compatible restore-check mismatch.' }
$p6InstallSourceDb = (Resolve-Path -LiteralPath $p6InstallRestore.restoredPath).Path
$p6InstallRootItem = New-Item -ItemType Directory -Path (Join-Path $p6PreflightDir 'restore-install-rehearsal-checks')
$p6InstallRoot = (Resolve-Path -LiteralPath $p6InstallRootItem.FullName).Path.TrimEnd([IO.Path]::DirectorySeparatorChar, [IO.Path]::AltDirectorySeparatorChar)
$p6InstallRelativeTarget = ("install-validation-" + [guid]::NewGuid().ToString('N') + '/app.db')
$p6StageJson = & $p6Python -B -m backend.app.cli.compatibility prepare-restore-rehearsal-target --bound-root $p6InstallRoot --relative-target $p6InstallRelativeTarget --seed-database $p6InstallSourceDb
$p6StageExit = $LASTEXITCODE
if ($p6StageExit -ne 0) { throw "P6 bound-root rehearsal target staging failed with exit code $p6StageExit." }
$p6Stage = $p6StageJson | ConvertFrom-Json
$p6InstallTarget = (Resolve-Path -LiteralPath ([string]$p6Stage.targetDatabasePath)).Path
$p6InstallPrefix = $p6InstallRoot + [IO.Path]::DirectorySeparatorChar
if (-not $p6InstallTarget.StartsWith($p6InstallPrefix, [StringComparison]::OrdinalIgnoreCase) -or $p6InstallTarget -eq $p6LiveDb) { throw 'P6 restore-install-rehearsal target is not an isolated explicit path.' }
$p6TargetSha = [string]$p6Stage.targetSha256
if ($p6TargetSha -notmatch '^[0-9a-f]{64}$' -or (Get-FileHash -Algorithm SHA256 -LiteralPath $p6InstallTarget).Hash.ToLowerInvariant() -ne $p6TargetSha) { throw 'P6 bound-root staged target SHA-256 mismatch.' }
$p6RestorePreviousDbPath = [Environment]::GetEnvironmentVariable('DB_PATH', 'Process')
$p6RestoreHadDbPath = $null -ne $p6RestorePreviousDbPath
try {
  $p6PreflightInstalledIdentityPath = Join-Path $p6PreflightDir 'restore-install-rehearsal-database-identity-v1.json'
  $p6PreflightRestoreEvidencePath = Join-Path $p6PreflightDir 'restore-install-rehearsal.json'
  $p6PreflightRestoreJson = & $p6Python -B -m backend.app.cli.compatibility capture-evidence --key restore-install-rehearsal --phase provisional --result-kind json-cli @p6ProvisionalCaptureRunArgs --build-identity-manifest $p6ProvisionalBuildIdentityPath --database-identity-from-json installedDatabaseIdentityManifestPath --output (Join-Path $p6PreflightDir 'restore-install-rehearsal.capture.json') --artifact-from-json installedDatabaseIdentityManifestPath --artifact ("restore-install-rehearsal=" + $p6PreflightRestoreEvidencePath) -- $p6Python -B -m backend.app.cli.compatibility restore-install-rehearsal --backup $p6InstallCreate.backupPath --manifest $p6InstallCreate.manifestPath --target-database $p6InstallTarget --expected-target-sha256 $p6TargetSha --rehearsal-root $p6InstallRoot --build-identity-manifest $p6ProvisionalBuildIdentityPath --parent-database-identity-manifest $p6LiveDatabaseIdentityPath --installed-database-identity-output $p6PreflightInstalledIdentityPath --evidence-output $p6PreflightRestoreEvidencePath
  $p6PreflightRestoreExit = $LASTEXITCODE
  if ($p6PreflightRestoreExit -ne 0) { throw "P6 isolated restore-install-rehearsal capture failed with exit code $p6PreflightRestoreExit." }
  $p6PreflightRestore = $p6PreflightRestoreJson | ConvertFrom-Json
  if (-not $p6PreflightRestore.ok -or $p6PreflightRestore.installedDatabaseIdentityManifestPath -ne $p6PreflightInstalledIdentityPath) { throw 'P6 isolated restore-install-rehearsal returned an invalid installed database identity.' }
  $env:DB_PATH = $p6InstallTarget
  $p6InstalledInspectJson = & $p6Python -B -m backend.app.cli.database_backup inspect --database $p6InstallTarget
  $p6InstalledInspectExit = $LASTEXITCODE
  if ($p6InstalledInspectExit -ne 0) { throw "P6 installed-copy inspect failed with exit code $p6InstalledInspectExit." }
  $p6Installed = $p6InstalledInspectJson | ConvertFrom-Json
  if (-not $p6Installed.ok -or $p6Installed.database.alembicVersion -ne '20260807_03') { throw 'P6 installed copy failed fingerprint/revision validation.' }
  & $p6Python -B -m unittest backend.tests.test_api_health.ApiHealthTests.test_ready_on_expected_head -v
  $p6InstalledSmokeExit = $LASTEXITCODE
  if ($p6InstalledSmokeExit -ne 0) { throw "P6 installed-copy FastAPI smoke failed with exit code $p6InstalledSmokeExit." }
} finally {
  if ($p6RestoreHadDbPath) { $env:DB_PATH = $p6RestorePreviousDbPath } else { Remove-Item Env:DB_PATH -ErrorAction SilentlyContinue }
}
~~~

Expected: block 自身 fresh backup 的 create/verify/restore-check 与 wrapper-produced `restore-install-rehearsal`、inspect、health raw exit 全为 0；全部 backup/source/target/recovery/evidence artifacts 位于同一 provisional run root。BoundRoot 从首次 target staging到原子安装/验证持续绑定目录对象，安装副本 health/integrity/hash成功，原隔离 target recovery文件可反向安装；调用者 `DB_PATH` 精确恢复，Live resolved path-open/sqlite-connect 次数为 0。真实 `restore-production-data` 未被调用。随后才返回 Task 7 Step 13 运行唯一完整 preflight gate。

## Task 10：最终验证并冻结“保留、不删除”边界

**Files:**
- Modify: backend/tests/test_compatibility_gate.py
- Modify: backend/tests/test_runtime_ownership.py
- Create: backend/tests/test_production_rollback.py
- Create: backend/app/application/final_window.py
- Create: backend/app/application/runtime_handoff.py
- Create: backend/app/application/production_rollback.py
- Modify: backend/app/runtime.py
- Modify: backend/app/providers/runtime_lease.py
- Modify: backend/app/api/compat/gates.py
- Modify: backend/app/cli/compatibility.py
- Modify: README.md
- Modify: docs/DATABASE.md
- Verify only: server.js
- Verify only: db.js
- Verify only: db/schema.sql
- Final evidence output only: data/compatibility/evidence/ 与 data/compatibility/runtime/，保持 gitignored

- [ ] **Step 1（2–5 分钟）：写物理保留与 canonical enum 红测**

新增 `CompatibilityGateTests.test_legacy_runtime_schema_and_fields_remain_present`，断言 server.js、db.js、frozen-node image、全部旧 route inventory、旧表及 papers.explainer/papers.pdf_path/translations.content、legacy `apiKey|ocrApiKey|embedApiKey|s2ApiKey` fields 仍存在且 `finalize_legacy_migration` 未执行；同一测试还断言 `processing_jobs` 的全部 P1 legacy columns 与 P2 additive columns（含 non-null `spec_json`）仍按冻结顺序存在，`processingJobs`/`processingJobSpecs` count/hash/strict decode一致，schema trigger 总数与名称精确为两个 spec guard加三个 FTS trigger，frozen Node 能忽略 additive schema而不删除或改写。新增 `test_canonical_domain_enums_remain_exact`，逐一断言：SourceMode=`native|ocr`；SourceDocumentStatus 与 GeneratedArtifactStatus 都精确为 `queued|running|ready|failed|stale|cancelled`；ArtifactKind=`explainer|translation|summary|outline|study_card|classification|metadata`；ProcessingJobType=`source_materialize|ocr|explain|translate|embed|obsidian_export|obsidian_sync`；ProcessingJobStatus=`queued|running|succeeded|failed|cancelled`；CredentialKind=`llm|ocr|embedding|semantic_scholar`。任何缺值、多值、别名或 P6-only kind/status 都失败。新增 `test_static_runbook_is_state_neutral_and_preserves_deletion_boundary`，证明 README/docs 不硬编码当前 owner、指向 runtime marker，并完整保留独立删除计划边界。

- [ ] **Step 2（2–5 分钟）：运行并确认保留/enum gate RED**

Run:

~~~powershell
.\.venv\Scripts\python.exe -B -m unittest backend.tests.test_compatibility_gate.CompatibilityGateTests.test_legacy_runtime_schema_and_fields_remain_present backend.tests.test_compatibility_gate.CompatibilityGateTests.test_canonical_domain_enums_remain_exact backend.tests.test_compatibility_gate.CompatibilityGateTests.test_static_runbook_is_state_neutral_and_preserves_deletion_boundary -v
~~~

Expected RED: final gate 尚未同时验证 rollback artifacts、legacy credential fields、禁止 finalization、全部 canonical enum allowlist 与 static runbook；若实体文件本身已缺失则立即停止并先恢复。

- [ ] **Step 3（2–5 分钟）：实现最终保留/enum validator**

只读取 source/schema/evidence manifest 与显式 README/docs 路径；禁止读取真实 credential value。validator 精确比较 allowlists，缺失或多出 kind/status、legacy field removal/finalization marker、硬编码 owner 状态或删除边界缺失均分类拒绝，不自行恢复文件或改 DB。CLI 暴露 `verify-static-runbook --readme <exact> --database-doc <exact>`，不得从 cwd 猜测替代路径。

- [ ] **Step 4（2–5 分钟）：重新运行保留/enum gate 测试并确认 GREEN**

Run:

~~~powershell
.\.venv\Scripts\python.exe -B -m unittest backend.tests.test_compatibility_gate.CompatibilityGateTests.test_legacy_runtime_schema_and_fields_remain_present backend.tests.test_compatibility_gate.CompatibilityGateTests.test_canonical_domain_enums_remain_exact backend.tests.test_compatibility_gate.CompatibilityGateTests.test_static_runbook_is_state_neutral_and_preserves_deletion_boundary -v
~~~

Expected GREEN: 所有 rollback artifact/字段、六组 canonical domain allowlist 与状态中立 static runbook 存在且精确；MCP SourceDocument view 对六种 source/artifact 状态的 wire 语义也已由 contract suite 覆盖。

- [ ] **Step 5（2–5 分钟）：写 promotion authorization 与 owner handoff 红测**

新增 `RuntimeOwnershipTests.test_live_bootstrap_rejects_missing_expired_replayed_or_identity_mismatched_authorization_before_side_effects`、`test_shutdown_gate_exclusive_creates_bound_single_use_authorization`、`test_canonical_startup_snapshot_rejects_missing_extra_wrong_or_env_override_before_side_effects`、`test_handoff_transitions_node_quiesced_to_pending_then_python_active_with_role_scoped_locks`、`test_handoff_failure_releases_python_locks_and_restores_frozen_node`、`test_quiesce_live_requires_node_active_cas_and_zero_process_port_handle_evidence`、`test_promote_requires_exact_build_database_and_startup_snapshot_paths_and_hashes`、`test_abort_cutover_restores_frozen_node_after_each_post_quiesce_failure`、`test_abort_cutover_is_token_bound_and_idempotent`、`test_abort_never_marks_node_active_before_legacy_smoke`、`test_final_window_watchdog_recovers_after_coordinator_process_exit` 与 `test_begin_handoff_takes_over_final_window_watchdog_lease`。另新增 `ProductionRollbackTests.test_python_active_commit_writes_durable_handoff_receipt`、`test_rollback_production_new_process_revalidates_all_identities_and_locks`、`test_rollback_production_resumes_after_crash_at_every_event`、`test_rollback_production_same_receipt_is_idempotent`、`test_rollback_production_start_or_smoke_failure_never_marks_active` 与 `test_resident_coordinator_recovers_or_hands_off_after_restart`。post-quiesce failure test用 named subtests注入 `cutover_create|cutover_verify|cutover_restore_check|final_suites|strict_convergence|write_smoke|rollback_recovery_restore_install|shutdown_gate`；rollback crash test逐项注入 `authorization_cleared|python_drained|locks_released|node_started|legacy_smoked|owner_cas`并从新进程重进。每条恢复路径都必须保持 `node_quiesced|handoff_pending` 非 active，清 authorization、drain Python、释放 locks、启动 frozen Node、legacy smoke，最后才 CAS `node_active`。watchdog另覆盖 operator/coordinator exit、heartbeat deadline、unused/expired authorization。spy证明所有前置拒绝发生在 socket、SQLite connect、Provider构造与 role lease前；gate/lease/receipt fixtures分别绑定 exact run、build、database、OriginReceipt、startup snapshot、owner、locks 与 cutover backup，任一 path/SHA/identity替换都失败。

- [ ] **Step 6（2–5 分钟）：运行 handoff 红测**

~~~powershell
.\.venv\Scripts\python.exe -B -m unittest backend.tests.test_runtime_ownership backend.tests.test_production_rollback -v
~~~

Expected RED: P4 live-deny adapter 尚无 P6 FinalWindowCoordinator、canonical startup snapshot、durable lease/watchdog/HandoffReceipt、restart-safe rollback coordinator 或 owner handoff；任何通过普通 boolean/逐项 env 绕开拒绝门、提前写 `node_active`、或重启后只能靠 operator 手工恢复的实现都必须 RED。fixture/import/skip 不算有效 RED。

- [ ] **Step 7（2–5 分钟）：实现 identity-bound、单次 owner transfer seam**

实现 `FinalWindowCoordinator.begin_final_window(final_run, startup_snapshot, owner_marker) -> CutoverLease` 与 `abort_cutover(lease, reason) -> FrozenNodeRecovery`。`begin_final_window` 在任何 quiesce 前 exclusive-create `data/compatibility/runtime/final-window-<runId>.json` 和仅当前 OS 用户可读的随机 256-bit token file；权限/ACL 无法收紧时 fail closed。durable lease 的 strict schema 至少绑定 `runId/EvidenceRunManifest path+SHA`、canonical `ProductionStartupSnapshot path+SHA`、owner marker path/version、`cutoverTokenSha256`（绝不保存 raw token）、build/database manifest path+SHA、OriginReceipt exact path/file SHA、databaseLineageId/liveSubjectDatabaseId、runtime namespace、完整 `frozenNodeRollbackMap` 及其 digest、coordinator/operator/watchdog PID、heartbeat deadline、phase、monotonic version、previous digest 与 self hash。rollback map 固定包含 frozen image digest、resolved server entrypoint/cwd、loopback ports、exact Live DB path和完整 legacy/off startup map，并显式排除 promotion authorization/startup capability env。所有 lease 更新使用 file lock、atomic replace、version CAS 与 hash chain；raw token 不进 stdout capture、日志、owner marker、authorization 或异常。

`create-startup-snapshot --final-evidence-run-manifest <exact> --expected-final-evidence-run-manifest-sha256 <sha> --build-identity-manifest <exact> --database-identity-manifest <exact-live> --frozen-node-rollback-map <exact-json> --output <exclusive-run-local-json>` 先产生 canonical startup snapshot；missing/extra/wrong mode、path/SHA或未知 role立即失败。`begin-final-window` 除原参数外必须接收 `--startup-snapshot <exact> --expected-startup-snapshot-sha256 <sha>` 并在返回前启动独立 watchdog确认 ready；watchdog启动失败时删除尚未发布的临时状态且 Node仍 `node_active`。`quiesce-live` 随后接收 exact run/startup/lease/token；它只允许 matching lease 的 `armed` phase和 P4 `node_active` marker作为 CAS起点，drain Node HTTP/NDJSON/Agent child/scheduler/job runner与所有已知 Live Python/MCP/Obsidian writer，平台级证明相关 PID、监听端口、SQLite transaction/DB handle/sidecar writer全部为零后才 commit owner=`node_quiesced`、lease=`node_quiesced`。任一 quiesce检查失败保持/恢复原 `node_active`，不得创建“部分 quiesced” evidence。

`abort-cutover --cutover-lease <exact> --cutover-token-file <exact> --final-evidence-run-manifest <exact> --expected-final-evidence-run-manifest-sha256 <exact> --startup-snapshot <exact> --expected-startup-snapshot-sha256 <sha> --build-identity-manifest <exact> --database-identity-manifest <exact-live> --owner-marker <exact> --reason-code <allowlisted> --recovery-output <exclusive-or-existing-same-token-json>` 明确**不要求 promotion authorization**，只接受 lease phase `armed|node_quiesced|authorization_issued|recovered`；`handoff_pending|python_active` 必须交给 promotion rollback。它先验证 exact run/token/startup/marker/build/database/origin/lineage/subject/namespace/rollback-map identities。若 phase=`armed` 且 owner仍是 matching `node_active`，只验证现有 frozen Node/legacy smoke、停止 watchdog并 seal run，不重复启动 Node；否则 owner marker持续保持 `node_quiesced`，并严格执行：清除/撤销 authorization capability与当前 process env → drain/停止任何 Python candidate → 证明零 Python PID/port/DB handle并释放 Worker/Scheduler locks → 按 lease中完整 frozen map启动 Node → 运行 `/api/papers|/api/reviews|/pdfbytes|/workspace/|/legacy/` smoke → smoke全绿后最后一次 CAS owner marker到 `node_active`。Node start或smoke失败时 lease标记 `recovery_failed`、owner marker保持 `node_quiesced`，绝不虚报 active；same-token已成功 recovery返回原 receipt且零副作用，different token/run/startup/marker/build/database identity全部在副作用前拒绝。成功 abort以 run root内 immutable `abort-recovery.json` seal failed run，后续 capture/gate禁止补写或复制，重试必须新 runId。

watchdog 必须在 quiesce 前运行并监视 operator/coordinator PID、lease heartbeat deadline 与 authorization expiry；final `capture-evidence` wrapper在 child存活期间以独立 heartbeat更新 lease。operator/coordinator退出、heartbeat timeout、authorization已创建但未进入 handoff或到期时，watchdog调用同一 `abort_cutover`。`ProductionPromotionCoordinator.begin_handoff` 必须在创建任何 Python socket/DB/provider/role lock前，以 authorization中的 exact run/lease/token/startup hash原子接管 watchdog lease并把 owner/lease CAS到 `handoff_pending`；此后 `abort-cutover` 对 `handoff_pending|python_active` fail closed，由 promotion rollback接管。promotion rollback保持 `handoff_pending`，先清 authorization、再 drain Python/释放 locks、启动 Node、legacy smoke，最后 CAS `node_active`。成功 smoke后才把 owner=`python_active`、lease=`completed`，O_EXCL写 durable HandoffReceipt并终止 cutover watchdog；常驻 ProductionOwnershipCoordinator随 Python runtime启动，验证 owner引用的 exact receipt并持续提供 restart-safe rollback协调。

shutdown gate 除 stdout外必须 O_EXCL写 schema-versioned promotion authorization，固定含 authorizationId、build path/SHA/buildId、database path/SHA/lineage/subject、startup snapshot path/SHA、OriginReceipt path/file SHA、cutover backup/Manifest SHA、runtimeNamespace、roles、Node owner-marker version、Node零资源 evidence SHA、issuedAt/expiresAt与 aggregate SHA；TTL≤15分钟。Production launcher必须同时接收 authorization、startup snapshot、build/database manifests exact path+SHA；`backend/app/runtime.py`在任何副作用前严格解码四种 typed artifact并验证当前时间、source/build/database/origin/startup/namespace/cutover chain、Node仍 quiesced与 authorization未消费。missing/extra/wrong snapshot字段、env override、模糊路径或boolean均拒绝。

`ProductionPromotionCoordinator` Interface固定为 `begin_handoff(authorization,cutover_lease,startup_snapshot)->HandoffLease`、`commit_python_owner(handoff,smoke_evidence)->HandoffReceipt`、`rollback_to_frozen_node(handoff,reason)`。begin takeover后才允许 Worker/Scheduler获取 role locks；API/MCP不获取 singleton lease。授权只能消费一次。任一 lock、process、readiness或smoke失败时保持 `handoff_pending`，清 authorization → drain/停 Python → 释放 locks → 启动 frozen Node → legacy smoke → 最后 CAS `node_active`；start/smoke失败保持 non-active。成功 smoke后先 CAS `python_active`，随即 O_EXCL写 immutable HandoffReceipt并让 owner marker引用 exact receipt path/SHA；若 receipt落盘失败，视为 promotion失败并按同一尾序回滚，不得留下不可恢复的 `python_active`。marker、receipt、recovery lease与runtime events只写 `data/compatibility/runtime/`，不写 SQLite。

`rollback-production` 是 application rollback，不是数据恢复。exact signature固定为 `rollback-production --handoff-receipt <exact> --expected-handoff-receipt-sha256 <sha> --startup-snapshot <exact> --expected-startup-snapshot-sha256 <sha> --build-identity-manifest <exact> --database-identity-manifest <exact-live> --p0-origin-receipt <exact> --expected-p0-origin-receipt-sha256 <sha> --owner-marker <exact> --recovery-lease-output <exclusive-or-existing-same-receipt> --recovery-output <exclusive-or-existing-same-receipt>`。新进程/常驻协调器均先严格复验 run/build/database/origin/startup/owner/role-lock/process identities；从 `python_active` CAS为 `handoff_pending`后写 durable hash-chained recovery lease，逐事件落盘。进程在任一事件崩溃时，下一进程只凭 exact receipt+lease继续未完成事件；same receipt successful retry零副作用，不同 receipt/run/build/database/startup/owner identity在任何 drain/start前拒绝。它绝不移动/恢复/降级 SQLite；真实数据恢复只能用前述 `restore-production-data`。

实现 `promote --authorization <exact> --expected-authorization-sha256 <sha> --final-evidence-run-manifest <exact> --expected-final-evidence-run-manifest-sha256 <sha> --cutover-lease <exact> --cutover-token-file <exact> --startup-snapshot <exact> --expected-startup-snapshot-sha256 <sha> --build-identity-manifest <exact> --database-identity-manifest <exact-live> --owner-marker <exact> --python-profile production --rollback-profile frozen-node --handoff-receipt-output <exclusive-new-json> --evidence-output <exclusive-new-json>`；CLI只组装 coordinator。validation在 begin前失败由 FinalWindowCoordinator abort；begin后失败由 ProductionPromotionCoordinator rollback，命令返回前必须留下可验证 recovery result。缺任一 typed artifact/path/SHA、未知/extra startup字段、authorization与lease binding不一致或 Live file identity drift均在任何 Python副作用前 exit 2。

- [ ] **Step 8（2–5 分钟）：重新运行 handoff 测试并确认 GREEN**

~~~powershell
.\.venv\Scripts\python.exe -B -m unittest backend.tests.test_runtime_ownership backend.tests.test_production_rollback -v
~~~

Expected GREEN: 两个模块全部 OK、0 skip；expired/replayed/mismatched artifact与startup snapshot均零副作用，八类 post-quiesce failure、operator crash/timeout/unused authorization都恢复 frozen Node；HandoffReceipt durable且可由新进程严格验证，rollback-production在每个 crash boundary可续跑、same receipt幂等，`node_active`永远晚于 legacy smoke，start/smoke失败保持 non-active且无 Python role lease/process残留。

- [ ] **Step 9（2–5 分钟）：完成所有静态文件后构建并冻结 BuildIdentityManifest**

在本步骤前完成 README/docs 的状态中立 runbook 与“保留、不删除”原则；此后直到 promotion smoke 完成，禁止修改 source、tests、README、docs、Dockerfile、compose 或部署 artifact。先产生最终前端与两个容器 artifact，再在 immutable build identity root按 buildId exclusive-create只含 source/build的 manifest；P4 Live database manifest不复制进该文件。若后续失败需要修改任何 source/build byte，旧 manifest保留不覆盖，重新构建必得新 buildId/新 path：

~~~powershell
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$p6Python = (Resolve-Path -LiteralPath '.\.venv\Scripts\python.exe').Path
$p6BuildIdentityRootItem = New-Item -ItemType Directory -Force -Path 'data/compatibility/runtime/build-identities'
$p6BuildIdentityRoot = (Resolve-Path -LiteralPath $p6BuildIdentityRootItem.FullName).Path
.\.venv\Scripts\python.exe -B -m backend.app.cli.compatibility verify-static-runbook --readme README.md --database-doc docs/DATABASE.md
if ($LASTEXITCODE -ne 0) { throw 'Static runbook verification failed before build freeze.' }
npm.cmd run build --prefix frontend
if ($LASTEXITCODE -ne 0) { throw 'Final frontend build failed.' }
docker build --target python-production --tag study-app-python:p6-candidate .
if ($LASTEXITCODE -ne 0) { throw 'Python production image build failed.' }
docker build --target frozen-node --tag study-app-node:p6-rollback .
if ($LASTEXITCODE -ne 0) { throw 'Frozen Node image build failed.' }
$p6BuildIdentityJson = & $p6Python -B -m backend.app.cli.compatibility freeze-identity --source-root . --compose-file docker-compose.yml --frontend-root frontend/dist --python-image study-app-python:p6-candidate --node-image study-app-node:p6-rollback --build-identity-directory $p6BuildIdentityRoot
$p6BuildIdentityExit = $LASTEXITCODE
if ($p6BuildIdentityExit -ne 0) { throw 'BuildIdentityManifest freeze failed.' }
$p6BuildIdentity = $p6BuildIdentityJson | ConvertFrom-Json
$p6BuildId = [string]$p6BuildIdentity.buildId
$p6BuildIdentityPath = (Resolve-Path -LiteralPath ([string]$p6BuildIdentity.manifestPath)).Path
$p6BuildIdentitySha256 = [string]$p6BuildIdentity.manifestFileSha256
if ($p6BuildId -notmatch '^[0-9a-f]{64}$' -or (Split-Path -Leaf $p6BuildIdentityPath) -ne ("frozen-build-identity-" + $p6BuildId + '.json') -or $p6BuildIdentitySha256 -notmatch '^[0-9a-f]{64}$' -or (Get-FileHash -Algorithm SHA256 -LiteralPath $p6BuildIdentityPath).Hash.ToLowerInvariant() -ne $p6BuildIdentitySha256) { throw 'Content-addressed BuildIdentityManifest result is invalid.' }
$env:P6_FROZEN_BUILD_IDENTITY_PATH = $p6BuildIdentityPath
$env:P6_FROZEN_BUILD_IDENTITY_SHA256 = $p6BuildIdentitySha256
~~~

Expected: static runbook validator先证明状态中立 owner说明与独立删除原则完整；`frozen-build-identity-<buildId>.json` 的 path、payload buildId和file SHA一致，绑定当前 gitRevision、dirty-aware sourceTreeHash、resolved compose、frontend files与两个不可变 image digest，且不含 database lineage/subject/backup field。同 content只读复验而不改 bytes/mtime；changed source/build产生新 path。`data/compatibility/runtime`是 freeze后唯一允许新增的状态目录且被 source hash明确排除。

- [ ] **Step 10（2–5 分钟）：冻结 final suite matrix，证明尚未生成任何 final record**

本步骤只验证 frozen identity、原子创建唯一 run-specific directory/manifest、冻结 key matrix并在 quiesce 前 arm durable cutover lease/watchdog；绝不运行 `capture-evidence --phase final`。任何 Node quiesce/cutover restore-check 之前产生的 final record 都是无效证据，必须 abort 当前窗口并换一个 fresh runId，不能覆盖、复制或复用：

~~~powershell
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$p6Python = (Resolve-Path -LiteralPath '.\.venv\Scripts\python.exe').Path
$p6BuildIdentityInput = [Environment]::GetEnvironmentVariable('P6_FROZEN_BUILD_IDENTITY_PATH', 'Process')
$p6BuildIdentitySha256 = [Environment]::GetEnvironmentVariable('P6_FROZEN_BUILD_IDENTITY_SHA256', 'Process')
if ([string]::IsNullOrWhiteSpace($p6BuildIdentityInput) -or $p6BuildIdentitySha256 -notmatch '^[0-9a-f]{64}$') { throw 'P6 exact content-addressed frozen build identity process snapshot is required.' }
$p6BuildIdentityPath = (Resolve-Path -LiteralPath $p6BuildIdentityInput).Path
if ((Get-FileHash -Algorithm SHA256 -LiteralPath $p6BuildIdentityPath).Hash.ToLowerInvariant() -ne $p6BuildIdentitySha256 -or (Split-Path -Leaf $p6BuildIdentityPath) -notmatch '^frozen-build-identity-[0-9a-f]{64}\.json$') { throw 'P6 frozen build identity path/SHA is invalid.' }
$p6LiveDatabaseIdentityPath = (Resolve-Path -LiteralPath 'data/compatibility/runtime/live-database-identity-v1.json').Path
$p6OwnerMarkerPath = (Resolve-Path -LiteralPath 'data/compatibility/runtime/production-owner.json').Path
$p6EvidenceRootItem = New-Item -ItemType Directory -Force -Path 'data/compatibility/evidence'
$p6EvidenceRoot = (Resolve-Path -LiteralPath $p6EvidenceRootItem.FullName).Path
$p6ExpectedFinalKeys = @(
  'build-identity-verify','bound-root-zero-skip','suite-isolation','backend-suite','legacy-python-suite','mcp-server-suite','node-suite',
  'frontend-vitest','frontend-typecheck','frontend-lint','frontend-build','frontend-e2e',
  'migration-head-ready','http-v2-ndjson-static','runtime-worker-scheduler-obsidian','mcp-credentials',
  'legacy-reconciliation','node-quiesce','cutover-backup-create','cutover-backup-verify',
  'cutover-backup-restore-check','live-pre-fingerprint','live-post-fingerprint','strict-readonly-compare',
  'convergence-gate','candidate-production-profile','candidate-write-smoke','explained-write-compare',
  'frozen-node-rollback','python-recovery','restore-install-rehearsal','final-enum-runbook','handoff-contract'
)
if (($p6ExpectedFinalKeys | Sort-Object -Unique).Count -ne $p6ExpectedFinalKeys.Count) { throw 'P6 final suite matrix contains duplicate keys.' }
& $p6Python -B -m backend.app.cli.compatibility verify-identity --build-identity-manifest $p6BuildIdentityPath
$p6PreWindowIdentityExit = $LASTEXITCODE
if ($p6PreWindowIdentityExit -ne 0) { throw "P6 frozen identity verification failed before the final window with exit code $p6PreWindowIdentityExit." }
$p6FinalRunId = [guid]::NewGuid().ToString('N')
$p6CreateRunArgs = @(
  '-B','-m','backend.app.cli.compatibility','create-evidence-run',
  '--evidence-root',$p6EvidenceRoot,
  '--run-id',$p6FinalRunId,
  '--phase','final',
  '--build-identity-manifest',$p6BuildIdentityPath,
  '--database-identity-manifest',$p6LiveDatabaseIdentityPath
)
foreach ($p6ExpectedFinalKey in $p6ExpectedFinalKeys) { $p6CreateRunArgs += @('--expected-key', $p6ExpectedFinalKey) }
$p6RunJson = & $p6Python @p6CreateRunArgs
$p6CreateRunExit = $LASTEXITCODE
if ($p6CreateRunExit -ne 0) { throw "P6 final evidence run creation failed with exit code $p6CreateRunExit." }
$p6Run = $p6RunJson | ConvertFrom-Json
foreach ($p6RunField in @('ok','runId','runDirectory','runManifestPath','runManifestFileSha256')) {
  if (-not ($p6Run.PSObject.Properties.Name -contains $p6RunField)) { throw "P6 final evidence run result omitted $p6RunField." }
}
$p6ExpectedRunDirectory = Join-Path $p6EvidenceRoot ("run-" + $p6FinalRunId)
$p6EvidenceDir = (Resolve-Path -LiteralPath ([string]$p6Run.runDirectory)).Path
$p6FinalRunManifestPath = (Resolve-Path -LiteralPath ([string]$p6Run.runManifestPath)).Path
$p6FinalRunManifestSha256 = [string]$p6Run.runManifestFileSha256
if (-not $p6Run.ok -or $p6Run.runId -ne $p6FinalRunId -or $p6EvidenceDir -ne (Resolve-Path -LiteralPath $p6ExpectedRunDirectory).Path -or $p6FinalRunManifestSha256 -notmatch '^[0-9a-f]{64}$') { throw 'P6 final evidence run identity/path is invalid.' }
if ((Get-FileHash -Algorithm SHA256 -LiteralPath $p6FinalRunManifestPath).Hash.ToLowerInvariant() -ne $p6FinalRunManifestSha256) { throw 'P6 final EvidenceRunManifest file SHA-256 mismatch.' }
$p6ExistingFinalRecords = @(Get-ChildItem -LiteralPath $p6EvidenceDir -Filter '*.capture.json' -File -ErrorAction Stop)
if ($p6ExistingFinalRecords.Count -ne 0) { throw 'Fresh P6 run unexpectedly contains pre-quiesce capture records.' }
$env:P6_FINAL_EVIDENCE_RUN_MANIFEST_PATH = $p6FinalRunManifestPath
$env:P6_FINAL_EVIDENCE_RUN_MANIFEST_SHA256 = $p6FinalRunManifestSha256
$env:P6_FINAL_EVIDENCE_RUN_ID = $p6FinalRunId
$p6FrozenRollbackMapPath = Join-Path $p6EvidenceDir 'frozen-node-rollback-map-v1.json'
& $p6Python -B -m backend.app.cli.compatibility export-frozen-node-rollback-map --build-identity-manifest $p6BuildIdentityPath --database-identity-manifest $p6LiveDatabaseIdentityPath --owner-marker $p6OwnerMarkerPath --rollback-profile frozen-node --output $p6FrozenRollbackMapPath
$p6RollbackMapExit = $LASTEXITCODE
if ($p6RollbackMapExit -ne 0) { throw "P6 frozen Node rollback map creation failed with exit code $p6RollbackMapExit." }
$p6StartupSnapshotPath = Join-Path $p6EvidenceDir 'production-startup-snapshot-v1.json'
$p6StartupJson = & $p6Python -B -m backend.app.cli.compatibility create-startup-snapshot --final-evidence-run-manifest $p6FinalRunManifestPath --expected-final-evidence-run-manifest-sha256 $p6FinalRunManifestSha256 --build-identity-manifest $p6BuildIdentityPath --database-identity-manifest $p6LiveDatabaseIdentityPath --frozen-node-rollback-map $p6FrozenRollbackMapPath --production-profile production --output $p6StartupSnapshotPath
$p6StartupExit = $LASTEXITCODE
if ($p6StartupExit -ne 0) { throw "P6 canonical production startup snapshot creation failed with exit code $p6StartupExit." }
$p6Startup = $p6StartupJson | ConvertFrom-Json
$p6StartupSnapshotSha256 = [string]$p6Startup.startupSnapshotFileSha256
if (-not $p6Startup.ok -or $p6Startup.startupSnapshotPath -ne $p6StartupSnapshotPath -or $p6StartupSnapshotSha256 -notmatch '^[0-9a-f]{64}$' -or (Get-FileHash -Algorithm SHA256 -LiteralPath $p6StartupSnapshotPath).Hash.ToLowerInvariant() -ne $p6StartupSnapshotSha256) { throw 'P6 canonical startup snapshot path/SHA is invalid.' }
$env:P6_PRODUCTION_STARTUP_SNAPSHOT_PATH = $p6StartupSnapshotPath
$env:P6_PRODUCTION_STARTUP_SNAPSHOT_SHA256 = $p6StartupSnapshotSha256
$p6CutoverLeaseOutput = Join-Path 'data/compatibility/runtime' ("final-window-" + $p6FinalRunId + '.json')
$p6CutoverTokenFileOutput = Join-Path 'data/compatibility/runtime' ("final-window-" + $p6FinalRunId + '.token')
$p6WindowJson = & $p6Python -B -m backend.app.cli.compatibility begin-final-window --final-evidence-run-manifest $p6FinalRunManifestPath --expected-final-evidence-run-manifest-sha256 $p6FinalRunManifestSha256 --startup-snapshot $p6StartupSnapshotPath --expected-startup-snapshot-sha256 $p6StartupSnapshotSha256 --build-identity-manifest $p6BuildIdentityPath --database-identity-manifest $p6LiveDatabaseIdentityPath --owner-marker $p6OwnerMarkerPath --runtime-namespace production --operator-pid $PID --rollback-profile frozen-node --heartbeat-timeout-seconds 120 --lease-output $p6CutoverLeaseOutput --token-file-output $p6CutoverTokenFileOutput
$p6WindowExit = $LASTEXITCODE
if ($p6WindowExit -ne 0) { throw "P6 final window arm failed with exit code $p6WindowExit; Node must still be active." }
$p6Window = $p6WindowJson | ConvertFrom-Json
if (-not $p6Window.ok -or -not $p6Window.watchdogReady -or $p6Window.runId -ne $p6FinalRunId) { throw 'P6 final window did not return a ready matching watchdog lease.' }
$p6CutoverLeasePath = (Resolve-Path -LiteralPath ([string]$p6Window.cutoverLeasePath)).Path
$p6CutoverTokenFile = (Resolve-Path -LiteralPath ([string]$p6Window.cutoverTokenFile)).Path
if ($p6CutoverLeasePath -ne (Resolve-Path -LiteralPath $p6CutoverLeaseOutput).Path -or $p6CutoverTokenFile -ne (Resolve-Path -LiteralPath $p6CutoverTokenFileOutput).Path) { throw 'P6 final window returned unexpected lease/token paths.' }
$env:P6_FINAL_WINDOW_LEASE_PATH = $p6CutoverLeasePath
$env:P6_FINAL_WINDOW_TOKEN_FILE = $p6CutoverTokenFile
function Invoke-P6FinalWindowAbort {
  param([Parameter(Mandatory = $true)][ValidateSet('step11_failure','step12_failure','step13_failure','step14_failure')][string]$ReasonCode)
  $p6AbortRecoveryPath = Join-Path $p6EvidenceDir 'abort-recovery.json'
  $p6AbortJson = & $p6Python -B -m backend.app.cli.compatibility abort-cutover --cutover-lease $p6CutoverLeasePath --cutover-token-file $p6CutoverTokenFile --final-evidence-run-manifest $p6FinalRunManifestPath --expected-final-evidence-run-manifest-sha256 $p6FinalRunManifestSha256 --startup-snapshot $p6StartupSnapshotPath --expected-startup-snapshot-sha256 $p6StartupSnapshotSha256 --build-identity-manifest $p6BuildIdentityPath --database-identity-manifest $p6LiveDatabaseIdentityPath --owner-marker $p6OwnerMarkerPath --reason-code $ReasonCode --recovery-output $p6AbortRecoveryPath
  $p6AbortExit = $LASTEXITCODE
  if ($p6AbortExit -ne 0) { throw "P6 abort-cutover failed with exit code $p6AbortExit; owner state must not be reported node_active without manual legacy smoke." }
  $p6Abort = $p6AbortJson | ConvertFrom-Json
  if (-not $p6Abort.ok -or $p6Abort.ownerState -ne 'node_active' -or -not $p6Abort.legacySmokePassed) { throw 'P6 abort-cutover did not prove frozen Node recovery.' }
  $p6Abort
}
~~~

Expected: matrix无重复、content-addressed frozen identity仍匹配；新 GUID run directory由 CLI独占创建，run manifest path/SHA/ID env精确一致。canonical startup snapshot与frozen rollback map在 run root O_EXCL创建，missing/extra/wrong字段先于副作用失败，CutoverLease同时绑定其 exact path/SHA；capture record总数为0，lease/token/watchdog在 quiesce前ready。失败或重试不得复用该 run directory、startup snapshot或authorization。

- [ ] **Step 11（按实际停机窗口）：先 quiesce，再创建 authoritative cutover backup 并采集 final convergence evidence**

Steps 11–15必须在 Step 10同一 operator process snapshot连续执行；每个 block复验 run/lease/startup/build/database capability。pre-quiesce backup只供 preflight，不得传 final write-smoke/rollback/recovery/restore rehearsal/gate/authorization。固定顺序：exact identities → quiesce/零资源 → run-root cutover create/verify/restore-check → machine-readable raw-zero/zero-skip suites（每 suite run-local DB/settings/PDF/Vault/Keyring与 Live deny tripwire）→ Live strict convergence。suite startedAt晚于 restore-check；唯一 cutover pair供后续。Live DB只 mode=ro/query_only；异常调用 same-token abort并按统一 recovery尾序恢复。

~~~powershell
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$p6Python = (Resolve-Path -LiteralPath '.\.venv\Scripts\python.exe').Path
$p6LiveDb = (Resolve-Path -LiteralPath 'data/app.db').Path
$p6BuildIdentityPath = (Resolve-Path -LiteralPath ([Environment]::GetEnvironmentVariable('P6_FROZEN_BUILD_IDENTITY_PATH', 'Process'))).Path
$p6BuildIdentitySha256 = [Environment]::GetEnvironmentVariable('P6_FROZEN_BUILD_IDENTITY_SHA256', 'Process')
$p6LiveDatabaseIdentityPath = (Resolve-Path -LiteralPath 'data/compatibility/runtime/live-database-identity-v1.json').Path
$p6OwnerMarkerPath = (Resolve-Path -LiteralPath 'data/compatibility/runtime/production-owner.json').Path
$p6FinalRunManifestPath = (Resolve-Path -LiteralPath ([Environment]::GetEnvironmentVariable('P6_FINAL_EVIDENCE_RUN_MANIFEST_PATH', 'Process'))).Path
$p6FinalRunManifestSha256 = [Environment]::GetEnvironmentVariable('P6_FINAL_EVIDENCE_RUN_MANIFEST_SHA256', 'Process')
$p6FinalRunId = [Environment]::GetEnvironmentVariable('P6_FINAL_EVIDENCE_RUN_ID', 'Process')
$p6CutoverLeasePath = (Resolve-Path -LiteralPath ([Environment]::GetEnvironmentVariable('P6_FINAL_WINDOW_LEASE_PATH', 'Process'))).Path
$p6CutoverTokenFile = (Resolve-Path -LiteralPath ([Environment]::GetEnvironmentVariable('P6_FINAL_WINDOW_TOKEN_FILE', 'Process'))).Path
$p6StartupSnapshotPath = (Resolve-Path -LiteralPath ([Environment]::GetEnvironmentVariable('P6_PRODUCTION_STARTUP_SNAPSHOT_PATH', 'Process'))).Path
$p6StartupSnapshotSha256 = [Environment]::GetEnvironmentVariable('P6_PRODUCTION_STARTUP_SNAPSHOT_SHA256', 'Process')
$p6EvidenceDir = (Split-Path -Parent $p6FinalRunManifestPath)
if ($p6FinalRunManifestSha256 -notmatch '^[0-9a-f]{64}$' -or $p6FinalRunId -notmatch '^[0-9a-f]{32}$' -or $p6BuildIdentitySha256 -notmatch '^[0-9a-f]{64}$' -or $p6StartupSnapshotSha256 -notmatch '^[0-9a-f]{64}$' -or (Split-Path -Leaf $p6EvidenceDir) -ne ("run-" + $p6FinalRunId)) { throw 'P6 final run immutable process snapshot is invalid.' }
if ((Get-FileHash -Algorithm SHA256 -LiteralPath $p6FinalRunManifestPath).Hash.ToLowerInvariant() -ne $p6FinalRunManifestSha256) { throw 'P6 final run manifest drifted before quiesce.' }
if ((Get-FileHash -Algorithm SHA256 -LiteralPath $p6BuildIdentityPath).Hash.ToLowerInvariant() -ne $p6BuildIdentitySha256 -or (Get-FileHash -Algorithm SHA256 -LiteralPath $p6StartupSnapshotPath).Hash.ToLowerInvariant() -ne $p6StartupSnapshotSha256 -or (Split-Path -Parent $p6StartupSnapshotPath) -ne $p6EvidenceDir) { throw 'P6 build/startup snapshot drifted before quiesce.' }
$p6FinalCaptureRunArgs = @('--run-manifest',$p6FinalRunManifestPath,'--expected-run-manifest-sha256',$p6FinalRunManifestSha256,'--cutover-lease',$p6CutoverLeasePath,'--cutover-token-file',$p6CutoverTokenFile,'--startup-snapshot',$p6StartupSnapshotPath,'--expected-startup-snapshot-sha256',$p6StartupSnapshotSha256)
$p6LiveDataRoot = (Resolve-Path -LiteralPath 'data').Path.TrimEnd([IO.Path]::DirectorySeparatorChar, [IO.Path]::AltDirectorySeparatorChar)
$p6LiveSettingsPath = [IO.Path]::GetFullPath((Join-Path $p6LiveDataRoot 'settings.json'))
$p6LivePdfRoot = [IO.Path]::GetFullPath((Join-Path $p6LiveDataRoot 'pdfs'))

function Invoke-P6FinalMachineSuite {
  param(
    [Parameter(Mandatory = $true)][string]$Key,
    [Parameter(Mandatory = $true)][ValidateSet('unittest','node-test','vitest','playwright','check')][string]$Adapter,
    [Parameter(Mandatory = $true)][string[]]$ChildArgv
  )
  $p6SuiteIsolationPath = Join-Path $p6EvidenceDir ($Key + '.isolation.json')
  & $p6Python -B -m backend.app.cli.compatibility create-suite-isolation --run-manifest $p6FinalRunManifestPath --expected-run-manifest-sha256 $p6FinalRunManifestSha256 --suite-key $Key --deny-live-database $p6LiveDb --deny-live-settings $p6LiveSettingsPath --deny-live-pdf-root $p6LivePdfRoot --deny-live-keyring 1 --deny-network 1 --output $p6SuiteIsolationPath
  $p6SuiteIsolationExit = $LASTEXITCODE
  if ($p6SuiteIsolationExit -ne 0) { throw "P6 final isolation creation for $Key failed with exit code $p6SuiteIsolationExit." }
  $p6SuiteSummaryPath = Join-Path $p6EvidenceDir ($Key + '.summary.json')
  & $p6Python -B -m backend.app.cli.compatibility capture-evidence --key $Key --phase final --result-kind machine-summary @p6FinalCaptureRunArgs --build-identity-manifest $p6BuildIdentityPath --summary-artifact $p6SuiteSummaryPath --isolation-manifest $p6SuiteIsolationPath --output (Join-Path $p6EvidenceDir ($Key + '.capture.json')) -- $p6Python -B -m backend.app.cli.machine_summary_runner --adapter $Adapter --summary-output $p6SuiteSummaryPath --isolation-manifest $p6SuiteIsolationPath -- @ChildArgv
  $p6SuiteCaptureExit = $LASTEXITCODE
  if ($p6SuiteCaptureExit -ne 0) { throw "P6 final machine suite $Key failed with exit code $p6SuiteCaptureExit." }
}

try {
$p6NodeQuiescePath = Join-Path $p6EvidenceDir 'node-quiesce.json'
$p6QuiesceJson = & $p6Python -B -m backend.app.cli.compatibility capture-evidence --key node-quiesce --phase final --result-kind json-cli @p6FinalCaptureRunArgs --build-identity-manifest $p6BuildIdentityPath --database-identity-manifest $p6LiveDatabaseIdentityPath --output (Join-Path $p6EvidenceDir 'node-quiesce.capture.json') --artifact ("node-quiesce=" + $p6NodeQuiescePath) -- $p6Python -B -m backend.app.cli.compatibility quiesce-live --final-evidence-run-manifest $p6FinalRunManifestPath --expected-final-evidence-run-manifest-sha256 $p6FinalRunManifestSha256 --startup-snapshot $p6StartupSnapshotPath --expected-startup-snapshot-sha256 $p6StartupSnapshotSha256 --cutover-lease $p6CutoverLeasePath --cutover-token-file $p6CutoverTokenFile --owner-marker $p6OwnerMarkerPath --build-identity-manifest $p6BuildIdentityPath --database-identity-manifest $p6LiveDatabaseIdentityPath --expected-database $p6LiveDb --evidence-output $p6NodeQuiescePath
$p6QuiesceExit = $LASTEXITCODE
if ($p6QuiesceExit -ne 0) { throw 'Live quiesce failed before cutover backup creation.' }
$p6Quiesce = $p6QuiesceJson | ConvertFrom-Json
if (-not $p6Quiesce.ok -or $p6Quiesce.ownerState -ne 'node_quiesced' -or -not $p6Quiesce.zeroPidPortDatabaseHandles) { throw 'Live Node did not enter the fully verified quiesced state.' }

$p6CutoverBackupDirItem = New-Item -ItemType Directory -Path (Join-Path $p6EvidenceDir 'cutover-backup')
$p6CutoverBackupDir = (Resolve-Path -LiteralPath $p6CutoverBackupDirItem.FullName).Path
$p6CutoverRestoreDirItem = New-Item -ItemType Directory -Path (Join-Path $p6EvidenceDir 'cutover-restore-check')
$p6CutoverRestoreDir = (Resolve-Path -LiteralPath $p6CutoverRestoreDirItem.FullName).Path
$p6CreateJson = & $p6Python -B -m backend.app.cli.compatibility capture-evidence --key cutover-backup-create --phase final --result-kind json-cli @p6FinalCaptureRunArgs --build-identity-manifest $p6BuildIdentityPath --database-identity-manifest $p6LiveDatabaseIdentityPath --output (Join-Path $p6EvidenceDir 'cutover-backup-create.capture.json') --artifact-from-json backupPath --artifact-from-json manifestPath -- $p6Python -B -m backend.app.cli.database_backup create --database $p6LiveDb --output-directory $p6CutoverBackupDir --label ("cutover-p6-final-" + $p6FinalRunId)
$p6CreateExit = $LASTEXITCODE
if ($p6CreateExit -ne 0) { throw 'Post-quiesce cutover backup create failed; restore frozen Node before retry.' }
$p6Cutover = $p6CreateJson | ConvertFrom-Json
if (-not $p6Cutover.ok) { throw 'Cutover backup JSON did not report success.' }
$p6CutoverBackupCompatibleLogicalSha256 = [string]$p6Cutover.logicalSha256
if ($p6CutoverBackupCompatibleLogicalSha256 -notmatch '^[0-9a-f]{64}$') { throw 'Cutover create omitted backup-compatible logical SHA-256.' }
$p6VerifyJson = & $p6Python -B -m backend.app.cli.compatibility capture-evidence --key cutover-backup-verify --phase final --result-kind json-cli @p6FinalCaptureRunArgs --build-identity-manifest $p6BuildIdentityPath --database-identity-manifest $p6LiveDatabaseIdentityPath --output (Join-Path $p6EvidenceDir 'cutover-backup-verify.capture.json') --artifact ("backup=" + $p6Cutover.backupPath) --artifact ("manifest=" + $p6Cutover.manifestPath) -- $p6Python -B -m backend.app.cli.database_backup verify --backup $p6Cutover.backupPath --manifest $p6Cutover.manifestPath
$p6VerifyExit = $LASTEXITCODE
if ($p6VerifyExit -ne 0) { throw 'Post-quiesce cutover backup verify failed; restore frozen Node before retry.' }
$p6CutoverVerify = $p6VerifyJson | ConvertFrom-Json
$p6CutoverVerifyBackupCompatibleLogicalSha256 = [string]$p6CutoverVerify.logicalSha256
if (-not $p6CutoverVerify.ok -or $p6CutoverVerifyBackupCompatibleLogicalSha256 -ne $p6CutoverBackupCompatibleLogicalSha256) { throw 'Cutover backup-compatible logical hash mismatch.' }
$p6RestoreJson = & $p6Python -B -m backend.app.cli.compatibility capture-evidence --key cutover-backup-restore-check --phase final --result-kind json-cli @p6FinalCaptureRunArgs --build-identity-manifest $p6BuildIdentityPath --database-identity-manifest $p6LiveDatabaseIdentityPath --output (Join-Path $p6EvidenceDir 'cutover-backup-restore-check.capture.json') --artifact-from-json restoredPath -- $p6Python -B -m backend.app.cli.database_backup restore-check --backup $p6Cutover.backupPath --manifest $p6Cutover.manifestPath --output-directory $p6CutoverRestoreDir
$p6RestoreExit = $LASTEXITCODE
if ($p6RestoreExit -ne 0) { throw 'Post-quiesce cutover backup restore-check failed; restore frozen Node before retry.' }
$p6CutoverRestore = $p6RestoreJson | ConvertFrom-Json
$p6CutoverRestoreBackupCompatibleLogicalSha256 = [string]$p6CutoverRestore.logicalSha256
if (-not $p6CutoverRestore.ok -or $p6CutoverRestoreBackupCompatibleLogicalSha256 -ne $p6CutoverVerifyBackupCompatibleLogicalSha256) { throw 'Cutover restore-check backup-compatible logical hash mismatch.' }

# Node 已 quiesced 且 authoritative cutover backup 已独立 verify/restore-check 后，才允许产生唯一 final suite records。
Invoke-P6FinalMachineSuite -Key 'bound-root-zero-skip' -Adapter 'unittest' -ChildArgv @($p6Python,'-B','-m','unittest','backend.tests.test_database_backup.DatabaseBackupTests.test_bound_root_windows_contract_runs_without_platform_skip','backend.tests.test_database_backup.DatabaseBackupTests.test_bound_root_posix_contract_runs_without_platform_skip','-v')
$p6FinalBoundRootExit = $LASTEXITCODE
if ($p6FinalBoundRootExit -ne 0) { throw "Captured deterministic BoundRoot suite failed with exit code $p6FinalBoundRootExit." }
Invoke-P6FinalMachineSuite -Key 'suite-isolation' -Adapter 'unittest' -ChildArgv @($p6Python,'-B','-m','unittest','backend.tests.test_suite_isolation','backend.tests.test_machine_summary','-v')
$p6FinalIsolationExit = $LASTEXITCODE
if ($p6FinalIsolationExit -ne 0) { throw "Captured suite-isolation contract failed with exit code $p6FinalIsolationExit." }
Invoke-P6FinalMachineSuite -Key 'backend-suite' -Adapter 'unittest' -ChildArgv @($p6Python,'-B','-m','unittest','discover','-s','backend/tests','-p','test_*.py','-v')
$p6FinalBackendExit = $LASTEXITCODE
if ($p6FinalBackendExit -ne 0) { throw "Captured final backend suite failed with exit code $p6FinalBackendExit." }
Invoke-P6FinalMachineSuite -Key 'legacy-python-suite' -Adapter 'unittest' -ChildArgv @($p6Python,'-B','-m','unittest','discover','-s','test','-p','test_*.py','-v')
$p6FinalLegacyPythonExit = $LASTEXITCODE
if ($p6FinalLegacyPythonExit -ne 0) { throw "Captured final legacy Python suite failed with exit code $p6FinalLegacyPythonExit." }
Invoke-P6FinalMachineSuite -Key 'mcp-server-suite' -Adapter 'unittest' -ChildArgv @($p6Python,'-B','-m','unittest','discover','-s','test','-p','test_mcp_server.py','-v')
$p6FinalMcpServerExit = $LASTEXITCODE
if ($p6FinalMcpServerExit -ne 0) { throw "Captured final MCP server suite failed with exit code $p6FinalMcpServerExit." }
Invoke-P6FinalMachineSuite -Key 'node-suite' -Adapter 'node-test' -ChildArgv @('npm.cmd','test')
$p6FinalNodeExit = $LASTEXITCODE
if ($p6FinalNodeExit -ne 0) { throw "Captured final Node suite failed with exit code $p6FinalNodeExit." }
Invoke-P6FinalMachineSuite -Key 'frontend-vitest' -Adapter 'vitest' -ChildArgv @('npm.cmd','run','test:run','--prefix','frontend')
$p6FinalVitestExit = $LASTEXITCODE
if ($p6FinalVitestExit -ne 0) { throw "Captured final frontend Vitest suite failed with exit code $p6FinalVitestExit." }
Invoke-P6FinalMachineSuite -Key 'frontend-typecheck' -Adapter 'check' -ChildArgv @('npm.cmd','run','typecheck','--prefix','frontend')
$p6FinalTypecheckExit = $LASTEXITCODE
if ($p6FinalTypecheckExit -ne 0) { throw "Captured final frontend typecheck failed with exit code $p6FinalTypecheckExit." }
Invoke-P6FinalMachineSuite -Key 'frontend-lint' -Adapter 'check' -ChildArgv @('npm.cmd','run','lint','--prefix','frontend')
$p6FinalLintExit = $LASTEXITCODE
if ($p6FinalLintExit -ne 0) { throw "Captured final frontend lint failed with exit code $p6FinalLintExit." }
Invoke-P6FinalMachineSuite -Key 'frontend-build' -Adapter 'check' -ChildArgv @('npm.cmd','run','build','--prefix','frontend')
$p6FinalBuildExit = $LASTEXITCODE
if ($p6FinalBuildExit -ne 0) { throw "Captured final frontend build failed with exit code $p6FinalBuildExit." }
Invoke-P6FinalMachineSuite -Key 'frontend-e2e' -Adapter 'playwright' -ChildArgv @('npm.cmd','run','e2e','--prefix','frontend')
$p6FinalE2eExit = $LASTEXITCODE
if ($p6FinalE2eExit -ne 0) { throw "Captured final frontend E2E failed with exit code $p6FinalE2eExit." }
Invoke-P6FinalMachineSuite -Key 'candidate-production-profile' -Adapter 'unittest' -ChildArgv @($p6Python,'-B','-m','unittest','backend.tests.test_compatibility_gate.CompatibilityGateTests.test_production_profile_has_no_node_runtime_and_keeps_frozen_rollback','-v')
$p6FinalProfileExit = $LASTEXITCODE
if ($p6FinalProfileExit -ne 0) { throw "Captured final production-profile contract failed with exit code $p6FinalProfileExit." }
Invoke-P6FinalMachineSuite -Key 'final-enum-runbook' -Adapter 'unittest' -ChildArgv @($p6Python,'-B','-m','unittest','backend.tests.test_compatibility_gate.CompatibilityGateTests.test_legacy_runtime_schema_and_fields_remain_present','backend.tests.test_compatibility_gate.CompatibilityGateTests.test_canonical_domain_enums_remain_exact','backend.tests.test_compatibility_gate.CompatibilityGateTests.test_static_runbook_is_state_neutral_and_preserves_deletion_boundary','-v')
$p6FinalEnumRunbookExit = $LASTEXITCODE
if ($p6FinalEnumRunbookExit -ne 0) { throw "Captured final enum/runbook contract failed with exit code $p6FinalEnumRunbookExit." }
Invoke-P6FinalMachineSuite -Key 'handoff-contract' -Adapter 'unittest' -ChildArgv @($p6Python,'-B','-m','unittest','backend.tests.test_runtime_ownership','backend.tests.test_production_rollback','-v')
$p6FinalHandoffExit = $LASTEXITCODE
if ($p6FinalHandoffExit -ne 0) { throw "Captured final handoff contract failed with exit code $p6FinalHandoffExit." }
& $p6Python -B -m backend.app.cli.compatibility capture-evidence --key build-identity-verify --phase final --result-kind json-cli @p6FinalCaptureRunArgs --build-identity-manifest $p6BuildIdentityPath --output (Join-Path $p6EvidenceDir 'build-identity-verify.capture.json') -- $p6Python -B -m backend.app.cli.compatibility verify-identity --build-identity-manifest $p6BuildIdentityPath
$p6FinalIdentityExit = $LASTEXITCODE
if ($p6FinalIdentityExit -ne 0) { throw "Captured final BuildIdentityManifest verification failed with exit code $p6FinalIdentityExit." }

$p6MigrationHeadJson = & $p6Python -B -m backend.app.cli.compatibility capture-evidence --key migration-head-ready --phase final --result-kind json-cli @p6FinalCaptureRunArgs --build-identity-manifest $p6BuildIdentityPath --database-identity-manifest $p6LiveDatabaseIdentityPath --output (Join-Path $p6EvidenceDir 'migration-head-ready.capture.json') -- $p6Python -B -m backend.app.cli.database_backup inspect --database $p6LiveDb
$p6MigrationHeadExit = $LASTEXITCODE
if ($p6MigrationHeadExit -ne 0) { throw "Captured exact-head readiness failed with exit code $p6MigrationHeadExit." }
$p6MigrationHead = $p6MigrationHeadJson | ConvertFrom-Json
if (-not $p6MigrationHead.ok -or $p6MigrationHead.database.alembicVersion -ne '20260807_03') { throw 'Final migration-head evidence is not exact revision 20260807_03.' }
$p6BackupCompatibleLogicalSha256 = [string]$p6MigrationHead.logicalSha256
if ($p6BackupCompatibleLogicalSha256 -notmatch '^[0-9a-f]{64}$' -or $p6BackupCompatibleLogicalSha256 -ne $p6CutoverRestoreBackupCompatibleLogicalSha256) { throw 'Quiesced Live database_backup inspect hash does not equal the authoritative cutover backup-compatible logical SHA-256.' }
$p6LivePrePath = Join-Path $p6EvidenceDir 'pre-convergence.json'
$p6LivePreJson = & $p6Python -B -m backend.app.cli.compatibility capture-evidence --key live-pre-fingerprint --phase final --result-kind json-cli @p6FinalCaptureRunArgs --build-identity-manifest $p6BuildIdentityPath --database-identity-manifest $p6LiveDatabaseIdentityPath --output (Join-Path $p6EvidenceDir 'live-pre-fingerprint.capture.json') --artifact ("live-pre=" + $p6LivePrePath) -- $p6Python -B -m backend.app.cli.compatibility fingerprint --database $p6LiveDb --database-identity-manifest $p6LiveDatabaseIdentityPath --subject-kind live --output $p6LivePrePath
$p6LivePreExit = $LASTEXITCODE
if ($p6LivePreExit -ne 0) { throw "Captured Live pre-convergence fingerprint failed with exit code $p6LivePreExit." }
$p6LivePre = $p6LivePreJson | ConvertFrom-Json
if (-not $p6LivePre.ok -or [string]$p6LivePre.canonicalDataSha256 -notmatch '^[0-9a-f]{64}$' -or ($p6LivePre.PSObject.Properties.Name -contains 'logicalSha256')) { throw 'P6 fingerprint must expose canonicalDataSha256 and must not expose logicalSha256.' }
$p6ReconciliationPath = Join-Path $p6EvidenceDir 'legacy-reconciliation-v1.json'
& $p6Python -B -m backend.app.cli.compatibility capture-evidence --key legacy-reconciliation --phase final --result-kind json-cli @p6FinalCaptureRunArgs --build-identity-manifest $p6BuildIdentityPath --database-identity-manifest $p6LiveDatabaseIdentityPath --output (Join-Path $p6EvidenceDir 'legacy-reconciliation.capture.json') --artifact ("reconciliation=" + $p6ReconciliationPath) -- $p6Python -B -m backend.app.cli.compatibility reconcile-legacy --database $p6LiveDb --database-identity-manifest $p6LiveDatabaseIdentityPath --output $p6ReconciliationPath
$p6ReconciliationExit = $LASTEXITCODE
if ($p6ReconciliationExit -ne 0) { throw "Captured Live legacy reconciliation failed with exit code $p6ReconciliationExit." }
Invoke-P6FinalMachineSuite -Key 'http-v2-ndjson-static' -Adapter 'unittest' -ChildArgv @($p6Python,'-B','-m','unittest','backend.tests.test_http_contract_inventory','backend.tests.test_api_legacy_json','backend.tests.test_api_ndjson','backend.tests.test_api_pdf_static','backend.tests.test_api_v2','-v')
$p6FinalHttpExit = $LASTEXITCODE
if ($p6FinalHttpExit -ne 0) { throw "Captured HTTP/v2/NDJSON/static suite failed with exit code $p6FinalHttpExit." }
Invoke-P6FinalMachineSuite -Key 'runtime-worker-scheduler-obsidian' -Adapter 'unittest' -ChildArgv @($p6Python,'-B','-m','unittest','backend.tests.test_runtime_ownership','backend.tests.test_obsidian_layout','backend.tests.test_obsidian_ownership','backend.tests.test_obsidian_pdf_modes','backend.tests.test_obsidian_jobs_api','backend.tests.test_obsidian_rebuild','-v')
$p6FinalRuntimeExit = $LASTEXITCODE
if ($p6FinalRuntimeExit -ne 0) { throw "Captured runtime/worker/scheduler/Obsidian suite failed with exit code $p6FinalRuntimeExit." }
Invoke-P6FinalMachineSuite -Key 'mcp-credentials' -Adapter 'unittest' -ChildArgv @($p6Python,'-B','-m','unittest','backend.tests.test_mcp_contract','backend.tests.test_mcp_readonly','backend.tests.test_mcp_shadow','backend.tests.test_credentials','-v')
$p6FinalMcpCredentialsExit = $LASTEXITCODE
if ($p6FinalMcpCredentialsExit -ne 0) { throw "Captured MCP/CredentialStore suite failed with exit code $p6FinalMcpCredentialsExit." }
$p6LivePostPath = Join-Path $p6EvidenceDir 'post-convergence.json'
& $p6Python -B -m backend.app.cli.compatibility capture-evidence --key live-post-fingerprint --phase final --result-kind json-cli @p6FinalCaptureRunArgs --build-identity-manifest $p6BuildIdentityPath --database-identity-manifest $p6LiveDatabaseIdentityPath --output (Join-Path $p6EvidenceDir 'live-post-fingerprint.capture.json') --artifact ("live-post=" + $p6LivePostPath) -- $p6Python -B -m backend.app.cli.compatibility fingerprint --database $p6LiveDb --database-identity-manifest $p6LiveDatabaseIdentityPath --subject-kind live --output $p6LivePostPath
$p6LivePostExit = $LASTEXITCODE
if ($p6LivePostExit -ne 0) { throw "Captured Live post-convergence fingerprint failed with exit code $p6LivePostExit." }
& $p6Python -B -m backend.app.cli.compatibility capture-evidence --key strict-readonly-compare --phase final --result-kind json-cli @p6FinalCaptureRunArgs --build-identity-manifest $p6BuildIdentityPath --database-identity-manifest $p6LiveDatabaseIdentityPath --output (Join-Path $p6EvidenceDir 'strict-readonly-compare.capture.json') -- $p6Python -B -m backend.app.cli.compatibility compare --mode strict-readonly --before (Join-Path $p6EvidenceDir 'pre-convergence.json') --after (Join-Path $p6EvidenceDir 'post-convergence.json')
$p6StrictCompareExit = $LASTEXITCODE
if ($p6StrictCompareExit -ne 0) { throw "Captured strict read-only compare failed with exit code $p6StrictCompareExit." }
$p6ConvergenceGateJson = & $p6Python -B -m backend.app.cli.compatibility capture-evidence --key convergence-gate --phase final --result-kind json-cli @p6FinalCaptureRunArgs --build-identity-manifest $p6BuildIdentityPath --database-identity-manifest $p6LiveDatabaseIdentityPath --output (Join-Path $p6EvidenceDir 'convergence-gate.capture.json') -- $p6Python -B -m backend.app.cli.compatibility gate --phase convergence --evidence-dir $p6EvidenceDir --final-evidence-run-manifest $p6FinalRunManifestPath --expected-final-evidence-run-manifest-sha256 $p6FinalRunManifestSha256 --startup-snapshot $p6StartupSnapshotPath --expected-startup-snapshot-sha256 $p6StartupSnapshotSha256 --cutover-lease $p6CutoverLeasePath --build-identity-manifest $p6BuildIdentityPath --database-identity-manifest $p6LiveDatabaseIdentityPath
$p6ConvergenceGateExit = $LASTEXITCODE
if ($p6ConvergenceGateExit -ne 0) { throw "Captured convergence gate failed with exit code $p6ConvergenceGateExit." }
$p6ConvergenceGate = $p6ConvergenceGateJson | ConvertFrom-Json
if (-not $p6ConvergenceGate.ok -or -not $p6ConvergenceGate.convergenceReady -or $p6ConvergenceGate.nodeShutdownAllowed) { throw 'Convergence gate returned an invalid pre-authorization state.' }
} catch {
  $p6Step11Failure = $_
  Invoke-P6FinalWindowAbort -ReasonCode 'step11_failure' | Out-Null
  throw $p6Step11Failure
}
~~~

Expected: `node-quiesce`先于全部 cutover records，`cutover-backup-restore-check`又先于所有唯一 final records。每条 suite由 machine-summary runner产生 run-local JSON/JUnit；wrapper从 artifact读取 totals/failures/skips，全部 raw exit 0、failures=0、skips=0，且每 suite拥有独立临时 DB/settings/PDF/Vault/Fake Keyring。isolation record列出 resolved roots、deny tripwire并证明 `liveAccessCount=0`、Provider/network调用为0；任何 console文本伪造、exit0+skip>0、Live path-open/sqlite-connect都失败并abort。cutover create/verify/restore-check与 quiesced Live只比较 **backupCompatibleLogicalSha256**；P6 strict pre/post只比较 `canonicalDataSha256`。所有 records绑定同一 final run/CutoverLease/startup snapshot/BuildIdentity，Live records绑定同一 DatabaseIdentity。任何 pre-quiesce record、duplicate、drift或命令失败均调用 token-bound abort，保持 non-active、清 authorization、drain/release、Node start、legacy smoke后最后恢复 `node_active`；failed run不可复用。

- [ ] **Step 12（按实际时长）：在 fresh verified descendant 上重跑 final explained write-smoke**

只以 Step 11 quiesce 后返回的 `$p6Cutover.backupPath/$p6Cutover.manifestPath` 创建新的 `subjectKind=write_smoke` restore copy，parent chain 指向 exact Live database manifest；运行固定 Fake provider/无用户 PDF 的 candidate smoke，并返回 descendant database manifest 与 final pre/post/delta：

~~~powershell
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
try {
if ([Environment]::GetEnvironmentVariable('P6_FINAL_EVIDENCE_RUN_ID', 'Process') -ne $p6FinalRunId -or [Environment]::GetEnvironmentVariable('P6_FINAL_EVIDENCE_RUN_MANIFEST_SHA256', 'Process') -ne $p6FinalRunManifestSha256) { throw 'P6 final run snapshot drifted before write-smoke.' }
$p6WriteSmokeRestoreRootItem = New-Item -ItemType Directory -Path (Join-Path $p6EvidenceDir 'write-smoke-descendants')
$p6WriteSmokeRestoreRoot = (Resolve-Path -LiteralPath $p6WriteSmokeRestoreRootItem.FullName).Path
$p6WriteSmokeIdentityOutput = Join-Path $p6EvidenceDir 'write-smoke-database-identity-v1.json'
$p6WriteSmokeJson = & $p6Python -B -m backend.app.cli.compatibility capture-evidence --key candidate-write-smoke --phase final --result-kind json-cli @p6FinalCaptureRunArgs --build-identity-manifest $p6BuildIdentityPath --database-identity-from-json descendantDatabaseIdentityManifestPath --output (Join-Path $p6EvidenceDir 'candidate-write-smoke.capture.json') --artifact-from-json beforePath --artifact-from-json afterPath --artifact-from-json deltaLedgerPath --artifact-from-json descendantDatabaseIdentityManifestPath -- $p6Python -B -m backend.app.cli.compatibility candidate-write-smoke --backup $p6Cutover.backupPath --manifest $p6Cutover.manifestPath --restore-root $p6WriteSmokeRestoreRoot --build-identity-manifest $p6BuildIdentityPath --parent-database-identity-manifest $p6LiveDatabaseIdentityPath --descendant-database-identity-output $p6WriteSmokeIdentityOutput --evidence-mode final --evidence-dir $p6EvidenceDir
$p6WriteSmokeExit = $LASTEXITCODE
if ($p6WriteSmokeExit -ne 0) { throw 'Captured final candidate write-smoke failed.' }
$p6WriteSmoke = $p6WriteSmokeJson | ConvertFrom-Json
if (-not $p6WriteSmoke.ok -or $p6WriteSmoke.descendantDatabaseIdentityManifestPath -ne $p6WriteSmokeIdentityOutput) { throw 'Final write-smoke did not return the requested descendant database identity.' }
$p6WriteSmokeDatabaseIdentityPath = (Resolve-Path -LiteralPath $p6WriteSmoke.descendantDatabaseIdentityManifestPath).Path
& $p6Python -B -m backend.app.cli.compatibility capture-evidence --key explained-write-compare --phase final --result-kind json-cli @p6FinalCaptureRunArgs --build-identity-manifest $p6BuildIdentityPath --database-identity-manifest $p6WriteSmokeDatabaseIdentityPath --output (Join-Path $p6EvidenceDir 'explained-write-compare.capture.json') -- $p6Python -B -m backend.app.cli.compatibility compare --mode explained-write --before $p6WriteSmoke.beforePath --after $p6WriteSmoke.afterPath --delta-ledger $p6WriteSmoke.deltaLedgerPath
$p6ExplainedWriteExit = $LASTEXITCODE
if ($p6ExplainedWriteExit -ne 0) { throw "Captured explained-write comparison failed with exit code $p6ExplainedWriteExit." }
} catch {
  $p6Step12Failure = $_
  Invoke-P6FinalWindowAbort -ReasonCode 'step12_failure' | Out-Null
  throw $p6Step12Failure
}
~~~

Expected: write-smoke pre/post 同一独立 subjectDatabaseId、与 Live 共享 lineage，且 parent chain 精确指向 Step 11 cutover backup/manifest；全部旧表 hash 不变，新/aux row delta 逐行可解释，Live DB 仍零写入。任何 Task 7/pre-quiesce backup path 或缺 descendant manifest 的结果都被拒绝。

- [ ] **Step 13（按实际时长）：在 frozen identity 下重跑 rollback、Python recovery 与 BoundRoot restore rehearsal final evidence**

~~~powershell
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
try {
if ([Environment]::GetEnvironmentVariable('P6_FINAL_EVIDENCE_RUN_ID', 'Process') -ne $p6FinalRunId -or [Environment]::GetEnvironmentVariable('P6_FINAL_EVIDENCE_RUN_MANIFEST_SHA256', 'Process') -ne $p6FinalRunManifestSha256) { throw 'P6 final run snapshot drifted before rollback/recovery/restore rehearsal.' }
$p6FrozenNodeRollbackPath = Join-Path $p6EvidenceDir 'frozen-node-rollback.json'
& $p6Python -B -m backend.app.cli.compatibility capture-evidence --key frozen-node-rollback --phase final --result-kind json-cli @p6FinalCaptureRunArgs --build-identity-manifest $p6BuildIdentityPath --database-identity-manifest $p6WriteSmokeDatabaseIdentityPath --output (Join-Path $p6EvidenceDir 'frozen-node-rollback.capture.json') --artifact ("frozen-node-rollback=" + $p6FrozenNodeRollbackPath) -- $p6Python -B -m backend.app.cli.compatibility rollback-smoke --database $p6WriteSmoke.restoredDatabasePath --build-identity-manifest $p6BuildIdentityPath --database-identity-manifest $p6WriteSmokeDatabaseIdentityPath --rollback-profile frozen-node --evidence-output $p6FrozenNodeRollbackPath
$p6FrozenNodeRollbackExit = $LASTEXITCODE
if ($p6FrozenNodeRollbackExit -ne 0) { throw "Captured frozen Node rollback smoke failed with exit code $p6FrozenNodeRollbackExit." }
$p6PythonRecoveryPath = Join-Path $p6EvidenceDir 'python-recovery.json'
& $p6Python -B -m backend.app.cli.compatibility capture-evidence --key python-recovery --phase final --result-kind json-cli @p6FinalCaptureRunArgs --build-identity-manifest $p6BuildIdentityPath --database-identity-manifest $p6WriteSmokeDatabaseIdentityPath --output (Join-Path $p6EvidenceDir 'python-recovery.capture.json') --artifact ("python-recovery=" + $p6PythonRecoveryPath) -- $p6Python -B -m backend.app.cli.compatibility recovery-smoke --database $p6WriteSmoke.restoredDatabasePath --build-identity-manifest $p6BuildIdentityPath --database-identity-manifest $p6WriteSmokeDatabaseIdentityPath --python-profile production --evidence-output $p6PythonRecoveryPath
$p6PythonRecoveryExit = $LASTEXITCODE
if ($p6PythonRecoveryExit -ne 0) { throw "Captured Python recovery smoke failed with exit code $p6PythonRecoveryExit." }
$p6InstallRootItem = New-Item -ItemType Directory -Path (Join-Path $p6EvidenceDir 'restore-install-rehearsal-checks')
$p6InstallRoot = (Resolve-Path -LiteralPath $p6InstallRootItem.FullName).Path.TrimEnd([IO.Path]::DirectorySeparatorChar, [IO.Path]::AltDirectorySeparatorChar)
$p6InstallRelativeTarget = ("install-validation-" + [guid]::NewGuid().ToString('N') + '/app.db')
$p6StageJson = & $p6Python -B -m backend.app.cli.compatibility prepare-restore-rehearsal-target --bound-root $p6InstallRoot --relative-target $p6InstallRelativeTarget --seed-database $p6CutoverRestore.restoredPath
$p6StageExit = $LASTEXITCODE
if ($p6StageExit -ne 0) { throw 'Final BoundRoot rehearsal target staging failed.' }
$p6Stage = $p6StageJson | ConvertFrom-Json
$p6InstallTarget = (Resolve-Path -LiteralPath ([string]$p6Stage.targetDatabasePath)).Path
$p6InstallPrefix = $p6InstallRoot + [IO.Path]::DirectorySeparatorChar
if (-not $p6InstallTarget.StartsWith($p6InstallPrefix, [StringComparison]::OrdinalIgnoreCase) -or $p6InstallTarget -eq $p6LiveDb) { throw 'Final restore-install-rehearsal target escaped the isolated root.' }
$p6TargetSha = [string]$p6Stage.targetSha256
if ($p6TargetSha -notmatch '^[0-9a-f]{64}$' -or (Get-FileHash -Algorithm SHA256 -LiteralPath $p6InstallTarget).Hash.ToLowerInvariant() -ne $p6TargetSha) { throw 'Final BoundRoot staged target SHA mismatch.' }
$p6InstalledIdentityOutput = Join-Path $p6EvidenceDir 'restore-install-rehearsal-database-identity-v1.json'
$p6RestoreInstallEvidencePath = Join-Path $p6EvidenceDir 'restore-install-rehearsal.json'
$p6RestoreInstallJson = & $p6Python -B -m backend.app.cli.compatibility capture-evidence --key restore-install-rehearsal --phase final --result-kind json-cli @p6FinalCaptureRunArgs --build-identity-manifest $p6BuildIdentityPath --database-identity-from-json installedDatabaseIdentityManifestPath --output (Join-Path $p6EvidenceDir 'restore-install-rehearsal.capture.json') --artifact-from-json installedDatabaseIdentityManifestPath --artifact ("restore-install-rehearsal=" + $p6RestoreInstallEvidencePath) -- $p6Python -B -m backend.app.cli.compatibility restore-install-rehearsal --backup $p6Cutover.backupPath --manifest $p6Cutover.manifestPath --target-database $p6InstallTarget --expected-target-sha256 $p6TargetSha --rehearsal-root $p6InstallRoot --build-identity-manifest $p6BuildIdentityPath --parent-database-identity-manifest $p6LiveDatabaseIdentityPath --installed-database-identity-output $p6InstalledIdentityOutput --evidence-output $p6RestoreInstallEvidencePath
$p6RestoreInstallExit = $LASTEXITCODE
if ($p6RestoreInstallExit -ne 0) { throw 'Captured restore-install-rehearsal failed.' }
$p6RestoreInstall = $p6RestoreInstallJson | ConvertFrom-Json
if (-not $p6RestoreInstall.ok -or $p6RestoreInstall.installedDatabaseIdentityManifestPath -ne $p6InstalledIdentityOutput) { throw 'Restore-install did not return the requested installed database identity.' }
} catch {
  $p6Step13Failure = $_
  Invoke-P6FinalWindowAbort -ReasonCode 'step13_failure' | Out-Null
  throw $p6Step13Failure
}
~~~

Expected: 三个 smoke/rehearsal child raw exit 0；每个隔离 subject有独立 DatabaseIdentity与以 Step 11 cutover backup为唯一 parent的完整 chain。BoundRoot持续持有目录对象，Windows no-delete-share/POSIX dirfd-openat-renameat-O_NOFOLLOW语义由 final contract覆盖，hostile swap tripwire与 Live path-open/sqlite-connect计数均为0。`restore-production-data`未被调用；frozen Node/Python recovery顺序、locks、ports、readiness可审计。

- [ ] **Step 14（2–5 分钟）：shutdown gate 生成短期、单次 promotion authorization**

~~~powershell
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
try {
if ([Environment]::GetEnvironmentVariable('P6_FINAL_EVIDENCE_RUN_ID', 'Process') -ne $p6FinalRunId -or [Environment]::GetEnvironmentVariable('P6_FINAL_EVIDENCE_RUN_MANIFEST_SHA256', 'Process') -ne $p6FinalRunManifestSha256) { throw 'P6 final run snapshot drifted before shutdown gate.' }
$p6AuthorizationPath = Join-Path $p6EvidenceDir 'promotion-authorization.json'
$p6GateJson = & $p6Python -B -m backend.app.cli.compatibility gate --phase shutdown --evidence-dir $p6EvidenceDir --final-evidence-run-manifest $p6FinalRunManifestPath --expected-final-evidence-run-manifest-sha256 $p6FinalRunManifestSha256 --startup-snapshot $p6StartupSnapshotPath --expected-startup-snapshot-sha256 $p6StartupSnapshotSha256 --cutover-lease $p6CutoverLeasePath --cutover-token-file $p6CutoverTokenFile --build-identity-manifest $p6BuildIdentityPath --database-identity-manifest $p6LiveDatabaseIdentityPath --cutover-backup $p6Cutover.backupPath --cutover-manifest $p6Cutover.manifestPath --authorization-output $p6AuthorizationPath --authorization-ttl-seconds 900
$p6GateExit = $LASTEXITCODE
if ($p6GateExit -ne 0) { throw 'Shutdown gate failed and did not authorize promotion.' }
$p6Gate = $p6GateJson | ConvertFrom-Json
if (-not $p6Gate.ok -or -not $p6Gate.nodeShutdownAllowed -or $p6Gate.authorizationPath -ne $p6AuthorizationPath) { throw 'Shutdown gate did not issue the requested authorization.' }
if ((Get-FileHash -Algorithm SHA256 -LiteralPath $p6AuthorizationPath).Hash.ToLowerInvariant() -ne $p6Gate.authorizationSha256) { throw 'Promotion authorization SHA-256 mismatch.' }
if ($p6Gate.runId -ne $p6FinalRunId -or $p6Gate.finalEvidenceRunManifestSha256 -ne $p6FinalRunManifestSha256 -or $p6Gate.cutoverLeasePath -ne $p6CutoverLeasePath -or $p6Gate.startupSnapshotSha256 -ne $p6StartupSnapshotSha256) { throw 'Promotion authorization is not bound to the exact final run/cutover lease/startup snapshot.' }
} catch {
  $p6Step14Failure = $_
  Invoke-P6FinalWindowAbort -ReasonCode 'step14_failure' | Out-Null
  throw $p6Step14Failure
}
~~~

Expected: gate在 exact EvidenceRunManifest/runId、CutoverLease/token、canonical startup snapshot、content-addressed BuildIdentity、Live DatabaseIdentity/OriginReceipt与完整 wrapper-produced final evidence上通过；authorization在同一 run root原子新建、TTL≤900秒，并绑定所有 path/SHA、Node quiesce evidence与唯一 post-quiesce cutover backup/Manifest。missing/extra/wrong startup字段、skip、pre-quiesce backup、cross-run identity或既有输出均 exit 2；catch恢复 frozen Node并seal run。unused/expired authorization由watchdog执行同一严格尾序。

- [ ] **Step 15（在授权 TTL 内）：执行唯一一次 production handoff并验证 smoke**

Production launcher 同时接受两个不可互换的 capability：Step 14 的短期单次 promotion authorization exact path/SHA，以及 Step 10 O_EXCL 创建的 canonical startup snapshot exact path/SHA。authorization 自身绑定 startup snapshot path/SHA；snapshot 固定 run/build/database/origin、roles 与 mode map，但不反向包含尚未创建的 authorization。下面是 launcher 的完整合并输入：前两项来自 authorization，startup/run/build/database 与 mode 项来自已验证 snapshot；operator 不得逐项重建、覆盖或把任一 path/SHA 换成另一份文件。

~~~text
RUNTIME_ENVIRONMENT=live
RUNTIME_NAMESPACE=production
PROMOTION_AUTHORIZATION_PATH=<Step 14 exact path>
PROMOTION_AUTHORIZATION_SHA256=<Step 14 exact SHA-256>
PRODUCTION_STARTUP_SNAPSHOT_PATH=<Step 10 exact path>
PRODUCTION_STARTUP_SNAPSHOT_SHA256=<Step 10 exact SHA-256>
P6_FINAL_EVIDENCE_RUN_ID=<Step 10 exact runId>
P6_FINAL_EVIDENCE_RUN_MANIFEST_PATH=<Step 10 exact path>
P6_FINAL_EVIDENCE_RUN_MANIFEST_SHA256=<Step 10 exact file SHA-256>
P6_FINAL_WINDOW_LEASE_PATH=<Step 10 exact durable lease path>
P6_FINAL_WINDOW_TOKEN_FILE=<Step 10 owner-only token file path>
BUILD_IDENTITY_MANIFEST_PATH=<frozen-build-identity-{buildId}.json exact path>
BUILD_IDENTITY_MANIFEST_SHA256=<Step 14 bound exact SHA-256>
DATABASE_IDENTITY_MANIFEST_PATH=<live-database-identity-v1.json exact path>
DATABASE_IDENTITY_MANIFEST_SHA256=<Step 14 bound exact SHA-256>
API_BACKEND_MODE=python
DOCUMENT_PIPELINE_MODE=p1
GENERATION_PIPELINE_MODE=p1
ARTIFACT_READ_MODE=prefer_new
ARTIFACT_WRITE_MODE=dual
OCR_ENABLED=0
OBSIDIAN_ENABLED=0
PAPER_STUDY_MCP_MODE=application
UI_ENTRY=react
~~~

调用以下精确命令，由 ProductionPromotionCoordinator 完成 marker handoff、启动 Python API/Worker/Scheduler/MCP、role locks 与 `/health/live`、`/health/ready`、`/api/papers`、`GET /api/v2/jobs`、`/workspace/`、`/legacy/`、MCP `tools/list` 九工具、read-only artifact fallback smoke：

~~~powershell
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$p6PromotionEvidencePath = Join-Path $p6EvidenceDir 'promotion-smoke.json'
$p6HandoffReceiptPath = Join-Path 'data/compatibility/runtime' ("handoff-receipt-" + $p6FinalRunId + '.json')
$p6PromotionJson = & $p6Python -B -m backend.app.cli.compatibility promote --authorization $p6Gate.authorizationPath --expected-authorization-sha256 $p6Gate.authorizationSha256 --final-evidence-run-manifest $p6FinalRunManifestPath --expected-final-evidence-run-manifest-sha256 $p6FinalRunManifestSha256 --cutover-lease $p6CutoverLeasePath --cutover-token-file $p6CutoverTokenFile --startup-snapshot $p6StartupSnapshotPath --expected-startup-snapshot-sha256 $p6StartupSnapshotSha256 --build-identity-manifest $p6BuildIdentityPath --database-identity-manifest $p6LiveDatabaseIdentityPath --owner-marker $p6OwnerMarkerPath --python-profile production --rollback-profile frozen-node --handoff-receipt-output $p6HandoffReceiptPath --evidence-output $p6PromotionEvidencePath
$p6PromotionExit = $LASTEXITCODE
if ($p6PromotionExit -ne 0) { throw 'Production handoff command failed; inspect automatic rollback evidence.' }
$p6Promotion = $p6PromotionJson | ConvertFrom-Json
if (-not $p6Promotion.ok -or $p6Promotion.ownerState -ne 'python_active' -or $p6Promotion.handoffReceiptPath -ne $p6HandoffReceiptPath -or [string]$p6Promotion.handoffReceiptSha256 -notmatch '^[0-9a-f]{64}$' -or (Get-FileHash -Algorithm SHA256 -LiteralPath $p6HandoffReceiptPath).Hash.ToLowerInvariant() -ne $p6Promotion.handoffReceiptSha256) { throw 'Production handoff or durable receipt verification failed; inspect automatic rollback evidence before retry.' }
~~~

成功时 `production-owner.json`记录 `python_active`并原子引用 exact HandoffReceipt path/SHA；receipt绑定 run/build/database/origin/startup/owner/locks/process/smoke identities，CutoverLease原子完成、token失效，常驻 ProductionOwnershipCoordinator开始监视 receipt/owner/role locks。receipt落盘失败也算 handoff失败并自动恢复 Node。begin前失败由 FinalWindowCoordinator abort；接管后失败由 PromotionCoordinator保持 non-active、清 authorization、drain Python、释放 locks、启动 frozen Node、legacy smoke后最后 CAS `node_active`。成功后需要应用回滚时，operator或新协调进程只调用 receipt-bound `rollback-production`；它可在任一 crash boundary续跑且不触碰SQLite内容。所有路径不 downgrade schema、不复用 authorization/failed run。

- [ ] **Step 16（2–5 分钟）：检查 frozen identity、repository 与真实数据边界**

~~~powershell
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
& $p6Python -B -m backend.app.cli.compatibility verify-identity --build-identity-manifest $p6BuildIdentityPath
if ($LASTEXITCODE -ne 0) { throw 'Final BuildIdentityManifest verification failed.' }
git diff --check
if ($LASTEXITCODE -ne 0) { throw 'Repository whitespace validation failed.' }
git status --short
if ($LASTEXITCODE -ne 0) { throw 'Repository status inspection failed.' }
~~~

Expected: source/build identity 自 Step 9 起未变化，无 whitespace error；data/app.db*、真实 backup、真实 Vault、secret、authorization、owner marker 和 shadow 正文日志均未进入版本控制。

- [ ] **Step 17（2–5 分钟）：验证静态 runbook 已冻结独立后续删除原则**

~~~powershell
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
& $p6Python -B -m backend.app.cli.compatibility verify-static-runbook --readme README.md --database-doc docs/DATABASE.md
$p6FinalRunbookExit = $LASTEXITCODE
if ($p6FinalRunbookExit -ne 0) { throw "Final static runbook verification failed with exit code $p6FinalRunbookExit." }
~~~

Expected: README 与 docs/DATABASE.md 自 freeze 起未变化且明确：P6 production 不依赖 Node 但随时可 runtime rollback；权威当前 owner 来自 gitignored runtime marker；删除 Node、旧 API、旧表/列、legacy fallback、legacy credential fields 或 obsidian ledger，以及调用 `finalize_legacy_migration`，必须另立版本化计划、正式关闭 Node rollback window、重新审计消费者、重新备份和重新演练。此步骤不得修改文件；任何意外失败先按 runtime rollback 恢复 frozen Node，再返回 Step 9 修正文档、重新冻结并重跑全部 final evidence。

## Node shutdown 硬门禁

- 旧 48 个 /api method/path、15 个 NDJSON、PDF/static、workspace/legacy 全绿；v2 精确包含 sources、explainer/translation/classification/metadata/summary、artifact GET、index/index-status、search/chunks、jobs read/action 和四条 Obsidian 路径，且不存在 generic create/cancel/export 替代路径；artifact/index camelCase source relation/mode contract 全绿。
- migration revision/hash、22 张必选 application table counts/PK/row hash、`processing_jobs` exact ordered columns、`processingJobs`/`processingJobSpecs` count/hash/strict decode、两个 spec guard + 三个 FTS trigger 的 exact name/normalized SQL SHA、FTS logical evidence、papersLegacyColumnsHash 与 translationsLegacyContentHash 可重复验证；trigger 总数必须精确为 5，旧列与旧字段完整保留。P6 strict pre/post 只比较 canonicalDataSha256。quiesced Live 与 cutover backup 等价只比较 `database_backup inspect.logicalSha256` 得到的 backupCompatibleLogicalSha256，两类 hash 不互换。
- legacy reconciliation ledger 完整覆盖每个非空 `papers.explainer` 与 `translations.content` 的 `(paperId,kind)`，逐内容与 aggregate hash 可重算，分类只含 `proven_migrated|legacy_only_unprovable|mismatch` 且 `mismatch=0`；只有可证明 ready SourceDocument relation 与相同内容 hash 的项计入 `proven_migrated`，不可证明项保持 legacy-only/null relation。notes/paper_vectors 仅证明保留；counts、完整 sets 与 hashes 都进入同 identity shutdown evidence，禁止把 `legacy_only_unprovable` 虚报为已迁移。
- Python Worker/Scheduler 单 owner、重启/重试/取消/drain 全绿；public job status 只有五值；Obsidian 单向/no-OCR/conflict/rebuild 用 `succeeded + result_json counts` 表达部分结果。
- MCP tools/list 恰为九个，input schema 不变；artifact `prefer_new`/legacy fallback、分页顺序、bounded mode-specific SourceDocument status、真正只读/zero-enqueue/zero-OCR、完整 shadow 窗口除 approved optional field 外零未解释差异，且可一键切 legacy。
- 四类 CredentialStore environment → Keyring → legacy priority、hasKey/keyTail/environmentManaged、blank-preserve、explicit clear、fixed/unsupported probes、redaction 全绿；四个 legacy fields 保留，P6 未调用 finalization。
- 所有 final capture records 位于同一 `run-<runId>`，引用同一 EvidenceRunManifest/CutoverLease/startup snapshot/content-addressed BuildIdentity；database records另引用 DatabaseIdentity/OriginReceipt。strict与write-smoke分开，跨 run copy或seal后补写拒绝。
- owner先 `node_active→node_quiesced`；唯一 cutover backup在 quiesce后 create/verify/restore-check。final write-smoke、rollback/recovery/restore-install-rehearsal与authorization只引用该 pair；pre-quiesce artifact不得进入 final gate。
- P6 suite全部由 machine-summary artifact驱动，raw exit 0/failures 0/skips 0；每 suite独立 sandbox并记录 `liveAccessCount=0`。console文本、skip、Live access、pre-cutover record或 provisional copy均阻止 shutdown。
- final window顺序固定 `fresh run → canonical startup/lease/watchdog → quiesce → cutover create/verify/restore-check → machine-readable zero suites → strict convergence → isolated write smoke → rollback/recovery/restore rehearsal → shutdown gate`。异常统一保持 non-active、清 authorization、drain/release、Node start、legacy smoke后最后 CAS active。
- shutdown authorization绑定 run/lease/startup/build/database/origin/cutover exact path+SHA。begin_handoff后才启动 Python；成功写 durable HandoffReceipt，重启后的 resident coordinator或 `rollback-production`严格复验 receipt并可幂等恢复。frozen Node、旧字段/表全部保留。

## 回滚边界

运行时回滚只切进程和 startup-only 模式：`RUNTIME_ENVIRONMENT=live`、`RUNTIME_NAMESPACE=production`、`PAPER_STUDY_MCP_MODE=legacy`、`API_BACKEND_MODE=legacy`、`DOCUMENT_PIPELINE_MODE=legacy`、`GENERATION_PIPELINE_MODE=legacy`、`ARTIFACT_READ_MODE=legacy`、`ARTIFACT_WRITE_MODE=legacy`、`OCR_ENABLED=0`、`OBSIDIAN_ENABLED=0`、`UI_ENTRY=react`。在 `armed|node_quiesced|authorization_issued` phase，FinalWindowCoordinator 不依赖 authorization，仅凭 exact run/lease/token/startup/build/database/origin identity执行 abort；在接管后但尚未形成成功 receipt 的 `handoff_pending` phase，由 ProductionPromotionCoordinator 或其 durable handoff lease恢复；成功进入 `python_active` 并生成 receipt 后，由常驻 ProductionOwnershipCoordinator或任意新进程调用 receipt-bound `rollback-production`。三条路径共享唯一尾序：owner 保持 `node_quiesced|handoff_pending` 非 active → 清 authorization → drain Python/停止新流量与 claim → 释放 locks/连接 → 按 canonical startup snapshot中的 frozen map启动 Node → legacy smoke → 最后 CAS `node_active`。start/smoke失败保持 non-active marker；不接受未定义 rollout value，不 downgrade或改写 Live DB。`UI_ENTRY=legacy` 仍是可选独立 UI-root rollback。

成功 handoff 后的独立 runbook 命令如下。operator 必须显式提供 owner marker当前引用的 exact receipt path/file SHA 到两个 process env；禁止 latest/glob、目录扫描或仅凭文件名选择 receipt。命令可在先前协调器已退出的新 PowerShell进程中运行；same receipt重试会读取同一 durable recovery lease并从最后完成事件继续：

~~~powershell
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$p6Python = (Resolve-Path -LiteralPath '.\.venv\Scripts\python.exe').Path
$p6ReceiptInput = [Environment]::GetEnvironmentVariable('P6_HANDOFF_RECEIPT_PATH', 'Process')
$p6ReceiptExpectedSha256 = [Environment]::GetEnvironmentVariable('P6_HANDOFF_RECEIPT_SHA256', 'Process')
if ([string]::IsNullOrWhiteSpace($p6ReceiptInput) -or $p6ReceiptExpectedSha256 -notmatch '^[0-9a-f]{64}$') { throw 'Exact handoff receipt path and SHA-256 process snapshot are required.' }
$p6HandoffReceiptPath = (Resolve-Path -LiteralPath $p6ReceiptInput).Path
if ((Get-FileHash -Algorithm SHA256 -LiteralPath $p6HandoffReceiptPath).Hash.ToLowerInvariant() -ne $p6ReceiptExpectedSha256) { throw 'Handoff receipt file SHA-256 mismatch.' }
$p6Receipt = Get-Content -Raw -Encoding utf8 -LiteralPath $p6HandoffReceiptPath | ConvertFrom-Json
foreach ($p6ReceiptField in @('receiptId','startupSnapshotPath','startupSnapshotSha256','buildIdentityManifestPath','databaseIdentityManifestPath','originReceiptPath','originReceiptFileSha256','ownerMarkerPath')) {
  if (-not ($p6Receipt.PSObject.Properties.Name -contains $p6ReceiptField) -or [string]::IsNullOrWhiteSpace([string]$p6Receipt.$p6ReceiptField)) { throw "Handoff receipt omitted $p6ReceiptField." }
}
if ([string]$p6Receipt.receiptId -notmatch '^[0-9a-f]{32}$' -or [string]$p6Receipt.startupSnapshotSha256 -notmatch '^[0-9a-f]{64}$' -or [string]$p6Receipt.originReceiptFileSha256 -notmatch '^[0-9a-f]{64}$') { throw 'Handoff receipt identifiers are malformed.' }
$p6StartupSnapshotPath = (Resolve-Path -LiteralPath ([string]$p6Receipt.startupSnapshotPath)).Path
$p6BuildIdentityPath = (Resolve-Path -LiteralPath ([string]$p6Receipt.buildIdentityManifestPath)).Path
$p6DatabaseIdentityPath = (Resolve-Path -LiteralPath ([string]$p6Receipt.databaseIdentityManifestPath)).Path
$p6OriginReceiptPath = (Resolve-Path -LiteralPath ([string]$p6Receipt.originReceiptPath)).Path
$p6OwnerMarkerPath = (Resolve-Path -LiteralPath ([string]$p6Receipt.ownerMarkerPath)).Path
if ((Get-FileHash -Algorithm SHA256 -LiteralPath $p6StartupSnapshotPath).Hash.ToLowerInvariant() -ne [string]$p6Receipt.startupSnapshotSha256) { throw 'Receipt-bound startup snapshot SHA-256 mismatch.' }
$p6RuntimeRootItem = Get-Item -LiteralPath 'data/compatibility/runtime'
$p6RuntimeRoot = (Resolve-Path -LiteralPath $p6RuntimeRootItem.FullName).Path
$p6RollbackLeasePath = Join-Path $p6RuntimeRoot ("production-rollback-" + [string]$p6Receipt.receiptId + '.lease.json')
$p6RollbackEvidencePath = Join-Path $p6RuntimeRoot ("production-rollback-" + [string]$p6Receipt.receiptId + '.json')
$p6RollbackJson = & $p6Python -B -m backend.app.cli.compatibility rollback-production --handoff-receipt $p6HandoffReceiptPath --expected-handoff-receipt-sha256 $p6ReceiptExpectedSha256 --startup-snapshot $p6StartupSnapshotPath --expected-startup-snapshot-sha256 ([string]$p6Receipt.startupSnapshotSha256) --build-identity-manifest $p6BuildIdentityPath --database-identity-manifest $p6DatabaseIdentityPath --p0-origin-receipt $p6OriginReceiptPath --expected-p0-origin-receipt-sha256 ([string]$p6Receipt.originReceiptFileSha256) --owner-marker $p6OwnerMarkerPath --recovery-lease-output $p6RollbackLeasePath --recovery-output $p6RollbackEvidencePath
$p6RollbackExit = $LASTEXITCODE
if ($p6RollbackExit -ne 0) { throw "Production application rollback failed with exit code $p6RollbackExit; owner must remain non-active until legacy smoke succeeds." }
$p6Rollback = $p6RollbackJson | ConvertFrom-Json
if (-not $p6Rollback.ok -or $p6Rollback.ownerState -ne 'node_active' -or -not $p6Rollback.legacySmokePassed -or $p6Rollback.handoffReceiptSha256 -ne $p6ReceiptExpectedSha256) { throw 'Production application rollback result is not bound to the exact receipt or did not prove legacy recovery.' }
~~~

Expected: CLI在任何 process/socket/DB/provider/lock副作用前复验 exact HandoffReceipt、ProductionStartupSnapshot、BuildIdentity、Live DatabaseIdentity、P0 OriginReceipt、owner marker与 role/process identities；从 `python_active` CAS到 `handoff_pending` 后逐事件持久化，清 authorization、drain/release、Node start、legacy smoke全绿后最后 CAS `node_active`。进程在任一事件崩溃后可由同一命令续跑；same receipt successful retry零副作用；不同 receipt或任一 identity drift fail closed。命令不调用 `restore-production-data`，不移动、恢复、downgrade或写入 SQLite 内容。

数据恢复是单独的破坏性运维，只能调用 `restore-production-data`：必须停止 Node、FastAPI、Worker、Scheduler、MCP 与 Obsidian projector，取得独立 recovery authorization与 full-writer-stop proof，在隔离路径验证 exact backup/Manifest、revision、integrity/FK/count/hash、`processingJobs`/`processingJobSpecs` 与五 trigger inventory，再保留当前 Live DB 为 hash-named recovery file后通过 P0 `BoundRoot` 原子安装。Windows 必须持续持有 no-delete-share root handle，POSIX 必须只用 dirfd/openat/renameat/O_NOFOLLOW；root/parent swap或能力不足在首次写前 fail closed。P0 restore-check、promotion authorization、HandoffReceipt 或 rehearsal flag本身都不足以授权替换 Live DB。
