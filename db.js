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

// ingest_jobs 增量列（幂等：给旧库补列）
for (const [col, ddl] of [['only_a', 'INTEGER DEFAULT 0'], ['queries', 'TEXT'], ['schedule_id', 'INTEGER']]) {
  const has = db.prepare(`SELECT 1 FROM pragma_table_info('ingest_jobs') WHERE name = ?`).get(col);
  if (!has) db.exec(`ALTER TABLE ingest_jobs ADD COLUMN ${col} ${ddl}`);
}

// 会议名归一化（与 public/app.js 的 normVenue、agent/db.py 的 norm_venue 保持一致）
const VENUE_CANON = { neurips: 'NeurIPS', nips: 'NeurIPS', cvpr: 'CVPR', iccv: 'ICCV', eccv: 'ECCV', wacv: 'WACV', icml: 'ICML', iclr: 'ICLR', aaai: 'AAAI', ijcai: 'IJCAI', acl: 'ACL', emnlp: 'EMNLP', naacl: 'NAACL', coling: 'COLING', tmlr: 'TMLR', tpami: 'TPAMI', corr: 'arXiv' };
const VENUE_FULL = [
  ['empirical methods in natural language', 'EMNLP'],
  ['north american chapter', 'NAACL'],
  ['findings of the association for computational linguistics', 'ACL Findings'],
  ['association for computational linguistics', 'ACL'],
  ['computer vision and pattern recognition', 'CVPR'],
  ['european conference on computer vision', 'ECCV'],
  ['winter conference on applications of computer vision', 'WACV'],
  ['international conference on computer vision', 'ICCV'],
  ['learning representations', 'ICLR'],
  ['international conference on machine learning', 'ICML'],
  ['neural information processing systems', 'NeurIPS'],
  ['international joint conference on artificial intelligence', 'IJCAI'],
  ['aaai conference on artificial intelligence', 'AAAI'],
  ['advancement of artificial intelligence', 'AAAI'],
  ['acm multimedia', 'ACM MM'],
  ['international conference on multimedia', 'ACM MM']
];
const normVenue = (v) => {
  if (!v) return v;
  const s = String(v).trim(), k = s.toLowerCase();
  if (VENUE_CANON[k]) return VENUE_CANON[k];
  if (k.startsWith('arxiv')) return 'arXiv';
  for (const [sub, abbr] of VENUE_FULL) { if (k.includes(sub)) return abbr; }
  return s;
};

// CCF 推荐目录（第七版）会议/期刊 → 级别 A/B/C
let CCF_RANKS = {};
try { CCF_RANKS = JSON.parse(fs.readFileSync(path.join(__dirname, 'db', 'ccf_ranks.json'), 'utf8')); } catch (e) {}
const ccfRank = (v) => CCF_RANKS[normVenue(v)] || null;

