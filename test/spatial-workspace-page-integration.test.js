const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');

const publicDir = path.resolve(__dirname, '..', 'public');
const html = fs.readFileSync(path.join(publicDir, 'index.html'), 'utf8');
const style = fs.readFileSync(path.join(publicDir, 'style.css'), 'utf8');
const app = fs.readFileSync(path.join(publicDir, 'app.js'), 'utf8');

function appFunction(name, nextName) {
  const start = app.indexOf(`function ${name}`);
  const end = app.indexOf(`function ${nextName}`, start);
  assert.notEqual(start, -1, `${name} must exist`);
  assert.notEqual(end, -1, `${nextName} must follow ${name}`);
  return app.slice(start, end);
}

function createReviewHarness() {
  const source = appFunction('blankReviewData', 'reviewItems');
  const requests = [];
  const renders = { list: 0, current: 0, details: 0 };
  const factory = Function(
    'fetch',
    'spatialWorkspace',
    'renderReviews',
    'renderCurrentReviewStatus',
    `let reviewData = null;
    let reviewLoadPromise = null;
    let reviewLoadVersion = 0;
    ${source}; return {
      loadReviews,
      getReviewData: () => reviewData,
    };`,
  );
  const harness = factory(
    () => new Promise((resolve, reject) => requests.push({ resolve, reject })),
    { refreshDetails() { renders.details++; } },
    () => { renders.list++; },
    () => { renders.current++; },
  );
  return { ...harness, requests, renders };
}

function reviewResponse(data) {
  return { json: async () => data };
}

test('classic mode hides one semantic spatial overview while preserving homeTable', () => {
  assert.equal((html.match(/id="spatialOverview"/g) || []).length, 1);
  assert.equal((html.match(/id="homeTable"/g) || []).length, 1);
  assert.match(style, /\.spatial-only\s*\{\s*display:\s*none\s*;?\s*\}/);
});

test('overview exposes queue, layers, inspector and explicit paths', () => {
  for (const id of [
    'spatialQueue', 'spatialLayers', 'spatialEmpty', 'spatialInspector',
    'spatialPrev', 'spatialNext', 'spatialOpen', 'spatialClearFilters',
    'spatialContext', 'spatialDirections', 'spatialQueueClose', 'spatialInspectorClose',
    'spatialFilterSlot', 'homeFilterActions',
  ]) assert.match(html, new RegExp(`id="${id}"`));
  assert.match(html, /href="#homeTable"[^>]*>查看完整数据</);
});

test('the single real home filter group stays intact beside the direct search child', () => {
  for (const id of ['homeFilterActions', 'yearFilters', 'semToggle', 'favFilter']) {
    assert.equal((html.match(new RegExp(`id="${id}"`, 'g')) || []).length, 1, `${id} must stay unique`);
  }
  assert.match(
    html,
    /<div\b(?=[^>]*\bid="topFilters")(?=[^>]*\bclass="filters")[^>]*>\s*<div\b[^>]*class="search-box"/,
  );
  assert.match(
    html,
    /<div\b[^>]*id="homeFilterActions"[^>]*>\s*<div\b[^>]*id="yearFilters"[^>]*><\/div>\s*<button\b[^>]*id="semToggle"[^>]*>[\s\S]*?<\/button>\s*<button\b[^>]*id="favFilter"[^>]*>[\s\S]*?<\/button>\s*<\/div>/,
  );
});

test('spatial-workspace loads once before the protected markdown script trio', () => {
  const sources = [...html.matchAll(/<script\b[^>]*src="([^"]+)"[^>]*>/g)].map(match => path.basename(match[1]));
  assert.equal(sources.filter(source => source === 'spatial-workspace.js').length, 1);
  const spatialIndex = sources.indexOf('spatial-workspace.js');
  assert.deepEqual(sources.slice(spatialIndex + 1, spatialIndex + 4), [
    'ingest-rendering.js',
    'markdown-rendering-coordinator.js',
    'app.js',
  ]);
});

