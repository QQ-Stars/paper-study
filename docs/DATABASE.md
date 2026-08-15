# 数据库详细设计 · Paper-Study

> 目标：用**一个 SQLite 文件** `data/app.db` 作为唯一数据中枢，Node(Web) 与 Python(Agent) 共读共写。
> 本文给出完整建表语句、字段含义、索引、去重、迁移、双语言访问方式与并发说明。

---

## 1. 为什么 SQLite（WAL 模式）

- **零运维**：单文件，不用单独装数据库服务，最适合本地起步。
- **够快够稳**：本项目读多写少、并发低，SQLite 完全胜任；将来数据量大/多用户再平滑迁 PostgreSQL（表结构基本不变）。
- **WAL（Write-Ahead Logging）**：允许"**一个写 + 多个读**同时进行"，正好匹配"Python 写论文 / Node 读论文+写笔记"的场景。

每次连接都先设置：
```sql
PRAGMA journal_mode = WAL;     -- 并发读写
PRAGMA foreign_keys = ON;      -- 开启外键约束
PRAGMA busy_timeout = 5000;    -- 遇到锁等待5秒，避免偶发 "database is locked"
PRAGMA synchronous = NORMAL;   -- WAL 下兼顾安全与性能
```

---

## 2. 完整建表语句（`db/schema.sql`）

```sql
-- ========== 论文主表 ==========
CREATE TABLE IF NOT EXISTS papers (
  id           TEXT PRIMARY KEY,          -- slug，人类可读且用于文件名/URL，如 "2310.14566_HallusionBench-CVPR24"
  source       TEXT NOT NULL,             -- 来源: semanticscholar|openalex|arxiv|manual|seed
  source_id    TEXT,                      -- 在该来源内的 id
  arxiv_id     TEXT,                      -- arXiv 编号(可空)，用于去重
  doi          TEXT,                      -- DOI(可空)，用于去重
  s2_id        TEXT,                      -- Semantic Scholar paperId(可空)
  openalex_id  TEXT,                      -- OpenAlex id(可空)
  title        TEXT NOT NULL,
  title_norm   TEXT,                      -- 标题归一化(小写去标点)，用于模糊去重
  authors      TEXT,                      -- JSON 数组字符串: ["A","B"]
  venue        TEXT,                      -- CVPR|ICCV|...|arXiv
  year         TEXT,                      -- "2024"(用文本，便于与前端筛选一致)
  abstract     TEXT,
  tldr         TEXT,                      -- AI 一句话总结(Semantic Scholar 免费; 无则 LLM 兜底)
  citations    INTEGER,                   -- 引用数(API)
  s2_fields    TEXT,                      -- S2 研究领域标签 JSON 数组(API)
  url          TEXT,                      -- 论文落地页
  pdf_url      TEXT,                      -- 远程 PDF 地址
  pdf_path     TEXT,                      -- 本地缓存路径 data/pdfs/<id>.pdf
  -- ↓↓↓ 大模型抽取/生成 ↓↓↓
  type         TEXT,                      -- 研究方向: 检测|缓解·解码|缓解·训练|机制|评测|定义|其他
  topic        TEXT,                      -- 主题: 知识-视觉冲突|多图|多物体|通用物体|语言先验|其他
  task         TEXT,
  models       TEXT,                      -- JSON 数组
  datasets     TEXT,                      -- JSON 数组
  contribution TEXT,                      -- 一句话核心贡献(LLM)
  tags         TEXT,                      -- JSON 数组(关键词, LLM)
  relevance    REAL,                      -- 与目标方向相关度 0~1(可空)
  explainer    TEXT,                      -- 自动生成的"科学方法论讲解" markdown(可空)
  extracted_by TEXT,                      -- 产出属性的模型名(溯源)，如 "deepseek-chat"
  order_no     INTEGER,                   -- 学习顺序(可空，2024 那批有 1..13)
  created_at   TEXT DEFAULT (datetime('now')),
  updated_at   TEXT DEFAULT (datetime('now'))
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_papers_arxiv ON papers(arxiv_id) WHERE arxiv_id IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS ux_papers_doi   ON papers(doi)      WHERE doi IS NOT NULL;
CREATE INDEX IF NOT EXISTS ix_papers_titlenorm ON papers(title_norm);
CREATE INDEX IF NOT EXISTS ix_papers_venue ON papers(venue);
CREATE INDEX IF NOT EXISTS ix_papers_year  ON papers(year);
CREATE INDEX IF NOT EXISTS ix_papers_type  ON papers(type);
CREATE INDEX IF NOT EXISTS ix_papers_topic ON papers(topic);

-- ========== 学习进度(单用户；将来加 user_id) ==========
CREATE TABLE IF NOT EXISTS progress (
  paper_id   TEXT PRIMARY KEY REFERENCES papers(id) ON DELETE CASCADE,
  status     TEXT NOT NULL DEFAULT '未开始',   -- 未开始|学习中|已理解
  updated_at TEXT DEFAULT (datetime('now'))
);

-- ========== 笔记(每篇一条；将来可 1:N) ==========
CREATE TABLE IF NOT EXISTS notes (
  paper_id   TEXT PRIMARY KEY REFERENCES papers(id) ON DELETE CASCADE,
  content    TEXT NOT NULL DEFAULT '',
  updated_at TEXT DEFAULT (datetime('now'))
);

-- ========== 论文向量(可选，语义检索用) ==========
CREATE TABLE IF NOT EXISTS paper_vectors (
  paper_id  TEXT PRIMARY KEY REFERENCES papers(id) ON DELETE CASCADE,
  dim       INTEGER,
  vector    BLOB                          -- 序列化 float32 向量(SPECTER2/自算)
);

-- ========== 采集任务(P5 后台任务用，先建好) ==========
CREATE TABLE IF NOT EXISTS ingest_jobs (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  query       TEXT,
  venues      TEXT,                       -- JSON 数组
  year_from   INTEGER,
  year_to     INTEGER,
  max_papers  INTEGER,
  min_relevance REAL,
  status      TEXT NOT NULL DEFAULT 'pending',  -- pending|running|done|error
  found       INTEGER DEFAULT 0,
  added       INTEGER DEFAULT 0,
  skipped     INTEGER DEFAULT 0,
  log         TEXT,
  created_at  TEXT DEFAULT (datetime('now')),
  finished_at TEXT
);

-- ========== 迁移版本(记录已执行的 schema 版本) ==========
CREATE TABLE IF NOT EXISTS schema_migrations (
  version    INTEGER PRIMARY KEY,
  applied_at TEXT DEFAULT (datetime('now'))
);
```

---

## 3. papers 字段速查

| 字段 | 来源 | 说明 |
|---|---|---|
| id / source / source_id | 系统 | 唯一标识与溯源 |
| arxiv_id / doi / title_norm | 系统 | **去重三件套** |
| title/authors/venue/year/abstract/url/pdf_url | 数据源 | 原始元数据 |
| title_zh | LLM/人工 | 可空的中文标题译文；原始英文 `title` 始终为权威来源 |
| pdf_path | 系统 | 本地缓存 PDF |
| type/topic/task/models/datasets/contribution/tags/relevance | **大模型** | 自定义分类与理解 |
| tldr/citations/s2_fields | **聚合API** | TLDR、引用数、领域免费拿 |
| explainer | **大模型** | 自动讲解 markdown |
| extracted_by | 系统 | 哪个模型抽的(溯源/复现) |
| order_no | 系统/人工 | 学习顺序 |

