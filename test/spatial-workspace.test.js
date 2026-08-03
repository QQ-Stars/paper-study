const assert = require('node:assert/strict');
const path = require('node:path');
const test = require('node:test');

const workspacePath = path.resolve(__dirname, '..', 'public', 'spatial-workspace.js');
const papers = Array.from({ length: 8 }, (_, index) => ({
  id: `p${index + 1}`,
  title: `Paper ${index + 1}`,
  venue: 'CVPR',
  year: '2026',
  status: index % 2 ? '学习中' : '未开始',
}));
const stringNullishPapers = [
  { id: 'first' },
  { id: 'null' },
  { id: 'undefined' },
];

class FakeClassList {
  constructor() { this.values = new Set(); }
  add(...values) { values.forEach(value => this.values.add(value)); }
  remove(...values) { values.forEach(value => this.values.delete(value)); }
  contains(value) { return this.values.has(value); }
  toggle(value, force) {
    const next = force === undefined ? !this.contains(value) : Boolean(force);
    if (next) this.add(value); else this.remove(value);
    return next;
  }
}

class FakeElement {
  constructor(tagName = 'div', id = '') {
    this.tagName = tagName.toUpperCase();
    this.id = id;
    this.children = [];
    this.parentNode = null;
    this.dataset = {};
    this.attributes = {};
    this.className = '';
    this.classList = new FakeClassList();
    this.listeners = new Map();
    this.textContent = '';
    this.hidden = false;
    this.disabled = false;
    this.checked = false;
    this.tabIndex = -1;
    this.focusCalls = 0;
    this.ownerDocument = null;
    this.scrollTop = 0;
    this.style = { values: {}, setProperty: (name, value) => { this.style.values[name] = String(value); } };
  }
  append(...nodes) {
    for (const node of nodes) {
      if (node.parentNode) {
        const index = node.parentNode.children.indexOf(node);
        if (index >= 0) node.parentNode.children.splice(index, 1);
      }
      node.parentNode = this;
      this.children.push(node);
    }
  }
  replaceChildren(...nodes) {
    this.children.forEach(node => { node.parentNode = null; });
    this.children = [];
    this.append(...nodes);
  }
  setAttribute(name, value) { this.attributes[name] = String(value); }
  getAttribute(name) { return this.attributes[name]; }
  addEventListener(type, listener) {
    const listeners = this.listeners.get(type) || [];
    listeners.push(listener);
    this.listeners.set(type, listeners);
  }
  listenerCount(type) { return (this.listeners.get(type) || []).length; }
  dispatch(type, init = {}) {
    const event = {
      type,
      target: init.target || this,
      key: init.key,
      defaultPrevented: false,
      preventDefault() { this.defaultPrevented = true; },
    };
    for (const listener of this.listeners.get(type) || []) listener(event);
    return event;
  }
  click() { return this.dispatch('click'); }
  focus() {
    this.focusCalls += 1;
    if (this.ownerDocument) this.ownerDocument.activeElement = this;
  }
  contains(node) {
    for (let cursor = node; cursor; cursor = cursor.parentNode) if (cursor === this) return true;
    return false;
  }
  closest(selector) {
    if (selector === '[data-spatial-paper-id]' && this.dataset.spatialPaperId != null) return this;
    return this.parentNode && typeof this.parentNode.closest === 'function' ? this.parentNode.closest(selector) : null;
  }
  querySelector(selector) {
    if (selector === 'button, a, input, select, textarea, [tabindex="0"]') {
      return this.children.find(child => ['BUTTON', 'A', 'INPUT', 'SELECT', 'TEXTAREA'].includes(child.tagName) || child.tabIndex === 0) || null;
    }
    return null;
  }
}

class FakeMediaQuery {
  constructor(matches = false) { this.matches = matches; this.listeners = []; }
  addEventListener(type, listener) { if (type === 'change') this.listeners.push(listener); }
  dispatch(matches) {
    this.matches = matches;
    this.listeners.forEach(listener => listener({ matches }));
  }
}

