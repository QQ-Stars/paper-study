# Ebbinghaus Review Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an Ebbinghaus review workflow that automatically schedules understood papers for repeated review and gives the user a dedicated review queue.

**Architecture:** Keep scheduling rules in a pure module, keep SQLite persistence in a small store module, and connect it to the existing `db.js` and `server.js` APIs. The frontend gets a new `复习` view that uses `/api/reviews` and opens papers through the existing reading flow.

**Tech Stack:** Node.js CommonJS, better-sqlite3, vanilla HTML/CSS/JavaScript, Node test runner.

---

## File Structure

- Create `lib/reviews.js`: pure date helpers and `createReviewStore(db)` for SQLite-backed review operations.
- Modify `db/schema.sql`: add `paper_reviews` table and indexes.
- Modify `db.js`: instantiate the review store, backfill current `已理解` papers, create plans from `setStatus`, and export review functions.
- Modify `server.js`: add `GET /api/reviews`, `POST /api/reviews/start`, and `POST /api/reviews/complete`.
- Modify `public/index.html`: add a left-rail `复习` button and a `section#review`.
- Modify `public/app.js`: load review data, render review counters/cards, complete review rounds, and open papers from cards.
- Modify `public/style.css` and `public/academic.css`: style review summary and cards using the existing calm academic UI language.
- Create `test/reviews.test.js`: pure scheduler and in-memory review store tests.
- Modify `test/server-modules.test.js`: keep current shared module tests intact; only add review tests here if a helper belongs with existing modules.

## Task 1: Pure Review Schedule And Store

**Files:**
- Create: `lib/reviews.js`
- Create: `test/reviews.test.js`

- [ ] **Step 1: Write failing tests for schedule generation and idempotent plan creation**

Add this file:

```js
const assert = require('node:assert/strict');
const Database = require('better-sqlite3');
const test = require('node:test');

const {
  REVIEW_INTERVALS_DAYS,
  addDays,
  createReviewStore,
  dateOnly,
  scheduleForStep
} = require('../lib/reviews');

function memoryDb() {
  const db = new Database(':memory:');
  db.pragma('foreign_keys = ON');
  db.exec(`
    CREATE TABLE papers (
      id TEXT PRIMARY KEY,
      title TEXT NOT NULL,
      venue TEXT,
      year TEXT
    );
    CREATE TABLE progress (
      paper_id TEXT PRIMARY KEY REFERENCES papers(id) ON DELETE CASCADE,
      status TEXT NOT NULL,
      updated_at TEXT
    );
    CREATE TABLE paper_reviews (
      paper_id TEXT PRIMARY KEY REFERENCES papers(id) ON DELETE CASCADE,
      started_at TEXT NOT NULL,
      current_step INTEGER NOT NULL DEFAULT 1,
      completed_steps INTEGER NOT NULL DEFAULT 0,
      next_due_at TEXT NOT NULL,
      completed_at TEXT,
      updated_at TEXT NOT NULL
    );
  `);
  db.prepare("INSERT INTO papers(id,title,venue,year) VALUES('p1','Paper One','ACL','2023')").run();
  db.prepare("INSERT INTO papers(id,title,venue,year) VALUES('p2','Paper Two','CVPR','2024')").run();
  return db;
}

test('review schedule uses the fixed Ebbinghaus intervals', () => {
  assert.deepEqual(REVIEW_INTERVALS_DAYS, [0, 1, 2, 4, 7, 15, 30]);
  assert.equal(dateOnly('2026-07-01T19:30:00'), '2026-07-01');
  assert.equal(addDays('2026-07-01', 4), '2026-07-05');
  assert.equal(scheduleForStep('2026-07-01', 1), '2026-07-01');
  assert.equal(scheduleForStep('2026-07-01', 7), '2026-07-31');
});

test('creating a review plan is idempotent', () => {
  const db = memoryDb();
  const store = createReviewStore(db);

  const first = store.ensureReviewPlan('p1', { now: '2026-07-01T10:00:00' });
  const second = store.ensureReviewPlan('p1', { now: '2026-07-02T10:00:00' });

  assert.equal(first.paper_id, 'p1');
  assert.equal(first.current_step, 1);
  assert.equal(first.next_due_at, '2026-07-01');
  assert.deepEqual(second, first);
  assert.equal(db.prepare('SELECT COUNT(*) AS c FROM paper_reviews').get().c, 1);
});
```

