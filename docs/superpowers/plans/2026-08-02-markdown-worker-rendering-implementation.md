# Markdown / KaTeX Worker Rendering Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move application Markdown / KaTeX parsing off the browser main thread and safely show literal source when a dedicated rendering Worker cannot finish within 200ms.

**Architecture:** Keep `markdown-rendering.js` as the Node-testable safe rendering core, make its browser wrapper Worker-compatible, and call it from a dedicated Worker entry. A small main-thread coordinator owns one Worker per target-element request, terminates stale or timed-out jobs, and is the only page-side writer. `app.js` preserves the existing `renderMd(el, text)` interface by delegating to that coordinator.

**Tech Stack:** Browser Web Workers, bundled Marked and KaTeX, CommonJS-compatible UMD modules, Node built-in test runner and `node:vm`.

---

## File structure

- Modify: `public/markdown-rendering.js` — expose the existing safe core through `self` when loaded by a Worker, while preserving Node and Window exports.
- Create: `public/markdown-rendering-worker.js` — load local dependencies and answer one rendering message with a matching request ID.
- Create: `public/markdown-rendering-coordinator.js` — create, time, cancel and clean up per-element Worker jobs; fall back through `textContent`.
- Modify: `public/index.html` — load the coordinator before `app.js`.
- Modify: `public/app.js` — replace the synchronous `renderMd` implementation with a coordinator delegation, without changing call sites.
- Create: `test/markdown-rendering-worker.test.js` — verify Worker bootstrap and protocol without a browser.
- Create: `test/markdown-rendering-coordinator.test.js` — verify success, timeout, failure, cancellation and stale-result behavior with fake Workers.
- Create: `test/markdown-rendering-page-integration.test.js` — lock the script order and `renderMd` delegation contract.

Do not stage, change, restore or commit the pre-existing `package-lock.json` CRLF change.

### Task 1: Make the safe core Worker-visible and add the Worker entry

**Files:**

- Modify: `public/markdown-rendering.js:1-6`
- Create: `public/markdown-rendering-worker.js`
- Create: `test/markdown-rendering-worker.test.js`

- [ ] **Step 1: Write the failing Worker bootstrap and protocol tests**

Create `test/markdown-rendering-worker.test.js` with a VM-backed Worker scope. The first test must prove that loading the core with only `self` exposes `self.MarkdownRendering.createMarkdownRenderer`. The second must prove the Worker imports local dependencies in this exact order and returns the original numeric ID with the renderer HTML. The third must prove malformed messages and renderer failures return an error result instead of throwing.

```js
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const root = path.resolve(__dirname, '..');
const coreSource = fs.readFileSync(path.join(root, 'public', 'markdown-rendering.js'), 'utf8');
const workerSource = fs.readFileSync(path.join(root, 'public', 'markdown-rendering-worker.js'), 'utf8');

test('safe Markdown core exposes its API when a Worker provides self', () => {
  const scope = {};
  vm.runInNewContext(coreSource, { self: scope });
  assert.equal(typeof scope.MarkdownRendering.createMarkdownRenderer, 'function');
});

function createWorkerScope(render) {
  const imports = [];
  const messages = [];
  const scope = {
    marked: { name: 'marked' },
    katex: { name: 'katex' },
    importScripts(...paths) {
      imports.push(...paths);
      this.MarkdownRendering = {
        createMarkdownRenderer({ getMarked, getKatex }) {
          assert.equal(getMarked(), scope.marked);
          assert.equal(getKatex(), scope.katex);
          return { render };
        },
      };
    },
    postMessage(message) { messages.push(message); },
  };
  vm.runInNewContext(workerSource, { self: scope });
  return { scope, imports, messages };
}

test('Worker loads bundled dependencies then returns HTML with the matching ID', () => {
  const { scope, imports, messages } = createWorkerScope(text => `<p>${text}</p>`);
  scope.onmessage({ data: { id: 17, text: '**safe**' } });

  assert.deepEqual(imports, ['vendor/marked.min.js', 'vendor/katex/katex.min.js', 'markdown-rendering.js']);
  assert.deepEqual(messages, [{ id: 17, html: '<p>**safe**</p>' }]);
});

test('Worker turns malformed requests and renderer failures into error messages', () => {
  const malformed = createWorkerScope(text => text);
  malformed.scope.onmessage({ data: { id: 3, text: { not: 'text' } } });
  assert.deepEqual(malformed.messages, [{ id: 3, error: true }]);

  const failing = createWorkerScope(() => { throw new Error('render failed'); });
  failing.scope.onmessage({ data: { id: 4, text: 'source' } });
  assert.deepEqual(failing.messages, [{ id: 4, error: true }]);
});
```

