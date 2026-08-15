# P5 Obsidian 单向投影实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Every production change uses superpowers:test-driven-development, and every completion claim uses superpowers:verification-before-completion. Steps use checkbox syntax and are sized for 2–5 minutes.

**Goal:** 将应用数据库中的 Paper、ready SourceDocument 与 ready GeneratedArtifact 确定性投影到用户指定 Obsidian Vault 的受管 Research 子树，支持单篇导出、批量同步、manifest、冲突保护和 PDF none|reference|copy，且绝不读取 Vault 回写数据库、绝不因导出隐式触发 OCR。

**Architecture:** <code>ObsidianExporter</code> 是深模块：Worker 只提交 paper/global projection request，它在内部固定 snapshot、模板、ownership、manifest、ledger、冲突与 summary 规则。它只读取 P4 <code>LibraryQueries</code>，通过单一 <code>BoundVaultRoot</code>/<code>VaultWriter</code> adapter 发布文件，并通过 P1 <code>VaultProjectionRepository</code> 复用既有九字段 <code>obsidian_exports</code>。P2 <code>ProcessingQueue</code> 仍是唯一 job seam；P5 只给 P2 versioned <code>spec_json</code> union 增加 <code>obsidian_export|obsidian_sync</code> variants并注册handler，不复制状态机或借用progress保存请求。单篇导出、全库同步、状态和写权限探测使用四条固定 v2 路由；八项非秘密 Obsidian 配置继续走 P4 <code>Settings</code> 与现有 <code>GET/POST /api/settings</code>。Vault 是可重建 projection，不是 canonical data。

**Tech Stack:** Python 3、pathlib（只做词法 DTO）、hashlib、json、ctypes/平台文件描述符 API、FastAPI、Pydantic v2、SQLAlchemy 2、SQLite WAL、unittest。

**Prerequisite gate:** P0–P4 全绿；P1 revision <code>20260807_01</code> 已创建 <code>obsidian_exports</code>，P2/P3 additive migrations 已完成且 Alembic 唯一 head 为 <code>20260807_03</code>；P2 <code>ProcessingQueue</code>/Worker 的 canonical <code>job_type</code> 已包含 <code>obsidian_export|obsidian_sync</code>，`processing_jobs.spec_json`、strict codec、spec-bound idempotency与两个guard triggers已通过；P3 <code>LibraryQueries</code> 可返回 ready SourceDocument/GeneratedArtifact；P4 的 exact Live `DatabaseEvidenceIdentityManifest`、`node_active` marker 与 fixed inventory CLI 已通过。P5 不新增 revision、table、column、job type、public job status 或第二套 queue；P5 前后 Alembic head 都必须是 <code>20260807_03</code>，并须在同一 verified descendant 上证明 12 legacy + 全部 P1/P2/P3/FTS/trigger inventory before/after 严格全等。

**Scope guardrails:** 默认 <code>enabled=false</code>、<code>auto_export=false</code>；只做 DB/application → Vault，不扫描 Vault 建库、不导入 Markdown、不建立 watcher。缺少 ready SourceDocument 时返回分类结果，不调用 <code>materialize_source</code>、不 enqueue OCR，真实 OCR provider 调用数必须为 0。Settings 保存只原子保存非秘密配置，绝不创建 Vault、迁移 PDF、enqueue job 或调用 CredentialStore secret getter。React 只允许 type/decoder/Gateway/Query Hook、fixture/test 与现有 Settings/PaperInspector 控件的最小接线；不新建或修改 CSS，不改布局、路由、GSAP/motion 或执行整页 reload。<code>pdfDir</code> 迁移只能由显式 operator CLI 的 dry-run → confirm 流执行。

---

## 文件职责

- backend/app/application/obsidian_projection.py：ObsidianExporter、ExportOptions、单篇/批量投影编排、确定性 projection plan。
- backend/app/application/library_queries.py：提供只读 Paper、ready SourceDocument、ready GeneratedArtifact、Note 与 PDF reference DTO；不为导出生成内容。
- backend/app/repositories/obsidian_exports.py：实现 P1 VaultProjectionRepository，读写 obsidian_exports 的九个固定字段。
- backend/app/infrastructure/bound_vault_root.py：Windows directory-handle/POSIX dirfd capability binding、true no-replace、identity-bound replace/delete与fsync；禁止path-only mutation。
- backend/app/providers/obsidian_vault.py：只通过`BoundVaultRoot`执行Vault路径验证、渲染文件发布、managed marker、manifest、hash与冲突编排。
- backend/app/providers/pdf_files.py：提供已验证 PDF reference/open-for-copy；不做 OCR。
- backend/app/workers/obsidian.py：obsidian_export 与 obsidian_sync ProcessingJob handler。
- backend/app/application/obsidian_auto_export.py：after-commit artifact-ready policy、每 Paper debounce、启动补偿与幂等 enqueue；绝不生成 source。
- backend/app/application/ports/obsidian_auto_export.py：<code>ObsidianAutoExportPort.on_artifact_ready</code> seam；production/no-op/fake adapter 共用一份 interface。
- backend/app/application/obsidian_pdf_migration.py：显式 pdfDir → Vault PDF plan/apply/recovery/rollback use case、MigrationIntent checkpoints 与 sealed receipt。
- backend/app/cli/obsidian_pdf_migration.py：显式 `pdfDir` → Vault `Attachments/PDF` plan/dry-run/apply/rollback；Settings 保存路径绝不调用它。
- backend/app/api/schemas/obsidian.py：ObsidianExportRequest、ObsidianSyncRequest、ObsidianStatusResponse 与 probe response；复用 P2 JobResponse，不复制 job schema。
- backend/app/api/routes/obsidian.py：只暴露 POST paper-scoped export、POST sync、GET status 与 POST test 四条明确路由。
- backend/app/api/router.py：挂载一次 Obsidian router；不建立第二套 v2 router/schema/error mapper。
- backend/app/application/settings.py：完整 Obsidian settings 的验证与原子保存；不创建目录、不搬 PDF。
- backend/tests/fixtures/obsidian/golden/：固定 Markdown、frontmatter 与 manifest golden files。
- backend/tests/test_obsidian_layout.py：paper-id 路径、ID 安全验证、渲染、排序与重复标题。
- backend/tests/test_bound_vault_root.py：平台capability、no-replace、root/parent/final identity race与path-open/file-write tripwire。
- backend/tests/test_obsidian_ownership.py：managed marker、冲突、BoundVaultRoot adapter、原子写与删除边界。
- backend/tests/test_obsidian_pdf_modes.py：none/reference/copy 与 no-OCR。
- backend/tests/test_obsidian_jobs_api.py：单篇/批量 jobs、幂等、部分失败和 v2 wire。
- backend/tests/test_obsidian_rebuild.py：空 Vault 全量重建与增量 hash 等价。
- backend/tests/test_obsidian_pdf_migration.py：plan/dry-run/conflict/atomic publish/crash recovery/rollback。
- backend/tests/test_obsidian_settings.py：八项设置、environment/file/default priority、CredentialStore preservation、save zero-side-effect 与 Vault missing/unwritable。
- backend/tests/test_obsidian_auto_export.py：after-commit、debounce、coalescing、restart reconciliation、幂等与 zero-OCR。
- backend/tests/test_obsidian_paper_delete.py：DB-only cascade、零 Vault I/O、manifest orphan/tombstone。
- frontend/src/lib/api/types.ts、decoders.ts、keys.ts、obsidianGateway.ts：Obsidian wire types、strict runtime decode、query keys 与四路由 Gateway。
- frontend/src/features/obsidian/useObsidianProjection.ts：status query 与 test/export/sync mutations；unmount 只 detach，不取消 server job。
- frontend/src/features/settings/settingsForm.ts、SettingsRoute.tsx：复用现有 Settings 控件连接八项配置、status/test/sync；不改 CSS/布局。
- frontend/src/features/dashboard/PaperInspector.tsx、DashboardRoute.tsx：在既有 inspector action 区加入单篇 Export；不改 route tree。
- frontend/src/test/fixtures/obsidian.ts：无真实路径、无秘密的 strict wire fixtures。
- docs/DATABASE.md：P5 配置、dry-run、冲突处理、清理预览与运行时回滚。

## 固定 Settings 契约

P5 只扩展 P4 <code>Settings</code>；P1 <code>CredentialStore</code> 继续管理 <code>llm|ocr|embedding|semantic_scholar</code> 四种 Credential。Obsidian 配置不是 Credential，不建立 Keyring service、第二个 JSON store 或 secret-returning DTO。Effective priority 固定为 environment → <code>data/settings.json</code> → default：

| Domain field | settings.json key | Environment | Default |
|---|---|---|---|
| <code>enabled</code> | <code>obsidianEnabled</code> | <code>OBSIDIAN_ENABLED</code> | <code>false</code> |
| <code>vault_path</code> | <code>obsidianVaultPath</code> | <code>OBSIDIAN_VAULT_PATH</code> | empty |
| <code>root_folder</code> | <code>obsidianRootFolder</code> | <code>OBSIDIAN_ROOT_FOLDER</code> | <code>Research</code> |
| <code>pdf_mode</code> | <code>obsidianPdfMode</code> | <code>OBSIDIAN_PDF_MODE</code> | <code>none</code> |
| <code>export_source</code> | <code>obsidianExportSource</code> | <code>OBSIDIAN_EXPORT_SOURCE</code> | <code>true</code> |
| <code>export_explainer</code> | <code>obsidianExportExplainer</code> | <code>OBSIDIAN_EXPORT_EXPLAINER</code> | <code>true</code> |
| <code>export_translation</code> | <code>obsidianExportTranslation</code> | <code>OBSIDIAN_EXPORT_TRANSLATION</code> | <code>true</code> |
| <code>auto_export</code> | <code>obsidianAutoExport</code> | <code>OBSIDIAN_AUTO_EXPORT</code> | <code>false</code> |

Environment booleans accept only <code>0|1</code>; JSON booleans accept only true boolean values. <code>pdf_mode</code> is exactly <code>none|reference|copy</code>. <code>vault_path</code> is empty or absolute; <code>root_folder</code> is a nonblank relative POSIX path whose segments are neither dot nor dot-dot and contain no drive, UNC prefix, control character or NUL. Saving disabled configuration may preserve a missing Vault path for later correction, but enabling/export/test validates existence and write capability at the use-case seam. The existing settings response/update uses camelCase names matching the JSON keys; status never returns the resolved absolute Vault path.

The same hash-guarded, per-file serialized atomic settings adapter used by P1/P4 preserves <code>apiKey</code>、<code>ocrApiKey</code>、<code>embedApiKey</code>、<code>s2ApiKey</code> and all unknown fields byte-for-byte. P5 does not call <code>finalize_legacy_migration</code>; P0–P6 Node rollback fields remain. A Settings POST never constructs <code>VaultWriter</code>, calls <code>ProcessingQueue</code>, touches <code>papers.pdf_path</code>, or invokes the PDF migration CLI/use case.

## 固定 Vault 布局

用户选择的 Vault 根下只有 Research 是本应用受管子树：

~~~text
Research/
  Papers/
    {paper_id}.md
  Sources/
    {paper_id}.md
  Explainers/
    {paper_id}.md
  Translations/
    {paper_id}.md
  Notes/
    {paper_id}.md
  Attachments/
    PDF/
      {paper_id}.pdf
  .paper-study/
    manifest.json
~~~

<code>{paper_id}</code> 必须是原始 <code>papers.id</code>，不做 title slug、不拼 artifact id、不因改名移动。<code>validate_paper_file_id</code> 要求 1–180 个 ASCII <code>A-Z a-z 0-9 . _ -</code> 字符、首字符为字母或数字、末尾不是点/空格，拒绝 dot/dot-dot、Windows device names、路径分隔符、冒号、控制字符、NUL 与大小写折叠冲突。非法 legacy ID 返回 <code>OBSIDIAN_PAPER_ID_UNSAFE</code>，零文件/manifest/ledger/job mutation；不得悄悄替换字符或改变 Paper identity。标题只出现在 frontmatter/body。所有 manifest path 使用相对 effective <code>root_folder</code> 的正斜杠路径；上图使用默认 <code>Research</code>。

Paper Markdown 的 body 顺序固定：title/metadata、<code>## Source</code>、<code>## Explainer</code>、<code>## Translation</code>、<code>## Notes</code>。存在且对应 export flag=true 时使用相对 Markdown link；不存在时分别写固定 placeholder <code>*Source unavailable.*</code>、<code>*Explainer unavailable.*</code>、<code>*Translation unavailable.*</code>，不创建生成任务。Tags 在 YAML 中逐项输出为安全 quoted list entry，去重后按 code-point 排序；title/titleZh/authors/aliases 均使用同一 YAML scalar encoder。

## 固定 managed 与冲突契约

- 应用管理的 Paper/Source/Explainer/Translation Markdown frontmatter 必须包含 <code>paper-study-managed: true</code>、paper id、kind、source hash；artifact 文件还包含 artifact id。Writer 先用 paper id + kind 解析固定目标，再验证 marker；title 改名只在相同路径更新 title/aliases/frontmatter/body，不 rename/move。
- PDF 无 frontmatter，其所有权只由 manifest 中 kind=pdf-copy、paperId、sourceHash、exportedHash 和相对路径证明。
- manifest 自身包含 schemaVersion=1、exporterVersion、generatedAt、entries；entry 含 path、kind、paperId、artifactId 或 null、ownership=managed|user、sourceHash、exportedHash；entries 按相对路径 code-point 顺序排序。
- source_hash 是 canonical application DTO 的 SHA-256；exported_hash 是发布后完整文件 bytes 的 SHA-256。
- 覆盖前若磁盘 hash 等于账本 exported_hash，允许原子替换；若不同，状态写 conflict、保留原文件且不更新 exported_hash。
- Notes 只允许在固定路径不存在且 DB note 非空时 exclusive seed 一次；seed marker 是 <code>paper-study-note-seed: true</code>、manifest ownership=<code>user</code>。文件一旦存在，无论由应用 seed 或用户预先创建，manual export、sync、auto-export、title update、cleanup 与 rebuild 都不得 overwrite、append、rename、move 或 delete；只报告 <code>user_managed</code>。绝不从 Vault 回写 DB。
- Stale managed file 只有 manifest ownership=managed、当前 profile 的 <code>obsidian_exports</code> row、Markdown marker/PDF hash 三方同时证明目标及当前 bytes 未修改时，才可进入显式 cleanup apply；缺任何证明、用户改动、Notes 或 orphan/tombstone 都保留并计 conflict。
- Paper delete 只由 P4 <code>PaperLibrary</code> 删除 DB row，SQLite cascade 可删 <code>obsidian_exports</code> ledger。Delete request 不构造/调用 Vault adapter、不读写 manifest、不删/移文件；原 manifest entry 原字节保留并在后续只读 status 中归类 orphan/tombstone。Sync/rebuild 必须 carry-forward 该 entry，自动 cleanup 永远不处理它。

## 固定 BoundVaultRoot 文件系统契约

任何 Vault write、exclusive seed/probe、temp create/cleanup、publish、replace、manifest merge、PDF copy 或 cleanup delete 都必须在一个已经成功绑定的 `BoundVaultRoot` 上执行；`VaultWriter`、PDF migration 和 test probe 不得直接调用 `Path.open/write_*`、builtin `open`、`tempfile.NamedTemporaryFile`、path-only `os.replace/rename/unlink`。只读 status 可以使用单独的 bounded reader，但一旦准备 mutation，就必须在首次 create/open-for-write 前完成 capability probe；平台缺少下述语义时以 `OBSIDIAN_ATOMIC_PRIMITIVE_UNAVAILABLE` fail closed，Vault/manifest/ledger/DB 写入均为 0。

```python
class BoundVaultRoot(Protocol):
    def ensure_directory(self, relative_path: VaultRelativePath) -> BoundDirectoryIdentity:
        """Create/bind each missing segment with mkdirat/native handle semantics."""

    def publish_new(self, relative_path: VaultRelativePath, data: bytes) -> PublishedFile:
        """Atomic true no-replace; an existing final path is never overwritten."""

    def replace_managed(
        self,
        relative_path: VaultRelativePath,
        data: bytes,
        expected: BoundTargetIdentity,
    ) -> PublishedFile:
        """Replace only the exact previously proved managed file identity."""

    def delete_managed(
        self,
        relative_path: VaultRelativePath,
        expected: BoundTargetIdentity,
    ) -> None:
        """Delete only the exact apply-time revalidated managed identity."""
```

平台绑定规则是硬门禁：

- Windows：用 native `CreateFileW` 以 `FILE_FLAG_BACKUP_SEMANTICS|FILE_FLAG_OPEN_REPARSE_POINT` 打开 Vault root、effective root_folder 与目标完整 ancestor chain；share mode 只含 `FILE_SHARE_READ|FILE_SHARE_WRITE`，明确不含 `FILE_SHARE_DELETE`。保存 `(volume serial,file id,file attributes,reparse tag)`，从第一个 child bind 起直到该次 temp cleanup/final publish/manifest/delete 全部完成持续持有全部 directory handles。每一级拒绝 reparse/junction/symlink，root/parent identity 在 final mutation 前后复验；managed target 另持平台 identity token，replacement/delete 必须使用经过审计的 native handle-bound primitive与恢复备份，不能退回 path-only `os.replace/unlink`。如果当前 Windows/Python build 无法提供 handle-bound no-replace、replace或delete中的任一所需能力，capability probe 在首次 write handle 前拒绝。
- POSIX：以 `O_RDONLY|O_DIRECTORY|O_CLOEXEC|O_NOFOLLOW` 绑定 Vault root，然后只用相对前一 `dirfd` 的 `openat` 逐级打开/创建目录；每级比较 `fstat` identity并拒绝symlink。temp使用parent dirfd上的`O_CREAT|O_EXCL|O_NOFOLLOW`；首次发布必须使用可证明 no-replace 的 `renameat2(RENAME_NOREPLACE)`、`renamex_np(RENAME_EXCL)` 或同文件系统 `linkat`+owned-temp unlink，绝不能用会覆盖的 rename。managed replace使用identity-preserving exchange/backup primitive并在删除旧bytes前验证被置换对象正是expected `(st_dev,st_ino)`；delete只对重新openat/fstat匹配的exact identity执行`unlinkat`。缺任一primitive时首次写前fail closed。
- 两个平台的目录创建也只能走bound `mkdirat`/native-handle child create并立即bind新目录；不允许`Path.mkdir/os.makedirs`。读取旧bytes/hash、写temp、publish前、publish后与cleanup前验证root/parent/target identity；错误码区分 `OBSIDIAN_ROOT_CHANGED|OBSIDIAN_PARENT_CHANGED|OBSIDIAN_TARGET_CHANGED|OBSIDIAN_TARGET_EXISTS`。错误和日志只返回相对路径与opaque identity，不泄露absolute Vault path。
- 首次发布包括 managed Markdown、Note seed、manifest bootstrap、probe与PDF copy，全部是真正 atomic no-replace；“先 exists 再 replace”不满足。managed replacement与显式delete都要求caller提供由manifest+ledger+marker/hash三证据得到、并在BoundVaultRoot内apply-time重验的`BoundTargetIdentity`。Notes与orphan永远拿不到delete/replace token。
- hostile tests 必须把 root/Research/任一 parent 在检查后替换为 Windows junction 或 POSIX symlink，交换 final target identity，并在最后 publish 窗口创建竞争文件；root外sentinel及竞争文件bytes必须不变。另安装path-open/file-write tripwire：除绑定adapter内白名单native syscall/dirfd操作外，任何`Path.mkdir/open/write_text/write_bytes/unlink/replace`、`os.makedirs`、builtin `open`、path-only `os.open/replace/rename/unlink`调用立即失败；该fixture同时断言Vault mutation为0且不会触碰真实Vault。