- [ ] **Step 2: Run the red test**

Run: `npm.cmd test test/reviews.test.js`

Expected: FAIL with `Cannot find module '../lib/reviews'`.

- [ ] **Step 3: Implement the minimal scheduler and store creation code**

Create `lib/reviews.js` with:

```js
const REVIEW_INTERVALS_DAYS = [0, 1, 2, 4, 7, 15, 30];

function pad2(n) {
  return String(n).padStart(2, '0');
}

function dateOnly(value = new Date()) {
  if (value instanceof Date) {
    return `${value.getFullYear()}-${pad2(value.getMonth() + 1)}-${pad2(value.getDate())}`;
  }
  const s = String(value || '').trim();
  if (/^\d{4}-\d{2}-\d{2}/.test(s)) return s.slice(0, 10);
  return dateOnly(new Date(s || Date.now()));
}

function addDays(day, days) {
  const [y, m, d] = dateOnly(day).split('-').map(Number);
  const dt = new Date(y, m - 1, d);
  dt.setDate(dt.getDate() + Number(days || 0));
  return dateOnly(dt);
}

function scheduleForStep(startedAt, step) {
  const idx = Math.max(0, Math.min(REVIEW_INTERVALS_DAYS.length - 1, Number(step || 1) - 1));
  return addDays(startedAt, REVIEW_INTERVALS_DAYS[idx]);
}

function createReviewStore(db) {
  const rowById = db.prepare('SELECT * FROM paper_reviews WHERE paper_id = ?');
  const paperExists = db.prepare('SELECT 1 FROM papers WHERE id = ?');
  const insert = db.prepare(`
    INSERT INTO paper_reviews(paper_id, started_at, current_step, completed_steps, next_due_at, updated_at)
    VALUES(?, ?, 1, 0, ?, ?)
  `);

  function ensureReviewPlan(paperId, { now = new Date(), startedAt = null } = {}) {
    const existing = rowById.get(paperId);
    if (existing) return existing;
    if (!paperExists.get(paperId)) return null;
    const start = dateOnly(startedAt || now);
    const due = scheduleForStep(start, 1);
    const updated = dateOnly(now);
    insert.run(paperId, start, due, updated);
    return rowById.get(paperId);
  }

  return { ensureReviewPlan };
}

module.exports = {
  REVIEW_INTERVALS_DAYS,
  addDays,
  createReviewStore,
  dateOnly,
  scheduleForStep
};
```

- [ ] **Step 4: Run the green test**

Run: `npm.cmd test test/reviews.test.js`

Expected: PASS with 2 tests.

## Task 2: Review Completion And Grouping

**Files:**
- Modify: `lib/reviews.js`
- Modify: `test/reviews.test.js`

- [ ] **Step 1: Write failing tests for completing steps and grouped lists**

Append to `test/reviews.test.js`:

```js
test('completing review steps advances due dates and completes the final round', () => {
  const db = memoryDb();
  const store = createReviewStore(db);

  store.ensureReviewPlan('p1', { now: '2026-07-01T10:00:00' });
  let plan = store.completeReviewStep('p1', { now: '2026-07-01T11:00:00' });

  assert.equal(plan.current_step, 2);
  assert.equal(plan.completed_steps, 1);
  assert.equal(plan.next_due_at, '2026-07-02');
  assert.equal(plan.completed_at, null);

  for (let i = 0; i < 6; i += 1) {
    plan = store.completeReviewStep('p1', { now: `2026-07-${padForTest(2 + i)}T11:00:00` });
  }

  assert.equal(plan.current_step, 7);
  assert.equal(plan.completed_steps, 7);
  assert.equal(plan.next_due_at, '2026-07-31');
  assert.equal(plan.completed_at, '2026-07-07');
});

test('review list groups overdue, due today, upcoming, and completed papers', () => {
  const db = memoryDb();
  const store = createReviewStore(db);
  db.prepare("INSERT INTO papers(id,title,venue,year) VALUES('p3','Paper Three','ICLR','2025')").run();
  db.prepare("INSERT INTO papers(id,title,venue,year) VALUES('p4','Paper Four','NeurIPS','2026')").run();

  store.ensureReviewPlan('p1', { now: '2026-07-01' });
  store.ensureReviewPlan('p2', { now: '2026-07-02' });
  store.ensureReviewPlan('p3', { now: '2026-07-04' });
  store.ensureReviewPlan('p4', { now: '2026-06-01' });
  for (let i = 0; i < 7; i += 1) store.completeReviewStep('p4', { now: `2026-06-${padForTest(1 + i)}` });

  const grouped = store.listReviewItems({ now: '2026-07-02' });

  assert.deepEqual(grouped.counts, { overdue: 1, dueToday: 1, upcoming: 1, completed: 1 });
  assert.equal(grouped.overdue[0].paper_id, 'p1');
  assert.equal(grouped.dueToday[0].paper_id, 'p2');
  assert.equal(grouped.upcoming[0].paper_id, 'p3');
  assert.equal(grouped.completed[0].paper_id, 'p4');
});

function padForTest(n) {
  return String(n).padStart(2, '0');
}
```

- [ ] **Step 2: Run the red test**

Run: `npm.cmd test test/reviews.test.js`

Expected: FAIL with `store.completeReviewStep is not a function`.

- [ ] **Step 3: Implement completion and grouping**

Extend `createReviewStore` in `lib/reviews.js`:

```js
const updateStep = db.prepare(`
  UPDATE paper_reviews
  SET current_step = ?, completed_steps = ?, next_due_at = ?, completed_at = ?, updated_at = ?
  WHERE paper_id = ?
`);
const listRows = db.prepare(`
  SELECT r.*, p.title, p.venue, p.year, COALESCE(g.status, '未开始') AS status
  FROM paper_reviews r
  JOIN papers p ON p.id = r.paper_id
  LEFT JOIN progress g ON g.paper_id = p.id
  ORDER BY r.completed_at IS NOT NULL, r.next_due_at, p.year, p.title
`);

function completeReviewStep(paperId, { now = new Date() } = {}) {
  const current = rowById.get(paperId);
  if (!current) return null;
  if (current.completed_at) return current;
  const today = dateOnly(now);
  const completedSteps = Math.min(REVIEW_INTERVALS_DAYS.length, Number(current.completed_steps || 0) + 1);
  const finalDone = completedSteps >= REVIEW_INTERVALS_DAYS.length;
  const nextStep = finalDone ? REVIEW_INTERVALS_DAYS.length : completedSteps + 1;
  const nextDue = scheduleForStep(current.started_at, nextStep);
  updateStep.run(nextStep, completedSteps, nextDue, finalDone ? today : null, today, paperId);
  return rowById.get(paperId);
}

function dueState(row, today) {
  if (row.completed_at) return 'completed';
  if (row.next_due_at < today) return 'overdue';
  if (row.next_due_at === today) return 'dueToday';
  return 'upcoming';
}

function listReviewItems({ now = new Date() } = {}) {
  const today = dateOnly(now);
  const out = {
    today,
    counts: { overdue: 0, dueToday: 0, upcoming: 0, completed: 0 },
    overdue: [],
    dueToday: [],
    upcoming: [],
    completed: []
  };
  for (const row of listRows.all()) {
    const state = dueState(row, today);
    const item = { ...row, review_state: state, total_steps: REVIEW_INTERVALS_DAYS.length };
    out[state].push(item);
    out.counts[state] += 1;
  }
  return out;
}
```

