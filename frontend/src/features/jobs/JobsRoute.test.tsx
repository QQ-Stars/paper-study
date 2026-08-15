import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import {
  MemoryRouter,
  Route,
  Routes,
  useLocation,
  useNavigate,
} from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { paperKeys } from '../../lib/api/keys';
import type {
  Candidate,
  JobDetail,
  JobSummary,
  Schedule,
} from '../../lib/api/types';
import { Component, jobPollingIntervalFor } from './JobsRoute';
import { jobDetailPollingIntervalFor } from './jobPolling';

const apiMocks = vi.hoisted(() => ({
  listJobs: vi.fn(),
  getJob: vi.fn(),
  createJob: vi.fn(),
  deleteJob: vi.fn(),
  confirmJob: vi.fn(),
  listSchedules: vi.fn(),
  createSchedule: vi.fn(),
  toggleSchedule: vi.fn(),
  deleteSchedule: vi.fn(),
}));

vi.mock('../../lib/api/jobsGateway', () => ({
  jobsGateway: {
    listJobs: apiMocks.listJobs,
    getJob: apiMocks.getJob,
    createJob: apiMocks.createJob,
    deleteJob: apiMocks.deleteJob,
    confirmJob: apiMocks.confirmJob,
  },
}));
vi.mock('../../lib/api/schedulesGateway', () => ({
  schedulesGateway: {
    listSchedules: apiMocks.listSchedules,
    createSchedule: apiMocks.createSchedule,
    toggleSchedule: apiMocks.toggleSchedule,
    deleteSchedule: apiMocks.deleteSchedule,
  },
}));

interface Deferred<T> {
  promise: Promise<T>;
  resolve(value: T): void;
  reject(reason: unknown): void;
}

function deferred<T>(): Deferred<T> {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return { promise, resolve, reject };
}

function job(id: number, overrides: Partial<JobSummary> = {}): JobSummary {
  return {
    id,
    query: `job ${id}`,
    sources: ['arxiv'],
    yearFrom: 2024,
    yearTo: 2026,
    maxPapers: 10,
    minRelevance: 0,
    onlyA: false,
    scheduleId: null,
    status: 'review',
    found: 2,
    added: 0,
    skipped: 0,
    pending: 2,
    createdAt: '2026-08-06T08:00:00.000Z',
    finishedAt: null,
    ...overrides,
  };
}

function candidate(id: number): Candidate {
  return {
    source: 'arxiv',
    sourceId: `candidate-${id}`,
    title: `Candidate ${id}`,
    authors: [],
    venue: 'CHI',
    year: '2026',
    abstract: null,
    tldr: null,
    fields: [],
    citations: null,
    url: null,
    pdfUrl: null,
    arxivId: null,
    doi: null,
    s2Id: null,
    ccf: 'A',
    type: null,
    topic: null,
    task: null,
    models: [],
    datasets: [],
    contribution: null,
    llmTldr: null,
    tags: [],
    relevance: 0.9,
    inLibrary: false,
    candidateId: id,
  };
}

function detail(id: number, candidates = [candidate(11), candidate(12)]): JobDetail {
  const summary = job(id);
  const { pending: _pending, ...record } = summary;
  void _pending;
  return {
    job: { ...record, log: 'STAGE::review', queries: ['job query'] },
    candidates,
  };
}

function schedule(id: number, overrides: Partial<Schedule> = {}): Schedule {
  return {
    id,
    query: `schedule ${id}`,
    sources: ['arxiv'],
    years: '2024-2026',
    maxPapers: 10,
    minRelevance: 0,
    onlyA: false,
    everyDays: 7,
    enabled: false,
    lastRun: null,
    nextRun: '2026-08-06T08:00:00.000Z',
    createdAt: '2026-08-06T08:00:00.000Z',
    ...overrides,
  };
}

function LocationProbe() {
  const navigate = useNavigate();
  return (
    <>
      <output data-testid="location">{useLocation().pathname}</output>
      <button type="button" onClick={() => navigate('/jobs/3')}>切换到任务 3</button>
    </>
  );
}

function renderJobs(path = '/jobs') {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  });
  queryClient.setQueryData(paperKeys.list(), []);
  const view = render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route path="/jobs/:jobId?" element={<><Component /><LocationProbe /></>} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
  return { ...view, queryClient };
}

