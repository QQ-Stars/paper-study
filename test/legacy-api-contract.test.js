const assert = require('node:assert/strict');
const { existsSync, mkdirSync, readFileSync, statSync, writeFileSync } = require('node:fs');
const { createHash } = require('node:crypto');
const path = require('node:path');
const test = require('node:test');

const REPO_ROOT = path.resolve(__dirname, '..');
const LEDGER_PATH = path.join(REPO_ROOT, 'contracts', 'legacy-api-v1.json');
const LEGACY_LEDGER = JSON.parse(readFileSync(LEDGER_PATH, 'utf8'));
const OBSERVED_BLACK_BOX_CASES = new Set();

const EXPECTED_ENDPOINTS = [
  ['GET', '/api/papers'],
  ['GET', '/api/paper/get'],
  ['GET', '/api/note'],
  ['GET', '/api/explainer'],
  ['GET', '/api/translation'],
  ['GET', '/pdfbytes'],
  ['GET', '/api/reviews'],
  ['POST', '/api/reviews/start'],
  ['POST', '/api/reviews/complete'],
  ['POST', '/api/note'],
  ['POST', '/api/progress'],
  ['POST', '/api/favorite'],
  ['POST', '/api/delete'],
  ['POST', '/api/paper/add'],
  ['POST', '/api/paper/update'],
  ['POST', '/api/expand'],
  ['POST', '/api/ingest'],
  ['POST', '/api/search'],
  ['POST', '/api/verify-venue'],
  ['POST', '/api/ingest-selected'],
  ['GET', '/api/title-translations'],
  ['POST', '/api/title-translations'],
  ['POST', '/api/explain'],
  ['GET', '/api/explain-batch'],
  ['POST', '/api/explain-batch'],
  ['POST', '/api/translate'],
  ['POST', '/api/translate-text'],
  ['GET', '/api/scan-pdfs'],
  ['POST', '/api/import-pdfs'],
  ['POST', '/api/download-pdfs'],
  ['GET', '/api/pdf/status'],
  ['POST', '/api/recommend'],
  ['POST', '/api/embed'],
  ['POST', '/api/semsearch'],
  ['GET', '/api/citegraph'],
  ['POST', '/api/norm-venues'],
  ['POST', '/api/cite-build'],
  ['GET', '/api/settings'],
  ['POST', '/api/settings'],
  ['POST', '/api/test-llm'],
  ['GET', '/api/jobs'],
  ['POST', '/api/jobs'],
  ['GET', '/api/jobs/detail'],
  ['POST', '/api/jobs/delete'],
  ['POST', '/api/jobs/confirm'],
  ['GET', '/api/schedules'],
  ['POST', '/api/schedules'],
  ['POST', '/api/schedules/toggle'],
  ['POST', '/api/schedules/delete'],
];