> **JSON 字段约定**：`authors/models/datasets/tags` 在库里存 JSON 字符串；Node 用 `JSON.parse`，Python 用 `json.loads`。读出来给前端时解析成数组。

---

## 4. 去重策略（避免重复入库）

按优先级判断一篇是否已存在：
1. `arxiv_id` 命中 → 同一篇（最可靠）
2. `doi` 命中 → 同一篇
3. `title_norm` 命中（标题归一化：转小写、去标点/空格/版本号）→ 高度疑似，跳过或合并

**跨源合并**：同一篇可能既在 arXiv 又在 CVF。策略：先入库者保留；后来者若 `title_norm` 命中，则**补全缺失字段**（如补 arxiv_id、补 venue），不新建记录。

`title_norm` 生成（两端一致实现）：
```
norm(s) = lower(s) 去掉所有非字母数字字符
"HallusionBench: An Advanced..." -> "hallusionbenchanadvanced..."
```

---

## 5. 迁移计划（P1：现有 JSON/文件 → SQLite）

一次性迁移脚本 `agent/migrate_seed.py`（或 Node `scripts/migrate.js`）：

| 现有 | 目标表 | 映射 |
|---|---|---|
| `data/papers.json`(38条) | papers | id/title/venue/year/type/topic/order→order_no；source='seed'；从 id 前缀解析 arxiv_id(如 `2310.14566`)；pdf_path 指向 `../paper/<file>` |
| `../paper/<id>.md`(讲解) | papers.explainer | 读取文件内容写入对应行 |
| `data/progress.json` | progress | key=paper_id, value=status |
| `notes/<id>.md` | notes | 文件名=paper_id, 内容=content |

迁移伪代码：
```python
for p in load("data/papers.json"):
    arxiv = p["id"].split("_")[0] if re.match(r"\d{4}\.\d{4,5}", p["id"]) else None
    db.upsert_paper(id=p["id"], source="seed", arxiv_id=arxiv, title=p["title"],
        venue=p["venue"], year=p["year"], type=p["type"], topic=p.get("topic"),
        order_no=p.get("order"), pdf_path=f"../paper/{p['file']}",
        title_norm=norm(p["title"]),
        explainer=read_if_exists(f"../paper/{p['id']}.md"))
for pid, status in load("data/progress.json").items(): db.set_status(pid, status)
for f in glob("notes/*.md"): db.set_note(stem(f), read(f))
db.set_migration(1)
```
> 原 JSON/文件**保留**（作为种子与回退）。迁移可重复执行（幂等 upsert）。

---

## 6. Node 访问（`better-sqlite3`，同步、简单）

安装（装到项目内 `node_modules`）：`npm i better-sqlite3`

```js
// db.js
const Database = require('better-sqlite3');
const db = new Database(process.env.DB_PATH || './data/app.db');
db.pragma('journal_mode = WAL');
db.pragma('foreign_keys = ON');
db.pragma('busy_timeout = 5000');

const listPapers = () => db.prepare(`
  SELECT p.*, COALESCE(g.status,'未开始') AS status,
         (n.content IS NOT NULL AND length(n.content)>0) AS hasNote
  FROM papers p
  LEFT JOIN progress g ON g.paper_id=p.id
  LEFT JOIN notes    n ON n.paper_id=p.id
  ORDER BY p.year, p.order_no`).all();

const setNote = (id, content) => db.prepare(`
  INSERT INTO notes(paper_id,content,updated_at) VALUES(?,?,datetime('now'))
  ON CONFLICT(paper_id) DO UPDATE SET content=excluded.content, updated_at=datetime('now')`).run(id, content);

const setStatus = (id, status) => db.prepare(`
  INSERT INTO progress(paper_id,status,updated_at) VALUES(?,?,datetime('now'))
  ON CONFLICT(paper_id) DO UPDATE SET status=excluded.status, updated_at=datetime('now')`).run(id, status);

module.exports = { db, listPapers, setNote, setStatus };
```
> `server.js` 的 `/api/papers /api/note /api/progress` 改为调用这些函数，前端**完全不用改**（返回结构保持一致：含 `status` 与 `hasNote`，JSON 字段解析成数组）。

> 备选：Node 22 自带实验性 `node:sqlite`，可零依赖；稳妥起见先用 `better-sqlite3`（成熟、有预编译包，免编译）。

---

## 7. Python 访问（标准库 `sqlite3`，零额外依赖）

```python
# agent/db.py
import sqlite3, json, os
def connect():
    con = sqlite3.connect(os.getenv("DB_PATH","./data/app.db"))
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA foreign_keys=ON")
    con.execute("PRAGMA busy_timeout=5000")
    return con

def exists(con, *, arxiv_id=None, title_norm=None) -> bool:
    if arxiv_id and con.execute("SELECT 1 FROM papers WHERE arxiv_id=?", (arxiv_id,)).fetchone():
        return True
    if title_norm and con.execute("SELECT 1 FROM papers WHERE title_norm=?", (title_norm,)).fetchone():
        return True
    return False

def insert_paper(con, row: dict):
    cols = ",".join(row); ph = ",".join("?"*len(row))
    con.execute(f"INSERT OR IGNORE INTO papers({cols}) VALUES({ph})", list(row.values()))
    con.commit()
```

---

## 8. 并发说明

- WAL 下：Python 写 `papers` 与 Node 写 `notes/progress` 互不阻塞（不同表、低频）。
- 偶发锁由 `busy_timeout` 自动重试等待。
- 大批量导入时，Python 端用**单事务批量提交**（`BEGIN ... COMMIT`）减少写次数。

## 9. 备份

不要直接复制正在使用的 `data/app.db`。WAL 中可能包含已经提交、但尚未 checkpoint
到主文件的数据。项目提供的备份命令使用 Python `sqlite3.Connection.backup()` 创建
一致性快照，随后验证完整性、外键、Schema、表数量和逻辑内容哈希。备份与
Manifest 使用同目录原子 no-clobber 发布：优先 hard-link；Windows 卷不支持 hard-link
时回退到 Windows `rename` 的“目标存在即失败”语义，POSIX 不使用该回退。目标路径已
存在或在发布瞬间由其他进程创建时，命令失败并保留既有字节，不会执行覆盖式 replace。
exFAT 在跨目录 rename 时可能为同一文件重新分配 inode；Windows 回退因此通过已绑定文件
句柄执行 no-replace rename，再把同一对象重开为 no-share-delete 句柄并保持到完整验证结束。
发布路径必须指向该句柄绑定对象，SHA-256 也从绑定描述符计算，不能用同字节替换绕过所有权门禁。

Windows：

```powershell
.\.venv\Scripts\python.exe -m backend.app.cli.database_backup create `
  --database data/app.db `
  --output-directory data/backups `
  --label pre-migration
```

macOS / Linux：

```bash
python -m backend.app.cli.database_backup create \
  --database data/app.db \
  --output-directory data/backups \
  --label pre-migration