- [ ] **Step 2: Run the focused test to verify it fails**

Run: `node --test test/markdown-rendering-worker.test.js`

Expected: FAIL because the Worker entry does not exist and the core only exports through `window`.

- [ ] **Step 3: Implement the Worker-safe UMD root and protocol**

Change the root expression at the start of `public/markdown-rendering.js` to prefer `self`, retaining the Window fallback:

```js
}(typeof self !== 'undefined' ? self : (typeof window !== 'undefined' ? window : undefined), function () {
```

Create `public/markdown-rendering-worker.js` as follows. It never accepts executable page configuration and it never writes to the DOM.

```js
(function (scope) {
  let renderer = null;

  try {
    scope.importScripts('vendor/marked.min.js', 'vendor/katex/katex.min.js', 'markdown-rendering.js');
    renderer = scope.MarkdownRendering.createMarkdownRenderer({
      getMarked: () => scope.marked,
      getKatex: () => scope.katex,
    });
  } catch (error) {
    renderer = null;
  }

  scope.onmessage = function (event) {
    const message = event && event.data;
    const id = message && message.id;
    if (!message || !Number.isInteger(id) || typeof message.text !== 'string' || !renderer) {
      scope.postMessage({ id, error: true });
      return;
    }

    try {
      const html = renderer.render(message.text);
      if (typeof html !== 'string') throw new TypeError('Markdown renderer returned non-string HTML');
      scope.postMessage({ id, html });
    } catch (error) {
      scope.postMessage({ id, error: true });
    }
  };
}(self));
```

- [ ] **Step 4: Run the Worker and existing core tests to verify they pass**

Run: `node --test test/markdown-rendering-worker.test.js test/markdown-rendering.test.js`

Expected: PASS; the existing 83 core tests remain green and the new Worker tests confirm the local protocol.

- [ ] **Step 5: Commit the isolated Worker layer**

```bash
git add -- public/markdown-rendering.js public/markdown-rendering-worker.js test/markdown-rendering-worker.test.js
git diff --cached --check
git commit -m "feat: add markdown rendering worker"
```

### Task 2: Add the main-thread Worker coordinator

**Files:**

- Create: `public/markdown-rendering-coordinator.js`
- Create: `test/markdown-rendering-coordinator.test.js`

- [ ] **Step 1: Write failing coordinator tests with deterministic Workers and timers**

Create `test/markdown-rendering-coordinator.test.js`. Use the fake Worker and manual timer below so the suite asserts behavior instead of elapsed wall-clock time. Cover a successful message, every failure route (constructor, `postMessage`, `error`, malformed message and timeout), a stale old callback, and simultaneous different-element jobs.

