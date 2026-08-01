# 采集流程不可信文本安全渲染 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 防止采集向导把查询词、流式进度和外部候选元数据当作 HTML 解析，同时保持原有采集交互。

**Architecture:** 新建 public/ingest-rendering.js，作为采集查询词、进度详情和候选卡片的安全 DOM module。public/app.js 继续保存采集状态和发起请求，只通过这个 module 渲染不可信文本；文本必须用 textContent 或 title 属性写入，class、索引和进度只由可信代码生成。

**Tech Stack:** 浏览器原生 DOM、CommonJS node --test、现有静态前端脚本。

---

## File Structure

- Create: public/ingest-rendering.js — 查询词、进度详情和候选卡片的安全 DOM 接口。
- Modify: public/index.html — 在 app.js 前载入该 module。
- Modify: public/app.js:1701-1850 — 调用安全 module，保留采集状态、请求和事件绑定。
- Create: test/ingest-rendering.test.js — 用轻量 fake DOM 验证文本、属性和勾选行为。

### Task 1: 为安全查询词渲染写出红色测试

**Files:**
- Create: test/ingest-rendering.test.js

- [ ] **Step 1: 写入测试基础设施和查询词失败测试**

```js
const assert = require('node:assert/strict');
const test = require('node:test');
const { createIngestRenderer } = require('../public/ingest-rendering');

class FakeElement {
  constructor(tagName = 'div') {
    this.tagName = tagName;
    this.children = [];
    this.dataset = {};
    this.className = '';
    this.textContent = '';
    this.title = '';
    this.type = '';
    this.disabled = false;
    this.checked = false;
    this.style = {};
    this.listeners = {};
  }
  append(...nodes) { this.children.push(...nodes); }
  replaceChildren(...nodes) { this.children = nodes; }
  addEventListener(name, listener) { this.listeners[name] = listener; }
  click() { this.listeners.click?.({ stopPropagation() {} }); }
}

const fakeDocument = () => ({
  createElement: tagName => new FakeElement(tagName),
});

test('query chips display hostile text literally and retain indexed removal', () => {
  const box = new FakeElement();
  const removed = [];
  const renderer = createIngestRenderer({ document: fakeDocument() });
  const payload = '<img src=x onerror=alert(1)>';

  renderer.renderQueryChips(box, [payload], index => removed.push(index));

  const chip = box.children[0];
  const remove = chip.children[1];
  assert.equal(box.dataset.qs, JSON.stringify([payload]));
  assert.equal(chip.className, 'iq-chip');
  assert.equal(chip.children[0].textContent, payload);
  assert.equal(chip.children[0].children.length, 0);
  assert.equal(remove.className, 'iq-x');
  assert.equal(remove.dataset.i, '0');
  remove.click();
  assert.deepEqual(removed, [0]);
});

test('query chips show the existing empty placeholder for invalid input', () => {
  const box = new FakeElement();
  const renderer = createIngestRenderer({ document: fakeDocument() });

  renderer.renderQueryChips(box, null, () => {});

  assert.equal(box.dataset.qs, '[]');
  assert.equal(box.children[0].className, 'placeholder');
  assert.equal(box.children[0].textContent, '（无检索词）');
});
```

- [ ] **Step 2: 运行测试，确认在 module 尚不存在时失败**

Run: `node --test test/ingest-rendering.test.js`

Expected: FAIL，提示无法找到 ../public/ingest-rendering。

### Task 2: 实现安全查询词与进度详情 module

**Files:**
- Create: public/ingest-rendering.js
- Modify: test/ingest-rendering.test.js

- [ ] **Step 1: 为进度详情添加失败测试**

```js
test('status detail writes hostile text literally and toggles the warning class', () => {
  const main = new FakeElement();
  const sub = new FakeElement();
  const renderer = createIngestRenderer({ document: fakeDocument() });

  renderer.setDetail(main, sub, '<svg onload=alert(1)>', '<b>source failed</b>', true);

  assert.equal(main.textContent, '<svg onload=alert(1)>');
  assert.equal(sub.textContent, '<b>source failed</b>');
  assert.equal(sub.className, 'ingd-sub ingd-warn');
});
```

- [ ] **Step 2: 运行测试，确认 module 尚不存在而失败**

