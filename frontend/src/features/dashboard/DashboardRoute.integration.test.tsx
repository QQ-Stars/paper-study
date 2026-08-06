import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { App } from '../../app/App';
import { createWorkspaceMemoryRouter } from '../../app/router';
import { resetWorkspaceStore, useWorkspaceStore } from '../../lib/workspace';
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
  getPaper: vi.fn(),
  getNote: vi.fn(),
  getExplainer: vi.fn(),
  getTranslation: vi.fn(),
  getReviews: vi.fn(),
  listJobs: vi.fn(),
  getTitleTranslationStatus: vi.fn(),
  getExplainerPending: vi.fn(),
  getSettings: vi.fn(),
}));

vi.mock('../../lib/api/paperApi', () => ({
  paperApi: {
    listPapers: apiMocks.listPapers,
    getPaper: apiMocks.getPaper,
    getNote: apiMocks.getNote,
    getExplainer: apiMocks.getExplainer,
    getTranslation: apiMocks.getTranslation,
    getReviews: apiMocks.getReviews,
  },
}));
vi.mock('../../lib/api/jobsGateway', () => ({
  jobsGateway: {
    listJobs: apiMocks.listJobs,
  },
}));
vi.mock('../../lib/api/artifactGateway', () => ({
  artifactGateway: {
    getTitleTranslationStatus: apiMocks.getTitleTranslationStatus,
    getExplainerPending: apiMocks.getExplainerPending,
  },
}));
vi.mock('../../lib/api/settingsGateway', () => ({
  settingsGateway: {
    getSettings: apiMocks.getSettings,
  },
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

function mobileMatchMedia(query: string): MediaQueryList {
  const matches = query === '(max-width: 760px)'
    || query === '(max-width: 1099px)';
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
  apiMocks.getPaper.mockReset().mockResolvedValue({
    authors: ['Ada Lovelace', '林研'],
    source: 'semanticscholar',
  });
  apiMocks.getNote.mockReset().mockResolvedValue('# research note');
  apiMocks.getExplainer.mockReset().mockResolvedValue('# explainer');
  apiMocks.getTranslation.mockReset().mockResolvedValue('');
  apiMocks.getReviews.mockReset().mockResolvedValue(reviews);
  apiMocks.listJobs.mockReset().mockResolvedValue(jobs);
  apiMocks.getTitleTranslationStatus.mockReset().mockResolvedValue({
    pending: 0,
    running: false,
  });
  apiMocks.getExplainerPending.mockReset().mockResolvedValue({
    pending: 0,
    withPdf: 0,
    noPdf: 0,
  });
  apiMocks.getSettings.mockReset().mockResolvedValue({
    researchTheme: 'Lifecycle-safe document readers',
  });
  Object.defineProperty(window, 'matchMedia', {
    configurable: true,
    value: vi.fn(matchMedia),
  });
});

describe('Dashboard route data adapter', () => {
  it('opens a real mobile research queue whose filters and sort drive the deck without losing its selected paper', async () => {
    const user = userEvent.setup();
    const alphaPaper: PaperListItem = {
      ...paper,
      id: 'p2',
      file: 'alpha.pdf',
      title: 'Alpha Paper',
      titleZh: '阿尔法论文',
      createdAt: '2026-08-02T08:00:00.000Z',
    };
    const betaPaper: PaperListItem = {
      ...paper,
      id: 'p3',
      file: 'beta.pdf',
      title: 'Beta Paper',
      titleZh: '贝塔论文',
      status: '已理解',
      createdAt: '2026-08-03T08:00:00.000Z',
    };
    const zuluPaper: PaperListItem = {
      ...paper,
      title: 'Zulu Paper',
      titleZh: '祖鲁论文',
      createdAt: '2026-08-01T08:00:00.000Z',
    };
    apiMocks.listPapers.mockResolvedValue([zuluPaper, alphaPaper, betaPaper]);
    useWorkspaceStore.getState().setWorkspaceSelectionId('p2');
    Object.defineProperty(window, 'matchMedia', {
      configurable: true,
      value: vi.fn(mobileMatchMedia),
    });
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    const router = createWorkspaceMemoryRouter(['/dashboard']);

    render(<App router={router} queryClient={queryClient} />);

    const deck = await screen.findByRole('listbox', { name: '论文甲板' });
    await waitFor(() => {
      expect(within(deck).getByRole('option', { name: /Alpha Paper/ })).toHaveAttribute(
        'aria-selected',
        'true',
      );
    });

    await user.click(screen.getByRole('button', { name: '研究队列' }));

    const drawer = screen.getByRole('dialog', { name: '研究队列' });
    expect(drawer.closest('.workspace-overlay')).toHaveAttribute(
      'data-presentation',
      'drawer',
    );
    expect(within(drawer).getByRole('searchbox', { name: '筛选研究队列' })).toBeInTheDocument();
    expect(within(drawer).getByRole('combobox', { name: '论文状态' })).toBeInTheDocument();
    expect(within(drawer).getByRole('combobox', { name: '论文排序' })).toBeInTheDocument();
    expect(within(drawer).getByRole('list', { name: '筛选后的真实论文' })).toHaveTextContent(
      'Alpha Paper',
    );

    await user.selectOptions(
      within(drawer).getByRole('combobox', { name: '论文排序' }),
      'title',
    );
    await waitFor(() => {
      const options = within(deck).getAllByRole('option');
      expect(options[0]).toHaveAccessibleName(/Alpha Paper/);
      expect(within(deck).getByRole('option', { name: /Alpha Paper/ })).toHaveAttribute(
        'aria-selected',
        'true',
      );
    });

    await user.selectOptions(
      within(drawer).getByRole('combobox', { name: '论文状态' }),
      '学习中',
    );
    await waitFor(() => {
      expect(within(deck).queryByRole('option', { name: /Beta Paper/ })).not.toBeInTheDocument();
      expect(within(deck).getByRole('option', { name: /Alpha Paper/ })).toHaveAttribute(
        'aria-selected',
        'true',
      );
    });
  });

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

  it('shows the selected paper detail, artifact availability, and current research direction in the inspector', async () => {
    apiMocks.listPapers.mockResolvedValue([{ ...paper, favorite: true }]);
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });

    render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={['/dashboard']}>
          <DashboardInspectorSlot />
        </MemoryRouter>
      </QueryClientProvider>,
    );

    const inspector = await screen.findByRole('region', { name: '论文上下文' });
    expect(await within(inspector).findByText('Ada Lovelace, 林研')).toBeInTheDocument();
    expect(within(inspector).getByText('semanticscholar')).toBeInTheDocument();
    expect(within(inspector).getByText('已收藏')).toBeInTheDocument();
    expect(within(inspector).getByText('Lifecycle-safe document readers')).toBeInTheDocument();
    expect(within(inspector).getByText('笔记：已有')).toBeInTheDocument();
    expect(within(inspector).getByText('讲解：已有')).toBeInTheDocument();
    expect(within(inspector).getByText('翻译：暂无')).toBeInTheDocument();
    expect(apiMocks.getPaper).toHaveBeenCalledWith('p1', expect.any(AbortSignal));
    expect(apiMocks.getNote).toHaveBeenCalledWith('p1', expect.any(AbortSignal));
    expect(apiMocks.getExplainer).toHaveBeenCalledWith('p1', expect.any(AbortSignal));
    expect(apiMocks.getTranslation).toHaveBeenCalledWith('p1', expect.any(AbortSignal));
    expect(apiMocks.getSettings).toHaveBeenCalledWith(expect.any(AbortSignal));
  });

  it('summarizes today, study state, overdue reviews, active intake, and recent AI queues from server facts', async () => {
    const completedPaper: PaperListItem = {
      ...paper,
      id: 'p2',
      file: 'completed.pdf',
      title: 'Completed Paper',
      titleZh: '已完成论文',
      status: '已理解',
    };
    const dueToday = {
      ...reviews.upcoming[0]!,
      paperId: 'p1',
      reviewState: 'dueToday' as const,
      nextDueAt: reviews.today,
    };
    const overdue = {
      ...reviews.upcoming[0]!,
      paperId: 'p2',
      title: completedPaper.title,
      reviewState: 'overdue' as const,
      nextDueAt: '2026-08-04',
    };
    apiMocks.listPapers.mockResolvedValue([paper, completedPaper]);
    apiMocks.getReviews.mockResolvedValue({
      ...reviews,
      counts: { overdue: 1, dueToday: 1, upcoming: 0, completed: 0 },
      overdue: [overdue],
      dueToday: [dueToday],
      upcoming: [],
    });
    apiMocks.listJobs.mockResolvedValue([
      jobs[0]!,
      { ...jobs[0]!, id: 10, status: 'review' as const },
      { ...jobs[0]!, id: 11, status: 'done' as const },
    ]);
    apiMocks.getTitleTranslationStatus.mockResolvedValue({ pending: 2, running: true });
    apiMocks.getExplainerPending.mockResolvedValue({ pending: 3, withPdf: 2, noPdf: 1 });
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });

    render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={['/dashboard']}>
          <Component />
        </MemoryRouter>
      </QueryClientProvider>,
    );

    const summary = await screen.findByRole('region', { name: '今日研究摘要' });
    expect(within(summary).getByText('今日论文').parentElement).toHaveTextContent('1');
    expect(within(summary).getByText('学习中').parentElement).toHaveTextContent('1');
    expect(within(summary).getByText('已完成论文').parentElement).toHaveTextContent('1');
    expect(within(summary).getByText('逾期复习').parentElement).toHaveTextContent('1');
    expect(within(summary).getByText('活跃采集').parentElement).toHaveTextContent('2');
    expect(within(summary).getByText('最近 AI 任务').parentElement).toHaveTextContent('5');
    expect(within(summary).getByText('题名 2（运行中） · 讲解 3')).toBeInTheDocument();
    expect(apiMocks.getTitleTranslationStatus).toHaveBeenCalled();
    expect(apiMocks.getExplainerPending).toHaveBeenCalled();
  });
});