```

当前创建器写入格式 v2；验证与恢复接口继续读取格式 v1 快照。v2 会把 SQLite 内部的
`sqlite_sequence` 纳入版本化逻辑指纹，避免 AUTOINCREMENT 状态变化绕过内容校验。
格式 v1 没有这项绑定，因此其校验强度弱于 v2；兼容读取 v1 不代表它能证明
`sqlite_sequence` 未被修改，新备份必须使用 v2。
成功时 stdout 只输出一行 JSON，其中包含 `formatVersion`、`backupId`、`backupPath`、
`manifestPath`、备份/Manifest 文件 SHA-256、Manifest 规范载荷 SHA-256、逻辑 SHA-256、
逐表数量/哈希和关键内容数量/哈希。Manifest 不包含 API Key、论文正文或笔记正文，
但 SQLite 备份本身包含完整用户数据，必须按 `data/app.db` 的敏感级别保护。

再次验证已有备份。校验在文件哈希前和逻辑指纹后都要求不存在 `-wal`、`-shm`
或 `-journal`；检测到这些 sidecar 时只返回分类错误，不删除调用者文件：

```powershell
.\.venv\Scripts\python.exe -m backend.app.cli.database_backup verify `
  --backup <backupPath> `
  --manifest <manifestPath>
```

回滚演练只允许恢复到命令创建的唯一新目录。`--output-directory` 必须是目录根，
不得是 `.db`、`.db3`、`.sqlite`、`.sqlite3`、`-wal`、`-shm`、`-journal` 路径或既有普通文件：

```powershell
.\.venv\Scripts\python.exe -m backend.app.cli.database_backup restore-check `
  --backup <backupPath> `
  --manifest <manifestPath> `
  --output-directory data/backups
```

该安全接口不接受任意目标文件，而是在输出目录下创建唯一的
`restore-validation-*/app.db`，因此不会覆盖 Live DB 或其 `-wal`、`-shm`、`-journal`
sidecar。恢复会在备份验证前后以及首次写入前复检输出路径的 symlink/reparse 状态。

恢复副本的逻辑证明按平台保持同一安全结论。POSIX 通过已绑定的 descriptor 路径直接
打开副本并重算 SQLite 逻辑指纹。Windows 的 Python SQLite VFS 不共享删除权限，无法在
no-share-delete 目标句柄保持期间安全地再次按 pathname 打开同一文件；因此 Windows 先在
绑定句柄上证明副本的完整 size 与 SHA-256 精确等于已独立验证的 backup，再复用该
Manifest 的逻辑指纹。任一后续同 identity 原地写入仍会被返回前的末次 size/SHA-256
检查拒绝，过程中不会为重新打开 SQLite 而提前释放所有权句柄。

失败清理按平台采取保守策略。Windows 对生成文件和空目录使用句柄绑定删除，并在句柄上
复核所有权；validation child 与其中的 `app.db` 都通过相对已绑定父句柄的原生
`NtCreateFile` 创建，不经过未绑定 pathname 窗口。POSIX 的 restore output root 必须由
当前 euid 拥有且权限严格为 `0700`，same-euid 与特权进程属于同一信任边界；不满足时会在
创建 validation child 前以分类错误拒绝。POSIX 只清理本次调用创建的私有
staging/validation 命名空间，且在
路径删除前再次核对目标、父目录身份和 symlink/reparse 链。无法证明私有父目录所有权或
检测到身份漂移时会 fail closed，可能留下需要人工检查的孤立文件。POSIX 的最终身份复检
与路径删除之间仍存在极小的 rename 竞态窗口，因此这里不声称绝对 TOCTOU 安全。碰撞目录、
已检测到的并发替换目标和调用者 sidecar 均保留。
把备份正式安装为 `data/app.db` 必须先停止 Node、FastAPI、Worker、Python Agent 和
MCP，并通过后续的离线恢复事务完成；不能把上述演练命令当作在线恢复。

### P0 OriginReceipt

只有同一份 exact backup/Manifest 已通过独立 `verify` 与 `restore-check` 后，才可将它封存为
固定路径 `data/compatibility/runtime/p0-origin-receipt-v1.json`。写入采用 exclusive-create，
既有收据不会被覆盖：

```powershell
.\.venv\Scripts\python.exe -m backend.app.cli.database_backup seal-origin `
  --backup <exact-backupPath> `
  --manifest <exact-manifestPath>
```

收据使用固定顺序的 12 字段 canonical UTF-8 JSON，无 BOM、空白或末尾换行。
`manifestSha256` 绑定 Manifest 文件的原始字节，而不是 Manifest 内部的规范 payload hash；
`databaseLineageId` 绑定 backup ID、Manifest 文件 SHA-256 与逻辑 SHA-256。
`receiptSha256` 只覆盖前 11 个字段。完整 12 字段文件的 SHA-256 以
`originReceiptFileSha256` 仅由 CLI stdout 返回，不写回收据，必须作为 out-of-band 证据保存。

独立只读复验必须显式传入该完整文件 SHA-256：

```powershell
.\.venv\Scripts\python.exe -m backend.app.cli.database_backup verify-origin-receipt `
  --receipt data/compatibility/runtime/p0-origin-receipt-v1.json `
  --expected-receipt-file-sha256 <exact-originReceiptFileSha256>
```

验证拒绝重复/未知/缺失字段、非 canonical 序列化、布尔型 `schemaVersion`、空字符串、NUL、
非小写 64 位十六进制 hash、内部 lineage/receipt hash 不一致，以及 out-of-band 文件 SHA
不匹配。此命令以只读方式复验收据及其引用的 exact backup/Manifest，不修改这些文件或
Live SQLite。

## 10. P0.1 兼容基线与紧急回滚

P0.1 启动设置在进程启动时只读取一次。修改任一值后必须重启 Node 进程或容器；运行中
修改环境变量不会切换 owner、流水线、Artifact 读写或 OCR。后端紧急回滚使用完整配置：

```powershell
$env:API_BACKEND_MODE = 'legacy'
$env:DOCUMENT_PIPELINE_MODE = 'legacy'
$env:GENERATION_PIPELINE_MODE = 'legacy'
$env:ARTIFACT_READ_MODE = 'legacy'
$env:ARTIFACT_WRITE_MODE = 'legacy'
$env:OCR_ENABLED = '0'
$env:UI_ENTRY = 'react'
```

`UI_ENTRY=legacy` 是独立的 UI 根入口回滚，不是后端回滚的必要条件。P0 中只有上述
legacy/off 组合可启动；`shadow`、`python`、`p1`、`prefer_new`、`dual` 或启用 OCR 都会在
绑定 socket、打开 SQLite 或构造 Provider 前以命名配置错误拒绝，不会静默退回 legacy。
`shadow` 的解析语义固定为只读，POST/PUT/PATCH/DELETE 与 NDJSON 副作用不得执行。

React 既有测试基线位于 `contracts/pre-existing-test-failures-v1.json`，由两次独立运行精确命令
`npm.cmd run test:run --prefix frontend` 后接受。本次两次原始 suite 退出码均为 `0`，因此
`failedTestIds`、`normalizedStackSignatures` 与 `relatedFileSha256` 都是空数组。后续 P0–P5
必须用 `scripts/pre-existing-failure-baseline.mjs verify` 复验；ID、签名、关联文件 hash 或
当前 slice 触及关联路径时立即停止。现有 Node HTTP/NDJSON 合同记录在
`contracts/legacy-api-v1.json`，共 49 个 method/path 记录，并由 OS 分配回环端口、临时 SQLite
和确定性测试 preload 的真实 `server.js` 子进程黑盒覆盖。