## 固定 ProcessingJob 与 route 契约

- Public status 继续只有 <code>queued|running|succeeded|failed|cancelled</code>。Conflict、user-managed、orphan 与 partial error 是 item/result 分类，不是 job status。
- Handler 完成遍历且任一非 error count 大于 0 时调用 P2 <code>complete</code>，terminal 为 <code>succeeded</code>；空 snapshot 的八项 count 全为 0 且没有错误时也必须 <code>succeeded</code>。<code>result_json</code> 固定含 exported、unchanged、conflicts、errors、skipped、userManaged、orphaned、deleted 八个非负整数。只有七个非 error count 全为 0 且 errors&gt;0 的 fatal run 才 <code>failed</code>。Progress/event 使用相同安全 counts，不含 Vault absolute path、Markdown、PDF bytes 或 secret。
- Four v2 paths exactly remain POST <code>/api/v2/papers/{paper_id}/exports/obsidian</code>、POST <code>/api/v2/obsidian/sync</code>、GET <code>/api/v2/obsidian/status</code>、POST <code>/api/v2/obsidian/test</code>。不得增加 generic Obsidian route、settings route 或 PDF migration HTTP route。
- Paper export requires paper_id and null source_mode; global sync uses null paper_id/source_mode。两者只写P2 canonical `spec_json`，`progress_json`仍只存八项counts/stage。`arguments`固定包含`dryRun`、`applyCleanup`、nullable`cleanupPlanSha`、`settingsFingerprint`、完整non-secret`settingsSnapshot`与content-safe`librarySnapshot`。Settings snapshot恰含normalized vaultPath、rootFolder、pdfMode和五个boolean export/auto flags；Library snapshot只含排序后的paper/source/artifact/note/PDF stable ids与content hashes，不含Markdown、Note正文、PDF bytes、API key、Credential/header或Provider数据。Worker重启后strict decode该spec，重新查询DB并验证snapshot hashes后执行；当前Settings/progress不得覆盖冻结参数。内部absolute vaultPath不得进入HTTP DTO/event/log/error。
- `obsidian_sync` 的canonical例子固定如下；`obsidian_export`只把顶层`paperId`改成目标id并要求`librarySnapshot.items`恰有同一paper。SHA示例是合法长度fixture，不代表生产常量：

```json
{
  "arguments": {
    "applyCleanup": false,
    "cleanupPlanSha": null,
    "dryRun": false,
    "librarySnapshot": {
      "items": [
        {
          "artifactHeads": [
            {
              "artifactId": "art_01",
              "contentSha256": "2222222222222222222222222222222222222222222222222222222222222222",
              "kind": "explainer"
            }
          ],
          "noteSha256": null,
          "paperId": "paper-1",
          "pdfSha256": "3333333333333333333333333333333333333333333333333333333333333333",
          "sourceContentSha256": "4444444444444444444444444444444444444444444444444444444444444444",
          "sourceDocumentId": "src_01"
        }
      ],
      "sha256": "5555555555555555555555555555555555555555555555555555555555555555"
    },
    "settingsFingerprint": "6666666666666666666666666666666666666666666666666666666666666666",
    "settingsSnapshot": {
      "autoExport": false,
      "enabled": true,
      "exportExplainer": true,
      "exportSource": true,
      "exportTranslation": true,
      "pdfMode": "copy",
      "rootFolder": "Research",
      "vaultPath": "C:\\TestVault"
    }
  },
  "jobType": "obsidian_sync",
  "paperId": null,
  "schemaVersion": 1,
  "sourceMode": null,
  "target": {"artifactId": null, "sourceDocumentId": null}
}
```
- Idempotency key 使用P2统一公式绑定raw `spec_sha256`；该spec SHA本身覆盖canonical job type、paper/global scope、effective settings fingerprint、requested cleanup/dry-run flags与immutable LibraryQueries snapshot。Concurrent manual/auto calls with the same desired snapshot return the same job；settings/snapshot/cleanup任一字段变化必须产生不同job。Claim、automatic retry与orphan recovery保持原spec bytes；explicit retry逐字节复制。无效或被篡改spec在任何Vault/ledger调用前以`JOB_SPEC_INVALID`fail closed。

## Task 0：在首个 P5 文件 mutation 前保护工作区并重放 P0–P4 入口门禁

**Files:**
- Verify only: Git worktree/index
- Verify only: data/compatibility/runtime/p0-origin-receipt-v1.json
- Verify only: data/compatibility/runtime/live-database-identity-v1.json
- Verify only: data/compatibility/runtime/production-owner.json
- Generate (ignored): fresh P5-entry backup、restore descendant、database identity 与 fixed inventory

- [ ] **Step 1（2–5 分钟）：记录并保护用户已有改动**

在创建/修改任何 P5 backend/frontend/test/doc 文件前运行。dirty tree 不是自动失败，但现有目标文件必须逐项识别并增量 patch；禁止 checkout/reset/clean/stash 或覆盖用户内容：

~~~powershell
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$p5EntryStatus = @(git status --porcelain=v1 --untracked-files=all)
if ($LASTEXITCODE -ne 0) { throw 'P5 entry git status failed.' }
$p5EntryDiffNames = @(git diff --name-only)
if ($LASTEXITCODE -ne 0) { throw 'P5 entry unstaged diff inventory failed.' }
$p5EntryCachedNames = @(git diff --cached --name-only)
if ($LASTEXITCODE -ne 0) { throw 'P5 entry staged diff inventory failed.' }
$p5EntryStatus | ForEach-Object { Write-Output ("P5_PREEXISTING_CHANGE " + $_) }
~~~

Expected: 三条 Git 命令 raw exit 0，所有 pre-existing changes 可见并归属清楚；本步骤零 Git 写操作。

- [ ] **Step 2（按实际时长）：只读复验 OriginReceipt、Live identity、Node owner、P0–P4 suites 与唯一 head**

以下 block 必须在 Task 1 Step 1 写首个测试前完整通过。它要求 P0 evidence 中 out-of-band receipt file SHA，重验 receipt 命名的 exact origin pair，以 P4 只读 Interface 证明 Node 仍为 Live owner，重跑 P0.1 verifier/P0–P4 suites，并拒绝任何 missing/multiple/non-P3 head：

~~~powershell
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
function Invoke-P5EntryCheckedNative {
  param([Parameter(Mandatory = $true)][string]$Label, [Parameter(Mandatory = $true)][scriptblock]$Command)
  $output = & $Command
  $exitCode = $LASTEXITCODE
  if ($exitCode -ne 0) { throw "$Label failed with exit code $exitCode." }
  $output
}
$p5Python = (Resolve-Path -LiteralPath '.\.venv\Scripts\python.exe').Path
$p5EntryReceiptPath = (Resolve-Path -LiteralPath 'data/compatibility/runtime/p0-origin-receipt-v1.json').Path
$p5EntryReceiptFileSha256 = [Environment]::GetEnvironmentVariable('P0_ORIGIN_RECEIPT_SHA256', 'Process')
if ([string]::IsNullOrWhiteSpace($p5EntryReceiptFileSha256) -or $p5EntryReceiptFileSha256 -notmatch '^[0-9a-f]{64}$') { throw 'P5 entry requires the exact lowercase P0 OriginReceipt file SHA-256.' }
$p5EntryReceiptJson = Invoke-P5EntryCheckedNative 'P5 entry OriginReceipt verification' { & $p5Python -B -m backend.app.cli.database_backup verify-origin-receipt --receipt $p5EntryReceiptPath --expected-receipt-file-sha256 $p5EntryReceiptFileSha256 }
$p5EntryReceipt = $p5EntryReceiptJson | ConvertFrom-Json
foreach ($field in @('ok','backupPath','manifestPath','databaseLineageId')) {
  if (-not ($p5EntryReceipt.PSObject.Properties.Name -contains $field)) { throw "P5 entry OriginReceipt verification omitted $field." }
}
if ($p5EntryReceipt.ok -isnot [bool] -or -not $p5EntryReceipt.ok) { throw 'P5 entry OriginReceipt verification did not return boolean ok=true.' }
$p5EntryOriginBackup = (Resolve-Path -LiteralPath ([string]$p5EntryReceipt.backupPath)).Path
$p5EntryOriginManifest = (Resolve-Path -LiteralPath ([string]$p5EntryReceipt.manifestPath)).Path
$p5LiveDb = (Resolve-Path -LiteralPath 'data/app.db').Path
$p5LiveIdentityPath = (Resolve-Path -LiteralPath 'data/compatibility/runtime/live-database-identity-v1.json').Path
$p5OwnerMarkerPath = (Resolve-Path -LiteralPath 'data/compatibility/runtime/production-owner.json').Path
$p5EntrypointPath = (Resolve-Path -LiteralPath 'server.js').Path
$p5OwnerJson = Invoke-P5EntryCheckedNative 'P5 entry read-only Node owner verification' { & $p5Python -B -m backend.app.cli.runtime_owner verify-node-owner --database-identity-manifest $p5LiveIdentityPath --p0-origin-receipt $p5EntryReceiptPath --expected-p0-origin-receipt-sha256 $p5EntryReceiptFileSha256 --origin-backup $p5EntryOriginBackup --origin-manifest $p5EntryOriginManifest --runtime-namespace production --expected-entrypoint-path $p5EntrypointPath --owner-marker $p5OwnerMarkerPath }
$p5Owner = $p5OwnerJson | ConvertFrom-Json
if (-not $p5Owner.ok -or $p5Owner.verificationMode -ne 'read_only' -or $p5Owner.ownerState -ne 'node_active') { throw 'P5 entry did not prove the exact Node owner in read-only mode.' }
Invoke-P5EntryCheckedNative 'P5 entry backend P0-P4 regression' { & $p5Python -B -m unittest discover -s backend/tests -p 'test_*.py' -v }
Invoke-P5EntryCheckedNative 'P5 entry legacy Python regression' { & $p5Python -B -m unittest discover -s test -p 'test_*.py' -v }
Invoke-P5EntryCheckedNative 'P5 entry Node regression' { npm.cmd test }
$p5EntryBaselineJson = Invoke-P5EntryCheckedNative 'P5 entry exact frontend baseline verification' { node scripts/pre-existing-failure-baseline.mjs verify --baseline contracts/pre-existing-test-failures-v1.json }
$p5EntryBaseline = $p5EntryBaselineJson | ConvertFrom-Json
foreach ($field in @('baselineMatched','observedSuiteExitCode','overallGreen')) {
  if (-not ($p5EntryBaseline.PSObject.Properties.Name -contains $field)) { throw "P5 entry baseline verifier omitted $field." }
}
if ($p5EntryBaseline.baselineMatched -isnot [bool] -or -not $p5EntryBaseline.baselineMatched) { throw 'P5 entry baselineMatched must be boolean true.' }
if ($p5EntryBaseline.observedSuiteExitCode -isnot [int] -and $p5EntryBaseline.observedSuiteExitCode -isnot [long]) { throw 'P5 entry observedSuiteExitCode must be an integer.' }
if ($p5EntryBaseline.overallGreen -isnot [bool] -or (($p5EntryBaseline.observedSuiteExitCode -eq 0) -ne $p5EntryBaseline.overallGreen)) { throw 'P5 entry baseline authorization fields are semantically inconsistent.' }
$p5EntryHeadsRaw = @(Invoke-P5EntryCheckedNative 'P5 entry Alembic heads' { & $p5Python -B -m alembic -c backend/alembic.ini heads })
$p5EntryHeads = @($p5EntryHeadsRaw | ForEach-Object { ([string]$_).Trim() } | Where-Object { $_ -ne '' })
if ($p5EntryHeads.Count -ne 1 -or $p5EntryHeads[0] -ne '20260807_03 (head)') { throw "P5 entry requires exactly 20260807_03 (head); observed: $($p5EntryHeads -join ' | ')." }
$p5LiveInspectJson = Invoke-P5EntryCheckedNative 'P5 entry Live revision inspection' { & $p5Python -B -m backend.app.cli.database_backup inspect --database $p5LiveDb }
$p5LiveInspect = $p5LiveInspectJson | ConvertFrom-Json
if (-not $p5LiveInspect.ok -or $p5LiveInspect.database.alembicVersion -ne '20260807_03') { throw 'P5 entry Live database is not exact revision 20260807_03.' }
~~~

Expected: exact P0 receipt/origin、P4 Live DatabaseEvidenceIdentityManifest、resolved `server.js` Node owner 与 P0–P4 regressions 全部通过；owner verifier 对 marker/DB bytes/mtime 零写入，Alembic graph/Live current 都恰为唯一 `20260807_03`。已审核 frontend non-zero 继续原样报告 `overallGreen=false`，不能称为全绿。

- [ ] **Step 3（按实际时长）：在 fresh verified descendant 上证明 P4 fixed inventory 完整**

本步骤只创建 ignored rehearsal artifacts；Live Node 保持 active，Live DB 零写入。fresh backup 必须独立 verify/restore-check，descendant identity 必须沿用 exact Live parent 与 canonical `--parent-database-identity-manifest`，随后 fixed inventory capture 自身验证 12 legacy、全部 P1/P2/P3/FTS 与三个 exact trigger：

~~~powershell
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$p5EntryCreateJson = & $p5Python -B -m backend.app.cli.database_backup create --database $p5LiveDb --output-directory data/backups --label pre-p5-entry
$p5EntryCreateExit = $LASTEXITCODE
if ($p5EntryCreateExit -ne 0) { throw "P5 entry backup create failed with exit code $p5EntryCreateExit." }
$p5EntryCreate = $p5EntryCreateJson | ConvertFrom-Json
if (-not $p5EntryCreate.ok) { throw 'P5 entry backup create did not return ok=true.' }
$p5EntryVerifyJson = & $p5Python -B -m backend.app.cli.database_backup verify --backup $p5EntryCreate.backupPath --manifest $p5EntryCreate.manifestPath
$p5EntryVerifyExit = $LASTEXITCODE
if ($p5EntryVerifyExit -ne 0) { throw "P5 entry backup verify failed with exit code $p5EntryVerifyExit." }
$p5EntryVerify = $p5EntryVerifyJson | ConvertFrom-Json
$p5EntryRestoreJson = & $p5Python -B -m backend.app.cli.database_backup restore-check --backup $p5EntryCreate.backupPath --manifest $p5EntryCreate.manifestPath --output-directory data/backups/restore-checks
$p5EntryRestoreExit = $LASTEXITCODE
if ($p5EntryRestoreExit -ne 0) { throw "P5 entry restore-check failed with exit code $p5EntryRestoreExit." }
$p5EntryRestore = $p5EntryRestoreJson | ConvertFrom-Json
if (-not $p5EntryVerify.ok -or -not $p5EntryRestore.ok -or $p5EntryVerify.logicalSha256 -ne $p5EntryCreate.logicalSha256 -or $p5EntryRestore.logicalSha256 -ne $p5EntryCreate.logicalSha256) { throw 'P5 entry backup verify/restore-check logical evidence mismatch.' }
$p5EntryDrillDb = (Resolve-Path -LiteralPath $p5EntryRestore.restoredPath).Path
if ($p5EntryDrillDb -eq $p5LiveDb) { throw 'P5 entry descendant resolves to Live DB.' }
$p5EntryEvidenceDir = New-Item -ItemType Directory -Path (Join-Path 'data/compatibility/preflight' ('p5-entry-' + [guid]::NewGuid().ToString('N')))
$p5EntryDescendantIdentityPath = Join-Path $p5EntryEvidenceDir.FullName 'database-identity-v1.json'
$p5EntryDescendantJson = & $p5Python -B -m backend.app.cli.runtime_owner create-descendant-database-identity --database $p5EntryDrillDb --subject-kind p5_entry --parent-database-identity-manifest $p5LiveIdentityPath --parent-backup $p5EntryCreate.backupPath --parent-manifest $p5EntryCreate.manifestPath --output $p5EntryDescendantIdentityPath
$p5EntryDescendantExit = $LASTEXITCODE
if ($p5EntryDescendantExit -ne 0) { throw "P5 entry descendant identity failed with exit code $p5EntryDescendantExit." }
$p5EntryInventoryPath = Join-Path $p5EntryEvidenceDir.FullName 'fixed-inventory.json'
& $p5Python -B -m backend.app.cli.schema_inventory capture --database $p5EntryDrillDb --database-identity-manifest $p5EntryDescendantIdentityPath --output $p5EntryInventoryPath
$p5EntryInventoryExit = $LASTEXITCODE
if ($p5EntryInventoryExit -ne 0) { throw "P5 entry fixed inventory failed with exit code $p5EntryInventoryExit." }
$p5EntryDescendantInspectJson = & $p5Python -B -m backend.app.cli.database_backup inspect --database $p5EntryDrillDb
$p5EntryDescendantInspectExit = $LASTEXITCODE
if ($p5EntryDescendantInspectExit -ne 0) { throw "P5 entry descendant inspection failed with exit code $p5EntryDescendantInspectExit." }
$p5EntryDescendantInspect = $p5EntryDescendantInspectJson | ConvertFrom-Json
if (-not $p5EntryDescendantInspect.ok -or $p5EntryDescendantInspect.database.alembicVersion -ne '20260807_03') { throw 'P5 entry descendant is not exact revision 20260807_03.' }
~~~

