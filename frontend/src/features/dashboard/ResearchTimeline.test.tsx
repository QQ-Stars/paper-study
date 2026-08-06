import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import {
  ResearchTimeline,
} from './ResearchTimeline';
import {
  deriveTimelineEvents,
  type DashboardJobEvidence,
  type DashboardPaperEvidence,
  type DashboardReviewEvidence,
} from './evidence';

describe('research timeline evidence', () => {
  it('derives sorted events only from timestamped paper, review, and job facts', () => {
    const papers: DashboardPaperEvidence[] = [{
      id: 'p1',
      title: 'Verified Paper',
      status: '学习中',
      createdAt: '2026-08-01T09:00:00.000Z',
      updatedAt: '2026-08-02T10:00:00.000Z',
    }];
    const reviews: DashboardReviewEvidence[] = [{
      paperId: 'p1',
      paperTitle: 'Verified Paper',
      dueAt: '2026-08-03T11:00:00.000Z',
      completedAt: '2026-08-04T12:00:00.000Z',
      currentStep: 2,
      totalSteps: 7,
    }];
    const jobs: DashboardJobEvidence[] = [{
      id: 'j1',
      label: '语义检索',
      status: 'running',
      updatedAt: '2026-08-05T13:00:00.000Z',
    }];

    const events = deriveTimelineEvents({ papers, reviews, jobs });

    expect(events.map((event) => event.source)).toEqual([
      'job',
      'review',
      'review',
      'paper',
      'paper',
    ]);
    expect(events.map((event) => event.timestamp)).toEqual([
      '2026-08-05T13:00:00.000Z',
      '2026-08-04T12:00:00.000Z',
      '2026-08-03T11:00:00.000Z',
      '2026-08-02T10:00:00.000Z',
      '2026-08-01T09:00:00.000Z',
    ]);
    expect(events.find((event) => event.source === 'job')?.paperId).toBeUndefined();
  });

  it('does not invent events for records without valid event timestamps', () => {
    expect(deriveTimelineEvents({
      papers: [{ id: 'p1', title: 'No History', status: '已理解' }],
      reviews: [{ paperId: 'p1', paperTitle: 'No History' }],
      jobs: [{ id: 'j1', label: 'Missing Time', status: 'done', updatedAt: 'not-a-date' }],
    })).toEqual([]);
  });

  it('does not claim a current job status occurred at its creation time', () => {
    const events = deriveTimelineEvents({
      papers: [],
      reviews: [],
      jobs: [{
        id: 'j1',
        label: '后台检索',
        status: 'running',
        createdAt: '2026-08-01T08:00:00.000Z',
      }],
    });

    expect(events).toHaveLength(1);
    expect(events[0]).toEqual(expect.objectContaining({
      detail: '任务创建',
      timestamp: '2026-08-01T08:00:00.000Z',
    }));
  });

  it('renders an explanatory empty state instead of sample activity', () => {
    render(<ResearchTimeline papers={[]} reviews={[]} jobs={[]} />);

    expect(screen.getByRole('status')).toHaveTextContent('没有可核验的时间线事件');
    expect(screen.getByRole('status')).toHaveTextContent('仅显示论文、复习和任务返回的真实时间字段');
  });

  it('renders source and machine-readable time for every real event', () => {
    render(
      <ResearchTimeline
        papers={[]}
        reviews={[]}
        jobs={[{
          id: 'j1',
          label: '导入 PDF',
          status: 'done',
          updatedAt: '2026-08-05T13:00:00.000Z',
        }]}
      />,
    );

    expect(screen.getByText('导入 PDF')).toBeInTheDocument();
    expect(screen.getByText('任务状态记录 · done')).toBeInTheDocument();
    expect(screen.getByText('任务')).toBeInTheDocument();
    expect(screen.getByRole('time')).toHaveAttribute('datetime', '2026-08-05T13:00:00.000Z');
  });
});
