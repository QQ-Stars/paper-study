-- Paper-Study 数据库结构（SQLite）。详见 docs/DATABASE.md
-- 连接级 PRAGMA（journal_mode/foreign_keys/busy_timeout）在 db.js 里设置。

-- ========== 论文主表 ==========
CREATE TABLE IF NOT EXISTS papers (
  id           TEXT PRIMARY KEY,          -- slug，用于文件名/URL
  source       TEXT NOT NULL,             -- semanticscholar|openalex|arxiv|manual|seed
  source_id    TEXT,
  arxiv_id     TEXT,
  doi          TEXT,
  s2_id        TEXT,
  openalex_id  TEXT,
  title        TEXT NOT NULL,
  title_norm   TEXT,                      -- 归一化标题，用于模糊去重
  authors      TEXT,                      -- JSON 数组
  venue        TEXT,
  year         TEXT,
  abstract     TEXT,
  tldr         TEXT,                      -- AI 一句话总结(S2 免费; 无则 LLM 兜底)
  citations    INTEGER,
  s2_fields    TEXT,                      -- JSON 数组(领域)
  url          TEXT,
  pdf_url      TEXT,
  pdf_path     TEXT,                      -- 本地缓存/相对路径
  type         TEXT,                      -- 检测|缓解·解码|缓解·训练|机制|评测|定义|其他
  topic        TEXT,
  task         TEXT,
  models       TEXT,                      -- JSON 数组
  datasets     TEXT,                      -- JSON 数组
  contribution TEXT,
  tags         TEXT,                      -- JSON 数组
  relevance    REAL,
  explainer    TEXT,                      -- 讲解 markdown
  extracted_by TEXT,
  order_no     INTEGER,
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

-- ========== 学习进度 ==========
CREATE TABLE IF NOT EXISTS progress (
  paper_id   TEXT PRIMARY KEY REFERENCES papers(id) ON DELETE CASCADE,
  status     TEXT NOT NULL DEFAULT '未开始',
  updated_at TEXT DEFAULT (datetime('now'))
);

-- ========== 笔记 ==========
CREATE TABLE IF NOT EXISTS notes (
  paper_id   TEXT PRIMARY KEY REFERENCES papers(id) ON DELETE CASCADE,
  content    TEXT NOT NULL DEFAULT '',
  updated_at TEXT DEFAULT (datetime('now'))
);

-- ========== 论文向量(可选, 语义检索) ==========
CREATE TABLE IF NOT EXISTS paper_vectors (
  paper_id  TEXT PRIMARY KEY REFERENCES papers(id) ON DELETE CASCADE,
  dim       INTEGER,
  vector    BLOB
);

-- ========== 采集任务(P5) ==========
CREATE TABLE IF NOT EXISTS ingest_jobs (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  query         TEXT,
  venues        TEXT,
  year_from     INTEGER,
  year_to       INTEGER,
  max_papers    INTEGER,
  min_relevance REAL,
  status        TEXT NOT NULL DEFAULT 'pending',
  found         INTEGER DEFAULT 0,
  added         INTEGER DEFAULT 0,
  skipped       INTEGER DEFAULT 0,
  log           TEXT,
  created_at    TEXT DEFAULT (datetime('now')),
  finished_at   TEXT
);

-- ========== 迁移版本 ==========
CREATE TABLE IF NOT EXISTS schema_migrations (
  version    INTEGER PRIMARY KEY,
  applied_at TEXT DEFAULT (datetime('now'))
);
