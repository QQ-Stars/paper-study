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

// 会议名归一化（与 public/app.js 的 normVenue、agent/db.py 的 norm_venue 保持一致）
const VENUE_CANON = { neurips: 'NeurIPS', nips: 'NeurIPS', cvpr: 'CVPR', iccv: 'ICCV', eccv: 'ECCV', wacv: 'WACV', icml: 'ICML', iclr: 'ICLR', aaai: 'AAAI', ijcai: 'IJCAI', acl: 'ACL', emnlp: 'EMNLP', naacl: 'NAACL', coling: 'COLING', tmlr: 'TMLR', tpami: 'TPAMI', corr: 'arXiv' };
const normVenue = (v) => {
  if (!v) return v;
  const s = String(v).trim(), k = s.toLowerCase();
  if (VENUE_CANON[k]) return VENUE_CANON[k];
  if (k.startsWith('arxiv')) return 'arXiv';
  return s;
};

// 列表：返回与旧 papers.json 相同的结构（含 file/status/hasNote）
const listPapers = () => db.prepare(`
  SELECT p.id,
         p.id || '.pdf'              AS file,
         p.title, p.venue, p.year, p.type, p.topic,
         p.pdf_url, p.url, p.tldr, p.contribution, p.citations, p.created_at, p.source,
         p.order_no                  AS "order",
         COALESCE(g.status,'未开始') AS status,
         CASE WHEN n.content IS NOT NULL AND length(n.content) > 0 THEN 1 ELSE 0 END AS hasNote,
         CASE WHEN f.paper_id IS NOT NULL THEN 1 ELSE 0 END AS favorite
  FROM papers p
  LEFT JOIN progress  g ON g.paper_id = p.id
  LEFT JOIN notes     n ON n.paper_id = p.id
  LEFT JOIN favorites f ON f.paper_id = p.id
  ORDER BY p.year, COALESCE(p.order_no, 999), p.venue
`).all();

const getExplainer = (id) => {
  const r = db.prepare('SELECT explainer FROM papers WHERE id = ?').get(id);
  return r && r.explainer ? r.explainer : null;
};
const getTranslation = (id) => {
  const r = db.prepare('SELECT content FROM translations WHERE paper_id = ?').get(id);
  return r && r.content ? r.content : null;
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

const setFavorite = (id, fav) => fav
  ? db.prepare(`INSERT INTO favorites(paper_id, created_at) VALUES(?, datetime('now')) ON CONFLICT(paper_id) DO NOTHING`).run(id)
  : db.prepare('DELETE FROM favorites WHERE paper_id = ?').run(id);

const deletePaper = (id) => db.prepare('DELETE FROM papers WHERE id = ?').run(id);
const getPdfPath = (id) => { const r = db.prepare('SELECT pdf_path FROM papers WHERE id = ?').get(id); return r ? r.pdf_path : null; };

// ---- 手动添加 / 编辑 ----
const getPaper = (id) => db.prepare('SELECT * FROM papers WHERE id = ?').get(id);

const EDITABLE = ['title', 'venue', 'year', 'type', 'topic', 'url', 'pdf_url', 'pdf_path', 'tldr', 'abstract', 'contribution', 'authors', 'relevance', 'order_no'];

const slugId = (title) => {
  const base = String(title || 'paper').toLowerCase()
    .replace(/[^a-z0-9一-龥]+/g, '-').replace(/^-+|-+$/g, '').slice(0, 40) || 'paper';
  return 'manual-' + base + '-' + Date.now().toString(36) + Math.random().toString(36).slice(2, 5);
};

const addPaper = (f) => {
  if (!f || !f.title || !String(f.title).trim()) throw new Error('标题不能为空');
  const id = slugId(f.title);
  const authors = Array.isArray(f.authors) ? JSON.stringify(f.authors) : (f.authors || null);
  db.prepare(`INSERT INTO papers
    (id, source, title, venue, year, abstract, tldr, url, pdf_url, pdf_path, type, topic, contribution, authors, created_at, updated_at)
    VALUES (@id,'manual',@title,@venue,@year,@abstract,@tldr,@url,@pdf_url,@pdf_path,@type,@topic,@contribution,@authors, datetime('now'), datetime('now'))`)
    .run({
      id, title: String(f.title).trim(),
      venue: normVenue(f.venue) || null, year: f.year ? String(f.year) : null,
      abstract: f.abstract || null, tldr: f.tldr || null,
      url: f.url || null, pdf_url: f.pdf_url || null, pdf_path: f.pdf_path || null,
      type: f.type || '其他', topic: f.topic || '其他',
      contribution: f.contribution || null, authors
    });
  return id;
};

const updatePaper = (id, f) => {
  const cols = [], vals = { id };
  for (const k of EDITABLE) {
    if (f && Object.prototype.hasOwnProperty.call(f, k)) {
      let v = f[k];
      if (k === 'authors' && Array.isArray(v)) v = JSON.stringify(v);
      if (v === '') v = null;
      if (k === 'venue') v = normVenue(v);
      cols.push(`${k} = @${k}`); vals[k] = v;
    }
  }
  if (!cols.length) return 0;
  return db.prepare(`UPDATE papers SET ${cols.join(', ')}, updated_at = datetime('now') WHERE id = @id`).run(vals).changes;
};

module.exports = { db, listPapers, getExplainer, getTranslation, getNote, setNote, setStatus, setFavorite, deletePaper, getPdfPath, getPaper, addPaper, updatePaper };