Return the new functions from `createReviewStore`.

- [ ] **Step 4: Run the green test**

Run: `npm.cmd test test/reviews.test.js`

Expected: PASS with 4 tests.

## Task 3: Database Integration And Backfill

**Files:**
- Modify: `db/schema.sql`
- Modify: `db.js`
- Modify: `test/reviews.test.js`

- [ ] **Step 1: Write failing integration tests for schema, status trigger, and backfill**

Append to `test/reviews.test.js`:

```js
test('backfill creates plans for existing understood papers from progress updated date', () => {
  const db = memoryDb();
  db.prepare("INSERT INTO progress(paper_id,status,updated_at) VALUES('p1','已理解','2026-06-15 09:00:00')").run();
  db.prepare("INSERT INTO progress(paper_id,status,updated_at) VALUES('p2','学习中','2026-06-20 09:00:00')").run();
  const store = createReviewStore(db);

  const result = store.backfillUnderstoodReviews();

  assert.equal(result.created, 1);
  assert.equal(store.getReviewPlan('p1').started_at, '2026-06-15');
  assert.equal(store.getReviewPlan('p2'), null);
});

test('setting status to understood can create a review plan through the store helper', () => {
  const db = memoryDb();
  const store = createReviewStore(db);

  const plan = store.ensureReviewPlanForStatus('p1', '已理解', { now: '2026-07-01' });
  const ignored = store.ensureReviewPlanForStatus('p2', '学习中', { now: '2026-07-01' });

  assert.equal(plan.paper_id, 'p1');
  assert.equal(ignored, null);
});
```

- [ ] **Step 2: Run the red test**

Run: `npm.cmd test test/reviews.test.js`

Expected: FAIL with `store.backfillUnderstoodReviews is not a function`.

- [ ] **Step 3: Add schema and store helpers**

Add to `db/schema.sql` before `schema_migrations`:

```sql
-- ========== 艾宾浩斯复习计划 ==========
CREATE TABLE IF NOT EXISTS paper_reviews (
  paper_id        TEXT PRIMARY KEY REFERENCES papers(id) ON DELETE CASCADE,
  started_at      TEXT NOT NULL DEFAULT (datetime('now')),
  current_step    INTEGER NOT NULL DEFAULT 1,
  completed_steps INTEGER NOT NULL DEFAULT 0,
  next_due_at     TEXT NOT NULL,
  completed_at    TEXT,
  updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS ix_paper_reviews_due ON paper_reviews(next_due_at);
CREATE INDEX IF NOT EXISTS ix_paper_reviews_completed ON paper_reviews(completed_at);
```

Add these statements and functions to `lib/reviews.js`:

```js
const backfillRows = db.prepare(`
  SELECT p.id, COALESCE(g.updated_at, p.updated_at, p.created_at) AS started_at
  FROM papers p
  JOIN progress g ON g.paper_id = p.id
  LEFT JOIN paper_reviews r ON r.paper_id = p.id
  WHERE g.status = '已理解' AND r.paper_id IS NULL
`);

function getReviewPlan(paperId) {
  return rowById.get(paperId) || null;
}

function backfillUnderstoodReviews() {
  let created = 0;
  const tx = db.transaction((rows) => {
    for (const row of rows) {
      if (ensureReviewPlan(row.id, { now: row.started_at, startedAt: row.started_at })) created += 1;
    }
  });
  tx(backfillRows.all());
  return { created };
}

function ensureReviewPlanForStatus(paperId, status, options) {
  return status === '已理解' ? ensureReviewPlan(paperId, options) : null;
}
```

Return `getReviewPlan`, `backfillUnderstoodReviews`, and `ensureReviewPlanForStatus` from `createReviewStore`.

