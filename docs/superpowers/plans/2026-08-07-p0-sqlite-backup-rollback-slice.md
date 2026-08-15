# P0 SQLite 一致性备份与隔离回滚演练实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Each production change follows superpowers:test-driven-development; each completion claim follows superpowers:verification-before-completion.

**Goal:** 在不写入 Live SQLite、不触碰 React、不迁移 Schema 的前提下，交付一个可独立验证的一致性单文件备份、Manifest 校验与隔离恢复演练切片。

**Architecture:** backend.app.infrastructure.database_backup 是唯一深模块，公开 create_verified_backup、verify_backup、restore_backup_for_validation、seal_origin_receipt 与 verify_origin_receipt 五个 Interface；CLI 只做参数解析与 JSON 序列化。备份使用 SQLite Online Backup 捕获 WAL 中已提交内容，副本随后切换到 DELETE journal mode；所有发布采用同目录原子 no-clobber，所有失败清理都以本次调用的文件所有权为前提。只有同一 exact backup/Manifest 通过独立 verify 与 restore-check 后，才可把它封装成固定路径、strict typed、不可覆盖的 `OriginReceipt`，作为 P4–P6 唯一 database lineage anchor。

**Tech Stack:** Python 3、标准库 sqlite3 / hashlib / pathlib / unittest、SQLite WAL、Node 现有回归测试。

**Workspace constraints:** 仓库根目录为 F:\paper\研究方向细化\study-app。保护用户已有 AGENTS.md 与 .agents/；不修改 frontend/、public/、server.js 或 Live data/app.db*。用户已授权创建独立 `codex/` 分支并在门禁全绿后连续实施 P0→P6；仍不得暂存、提交或推送。该切片不含 Alembic Schema 变更，因此迁移升级/降级不适用；回滚验证由隔离 restore-check 完成。

---

## 文件职责

- backend/app/infrastructure/database_backup.py：备份、Manifest、校验、恢复演练、`OriginReceipt` seal/verify 及文件安全边界。
- backend/app/cli/database_backup.py：create、verify、restore-check、seal-origin、verify-origin-receipt CLI Adapter。
- backend/tests/test_database_backup.py：Online Backup、Manifest、sidecar、碰撞、恢复隔离的行为测试。
- docs/DATABASE.md：运维命令、单文件契约、恢复限制与敏感数据说明。
- .gitignore：忽略真实备份和恢复演练物料 data/backups/。

## OriginReceipt v1 不可替换锚点

`OriginReceipt` 是独立于 backup Manifest 的 strict typed artifact，不是路径别名或实施报告中的一行文字。其顶层字段精确为 `{schemaVersion,manifestKind,backupId,backupPath,backupSha256,manifestPath,manifestSha256,logicalSha256,databaseLineageId,receiptPath,createdAt,receiptSha256}`：`schemaVersion=1`，`manifestKind="p0-origin"`；三个 path 都是在 seal 时完成 reparse/symlink 解析后的绝对 exact path；所有 SHA-256 都是 lowercase 64-hex。`databaseLineageId` 精确为 canonical `{version:1,originBackupId,originManifestSha256,originLogicalSha256}` 的 SHA-256。`receiptSha256` 是除自身外其余十一个字段按 UTF-8 canonical JSON（固定 key 顺序、无多余空白、末尾无换行）编码后的 SHA-256，因此不发生自引用。完整 receipt canonical file bytes 另计算 `originReceiptFileSha256`，只由 CLI 返回并写入 P0 实施 evidence；后续 P4 明确接收 `receiptPath + expected originReceiptFileSha256`，不能从 receipt 自己信任一个“期望 hash”。

`seal_origin_receipt(backup_path, manifest_path, expected_logical_sha256, receipt_path) -> OriginReceipt` 必须重新执行与 public `verify_backup` 相同的 strict Manifest/backup 验证，确认 backup ID、backup bytes hash、Manifest bytes hash 与 logical hash 都等于输入 evidence，且 `receipt_path` 精确为 `data/compatibility/runtime/p0-origin-receipt-v1.json`；它以 exclusive create + flush/fsync + 原子发布写一次，目标已存在时无论内容是否相同都拒绝，绝不覆盖、选择 latest 或接受 glob。`verify_origin_receipt(receipt_path, expected_receipt_file_sha256) -> OriginReceipt` 必须先验证 exact receipt file bytes hash、strict allowlist/type/receiptSha256，再重新验证 receipt 指向的 exact backup/Manifest bytes 与全部 identity 字段；receipt、backup 或 Manifest 的 path/identity/bytes 任一漂移都分类失败。验证只读，不创建 receipt、不修复路径、不搜索替代备份。

### Task 1：关闭连接生命周期与文件所有权缺口

**Files:**
- Modify: backend/app/infrastructure/database_backup.py
- Test: backend/tests/test_database_backup.py

- [x] **Step 1: 写出目录碰撞与目标碰撞失败测试**

测试使用固定 UUID 构造既有恢复目录与既有最终备份，保存 app.db、-wal、-shm 和临时文件的原始字节；调用失败后逐文件读取并要求字节完全不变。

- [x] **Step 2: 运行测试并确认正确红灯**

Run:

~~~powershell
.\.venv\Scripts\python.exe -m unittest backend.tests.test_database_backup.DatabaseBackupTests.test_restore_collision_never_cleans_files_it_does_not_own backend.tests.test_database_backup.DatabaseBackupTests.test_backup_target_collision_preserves_existing_file_and_sidecars -v
~~~

Observed: 两个测试都因既有 -wal 被当前异常清理删除而报 FileNotFoundError。

- [ ] **Step 3: 确保 SQLite 只读连接在异常时立即关闭**

在 database_backup.py 引入并使用 contextlib.closing：

~~~python
from contextlib import closing

with closing(_open_readonly(source_path)) as source:
    source_journal_mode = str(source.execute("PRAGMA journal_mode").fetchone()[0]).lower()
    _assert_healthy_database(source, "source database")
    source.backup(destination)

with closing(_open_readonly(database_path)) as connection:
    _assert_healthy_database(connection, "backup database")
    # The complete streaming fingerprint remains inside this lifetime.
~~~

这必须消除 Alembic 多 head 异常期间 Windows 对临时 SQLite 文件的锁定。

- [ ] **Step 4: 让异常清理显式服从所有权标志**

create_verified_backup 只在 published_backup=True 时删除最终备份及其 sidecar；restore_backup_for_validation 只在 validation_directory_created=True 时清理该目录，并只在 published=True 时清理最终 app.db。所有清理使用不会掩盖原始分类错误的 helper：

~~~python
def _unlink_owned_file(path: Path, *, strict: bool) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError as error:
        if strict:
            raise DatabaseBackupError(
                "BACKUP_CLEANUP_FAILED",
                f"Could not remove generated file {path.name}: {error}",
            ) from error
~~~

- [ ] **Step 5: 运行所有权测试并确认转绿**

Run:

~~~powershell
.\.venv\Scripts\python.exe -m unittest backend.tests.test_database_backup.DatabaseBackupTests.test_restore_collision_never_cleans_files_it_does_not_own backend.tests.test_database_backup.DatabaseBackupTests.test_backup_target_collision_preserves_existing_file_and_sidecars -v
~~~

Expected: 测试报告恰好 2 项并以 `OK` 结束；碰撞目录和最终路径的既有字节不变。

### Task 2：闭合单文件备份与原子 no-clobber 契约

**Files:**
- Modify: backend/app/infrastructure/database_backup.py
- Test: backend/tests/test_database_backup.py

- [x] **Step 1: 写出 sidecar 与非法恢复输出失败测试**

测试分别要求：独立 verify_backup 在备份旁出现 -wal 时返回 BACKUP_SIDECAR_PRESENT 且不删除该文件；restore-check 在输出目录名为 another-live.db-wal 时返回 RESTORE_OUTPUT_DIRECTORY_INVALID 且不创建目录。

- [x] **Step 2: 运行测试并确认正确红灯**

Run:

~~~powershell
.\.venv\Scripts\python.exe -m unittest backend.tests.test_database_backup.DatabaseBackupTests.test_verify_rejects_backup_sidecars_without_deleting_them backend.tests.test_database_backup.DatabaseBackupTests.test_restore_rejects_sidecar_like_output_directory_before_creating_it -v
~~~

Observed: 两个调用均未抛出期望的 DatabaseBackupError。

- [ ] **Step 3: 在独立校验前后拒绝 sidecar**

verify_backup 在读取文件哈希前和逻辑 fingerprint 后均调用：

~~~python
_assert_database_has_no_sidecars(backup_path)
actual_database = _fingerprint_database(backup_path)
_assert_database_has_no_sidecars(backup_path)
~~~

_assert_database_has_no_sidecars 只观察、不删除传入备份旁的文件，并把文件系统检查异常归一化为 DatabaseBackupError。

- [ ] **Step 4: 拒绝数据库文件/sidecar 形态的输出目录**

在任何 mkdir 前执行：

~~~python
def _validate_restore_output_directory(path: Path) -> None:
    lowered_name = path.name.casefold()
    if lowered_name.endswith(("-wal", "-shm", "-journal")) or path.suffix.casefold() in {
        ".db",
        ".db3",
        ".sqlite",
        ".sqlite3",
    }:
        raise DatabaseBackupError(
            "RESTORE_OUTPUT_DIRECTORY_INVALID",
            "Restore validation output must be a directory, not a SQLite database or sidecar path.",
        )
~~~

- [ ] **Step 5: 用原子 no-clobber 发布替换 exists + os.replace**

临时文件与最终文件始终位于同一目录。优先以 os.link(temporary_path, final_path) 原子创建新目录项；真实 `F:` Windows 卷返回 WinError 1、不支持 hard-link 时，使用 Windows `os.rename` 的原子 no-replace 语义回退，POSIX 禁止该回退。两种 primitive 的 FileExistsError 都映射为 BACKUP_TARGET_EXISTS；成功 hard-link 后删除本次拥有的临时目录项，成功 rename 后临时路径已被消费。若临时清理失败，按文件身份回滚本次创建的最终目录项并返回分类错误；竞争目标或发布后被替换的路径绝不删除。

- [ ] **Step 6: 运行 Task 2 定向测试并确认转绿**

Run:

~~~powershell
.\.venv\Scripts\python.exe -m unittest backend.tests.test_database_backup.DatabaseBackupTests.test_verify_rejects_backup_sidecars_without_deleting_them backend.tests.test_database_backup.DatabaseBackupTests.test_restore_rejects_sidecar_like_output_directory_before_creating_it backend.tests.test_database_backup.DatabaseBackupTests.test_backup_captures_committed_wal_rows_and_restores_them backend.tests.test_database_backup.DatabaseBackupTests.test_backup_target_collision_preserves_existing_file_and_sidecars -v
~~~

Expected: 四个测试通过；发布的 backup 和 restore app.db 均无 -wal/-shm，既有碰撞文件不变。

### Task 3：闭合 Manifest 与迁移状态验证

**Files:**
- Modify: backend/app/infrastructure/database_backup.py
- Test: backend/tests/test_database_backup.py

- [x] **Step 1: 写出恶意 backupId、重算哈希的逻辑指纹篡改、多 Alembic head 测试**

测试要求：路径型 backupId 即使重算 canonical manifestSha256 仍以 BACKUP_MANIFEST_INVALID 拒绝；database.tableCounts 被篡改并重算 Manifest 哈希后仍以 BACKUP_LOGICAL_MISMATCH 拒绝；两个 Alembic head 以 BACKUP_ALEMBIC_STATE_AMBIGUOUS 拒绝且不留下发布或临时文件。

- [x] **Step 2: 确认测试能暴露真实缺口**

Run（Task 1 实现前保存的 RED 命令；后续不得在已修复实现上伪造 RED）：

~~~powershell
.\.venv\Scripts\python.exe -m unittest backend.tests.test_database_backup.DatabaseBackupTests.test_backup_rejects_multiple_alembic_heads_without_publishing_files -v
~~~

Expected RED: fixture 含两个 Alembic head；公共 `create_verified_backup` seam 应暴露未关闭只读连接导致的未分类 Windows `PermissionError`/文件锁错误，而不是期望的 `BACKUP_ALEMBIC_STATE_AMBIGUOUS`。断言同时检查备份目录没有最终文件、Manifest、SQLite sidecar 或 staging 残留；import/fixture/skip 不算有效 RED。

Observed RED: 多 head 路径确实因未关闭只读连接被 Windows 文件锁转化为未分类 PermissionError；backupId 与逻辑指纹测试也达到各自预期。该根因由 Task 1 Step 3 修复，Step 3 的三测试命令是同一行为集合的 GREEN 证据。

- [ ] **Step 3: 运行 Manifest/迁移定向测试**

~~~powershell
.\.venv\Scripts\python.exe -m unittest backend.tests.test_database_backup.DatabaseBackupTests.test_restore_rejects_path_like_backup_id_even_with_recomputed_hash backend.tests.test_database_backup.DatabaseBackupTests.test_verify_rejects_rehashed_logical_fingerprint_tampering backend.tests.test_database_backup.DatabaseBackupTests.test_backup_rejects_multiple_alembic_heads_without_publishing_files -v
~~~

Expected: 测试报告恰好 3 项并以 `OK` 结束；备份目录在多 head 失败后为空。

### Task 4：文档、全量回归与双重代码审查

**Files:**
- Modify: docs/DATABASE.md
- Verify: .gitignore
- Verify: backend/app/cli/database_backup.py

- [ ] **Step 1: 更新运维契约**

在 docs/DATABASE.md 明确：备份副本固定为 DELETE journal mode；verify 会拒绝旁置 WAL/SHM；发布不会覆盖同名文件；restore-check --output-directory 必须是真实目录语义，不能是 *.db、*-wal、*-shm；正式 Live 恢复仍需停服并由后续独立切片提供。

- [ ] **Step 2: 运行本切片全量验证**

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
Invoke-CheckedNative 'backend tests' { .\.venv\Scripts\python.exe -m unittest discover -s backend/tests -p "test_*.py" -v }
Invoke-CheckedNative 'backend compileall' { .\.venv\Scripts\python.exe -m compileall -q backend }
Invoke-CheckedNative 'legacy Python tests' { .\.venv\Scripts\python.exe -m unittest discover -s test -p "test_*.py" -v }
Invoke-CheckedNative 'MCP characterization' { .\.venv\Scripts\python.exe -B -m unittest discover -s test -p "test_mcp_server.py" -v }
Invoke-CheckedNative 'root Node tests' { npm.cmd test }
Invoke-CheckedNative 'full frontend Vitest' { npm.cmd run test:run --prefix frontend }
Invoke-CheckedNative 'frontend typecheck' { npm.cmd run typecheck --prefix frontend }
Invoke-CheckedNative 'frontend lint' { npm.cmd run lint --prefix frontend }
Invoke-CheckedNative 'frontend build' { npm.cmd run build --prefix frontend }
Invoke-CheckedNative 'frontend e2e' { npm.cmd run e2e --prefix frontend }
Invoke-CheckedNative 'git diff check' { git diff --check }
~~~