Expected: fresh descendant 与 Live 共享 databaseLineageId、具有独立 subjectDatabaseId，revision 恰为 `20260807_03`；fixed inventory 精确包含 `document_chunks_fts_ai|document_chunks_fts_ad|document_chunks_fts_au` 及各自 normalized SQL hash/insert-delete-update behavior oracle，零 missing/extra/lookalike object。任一失败时尚未发生 P5 source/test/frontend/doc mutation。

## Task 1：锁定配置、schema 依赖与默认关闭

**Files:**
- Create: backend/tests/test_obsidian_jobs_api.py
- Create: backend/tests/test_obsidian_settings.py
- Modify: backend/tests/test_credentials.py
- Modify: backend/app/application/settings.py
- Modify: lib/backend-rollout.js
- Modify: test/backend-rollout.test.js
- Modify: backend/app/rollout.py
- Modify: backend/tests/test_rollout_defaults.py
- Create: backend/app/api/schemas/obsidian.py
- Create: backend/app/api/routes/obsidian.py
- Modify: backend/app/api/router.py
- Modify: backend/app/api/errors.py
- Verify only: backend/migrations/versions/20260807_01_domain_data_foundation.py
- Verify only: backend/migrations/versions/20260807_02_processing_queue_ocr.py
- Verify only: backend/migrations/versions/20260807_03_source_consumers_search.py

- [ ] **Step 1（2–5 分钟）：写默认关闭红测**

新增 ObsidianJobsApiTests.test_obsidian_is_disabled_by_default，断言未设置 OBSIDIAN_ENABLED 时 GET /api/v2/obsidian/status 返回 enabled=false；POST /api/v2/papers/paper-1/exports/obsidian、POST /api/v2/obsidian/sync 与 POST /api/v2/obsidian/test 均返回 409 OBSIDIAN_DISABLED，processing_jobs 与 obsidian_exports count 均不变。

- [ ] **Step 2（2–5 分钟）：确认红测**

Run:

~~~powershell
.\.venv\Scripts\python.exe -B -m unittest backend.tests.test_obsidian_jobs_api.ObsidianJobsApiTests.test_obsidian_is_disabled_by_default -v
~~~

Expected RED: /api/v2/obsidian/status 返回 404；数据库 fixture 本身可正常查询两张表。

- [ ] **Step 3（2–5 分钟）：验证现有 migration 精确表契约**

新增 test_obsidian_exports_schema_matches_p1_contract，断言表只有计划允许的业务列 id、paper_id、artifact_id、target_path、source_hash、exported_hash、status、exported_at、error_message，并验证外键/唯一索引；不得创建 obsidian_projection、vault_exports 等替代表。

- [ ] **Step 4（2–5 分钟）：实现完整八项 Settings 与 GET**

P4 Settings 按固定表解析 enabled、vault_path、root_folder、pdf_mode、export_source、export_explainer、export_translation、auto_export；environment > settings.json > default。复用现有 hash-guarded atomic JSON adapter与per-path lock；Obsidian values只作为非秘密 fields，不扩大 P1 CredentialKind。GET status 仅返回 effective flags、rootFolder、pdfMode、vaultConfigured/writable、last job与安全 aggregate，不回显resolved absolute Vault path。

- [ ] **Step 5（2–5 分钟）：实现 POST disabled guard**

三条 POST route 在任何目录创建、job enqueue、LibraryQueries 或 write probe 前检查 enabled；disabled 使用稳定 409 error envelope。不得注册 generic GET 或 POST /api/v2/obsidian。

- [ ] **Step 6（2–5 分钟）：运行并确认 GREEN**

Run:

~~~powershell
.\.venv\Scripts\python.exe -B -m unittest backend.tests.test_obsidian_jobs_api.ObsidianJobsApiTests.test_obsidian_is_disabled_by_default -v
~~~

Expected GREEN: unittest summary reports 1 test and OK；两个表 count 为 0，Vault 临时根为空。

- [ ] **Step 7（2–5 分钟）：运行 schema 契约**

Run:

~~~powershell
.\.venv\Scripts\python.exe -B -m unittest backend.tests.test_obsidian_jobs_api.ObsidianJobsApiTests.test_obsidian_exports_schema_matches_p1_contract -v
~~~

Expected: test OK；Alembic 唯一 head 仍为 20260807_03。

- [ ] **Step 8（2–5 分钟）：写 eight-field priority 与 validation 红测**

在 <code>backend/tests/test_obsidian_settings.py</code> 新增 <code>test_eight_fields_have_exact_priority_defaults_and_validation</code>。用 frozen env、temporary settings JSON 覆盖每一项 environment/file/default priority；断言 environment bool仅0|1、JSON bool仅boolean、pdf mode严格三值、vault path empty或absolute、root folder为无dot/dot-dot/drive/UNC/control/NUL的relative POSIX path。默认值精确为 false、empty、Research、none、true、true、true、false。

- [ ] **Step 9（2–5 分钟）：运行并确认 Settings RED**

Run:

~~~powershell
.\.venv\Scripts\python.exe -B -m unittest backend.tests.test_obsidian_settings.ObsidianSettingsTests.test_eight_fields_have_exact_priority_defaults_and_validation -v
~~~

Expected RED: Settings 尚未实现完整 field/priority matrix；构造与读取不得创建 settings/Vault/DB 文件。

- [ ] **Step 10（2–5 分钟）：实现 parser、view 与 atomic update**

在 <code>backend/app/application/settings.py</code> 实现 frozen ObsidianSettings。现有 <code>GET/POST /api/settings</code> 扩展同名 camelCase fields；environment effective value保持read-only authoritative。Update只改allowlist keys并atomic replace；不得调用 VaultWriter、ProcessingQueue、PdfMigration、CredentialStore secret getter、mkdir/copy/move/delete或DB。

- [ ] **Step 11（2–5 分钟）：重新运行 Settings priority/validation 测试并确认 GREEN**

Run:

~~~powershell
.\.venv\Scripts\python.exe -B -m unittest backend.tests.test_obsidian_settings.ObsidianSettingsTests.test_eight_fields_have_exact_priority_defaults_and_validation -v
~~~

Expected GREEN: exact defaults/priority/negative matrix通过；temporary settings以外零写入。

- [ ] **Step 12（2–5 分钟）：写 CredentialStore preservation 与 save-no-migration 红测**

新增 <code>ObsidianSettingsTests.test_obsidian_save_preserves_credentials_unknown_fields_and_never_moves_pdf</code>；fixture 含 apiKey、ocrApiKey、embedApiKey、s2ApiKey、八项 Obsidian keys、unknown nested value 与 pdfDir 文件。保存设置后四类 credential/unknown fields 完整保留，PDF/Vault 树与 papers.pdf_path 不变；Keyring、credential getters、migration、queue、Vault adapter、copy/move/unlink spies 全 0。扩展 <code>backend/tests/test_credentials.py</code> 并新增名称固定的 <code>LegacyCredentialMigrationTests.test_obsidian_fields_survive_credential_mutation</code>，证明 llm/ocr/embedding/semantic_scholar 四类 update/clear 都保留八项 Obsidian keys 与 P0–P6 Node rollback plaintext contract。

- [ ] **Step 13（2–5 分钟）：运行并确认 preservation RED**

Run:

~~~powershell
.\.venv\Scripts\python.exe -B -m unittest backend.tests.test_obsidian_settings.ObsidianSettingsTests.test_obsidian_save_preserves_credentials_unknown_fields_and_never_moves_pdf backend.tests.test_credentials.LegacyCredentialMigrationTests.test_obsidian_fields_survive_credential_mutation -v
~~~

Expected RED: P1/P4 adapters尚未共同证明allowlist preservation或Settings save仍有目录准备副作用；测试不得访问真实Keyring/Live settings。

- [ ] **Step 14（2–5 分钟）：共享 atomic document operation 并移除 side effects**

P1 legacy credential adapter 与 P4 Settings 共享 resolved-path serialization/expected-byte-hash operation，各自只改自己的 keys。保持 legacy apiKey/ocrApiKey/embedApiKey/s2ApiKey 至 P0–P6，绝不调用 finalize；四类 blank-preserve、explicit clear 与 redaction 语义完全一致。把任何 ensure-directory/PDF relocation 从 Settings save 移到显式 use case。

- [ ] **Step 15（2–5 分钟）：重新运行 preservation/no-migration 测试并确认 GREEN**

Run:

~~~powershell
.\.venv\Scripts\python.exe -B -m unittest backend.tests.test_obsidian_settings.ObsidianSettingsTests.test_obsidian_save_preserves_credentials_unknown_fields_and_never_moves_pdf backend.tests.test_credentials.LegacyCredentialMigrationTests.test_obsidian_fields_survive_credential_mutation -v
~~~

Expected GREEN: credentials、unknown keys、八项配置与PDF bytes符合contract；无temp residue、Keyring/Vault/DB call。

- [ ] **Step 16（2–5 分钟）：写 Vault missing/unwritable 红测**

新增 <code>test_missing_and_unwritable_vault_fail_before_enqueue</code>；覆盖missing root、root为普通file、managed root symlink/junction escape、BoundVaultRoot capability缺失与injected exclusive-create PermissionError。Status返回safe classified code；test/export/sync分别返回稳定错误且processing_jobs/ledger均零。Missing root绝不mkdir；writable case只经BoundVaultRoot创建、flush/fsync并按bound identity删除本次exclusive probe，最终tree全等。

- [ ] **Step 17（2–5 分钟）：运行并确认 access RED**

Run:

~~~powershell
.\.venv\Scripts\python.exe -B -m unittest backend.tests.test_obsidian_settings.ObsidianSettingsTests.test_missing_and_unwritable_vault_fail_before_enqueue -v
~~~

Expected RED: classified access inspection/probe尚不存在；root外sentinel与temporary DB不变。

- [ ] **Step 18（2–5 分钟）：实现 read-only inspection 与 exclusive probe**

只读inspection可以词法解析路径；probe在首次write前必须构造并持有BoundVaultRoot，完成平台capability/root/ancestor identity检查。错误code固定 <code>OBSIDIAN_VAULT_NOT_FOUND|OBSIDIAN_VAULT_NOT_DIRECTORY|OBSIDIAN_PATH_ESCAPE|OBSIDIAN_VAULT_NOT_WRITABLE|OBSIDIAN_ATOMIC_PRIMITIVE_UNAVAILABLE</code>。Probe publish使用true no-replace，cleanup只通过bound target identity删除本次exclusive-create成功且identity未变的文件/空parent，不吞掉用户竞态创建；Windows handle/POSIX dirfd保持到cleanup和parent fsync结束。

- [ ] **Step 19（2–5 分钟）：重新运行 Vault access 定向测试并确认 GREEN**

Run:

~~~powershell
.\.venv\Scripts\python.exe -B -m unittest backend.tests.test_obsidian_settings.ObsidianSettingsTests.test_missing_and_unwritable_vault_fail_before_enqueue -v
~~~

Expected GREEN: missing/unwritable/escape/writable matrix全绿；zero enqueue、zero ledger、zero residue。

- [ ] **Step 20（2–5 分钟）：写 P5 rollout vocabulary 红测**

在 Node/Python rollout tests 新增 versioned P5 case：P0.1 baseline inventory 仍精确为 API_BACKEND_MODE、DOCUMENT_PIPELINE_MODE、GENERATION_PIPELINE_MODE、ARTIFACT_READ_MODE、ARTIFACT_WRITE_MODE、OCR_ENABLED 六项，不修改其 golden；P5 schema 在此基础上只新增 <code>OBSIDIAN_ENABLED</code>，absent 精确为 <code>0</code>，只接受 <code>0|1</code>，两端 immutable startup snapshot 完全一致。证明 P5 没有把该变量伪装成 P0 已有字段。

- [ ] **Step 21（2–5 分钟）：运行并确认 rollout RED**

Run:

~~~powershell
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$p5NodeRedOutput = & node --test test/backend-rollout.test.js 2>&1
$p5NodeRedExit = $LASTEXITCODE
if ($p5NodeRedExit -eq 0 -or ($p5NodeRedOutput -join "`n") -notmatch 'OBSIDIAN_ENABLED|P5') { throw 'P5 Node rollout RED was absent or unrelated.' }
$p5PythonRedOutput = & .\.venv\Scripts\python.exe -B -m unittest backend.tests.test_rollout_defaults -v 2>&1
$p5PythonRedExit = $LASTEXITCODE
if ($p5PythonRedExit -eq 0 -or ($p5PythonRedOutput -join "`n") -notmatch 'OBSIDIAN_ENABLED|P5') { throw 'P5 Python rollout RED was absent or unrelated.' }
~~~

Expected RED: P0.1 strict parsers 尚无显式 P5 vocabulary/version，或 Node/Python 无法区分六项 baseline 与七项 P5 snapshot。

- [ ] **Step 22（2–5 分钟）：实现 versioned extension 并确认 GREEN**

在现有 Node/Python strict parser 中增加显式 P5 vocabulary；不复制 parser，不修改 P0 plan/golden 的六项 inventory。<code>OBSIDIAN_ENABLED</code> 在 composition 时只读一次；P4 Settings 的 environment priority 消费该 frozen boolean，不再次读取实时环境。invalid 值在 socket/DB/provider/Vault 前 fail-fast。

Run:

~~~powershell
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
node --test test/backend-rollout.test.js
if ($LASTEXITCODE -ne 0) { throw 'P5 Node rollout suite failed.' }
.\.venv\Scripts\python.exe -B -m unittest backend.tests.test_rollout_defaults -v
if ($LASTEXITCODE -ne 0) { throw 'P5 Python rollout suite failed.' }
~~~

Expected GREEN: P0.1 six-field fixtures 原样全绿；P5 seven-field parity/default/invalid/immutability tests 全绿。

## Task 2：生成确定性路径与 Markdown projection

**Files:**
- Create: backend/app/application/obsidian_projection.py
- Create: backend/tests/test_obsidian_layout.py
- Create: backend/tests/fixtures/obsidian/golden/paper.md
- Create: backend/tests/fixtures/obsidian/golden/source.md
- Create: backend/tests/fixtures/obsidian/golden/explainer.md
- Create: backend/tests/fixtures/obsidian/golden/translation.md
- Create: backend/tests/fixtures/obsidian/golden/note.md

- [ ] **Step 1（2–5 分钟）：写 layout golden 红测**

新增 <code>ObsidianLayoutTests.test_projection_matches_golden_layout</code>，使用固定安全 paper id <code>paper-01</code>、中英文标题、ready source、ready explainer/translation 与 DB note，逐字节比较五类 Markdown。路径必须精确为 <code>Papers/paper-01.md</code>、<code>Sources/paper-01.md</code>、<code>Explainers/paper-01.md</code>、<code>Translations/paper-01.md</code>、<code>Notes/paper-01.md</code>；title 不得出现在任何 filename。

- [ ] **Step 2（2–5 分钟）：确认 layout 红测**

Run:

~~~powershell
.\.venv\Scripts\python.exe -B -m unittest backend.tests.test_obsidian_layout.ObsidianLayoutTests.test_projection_matches_golden_layout -v
~~~

Expected RED: backend.app.application.obsidian_projection 不存在。

- [ ] **Step 3（2–5 分钟）：实现 paper-id validation 与固定路径**

实现 <code>validate_paper_file_id</code> 与 <code>project_paths</code>。验证规则与固定 Vault 契约逐项一致；输出只使用原始 validated <code>papers.id</code>，严格产生 <code>Papers/{paper_id}.md</code>、<code>Sources/{paper_id}.md</code>、<code>Explainers/{paper_id}.md</code>、<code>Translations/{paper_id}.md</code>、<code>Notes/{paper_id}.md</code> 与 <code>Attachments/PDF/{paper_id}.pdf</code>。不得 sanitize、encode、truncate、拼 title/artifact id 或加入序号/时间。

- [ ] **Step 4（2–5 分钟）：实现 canonical frontmatter 与 renderer**

固定 key 顺序、JSON-compatible scalar、LF 和单个文件末尾换行；projection bytes 不嵌入当前时间。title、titleZh、authors、aliases 与 tags 使用同一 YAML scalar encoder；tags 去重后按 code-point 排序并逐项输出 quoted list，绝不拼接未转义冒号、换行或 YAML tag syntax。

- [ ] **Step 5（2–5 分钟）：实现确定性 Paper 模板与五类 renderer**

Paper body 固定 title/metadata 与 Source、Explainer、Translation、Notes 四节：eligible 且相应 export flag=true 时使用固定相对 Markdown link；Source/Explainer/Translation 缺失时分别写固定 placeholder；Notes 始终链接 <code>../Notes/{paper_id}.md</code>，但只在 DB note 非空时生成一次 seed candidate。Source/Explainer/Translation renderer 保留完整 ready Markdown；缺失时不生成对应文件、不写“生成中”、不创建任务。

- [ ] **Step 6（2–5 分钟）：运行并确认 layout GREEN**

Run:

~~~powershell
.\.venv\Scripts\python.exe -B -m unittest backend.tests.test_obsidian_layout.ObsidianLayoutTests.test_projection_matches_golden_layout -v
~~~

Expected GREEN: golden path 与 bytes 全等。

- [ ] **Step 7（2–5 分钟）：写 unsafe ID 与 case-fold collision 红测**

新增 <code>test_rejects_unsafe_paper_ids_without_partial_projection</code> 与 <code>test_rejects_casefold_id_collisions_before_projection</code>。覆盖 empty、dot/dot-dot、绝对路径、drive/UNC、slash/backslash、colon、control/NUL、Unicode/emoji、Windows device name、首字符非法、尾点/空格、181 字符，以及同一 snapshot 中 <code>Paper-1|paper-1</code>；断言错误为 <code>OBSIDIAN_PAPER_ID_UNSAFE</code> 或明确 collision，projection/manifest/ledger/job mutation 全为 0。

- [ ] **Step 8（2–5 分钟）：确认 ID validation RED**

Run:

~~~powershell
.\.venv\Scripts\python.exe -B -m unittest backend.tests.test_obsidian_layout.ObsidianLayoutTests.test_rejects_unsafe_paper_ids_without_partial_projection backend.tests.test_obsidian_layout.ObsidianLayoutTests.test_rejects_casefold_id_collisions_before_projection -v
~~~

Expected RED: batch preflight 尚未在构造任何 projection 前验证全部 ID 与 case-fold uniqueness。

- [ ] **Step 9（2–5 分钟）：实现全 snapshot preflight 并确认 GREEN**

Exporter 在读取 snapshot 后、render/enqueue/Vault/ledger 前验证全部 paper id，并将 existing manifest/target 的 case-fold identity 纳入冲突检查；任一失败整次 request 零 mutation。单篇与批量调用复用同一 validator，不存在 fallback filename。

Run:

~~~powershell
.\.venv\Scripts\python.exe -B -m unittest backend.tests.test_obsidian_layout.ObsidianLayoutTests.test_rejects_unsafe_paper_ids_without_partial_projection backend.tests.test_obsidian_layout.ObsidianLayoutTests.test_rejects_casefold_id_collisions_before_projection -v
~~~

Expected GREEN: 全部 negative matrix 被同步拒绝，valid IDs 保持原字节身份。

- [ ] **Step 10（2–5 分钟）：写 template/rename/YAML 红测**

新增 <code>test_paper_template_links_placeholders_and_yaml_lists_are_canonical</code> 与 <code>test_title_change_keeps_all_paths_and_only_changes_managed_bytes</code>。覆盖 flags、三类 ready/missing 组合、重复/冒号/换行/Unicode tags、authors/aliases，以及 title 改名；断言相同输入 bytes 稳定，改名仍使用相同 paper-id paths，Notes seed bytes 不被重渲染。

- [ ] **Step 11（2–5 分钟）：运行 template command 并确认 RED**

Run:

~~~powershell
.\.venv\Scripts\python.exe -B -m unittest backend.tests.test_obsidian_layout.ObsidianLayoutTests.test_paper_template_links_placeholders_and_yaml_lists_are_canonical backend.tests.test_obsidian_layout.ObsidianLayoutTests.test_title_change_keeps_all_paths_and_only_changes_managed_bytes -v
~~~

Expected RED: renderer/template尚未覆盖至少一个canonical list/link/rename case；golden fixture语法或路径错误不算RED。

- [ ] **Step 12（2–5 分钟）：实现 template matrix**

复用唯一renderer/YAML encoder与paper-id path builder；title只改变managedbytes，不改变任何relative path，Note seed不重渲染。

- [ ] **Step 13（2–5 分钟）：运行相同 template command 并确认 GREEN**

~~~powershell
.\.venv\Scripts\python.exe -B -m unittest backend.tests.test_obsidian_layout.ObsidianLayoutTests.test_paper_template_links_placeholders_and_yaml_lists_are_canonical backend.tests.test_obsidian_layout.ObsidianLayoutTests.test_title_change_keeps_all_paths_and_only_changes_managed_bytes -v
~~~

Expected GREEN: links/placeholders、YAML list与同路径title update全部逐字节匹配golden；不存在title-derived path。

## Task 3：实现 managed marker 与 manifest

**Files:**
- Create: backend/app/infrastructure/bound_vault_root.py
- Create: backend/tests/test_bound_vault_root.py
- Create: backend/app/providers/obsidian_vault.py
- Create: backend/tests/test_obsidian_ownership.py
- Create: backend/tests/fixtures/obsidian/golden/manifest.json
- Modify: backend/app/application/obsidian_projection.py

- [ ] **Step 1（2–5 分钟）：写 marker/manifest 红测**

新增 <code>ObsidianOwnershipTests.test_manifest_and_markers_are_deterministic</code>，断言 Paper/Source/Explainer/Translation 使用完整 managed marker；Notes 只使用 <code>paper-study-note-seed: true</code> 且 manifest ownership=user，不得带 managed=true。manifest schemaVersion=1、entries 按 path 排序、路径无反斜杠/absolute/dot-dot，同一输入除 generatedAt 外 canonical bytes 可复现。

- [ ] **Step 2（2–5 分钟）：确认红测**

Run:

~~~powershell
.\.venv\Scripts\python.exe -B -m unittest backend.tests.test_obsidian_ownership.ObsidianOwnershipTests.test_manifest_and_markers_are_deterministic -v
~~~

Expected RED: manifest writer 尚不存在或缺少 entries。

- [ ] **Step 3（2–5 分钟）：实现 manifest model**

manifest 固定写 schemaVersion、exporterVersion、generatedAt、entries；每个 entry 固定含 path、kind、paperId、artifactId 或 null、ownership=<code>managed|user</code>、sourceHash、exportedHash。generatedAt 不参与 projection source_hash；序列化使用 UTF-8、indent=2、固定 key 顺序、LF。PDF ownership 仅由 kind=pdf-copy 与 manifest hashes 证明。

- [ ] **Step 4（2–5 分钟）：实现 marker parser**

parser 只读取文件开头有限字节的 frontmatter；managed parser 要求 <code>paper-study-managed</code> 严格 boolean true 且 paper id/kind/artifact id 匹配，Note seed parser 只识别 user-owned seed marker。解析失败、duplicate key、type mismatch 或截断一律视为 unowned，不尝试修复用户文件。

- [ ] **Step 5（2–5 分钟）：只写 root/parent bind-and-swap 红测**

新增 `BoundVaultRootTests.test_rejects_root_and_parent_junction_symlink_swaps_while_bound`。Windows fixture在initial inspection后把Vault root、Research与两级parent尝试替换为junction；POSIX fixture对应symlink/rename swap。mutation线程在barrier后继续，断言ancestor handles/dirfds阻止swap或检测identity change并返回`OBSIDIAN_ROOT_CHANGED|OBSIDIAN_PARENT_CHANGED`，root外sentinel、Vault、ledger、manifest、temp均不变。平台fixture无法实际建立swap时必须skip该平台case并由对应capability unit test覆盖，不能假造通过。

- [ ] **Step 6（2–5 分钟）：运行 root/parent swap command 并确认 RED**

~~~powershell
.\.venv\Scripts\python.exe -B -m unittest backend.tests.test_bound_vault_root.BoundVaultRootTests.test_rejects_root_and_parent_junction_symlink_swaps_while_bound -v
~~~

Expected RED: BoundVaultRoot尚不存在，或path-only实现让至少一个swap抵达root外；import/spelling/barrier未真正发生不算有效RED。

- [ ] **Step 7（2–5 分钟）：最小实现平台 root/ancestor binding**

按固定契约实现Windows no-delete-share directory handle chain与POSIX `O_NOFOLLOW`逐级dirfd/openat；记录identity并持有到operation结束。Capability缺失在首次write前返回typed failure，不得回退到resolve-prefix检查。

- [ ] **Step 8（2–5 分钟）：运行相同 root/parent swap command 并确认 GREEN**

~~~powershell
.\.venv\Scripts\python.exe -B -m unittest backend.tests.test_bound_vault_root.BoundVaultRootTests.test_rejects_root_and_parent_junction_symlink_swaps_while_bound -v
~~~

Expected GREEN: 当前平台全部可执行swap case `OK`，另一平台case有明确platform skip；root外sentinel与Vault树全等。

- [ ] **Step 9（2–5 分钟）：只写 path-open/file-write tripwire 红测**

新增`BoundVaultRootTests.test_all_vault_mutations_use_only_bound_handle_or_dirfd_operations`。对managed directory creation、Paper/Source/manifest bootstrap/Note seed/probe/PDF copy/temp cleanup各执行一次，在adapter白名单native syscall wrapper之外patch `Path.mkdir/open/write_text/write_bytes/unlink/replace`、`os.makedirs`、builtin `open`及path-only`os.open/replace/rename/unlink`为AssertionError；断言当前实现触发tripwire且真实Vault/DB均未触碰。

- [ ] **Step 10（2–5 分钟）：运行 tripwire command 并确认 RED**

~~~powershell
.\.venv\Scripts\python.exe -B -m unittest backend.tests.test_bound_vault_root.BoundVaultRootTests.test_all_vault_mutations_use_only_bound_handle_or_dirfd_operations -v
~~~

Expected RED: 至少一条现有mutation仍走path-only API；如果spy未覆盖实际调用栈则先修fixture并重跑。

- [ ] **Step 11（2–5 分钟）：将所有 Vault mutation 收口到 BoundVaultRoot**

VaultWriter只接受BoundVaultRoot与validated relative segments；probe、manifest、Notes、PDF和temp cleanup调用同一接口。Renderer/Exporter不得持有absolute target path或自行open/write。

- [ ] **Step 12（2–5 分钟）：运行相同 tripwire 与 marker test 并确认 GREEN**

~~~powershell
.\.venv\Scripts\python.exe -B -m unittest backend.tests.test_bound_vault_root.BoundVaultRootTests.test_all_vault_mutations_use_only_bound_handle_or_dirfd_operations backend.tests.test_obsidian_ownership.ObsidianOwnershipTests.test_manifest_and_markers_are_deterministic -v
~~~

Expected GREEN: 2 tests `OK`；全部mutation经bound primitive且manifest golden/marker不变。

- [ ] **Step 13（2–5 分钟）：写 manifest carry-forward 红测**

新增 <code>test_manifest_carries_forward_user_stale_and_orphan_entries</code>。准备 user-owned Note、当前 Paper 的 stale managed artifact、已删除 Paper 的 orphan/tombstone 与新 projection；重建 manifest 后前三类 entry/bytes 必须原样保留并按 path 重新排序，不能因当前 LibraryQueries snapshot 缺失而 drop、rewrite 或转成 deletable。

- [ ] **Step 14（2–5 分钟）：运行 carry-forward command 并确认 RED**

~~~powershell
.\.venv\Scripts\python.exe -B -m unittest backend.tests.test_obsidian_ownership.ObsidianOwnershipTests.test_manifest_carries_forward_user_stale_and_orphan_entries -v
~~~

Expected RED: prior manifest merge尚未保留至少一种user/stale/orphan entry；fixture缺少真实prior bytes不算RED。

- [ ] **Step 15（2–5 分钟）：实现 merge model**

manifest writer以prior manifest为输入，合并本次成功发布的managed entries；user-owned、stale未获cleanup confirmation与orphan/tombstone carry-forward。path/paper identity冲突返回conflict，不覆盖prior entry；manifest只用BoundVaultRoot identity-bound replace或bootstrap no-replace。

- [ ] **Step 16（2–5 分钟）：运行相同 carry-forward command 并确认 GREEN**

Run:

~~~powershell
.\.venv\Scripts\python.exe -B -m unittest backend.tests.test_obsidian_ownership.ObsidianOwnershipTests.test_manifest_carries_forward_user_stale_and_orphan_entries -v
~~~

Expected GREEN: carry-forward bytes/entry fields 全等，自动 drop 数为 0。

## Task 4：实现原子发布、幂等与冲突保护

**Files:**
- Modify: backend/app/infrastructure/bound_vault_root.py
- Modify: backend/tests/test_bound_vault_root.py
- Modify: backend/app/providers/obsidian_vault.py
- Modify: backend/tests/test_obsidian_ownership.py

- [ ] **Step 1（2–5 分钟）：写用户编辑冲突红测**

新增 test_user_modified_managed_file_is_never_overwritten，先发布、再修改正文、再以新 source_hash 投影；断言用户 bytes 不变、结果 status=conflict、临时文件清零。

- [ ] **Step 2（2–5 分钟）：确认冲突红测**

Run:

~~~powershell
.\.venv\Scripts\python.exe -B -m unittest backend.tests.test_obsidian_ownership.ObsidianOwnershipTests.test_user_modified_managed_file_is_never_overwritten -v
~~~

Expected RED: 当前 writer 覆盖用户修改或没有 conflict 结果。

- [ ] **Step 3（2–5 分钟）：实现 compare-before-replace**

覆盖前依次验证BoundVaultRoot仍绑定root/parent、manifest ownership=managed、当前profile ledger target/paper/artifact/source/exported hashes、Markdown marker或PDF manifest hash，以及通过bound target handle/dirfd读取的当前磁盘SHA-256等于最后成功ledger exported_hash；将该exact file identity封装为`BoundTargetIdentity`。任一步失败不写final target、不改exported_hash/manifest并返回分类conflict。首次创建必须走true no-replace，不放宽成“无ledger即owned”。

- [ ] **Step 4（2–5 分钟）：实现同目录原子写**

在bound parent handle/dirfd下exclusive create唯一temp，write/flush/fsync后复验root/parent/target。首次发布调用`publish_new`的platform no-replace primitive；managed更新调用`replace_managed(expected_identity)`的handle-bound exchange/backup primitive并验证被置换identity，绝不调用path-only`os.replace`。最后fsync bound parent；异常只按owned temp identity cleanup。

- [ ] **Step 5（2–5 分钟）：运行并确认冲突 GREEN**

Run:

~~~powershell
.\.venv\Scripts\python.exe -B -m unittest backend.tests.test_obsidian_ownership.ObsidianOwnershipTests.test_user_modified_managed_file_is_never_overwritten -v
~~~

Expected GREEN: 用户 bytes 与修改前捕获值一致，conflict 明确可诊断。

- [ ] **Step 6（2–5 分钟）：写 Note seed-once 红测**

新增 <code>test_note_is_seeded_once_then_permanently_user_managed</code>。覆盖 DB note nonblank 且 target absent 的首次 exclusive seed、用户预建 target、seed 后用户编辑、DB note/title 后续变化，以及 manual export/sync/auto-export/cleanup/rebuild；首次成功后所有路径的 Note write/replace/rename/unlink 均为 0，结果计 userManaged，Vault 内容绝不回写 DB。

- [ ] **Step 7（2–5 分钟）：运行 Note seed command 并确认 RED**

~~~powershell
.\.venv\Scripts\python.exe -B -m unittest backend.tests.test_obsidian_ownership.ObsidianOwnershipTests.test_note_is_seeded_once_then_permanently_user_managed -v
~~~

Expected RED: Note尚未通过BoundVaultRoot true no-replace seed，或后续流程存在至少一次write/replace/delete；fixture未真正模拟用户预建/编辑不算RED。

- [ ] **Step 8（2–5 分钟）：实现 Note user-ownership**

Note seed只调用BoundVaultRoot.publish_new；若target在final no-replace窗口出现则保留竞争文件并返回user_managed。manifest只登记ownership=user与初始hashes；以后无论磁盘hash是否改变都不进入replace/delete/cleanup interface。

- [ ] **Step 9（2–5 分钟）：运行相同 Note seed command 并确认 GREEN**

Run:

~~~powershell
.\.venv\Scripts\python.exe -B -m unittest backend.tests.test_obsidian_ownership.ObsidianOwnershipTests.test_note_is_seeded_once_then_permanently_user_managed -v
~~~

Expected GREEN: 只有首次 absent/nonblank case 写一次；其余 Note mutation spies 全 0。

- [ ] **Step 10（2–5 分钟）：只写 unchanged no-op 红测**

新增`test_same_source_hash_is_noop`；source_hash与bound current exported_hash相同，spy断言不创建temp、不调用no-replace/replace/delete且ledger/manifest bytes不变。

- [ ] **Step 11（2–5 分钟）：运行 no-op command 并确认 RED**

~~~powershell
.\.venv\Scripts\python.exe -B -m unittest backend.tests.test_obsidian_ownership.ObsidianOwnershipTests.test_same_source_hash_is_noop -v
~~~

Expected RED: writer仍产生temp或进入publish/ledger mutation。

- [ ] **Step 12a（2–5 分钟）：实现 no-op**

source_hash相同且bound current bytes hash等于exported_hash时直接unchanged，仍复验root/parent/target identity但零write。

- [ ] **Step 12b（2–5 分钟）：运行相同 no-op command 并确认 GREEN**

~~~powershell
.\.venv\Scripts\python.exe -B -m unittest backend.tests.test_obsidian_ownership.ObsidianOwnershipTests.test_same_source_hash_is_noop -v
~~~

Expected GREEN: 单一test `OK`且所有mutation spies为0。

- [ ] **Step 13（2–5 分钟）：只写首次 final-publish竞态红测**

新增`BoundVaultRootTests.test_first_publish_is_true_no_replace_under_final_target_race`。Barrier位于temp fsync后/final publish前，竞争线程创建不同bytes的final target；Windows与POSIX实现都必须返回`OBSIDIAN_TARGET_EXISTS`，竞争bytes/identity不变，只清理owned temp。测试另断言adapter选择的是平台true no-replace primitive，禁止用exists-check+replace。

- [ ] **Step 14（2–5 分钟）：运行 final-publish race command 并确认 RED**

~~~powershell
.\.venv\Scripts\python.exe -B -m unittest backend.tests.test_bound_vault_root.BoundVaultRootTests.test_first_publish_is_true_no_replace_under_final_target_race -v
~~~

Expected RED: path-only replace覆盖竞争文件，或实现无能力检查却开始写temp；barrier未命中final窗口不算RED。

- [ ] **Step 15（2–5 分钟）：实现 platform true no-replace**

按固定契约选择Windows handle-bound create/publish或POSIX `renameat2(RENAME_NOREPLACE)|renamex_np(RENAME_EXCL)|linkat`；capability在temp创建前冻结，不能在失败后fallback到覆盖式rename。

- [ ] **Step 16（2–5 分钟）：运行相同 final-publish race command 并确认 GREEN**

Run:

~~~powershell
.\.venv\Scripts\python.exe -B -m unittest backend.tests.test_bound_vault_root.BoundVaultRootTests.test_first_publish_is_true_no_replace_under_final_target_race -v
~~~

Expected GREEN: 竞争target bytes/identity全等、owned temp清零、单一test `OK`。

- [ ] **Step 17（2–5 分钟）：只写 managed final identity replace/delete 红测**

新增`BoundVaultRootTests.test_managed_replace_and_delete_require_exact_final_identity`。完成三证据proof并捕获target identity后，在apply barrier交换final file；replace与delete都必须检测`OBSIDIAN_TARGET_CHANGED`、保留竞争bytes并恢复任何exchange backup。Exact identity case可replace/delete，且old/new/removed hashes逐项匹配；root/parent handles/dirfds始终持有。

- [ ] **Step 18（2–5 分钟）：运行 managed identity command 并确认 RED**

