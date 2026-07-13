// 论文学习 App — Node Web/API（数据走 SQLite，见 db.js / docs/DATABASE.md）
// 启动：node server.js   →  http://localhost:5173
const http = require('http');
const fs = require('fs');
const path = require('path');
const { spawn } = require('child_process');
const dbapi = require('./db');
const { createAgentRunner } = require('./lib/agent-runner');
const { createArtifactLocator, scanPdfDirectory } = require('./lib/artifacts');
const { MIME, readBody, safeBase, send, startNdjson } = require('./lib/http');
const { createTitleTranslationService } = require('./lib/title-translations');
const {
  applySettingsUpdate,
  buildSettingsView,
  createSettingsStore,
  ensureSettingsDirs,
  resolveDir: resolveSettingDir,
} = require('./lib/settings');

const ROOT = __dirname;
const PUBLIC = path.join(ROOT, 'public');

let cfg = { papersDir: '../paper', port: 5173 };
try { Object.assign(cfg, JSON.parse(fs.readFileSync(path.join(ROOT, 'config.json'), 'utf8'))); } catch (e) {}
const PAPERS_DIR = path.resolve(ROOT, cfg.papersDir);
const PDFS_DIR = path.join(ROOT, 'data', 'pdfs');   // 采集 Agent 下载的 PDF
const EXPLAINERS_DIR = path.join(ROOT, 'data', 'explainers');
const TRANSLATIONS_DIR = path.join(ROOT, 'data', 'translations');
const PORT = process.env.PORT || cfg.port || 5173;

const resolveDir = (d) => resolveSettingDir(ROOT, d);
const settingDir = (s, key, fallback) => resolveDir((s && s[key]) || fallback);
const agentRunner = createAgentRunner({ root: ROOT });
const pyExe = () => agentRunner.pythonExecutable();
const spawnAgent = (args, opts = {}) => agentRunner.spawn(args, opts);

// ---- 设置（模型/数据源），存 data/settings.json（gitignore），Python Agent 也读它 ----
const SETTINGS_PATH = path.join(ROOT, 'data', 'settings.json');
const settingsStore = createSettingsStore({ root: ROOT, settingsPath: SETTINGS_PATH });
const readSettings = () => settingsStore.read();
const writeSettings = (s) => settingsStore.write(s);
const readEnv = () => settingsStore.readEnv();
const artifactLocator = createArtifactLocator({
  root: ROOT,
  defaultPdfDir: PDFS_DIR,
  seedPdfDir: PAPERS_DIR,
  settingsStore,
  getPdfPath: (id) => dbapi.getPdfPath(id),
});
const resolvePdfById = (id) => artifactLocator.resolvePdfById(id);

// ---- 大模型直连（OpenAI 兼容协议）。与 agent/config.py 保持一致：settings.json 优先于 .env，再退供应商预设。
//      用于划词翻译这类“小而快”的请求，免去每次 spawn Python 的解释器冷启动(约 1~2s，重启后首次更久)。----
const llmConfig = () => settingsStore.llmConfig();
async function llmChat(messages, { temperature = 0.2, timeoutMs = 60000 } = {}) {
  const { apiKey, baseUrl, model } = llmConfig();
  if (!apiKey) throw new Error('未配置大模型 API Key');
  const url = baseUrl.replace(/\/+$/, '') + '/chat/completions';
  const ac = new AbortController();
  const timer = setTimeout(() => ac.abort(), timeoutMs);
  try {
    const r = await fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'Authorization': 'Bearer ' + apiKey },
      body: JSON.stringify({ model, messages, temperature }),
      signal: ac.signal,
    });
    if (!r.ok) throw new Error('LLM ' + r.status + ': ' + (await r.text().catch(() => '')).slice(0, 200));
    const j = await r.json();
    return ((j.choices && j.choices[0] && j.choices[0].message && j.choices[0].message.content) || '').trim();
  } finally { clearTimeout(timer); }
}
const titleTranslationService = createTitleTranslationService({
  repository: dbapi,
  chat: llmChat
});
// 划词翻译系统提示——与 agent/llm.py 的 TRANSLATE_SNIPPET_SYSTEM 保持一致（改一处记得两边同步）。
const TRANSLATE_SNIPPET_SYSTEM =
  '你是专业的学术论文翻译。用户会给你一段从 PDF 里直接选取的英文文字' +
  '（可能带换行、连字符断词，甚至从句子中间开始）。把它翻译成**通顺、地道的简体中文**。\n' +
  '- **只输出译文本身**：不要重复原文、不要任何前后缀说明、不要加引号或代码块。\n' +
  '- 合并 PDF 造成的硬换行与连字符断词（如 represen-\\ntation → representation），译成连贯句子，意译而非逐字硬译。\n' +
  '- 专有名词、模型/数据集/方法名、缩写(如 LLaVA、POPE、Transformer、CVPR)保留英文。\n' +
  '- 数学公式/变量/符号(如 $x$、\\alpha)保持原样不译。\n' +
  '- 即使片段很短或从句中间开始，也要尽力译成中文，绝不原样返回英文。';
