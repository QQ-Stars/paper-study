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
