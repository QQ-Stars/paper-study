const REVIEW_INTERVALS_DAYS = [0, 1, 2, 4, 7, 15, 30];

function pad2(n) {
  return String(n).padStart(2, '0');
}

function invalidDate(value) {
  throw new TypeError(`Invalid date value: ${String(value)}`);
}

function validDateFromParts(year, month, day) {
  const dt = new Date(0);
  dt.setHours(0, 0, 0, 0);
  dt.setFullYear(year, month - 1, day);
  return dt.getFullYear() === year && dt.getMonth() === month - 1 && dt.getDate() === day;
}

function dateOnly(value = new Date()) {
  if (value instanceof Date) {
    if (Number.isNaN(value.getTime())) invalidDate(value);
    return `${value.getFullYear()}-${pad2(value.getMonth() + 1)}-${pad2(value.getDate())}`;
  }
  const s = String(value ?? '').trim();
  const dateMatch = /^(\d{4})-(\d{2})-(\d{2})(.*)$/.exec(s);
  if (dateMatch) {
    const year = Number(dateMatch[1]);
    const month = Number(dateMatch[2]);
    const day = Number(dateMatch[3]);
    if (!validDateFromParts(year, month, day)) invalidDate(value);
    if (dateMatch[4] && Number.isNaN(new Date(s).getTime())) invalidDate(value);
    return s.slice(0, 10);
  }
  const parsed = new Date(s);
  if (Number.isNaN(parsed.getTime())) invalidDate(value);
  return dateOnly(parsed);
}

function addDays(day, days) {
  const [y, m, d] = dateOnly(day).split('-').map(Number);
  const dt = new Date(y, m - 1, d);
  dt.setDate(dt.getDate() + Number(days || 0));
  return dateOnly(dt);
}

function scheduleForStep(startedAt, step) {
  const n = Number(step);
  const normalizedStep = Number.isFinite(n) ? Math.trunc(n) : 1;
  const clampedStep = Math.max(1, Math.min(REVIEW_INTERVALS_DAYS.length, normalizedStep));
  return addDays(startedAt, REVIEW_INTERVALS_DAYS[clampedStep - 1]);
}

function createReviewStore(db) {
  const rowById = db.prepare('SELECT * FROM paper_reviews WHERE paper_id = ?');
  const paperExists = db.prepare('SELECT 1 FROM papers WHERE id = ?');
  const insert = db.prepare(`
    INSERT OR IGNORE INTO paper_reviews(paper_id, started_at, current_step, completed_steps, next_due_at, updated_at)
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
