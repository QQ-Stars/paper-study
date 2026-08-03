# 空间研究台全局界面 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在完整保留当前“经典模式”的前提下，新增可在设置中全局切换的“空间研究台”，提供翡翠绿、近黑/雾白双主题、真实论文层叠总览和完整的桌面/移动端体验。

**Architecture:** `public/appearance.js` 是外观状态的唯一写入者，负责同步首屏启动、校验、根属性、控件同步、持久化和单一事件；`public/spatial-workspace.js` 负责可测试的论文队列状态机与空间总览 DOM 控制器；`public/app.js` 只提供现有筛选结果、打开论文回调、复习详情和图表重绘。所有新视觉规则放入后加载的 `public/spatial.css`，并严格限定在 `html[data-ui-style="spatial"]` 下；经典模式只增加空间容器隐藏规则与一个 `display: contents` 的透明筛选包装。

**Tech Stack:** Vanilla HTML/CSS/JavaScript、CommonJS/UMD、Node `node:test`、现有 ECharts/PDF.js、Codex 应用内浏览器视觉验收。

---

## Locked contracts

- 根属性只能是 `data-ui-style="classic|spatial"` 和 `data-theme="light|dark"`。
- 本地键只能是 `paperstudy.uiStyle` 与现有 `theme`；首次升级保留合法旧主题并默认为 `classic`。
- 任一非空存储值非法或读取抛错时整组回退 `classic/light`；写入失败不回滚已应用的界面。
- `paperstudy:appearancechange` 的 `detail` 严格为 `{ uiStyle, theme }`；启动和同值设置不发事件。
- `appearance.js` 独占根属性、两个外观存储键、设置 radio 和 `#themeBtn`；`app.js` 只监听事件并重绘当前图表。
- `spatial.css` 在 `academic.css` 后加载，所有普通选择器均以 `html[data-ui-style="spatial"]` 开头。
- 空间总览中的一层只代表一篇真实论文，最多展示五篇相邻结果，精确总数单独显示。
- 单击只选中；双击、Enter 或“打开阅读”进入现有阅读页；上一项/下一项不循环。
- 筛选/排序后保留仍有效的选择，否则选第一项；空结果不创建假卡片；一篇结果只创建一层。
- 空间预览选择与现有 `current` 分离；所有从表格、复习或管理页触发的 `openPaper()` 会把空间选择同步到新论文，但单击空间层绝不修改 `current`。
- 复习数据“尚未载入”和“已载入但无计划”使用不同文案，不能把未知状态误报成无计划。
- 原 ECharts 与精确表格保留在空间舞台后方，并提供原生 `#homeTable` 锚点。
- 手机端使用底部全局导航、左侧研究目录抽屉、底部论文检查器；主要触控目标至少 `44×44 CSS px`。
- 不增加依赖，不使用 WebGL/Three.js、图片背景、粒子或循环环境动画。

## File map

- Create: `public/appearance.js` — 外观读取、规范化、首屏应用、控件、存储和事件的唯一所有者。
- Create: `public/spatial-workspace.js` — 论文队列纯状态机、真实层窗口和空间总览控制器。
- Create: `public/spatial.css` — 深/浅空间令牌、全局工作台、总览、阅读/复习/管理/采集/洞察/设置，以及响应式和能力回退。
- Modify: `public/index.html` — 根回退属性、同步外观脚本、空间样式、外观设置、语义化总览和脚本顺序。
- Modify: `public/style.css` — 加入 `.spatial-only { display: none; }` 与透明结构包装 `.home-filter-actions { display: contents; }`，确保经典模式外观不变。
- Modify: `public/app.js` — 删除旧主题写入；监听外观事件；把筛选结果接入空间控制器；复用 `openPaper()`。
- Create: `test/appearance.test.js` — 外观模块单元测试与浏览器构建同步启动测试。
- Create: `test/appearance-page-integration.test.js` — 首屏顺序、设置接线、单一写入者和经典隔离契约。
- Create: `test/spatial-workspace.test.js` — 队列状态机、最多五层、DOM 内容与交互控制器测试。
- Create: `test/spatial-workspace-page-integration.test.js` — 总览语义容器、脚本顺序及 `app.js` 接入契约。
- Create: `test/spatial-style-contract.test.js` — CSS 作用域、主题、全局覆盖、移动端、降级与动效契约。

## Preflight: protect the existing UI

- [ ] **Step 1: Confirm the worktree boundary**

Run:

```powershell
git status --short
```

Expected: the existing user-owned `AGENTS.md` change may appear; do not stage or modify it. Record any other pre-existing paths before continuing.

- [ ] **Step 2: Run the untouched suite**

Run:

```powershell
npm test
```

Expected: PASS. If it does not pass, record the pre-existing failures and stop implementation until they are separated from this feature.

- [ ] **Step 3: Capture classic visual baselines**

Start the app with `npm start`, open `http://localhost:5173/`, and capture these four pre-change states with the application browser screenshot tool:

```text
C:\tmp\study-app-ui-baselines\classic-light-desktop.png  1440×1000
C:\tmp\study-app-ui-baselines\classic-dark-desktop.png   1440×1000
C:\tmp\study-app-ui-baselines\classic-light-mobile.png   390×844
C:\tmp\study-app-ui-baselines\classic-dark-mobile.png    390×844
```

For each state, include the overview top bar, dashboard, first table rows, Settings modal, and one reading view. These are comparison artifacts, not repository files.

---

### Task 1: Appearance state and synchronous bootstrap

**Files:**
- Create: `test/appearance.test.js`
- Create: `public/appearance.js`

- [ ] **Step 1: Write the failing appearance tests**

Create `test/appearance.test.js` with real fake DOM/storage objects and the following public contract:

```javascript
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
```

- [ ] **Step 2: Run the tests and verify the RED state**

Run:

```powershell
node --test test/appearance.test.js
```

Expected: FAIL because `public/appearance.js` does not exist.

- [ ] **Step 3: Implement the minimal UMD appearance controller**

Create `public/appearance.js` with these exact exports and sequencing:

```javascript
(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  if (!root || !root.document) return;

  const controller = api.createAppearanceController({
    document: root.document,
    getStorage: () => root.localStorage,
    CustomEvent: root.CustomEvent,
  });
  controller.bootstrap();
  root.PaperStudyAppearance = { ...api, controller };

  const bind = () => controller.bindControls();
  if (root.document.readyState === 'loading') {
    root.document.addEventListener('DOMContentLoaded', bind, { once: true });
  } else {
    bind();
  }
})(typeof window !== 'undefined' ? window : undefined, function () {
  const DEFAULT_APPEARANCE = Object.freeze({ uiStyle: 'classic', theme: 'light' });
  const STORAGE_KEYS = Object.freeze({ uiStyle: 'paperstudy.uiStyle', theme: 'theme' });
  const EVENT_NAME = 'paperstudy:appearancechange';
  const VALID_UI_STYLES = new Set(['classic', 'spatial']);
  const VALID_THEMES = new Set(['light', 'dark']);

  function copy(state) { return { uiStyle: state.uiStyle, theme: state.theme }; }
  function validPair(state) {
    return VALID_UI_STYLES.has(state.uiStyle) && VALID_THEMES.has(state.theme);
  }
  function normalizePair(candidate) {
    return validPair(candidate || {}) ? copy(candidate) : copy(DEFAULT_APPEARANCE);
  }

  function createAppearanceController({ document, storage, getStorage, CustomEvent } = {}) {
    let state = copy(DEFAULT_APPEARANCE);
    let controlsBound = false;

    function resolveStorage() {
      if (storage) return storage;
      return typeof getStorage === 'function' ? getStorage() : null;
    }

    function readState() {
      try {
        const store = resolveStorage();
        if (!store) return copy(DEFAULT_APPEARANCE);
        const storedStyle = store.getItem(STORAGE_KEYS.uiStyle);
        const storedTheme = store.getItem(STORAGE_KEYS.theme);
        const candidate = {
          uiStyle: storedStyle == null ? DEFAULT_APPEARANCE.uiStyle : storedStyle,
          theme: storedTheme == null ? DEFAULT_APPEARANCE.theme : storedTheme,
        };
        return normalizePair(candidate);
      } catch (error) {
        return copy(DEFAULT_APPEARANCE);
      }
    }

    function applyRoot() {
      const root = document && document.documentElement;
      if (!root || typeof root.setAttribute !== 'function') return;
      root.setAttribute('data-ui-style', state.uiStyle);
      root.setAttribute('data-theme', state.theme);
    }

    function controls(field) {
      if (!document || typeof document.querySelectorAll !== 'function') return [];
      return Array.from(document.querySelectorAll(`[data-appearance-field="${field}"]`));
    }

    function themeButton() {
      return document && typeof document.querySelector === 'function'
        ? document.querySelector('#themeBtn')
        : null;
    }

    function syncControls() {
      for (const control of controls('uiStyle')) control.checked = control.value === state.uiStyle;
      for (const control of controls('theme')) control.checked = control.value === state.theme;
      const button = themeButton();
      if (!button) return;
      button.textContent = state.theme === 'dark' ? 'Light' : 'Dark';
      button.setAttribute(
        'aria-label',
        state.theme === 'dark' ? 'Switch to light theme' : 'Switch to dark theme',
      );
    }

    function persist() {
      try {
        const store = resolveStorage();
        if (!store) return;
        store.setItem(STORAGE_KEYS.uiStyle, state.uiStyle);
        store.setItem(STORAGE_KEYS.theme, state.theme);
      } catch (error) {
        // Local appearance remains usable when storage is unavailable or full.
      }
    }

    function emit() {
      if (!document || typeof document.dispatchEvent !== 'function' || typeof CustomEvent !== 'function') return;
      document.dispatchEvent(new CustomEvent(EVENT_NAME, { detail: copy(state) }));
    }

    function bootstrap() {
      state = readState();
      applyRoot();
      return copy(state);
    }

    function getState() { return copy(state); }

    function setAppearance(partial = {}) {
      const next = normalizePair({ ...state, ...partial });
      if (next.uiStyle === state.uiStyle && next.theme === state.theme) return copy(state);
      state = next;
      applyRoot();
      syncControls();
      persist();
      emit();
      return copy(state);
    }

    function bindControls() {
      syncControls();
      if (controlsBound) return;
      controlsBound = true;
      for (const control of controls('uiStyle')) {
        control.addEventListener('change', () => {
          if (control.checked) setAppearance({ uiStyle: control.value });
        });
      }
      for (const control of controls('theme')) {
        control.addEventListener('change', () => {
          if (control.checked) setAppearance({ theme: control.value });
        });
      }
      const button = themeButton();
      if (button) {
        button.addEventListener('click', () => {
          setAppearance({ theme: state.theme === 'dark' ? 'light' : 'dark' });
        });
      }
    }

    return { bootstrap, bindControls, getState, setAppearance };
  }

  return { createAppearanceController, DEFAULT_APPEARANCE, STORAGE_KEYS };
});
```

- [ ] **Step 4: Run the focused tests and verify GREEN**

Run:

```powershell
node --test test/appearance.test.js
```

Expected: all appearance tests PASS with no warnings.

- [ ] **Step 5: Commit the appearance controller**

Run separately:

```powershell
git add -- public/appearance.js test/appearance.test.js
git diff --cached --check
git commit -m "feat: add appearance state controller"
```

Expected: only the two listed files are committed.

---

### Task 2: First-paint wiring and Settings appearance controls

**Files:**
- Create: `test/appearance-page-integration.test.js`
- Create: `public/spatial.css`
- Modify: `public/index.html:2-16,334-343`
- Modify: `public/app.js:20-104,1296-1366`

- [ ] **Step 1: Write failing page integration contracts**

Create `test/appearance-page-integration.test.js`. Parse tags semantically rather than comparing whitespace, and assert:

```javascript
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');

const publicDir = path.resolve(__dirname, '..', 'public');
const html = fs.readFileSync(path.join(publicDir, 'index.html'), 'utf8');
const app = fs.readFileSync(path.join(publicDir, 'app.js'), 'utf8');

function indexOfSource(source) {
  const expression = new RegExp(`<script\\b(?=[^>]*\\bsrc=["']${source.replace('.', '\\.') }["'])[^>]*>`, 'i');
  const match = expression.exec(html);
  assert.ok(match, `missing ${source}`);
  return { index: match.index, tag: match[0] };
}

test('the root has a script-free classic light fallback', () => {
  assert.match(html, /<html\b(?=[^>]*data-ui-style="classic")(?=[^>]*data-theme="light")[^>]*>/i);
});

test('appearance bootstraps synchronously before styles and application data', () => {
  const appearance = indexOfSource('appearance.js');
  assert.doesNotMatch(appearance.tag, /\b(?:async|defer|type\s*=\s*["']module["'])\b/i);
  assert.ok(appearance.index < html.indexOf('href="style.css"'));
  assert.ok(appearance.index < html.indexOf('src="app.js"'));
  assert.match(app, /fetch\(['"]\/api\/papers['"]\)/);
});

test('spatial CSS loads after the existing cascade', () => {
  const style = html.indexOf('href="style.css"');
  const academic = html.indexOf('href="academic.css"');
  const spatial = html.indexOf('href="spatial.css"');
  assert.ok(style >= 0 && style < academic && academic < spatial);
});

test('Settings exposes exactly four native appearance choices', () => {
  const fields = [...html.matchAll(/<input\b[^>]*data-appearance-field="(uiStyle|theme)"[^>]*value="(classic|spatial|light|dark)"[^>]*>/g)]
    .map(match => [match[1], match[2]]);
  assert.deepEqual(fields, [
    ['uiStyle', 'classic'],
    ['uiStyle', 'spatial'],
    ['theme', 'light'],
    ['theme', 'dark'],
  ]);
});

test('app.js is not an appearance writer', () => {
  assert.doesNotMatch(app, /function\s+(?:applyTheme|toggleTheme)\b/);
  assert.doesNotMatch(app, /setAttribute\(\s*['"]data-(?:theme|ui-style)['"]/);
  assert.doesNotMatch(app, /localStorage\.setItem\(\s*['"](?:theme|paperstudy\.uiStyle)['"]/);
  assert.doesNotMatch(app, /#themeBtn['"]\)\.onclick/);
  assert.equal([...app.matchAll(/addEventListener\(\s*['"]paperstudy:appearancechange['"]/g)].length, 1);
});

test('appearance switching redraws cached charts without a network request', () => {
  const handler = app.slice(app.indexOf('function handleAppearanceChange'), app.indexOf("document.addEventListener('paperstudy:appearancechange'"));
  assert.match(handler, /redrawInsightChartsFromCache\(\)/);
  assert.doesNotMatch(handler, /renderInsights\(\)|fetch\(/);
  assert.doesNotMatch(handler, /layoutPages\(/);
});

test('both citation fetch paths refresh the appearance redraw cache', () => {
  const assignments = [...app.matchAll(/insightCiteGraph\s*=\s*g\b/g)];
  assert.ok(assignments.length >= 2, 'renderInsights and buildCite both cache the latest graph');
});

test('server Settings payload never contains local appearance fields', () => {
  const saveSettings = app.slice(app.indexOf('async function saveSettings'), app.indexOf('function openSettingsModal'));
  assert.doesNotMatch(saveSettings, /uiStyle|paperstudy\.uiStyle|data-theme|\btheme\s*:/);
  assert.match(saveSettings, /try\s*\{/);
  assert.match(saveSettings, /catch\s*\(/);
});
```

- [ ] **Step 2: Verify the integration tests fail for the missing wiring**

Run:

```powershell
node --test test/appearance-page-integration.test.js
```

Expected: FAIL on the missing root attribute, head script, stylesheet and Settings controls, plus legacy `app.js` ownership.

- [ ] **Step 3: Wire the root, synchronous script and stylesheet order**

Change the opening document to:

```html
<!DOCTYPE html>
<html lang="zh-CN" data-ui-style="classic" data-theme="light">
<head>
<meta charset="UTF-8" />
<meta name="viewport" content="width=device-width, initial-scale=1.0" />
<title>论文精读 · Paper Study</title>
<script src="appearance.js"></script>
<link rel="stylesheet" href="style.css" />
<link rel="stylesheet" href="academic.css" />
<link rel="stylesheet" href="spatial.css" />
<link rel="stylesheet" href="vendor/katex/katex.min.css" />
```

Create the first minimal, already-scoped `public/spatial.css`:

```css
html[data-ui-style="spatial"][data-theme="dark"] { color-scheme: dark; }
html[data-ui-style="spatial"][data-theme="light"] { color-scheme: light; }
```

- [ ] **Step 4: Add the first Settings card without joining `/api/settings`**

Insert as the first child of `.modal-body`:

```html
<fieldset class="set-card appearance-card" aria-labelledby="appearanceLegend">
  <legend id="appearanceLegend" class="settings-sub">外观<span class="sub-note">仅保存在当前设备</span></legend>
  <div class="appearance-row">
    <span class="appearance-label">界面风格</span>
    <div class="appearance-options" role="radiogroup" aria-label="界面风格">
      <label><input type="radio" name="uiStyle" data-appearance-field="uiStyle" value="classic" />经典模式</label>
      <label><input type="radio" name="uiStyle" data-appearance-field="uiStyle" value="spatial" />空间研究台</label>
    </div>
  </div>
  <div class="appearance-row">
    <span class="appearance-label">明暗模式</span>
    <div class="appearance-options" role="radiogroup" aria-label="明暗模式">
      <label><input type="radio" name="theme" data-appearance-field="theme" value="light" />浅色</label>
      <label><input type="radio" name="theme" data-appearance-field="theme" value="dark" />深色</label>
    </div>
  </div>
</fieldset>
```

- [ ] **Step 5: Make `app.js` a read-only appearance subscriber**

Delete `applyTheme()`, `toggleTheme()`, the theme read in `init()`, and `$('#themeBtn').onclick = toggleTheme`. Add state and one listener before `init()`:

```javascript
let appReady = false;
let appearanceFrame = 0;
let insightCiteGraph = null;

function redrawInsightChartsFromCache() {
  buildInsightsShell();
  renderTree();
  renderTrend();
  if (insightCiteGraph && insightCiteGraph.edgeCount > 0) {
    renderCite(insightCiteGraph);
    renderCited(insightCiteGraph);
  } else {
    showCitePrompt();
    renderCited(null);
  }
}

function handleAppearanceChange() {
  if (!appReady) return;
  try {
    if (currentView === 'home') renderHome();
    if (currentView === 'insights') redrawInsightChartsFromCache();
  } catch (error) {
    console.warn('Appearance chart redraw failed', error);
  }
  if (appearanceFrame) cancelAnimationFrame(appearanceFrame);
  appearanceFrame = requestAnimationFrame(() => {
    appearanceFrame = 0;
    [chProgress, chDir, chVenue, chTrend, chTree, chCited, chCite]
      .forEach(chart => {
        try { if (chart) chart.resize(); } catch (error) { console.warn('Chart resize failed', error); }
      });
  });
}

document.addEventListener('paperstudy:appearancechange', handleAppearanceChange);
init();
```

Remove the earlier top-level `init();` call so the call beside the appearance listener is the only one. Replace `init()` with the same startup sequence minus the legacy theme read, and set readiness last:

```javascript
async function init() {
  PAPERS = normPapers(await (await fetch('/api/papers')).json());
  if (localStorage.getItem('hide-left') === '1') $('#layout').classList.add('hide-left');
  if (localStorage.getItem('hide-right') === '1') $('#layout').classList.add('hide-right');
  buildYearFilters();
  buildSideYears();
  renderSidebar();
  buildDashShell();
  renderHome();
  bindUI();
  initResizers();
  showView('home');
  appReady = true;
}
```

Replace `renderInsights()` so its ordinary navigation fetch always owns the latest citation cache, including empty and error states:

```javascript
async function renderInsights() {
  buildInsightsShell();
  renderTree();
  renderTrend();
  try {
    const g = await (await fetch('/api/citegraph')).json();
    insightCiteGraph = g;
    if (g && g.edgeCount > 0) {
      renderCite(g);
      renderCited(g);
    } else {
      insightCiteGraph = null;
      showCitePrompt();
      renderCited(null);
    }
  } catch (error) {
    insightCiteGraph = null;
    showCitePrompt();
    renderCited(null);
  }
}
```

Replace `buildCite()` with:

```javascript
async function buildCite() {
  const btn = $('#citeBuildBtn');
  const hint = $('#citeHint');
  btn.disabled = true;
  const old = btn.textContent;
  btn.textContent = '构建中…';
  hint.textContent = '抓取参考文献中（约 1~2 分钟，有 S2 key 更快）…';
  try {
    await streamNDJSON('/api/cite-build', {}, event => {
      if (event.type === 'progress') {
        const match = /^PROG::(\d+)::(\d+)/.exec(event.line);
        if (match) hint.textContent = `抓取参考文献 ${match[1]} / ${match[2]}…`;
      } else if (event.type === 'result') {
        hint.textContent = event.ok ? `✅ 已建 ${event.edges} 条引用` : `失败：${event.error || '未知'}`;
      }
    });
    const g = await (await fetch('/api/citegraph')).json();
    insightCiteGraph = g;
    if (g && g.edgeCount > 0) {
      renderCite(g);
      renderCited(g);
    } else {
      insightCiteGraph = null;
      showCitePrompt();
      renderCited(null);
    }
  } catch (error) {
    insightCiteGraph = null;
    showCitePrompt();
    renderCited(null);
    hint.textContent = '失败：' + error;
  } finally {
    btn.disabled = false;
    btn.textContent = old;
  }
}
```

These two ordinary fetch paths remain the only network owners. `handleAppearanceChange()` never fetches, changes view, or rebuilds PDF pages; it redraws the latest cached graph and resizes chart instances in one animation frame.

Wrap the existing `/api/settings` POST in `saveSettings()` without adding appearance fields:

```javascript
const hint = $('#setHint');
try {
  const response = await fetch('/api/settings', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  hint.textContent = '已保存 ✓（下次采集生效）';
  setTimeout(() => { hint.textContent = ''; }, 3000);
  loadSettings();
} catch (error) {
  hint.textContent = `保存失败：${error.message || error}`;
}
```

This catch changes only the model-settings hint; the appearance controller and root attributes are not referenced.

- [ ] **Step 6: Verify focused and full tests**

Run separately:

```powershell
node --test test/appearance.test.js test/appearance-page-integration.test.js
npm test
```

Expected: both focused files PASS; full suite PASS, including the existing adjacent Markdown script-order contract.

- [ ] **Step 7: Commit first-paint and Settings integration**

```powershell
git add -- public/index.html public/app.js public/spatial.css test/appearance-page-integration.test.js
git diff --cached --check
git commit -m "feat: wire global appearance settings"
```

---

### Task 3: Deterministic paper-layer state machine

**Files:**
- Create: `test/spatial-workspace.test.js`
- Create: `public/spatial-workspace.js`

- [ ] **Step 1: Write failing pure-state tests**

Start `test/spatial-workspace.test.js` with:

```javascript
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
```

- [ ] **Step 2: Verify RED**

Run:

```powershell
node --test test/spatial-workspace.test.js
```

Expected: FAIL because `public/spatial-workspace.js` does not exist.

- [ ] **Step 3: Implement the pure model first**

Create a UMD module exposing this exact pure API:

```javascript
(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  if (root) root.SpatialWorkspace = api;
})(typeof window !== 'undefined' ? window : undefined, function () {
  const MAX_VISIBLE_LAYERS = 5;

  function paperList(value) {
    return Array.isArray(value) ? value.filter(paper => paper && paper.id != null) : [];
  }

  function stateFrom(items, selectedIndex, preservedStart = null) {
    const count = Math.min(MAX_VISIBLE_LAYERS, items.length);
    const centeredStart = selectedIndex < 0
      ? 0
      : Math.min(Math.max(selectedIndex - Math.floor(count / 2), 0), items.length - count);
    const maxStart = Math.max(0, items.length - count);
    const candidateStart = Number.isInteger(preservedStart)
      ? Math.min(Math.max(preservedStart, 0), maxStart)
      : centeredStart;
    const preservesSelected = selectedIndex >= candidateStart && selectedIndex < candidateStart + count;
    const start = preservesSelected ? candidateStart : centeredStart;
    return {
      papers: items,
      selectedId: selectedIndex < 0 ? null : items[selectedIndex].id,
      selectedIndex,
      total: items.length,
      visibleStart: start,
      visiblePapers: items.slice(start, start + count),
      canPrevious: selectedIndex > 0,
      canNext: selectedIndex >= 0 && selectedIndex < items.length - 1,
    };
  }

  function createWorkspaceState(papers, preferredId = null) {
    const items = paperList(papers);
    let selectedIndex = items.findIndex(paper => String(paper.id) === String(preferredId));
    if (selectedIndex < 0) selectedIndex = items.length ? 0 : -1;
    return stateFrom(items, selectedIndex);
  }

  function reconcilePapers(state, papers) {
    return createWorkspaceState(papers, state && state.selectedId);
  }

  function selectPaper(state, paperId) {
    if (!state) return state;
    const nextIndex = state.papers.findIndex(paper => String(paper.id) === String(paperId));
    if (nextIndex < 0 || nextIndex === state.selectedIndex) return state;
    return stateFrom(state.papers, nextIndex, state.visibleStart);
  }

  function moveSelection(state, delta) {
    if (!state || state.selectedIndex < 0) return state;
    const nextIndex = Math.min(
      state.papers.length - 1,
      Math.max(0, state.selectedIndex + Math.sign(Number(delta) || 0)),
    );
    return selectPaper(state, state.papers[nextIndex].id);
  }

  function selectedPaper(state) {
    return state && state.selectedIndex >= 0 ? state.papers[state.selectedIndex] : null;
  }

  return {
    MAX_VISIBLE_LAYERS,
    createWorkspaceState,
    moveSelection,
    reconcilePapers,
    selectPaper,
    selectedPaper,
  };
});
```

- [ ] **Step 4: Verify state-machine GREEN**

Run:

```powershell
node --test test/spatial-workspace.test.js
```

Expected: all state tests PASS.

- [ ] **Step 5: Commit the state machine**

```powershell
git add -- public/spatial-workspace.js test/spatial-workspace.test.js
git diff --cached --check
git commit -m "feat: add spatial paper queue model"
```

---

### Task 4: Semantic spatial overview and tested controller

**Files:**
- Modify: `test/spatial-workspace.test.js`
- Create: `test/spatial-workspace-page-integration.test.js`
- Modify: `public/spatial-workspace.js`
- Modify: `public/index.html:59-99,541-545`
- Modify: `public/style.css`

- [ ] **Step 1: Add failing controller behavior tests**

Extend `test/spatial-workspace.test.js` with a fake document based on the existing `test/ingest-rendering.test.js` pattern. Assert these public controller behaviors one at a time:

```javascript
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
```

Add this complete harness above the tests (later mobile tests extend the same objects rather than replacing them):

```javascript
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
  focus() { this.focusCalls += 1; }
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
    listeners: new Map(),
    createElement(tagName) { return new FakeElement(tagName); },
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
  const desktopMedia = new FakeMediaQuery();
  const mobileMedia = new FakeMediaQuery(true);
  const scrollContainer = new FakeElement('section', 'home');
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
```

Tests inspect created nodes and real event listeners rather than mocks of rendering calls.

- [ ] **Step 2: Write the failing page contract**

Create `test/spatial-workspace-page-integration.test.js` and assert:

```javascript
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');

const publicDir = path.resolve(__dirname, '..', 'public');
const html = fs.readFileSync(path.join(publicDir, 'index.html'), 'utf8');
const style = fs.readFileSync(path.join(publicDir, 'style.css'), 'utf8');

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
```

- [ ] **Step 3: Verify both new RED states**

Run:

```powershell
node --test test/spatial-workspace.test.js test/spatial-workspace-page-integration.test.js
```

Expected: FAIL because the controller and semantic DOM do not exist.

- [ ] **Step 4: Add the semantic overview without changing existing IDs**

Insert after the existing overview `.page-head` and before `#dash`:

```html
<section id="spatialOverview" class="spatial-only spatial-overview" aria-labelledby="spatialHeading">
  <div class="spatial-mobile-tools" aria-label="空间研究台面板">
    <button id="spatialQueueToggle" type="button" aria-controls="spatialQueue" aria-expanded="false">研究目录</button>
    <button id="spatialInspectorToggle" type="button" aria-controls="spatialInspector" aria-expanded="false">论文详情</button>
  </div>
  <aside id="spatialQueue" class="spatial-queue" aria-label="研究目录与队列">
    <button id="spatialQueueClose" class="spatial-panel-close" type="button" aria-label="关闭研究目录">关闭</button>
    <div class="spatial-kicker">RESEARCH QUEUE</div>
    <h2 id="spatialHeading">空间研究台</h2>
    <div id="spatialFilterSlot" class="spatial-filter-slot" aria-label="当前总览筛选"></div>
    <div class="spatial-queue-counts" aria-live="polite">
      <span><b id="spatialQueueTotal">0</b>当前结果</span>
      <span><b id="spatialQueueLearning">0</b>学习中</span>
      <span><b id="spatialQueueDone">0</b>已理解</span>
    </div>
    <div class="spatial-direction-block">
      <h3>当前研究方向</h3>
      <ul id="spatialDirections"></ul>
    </div>
    <p>搜索、年份、语义和收藏筛选继续使用顶部现有控件。</p>
  </aside>
  <div class="spatial-stage" aria-label="当前论文队列">
    <header class="spatial-stage-head">
      <div><span id="spatialCount">共 0 篇</span><strong id="spatialPosition">0 / 0</strong></div>
      <a class="spatial-data-link" href="#homeTable">查看完整数据</a>
    </header>
    <div id="spatialLayers" class="spatial-layers" role="listbox" aria-label="筛选后的论文"></div>
    <div id="spatialEmpty" class="spatial-empty" hidden>
      <strong>没有可显示的论文</strong>
      <span id="spatialEmptyText"></span>
      <button id="spatialClearFilters" type="button">清除筛选</button>
    </div>
    <footer class="spatial-stage-nav">
      <button id="spatialPrev" type="button">上一项</button>
      <button id="spatialNext" type="button">下一项</button>
    </footer>
    <div id="spatialContext" class="spatial-context" aria-live="polite">已理解 0 · 学习中 0 · 当前 0 / 0</div>
  </div>
  <aside id="spatialInspector" class="spatial-inspector" aria-label="当前论文详情" hidden>
    <button id="spatialInspectorClose" class="spatial-panel-close" type="button" aria-label="关闭论文详情">关闭</button>
    <div class="spatial-kicker">CURRENT PAPER</div>
    <h3 id="spatialInspectorTitle"></h3>
    <p id="spatialInspectorTitleZh" class="spatial-title-zh"></p>
    <dl class="spatial-meta">
      <div><dt>来源</dt><dd id="spatialInspectorMeta"></dd></div>
      <div><dt>状态</dt><dd id="spatialInspectorStatus"></dd></div>
      <div><dt>复习</dt><dd id="spatialInspectorReview"></dd></div>
      <div><dt>笔记</dt><dd id="spatialInspectorNote"></dd></div>
    </dl>
    <p id="spatialInspectorSummary" class="spatial-summary"></p>
    <button id="spatialOpen" type="button">打开阅读</button>
  </aside>
  <button id="spatialScrim" class="spatial-scrim" type="button" aria-label="关闭空间面板" hidden></button>
</section>
```

Wrap only the existing secondary home controls in `#topFilters`; leave `.search-box` as its direct first child:

```html
<div id="homeFilterActions" class="home-filter-actions">
  <div id="yearFilters" class="chips"></div>
  <button id="semToggle" class="chip-btn sem-chip" title="语义检索：用大意或中文描述找论文（本地嵌入，跨语言）">🔮 语义</button>
  <button id="favFilter" class="chip-btn fav-chip" title="只看收藏">☆ 收藏</button>
</div>
```

Add only these classic-safe rules to `public/style.css`:

```css
.spatial-only { display: none; }
.home-filter-actions { display: contents; }
```

Use this exact protected tail order in `index.html`:

```html
<script src="spatial-workspace.js"></script>
<script src="ingest-rendering.js"></script>
<script src="markdown-rendering-coordinator.js"></script>
<script src="app.js"></script>
```

- [ ] **Step 5: Implement the controller as a thin DOM adapter over the pure model**

Extend `public/spatial-workspace.js` with `createWorkspaceController(options)`. It must:

```javascript
function createWorkspaceController({ root, document, scrollContainer, onOpen, onClearFilters, getDetails } = {}) {
  let state = createWorkspaceState([]);
  let initialized = false;
  let bound = false;
  let emptyMessage = '';
  let lastPreferredKey = null;
  let pendingPreferredId = null;

  const byId = id => document.getElementById(id);
  const elements = {
    layers: byId('spatialLayers'),
    count: byId('spatialCount'),
    position: byId('spatialPosition'),
    queueTotal: byId('spatialQueueTotal'),
    queueLearning: byId('spatialQueueLearning'),
    queueDone: byId('spatialQueueDone'),
    directions: byId('spatialDirections'),
    context: byId('spatialContext'),
    empty: byId('spatialEmpty'),
    emptyText: byId('spatialEmptyText'),
    clear: byId('spatialClearFilters'),
    inspector: byId('spatialInspector'),
    title: byId('spatialInspectorTitle'),
    titleZh: byId('spatialInspectorTitleZh'),
    meta: byId('spatialInspectorMeta'),
    status: byId('spatialInspectorStatus'),
    review: byId('spatialInspectorReview'),
    note: byId('spatialInspectorNote'),
    summary: byId('spatialInspectorSummary'),
    previous: byId('spatialPrev'),
    next: byId('spatialNext'),
    open: byId('spatialOpen'),
    inspectorToggle: byId('spatialInspectorToggle'),
  };

  function createTextElement(tag, className, text) {
    const element = document.createElement(tag);
    element.className = className;
    element.textContent = text == null ? '' : String(text);
    return element;
  }

  function layerNode(paper) {
    const sourceIndex = state.papers.findIndex(item => String(item.id) === String(paper.id));
    const offset = Math.max(-4, Math.min(4, sourceIndex - state.selectedIndex));
    const button = document.createElement('button');
    button.type = 'button';
    button.className = `spatial-layer layer-offset-${offset}`;
    button.dataset.spatialPaperId = paper.id;
    button.setAttribute('role', 'option');
    button.setAttribute('aria-selected', String(String(paper.id) === String(state.selectedId)));
    button.tabIndex = String(paper.id) === String(state.selectedId) ? 0 : -1;
    button.append(
      createTextElement('span', 'spatial-layer-index', String(sourceIndex + 1).padStart(2, '0')),
      createTextElement('strong', 'spatial-layer-title', paper.title || paper.id),
      createTextElement('span', 'spatial-layer-meta', [paper.venue, paper.year, paper.type].filter(Boolean).join(' · ')),
      createTextElement('span', 'spatial-layer-status', paper.status || '未开始'),
    );
    return button;
  }

  function detailsFor(paper) {
    if (typeof getDetails !== 'function') {
      return { reviewText: '复习数据载入中…', noteText: paper.hasNote ? '已有笔记' : '暂无笔记' };
    }
    try {
      return getDetails(paper) || { reviewText: '尚未安排', noteText: '暂无笔记' };
    } catch (error) {
      return { reviewText: '复习信息暂不可用', noteText: paper.hasNote ? '已有笔记' : '暂无笔记' };
    }
  }

  function renderDirections() {
    const counts = new Map();
    for (const paper of state.papers) {
      const direction = String(paper.type || '').split('·')[0].trim() || '其他';
      counts.set(direction, (counts.get(direction) || 0) + 1);
    }
    const nodes = [...counts.entries()]
      .sort((left, right) => right[1] - left[1] || left[0].localeCompare(right[0]))
      .slice(0, 5)
      .map(([name, count]) => createTextElement('li', 'spatial-direction', `${name} ${count}`));
    elements.directions.replaceChildren(...nodes);
  }

  function renderInspector() {
    const paper = selectedPaper(state);
    elements.inspector.hidden = !paper;
    elements.inspectorToggle.disabled = !paper;
    elements.open.disabled = !paper;
    if (!paper) {
      for (const element of [
        elements.title, elements.titleZh, elements.meta, elements.status,
        elements.review, elements.note, elements.summary,
      ]) element.textContent = '';
      return;
    }
    const details = detailsFor(paper);
    elements.title.textContent = paper.title || paper.id;
    elements.titleZh.textContent = paper.title_zh || '';
    elements.titleZh.hidden = !paper.title_zh;
    elements.meta.textContent = [paper.venue, paper.year, paper.type, paper.topic].filter(Boolean).join(' · ');
    elements.status.textContent = paper.status || '未开始';
    elements.review.textContent = details.reviewText || '尚未安排';
    elements.note.textContent = details.noteText || (paper.hasNote ? '已有笔记' : '暂无笔记');
    elements.summary.textContent = paper.tldr || paper.contribution || paper.abstract || '打开阅读查看论文、讲解与笔记。';
  }

  function sameLayerWindow() {
    const renderedIds = Array.from(elements.layers.children)
      .map(node => String(node.dataset.spatialPaperId));
    const desiredIds = state.visiblePapers.map(paper => String(paper.id));
    return renderedIds.length === desiredIds.length
      && renderedIds.every((id, index) => id === desiredIds[index]);
  }

  function syncLayerPresentation() {
    for (const node of Array.from(elements.layers.children)) {
      const sourceIndex = state.papers.findIndex(
        paper => String(paper.id) === String(node.dataset.spatialPaperId),
      );
      const offset = Math.max(-4, Math.min(4, sourceIndex - state.selectedIndex));
      const selected = String(node.dataset.spatialPaperId) === String(state.selectedId);
      node.className = `spatial-layer layer-offset-${offset}`;
      node.setAttribute('aria-selected', String(selected));
      node.tabIndex = selected ? 0 : -1;
    }
  }

  function render({ focusLayer = false, preserveLayerNodes = false } = {}) {
    const done = state.papers.filter(paper => paper.status === '已理解').length;
    const learning = state.papers.filter(paper => paper.status === '学习中').length;
    const position = state.selectedIndex < 0 ? 0 : state.selectedIndex + 1;
    if (!preserveLayerNodes || !sameLayerWindow()) {
      elements.layers.replaceChildren(...state.visiblePapers.map(layerNode));
    }
    else syncLayerPresentation();
    elements.count.textContent = `共 ${state.total} 篇`;
    elements.position.textContent = `${position} / ${state.total}`;
    elements.queueTotal.textContent = String(state.total);
    elements.queueLearning.textContent = String(learning);
    elements.queueDone.textContent = String(done);
    elements.context.textContent = `已理解 ${done} · 学习中 ${learning} · 当前 ${position} / ${state.total}`;
    elements.empty.hidden = state.total > 0;
    elements.emptyText.textContent = state.total > 0 ? '' : emptyMessage;
    elements.previous.disabled = !state.canPrevious;
    elements.next.disabled = !state.canNext;
    renderDirections();
    renderInspector();
    if (focusLayer) {
      const selected = Array.from(elements.layers.children).find(node => node.getAttribute('aria-selected') === 'true');
      if (selected && typeof selected.focus === 'function') selected.focus();
    }
  }

  function getState() {
    return {
      ...state,
      papers: state.papers.slice(),
      visiblePapers: state.visiblePapers.slice(),
    };
  }

  function preferredKey(value) {
    return value == null ? null : String(value);
  }

  function hasPaper(papers, paperId) {
    return paperId != null
      && papers.some(paper => String(paper.id) === String(paperId));
  }

  function update(papers, options = {}) {
    emptyMessage = String(options.emptyMessage || '没有匹配的论文。');
    const nextPreferredKey = preferredKey(options.preferredId);
    if (!initialized) {
      state = createWorkspaceState(papers, options.preferredId);
      initialized = true;
      lastPreferredKey = nextPreferredKey;
      pendingPreferredId = hasPaper(state.papers, options.preferredId)
        ? null
        : options.preferredId;
    } else {
      state = reconcilePapers(state, papers);
      if (nextPreferredKey !== lastPreferredKey) {
        lastPreferredKey = nextPreferredKey;
        pendingPreferredId = options.preferredId;
      }
      if (hasPaper(state.papers, pendingPreferredId)) {
        state = selectPaper(state, pendingPreferredId);
        pendingPreferredId = null;
      }
    }
    render();
    return getState();
  }

  function select(paperId, options = {}) {
    const next = selectPaper(state, paperId);
    if (next === state) return getState();
    state = next;
    render({
      focusLayer: Boolean(options.focus),
      preserveLayerNodes: Boolean(options.preserveLayerNodes),
    });
    return getState();
  }

  function move(delta) {
    const next = moveSelection(state, delta);
    if (next === state || next.selectedId === state.selectedId) return getState();
    state = next;
    render({ focusLayer: true });
    return getState();
  }

  function openSelected() {
    const paper = selectedPaper(state);
    if (paper && typeof onOpen === 'function') onOpen(paper);
  }

  function paperButton(event) {
    const button = event && event.target && typeof event.target.closest === 'function'
      ? event.target.closest('[data-spatial-paper-id]')
      : null;
    return button && root.contains(button) ? button : null;
  }

  function bind() {
    if (bound) return;
    bound = true;
    root.addEventListener('click', event => {
      const button = paperButton(event);
      if (button) select(button.dataset.spatialPaperId, { focus: true, preserveLayerNodes: true });
    });
    root.addEventListener('dblclick', event => {
      const button = paperButton(event);
      if (!button) return;
      select(button.dataset.spatialPaperId, { preserveLayerNodes: true });
      openSelected();
    });
    root.addEventListener('keydown', event => {
      const button = paperButton(event);
      if (event.key === 'Escape') return;
      if (!button) return;
      if (event.key === 'Enter') {
        event.preventDefault();
        select(button.dataset.spatialPaperId, { preserveLayerNodes: true });
        openSelected();
      } else if (event.key === 'ArrowLeft') {
        event.preventDefault();
        select(button.dataset.spatialPaperId);
        move(-1);
      } else if (event.key === 'ArrowRight') {
        event.preventDefault();
        select(button.dataset.spatialPaperId);
        move(1);
      }
    });
    elements.previous.addEventListener('click', () => move(-1));
    elements.next.addEventListener('click', () => move(1));
    elements.open.addEventListener('click', openSelected);
    elements.clear.addEventListener('click', () => {
      if (typeof onClearFilters === 'function') onClearFilters();
    });
  }

  function refreshDetails() { renderInspector(); }

  return { bind, getState, move, openSelected, refreshDetails, select, update };
}
```

Replace the Task 3 module export object with:

```javascript
return {
  MAX_VISIBLE_LAYERS,
  createWorkspaceController,
  createWorkspaceState,
  moveSelection,
  reconcilePapers,
  selectPaper,
  selectedPaper,
};
```

The implementation uses only real paper records and DOM text, keeps preview selection independent from `current`, and keeps keyboard focus after layer nodes are recreated. A pointer selection already inside the five-item window updates the existing nodes in place; this preserves the browser's native click target long enough for a reliable `dblclick` event instead of replacing it after the first click.

- [ ] **Step 6: Run focused and protected integration tests**

Run separately:

```powershell
node --test test/spatial-workspace.test.js test/spatial-workspace-page-integration.test.js
node --test test/markdown-rendering-page-integration.test.js
```

Expected: PASS. The protected Markdown script trio remains adjacent.

- [ ] **Step 7: Commit semantic overview and controller**

```powershell
git add -- public/index.html public/style.css public/spatial-workspace.js test/spatial-workspace.test.js test/spatial-workspace-page-integration.test.js
git diff --cached --check
git commit -m "feat: add spatial paper overview"
```

---

### Task 5: Connect real filters, current paper, empty reset and review details

**Files:**
- Modify: `test/spatial-workspace-page-integration.test.js`
- Modify: `public/app.js:20-94,256-298,399-417,747-774,1296-1382`

- [ ] **Step 1: Add failing source-level integration assertions**

Append tests proving the integration boundary rather than implementation formatting:

```javascript
const app = fs.readFileSync(path.join(publicDir, 'app.js'), 'utf8');

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
```

- [ ] **Step 2: Verify RED**

Run:

```powershell
node --test test/spatial-workspace-page-integration.test.js
```

Expected: FAIL because `app.js` does not yet create or update the controller.

- [ ] **Step 3: Add the integration functions**

Add:

```javascript
let spatialWorkspace = null;

function clearHomeFilters() {
  yearFilter = 'all';
  favOnly = false;
  semActive = false;
  semRank = null;
  q = '';
  const search = $('#search');
  search.value = '';
  search.placeholder = '搜索…';
  $('#favFilter').classList.remove('on');
  $('#favFilter').textContent = '☆ 收藏';
  $('#semToggle').classList.remove('on');
  $('#semToggle').textContent = '🔮 语义';
  buildYearFilters();
  refresh();
  search.focus();
}

function spatialPaperDetails(paper) {
  if (!reviewData) {
    return {
      reviewText: '复习数据载入中…',
      noteText: paper && paper.hasNote ? '已有笔记' : '暂无笔记',
    };
  }
  const review = currentReviewItem(paper && paper.id);
  return {
    reviewText: review
      ? `第 ${review.current_step || 1}/${review.total_steps || 7} 轮 · ${dueText(review, reviewData && reviewData.today)}`
      : '尚未安排',
    noteText: paper && paper.hasNote ? '已有笔记' : '暂无笔记',
  };
}

function buildSpatialWorkspace() {
  if (spatialWorkspace || !window.SpatialWorkspace || !$('#spatialOverview')) return;
  spatialWorkspace = window.SpatialWorkspace.createWorkspaceController({
    root: $('#spatialOverview'),
    document,
    scrollContainer: $('#home'),
    onOpen: openPaper,
    onClearFilters: clearHomeFilters,
    getDetails: spatialPaperDetails,
  });
  spatialWorkspace.bind();
}
```

Replace the matching startup lines with:

```javascript
buildDashShell();
buildSpatialWorkspace();
renderHome();
```

- [ ] **Step 4: Feed the exact filtered list into the controller**

After `emptyMsg` is computed in `renderHome()` and after sorting has finished, add:

```javascript
if (spatialWorkspace) {
  spatialWorkspace.update(list, {
    preferredId: current && current.id,
    emptyMessage: emptyMsg,
  });
}
```

The controller treats a changed `preferredId` as a one-time external-current synchronization. An unchanged `preferredId` never overrides later spatial preview clicks; if a changed current is temporarily excluded by filters, the pending ID is applied once when that paper re-enters the exact list.

- [ ] **Step 5: Keep selection and review detail synchronized**

At the start of `openPaper(p)`, after the null guard and before changing views:

```javascript
spatialWorkspace?.select(p.id);
```

After `reviewData` is assigned in `loadReviews()`:

```javascript
spatialWorkspace?.refreshDetails();
```

Replace the final `init()` lines with this non-blocking first-paint order; completion updates only review-derived details:

```javascript
showView('home');
appReady = true;
void loadReviews(false);
```

- [ ] **Step 6: Verify behavior contracts and full regression**

Run separately:

```powershell
node --test test/spatial-workspace.test.js test/spatial-workspace-page-integration.test.js
npm test
```

Expected: PASS with no new API calls during appearance switching and no duplicate `openPaper` workflow.

- [ ] **Step 7: Commit the real-data integration**

```powershell
git add -- public/app.js test/spatial-workspace-page-integration.test.js
git diff --cached --check
git commit -m "feat: connect spatial overview to paper data"
```

---

### Task 6: Desktop spatial visual system across all pages

**Files:**
- Create: `test/spatial-style-contract.test.js`
- Modify: `public/spatial.css`

- [ ] **Step 1: Write failing CSS contract tests**

Create `test/spatial-style-contract.test.js`:

```javascript
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');

const css = fs.readFileSync(path.resolve(__dirname, '..', 'public', 'spatial.css'), 'utf8');

function selectorPreludes(source) {
  const clean = source.replace(/\/\*[\s\S]*?\*\//g, '');
  const selectors = [];
  let tokenStart = 0;
  for (let index = 0; index < clean.length; index += 1) {
    const character = clean[index];
    if (character === '{') {
      const prelude = clean.slice(tokenStart, index).trim();
      if (prelude && !prelude.startsWith('@')) {
        selectors.push(...prelude.split(',').map(selector => selector.trim()).filter(Boolean));
      }
      tokenStart = index + 1;
    } else if (character === '}') {
      tokenStart = index + 1;
    }
  }
  return selectors;
}

function atRuleBody(source, header) {
  const headerIndex = source.indexOf(header);
  assert.ok(headerIndex >= 0, `missing ${header}`);
  const open = source.indexOf('{', headerIndex);
  let depth = 1;
  for (let index = open + 1; index < source.length; index += 1) {
    if (source[index] === '{') depth += 1;
    if (source[index] === '}') depth -= 1;
    if (depth === 0) return source.slice(open + 1, index);
  }
  assert.fail(`unclosed ${header}`);
}

function ruleBody(source, selector) {
  const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  const match = source.match(new RegExp(`${escaped}\\s*\\{([^}]*)\\}`));
  assert.ok(match, `missing rule ${selector}`);
  return match[1];
}

function themeHex(theme, token) {
  const block = css.match(new RegExp(`data-theme="${theme}"[^\\{]*\\{([^}]*)\\}`, 's'));
  assert.ok(block, `missing ${theme} theme block`);
  const value = block[1].match(new RegExp(`${token}:\\s*(#[0-9a-f]{6})`, 'i'));
  assert.ok(value, `missing ${token} in ${theme}`);
  return value[1];
}

function relativeLuminance(hex) {
  const channels = hex.slice(1).match(/../g).map(channel => parseInt(channel, 16) / 255);
  const linear = channels.map(channel => (
    channel <= 0.04045 ? channel / 12.92 : ((channel + 0.055) / 1.055) ** 2.4
  ));
  return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2];
}

function contrastRatio(first, second) {
  const a = relativeLuminance(first);
  const b = relativeLuminance(second);
  return (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05);
}

test('every spatial selector is positively scoped', () => {
  const selectors = selectorPreludes(css);
  assert.ok(selectors.length > 25);
  for (const selector of selectors) {
    assert.ok(
      selector.startsWith('html[data-ui-style="spatial"]'),
      `unscoped selector: ${selector}`,
    );
  }
  assert.doesNotMatch(css, /(^|\n)\s*:root\b/);
});

test('dark and light themes have emerald role ledgers', () => {
  assert.match(css, /data-theme="dark"[^\{]*\{[^}]*--sp-accent:\s*#2fe586/is);
  assert.match(css, /data-theme="light"[^\{]*\{[^}]*--sp-accent:\s*#08944f/is);
  for (const token of ['--sp-bg', '--sp-surface', '--sp-text', '--sp-muted', '--sp-border', '--sp-accent-fg', '--sp-danger', '--sp-warning']) {
    assert.ok(css.includes(token), `missing ${token}`);
  }
  assert.match(css, /--primary:\s*var\(--sp-accent-strong\)/);
  assert.match(css, /--ink:\s*var\(--sp-text\)/);
  for (const bridge of [
    '--accent', '--accent-ink', '--accent-soft', '--primary-2', '--ring',
    '--ok-soft', '--warn-soft', '--idle-soft', '--pdf-stage', '--star',
  ]) assert.ok(css.includes(`${bridge}:`), `missing legacy bridge ${bridge}`);
  assert.match(css, /--accent:\s*var\(--sp-accent-fg\)/);
  assert.match(css, /--accent-ink:\s*var\(--sp-accent-fg\)/);
  assert.match(css, /--ok:\s*var\(--sp-accent-fg\)/);
  assert.doesNotMatch(css, /--accent-ink:\s*var\(--sp-accent-ink\)/);
  assert.match(css, /--ring:\s*0 0 0 3px var\(--sp-accent\)/);
  assert.doesNotMatch(css, /(?:^|\n)\s*color:\s*var\(--sp-accent\)\s*;/);
  for (const theme of ['dark', 'light']) {
    assert.ok(
      contrastRatio(themeHex(theme, '--sp-accent'), themeHex(theme, '--sp-accent-ink')) >= 4.5,
      `${theme} filled emerald controls must meet normal-text contrast`,
    );
    assert.ok(
      contrastRatio(themeHex(theme, '--sp-accent-fg'), themeHex(theme, '--sp-surface-solid')) >= 4.5,
      `${theme} emerald foreground must meet normal-text contrast`,
    );
    assert.ok(
      contrastRatio(themeHex(theme, '--sp-accent-strong'), '#ffffff') >= 4.5,
      `${theme} legacy primary fills must remain readable with existing white text`,
    );
    for (const adjacent of ['--sp-bg', '--sp-surface-solid']) {
      assert.ok(
        contrastRatio(themeHex(theme, '--sp-accent'), themeHex(theme, adjacent)) >= 3,
        `${theme} focus and selection accent must remain visible against ${adjacent}`,
      );
    }
    assert.ok(
      contrastRatio(themeHex(theme, '--sp-border-strong'), themeHex(theme, '--sp-surface-solid')) >= 3,
      `${theme} strong control boundaries must meet non-text contrast`,
    );
  }
});

test('spatial styling covers every existing global work surface', () => {
  for (const selector of [
    '#rail', '#topbar', '#home', '#layout', '#review', '#manage', '#jobs',
    '#insights', '#settingsModal', '.table-card', '.modal-card', 'input', 'button',
  ]) assert.ok(css.includes(selector), `missing spatial treatment for ${selector}`);
  for (const selector of [
    'html[data-ui-style="spatial"] .paper-item.active .order-badge',
    'html[data-ui-style="spatial"] .panel-stub:hover .stub-ico',
  ]) {
    const declarations = ruleBody(css, selector);
    assert.match(declarations, /background:\s*var\(--sp-accent-strong\)/);
    assert.match(declarations, /color:\s*#fff/);
  }
});

test('the data stage has semantic depth without forbidden renderers', () => {
  assert.match(css, /\.spatial-layer/);
  assert.match(css, /\.layer-offset--2/);
  assert.match(css, /\.layer-offset-2/);
  assert.match(css, /\.layer-offset--4/);
  assert.match(css, /\.layer-offset-4/);
  assert.match(ruleBody(css, 'html[data-ui-style="spatial"] .spatial-layer-status'), /color:\s*var\(--sp-muted\)/);
  assert.match(ruleBody(css, 'html[data-ui-style="spatial"] .spatial-layer[aria-selected="true"] .spatial-layer-status'), /color:\s*var\(--sp-accent-fg\)/);
  assert.doesNotMatch(css, /@keyframes|canvas|webgl|three\.js|url\s*\(/i);
});
```

- [ ] **Step 2: Verify RED**

Run:

```powershell
node --test test/spatial-style-contract.test.js
```

Expected: FAIL because only the minimal `color-scheme` rules exist.

- [ ] **Step 3: Add paired design tokens and global shell styling**

Expand `public/spatial.css` using one selector prelude per line so the scope test remains meaningful. Define exact color roles:

```css
html[data-ui-style="spatial"][data-theme="dark"] {
  --sp-bg: #050706;
  --sp-bg-raised: #0b100d;
  --sp-surface: rgba(16, 24, 19, 0.78);
  --sp-surface-solid: #101713;
  --sp-surface-muted: #18211c;
  --sp-text: #f0f7f3;
  --sp-muted: #8fa49a;
  --sp-border: rgba(190, 226, 207, 0.16);
  --sp-border-strong: #56685e;
  --sp-highlight: rgba(255, 255, 255, 0.09);
  --sp-accent: #2fe586;
  --sp-accent-strong: #087a45;
  --sp-accent-fg: #2fe586;
  --sp-accent-ink: #031b0e;
  --sp-accent-soft: rgba(47, 229, 134, 0.14);
  --sp-danger: #ff6b76;
  --sp-danger-soft: rgba(255, 107, 118, 0.14);
  --sp-warning: #e8b85a;
  --sp-warning-soft: rgba(232, 184, 90, 0.14);
  --sp-idle-soft: rgba(143, 164, 154, 0.14);
  color-scheme: dark;
}

html[data-ui-style="spatial"][data-theme="light"] {
  --sp-bg: #edf2ef;
  --sp-bg-raised: #f7faf8;
  --sp-surface: rgba(255, 255, 255, 0.78);
  --sp-surface-solid: #f8fbf9;
  --sp-surface-muted: #dde7e1;
  --sp-text: #14211a;
  --sp-muted: #64766d;
  --sp-border: rgba(32, 73, 51, 0.16);
  --sp-border-strong: #769082;
  --sp-highlight: rgba(255, 255, 255, 0.8);
  --sp-accent: #08944f;
  --sp-accent-strong: #067a45;
  --sp-accent-fg: #067a45;
  --sp-accent-ink: #031b0e;
  --sp-accent-soft: rgba(8, 148, 79, 0.12);
  --sp-danger: #c83c49;
  --sp-danger-soft: rgba(200, 60, 73, 0.12);
  --sp-warning: #9b681a;
  --sp-warning-soft: rgba(155, 104, 26, 0.12);
  --sp-idle-soft: rgba(100, 118, 109, 0.12);
  color-scheme: light;
}

html[data-ui-style="spatial"] {
  --bg: var(--sp-bg);
  --surface: var(--sp-surface);
  --surface-2: var(--sp-surface-solid);
  --surface-3: var(--sp-surface-muted);
  --ink: var(--sp-text);
  --ink-2: var(--sp-muted);
  --ink-3: var(--sp-muted);
  --border: var(--sp-border);
  --border-2: var(--sp-border-strong);
  --primary: var(--sp-accent-strong);
  --primary-2: var(--sp-accent-strong);
  --accent: var(--sp-accent-fg);
  --accent-ink: var(--sp-accent-fg);
  --accent-soft: var(--sp-accent-soft);
  --ring: 0 0 0 3px var(--sp-accent);
  --ok: var(--sp-accent-fg);
  --ok-soft: var(--sp-accent-soft);
  --warn: var(--sp-warning);
  --warn-soft: var(--sp-warning-soft);
  --idle: var(--sp-muted);
  --idle-soft: var(--sp-idle-soft);
  --star: var(--sp-warning);
  --pdf-stage: #2a2e2c;
  --shadow: 0 20px 48px rgba(0, 12, 6, 0.2);
  --shadow-sm: 0 8px 20px rgba(0, 12, 6, 0.14);
  --card-shadow: 0 12px 28px rgba(0, 12, 6, 0.12);
}
```

Append the exact global shell and control rules below. Every selector is positively scoped, while warning/error roles remain distinct from emerald selection:

```css
html[data-ui-style="spatial"] body {
  background: var(--sp-bg);
  color: var(--sp-text);
}

html[data-ui-style="spatial"] #rail {
  width: 82px;
  padding: 14px 8px;
  gap: 8px;
  background: var(--sp-surface);
  border-right: 1px solid var(--sp-border);
  box-shadow: inset -1px 0 var(--sp-highlight);
  -webkit-backdrop-filter: blur(20px) saturate(125%);
  backdrop-filter: blur(20px) saturate(125%);
}

html[data-ui-style="spatial"] .rail-brand .logo {
  background: var(--sp-accent);
  border: 1px solid color-mix(in srgb, var(--sp-accent) 70%, white);
  color: var(--sp-accent-ink);
  box-shadow: 0 0 0 3px var(--sp-accent-soft);
}

html[data-ui-style="spatial"] .viewnav button,
html[data-ui-style="spatial"] .rail-act {
  border: 1px solid transparent;
  border-radius: 10px;
  color: var(--sp-muted);
  background: transparent;
}

html[data-ui-style="spatial"] .viewnav button:hover,
html[data-ui-style="spatial"] .rail-act:hover {
  border-color: var(--sp-border);
  background: var(--sp-surface-muted);
  color: var(--sp-text);
}

html[data-ui-style="spatial"] .viewnav button.active {
  border-color: color-mix(in srgb, var(--sp-accent) 46%, var(--sp-border));
  background: var(--sp-accent-soft);
  color: var(--sp-accent-fg);
}

html[data-ui-style="spatial"] .viewnav button.active::before {
  display: block;
  width: 2px;
  background: var(--sp-accent);
}

html[data-ui-style="spatial"] #shell,
html[data-ui-style="spatial"] #viewstack {
  background: var(--sp-bg);
}

html[data-ui-style="spatial"] #topbar {
  min-height: 60px;
  height: auto;
  padding: 8px 22px;
  background: var(--sp-surface);
  border-bottom: 1px solid var(--sp-border);
  box-shadow: inset 0 -1px var(--sp-highlight);
  -webkit-backdrop-filter: blur(20px) saturate(125%);
  backdrop-filter: blur(20px) saturate(125%);
}

html[data-ui-style="spatial"] .brand-title,
html[data-ui-style="spatial"] .page-head h1 {
  color: var(--sp-text);
}

html[data-ui-style="spatial"] .brand-sub,
html[data-ui-style="spatial"] .page-head em,
html[data-ui-style="spatial"] .summary {
  color: var(--sp-muted);
}

html[data-ui-style="spatial"] #home,
html[data-ui-style="spatial"] #review,
html[data-ui-style="spatial"] #manage,
html[data-ui-style="spatial"] #jobs,
html[data-ui-style="spatial"] #insights {
  max-width: none;
  padding: 22px 26px 46px;
  background: var(--sp-bg);
}

html[data-ui-style="spatial"] input,
html[data-ui-style="spatial"] select,
html[data-ui-style="spatial"] textarea {
  border-color: var(--sp-border-strong);
  background: var(--sp-surface-solid);
  color: var(--sp-text);
  color-scheme: inherit;
}

html[data-ui-style="spatial"] input::placeholder,
html[data-ui-style="spatial"] textarea::placeholder {
  color: var(--sp-muted);
}

html[data-ui-style="spatial"] button,
html[data-ui-style="spatial"] .chip-btn,
html[data-ui-style="spatial"] .year-select {
  border-color: var(--sp-border-strong);
}

html[data-ui-style="spatial"] .chip-btn,
html[data-ui-style="spatial"] .year-select,
html[data-ui-style="spatial"] .seg button,
html[data-ui-style="spatial"] .tbtn,
html[data-ui-style="spatial"] .mini.ghost {
  background: var(--sp-surface-solid);
  color: var(--sp-muted);
}

html[data-ui-style="spatial"] .chip-btn:hover,
html[data-ui-style="spatial"] .year-select:hover,
html[data-ui-style="spatial"] .seg button:hover,
html[data-ui-style="spatial"] .tbtn:hover,
html[data-ui-style="spatial"] .mini.ghost:hover {
  border-color: var(--sp-accent);
  color: var(--sp-text);
}

html[data-ui-style="spatial"] .chip-btn.active,
html[data-ui-style="spatial"] .sem-chip.on,
html[data-ui-style="spatial"] .fav-chip.on,
html[data-ui-style="spatial"] .seg button.active {
  border-color: color-mix(in srgb, var(--sp-accent) 52%, var(--sp-border));
  background: var(--sp-accent-soft);
  color: var(--sp-accent-fg);
}

html[data-ui-style="spatial"] .mini.primary,
html[data-ui-style="spatial"] #spatialOpen,
html[data-ui-style="spatial"] #spatialClearFilters {
  border-color: var(--sp-accent);
  background: var(--sp-accent);
  color: var(--sp-accent-ink);
}

html[data-ui-style="spatial"] .paper-item.active .order-badge {
  background: var(--sp-accent-strong);
  color: #fff;
}

html[data-ui-style="spatial"] .panel-stub:hover .stub-ico {
  background: var(--sp-accent-strong);
  color: #fff;
}

html[data-ui-style="spatial"] .progress,
html[data-ui-style="spatial"] .eb-bar {
  background: var(--sp-surface-muted);
}

html[data-ui-style="spatial"] #progressBar,
html[data-ui-style="spatial"] .eb-bar-fill {
  background: var(--sp-accent);
}

html[data-ui-style="spatial"] :focus-visible {
  outline: 2px solid var(--sp-accent);
  outline-offset: 3px;
  box-shadow: 0 0 0 1px var(--sp-bg);
}

html[data-ui-style="spatial"] button:disabled,
html[data-ui-style="spatial"] input:disabled,
html[data-ui-style="spatial"] select:disabled {
  cursor: not-allowed;
  opacity: 0.48;
}
```

- [ ] **Step 4: Implement the desktop “空间研究台” composition**

Use:

```css
html[data-ui-style="spatial"] .spatial-overview {
  display: grid;
  grid-template-columns: minmax(190px, 0.72fr) minmax(420px, 1.8fr) minmax(240px, 0.9fr);
  min-height: 590px;
  gap: 14px;
  margin-bottom: 18px;
  color: var(--sp-text);
}

html[data-ui-style="spatial"] .spatial-queue,
html[data-ui-style="spatial"] .spatial-stage,
html[data-ui-style="spatial"] .spatial-inspector {
  border: 1px solid var(--sp-border);
  border-radius: 16px;
  background: var(--sp-surface);
  box-shadow: inset 0 1px var(--sp-highlight);
  -webkit-backdrop-filter: blur(18px) saturate(125%);
  backdrop-filter: blur(18px) saturate(125%);
}

html[data-ui-style="spatial"] .spatial-queue,
html[data-ui-style="spatial"] .spatial-inspector {
  min-width: 0;
  padding: 20px;
  overflow: auto;
}

html[data-ui-style="spatial"] .spatial-stage {
  min-width: 0;
  padding: 18px;
  overflow: hidden;
}

html[data-ui-style="spatial"] .spatial-mobile-tools,
html[data-ui-style="spatial"] .spatial-panel-close,
html[data-ui-style="spatial"] .spatial-scrim {
  display: none;
}

html[data-ui-style="spatial"] .spatial-kicker {
  margin-bottom: 8px;
  color: var(--sp-accent-fg);
  font-family: var(--font-mono);
  font-size: 10px;
  letter-spacing: 0.16em;
}

html[data-ui-style="spatial"] .spatial-queue h2,
html[data-ui-style="spatial"] .spatial-inspector h3 {
  margin: 0;
  color: var(--sp-text);
  line-height: 1.28;
}

html[data-ui-style="spatial"] .spatial-queue-counts {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 6px;
  margin: 20px 0;
}

html[data-ui-style="spatial"] .spatial-queue-counts span {
  min-width: 0;
  padding: 9px 7px;
  border: 1px solid var(--sp-border);
  border-radius: 9px;
  background: var(--sp-surface-solid);
  color: var(--sp-muted);
  font-size: 10px;
  text-align: center;
}

html[data-ui-style="spatial"] .spatial-queue-counts b {
  display: block;
  margin-bottom: 3px;
  color: var(--sp-text);
  font-family: var(--font-mono);
  font-size: 18px;
}

html[data-ui-style="spatial"] .spatial-direction-block {
  padding-top: 16px;
  border-top: 1px solid var(--sp-border);
}

html[data-ui-style="spatial"] .spatial-direction-block h3 {
  margin: 0 0 10px;
  color: var(--sp-muted);
  font-size: 11px;
}

html[data-ui-style="spatial"] #spatialDirections {
  display: grid;
  gap: 7px;
  margin: 0;
  padding: 0;
  list-style: none;
}

html[data-ui-style="spatial"] .spatial-direction {
  display: flex;
  justify-content: space-between;
  padding: 8px 10px;
  border-left: 2px solid var(--sp-accent);
  background: var(--sp-accent-soft);
  color: var(--sp-text);
  font-size: 12px;
}

html[data-ui-style="spatial"] .spatial-stage-head,
html[data-ui-style="spatial"] .spatial-stage-nav {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

html[data-ui-style="spatial"] .spatial-stage-head > div {
  display: flex;
  align-items: baseline;
  gap: 10px;
  color: var(--sp-muted);
}

html[data-ui-style="spatial"] #spatialPosition {
  color: var(--sp-accent-fg);
  font-family: var(--font-mono);
  font-size: 18px;
}

html[data-ui-style="spatial"] .spatial-data-link {
  color: var(--sp-accent-fg);
  text-decoration: none;
}

html[data-ui-style="spatial"] .spatial-layers {
  position: relative;
  min-height: 420px;
  perspective: 1200px;
}

html[data-ui-style="spatial"] .spatial-layer {
  position: absolute;
  inset: 50% 8% auto;
  min-height: 230px;
  padding: 28px;
  border: 1px solid var(--sp-border);
  border-radius: 14px;
  background: var(--sp-surface-solid);
  color: var(--sp-text);
  text-align: left;
  overflow: hidden;
  transform: translateY(-50%);
  transition: transform 180ms ease, opacity 180ms ease, border-color 180ms ease;
}

html[data-ui-style="spatial"] .spatial-layer[aria-selected="true"] {
  z-index: 5;
  border-color: var(--sp-accent);
  box-shadow: 0 0 0 1px var(--sp-accent-soft), inset 0 1px var(--sp-highlight);
}

html[data-ui-style="spatial"] .spatial-layer-index,
html[data-ui-style="spatial"] .spatial-layer-meta,
html[data-ui-style="spatial"] .spatial-layer-status {
  display: block;
  color: var(--sp-muted);
  font-family: var(--font-mono);
  font-size: 10px;
  letter-spacing: 0.06em;
}

html[data-ui-style="spatial"] .spatial-layer-title {
  display: block;
  max-width: 36ch;
  margin: 18px 0;
  color: var(--sp-text);
  font-size: clamp(18px, 2vw, 28px);
  line-height: 1.22;
}

html[data-ui-style="spatial"] .spatial-layer-status {
  margin-top: 12px;
  color: var(--sp-muted);
}

html[data-ui-style="spatial"] .spatial-layer[aria-selected="true"] .spatial-layer-status {
  color: var(--sp-accent-fg);
}

html[data-ui-style="spatial"] .spatial-stage-nav button {
  min-height: 36px;
  padding: 7px 14px;
  border: 1px solid var(--sp-border-strong);
  border-radius: 9px;
  background: var(--sp-surface-solid);
  color: var(--sp-text);
}

html[data-ui-style="spatial"] .spatial-context {
  margin-top: 12px;
  padding-top: 12px;
  border-top: 1px solid var(--sp-border);
  color: var(--sp-muted);
  font-family: var(--font-mono);
  font-size: 11px;
}

html[data-ui-style="spatial"] .spatial-empty {
  min-height: 420px;
  place-content: center;
  gap: 10px;
  text-align: center;
}

html[data-ui-style="spatial"] .spatial-empty:not([hidden]) {
  display: grid;
}

html[data-ui-style="spatial"] .spatial-empty span {
  color: var(--sp-muted);
}

html[data-ui-style="spatial"] .spatial-title-zh,
html[data-ui-style="spatial"] .spatial-summary {
  color: var(--sp-muted);
  line-height: 1.55;
}

html[data-ui-style="spatial"] .spatial-meta {
  display: grid;
  gap: 0;
  margin: 20px 0;
}

html[data-ui-style="spatial"] .spatial-meta > div {
  display: grid;
  grid-template-columns: 48px 1fr;
  gap: 8px;
  padding: 9px 0;
  border-bottom: 1px solid var(--sp-border);
}

html[data-ui-style="spatial"] .spatial-meta dt {
  color: var(--sp-muted);
  font-size: 11px;
}

html[data-ui-style="spatial"] .spatial-meta dd {
  margin: 0;
  color: var(--sp-text);
  font-size: 12px;
}

html[data-ui-style="spatial"] #spatialOpen {
  width: 100%;
  min-height: 40px;
  border-radius: 10px;
}

html[data-ui-style="spatial"] .layer-offset--4 { transform: translate(-12%, -72%) scale(0.82); opacity: 0.22; z-index: 1; }
html[data-ui-style="spatial"] .layer-offset--3 { transform: translate(-9.5%, -67%) scale(0.86); opacity: 0.32; z-index: 1; }
html[data-ui-style="spatial"] .layer-offset--2 { transform: translate(-7%, -62%) scale(0.9); opacity: 0.44; z-index: 2; }
html[data-ui-style="spatial"] .layer-offset--1 { transform: translate(-3.5%, -56%) scale(0.95); opacity: 0.68; z-index: 2; }
html[data-ui-style="spatial"] .layer-offset-0 { transform: translate(0, -50%) scale(1); opacity: 1; z-index: 5; }
html[data-ui-style="spatial"] .layer-offset-1 { transform: translate(3.5%, -44%) scale(0.95); opacity: 0.68; z-index: 2; }
html[data-ui-style="spatial"] .layer-offset-2 { transform: translate(7%, -38%) scale(0.9); opacity: 0.44; z-index: 2; }
html[data-ui-style="spatial"] .layer-offset-3 { transform: translate(9.5%, -33%) scale(0.86); opacity: 0.32; z-index: 1; }
html[data-ui-style="spatial"] .layer-offset-4 { transform: translate(12%, -28%) scale(0.82); opacity: 0.22; z-index: 1; }
```

Keep all titles, counts and status labels as live DOM text. Do not use generated atmospheric images or decorative animated glows.

- [ ] **Step 5: Style the existing precise data and all non-home pages**

Append this complete cross-page block. It keeps the existing charts and exact table in normal flow, retains the resizable reader, and assigns warning/error surfaces explicitly:

```css
html[data-ui-style="spatial"] #dash,
html[data-ui-style="spatial"] #explainBatch,
html[data-ui-style="spatial"] .table-card {
  position: relative;
  margin-top: 16px;
}

html[data-ui-style="spatial"] .chart-card,
html[data-ui-style="spatial"] .table-card,
html[data-ui-style="spatial"] .ebatch,
html[data-ui-style="spatial"] .review-stat,
html[data-ui-style="spatial"] .review-card,
html[data-ui-style="spatial"] .ingest-bar,
html[data-ui-style="spatial"] .lib-card,
html[data-ui-style="spatial"] .m-card,
html[data-ui-style="spatial"] .job-card,
html[data-ui-style="spatial"] .set-card,
html[data-ui-style="spatial"] .modal-card {
  border: 1px solid var(--sp-border);
  background: var(--sp-surface);
  color: var(--sp-text);
  box-shadow: inset 0 1px var(--sp-highlight), var(--shadow-sm);
  -webkit-backdrop-filter: blur(14px) saturate(120%);
  backdrop-filter: blur(14px) saturate(120%);
}

html[data-ui-style="spatial"] .chart-card.kpi {
  border-color: color-mix(in srgb, var(--sp-accent) 36%, var(--sp-border));
  background: var(--sp-surface-solid);
  color: var(--sp-text);
}

html[data-ui-style="spatial"] .chart-card.kpi::after,
html[data-ui-style="spatial"] .kpi-big::after {
  display: none;
}

html[data-ui-style="spatial"] .kpi-big,
html[data-ui-style="spatial"] .kpi-rows b {
  color: var(--sp-accent-fg);
}

html[data-ui-style="spatial"] .kpi-sub,
html[data-ui-style="spatial"] .kpi-rows > div,
html[data-ui-style="spatial"] .chart-title {
  color: var(--sp-muted);
}

html[data-ui-style="spatial"] .kpi-rows > div {
  border-top-color: var(--sp-border);
}

html[data-ui-style="spatial"] .chart-title::before,
html[data-ui-style="spatial"] .ib-title::before,
html[data-ui-style="spatial"] .lib-title::before,
html[data-ui-style="spatial"] .settings-sub::before {
  background: var(--sp-accent);
}

html[data-ui-style="spatial"] .table-card {
  overflow-x: auto;
  border-radius: 12px;
}

html[data-ui-style="spatial"] #homeTable {
  background: transparent;
  color: var(--sp-text);
}

html[data-ui-style="spatial"] #homeTable thead th {
  top: 0;
  border-bottom-color: var(--sp-border-strong);
  background: var(--sp-surface-solid);
  color: var(--sp-muted);
}

html[data-ui-style="spatial"] #homeTable tbody td {
  border-bottom-color: var(--sp-border);
  color: var(--sp-text);
}

html[data-ui-style="spatial"] #homeTable tbody tr:hover {
  background: var(--sp-accent-soft);
}

html[data-ui-style="spatial"] #homeTable tbody tr:hover .ht-title,
html[data-ui-style="spatial"] #homeTable tbody tr:hover .ht-idx {
  color: var(--sp-accent-fg);
}

html[data-ui-style="spatial"] #layout {
  background: var(--sp-bg);
}

html[data-ui-style="spatial"] #sidebar,
html[data-ui-style="spatial"] #panel {
  border-color: var(--sp-border);
  background: var(--sp-surface);
  color: var(--sp-text);
  box-shadow: inset 0 1px var(--sp-highlight);
}

html[data-ui-style="spatial"] #sideFilter,
html[data-ui-style="spatial"] #pdfBar,
html[data-ui-style="spatial"] #panelHead {
  border-color: var(--sp-border);
  background: var(--sp-surface-solid);
}

html[data-ui-style="spatial"] #viewer {
  background: var(--pdf-stage);
}

html[data-ui-style="spatial"] #pdfScroll {
  background: var(--pdf-stage);
}

html[data-ui-style="spatial"] .paper-item.active,
html[data-ui-style="spatial"] .tab.active,
html[data-ui-style="spatial"] .review-card.dueToday {
  border-color: var(--sp-accent);
  background: var(--sp-accent-soft);
  color: var(--sp-accent-fg);
}

html[data-ui-style="spatial"] .review-card.overdue {
  border-left-color: var(--sp-warning);
}

html[data-ui-style="spatial"] .review-error {
  border-color: var(--sp-danger);
  background: var(--sp-danger-soft);
  color: var(--sp-danger);
}

html[data-ui-style="spatial"] .review-group-head,
html[data-ui-style="spatial"] .review-title,
html[data-ui-style="spatial"] .job-q,
html[data-ui-style="spatial"] .m-item-title {
  color: var(--sp-text);
}

html[data-ui-style="spatial"] .review-meta,
html[data-ui-style="spatial"] .job-meta,
html[data-ui-style="spatial"] .job-stats,
html[data-ui-style="spatial"] .ib-tip,
html[data-ui-style="spatial"] .lib-meta {
  color: var(--sp-muted);
}

html[data-ui-style="spatial"] .job-card[data-status="review"] {
  border-color: color-mix(in srgb, var(--sp-warning) 44%, var(--sp-border));
}

html[data-ui-style="spatial"] .job-badge.st-pending {
  background: var(--sp-idle-soft);
  color: var(--sp-muted);
}

html[data-ui-style="spatial"] .job-badge.st-running,
html[data-ui-style="spatial"] .job-badge.st-done {
  background: var(--sp-accent-soft);
  color: var(--sp-accent-fg);
}

html[data-ui-style="spatial"] .job-badge.st-review {
  background: var(--sp-warning-soft);
  color: var(--sp-warning);
}

html[data-ui-style="spatial"] .job-badge.st-failed {
  background: var(--sp-danger-soft);
  color: var(--sp-danger);
}

html[data-ui-style="spatial"] #insights .chart-card,
html[data-ui-style="spatial"] #manage .ingest-bar,
html[data-ui-style="spatial"] #manage .lib-card,
html[data-ui-style="spatial"] #jobs .ingest-bar {
  border-radius: 12px;
}

html[data-ui-style="spatial"] .modal {
  background: color-mix(in srgb, var(--sp-bg) 76%, transparent);
  -webkit-backdrop-filter: blur(9px);
  backdrop-filter: blur(9px);
}

html[data-ui-style="spatial"] #settingsModal .modal-card {
  background: var(--sp-surface-solid);
}

html[data-ui-style="spatial"] #settingsModal .modal-head,
html[data-ui-style="spatial"] #settingsModal .modal-foot {
  border-color: var(--sp-border);
  background: var(--sp-surface);
}

html[data-ui-style="spatial"] .appearance-card {
  display: grid;
  gap: 14px;
}

html[data-ui-style="spatial"] .appearance-row {
  display: grid;
  grid-template-columns: minmax(110px, 0.4fr) minmax(0, 1fr);
  align-items: center;
  gap: 12px;
}

html[data-ui-style="spatial"] .appearance-label {
  color: var(--sp-muted);
  font-size: 12px;
}

html[data-ui-style="spatial"] .appearance-options {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 8px;
}

html[data-ui-style="spatial"] .appearance-options label {
  display: flex;
  align-items: center;
  gap: 8px;
  min-height: 38px;
  padding: 7px 10px;
  border: 1px solid var(--sp-border);
  border-radius: 9px;
  background: var(--sp-surface);
  color: var(--sp-text);
}

html[data-ui-style="spatial"] .appearance-options label:has(input:checked) {
  border-color: var(--sp-accent);
  background: var(--sp-accent-soft);
}
```

- [ ] **Step 6: Verify the desktop style contract and full suite**

Run separately:

```powershell
node --test test/spatial-style-contract.test.js
npm test
```

Expected: PASS; no unscoped selector is reported.

- [ ] **Step 7: Commit the desktop visual system**

```powershell
git add -- public/spatial.css test/spatial-style-contract.test.js
git diff --cached --check
git commit -m "feat: style spatial research workspace"
```

---

### Task 7: Mobile drawers, accessibility and capability fallbacks

**Files:**
- Modify: `test/spatial-workspace.test.js`
- Modify: `test/spatial-workspace-page-integration.test.js`
- Modify: `test/spatial-style-contract.test.js`
- Modify: `public/app.js`
- Modify: `public/spatial-workspace.js`
- Modify: `public/spatial.css`

- [ ] **Step 1: Add failing mobile controller tests**

Extend the controller harness to expose queue/inspector toggles, scrim, the real home scroll container and focus. Assert:

```javascript
test('mobile panels are mutually exclusive and restore trigger focus', () => {
  const harness = createWorkspaceHarness();
  const controller = require(workspacePath).createWorkspaceController(harness.options);
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

test('a desktop breakpoint transition closes transient panels', () => {
  const harness = createWorkspaceHarness({ scrollTop: 210 });
  const controller = require(workspacePath).createWorkspaceController(harness.options);
  controller.bind();
  controller.openPanel('queue');
  harness.scrollContainer.scrollTop = 25;
  harness.desktopMedia.dispatch(true);
  assert.equal(harness.root.classList.contains('is-queue-open'), false);
  assert.equal(harness.document.documentElement.classList.contains('spatial-queue-open'), false);
  assert.equal(harness.queueToggle.attributes['aria-expanded'], 'false');
  assert.equal(harness.scrim.hidden, true);
  assert.equal(harness.scrollContainer.scrollTop, harness.initialScrollTop);
  assert.equal(harness.filterActions.parentNode, harness.filterHome);
  assert.equal(harness.queueToggle.focusCalls, 0);
});

test('leaving the mobile breakpoint closes the queue before its toggle disappears', () => {
  const harness = createWorkspaceHarness({ scrollTop: 190 });
  const controller = require(workspacePath).createWorkspaceController(harness.options);
  controller.bind();
  controller.openPanel('queue');
  harness.scrollContainer.scrollTop = 30;
  harness.mobileMedia.dispatch(false);
  assert.equal(harness.root.classList.contains('is-queue-open'), false);
  assert.equal(harness.scrim.hidden, true);
  assert.equal(harness.filterActions.parentNode, harness.filterHome);
  assert.equal(harness.scrollContainer.scrollTop, 190);
  assert.equal(harness.queueToggle.focusCalls, 0);
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
  harness.root.classList.add('is-queue-open');
  harness.document.documentElement.classList.add('spatial-queue-open');
  harness.queueToggle.setAttribute('aria-expanded', 'true');
  harness.scrim.hidden = false;
  controller.closePanels({ restoreFocus: false });
  assert.equal(harness.root.classList.contains('is-queue-open'), false);
  assert.equal(harness.document.documentElement.classList.contains('spatial-queue-open'), false);
  assert.equal(harness.queueToggle.attributes['aria-expanded'], 'false');
  assert.equal(harness.scrim.hidden, true);
  assert.equal(harness.scrollContainer.scrollTop, 180);
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
```

- [ ] **Step 2: Add failing responsive and fallback CSS assertions**

Append:

```javascript
test('mobile is a distinct bottom-navigation and drawer state', () => {
  const medium = atRuleBody(css, '@media (max-width: 1100px)');
  const mobile = atRuleBody(css, '@media (max-width: 760px)');
  assert.match(ruleBody(medium, 'html[data-ui-style="spatial"] .spatial-inspector'), /visibility:\s*hidden[\s\S]*pointer-events:\s*none/);
  assert.match(ruleBody(medium, 'html[data-ui-style="spatial"] .spatial-overview.is-inspector-open .spatial-inspector'), /visibility:\s*visible[\s\S]*pointer-events:\s*auto/);
  assert.match(ruleBody(mobile, 'html[data-ui-style="spatial"] #rail'), /position:\s*fixed[\s\S]*bottom:/);
  assert.match(ruleBody(mobile, 'html[data-ui-style="spatial"] #rail'), /-webkit-backdrop-filter:\s*blur\(8px\)[\s\S]*backdrop-filter:\s*blur\(8px\)/);
  assert.match(ruleBody(mobile, 'html[data-ui-style="spatial"] .rail-foot'), /flex-direction:\s*row[\s\S]*width:\s*auto/);
  assert.match(ruleBody(mobile, 'html[data-ui-style="spatial"] #shell'), /max-width:\s*none/);
  assert.match(ruleBody(mobile, 'html[data-ui-style="spatial"] .progress-wrap'), /display:\s*flex/);
  assert.match(ruleBody(mobile, 'html[data-ui-style="spatial"] .spatial-filter-slot .home-filter-actions'), /display:\s*grid/);
  assert.match(ruleBody(mobile, 'html[data-ui-style="spatial"] .spatial-queue'), /position:\s*fixed[\s\S]*visibility:\s*hidden/);
  assert.match(ruleBody(mobile, 'html[data-ui-style="spatial"] .spatial-inspector'), /position:\s*fixed[\s\S]*visibility:\s*hidden/);
  assert.match(ruleBody(mobile, 'html[data-ui-style="spatial"] .spatial-overview.is-queue-open .spatial-queue'), /visibility:\s*visible[\s\S]*pointer-events:\s*auto/);
  assert.match(ruleBody(mobile, 'html[data-ui-style="spatial"] .spatial-overview.is-inspector-open .spatial-inspector'), /visibility:\s*visible[\s\S]*pointer-events:\s*auto/);
  for (const selector of [
    'html[data-ui-style="spatial"] #rail button',
    'html[data-ui-style="spatial"] .spatial-mobile-tools button',
    'html[data-ui-style="spatial"] .spatial-stage-nav button',
    'html[data-ui-style="spatial"] .spatial-panel-close',
    'html[data-ui-style="spatial"] #spatialOpen',
    'html[data-ui-style="spatial"] #spatialClearFilters',
    'html[data-ui-style="spatial"] .spatial-data-link',
    'html[data-ui-style="spatial"] #homeFilterActions button',
    'html[data-ui-style="spatial"] #homeFilterActions select',
    'html[data-ui-style="spatial"] #search',
    'html[data-ui-style="spatial"] .appearance-options label',
  ]) {
    const declarations = ruleBody(mobile, selector);
    assert.match(declarations, /min-width:\s*44px/);
    assert.match(declarations, /min-height:\s*44px/);
  }
});

test('reduced motion and missing backdrop filter have usable fallbacks', () => {
  const reduced = atRuleBody(css, '@media (prefers-reduced-motion: reduce)');
  const noBlur = atRuleBody(css, '@supports not ((backdrop-filter: blur(1px)) or (-webkit-backdrop-filter: blur(1px)))');
  const modal = ruleBody(css, 'html[data-ui-style="spatial"] .modal');
  assert.match(ruleBody(reduced, 'html[data-ui-style="spatial"] .spatial-layer'), /pointer-events:\s*auto/);
  assert.match(ruleBody(noBlur, 'html[data-ui-style="spatial"] .spatial-layer'), /pointer-events:\s*auto/);
  assert.match(noBlur, /--sp-surface:\s*var\(--sp-surface-solid\)/);
  assert.match(noBlur, /background:\s*var\(--sp-surface-solid\)/);
  assert.match(modal, /(?:^|\n)\s*-webkit-backdrop-filter:\s*blur\(9px\)/);
  assert.match(modal, /(?:^|\n)\s*backdrop-filter:\s*blur\(9px\)/);
});
```

Also add to `test/spatial-workspace-page-integration.test.js`:

```javascript
test('reduced-motion preference controls ECharts animation as well as CSS', () => {
  assert.match(app, /matchMedia\(\s*['"]\(prefers-reduced-motion:\s*reduce\)['"]\s*\)/);
  assert.match(app, /function\s+chartAnimationDuration\s*\(/);
  assert.doesNotMatch(app, /animationDuration(?:Update)?:\s*(?:600|700|750)\b/);
});

test('Settings close is idempotent and restores the active view scroll and focus', () => {
  const settings = app.slice(app.indexOf('function activeViewScrollContainer'), app.indexOf('function closeSettingsModal') + 900);
  assert.match(settings, /document\.activeElement/);
  assert.match(settings, /openSettingsModal[\s\S]*spatialWorkspace\?\.closePanels/);
  assert.match(settings, /scrollTop/);
  assert.match(settings, /classList\.contains\(['"]hidden['"]\)/);
  assert.match(settings, /\.focus\(\)/);
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
```

- [ ] **Step 3: Verify RED**

Run:

```powershell
node --test test/spatial-workspace.test.js test/spatial-workspace-page-integration.test.js test/spatial-style-contract.test.js
```

Expected: FAIL on panel methods, Settings/chart-motion integration and responsive/fallback contracts.

- [ ] **Step 4: Implement panel state and focus/scroll restoration**

Replace the controller signature with the injected breakpoint dependency and create its safe browser default immediately inside the function:

```javascript
function createWorkspaceController({
  root,
  document,
  scrollContainer,
  onOpen,
  onClearFilters,
  getDetails,
  desktopMedia,
  mobileMedia,
} = {}) {
  const media = desktopMedia || (
    typeof window !== 'undefined' && typeof window.matchMedia === 'function'
      ? window.matchMedia('(min-width: 1101px)')
      : null
  );
  const mobile = mobileMedia || (
    typeof window !== 'undefined' && typeof window.matchMedia === 'function'
      ? window.matchMedia('(max-width: 760px)')
      : null
  );
```

Add these entries to the existing `elements` object:

```javascript
queue: byId('spatialQueue'),
queueToggle: byId('spatialQueueToggle'),
queueClose: byId('spatialQueueClose'),
inspectorClose: byId('spatialInspectorClose'),
scrim: byId('spatialScrim'),
filterActions: byId('homeFilterActions'),
filterHome: byId('topFilters'),
filterSlot: byId('spatialFilterSlot'),
```

Place the complete panel state and methods beside the other controller-local state. Cleanup is unconditional, while scroll/focus restoration occurs only if a panel was genuinely active:

```javascript
let panelTrigger = null;
let savedScrollTop = 0;
let activePanel = null;

function resetPanelPresentation() {
  root.classList.remove('is-queue-open', 'is-inspector-open');
  document.documentElement.classList.remove('spatial-queue-open', 'spatial-inspector-open');
  elements.queueToggle.setAttribute('aria-expanded', 'false');
  elements.inspectorToggle.setAttribute('aria-expanded', 'false');
  elements.scrim.hidden = true;
  elements.filterHome.append(elements.filterActions);
}

function closePanels({ restoreFocus = true } = {}) {
  const wasActive = Boolean(activePanel);
  const trigger = panelTrigger;
  const scrollTop = savedScrollTop;
  resetPanelPresentation();
  activePanel = null;
  panelTrigger = null;
  if (!wasActive) return;
  if (scrollContainer) scrollContainer.scrollTop = scrollTop;
  if (restoreFocus && trigger && typeof trigger.focus === 'function') trigger.focus();
}

function openPanel(name) {
  const nextPanel = name === 'inspector' ? 'inspector' : 'queue';
  const inspector = nextPanel === 'inspector';
  if (inspector && elements.inspectorToggle.disabled) return;
  if (!activePanel) savedScrollTop = Number(scrollContainer && scrollContainer.scrollTop) || 0;
  resetPanelPresentation();
  activePanel = nextPanel;
  panelTrigger = inspector ? elements.inspectorToggle : elements.queueToggle;
  if (!inspector) elements.filterSlot.append(elements.filterActions);
  root.classList.add(inspector ? 'is-inspector-open' : 'is-queue-open');
  document.documentElement.classList.add(inspector ? 'spatial-inspector-open' : 'spatial-queue-open');
  panelTrigger.setAttribute('aria-expanded', 'true');
  elements.scrim.hidden = false;
  const panel = inspector ? elements.inspector : elements.queue;
  const firstFocusable = panel.querySelector('button, a, input, select, textarea, [tabindex="0"]');
  if (firstFocusable) firstFocusable.focus();
}
```

After `elements.inspectorToggle.disabled = !paper;` in `renderInspector()`, add the empty-transition cleanup. The function declaration is hoisted, and `activePanel` is initialized before any controller update occurs:

```javascript
if (!paper && activePanel === 'inspector') {
  closePanels({ restoreFocus: false });
  elements.clear.focus();
}
```

Replace the controller's existing `bind()` with this final single-shot version. Escape is bound on `document`, so it still works when focus is in the existing top filter controls outside `#spatialOverview`:

```javascript
function bind() {
  if (bound) return;
  bound = true;
  root.addEventListener('click', event => {
    const button = paperButton(event);
    if (button) {
      select(button.dataset.spatialPaperId, { focus: true, preserveLayerNodes: true });
    }
  });
  root.addEventListener('dblclick', event => {
    const button = paperButton(event);
    if (!button) return;
    select(button.dataset.spatialPaperId, { preserveLayerNodes: true });
    openSelected();
  });
  root.addEventListener('keydown', event => {
    const button = paperButton(event);
    if (!button) return;
    if (event.key === 'Enter') {
      event.preventDefault();
      select(button.dataset.spatialPaperId, { preserveLayerNodes: true });
      openSelected();
    } else if (event.key === 'ArrowLeft') {
      event.preventDefault();
      select(button.dataset.spatialPaperId);
      move(-1);
    } else if (event.key === 'ArrowRight') {
      event.preventDefault();
      select(button.dataset.spatialPaperId);
      move(1);
    }
  });
  elements.previous.addEventListener('click', () => move(-1));
  elements.next.addEventListener('click', () => move(1));
  elements.open.addEventListener('click', openSelected);
  elements.clear.addEventListener('click', () => {
    if (typeof onClearFilters === 'function') onClearFilters();
  });
  elements.queueToggle.addEventListener('click', () => openPanel('queue'));
  elements.inspectorToggle.addEventListener('click', () => openPanel('inspector'));
  elements.queueClose.addEventListener('click', () => closePanels());
  elements.inspectorClose.addEventListener('click', () => closePanels());
  elements.scrim.addEventListener('click', () => closePanels());
  document.addEventListener('keydown', event => {
    if (event.key !== 'Escape' || !activePanel) return;
    if (typeof event.preventDefault === 'function') event.preventDefault();
    closePanels();
  });
  if (media) {
    const onDesktopChange = event => {
      if (event.matches) closePanels({ restoreFocus: false });
    };
    if (typeof media.addEventListener === 'function') media.addEventListener('change', onDesktopChange);
    else if (typeof media.addListener === 'function') media.addListener(onDesktopChange);
  }
  if (mobile) {
    const onMobileChange = event => {
      if (!event.matches && activePanel === 'queue') closePanels({ restoreFocus: false });
    };
    if (typeof mobile.addEventListener === 'function') mobile.addEventListener('change', onMobileChange);
    else if (typeof mobile.addListener === 'function') mobile.addListener(onMobileChange);
  }
}
```

Expose the panel methods in the controller return object:

```javascript
return {
  bind,
  closePanels,
  getState,
  move,
  openPanel,
  openSelected,
  refreshDetails,
  select,
  update,
};
```

Replace the beginning of `handleAppearanceChange` with the following complete function body; it clears transient panels before the readiness guard but never changes the current view or PDF layout:

```javascript
function handleAppearanceChange(event) {
  if (event.detail.uiStyle !== 'spatial') spatialWorkspace?.closePanels({ restoreFocus: false });
  if (!appReady) return;
  try {
    if (currentView === 'home') renderHome();
    if (currentView === 'insights') redrawInsightChartsFromCache();
  } catch (error) {
    console.warn('Appearance chart redraw failed', error);
  }
  if (appearanceFrame) cancelAnimationFrame(appearanceFrame);
  appearanceFrame = requestAnimationFrame(() => {
    appearanceFrame = 0;
    [chProgress, chDir, chVenue, chTrend, chTree, chCited, chCite]
      .forEach(chart => {
        try { if (chart) chart.resize(); } catch (error) { console.warn('Chart resize failed', error); }
      });
  });
}
```

Start `showView(v)` with these exact lines before its existing visibility changes:

```javascript
function showView(v) {
  if (v !== 'home') spatialWorkspace?.closePanels({ restoreFocus: false });
  currentView = v;
```

Use the same accessibility contract for the global Settings modal in `public/app.js`:

```javascript
let settingsReturnFocus = null;
let settingsScrollContainer = null;
let settingsScrollTop = 0;

function activeViewScrollContainer() {
  return currentView === 'read' ? $('#pdfScroll') : $('#' + currentView);
}

function openSettingsModal() {
  spatialWorkspace?.closePanels({ restoreFocus: false });
  settingsReturnFocus = document.activeElement;
  settingsScrollContainer = activeViewScrollContainer();
  settingsScrollTop = Number(settingsScrollContainer && settingsScrollContainer.scrollTop) || 0;
  loadSettings();
  $('#settingsModal').classList.remove('hidden');
  setTimeout(() => $('#setClose').focus(), 0);
}

function closeSettingsModal() {
  const modal = $('#settingsModal');
  if (!modal || modal.classList.contains('hidden')) return;
  modal.classList.add('hidden');
  if (settingsScrollContainer) settingsScrollContainer.scrollTop = settingsScrollTop;
  if (settingsReturnFocus && typeof settingsReturnFocus.focus === 'function') settingsReturnFocus.focus();
  settingsReturnFocus = null;
  settingsScrollContainer = null;
}
```

- [ ] **Step 5: Add mobile, reduced-motion and no-blur CSS**

Append both responsive states exactly. The existing search stays in the command bar; the same `#homeFilterActions` node is moved into `#spatialFilterSlot` while the queue is open and returned on close, so focus order follows the drawer without cloning or creating another filter state:

```css
@media (max-width: 1100px) {
  html[data-ui-style="spatial"] .spatial-overview {
    grid-template-columns: minmax(190px, 0.72fr) minmax(0, 1.8fr);
    min-height: 560px;
  }

  html[data-ui-style="spatial"] .spatial-mobile-tools {
    display: flex;
    grid-column: 1 / -1;
    justify-content: flex-end;
    gap: 8px;
  }

  html[data-ui-style="spatial"] #spatialQueueToggle {
    display: none;
  }

  html[data-ui-style="spatial"] .spatial-inspector {
    position: fixed;
    top: 74px;
    right: 16px;
    bottom: 16px;
    z-index: 72;
    width: min(360px, calc(100vw - 32px));
    visibility: hidden;
    pointer-events: none;
    transform: translateX(calc(100% + 24px));
    transition: transform 180ms ease, visibility 0s linear 180ms;
  }

  html[data-ui-style="spatial"] .spatial-overview.is-inspector-open .spatial-inspector {
    visibility: visible;
    pointer-events: auto;
    transform: translateX(0);
    transition-delay: 0s;
  }

  html[data-ui-style="spatial"] #spatialInspectorClose {
    display: inline-grid;
    place-items: center;
    margin: 0 0 12px auto;
  }

  html[data-ui-style="spatial"] .spatial-scrim:not([hidden]) {
    display: block;
    position: fixed;
    inset: 0;
    z-index: 70;
    border: 0;
    background: color-mix(in srgb, var(--sp-bg) 64%, transparent);
  }

  html[data-ui-style="spatial"] #topFilters {
    min-width: 0;
    overflow-x: auto;
    scrollbar-width: thin;
  }

  html[data-ui-style="spatial"] .layer-offset--4,
  html[data-ui-style="spatial"] .layer-offset-4 {
    opacity: 0.12;
  }

  html[data-ui-style="spatial"] .layer-offset--2 {
    transform: translate(-4%, -60%) scale(0.92);
  }

  html[data-ui-style="spatial"] .layer-offset-2 {
    transform: translate(4%, -40%) scale(0.92);
  }
}

@media (max-width: 760px) {
  html[data-ui-style="spatial"] body {
    padding-bottom: 72px;
  }

  html[data-ui-style="spatial"] #rail {
    position: fixed;
    right: 0;
    bottom: 0;
    left: 0;
    z-index: 90;
    display: flex;
    flex-direction: row;
    width: auto;
    height: 72px;
    padding: 6px 8px;
    border-top: 1px solid var(--sp-border-strong);
    border-right: 0;
    overflow: hidden;
    -webkit-backdrop-filter: blur(8px) saturate(110%);
    backdrop-filter: blur(8px) saturate(110%);
  }

  html[data-ui-style="spatial"] #rail button {
    min-width: 44px;
    min-height: 44px;
  }

  html[data-ui-style="spatial"] .rail-brand,
  html[data-ui-style="spatial"] .rail-spacer {
    display: none;
  }

  html[data-ui-style="spatial"] .viewnav {
    display: flex;
    flex: 1 1 auto;
    flex-direction: row;
    gap: 4px;
    min-width: 0;
    overflow-x: auto;
    overflow-y: hidden;
    scrollbar-width: thin;
  }

  html[data-ui-style="spatial"] .viewnav button {
    flex: 0 0 54px;
    width: 54px;
    padding: 4px;
  }

  html[data-ui-style="spatial"] .viewnav button.active::before {
    top: -6px;
    right: 12px;
    left: 12px;
    width: auto;
    height: 2px;
    transform: none;
  }

  html[data-ui-style="spatial"] .rail-foot {
    display: flex;
    flex: 0 0 auto;
    flex-direction: row;
    align-items: center;
    gap: 4px;
    width: auto;
    padding-top: 0;
    padding-left: 6px;
    border-top: 0;
    border-left: 1px solid var(--sp-border);
  }

  html[data-ui-style="spatial"] #shell {
    width: 100%;
    max-width: none;
    min-width: 0;
    padding-bottom: 0;
  }

  html[data-ui-style="spatial"] #topbar {
    display: grid;
    grid-template-columns: minmax(0, 1fr) auto;
    gap: 8px;
    min-height: 58px;
    padding: 7px 10px;
    overflow: visible;
  }

  html[data-ui-style="spatial"] .brand {
    display: none;
  }

  html[data-ui-style="spatial"] #topFilters {
    display: contents;
  }

  html[data-ui-style="spatial"] .search-box {
    grid-column: 1;
    min-width: 0;
  }

  html[data-ui-style="spatial"] #search {
    width: 100%;
    min-width: 44px;
    min-height: 44px;
  }

  html[data-ui-style="spatial"] .progress-wrap {
    display: flex;
    grid-column: 2;
    min-width: 76px;
  }

  html[data-ui-style="spatial"] #topFilters > .home-filter-actions {
    display: none;
  }

  html[data-ui-style="spatial"] .spatial-filter-slot .home-filter-actions {
    display: grid;
    grid-template-columns: repeat(2, minmax(0, 1fr));
    gap: 8px;
    margin: 14px 0 18px;
  }

  html[data-ui-style="spatial"] .spatial-filter-slot #yearFilters {
    display: flex;
    grid-column: 1 / -1;
    gap: 6px;
    overflow-x: auto;
  }

  html[data-ui-style="spatial"] #homeFilterActions button {
    min-width: 44px;
    min-height: 44px;
  }

  html[data-ui-style="spatial"] #homeFilterActions select {
    min-width: 44px;
    min-height: 44px;
  }

  html[data-ui-style="spatial"] #home,
  html[data-ui-style="spatial"] #review,
  html[data-ui-style="spatial"] #manage,
  html[data-ui-style="spatial"] #jobs,
  html[data-ui-style="spatial"] #insights {
    padding: 16px 12px 88px;
  }

  html[data-ui-style="spatial"] .chart-card,
  html[data-ui-style="spatial"] .table-card,
  html[data-ui-style="spatial"] .ebatch,
  html[data-ui-style="spatial"] .review-stat,
  html[data-ui-style="spatial"] .review-card,
  html[data-ui-style="spatial"] .ingest-bar,
  html[data-ui-style="spatial"] .lib-card,
  html[data-ui-style="spatial"] .job-card,
  html[data-ui-style="spatial"] .set-card {
    box-shadow: inset 0 1px var(--sp-highlight);
    -webkit-backdrop-filter: none;
    backdrop-filter: none;
  }

  html[data-ui-style="spatial"] .spatial-queue,
  html[data-ui-style="spatial"] .spatial-stage,
  html[data-ui-style="spatial"] .spatial-inspector,
  html[data-ui-style="spatial"] #topbar {
    -webkit-backdrop-filter: blur(8px) saturate(110%);
    backdrop-filter: blur(8px) saturate(110%);
  }

  html[data-ui-style="spatial"] .spatial-overview {
    display: grid;
    grid-template-columns: minmax(0, 1fr);
    min-height: 0;
    gap: 10px;
  }

  html[data-ui-style="spatial"] .spatial-mobile-tools {
    position: sticky;
    top: 0;
    z-index: 12;
    display: flex;
    grid-column: 1;
    justify-content: space-between;
    padding: 6px;
    border: 1px solid var(--sp-border);
    border-radius: 12px;
    background: var(--sp-surface-solid);
  }

  html[data-ui-style="spatial"] .spatial-mobile-tools button {
    min-width: 44px;
    min-height: 44px;
  }

  html[data-ui-style="spatial"] #spatialQueueToggle {
    display: inline-grid;
  }

  html[data-ui-style="spatial"] .spatial-stage {
    grid-column: 1;
    min-height: 500px;
    padding: 14px;
  }

  html[data-ui-style="spatial"] .spatial-queue {
    position: fixed;
    top: 0;
    bottom: 72px;
    left: 0;
    z-index: 82;
    width: min(86vw, 360px);
    padding: 18px 18px 22px;
    border-radius: 0 16px 16px 0;
    visibility: hidden;
    pointer-events: none;
    transform: translateX(-104%);
    transition: transform 180ms ease, visibility 0s linear 180ms;
  }

  html[data-ui-style="spatial"] .spatial-inspector {
    position: fixed;
    top: auto;
    right: 0;
    bottom: 72px;
    left: 0;
    z-index: 82;
    width: auto;
    max-height: 64vh;
    padding: 16px 18px 22px;
    border-radius: 16px 16px 0 0;
    visibility: hidden;
    pointer-events: none;
    transform: translateY(104%);
    transition: transform 180ms ease, visibility 0s linear 180ms;
  }

  html[data-ui-style="spatial"] .spatial-overview.is-queue-open .spatial-queue {
    visibility: visible;
    pointer-events: auto;
    transform: translateX(0);
    transition-delay: 0s;
  }

  html[data-ui-style="spatial"] .spatial-overview.is-inspector-open .spatial-inspector {
    visibility: visible;
    pointer-events: auto;
    transform: translateY(0);
    transition-delay: 0s;
  }

  html[data-ui-style="spatial"] .spatial-scrim:not([hidden]) {
    display: block;
    position: fixed;
    inset: 0 0 72px;
    z-index: 80;
  }

  html[data-ui-style="spatial"] .spatial-panel-close {
    display: inline-grid;
    place-items: center;
    min-width: 44px;
    min-height: 44px;
  }

  html[data-ui-style="spatial"] .spatial-layers {
    min-height: 390px;
  }

  html[data-ui-style="spatial"] .spatial-layer {
    inset: 50% 3% auto;
    min-height: 214px;
    padding: 22px 18px;
  }

  html[data-ui-style="spatial"] .spatial-layer-title {
    margin: 14px 0;
    font-size: 19px;
  }

  html[data-ui-style="spatial"] .layer-offset--4,
  html[data-ui-style="spatial"] .layer-offset-4 {
    opacity: 0;
    pointer-events: none;
  }

  html[data-ui-style="spatial"] .layer-offset--2 {
    transform: translate(-2%, -59%) scale(0.94);
  }

  html[data-ui-style="spatial"] .layer-offset-2 {
    transform: translate(2%, -41%) scale(0.94);
  }

  html[data-ui-style="spatial"] .spatial-stage-nav button {
    min-width: 44px;
    min-height: 44px;
  }

  html[data-ui-style="spatial"] #spatialOpen {
    min-width: 44px;
    min-height: 44px;
  }

  html[data-ui-style="spatial"] #spatialClearFilters {
    min-width: 44px;
    min-height: 44px;
  }

  html[data-ui-style="spatial"] .spatial-data-link {
    display: inline-grid;
    min-width: 44px;
    min-height: 44px;
    place-items: center;
  }

  html[data-ui-style="spatial"] .appearance-row {
    grid-template-columns: 1fr;
  }

  html[data-ui-style="spatial"] .appearance-options label {
    min-width: 44px;
    min-height: 44px;
  }
}

@media (prefers-reduced-motion: reduce) {
  html[data-ui-style="spatial"] *,
  html[data-ui-style="spatial"] *::before,
  html[data-ui-style="spatial"] *::after {
    scroll-behavior: auto !important;
    transition-duration: 0.01ms !important;
    animation-duration: 0.01ms !important;
    animation-iteration-count: 1 !important;
  }
  html[data-ui-style="spatial"] .spatial-layers {
    display: grid;
    min-height: 0;
    perspective: none;
    gap: 8px;
  }
  html[data-ui-style="spatial"] .spatial-layer {
    position: relative;
    inset: auto;
    transform: none !important;
    opacity: 1 !important;
    pointer-events: auto;
  }
}

@supports not ((backdrop-filter: blur(1px)) or (-webkit-backdrop-filter: blur(1px))) {
  html[data-ui-style="spatial"] {
    --sp-surface: var(--sp-surface-solid);
  }
  html[data-ui-style="spatial"] .spatial-queue,
  html[data-ui-style="spatial"] .spatial-stage,
  html[data-ui-style="spatial"] .spatial-inspector,
  html[data-ui-style="spatial"] #topbar,
  html[data-ui-style="spatial"] .modal-card {
    background: var(--sp-surface-solid);
  }
  html[data-ui-style="spatial"] .spatial-layers {
    display: grid;
    min-height: 0;
    perspective: none;
    gap: 8px;
  }
  html[data-ui-style="spatial"] .spatial-layer {
    position: relative;
    inset: auto;
    transform: none;
    opacity: 1;
    pointer-events: auto;
  }
}
```

CSS cannot disable ECharts canvas transitions. Add this helper near `cssVar()` in `public/app.js`:

```javascript
function prefersReducedMotion() {
  return Boolean(window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches);
}

function chartAnimationDuration(duration) {
  return prefersReducedMotion() ? 0 : duration;
}
```

Make these exact option-line replacements:

```javascript
// updateCharts(): chProgress.setOption(...)
animationDuration: chartAnimationDuration(750),
animationDurationUpdate: chartAnimationDuration(600),
animationEasing: 'cubicOut',

// barOption(): returned option used by both direction and venue bars
animationDuration: chartAnimationDuration(750),
animationDurationUpdate: chartAnimationDuration(600),
animationEasing: 'cubicOut',

// renderTree(): chTree.setOption(...)
animationDuration: chartAnimationDuration(600),

// renderTrend(): chTrend.setOption(...)
animationDuration: chartAnimationDuration(700),
animationEasing: 'cubicOut',

// renderCited(): chCited.setOption(...)
animationDuration: chartAnimationDuration(600),
```

Replace the `chCite.setOption(...)` call in `renderCite()` with:

```javascript
chCite.setOption({
  animation: !prefersReducedMotion(),
  tooltip: {
    confine: true,
    formatter: p => p.dataType === 'node'
      ? `${esc(p.data.name)}<br>被库内 <b>${p.data.value}</b> 篇引用`
      : '',
  },
  legend: [{
    data: cats,
    top: 2,
    textStyle: { color: t2, fontSize: 11 },
    itemWidth: 11,
    itemHeight: 11,
    itemGap: 12,
  }],
  series: [{
    type: 'graph',
    layout: 'circular',
    circular: { rotateLabel: true },
    roam: true,
    categories: cats.map(c => ({ name: c, itemStyle: { color: colorOf[c] } })),
    data,
    links: g.links.map(link => ({ source: link.source, target: link.target })),
    edgeSymbol: ['none', 'arrow'],
    edgeSymbolSize: 4,
    lineStyle: { color: 'source', opacity: 0.18, width: 1, curveness: 0.3 },
    label: {
      position: 'right',
      fontSize: 9.5,
      color: t2,
      formatter: p => p.data.name.length > 14 ? p.data.name.slice(0, 14) + '…' : p.data.name,
    },
    emphasis: {
      focus: 'adjacency',
      lineStyle: { width: 2, opacity: 0.65 },
      label: { show: true },
    },
  }],
});
```

No raw `600`, `700` or `750` duration remains assigned directly.

- [ ] **Step 6: Verify focused and full tests**

Run separately:

```powershell
node --test test/spatial-workspace.test.js test/spatial-workspace-page-integration.test.js test/spatial-style-contract.test.js
npm test
```

Expected: PASS with no event-listener duplication or unscoped selector.

- [ ] **Step 7: Commit mobile and accessibility behavior**

```powershell
git add -- public/app.js public/spatial-workspace.js public/spatial.css test/spatial-workspace.test.js test/spatial-workspace-page-integration.test.js test/spatial-style-contract.test.js
git diff --cached --check
git commit -m "feat: add responsive spatial workspace"
```

---

### Task 8: Automated regression and browser visual matrix

**Files:**
- Modify only if a failing verification exposes a real defect; add a failing regression test before every fix.

- [ ] **Step 1: Run all automated gates from a clean staging area**

Run separately:

```powershell
git diff --check
node --test test/appearance.test.js test/appearance-page-integration.test.js
node --test test/spatial-workspace.test.js test/spatial-workspace-page-integration.test.js test/spatial-style-contract.test.js
npm test
git status --short
```

Expected: all tests PASS; only intended feature files plus the pre-existing user-owned `AGENTS.md` change appear.

- [ ] **Step 2: Verify the four appearance combinations and persistence**

In Settings, verify in order:

```text
经典模式 / 浅色
经典模式 / 深色
空间研究台 / 浅色
空间研究台 / 深色
```

After each selection, reload and confirm the same pair remains. Then set storage to invalid values in browser devtools, reload, and confirm `classic/light`. Block storage writes, switch appearance, and confirm the page changes for the session without crashing. Finally force `/api/settings` load and save failures and confirm they change only the model-settings error hint: the selected local appearance and both root attributes remain unchanged.

- [ ] **Step 3: Compare classic mode with the baselines**

At `1440×1000` and `390×844`, compare classic light/dark against the four preflight screenshots. The intentionally added first Settings “外观” card is the sole accepted structural difference; all pre-existing Settings controls below it keep their classic styling, order and behavior. Verify overview and reading view have no new visible spatial container, emerald recoloring, unrelated layout shift, missing ID, or changed workflow.

- [ ] **Step 4: Verify the spatial overview state matrix**

Use real application controls to verify:

```text
Initial result set             current paper if valid, otherwise first
Single click                   selection and inspector only
Double-click / Enter / button  existing reading workflow
Previous / next                disabled at boundaries, no wrapping
Sort change                    valid selection preserved
Year/favorite/text/semantic    same list in table and layers
Empty filter                   real empty message and clear action
One result                     one visible layer, both boundaries disabled
More than five results         at most five real neighboring layers, exact total visible
查看完整数据                    native jump to #homeTable
```

- [ ] **Step 5: Verify every global page in both spatial themes**

At `1440×900` and a medium `1024×768`, inspect `总览 / 阅读 / 复习 / 管理 / 采集 / 洞察 / 设置` in light and dark. Confirm high contrast, emerald is not used for errors/warnings, PDF pages remain neutral, ECharts redraw after switching, the medium inspector collapses without hiding search or the selected title, modals remain above glass surfaces, and no horizontal overflow occurs.

- [ ] **Step 6: Verify mobile and keyboard behavior**

At `390×844`:

- Navigate all global pages from the bottom navigation.
- Open/close the research drawer and detail bottom panel with their buttons, scrim and Escape.
- Confirm focus enters the panel and returns to its trigger; page scroll returns to the prior position.
- Tab through layer cards; single activation selects, Enter opens, and the explicit button is reachable.
- Confirm primary touch targets measure at least `44×44 CSS px`.
- Confirm the stage remains primary and neither drawer covers it while closed.

- [ ] **Step 7: Verify fallbacks and finish only after fresh evidence**

Enable reduced motion and confirm no depth transition is required to understand selection. Disable `backdrop-filter` support in devtools and confirm solid high-contrast panels. Re-run `npm test` after any fix. If all checks pass, use `superpowers:requesting-code-review`, address findings with test-first fixes, then use `superpowers:verification-before-completion` before claiming completion.

Expected final evidence:

```text
Automated suite: PASS
Classic regression: 2 themes × desktop/mobile PASS
Spatial matrix: 2 themes × desktop/medium/mobile × all major pages PASS
Queue state machine: empty/one/many/filter/sort/open/boundaries PASS
Accessibility/fallbacks: keyboard/focus/touch/reduced-motion/no-blur PASS
```

---

## Plan self-review

- Spec coverage: appearance persistence, classic isolation, four appearance states, global pages, real layer semantics, one-time external-current following, native double-click stability, exact data fallback, ECharts redraw, desktop/mobile layouts, real filter-node reparenting, focus/scroll restoration, reduced motion and no-blur fallback each map to a task above.
- No production behavior is introduced before a failing focused test in its task.
- API names are consistent throughout: `createAppearanceController`, `bootstrap`, `bindControls`, `getState`, `setAppearance`; `createWorkspaceState`, `reconcilePapers`, `selectPaper`, `moveSelection`, `selectedPaper`, `createWorkspaceController`, `openPanel`, `closePanels`.
- The complete Task 6/7 CSS contains only positively scoped ordinary selectors; every tested mobile control group declares both `min-width: 44px` and `min-height: 44px`.
- Existing script adjacency remains `ingest-rendering.js → markdown-rendering-coordinator.js → app.js`; `spatial-workspace.js` is loaded immediately before that trio.
- Existing `AGENTS.md` changes are explicitly excluded from every staging command.