P0.1 入口的新 Live 快照证据如下。创建前后 `data/app.db` 的 SHA-256、长度、创建/修改时间和
属性完全一致；WAL 完全一致；SHM 的 SHA-256、长度、创建时间和属性一致，仅只读 SQLite 在
exFAT 上更新了 mtime：

- backup ID：`d11be39da7d54b2cb837560ac00cf040`
- backup：`data/backups/app-pre-p0-compatibility-20260809T025704Z-d11be39da7d5.sqlite3`
- backup SHA-256：`5b14f137d97baa526ae2a8a6d981a22cb9e9f9f23cb5439ea6f083f78d492f75`
- Manifest：`data/backups/app-pre-p0-compatibility-20260809T025704Z-d11be39da7d5.sqlite3.manifest.json`
- Manifest 文件 SHA-256：`32b88dea0a88e70b1e18bb58b37e20b777ddfccbce1eaea6ff22f7145e5f97c1`
- logical SHA-256：`92ab98428b08700205ff4453013bfd84bfd910b34e84f57862153d8691755088`
- 隔离恢复：`data/backups/restore-checks/restore-validation-dad1b7e5fe454a77919a907f88526dbe/app.db`

该 backup/Manifest 已通过独立 `verify`，隔离恢复已通过 `restore-check`，两端均无 WAL、SHM
或 journal。P0 OriginReceipt 继续绑定原始 P0 快照 `88c5c8690be44965bf86ececc5e0f2d2`；
其外部完整文件 SHA-256 为
`7428474fb74bee7bbe6db97a56f08f30520f7d020ff51c149e85ea8a27be6224`，本次已用该外部值重新
验证收据、backup、Manifest、逻辑 hash 与数据库 lineage。

## 11. P1 domain/data foundation 与运维门禁

P1 的固定 Alembic revision 是 `20260807_01`。它只新增五张空表：
`document_sources`、`generated_artifacts`、`processing_jobs`、`document_chunks`、
`obsidian_exports`。CLI 从不自动执行 Alembic；任一门禁失败都必须在 Live mutation 之前停止。
本阶段以 Python 3.10 为兼容基线。Do not rebuild the existing virtual environment；在现有 `.venv`
中安装 hash lock 即可。

### 11.1 身份、来源与任务约束

SourceDocument cache identity 是
`(paper_id, pdf_sha256, mode, provider, model, options_hash, processing_version)`；
GeneratedArtifact version identity 是
`(source_document_id, kind, generator_provider, generator_model, prompt_version)`。
这两个 identity 都是完整 key，不能省略字段、猜测来源或用当前 PDF 替代已证明的来源。

ArtifactKind 的七个规范值是：
`explainer, translation, summary, outline, study_card, classification, metadata`。
ProcessingJob type 的七个规范值是：
`source_materialize, ocr, explain, translate, embed, obsidian_export, obsidian_sync`。
其中 source_materialize、ocr、explain、translate、embed 的 paper_id and source_mode are both required；
source_materialize 只允许 native，ocr 只允许 ocr。obsidian_export requires paper_id，但 source_mode
可为 NULL；obsidian_sync permits paper_id and source_mode to be NULL。attempt 必须非负，max_attempts
至少为 1，idempotency_key 不得为空。

No historical backfill：P1 不为旧 explainer/translation 伪造 SourceDocument 或 provenance。
旧内容继续标记为 legacy provenance，只从 P1 启用后的真实生成开始建立可追踪 lineage。

删除 Paper 时，`obsidian_exports.paper_id ON DELETE CASCADE` 只删除 database ledger row；它不删除
Vault 文件。Vault manifest 必须保留一个 non-auto-cleanable orphan/tombstone，供人工审计和清理。

### 11.2 CredentialStore、安全边界与探针

四类 Credential 的有效值优先级固定为 `environment -> Keyring -> legacy settings`：

| kind | environment | Keyring username (`service=study-app`) | legacy field |
|---|---|---|---|
| llm | `LLM_API_KEY` | `credential:llm` | `apiKey` |
| ocr | `OCR_API_KEY` | `credential:ocr` | `ocrApiKey` |
| embedding | `EMBED_API_KEY` | `credential:embedding` | `embedApiKey` |
| semantic_scholar | `S2_API_KEY` | `credential:semantic_scholar` | `s2ApiKey` |

状态接口只返回 `hasKey`、遮罩后的 `keyTail` 和是否由环境变量管理，不返回完整 secret。
Blank submission preserves the current credential；空白输入不是删除。Explicit clear 必须调用 clear
操作，并同时清理 Keyring 与 legacy field。更新、迁移和补偿后的 readback 使用常量时间比较；无法确认
补偿结果时返回 indeterminate，而不是声称成功。

LLM 连通性测试只发送仓库内 fixed fixture；OCR 只在 provider contract 明确验证后发送固定小图，
否则返回 `OCR_PROVIDER_CONTRACT_UNVERIFIED`。embedding 和 semantic_scholar 当前返回
`CREDENTIAL_PROBE_UNSUPPORTED`，不得用真实论文、PDF、笔记或用户输入做探针。

P1 仍有 retained legacy plaintext security debt：写 Keyring 后暂时同步 legacy settings 中的明文字段，
原因是 Node runtime rollback 仍依赖这些字段。No P0-P6 phase removes 这些 legacy fields；最终删除必须是
后续独立迁移，且需先证明 Node rollback 已不再读取它们。

### 11.3 备份与隔离迁移演练

Live 迁移前必须依次创建新快照、独立验证并做隔离恢复：

```powershell
.\.venv\Scripts\python.exe -m backend.app.cli.database_backup create --database data/app.db --output-directory data/backups --label pre-p1-domain-data
.\.venv\Scripts\python.exe -m backend.app.cli.database_backup verify --backup <backupPath> --manifest <manifestPath>
.\.venv\Scripts\python.exe -m backend.app.cli.database_backup restore-check --backup <backupPath> --manifest <manifestPath> --output-directory data/backups/p1-restore-checks
.\.venv\Scripts\python.exe -m backend.app.cli.database_backup inspect --database <restoredPath>
```

只在 `restore-validation-*/app.db` 上做 `upgrade -> downgrade -> re-upgrade`。每一步都要验证：

- 每个 legacy table count/hash 与迁移前一致；
- five P1 table counts are zero；
- exactly one Alembic head 为 `20260807_01 (head)`；
- `quick_check=ok`、`integrity_check=ok`、`foreign_key_violations=0`；
- verified backup 与 Live 的字节、SHA-256 和文件元数据未被隔离演练改变。

空表 downgrade 可执行；任意 P1 表非空时必须以 `P1_DOWNGRADE_NONEMPTY` 拒绝，且不能先丢表。

### 11.4 Live gate、canary 与 runtime rollback

迁移 Live 前要记录 Node、Python Agent、worker、scheduler、FastAPI 和 MCP 等所有 writer 已停止，
并用只读 inspect 记录迁移前的 legacy count/hash、健康值、Live 文件 SHA-256、字节和元数据。
只有 fresh create/verify/restore-check、隔离 upgrade/downgrade/re-upgrade、单 head 与 writer-stop 全绿时，
本次授权才允许执行加法迁移：

