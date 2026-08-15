const fs = require('node:fs');
const path = require('node:path');
const { EventEmitter } = require('node:events');
const { PassThrough, Writable } = require('node:stream');
const childProcess = require('node:child_process');

const liveDatabasePaths = new Set(
  ['', '-wal', '-shm'].map((suffix) => path.resolve(process.cwd(), `data/app.db${suffix}`)),
);
const liveDatabaseAccesses = new Set();

function recordPath(value) {
  if (typeof value !== 'string' && !Buffer.isBuffer(value) && !(value instanceof URL)) return;
  const resolved = path.resolve(value instanceof URL ? require('node:url').fileURLToPath(value) : value.toString());
  if (liveDatabasePaths.has(resolved)) liveDatabaseAccesses.add(resolved);
}

for (const name of ['accessSync', 'existsSync', 'openSync', 'readFileSync', 'statSync']) {
  const original = fs[name];
  fs[name] = function trackedPath(pathValue, ...args) {
    recordPath(pathValue);
    return original.call(this, pathValue, ...args);
  };
}

const originalSetTimeout = global.setTimeout;
const originalSetInterval = global.setInterval;
global.setTimeout = function deterministicTimeout(callback, delay, ...args) {
  if (process.env.DISABLE_SCHEDULES === '1' && Number(delay) === 8000) {
    return { ref() { return this; }, unref() { return this; }, hasRef() { return false; } };
  }
  return originalSetTimeout(callback, delay, ...args);
};
global.setInterval = function deterministicInterval(callback, delay, ...args) {
  if (process.env.DISABLE_SCHEDULES === '1' && Number(delay) === 10 * 60 * 1000) {
    return { ref() { return this; }, unref() { return this; }, hasRef() { return false; } };
  }
  return originalSetInterval(callback, delay, ...args);
};

process.on('message', (message) => {
  if (!message || message.type !== 'legacy-server-inspect') return;
  if (typeof process.send === 'function') {
    process.send({
      type: 'legacy-server-inspection',
      requestId: message.requestId,
      liveDatabaseAccesses: [...liveDatabaseAccesses].sort(),
    });
  }
});

const candidate = {
  source: 'semanticscholar', source_id: 'fixture-source', title: 'Fixture Candidate', authors: ['Ada'],
  venue: 'CVPR', year: '2026', abstract: 'abstract', tldr: 'tldr', fields: ['Computer Science'],
  citations: 7, url: 'https://example.test/paper', pdf_url: null, arxiv_id: null, doi: null,
  s2_id: 'fixture-s2', ccf: 'A', type: '方法', topic: '测试', task: null, models: [], datasets: [],
  contribution: null, llm_tldr: null, tags: [], relevance: 0.9, in_library: false, _cid: null,
};