~~~powershell
.\.venv\Scripts\python.exe -B -m unittest backend.tests.test_bound_vault_root.BoundVaultRootTests.test_managed_replace_and_delete_require_exact_final_identity -v
~~~

Expected RED: replace/delete只检查pathname或hash，导致竞态文件被替换/删除；fixture必须证明identity确实交换。

- [ ] **Step 19a（2–5 分钟）：实现 identity-bound replace/delete**

实现平台handle-bound exchange/backup与delete primitive；被置换identity不等于expected时原子恢复并fail closed，删除只作用于apply-time重验的exact handle/dirfd identity。

- [ ] **Step 19b（2–5 分钟）：运行相同 managed identity command 并确认 GREEN**

~~~powershell
.\.venv\Scripts\python.exe -B -m unittest backend.tests.test_bound_vault_root.BoundVaultRootTests.test_managed_replace_and_delete_require_exact_final_identity -v
~~~

Expected GREEN: 竞态文件不变、exact case成功、无backup/temp残留。

- [ ] **Step 20（2–5 分钟）：只写 stale delete-interface 红测**

新增 <code>test_vault_writer_has_no_unproven_or_automatic_delete_path</code>；VaultWriter 只能接受由 cleanup use case 生成并在 apply-time 重验的 typed proof，证明 manifest+ledger+marker/hash 三方一致。普通 export/sync/rebuild、Note、missing-ledger orphan 与 hash mismatch 均不能调用 unlink/rename-to-trash。

- [ ] **Step 21（2–5 分钟）：运行 stale delete command 并确认 RED**

Run:

~~~powershell
.\.venv\Scripts\python.exe -B -m unittest backend.tests.test_obsidian_ownership.ObsidianOwnershipTests.test_vault_writer_has_no_unproven_or_automatic_delete_path -v
~~~

Expected RED: cleanup尚未要求typed `BoundTargetIdentity`，或至少一个无三证据路径抵达delete primitive。

- [ ] **Step 22a（2–5 分钟）：实现 typed delete seam**

VaultWriter.delete只接受cleanup planner产出的typed proof，经BoundVaultRoot重新绑定exact target identity后调用`delete_managed`；普通projection interface不暴露delete。

- [ ] **Step 22b（2–5 分钟）：运行相同 stale delete command 并确认 GREEN**

~~~powershell
.\.venv\Scripts\python.exe -B -m unittest backend.tests.test_obsidian_ownership.ObsidianOwnershipTests.test_vault_writer_has_no_unproven_or_automatic_delete_path -v
~~~

Expected GREEN: 所有无三证据路径只分类并保留，delete调用数0。

## Task 5：接入 obsidian_exports 账本

**Files:**
- Create: backend/app/repositories/obsidian_exports.py
- Create: backend/tests/test_obsidian_exports_repository.py
- Modify: backend/app/application/obsidian_projection.py
- Modify: backend/app/api/dependencies.py

- [ ] **Step 1（2–5 分钟）：写九字段 repository 红测**

新增 <code>ObsidianExportsRepositoryTests.test_upsert_and_conflict_preserve_exported_hash</code>，插入 exported，再写 conflict；断言 id、paper_id、artifact_id、target_path、source_hash、exported_hash、status、exported_at、error_message 九字段精确值，conflict/error 不改最后成功 exported_hash/exported_at。

- [ ] **Step 2（2–5 分钟）：确认 repository 红测**

Run:

~~~powershell
.\.venv\Scripts\python.exe -B -m unittest backend.tests.test_obsidian_exports_repository.ObsidianExportsRepositoryTests.test_upsert_and_conflict_preserve_exported_hash -v
~~~

Expected RED: backend.app.repositories.obsidian_exports 不存在。

- [ ] **Step 3（2–5 分钟）：实现 VaultProjectionRepository adapter**

只操作 P1 obsidian_exports；target_path 使用相对 effective root_folder 的固定正斜杠路径并保持全局唯一。exported/unchanged 写 source_hash/exported_hash/exported_at 并清 error_message；conflict/error 更新 status 与安全 error_message，但保留最后成功 exported_hash/exported_at 作为用户编辑检测基线。不得创建第二表或在 repository 内执行 Vault I/O。

- [ ] **Step 4（2–5 分钟）：把文件与账本置于可恢复顺序**

先构造immutable plan，再通过BoundVaultRoot发布文件，再在短UoW中写ledger，最后通过同一bound root identity-bound merge manifest；任一步都不把文件I/O放进SQLite write transaction。ledger失败不回删已发布文件；manifest失败不回滚成功ledger。下次只在desired bytes、固定path、bound target identity、marker/hash与已有ledger/manifest的非冲突证据一致时补记缺失阶段。

- [ ] **Step 5（2–5 分钟）：运行并确认 GREEN**

Run:

~~~powershell
.\.venv\Scripts\python.exe -B -m unittest backend.tests.test_obsidian_exports_repository.ObsidianExportsRepositoryTests.test_upsert_and_conflict_preserve_exported_hash -v
~~~

Expected GREEN: repository round-trip 通过，连接在异常路径关闭。

- [ ] **Step 6（2–5 分钟）：只写 publish 后 ledger crash reconciliation 红测**

新增`test_reconciles_published_file_after_ledger_crash`；模拟BoundVaultRoot publish成功后DB commit失败。第二次执行只凭fixed desired bytes、bound target identity、marker/hash与非冲突证据补ledger/manifest，不替换文件。若期间target identity或bytes变化则conflict且保留用户bytes。

- [ ] **Step 7（2–5 分钟）：运行 published-file reconciliation command 并确认 RED**

Run:

~~~powershell
.\.venv\Scripts\python.exe -B -m unittest backend.tests.test_obsidian_exports_repository.ObsidianExportsRepositoryTests.test_reconciles_published_file_after_ledger_crash -v
~~~

Expected RED: second run重复publish或无法证明已发布文件identity；crash未发生在publish/commit边界不算RED。

- [ ] **Step 8a（2–5 分钟）：实现 published-file reconciliation**

读取bound target并验证exact desired hash/marker/identity后只补ledger，再用identity-bound manifest merge；任一变化返回conflict。

- [ ] **Step 8b（2–5 分钟）：运行相同 published-file reconciliation command 并确认 GREEN**

~~~powershell
.\.venv\Scripts\python.exe -B -m unittest backend.tests.test_obsidian_exports_repository.ObsidianExportsRepositoryTests.test_reconciles_published_file_after_ledger_crash -v
~~~

Expected GREEN: 文件一份、ledger一行、manifest一项且零temp。

- [ ] **Step 9（2–5 分钟）：只写 ledger 后 manifest crash reconciliation 红测**

新增`test_reconciles_ledger_after_manifest_crash`；模拟ledger commit后manifest identity-bound replace失败。第二次执行不得改已发布文件/ledger，只在prior manifest identity/bytes仍符合proof时补manifest；竞态修改manifest必须conflict并保留其bytes。

- [ ] **Step 10（2–5 分钟）：运行 manifest reconciliation command 并确认 RED**

~~~powershell
.\.venv\Scripts\python.exe -B -m unittest backend.tests.test_obsidian_exports_repository.ObsidianExportsRepositoryTests.test_reconciles_ledger_after_manifest_crash -v
~~~

Expected RED: second run重复ledger/file write或path-only覆盖竞态manifest。

- [ ] **Step 11a（2–5 分钟）：实现 manifest reconciliation**

只在bound prior manifest identity与expected hash匹配时replace；不存在manifest走true no-replace。

- [ ] **Step 11b（2–5 分钟）：运行相同 manifest reconciliation command 并确认 GREEN**

~~~powershell
.\.venv\Scripts\python.exe -B -m unittest backend.tests.test_obsidian_exports_repository.ObsidianExportsRepositoryTests.test_reconciles_ledger_after_manifest_crash -v
~~~

Expected GREEN: file/ledger无新mutation、manifest一项、竞态case保留。

- [ ] **Step 12（2–5 分钟）：写 missing-ledger orphan 非清理红测**

新增 <code>test_missing_live_ledger_can_never_authorize_stale_cleanup</code>；准备 manifest+marker/hash 完整但 Paper 已删除、ledger 已 cascade 的 entry。repository 返回 no proof，cleanup planner 只报告 orphaned=1；无 unlink、manifest drop 或 ledger recreation。

- [ ] **Step 13（2–5 分钟）：运行 orphan command 并确认 RED**

~~~powershell
.\.venv\Scripts\python.exe -B -m unittest backend.tests.test_obsidian_exports_repository.ObsidianExportsRepositoryTests.test_missing_live_ledger_can_never_authorize_stale_cleanup -v
~~~

Expected RED: planner仍把缺失ledger当作ownership，或调用manifest drop/delete/ledger recreate。

- [ ] **Step 14（2–5 分钟）：实现 typed cleanup proof query**

VaultProjectionRepository 只为 live paper/profile 且 target/hash 完整匹配的 row 返回 typed cleanup proof；absence 不是可恢复 ownership。普通 reconciliation 不为已删除 Paper 重建 ledger，manifest entry 原字节 carry-forward。

- [ ] **Step 15（2–5 分钟）：运行相同 orphan command 并确认 GREEN**

Run:

~~~powershell
.\.venv\Scripts\python.exe -B -m unittest backend.tests.test_obsidian_exports_repository.ObsidianExportsRepositoryTests.test_missing_live_ledger_can_never_authorize_stale_cleanup -v
~~~

Expected GREEN: orphan/tombstone 永久保留，自动 cleanup 数为 0。

## Task 6：实现 PDF none|reference|copy，并证明零隐式 OCR

**Files:**
- Create: backend/tests/test_obsidian_pdf_modes.py
- Modify: backend/app/application/obsidian_projection.py
- Modify: backend/app/providers/obsidian_vault.py
- Modify: backend/app/providers/pdf_files.py

- [ ] **Step 1（2–5 分钟）：写三模式红测**

新增 <code>ObsidianPdfModeTests.test_none_reference_copy_have_distinct_outputs</code>：none 无 PDF entry/link/open；reference 只写 percent-encoded file URI/link 且不复制；copy 只写 <code>Attachments/PDF/{paper_id}.pdf</code> 并产生 manifest ownership=managed、kind=pdf-copy entry 与 ledger row。三种模式均不得更新 papers.pdf_path。

- [ ] **Step 2（2–5 分钟）：确认三模式红测**

Run:

~~~powershell
.\.venv\Scripts\python.exe -B -m unittest backend.tests.test_obsidian_pdf_modes.ObsidianPdfModeTests.test_none_reference_copy_have_distinct_outputs -v
~~~

Expected RED: ExportOptions 尚不接受严格 pdf_mode 或 copy 路径缺失。

- [ ] **Step 3（2–5 分钟）：实现 none/reference**

none 完全不解析 PDF；reference 通过 PdfFiles 得到 validated path，使用 percent-encoded file URI 或稳定 Obsidian link，不读取 PDF bytes、不把绝对路径写入 manifest target_path。

- [ ] **Step 4（2–5 分钟）：实现 copy**

copy在paper-id/snapshot/Vault preflight全部通过后打开已containment验证的source descriptor，通过BoundVaultRoot parent handle/dirfd流式写exclusive temp，hash/flush/fsync后true no-replace publish并fsyncparent；缺文件返回pdf_missing。覆盖既有copy必须满足manifest+ledger+bound current identity/hash三证据并调用identity-bound replace；任何路径都不修改papers.pdf_path。

- [ ] **Step 5（2–5 分钟）：运行并确认三模式 GREEN**

Run:

~~~powershell
.\.venv\Scripts\python.exe -B -m unittest backend.tests.test_obsidian_pdf_modes.ObsidianPdfModeTests.test_none_reference_copy_have_distinct_outputs -v
~~~

Expected GREEN: 三模式 outputs 与 manifest 各自精确匹配。

- [ ] **Step 6（2–5 分钟）：写 no-OCR 红测**

新增 test_export_never_materializes_missing_source_or_calls_ocr，注入 SourceDocumentProcessor、ProcessingQueue.enqueue 与 OcrProvider spies；当 paper 只有 PDF 没有 ready source 时导出 paper metadata/PDF policy 可用部分并报告 source_unavailable。

- [ ] **Step 7（2–5 分钟）：确认 no-OCR 红测**

Run:

~~~powershell
.\.venv\Scripts\python.exe -B -m unittest backend.tests.test_obsidian_pdf_modes.ObsidianPdfModeTests.test_export_never_materializes_missing_source_or_calls_ocr -v
~~~

Expected RED: 若 exporter 调用了 materialize/enqueue/OCR，spy 以明确 AssertionError 失败。

- [ ] **Step 8（2–5 分钟）：移除隐式 materialization 并确认 GREEN**

Exporter 只调用 LibraryQueries.get_ready_source；缺失即记录 source_unavailable，三类 spy 调用数均保持 0。

Run:

~~~powershell
.\.venv\Scripts\python.exe -B -m unittest backend.tests.test_obsidian_pdf_modes.ObsidianPdfModeTests.test_export_never_materializes_missing_source_or_calls_ocr -v
~~~

Expected GREEN: test OK；OCR、materialize、enqueue 调用数均为 0。

- [ ] **Step 9（2–5 分钟）：写固定 PDF identity 与 no-writeback 红测**

新增 <code>test_copy_uses_validated_raw_paper_id_and_never_updates_pdf_path</code>。对 valid id 断言 target basename 精确等于 <code>{paper_id}.pdf</code>，title/rename/artifact变化不移动；对 unsafe/case-fold collision 断言在 source open 前失败。copy conflict、missing 与 success 后 papers.pdf_path 均为原值，原 PDF bytes 不变。

- [ ] **Step 10（2–5 分钟）：运行 PDF identity command 并确认 RED**

Run:

~~~powershell
.\.venv\Scripts\python.exe -B -m unittest backend.tests.test_obsidian_pdf_modes.ObsidianPdfModeTests.test_copy_uses_validated_raw_paper_id_and_never_updates_pdf_path -v
~~~

Expected RED: target仍来自title/sanitizer或普通export写回papers.pdf_path；unsafe id未在source open前拒绝也算RED。

- [ ] **Step 11（2–5 分钟）：复用 validator与BoundVaultRoot**

PDF target只由validated raw paper_id构造；source descriptor与bound target lifecycle分离，publish/replace只走BoundVaultRoot，普通export repository interface不暴露pdf_path update。

- [ ] **Step 12（2–5 分钟）：运行相同 PDF identity command 并确认 GREEN**

Run:

~~~powershell
.\.venv\Scripts\python.exe -B -m unittest backend.tests.test_obsidian_pdf_modes.ObsidianPdfModeTests.test_copy_uses_validated_raw_paper_id_and_never_updates_pdf_path -v
~~~

Expected GREEN: PDF target identity 只来自 raw validated paper_id；普通 export 零 DB path writeback、零源文件 mutation。

## Task 7：接入单篇/批量 Worker 与四条明确 v2 路由

**Files:**
- Create: backend/app/workers/obsidian.py
- Modify: backend/app/api/routes/obsidian.py
- Modify: backend/app/api/schemas/obsidian.py
- Modify: backend/app/api/router.py
- Modify: backend/app/api/errors.py
- Modify: backend/app/api/dependencies.py
- Modify: backend/app/application/obsidian_projection.py
- Modify: backend/tests/test_obsidian_jobs_api.py

- [ ] **Step 1（2–5 分钟）：写 POST enqueue 红测**

新增 <code>test_post_obsidian_enqueues_without_writing_vault</code>：调用 POST <code>/api/v2/papers/paper-1/exports/obsidian</code> 与 POST <code>/api/v2/obsidian/sync</code>；断言 HTTP 202、复用 P2 <code>JobResponse</code>、canonical job_type 分别为 <code>obsidian_export|obsidian_sync</code>，request返回前Vault/manifest/ledger写入spies全为0。直接读取临时DB，要求两row都有P2 schemaVersion=1 canonical `spec_json`，`progress_json`只有初始stage/counts，API response/log不含spec/settings/vaultPath。

- [ ] **Step 2（2–5 分钟）：确认 enqueue 红测**

Run:

~~~powershell
.\.venv\Scripts\python.exe -B -m unittest backend.tests.test_obsidian_jobs_api.ObsidianJobsApiTests.test_post_obsidian_enqueues_without_writing_vault -v
~~~

Expected RED: POST 尚未实现或 request path 内发生 Vault I/O。

- [ ] **Step 3（2–5 分钟）：实现严格 request schema 与幂等 enqueue**

<code>ObsidianExportRequest</code> 的 paper_id 只来自 path，body 只接受 <code>dryRun</code>；<code>ObsidianSyncRequest</code> 只接受 <code>dryRun</code>、<code>applyCleanup</code> 与 nullable <code>cleanupPlanSha</code>，unknown field 一律 422。Vault path、rootFolder 与 pdfMode 只能来自 effective Settings。<code>applyCleanup=true</code> 必须同时给 64 位 lowercase SHA；否则 422。Use case把flags、cleanup SHA、effective settings fingerprint+snapshot与content-safe LibraryQueries snapshot编码进P2 `spec_json`，先strict encode/secret scan再enqueue；key绑定spec SHA。相同snapshot的manual/auto并发调用返回同一P2 job。不得新增job enum、table、public status、payload column或queue。

- [ ] **Step 4（2–5 分钟）：运行并确认 enqueue GREEN**

Run:

~~~powershell
.\.venv\Scripts\python.exe -B -m unittest backend.tests.test_obsidian_jobs_api.ObsidianJobsApiTests.test_post_obsidian_enqueues_without_writing_vault -v
~~~

Expected GREEN: 两个 typed 202 response；Vault 仍为空，processing_jobs 恰有两个 canonical row。

- [ ] **Step 5（2–5 分钟）：只写 job spec 重启恢复红测**

新增`test_obsidian_job_spec_survives_restart_with_exact_options_and_snapshot`。分别enqueue `obsidian_export(dryRun=true)`与`obsidian_sync(dryRun=false,applyCleanup=true,cleanupPlanSha=<sha>)`，捕获raw spec bytes/SHA后关闭app/worker/engine；修改`progress_json`和current settings，再以同DB启动新worker。Worker必须从lease spec恢复原dryRun/applyCleanup/cleanupPlanSha/settingsFingerprint/settingsSnapshot/librarySnapshot，先重验DB snapshot hash，再对原bound Vault执行；不得读取progress补参。automatic retry/orphan recovery bytes不变，explicit retry descendant逐字节相同。Spec/log/API/event不含credential、Markdown/Note/PDF bytes或provider payload；tampered spec在Vault/ledger前`JOB_SPEC_INVALID`。

