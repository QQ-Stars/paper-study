const assert = require('node:assert/strict');
const Database = require('better-sqlite3');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
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

function loadIsolatedDb(dbPath) {
  const dbModulePath = require.resolve('../db');
  const previousDbPath = process.env.DB_PATH;
  delete require.cache[dbModulePath];
  process.env.DB_PATH = dbPath;

  try {
    const dbModule = require('../db');
    return {
      dbModule,
      cleanup() {
        try {
          dbModule.db.close();
        } finally {
          delete require.cache[dbModulePath];
          if (previousDbPath === undefined) {
            delete process.env.DB_PATH;
          } else {
            process.env.DB_PATH = previousDbPath;
          }
        }
      }
    };
  } catch (err) {
    delete require.cache[dbModulePath];
    if (previousDbPath === undefined) {
      delete process.env.DB_PATH;
    } else {
      process.env.DB_PATH = previousDbPath;
    }
    throw err;
  }
}

test('review schedule uses the fixed Ebbinghaus intervals', () => {
  assert.deepEqual(REVIEW_INTERVALS_DAYS, [0, 1, 2, 4, 7, 15, 30]);
  assert.equal(dateOnly('2026-07-01T19:30:00'), '2026-07-01');
  assert.equal(addDays('2026-07-01', 4), '2026-07-05');
  assert.equal(scheduleForStep('2026-07-01', 1), '2026-07-01');
  assert.equal(scheduleForStep('2026-07-01', 7), '2026-07-31');
});

test('review schedule clamps steps to the supported range', () => {
  assert.equal(scheduleForStep('2026-07-01', -10), '2026-07-01');
  assert.equal(scheduleForStep('2026-07-01', 0), '2026-07-01');
  assert.equal(scheduleForStep('2026-07-01', 8), '2026-07-31');
  assert.equal(scheduleForStep('2026-07-01', 99), '2026-07-31');
});

test('review schedule coerces fractional and non-numeric steps before interval lookup', () => {
  REVIEW_INTERVALS_DAYS['1.5'] = 90;
  REVIEW_INTERVALS_DAYS.NaN = 90;
  try {
    assert.equal(scheduleForStep('2026-07-01', 2.5), '2026-07-02');
    assert.equal(scheduleForStep('2026-07-01', 'not-a-number'), '2026-07-01');
  } finally {
    delete REVIEW_INTERVALS_DAYS['1.5'];
    delete REVIEW_INTERVALS_DAYS.NaN;
  }
});