function agentResult(command) {
  const results = {
    ping: { stdout: 'pong\n' },
    ingest: { stdout: 'ingest complete\n' },
    expand: { stdout: `${JSON.stringify(['expanded query'])}\n` },
    search: { stdout: `${JSON.stringify([candidate])}\n`, stderr: 'SEARCH::fixture\n' },
    'verify-venue': {
      stdout: `${JSON.stringify([{ venue: 'CVPR', year: '2026', matched: true, skipped: false, source_of_truth: 'dblp', changed: false, orig_venue: 'CVPR', ccf: 'A', note: '', error: false }])}\n`,
      stderr: 'VERIFY::fixture\n',
    },
    'ingest-selected': { stderr: 'INGESTED::1\n' },
    explain: { stdout: '# Fixture explainer\n', stderr: 'EXPLAIN::fixture\n' },
    'explain-batch': { stdout: `${JSON.stringify({ total: 1, done: 1, failed: [], skipped_no_pdf: [] })}\n`, stderr: 'BATCH::fixture\n' },
    translate: { stdout: '# Fixture translation\n', stderr: 'TRANSLATE::fixture\n' },
    'translate-text': { stdout: '测试译文\n' },
    recommend: { stdout: `${JSON.stringify({ ok: true, candidates: [candidate] })}\n`, stderr: 'RECOMMEND::fixture\n' },
    embed: { stdout: `${JSON.stringify({ ok: true, indexed: 1, total: 1 })}\n`, stderr: 'EMBED::fixture\n' },
    semsearch: { stdout: `${JSON.stringify({ ok: true, results: [{ id: 'fixture-paper', score: 0.95 }] })}\n`, stderr: 'SEARCH::fixture\n' },
    'import-pdfs': { stdout: `${JSON.stringify({ ok: true, total: 1, added: 1, dup: 0, failed: 0 })}\n`, stderr: 'IMPORT::fixture\n' },
    'download-pdfs': { stdout: `${JSON.stringify({ ok: true, total: 1, downloaded: 1, skipped: 0, failed: 0 })}\n`, stderr: 'DOWNLOAD::fixture\n' },
    'norm-venues': { stdout: `${JSON.stringify({ ok: true, changed: 1, mapping: { cvpr: 'CVPR' } })}\n`, stderr: 'VENUES::fixture\n' },
    citegraph: { stdout: `${JSON.stringify({ ok: true, edges: 1, nodes: 2 })}\n`, stderr: 'CITE::fixture\n' },
    'run-job': {},
  };
  return results[command] ?? { code: 1, stderr: `unsupported fixture command: ${command}\n` };
}

function fakeAgentSpawn(_executable, args = []) {
  const child = new EventEmitter();
  child.stdout = new PassThrough();
  child.stderr = new PassThrough();
  child.killed = false;
  let closed = false;
  let input = '';
  const commandIndex = args[0] === '-m' && args[1] === 'agent' ? 2 : 0;
  const command = args[commandIndex];
  const forcedErrors = new Set(
    String(process.env.LEGACY_FIXTURE_AGENT_ERROR_COMMANDS || '').split(',').filter(Boolean),
  );
  const waitsForInput = new Set(['verify-venue', 'ingest-selected', 'import-pdfs', 'download-pdfs']).has(command);
  const finish = () => {
    if (closed) return;
    closed = true;
    const result = agentResult(command, input);
    if (result.stdout) child.stdout.write(result.stdout);
    if (result.stderr) child.stderr.write(result.stderr);
    child.stdout.end();
    child.stderr.end();
    setImmediate(() => child.emit('close', result.code ?? 0, null));
  };
  child.stdin = new Writable({
    write(chunk, _encoding, callback) {
      input += chunk.toString();
      callback();
    },
  });
  child.stdin.once('finish', finish);
  child.kill = () => {
    child.killed = true;
    if (!closed) {
      closed = true;
      setImmediate(() => child.emit('close', null, 'SIGTERM'));
    }
    return true;
  };
  if (forcedErrors.has(command)) {
    setImmediate(() => {
      if (closed) return;
      closed = true;
      child.stdout.end();
      child.stderr.end();
      child.emit('error', new Error(`forced fixture error: ${command}`));
    });
  } else if (!waitsForInput) {
    setImmediate(finish);
  }
  return child;
}

childProcess.spawn = fakeAgentSpawn;
global.fetch = async (_input, init = {}) => {
  const delay = Number(process.env.LEGACY_FIXTURE_TITLE_TRANSLATION_DELAY_MS || 0);
  if (delay > 0) {
    await new Promise((resolve, reject) => {
      const timer = setTimeout(resolve, delay);
      const abort = () => {
        clearTimeout(timer);
        reject(new DOMException('The operation was aborted', 'AbortError'));
      };
      if (init.signal?.aborted) abort();
      else init.signal?.addEventListener('abort', abort, { once: true });
    });
  }
  return new Response(JSON.stringify({
  choices: [{ message: { content: '测试中文题名' } }],
}), {
  status: 200,
  headers: { 'Content-Type': 'application/json' },
  });
};
