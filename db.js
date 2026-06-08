// 数据库访问层（better-sqlite3，同步）。详见 docs/DATABASE.md
const Database = require('better-sqlite3');
const fs = require('fs');
const path = require('path');

const DB_PATH = process.env.DB_PATH || path.join(__dirname, 'data', 'app.db');
fs.mkdirSync(path.dirname(DB_PATH), { recursive: true });

const db = new Database(DB_PATH);
db.pragma('journal_mode = WAL');
db.pragma('foreign_keys = ON');
db.pragma('busy_timeout = 5000');

// 应用表结构（幂等）
db.exec(fs.readFileSync(path.join(__dirname, 'db', 'schema.sql'), 'utf8'));

// 列表：返回与旧 papers.json 相同的结构（含 file/status/hasNote）
const listPapers = () => db.prepare(`
  SELECT p.id,
         p.id || '.pdf'              AS file,
         p.title, p.venue, p.year, p.type, p.topic,
         p.order_no                  AS "order",
         COALESCE(g.status,'未开始') AS status,
         CASE WHEN n.content IS NOT NULL AND length(n.content) > 0 THEN 1 ELSE 0 END AS hasNote
  FROM papers p
  LEFT JOIN progress g ON g.paper_id = p.id
  LEFT JOIN notes    n ON n.paper_id = p.id
  ORDER BY p.year, COALESCE(p.order_no, 999), p.venue
`).all();

const getExplainer = (id) => {
  const r = db.prepare('SELECT explainer FROM papers WHERE id = ?').get(id);
  return r && r.explainer ? r.explainer : null;
};
const getNote = (id) => {
  const r = db.prepare('SELECT content FROM notes WHERE paper_id = ?').get(id);
  return r ? r.content : '';
};
const setNote = (id, content) => db.prepare(`
  INSERT INTO notes(paper_id, content, updated_at) VALUES(?, ?, datetime('now'))
  ON CONFLICT(paper_id) DO UPDATE SET content = excluded.content, updated_at = datetime('now')
`).run(id, content == null ? '' : content);
const setStatus = (id, status) => db.prepare(`
  INSERT INTO progress(paper_id, status, updated_at) VALUES(?, ?, datetime('now'))
  ON CONFLICT(paper_id) DO UPDATE SET status = excluded.status, updated_at = datetime('now')
`).run(id, status);

module.exports = { db, listPapers, getExplainer, getNote, setNote, setStatus };