function createWorkspaceHarness(options = {}) {
  const ids = [
    'spatialOverview', 'spatialLayers', 'spatialCount', 'spatialPosition',
    'spatialQueueTotal', 'spatialQueueLearning', 'spatialQueueDone', 'spatialDirections',
    'spatialContext', 'spatialEmpty', 'spatialEmptyText', 'spatialClearFilters',
    'spatialInspector', 'spatialInspectorTitle', 'spatialInspectorTitleZh',
    'spatialInspectorMeta', 'spatialInspectorStatus', 'spatialInspectorReview',
    'spatialInspectorNote', 'spatialInspectorSummary', 'spatialPrev', 'spatialNext',
    'spatialOpen', 'spatialQueueToggle', 'spatialInspectorToggle', 'spatialQueueClose',
    'spatialInspectorClose', 'spatialQueue', 'spatialScrim', 'spatialFilterSlot',
    'topFilters', 'homeFilterActions',
  ];
  const elements = new Map(ids.map(id => [id, new FakeElement('div', id)]));
  for (const id of [
    'spatialClearFilters', 'spatialPrev', 'spatialNext', 'spatialOpen',
    'spatialQueueToggle', 'spatialInspectorToggle', 'spatialQueueClose',
    'spatialInspectorClose', 'spatialScrim',
  ]) elements.get(id).tagName = 'BUTTON';
  const root = elements.get('spatialOverview');
  const queue = elements.get('spatialQueue');
  const inspector = elements.get('spatialInspector');
  const topFilters = elements.get('topFilters');
  const nestedIds = new Set([
    'spatialQueueClose', 'spatialInspectorClose', 'spatialOpen', 'spatialFilterSlot',
    'topFilters', 'homeFilterActions',
  ]);
  for (const [id, element] of elements) {
    if (id !== 'spatialOverview' && !nestedIds.has(id)) root.append(element);
  }
  queue.append(elements.get('spatialQueueClose'), elements.get('spatialFilterSlot'));
  inspector.append(elements.get('spatialInspectorClose'), elements.get('spatialOpen'));
  topFilters.append(elements.get('homeFilterActions'));
  const documentElement = new FakeElement('html', 'documentElement');
  const document = {
    documentElement,
    activeElement: null,
    listeners: new Map(),
    createElement(tagName) {
      const element = new FakeElement(tagName);
      element.ownerDocument = this;
      return element;
    },
    getElementById(id) { return elements.get(id) || null; },
    addEventListener(type, listener) {
      const listeners = this.listeners.get(type) || [];
      listeners.push(listener);
      this.listeners.set(type, listeners);
    },
    dispatch(type, init = {}) {
      const event = { type, key: init.key, target: init.target || this };
      for (const listener of this.listeners.get(type) || []) listener(event);
      return event;
    },
  };
  documentElement.ownerDocument = document;
  for (const element of elements.values()) element.ownerDocument = document;
  const desktopMedia = new FakeMediaQuery();
  const mobileMedia = new FakeMediaQuery(true);
  const scrollContainer = new FakeElement('section', 'home');
  scrollContainer.ownerDocument = document;
  scrollContainer.scrollTop = Number(options.scrollTop) || 0;
  const initialScrollTop = scrollContainer.scrollTop;
  let details = options.getDetails || (paper => ({
    reviewText: '尚未安排',
    noteText: paper.hasNote ? '已有笔记' : '暂无笔记',
  }));
  const harness = {
    root,
    document,
    desktopMedia,
    mobileMedia,
    scrollContainer,
    initialScrollTop,
    layers: elements.get('spatialLayers'),
    count: elements.get('spatialCount'),
    position: elements.get('spatialPosition'),
    empty: elements.get('spatialEmpty'),
    emptyText: elements.get('spatialEmptyText'),
    clearButton: elements.get('spatialClearFilters'),
    inspector: elements.get('spatialInspector'),
    inspectorTitle: elements.get('spatialInspectorTitle'),
    inspectorReview: elements.get('spatialInspectorReview'),
    previous: elements.get('spatialPrev'),
    next: elements.get('spatialNext'),
    queueToggle: elements.get('spatialQueueToggle'),
    inspectorToggle: elements.get('spatialInspectorToggle'),
    queueClose: elements.get('spatialQueueClose'),
    inspectorClose: elements.get('spatialInspectorClose'),
    scrim: elements.get('spatialScrim'),
    filterActions: elements.get('homeFilterActions'),
    filterHome: elements.get('topFilters'),
    filterSlot: elements.get('spatialFilterSlot'),
    options: {
      root,
      document,
      scrollContainer,
      desktopMedia,
      mobileMedia,
      onOpen: options.onOpen,
      onClearFilters: options.onClearFilters,
      getDetails: paper => details(paper),
    },
    setDetails(next) { details = next; },
    dispatchPaper(type, id) {
      const target = this.layers.children.find(node => String(node.dataset.spatialPaperId) === String(id));
      assert.ok(target, `missing rendered paper ${id}`);
      return root.dispatch(type, { target });
    },
    dispatchPaperKey(id, key) {
      const target = this.layers.children.find(node => String(node.dataset.spatialPaperId) === String(id));
      assert.ok(target, `missing rendered paper ${id}`);
      return root.dispatch('keydown', { target, key });
    },
    dispatchRootKey(key) { return root.dispatch('keydown', { target: root, key }); },
    dispatchDocumentKey(key) { return document.dispatch('keydown', { target: document, key }); },
  };
  return harness;
}

