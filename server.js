// 论文学习 App — 零依赖本地服务（仅用 Node 内置模块）
// 启动：node server.js   然后浏览器打开 http://localhost:5173
const http = require('http');
const fs = require('fs');
const path = require('path');

const ROOT = __dirname;
const PUBLIC = path.join(ROOT, 'public');
const NOTES = path.join(ROOT, 'notes');
const DATA = path.join(ROOT, 'data');

// 读取配置（papersDir 指向现有 PDF 文件夹；端口）
let cfg = { papersDir: '../paper', port: 5173 };
try { Object.assign(cfg, JSON.parse(fs.readFileSync(path.join(ROOT, 'config.json'), 'utf8'))); } catch (e) {}
const PAPERS_DIR = path.resolve(ROOT, cfg.papersDir);
const PORT = process.env.PORT || cfg.port || 5173;

fs.mkdirSync(NOTES, { recursive: true });

const MIME = {
  '.html': 'text/html; charset=utf-8', '.css': 'text/css; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8', '.json': 'application/json; charset=utf-8',
  '.pdf': 'application/pdf', '.md': 'text/markdown; charset=utf-8',
  '.png': 'image/png', '.svg': 'image/svg+xml', '.ico': 'image/x-icon'
};

const send = (res, code, body, type) => {
  res.writeHead(code, { 'Content-Type': type || 'text/plain; charset=utf-8' });
  res.end(body);
};
const safeBase = (s) => path.basename(String(s || '')); // 防目录穿越
const readBody = (req) => new Promise((r) => { let d = ''; req.on('data', c => d += c); req.on('end', () => r(d)); });

const server = http.createServer(async (req, res) => {
  const u = new URL(req.url, 'http://localhost');
  const p = u.pathname;
  try {
    // ---- API ----
    if (p === '/api/papers') {
      const papers = JSON.parse(fs.readFileSync(path.join(DATA, 'papers.json'), 'utf8'));
      let prog = {};
      try { prog = JSON.parse(fs.readFileSync(path.join(DATA, 'progress.json'), 'utf8')); } catch (e) {}
      papers.forEach(x => {
        x.status = prog[x.id] || '未开始';
        const nf = path.join(NOTES, x.id + '.md');
        x.hasNote = fs.existsSync(nf) && fs.statSync(nf).size > 0;
      });
      return send(res, 200, JSON.stringify(papers), MIME['.json']);
    }
    if (p === '/api/note' && req.method === 'GET') {
      const f = path.join(NOTES, safeBase(u.searchParams.get('id')) + '.md');
      return send(res, 200, fs.existsSync(f) ? fs.readFileSync(f, 'utf8') : '', MIME['.md']);
    }
    if (p === '/api/note' && req.method === 'POST') {
      const b = JSON.parse(await readBody(req));
      fs.writeFileSync(path.join(NOTES, safeBase(b.id) + '.md'), b.content == null ? '' : b.content, 'utf8');
      return send(res, 200, JSON.stringify({ ok: true }), MIME['.json']);
    }
    if (p === '/api/explainer' && req.method === 'GET') {
      // 读取现有 paper/ 文件夹里的“论文讲解” md（只读参考）
      const f = path.join(PAPERS_DIR, safeBase(u.searchParams.get('id')) + '.md');
      return send(res, 200, fs.existsSync(f) ? fs.readFileSync(f, 'utf8') : '*(暂无讲解文件)*', MIME['.md']);
    }
    if (p === '/api/progress' && req.method === 'POST') {
      const b = JSON.parse(await readBody(req));
      let prog = {};
      try { prog = JSON.parse(fs.readFileSync(path.join(DATA, 'progress.json'), 'utf8')); } catch (e) {}
      prog[safeBase(b.id)] = b.status;
      fs.writeFileSync(path.join(DATA, 'progress.json'), JSON.stringify(prog, null, 2), 'utf8');
      return send(res, 200, JSON.stringify({ ok: true }), MIME['.json']);
    }
    // ---- PDF 字节（绕过迅雷类下载器：路径不含 .pdf，由脚本 fetch 取字节）----
    if (p === '/pdfbytes') {
      const f = path.join(PAPERS_DIR, safeBase(u.searchParams.get('id')) + '.pdf');
      if (!fs.existsSync(f)) return send(res, 404, 'not found');
      res.writeHead(200, { 'Content-Type': 'application/octet-stream', 'Content-Length': fs.statSync(f).size, 'Cache-Control': 'no-store' });
      return fs.createReadStream(f).pipe(res);
    }
    // ---- PDF 流式（原文直链，供“↗ 原文”用）----
    if (p.startsWith('/papers/')) {
      const f = path.join(PAPERS_DIR, safeBase(decodeURIComponent(p.slice('/papers/'.length))));
      if (!fs.existsSync(f)) return send(res, 404, 'PDF not found');
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
  console.log(' 论文学习 App 已启动');
  console.log(' 打开:  http://localhost:' + PORT);
  console.log(' PDF目录: ' + PAPERS_DIR);
  console.log(' 笔记目录: ' + NOTES);
  console.log(' 按 Ctrl+C 停止');
  console.log('========================================');
});