```js
const assert = require('node:assert/strict');
const test = require('node:test');
const { createMarkdownRenderCoordinator } = require('../public/markdown-rendering-coordinator.js');

class FakeWorker {
  constructor({ postError = false } = {}) {
    this.postError = postError;
    this.sent = [];
    this.terminated = false;
    this.onmessage = null;
    this.onerror = null;
  }

  postMessage(message) {
    if (this.postError) throw new Error('post failed');
    this.sent.push(message);
  }

  terminate() { this.terminated = true; }
  emitMessage(data) { this.onmessage({ data }); }
  emitError() { this.onerror(new Error('worker failed')); }
}

function createClock() {
  let next = 1;
  const callbacks = new Map();
  return {
    setTimeout(callback) { const id = next++; callbacks.set(id, callback); return id; },
    clearTimeout(id) { callbacks.delete(id); },
    fireAll() { for (const callback of [...callbacks.values()]) callback(); callbacks.clear(); },
  };
}

function createCoordinator(workers, clock) {
  return createMarkdownRenderCoordinator({
    createWorker: () => workers.shift(),
    setTimeout: clock.setTimeout,
    clearTimeout: clock.clearTimeout,
    timeoutMs: 200,
  });
}

test('writes matching Worker HTML and cleans up the completed Worker', () => {
  const worker = new FakeWorker();
  const clock = createClock();
  const element = { innerHTML: 'old', textContent: '' };
  const coordinator = createCoordinator([worker], clock);

  assert.equal(coordinator.renderInto(element, '**new**'), element);
  const request = worker.sent[0];
  worker.emitMessage({ id: request.id, html: '<strong>new</strong>' });

  assert.equal(element.innerHTML, '<strong>new</strong>');
  assert.equal(element.textContent, '');
  assert.equal(worker.terminated, true);
});

test('uses textContent after every Worker failure route', async (t) => {
  await t.test('constructor failure', () => {
    const element = { innerHTML: 'old', textContent: '' };
    const coordinator = createMarkdownRenderCoordinator({ createWorker: () => { throw new Error('unavailable'); } });
    coordinator.renderInto(element, '<img src=x>');
    assert.equal(element.textContent, '<img src=x>');
  });

  await t.test('postMessage failure', () => {
    const worker = new FakeWorker({ postError: true });
    const element = { innerHTML: 'old', textContent: '' };
    createCoordinator([worker], createClock()).renderInto(element, '<img src=x>');
    assert.equal(element.textContent, '<img src=x>');
    assert.equal(worker.terminated, true);
  });

  await t.test('error, malformed result and timeout', () => {
    for (const action of ['error', 'malformed', 'timeout']) {
      const worker = new FakeWorker();
      const clock = createClock();
      const element = { innerHTML: 'old', textContent: '' };
      createCoordinator([worker], clock).renderInto(element, '<svg onload=1>');
      if (action === 'error') worker.emitError();
      if (action === 'malformed') worker.emitMessage({ id: worker.sent[0].id, html: 7 });
      if (action === 'timeout') clock.fireAll();
      assert.equal(element.textContent, '<svg onload=1>', action);
      assert.equal(worker.terminated, true, action);
    }
  });
});

test('cancels a stale same-element job while allowing different elements to finish', () => {
  const first = new FakeWorker();
  const second = new FakeWorker();
  const third = new FakeWorker();
  const clock = createClock();
  const coordinator = createCoordinator([first, second, third], clock);
  const element = { innerHTML: 'old', textContent: '' };
  const other = { innerHTML: 'other-old', textContent: '' };

  coordinator.renderInto(element, 'first');
  const staleMessage = first.onmessage;
  const firstRequest = first.sent[0];
  coordinator.renderInto(element, 'second');
  coordinator.renderInto(other, 'third');
  assert.equal(first.terminated, true);

  staleMessage({ data: { id: firstRequest.id, html: '<p>first</p>' } });
  assert.equal(element.innerHTML, 'old');
  second.emitMessage({ id: second.sent[0].id, html: '<p>second</p>' });
  third.emitMessage({ id: third.sent[0].id, html: '<p>third</p>' });
  assert.equal(element.innerHTML, '<p>second</p>');
  assert.equal(other.innerHTML, '<p>third</p>');
});
```

- [ ] **Step 2: Run the focused coordinator test to verify it fails**

Run: `node --test test/markdown-rendering-coordinator.test.js`

Expected: FAIL because `public/markdown-rendering-coordinator.js` does not exist.

- [ ] **Step 3: Implement the coordinator with per-element versions and safe fallback**

Create `public/markdown-rendering-coordinator.js`. Keep worker lifecycle operations private; `renderInto` is the only public rendering operation. A completion must check both the active job object and its version before writing.