test('dateOnly rejects invalid date values clearly', () => {
  assert.throws(() => dateOnly('not-a-date'), /Invalid date value/);
  assert.throws(() => dateOnly('2026-02-30'), /Invalid date value/);
  assert.throws(() => dateOnly('2026-07-01T99:99:99'), /Invalid date value/);
  assert.throws(() => dateOnly(new Date('not-a-date')), /Invalid date value/);
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

test('creating a review plan returns null for a missing paper', () => {
  const db = memoryDb();
  const store = createReviewStore(db);

  const plan = store.ensureReviewPlan('missing-paper', { now: '2026-07-01T10:00:00' });

  assert.equal(plan, null);
  assert.equal(db.prepare('SELECT COUNT(*) AS c FROM paper_reviews').get().c, 0);
});

test('creating a review plan uses startedAt when provided', () => {
  const db = memoryDb();
  const store = createReviewStore(db);

  const plan = store.ensureReviewPlan('p1', {
    now: '2026-07-10T10:00:00',
    startedAt: '2026-07-01T09:00:00'
  });

  assert.equal(plan.started_at, '2026-07-01');
  assert.equal(plan.next_due_at, '2026-07-01');
  assert.equal(plan.updated_at, '2026-07-10');
});

test('completing the first review step advances the due date by one day', () => {
  const db = memoryDb();
  const store = createReviewStore(db);
  store.ensureReviewPlan('p1', {
    now: '2026-07-01T09:00:00',
    startedAt: '2026-07-01T09:00:00'
  });

  const row = store.completeReviewStep('p1', { now: '2026-07-01T20:00:00' });

  assert.equal(row.paper_id, 'p1');
  assert.equal(row.started_at, '2026-07-01');
  assert.equal(row.completed_steps, 1);
  assert.equal(row.current_step, 2);
  assert.equal(row.next_due_at, '2026-07-02');
  assert.equal(row.completed_at, null);
  assert.equal(row.updated_at, '2026-07-01');
});

test('completing all seven review steps marks the plan complete at step seven', () => {
  const db = memoryDb();
  const store = createReviewStore(db);
  store.ensureReviewPlan('p1', {
    now: '2026-07-01T09:00:00',
    startedAt: '2026-07-01T09:00:00'
  });

  let row = null;
  for (let i = 0; i < 7; i += 1) {
    row = store.completeReviewStep('p1', { now: `2026-08-0${i + 1}T10:00:00` });
  }
  const afterCompleted = store.completeReviewStep('p1', { now: '2026-09-01T10:00:00' });

  assert.equal(row.completed_steps, 7);
  assert.equal(row.current_step, 7);
  assert.equal(row.next_due_at, '2026-07-31');
  assert.equal(row.completed_at, '2026-08-07');
  assert.deepEqual(afterCompleted, row);
});

test('completing a review step returns null when no plan exists', () => {
  const db = memoryDb();
  const store = createReviewStore(db);

  const row = store.completeReviewStep('p1', { now: '2026-07-01T10:00:00' });

  assert.equal(row, null);
});

test('listReviewItems groups review plans and joins paper progress', () => {
  const db = memoryDb();
  db.prepare("INSERT INTO papers(id,title,venue,year) VALUES('p3','Paper Three','ICML','2025')").run();
  db.prepare("INSERT INTO papers(id,title,venue,year) VALUES('p4','Paper Four','NeurIPS','2026')").run();
  db.prepare("INSERT INTO progress(paper_id,status,updated_at) VALUES('p1','学习中','2026-06-30')").run();
  db.prepare("INSERT INTO progress(paper_id,status,updated_at) VALUES('p3','已理解','2026-07-01')").run();
  db.prepare(`
    INSERT INTO paper_reviews(
      paper_id, started_at, current_step, completed_steps, next_due_at, completed_at, updated_at
    ) VALUES
      ('p1', '2026-06-25', 4, 3, '2026-06-29', NULL, '2026-06-29'),
      ('p2', '2026-07-01', 1, 0, '2026-07-01', NULL, '2026-07-01'),
      ('p3', '2026-07-01', 2, 1, '2026-07-02', NULL, '2026-07-01'),
      ('p4', '2026-06-01', 7, 7, '2026-07-01', '2026-07-01', '2026-07-01')
  `).run();
  const store = createReviewStore(db);

  const list = store.listReviewItems({ now: '2026-07-01T18:00:00' });

  assert.equal(list.today, '2026-07-01');
  assert.deepEqual(list.counts, {
    overdue: 1,
    dueToday: 1,
    upcoming: 1,
    completed: 1
  });
  assert.deepEqual(list.overdue.map((item) => item.paper_id), ['p1']);
  assert.deepEqual(list.dueToday.map((item) => item.paper_id), ['p2']);
  assert.deepEqual(list.upcoming.map((item) => item.paper_id), ['p3']);
  assert.deepEqual(list.completed.map((item) => item.paper_id), ['p4']);
  assert.equal(list.overdue[0].title, 'Paper One');
  assert.equal(list.overdue[0].venue, 'ACL');
  assert.equal(list.overdue[0].year, '2023');
  assert.equal(list.overdue[0].status, '学习中');
  assert.equal(list.dueToday[0].status, '未开始');
  assert.equal(list.upcoming[0].status, '已理解');
  assert.equal(list.overdue[0].review_state, 'overdue');
  assert.equal(list.dueToday[0].review_state, 'dueToday');
  assert.equal(list.upcoming[0].review_state, 'upcoming');
  assert.equal(list.completed[0].review_state, 'completed');
  assert.equal(list.overdue[0].total_steps, 7);
});

test('backfillUnderstoodReviews creates plans for understood progress using updated_at as started_at', () => {
  const db = memoryDb();
  db.prepare("INSERT INTO progress(paper_id,status,updated_at) VALUES('p1','已理解','2026-06-28 18:30:00')").run();
  db.prepare("INSERT INTO progress(paper_id,status,updated_at) VALUES('p2','已理解','2026-07-01T09:00:00')").run();
  const store = createReviewStore(db);

  const created = store.backfillUnderstoodReviews({ now: '2026-07-10T12:00:00' });

  assert.equal(created, 2);
  assert.equal(store.getReviewPlan('p1').started_at, '2026-06-28');
  assert.equal(store.getReviewPlan('p1').next_due_at, '2026-06-28');
  assert.equal(store.getReviewPlan('p1').updated_at, '2026-07-10');
  assert.equal(store.getReviewPlan('p2').started_at, '2026-07-01');
});

test('backfillUnderstoodReviews ignores non-understood progress statuses', () => {
  const db = memoryDb();
  db.prepare("INSERT INTO progress(paper_id,status,updated_at) VALUES('p1','学习中','2026-06-28 18:30:00')").run();
  db.prepare("INSERT INTO progress(paper_id,status,updated_at) VALUES('p2','未开始','2026-07-01T09:00:00')").run();
  const store = createReviewStore(db);

  const created = store.backfillUnderstoodReviews({ now: '2026-07-10T12:00:00' });

  assert.equal(created, 0);
  assert.equal(store.getReviewPlan('p1'), null);
  assert.equal(store.getReviewPlan('p2'), null);
  assert.equal(db.prepare('SELECT COUNT(*) AS c FROM paper_reviews').get().c, 0);
});

test('ensureReviewPlanForStatus creates a plan only for understood status', () => {
  const db = memoryDb();
  const store = createReviewStore(db);

  const ignored = store.ensureReviewPlanForStatus('p1', '学习中', { now: '2026-07-01T10:00:00' });
  const created = store.ensureReviewPlanForStatus('p1', '已理解', { now: '2026-07-02T10:00:00' });

  assert.equal(ignored, null);
  assert.equal(created.paper_id, 'p1');
  assert.equal(created.started_at, '2026-07-02');
  assert.equal(store.getReviewPlan('p1').paper_id, 'p1');
  assert.equal(db.prepare('SELECT COUNT(*) AS c FROM paper_reviews').get().c, 1);
});

test('db.js schema and setStatus create review plans in an isolated DB_PATH', () => {
  const tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), 'study-app-reviews-'));
  const dbPath = path.join(tmpDir, 'isolated.db');
  const loaded = loadIsolatedDb(dbPath);
  const { dbModule } = loaded;

  try {
    assert.equal(typeof dbModule.ensureReviewPlan, 'function');
    assert.equal(typeof dbModule.getReviewPlan, 'function');

    dbModule.db.prepare("INSERT INTO papers(id,source,title,created_at,updated_at) VALUES('db-p1','manual','DB Paper',datetime('now'),datetime('now'))").run();
    dbModule.setStatus('db-p1', '已理解');

    const plan = dbModule.getReviewPlan('db-p1');
    assert.equal(plan.paper_id, 'db-p1');
    assert.equal(plan.current_step, 1);
    assert.equal(plan.completed_steps, 0);
    assert.equal(plan.started_at, plan.next_due_at);
  } finally {
    loaded.cleanup();
    fs.rmSync(tmpDir, { recursive: true, force: true });
  }
});