Expected: backend backup 定向套件、compileall 与 diff check 必须退出 0。其余完整命令必须逐条报告真实 exit code；不得把 non-zero 写成“全绿”。若完整 frontend Vitest 为 0，记录空失败集合；若为 non-zero，本阶段只允许进入 P0.1 的首个 versioned baseline capture task，不得进入任何 P1 migration 或功能切片。P0.1 必须对同一完整命令连续运行至少两次，并把完整 test id、normalized stack signature、相关文件 SHA-256、sourceTreeHash 与 exit code 固化后，后续阶段才可能按“与已审核基线完全一致且本切片未改相关路径”的例外继续；新增失败、消失后又出现的失败、signature/hash 漂移或相关路径被本切片修改都立即停止。该例外始终报告 non-zero，不能称为绿色。

- [ ] **Step 3: 规格审查后再做代码质量审查**

规格审查必须先确认：不写 Live DB、不触碰前端、Online Backup 捕获 WAL、单文件契约、数量/hash Manifest、隔离恢复、碰撞不覆盖、失败分类。通过后，代码质量审查再检查连接关闭、Windows 文件锁、TOCTOU、异常清理与测试有效性；任何阻断项修复后必须重审。

### Task 5：追加闭合发布后篡改、Manifest 严格性与目录身份竞态

**Files:**
- Modify: backend/app/infrastructure/database_backup.py
- Test: backend/tests/test_database_backup.py

- [ ] **Step 1: 写出可在当前公共 Interface 上执行的安全边界红测**

新增以下测试。所有测试只使用 `TemporaryDirectory`、确定性 fault injection 和 invocation-owned 路径，不接触 Live DB；测试名必须与实际收集名一致：

- `DatabaseBackupTests.test_create_rejects_in_place_mutation_after_public_verification`：在 `verify_backup` 已返回后原地改变已发布 backup 的 bytes；create 必须以 `BACKUP_FILE_CHANGED_AFTER_VERIFY` 失败，不得返回成功报告。
- `DatabaseBackupTests.test_create_rejects_in_place_manifest_mutation_after_verification`：在 verification 返回后原地改变同一 Manifest 文件对象；create 必须以 `BACKUP_MANIFEST_CHANGED_AFTER_VERIFY` 失败。
- `DatabaseBackupTests.test_restore_rejects_in_place_mutation_after_destination_fingerprint`：在恢复副本完成 logical fingerprint 后原地改变 destination bytes；restore-check 必须以 `RESTORE_FILE_CHANGED_DURING_VALIDATION` 失败。
- `DatabaseBackupTests.test_cleanup_failure_never_masks_the_original_classified_error`：主流程先产生 `BACKUP_ALEMBIC_STATE_AMBIGUOUS`，真实异常清理调用再抛 `RuntimeError`；最终仍抛原始分类错误。
- `DatabaseBackupTests.test_verify_rejects_unknown_manifest_fields_for_v1_and_v2`：以四个 subtest 覆盖 v1/v2 的顶层与 `database` object 未知字段；即使重算 Manifest hash 也统一返回 `BACKUP_MANIFEST_INVALID`。
- `DatabaseBackupTests.test_staging_cleanup_preserves_directory_replaced_after_identity_check`：模拟 Windows 空目录在身份检查窗口被同名不同 identity 目录替换；替换目录与 sentinel bytes 必须保留。
- `DatabaseBackupTests.test_posix_private_namespace_cleanup_removes_owned_paths`：模拟 POSIX 时，绑定的 `0700` 私有 namespace 正常文件与空目录仍可清理，避免安全修复破坏正常 create。
- `DatabaseBackupTests.test_posix_private_file_cleanup_preserves_a_racing_replacement` 与 `DatabaseBackupTests.test_posix_private_directory_cleanup_preserves_a_racing_replacement`：第二次 child/parent identity 检查观察到替换时返回 fail-closed，不调用路径删除。
- `DatabaseBackupTests.test_posix_cleanup_without_private_parent_ownership_fails_closed`：无法证明 `0700` 私有 parent ownership 时不删除任何对象。
- `DatabaseBackupTests.test_create_wires_posix_private_namespace_ownership_through_public_seam`：经 `create_verified_backup` 证明 POSIX 私有 namespace 身份从创建、发布到清理完整传递且无 staging 残留。
- `DatabaseBackupTests.test_restore_rechecks_output_root_for_reparse_before_first_write`：初次检查后、backup verification 期间输出根变为 reparse/symlink 时，在创建 validation directory 或复制 bytes 前以 `RESTORE_OUTPUT_DIRECTORY_INVALID` 拒绝。
- `DatabaseBackupTests.test_directory_identity_rejects_windows_reparse_attribute`：Windows reparse attribute 不能被当作物理目录 identity 接受。

- [ ] **Step 2: 运行完整追加测试并确认 RED**

Run:

~~~powershell
$env:PYTHONDONTWRITEBYTECODE = '1'
.\.venv\Scripts\python.exe -m unittest `
  backend.tests.test_database_backup.DatabaseBackupTests.test_create_rejects_in_place_mutation_after_public_verification `
  backend.tests.test_database_backup.DatabaseBackupTests.test_create_rejects_in_place_manifest_mutation_after_verification `
  backend.tests.test_database_backup.DatabaseBackupTests.test_restore_rejects_in_place_mutation_after_destination_fingerprint `
  backend.tests.test_database_backup.DatabaseBackupTests.test_cleanup_failure_never_masks_the_original_classified_error `
  backend.tests.test_database_backup.DatabaseBackupTests.test_verify_rejects_unknown_manifest_fields_for_v1_and_v2 `
  backend.tests.test_database_backup.DatabaseBackupTests.test_staging_cleanup_preserves_directory_replaced_after_identity_check `
  backend.tests.test_database_backup.DatabaseBackupTests.test_posix_private_namespace_cleanup_removes_owned_paths `
  backend.tests.test_database_backup.DatabaseBackupTests.test_posix_private_file_cleanup_preserves_a_racing_replacement `
  backend.tests.test_database_backup.DatabaseBackupTests.test_posix_private_directory_cleanup_preserves_a_racing_replacement `
  backend.tests.test_database_backup.DatabaseBackupTests.test_posix_cleanup_without_private_parent_ownership_fails_closed `
  backend.tests.test_database_backup.DatabaseBackupTests.test_create_wires_posix_private_namespace_ownership_through_public_seam `
  backend.tests.test_database_backup.DatabaseBackupTests.test_restore_rechecks_output_root_for_reparse_before_first_write `
  backend.tests.test_database_backup.DatabaseBackupTests.test_directory_identity_rejects_windows_reparse_attribute `
  -v
~~~

Expected RED: 十三项测试均被收集；失败分别证明 verification 后 bytes 可漂移、primary error 可被 cleanup 覆盖、Manifest 未严格拒绝未知字段，或目录 identity/reparse 竞态仍可能删除替换物或越过授权根。import error、fixture 未执行 fault injection 或平台分支空通过不算有效 RED。

- [ ] **Step 3: 实现最小的发布证明与严格 Manifest allowlist**

create 在公开 verification 返回后重新读取已发布 backup 的 identity、size 与 SHA-256，并重新加载 Manifest，比较其 identity、解析后的完整语义和原始文件 SHA-256；任一漂移分别返回 `BACKUP_FILE_CHANGED_AFTER_VERIFY`、`BACKUP_PUBLISH_OWNERSHIP_CHANGED` 或 `BACKUP_MANIFEST_CHANGED_AFTER_VERIFY`。restore 在 destination logical fingerprint 后、返回成功报告前重新比较 destination identity、size 与 SHA-256。v1/v2 共用固定顶层和 `database` 字段 allowlist，未知字段统一返回 `BACKUP_MANIFEST_INVALID`。

- [ ] **Step 4: 实现不掩盖主错误的 cleanup 与跨平台目录 identity guard**

主流程已有 `DatabaseBackupError` 时，所有 non-strict cleanup 都经 best-effort wrapper；cleanup 的 `RuntimeError`、`OSError` 或分类清理错误不得替换 primary code。Windows 文件与空目录通过 no-share-delete handle 绑定对象，再用 `SetFileInformationByHandle` 删除。POSIX 只在调用者提供匹配 parent identity、路径位于该 parent、namespace 为 `0700` 且 child/parent/reparse 两次检查均稳定时执行非递归 `unlink`/`rmdir`；任一漂移或无法证明私有 ownership 时 fail-closed。restore output root 在初检后、backup verification 后及首次创建 child 前重复拒绝 reparse/symlink。文档必须明确 POSIX 最终检查与路径删除之间仍存在极小 rename race，不声称绝对 TOCTOU 消除。

- [ ] **Step 5: 以 Step 2 完全相同的命令确认 GREEN**

Run:

~~~powershell
$env:PYTHONDONTWRITEBYTECODE = '1'
.\.venv\Scripts\python.exe -m unittest `
  backend.tests.test_database_backup.DatabaseBackupTests.test_create_rejects_in_place_mutation_after_public_verification `
  backend.tests.test_database_backup.DatabaseBackupTests.test_create_rejects_in_place_manifest_mutation_after_verification `
  backend.tests.test_database_backup.DatabaseBackupTests.test_restore_rejects_in_place_mutation_after_destination_fingerprint `
  backend.tests.test_database_backup.DatabaseBackupTests.test_cleanup_failure_never_masks_the_original_classified_error `
  backend.tests.test_database_backup.DatabaseBackupTests.test_verify_rejects_unknown_manifest_fields_for_v1_and_v2 `
  backend.tests.test_database_backup.DatabaseBackupTests.test_staging_cleanup_preserves_directory_replaced_after_identity_check `
  backend.tests.test_database_backup.DatabaseBackupTests.test_posix_private_namespace_cleanup_removes_owned_paths `
  backend.tests.test_database_backup.DatabaseBackupTests.test_posix_private_file_cleanup_preserves_a_racing_replacement `
  backend.tests.test_database_backup.DatabaseBackupTests.test_posix_private_directory_cleanup_preserves_a_racing_replacement `
  backend.tests.test_database_backup.DatabaseBackupTests.test_posix_cleanup_without_private_parent_ownership_fails_closed `
  backend.tests.test_database_backup.DatabaseBackupTests.test_create_wires_posix_private_namespace_ownership_through_public_seam `
  backend.tests.test_database_backup.DatabaseBackupTests.test_restore_rechecks_output_root_for_reparse_before_first_write `
  backend.tests.test_database_backup.DatabaseBackupTests.test_directory_identity_rejects_windows_reparse_attribute `
  -v