```js
(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  if (root) root.MarkdownRenderingCoordinator = api;
}(typeof window !== 'undefined' ? window : undefined, function () {
  function text(value) {
    if (value == null) return '';
    try { return String(value); } catch (error) { return ''; }
  }

  function createMarkdownRenderCoordinator(options = {}) {
    const workerUrl = options.workerUrl || 'markdown-rendering-worker.js';
    const createWorker = options.createWorker || (() => new Worker(workerUrl));
    const setTimer = options.setTimeout || setTimeout;
    const clearTimer = options.clearTimeout || clearTimeout;
    const timeoutMs = Number.isFinite(options.timeoutMs) && options.timeoutMs >= 0 ? options.timeoutMs : 200;
    const jobs = new WeakMap();
    const versions = new WeakMap();
    let nextId = 1;

    function terminate(worker) {
      try { if (worker && typeof worker.terminate === 'function') worker.terminate(); } catch (error) { /* cleanup is best effort */ }
    }

    function current(job) {
      return jobs.get(job.element) === job && versions.get(job.element) === job.version;
    }

    function cleanup(job) {
      if (job.timer != null) clearTimer(job.timer);
      job.timer = null;
      if (job.worker) {
        job.worker.onmessage = null;
        job.worker.onerror = null;
        terminate(job.worker);
      }
      if (jobs.get(job.element) === job) jobs.delete(job.element);
    }

    function fallback(job) {
      if (!current(job)) return;
      cleanup(job);
      job.element.textContent = job.source;
    }

    function complete(job, event) {
      if (!current(job)) return;
      const message = event && event.data;
      if (!message || message.id !== job.id || typeof message.html !== 'string') {
        fallback(job);
        return;
      }
      cleanup(job);
      job.element.innerHTML = message.html;
    }

    function renderInto(element, value) {
      if (!element) return element;
      const source = text(value);
      const prior = jobs.get(element);
      if (prior) cleanup(prior);
      const version = (versions.get(element) || 0) + 1;
      versions.set(element, version);

      let worker;
      try { worker = createWorker(); } catch (error) {
        element.textContent = source;
        return element;
      }

      const job = { element, source, version, id: nextId++, worker, timer: null };
      jobs.set(element, job);
      if (!worker || typeof worker.postMessage !== 'function') {
        fallback(job);
        return element;
      }

      worker.onmessage = event => complete(job, event);
      worker.onerror = () => fallback(job);
      job.timer = setTimer(() => fallback(job), timeoutMs);
      try {
        worker.postMessage({ id: job.id, text: source });
      } catch (error) {
        fallback(job);
      }
      return element;
    }

    return { renderInto };
  }

  return { createMarkdownRenderCoordinator };
}));
```

- [ ] **Step 4: Run the focused coordinator test to verify it passes**

Run: `node --test test/markdown-rendering-coordinator.test.js`

Expected: PASS; no failure path writes input through `innerHTML`, stale callbacks are ignored, and each finished or failed Worker is terminated.

- [ ] **Step 5: Commit the coordination layer**

```bash
git add -- public/markdown-rendering-coordinator.js test/markdown-rendering-coordinator.test.js
git diff --cached --check
git commit -m "feat: isolate markdown rendering jobs"
```

### Task 3: Wire the application without changing existing call sites

**Files:**

- Modify: `public/index.html:543-544`
- Modify: `public/app.js:25-51`
- Create: `test/markdown-rendering-page-integration.test.js`

- [ ] **Step 1: Write the failing page wiring test**

Create `test/markdown-rendering-page-integration.test.js`. It must lock the required script ordering and ensure the original `renderMd(el, text)` remains a simple delegation, with no Marked, KaTeX or direct `innerHTML` work left in that function.