test('renderHome gives the same filtered sorted list to table and spatial controller', () => {
  const body = app.slice(app.indexOf('function renderHome()'), app.indexOf('function updateCharts'));
  assert.match(body, /spatialWorkspace\.update\(list,/);
  assert.match(body, /emptyMessage:\s*emptyMsg/);
});

test('spatial opening reuses openPaper and never duplicates the reading workflow', () => {
  const builder = app.slice(app.indexOf('function buildSpatialWorkspace'), app.indexOf('function renderHome'));
  assert.match(builder, /onOpen:\s*openPaper/);
  assert.doesNotMatch(builder, /fetch\(|showView\(|renderPdf\(/);
});

test('clearing spatial filters resets only home filtering controls', () => {
  const clear = app.slice(app.indexOf('function clearHomeFilters'), app.indexOf('function buildSpatialWorkspace'));
  for (const assignment of [
    /yearFilter\s*=\s*['"]all['"]/,
    /favOnly\s*=\s*false/,
    /semActive\s*=\s*false/,
    /semRank\s*=\s*null/,
    /q\s*=\s*['"]/,
  ]) assert.match(clear, assignment);
  assert.doesNotMatch(clear, /homeSort\s*=/);
});

test('opening and review refresh keep the spatial inspector current', () => {
  assert.match(app, /spatialWorkspace\?\.select\(p\.id\)/);
  assert.match(app, /spatialWorkspace\?\.refreshDetails\(\)/);
});

test('the controller receives the real home scroll container', () => {
  const builder = app.slice(app.indexOf('function buildSpatialWorkspace'), app.indexOf('function renderHome'));
  assert.match(builder, /scrollContainer:\s*\$\(['"]#home['"]\)/);
});

test('concurrent ordinary review loads share one request and preserve every render-list request', async () => {
  const harness = createReviewHarness();
  const quietLoad = harness.loadReviews(false);
  const visibleLoad = harness.loadReviews(true);
  assert.equal(harness.requests.length, 1);

  const data = { counts: {}, overdue: [], dueToday: [], upcoming: [], completed: [] };
  harness.requests[0].resolve(reviewResponse(data));
  assert.equal(await quietLoad, data);
  assert.equal(await visibleLoad, data);
  assert.deepEqual(harness.renders, { list: 1, current: 1, details: 1 });
});

test('a stale forced review refresh cannot overwrite a newer forced refresh', async () => {
  const harness = createReviewHarness();
  const older = harness.loadReviews(false, true);
  const newer = harness.loadReviews(false, true);
  assert.equal(harness.requests.length, 2);

  const newData = { counts: { dueToday: 2 }, overdue: [], dueToday: [], upcoming: [], completed: [] };
  const oldData = { counts: { overdue: 1 }, overdue: [], dueToday: [], upcoming: [], completed: [] };
  harness.requests[1].resolve(reviewResponse(newData));
  await newer;
  harness.requests[0].resolve(reviewResponse(oldData));
  await older;
  assert.equal(harness.getReviewData(), newData);
});

test('spatial review details distinguish load errors from no scheduled review', () => {
  const source = appFunction('spatialPaperDetails', 'buildSpatialWorkspace');
  const factory = Function(
    'currentReviewItem',
    'dueText',
    `let reviewData = null;
    ${source}; return (paper, data) => { reviewData = data; return spatialPaperDetails(paper); };`,
  );
  const details = factory(() => null, () => '');
  const unavailable = details({ id: 'p1', hasNote: true }, { error: 'network down' });
  assert.equal(unavailable.reviewText, '复习信息暂不可用');
  assert.equal(unavailable.noteText, '已有笔记');
  const unscheduled = details({ id: 'p1', hasNote: false }, { error: '' });
  assert.equal(unscheduled.reviewText, '尚未安排');
  assert.equal(unscheduled.noteText, '暂无笔记');
});
