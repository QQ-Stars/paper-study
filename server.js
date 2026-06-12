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

const resolveDir = (d) => (path.isAbsolute(d) ? d : path.join(ROOT, d));
// 按论文 id 解析本地 PDF：① DB 存的 pdf_path ② 按 slug 找（默认 data/pdfs 优先 → 自定义目录 → 种子 ../paper）
const resolvePdfById = (id) => {
  try {
    const sp = dbapi.getPdfPath(id);
    if (sp) { const abs = path.isAbsolute(sp) ? sp : path.join(ROOT, sp); if (fs.existsSync(abs)) return abs; }
  } catch (e) {}
  const custom = readSettings().pdfDir;
  const dirs = [PDFS_DIR];
  if (custom) dirs.push(resolveDir(custom));
  dirs.push(PAPERS_DIR);
  for (const dir of dirs) { const f = path.join(dir, id + '.pdf'); if (fs.existsSync(f)) return f; }
  return null;
};
const pyExe = () => { const w = path.join(ROOT, '.venv', 'Scripts', 'python.exe'); return fs.existsSync(w) ? w : 'python'; };
const spawnAgent = (args, opts = {}) => spawn(pyExe(), ['-m', 'agent', ...args], { cwd: ROOT, env: { ...process.env, PYTHONIOENCODING: 'utf-8', PYTHONUTF8: '1' }, ...opts });

// ---- 设置（模型/数据源），存 data/settings.json（gitignore），Python Agent 也读它 ----
const SETTINGS_PATH = path.join(ROOT, 'data', 'settings.json');
const readSettings = () => { try { return JSON.parse(fs.readFileSync(SETTINGS_PATH, 'utf8')); } catch (e) { return {}; } };
const writeSettings = (s) => { fs.mkdirSync(path.dirname(SETTINGS_PATH), { recursive: true }); fs.writeFileSync(SETTINGS_PATH, JSON.stringify(s, null, 2)); };
const readEnv = () => { const o = {}; try { fs.readFileSync(path.join(ROOT, '.env'), 'utf8').split(/\r?\n/).forEach(l => { const m = /^([A-Z0-9_]+)=(.*)$/.exec(l.trim()); if (m) o[m[1]] = m[2]; }); } catch (e) {} return o; };
const maskKey = (k) => k ? '****' + String(k).slice(-4) : '';