test('empty results have no selection and no fake layers', () => {
  const { createWorkspaceState } = require(workspacePath);
  const state = createWorkspaceState([], 'missing');
  assert.deepEqual(state.visiblePapers, []);
  assert.equal(state.selectedId, null);
  assert.equal(state.total, 0);
  assert.equal(state.canPrevious, false);
  assert.equal(state.canNext, false);
});

test('initial state prefers the current paper when it exists', () => {
  const { createWorkspaceState } = require(workspacePath);
  const state = createWorkspaceState(papers, 'p4');
  assert.equal(state.selectedId, 'p4');
  assert.equal(state.selectedIndex, 3);
});

test('an unavailable preferred paper falls back to the first result', () => {
  const { createWorkspaceState } = require(workspacePath);
  assert.equal(createWorkspaceState(papers, 'missing').selectedId, 'p1');
});

test('one result produces exactly one layer and disabled boundaries', () => {
  const { createWorkspaceState } = require(workspacePath);
  const state = createWorkspaceState(papers.slice(0, 1));
  assert.deepEqual(state.visiblePapers.map(paper => paper.id), ['p1']);
  assert.equal(state.canPrevious, false);
  assert.equal(state.canNext, false);
});

test('many results expose at most five real adjacent papers and the exact total', () => {
  const { createWorkspaceState } = require(workspacePath);
  const state = createWorkspaceState(papers, 'p5');
  assert.equal(state.total, 8);
  assert.deepEqual(state.visiblePapers.map(paper => paper.id), ['p3', 'p4', 'p5', 'p6', 'p7']);
});

test('selecting another paper already in the layer window preserves that window', () => {
  const { createWorkspaceState, selectPaper } = require(workspacePath);
  const state = createWorkspaceState(papers, 'p4');
  const next = selectPaper(state, 'p5');
  assert.equal(next.visibleStart, state.visibleStart);
  assert.deepEqual(next.visiblePapers.map(paper => paper.id), state.visiblePapers.map(paper => paper.id));
});

test('filter and sort reconciliation preserves a valid selection', () => {
  const { createWorkspaceState, reconcilePapers } = require(workspacePath);
  const state = createWorkspaceState(papers, 'p5');
  const next = reconcilePapers(state, [papers[6], papers[4], papers[2]]);
  assert.equal(next.selectedId, 'p5');
  assert.equal(next.selectedIndex, 1);
});

test('filter reconciliation selects the first result after invalidation', () => {
  const { createWorkspaceState, reconcilePapers } = require(workspacePath);
  const state = createWorkspaceState(papers, 'p5');
  assert.equal(reconcilePapers(state, [papers[7], papers[6]]).selectedId, 'p8');
});

test('previous and next never wrap', () => {
  const { createWorkspaceState, moveSelection } = require(workspacePath);
  const first = createWorkspaceState(papers.slice(0, 3), 'p1');
  assert.equal(moveSelection(first, -1).selectedId, 'p1');
  const last = createWorkspaceState(papers.slice(0, 3), 'p3');
  assert.equal(moveSelection(last, 1).selectedId, 'p3');
});

test('nullish preferred ids do not collide with string paper ids', () => {
  const { createWorkspaceState } = require(workspacePath);
  assert.equal(createWorkspaceState(stringNullishPapers).selectedId, 'first');
  assert.equal(createWorkspaceState(stringNullishPapers, null).selectedId, 'first');
  assert.equal(createWorkspaceState(stringNullishPapers, undefined).selectedId, 'first');
});

