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
  const complete = db.prepare(`
    UPDATE paper_reviews
    SET completed_steps = ?,
        current_step = ?,
        next_due_at = ?,
        completed_at = ?,
        updated_at = ?
    WHERE paper_id = ?
  `);
  const listRows = db.prepare(`
    SELECT
      r.paper_id,
      r.started_at,
      r.current_step,
      r.completed_steps,
      r.next_due_at,
      r.completed_at,
      r.updated_at,
      p.title,
      p.venue,
      p.year,
      COALESCE(NULLIF(TRIM(progress.status), ''), '未开始') AS status
    FROM paper_reviews r
    JOIN papers p ON p.id = r.paper_id
    LEFT JOIN progress ON progress.paper_id = r.paper_id
    ORDER BY r.next_due_at ASC, p.title COLLATE NOCASE ASC, r.paper_id ASC
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

  function completeReviewStep(paperId, { now = new Date() } = {}) {
    const existing = rowById.get(paperId);
    if (!existing) return null;
    if (existing.completed_at) return existing;

    const totalSteps = REVIEW_INTERVALS_DAYS.length;
    const completedSteps = Math.min(totalSteps, Math.max(0, Number(existing.completed_steps) || 0) + 1);
    const currentStep = Math.min(totalSteps, Math.max(1, Number(existing.current_step) || 1) + 1);
    const updated = dateOnly(now);
    const completedAt = completedSteps >= totalSteps ? updated : null;
    const nextDueAt = scheduleForStep(existing.started_at, currentStep);

    complete.run(completedSteps, currentStep, nextDueAt, completedAt, updated, paperId);
    return rowById.get(paperId);
  }

  function listReviewItems({ now = new Date() } = {}) {
    const today = dateOnly(now);
    const groups = {
      overdue: [],
      dueToday: [],
      upcoming: [],
      completed: []
    };

    for (const row of listRows.all()) {
      let reviewState = 'upcoming';
      if (row.completed_at) {
        reviewState = 'completed';
      } else if (row.next_due_at < today) {
        reviewState = 'overdue';
      } else if (row.next_due_at === today) {
        reviewState = 'dueToday';
      }

      groups[reviewState].push({
        ...row,
        status: row.status || '未开始',
        review_state: reviewState,
        total_steps: REVIEW_INTERVALS_DAYS.length
      });
    }

    return {
      today,
      counts: {
        overdue: groups.overdue.length,
        dueToday: groups.dueToday.length,
        upcoming: groups.upcoming.length,
        completed: groups.completed.length
      },
      ...groups
    };
  }

  return { completeReviewStep, ensureReviewPlan, listReviewItems };
}

module.exports = {
  REVIEW_INTERVALS_DAYS,
  addDays,
  createReviewStore,
  dateOnly,
  scheduleForStep
};