```js
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');

const root = path.resolve(__dirname, '..');
const index = fs.readFileSync(path.join(root, 'public', 'index.html'), 'utf8');
const app = fs.readFileSync(path.join(root, 'public', 'app.js'), 'utf8');

test('page delegates Markdown rendering to the Worker coordinator', () => {
  assert.match(index, /<script\s+src=["']ingest-rendering\.js["']><\/script>\s*<script\s+src=["']markdown-rendering-coordinator\.js["']><\/script>\s*<script\s+src=["']app\.js["']><\/script>/);
  assert.match(app, /const\s+markdownRenderer\s*=\s*window\.MarkdownRenderingCoordinator\.createMarkdownRenderCoordinator\(\)\s*;/);

  const match = app.match(/function\s+renderMd\s*\(\s*el\s*,\s*text\s*\)\s*\{([\s\S]*?)\n\}/);
  assert.ok(match, 'expected renderMd function');
  assert.match(match[1], /return\s+markdownRenderer\.renderInto\(\s*el\s*,\s*text\s*\)\s*;/);
  assert.doesNotMatch(match[1], /\bmarked\b|\bkatex\b|innerHTML/);
});
```

- [ ] **Step 2: Run the wiring test to verify it fails**

Run: `node --test test/markdown-rendering-page-integration.test.js`

Expected: FAIL because the coordinator is not loaded and `renderMd` still synchronously parses Markdown on the main thread.

- [ ] **Step 3: Replace only the page-side renderer wiring**

In `public/index.html`, add the coordinator immediately before `app.js`:

```html
<script src="ingest-rendering.js"></script>
<script src="markdown-rendering-coordinator.js"></script>
<script src="app.js"></script>
```

In `public/app.js`, remove the unused `md` helper and replace the old formula extraction / `innerHTML` implementation with exactly:

```js
const markdownRenderer = window.MarkdownRenderingCoordinator.createMarkdownRenderCoordinator();

function renderMd(el, text) {
  return markdownRenderer.renderInto(el, text);
}
```

Keep every existing `renderMd(...)` call unchanged. Do not add `markdown-rendering.js` as a main-page script: it is loaded only by `markdown-rendering-worker.js` and directly by Node tests.

- [ ] **Step 4: Run all Markdown-focused tests to verify they pass together**

Run: `node --test test/markdown-rendering.test.js test/markdown-rendering-worker.test.js test/markdown-rendering-coordinator.test.js test/markdown-rendering-page-integration.test.js`

Expected: PASS; the core safety suite and all Worker / page integration tests are green.

- [ ] **Step 5: Commit the application wiring**

```bash
git add -- public/index.html public/app.js test/markdown-rendering-page-integration.test.js
git diff --cached --check
git commit -m "feat: wire worker markdown rendering"
```

### Task 4: Verify the complete application behavior

**Files:**

- Verify only; do not modify unrelated files.

- [ ] **Step 1: Run static syntax and the full Node suite**

Run:

```bash
node --check public/markdown-rendering.js
node --check public/markdown-rendering-worker.js
node --check public/markdown-rendering-coordinator.js
npm.cmd test
```

Expected: every syntax check exits 0 and the full Node suite passes.

- [ ] **Step 2: Run the Python regression suite**

Run: `.venv\Scripts\python.exe -m unittest discover -s test -p "test_*.py"`

Expected: all Python tests pass. If the local virtual environment is absent, record that precise environmental limitation and do not alter dependencies.

- [ ] **Step 3: Perform a browser smoke test**

Start the existing application and verify a normal note / explanation renders Markdown and all four math delimiters (`$...$`, `$$...$$`, `\(...\)`, `\[...\]`). Then submit or temporarily display each of the following inputs in a Markdown target:

```text
<img src=x onerror=alert(1)> [bad](javascript:alert(1)) ![remote](https://example.com/x.png)
```

```text
$ + <a repeated 30000 times + $
```

Expected: the hostile markup is literal or safely stripped on the normal Worker path; the pathological input becomes literal source after roughly 200ms and the page remains interactive. Restore any temporary UI data after the check.

- [ ] **Step 4: Inspect the final diff and branch state**

Run:

```bash
git status --short --branch
git log --oneline -4
git diff main...HEAD --check
```

Expected: only the intentional Worker-rendering files and their commits differ from `main`; `package-lock.json` remains unstaged and untouched.

- [ ] **Step 5: Request two-stage code review before integration**

Ask one independent reviewer to inspect correctness and test coverage, and a second reviewer to focus on safety, Worker lifecycle, stale responses and browser compatibility. Address every substantiated finding, rerun the affected tests, then rerun the full verification commands above before claiming completion.