Run: `node --test test/ingest-rendering.test.js`

Expected: FAIL，提示无法找到 ../public/ingest-rendering。

- [ ] **Step 3: 创建 browser/CommonJS 兼容 module 并实现两个接口**

```js
(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  if (root) root.IngestRendering = api;
})(typeof window !== 'undefined' ? window : globalThis, function () {
  function createIngestRenderer({ document }) {
    function element(tagName, className, text) {
      const node = document.createElement(tagName);
      node.className = className || '';
      if (text != null) node.textContent = String(text);
      return node;
    }

    function renderQueryChips(box, values, onRemove) {
      const queries = Array.isArray(values) ? values.map(value => String(value)) : [];
      box.dataset.qs = JSON.stringify(queries);
      if (!queries.length) {
        box.replaceChildren(element('span', 'placeholder', '（无检索词）'));
        return;
      }
      const chips = queries.map((query, index) => {
        const chip = element('span', 'iq-chip');
        const label = element('span', '', query);
        const remove = element('button', 'iq-x', '×');
        remove.type = 'button';
        remove.dataset.i = String(index);
        remove.addEventListener('click', () => onRemove(index));
        chip.append(label, remove);
        return chip;
      });
      box.replaceChildren(...chips);
    }

    function setDetail(main, sub, mainText, subText, warning = false) {
      if (mainText != null && main) main.textContent = String(mainText);
      if (subText != null && sub) {
        sub.textContent = String(subText);
        sub.className = warning ? 'ingd-sub ingd-warn' : 'ingd-sub';
      }
    }

    return { renderQueryChips, setDetail };
  }

  return { createIngestRenderer };
});
```

- [ ] **Step 4: 运行测试，确认查询词和状态详情均通过**

Run: `node --test test/ingest-rendering.test.js`

Expected: PASS，3 tests、0 failures。

- [ ] **Step 5: 提交查询词和状态详情实现**

```bash
git add public/ingest-rendering.js test/ingest-rendering.test.js
git commit -m "fix: safely render ingest queries and progress"
```

### Task 3: 为候选论文卡片写出红色测试

**Files:**
- Modify: test/ingest-rendering.test.js

- [ ] **Step 1: 添加带恶意论文元数据的候选卡片测试**

```js
function findByClass(node, className) {
  const required = className.split(/\s+/);
  const actual = String(node.className || '').split(/\s+/);
  if (required.every(name => actual.includes(name))) return node;
  for (const child of node.children || []) {
    const found = findByClass(child, className);
    if (found) return found;
  }
  return null;
}

test('candidate card keeps hostile metadata as text and preserves checkbox state', () => {
  const renderer = createIngestRenderer({ document: fakeDocument() });
  const payload = '<img src=x onerror=alert(1)>';
  const card = renderer.createCandidateCard({
    candidate: {
      title: payload,
      venue: 'CVPR"><svg onload=alert(2)>',
      year: '2026',
      type: '<b>type</b>',
      topic: '<i>topic</i>',
      relevance: 0.73,
      in_library: false,
      ccf: 'A',
      _verify: { skipped: true, note: '<svg onload=alert(3)>', source_of_truth: 'dblp' },
    },
    index: 4,
    venueName: 'CVPR"><svg onload=alert(2)>',
    sourceLabels: { dblp: 'DBLP' },
  });

  const title = findByClass(card, 'cand-title');
  const venue = findByClass(card, 'venue');
  const badge = findByClass(card, 'vbadge src');
  assert.equal(card.children[0].dataset.i, '4');
  assert.equal(card.children[0].checked, true);
  assert.equal(title.textContent, payload);
  assert.equal(title.children.length, 0);
  assert.equal(venue.textContent, 'CVPR"><svg onload=alert(2)> 2026');
  assert.doesNotMatch(venue.className, /[<">]/);
  assert.equal(badge.title, '<svg onload=alert(3)>');
});
```

- [ ] **Step 2: 运行测试，确认候选卡片接口尚不存在**

Run: `node --test test/ingest-rendering.test.js`

Expected: FAIL，提示 renderer.createCandidateCard is not a function。

### Task 4: 实现安全候选论文卡片

**Files:**
- Modify: public/ingest-rendering.js
- Modify: test/ingest-rendering.test.js