- [ ] **Step 6（2–5 分钟）：运行 job spec restart command 并确认 RED**

~~~powershell
.\.venv\Scripts\python.exe -B -m unittest backend.tests.test_obsidian_jobs_api.ObsidianJobsApiTests.test_obsidian_job_spec_survives_restart_with_exact_options_and_snapshot -v
~~~

Expected RED: handler仍依赖in-memory request/progress/current Settings，或spec未保存全部flags/snapshot；fixture必须真正dispose并recreateengine/worker。

- [ ] **Step 7（2–5 分钟）：实现 Obsidian JobSpec variants 与 restart dispatch**

在P2唯一JobSpec union注册两个strict variants；worker只消费`lease.spec.value`，重查LibraryQueries并比较冻结ids/hashes后调用Exporter。Current immutable `OBSIDIAN_ENABLED=0`仍是kill switch；启用时执行冻结non-secret settings snapshot。Mismatch返回`OBSIDIAN_SNAPSHOT_CHANGED`并零Vault write，用户重新enqueue得到新spec/key。

- [ ] **Step 8（2–5 分钟）：运行相同 job spec restart command 并确认 GREEN**

~~~powershell
.\.venv\Scripts\python.exe -B -m unittest backend.tests.test_obsidian_jobs_api.ObsidianJobsApiTests.test_obsidian_job_spec_survives_restart_with_exact_options_and_snapshot -v
~~~

Expected GREEN: restart/retry/recovery raw bytes与SHA精确相等，原options/snapshot生效，tamper与snapshot drift均在首次Vault mutation前fail closed。

- [ ] **Step 9（2–5 分钟）：写 status/test route 红测**

新增 <code>test_status_and_test_routes_do_not_enqueue_or_project</code>：GET <code>/api/v2/obsidian/status</code> 只返回 enabled、vaultConfigured、writable、rootFolder、pdfMode、lastJob 与八项安全 aggregate；POST <code>/api/v2/obsidian/test</code> 只做 exclusive write probe。响应不得含 resolved absolute Vault path；processing_jobs、obsidian_exports 与 probe 外的 Vault bytes 均不变。

- [ ] **Step 10（2–5 分钟）：运行 status/test command 并确认 RED**

Run:

~~~powershell
.\.venv\Scripts\python.exe -B -m unittest backend.tests.test_obsidian_jobs_api.ObsidianJobsApiTests.test_status_and_test_routes_do_not_enqueue_or_project -v
~~~

Expected RED: status/test route缺失，或test不经BoundVaultRoot probe且产生job/projection mutation。

- [ ] **Step 11（2–5 分钟）：实现 status/test routes**

<code>routes/obsidian.py</code>只读status与调用<code>VaultWriter.test_access</code>；probe经BoundVaultRoot true no-replace创建并按exact identity cleanup。router只挂载一次，继续复用现有error mapper。

- [ ] **Step 12（2–5 分钟）：运行相同 status/test command 并确认 GREEN**

~~~powershell
.\.venv\Scripts\python.exe -B -m unittest backend.tests.test_obsidian_jobs_api.ObsidianJobsApiTests.test_status_and_test_routes_do_not_enqueue_or_project -v
~~~

Expected GREEN: exact safe DTO通过，probe外Vaultbytes、processing_jobs与ledger全等。

- [ ] **Step 13（2–5 分钟）：写 Worker terminal summary truth-table 红测**

新增单一table-driven `test_worker_terminal_summary_truth_table`，以subTest覆盖partial conflict、empty snapshot与all-error三行。第一行两篇exported/一篇conflict→succeeded；第二行八项0→succeeded；第三行errors=3且七个non-error全0→failed。每行`result_json`恰含exported、unchanged、conflicts、errors、skipped、userManaged、orphaned、deleted八个非负整数，无额外key；progress仍不含request spec。

- [ ] **Step 14（2–5 分钟）：运行 Worker truth-table command 并确认 RED**

Run:

~~~powershell
.\.venv\Scripts\python.exe -B -m unittest backend.tests.test_obsidian_jobs_api.ObsidianJobsApiTests.test_worker_terminal_summary_truth_table -v
~~~

Expected RED: handler 尚未注册或 terminal/count contract 不匹配。

- [ ] **Step 15（2–5 分钟）：实现 P2 handler 与结果归并**

handler从strict decoded `lease.spec`读取并核验immutable snapshot/options，按paper_id code-point顺序调用exporter；单项异常归一化为error后继续，conflict、user-managed与orphan都是分类结果而不是新job status。每项结束后用P2 progress/event interface只写同一组safe counts；取消只在当前BoundVaultRoot原子publish完成后停止，复用P2 cancel settle。存在任一non-error分类时complete为succeeded；空snapshot也以八项零count complete为succeeded；只有至少一个error且全部item均error才fail。

- [ ] **Step 16（2–5 分钟）：运行相同 Worker truth-table command 并确认 GREEN**

Run:

~~~powershell
.\.venv\Scripts\python.exe -B -m unittest backend.tests.test_obsidian_jobs_api.ObsidianJobsApiTests.test_worker_terminal_summary_truth_table -v
~~~

Expected GREEN: partial case 精确为 exported=2、conflicts=1、errors=0；empty case 八项全零且 succeeded；fatal case errors=3 且 failed；用户文件不变，无新 public status。

## Task 8：锁定稳定路径、DB-only 删除、显式清理、重建与运行时回滚

**Files:**
- Create: backend/tests/test_obsidian_rebuild.py
- Create: backend/tests/test_obsidian_paper_delete.py
- Modify: backend/app/application/obsidian_projection.py
- Modify: backend/app/providers/obsidian_vault.py
- Verify only: backend/app/application/paper_library.py
- Modify: docs/DATABASE.md

- [ ] **Step 1（2–5 分钟）：写同路径改名与 stale artifact 红测**

新增 <code>test_title_change_updates_managed_bytes_without_moving_paths</code>。准备 <code>Papers/paper-1.md</code>、四类关联文件、已由用户修改的 <code>Notes/paper-1.md</code> 和无关 sentinel；改 title 并让一个 GeneratedArtifact 不再 eligible。断言所有 identity 仍是 <code>{paper_id}</code> 路径，managed Paper frontmatter/body 在原路径更新，零 rename/move，Notes bytes 不变；stale artifact 只进入 cleanup preview，不自动删除。

- [ ] **Step 2（2–5 分钟）：运行 stable-path command 并确认 RED**

Run:

~~~powershell
.\.venv\Scripts\python.exe -B -m unittest backend.tests.test_obsidian_rebuild.ObsidianRebuildTests.test_title_change_updates_managed_bytes_without_moving_paths -v
~~~

Expected RED: 至少一条path来自title/artifact，或stale file被自动delete/Notes被rewrite；fixture必须先有真实旧projection。

- [ ] **Step 3（2–5 分钟）：实现 stable path 与 preview-only stale policy**

Projection identity完全由validated paper_id决定；title只改变canonical managed bytes。Stale candidate只生成cleanup preview，Notes始终user-managed；更新managed file只走BoundVaultRoot expected identity replace。

- [ ] **Step 4（2–5 分钟）：运行相同 stable-path command 并确认 GREEN**

~~~powershell
.\.venv\Scripts\python.exe -B -m unittest backend.tests.test_obsidian_rebuild.ObsidianRebuildTests.test_title_change_updates_managed_bytes_without_moving_paths -v
~~~

Expected GREEN: paths全等、managed bytes按预期更新、rename/delete spies为0、Notes与stale bytes不变。

- [ ] **Step 5（2–5 分钟）：写 Paper delete 零 Vault I/O characterization test**

新增 <code>test_paper_delete_cascades_only_ledger_and_leaves_vault_orphan</code>：通过 P4 PaperLibrary 删除已有 projection 的 Paper，断言 Paper 与 obsidian_exports 由 SQLite cascade 删除，而 Vault adapter 构造、list/read/write/replace/unlink/rename、manifest writer 与 worker enqueue spies 全为 0；Vault tree 和 manifest bytes 全等。后续 status 将旧 entry 只读归类为 orphan/tombstone，sync/rebuild carry-forward 原 entry，永不自动 cleanup。

- [ ] **Step 6（2–5 分钟）：运行并确认 DB-only delete GREEN**

Run:

~~~powershell
.\.venv\Scripts\python.exe -B -m unittest backend.tests.test_obsidian_paper_delete.ObsidianPaperDeleteTests.test_paper_delete_cascades_only_ledger_and_leaves_vault_orphan -v
~~~

Expected: test OK；如果需要修改 P4 PaperLibrary 才能达成则停止并回到 P4 修正，P5 不给 delete route 加 Vault callback。

- [ ] **Step 7（2–5 分钟）：写 cleanup preview/confirm/race 红测**

新增 <code>test_cleanup_requires_matching_plan_sha_and_three_proofs</code>。dry-run对stale managed candidates生成canonical CleanupPlan与lowercase SHA，零文件/manifest/ledger mutation；applyCleanup没有SHA、SHA不匹配或plan重算变化均拒绝。有效apply仍逐项通过BoundVaultRoot重验root/parent/final identity、manifest ownership=managed、当前profile ledger row、Markdown marker或PDF exported hash；race改动变conflict。Notes与orphan/tombstone永远不进入deletable集合。enqueue的`spec_json`必须原样保存dryRun/applyCleanup/cleanupPlanSha/settings fingerprint/snapshot，重启后不得从progress或current request恢复。

- [ ] **Step 8（2–5 分钟）：运行 cleanup command 并确认 RED**

Run:

~~~powershell
.\.venv\Scripts\python.exe -B -m unittest backend.tests.test_obsidian_rebuild.ObsidianRebuildTests.test_cleanup_requires_matching_plan_sha_and_three_proofs -v
~~~

Expected RED: preview/apply确认门、spec持久化或BoundVaultRoot exact-identity delete至少一项缺失；barrier未交换target不算race RED。

- [ ] **Step 9（2–5 分钟）：实现显式 cleanup**

Planner生成canonical plan/SHA；apply只消费strict lease spec并重算同一plan，三证据+exact bound identity成功后调用`delete_managed`，随后identity-bound更新manifest/ledger。任一item变化只计conflict且保留bytes，不做部分猜测。

- [ ] **Step 10（2–5 分钟）：运行相同 cleanup command 并确认 GREEN**

Run:

~~~powershell
.\.venv\Scripts\python.exe -B -m unittest backend.tests.test_obsidian_rebuild.ObsidianRebuildTests.test_cleanup_requires_matching_plan_sha_and_three_proofs -v
~~~

Expected GREEN: 只有 plan SHA 一致且 apply-time 三证据仍成立的 stale managed file 被删；结果 deleted 精确，所有保留项计 conflict/userManaged/orphaned，sentinel 不变。

- [ ] **Step 11（2–5 分钟）：写全量/增量等价与 orphan carry-forward 红测**

新增 <code>test_empty_vault_rebuild_matches_incremental_managed_hashes</code> 与 <code>test_rebuild_carries_forward_orphan_and_user_note_entries</code>。同一 DB snapshot 分别执行空 Vault rebuild 与多轮 incremental projection，忽略 manifest generatedAt 后比较非 orphan managed entries 与文件 SHA；existing Vault rebuild 必须原字节 carry-forward orphan entry 与 user-owned Note entry，绝不据 DB 缺失清除。

- [ ] **Step 12（2–5 分钟）：运行 rebuild command 并确认 RED**

Run:

~~~powershell
.\.venv\Scripts\python.exe -B -m unittest backend.tests.test_obsidian_rebuild.ObsidianRebuildTests.test_empty_vault_rebuild_matches_incremental_managed_hashes backend.tests.test_obsidian_rebuild.ObsidianRebuildTests.test_rebuild_carries_forward_orphan_and_user_note_entries -v
~~~

Expected RED: full/incremental managed hash不等，或orphan/user Note entry/bytes被drop/rewrite；fixture空Vault与existingVault必须独立。

- [ ] **Step 13（2–5 分钟）：修复确定性 projection/manifest merge**

统一排序、renderer与BoundVaultRoot publish/replace路径；rebuild沿用同一Exporter，不建立删除捷径，manifest merge原样carry-forward orphan/user entries。

- [ ] **Step 14（2–5 分钟）：运行相同 rebuild command 并确认 GREEN**

Run:

~~~powershell
.\.venv\Scripts\python.exe -B -m unittest backend.tests.test_obsidian_rebuild.ObsidianRebuildTests.test_empty_vault_rebuild_matches_incremental_managed_hashes backend.tests.test_obsidian_rebuild.ObsidianRebuildTests.test_rebuild_carries_forward_orphan_and_user_note_entries -v
~~~

Expected GREEN: stable path、canonical ordering 与 managed hashes 一致；orphan/user-owned bytes 和 entries 原样保留。

- [ ] **Step 15（2–5 分钟）：写并演练运行时回滚**

在<code>docs/DATABASE.md</code>固定：设置<code>OBSIDIAN_ENABLED=0</code>→停止obsidian worker claim→等待当前BoundVaultRoot原子operation/ledger transaction/intent checkpoint settle→停止projector role。保留queued job的canonical `spec_json`、Vault、manifest、obsidian_exports与全部MigrationIntent/sealed receipt；不downgrade、不删用户文件。恢复服务后job从原spec重启；PDF migration只用exact intent path+最新SHA显式resume或rollback。清理只能先取得managed-only dry-run plan，再以相同plan SHA单独授权；orphan/tombstone不可授权清理。

## Task 9：接入 ready GeneratedArtifact 的 after-commit 自动导出

**Files:**
- Create: backend/app/application/ports/obsidian_auto_export.py
- Create: backend/app/application/obsidian_auto_export.py
- Create: backend/tests/test_obsidian_auto_export.py
- Modify: backend/app/application/generated_artifacts.py
- Modify: backend/app/application/document_artifacts.py
- Modify: backend/app/api/dependencies.py
- Create: backend/app/workers/runtime.py

- [ ] **Step 1（2–5 分钟）：写 after-commit 与 generation isolation 红测**

新增 <code>test_artifact_ready_notifies_only_after_commit</code> 与 <code>test_enqueue_failure_never_changes_generation_success</code>。rollback 路径 port 调用数为 0；ready artifact UoW commit 后才调用 <code>ObsidianAutoExportPort.on_artifact_ready</code>。port 抛错时 artifact 与原 generation job 仍 ready/succeeded，事务不回滚、不重试模型、不修改正文。

- [ ] **Step 2（2–5 分钟）：确认 hook RED**

Run:

~~~powershell
.\.venv\Scripts\python.exe -B -m unittest backend.tests.test_obsidian_auto_export.ObsidianAutoExportTests.test_artifact_ready_notifies_only_after_commit backend.tests.test_obsidian_auto_export.ObsidianAutoExportTests.test_enqueue_failure_never_changes_generation_success -v
~~~

Expected RED: port/policy 不存在或通知发生在 commit 前。

- [ ] **Step 3（2–5 分钟）：实现最小 after-commit port**

interface 只有 <code>on_artifact_ready(paper_id, artifact_id, committed_at)</code>；production adapter 捕获并记录安全 telemetry，no-op/fake adapter 共享同一 interface。生成 use case 只在 ready publish 的 UoW 成功返回后通知；通知结果不参与 artifact/job settle。该 port 不接收 Markdown/PDF bytes，也不能调用 materialize_source 或 OCR。

- [ ] **Step 4（2–5 分钟）：运行相同 after-commit command 并确认 GREEN**

~~~powershell
.\.venv\Scripts\python.exe -B -m unittest backend.tests.test_obsidian_auto_export.ObsidianAutoExportTests.test_artifact_ready_notifies_only_after_commit backend.tests.test_obsidian_auto_export.ObsidianAutoExportTests.test_enqueue_failure_never_changes_generation_success -v
~~~

Expected GREEN: 两个tests `OK`；rollback零通知，enqueue failure不改变artifact/generation job。

- [ ] **Step 5（2–5 分钟）：写 default-off/debounce/idempotency 红测**

新增 <code>test_default_off_has_zero_hook_queue_materialization_and_ocr</code> 与 <code>test_auto_export_is_optional_coalesced_and_idempotent_per_paper</code>：未配置时 enabled=false、auto_export=false，ready artifact commit后production hook、queue、materialize与OCR spies全0；任一flag=false也保持全0。两项都为true时，hook只读取该artifact已绑定的ready SourceDocument；同一Paper在debounce窗口内多个ready artifact event合并为最新immutable snapshot的一个<code>obsidian_export</code>，其canonical `spec_json`保存settings fingerprint/snapshot、latest library ids/hashes及`dryRun=false,applyCleanup=false,cleanupPlanSha=null`。窗口外重复相同snapshot仍由P2 spec-bound idempotency返回同一job。不同Paper不互相阻塞。

- [ ] **Step 6（2–5 分钟）：运行 auto-export policy command 并确认 RED**

Run:

~~~powershell
.\.venv\Scripts\python.exe -B -m unittest backend.tests.test_obsidian_auto_export.ObsidianAutoExportTests.test_default_off_has_zero_hook_queue_materialization_and_ocr backend.tests.test_obsidian_auto_export.ObsidianAutoExportTests.test_auto_export_is_optional_coalesced_and_idempotent_per_paper -v
~~~

Expected RED: default-off仍触发side effect、coalescing缺失或job spec未持久化完整snapshot；fake clock/flags未命中真实policy不算RED。

- [ ] **Step 7（2–5 分钟）：实现 per-Paper coalescing**

用bounded in-memory debounce只合并commit notifications；真正持久性来自P2 enqueue。Flush时构造与manual export完全相同的canonical JobSpec/idempotency，先读取non-secret settings与content-safe snapshot；失败只记录安全telemetry。

- [ ] **Step 8（2–5 分钟）：运行相同 auto-export policy command 并确认 GREEN**

~~~powershell
.\.venv\Scripts\python.exe -B -m unittest backend.tests.test_obsidian_auto_export.ObsidianAutoExportTests.test_default_off_has_zero_hook_queue_materialization_and_ocr backend.tests.test_obsidian_auto_export.ObsidianAutoExportTests.test_auto_export_is_optional_coalesced_and_idempotent_per_paper -v
~~~