~~~

Expected GREEN: `Ran 13 tests` 与 `OK`；backup/Manifest/restore 三个 rewrite case 均被末次证明拒绝，primary error code 保持不变，v1/v2 四个 unknown-field subtest 全部拒绝，Windows/POSIX replacement 保留，正常 POSIX 私有 create 无 staging 残留，restore 在首次写前拒绝变化后的 reparse root。

- [ ] **Step 6: 依次完成独立规格审查与代码质量审查**

规格审查先逐项映射本 Task 的十三个测试到六类要求：create 最终再证明、restore 最终再证明、primary error precedence、v1/v2 strict allowlist、Windows/POSIX replacement ownership、post-check reparse escape。首轮独立规格审查发现两个 Important：restore output root 在最终检查与 child 创建之间未绑定；Manifest exclusive-create 后在首次 identity capture 前失败时 cleanup 仍可能误认替换物。按顺序门禁，代码质量审查尚未启动；先执行 Task 5A。Task 5A 规格复审 Important=0 后才进入质量审查。质量审查再检查 fault injection 有效性、handle 生命周期、Windows/POSIX 分支可达性、TOCTOU、错误脱敏和测试是否真正改写 bytes/identity；修复任一 Important 后，必须重新运行 Task 5 与 5A 完整定向命令并重新执行两次审查。只有两份审查的 Important 都为 0，才允许进入 Live Task。

### Task 5A：绑定 Manifest 临时文件与 restore output root 的对象所有权

**Files:**
- Modify: backend/app/infrastructure/database_backup.py
- Test: backend/tests/test_database_backup.py
- Verify: docs/DATABASE.md

- [ ] **Step 1: 在公共 Interface 写三个确定性 RED 测试**

三个 public-seam 测试都必须使用同一个 `_InvocationPathTripwire` fixture：进入 seam 前把 process `DB_PATH` 设为当前 workspace `data/app.db` 的绝对 hostile sentinel；在 seam 调用期间拦截 `backend.app.infrastructure.database_backup` 使用的 `Path.open`、`os.open`、`sqlite3.connect` 与 `_open_readonly`，把 URI/file 参数解析为 resolved filesystem path，只允许本测试 `TemporaryDirectory` 下本次拥有的 source/backup/manifest/restore roots。任何 workspace `data/app.db*`、调用者原 DB_PATH、repo 外路径或未登记路径访问都立即以测试失败终止。fixture teardown 恢复原 DB_PATH 和 patched symbols，并断言至少观察到预期 temp source/destination open、hostile sentinel open count 恰为 0；不得通过把所有 open mock 成 no-op 获得假绿。

- `DatabaseBackupTests.test_create_preserves_replacement_when_manifest_write_fails_before_identity_capture`：在 `_InvocationPathTripwire` 内经 `create_verified_backup`，在 Manifest exclusive open 后、原实现保存 fd identity 前让 `fsync` 产生分类写失败，并在 cleanup 前把 pathname 换成不同 file identity/sentinel bytes；最终保留原始 `BACKUP_MANIFEST_WRITE_FAILED`，替换物必须存在且 bytes 不变。
- `DatabaseBackupTests.test_restore_holds_windows_output_root_handle_until_child_is_bound`：仅 Windows，在 `_InvocationPathTripwire` 内创建真实目录与 junction target；在旧实现最后一次 output-root identity read 返回后尝试把 root 替换成 junction，再继续公共 `restore_backup_for_validation`。有效 RED 必须证明替换成功且旧实现把 validation child 写到 target；测试 cleanup 只删除自身 TemporaryDirectory，绝不触及 workspace/Live。若无法创建普通 junction，测试失败而不是 skip；非 Windows 才 skip。
- `DatabaseBackupTests.test_restore_creates_validation_directory_through_bound_posix_dirfd`：仅 POSIX，在 `_InvocationPathTripwire` 内把 root path 重命名并换入不同目录后，证明 child/destination 的 create/copy/verify 都仍通过已绑定 dirfd/file descriptor 落在原 invocation-owned root；若平台缺安全 dirfd/fd-path 能力，公共 Interface 必须在首次写前以 `RESTORE_BOUND_DIRECTORY_UNSUPPORTED` fail closed，不允许 pathname fallback。

- [ ] **Step 2: 运行完整新增集合并确认 RED**

~~~powershell
.\.venv\Scripts\python.exe -B -m unittest `
  backend.tests.test_database_backup.DatabaseBackupTests.test_create_preserves_replacement_when_manifest_write_fails_before_identity_capture `
  backend.tests.test_database_backup.DatabaseBackupTests.test_restore_holds_windows_output_root_handle_until_child_is_bound `
  backend.tests.test_database_backup.DatabaseBackupTests.test_restore_creates_validation_directory_through_bound_posix_dirfd `
  -v
~~~

Expected RED: 当前平台恰有两个行为测试执行、另一平台专属测试 skip；两项已执行测试的 tripwire 都观察到 temp open 且 hostile DB_PATH/workspace Live open 为 0；Manifest replacement 被旧 cleanup 删除，或 restore replacement/dirfd 测试证明 child 未绑定已验证 root。import error、未触发替换、tripwire 未实际拦截 open/connect、Windows junction 被无条件 skip 或 POSIX 仅 mock checker 都不算有效 RED。