```powershell
$env:DB_PATH = (Resolve-Path -LiteralPath 'data/app.db').Path
.\.venv\Scripts\python.exe -m alembic -c backend/alembic.ini upgrade 20260807_01
```

升级后必须再次证明 legacy table count/hash 不变、five P1 table counts are zero、exactly one Alembic head、
`quick_check=ok`、`integrity_check=ok` 和 `foreign_key_violations=0`。先用 emergency 配置重启，再单独选择
canary；环境值只在进程启动时读取，任何切换都需要 restart。

P1 canary：

```text
API_BACKEND_MODE=legacy
DOCUMENT_PIPELINE_MODE=p1
GENERATION_PIPELINE_MODE=p1
ARTIFACT_READ_MODE=prefer_new
ARTIFACT_WRITE_MODE=dual
OCR_ENABLED=0
```

Emergency runtime rollback：

```text
API_BACKEND_MODE=legacy
DOCUMENT_PIPELINE_MODE=legacy
GENERATION_PIPELINE_MODE=legacy
ARTIFACT_READ_MODE=legacy
ARTIFACT_WRITE_MODE=legacy
OCR_ENABLED=0
```

Runtime rollback comes before schema downgrade：先停止 canary 并以上述 emergency 值重启；保留 P1 表和
数据以便再次启用。正常向前路径绝不对 Live downgrade。只有停止所有 writer、创建并验证新快照、证明
五表为空且明确放弃继续向前迁移后，才可单独执行 `alembic downgrade base`。

灾难恢复也必须是独立、显式的决定。恢复 pre-upgrade snapshot 会丢弃快照之后的全部写入——restoring a
pre-upgrade snapshot discards every write made after that snapshot；因此不能把 restore-check 当作在线恢复，
也不能在未记录数据损失决定时覆盖 `data/app.db`。

## 12. P2 processing queue backup、迁移与回滚门禁

P2 的 backup fingerprint 同时保留 full-P2 projection 与跨 revision 稳定的 P1 core
projection。固定 ordered tuple（顺序本身属于契约）如下：

```text
p1CoreDocumentSources = (id,paper_id,mode,status,provider,model,pdf_sha256,options_hash,content_sha256,markdown,page_count,processing_version,error_code,error_message,created_at,updated_at)
p1CoreGeneratedArtifacts = (id,paper_id,kind,source_document_id,status,content,content_sha256,generator_provider,generator_model,prompt_version,error_code,error_message,created_at,updated_at)
p1CoreProcessingJobs = (id,paper_id,job_type,source_mode,status,progress_json,attempt,max_attempts,idempotency_key,error_code,error_message,created_at,started_at,finished_at,cancelled_at)
processingJobSpecs = (id,spec_json)
```

`p1CoreDocumentSources` 排除 P2 columns `source_key|ready_at|stale_at`；
`p1CoreGeneratedArtifacts` 排除 `artifact_key|ready_at|stale_at`；
`p1CoreProcessingJobs` 排除
`source_document_id|artifact_id|spec_json|available_at|lease_owner|lease_token|lease_expires_at|heartbeat_at|cancel_requested_at|result_json|updated_at|retry_of_job_id|retry_sequence`。
这些 exclusion 只排除 additive columns，不过滤任何 row；NULL 也按 SQLite encoder 进入 hash。
Full-P2 的 `documentSources`、`generatedArtifacts`、`processingJobs`、
`processingJobSpecs` 都必须有 count/hash；后两者 count 必须等于 `processing_jobs` table count，
每条 `spec_json` 必须 strict decode。Manifest、日志和错误不得输出 spec 正文、secret 或 lease token，
只能输出 count/hash 与出错 row id。

P2 schema inventory 必须同时存在 exact triggers
`processing_jobs_spec_guard_insert` 与 `processing_jobs_spec_guard_update`，并匹配固定的
normalized SQL SHA-256。缺失、改名、额外 lookalike、SQL hash 漂移、spec noncanonical、
schemaVersion 错误、secret key 或 row binding mismatch 都是停止条件。

### 12.1 Writer drain 与 P0 精确快照

固定停止顺序是：停止新 enqueue → 停止 worker claim → 等待/取消 running jobs → 停止 API writer。
还必须停止 Node、FastAPI、scheduler、Python Agent、MCP 等所有 SQLite writer，并记录 drain 证据。
随后用 P0 CLI 对精确 Live DB 执行 create → independent verify → isolated restore-check，记录 CLI
返回的 exact backupPath、manifestPath、backup SHA-256、Manifest SHA-256、logical SHA-256 与 restoredPath：

```powershell
.\.venv\Scripts\python.exe -B -m backend.app.cli.database_backup create --database data/app.db --output-directory data/backups --label pre-p2-processing
.\.venv\Scripts\python.exe -B -m backend.app.cli.database_backup verify --backup <backupPath> --manifest <manifestPath>
.\.venv\Scripts\python.exe -B -m backend.app.cli.database_backup restore-check --backup <backupPath> --manifest <manifestPath> --output-directory data/backups/p2-restore-checks
```

`verify` 必须是独立调用，`restore-check` 必须落在新建的
`restore-validation-*/app.db`，不得指向或覆盖 Live。Live before/after 的 DB bytes、length、SHA-256、
mtime/ctime/attributes 和 WAL/SHM/journal metadata 必须按 P0 规则记录、比较；继续独立验证
OriginReceipt 外部 SHA-256，并保留既有 no-clobber、bound root、sidecar 与并发替换竞态门禁。

### 12.2 只在 restore-check 副本演练

只能在上述可丢弃的 restored copy 上执行下列固定序列，不得在 Live 使用 downgrade 或 data-loss opt-in：

```powershell
.\.venv\Scripts\python.exe -B -m alembic -c backend/alembic.ini upgrade 20260807_02
.\.venv\Scripts\python.exe -B -m alembic -c backend/alembic.ini downgrade 20260807_01
.\.venv\Scripts\python.exe -B -m alembic -c backend/alembic.ini -x allow_p2_data_loss=true downgrade 20260807_01
.\.venv\Scripts\python.exe -B -m alembic -c backend/alembic.ini upgrade 20260807_02
```

演练状态必须是 `20260807_01 → 20260807_02 → 20260807_01 → 20260807_02`。
当 `processing_jobs` nonempty 时，第一次无 x argument downgrade 必须以
`P2_DOWNGRADE_BLOCKED_NONEMPTY` 停止且零数据变化；只有证明该 isolated disposable copy 可丢弃后，
才可显式使用 `-x allow_p2_data_loss=true downgrade 20260807_01`。每一阶段都 inspect 并比较三个
P1 core count/hash；P2 before-state 另验证 `processingJobs`/`processingJobSpecs` count/hash、全部 spec
strict decode 与两项 trigger inventory。任何缺键或未解释 delta 都停止。

恢复副本演练、全部 P2 tests、fresh verified snapshot、writer drain 与审查全绿后，才允许对 Live
执行 additive `alembic -c backend/alembic.ini upgrade 20260807_02`。Live before/after 必须执行
core/spec/inventory stop conditions：三个 P1 core count/hash 前后相等；after-state 的四个 full-P2 keys、
spec count equality/strict decode、三张 auxiliary table 与两个 exact trigger normalized SQL SHA 全部存在；
健康检查、唯一 Alembic head、12 张 legacy table count/hash 也必须通过。

