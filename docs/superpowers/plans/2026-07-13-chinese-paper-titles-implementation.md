# Chinese Paper Titles Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Persist LLM-generated Chinese paper titles and show them as secondary lines beneath the original English titles throughout the research workspace.

**Architecture:** Add a nullable `papers.title_zh` column with an idempotent migration and guarded repository methods. Put title cleaning and sequential batch orchestration in a focused Node module that receives the database repository and LLM chat function as dependencies; expose it through a small NDJSON API. Put title formatting and search text in a browser/CommonJS helper so the home, reading, review, and management views share one tested rule.

**Tech Stack:** Node.js CommonJS, `better-sqlite3`, Node test runner, existing OpenAI-compatible chat endpoint, vanilla HTML/CSS/JavaScript, existing NDJSON streaming helper.

## Global Constraints

- Keep `papers.title` as the original paper title and stable source of truth.
- Store the Chinese title separately in nullable `papers.title_zh`.
- Do not call the LLM automatically when a paper is collected or imported.
- Batch generation processes only rows whose `title_zh` is null or blank.
- Never overwrite an existing or manually edited Chinese title during batch generation.
- Display English as the primary line and Chinese as the secondary line.
- Use the existing model settings and add no runtime dependency.
- Preserve the user's unrelated `AGENTS.md` timestamp change.

---

### Task 1: Persist Chinese titles safely

**Files:**
- Modify: `db/schema.sql:4-36`
- Modify: `db.js:12-21,63-82,126-168,230-236`
- Modify: `lib/reviews.js:68-86`
- Modify: `docs/DATABASE.md`
- Create: `test/title-translations.test.js`

**Interfaces:**
- Produces: `papers.title_zh: string | null`
- Produces: `dbapi.countMissingTitleTranslations(): number`
- Produces: `dbapi.listMissingTitleTranslations(limit?: number): Array<{id:string,title:string}>`
- Produces: `dbapi.setTitleTranslationIfMissing(id:string, titleZh:string): number`
- Produces: `listPapers()` and review rows containing `title_zh`

- [ ] **Step 1: Write failing migration and repository tests**

Create `test/title-translations.test.js` with an isolated-database loader and these assertions:

```js
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const Database = require('better-sqlite3');
const test = require('node:test');

function loadIsolatedDb(dbPath) {
  const previous = process.env.DB_PATH;
  process.env.DB_PATH = dbPath;
  delete require.cache[require.resolve('../db')];
  const dbapi = require('../db');
  return {
    dbapi,
    cleanup() {
      dbapi.db.close();
      delete require.cache[require.resolve('../db')];
      if (previous == null) delete process.env.DB_PATH;
      else process.env.DB_PATH = previous;
    }
  };
}

test('existing databases gain a nullable title_zh column without losing papers', () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'study-app-title-'));
  const dbPath = path.join(root, 'legacy.db');
  const legacy = new Database(dbPath);
  const currentSchema = fs.readFileSync(path.join(__dirname, '..', 'db', 'schema.sql'), 'utf8');
  const legacySchema = currentSchema.replace(/^\s*title_zh\s+TEXT[^\n]*\r?\n/m, '');
  legacy.exec(legacySchema);
  legacy.prepare("INSERT INTO papers(id,source,title) VALUES('p1','manual','Original Title')").run();
  legacy.close();

  const loaded = loadIsolatedDb(dbPath);
  try {
    const columns = loaded.dbapi.db.prepare("SELECT name FROM pragma_table_info('papers')").all().map(row => row.name);
    assert.ok(columns.includes('title_zh'));
    assert.deepEqual(loaded.dbapi.db.prepare("SELECT title,title_zh FROM papers WHERE id='p1'").get(), {
      title: 'Original Title', title_zh: null
    });
  } finally {
    loaded.cleanup();
    fs.rmSync(root, { recursive: true, force: true });
  }
});

test('title translation repository lists blanks and never overwrites an existing value', () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'study-app-title-'));
  const loaded = loadIsolatedDb(path.join(root, 'app.db'));
  try {
    const insert = loaded.dbapi.db.prepare(
      'INSERT INTO papers(id,source,title,title_zh) VALUES(?,?,?,?)'
    );
    insert.run('p1', 'manual', 'First Paper', null);
    insert.run('p2', 'manual', 'Second Paper', '第二篇论文');

    assert.equal(loaded.dbapi.countMissingTitleTranslations(), 1);
    assert.deepEqual(loaded.dbapi.listMissingTitleTranslations(), [{ id: 'p1', title: 'First Paper' }]);
    assert.equal(loaded.dbapi.setTitleTranslationIfMissing('p1', '第一篇论文'), 1);
    assert.equal(loaded.dbapi.setTitleTranslationIfMissing('p1', '覆盖文本'), 0);
    assert.equal(loaded.dbapi.getPaper('p1').title_zh, '第一篇论文');
    assert.equal(loaded.dbapi.getPaper('p2').title_zh, '第二篇论文');
  } finally {
    loaded.cleanup();
    fs.rmSync(root, { recursive: true, force: true });
  }
});
```