test('an explicit string null id remains selectable', () => {
  const { createWorkspaceState, selectPaper } = require(workspacePath);
  assert.equal(createWorkspaceState(stringNullishPapers, 'null').selectedId, 'null');
  const state = createWorkspaceState(stringNullishPapers, 'first');
  assert.equal(selectPaper(state, 'null').selectedId, 'null');
});

test('reconciliation preserves an explicit string null selection', () => {
  const { createWorkspaceState, reconcilePapers } = require(workspacePath);
  const state = createWorkspaceState(stringNullishPapers, 'null');
  const next = reconcilePapers(state, [stringNullishPapers[2], stringNullishPapers[1]]);
  assert.equal(next.selectedId, 'null');
  assert.equal(next.selectedIndex, 1);
});

test('nullish direct selections are no-ops', () => {
  const { createWorkspaceState, selectPaper } = require(workspacePath);
  const state = createWorkspaceState(stringNullishPapers, 'first');
  assert.equal(selectPaper(state, null), state);
  assert.equal(selectPaper(state, undefined), state);
});

test('controller update renders only real layers with one selected option', () => {
  const { createWorkspaceController } = require(workspacePath);
  const harness = createWorkspaceHarness();
  const controller = createWorkspaceController(harness.options);
  controller.update(papers, { preferredId: 'p4', emptyMessage: '没有匹配的论文。' });
  assert.equal(harness.layers.children.length, 5);
  assert.equal(harness.layers.children.filter(node => node.attributes['aria-selected'] === 'true').length, 1);
  assert.equal(harness.count.textContent, '共 8 篇');
  assert.equal(harness.position.textContent, '4 / 8');
});

test('controller selection updates the inspector without opening', () => {
  const opened = [];
  const harness = createWorkspaceHarness({ onOpen: paper => opened.push(paper.id) });
  const controller = require(workspacePath).createWorkspaceController(harness.options);
  controller.update(papers);
  controller.select('p3');
  assert.equal(controller.getState().selectedId, 'p3');
  assert.equal(harness.inspectorTitle.textContent, 'Paper 3');
  assert.deepEqual(opened, []);
});

test('a changed external current is followed once, then preview selection stays independent', () => {
  const harness = createWorkspaceHarness();
  const controller = require(workspacePath).createWorkspaceController(harness.options);
  controller.update(papers, { preferredId: 'p2' });
  controller.select('p3');
  controller.update(papers, { preferredId: 'p6' });
  assert.equal(controller.getState().selectedId, 'p6');
  controller.select('p5');
  controller.update(papers.slice().reverse(), { preferredId: 'p6' });
  assert.equal(controller.getState().selectedId, 'p5');
});

test('an external current excluded by filters is followed when it later becomes visible', () => {
  const harness = createWorkspaceHarness();
  const controller = require(workspacePath).createWorkspaceController(harness.options);
  controller.update(papers.slice(0, 3), { preferredId: 'p1' });
  controller.update(papers.slice(0, 3), { preferredId: 'p8' });
  assert.equal(controller.getState().selectedId, 'p1');
  controller.update(papers, { preferredId: 'p8' });
  assert.equal(controller.getState().selectedId, 'p8');
});

test('review loading and a loaded result with no plan use different text', () => {
  const harness = createWorkspaceHarness({
    getDetails: () => ({ reviewText: '复习数据载入中…', noteText: '暂无笔记' }),
  });
  const controller = require(workspacePath).createWorkspaceController(harness.options);
  controller.update(papers.slice(0, 1));
  assert.equal(harness.inspectorReview.textContent, '复习数据载入中…');
  harness.setDetails(() => ({ reviewText: '尚未安排', noteText: '暂无笔记' }));
  controller.refreshDetails();
  assert.equal(harness.inspectorReview.textContent, '尚未安排');
});

test('single click keeps the layer node stable so native double-click opens once', () => {
  const opened = [];
  const harness = createWorkspaceHarness({ onOpen: paper => opened.push(paper.id) });
  const controller = require(workspacePath).createWorkspaceController(harness.options);
  controller.update(papers, { preferredId: 'p2' });
  controller.bind();
  const before = harness.layers.children.find(node => node.dataset.spatialPaperId === 'p3');
  harness.dispatchPaper('click', 'p3');
  const after = harness.layers.children.find(node => node.dataset.spatialPaperId === 'p3');
  assert.equal(after, before);
  harness.dispatchPaper('dblclick', 'p3');
  assert.deepEqual(opened, ['p3']);
});