### 12.3 Runtime rollback 优先于 schema rollback

先停止 P2 worker claim，再以完整配置重启运行时：

```text
API_BACKEND_MODE=legacy
DOCUMENT_PIPELINE_MODE=legacy
GENERATION_PIPELINE_MODE=legacy
ARTIFACT_READ_MODE=legacy
ARTIFACT_WRITE_MODE=legacy
OCR_ENABLED=0
```

Runtime rollback 保留 P2 additive tables、`spec_json`、events、checkpoints 及全部 queued/running/terminal
jobs；不清空任务，也不从 progress 重建请求。任何 nonempty `processing_jobs`（包括 backfilled
`spec_json`）默认阻止 schema downgrade。Schema downgrade 仅适用于 isolated disposable copy，且必须
显式 `allow_p2_data_loss=true`；否则只能在所有 writer 离线后恢复精确 P0 snapshot，并明确记录
snapshot 后数据丢失。Downgrade 前后都验证 P1 core count/hash，P2 before-state 还要验证 spec
count/hash/strict decode 与 trigger inventory；restore-check 本身绝不是安装或在线恢复授权。
## 13. P3 source consumers、search 与回滚门禁

P3 的唯一加法迁移序列为 `20260807_02 → 20260807_03`，最终且唯一的 Alembic
head 必须是 `20260807_03 (head)`。迁移前先验证 SQLite 支持固定 tokenizer
`trigram case_sensitive 0 remove_diacritics 1`；缺失 FTS5 或 tokenizer 选项时立即停止，
不得降级 tokenizer。P3 search query path never re-embeds：查询只读取 ready chunk 和与请求
profile 完全匹配的 ready embedding。embedding 构建必须由显式 index command 入队，不能由查询
触发。source stale cascade 在一个显式写事务内把旧 source、dependent artifact、chunk 和 vector
标记 stale，删除 stale artifact head，并取消 queued job 或向 running job 请求 cooperative cancel；
query path 不得修复或回填这些状态。

### 13.1 P3 logical backup fingerprints 与 FTS 诊断

Manifest 必须记录以下 ordered logical projections 的 `contentCounts/contentSha256`：

```text
documentChunks = (id,source_document_id,sequence,heading_path,page_start,page_end,content,content_sha256,token_count,status,content_kind,chunk_key,chunking_version,source_content_sha256,char_start,char_end,created_at,updated_at,stale_at)
chunkEmbeddings = (id,chunk_id,source_document_id,provider,model,embedding_version,dimensions,vector_sha256,chunk_content_sha256,status,error_code,error_message,created_at,updated_at,stale_at)
translationCheckpoints = (artifact_id,chunk_id,sequence,source_content_sha256,provider,model,prompt_version,status,translated_markdown,content_sha256,attempt,error_code,error_message,created_at,updated_at)
documentChunksFtsCoverage = logical (rowid,id,source_document_id,sequence,heading_path,content) coverage
documentChunksFtsIntegrity = successful FTS5 integrity-check sentinel
```

`chunkEmbeddings` 只记录 stored `vector_sha256` 与 embedding identity，不把 vector bytes 写进
Manifest、日志或错误。`document_chunks_fts` 及其 `_data|_idx|_content|_docsize|_config`
shadow tables 的物理布局不进入 `tableCounts/tableSha256`；这些实现细节可能随 SQLite 版本改变。
替代诊断是 exact trigger inventory、logical row coverage，以及在隔离内存快照上执行：

```sql
INSERT INTO document_chunks_fts(document_chunks_fts,rank) VALUES('integrity-check',1);
```

缺失或额外的 `document_chunks_fts_ai|document_chunks_fts_ad|document_chunks_fts_au` trigger、
external-content/table-rowid/tokenizer 漂移、coverage 差异或 integrity-check 失败全部是停止条件。
经诊断确认仅索引可重建，且 source/chunk identity 没有漂移时，只能由显式 index command 在写事务
执行 documented rebuild，再复验 coverage 与 integrity：

```sql
INSERT INTO document_chunks_fts(document_chunks_fts) VALUES('rebuild');
INSERT INTO document_chunks_fts(document_chunks_fts,rank) VALUES('integrity-check',1);
```

### 13.2 Writer drain、fresh backup 与 12-table/8-content guards

固定停止顺序是：停止新 enqueue → 停止 worker claim → 等待/取消 running jobs → 停止 API writer。
随后停止 Node、FastAPI、scheduler、Python Agent 和 MCP 等全部 SQLite writer，并记录 owner、
database identity、WAL/SHM/journal 与 drain 证据。对 exact Live path 创建 fresh snapshot，然后用两个
独立命令做 verify 和隔离 restore-check：

```powershell
.\.venv\Scripts\python.exe -B -m backend.app.cli.database_backup create --database data/app.db --output-directory data/backups --label pre-p3-source-consumers-search
.\.venv\Scripts\python.exe -B -m backend.app.cli.database_backup verify --backup <exact-backupPath> --manifest <exact-manifestPath>
.\.venv\Scripts\python.exe -B -m backend.app.cli.database_backup restore-check --backup <exact-backupPath> --manifest <exact-manifestPath> --output-directory data/backups/restore-checks
```

每次 inspect 都应用 map-presence guard，要求下列固定 12 张 legacy table 的 count/hash key 都存在；
upgrade/downgrade/re-upgrade 和 Live before/after 另应用 before/after equality guard：

- `papers` tableCounts/tableSha256
- `progress` tableCounts/tableSha256
- `paper_reviews` tableCounts/tableSha256
- `notes` tableCounts/tableSha256
- `favorites` tableCounts/tableSha256
- `translations` tableCounts/tableSha256
- `paper_vectors` tableCounts/tableSha256
- `cite_edges` tableCounts/tableSha256
- `ingest_jobs` tableCounts/tableSha256
- `job_candidates` tableCounts/tableSha256
- `job_schedules` tableCounts/tableSha256
- `schema_migrations` tableCounts/tableSha256

跨 P2/P3 必须稳定的 content map 也同时执行 presence/equality guard：

- `paperIds` contentCounts/contentSha256
- `explainers` contentCounts/contentSha256
- `translations` contentCounts/contentSha256
- `notes` contentCounts/contentSha256
- `paperVectors` contentCounts/contentSha256
- `documentSources` contentCounts/contentSha256
- `generatedArtifacts` contentCounts/contentSha256
- `processingJobs` contentCounts/contentSha256

任一缺键、hash 漂移、非唯一 head、`quick_check`/`integrity_check`/foreign-key failure、
OriginReceipt 外部 SHA 不匹配或 Live 文件 bytes/SHA/metadata 意外变化，都必须在下一次 Live write 前停止。

### 13.3 Only restored-copy drill 与 runtime rollback

迁移演练 only on restore-validation-*/app.db；先证明 resolved path 位于本次 restore-check root，
且绝不等于 Live `data/app.db`。固定命令序列为：