const MIME = {
  '.html': 'text/html; charset=utf-8', '.css': 'text/css; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8', '.json': 'application/json; charset=utf-8',
  '.pdf': 'application/pdf', '.md': 'text/markdown; charset=utf-8',
  '.png': 'image/png', '.svg': 'image/svg+xml', '.ico': 'image/x-icon',
  '.woff2': 'font/woff2', '.woff': 'font/woff', '.ttf': 'font/ttf'
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
    if (p === '/api/translation' && req.method === 'GET') {
      const t = dbapi.getTranslation(safeBase(u.searchParams.get('id')));
      return send(res, 200, t || '', MIME['.md']);
    }
    if (p === '/api/progress' && req.method === 'POST') {
      const b = JSON.parse(await readBody(req));
      dbapi.setStatus(safeBase(b.id), b.status);
      return send(res, 200, JSON.stringify({ ok: true }), MIME['.json']);
    }
    if (p === '/api/favorite' && req.method === 'POST') {
      const b = JSON.parse(await readBody(req));
      dbapi.setFavorite(safeBase(b.id), !!b.favorite);
      return send(res, 200, JSON.stringify({ ok: true }), MIME['.json']);
    }
    if (p === '/api/delete' && req.method === 'POST') {
      const b = JSON.parse(await readBody(req));
      const id = safeBase(b.id);
      dbapi.deletePaper(id);
      try { const f = path.join(PDFS_DIR, id + '.pdf'); if (fs.existsSync(f)) fs.unlinkSync(f); } catch (e) {}
      return send(res, 200, JSON.stringify({ ok: true }), MIME['.json']);
    }
    if (p === '/api/paper/get' && req.method === 'GET') {
      const row = dbapi.getPaper(safeBase(u.searchParams.get('id')));
      return send(res, 200, JSON.stringify(row || null), MIME['.json']);
    }
    if (p === '/api/paper/add' && req.method === 'POST') {
      const b = JSON.parse(await readBody(req));
      if (!b.title || !String(b.title).trim()) return send(res, 400, JSON.stringify({ ok: false, error: '标题不能为空' }), MIME['.json']);
      try { const id = dbapi.addPaper(b); return send(res, 200, JSON.stringify({ ok: true, id }), MIME['.json']); }
      catch (e) { return send(res, 500, JSON.stringify({ ok: false, error: String(e.message || e) }), MIME['.json']); }
    }
    if (p === '/api/paper/update' && req.method === 'POST') {
      const b = JSON.parse(await readBody(req));
      const id = safeBase(b.id);
      if (!id) return send(res, 400, JSON.stringify({ ok: false, error: '缺少 id' }), MIME['.json']);
      const { id: _omit, ...fields } = b;
      try { const changes = dbapi.updatePaper(id, fields); return send(res, 200, JSON.stringify({ ok: true, changes }), MIME['.json']); }
      catch (e) { return send(res, 500, JSON.stringify({ ok: false, error: String(e.message || e) }), MIME['.json']); }
    }
    if (p === '/api/ingest' && req.method === 'POST') {
      const b = JSON.parse(await readBody(req));
      const sources = (Array.isArray(b.sources) ? b.sources : []).filter(s => ['semanticscholar', 'arxiv', 'openalex', 'dblp'].includes(s));
      if (!b.query || !sources.length) return send(res, 400, JSON.stringify({ ok: false, error: '缺少检索方向或数据源' }), MIME['.json']);
      const pyWin = path.join(ROOT, '.venv', 'Scripts', 'python.exe');
      const py = fs.existsSync(pyWin) ? pyWin : 'python';
      const args = ['-m', 'agent', 'ingest', '--query', String(b.query), '--sources', sources.join(','),
        '--years', String(b.years || '2024-2026'), '--max', String(Math.min(parseInt(b.max) || 10, 50)),
        '--min-relevance', String(b.minRelevance == null ? 0.5 : b.minRelevance)];
      if (b.deep) args.push('--deep');
      if (b.expand) args.push('--expand');
      let out = '';
      const child = spawn(py, args, { cwd: ROOT, env: { ...process.env, PYTHONIOENCODING: 'utf-8', PYTHONUTF8: '1' } });
      child.stdout.on('data', d => out += d.toString());
      child.stderr.on('data', d => out += d.toString());
      child.on('error', e => send(res, 200, JSON.stringify({ ok: false, output: String(e) }), MIME['.json']));
      child.on('close', code => send(res, 200, JSON.stringify({ ok: code === 0, code, output: out }), MIME['.json']));
      return;
    }
    // 生成扩展检索词（可编辑）
    if (p === '/api/expand' && req.method === 'POST') {
      const b = JSON.parse(await readBody(req));
      let out = ''; const ch = spawnAgent(['expand', '--query', String(b.query || ''), '--expand-n', String(b.expandN || 6)]);
      ch.stdout.on('data', d => out += d.toString());
      ch.on('error', e => send(res, 200, JSON.stringify({ ok: false, error: String(e) }), MIME['.json']));
      ch.on('close', () => { let qs = []; try { qs = JSON.parse(out); } catch (e) {} send(res, 200, JSON.stringify({ ok: true, queries: qs }), MIME['.json']); });
      return;
    }
    // 第一阶段：流式检索候选（NDJSON：progress... + 最终 result）
    if (p === '/api/search' && req.method === 'POST') {
      const b = JSON.parse(await readBody(req));
      const sources = (Array.isArray(b.sources) ? b.sources : []).filter(s => ['semanticscholar', 'arxiv', 'openalex', 'dblp'].includes(s));
      if (!b.query || !sources.length) return send(res, 400, JSON.stringify({ ok: false, error: '缺少检索方向或数据源' }), MIME['.json']);
      res.writeHead(200, { 'Content-Type': 'application/x-ndjson; charset=utf-8', 'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no' });
      const emit = (o) => res.write(JSON.stringify(o) + '\n');
      const args = ['search', '--query', String(b.query), '--sources', sources.join(','), '--years', String(b.years || '2024-2026'),
        '--max', String(Math.min(parseInt(b.max) || 10, 60)), '--min-relevance', String(b.minRelevance == null ? 0 : b.minRelevance)];
      if (b.expand) args.push('--expand');
      if (Array.isArray(b.queries) && b.queries.length) args.push('--queries', JSON.stringify(b.queries));
      let out = ''; const ch = spawnAgent(args);
      ch.stderr.on('data', d => String(d).split(/\r?\n/).forEach(l => l.trim() && emit({ type: 'progress', line: l })));
      ch.stdout.on('data', d => out += d.toString());
      ch.on('error', e => { emit({ type: 'result', ok: false, error: String(e), candidates: [] }); res.end(); });
      ch.on('close', code => { let c = []; try { c = JSON.parse(out); } catch (e) {} emit({ type: 'result', ok: code === 0, candidates: c }); res.end(); });
      return;
    }
    // 第二阶段：流式入库勾选（NDJSON）
    if (p === '/api/ingest-selected' && req.method === 'POST') {
      const b = JSON.parse(await readBody(req));
      const cands = Array.isArray(b.candidates) ? b.candidates : [];
      res.writeHead(200, { 'Content-Type': 'application/x-ndjson; charset=utf-8', 'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no' });
      const emit = (o) => res.write(JSON.stringify(o) + '\n');
      const args = ['ingest-selected']; if (b.deep) args.push('--deep');
      let added = 0; const ch = spawnAgent(args);
      ch.stderr.on('data', d => String(d).split(/\r?\n/).forEach(l => { if (!l.trim()) return; const m = /^INGESTED::(\d+)/.exec(l); if (m) added = +m[1]; emit({ type: 'progress', line: l }); }));
      ch.on('error', e => { emit({ type: 'done', ok: false, error: String(e) }); res.end(); });
      ch.on('close', code => { emit({ type: 'done', ok: code === 0, added }); res.end(); });
      ch.stdin.write(JSON.stringify(cands)); ch.stdin.end();
      return;
    }
    // 会议核实：查 S2/DBLP 权威库（NDJSON：progress... + 最终 result.verifications）
    if (p === '/api/verify-venue' && req.method === 'POST') {
      const b = JSON.parse(await readBody(req));
      const cands = Array.isArray(b.candidates) ? b.candidates : [];
      res.writeHead(200, { 'Content-Type': 'application/x-ndjson; charset=utf-8', 'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no' });
      const emit = (o) => res.write(JSON.stringify(o) + '\n');
      const vsources = (Array.isArray(b.sources) ? b.sources : ['dblp', 'semanticscholar']).filter(s => ['dblp', 'semanticscholar', 'openalex'].includes(s));
      let out = ''; const ch = spawnAgent(['verify-venue', '--sources', vsources.join(',') || 'dblp,semanticscholar']);
      ch.stderr.on('data', d => String(d).split(/\r?\n/).forEach(l => l.trim() && emit({ type: 'progress', line: l })));
      ch.stdout.on('data', d => out += d.toString());
      ch.on('error', e => { emit({ type: 'result', ok: false, error: String(e), verifications: [] }); res.end(); });
      ch.on('close', code => { let v = []; try { v = JSON.parse(out); } catch (e) {} emit({ type: 'result', ok: code === 0, verifications: v }); res.end(); });
      ch.stdin.write(JSON.stringify(cands)); ch.stdin.end();
      return;
    }
    // 自动生成论文讲解（LLM）：NDJSON 流，progress... + 最终 result.markdown（同时已写入 DB）
    if (p === '/api/explain' && req.method === 'POST') {
      const b = JSON.parse(await readBody(req));
      const id = safeBase(b.id);
      if (!id) return send(res, 400, JSON.stringify({ ok: false, error: '缺少 id' }), MIME['.json']);
      res.writeHead(200, { 'Content-Type': 'application/x-ndjson; charset=utf-8', 'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no' });
      const emit = (o) => res.write(JSON.stringify(o) + '\n');
      const args = ['explain', '--id', id]; if (b.deep) args.push('--deep');
      let out = '', err = ''; const ch = spawnAgent(args);
      ch.stderr.on('data', d => String(d).split(/\r?\n/).forEach(l => { if (l.trim()) { err += l + '\n'; emit({ type: 'progress', line: l }); } }));
      ch.stdout.on('data', d => out += d.toString());
      ch.on('error', e => { emit({ type: 'result', ok: false, error: String(e) }); res.end(); });
      ch.on('close', code => { emit({ type: 'result', ok: code === 0 && !!out.trim(), markdown: out, error: code === 0 ? '' : (err.trim().split(/\n/).pop() || '生成失败') }); res.end(); });
      return;
    }
    // 全文翻译（LLM，分块并发）：NDJSON 流，progress(TOTAL/CHUNK…) + 最终 result.markdown（已写入 DB）
    if (p === '/api/translate' && req.method === 'POST') {
      const b = JSON.parse(await readBody(req));
      const id = safeBase(b.id);
      if (!id) return send(res, 400, JSON.stringify({ ok: false, error: '缺少 id' }), MIME['.json']);
      res.writeHead(200, { 'Content-Type': 'application/x-ndjson; charset=utf-8', 'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no' });
      const emit = (o) => res.write(JSON.stringify(o) + '\n');
      let out = '', err = ''; const ch = spawnAgent(['translate', '--id', id]);
      ch.stderr.on('data', d => String(d).split(/\r?\n/).forEach(l => { if (l.trim()) { err += l + '\n'; emit({ type: 'progress', line: l }); } }));
      ch.stdout.on('data', d => out += d.toString());
      ch.on('error', e => { emit({ type: 'result', ok: false, error: String(e) }); res.end(); });
      ch.on('close', code => { emit({ type: 'result', ok: code === 0 && !!out.trim(), markdown: out, error: code === 0 ? '' : (err.trim().split(/\n/).pop() || '翻译失败') }); res.end(); });
      return;
    }
    // 相似论文推荐（S2 Recommendations）：NDJSON 流，progress… + 最终 result.candidates
    if (p === '/api/recommend' && req.method === 'POST') {
      const b = JSON.parse(await readBody(req));
      const id = safeBase(b.id);
      if (!id) return send(res, 400, JSON.stringify({ ok: false, error: '缺少 id' }), MIME['.json']);
      res.writeHead(200, { 'Content-Type': 'application/x-ndjson; charset=utf-8', 'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no' });
      const emit = (o) => res.write(JSON.stringify(o) + '\n');
      const limit = String(Math.min(parseInt(b.limit) || 14, 40));
      let out = ''; const ch = spawnAgent(['recommend', '--id', id, '--limit', limit]);
      ch.stderr.on('data', d => String(d).split(/\r?\n/).forEach(l => l.trim() && emit({ type: 'progress', line: l })));
      ch.stdout.on('data', d => out += d.toString());
      ch.on('error', e => { emit({ type: 'result', ok: false, error: String(e), candidates: [] }); res.end(); });
      ch.on('close', code => { let r = {}; try { r = JSON.parse(out); } catch (e) {} emit({ type: 'result', ok: code === 0 && r.ok !== false, candidates: r.candidates || [], error: r.error || '' }); res.end(); });
      return;
    }
    // 语义索引：建立/更新论文向量（NDJSON 流，progress… + 最终 result）
    if (p === '/api/embed' && req.method === 'POST') {
      const b = JSON.parse(await readBody(req));
      const scope = b.scope === 'all' ? 'all' : 'missing';
      res.writeHead(200, { 'Content-Type': 'application/x-ndjson; charset=utf-8', 'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no' });
      const emit = (o) => res.write(JSON.stringify(o) + '\n');
      let out = ''; const ch = spawnAgent(['embed', '--scope', scope]);
      ch.stderr.on('data', d => String(d).split(/\r?\n/).forEach(l => l.trim() && emit({ type: 'progress', line: l })));
      ch.stdout.on('data', d => out += d.toString());
      ch.on('error', e => { emit({ type: 'result', ok: false, error: String(e) }); res.end(); });
      ch.on('close', code => { let r = {}; try { r = JSON.parse(out); } catch (e) {} emit({ type: 'result', ok: code === 0 && r.ok !== false, indexed: r.indexed || 0, total: r.total || 0, error: r.error || '' }); res.end(); });
      return;
    }
    // 语义检索（NDJSON：首次可能先下载模型+建索引，故走流式给进度）
    if (p === '/api/semsearch' && req.method === 'POST') {
      const b = JSON.parse(await readBody(req));
      const query = String(b.query || '').slice(0, 500);
      if (!query.trim()) return send(res, 400, JSON.stringify({ ok: false, error: '缺少查询' }), MIME['.json']);
      res.writeHead(200, { 'Content-Type': 'application/x-ndjson; charset=utf-8', 'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no' });
      const emit = (o) => res.write(JSON.stringify(o) + '\n');
      let out = ''; const ch = spawnAgent(['semsearch', '--query', query, '--k', String(Math.min(parseInt(b.k) || 60, 200))]);
      ch.stderr.on('data', d => String(d).split(/\r?\n/).forEach(l => l.trim() && emit({ type: 'progress', line: l })));
      ch.stdout.on('data', d => out += d.toString());
      ch.on('error', e => { emit({ type: 'result', ok: false, error: String(e), results: [] }); res.end(); });
      ch.on('close', code => { let r = {}; try { r = JSON.parse(out); } catch (e) {} emit({ type: 'result', ok: code === 0 && r.ok !== false, results: r.results || [], error: r.error || '' }); res.end(); });
      return;
    }
    // 扫描文件夹里的 PDF（递归，最多 4 层 / 2000 个）——纯 Node，给批量导入选片用
    if (p === '/api/scan-pdfs' && req.method === 'GET') {
      const dir = (u.searchParams.get('dir') || '').trim();
      if (!dir) return send(res, 400, JSON.stringify({ ok: false, error: '缺少文件夹路径' }), MIME['.json']);
      try {
        if (!fs.existsSync(dir) || !fs.statSync(dir).isDirectory())
          return send(res, 200, JSON.stringify({ ok: false, error: '文件夹不存在或不是目录' }), MIME['.json']);
        const files = [];
        const walk = (d, depth) => {
          if (depth > 4 || files.length >= 2000) return;
          let ents = []; try { ents = fs.readdirSync(d, { withFileTypes: true }); } catch (e) { return; }
          for (const ent of ents) {
            if (files.length >= 2000) break;
            const fp = path.join(d, ent.name);
            if (ent.isDirectory()) { if (!ent.name.startsWith('.')) walk(fp, depth + 1); }
            else if (/\.pdf$/i.test(ent.name)) { try { files.push({ path: fp, name: ent.name, size: fs.statSync(fp).size }); } catch (e) {} }
          }
        };
        walk(dir, 0);
        files.sort((a, b) => a.path.localeCompare(b.path));
        return send(res, 200, JSON.stringify({ ok: true, dir, count: files.length, files }), MIME['.json']);
      } catch (e) { return send(res, 200, JSON.stringify({ ok: false, error: String(e) }), MIME['.json']); }
    }
    // 本地 PDF 批量导入（NDJSON 流，progress… + 最终 result）
    if (p === '/api/import-pdfs' && req.method === 'POST') {
      const b = JSON.parse(await readBody(req));
      const paths = (Array.isArray(b.paths) ? b.paths : []).filter(x => typeof x === 'string' && x.trim());
      if (!paths.length) return send(res, 400, JSON.stringify({ ok: false, error: '未选择 PDF' }), MIME['.json']);
      res.writeHead(200, { 'Content-Type': 'application/x-ndjson; charset=utf-8', 'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no' });
      const emit = (o) => res.write(JSON.stringify(o) + '\n');
      const args = ['import-pdfs']; if (b.enrich === false) args.push('--no-enrich');
      let out = ''; const ch = spawnAgent(args);
      ch.stderr.on('data', d => String(d).split(/\r?\n/).forEach(l => l.trim() && emit({ type: 'progress', line: l })));
      ch.stdout.on('data', d => out += d.toString());
      ch.on('error', e => { emit({ type: 'result', ok: false, error: String(e) }); res.end(); });
      ch.on('close', code => { let r = {}; try { r = JSON.parse(out); } catch (e) {} emit({ type: 'result', ok: code === 0 && r.ok !== false, added: r.added || 0, dup: r.dup || 0, failed: r.failed || 0, error: r.error || '' }); res.end(); });
      ch.stdin.write(JSON.stringify(paths)); ch.stdin.end();
      return;
    }
    // 引用关系图：读缓存的库内引用边 + 节点
    if (p === '/api/citegraph' && req.method === 'GET') {
      return send(res, 200, JSON.stringify(dbapi.getCiteGraph()), MIME['.json']);
    }
    // 构建/刷新引用图（抓 S2 参考文献，较慢；NDJSON 进度）
    if (p === '/api/cite-build' && req.method === 'POST') {
      res.writeHead(200, { 'Content-Type': 'application/x-ndjson; charset=utf-8', 'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no' });
      const emit = (o) => res.write(JSON.stringify(o) + '\n');
      let out = ''; const ch = spawnAgent(['citegraph']);
      ch.stderr.on('data', d => String(d).split(/\r?\n/).forEach(l => l.trim() && emit({ type: 'progress', line: l })));
      ch.stdout.on('data', d => out += d.toString());
      ch.on('error', e => { emit({ type: 'result', ok: false, error: String(e) }); res.end(); });
      ch.on('close', code => { let r = {}; try { r = JSON.parse(out); } catch (e) {} emit({ type: 'result', ok: code === 0 && r.ok !== false, edges: r.edges || 0, nodes: r.nodes || 0, error: r.error || '' }); res.end(); });
      return;
    }
    if (p === '/api/settings' && req.method === 'GET') {
      const s = readSettings(), e = readEnv();
      return send(res, 200, JSON.stringify({
        provider: s.provider || e.LLM_PROVIDER || 'deepseek',
        baseUrl: s.baseUrl || e.LLM_BASE_URL || '',
        model: s.model || e.LLM_MODEL || '',
        apiKeyTail: maskKey(s.apiKey || e.LLM_API_KEY), hasApiKey: !!(s.apiKey || e.LLM_API_KEY),
        s2KeyTail: maskKey(s.s2ApiKey), hasS2Key: !!s.s2ApiKey,
        pdfDir: s.pdfDir || '',
        researchTheme: s.researchTheme || ''
      }), MIME['.json']);
    }
    if (p === '/api/settings' && req.method === 'POST') {
      const b = JSON.parse(await readBody(req));
      const s = readSettings();
      if (b.provider) s.provider = b.provider;
      if (b.baseUrl !== undefined) s.baseUrl = b.baseUrl;
      if (b.model !== undefined) s.model = b.model;
      if (b.apiKey) s.apiKey = b.apiKey;          // 非空才更新
      if (b.s2ApiKey) s.s2ApiKey = b.s2ApiKey;
      if (b.pdfDir !== undefined) s.pdfDir = b.pdfDir.trim();
      if (b.researchTheme !== undefined) s.researchTheme = b.researchTheme.trim();
      writeSettings(s);
      return send(res, 200, JSON.stringify({ ok: true }), MIME['.json']);
    }
    if (p === '/api/test-llm' && req.method === 'POST') {
      const pyWin = path.join(ROOT, '.venv', 'Scripts', 'python.exe');
      const py = fs.existsSync(pyWin) ? pyWin : 'python';
      let out = '';
      const child = spawn(py, ['-m', 'agent', 'ping'], { cwd: ROOT, env: { ...process.env, PYTHONIOENCODING: 'utf-8', PYTHONUTF8: '1' } });
      child.stdout.on('data', d => out += d.toString());
      child.stderr.on('data', d => out += d.toString());
      child.on('error', e => send(res, 200, JSON.stringify({ ok: false, output: String(e) }), MIME['.json']));
      child.on('close', code => send(res, 200, JSON.stringify({ ok: code === 0, output: out }), MIME['.json']));
      return;
    }
    // ---- PDF 字节（绕过迅雷类下载器：路径不含 .pdf，由脚本 fetch 取字节）----
    if (p === '/pdfbytes') {
      const f = resolvePdfById(safeBase(u.searchParams.get('id')));
      if (!f) return send(res, 404, 'not found');
      res.writeHead(200, { 'Content-Type': 'application/octet-stream', 'Content-Length': fs.statSync(f).size, 'Cache-Control': 'no-store' });
      return fs.createReadStream(f).pipe(res);
    }
    // ---- PDF 流式（原文直链，供“↗ 原文”用）----
    if (p.startsWith('/papers/')) {
      const f = resolvePdfById(safeBase(decodeURIComponent(p.slice('/papers/'.length))).replace(/\.pdf$/i, ''));
      if (!f) return send(res, 404, 'PDF not found');
      res.writeHead(200, { 'Content-Type': MIME[path.extname(f).toLowerCase()] || 'application/octet-stream' });
      return fs.createReadStream(f).pipe(res);
    }
    // ---- 静态前端 ----
    let rel = p === '/' ? 'index.html' : decodeURIComponent(p).replace(/^\/+/, '');
    const fp = path.normalize(path.join(PUBLIC, rel));
    if (!fp.startsWith(PUBLIC)) return send(res, 403, 'forbidden');
    if (fs.existsSync(fp) && fs.statSync(fp).isFile()) {
      res.writeHead(200, { 'Content-Type': MIME[path.extname(fp).toLowerCase()] || 'application/octet-stream', 'Cache-Control': 'no-cache' });
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