- [ ] **Step 4: Wire review store into db.js**

Modify `db.js`:

```js
const { createReviewStore } = require('./lib/reviews');
```

After schema setup:

```js
const reviewStore = createReviewStore(db);
try { reviewStore.backfillUnderstoodReviews(); } catch (e) {}
```

Replace `setStatus` with:

```js
const setStatus = (id, status) => {
  const result = db.prepare(`
    INSERT INTO progress(paper_id, status, updated_at) VALUES(?, ?, datetime('now'))
    ON CONFLICT(paper_id) DO UPDATE SET status = excluded.status, updated_at = datetime('now')
  `).run(id, status);
  reviewStore.ensureReviewPlanForStatus(id, status);
  return result;
};
```

Add these exports:

```js
const ensureReviewPlan = (id) => reviewStore.ensureReviewPlan(id);
const completeReviewStep = (id) => reviewStore.completeReviewStep(id);
const listReviewItems = () => reviewStore.listReviewItems();
const getReviewPlan = (id) => reviewStore.getReviewPlan(id);
```

Include them in `module.exports`.

- [ ] **Step 5: Run the green test and full Node suite**

Run: `npm.cmd test test/reviews.test.js`

Expected: PASS with 6 tests.

Run: `npm.cmd test`

Expected: PASS for all Node tests.

## Task 4: HTTP API

**Files:**
- Modify: `server.js`

- [ ] **Step 1: Write a failing service smoke script**

Use this command after the server can start:

```powershell
node -e "const db=require('./db').db; const id='review-smoke-'+Date.now(); db.prepare(\"INSERT INTO papers(id,source,title,created_at,updated_at) VALUES(?, 'manual', 'Review Smoke Paper', datetime('now'), datetime('now'))\").run(id); fetch('http://127.0.0.1:5173/api/reviews/start',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({id})}).then(r=>r.json()).then(j=>{if(!j.ok) process.exit(1); return fetch('http://127.0.0.1:5173/api/reviews')}).then(r=>r.json()).then(j=>{if(!j.counts) process.exit(1); console.log(JSON.stringify(j.counts));})"
```

Expected before implementation: route returns 404 or non-JSON.

- [ ] **Step 2: Add review routes**

In `server.js`, near other JSON API routes, add:

```js
if (p === '/api/reviews' && req.method === 'GET') {
  return send(res, 200, JSON.stringify({ ok: true, ...dbapi.listReviewItems() }), MIME['.json']);
}
if (p === '/api/reviews/start' && req.method === 'POST') {
  const b = JSON.parse(await readBody(req));
  const id = safeBase(b.id);
  if (!id) return send(res, 400, JSON.stringify({ ok: false, error: '缺少 id' }), MIME['.json']);
  const plan = dbapi.ensureReviewPlan(id);
  if (!plan) return send(res, 404, JSON.stringify({ ok: false, error: '论文不存在' }), MIME['.json']);
  return send(res, 200, JSON.stringify({ ok: true, plan }), MIME['.json']);
}
if (p === '/api/reviews/complete' && req.method === 'POST') {
  const b = JSON.parse(await readBody(req));
  const id = safeBase(b.id);
  if (!id) return send(res, 400, JSON.stringify({ ok: false, error: '缺少 id' }), MIME['.json']);
  const plan = dbapi.completeReviewStep(id);
  if (!plan) return send(res, 404, JSON.stringify({ ok: false, error: '尚未加入复习计划' }), MIME['.json']);
  return send(res, 200, JSON.stringify({ ok: true, plan, reviews: dbapi.listReviewItems() }), MIME['.json']);
}
```

- [ ] **Step 3: Run syntax and service checks**

Run: `node --check server.js`

Expected: exit 0.

Run the service smoke from Step 1 while the app server is running.

Expected: JSON counters are printed and the process exits 0.

## Task 5: Frontend Review View