- [ ] **Step 2: Run the focused tests and verify the red state**

Run: `node --test test/title-translations.test.js`

Expected: FAIL because the legacy database lacks `title_zh` and the three repository methods do not exist.

- [ ] **Step 3: Add the schema column, idempotent upgrade, and repository methods**

Add `title_zh TEXT` immediately after `title TEXT NOT NULL` in `db/schema.sql`. In `db.js`, extend the existing incremental-column loop with a dedicated papers migration:

```js
const titleZhColumn = db.prepare("SELECT 1 FROM pragma_table_info('papers') WHERE name = 'title_zh'").get();
if (!titleZhColumn) db.exec('ALTER TABLE papers ADD COLUMN title_zh TEXT');
```

Include `p.title_zh` in `listPapers()`, add `title_zh` to `EDITABLE`, and include `title_zh` in `addPaper()`'s insert columns and parameter object. Add these guarded repository methods before `module.exports`:

```js
const countMissingTitleTranslations = () => db.prepare(`
  SELECT COUNT(*) AS count
  FROM papers
  WHERE TRIM(COALESCE(title, '')) <> ''
    AND TRIM(COALESCE(title_zh, '')) = ''
`).get().count;

const listMissingTitleTranslations = (limit = 0) => {
  const n = Math.max(0, Math.trunc(Number(limit) || 0));
  const sql = `SELECT id, title FROM papers
    WHERE TRIM(COALESCE(title, '')) <> ''
      AND TRIM(COALESCE(title_zh, '')) = ''
    ORDER BY created_at ASC, id ASC${n ? ' LIMIT ?' : ''}`;
  return n ? db.prepare(sql).all(n) : db.prepare(sql).all();
};

const setTitleTranslationIfMissing = (id, titleZh) => db.prepare(`
  UPDATE papers
  SET title_zh = ?, updated_at = datetime('now')
  WHERE id = ? AND TRIM(COALESCE(title_zh, '')) = ''
`).run(String(titleZh || '').trim(), id).changes;
```

Export all three methods. Add `p.title_zh` to the review list query in `lib/reviews.js`. Document `title_zh` in the papers field table in `docs/DATABASE.md`.

- [ ] **Step 4: Run the focused and review tests**

Run: `node --test test/title-translations.test.js test/reviews.test.js`

Expected: PASS, including the existing review scheduler tests.

- [ ] **Step 5: Commit the persistence slice**

```powershell
git add db/schema.sql db.js lib/reviews.js docs/DATABASE.md test/title-translations.test.js
git commit -m "feat: persist Chinese paper titles"
```

---

### Task 2: Build the LLM title translation service

**Files:**
- Create: `lib/title-translations.js`
- Modify: `test/title-translations.test.js`

**Interfaces:**
- Consumes: repository methods from Task 1
- Consumes: `chat(messages, options): Promise<string>`
- Produces: `cleanTitleTranslation(value): string`
- Produces: `createTitleTranslationService({repository, chat})`
- Produces: `service.pendingCount(): number`
- Produces: `service.runBatch({limit,isCancelled,onEvent}): Promise<{total:number,done:number,failed:Array,cancelled:boolean}>`