- [ ] **Step 3: 立即绑定 Manifest exclusive file identity**

把 Manifest 写入收敛到内部 `_OwnedExclusiveFile` Adapter：`open('xb')` 成功后、任何 write/flush/fsync 前立刻对该已打开 handle 执行 `fstat`/平台 file-ID capture；后续 cleanup 只允许删除与该 identity 相同的路径，identity 未能捕获时 fail closed 并保留 residue。主流程已经产生的 `DatabaseBackupError` 始终优先，cleanup 诊断不得覆盖它。不得新增公共方法或按 pathname 当前 identity 推断“这是我的文件”。

- [ ] **Step 4: 通过内部 BoundRestoreRoot seam 绑定 output root**

新增内部 Interface `open_bound_restore_root(path) -> BoundRestoreRoot`，只暴露 `create_validation_directory(prefix) -> BoundValidationDirectory`、`copy_verified_database(source,name)`、`verify_destination(name)`、`close()`；公共 restore caller 不再在最终 reparse/identity 检查后直接调用 pathname `tempfile.mkdtemp`。

- Windows Adapter 使用 `CreateFileW(FILE_FLAG_BACKUP_SEMANTICS|FILE_FLAG_OPEN_REPARSE_POINT)` 打开已验证物理 root，share flags 明确不含 `FILE_SHARE_DELETE`，并在 handle 上复核 volume/file ID 与 reparse attribute；handle 保持到 child directory 创建、destination copy/verification 与返回报告完成。child 也持有目录 handle。替换/rename/junction 操作必须因共享约束失败或被 identity check 拒绝。
- POSIX Adapter 以 `O_DIRECTORY|O_NOFOLLOW` 打开 root dirfd，使用 `mkdir(...,dir_fd=root_fd)` 创建随机 child，再以 child dirfd + `O_CREAT|O_EXCL|O_NOFOLLOW` 创建 destination，并通过绑定 descriptor 完成 copy/fsync/hash/SQLite validation；Linux 可使用验证后的 `/proc/self/fd/<fd>`，其他平台仅可使用经测试等价的 descriptor path。缺能力时在首次写前返回 `RESTORE_BOUND_DIRECTORY_UNSUPPORTED`，禁止退回未绑定 pathname。
- cleanup 同样经 bound handle/dirfd 删除本次拥有对象；关闭顺序为 destination handle → child handle → root handle。返回可读路径前再次证明当前 root pathname 仍指向原 root；若漂移则返回分类错误并只清理绑定对象。

- [ ] **Step 5: 重新运行新增测试、Task 5 定向集合与完整 backend suite**

依次运行 Step 2 三测试命令、Task 5 Step 2 的完整十三测试命令，以及：

~~~powershell
.\.venv\Scripts\python.exe -B -m unittest discover -s backend/tests -p "test_*.py" -v
~~~

Expected GREEN: 当前平台两个新增行为测试通过、另一平台专属测试只因平台不同而 skip；Task 5 全部通过；完整 suite 零 failure。两项 public seam 的 `_InvocationPathTripwire` 均证明所有 path-open/sqlite-connect 落在 invocation-owned temp roots，hostile DB_PATH 与 workspace Live open count 为 0；Manifest sentinel 与 junction target bytes 不变，无 staging/validation residue，错误响应不泄露绝对路径或原始系统异常。

- [ ] **Step 6: 重新完成规格审查，再启动独立代码质量审查**

规格复审必须显式重放两个原 release blocker 的 public-seam tests，核对 Manifest identity 在首次可能失败操作前绑定、restore child/bytes/verification 全部处于同一 bound root lifetime，且 Windows/POSIX 测试并非 mock-only false green。Important=0 后才启动质量审查；质量审查检查 handle/dirfd flags、share mode、关闭/异常顺序、unsupported-platform fail-closed、cleanup ownership、错误脱敏和 Live 零接触。任一 Important 修复都要再次运行 Step 5 并依次重审，不能直接进入 Task 6。

### Task 6：Live 快照、独立校验与隔离恢复演练

**Files:**
- Generate (ignored): data/backups/app-pre-p0-*.sqlite3
- Generate (ignored): data/backups/app-pre-p0-*.sqlite3.manifest.json
- Generate (ignored): data/backups/restore-checks/restore-validation-*/app.db
- Move after validation (ignored): 第一份问题快照及其精确关联文件到 `data/backups/quarantine/4634c851208c415995a50ed8dadae9d4/`

- [ ] **Step 1: 记录 Live 文件只读基线**

记录 data/app.db、data/app.db-wal、data/app.db-shm 的存在性、长度、mtime；不得 checkpoint、复制主文件或删除 sidecar。

- [ ] **Step 2: 创建修正后的 Live 快照**

~~~powershell
$createJson = .\.venv\Scripts\python.exe -m backend.app.cli.database_backup create --database data/app.db --output-directory data/backups --label pre-p0
$createExit = $LASTEXITCODE
if ($createExit -ne 0) { throw "P0 backup create failed with exit code $createExit." }
$createdBackup = $createJson | ConvertFrom-Json
if (-not $createdBackup.ok) { throw 'P0 backup create did not return ok=true.' }
$backupPath = [string]$createdBackup.backupPath
$manifestPath = [string]$createdBackup.manifestPath
~~~