test('data refresh with the same paper ids refreshes live layer text', () => {
  const harness = createWorkspaceHarness();
  const controller = require(workspacePath).createWorkspaceController(harness.options);
  controller.update(papers, { preferredId: 'p3' });
  const changed = papers.map(paper => paper.id === 'p3'
    ? { ...paper, title: 'Updated Paper 3', status: '已理解' }
    : paper);
  controller.update(changed);
  const layer = harness.layers.children.find(node => node.dataset.spatialPaperId === 'p3');
  assert.equal(layer.children[1].textContent, 'Updated Paper 3');
  assert.equal(layer.children[3].textContent, '已理解');
});

test('explicit open and Enter reuse the selected paper callback', () => {
  const opened = [];
  const harness = createWorkspaceHarness({ onOpen: paper => opened.push(paper.id) });
  const controller = require(workspacePath).createWorkspaceController(harness.options);
  controller.update(papers, { preferredId: 'p2' });
  controller.bind();
  controller.openSelected();
  harness.dispatchPaperKey('p4', 'Enter');
  assert.deepEqual(opened, ['p2', 'p4']);
});

test('empty results show the real empty state and clear-filter action', () => {
  let clears = 0;
  const harness = createWorkspaceHarness({ onClearFilters: () => { clears += 1; } });
  const controller = require(workspacePath).createWorkspaceController(harness.options);
  controller.update([], { emptyMessage: '语义检索没有命中。' });
  controller.bind();
  assert.equal(harness.empty.hidden, false);
  assert.equal(harness.emptyText.textContent, '语义检索没有命中。');
  assert.equal(harness.inspector.hidden, true);
  assert.equal(harness.inspectorToggle.disabled, true);
  harness.clearButton.click();
  assert.equal(clears, 1);
});

test('controller binding is single-shot and boundary buttons stay disabled', () => {
  const harness = createWorkspaceHarness();
  const controller = require(workspacePath).createWorkspaceController(harness.options);
  controller.update(papers.slice(0, 1));
  controller.bind();
  controller.bind();
  assert.equal(harness.previous.disabled, true);
  assert.equal(harness.next.disabled, true);
  assert.equal(harness.root.listenerCount('click'), 1);
});

test('mobile panels are mutually exclusive and restore trigger focus', () => {
  const harness = createWorkspaceHarness();
  const controller = require(workspacePath).createWorkspaceController(harness.options);
  controller.update(papers, { preferredId: 'p2' });
  controller.bind();
  controller.openPanel('queue');
  assert.equal(harness.root.classList.contains('is-queue-open'), true);
  assert.equal(harness.document.documentElement.classList.contains('spatial-queue-open'), true);
  assert.equal(harness.queueToggle.attributes['aria-expanded'], 'true');
  assert.equal(harness.scrim.hidden, false);
  assert.equal(harness.queueClose.focusCalls, 1);
  assert.equal(harness.filterActions.parentNode, harness.filterSlot);
  assert.equal(harness.filterHome.children.includes(harness.filterActions), false);
  assert.equal(harness.filterSlot.children.filter(node => node === harness.filterActions).length, 1);
  controller.openPanel('inspector');
  assert.equal(harness.root.classList.contains('is-queue-open'), false);
  assert.equal(harness.root.classList.contains('is-inspector-open'), true);
  assert.equal(harness.document.documentElement.classList.contains('spatial-queue-open'), false);
  assert.equal(harness.document.documentElement.classList.contains('spatial-inspector-open'), true);
  assert.equal(harness.queueToggle.attributes['aria-expanded'], 'false');
  assert.equal(harness.inspectorToggle.attributes['aria-expanded'], 'true');
  assert.equal(harness.filterActions.parentNode, harness.filterHome);
  assert.equal(harness.filterSlot.children.includes(harness.filterActions), false);
  assert.equal(harness.filterHome.children.filter(node => node === harness.filterActions).length, 1);
  controller.closePanels();
  assert.equal(harness.root.classList.contains('is-inspector-open'), false);
  assert.equal(harness.scrim.hidden, true);
  assert.equal(harness.filterActions.parentNode, harness.filterHome);
  assert.equal(harness.inspectorToggle.focusCalls, 1);
});

