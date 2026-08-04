const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');

const publicDir = path.resolve(__dirname, '..', 'public');
const html = fs.readFileSync(path.join(publicDir, 'index.html'), 'utf8');
const style = fs.readFileSync(path.join(publicDir, 'style.css'), 'utf8');
const app = fs.readFileSync(path.join(publicDir, 'app.js'), 'utf8');

function appFunction(name, nextName) {
  const functionStart = (functionName) => {
    const plainStart = app.indexOf(`function ${functionName}(`);
    if (plainStart < 6 || app.slice(plainStart - 6, plainStart) !== 'async ') return plainStart;
    return plainStart - 6;
  };
  const start = functionStart(name);
  const end = functionStart(nextName);
  assert.notEqual(start, -1, `${name} must exist`);
  assert.notEqual(end, -1, `${nextName} must follow ${name}`);
  return app.slice(start, end);
}

function chartAnimationFunctions() {
  return [
    appFunction('prefersReducedMotion', 'chartAnimationDuration'),
    appFunction('chartAnimationDuration', 'clearHomeFilters'),
  ].join('\n');
}

function createChartAnimationHelper(windowValue) {
  return Function(
    'window',
    `${chartAnimationFunctions()}; return { prefersReducedMotion, chartAnimationDuration };`,
  )(windowValue);
}