// 列表：返回与旧 papers.json 相同的结构（含 file/status/hasNote/ccf）
const listPapers = () => {
  const rows = db.prepare(`
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
  rows.forEach(r => { r.ccf = ccfRank(r.venue); });   // 现算 CCF 级别（A/B/C），随 venue 变化
  return rows;
};

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
// 引用关系图：库内论文为节点，cite_edges 为有向边（src 引用 dst）。
const getCiteGraph = () => {
  db.exec(`CREATE TABLE IF NOT EXISTS cite_edges (src_id TEXT NOT NULL, dst_id TEXT NOT NULL, PRIMARY KEY(src_id,dst_id))`);
  const edges = db.prepare('SELECT src_id, dst_id FROM cite_edges').all();
  const papers = db.prepare('SELECT id, title, venue, year, type, topic, citations FROM papers').all();
  const indeg = {}, outdeg = {};
  edges.forEach(e => { indeg[e.dst_id] = (indeg[e.dst_id] || 0) + 1; outdeg[e.src_id] = (outdeg[e.src_id] || 0) + 1; });
  const nodes = papers.map(p => ({ id: p.id, title: p.title, venue: p.venue, year: p.year, type: p.type, topic: p.topic, citations: p.citations, indeg: indeg[p.id] || 0, outdeg: outdeg[p.id] || 0 }));
  return { nodes, links: edges.map(e => ({ source: e.src_id, target: e.dst_id })), edgeCount: edges.length };
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

// ====== 后台采集任务（ingest_jobs / job_candidates） ======
const createJob = (j) => {
  const r = db.prepare(`INSERT INTO ingest_jobs
    (query, venues, year_from, year_to, max_papers, min_relevance, only_a, schedule_id, status)
    VALUES (@query,@venues,@year_from,@year_to,@max_papers,@min_relevance,@only_a,@schedule_id,'pending')`)
    .run({
      query: String(j.query || '').trim(),
      venues: Array.isArray(j.sources) ? j.sources.join(',') : (j.sources || ''),
      year_from: j.yearFrom || null, year_to: j.yearTo || null,
      max_papers: Math.min(parseInt(j.max) || 12, 50),
      min_relevance: j.minRelevance == null ? 0.5 : j.minRelevance,
      only_a: j.onlyA ? 1 : 0, schedule_id: j.scheduleId || null
    });
  return r.lastInsertRowid;
};
const listJobs = () => db.prepare(`SELECT id, query, venues, year_from, year_to, max_papers, min_relevance, only_a, schedule_id,
  status, found, added, skipped, created_at, finished_at,
  (SELECT COUNT(*) FROM job_candidates c WHERE c.job_id = ingest_jobs.id AND c.status='pending') AS pending
  FROM ingest_jobs ORDER BY id DESC`).all();
const getJob = (id) => db.prepare('SELECT * FROM ingest_jobs WHERE id = ?').get(id);
const setJobStatus = (id, status) => db.prepare(
  `UPDATE ingest_jobs SET status=?, finished_at=CASE WHEN ? IN ('done','failed') THEN datetime('now') ELSE finished_at END WHERE id=?`
).run(status, status, id);
const appendJobLog = (id, text) => db.prepare(`UPDATE ingest_jobs SET log = substr(COALESCE(log,'') || ?, -8000) WHERE id=?`).run(text, id);
const bumpJobAdded = (id, n) => db.prepare('UPDATE ingest_jobs SET added = COALESCE(added,0) + ? WHERE id=?').run(n, id);
const deleteJob = (id) => { db.prepare('DELETE FROM job_candidates WHERE job_id=?').run(id); return db.prepare('DELETE FROM ingest_jobs WHERE id=?').run(id).changes; };
const listJobCandidates = (id) => db.prepare(`SELECT id, data FROM job_candidates WHERE job_id=? AND status='pending' ORDER BY id`).all(id)
  .map(r => { try { const c = JSON.parse(r.data); c._cid = r.id; return c; } catch (e) { return null; } }).filter(Boolean);
const markJobCandidates = (jobId, titleNorms, status) => {
  if (!titleNorms || !titleNorms.length) return 0;
  const stmt = db.prepare(`UPDATE job_candidates SET status=? WHERE job_id=? AND title_norm=?`);
  const tx = db.transaction((tns) => { let n = 0; for (const tn of tns) n += stmt.run(status, jobId, tn).changes; return n; });
  return tx(titleNorms);
};
const closeJobIfEmpty = (id) => { const r = db.prepare(`SELECT COUNT(*) AS p FROM job_candidates WHERE job_id=? AND status='pending'`).get(id); if (r && r.p === 0) setJobStatus(id, 'done'); };
const resetOrphanJobs = () => db.prepare(`UPDATE ingest_jobs SET status='failed', finished_at=datetime('now') WHERE status IN ('running','pending')`).run().changes;

// ====== 定时任务（job_schedules） ======
const listSchedules = () => db.prepare('SELECT * FROM job_schedules ORDER BY id DESC').all();
const createSchedule = (s) => db.prepare(`INSERT INTO job_schedules
    (query, sources, years, max_papers, min_relevance, only_a, every_days, enabled, next_run)
    VALUES (@query,@sources,@years,@max_papers,@min_relevance,@only_a,@every_days,1, datetime('now'))`)
  .run({
    query: String(s.query || '').trim(),
    sources: Array.isArray(s.sources) ? s.sources.join(',') : (s.sources || 'semanticscholar'),
    years: s.years || '2024-2026', max_papers: Math.min(parseInt(s.max) || 12, 50),
    min_relevance: s.minRelevance == null ? 0.5 : s.minRelevance,
    only_a: s.onlyA ? 1 : 0, every_days: Math.max(1, parseInt(s.everyDays) || 7)
  }).lastInsertRowid;
const toggleSchedule = (id, enabled) => db.prepare('UPDATE job_schedules SET enabled=? WHERE id=?').run(enabled ? 1 : 0, id).changes;
const deleteSchedule = (id) => db.prepare('DELETE FROM job_schedules WHERE id=?').run(id).changes;
const dueSchedules = () => db.prepare(`SELECT * FROM job_schedules WHERE enabled=1 AND (next_run IS NULL OR next_run <= datetime('now'))`).all();
const markScheduleRan = (id, everyDays) => db.prepare(`UPDATE job_schedules SET last_run=datetime('now'), next_run=datetime('now', ?) WHERE id=?`).run(`+${Math.max(1, parseInt(everyDays) || 7)} days`, id);

module.exports = {
  db, listPapers, getExplainer, getTranslation, getNote, getCiteGraph, setNote, setStatus, setFavorite, deletePaper, getPdfPath, getPaper, addPaper, updatePaper,
  createJob, listJobs, getJob, setJobStatus, appendJobLog, bumpJobAdded, deleteJob, listJobCandidates, markJobCandidates, closeJobIfEmpty, resetOrphanJobs,
  listSchedules, createSchedule, toggleSchedule, deleteSchedule, dueSchedules, markScheduleRan
};