```powershell
.\.venv\Scripts\python.exe -B -m alembic -c backend/alembic.ini upgrade 20260807_03
.\.venv\Scripts\python.exe -B -m alembic -c backend/alembic.ini downgrade 20260807_02
.\.venv\Scripts\python.exe -B -m alembic -c backend/alembic.ini -x allow_p3_data_loss=true downgrade 20260807_02
.\.venv\Scripts\python.exe -B -m alembic -c backend/alembic.ini upgrade 20260807_03
```

当 `document_chunks`、`document_chunk_embeddings` 或 `artifact_translation_checkpoints` 有数据时，
第一次无 opt-in downgrade 必须以 `P3_DOWNGRADE_BLOCKED_NONEMPTY` 失败且零数据变化。只有该副本已证明
是 disposable restored copy 后，才可执行 explicit destructive downgrade；不得在 Live 使用
allow_p3_data_loss=true。每一步都复验 12-table/8-content equality、P2/P3 validators 和唯一 revision。
不得在 Live 使用 allow_p3_data_loss=true；该句是不可覆盖的 production stop condition。

运行时回滚优先于 schema rollback。先停止 P3 worker claim，再使用完整 legacy 配置重启：

```text
API_BACKEND_MODE=legacy
DOCUMENT_PIPELINE_MODE=legacy
GENERATION_PIPELINE_MODE=legacy
ARTIFACT_READ_MODE=legacy
ARTIFACT_WRITE_MODE=legacy
OCR_ENABLED=0
```

Runtime rollback 保留 additive P3 schema、chunks、embeddings、translation checkpoints、FTS 与所有
queued/running/terminal jobs；不清空、不重建、不 downgrade。恢复 pre-P3 snapshot 是独立 disaster
recovery 决定，会丢弃 snapshot 后的全部写入；restore-check 本身不是安装或在线恢复授权。

## 14. P4 FastAPI candidate、ownership 与 rollback rehearsal

P4 只交付隔离 FastAPI candidate，不接管生产。默认 Compose 仍由 `frozen-node` target
运行 `node server.js`，并继续拥有 Live HTTP、worker 与 scheduler。`p4-candidate` profile
只允许连接已验证 restore copy 或临时数据库，且必须同时提供该副本的 descendant
`DatabaseEvidenceIdentityManifest`、独立 candidate runtime namespace，以及只读的
`node_active` production owner marker。任何 candidate path 指向 `data/app.db`、Live runtime
目录或可写 owner marker，都必须在容器启动前失败。

### 14.1 隔离 rehearsal 固定顺序

1. 只使用 P0 `OriginReceipt` 命名的 exact backup/Manifest，重新执行独立 `verify` 与
   `restore-check`，并从 restore-check JSON 取得本次隔离 `app.db`。
2. 为该隔离文件创建并验证 descendant database identity；确认 `subjectKind` 非 `live`、
   lineage 与 Live 相同、platform file identity 指向本次 restore copy。
3. 对隔离数据库 capture fixed P4 inventory，证明唯一 revision 为 `20260807_03`、
   `processing_jobs` 28-column projection 含 non-null `spec_json`、五个 exact triggers、
   ProcessingJob fingerprints 与 FTS logical coverage 全部有效。
4. 保持 Live Node PID、loopback port、database handle 与 owner-marker bytes/SHA/mtime 不变，
   在独立 namespace 启动 `candidate-api`、`candidate-worker`、`candidate-scheduler`。每个 OS
   进程只声明一个 `API_PROCESS_ROLE`；API 只绑定 IPv4 loopback 和 OS-assigned host port，
   Worker/Scheduler 分别持有 role-scoped singleton lease。
5. 执行 FastAPI parity smoke 后 drain candidate：先关闭 API admission 并等待 in-flight，
   再停止 Worker claim 并提交在途事务，最后停止 Scheduler tick、持久化 `next_run`，然后
   释放两项 role lease。deadline 只取消 candidate provider scope，不回滚已提交 artifact。
6. 对同一隔离数据库运行 `candidate-rollback-smoke --rollback-profile frozen-node`：capture
   inventory before，随机 loopback 启动 frozen Node，验证 `/api/papers`、`/api/reviews`、
   `/pdfbytes`、`/workspace/`、`/legacy/`，停止该隔离进程，再 capture/strict compare after。
7. 最后重新验证 Live Node owner evidence 完全未变，并确认 candidate lease、子进程与端口均已释放。

Compose candidate profile 必须显式传入以下四个宿主路径和 namespace；默认占位路径不是
Live 授权，缺少任何文件时应 fail closed：

```text
P4_CANDIDATE_DB_DIR=<exact restored-copy directory>
P4_CANDIDATE_IDENTITY_MANIFEST=<exact descendant identity manifest>
P4_CANDIDATE_PARENT_IDENTITY_MANIFEST=<exact Live parent identity manifest>
P4_CANDIDATE_ORIGIN_RECEIPT=<exact retained P0 OriginReceipt>
P4_CANDIDATE_PARENT_BACKUP=<exact verified candidate parent backup>
P4_CANDIDATE_PARENT_MANIFEST=<exact candidate parent backup Manifest>
P4_CANDIDATE_RUNTIME_DIR=<dedicated candidate lease directory>
P4_CANDIDATE_RUNTIME_NAMESPACE=<non-production namespace>
P4_PRODUCTION_OWNER_MARKER=<exact existing node_active marker>
```

The host descendant identity is transport evidence, not the container runtime identity.
Each candidate role verifies the mounted host identity, Live parent identity, P0 receipt
envelope, parent backup/Manifest, and the owner marker's exact Live
`subjectDatabaseId`. The first role then exclusively creates
`/candidate/runtime/database-identity-v1.json`, bound to the actual
`/candidate/data/app.db` path and Linux file identity; the other roles strictly reuse
the same bytes. The Live database is never mounted. A host path embedded in retained
evidence is therefore not treated as a container path and cannot be used to bypass the
runtime rebind.

### 14.2 P4 rollback 固定值与 P6 边界

P4 rollback 只切换隔离 candidate 的 runtime modes，不 downgrade、不恢复旧 backup，也不删除
source、artifact、job、chunk、embedding 或 future Obsidian rows：

```text
API_BACKEND_MODE=legacy
DOCUMENT_PIPELINE_MODE=legacy
GENERATION_PIPELINE_MODE=legacy
ARTIFACT_READ_MODE=legacy
ARTIFACT_WRITE_MODE=legacy
OCR_ENABLED=0
OBSIDIAN_ENABLED=0
```

P4 禁止停止 Live Node、启动连接 Live DB 的 Python role、释放/替换 production owner marker，
也禁止应用 production profile。正式 Node shutdown、Python production startup、promotion 与失败
回滚只能由 P6 shutdown gate 在 writer-drain、durable identity/receipt 和 strict convergence 全部通过后授权。

## 15. P5 Obsidian projection runtime rollback

### 15.1 显式 PDF migration operator 流程

PDF migration 不是 Settings 保存、HTTP API 或普通 Obsidian export 的一部分。它只接受显式
operator CLI，要求 `DB_PATH` 指向唯一 revision `20260807_03` 的数据库，并从
`SETTINGS_PATH`（默认 `data/settings.json`）读取 `pdfDir`、`obsidianVaultPath` 和
`obsidianRootFolder`。`pdfDir` 与 Vault 必须已经存在；`plan` 不创建目录、不写 Vault/DB，
也不调用 materialize、OCR 或队列。