Expected GREEN: default/disabled cases的hook/queue/materialize/OCR全0；enabled fake clock下每个Paper精确一次canonical spec enqueue。只注册P2 obsidian_export，不生成SourceDocument，不创建第二套scheduler或持久表。

- [ ] **Step 9（2–5 分钟）：写启动补偿与 zero-generation 红测**

新增 <code>test_startup_reconciliation_enqueues_missing_latest_snapshots_without_generating</code>。模拟 commit 后进程崩溃导致 hook 丢失；启动 reconciliation 只读 ready GeneratedArtifact、ledger 与 P2 jobs，为缺失的最新 Paper snapshot enqueue 一次；已有 equivalent queued/running/succeeded job 不重复。SourceDocumentProcessor、ArtifactGenerator、OcrProvider、VaultWriter spies 全 0。

- [ ] **Step 10（2–5 分钟）：运行 startup reconciliation command 并确认 RED**

Run:

~~~powershell
.\.venv\Scripts\python.exe -B -m unittest backend.tests.test_obsidian_auto_export.ObsidianAutoExportTests.test_startup_reconciliation_enqueues_missing_latest_snapshots_without_generating -v
~~~

Expected RED: startup reconciliation缺失、重复enqueue或触发任一generation/materialize/OCR/Vault spy。

- [ ] **Step 11（2–5 分钟）：实现 bounded startup reconciliation**

按paper-id bounded batches只读ready artifact/ledger/jobs；对缺失snapshot调用同一auto-export JobSpec builder与P2 enqueue。已有active/terminal同spec SHA job视为已补偿；不得直接调用Exporter。

- [ ] **Step 12（2–5 分钟）：运行相同 startup reconciliation command 并确认 GREEN**

~~~powershell
.\.venv\Scripts\python.exe -B -m unittest backend.tests.test_obsidian_auto_export.ObsidianAutoExportTests.test_startup_reconciliation_enqueues_missing_latest_snapshots_without_generating -v
~~~

Expected GREEN: deterministic paper-id batches完成补偿；重启重复运行仍幂等，生成成功永不受enqueue failure影响，四类forbidden spies全0。

## Task 10：实现显式 PDF migration plan/apply/rollback CLI

CLI surface固定为四个operator-only subcommands：`plan`（只读stdout）、`prepare --confirm-plan-sha <sha> --intent-output <exact-new-path>`（只发布intent，不触碰Vault/DB）、`apply --intent <exact-path> --confirm-intent-sha <sha>`（首次apply与crash recovery共用）、`rollback --intent <exact-path> --confirm-intent-sha <sha>`。因此每一次真正apply/recovery/rollback都接收并验证exact intent path、当前file identity与current SHA；不存在没有intent的apply捷径，也不存在HTTP route。

**Files:**
- Create: backend/app/application/obsidian_pdf_migration.py
- Create: backend/app/cli/obsidian_pdf_migration.py
- Create: backend/tests/test_obsidian_pdf_migration.py
- Modify: backend/app/infrastructure/bound_vault_root.py
- Modify: backend/app/providers/pdf_files.py
- Modify: backend/app/repositories/obsidian_exports.py
- Modify: docs/DATABASE.md

- [ ] **Step 1（2–5 分钟）：只写 canonical plan/dry-run 红测**

新增`test_plan_is_canonical_and_dry_run_has_zero_side_effects`。Plan的sorted `items[]`逐篇记录paperId、old papers.pdf_path、validated source path/hash、固定target relative path、target prior existence/identity/ownership/hash、prior ledger row/hash与prior manifest entry/hash；输出canonical JSON与lowercase plan SHA。重复运行bytes/SHA一致。copy/temp/replace/delete、papers update、ledger/manifest/intent、OCR/materialize spies全部0。

- [ ] **Step 2（2–5 分钟）：运行 plan command 并确认 RED**

~~~powershell
.\.venv\Scripts\python.exe -B -m unittest backend.tests.test_obsidian_pdf_migration.ObsidianPdfMigrationTests.test_plan_is_canonical_and_dry_run_has_zero_side_effects -v
~~~

Expected RED: migration planner/CLI不存在或plan产生任一side effect；fixture必须至少含两篇乱序paper以证明sorting。

- [ ] **Step 3（2–5 分钟）：实现 canonical plan**

CLI `plan`是唯一dry-run operation，只向stdout写canonical JSON；Library/Settings/Vault inspection全只读，不创建output directory。Plan schemaVersion=1，items按`(paperId,targetPath)` code-point排序，绝不含PDF bytes、credential或absolute Vault target；sourcePath是operator内部字段，CLI日志/error不回显。

- [ ] **Step 4（2–5 分钟）：运行相同 plan command 并确认 GREEN**

~~~powershell
.\.venv\Scripts\python.exe -B -m unittest backend.tests.test_obsidian_pdf_migration.ObsidianPdfMigrationTests.test_plan_is_canonical_and_dry_run_has_zero_side_effects -v
~~~

Expected GREEN: canonical bytes/SHA稳定且全部mutation spies为0。

- [ ] **Step 5（2–5 分钟）：重放 Settings-save isolation characterization**

运行已在Task 1落地的单一test；它必须先于任何apply实现保持GREEN：

~~~powershell
.\.venv\Scripts\python.exe -B -m unittest backend.tests.test_obsidian_settings.ObsidianSettingsTests.test_obsidian_save_preserves_credentials_unknown_fields_and_never_moves_pdf -v
~~~

Expected GREEN: Settings save不调用PdfMigration/BoundVaultRoot/queue，不搬文件、不改papers.pdf_path。

- [ ] **Step 6（2–5 分钟）：只写 pre-mutation exclusive MigrationIntent 红测**

新增`test_prepare_publishes_exclusive_fsynced_intent_before_any_app_mutation`。调用`prepare --confirm-plan-sha <sha> --intent-output <exact-new-path>`；barrier分别放在intent exclusive open前、首次write前、flush前、file fsync前、parent fsync前。任何失败时Vault/DB/ledger/manifest mutation均0；竞争者预建intent时不得覆盖/删除。成功时完整可解析intent已经durable，prepare本身仍无app mutation，CLI返回exact path与当前file SHA；随后apply test只能把这两个返回值作为参数。

- [ ] **Step 7（2–5 分钟）：运行 intent publication command 并确认 RED**

~~~powershell
.\.venv\Scripts\python.exe -B -m unittest backend.tests.test_obsidian_pdf_migration.ObsidianPdfMigrationTests.test_prepare_publishes_exclusive_fsynced_intent_before_any_app_mutation -v
~~~

Expected RED: prepare/intent store不存在、intent使用overwrite式write或prepare触发app mutation；hostile spy必须证明durability边界。

- [ ] **Step 8（2–5 分钟）：实现 MigrationIntentStore 与固定 schema**

Intent output root先以P0同级安全语义绑定；以`O_EXCL|O_NOFOLLOW`/Windows`CREATE_NEW`打开exact path，立即捕获file identity，再写canonical bytes、flush/fsync并fsync bound parent。失败cleanup只删除本次owned exact identity。Initial schema固定：`schemaVersion=1`、`planSha256`、`settingsFingerprint`、`state=prepared`、`createdAt`、`updatedAt`、`items`、`receipt=null`。排序items[]每项固定含`sequence,paperId,source,target,prior,expectedPost,phase,checkpoints`；`prior`完整保存DB value、ledger canonical row/hash、manifest entry/hash、target existence/identity/ownership/hash，phase初值`prepared`。不得只保存最后一篇或共享mutable prior state。

- [ ] **Step 9（2–5 分钟）：运行相同 intent publication command 并确认 GREEN**

~~~powershell
.\.venv\Scripts\python.exe -B -m unittest backend.tests.test_obsidian_pdf_migration.ObsidianPdfMigrationTests.test_prepare_publishes_exclusive_fsynced_intent_before_any_app_mutation -v
~~~

Expected GREEN: every failure boundary zero app mutation，success intent durable/canonical且竞争文件不变。

- [ ] **Step 10（2–5 分钟）：只写 apply ordering/checkpoint 红测**

新增`test_apply_copies_then_checkpoints_db_ledger_and_manifest_in_order`。每篇严格执行：BoundVaultRoot stream temp/hash/fsync→true no-replace或proved managed replace→verify target→CAS papers.pdf_path→ledger upsert→identity-bound manifest merge。每一成功边界后先atomic rewrite+fsync同一intent，再进入下一边界；phase依次`target_published|db_updated|ledger_updated|manifest_updated|item_sealed`。原PDF永远存在且bytes不变，target只含paper_id。

- [ ] **Step 11（2–5 分钟）：运行 apply ordering command 并确认 RED**

~~~powershell
.\.venv\Scripts\python.exe -B -m unittest backend.tests.test_obsidian_pdf_migration.ObsidianPdfMigrationTests.test_apply_copies_then_checkpoints_db_ledger_and_manifest_in_order -v
~~~

Expected RED: apply/phase checkpoints缺失、顺序错误或任一Vault mutation绕过BoundVaultRoot。

- [ ] **Step 12（2–5 分钟）：实现 apply state machine**

Apply首先要求`--intent` exact path/identity与`--confirm-intent-sha`匹配，strict decode frozen plan，再重算source/target/DB/ledger/manifest；任何变化在首次Vault/DB mutation前拒绝。每项只凭intent frozen expected/prior state执行，checkpoint通过bound intent identity的temp+fsync+identity-bound replace发布并返回新SHA。每次UoW短事务CAS；不把file I/O放进SQLite transaction。CLI每次成功/分类失败都返回exact intent path与最新SHA，不新增HTTP route/table/settings side effect。

- [ ] **Step 13（2–5 分钟）：运行相同 apply ordering command 并确认 GREEN**

~~~powershell
.\.venv\Scripts\python.exe -B -m unittest backend.tests.test_obsidian_pdf_migration.ObsidianPdfMigrationTests.test_apply_copies_then_checkpoints_db_ledger_and_manifest_in_order -v
~~~

Expected GREEN: boundary order/phase/hash精确，source不变，target/DB/ledger/manifest与intent expectedPost一致。

- [ ] **Step 14（2–5 分钟）：只写批次 crash/recovery 红测**

新增单一table-driven`test_recovery_resumes_each_batch_boundary_from_exact_intent`。至少三篇，逐subTest注入：第一篇target后crash、第二篇DB CAS后但checkpoint前crash、第二篇ledger后crash、manifest后crash、items之间crash。Recovery只接受`apply --intent <exact-path> --confirm-intent-sha <current-sha>`；wrong/missing/path-alias/SHA mismatch在任何write前拒绝。DB已更新但checkpoint未写时，通过prior/expected post CAS/hash唯一判定并补checkpoint；既非prior也非post则conflict。已成功item不得recopy/rebill/rewrite。

- [ ] **Step 15（2–5 分钟）：运行 crash/recovery command 并确认 RED**

~~~powershell
.\.venv\Scripts\python.exe -B -m unittest backend.tests.test_obsidian_pdf_migration.ObsidianPdfMigrationTests.test_recovery_resumes_each_batch_boundary_from_exact_intent -v
~~~

Expected RED: recovery依赖内存/plan重建、重复已完成item、不能识别DB-update-before-checkpoint或接受非exact intent identity/SHA。

- [ ] **Step 16（2–5 分钟）：实现 evidence-based recovery**

Strict decode intent并验证canonical bytes/path identity/SHA/plan/settings；按sequence读取每项phase，再比较source、bound target、DB、ledger、manifest的prior/expectedPost hashes。恰好匹配一个合法边界时先补缺checkpoint再继续；ambiguous/diverged state停止整个batch且不修改后续item。Automatic recovery不创建新intent，重复调用sealed/complete phase为no-op。

- [ ] **Step 17（2–5 分钟）：运行相同 crash/recovery command 并确认 GREEN**

~~~powershell
.\.venv\Scripts\python.exe -B -m unittest backend.tests.test_obsidian_pdf_migration.ObsidianPdfMigrationTests.test_recovery_resumes_each_batch_boundary_from_exact_intent -v
~~~

Expected GREEN: 所有boundary幂等恢复，三篇各一次target/DB/ledger/manifest结果；wrong intent/SHA与divergence zero mutation。

- [ ] **Step 18（2–5 分钟）：只写 completion seal 红测**

新增`test_completed_intent_is_sealed_as_verifiable_receipt`。最后item checkpoint后，intent必须atomic rewrite为`state=sealed`，所有items phase=`item_sealed`，receipt固定含sealedAt、planSha256、ordered final item hashes、finalDbHash、finalLedgerHash、finalManifestHash与sourcePreserved=true。CLI返回同一exact path与sealed file SHA；再次apply exact path/SHA为no-op。不得创建第二个receipt文件。

- [ ] **Step 19（2–5 分钟）：运行 seal command 并确认 RED**

~~~powershell
.\.venv\Scripts\python.exe -B -m unittest backend.tests.test_obsidian_pdf_migration.ObsidianPdfMigrationTests.test_completed_intent_is_sealed_as_verifiable_receipt -v
~~~

Expected RED: completion未seal、receipt另写导致lineage断裂或final hashes不全。

- [ ] **Step 20a（2–5 分钟）：实现 completion seal**

在全部item evidence复验后以同一BoundIntentStore checkpoint写receipt/state，fsync file+parent，再独立读取/hash并返回。

- [ ] **Step 20b（2–5 分钟）：运行相同 seal command 并确认 GREEN**

~~~powershell
.\.venv\Scripts\python.exe -B -m unittest backend.tests.test_obsidian_pdf_migration.ObsidianPdfMigrationTests.test_completed_intent_is_sealed_as_verifiable_receipt -v
~~~

Expected GREEN: 单文件sealed receipt可验证且reapply零mutation。

- [ ] **Step 21（2–5 分钟）：只写 exact-intent rollback/tamper 红测**

新增`test_rollback_uses_exact_intent_and_restores_batch_prior_state_idempotently`。Rollback命令固定为`rollback --intent <exact-path> --confirm-intent-sha <sha>`，覆盖sealed batch与partially-applied batch。按items逆序、每边界checkpoint，只有DB/ledger/manifest/current target仍等于intent expectedPost/owned identity才恢复prior。只删除本migration首次创建且bytes/ownership/identity未变的copy；复用existing target恢复其prior bytes，用户/竞态/unowned target永不删除。覆盖intent篡改/缺失/alias/wrong SHA、target改动、DB-update-after-apply、batch中途rollback crash与重复rollback。

- [ ] **Step 22（2–5 分钟）：运行 rollback command 并确认 RED**

~~~powershell
.\.venv\Scripts\python.exe -B -m unittest backend.tests.test_obsidian_pdf_migration.ObsidianPdfMigrationTests.test_rollback_uses_exact_intent_and_restores_batch_prior_state_idempotently -v
~~~

Expected RED: rollback仍依赖旧receipt shape、不能恢复batch prior states、删除竞态文件或中途crash后不幂等。

- [ ] **Step 23（2–5 分钟）：实现 checkpointed rollback**

先strict verify exact intent identity/SHA；若phase evidence落在mutation与checkpoint之间，复用recovery判定再开始逆序rollback。每个reverse boundary都CAS/exact-identity操作并checkpoint`rolling_back` phases，最终seal`state=rolled_back`与prior aggregate hashes。任何item conflict停止且不触碰尚未回滚item；原source永不删除。

- [ ] **Step 24（2–5 分钟）：运行相同 rollback command 并确认 GREEN**

~~~powershell
.\.venv\Scripts\python.exe -B -m unittest backend.tests.test_obsidian_pdf_migration.ObsidianPdfMigrationTests.test_rollback_uses_exact_intent_and_restores_batch_prior_state_idempotently -v
~~~

Expected GREEN: sealed/partial/mid-rollback全部安全恢复或typed conflict；prior DB/ledger/manifest/target hashes逐项全等，tamper/wrong identity/SHA zero destructive writes。

## Task 11：最小 React 类型、Gateway、Query Hook 与现有控件接线

**Files:**
- Modify: frontend/src/lib/api/types.ts
- Modify: frontend/src/lib/api/decoders.ts
- Modify: frontend/src/lib/api/keys.ts
- Create: frontend/src/lib/api/obsidianGateway.ts
- Create: frontend/src/lib/api/obsidianGateway.test.ts
- Create: frontend/src/features/obsidian/useObsidianProjection.ts
- Create: frontend/src/features/obsidian/useObsidianProjection.test.tsx
- Modify: frontend/src/features/settings/settingsForm.ts
- Modify: frontend/src/features/settings/SettingsRoute.tsx
- Modify: frontend/src/features/settings/SettingsRoute.test.tsx
- Modify: frontend/src/features/dashboard/PaperInspector.tsx
- Modify: frontend/src/features/dashboard/PaperInspector.test.tsx
- Modify: frontend/src/features/dashboard/DashboardRoute.tsx
- Create: frontend/src/test/fixtures/obsidian.ts
- Verify only: backend/app/cli/runtime_owner.py
- Verify only: backend/app/cli/schema_inventory.py

- [ ] **Step 1（2–5 分钟）：写 strict decoder/Gateway 红测**

新增 exact fixtures 与 <code>obsidianGateway.test.ts</code>：status/test/JobResponse/result counts 的正确 wire 通过，missing/wrong/unknown field fail closed；Gateway 只请求四条固定 v2 path，method/body 精确，paper id 使用 path-segment encoding，永不接受/发送 Vault absolute path。settings 继续走现有 Gateway，不在 Obsidian Gateway 新增 settings route。

- [ ] **Step 2（2–5 分钟）：确认 decoder/Gateway RED**

Run:

~~~powershell
npm.cmd --prefix frontend run test:run -- src/lib/api/obsidianGateway.test.ts
~~~

Expected RED: types、decoder 或 Gateway 不存在。

- [ ] **Step 3（2–5 分钟）：实现 types/decoder/keys/Gateway 并确认 GREEN**

在既有 files 添加八项 Settings fields、ObsidianStatus/TestResult/CleanupRequest 与八项 result count types；decoder 使用现有 primitives 严格拒绝 unknown/missing key。query key 固定按 status/global/paper scope；Gateway 复用 ApiClient、现有 error 与 P2 JobResponse decoder，不复制 transport/polling。

Run:

~~~powershell
npm.cmd --prefix frontend run test:run -- src/lib/api/obsidianGateway.test.ts
~~~

Expected GREEN: exact wire fixtures 全绿，无 generic Obsidian path。

