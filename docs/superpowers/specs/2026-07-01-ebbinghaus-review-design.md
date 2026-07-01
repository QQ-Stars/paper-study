# Ebbinghaus Review Feature Design

## Context

Paper Study already tracks each paper's reading progress with the statuses `未开始`, `学习中`, and `已理解`. The requested feature adds a review workflow based on the Ebbinghaus forgetting curve so that understood papers come back at useful intervals.

The first version should be focused and local-first: no notifications, accounts, or background services. The review system should help the user answer one practical question: "Which papers should I review today?"

## Goals

- Automatically create a review plan when a paper is marked `已理解`.
- Use a fixed Ebbinghaus-style interval schedule: same day, 1 day, 2 days, 4 days, 7 days, 15 days, and 30 days.
- Add a dedicated review view that groups papers into due, overdue, upcoming, and completed review items.
- Let the user open a due paper and mark the current review round complete.
- Keep review state separate from reading progress so changing reading status does not erase review history.
- Make the scheduling logic small, deterministic, and directly testable.

## Non-Goals

- No email, desktop, browser, or system notifications.
- No spaced-repetition grading such as "easy/hard/forgot".
- No per-user configuration or custom interval editor in the first version.
- No automatic deletion of review history when a paper status changes away from `已理解`.

## Review Schedule

The default review steps are:

| Step | Offset | Meaning |
| --- | ---: | --- |
| 1 | 0 days | Review on the day the paper is understood |
| 2 | 1 day | First recall |
| 3 | 2 days | Early reinforcement |
| 4 | 4 days | Short-term consolidation |
| 5 | 7 days | One-week review |
| 6 | 15 days | Half-month review |
| 7 | 30 days | Final first-cycle review |

When a paper is marked `已理解`, the app creates a plan only if the paper has no existing review plan. The first `next_due_at` is the current local day. Completing a review advances to the next offset. Completing step 7 marks the review plan as complete.

If the user changes a paper from `已理解` back to another status, the review plan remains in the database. The app does not create a duplicate plan if the user later marks it `已理解` again.

## Data Model

Add a `paper_reviews` table:

```sql
CREATE TABLE IF NOT EXISTS paper_reviews (
  paper_id        TEXT PRIMARY KEY REFERENCES papers(id) ON DELETE CASCADE,
  started_at      TEXT NOT NULL DEFAULT (datetime('now')),
  current_step    INTEGER NOT NULL DEFAULT 1,
  completed_steps INTEGER NOT NULL DEFAULT 0,
  next_due_at     TEXT NOT NULL,
  completed_at    TEXT,
  updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
);
```

`current_step` is the step currently due or upcoming. `completed_steps` records how many rounds have already been marked complete. `completed_at` is set only after the final step.

`listPapers()` can optionally include review summary fields for badges and counters, but the review view should read from a dedicated query so the main paper list does not carry complex scheduling logic.

## Backend API

Add review-oriented functions in a small module, for example `lib/reviews.js`:

- `REVIEW_INTERVALS_DAYS`
- `calculateReviewSchedule(startDate)`
- `createReviewPlan({ paperId, now })`
- `completeReviewStep({ paperId, now })`
- `listReviewItems({ now })`

Expose through `db.js` wrappers that own SQLite statements.

HTTP routes:

- `GET /api/reviews`
  - Returns grouped review items and counters:
    - `overdue`
    - `dueToday`
    - `upcoming`
    - `completed`
- `POST /api/reviews/start`
  - Body: `{ id }`
  - Idempotently creates a review plan for a paper.
- `POST /api/reviews/complete`
  - Body: `{ id }`
  - Completes the current step and returns the updated plan.

`/api/status` should call review-plan creation after successfully setting status to `已理解`.

## Frontend UX

Add a new left-rail view named `复习`.

Review page layout:

- Top summary strip:
  - 今日到期
  - 已逾期
  - 未来计划
  - 已完成
- Main list:
  - Due and overdue cards first.
  - Upcoming cards below with lighter treatment.
  - Completed plans collapsed or listed last.

Each active review card shows:

- Paper title, venue, year, status, and review step such as `第 3/7 轮`.
- Due label: `今天`, `已逾期 2 天`, or `3 天后`.
- Actions:
  - `开始阅读`: opens the existing reading view for the paper.
  - `完成本轮`: advances the schedule.

Reading page integration:

- When the current paper has a review plan, show a small review status near the existing status buttons.
- If the paper is due, show `今日复习` and a `完成本轮` action.

## Error Handling

- Completing a review for a paper with no plan returns a clear JSON error.
- Starting a plan for a missing paper returns 404-style JSON.
- Duplicate start calls are idempotent and return the existing plan.
- Dates are stored in SQLite text format and compared by local date strings to avoid time-of-day surprises.

## Testing

Node tests should cover:

- Schedule generation for the fixed 7-step interval list.
- Creating a plan is idempotent.
- Marking status `已理解` creates a plan.
- Completing each step advances `next_due_at`; completing step 7 sets `completed_at`.
- `listReviewItems` groups overdue, due today, upcoming, and completed rows correctly.

Frontend validation should cover:

- Review view renders counters and cards from `/api/reviews`.
- Completing a review updates the card and counters.
- Opening a paper from review navigates to the existing reading flow.

## Rollout

The migration is additive. Existing papers that are already marked `已理解` should receive review plans using their `progress.updated_at` value as the schedule start date. Existing papers in `未开始` or `学习中` do not receive plans until they are marked `已理解`.

This keeps the review page useful immediately while avoiding surprise plans for papers the user has not finished reading.