function renderInsightChartOptions(reducedMotion) {
  const captured = {};
  const chart = name => ({
    clear() {},
    off() {},
    on() {},
    setOption(option) { captured[name] = option; },
  });
  const source = [
    chartAnimationFunctions(),
    appFunction('renderTree', 'renderTrend'),
    appFunction('renderTrend', 'renderCited'),
    appFunction('renderCited', 'renderCite'),
  ].join('\n');
  const run = Function(
    'window', 'PAPERS', 'topGroups', 'cssVar', 'echarts', 'chTree', 'chTrend', 'chCited',
    `${source};
      renderTree();
      renderTrend();
      renderCited({ nodes: [{ id: 'p1', title: 'Paper 1', indeg: 1 }] });`,
  );
  run(
    { matchMedia: () => ({ matches: reducedMotion }) },
    [{ id: 'p1', title: 'Paper 1', type: 'Vision', topic: 'Detection', year: '2026' }],
    () => [{ name: 'Vision', value: 1, color: '#123456' }],
    () => '#123456',
    { graphic: { LinearGradient: function LinearGradient() {} } },
    chart('tree'),
    chart('trend'),
    chart('cited'),
  );
  return captured;
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

function reviewDataFixture(marker, counts = {}) {
  return { marker, counts, overdue: [], dueToday: [], upcoming: [], completed: [] };
}

function createReviewMutationHarness() {
  const source = [
    appFunction('blankReviewData', 'reviewItems'),
    appFunction('completeReview', 'buildInsightsShell'),
    appFunction('saveStatus', 'refreshTitleTranslationBatch'),
  ].join('\n');
  const reviewRequests = [];
  const renders = { list: 0, current: 0, details: 0 };
  let completeResponse = { ok: true };
  const fetch = (url) => {
    if (url === '/api/reviews') {
      return new Promise((resolve, reject) => reviewRequests.push({ resolve, reject }));
    }
    if (url === '/api/progress') return Promise.resolve({ ok: true });
    if (url === '/api/reviews/complete') return Promise.resolve(reviewResponse(completeResponse));
    throw new Error(`Unexpected fetch: ${url}`);
  };
  const factory = Function(
    'fetch',
    'spatialWorkspace',
    'renderReviews',
    'renderCurrentReviewStatus',
    'alert',
    'setStatusUI',
    'renderSidebar',
    `let reviewData = null;
    let reviewLoadPromise = null;
    let reviewLoadVersion = 0;
    let current = { id: 'p1', status: '未开始' };
    let PAPERS = [{ id: 'p1', status: '未开始' }];
    let currentView = 'review';
    ${source}; return {
      loadReviews,
      completeReview,
      saveStatus,
      getReviewData: () => reviewData,
    };`,
  );
  const harness = factory(
    fetch,
    { refreshDetails() { renders.details++; } },
    () => { renders.list++; },
    () => { renders.current++; },
    (message) => { throw new Error(message); },
    () => {},
    () => {},
  );
  return {
    ...harness,
    reviewRequests,
    renders,
    setCompleteResponse(value) { completeResponse = value; },
  };
}

function createSettingsHarness(initialView = 'home', scrollTop = 240) {
  const start = app.indexOf('let settingsReturnFocus');
  assert.notEqual(start, -1, 'Settings modal state must exist');
  const source = app.slice(start);
  const hiddenClasses = new Set(['hidden']);
  const modal = {
    classList: {
      add(value) { hiddenClasses.add(value); },
      remove(value) { hiddenClasses.delete(value); },
      contains(value) { return hiddenClasses.has(value); },
    },
  };
  const returnFocus = { focusCalls: 0, focus() { this.focusCalls += 1; } };
  const closeButton = { focusCalls: 0, focus() { this.focusCalls += 1; } };
  const elements = {
    settingsModal: modal,
    setClose: closeButton,
    home: { scrollTop },
    insights: { scrollTop },
    pdfScroll: { scrollTop },
  };
  const queries = [];
  const panelCloses = [];
  let settingsLoads = 0;
  const factory = Function(
    'document',
    '$',
    'spatialWorkspace',
    'loadSettings',
    'setTimeout',
    'initialView',
    `let currentView = initialView;
    ${source}
    return {
      openSettingsModal,
      closeSettingsModal,
      getCapturedState: () => ({ settingsReturnFocus, settingsScrollContainer, settingsScrollTop }),
    };`,
  );
  const api = factory(
    { activeElement: returnFocus },
    selector => {
      queries.push(selector);
      return elements[selector.slice(1)] || null;
    },
    { closePanels(options) { panelCloses.push(options); } },
    () => { settingsLoads += 1; },
    callback => { callback(); },
    initialView,
  );
  return {
    ...api,
    closeButton,
    elements,
    panelCloses,
    queries,
    returnFocus,
    settingsLoads: () => settingsLoads,
  };
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

test('status mutation forces a post-mutation review load and rejects the older response', async () => {
  const harness = createReviewMutationHarness();
  const oldLoad = harness.loadReviews(false);
  const saving = harness.saveStatus('已理解');
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(harness.reviewRequests.length, 2);

  const newData = reviewDataFixture('after-status');
  const oldData = reviewDataFixture('before-status');
  harness.reviewRequests[1].resolve(reviewResponse(newData));
  await saving;
  harness.reviewRequests[0].resolve(reviewResponse(oldData));
  assert.equal(await oldLoad, newData);
  assert.equal(harness.getReviewData(), newData);
  assert.deepEqual(harness.renders, { list: 1, current: 1, details: 1 });
});

test('completeReview invalidates an older load before committing mutation review data', async () => {
  const harness = createReviewMutationHarness();
  const oldLoad = harness.loadReviews(false);
  const newData = reviewDataFixture('completed-review');
  harness.setCompleteResponse({ ok: true, reviews: newData });
  await harness.completeReview('p1');

  const oldData = reviewDataFixture('before-completion');
  harness.reviewRequests[0].resolve(reviewResponse(oldData));
  assert.equal(await oldLoad, newData);
  assert.equal(harness.getReviewData(), newData);
  assert.deepEqual(harness.renders, { list: 1, current: 1, details: 1 });
});

test('completeReview without review data uses a forced versioned load', async () => {
  const harness = createReviewMutationHarness();
  const oldLoad = harness.loadReviews(false);
  harness.setCompleteResponse({ ok: true });
  const completing = harness.completeReview('p1');
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(harness.reviewRequests.length, 2);

  const newData = reviewDataFixture('forced-after-completion');
  const oldData = reviewDataFixture('before-completion');
  harness.reviewRequests[1].resolve(reviewResponse(newData));
  await completing;
  harness.reviewRequests[0].resolve(reviewResponse(oldData));
  assert.equal(await oldLoad, newData);
  assert.equal(harness.getReviewData(), newData);
  assert.deepEqual(harness.renders, { list: 1, current: 1, details: 1 });
});

test('reduced-motion preference controls ECharts animation as well as CSS', () => {
  const mediaQueries = [];
  const normal = createChartAnimationHelper({
    matchMedia(query) {
      mediaQueries.push(query);
      return { matches: false };
    },
  });
  const reduced = createChartAnimationHelper({ matchMedia: () => ({ matches: true }) });
  const unavailable = createChartAnimationHelper({});
  assert.equal(normal.prefersReducedMotion(), false);
  assert.equal(normal.chartAnimationDuration(700), 700);
  assert.deepEqual(mediaQueries, [
    '(prefers-reduced-motion: reduce)',
    '(prefers-reduced-motion: reduce)',
  ]);
  assert.equal(reduced.prefersReducedMotion(), true);
  assert.equal(reduced.chartAnimationDuration(700), 0);
  assert.equal(unavailable.prefersReducedMotion(), false);
  assert.equal(unavailable.chartAnimationDuration(700), 700);
  assert.doesNotMatch(app, /animationDuration(?:Update)?:\s*(?:600|700|750)\b/);

  const normalOptions = renderInsightChartOptions(false);
  assert.deepEqual(
    [normalOptions.tree.animationDuration, normalOptions.trend.animationDuration, normalOptions.cited.animationDuration],
    [600, 700, 600],
  );
  assert.deepEqual(
    [normalOptions.tree.animationDurationUpdate, normalOptions.trend.animationDurationUpdate, normalOptions.cited.animationDurationUpdate],
    [600, 700, 600],
  );

  const reducedOptions = renderInsightChartOptions(true);
  assert.deepEqual(
    [reducedOptions.tree.animationDuration, reducedOptions.trend.animationDuration, reducedOptions.cited.animationDuration],
    [0, 0, 0],
  );
  assert.deepEqual(
    [reducedOptions.tree.animationDurationUpdate, reducedOptions.trend.animationDurationUpdate, reducedOptions.cited.animationDurationUpdate],
    [0, 0, 0],
  );
});

test('Settings close restores captured home scroll and focus exactly once', () => {
  const harness = createSettingsHarness('home', 240);
  harness.openSettingsModal();
  assert.deepEqual(harness.panelCloses, [{ restoreFocus: false }]);
  assert.equal(harness.settingsLoads(), 1);
  assert.equal(harness.elements.settingsModal.classList.contains('hidden'), false);
  assert.equal(harness.closeButton.focusCalls, 1);
  assert.equal(harness.getCapturedState().settingsReturnFocus, harness.returnFocus);
  assert.equal(harness.getCapturedState().settingsScrollContainer, harness.elements.home);
  assert.equal(harness.getCapturedState().settingsScrollTop, 240);

  harness.elements.home.scrollTop = 12;
  harness.closeSettingsModal();
  assert.equal(harness.elements.settingsModal.classList.contains('hidden'), true);
  assert.equal(harness.elements.home.scrollTop, 240);
  assert.equal(harness.returnFocus.focusCalls, 1);
  assert.equal(harness.getCapturedState().settingsReturnFocus, null);
  assert.equal(harness.getCapturedState().settingsScrollContainer, null);

  harness.elements.home.scrollTop = 88;
  harness.closeSettingsModal();
  assert.equal(harness.elements.home.scrollTop, 88);
  assert.equal(harness.returnFocus.focusCalls, 1);
});

test('Settings captures pdfScroll for reading and the named container for other views', () => {
  const reading = createSettingsHarness('read', 130);
  reading.openSettingsModal();
  assert.equal(reading.getCapturedState().settingsScrollContainer, reading.elements.pdfScroll);
  assert.equal(reading.queries.includes('#pdfScroll'), true);
  assert.equal(reading.queries.includes('#read'), false);

  const insights = createSettingsHarness('insights', 150);
  insights.openSettingsModal();
  assert.equal(insights.getCapturedState().settingsScrollContainer, insights.elements.insights);
  assert.equal(insights.queries.includes('#insights'), true);
});

test('leaving Home closes transient spatial panels without changing navigation state', () => {
  const showView = app.slice(app.indexOf('function showView'), app.indexOf('function fmtTime'));
  assert.match(showView, /v\s*!==\s*['"]home['"][^\n]*spatialWorkspace\?\.closePanels/);
});

test('leaving spatial appearance clears transient panels without changing view', () => {
  const handler = app.slice(app.indexOf('function handleAppearanceChange'), app.indexOf("document.addEventListener('paperstudy:appearancechange'"));
  assert.match(handler, /event\.detail\.uiStyle\s*!==\s*['"]spatial['"][^\n]*spatialWorkspace\?\.closePanels/);
  assert.doesNotMatch(handler, /showView\(/);
});