test('a desktop breakpoint transition closes a focused panel before moving focus to the selected layer', () => {
  const harness = createWorkspaceHarness({ scrollTop: 210 });
  const controller = require(workspacePath).createWorkspaceController(harness.options);
  controller.update(papers, { preferredId: 'p3' });
  controller.bind();
  controller.openPanel('inspector');
  assert.equal(harness.document.activeElement, harness.inspectorClose);
  const selectedLayer = harness.layers.children.find(node => node.getAttribute('aria-selected') === 'true');
  assert.ok(selectedLayer);
  let stateWhenFocused = null;
  const focusSelectedLayer = selectedLayer.focus.bind(selectedLayer);
  selectedLayer.focus = () => {
    stateWhenFocused = {
      panelOpen: harness.root.classList.contains('is-inspector-open'),
      scrollTop: harness.scrollContainer.scrollTop,
    };
    focusSelectedLayer();
  };
  harness.scrollContainer.scrollTop = 25;
  harness.desktopMedia.dispatch(true);
  assert.equal(harness.root.classList.contains('is-inspector-open'), false);
  assert.equal(harness.document.documentElement.classList.contains('spatial-inspector-open'), false);
  assert.equal(harness.inspectorToggle.attributes['aria-expanded'], 'false');
  assert.equal(harness.scrim.hidden, true);
  assert.equal(harness.scrollContainer.scrollTop, harness.initialScrollTop);
  assert.equal(harness.filterActions.parentNode, harness.filterHome);
  assert.deepEqual(stateWhenFocused, { panelOpen: false, scrollTop: harness.initialScrollTop });
  assert.equal(harness.document.activeElement, selectedLayer);
  assert.equal(harness.inspectorToggle.focusCalls, 0);
});

test('leaving the mobile breakpoint moves focus from an empty queue to clear filters', () => {
  const harness = createWorkspaceHarness({ scrollTop: 190 });
  const controller = require(workspacePath).createWorkspaceController(harness.options);
  controller.update([], { emptyMessage: 'No matching papers' });
  controller.bind();
  controller.openPanel('queue');
  assert.equal(harness.document.activeElement, harness.queueClose);
  harness.scrollContainer.scrollTop = 30;
  harness.mobileMedia.dispatch(false);
  assert.equal(harness.root.classList.contains('is-queue-open'), false);
  assert.equal(harness.scrim.hidden, true);
  assert.equal(harness.filterActions.parentNode, harness.filterHome);
  assert.equal(harness.scrollContainer.scrollTop, 190);
  assert.equal(harness.document.activeElement, harness.clearButton);
  assert.equal(harness.queueToggle.focusCalls, 0);
});

test('breakpoint closure does not steal unrelated focus', () => {
  const harness = createWorkspaceHarness({ scrollTop: 150 });
  const controller = require(workspacePath).createWorkspaceController(harness.options);
  controller.update(papers, { preferredId: 'p2' });
  controller.bind();
  controller.openPanel('inspector');
  harness.clearButton.focus();
  harness.desktopMedia.dispatch(true);
  assert.equal(harness.root.classList.contains('is-inspector-open'), false);
  assert.equal(harness.document.activeElement, harness.clearButton);
});

test('Escape and scrim close a panel without changing selection or home scroll', () => {
  const harness = createWorkspaceHarness({ scrollTop: 320 });
  const controller = require(workspacePath).createWorkspaceController(harness.options);
  controller.update(papers, { preferredId: 'p3' });
  controller.bind();
  controller.openPanel('inspector');
  harness.scrollContainer.scrollTop = 20;
  harness.dispatchDocumentKey('Escape');
  assert.equal(harness.root.classList.contains('is-inspector-open'), false);
  assert.equal(harness.inspectorToggle.attributes['aria-expanded'], 'false');
  assert.equal(harness.scrim.hidden, true);
  assert.equal(controller.getState().selectedId, 'p3');
  assert.equal(harness.scrollContainer.scrollTop, 320);
  assert.equal(harness.inspectorToggle.focusCalls, 1);

  controller.openPanel('queue');
  harness.scrollContainer.scrollTop = 40;
  harness.scrim.click();
  assert.equal(harness.root.classList.contains('is-queue-open'), false);
  assert.equal(harness.scrollContainer.scrollTop, 320);
  assert.equal(harness.queueToggle.focusCalls, 1);
});