**Files:**
- Modify: `public/index.html`
- Modify: `public/app.js`
- Modify: `public/style.css`
- Modify: `public/academic.css`

- [ ] **Step 1: Add the review route shell**

Add a left-rail button in `public/index.html` after `阅读`:

```html
<button data-view="review" title="复习"><span class="nav-ico"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><path d="M3 12a9 9 0 1 0 3-6.7"/><path d="M3 4v5h5"/><path d="M12 7v5l3 2"/></svg></span><span class="nav-tx">复习</span></button>
```

Add a section after `home`:

```html
<section id="review" class="hidden">
  <div class="page-head"><h1>复习<em>Ebbinghaus</em></h1><span class="ph-line"></span></div>
  <div id="reviewDash" class="review-dash"></div>
  <div id="reviewList" class="review-list"></div>
</section>
```

- [ ] **Step 2: Add frontend review state and rendering functions**

Add near top-level state in `public/app.js`:

```js
let reviewData = null;
```

Add functions near the home/dashboard functions:

```js
async function loadReviews() {
  try {
    reviewData = await (await fetch('/api/reviews')).json();
  } catch (e) {
    reviewData = { ok: false, error: String(e), counts: { overdue: 0, dueToday: 0, upcoming: 0, completed: 0 }, overdue: [], dueToday: [], upcoming: [], completed: [] };
  }
  renderReviews();
}

function reviewCard(item, kind) {
  const p = PAPERS.find(x => x.id === item.paper_id) || item;
  const due = dueText(item, reviewData && reviewData.today);
  const active = kind === 'overdue' || kind === 'dueToday';
  return `<div class="review-card ${kind}" data-id="${item.paper_id}">
    <div class="review-main">
      <div class="review-title">${esc(item.title || p.title || item.paper_id)}</div>
      <div class="review-meta"><span>${esc(item.venue || p.venue || '—')} ${esc(item.year || p.year || '')}</span><span>第 ${item.current_step}/${item.total_steps || 7} 轮</span><span>${due}</span></div>
    </div>
    <div class="review-actions">
      <button class="mini ghost review-open" data-id="${item.paper_id}">开始阅读</button>
      ${active ? `<button class="mini primary review-done" data-id="${item.paper_id}">完成本轮</button>` : ''}
    </div>
  </div>`;
}

function dueText(item, today) {
  if (item.completed_at) return '已完成';
  if (!today || !item.next_due_at) return item.next_due_at || '';
  const diff = Math.round((new Date(item.next_due_at) - new Date(today)) / 86400000);
  if (diff < 0) return `已逾期 ${Math.abs(diff)} 天`;
  if (diff === 0) return '今天';
  return `${diff} 天后`;
}

function renderReviews() {
  const d = reviewData || { counts: { overdue: 0, dueToday: 0, upcoming: 0, completed: 0 }, overdue: [], dueToday: [], upcoming: [], completed: [] };
  $('#reviewDash').innerHTML = [
    ['今日到期', d.counts.dueToday || 0],
    ['已逾期', d.counts.overdue || 0],
    ['未来计划', d.counts.upcoming || 0],
    ['已完成', d.counts.completed || 0]
  ].map(([label, value]) => `<div class="review-stat"><b>${value}</b><span>${label}</span></div>`).join('');
  const groups = [
    ['overdue', '已逾期', d.overdue || []],
    ['dueToday', '今日到期', d.dueToday || []],
    ['upcoming', '未来计划', d.upcoming || []],
    ['completed', '已完成', d.completed || []]
  ];
  $('#reviewList').innerHTML = groups.map(([kind, title, items]) => `
    <div class="review-group ${kind}">
      <div class="review-group-head">${title} · ${items.length}</div>
      ${items.length ? items.map(item => reviewCard(item, kind)).join('') : '<div class="review-empty">这一组暂时没有论文。</div>'}
    </div>
  `).join('');
  document.querySelectorAll('.review-open').forEach(btn => btn.onclick = () => openPaper(PAPERS.find(p => p.id === btn.dataset.id)));
  document.querySelectorAll('.review-done').forEach(btn => btn.onclick = () => completeReview(btn.dataset.id));
}

async function completeReview(id) {
  const r = await (await fetch('/api/reviews/complete', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ id }) })).json();
  if (!r.ok) { alert(r.error || '复习更新失败'); return; }
  reviewData = r.reviews || await (await fetch('/api/reviews')).json();
  renderReviews();
}
```

