// 论文学习 App — Node Web/API（数据走 SQLite，见 db.js / docs/DATABASE.md）
// 启动：node server.js   →  http://localhost:5173
const http = require('http');
const fs = require('fs');
const path = require('path');
const { spawn } = require('child_process');
const dbapi = require('./db');

const ROOT = __dirname;
const PUBLIC = path.join(ROOT, 'public');

let cfg = { papersDir: '../paper', port: 5173 };
try { Object.assign(cfg, JSON.parse(fs.readFileSync(path.join(ROOT, 'config.json'), 'utf8'))); } catch (e) {}
const PAPERS_DIR = path.resolve(ROOT, cfg.papersDir);
const PDFS_DIR = path.join(ROOT, 'data', 'pdfs');   // 采集 Agent 下载的 PDF
const PORT = process.env.PORT || cfg.port || 5173;

// 优先用本地缓存(data/pdfs)，再回退到种子目录(../paper)
const resolvePdf = (name) => {
  for (const dir of [PDFS_DIR, PAPERS_DIR]) {
    const f = path.join(dir, name);
    if (fs.existsSync(f)) return f;
  }
  return null;
};

const MIME = {
  '.html': 'text/html; charset=utf-8', '.css': 'text/css; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8', '.json': 'application/json; charset=utf-8',
  '.pdf': 'application/pdf', '.md': 'text/markdown; charset=utf-8',
  '.png': 'image/png', '.svg': 'image/svg+xml', '.ico': 'image/x-icon', '.woff2': 'font/woff2'
};
const send = (res, code, body, type) => { res.writeHead(code, { 'Content-Type': type || 'text/plain; charset=utf-8' }); res.end(body); };
const safeBase = (s) => path.basename(String(s || ''));
const readBody = (req) => new Promise((r) => { let d = ''; req.on('data', c => d += c); req.on('end', () => r(d)); });

const server = http.createServer(async (req, res) => {
  const u = new URL(req.url, 'http://localhost');
  const p = u.pathname;
  try {
    // ---- API（SQLite）----
    if (p === '/api/papers') {
      return send(res, 200, JSON.stringify(dbapi.listPapers()), MIME['.json']);
    }
    if (p === '/api/note' && req.method === 'GET') {
      return send(res, 200, dbapi.getNote(safeBase(u.searchParams.get('id'))), MIME['.md']);
    }
    if (p === '/api/note' && req.method === 'POST') {
      const b = JSON.parse(await readBody(req));
      dbapi.setNote(safeBase(b.id), b.content);
      return send(res, 200, JSON.stringify({ ok: true }), MIME['.json']);
    }
    if (p === '/api/explainer' && req.method === 'GET') {
      const id = safeBase(u.searchParams.get('id'));
      let ex = dbapi.getExplainer(id);
      if (!ex) { const f = path.join(PAPERS_DIR, id + '.md'); ex = fs.existsSync(f) ? fs.readFileSync(f, 'utf8') : '*(暂无讲解)*'; }
      return send(res, 200, ex, MIME['.md']);
    }
    if (p === '/api/progress' && req.method === 'POST') {
      const b = JSON.parse(await readBody(req));
      dbapi.setStatus(safeBase(b.id), b.status);
      return send(res, 200, JSON.stringify({ ok: true }), MIME['.json']);
    }
    if (p === '/api/delete' && req.method === 'POST') {
      const b = JSON.parse(await readBody(req));
      const id = safeBase(b.id);
      dbapi.deletePaper(id);
      try { const f = path.join(PDFS_DIR, id + '.pdf'); if (fs.existsSync(f)) fs.unlinkSync(f); } catch (e) {}
      return send(res, 200, JSON.stringify({ ok: true }), MIME['.json']);
    }
    if (p === '/api/ingest' && req.method === 'POST') {
      const b = JSON.parse(await readBody(req));
      const sources = (Array.isArray(b.sources) ? b.sources : []).filter(s => ['semanticscholar', 'arxiv'].includes(s));
      if (!b.query || !sources.length) return send(res, 400, JSON.stringify({ ok: false, error: '缺少检索方向或数据源' }), MIME['.json']);
      const pyWin = path.join(ROOT, '.venv', 'Scripts', 'python.exe');
      const py = fs.existsSync(pyWin) ? pyWin : 'python';
      const args = ['-m', 'agent', 'ingest', '--query', String(b.query), '--sources', sources.join(','),
        '--years', String(b.years || '2024-2026'), '--max', String(Math.min(parseInt(b.max) || 10, 50)),
        '--min-relevance', String(b.minRelevance == null ? 0.5 : b.minRelevance)];
      if (b.deep) args.push('--deep');
      let out = '';
      const child = spawn(py, args, { cwd: ROOT, env: { ...process.env, PYTHONIOENCODING: 'utf-8' } });
      child.stdout.on('data', d => out += d.toString());
      child.stderr.on('data', d => out += d.toString());
      child.on('error', e => send(res, 200, JSON.stringify({ ok: false, output: String(e) }), MIME['.json']));
      child.on('close', code => send(res, 200, JSON.stringify({ ok: code === 0, code, output: out }), MIME['.json']));
      return;
    }
    // ---- PDF 字节（绕过迅雷类下载器：路径不含 .pdf，由脚本 fetch 取字节）----
    if (p === '/pdfbytes') {
      const f = resolvePdf(safeBase(u.searchParams.get('id')) + '.pdf');
      if (!f) return send(res, 404, 'not found');
      res.writeHead(200, { 'Content-Type': 'application/octet-stream', 'Content-Length': fs.statSync(f).size, 'Cache-Control': 'no-store' });
      return fs.createReadStream(f).pipe(res);
    }
    // ---- PDF 流式（原文直链，供“↗ 原文”用）----
    if (p.startsWith('/papers/')) {
      const f = resolvePdf(safeBase(decodeURIComponent(p.slice('/papers/'.length))));
      if (!f) return send(res, 404, 'PDF not found');
      res.writeHead(200, { 'Content-Type': MIME[path.extname(f).toLowerCase()] || 'application/octet-stream' });
      return fs.createReadStream(f).pipe(res);
    }
    // ---- 静态前端 ----
    let rel = p === '/' ? 'index.html' : decodeURIComponent(p).replace(/^\/+/, '');
    const fp = path.normalize(path.join(PUBLIC, rel));
    if (!fp.startsWith(PUBLIC)) return send(res, 403, 'forbidden');
    if (fs.existsSync(fp) && fs.statSync(fp).isFile()) {
      res.writeHead(200, { 'Content-Type': MIME[path.extname(fp).toLowerCase()] || 'application/octet-stream' });
      return fs.createReadStream(fp).pipe(res);
    }
    return send(res, 404, 'not found');
  } catch (e) {
    return send(res, 500, String(e && e.stack || e));
  }
});

server.listen(PORT, () => {
  console.log('========================================');
  console.log(' 论文学习 App 已启动 (SQLite)');
  console.log(' 打开:  http://localhost:' + PORT);
  console.log(' 数据库: ' + (process.env.DB_PATH || path.join(ROOT, 'data', 'app.db')));
  console.log(' PDF目录: ' + PAPERS_DIR);
  console.log(' 按 Ctrl+C 停止');
  console.log('========================================');
});