- [ ] **Step 1: 在 createIngestRenderer 内加入以下 helper 和候选卡片接口**

```js
function safeVenueClass(value) {
  const token = String(value || '').replace(/[^A-Za-z0-9_-]/g, '');
  return token ? ' v-' + token : '';
}

function createCcfBadge(rank) {
  const value = String(rank || '');
  if (!/^[ABC]$/.test(value)) return null;
  return element('span', 'ccf ccf-' + value, value);
}

function sourceName(value, sourceLabels) {
  return sourceLabels[String(value || '')] || String(value || '');
}

function createVerificationBadge(verification, sourceLabels) {
  if (!verification) return null;
  const source = sourceName(verification.source_of_truth, sourceLabels);
  if (verification.skipped) {
    const badge = element('b', 'vbadge src', '源自' + source);
    badge.title = String(verification.note || '');
    return badge;
  }
  if (verification.matched) {
    const badge = element('b', 'vbadge ok', '✓ 已核实' + (verification.changed ? ' · 已更正' : ''));
    badge.title = '权威来源：' + source;
    return badge;
  }
  const badge = element('b', 'vbadge miss', '仅预印本');
  badge.title = String(verification.note || '');
  return badge;
}

function createCandidateCard({ candidate, index, venueName, sourceLabels }) {
  const paper = candidate || {};
  const card = element('label', 'cand' + (paper.in_library ? ' in-lib' : ''));
  const checkbox = element('input', 'cand-ck');
  checkbox.type = 'checkbox';
  checkbox.dataset.i = String(index);
  checkbox.disabled = !!paper.in_library;
  checkbox.checked = !paper.in_library;

  const main = element('div', 'cand-main');
  main.append(element('div', 'cand-title', paper.title));
  const meta = element('div', 'cand-meta');
  meta.append(element('span', 'venue' + safeVenueClass(venueName), String(venueName || '—') + ' ' + String(paper.year || '')));
  const ccf = createCcfBadge(paper.ccf);
  if (ccf) meta.append(ccf);
  const verification = createVerificationBadge(paper._verify, sourceLabels || {});
  if (verification) meta.append(verification);
  const kind = String(paper.type || '') + (paper.topic ? ' · ' + String(paper.topic) : '');
  if (kind) meta.append(element('span', '', ' · ' + kind));
  if (paper.in_library) meta.append(element('b', 'inlib-tag', ' · 已在库'));
  main.append(meta);

  const relevance = Number(paper.relevance);
  const percent = Number.isFinite(relevance) ? Math.max(0, Math.min(100, Math.round(relevance * 100))) : 0;
  const right = element('div', 'cand-rel');
  right.title = '相关度 ' + percent + '%';
  const track = element('div', 'cand-rel-track');
  const bar = element('div', 'cand-rel-bar');
  bar.style.width = percent + '%';
  track.append(bar);
  right.append(track, element('span', '', percent));
  card.append(checkbox, main, right);
  return card;
}
```

将 createCandidateCard 加入 createIngestRenderer 的返回对象。

- [ ] **Step 2: 运行候选卡片测试**

Run: `node --test test/ingest-rendering.test.js`

Expected: PASS，4 tests、0 failures。

- [ ] **Step 3: 提交候选卡片实现**

```bash
git add public/ingest-rendering.js test/ingest-rendering.test.js
git commit -m "fix: safely render ingest candidates"
```

### Task 5: 将采集向导接入安全 module

**Files:**
- Modify: public/index.html:512-514
- Modify: public/app.js:1701-1850
- Modify: test/ingest-rendering.test.js

- [ ] **Step 1: 添加静态集成失败测试**

```js
const fs = require('node:fs');
const path = require('node:path');
const root = path.resolve(__dirname, '..');
const appSource = fs.readFileSync(path.join(root, 'public', 'app.js'), 'utf8');
const indexSource = fs.readFileSync(path.join(root, 'public', 'index.html'), 'utf8');

test('ingest flow delegates untrusted rendering to IngestRendering', () => {
  assert.match(indexSource, /<script src="ingest-rendering\.js"><\/script>\s*<script src="app\.js">/);
  assert.match(appSource, /const ingestRenderer = window\.IngestRendering\.createIngestRenderer\(\{ document \}\);/);
  assert.doesNotMatch(appSource, /box\.innerHTML = qs\.map/);
  assert.doesNotMatch(appSource, /#ingdSub'\)\.innerHTML = sub/);
  assert.doesNotMatch(appSource, /<div class="cand-title">\$\{c\.title\}<\/div>/);
  assert.match(appSource, /function renderCandidates\(\)[\s\S]*?ingestRenderer\.createCandidateCard/);
});
```