Expected: stdout 单行 JSON ok=true；Manifest quickCheck=ok、integrityCheck=ok、FK violations 为 0，并包含表数量、关键内容数量、数据库/文件/Manifest SHA-256。

- [ ] **Step 3: 独立 verify 与 restore-check**

使用 Step 2 从 JSON 解析出的 `$backupPath` 和 `$manifestPath` 逐字传入，不通过 glob 选取：

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
Invoke-CheckedNative 'P0 independent verify' { .\.venv\Scripts\python.exe -m backend.app.cli.database_backup verify --backup $backupPath --manifest $manifestPath }
Invoke-CheckedNative 'P0 isolated restore-check' { .\.venv\Scripts\python.exe -m backend.app.cli.database_backup restore-check --backup $backupPath --manifest $manifestPath --output-directory data/backups/restore-checks }
~~~

Expected: 两条命令 ok=true，logical SHA-256 相同；backup 与恢复目录都只有主数据库文件，无 WAL/SHM/临时残留。

- [ ] **Step 4: 核对 Live 未被写入及数量/hash**

重新读取 Live 文件元数据，确认 data/app.db 的长度与 mtime 未因本流程改变；比较 Manifest、独立 verify、恢复结果的表数量、关键内容数量与 logical SHA-256。

- [ ] **Step 5: 可恢复地隔离第一份问题快照**

仅在新快照完全通过后，把 backup ID 4634c851208c415995a50ed8dadae9d4 对应的已知 backup、Manifest、临时 WAL/SHM、backup WAL/SHM 和旧 restore-check 目录移动到专属 quarantine 目录。逐个使用 Move-Item -LiteralPath，移动前解析并确认每个绝对路径仍位于 F:\paper\研究方向细化\study-app\data\backups；不删除任何文件。

- [ ] **Step 6: 最终新鲜验证并交给下一纵向切片**

先以 TDD 增加并通过 `DatabaseBackupTests.test_seal_origin_receipt_exclusive_creates_strict_lineage_anchor`、`test_seal_origin_receipt_rejects_existing_output_even_when_equal`、`test_verify_origin_receipt_rejects_receipt_backup_or_manifest_drift`、`test_verify_origin_receipt_rejects_unknown_missing_or_wrong_typed_fields` 与 `test_verify_origin_receipt_requires_out_of_band_receipt_file_sha256`。测试必须使用两个各自可独立 verify 的 backup，证明只有显式传入 seal 的那一个能成为 lineage anchor；交换成另一份“同样健康”的 backup/Manifest、修改 receipt path/SHA/self-hash、增加未知字段、把整数/布尔/null 偷换进 string 字段或重放到另一路径都 fail closed，且 verify path bytes/mtime 零变化。

随后用 Step 2/3 已解析的 exact `$backupPath/$manifestPath` 执行 `seal-origin`，只允许 exclusive-create 固定 `data/compatibility/runtime/p0-origin-receipt-v1.json`；保存 CLI 返回的 `originReceiptFileSha256`，再以 `verify-origin-receipt --receipt <exact> --expected-receipt-file-sha256 <exact>` 做独立只读复验。P0 实施 evidence 必须记录 receipt exact path、receipt file SHA、backupId、backup SHA、Manifest SHA、logical SHA 与 databaseLineageId；这些值是 P4 唯一可消费的 lineage 输入，不再接受手工设置 `P0_RETAINED_ORIGIN_BACKUP`/`P0_RETAINED_ORIGIN_MANIFEST`。

再次执行 backend tests、Task 5 十三项集合、Task 5A 当前平台两项有效行为测试、OriginReceipt 五项定向测试、独立 verify、restore-check、sidecar 清洁检查与 git diff --check。报告实际命令、通过/平台 skip 数量、Live 快照 ID/hash/counts、OriginReceipt exact path/file hash、两次独立审查的 Important=0 evidence、未运行项及原因。只有 backup scoped gate、OriginReceipt gate 与两份审查通过，且完整套件为 0 failure 或随后由 P0.1 versioned baseline guard 证明 non-zero 集合/签名/hash 完全一致，才可继续；任何新增或漂移失败都停止并保留快照，不跳过验证，也不得把允许的既有 non-zero 报告成绿色。

---

## 自审结论

- Spec coverage：本计划仅覆盖当前 P0 “一致性备份 + Manifest + 隔离回滚演练”纵向切片；明确不包含 API 契约基线、React Gateway、OCR、Alembic 新表或 FastAPI。
- Placeholder scan：已检查并移除禁用的占位模式；所有错误码、命令、路径与预期行为均明确。
- Type consistency：公开 Interface 的 backupId、manifestSha256、logicalSha256 与 CLI JSON/Manifest 契约一致；`OriginReceipt` 另以 exact strict schema、databaseLineageId、receiptSha256 与 out-of-band originReceiptFileSha256 固定 P4–P6 唯一谱系起点。
- Commit policy：Skill 默认的逐任务提交被用户“不得提交或推送”覆盖，本计划不包含任何 Git 写操作。
