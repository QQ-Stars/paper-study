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

- 本地：定期复制 `data/app.db`（WAL 下用 `VACUUM INTO 'backup.db'` 得到一致快照）。
- 上线后：换 Postgres + 定时 `pg_dump`。