test('legacy contract ledger is versioned, complete, and internally consistent', () => {
  const ledger = LEGACY_LEDGER;
  assert.equal(ledger.version, 'legacy-api-v1');
  assert.ok(Array.isArray(ledger.endpoints));

  const observedPairs = [];
  for (const endpoint of ledger.endpoints) {
    assert.match(endpoint.method, /^(GET|POST)$/);
    assert.match(endpoint.path, /^\//);
    assert.ok(['json', 'ndjson', 'text', 'bytes'].includes(endpoint.responseKind));
    assert.equal(typeof endpoint.successShape, 'object');
    assert.ok(Array.isArray(endpoint.successShape.requiredKeys));
    assert.ok(Array.isArray(endpoint.successShape.optionalKeys));
    if (endpoint.requestKind === 'json') {
      assert.ok(Array.isArray(endpoint.request.requiredKeys));
      assert.ok(Array.isArray(endpoint.request.optionalKeys));
    }
    if (endpoint.responseKind === 'ndjson') {
      assert.ok(['result', 'done'].includes(endpoint.terminalType));
      assert.ok(Array.isArray(endpoint.terminalSuccessKeys));
      assert.ok(endpoint.terminalSuccessKeys.length > 0);
    }
    observedPairs.push(`${endpoint.method} ${endpoint.path}`);
  }

  assert.equal(new Set(observedPairs).size, observedPairs.length, 'duplicate method/path record');
  assert.deepEqual(
    [...observedPairs].sort(),
    EXPECTED_ENDPOINTS.map(([method, endpointPath]) => `${method} ${endpointPath}`).sort(),
  );
  assert.equal(ledger.endpoints.length, 49);
});

function endpoint(method, endpointPath) {
  const record = LEGACY_LEDGER.endpoints.find(
    (candidate) => candidate.method === method && candidate.path === endpointPath,
  );
  assert.ok(record, `missing ledger record for ${method} ${endpointPath}`);
  OBSERVED_BLACK_BOX_CASES.add(`${method} ${endpointPath}`);
  return record;
}

function assertObjectShape(value, requiredKeys, optionalKeys) {
  assert.ok(value && typeof value === 'object' && !Array.isArray(value));
  for (const key of requiredKeys) assert.ok(Object.hasOwn(value, key), `missing response key ${key}`);
  const allowed = new Set([...requiredKeys, ...optionalKeys]);
  assert.deepEqual(Object.keys(value).filter((key) => !allowed.has(key)), []);
}

function assertSuccessShape(value, shape) {
  if (shape.rootKind === 'array') {
    assert.ok(Array.isArray(value));
    for (const item of value) {
      assertObjectShape(item, shape.itemRequiredKeys ?? [], shape.itemOptionalKeys ?? []);
    }
    return;
  }
  if (shape.rootKind === 'nullable-object' && value === null) return;
  if (shape.rootKind === 'string') {
    assert.equal(typeof value, 'string');
    return;
  }
  assertObjectShape(value, shape.requiredKeys, shape.optionalKeys);
}

function assertErrorShape(value, shape) {
  if (shape.rootKind === 'text') {
    assert.equal(typeof value, 'string');
    return;
  }
  assertObjectShape(value, shape.requiredKeys, shape.optionalKeys);
  if (Object.hasOwn(value, 'ok')) assert.equal(value.ok, false);
}

async function requestDocumentedError(child, method, endpointPath, body) {
  const record = endpoint(method, endpointPath.split('?')[0]);
  assert.ok(record.errorShape, `${method} ${endpointPath} has no documented error shape`);
  const response = await child.request(endpointPath, body === undefined ? { method } : {
    method,
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  assert.ok(
    record.errorShape.statuses.includes(response.status),
    `${method} ${endpointPath} returned undocumented error status ${response.status}`,
  );
  const value = record.errorShape.rootKind === 'text'
    ? await response.text()
    : await response.json();
  assertErrorShape(value, record.errorShape);
  return { response, value };
}

async function requestJson(child, method, endpointPath, body) {
  const record = endpoint(method, endpointPath.split('?')[0]);
  const response = await child.request(endpointPath, body === undefined ? { method } : {
    method,
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  assert.equal(response.status, 200, `${method} ${endpointPath}`);
  assert.equal(response.headers.get('content-type'), record.contentType);
  const value = await response.json();
  assertSuccessShape(value, record.successShape);
  return value;
}

async function requestText(child, endpointPath) {
  const record = endpoint('GET', endpointPath.split('?')[0]);
  const response = await child.request(endpointPath);
  assert.equal(response.status, 200, endpointPath);
  assert.equal(response.headers.get('content-type'), record.contentType);
  return response.text();
}

async function requestNdjson(child, endpointPath, body) {
  const record = endpoint('POST', endpointPath);
  const response = await child.request(endpointPath, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  assert.equal(response.status, 200, endpointPath);
  assert.equal(response.headers.get('content-type'), record.contentType);
  const frames = (await response.text()).trim().split(/\r?\n/).filter(Boolean).map(JSON.parse);
  const terminalIndexes = frames
    .map((frame, index) => frame.type === record.terminalType ? index : -1)
    .filter((index) => index >= 0);
  assert.deepEqual(terminalIndexes, [frames.length - 1], `${endpointPath} terminal ordering`);
  for (const frame of frames.slice(0, -1)) {
    assert.equal(frame.type, 'progress');
  }
  const terminal = frames.at(-1);
  assert.equal(terminal.ok, true, terminal.error);
  assertSuccessShape(terminal, record.successShape);
  return { frames, terminal };
}

function liveDatabaseState() {
  return ['data/app.db', 'data/app.db-wal', 'data/app.db-shm'].map((relativePath) => {
    const absolutePath = path.join(REPO_ROOT, relativePath);
    if (!existsSync(absolutePath)) return { relativePath, exists: false };
    const stat = statSync(absolutePath);
    return {
      relativePath,
      exists: true,
      size: stat.size,
      birthtimeMs: stat.birthtimeMs,
      mtimeMs: stat.mtimeMs,
      mode: stat.mode,
      sha256: createHash('sha256').update(readFileSync(absolutePath)).digest('hex'),
    };
  });
}

test('legacy server process boots current server.js on an OS-assigned loopback port with a temporary database', async () => {
  const before = liveDatabaseState();
  const { startLegacyServer } = require('./support/legacy-server-process');
  const child = await startLegacyServer();
  try {
    assert.equal(child.address.address, '127.0.0.1');
    assert.ok(child.address.port > 0);
    assert.ok(child.databasePath.startsWith(child.tempRoot));
    const response = await child.request('/api/papers');
    assert.equal(response.status, 200);
    assert.match(response.headers.get('content-type'), /^application\/json/);
    assert.ok(Array.isArray(await response.json()));
    assert.equal(existsSync(child.databasePath), true);
    assert.deepEqual((await child.inspect()).liveDatabaseAccesses, []);
  } finally {
    await child.stop();
  }
  assert.equal(existsSync(child.tempRoot), false);
  assert.deepEqual(liveDatabaseState(), before);
});

test('a fresh local runtime serves the committed workspace build', async () => {
  const { startLegacyServer } = require('./support/legacy-server-process');
  const child = await startLegacyServer({ localRuntime: true });
  try {
    const papers = await child.request('/api/papers');
    assert.equal(papers.status, 200);
    assert.deepEqual(await papers.json(), []);

    const workspace = await child.request('/workspace/');
    assert.equal(workspace.status, 200);
    assert.match(await workspace.text(), /<title>Paper Study · 研究工作区<\/title>/u);
  } finally {
    await child.stop();
  }
});

test('legacy black-box freezes paper reads, writes, and reviews', async () => {
  const { startLegacyServer } = require('./support/legacy-server-process');
  const child = await startLegacyServer();
  try {
    assert.deepEqual(await requestJson(child, 'GET', '/api/papers'), []);
    const added = await requestJson(child, 'POST', '/api/paper/add', {
      title: 'Contract Paper',
      authors: ['Ada'],
      year: '2026',
      venue: 'CVPR',
    });
    const id = added.id;
    assert.equal(typeof id, 'string');

    const paper = await requestJson(child, 'GET', `/api/paper/get?id=${encodeURIComponent(id)}`);
    assert.equal(paper.id, id);
    assert.equal(await requestText(child, `/api/note?id=${encodeURIComponent(id)}`), '');
    await requestJson(child, 'POST', '/api/note', { id, content: '# note' });
    assert.equal(await requestText(child, `/api/note?id=${encodeURIComponent(id)}`), '# note');
    assert.equal(await requestText(child, `/api/explainer?id=${encodeURIComponent(id)}`), '*(暂无讲解)*');
    assert.equal(await requestText(child, `/api/translation?id=${encodeURIComponent(id)}`), '');

    const pdfDir = path.join(child.tempRoot, 'pdfs');
    mkdirSync(pdfDir, { recursive: true });
    await requestJson(child, 'POST', '/api/settings', { pdfDir });
    const pdfBytes = Buffer.from('%PDF-contract');
    writeFileSync(path.join(pdfDir, `${id}.pdf`), pdfBytes);
    const pdfResponse = await child.request(`/pdfbytes?id=${encodeURIComponent(id)}`);
    assert.equal(pdfResponse.status, 200);
    assert.equal(pdfResponse.headers.get('content-type'), endpoint('GET', '/pdfbytes').contentType);
    assert.deepEqual(Buffer.from(await pdfResponse.arrayBuffer()), pdfBytes);

    const reviews = await requestJson(child, 'GET', '/api/reviews');
    assert.equal(reviews.ok, true);
    const started = await requestJson(child, 'POST', '/api/reviews/start', { id });
    assert.equal(started.plan.paper_id, id);
    const completed = await requestJson(child, 'POST', '/api/reviews/complete', { id });
    assert.equal(completed.plan.paper_id, id);
    await requestJson(child, 'POST', '/api/progress', { id, status: '已理解' });
    await requestJson(child, 'POST', '/api/favorite', { id, favorite: true });
    const updated = await requestJson(child, 'POST', '/api/paper/update', { id, title_zh: '契约论文' });
    assert.equal(updated.changes, 1);
    const papers = await requestJson(child, 'GET', '/api/papers');
    assert.equal(papers[0].favorite, 1);
    await requestJson(child, 'POST', '/api/delete', { id });
  } finally {
    await child.stop();
  }
});

test('legacy black-box freezes acquisition JSON and NDJSON contracts', async () => {
  const { startLegacyServer } = require('./support/legacy-server-process');
  const child = await startLegacyServer();
  try {
    const expanded = await requestJson(child, 'POST', '/api/expand', { query: 'contracts', expandN: 6 });
    assert.deepEqual(expanded.queries, ['expanded query']);
    const ingested = await requestJson(child, 'POST', '/api/ingest', {
      query: 'contracts', sources: ['semanticscholar'], years: '2024-2026', max: 2,
    });
    assert.equal(ingested.code, 0);
    const searched = await requestNdjson(child, '/api/search', {
      query: 'contracts', sources: ['semanticscholar'], years: '2024-2026', max: 2,
    });
    assert.equal(searched.terminal.candidates[0].source_id, 'fixture-source');
    const candidate = searched.terminal.candidates[0];
    const verified = await requestNdjson(child, '/api/verify-venue', {
      candidates: [candidate], sources: ['dblp'],
    });
    assert.equal(verified.terminal.verifications[0].source_of_truth, 'dblp');
    const selected = await requestNdjson(child, '/api/ingest-selected', {
      candidates: [candidate], deep: false, downloadPdf: false,
    });
    assert.equal(selected.terminal.added, 1);
  } finally {
    await child.stop();
  }
});

test('legacy black-box freezes generated-content JSON and NDJSON contracts', async () => {
  const { startLegacyServer } = require('./support/legacy-server-process');
  const child = await startLegacyServer();
  try {
    const added = await requestJson(child, 'POST', '/api/paper/add', { title: 'Untranslated Fixture' });
    const id = added.id;
    const titleStatus = await requestJson(child, 'GET', '/api/title-translations');
    assert.equal(titleStatus.pending, 1);
    const titles = await requestNdjson(child, '/api/title-translations', { limit: 1 });
    assert.equal(titles.terminal.summary.done, 1);

    const explained = await requestNdjson(child, '/api/explain', { id, deep: false });
    assert.equal(explained.terminal.markdown, '# Fixture explainer\n');
    const pending = await requestJson(child, 'GET', '/api/explain-batch');
    assert.equal(pending.pending, 1);
    const batch = await requestNdjson(child, '/api/explain-batch', { limit: 1 });
    assert.equal(batch.terminal.summary.done, 1);
    const translated = await requestNdjson(child, '/api/translate', { id });
    assert.equal(translated.terminal.markdown, '# Fixture translation\n');
    const text = await requestJson(child, 'POST', '/api/translate-text', { text: 'contract text' });
    assert.equal(text.text, '测试中文题名');
  } finally {
    await child.stop();
  }
});

test('legacy black-box freezes PDF and insight JSON and NDJSON contracts', async () => {
  const { startLegacyServer } = require('./support/legacy-server-process');
  const child = await startLegacyServer();
  try {
    const localPdf = path.join(child.tempRoot, 'incoming.pdf');
    writeFileSync(localPdf, '%PDF-local', 'utf8');
    const scanned = await requestJson(
      child,
      'GET',
      `/api/scan-pdfs?dir=${encodeURIComponent(child.tempRoot)}`,
    );
    assert.equal(scanned.count, 1);
    const imported = await requestNdjson(child, '/api/import-pdfs', { paths: [localPdf], enrich: false });
    assert.equal(imported.terminal.added, 1);
    const downloaded = await requestNdjson(child, '/api/download-pdfs', { ids: [], limit: 1 });
    assert.equal(downloaded.terminal.downloaded, 1);

    const added = await requestJson(child, 'POST', '/api/paper/add', { title: 'PDF Fixture' });
    writeFileSync(path.join(child.pdfDir, `${added.id}.pdf`), '%PDF-library', 'utf8');
    const status = await requestJson(child, 'GET', `/api/pdf/status?id=${encodeURIComponent(added.id)}`);
    assert.equal(status.hasPdf, true);
    const recommended = await requestNdjson(child, '/api/recommend', { id: added.id, limit: 14 });
    assert.equal(recommended.terminal.candidates.length, 1);
    const embedded = await requestNdjson(child, '/api/embed', { scope: 'missing' });
    assert.equal(embedded.terminal.indexed, 1);
    const semantic = await requestNdjson(child, '/api/semsearch', { query: 'fixture', k: 60 });
    assert.equal(semantic.terminal.results[0].id, 'fixture-paper');
    const graph = await requestJson(child, 'GET', '/api/citegraph');
    assert.deepEqual(graph.links, []);
    const normalized = await requestNdjson(child, '/api/norm-venues', {});
    assert.equal(normalized.terminal.mapping.cvpr, 'CVPR');
    const citationBuild = await requestNdjson(child, '/api/cite-build', {});
    assert.equal(citationBuild.terminal.edges, 1);
  } finally {
    await child.stop();
  }
});

test('legacy black-box freezes settings, jobs, and schedules contracts', async () => {
  const { startLegacyServer } = require('./support/legacy-server-process');
  const child = await startLegacyServer();
  try {
    const settings = await requestJson(child, 'GET', '/api/settings');
    assert.equal(settings.model, 'fixture-model');
    await requestJson(child, 'POST', '/api/settings', { researchTheme: 'contract testing' });
    const llm = await requestJson(child, 'POST', '/api/test-llm', {});
    assert.equal(llm.output, 'pong\n');

    assert.deepEqual(await requestJson(child, 'GET', '/api/jobs'), []);
    const createdJob = await requestJson(child, 'POST', '/api/jobs', {
      query: 'contracts', sources: ['semanticscholar'], years: '2024-2026', max: 2,
      minRelevance: 0.5, onlyA: false,
    });
    const job = await requestJson(child, 'GET', `/api/jobs/detail?id=${createdJob.id}`);
    assert.equal(job.job.id, createdJob.id);
    const candidate = {
      source: 'semanticscholar', source_id: 'fixture-source', title: 'Fixture Candidate', authors: ['Ada'],
      venue: 'CVPR', year: '2026', abstract: null, tldr: null, fields: [], citations: 0,
      url: null, pdf_url: null, arxiv_id: null, doi: null, s2_id: 'fixture-s2', ccf: 'A',
      type: null, topic: null, task: null, models: [], datasets: [], contribution: null,
      llm_tldr: null, tags: [], relevance: 0.9, in_library: false, _cid: null,
    };
    const confirmed = await requestNdjson(child, '/api/jobs/confirm', {
      jobId: createdJob.id, candidates: [candidate], deep: false, downloadPdf: false,
    });
    assert.equal(confirmed.terminal.added, 1);
    const jobs = await requestJson(child, 'GET', '/api/jobs');
    assert.equal(jobs[0].id, createdJob.id);
    await requestJson(child, 'POST', '/api/jobs/delete', { id: createdJob.id });

    assert.deepEqual(await requestJson(child, 'GET', '/api/schedules'), []);
    const schedule = await requestJson(child, 'POST', '/api/schedules', {
      query: 'contracts', sources: ['semanticscholar'], years: '2024-2026', max: 2,
      minRelevance: 0.5, onlyA: false, everyDays: 7,
    });
    await requestJson(child, 'POST', '/api/schedules/toggle', { id: schedule.id, enabled: false });
    const schedules = await requestJson(child, 'GET', '/api/schedules');
    assert.equal(schedules[0].enabled, 0);
    await requestJson(child, 'POST', '/api/schedules/delete', { id: schedule.id });
  } finally {
    await child.stop();
  }
});

test('legacy black-box freezes every documented error shape and cancellation terminal behavior', async () => {
  const { startLegacyServer } = require('./support/legacy-server-process');
  const child = await startLegacyServer({
    agentErrorCommands: ['expand', 'ping'],
    titleTranslationDelayMs: 5_000,
  });
  try {
    const invalidCases = [
      ['GET', '/pdfbytes?id=missing-contract-paper', undefined],
      ['POST', '/api/reviews/start', {}],
      ['POST', '/api/reviews/complete', {}],
      ['POST', '/api/paper/add', {}],
      ['POST', '/api/paper/update', {}],
      ['POST', '/api/ingest', {}],
      ['POST', '/api/search', {}],
      ['POST', '/api/explain', {}],
      ['POST', '/api/translate', {}],
      ['POST', '/api/translate-text', {}],
      ['GET', '/api/scan-pdfs', undefined],
      ['POST', '/api/import-pdfs', {}],
      ['POST', '/api/recommend', {}],
      ['POST', '/api/semsearch', {}],
      ['POST', '/api/jobs', {}],
      ['GET', '/api/jobs/detail?id=999999', undefined],
      ['POST', '/api/jobs/confirm', {}],
      ['POST', '/api/schedules', {}],
    ];
    for (const [method, endpointPath, body] of invalidCases) {
      await requestDocumentedError(child, method, endpointPath, body);
    }

    await requestDocumentedError(child, 'POST', '/api/expand', { query: 'force error', expandN: 6 });
    await requestDocumentedError(child, 'POST', '/api/test-llm', {});

    await requestJson(child, 'POST', '/api/paper/add', { title: 'Pending translation' });
    const cancellation = new AbortController();
    const activeResponse = await child.request('/api/title-translations', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ limit: 1 }),
      signal: cancellation.signal,
    });
    const reader = activeResponse.body.getReader();
    const firstFrame = await reader.read();
    assert.equal(firstFrame.done, false);
    const firstPayload = Buffer.from(firstFrame.value).toString('utf8');
    assert.match(firstPayload, /"type":"progress"/);
    assert.doesNotMatch(firstPayload, /"type":"result"/);

    await requestDocumentedError(child, 'POST', '/api/title-translations', { limit: 1 });
    cancellation.abort();
    try { await reader.cancel(); } catch (_) {}

    const deadline = Date.now() + 2_000;
    let running = true;
    while (running && Date.now() < deadline) {
      const status = await requestJson(child, 'GET', '/api/title-translations');
      running = status.running;
      if (running) await new Promise((resolve) => setTimeout(resolve, 20));
    }
    assert.equal(running, false, 'cancelled title translation remained active');
  } finally {
    await child.stop();
  }
});

test('legacy route ledger and executed black-box case inventory are exhaustive', () => {
  const ledgerPairs = LEGACY_LEDGER.endpoints
    .map((record) => `${record.method} ${record.path}`)
    .sort();
  assert.deepEqual([...OBSERVED_BLACK_BOX_CASES].sort(), ledgerPairs);

  const serverSource = readFileSync(path.join(REPO_ROOT, 'server.js'), 'utf8');
  const routePairs = new Set();
  for (const match of serverSource.matchAll(
    /if\s*\(p\s*===\s*'([^']+)'\s*&&\s*req\.method\s*===\s*'(GET|POST)'\)/g,
  )) {
    if (match[1].startsWith('/api/')) routePairs.add(`${match[2]} ${match[1]}`);
  }
  for (const unguardedGet of ['/api/papers', '/pdfbytes']) {
    assert.match(serverSource, new RegExp(`if\\s*\\(p\\s*===\\s*'${unguardedGet.replace('/', '\\/')}'\\)`));
    routePairs.add(`GET ${unguardedGet}`);
  }
  assert.deepEqual([...routePairs].sort(), ledgerPairs);
});
