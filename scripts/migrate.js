// P1 迁移：把 papers.json / 讲解 md / progress.json / notes 导入 SQLite。幂等可重跑。
const fs = require('fs');
const path = require('path');
const { db } = require('../db');

const ROOT = path.join(__dirname, '..');
const PAPER_DIR = path.join(ROOT, '..', 'paper');   // 讲解 md 与 PDF 所在
const NOTES_DIR = path.join(ROOT, 'notes');
const norm = (s) => (s || '').toLowerCase().replace(/[^a-z0-9一-龥]+/g, '');
const exists = (id) => !!db.prepare('SELECT 1 FROM papers WHERE id = ?').get(id);

const papers = JSON.parse(fs.readFileSync(path.join(ROOT, 'data', 'papers.json'), 'utf8'));

const insertPaper = db.prepare(`
  INSERT INTO papers (id, source, arxiv_id, title, title_norm, venue, year, type, topic, order_no, pdf_path, explainer)
  VALUES (@id,@source,@arxiv_id,@title,@title_norm,@venue,@year,@type,@topic,@order_no,@pdf_path,@explainer)
  ON CONFLICT(id) DO UPDATE SET
    title=excluded.title, venue=excluded.venue, year=excluded.year, type=excluded.type,
    topic=excluded.topic, order_no=excluded.order_no, pdf_path=excluded.pdf_path,
    explainer=COALESCE(excluded.explainer, papers.explainer), updated_at=datetime('now')
`);
const insertStatus = db.prepare(`INSERT INTO progress(paper_id,status) VALUES(?,?)
  ON CONFLICT(paper_id) DO UPDATE SET status=excluded.status`);
const insertNote = db.prepare(`INSERT INTO notes(paper_id,content) VALUES(?,?)
  ON CONFLICT(paper_id) DO UPDATE SET content=excluded.content`);

let nPapers = 0, nNotes = 0, nProg = 0;
const run = db.transaction(() => {
  for (const p of papers) {
    const m = /^(\d{4}\.\d{4,5})/.exec(p.id);
    const expPath = path.join(PAPER_DIR, p.id + '.md');
    insertPaper.run({
      id: p.id, source: 'seed', arxiv_id: m ? m[1] : null,
      title: p.title, title_norm: norm(p.title), venue: p.venue, year: String(p.year),
      type: p.type, topic: p.topic || null, order_no: p.order ?? null,
      pdf_path: '../paper/' + p.file,
      explainer: fs.existsSync(expPath) ? fs.readFileSync(expPath, 'utf8') : null
    });
    nPapers++;
  }
  // 进度
  const progPath = path.join(ROOT, 'data', 'progress.json');
  if (fs.existsSync(progPath)) {
    const prog = JSON.parse(fs.readFileSync(progPath, 'utf8'));
    for (const [pid, status] of Object.entries(prog)) {
      if (exists(pid)) { insertStatus.run(pid, status); nProg++; }
    }
  }
  // 笔记
  if (fs.existsSync(NOTES_DIR)) {
    for (const f of fs.readdirSync(NOTES_DIR)) {
      if (!f.endsWith('.md')) continue;
      const pid = f.replace(/\.md$/, '');
      if (!exists(pid)) continue;
      const content = fs.readFileSync(path.join(NOTES_DIR, f), 'utf8');
      if (content.trim()) { insertNote.run(pid, content); nNotes++; }
    }
  }
  db.prepare('INSERT OR IGNORE INTO schema_migrations(version) VALUES(1)').run();
});
run();

console.log(`迁移完成：papers=${nPapers}  progress=${nProg}  notes=${nNotes}`);
console.log('库内论文总数：', db.prepare('SELECT COUNT(*) c FROM papers').get().c);
console.log('有讲解的论文：', db.prepare('SELECT COUNT(*) c FROM papers WHERE explainer IS NOT NULL').get().c);