// Node 直连失败（无 Key/网络/超时/空结果）时的回退：仍用 Python agent（冷启动慢但稳）。
const translateTextViaPython = (text) => new Promise((resolve) => {
  let out = '', err = '', done = false;
  const finish = (o) => { if (!done) { done = true; resolve(o); } };
  const ch = spawnAgent(['translate-text']);
  ch.stdout.on('data', d => out += d.toString());
  ch.stderr.on('data', d => err += d.toString());
  ch.on('error', e => finish({ ok: false, error: String(e) }));
  ch.on('close', code => finish({ ok: code === 0 && !!out.trim(), text: out.trim(), error: code === 0 ? '' : (err.trim().split(/\n/).pop() || '翻译失败') }));
  ch.stdin.write(text, 'utf8'); ch.stdin.end();
});

const server = http.createServer(async (req, res) => {
  const u = new URL(req.url, 'http://localhost');
  const p = u.pathname;
  try {
    // ---- API（SQLite）----
    if (p === '/api/papers') {
      const rows = dbapi.listPapers();
      // 标注 PDF 是否在本地：DB 记录的 pdf_path → 默认目录 → 自定义目录 → 种子目录
      for (const r of rows) r.hasPdf = artifactLocator.hasPdfForRow(r);
      return send(res, 200, JSON.stringify(rows), MIME['.json']);
    }
    if (p === '/api/title-translations' && req.method === 'GET') {
      return send(res, 200, JSON.stringify({
        ok: true,
        pending: titleTranslationService.pendingCount()
      }), MIME['.json']);
    }
    if (p === '/api/title-translations' && req.method === 'POST') {
      const body = JSON.parse((await readBody(req)) || '{}');
      const emit = startNdjson(res);
      let cancelled = false;
      res.on('close', () => { if (!res.writableEnded) cancelled = true; });
      const summary = await titleTranslationService.runBatch({
        limit: body.limit,
        isCancelled: () => cancelled,
        onEvent: event => { if (!cancelled) emit(event); }
      });
      if (!cancelled) {
        emit({ type: 'result', ok: true, summary });
        res.end();
      }
      return;
    }
    if (p === '/api/reviews' && req.method === 'GET') {
      return send(res, 200, JSON.stringify({ ok: true, ...dbapi.listReviewItems() }), MIME['.json']);
    }
    if (p === '/api/reviews/start' && req.method === 'POST') {
      const b = JSON.parse((await readBody(req)) || '{}');
      const id = safeBase(b.id);
      if (!id) return send(res, 400, JSON.stringify({ ok: false, error: '缺少 id' }), MIME['.json']);
      const plan = dbapi.ensureReviewPlan(id);
      if (!plan) return send(res, 404, JSON.stringify({ ok: false, error: '论文不存在' }), MIME['.json']);
      return send(res, 200, JSON.stringify({ ok: true, plan }), MIME['.json']);
    }
    if (p === '/api/reviews/complete' && req.method === 'POST') {
      const b = JSON.parse((await readBody(req)) || '{}');
      const id = safeBase(b.id);
      if (!id) return send(res, 400, JSON.stringify({ ok: false, error: '缺少 id' }), MIME['.json']);
      const plan = dbapi.completeReviewStep(id);
      if (!plan) return send(res, 404, JSON.stringify({ ok: false, error: '尚未加入复习计划' }), MIME['.json']);
      return send(res, 200, JSON.stringify({ ok: true, plan, reviews: dbapi.listReviewItems() }), MIME['.json']);
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
      const py = pyExe();
      const args = ['-m', 'agent', 'ingest', '--query', String(b.query), '--sources', sources.join(','),
        '--years', String(b.years || '2024-2026'), '--max', String(Math.min(parseInt(b.max) || 10, 50)),
        '--min-relevance', String(b.minRelevance == null ? 0.5 : b.minRelevance)];
      if (b.deep) args.push('--deep');
      if (b.expand) args.push('--expand');
      if (b.downloadPdf === false) args.push('--no-pdf');
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
      const emit = startNdjson(res);
      const args = ['search', '--query', String(b.query), '--sources', sources.join(','), '--years', String(b.years || '2024-2026'),
        '--max', String(Math.min(parseInt(b.max) || 10, 60)), '--min-relevance', String(b.minRelevance == null ? 0 : b.minRelevance)];
      if (b.expand) args.push('--expand');
      if (b.onlyA) args.push('--only-a');
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
      const emit = startNdjson(res);
      const args = ['ingest-selected']; if (b.deep) args.push('--deep'); if (b.downloadPdf === false) args.push('--no-pdf');
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
      const emit = startNdjson(res);
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
      const emit = startNdjson(res);
      const args = ['explain', '--id', id]; if (b.deep) args.push('--deep');
      let out = '', err = ''; const ch = spawnAgent(args);
      ch.stderr.on('data', d => String(d).split(/\r?\n/).forEach(l => { if (l.trim()) { err += l + '\n'; emit({ type: 'progress', line: l }); } }));
      ch.stdout.on('data', d => out += d.toString());
      ch.on('error', e => { emit({ type: 'result', ok: false, error: String(e) }); res.end(); });
      ch.on('close', code => { emit({ type: 'result', ok: code === 0 && !!out.trim(), markdown: out, error: code === 0 ? '' : (err.trim().split(/\n/).pop() || '生成失败') }); res.end(); });
      return;
    }
    // 批量生成讲解 · 缺讲解论文计数（GET）：给「一键生成讲解」按钮显示可生成篇数
    if (p === '/api/explain-batch' && req.method === 'GET') {
      const rows = dbapi.db.prepare("SELECT id FROM papers WHERE explainer IS NULL OR TRIM(explainer)=''").all();
      let withPdf = 0;
      for (const r of rows) { if (resolvePdfById(r.id)) withPdf++; }
      return send(res, 200, JSON.stringify({ pending: rows.length, withPdf, noPdf: rows.length - withPdf }), MIME['.json']);
    }
    // 批量生成讲解（LLM，逐篇通读本地 PDF 全文，与单篇「读PDF全文」逻辑一致）：NDJSON 流
    //   progress: BATCH::total::N::skip::M / ITEM::i::N::(start|done|fail)::id::info / STAGE:: / 末尾重建索引
    //   result:   { ok, summary:{ total, done, failed[], skipped_no_pdf[] } }
    if (p === '/api/explain-batch' && req.method === 'POST') {
      const b = JSON.parse((await readBody(req)) || '{}');
      const emit = startNdjson(res);
      const args = ['explain-batch'];
      const lim = parseInt(b.limit); if (lim && lim > 0) args.push('--limit', String(lim));
      let out = '', err = '', aborted = false;
      const ch = spawnAgent(args);
      ch.stderr.on('data', d => String(d).split(/\r?\n/).forEach(l => { if (l.trim()) { err += l + '\n'; emit({ type: 'progress', line: l }); } }));
      ch.stdout.on('data', d => out += d.toString());
      req.on('close', () => { if (!res.writableEnded) { aborted = true; try { ch.kill(); } catch (e) {} } });  // 关页/停止 → 杀子进程（已生成的已落库）
      ch.on('error', e => { if (!aborted) { emit({ type: 'result', ok: false, error: String(e) }); res.end(); } });
      ch.on('close', code => {
        if (aborted) return;
        let summary = null; try { summary = JSON.parse(out); } catch (e) {}
        const finish = () => { emit({ type: 'result', ok: code === 0 && !!summary, summary, error: code === 0 ? '' : (err.trim().split(/\n/).pop() || '批量生成失败') }); res.end(); };
        if (summary && summary.done > 0) {   // 新讲解作废了旧向量 → 末尾补建语义索引，保持检索 / MCP 最新
          emit({ type: 'progress', line: 'STAGE::reindex::重建语义索引…' });
          const ch2 = spawnAgent(['embed', '--scope', 'missing']);
          ch2.on('close', finish); ch2.on('error', finish);
        } else finish();
      });
      return;
    }
    // 全文翻译（LLM，分块并发）：NDJSON 流，progress(TOTAL/CHUNK…) + 最终 result.markdown（已写入 DB）
    if (p === '/api/translate' && req.method === 'POST') {
      const b = JSON.parse(await readBody(req));
      const id = safeBase(b.id);
      if (!id) return send(res, 400, JSON.stringify({ ok: false, error: '缺少 id' }), MIME['.json']);
      const emit = startNdjson(res);
      let out = '', err = ''; const ch = spawnAgent(['translate', '--id', id]);
      ch.stderr.on('data', d => String(d).split(/\r?\n/).forEach(l => { if (l.trim()) { err += l + '\n'; emit({ type: 'progress', line: l }); } }));
      ch.stdout.on('data', d => out += d.toString());
      ch.on('error', e => { emit({ type: 'result', ok: false, error: String(e) }); res.end(); });
      ch.on('close', code => { emit({ type: 'result', ok: code === 0 && !!out.trim(), markdown: out, error: code === 0 ? '' : (err.trim().split(/\n/).pop() || '翻译失败') }); res.end(); });
      return;
    }
    // 划词翻译：选中一小段 PDF 文字 → 译中文（单次 JSON，非流式）。
    // 默认 Node 直连大模型（免 Python 冷启动，约快 1~2s）；出错/无 Key/空结果时回退 Python agent。
    if (p === '/api/translate-text' && req.method === 'POST') {
      const b = JSON.parse(await readBody(req));
      const text = String(b.text || '').trim();
      if (!text) return send(res, 400, JSON.stringify({ ok: false, error: '缺少文本' }), MIME['.json']);
      if (text.length > 6000) return send(res, 413, JSON.stringify({ ok: false, error: '选区过长，请缩短后再试' }), MIME['.json']);
      try {
        const out = await llmChat([
          { role: 'system', content: TRANSLATE_SNIPPET_SYSTEM },
          { role: 'user', content: text },
        ], { temperature: 0.2 });
        if (out) return send(res, 200, JSON.stringify({ ok: true, text: out }), MIME['.json']);
      } catch (e) { /* 落到下面的 Python 回退 */ }
      const r = await translateTextViaPython(text);
      return send(res, 200, JSON.stringify(r), MIME['.json']);
    }
    // 相似论文推荐（S2 Recommendations）：NDJSON 流，progress… + 最终 result.candidates
    if (p === '/api/recommend' && req.method === 'POST') {
      const b = JSON.parse(await readBody(req));
      const id = safeBase(b.id);
      if (!id) return send(res, 400, JSON.stringify({ ok: false, error: '缺少 id' }), MIME['.json']);
      const emit = startNdjson(res);
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
      const emit = startNdjson(res);
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
      const emit = startNdjson(res);
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
      return send(res, 200, JSON.stringify(scanPdfDirectory(dir)), MIME['.json']);
    }
    // 本地 PDF 批量导入（NDJSON 流，progress… + 最终 result）
    if (p === '/api/import-pdfs' && req.method === 'POST') {
      const b = JSON.parse(await readBody(req));
      const paths = (Array.isArray(b.paths) ? b.paths : []).filter(x => typeof x === 'string' && x.trim());
      if (!paths.length) return send(res, 400, JSON.stringify({ ok: false, error: '未选择 PDF' }), MIME['.json']);
      const emit = startNdjson(res);
      const args = ['import-pdfs']; if (b.enrich === false) args.push('--no-enrich');
      let out = ''; const ch = spawnAgent(args);
      ch.stderr.on('data', d => String(d).split(/\r?\n/).forEach(l => l.trim() && emit({ type: 'progress', line: l })));
      ch.stdout.on('data', d => out += d.toString());
      ch.on('error', e => { emit({ type: 'result', ok: false, error: String(e) }); res.end(); });
      ch.on('close', code => { let r = {}; try { r = JSON.parse(out); } catch (e) {} emit({ type: 'result', ok: code === 0 && r.ok !== false, added: r.added || 0, dup: r.dup || 0, failed: r.failed || 0, error: r.error || '' }); res.end(); });
      ch.stdin.write(JSON.stringify(paths)); ch.stdin.end();
      return;
    }
    // 下载/补齐库内 PDF（NDJSON：progress... + result）
    if (p === '/api/download-pdfs' && req.method === 'POST') {
      const b = JSON.parse(await readBody(req));
      const ids = Array.isArray(b.ids) ? b.ids.map(x => safeBase(x)).filter(Boolean) : [];
      const limit = Math.max(0, Math.min(parseInt(b.limit) || 0, 500));
      const emit = startNdjson(res);
      const args = ['download-pdfs']; if (limit) args.push('--limit', String(limit));
      let out = ''; const ch = spawnAgent(args);
      ch.stderr.on('data', d => String(d).split(/\r?\n/).forEach(l => l.trim() && emit({ type: 'progress', line: l })));
      ch.stdout.on('data', d => out += d.toString());
      ch.on('error', e => { emit({ type: 'result', ok: false, error: String(e) }); res.end(); });
      ch.on('close', code => { let r = {}; try { r = JSON.parse(out); } catch (e) {} emit({ type: 'result', ok: code === 0 && r.ok !== false, ...r, error: r.error || '' }); res.end(); });
      ch.stdin.write(ids.length ? JSON.stringify(ids) : '');
      ch.stdin.end();
      return;
    }
    if (p === '/api/pdf/status' && req.method === 'GET') {
      const id = safeBase(u.searchParams.get('id'));
      const f = id ? resolvePdfById(id) : null;
      const row = id ? dbapi.getPaper(id) : null;
      return send(res, 200, JSON.stringify({
        ok: true,
        id,
        hasPdf: !!f,
        size: f ? fs.statSync(f).size : 0,
        path: f || '',
        canDownload: !!(row && (row.pdf_url || row.arxiv_id || /arxiv/i.test(row.url || '') || /^\d{4}\.\d{4,5}/.test(row.id || '')))
      }), MIME['.json']);
    }
    // 引用关系图：读缓存的库内引用边 + 节点
    if (p === '/api/citegraph' && req.method === 'GET') {
      return send(res, 200, JSON.stringify(dbapi.getCiteGraph()), MIME['.json']);
    }
    // 规整会议名（LLM 把全库 venue 统一成标准简称；NDJSON 进度 + 映射）
    if (p === '/api/norm-venues' && req.method === 'POST') {
      const emit = startNdjson(res);
      let out = ''; const ch = spawnAgent(['norm-venues']);
      ch.stderr.on('data', d => String(d).split(/\r?\n/).forEach(l => l.trim() && emit({ type: 'progress', line: l })));
      ch.stdout.on('data', d => out += d.toString());
      ch.on('error', e => { emit({ type: 'result', ok: false, error: String(e) }); res.end(); });
      ch.on('close', code => { let r = {}; try { r = JSON.parse(out); } catch (e) {} emit({ type: 'result', ok: code === 0 && r.ok !== false, changed: r.changed || 0, mapping: r.mapping || {}, error: r.error || '' }); res.end(); });
      return;
    }
    // 构建/刷新引用图（抓 S2 参考文献，较慢；NDJSON 进度）
    if (p === '/api/cite-build' && req.method === 'POST') {
      const emit = startNdjson(res);
      let out = ''; const ch = spawnAgent(['citegraph']);
      ch.stderr.on('data', d => String(d).split(/\r?\n/).forEach(l => l.trim() && emit({ type: 'progress', line: l })));
      ch.stdout.on('data', d => out += d.toString());
      ch.on('error', e => { emit({ type: 'result', ok: false, error: String(e) }); res.end(); });
      ch.on('close', code => { let r = {}; try { r = JSON.parse(out); } catch (e) {} emit({ type: 'result', ok: code === 0 && r.ok !== false, edges: r.edges || 0, nodes: r.nodes || 0, error: r.error || '' }); res.end(); });
      return;
    }
    if (p === '/api/settings' && req.method === 'GET') {
      const s = readSettings(), e = readEnv();
      return send(res, 200, JSON.stringify(buildSettingsView({
        root: ROOT,
        settings: s,
        env: e,
        defaultDirs: {
          pdfDir: PDFS_DIR,
          explainerDir: EXPLAINERS_DIR,
          translationDir: TRANSLATIONS_DIR,
        },
      })), MIME['.json']);
    }
    if (p === '/api/settings' && req.method === 'POST') {
      const b = JSON.parse(await readBody(req));
      const s = applySettingsUpdate(readSettings(), b);
      ensureSettingsDirs(s, { root: ROOT });
      writeSettings(s);
      return send(res, 200, JSON.stringify({ ok: true }), MIME['.json']);
    }
    if (p === '/api/test-llm' && req.method === 'POST') {
      const py = pyExe();
      let out = '';
      const child = spawn(py, ['-m', 'agent', 'ping'], { cwd: ROOT, env: { ...process.env, PYTHONIOENCODING: 'utf-8', PYTHONUTF8: '1' } });
      child.stdout.on('data', d => out += d.toString());
      child.stderr.on('data', d => out += d.toString());
      child.on('error', e => send(res, 200, JSON.stringify({ ok: false, output: String(e) }), MIME['.json']));
      child.on('close', code => send(res, 200, JSON.stringify({ ok: code === 0, output: out }), MIME['.json']));
      return;
    }
    // ============ 后台采集任务（P5）============
    // 发起后台任务：建行 → 后台 spawn run-job（不等结束）→ 立即返回 id
    if (p === '/api/jobs' && req.method === 'POST') {
      const b = JSON.parse(await readBody(req));
      const sources = (Array.isArray(b.sources) ? b.sources : []).filter(s => ['semanticscholar', 'arxiv', 'openalex', 'dblp'].includes(s));
      if (!b.query || !String(b.query).trim() || !sources.length) return send(res, 400, JSON.stringify({ ok: false, error: '缺少检索方向或数据源' }), MIME['.json']);
      const yrs = String(b.years || '2024-2026').split('-');
      const id = dbapi.createJob({ query: b.query, sources, yearFrom: parseInt(yrs[0]) || null, yearTo: parseInt(yrs[1] || yrs[0]) || null, max: b.max, minRelevance: b.minRelevance, onlyA: !!b.onlyA });
      runJobBackground(id);
      return send(res, 200, JSON.stringify({ ok: true, id }), MIME['.json']);
    }
    if (p === '/api/jobs' && req.method === 'GET') {
      return send(res, 200, JSON.stringify(dbapi.listJobs()), MIME['.json']);
    }
    if (p === '/api/jobs/detail' && req.method === 'GET') {
      const id = parseInt(u.searchParams.get('id'));
      const job = dbapi.getJob(id);
      if (!job) return send(res, 404, JSON.stringify({ ok: false, error: '任务不存在' }), MIME['.json']);
      return send(res, 200, JSON.stringify({ ok: true, job, candidates: dbapi.listJobCandidates(id) }), MIME['.json']);
    }
    if (p === '/api/jobs/delete' && req.method === 'POST') {
      const b = JSON.parse(await readBody(req));
      dbapi.deleteJob(parseInt(b.id));
      return send(res, 200, JSON.stringify({ ok: true }), MIME['.json']);
    }
    // 确认入库：选中的暂存候选走 ingest-selected（复用入库链路）→ 标记 added + 收尾
    if (p === '/api/jobs/confirm' && req.method === 'POST') {
      const b = JSON.parse(await readBody(req));
      const jobId = parseInt(b.jobId);
      const cands = Array.isArray(b.candidates) ? b.candidates : [];
      if (!jobId || !cands.length) return send(res, 400, JSON.stringify({ ok: false, error: '缺少任务或候选' }), MIME['.json']);
      const cids = cands.map(c => c._cid).filter(Boolean);
      const emit = startNdjson(res);
      const args = ['ingest-selected']; if (b.deep) args.push('--deep'); if (b.downloadPdf === false) args.push('--no-pdf');
      let added = 0; const ch = spawnAgent(args);
      ch.stderr.on('data', d => String(d).split(/\r?\n/).forEach(l => { if (!l.trim()) return; const m = /^INGESTED::(\d+)/.exec(l); if (m) added = +m[1]; emit({ type: 'progress', line: l }); }));
      ch.on('error', e => { emit({ type: 'done', ok: false, error: String(e) }); res.end(); });
      ch.on('close', code => {
        try { dbapi.markJobCandidateIds(jobId, cids, 'added'); dbapi.bumpJobAdded(jobId, added); dbapi.closeJobIfEmpty(jobId); } catch (e) {}
        emit({ type: 'done', ok: code === 0, added }); res.end();
      });
      ch.stdin.write(JSON.stringify(cands)); ch.stdin.end();
      return;
    }
    // ============ 定时任务（P5）============
    if (p === '/api/schedules' && req.method === 'GET') {
      return send(res, 200, JSON.stringify(dbapi.listSchedules()), MIME['.json']);
    }
    if (p === '/api/schedules' && req.method === 'POST') {
      const b = JSON.parse(await readBody(req));
      const sources = (Array.isArray(b.sources) ? b.sources : []).filter(s => ['semanticscholar', 'arxiv', 'openalex', 'dblp'].includes(s));
      if (!b.query || !String(b.query).trim() || !sources.length) return send(res, 400, JSON.stringify({ ok: false, error: '缺少检索方向或数据源' }), MIME['.json']);
      const id = dbapi.createSchedule({ query: b.query, sources, years: b.years, max: b.max, minRelevance: b.minRelevance, onlyA: !!b.onlyA, everyDays: b.everyDays });
      return send(res, 200, JSON.stringify({ ok: true, id }), MIME['.json']);
    }
    if (p === '/api/schedules/toggle' && req.method === 'POST') {
      const b = JSON.parse(await readBody(req));
      dbapi.toggleSchedule(parseInt(b.id), !!b.enabled);
      return send(res, 200, JSON.stringify({ ok: true }), MIME['.json']);
    }
    if (p === '/api/schedules/delete' && req.method === 'POST') {
      const b = JSON.parse(await readBody(req));
      dbapi.deleteSchedule(parseInt(b.id));
      return send(res, 200, JSON.stringify({ ok: true }), MIME['.json']);
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

// ============ 后台任务执行 + 定时调度（P5）============
// 后台跑一个采集任务：spawn run-job（不绑定 HTTP 响应），stderr 进 job.log；结束兜底状态
function runJobBackground(jobId) {
  const ch = spawnAgent(['run-job', '--id', String(jobId)]);
  ch.stderr.on('data', d => { try { dbapi.appendJobLog(jobId, d.toString()); } catch (e) {} });
  ch.on('error', e => { try { dbapi.appendJobLog(jobId, '\nERR::' + e); dbapi.setJobStatus(jobId, 'failed'); } catch (_) {} });
  ch.on('close', code => {
    try { const j = dbapi.getJob(jobId); if (j && j.status === 'running') dbapi.setJobStatus(jobId, code === 0 ? 'review' : 'failed'); } catch (e) {}
  });
}
// 定时调度：到点的 schedule → 建任务 + 跑 + 顺延 next_run
function checkSchedules() {
  try {
    for (const s of dbapi.dueSchedules()) {
      const yrs = String(s.years || '2024-2026').split('-');
      const id = dbapi.createJob({
        query: s.query, sources: (s.sources || 'semanticscholar').split(','),
        yearFrom: parseInt(yrs[0]) || null, yearTo: parseInt(yrs[1] || yrs[0]) || null,
        max: s.max_papers, minRelevance: s.min_relevance, onlyA: !!s.only_a, scheduleId: s.id
      });
      runJobBackground(id);
      dbapi.markScheduleRan(s.id, s.every_days);
      console.log(`[调度] 定时 #${s.id} 触发 → job #${id}：${s.query}`);
    }
  } catch (e) { console.error('[调度] 出错', e); }
}

server.listen(PORT, () => {
  console.log('========================================');
  console.log(' 论文学习 App 已启动 (SQLite)');
  console.log(' 打开:  http://localhost:' + PORT);
  console.log(' 数据库: ' + (process.env.DB_PATH || path.join(ROOT, 'data', 'app.db')));
  console.log(' PDF目录: ' + settingDir(readSettings(), 'pdfDir', path.relative(ROOT, PDFS_DIR)));
  console.log(' 按 Ctrl+C 停止');
  console.log('========================================');
  try { const n = dbapi.resetOrphanJobs(); if (n) console.log(` ⚠ 已重置 ${n} 个中断的采集任务`); } catch (e) {}
  setTimeout(checkSchedules, 8000);              // 启动 8s 后查一次定时
  setInterval(checkSchedules, 10 * 60 * 1000);   // 之后每 10 分钟查一次
});