- [ ] **Step 4（2–5 分钟）：写 Query Hook 生命周期红测**

新增 <code>useObsidianProjection.test.tsx</code>，覆盖 status query、test/export/sync mutations、成功后精确 invalidation、P2 job polling handoff、并发 mutation去重和 error envelope；切 Paper/unmount 只 detach observer，不 cancel server job、不 reload 页面。

- [ ] **Step 5（2–5 分钟）：运行 Query Hook command 并确认 RED**

Run:

~~~powershell
npm.cmd --prefix frontend run test:run -- src/features/obsidian/useObsidianProjection.test.tsx
~~~

Expected RED: Hook不存在或至少一个lifecycle/invalidation assertion失败；test import/fixture错误不算RED。

- [ ] **Step 6（2–5 分钟）：实现最小 Hook**

Hook只组合既有Gateway/QueryClient/P2 job polling；mutation key按paper/global scope，unmount只取消observer subscription。不得新增视觉state、raw fetch、window.location、interval全量刷新或隐式cancel。

- [ ] **Step 7（2–5 分钟）：运行相同 Query Hook command 并确认 GREEN**

~~~powershell
npm.cmd --prefix frontend run test:run -- src/features/obsidian/useObsidianProjection.test.tsx
~~~

Expected GREEN: Hook只经Gateway/QueryClient工作；detach/invalidation/error行为精确通过。

- [ ] **Step 8（2–5 分钟）：写现有 Settings/PaperInspector 接线红测**

扩展既有测试：Settings 显示并保存 enabled、vaultPath、rootFolder、pdfMode、exportSource、exportExplainer、exportTranslation、autoExport 八项控件，Test/Sync 使用 mutation 状态与可访问名称；PaperInspector 只在有 paper 时显示 per-paper Export，点击发送该 paper id，disabled/pending/error 可恢复。fixture 不含真实机器路径或 secret。

- [ ] **Step 9（2–5 分钟）：运行 UI接线 command 并确认 RED**

Run:

~~~powershell
npm.cmd --prefix frontend run test:run -- src/features/settings/SettingsRoute.test.tsx src/features/dashboard/PaperInspector.test.tsx
~~~

Expected RED: 既有控件尚未连接至少一个field/action；snapshot/fixture自身错误不算RED。

- [ ] **Step 10（2–5 分钟）：实现最小接线**

只在现有action/form区复用既有components/classes连接八项Settings、Test/Sync与per-paper Export；不新增/修改CSS，不改layout、route tree、GSAP/motion或整页reload。

- [ ] **Step 11（2–5 分钟）：运行相同 UI接线 command 并确认 GREEN**

~~~powershell
npm.cmd --prefix frontend run test:run -- src/features/settings/SettingsRoute.test.tsx src/features/dashboard/PaperInspector.test.tsx
~~~

Expected GREEN: exact accessibility/pending/error behavior通过且DOM/layout snapshots仅出现授权控件。

- [ ] **Step 12（2–5 分钟）：运行 P5 backend 定向门禁**

Run:

~~~powershell
.\.venv\Scripts\python.exe -B -m unittest backend.tests.test_obsidian_settings backend.tests.test_obsidian_layout backend.tests.test_bound_vault_root backend.tests.test_obsidian_ownership backend.tests.test_obsidian_exports_repository backend.tests.test_obsidian_pdf_modes backend.tests.test_obsidian_jobs_api backend.tests.test_obsidian_rebuild backend.tests.test_obsidian_paper_delete backend.tests.test_obsidian_auto_export backend.tests.test_obsidian_pdf_migration -v
~~~

Expected: 全部OK；OCR/materialize/generation spies总调用数为0；temporary Vault全部清理，root/parent/final race sentinel与orphan/Notes bytes不变；MigrationIntent apply/recovery/seal/rollback/tamper证据完整。

- [ ] **Step 13（每条 2–5 分钟）：运行 frontend unit/typecheck/lint/build 门禁**

Run:

~~~powershell
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
npm.cmd --prefix frontend run test:run -- src/lib/api/obsidianGateway.test.ts src/features/obsidian/useObsidianProjection.test.tsx src/features/settings/SettingsRoute.test.tsx src/features/dashboard/PaperInspector.test.tsx
if ($LASTEXITCODE -ne 0) { throw 'P5 targeted frontend tests failed.' }
npm.cmd --prefix frontend run typecheck
if ($LASTEXITCODE -ne 0) { throw 'P5 frontend typecheck failed.' }
npm.cmd --prefix frontend run lint
if ($LASTEXITCODE -ne 0) { throw 'P5 frontend lint failed.' }
npm.cmd --prefix frontend run build
if ($LASTEXITCODE -ne 0) { throw 'P5 frontend build failed.' }
npm.cmd --prefix frontend run e2e
if ($LASTEXITCODE -ne 0) { throw 'P5 frontend E2E failed.' }
~~~

Expected: targeted frontend unit、全量 E2E、typecheck、lint、build 全部 PASS；无真实 Vault path/secret snapshot，无 CSS/layout/router diff。

- [ ] **Step 14（2–5 分钟）：验证 zero-migration 与阶段 diff**

Run:

~~~powershell
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$p5LiveDb = (Resolve-Path -LiteralPath 'data/app.db').Path
$p5LiveIdentityPath = (Resolve-Path -LiteralPath 'data/compatibility/runtime/live-database-identity-v1.json').Path
$p5CreateJson = & .\.venv\Scripts\python.exe -B -m backend.app.cli.database_backup create --database $p5LiveDb --output-directory data/backups --label pre-p5-inventory
$p5CreateExit = $LASTEXITCODE
if ($p5CreateExit -ne 0) { throw "P5 inventory backup create failed with exit code $p5CreateExit." }
$p5Create = $p5CreateJson | ConvertFrom-Json
if (-not $p5Create.ok) { throw 'P5 inventory backup create JSON did not report success.' }
$p5VerifyJson = & .\.venv\Scripts\python.exe -B -m backend.app.cli.database_backup verify --backup $p5Create.backupPath --manifest $p5Create.manifestPath
$p5VerifyExit = $LASTEXITCODE
if ($p5VerifyExit -ne 0) { throw "P5 inventory backup verify failed with exit code $p5VerifyExit." }
$p5Verify = $p5VerifyJson | ConvertFrom-Json
if (-not $p5Verify.ok -or $p5Verify.logicalSha256 -ne $p5Create.logicalSha256) { throw 'P5 inventory backup verify mismatch.' }
$p5RestoreJson = & .\.venv\Scripts\python.exe -B -m backend.app.cli.database_backup restore-check --backup $p5Create.backupPath --manifest $p5Create.manifestPath --output-directory data/backups/restore-checks
$p5RestoreExit = $LASTEXITCODE
if ($p5RestoreExit -ne 0) { throw "P5 inventory restore-check failed with exit code $p5RestoreExit." }
$p5Restore = $p5RestoreJson | ConvertFrom-Json
if (-not $p5Restore.ok -or $p5Restore.logicalSha256 -ne $p5Verify.logicalSha256) { throw 'P5 inventory restore-check mismatch.' }
$p5DrillDb = (Resolve-Path -LiteralPath $p5Restore.restoredPath).Path
if ($p5DrillDb -eq $p5LiveDb) { throw 'P5 inventory drill resolved to Live.' }
$p5InventoryDir = New-Item -ItemType Directory -Path (Join-Path 'data/compatibility/preflight' ('p5-' + [guid]::NewGuid().ToString('N')))
$p5DrillIdentityPath = Join-Path $p5InventoryDir.FullName 'database-identity-v1.json'
$p5DrillIdentityJson = & .\.venv\Scripts\python.exe -B -m backend.app.cli.runtime_owner create-descendant-database-identity --database $p5DrillDb --subject-kind p5_rehearsal --parent-database-identity-manifest $p5LiveIdentityPath --parent-backup $p5Create.backupPath --parent-manifest $p5Create.manifestPath --output $p5DrillIdentityPath
$p5DrillIdentityExit = $LASTEXITCODE
if ($p5DrillIdentityExit -ne 0) { throw "P5 database identity failed with exit code $p5DrillIdentityExit." }
$p5DrillIdentity = $p5DrillIdentityJson | ConvertFrom-Json
if (-not $p5DrillIdentity.ok -or $p5DrillIdentity.subjectKind -ne 'p5_rehearsal') { throw 'P5 database identity JSON is invalid.' }
$p5InventoryBeforePath = Join-Path $p5InventoryDir.FullName 'inventory-before.json'
$p5InventoryAfterPath = Join-Path $p5InventoryDir.FullName 'inventory-after.json'
.\.venv\Scripts\python.exe -B -m backend.app.cli.schema_inventory capture --database $p5DrillDb --database-identity-manifest $p5DrillIdentityPath --output $p5InventoryBeforePath
if ($LASTEXITCODE -ne 0) { throw 'P5 inventory-before capture failed.' }
$p5PreviousDbPath = [Environment]::GetEnvironmentVariable('DB_PATH', 'Process')
$p5HadDbPath = $null -ne $p5PreviousDbPath
try {
  $env:DB_PATH = $p5DrillDb
  $p5CurrentRaw = @(& .\.venv\Scripts\python.exe -B -m alembic -c backend/alembic.ini current)
  $p5CurrentExit = $LASTEXITCODE
  if ($p5CurrentExit -ne 0) { throw "P5 alembic current failed with exit code $p5CurrentExit." }
  $p5Current = @($p5CurrentRaw | ForEach-Object { ([string]$_).Trim() } | Where-Object { $_ -ne '' })
  if ($p5Current.Count -ne 1 -or $p5Current[0] -ne '20260807_03 (head)') { throw "P5 drill must have exactly one current revision equal to 20260807_03 (head); observed: $($p5Current -join ' | ')." }
  .\.venv\Scripts\python.exe -B -m alembic -c backend/alembic.ini upgrade 20260807_03
  $p5UpgradeExit = $LASTEXITCODE
  if ($p5UpgradeExit -ne 0) { throw "P5 exact no-op upgrade failed with exit code $p5UpgradeExit." }
  .\.venv\Scripts\python.exe -B -m unittest backend.tests.test_api_health.ApiHealthTests.test_ready_on_expected_head -v
  if ($LASTEXITCODE -ne 0) { throw 'P5 restored-copy readiness failed.' }
  .\.venv\Scripts\python.exe -B -m backend.app.cli.schema_inventory capture --database $p5DrillDb --database-identity-manifest $p5DrillIdentityPath --output $p5InventoryAfterPath
  if ($LASTEXITCODE -ne 0) { throw 'P5 inventory-after capture failed.' }
  .\.venv\Scripts\python.exe -B -m backend.app.cli.schema_inventory compare --before $p5InventoryBeforePath --after $p5InventoryAfterPath
  if ($LASTEXITCODE -ne 0) { throw 'P5 fixed inventory changed.' }
} finally {
  if ($p5HadDbPath) { $env:DB_PATH = $p5PreviousDbPath } else { Remove-Item Env:DB_PATH -ErrorAction SilentlyContinue }
}
.\.venv\Scripts\python.exe -B -m unittest discover -s backend/tests -p "test_*.py" -v
if ($LASTEXITCODE -ne 0) { throw 'P5 backend suite failed.' }
.\.venv\Scripts\python.exe -B -m unittest discover -s test -p "test_*.py" -v
if ($LASTEXITCODE -ne 0) { throw 'P5 legacy Python suite failed.' }
.\.venv\Scripts\python.exe -B -m unittest discover -s test -p "test_mcp_server.py" -v
if ($LASTEXITCODE -ne 0) { throw 'P5 MCP server suite failed.' }
npm.cmd test
if ($LASTEXITCODE -ne 0) { throw 'P5 Node suite failed.' }
$p5BaselineVerifyJson = node scripts/pre-existing-failure-baseline.mjs verify --baseline contracts/pre-existing-test-failures-v1.json
$p5BaselineVerifyExit = $LASTEXITCODE
if ($p5BaselineVerifyExit -ne 0) { throw "P5 frontend baseline verifier failed with exit code $p5BaselineVerifyExit." }
$p5BaselineVerify = $p5BaselineVerifyJson | ConvertFrom-Json
$p5BaselineRequiredFields = @('baselineMatched','observedSuiteExitCode','overallGreen')
foreach ($p5BaselineField in $p5BaselineRequiredFields) {
  if (-not ($p5BaselineVerify.PSObject.Properties.Name -contains $p5BaselineField)) { throw "P5 baseline verifier omitted required field $p5BaselineField." }
}
if ($p5BaselineVerify.baselineMatched -isnot [bool] -or $p5BaselineVerify.baselineMatched -ne $true) { throw 'P5 baseline verifier did not report boolean baselineMatched=true.' }
if ($p5BaselineVerify.observedSuiteExitCode -isnot [int] -and $p5BaselineVerify.observedSuiteExitCode -isnot [long]) { throw 'P5 baseline verifier did not report an integer observedSuiteExitCode.' }
if ($p5BaselineVerify.overallGreen -isnot [bool]) { throw 'P5 baseline verifier did not report boolean overallGreen.' }
$p5ObservedSuiteExitCode = [long]$p5BaselineVerify.observedSuiteExitCode
if (($p5ObservedSuiteExitCode -eq 0) -ne $p5BaselineVerify.overallGreen) { throw 'P5 baseline verifier reported inconsistent observedSuiteExitCode and overallGreen.' }
npm.cmd run typecheck --prefix frontend
if ($LASTEXITCODE -ne 0) { throw 'P5 frontend typecheck failed.' }
npm.cmd run lint --prefix frontend
if ($LASTEXITCODE -ne 0) { throw 'P5 frontend lint failed.' }
npm.cmd run build --prefix frontend
if ($LASTEXITCODE -ne 0) { throw 'P5 frontend build failed.' }
npm.cmd run e2e --prefix frontend
if ($LASTEXITCODE -ne 0) { throw 'P5 frontend E2E failed.' }
git diff --check
if ($LASTEXITCODE -ne 0) { throw 'P5 whitespace validation failed.' }
git status --short
if ($LASTEXITCODE -ne 0) { throw 'P5 repository status inspection failed.' }
~~~

Expected: P5 verified descendant的before/after在同一subject上精确覆盖12 legacy、五主表、全部P2/P3 aux、`processingJobSpecs` count/hash、FTS logical/join、两个`processing_jobs_spec_guard_*`与三个`document_chunks_fts_*` triggers且全等；每条job spec都strict decode。`alembic current`的全部非空输出总数恰为1且值为`20260807_03 (head)`。backend/legacy Python/MCP/root Node、typecheck、lint、build、E2E与diff raw exit 0；完整frontend Vitest只能由versioned baseline verifier重跑：若raw suite为0，必须`overallGreen=true`；若是P0.1已审查且exact match的non-zero，必须报告原始`observedSuiteExitCode`与`overallGreen=false`，不得称为全绿；任何verifier/字段/ID/signature/hash/related-path漂移都停止P5。P5无migration/table/column/job type/public status；无whitespace error、真实Vault/PDF、data/app.db*、CSS/layout/router/motion或意外配置改动。

## P5 完成门禁

- 八项非秘密 Settings 具有精确 environment/file/default priority；Settings save 保留 credentials/unknown fields且零 Vault、PDF、job、DB side effect。
- 固定 <code>Papers|Sources|Explainers|Translations|Notes|Attachments/PDF/{paper_id}</code> 布局通过 golden tests；title rename 不移动路径，Notes seed 后永久 user-managed。
- 所有Vault directory/create/write/replace/delete都经BoundVaultRoot：Windows全操作持有no-delete-share ancestor handles，POSIX逐级dirfd/openat/*at/O_NOFOLLOW；首次publish true no-replace，managed replace/delete绑定exact identity，能力不足首次写前fail closed。
- managed marker、manifest、ledger、hash、identity-bound replace、no-clobber与conflict可证明；root/parent junction/symlink swap、final publish race及path-open/file-write tripwire全绿；显式cleanup需要plan SHA和三证据，orphan/tombstone永不自动删除。
- Paper delete 只级联 DB ledger，Vault I/O 为 0；sync/rebuild carry-forward orphan/tombstone 与 user-owned Note entries。
- none|reference|copy行为不同且稳定；普通导出不改papers.pdf_path。显式PDF migration只在canonical plan SHA确认后、首次mutation前exclusive+fsync发布batch MigrationIntent；apply/recovery/rollback只接受exact intent path+SHA，逐item checkpoint并最终seal同一文件为receipt，原文件永不删除且可幂等rollback。
- 单篇与全量同步只复用P2 ProcessingJob；canonical `spec_json`原样持久化dryRun/applyCleanup/cleanupPlanSha/settings fingerprint+snapshot/library snapshot，idempotency绑定spec SHA，claim/retry/recovery strict decode/copy；`progress_json`只含安全counts。Partial conflict terminal为succeeded，fatal zero-classified run才failed，result_json恰含八个固定counts。
- auto-export 只在 artifact ready commit 后可选触发，per-Paper coalesced/idempotent且有 startup reconciliation；enqueue failure 不改变 generation success。
- React 只增加 strict type/decoder/Gateway/Query Hook、fixture/test 与现有 Settings/PaperInspector 接线；不改 CSS、layout、route tree 或 motion。
- P4 fixed inventory CLI在fresh verified P5 descendant上给出严格before/after equality；12 legacy、五主表、全部aux/FTS、`processingJobSpecs`与五个固定trigger对象无未解释变化。
- P5 阶段出口不直接执行 full Vitest；只接受 `scripts/pre-existing-failure-baseline.mjs verify` 的 raw exit 0、`baselineMatched=true`、整数 `observedSuiteExitCode`、布尔 `overallGreen`，并证明 `(observedSuiteExitCode == 0) == overallGreen`。P0.1 exact reviewed non-zero 只允许以 `overallGreen=false` 推进，不能报告阶段全绿。
- 公开路径严格为 POST <code>/api/v2/papers/{paper_id}/exports/obsidian</code>、POST <code>/api/v2/obsidian/sync</code>、GET <code>/api/v2/obsidian/status</code>、POST <code>/api/v2/obsidian/test</code>；不存在 generic Obsidian 或 PDF migration HTTP route。
- 缺 ready SourceDocument 时不 materialize、不 enqueue OCR、不调用 OCR；Vault 始终是单向、可重建 projection，停用与运行时回滚不删除任何用户文件。
