const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const appearancePath = path.resolve(__dirname, '..', 'public', 'appearance.js');

function memoryStorage(values = {}, options = {}) {
  const data = new Map(Object.entries(values));
  const writes = [];
  return {
    writes,
    getItem(key) {
      if (options.readError) throw options.readError;
      return data.has(key) ? data.get(key) : null;
    },
    setItem(key, value) {
      if (options.writeError) throw options.writeError;
      data.set(key, String(value));
      writes.push([key, String(value)]);
    },
  };
}

function fakeControl(value = '') {
  return {
    value,
    checked: false,
    textContent: '',
    attributes: {},
    listeners: new Map(),
    addEventListener(type, listener) {
      const listeners = this.listeners.get(type) || [];
      listeners.push(listener);
      this.listeners.set(type, listeners);
    },
    dispatch(type) {
      for (const listener of this.listeners.get(type) || []) listener({ target: this });
    },
    setAttribute(name, value) { this.attributes[name] = String(value); },
  };
}

function fakeDocument() {
  const styleControls = [fakeControl('classic'), fakeControl('spatial')];
  const themeControls = [fakeControl('light'), fakeControl('dark')];
  const themeButton = fakeControl();
  const events = [];
  const root = { attributes: {}, setAttribute(name, value) { this.attributes[name] = String(value); } };
  return {
    documentElement: root,
    events,
    styleControls,
    themeControls,
    themeButton,
    querySelectorAll(selector) {
      if (selector === '[data-appearance-field="uiStyle"]') return styleControls;
      if (selector === '[data-appearance-field="theme"]') return themeControls;
      return [];
    },
    querySelector(selector) { return selector === '#themeBtn' ? themeButton : null; },
    dispatchEvent(event) { events.push(event); return true; },
  };
}

function createHarness(values, storageOptions) {
  const document = fakeDocument();
  const storage = memoryStorage(values, storageOptions);
  const CustomEvent = class {
    constructor(type, init) { this.type = type; this.detail = init.detail; }
  };
  const api = require(appearancePath);
  const controller = api.createAppearanceController({ document, storage, CustomEvent });
  return { api, controller, document, storage };
}

test('empty storage bootstraps classic light without writes or events', () => {
  const { controller, document, storage } = createHarness();
  assert.deepEqual(controller.bootstrap(), { uiStyle: 'classic', theme: 'light' });
  assert.deepEqual(document.documentElement.attributes, {
    'data-ui-style': 'classic',
    'data-theme': 'light',
  });
  assert.deepEqual(storage.writes, []);
  assert.deepEqual(document.events, []);
});

for (const uiStyle of ['classic', 'spatial']) {
  for (const theme of ['light', 'dark']) {
    test(`bootstraps ${uiStyle}/${theme}`, () => {
      const { controller } = createHarness({ 'paperstudy.uiStyle': uiStyle, theme });
      assert.deepEqual(controller.bootstrap(), { uiStyle, theme });
    });
  }
}

test('an upgrade with only the old dark theme becomes classic dark', () => {
  const { controller } = createHarness({ theme: 'dark' });
  assert.deepEqual(controller.bootstrap(), { uiStyle: 'classic', theme: 'dark' });
});

for (const values of [
  { 'paperstudy.uiStyle': 'future', theme: 'dark' },
  { 'paperstudy.uiStyle': 'spatial', theme: 'sepia' },
]) {
  test('an invalid stored value falls back as one classic light pair', () => {
    const { controller } = createHarness(values);
    assert.deepEqual(controller.bootstrap(), { uiStyle: 'classic', theme: 'light' });
  });
}

test('a storage read error falls back to classic light', () => {
  const { controller } = createHarness({}, { readError: new Error('blocked') });
  assert.deepEqual(controller.bootstrap(), { uiStyle: 'classic', theme: 'light' });
});

test('an error while accessing the storage object also falls back safely', () => {
  const api = require(appearancePath);
  const document = fakeDocument();
  const controller = api.createAppearanceController({
    document,
    getStorage() { throw new Error('security'); },
    CustomEvent: class {},
  });
  assert.deepEqual(controller.bootstrap(), { uiStyle: 'classic', theme: 'light' });
});

test('setAppearance applies, syncs, persists, and emits one exact event', () => {
  const { controller, document, storage } = createHarness();
  controller.bootstrap();
  controller.bindControls();
  controller.setAppearance({ uiStyle: 'spatial', theme: 'dark' });
  assert.deepEqual(controller.getState(), { uiStyle: 'spatial', theme: 'dark' });
  assert.deepEqual(storage.writes, [
    ['paperstudy.uiStyle', 'spatial'],
    ['theme', 'dark'],
  ]);
  assert.equal(document.styleControls[1].checked, true);
  assert.equal(document.themeControls[1].checked, true);
  assert.equal(document.themeButton.textContent, 'Light');
  assert.equal(document.events.length, 1);
  assert.equal(document.events[0].type, 'paperstudy:appearancechange');
  assert.deepEqual(document.events[0].detail, { uiStyle: 'spatial', theme: 'dark' });
});

test('same-value writes and repeated binding are inert', () => {
  const { controller, document, storage } = createHarness();
  controller.bootstrap();
  controller.bindControls();
  controller.bindControls();
  controller.setAppearance({ uiStyle: 'classic', theme: 'light' });
  document.themeButton.dispatch('click');
  assert.deepEqual(storage.writes, [
    ['paperstudy.uiStyle', 'classic'],
    ['theme', 'dark'],
  ]);
  assert.equal(document.events.length, 1);
});

test('radio controls update state and stay synchronized with the shortcut', () => {
  const { controller, document } = createHarness();
  controller.bootstrap();
  controller.bindControls();
  document.styleControls[1].checked = true;
  document.styleControls[1].dispatch('change');
  document.themeControls[1].checked = true;
  document.themeControls[1].dispatch('change');
  assert.deepEqual(controller.getState(), { uiStyle: 'spatial', theme: 'dark' });
  assert.equal(document.themeButton.textContent, 'Light');
});

test('missing appearance controls do not block bootstrap or binding', () => {
  const api = require(appearancePath);
  const document = {
    documentElement: { setAttribute() {} },
    querySelectorAll() { return []; },
    querySelector() { return null; },
    dispatchEvent() {},
  };
  const controller = api.createAppearanceController({ document, storage: memoryStorage() });
  assert.doesNotThrow(() => {
    controller.bootstrap();
    controller.bindControls();
  });
});

test('a write error keeps the DOM state and still emits', () => {
  const { controller, document } = createHarness({}, { writeError: new Error('quota') });
  controller.bootstrap();
  controller.setAppearance({ uiStyle: 'spatial' });
  assert.deepEqual(controller.getState(), { uiStyle: 'spatial', theme: 'light' });
  assert.equal(document.events.length, 1);
});

test('the browser build applies root attributes before DOMContentLoaded', () => {
  const source = fs.readFileSync(appearancePath, 'utf8');
  const attributes = {};
  const context = {
    window: null,
    document: {
      readyState: 'loading',
      documentElement: { setAttribute(name, value) { attributes[name] = value; } },
      addEventListener() {},
      querySelectorAll() { return []; },
      querySelector() { return null; },
      dispatchEvent() {},
    },
    localStorage: memoryStorage({ 'paperstudy.uiStyle': 'spatial', theme: 'dark' }),
    CustomEvent: class {},
  };
  context.window = context;
  vm.runInNewContext(source, context, { filename: appearancePath });
  assert.deepEqual(attributes, { 'data-ui-style': 'spatial', 'data-theme': 'dark' });
});