- [ ] **Step 1: Add failing cleaner and batch tests**

Append tests that require the new module and prove cleaning, partial failure, and cancellation:

```js
const {
  cleanTitleTranslation,
  createTitleTranslationService
} = require('../lib/title-translations');

test('Chinese title cleaner accepts one academic title and rejects explanations', () => {
  assert.equal(cleanTitleTranslation('“验证链减少大型语言模型幻觉”'), '验证链减少大型语言模型幻觉');
  assert.equal(cleanTitleTranslation('中文标题：视觉变化缓解多模态大模型幻觉'), '视觉变化缓解多模态大模型幻觉');
  assert.equal(cleanTitleTranslation('Translation only'), '');
  assert.equal(cleanTitleTranslation('标题一\n补充说明'), '');
});

test('batch translation continues after one failure and persists only valid results', async () => {
  const saved = [];
  const events = [];
  const repository = {
    countMissingTitleTranslations: () => 3,
    listMissingTitleTranslations: () => [
      { id: 'p1', title: 'First Paper' },
      { id: 'p2', title: 'Second Paper' },
      { id: 'p3', title: 'Third Paper' }
    ],
    setTitleTranslationIfMissing(id, titleZh) { saved.push([id, titleZh]); return 1; }
  };
  const chat = async (_messages, _options) => {
    const title = _messages.at(-1).content;
    if (title === 'Second Paper') throw new Error('timeout');
    return title === 'First Paper' ? '第一篇论文' : '第三篇论文';
  };
  const service = createTitleTranslationService({ repository, chat });

  const summary = await service.runBatch({ onEvent: event => events.push(event) });

  assert.deepEqual(saved, [['p1', '第一篇论文'], ['p3', '第三篇论文']]);
  assert.equal(summary.total, 3);
  assert.equal(summary.done, 2);
  assert.equal(summary.failed.length, 1);
  assert.equal(summary.failed[0].id, 'p2');
  assert.ok(events.some(event => event.stage === 'item' && event.state === 'failed'));
});

test('batch translation stops before starting the next paper when cancelled', async () => {
  let cancelled = false;
  const saved = [];
  const service = createTitleTranslationService({
    repository: {
      countMissingTitleTranslations: () => 2,
      listMissingTitleTranslations: () => [
        { id: 'p1', title: 'First Paper' },
        { id: 'p2', title: 'Second Paper' }
      ],
      setTitleTranslationIfMissing(id, titleZh) { saved.push([id, titleZh]); cancelled = true; return 1; }
    },
    chat: async () => '中文题名'
  });

  const summary = await service.runBatch({ isCancelled: () => cancelled });

  assert.equal(summary.cancelled, true);
  assert.deepEqual(saved, [['p1', '中文题名']]);
});
```

- [ ] **Step 2: Run the tests and verify the red state**

Run: `node --test test/title-translations.test.js`

Expected: FAIL with `Cannot find module '../lib/title-translations'`.

- [ ] **Step 3: Implement the focused service**

Create `lib/title-translations.js` with this public shape and validation policy:

```js
const TITLE_TRANSLATION_SYSTEM = [
  '你是专业的学术论文题名翻译助手。',
  '把英文论文题名翻译为忠实、精炼、自然的简体中文学术题名。',
  '保留必要的模型名、数据集名、缩写与专有名词。',
  '只返回一行中文题名，不要引号、Markdown、标签或解释。'
].join('\n');

function cleanTitleTranslation(value) {
  let text = String(value || '').trim()
    .replace(/^```(?:text|markdown)?\s*/i, '')
    .replace(/\s*```$/, '')
    .trim();
  const lines = text.split(/\r?\n/).map(line => line.trim()).filter(Boolean);
  if (lines.length !== 1) return '';
  text = lines[0]
    .replace(/^(?:中文(?:标题|题名|翻译)|译名)\s*[：:]\s*/i, '')
    .replace(/^[“”"'‘’]+|[“”"'‘’]+$/g, '')
    .trim();
  if (!/[\u3400-\u9fff]/.test(text) || text.length > 300) return '';
  return text;
}

function createTitleTranslationService({ repository, chat }) {
  if (!repository || typeof chat !== 'function') throw new TypeError('repository and chat are required');
  return {
    pendingCount: () => repository.countMissingTitleTranslations(),
    async runBatch({ limit = 0, isCancelled = () => false, onEvent = () => {} } = {}) {
      const rows = repository.listMissingTitleTranslations(limit);
      const summary = { total: rows.length, done: 0, failed: [], cancelled: false };
      onEvent({ type: 'progress', stage: 'batch', total: rows.length });
      for (let index = 0; index < rows.length; index += 1) {
        if (isCancelled()) { summary.cancelled = true; break; }
        const row = rows[index];
        onEvent({ type: 'progress', stage: 'item', state: 'start', index: index + 1, total: rows.length, id: row.id, title: row.title });
        try {
          const raw = await chat([
            { role: 'system', content: TITLE_TRANSLATION_SYSTEM },
            { role: 'user', content: row.title }
          ], { temperature: 0.1, timeoutMs: 45000 });
          const titleZh = cleanTitleTranslation(raw);
          if (!titleZh) throw new Error('大模型未返回有效中文题名');
          const changed = repository.setTitleTranslationIfMissing(row.id, titleZh);
          if (changed) summary.done += 1;
          onEvent({ type: 'progress', stage: 'item', state: changed ? 'done' : 'skipped', index: index + 1, total: rows.length, id: row.id, title_zh: titleZh });
        } catch (error) {
          const failure = { id: row.id, title: row.title, error: String(error && error.message || error) };
          summary.failed.push(failure);
          onEvent({ type: 'progress', stage: 'item', state: 'failed', index: index + 1, total: rows.length, ...failure });
        }
      }
      return summary;
    }
  };
}

module.exports = { TITLE_TRANSLATION_SYSTEM, cleanTitleTranslation, createTitleTranslationService };
```

- [ ] **Step 4: Run the focused tests**

Run: `node --test test/title-translations.test.js`

Expected: PASS.

- [ ] **Step 5: Commit the service slice**

```powershell
git add lib/title-translations.js test/title-translations.test.js
git commit -m "feat: add Chinese title translation service"
```

---

### Task 3: Expose pending count and cancellable NDJSON batch API

**Files:**
- Modify: `server.js:10-17,51-89,97-122,263-295`

**Interfaces:**
- Consumes: `createTitleTranslationService` from Task 2
- Produces: `GET /api/title-translations` returning `{ok:true,pending:number}`
- Produces: `POST /api/title-translations` streaming progress events and one final result event

- [ ] **Step 1: Add the service import and instance**

At the top of `server.js`, import the factory. Instantiate it after `llmChat` so it reuses the current settings-backed OpenAI-compatible client:

```js
const { createTitleTranslationService } = require('./lib/title-translations');

const titleTranslationService = createTitleTranslationService({
  repository: dbapi,
  chat: llmChat
});
```

- [ ] **Step 2: Add the GET and POST routes**

Place these routes near `/api/papers` and the other LLM batch endpoints:

```js
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
```

- [ ] **Step 3: Verify syntax and service tests**

Run: `node --check server.js`

Expected: no output and exit code 0.

Run: `node --test test/title-translations.test.js`

Expected: PASS.

- [ ] **Step 4: Commit the API slice**

```powershell
git add server.js
git commit -m "feat: expose Chinese title batch API"
```

---

### Task 4: Share title rendering and Chinese search across views

**Files:**
- Create: `public/paper-titles.js`
- Modify: `public/index.html:650-675`
- Modify: `public/app.js:23-27,262-280,445-475,685-758,1424-1468`
- Modify: `public/style.css:150-220,285-360,500-560`
- Modify: `public/academic.css:270-455`
- Modify: `test/server-modules.test.js:1-25`

**Interfaces:**
- Produces: `PaperTitles.titleLines(paper)`
- Produces: `PaperTitles.searchableTitle(paper)`
- Produces: `PaperTitles.titleMarkup(paper, classes?)`

- [ ] **Step 1: Write failing shared-helper tests**

Import the future helper in `test/server-modules.test.js` and add:

```js
const { searchableTitle, titleLines, titleMarkup } = require('../public/paper-titles');

test('paper title helper keeps English primary, Chinese secondary, and searchable', () => {
  const paper = {
    title: 'Chain-of-Verification Reduces Hallucination',
    title_zh: '验证链减少幻觉'
  };
  assert.deepEqual(titleLines(paper), {
    primary: 'Chain-of-Verification Reduces Hallucination',
    secondary: '验证链减少幻觉'
  });
  assert.match(searchableTitle(paper), /验证链减少幻觉/);
  assert.match(titleMarkup(paper), /paper-title-secondary/);
  assert.match(titleMarkup(paper), /验证链减少幻觉/);
  assert.doesNotMatch(titleMarkup({ title: 'English Only' }), /paper-title-secondary/);
});

test('paper title helper escapes model and source text', () => {
  const html = titleMarkup({ title: '<img src=x>', title_zh: '<script>中文</script>' });
  assert.doesNotMatch(html, /<img|<script>/);
  assert.match(html, /&lt;img/);
});
```

- [ ] **Step 2: Run the helper tests and verify the red state**

Run: `node --test test/server-modules.test.js`

Expected: FAIL with `Cannot find module '../public/paper-titles'`.

- [ ] **Step 3: Create the UMD title helper**

Create `public/paper-titles.js`:

```js
(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  if (root) root.PaperTitles = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  const escapeHtml = value => String(value == null ? '' : value).replace(/[&<>"']/g, char => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
  })[char]);

  function titleLines(paper) {
    const row = paper || {};
    return {
      primary: String(row.title || '').trim(),
      secondary: String(row.title_zh || '').trim()
    };
  }

  function searchableTitle(paper) {
    const lines = titleLines(paper);
    return `${lines.primary} ${lines.secondary}`.trim();
  }

  function titleMarkup(paper, classes = {}) {
    const lines = titleLines(paper);
    const rootClass = classes.root || 'paper-title-stack';
    const primaryClass = classes.primary || 'paper-title-primary';
    const secondaryClass = classes.secondary || 'paper-title-secondary';
    return `<span class="${escapeHtml(rootClass)}"><span class="${escapeHtml(primaryClass)}">${escapeHtml(lines.primary)}</span>${
      lines.secondary ? `<span class="${escapeHtml(secondaryClass)}">${escapeHtml(lines.secondary)}</span>` : ''
    }</span>`;
  }

  return { escapeHtml, searchableTitle, titleLines, titleMarkup };
});
```

Load it immediately before `app.js` in `public/index.html`.

- [ ] **Step 4: Replace title and search duplication in all four surfaces**

In `public/app.js`, add aliases near `esc`:

```js
const titleMarkup = (paper, classes) => window.PaperTitles.titleMarkup(paper, classes);
const titleSearch = paper => window.PaperTitles.searchableTitle(paper);
```

Use `titleSearch(p)` in the home, reading-sidebar, and management text filters. Use `titleMarkup(...)` in `rowHTML`, `paperItem`, `reviewCard`, `renderManage`, and `openPaper`'s `#paperTitle`. Keep `#pdfDocTitle` compact with the English title and venue/year. Merge review API data with the matching `PAPERS` row so `title_zh` is available even during a refresh:

```js
const paper = { ...p, ...item, title: item.title || p.title || item.paper_id };
```

Add stable two-line styles without fixed heights:

```css
.paper-title-stack{display:flex;min-width:0;flex-direction:column;gap:2px}
.paper-title-primary{min-width:0;color:inherit;font:inherit;line-height:inherit}
.paper-title-secondary{min-width:0;color:var(--ink-3);font-size:.86em;font-weight:500;line-height:1.35}
.ht-title .paper-title-stack,.m-item-title .paper-title-stack{display:inline-flex;vertical-align:middle}
.pi-title .paper-title-primary,.m-item-title .paper-title-primary{overflow:hidden;text-overflow:ellipsis}
.review-title .paper-title-secondary,.paper-title .paper-title-secondary{margin-top:2px}
```

Remove `white-space: nowrap` only from title containers that now hold two lines; retain ellipsis on each primary line so narrow sidebars remain stable.

- [ ] **Step 5: Run helper and existing frontend-adjacent tests**

Run: `node --test test/server-modules.test.js test/reviews.test.js`

Expected: PASS.

- [ ] **Step 6: Commit the shared rendering slice**

```powershell
git add public/paper-titles.js public/index.html public/app.js public/style.css public/academic.css test/server-modules.test.js
git commit -m "feat: show Chinese titles across paper views"
```

---

### Task 5: Add manual editing and the stoppable management workflow

**Files:**
- Modify: `public/index.html:198-260`
- Modify: `public/app.js:18-21,214-230,1302-1365,1424-1468,1591-1625,1631-1648,1887-1893`
- Modify: `public/style.css:525-560`
- Modify: `public/academic.css:180-220,530-575`
- Modify: `README.md`

**Interfaces:**
- Consumes: `GET/POST /api/title-translations` from Task 3
- Consumes: existing `/api/paper/update` with `title_zh` enabled in Task 1
- Produces: management batch button, progress summary, stop action, and modal field

- [ ] **Step 1: Add management and modal controls**

Add this compact action next to the manual-add button in the library toolbar:

```html
<button id="titleZhBatchBtn" class="mini" title="只为尚无中文题名的论文调用大模型">生成中文题名</button>
<span id="titleZhBatchHint" class="hint title-zh-batch-hint"></span>
```

Add this field directly below the required English title field:

```html
<label class="pm-field"><span>中文题名</span><input id="pmTitleZh" placeholder="可手动填写；留空可参加批量生成" /></label>
```

- [ ] **Step 2: Extend manual editing and cancellable NDJSON transport**

Add `title_zh: 'pmTitleZh'` to `PM_FIELDS`. Extend `streamNDJSON` without changing existing callers:

```js
async function streamNDJSON(url, body, onEvent, options = {}) {
  const resp = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
    signal: options.signal
  });
  // Keep the current reader and line-decoding body unchanged.
}
```

- [ ] **Step 3: Implement pending-count refresh, progress, and stop behavior**

Add one controller and two functions near the management code:

```js
let titleZhAbort = null;

async function refreshTitleTranslationBatch() {
  if (titleZhAbort) return;
  const button = $('#titleZhBatchBtn');
  const hint = $('#titleZhBatchHint');
  try {
    const result = await (await fetch('/api/title-translations')).json();
    const pending = Number(result.pending) || 0;
    button.disabled = pending === 0;
    button.textContent = pending ? `生成中文题名 · ${pending}` : '中文题名已补全';
    hint.textContent = pending ? `待翻译 ${pending} 篇` : '';
  } catch (error) {
    button.disabled = false;
    hint.textContent = '暂时无法读取待翻译数量';
  }
}

async function runTitleTranslationBatch() {
  if (titleZhAbort) { titleZhAbort.abort(); return; }
  const button = $('#titleZhBatchBtn');
  const hint = $('#titleZhBatchHint');
  const controller = new AbortController();
  titleZhAbort = controller;
  button.disabled = false;
  button.textContent = '停止生成';
  try {
    await streamNDJSON('/api/title-translations', {}, event => {
      if (event.type === 'progress' && event.stage === 'batch') hint.textContent = `共 ${event.total} 篇，逐篇翻译中`;
      if (event.type === 'progress' && event.stage === 'item' && event.state === 'start') hint.textContent = `${event.index}/${event.total} · ${event.title}`;
      if (event.type === 'progress' && event.stage === 'item' && event.state === 'failed') hint.textContent = `${event.index}/${event.total} · 本篇失败，继续处理`;
      if (event.type === 'result') {
        const summary = event.summary || {};
        hint.textContent = `完成 ${summary.done || 0} · 失败 ${(summary.failed || []).length}`;
      }
    }, { signal: controller.signal });
  } catch (error) {
    hint.textContent = error && error.name === 'AbortError' ? '已停止，已生成的题名已保存' : `生成失败：${error}`;
  } finally {
    titleZhAbort = null;
    await reloadPapers();
    renderManage();
  }
}
```

Bind the button to `runTitleTranslationBatch`. Call `refreshTitleTranslationBatch()` when entering the management view and at the end of `reloadPapers()` when `currentView === 'manage'`. During an active run, the same button remains enabled and changes to “停止生成”.

- [ ] **Step 4: Add responsive management styles**

Keep the hint compact and prevent long current titles from resizing the toolbar:

```css
.title-zh-batch-hint{max-width:260px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
#titleZhBatchBtn{flex:0 0 auto}
@media (max-width:760px){.title-zh-batch-hint{order:10;max-width:100%;width:100%}}
```

Add a short README entry under the daily workflow: open Management, configure the model, run “生成中文题名”, and edit individual translations from the paper edit action.

- [ ] **Step 5: Run syntax and automated regression tests**

Run: `node --check public/app.js`

Expected: no output and exit code 0.

Run: `npm.cmd test`

Expected: all Node tests pass.

- [ ] **Step 6: Commit the workflow slice**

```powershell
git add public/index.html public/app.js public/style.css public/academic.css README.md
git commit -m "feat: add Chinese title management workflow"
```

---

### Task 6: Verify migration, UI layout, and full project behavior

**Files:**
- Verify only; modify the smallest relevant file if a check exposes a defect

**Interfaces:**
- Verifies every interface produced by Tasks 1-5

- [ ] **Step 1: Run all automated tests and syntax checks**

Run: `npm.cmd test`

Expected: every Node test passes with zero failures.

Run: `.\.venv\Scripts\python.exe -m unittest discover -s test -p test_*.py`

Expected: every Python test passes with `OK`.

Run: `node --check server.js`

Expected: no output and exit code 0.

Run: `node --check public/app.js`

Expected: no output and exit code 0.

- [ ] **Step 2: Verify an existing database upgrades idempotently**

Run the isolated migration test twice:

```powershell
node --test test/title-translations.test.js
node --test test/title-translations.test.js
```

Expected: both runs pass; the second run does not report a duplicate-column error.

- [ ] **Step 3: Start an isolated visual-QA server**

Use a temporary database so visual testing does not alter the user's library:

```powershell
$env:DB_PATH = Join-Path $env:TEMP 'paper-study-title-qa.db'
$env:PORT = '5274'
node -e "const d=require('./db'); d.addPaper({title:'Chain-of-Verification Reduces Hallucination in Large Language Models',title_zh:'验证链减少大型语言模型幻觉',venue:'ACL',year:'2023'}); d.db.close()"
npm.cmd start
```

Expected: the app starts at `http://localhost:5274`.

- [ ] **Step 4: Perform browser visual and interaction QA**

Use the installed in-app Browser skill at desktop `1440x900` and mobile `390x844`. Verify:

- Home table shows English primary and Chinese secondary without overlapping adjacent columns.
- Reading sidebar keeps both title lines inside the sidebar at its minimum width.
- Reading detail header shows both titles while the PDF toolbar remains stable.
- Review cards and management rows show both titles without changing action-button positions.
- Searching for `验证链` finds the English-titled paper in home, reading, and management views.
- Editing the Chinese title persists after reload; clearing it makes the pending count increase by one.
- Starting a batch with no API key shows failures in the summary while leaving English titles intact.
- The batch button changes to “停止生成”; stopping leaves completed results persisted.

- [ ] **Step 5: Inspect the final diff and working tree**

Run: `git diff --check`

Expected: no whitespace errors.

Run: `git status --short`

Expected: only the pre-existing `AGENTS.md` timestamp change may remain outside the feature commits.

- [ ] **Step 6: Commit any verification-only correction**

If Step 4 required a focused correction, stage only the files changed for that correction and commit:

```powershell
git add public/app.js public/style.css public/academic.css
git commit -m "fix: refine Chinese title presentation"
```

If no correction was required, do not create an empty commit.
