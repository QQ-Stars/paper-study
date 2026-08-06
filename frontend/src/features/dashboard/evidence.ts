export interface DashboardPaperEvidence {
  readonly id: string;
  readonly title: string;
  readonly status?: string | null;
  readonly createdAt?: string | null;
  readonly updatedAt?: string | null;
}

export interface DashboardReviewEvidence {
  readonly paperId: string;
  readonly paperTitle?: string | null;
  readonly startedAt?: string | null;
  readonly dueAt?: string | null;
  readonly completedAt?: string | null;
  readonly currentStep?: number | null;
  readonly totalSteps?: number | null;
}

export interface DashboardJobEvidence {
  readonly id: string;
  readonly label: string;
  readonly status: string;
  readonly createdAt?: string | null;
  readonly updatedAt?: string | null;
  readonly finishedAt?: string | null;
}

export type TimelineSource = 'paper' | 'review' | 'job';

export interface ResearchTimelineEvent {
  readonly id: string;
  readonly source: TimelineSource;
  readonly timestamp: string;
  readonly title: string;
  readonly detail: string;
  readonly paperId?: string;
}

export interface ResearchTimelineEvidence {
  readonly papers: readonly DashboardPaperEvidence[];
  readonly reviews: readonly DashboardReviewEvidence[];
  readonly jobs: readonly DashboardJobEvidence[];
}

function timestampValue(value: string | null | undefined): number | null {
  if (value == null || value.trim() === '') return null;
  const timestamp = Date.parse(value);
  return Number.isFinite(timestamp) ? timestamp : null;
}

function reviewProgress(review: DashboardReviewEvidence): string | null {
  if (review.currentStep == null || review.totalSteps == null) return null;
  return `第 ${review.currentStep} / ${review.totalSteps} 轮`;
}

function pushEvent(
  events: ResearchTimelineEvent[],
  event: ResearchTimelineEvent,
) {
  if (timestampValue(event.timestamp) != null) events.push(event);
}

export function deriveTimelineEvents({
  papers,
  reviews,
  jobs,
}: ResearchTimelineEvidence): ResearchTimelineEvent[] {
  const events: ResearchTimelineEvent[] = [];

  for (const paper of papers) {
    if (paper.createdAt != null) {
      pushEvent(events, {
        id: `paper:${paper.id}:created:${paper.createdAt}`,
        source: 'paper',
        timestamp: paper.createdAt,
        title: paper.title,
        detail: '论文入库记录',
        paperId: paper.id,
      });
    }

    if (
      paper.updatedAt != null
      && timestampValue(paper.updatedAt) !== timestampValue(paper.createdAt)
    ) {
      pushEvent(events, {
        id: `paper:${paper.id}:updated:${paper.updatedAt}`,
        source: 'paper',
        timestamp: paper.updatedAt,
        title: paper.title,
        detail: paper.status
          ? `论文记录更新 · ${paper.status}`
          : '论文记录更新',
        paperId: paper.id,
      });
    }
  }

  for (const review of reviews) {
    const title = review.paperTitle || review.paperId;
    const progress = reviewProgress(review);

    if (review.startedAt != null) {
      pushEvent(events, {
        id: `review:${review.paperId}:started:${review.startedAt}`,
        source: 'review',
        timestamp: review.startedAt,
        title,
        detail: progress ? `复习计划开始 · ${progress}` : '复习计划开始',
        paperId: review.paperId,
      });
    }

    if (review.dueAt != null) {
      pushEvent(events, {
        id: `review:${review.paperId}:due:${review.dueAt}`,
        source: 'review',
        timestamp: review.dueAt,
        title,
        detail: progress ? `复习节点到期 · ${progress}` : '复习节点到期',
        paperId: review.paperId,
      });
    }

    if (review.completedAt != null) {
      pushEvent(events, {
        id: `review:${review.paperId}:completed:${review.completedAt}`,
        source: 'review',
        timestamp: review.completedAt,
        title,
        detail: progress ? `复习完成 · ${progress}` : '复习完成',
        paperId: review.paperId,
      });
    }
  }

  for (const job of jobs) {
    if (job.createdAt != null) {
      pushEvent(events, {
        id: `job:${job.id}:created:${job.createdAt}`,
        source: 'job',
        timestamp: job.createdAt,
        title: job.label,
        detail: '任务创建',
      });
    }
    if (job.updatedAt != null) {
      pushEvent(events, {
        id: `job:${job.id}:status:${job.updatedAt}`,
        source: 'job',
        timestamp: job.updatedAt,
        title: job.label,
        detail: `任务状态记录 · ${job.status}`,
      });
    }
    if (job.finishedAt != null) {
      pushEvent(events, {
        id: `job:${job.id}:finished:${job.finishedAt}`,
        source: 'job',
        timestamp: job.finishedAt,
        title: job.label,
        detail: `任务结束 · ${job.status}`,
      });
    }
  }

  return events.sort((left, right) => {
    const byTime = (timestampValue(right.timestamp) ?? 0)
      - (timestampValue(left.timestamp) ?? 0);
    return byTime || left.id.localeCompare(right.id);
  });
}