test('idempotent close clears stale panel presentation even without active state', () => {
  const harness = createWorkspaceHarness({ scrollTop: 180 });
  const controller = require(workspacePath).createWorkspaceController(harness.options);
  harness.filterSlot.append(harness.filterActions);
  harness.root.classList.add('is-queue-open', 'is-inspector-open');
  harness.document.documentElement.classList.add('spatial-queue-open', 'spatial-inspector-open');
  harness.queueToggle.setAttribute('aria-expanded', 'true');
  harness.inspectorToggle.setAttribute('aria-expanded', 'true');
  harness.scrim.hidden = false;
  controller.closePanels();
  assert.equal(harness.root.classList.contains('is-queue-open'), false);
  assert.equal(harness.root.classList.contains('is-inspector-open'), false);
  assert.equal(harness.document.documentElement.classList.contains('spatial-queue-open'), false);
  assert.equal(harness.document.documentElement.classList.contains('spatial-inspector-open'), false);
  assert.equal(harness.queueToggle.attributes['aria-expanded'], 'false');
  assert.equal(harness.inspectorToggle.attributes['aria-expanded'], 'false');
  assert.equal(harness.scrim.hidden, true);
  assert.equal(harness.filterActions.parentNode, harness.filterHome);
  assert.equal(harness.filterSlot.children.includes(harness.filterActions), false);
  assert.equal(harness.scrollContainer.scrollTop, 180);
  assert.equal(harness.queueToggle.focusCalls, 0);
  assert.equal(harness.inspectorToggle.focusCalls, 0);
});

test('inspector refuses to open before a valid paper has been rendered', () => {
  const harness = createWorkspaceHarness();
  const controller = require(workspacePath).createWorkspaceController(harness.options);
  controller.bind();
  controller.openPanel('inspector');
  assert.equal(harness.root.classList.contains('is-inspector-open'), false);
  assert.equal(harness.document.documentElement.classList.contains('spatial-inspector-open'), false);
  assert.equal(harness.inspectorToggle.attributes['aria-expanded'], 'false');
  assert.equal(harness.scrim.hidden, true);
  assert.equal(harness.inspectorClose.focusCalls, 0);
});

test('a rejected inspector open fully closes an active queue without stale restoration', () => {
  const harness = createWorkspaceHarness({ scrollTop: 260 });
  const controller = require(workspacePath).createWorkspaceController(harness.options);
  controller.bind();
  controller.openPanel('queue');
  harness.scrollContainer.scrollTop = 25;
  controller.update([]);
  controller.openPanel('inspector');
  assert.equal(harness.root.classList.contains('is-queue-open'), false);
  assert.equal(harness.root.classList.contains('is-inspector-open'), false);
  assert.equal(harness.document.documentElement.classList.contains('spatial-queue-open'), false);
  assert.equal(harness.document.documentElement.classList.contains('spatial-inspector-open'), false);
  assert.equal(harness.queueToggle.attributes['aria-expanded'], 'false');
  assert.equal(harness.inspectorToggle.attributes['aria-expanded'], 'false');
  assert.equal(harness.scrim.hidden, true);
  assert.equal(harness.filterActions.parentNode, harness.filterHome);
  assert.equal(harness.scrollContainer.scrollTop, 260);
  assert.equal(harness.queueToggle.focusCalls, 0);

  harness.scrollContainer.scrollTop = 70;
  controller.closePanels();
  assert.equal(harness.scrollContainer.scrollTop, 70);
  assert.equal(harness.queueToggle.focusCalls, 0);
});

test('empty results cannot open an invisible inspector and close one already open', () => {
  const harness = createWorkspaceHarness();
  const controller = require(workspacePath).createWorkspaceController(harness.options);
  controller.bind();
  controller.update([]);
  controller.openPanel('inspector');
  assert.equal(harness.root.classList.contains('is-inspector-open'), false);
  assert.equal(harness.scrim.hidden, true);

  controller.update(papers, { preferredId: 'p2' });
  controller.openPanel('inspector');
  assert.equal(harness.root.classList.contains('is-inspector-open'), true);
  controller.update([]);
  assert.equal(harness.root.classList.contains('is-inspector-open'), false);
  assert.equal(harness.scrim.hidden, true);
  assert.equal(harness.clearButton.focusCalls, 1);
});