- [ ] **Step 3: Integrate view switching and status changes**

In `showView(v)`, add:

```js
if (v === 'review') loadReviews();
```

After status changes in the existing status update function, add:

```js
if (status === '已理解') loadReviews();
```

- [ ] **Step 4: Add styles**

Add to `public/style.css`:

```css
.review-dash{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px;margin-bottom:16px}
.review-stat{background:var(--surface);border:1px solid var(--border);border-radius:8px;padding:14px 16px}
.review-stat b{display:block;font-size:24px;line-height:1.1;color:var(--ink)}
.review-stat span{display:block;margin-top:6px;color:var(--muted);font-size:13px}
.review-list{display:grid;gap:16px}
.review-group-head{font-weight:700;color:var(--ink);margin:4px 0 10px}
.review-empty{color:var(--muted);font-size:13px;padding:12px 0}
.review-card{display:flex;align-items:center;justify-content:space-between;gap:16px;background:var(--surface);border:1px solid var(--border);border-radius:8px;padding:14px 16px}
.review-card.overdue{border-left:3px solid var(--danger,#d44)}
.review-card.dueToday{border-left:3px solid var(--primary)}
.review-title{font-weight:700;color:var(--ink);line-height:1.35}
.review-meta{display:flex;flex-wrap:wrap;gap:8px;margin-top:7px;color:var(--muted);font-size:12px}
.review-actions{display:flex;gap:8px;flex:0 0 auto}
@media (max-width:900px){.review-dash{grid-template-columns:repeat(2,minmax(0,1fr))}.review-card{align-items:flex-start;flex-direction:column}.review-actions{width:100%;justify-content:flex-end}}
```

If `academic.css` contains theme-specific grouped selectors for cards, add `.review-stat` and `.review-card` to the same visual grouping.

- [ ] **Step 5: Run frontend syntax checks**

Run: `node --check public/app.js`

Expected: exit 0.

## Task 6: Verification And Completion Audit

**Files:**
- No new source files unless a prior task finds a focused missing test.

- [ ] **Step 1: Run all automated tests**

Run: `npm.cmd test`

Expected: all Node tests pass.

- [ ] **Step 2: Run syntax checks**

Run:

```powershell
node --check server.js
node --check public\app.js
node --check lib\reviews.js
```

Expected: all commands exit 0.

- [ ] **Step 3: Run API smoke against a temporary server**

Start the app on a non-default port and call:

```powershell
$env:PORT='5322'; node server.js
```

In another shell, call:

```powershell
node -e "fetch('http://127.0.0.1:5322/api/reviews').then(r=>r.json()).then(j=>{console.log(JSON.stringify({ok:j.ok, counts:j.counts})); if(!j.ok || !j.counts) process.exit(1);})"
```

Expected: output contains `{"ok":true,"counts":...}`.

- [ ] **Step 4: Completion audit against the spec**

Check each spec requirement:

- Marking status `已理解` creates a review plan.
- Existing `已理解` papers are backfilled using `progress.updated_at`.
- Review intervals are `[0, 1, 2, 4, 7, 15, 30]`.
- `GET /api/reviews`, `POST /api/reviews/start`, and `POST /api/reviews/complete` work.
- Left rail includes `复习`.
- Review page has counters and grouped cards.
- Due/overdue cards can open reading and complete the current round.
- Tests and syntax checks pass.

Expected: each item is proven by test output, command output, or direct file inspection.