beforeEach(() => {
  for (const mock of Object.values(apiMocks)) mock.mockReset();
  apiMocks.listJobs.mockResolvedValue([]);
  apiMocks.getJob.mockResolvedValue(detail(2));
  apiMocks.createJob.mockResolvedValue(42);
  apiMocks.deleteJob.mockResolvedValue(undefined);
  apiMocks.confirmJob.mockResolvedValue({ type: 'done', ok: true, added: 1 });
  apiMocks.listSchedules.mockResolvedValue([]);
  apiMocks.createSchedule.mockResolvedValue(8);
  apiMocks.toggleSchedule.mockResolvedValue(undefined);
  apiMocks.deleteSchedule.mockResolvedValue(undefined);
});

describe('Jobs route', () => {
  it('shows a true zero state and polls only while a listed job is active', async () => {
    renderJobs();
    expect(await screen.findByText('当前没有后台任务')).toBeInTheDocument();
    expect(screen.getByText('0', { selector: '.jobs-zero__count' })).toBeInTheDocument();
    expect(apiMocks.getJob).not.toHaveBeenCalled();

    expect(jobPollingIntervalFor([])).toBe(false);
    expect(jobPollingIntervalFor([job(1, { status: 'done' })])).toBe(false);
    expect(jobPollingIntervalFor([job(1, { status: 'running' })])).toBe(2500);
  });

  it('polls an active detail only while its candidate panel is collapsed', () => {
    const activeDetail = detail(2);
    activeDetail.job.status = 'running';

    expect(jobDetailPollingIntervalFor(activeDetail, false)).toBe(2500);
    expect(jobDetailPollingIntervalFor(activeDetail, true)).toBe(false);
    activeDetail.job.status = 'review';
    expect(jobDetailPollingIntervalFor(activeDetail, false)).toBe(false);
    expect(jobDetailPollingIntervalFor(undefined, false)).toBe(false);
  });

  it('creates a background job from validated fields and opens its server id', async () => {
    const user = userEvent.setup();
    renderJobs();

    await user.type(await screen.findByRole('textbox', { name: '后台研究方向' }), 'deep modules');
    await user.click(screen.getByRole('button', { name: '创建后台任务' }));

    await waitFor(() => expect(apiMocks.createJob).toHaveBeenCalledOnce());
    expect(apiMocks.createJob.mock.calls[0][0]).toEqual(expect.objectContaining({
      query: 'deep modules',
      sources: ['semanticscholar', 'arxiv'],
      years: '2024-2026',
      max: 10,
    }));
    expect(apiMocks.createJob.mock.calls[0][1]).toBeInstanceOf(AbortSignal);
    await waitFor(() => expect(screen.getByTestId('location')).toHaveTextContent('/jobs/42'));
  });

  it('refetches authoritative job facts after failed confirm and exposes changed candidates', async () => {
    const user = userEvent.setup();
    apiMocks.listJobs.mockResolvedValue([job(2)]);
    apiMocks.getJob
      .mockResolvedValueOnce(detail(2))
      .mockResolvedValue(detail(2, [candidate(12)]));
    apiMocks.confirmJob.mockRejectedValueOnce(new Error('确认失败，但服务端状态可能已改变'));
    renderJobs('/jobs/2');

    const detailPanel = await screen.findByRole('region', { name: '任务 2 详情' });
    expect(await within(detailPanel).findByText('Candidate 11')).toBeInTheDocument();
    expect(within(detailPanel).queryByRole('button', { name: /忽略/ })).not.toBeInTheDocument();

    await user.click(within(detailPanel).getByRole('button', { name: '确认选中候选' }));

    expect(await within(detailPanel).findByRole('alert')).toHaveTextContent('确认失败');
    await waitFor(() => expect(apiMocks.getJob.mock.calls.length).toBeGreaterThan(1));
    expect(within(detailPanel).queryByText('Candidate 11')).not.toBeInTheDocument();
    expect(within(detailPanel).getByText('Candidate 12')).toBeInTheDocument();
    expect(apiMocks.listJobs.mock.calls.length).toBeGreaterThan(1);
  });

  it('sends only the fixed candidate selection to confirm', async () => {
    const user = userEvent.setup();
    apiMocks.listJobs.mockResolvedValue([job(2)]);
    renderJobs('/jobs/2');

    const detailPanel = await screen.findByRole('region', { name: '任务 2 详情' });
    await within(detailPanel).findByText('Candidate 11');
    await user.click(within(detailPanel).getByRole('checkbox', { name: '选择 Candidate 12' }));
    await user.click(within(detailPanel).getByRole('button', { name: '确认选中候选' }));

    await waitFor(() => expect(apiMocks.confirmJob).toHaveBeenCalledOnce());
    const [fixedJobId, input, options] = apiMocks.confirmJob.mock.calls[0];
    expect(fixedJobId).toBe(2);
    expect(input.candidates.map((item: Candidate) => item.candidateId)).toEqual([11]);
    expect(options.signal).toBeInstanceOf(AbortSignal);
  });

  it('aborts confirm with honest copy and refetches detail/list after abort settles', async () => {
    const user = userEvent.setup();
    const pending = deferred<never>();
    let signal: AbortSignal | undefined;
    apiMocks.listJobs.mockResolvedValue([job(2)]);
    apiMocks.confirmJob.mockImplementation((_id, _input, options) => {
      const confirmSignal = options.signal;
      signal = confirmSignal;
      confirmSignal.addEventListener('abort', () => {
        pending.reject(new DOMException('stopped', 'AbortError'));
      }, { once: true });
      return pending.promise;
    });
    renderJobs('/jobs/2');

    const detailPanel = await screen.findByRole('region', { name: '任务 2 详情' });
    await within(detailPanel).findByText('Candidate 11');
    const initialDetails = apiMocks.getJob.mock.calls.length;
    const initialLists = apiMocks.listJobs.mock.calls.length;
    await user.click(within(detailPanel).getByRole('button', { name: '确认选中候选' }));
    await user.click(within(detailPanel).getByRole('button', { name: '停止接收' }));

    expect(signal?.aborted).toBe(true);
    expect(await within(detailPanel).findByText('已停止接收；服务端可能仍在运行。')).toBeInTheDocument();
    await waitFor(() => {
      expect(apiMocks.getJob.mock.calls.length).toBeGreaterThan(initialDetails);
      expect(apiMocks.listJobs.mock.calls.length).toBeGreaterThan(initialLists);
    });
  });

  it('invalidates papers only when confirmation reports added papers', async () => {
    const user = userEvent.setup();
    apiMocks.listJobs.mockResolvedValue([job(2)]);
    const { queryClient } = renderJobs('/jobs/2');
    const detailPanel = await screen.findByRole('region', { name: '任务 2 详情' });
    await within(detailPanel).findByText('Candidate 11');

    await user.click(within(detailPanel).getByRole('button', { name: '确认选中候选' }));

    await waitFor(() => {
      expect(queryClient.getQueryState(paperKeys.list())?.isInvalidated).toBe(true);
    });
    expect(await within(detailPanel).findByText('服务器确认新增 1 篇。')).toBeInTheDocument();
  });

  it('does not refresh papers when confirmation reports zero additions', async () => {
    const user = userEvent.setup();
    apiMocks.listJobs.mockResolvedValue([job(2)]);
    apiMocks.confirmJob.mockResolvedValue({ type: 'done', ok: true, added: 0 });
    const { queryClient } = renderJobs('/jobs/2');
    const invalidate = vi.spyOn(queryClient, 'invalidateQueries');
    const detailPanel = await screen.findByRole('region', { name: '任务 2 详情' });
    await within(detailPanel).findByText('Candidate 11');

    await user.click(within(detailPanel).getByRole('button', { name: '确认选中候选' }));
    expect(await within(detailPanel).findByText('服务器确认新增 0 篇。')).toBeInTheDocument();

    expect(invalidate).not.toHaveBeenCalledWith(expect.objectContaining({
      queryKey: paperKeys.all(),
    }));
  });

  it('does not carry a confirmation session into a different job detail', async () => {
    const user = userEvent.setup();
    apiMocks.listJobs.mockResolvedValue([job(2), job(3)]);
    apiMocks.getJob.mockImplementation((id: number) => Promise.resolve(detail(id)));
    renderJobs('/jobs/2');

    const jobTwo = await screen.findByRole('region', { name: '任务 2 详情' });
    await within(jobTwo).findByText('Candidate 11');
    await user.click(within(jobTwo).getByRole('button', { name: '确认选中候选' }));
    expect(await within(jobTwo).findByText('服务器确认新增 1 篇。')).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: '切换到任务 3' }));
    const jobThree = await screen.findByRole('region', { name: '任务 3 详情' });
    expect(within(jobThree).queryByText('服务器确认新增 1 篇。')).not.toBeInTheDocument();
  });

  it('recovers from a detail read failure through an explicit retry', async () => {
    const user = userEvent.setup();
    apiMocks.listJobs.mockResolvedValue([job(2)]);
    apiMocks.getJob
      .mockRejectedValueOnce(new Error('任务不存在'))
      .mockResolvedValue(detail(2));
    renderJobs('/jobs/2');

    const detailPanel = await screen.findByRole('region', { name: '任务 2 详情' });
    expect(await within(detailPanel).findByRole('alert')).toHaveTextContent('任务不存在');
    await user.click(within(detailPanel).getByRole('button', { name: '重试读取任务' }));

    expect(await within(detailPanel).findByText('Candidate 11')).toBeInTheDocument();
  });

  it('shows schedule status only after the server-confirmed refetch', async () => {
    const user = userEvent.setup();
    apiMocks.listSchedules
      .mockResolvedValueOnce([schedule(7)])
      .mockResolvedValue([schedule(7, { enabled: true })]);
    renderJobs();

    const schedules = await screen.findByRole('region', { name: '定时计划' });
    expect(await within(schedules).findByText('已停用')).toBeInTheDocument();
    await user.click(within(schedules).getByRole('button', { name: '启用计划 7' }));

    await waitFor(() => expect(apiMocks.toggleSchedule).toHaveBeenCalledWith(7, true));
    expect(await within(schedules).findByText('已启用')).toBeInTheDocument();
    expect(within(schedules).getByRole('spinbutton', { name: '间隔天数' })).toHaveValue(7);
  });

  it('shows only the server supplied last-run and next-run timestamps', async () => {
    apiMocks.listSchedules.mockResolvedValue([schedule(7, {
      lastRun: 'SERVER_LAST_RUN',
      nextRun: 'SERVER_NEXT_RUN',
    })]);
    renderJobs();

    const schedules = await screen.findByRole('region', { name: '定时计划' });
    expect(await within(schedules).findByText('上次：SERVER_LAST_RUN')).toBeInTheDocument();
    expect(within(schedules).getByText('下次：SERVER_NEXT_RUN')).toBeInTheDocument();
    expect(within(schedules).queryByText(/后运行|倒计时/)).not.toBeInTheDocument();
  });

  it('creates and deletes schedules through server-confirmed refetches', async () => {
    const user = userEvent.setup();
    const confirm = vi.spyOn(globalThis, 'confirm').mockReturnValue(true);
    apiMocks.listSchedules
      .mockResolvedValueOnce([])
      .mockResolvedValueOnce([schedule(8)])
      .mockResolvedValue([]);
    renderJobs();

    const schedules = await screen.findByRole('region', { name: '定时计划' });
    await within(schedules).findByText('没有定时计划。新建计划会由服务端调度器执行。');
    await user.type(within(schedules).getByRole('textbox', { name: '计划研究方向' }), 'agent memory');
    await user.click(within(schedules).getByRole('button', { name: '创建计划' }));

    await waitFor(() => expect(apiMocks.createSchedule).toHaveBeenCalledOnce());
    expect(apiMocks.createSchedule.mock.calls[0][0]).toEqual(expect.objectContaining({
      query: 'agent memory',
      everyDays: 7,
    }));
    expect(await within(schedules).findByText('schedule 8')).toBeInTheDocument();

    await user.click(within(schedules).getByRole('button', { name: '删除计划 8' }));
    await waitFor(() => expect(apiMocks.deleteSchedule).toHaveBeenCalledWith(8));
    expect(await within(schedules).findByText('没有定时计划。新建计划会由服务端调度器执行。')).toBeInTheDocument();
    confirm.mockRestore();
  });
});