CLI 只有四个子命令：

```powershell
$python = (Resolve-Path -LiteralPath '.\.venv\Scripts\python.exe').Path
$env:DB_PATH = (Resolve-Path -LiteralPath '<exact-p3-database>').Path
$env:SETTINGS_PATH = (Resolve-Path -LiteralPath '<exact-settings-json>').Path
$planPath = [IO.Path]::GetFullPath('<exact-new-plan-json>')
$intentPath = [IO.Path]::GetFullPath('<exact-new-intent-json>')

# cmd.exe preserves the native stdout bytes; do not transcode canonical plan JSON.
cmd.exe /d /s /c "`"$python`" -B -m backend.app.cli.obsidian_pdf_migration plan > `"$planPath`""
if ($LASTEXITCODE -ne 0) { throw 'Obsidian PDF migration plan failed.' }
$planSha = (Get-FileHash -LiteralPath $planPath -Algorithm SHA256).Hash.ToLowerInvariant()

$prepareRaw = & $python -B -m backend.app.cli.obsidian_pdf_migration prepare `
  --confirm-plan-sha $planSha --intent-output $intentPath
if ($LASTEXITCODE -ne 0) { throw 'Obsidian PDF migration prepare failed.' }
$prepared = $prepareRaw | ConvertFrom-Json

$applyRaw = & $python -B -m backend.app.cli.obsidian_pdf_migration apply `
  --intent $prepared.intentPath --confirm-intent-sha $prepared.intentSha256
if ($LASTEXITCODE -ne 0) { throw 'Obsidian PDF migration apply failed.' }
$applied = $applyRaw | ConvertFrom-Json

$rollbackRaw = & $python -B -m backend.app.cli.obsidian_pdf_migration rollback `
  --intent $applied.intentPath --confirm-intent-sha $applied.intentSha256
if ($LASTEXITCODE -ne 0) { throw 'Obsidian PDF migration rollback failed.' }
$rolledBack = $rollbackRaw | ConvertFrom-Json
```

`prepare` 只以 exclusive create 发布并 fsync 一个 exact-new intent，不触碰 Vault、DB、ledger
或 manifest。`apply` 同时承担首次执行与 crash recovery；`rollback` 可处理 partial 或 sealed
intent。每次命令都必须使用上一条成功结果返回的同一 `intentPath` 和最新
`intentSha256`。若进程在返回 JSON 前崩溃，只能对已冻结的 exact intent path 执行
`Get-FileHash -LiteralPath <exact-intent-path> -Algorithm SHA256` 取得当前 SHA 后恢复；不得使用
glob、`latest`、路径猜测、重新生成 plan 或新建 intent 替代原 intent。Intent 会在每个边界
原子 checkpoint，完成时同一文件 seal 为 receipt。源 PDF 永不删除；rollback 只在当前
DB/ledger/manifest/target 仍与 intent 证据精确一致时恢复 prior state。

P5 回滚只停用 projection runtime，不 downgrade schema，也不删除 Vault 或用户文件：

1. 将冻结启动配置设为 `OBSIDIAN_ENABLED=0`，停止新的 `obsidian_export` / `obsidian_sync` claim。
2. 等待当前 `BoundVaultRoot` 原子操作、ledger 短事务和 MigrationIntent checkpoint 完成，再停止 projector role。
3. 保留 queued job 的 canonical `spec_json`、Vault、manifest、`obsidian_exports`、MigrationIntent 与 sealed receipt。恢复服务后只从原 spec 重启。
4. PDF migration 只能使用 exact intent path 与最新 SHA 显式 resume 或 rollback，不从 settings 或目录扫描推导状态。
5. 清理必须先生成 managed-only dry-run plan，再以同一 plan SHA 单独授权；Note、orphan 和 tombstone 永远不能获得清理授权。

## 16. P6 Python production adapters 与 frozen Node rollback

P6 production 支持两个等价 deployment adapter：Windows 默认使用 `native-windows`，直接运行
Python `api`、`worker`、`scheduler` 和 `mcp`；`container` 使用 Docker Compose 运行相同四个
角色。Docker 不是功能依赖。Node 不属于默认 production runtime，只作为 frozen rollback
entrypoint 保留；runtime rollback 不执行 schema downgrade，也不安装旧数据库快照。

每次候选构建和最终发布都必须在 content-addressed BuildIdentity 与 canonical startup snapshot
中记录并复验以下运行时证据，不能把某一次发布值写死在本文件：

- container adapter 记录 Python candidate 与 frozen Node 的 image digest；
- native-windows adapter 记录 Python/Node executable bytes、requirements lock、frontend artifact、
  application cwd、四角色与 rollback exact argv，以及环境值 hash；原生模式不得伪造 image digest；
- exact `gitRevision`、source tree/build artifact identity；
- API、Worker、Scheduler、MCP 与 frozen Node 的 exact start command；
- frozen Node artifact、旧 API 和 legacy schema/credential fallback 的 retention deadline 或正式
  rollback-window closure receipt。

权威当前 owner 只能从 gitignored runtime marker
`data/compatibility/runtime/production-owner.json` 读取。README、runbook、Compose 和受
`sourceTreeHash` 保护的其他静态文件都不得硬编码当前 active/inactive owner；promotion 与
rollback 只更新 identity-bound runtime evidence。

原生日常启动使用 `backend.app.cli.native_runtime start|status|stop`，并显式传入 exact native
runtime spec、BuildIdentity manifest、state directory 与 owner marker。`start` 只接受
`python_active` marker 引用的 exact HandoffReceipt，并在任何 role 副作用前复验 receipt、startup
snapshot、build/database/origin identity 与 completed cutover lease。状态文件记录四角色 exact
argv/cwd/PID 并自哈希；attach 时重新对照 frozen spec，不能用被重写的 state 文件终止其他进程。
真实 stdout/stderr 位于 state directory 的逐角色日志。readiness 必须覆盖 `/health/live`、
`/health/ready`、`/api/papers`、`/api/v2/jobs`、`/workspace/`、`/legacy/` 以及 MCP `tools/list`
九工具 exact set，单纯 PID 存活不算 ready。

若升级前 `node_active` marker 的 PID 已失效，只能调用 native operator 的
`recover-stale-node-owner`：按 frozen spec 启动 exact Node，五路径 legacy smoke 成功后，使用
Windows Inspector 双快照和旧 marker bytes CAS reattest；任一步失败都停止新 Node并保持旧 marker。
不得手工删除、覆盖或重新生成 owner marker。

进入 `rollback` profile 前，必须先停止新的 Python 流量和 claim，drain 在途工作，关闭连接并
停止 Python API、Worker、Scheduler、MCP，确认这些 role 已 scale 到 0，随后才可按冻结启动快照
启动 Node。只有 legacy smoke 成功后才能更新 owner marker。Runtime rollback 只切换进程和
startup-only mode，不自动 downgrade、恢复、移动或改写 Live SQLite。

删除 frozen Node、旧 API、旧表/列、legacy fallback、legacy credential fields 或 Obsidian ledger，
以及调用 `finalize_legacy_migration`，都必须另立版本化计划：先正式关闭 Node rollback window，
重新审计所有消费者，再创建并验证新备份并完整重演 rollback。P6 本身不执行这些删除或 finalization。
