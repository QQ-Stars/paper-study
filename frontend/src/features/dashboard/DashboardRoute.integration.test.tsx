import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { resetWorkspaceStore, useWorkspaceStore } from '../../app/stores/workspaceStore';
import type {
  JobSummary,
  PaperListItem,
  ReviewSnapshot,
} from '../../lib/api/types';
import {
  Component,
  DashboardInspectorSlot,
  DashboardTimelineSlot,
} from './DashboardRoute';

const apiMocks = vi.hoisted(() => ({
  listPapers: vi.fn(),
  getReviews: vi.fn(),
  listJobs: vi.fn(),
}));

vi.mock('../../lib/api/paperApi', () => ({
  paperApi: {
    listPapers: apiMocks.listPapers,
    getReviews: apiMocks.getReviews,
  },
}));
vi.mock('../../lib/api/workspaceApi', () => ({
  workspaceApi: { listJobs: apiMocks.listJobs },
}));

const paper: PaperListItem = {
  id: 'p1',
  file: 'paper.pdf',
  title: 'Server Paper',
  titleZh: '服务端论文',
  venue: 'CSCW',
  year: '2026',
  type: 'Research',
  topic: 'Interfaces',
  pdfUrl: null,
  pdfPath: null,
  url: null,
  tldr: 'Decoded server summary.',
  contribution: null,
  citations: 3,
  createdAt: '2026-08-01T08:00:00.000Z',
  source: 'seed',
  arxivId: null,
  doi: null,
  s2Id: null,
  openalexId: null,
  relevance: 0.9,
  order: 1,
  ccf: 'A',
  status: '学习中',
  hasNote: true,
  favorite: false,
  hasPdf: true,
};

const reviews: ReviewSnapshot = {
  today: '2026-08-05',
  counts: { overdue: 0, dueToday: 0, upcoming: 1, completed: 0 },
  overdue: [],
  dueToday: [],
  upcoming: [{
    paperId: 'p1',
    title: 'Server Paper',
    titleZh: '服务端论文',
    venue: 'CSCW',
    year: '2026',
    status: '学习中',
    reviewState: 'upcoming',
    startedAt: '2026-08-01T08:00:00.000Z',
    currentStep: 2,
    completedSteps: 1,
    nextDueAt: '2026-08-07T08:00:00.000Z',
    completedAt: null,
    updatedAt: '2026-08-02T08:00:00.000Z',
    totalSteps: 7,
  }],
  completed: [],
};

const jobs: JobSummary[] = [{
  id: 9,
  query: 'evidence UI',
  sources: ['dblp'],
  yearFrom: 2024,
  yearTo: 2026,
  maxPapers: 10,
  minRelevance: 0.5,
  onlyA: false,
  scheduleId: null,
  status: 'running',
  found: 2,
  added: 0,
  skipped: 0,
  pending: 2,
  createdAt: '2026-08-03T08:00:00.000Z',
  finishedAt: null,
}];

function matchMedia(query: string): MediaQueryList {
  const matches = query === '(min-width: 1100px)'
    || query === '(prefers-reduced-motion: no-preference)';
  return {
    matches,
    media: query,
    onchange: null,
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
    addListener: vi.fn(),
    removeListener: vi.fn(),
    dispatchEvent: vi.fn(() => true),
  };
}

beforeEach(() => {
  resetWorkspaceStore();
  useWorkspaceStore.getState().setWorkspaceSelectionId('p1');
  apiMocks.listPapers.mockReset().mockResolvedValue([paper]);
  apiMocks.getReviews.mockReset().mockResolvedValue(reviews);
  apiMocks.listJobs.mockReset().mockResolvedValue(jobs);
  Object.defineProperty(window, 'matchMedia', {
    configurable: true,
    value: vi.fn(matchMedia),
  });
});

describe('Dashboard route data adapter', () => {
  it('consumes typed paper, review, and job queries in the route and shell slots', async () => {
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });

    render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={['/dashboard']}>
          <Component />
          <DashboardInspectorSlot />
          <DashboardTimelineSlot />
        </MemoryRouter>
      </QueryClientProvider>,
    );

    expect(await screen.findByRole('option', { name: /Server Paper/ })).toBeInTheDocument();
    expect(screen.getByRole('region', { name: '论文上下文' })).toHaveTextContent('服务端论文');
    expect(screen.getByText('任务创建')).toBeInTheDocument();
    expect(screen.getByText('第 2 / 7 轮')).toBeInTheDocument();
    expect(apiMocks.listPapers).toHaveBeenCalled();
    expect(apiMocks.getReviews).toHaveBeenCalled();
    expect(apiMocks.listJobs).toHaveBeenCalled();
  });
});