- [ ] **Step 2: 运行测试，确认当前应用尚未接线**

Run: `node --test test/ingest-rendering.test.js`

Expected: FAIL，指出 index.html 或 app.js 尚未使用 IngestRendering。

- [ ] **Step 3: 载入 module 并替换采集路径**

在 public/index.html 的 app.js 前添加：

```html
<script src="ingest-rendering.js"></script>
```

在 public/app.js 的 let candidates = []; 后添加：

```js
const ingestRenderer = window.IngestRendering.createIngestRenderer({ document });
```

替换 renderQueryChips：

```js
function renderQueryChips(qs) {
  ingestRenderer.renderQueryChips($('#ingQueryChips'), qs, index => {
    const next = currentQueries();
    next.splice(index, 1);
    renderQueryChips(next);
  });
}
```

替换 setDetail：

```js
function setDetail(main, sub, warning = false) {
  ingestRenderer.setDetail($('#ingdMain'), $('#ingdSub'), main, sub, warning);
}
```

将 SRCERR:: 分支替换为：

```js
const source = SRC_LABEL[p[1]] || p[1];
setDetail(null, (srcSummary() ? srcSummary() + ' · ' : '') + source + ' 失败', true);
```

将 DOING:: 分支的副文本改为未转义的原始 title，因为 renderer 会把它写入 textContent：

```js
setDetail('分类打分 · 第 ' + idx + ' 篇', '“' + title + '”');
```

将 renderCandidates 的 innerHTML 映射替换为：

```js
const box = $('#candList');
box.replaceChildren();
if (!candidates.length) {
  const empty = document.createElement('div');
  empty.className = 'placeholder';
  empty.textContent = '没有匹配的候选论文。';
  box.append(empty);
} else {
  candidates.forEach((candidate, index) => {
    const venueName = normVenue(candidate.venue) || '';
    box.append(ingestRenderer.createCandidateCard({
      candidate,
      index,
      venueName,
      sourceLabels: SRC_SHORT,
    }));
  });
}
```

保留其后的 #candSelAll 绑定代码不变。

- [ ] **Step 4: 运行新增测试与完整 Node 回归**

Run: `node --test test/ingest-rendering.test.js; npm.cmd test`

Expected: 两个命令均 exit 0；新增测试及既有 Node 测试均无失败。

- [ ] **Step 5: 浏览器手工验证**

Run: `node server.js`

在采集页验证：

1. 输入 &lt;img src=x onerror=alert(1)&gt; 并按回车，词条显示字面文本且没有新增 image。
2. 删除该词条，确认其余词条与检索按钮可用。
3. 执行扩词和普通检索，确认词条、进度、候选卡片、全选和勾选正常。
4. 在浅色和深色主题下，数据源失败状态仍使用警告色。

- [ ] **Step 6: 提交前端接线**

```bash
git add public/index.html public/app.js public/ingest-rendering.js test/ingest-rendering.test.js
git commit -m "fix: harden ingest rendering"
```

### Task 6: 最终验证

**Files:**
- Verify: docs/superpowers/specs/2026-08-01-ingest-rendering-safety-design.md
- Verify: public/ingest-rendering.js
- Verify: public/app.js
- Verify: test/ingest-rendering.test.js

- [ ] **Step 1: 核对规格**

确认查询词、状态详情和候选元数据均由安全 DOM module 输出；确认没有修改接口、数据库、Markdown、KaTeX 或其他页面。

- [ ] **Step 2: 运行完整回归**

Run:

```powershell
npm.cmd test
.\.venv\Scripts\python.exe -m unittest discover -s test -p "test_*.py"
git diff --check
```

Expected: 三个命令均 exit 0。

- [ ] **Step 3: 检查工作树归属**

Run: `git status --short`

Expected: 不暂存或修改用户已有的 AGENTS.md。
