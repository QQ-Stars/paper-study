import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, useLocation } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { paperKeys } from '../../lib/api/keys';
import type { Candidate } from '../../lib/api/types';
import { Component } from './AcquireRoute';

const apiMocks = vi.hoisted(() => ({
  expand: vi.fn(),
  search: vi.fn(),
  verifyVenue: vi.fn(),
  ingestSelected: vi.fn(),
  scanPdfs: vi.fn(),
  importPdfs: vi.fn(),
  downloadPdfs: vi.fn(),
  listJobs: vi.fn(),
  createJob: vi.fn(),
}));

vi.mock('../../lib/api/acquisitionGateway', () => ({
  acquisitionGateway: {
    expand: apiMocks.expand,
    search: apiMocks.search,
    verifyVenue: apiMocks.verifyVenue,
    ingestSelected: apiMocks.ingestSelected,
  },
}));
vi.mock('../../lib/api/jobsGateway', () => ({
  jobsGateway: {
    listJobs: apiMocks.listJobs,
    createJob: apiMocks.createJob,
  },
}));
vi.mock('../../lib/api/pdfGateway', () => ({
  pdfGateway: {
    scanPdfs: apiMocks.scanPdfs,
    importPdfs: apiMocks.importPdfs,
    downloadPdfs: apiMocks.downloadPdfs,
  },
}));

function candidate(
  sourceId: string,
  overrides: Partial<Candidate> = {},
): Candidate {
  return {
    source: 'arxiv',
    sourceId,
    title: `Candidate ${sourceId}`,
    authors: ['Researcher'],
    venue: 'CoRR',
    year: '2026',
    abstract: null,
    tldr: null,
    fields: [],
    citations: 3,
    url: null,
    pdfUrl: null,
    arxivId: sourceId,
    doi: null,
    s2Id: null,
    ccf: null,
    type: 'systems',
    topic: 'lifecycle',
    task: null,
    models: [],
    datasets: [],
    contribution: null,
    llmTldr: null,
    tags: [],
    relevance: 0.82,
    inLibrary: false,
    candidateId: null,
    ...overrides,
  };
}

function deferred<T>() {
  let resolve!: (value: T | PromiseLike<T>) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return { promise, reject, resolve };
}

function LocationProbe() {
  const location = useLocation();
  return <output data-testid="location-probe">{location.pathname}</output>;
}

function renderAcquire() {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  });
  queryClient.setQueryData(paperKeys.list(), []);
  const view = render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={['/acquire']}>
        <Component />
        <LocationProbe />
      </MemoryRouter>
    </QueryClientProvider>,
  );
  return { ...view, queryClient };
}

beforeEach(() => {
  localStorage.clear();
  for (const mock of Object.values(apiMocks)) mock.mockReset();
  apiMocks.expand.mockResolvedValue({ queries: ['expanded query'] });
  apiMocks.listJobs.mockResolvedValue([]);
  apiMocks.createJob.mockResolvedValue(7);
  apiMocks.search.mockImplementation(async (_request, options) => {
    options.onEvent?.({ type: 'progress', line: 'STAGE::search' });
    return {
      type: 'result',
      ok: true,
      candidates: [
        candidate('2401.00001'),
        candidate('2401.00002', { inLibrary: true }),
      ],
    };
  });
  apiMocks.verifyVenue.mockResolvedValue({
    type: 'result',
    ok: true,
    verifications: [],
  });
  apiMocks.ingestSelected.mockResolvedValue({ type: 'done', ok: true, added: 1 });
  apiMocks.scanPdfs.mockResolvedValue({
    dir: 'C:/papers',
    count: 2,
    files: [
      { path: 'C:/papers/one.pdf', name: 'one.pdf', size: 1024 },
      { path: 'C:/papers/two.pdf', name: 'two.pdf', size: 2048 },
    ],
  });
  apiMocks.importPdfs.mockResolvedValue({
    type: 'result',
    ok: true,
    added: 1,
    dup: 1,
    failed: 0,
    total: 2,
  });
  apiMocks.downloadPdfs.mockResolvedValue({
    type: 'result',
    ok: true,
    downloaded: 2,
    skipped: 1,
    failed: 0,
    total: 3,
  });
});

describe('Acquire route', () => {
  it('does not restore a pending query expansion after the research direction changes', async () => {
    const user = userEvent.setup();
    const lateExpansion = deferred<{ queries: string[] }>();
    let lateSignal: AbortSignal | undefined;
    apiMocks.expand
      .mockResolvedValueOnce({ queries: ['generated query for direction A'] })
      .mockImplementationOnce((_query, _limit, signal) => {
        lateSignal = signal;
        return lateExpansion.promise;
      });
    renderAcquire();

    await user.click(screen.getByRole('button', { name: '开始检索' }));
    expect(screen.getByRole('alert')).toHaveTextContent('请输入研究方向');

    const direction = screen.getByRole('textbox', { name: '研究方向' });
    await user.type(direction, 'direction A');
    await user.click(screen.getByRole('button', { name: '生成检索词' }));
    expect(await screen.findByDisplayValue('generated query for direction A')).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: '生成检索词' }));
    await waitFor(() => expect(apiMocks.expand).toHaveBeenCalledTimes(2));
    expect(screen.getByText('正在生成检索词…')).toBeInTheDocument();

    await user.clear(direction);
    await user.type(direction, 'direction B');

    expect(direction).toHaveValue('direction B');
    expect(lateSignal?.aborted).toBe(true);
    expect(screen.queryByText('正在生成检索词…')).not.toBeInTheDocument();
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();

    await act(async () => {
      lateExpansion.resolve({ queries: ['late query from direction A'] });
      await lateExpansion.promise;
    });
    expect(screen.queryByDisplayValue('late query from direction A')).not.toBeInTheDocument();
    expect(screen.queryByRole('textbox', { name: '检索词（每行一条）' })).not.toBeInTheDocument();
  });

  it('selects a recent query without retaining or restoring another query expansion', async () => {
    const user = userEvent.setup();
    const lateExpansion = deferred<{ queries: string[] }>();
    let lateSignal: AbortSignal | undefined;
    localStorage.setItem('paper-study:search-history', JSON.stringify(['saved direction']));
    apiMocks.expand
      .mockResolvedValueOnce({ queries: ['generated query'] })
      .mockImplementationOnce((_query, _limit, signal) => {
        lateSignal = signal;
        return lateExpansion.promise;
      });
    renderAcquire();

    const direction = screen.getByRole('textbox', { name: '研究方向' });
    await user.type(direction, 'current direction');
    await user.click(screen.getByRole('button', { name: '生成检索词' }));
    const expandedQueries = await screen.findByRole('textbox', { name: '检索词（每行一条）' });
    await user.clear(expandedQueries);
    await user.type(expandedQueries, 'edited stale query');

    await user.click(screen.getByRole('button', { name: '生成检索词' }));
    await waitFor(() => expect(apiMocks.expand).toHaveBeenCalledTimes(2));
    await user.click(screen.getByRole('button', { name: 'saved direction' }));

    expect(direction).toHaveValue('saved direction');
    expect(screen.queryByRole('textbox', { name: '检索词（每行一条）' })).not.toBeInTheDocument();
    expect(screen.queryByText('正在生成检索词…')).not.toBeInTheDocument();
    expect(lateSignal?.aborted).toBe(true);

    await act(async () => {
      lateExpansion.resolve({ queries: ['late stale query'] });
      await lateExpansion.promise;
    });
    expect(screen.queryByDisplayValue('late stale query')).not.toBeInTheDocument();
  });

  it('submits a background job with edited queries and navigates to the job detail', async () => {
    const user = userEvent.setup();
    renderAcquire();

    await user.click(screen.getByRole('button', { name: '后台检索' }));
    expect(screen.getByRole('alert')).toHaveTextContent('请输入研究方向');

    await user.type(screen.getByRole('textbox', { name: '研究方向' }), 'background direction');
    await user.click(screen.getByRole('button', { name: '生成检索词' }));
    expect(await screen.findByDisplayValue('expanded query')).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: '后台检索' }));

    await waitFor(() => expect(apiMocks.createJob).toHaveBeenCalledOnce());
    expect(apiMocks.createJob.mock.calls[0][0]).toMatchObject({
      query: 'background direction',
      sources: ['semanticscholar', 'arxiv'],
      queries: ['expanded query'],
    });
    // 流式检索未被触发：后台任务由服务端独立执行。
    expect(apiMocks.search).not.toHaveBeenCalled();
    await waitFor(() => {
      expect(screen.getByTestId('location-probe')).toHaveTextContent('/jobs/7');
    });
  });

  it('lists server-side background jobs so a returning visitor can resume monitoring', async () => {
    apiMocks.listJobs.mockResolvedValue([
      {
        id: 3,
        query: 'running direction',
        sources: ['arxiv'],
        yearFrom: 2024,
        yearTo: 2026,
        maxPapers: 10,
        minRelevance: 0,
        onlyA: false,
        scheduleId: null,
        status: 'running',
        found: 4,
        added: 0,
        skipped: 0,
        pending: 0,
        createdAt: null,
        finishedAt: null,
      },
    ]);
    renderAcquire();

    const panel = screen.getByRole('region', { name: '后台任务' });
    expect(await within(panel).findByText('running direction')).toBeInTheDocument();
    expect(within(panel).getByText('running')).toBeInTheDocument();
    expect(within(panel).getByRole('link', { name: '任务页' })).toHaveAttribute('href', '/jobs');
  });

  it('toggles academic sources without crashing and searches with the remaining sources', async () => {
    const user = userEvent.setup();
    renderAcquire();

    await user.type(screen.getByRole('textbox', { name: '研究方向' }), 'source toggle');
    // 取消勾选 arXiv：历史上这里会因 updater 延迟执行时读空
    // event.currentTarget 抛 TypeError，把整页打进 ErrorBoundary 重试页。
    await user.click(screen.getByRole('checkbox', { name: 'arXiv' }));

    expect(screen.getByRole('checkbox', { name: 'arXiv' })).not.toBeChecked();
    // 表单状态保持、页面未落入错误边界。
    expect(screen.getByRole('textbox', { name: '研究方向' })).toHaveValue('source toggle');
    expect(screen.queryByText(/页面崩溃|发生了错误/)).not.toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: '开始检索' }));
    await waitFor(() => expect(apiMocks.search).toHaveBeenCalledOnce());
    expect(apiMocks.search.mock.calls[0][0].sources).toEqual(['semanticscholar']);

    // 再勾回 arXiv，确认勾选方向同样稳定。
    await user.click(screen.getByRole('checkbox', { name: 'arXiv' }));
    expect(screen.getByRole('checkbox', { name: 'arXiv' })).toBeChecked();
    expect(screen.getByRole('textbox', { name: '研究方向' })).toHaveValue('source toggle');
  });

  it('validates query/source, clamps max, streams progress, and disables existing papers', async () => {
    const user = userEvent.setup();
    renderAcquire();

    await user.click(screen.getByRole('button', { name: '开始检索' }));
    expect(screen.getByRole('alert')).toHaveTextContent('请输入研究方向');

    await user.type(screen.getByRole('textbox', { name: '研究方向' }), 'reader lifecycle');
    await user.clear(screen.getByRole('spinbutton', { name: '最多候选' }));
    await user.type(screen.getByRole('spinbutton', { name: '最多候选' }), '999');
    await user.click(screen.getByRole('button', { name: '开始检索' }));

    await waitFor(() => expect(apiMocks.search).toHaveBeenCalledOnce());
    expect(apiMocks.search.mock.calls[0][0]).toMatchObject({
      query: 'reader lifecycle',
      sources: ['semanticscholar', 'arxiv'],
      max: 60,
    });
    expect(await screen.findByText('STAGE::search')).toBeInTheDocument();
    expect(screen.getByRole('checkbox', { name: '选择 Candidate 2401.00001' })).toBeChecked();
    expect(screen.getByRole('checkbox', { name: '选择 Candidate 2401.00002' })).toBeDisabled();
    expect(screen.getByText('已在库')).toBeInTheDocument();
  });

  it('aborts the current owner, tells the truth about stopping reception, and retries explicitly', async () => {
    const user = userEvent.setup();
    const first = deferred<never>();
    apiMocks.search
      .mockImplementationOnce((_request, options) => {
        options.signal.addEventListener('abort', () => {
          first.reject(new DOMException('Aborted', 'AbortError'));
        }, { once: true });
        return first.promise;
      })
      .mockResolvedValueOnce({
        type: 'result',
        ok: true,
        candidates: [candidate('retry')],
      });
    renderAcquire();

    await user.type(screen.getByRole('textbox', { name: '研究方向' }), 'cancel test');
    await user.click(screen.getByRole('button', { name: '开始检索' }));
    await user.click(screen.getByRole('button', { name: '停止接收' }));

    expect(await screen.findByText('已停止接收；服务端可能仍在运行。')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: '重试检索' }));

    expect(await screen.findByText('Candidate retry')).toBeInTheDocument();
    expect(apiMocks.search).toHaveBeenCalledTimes(2);
  });

  it('keeps partial ingest details and invalidates papers on every settled side effect', async () => {
    const user = userEvent.setup();
    const { queryClient } = renderAcquire();

    await user.type(screen.getByRole('textbox', { name: '研究方向' }), 'partial ingest');
    await user.click(screen.getByRole('button', { name: '开始检索' }));
    const firstCandidate = await screen.findByRole('checkbox', { name: '选择 Candidate 2401.00001' });
    expect(firstCandidate).toBeChecked();

    await user.click(screen.getByRole('button', { name: '入库选中项' }));

    await waitFor(() => expect(apiMocks.ingestSelected).toHaveBeenCalledOnce());
    expect(apiMocks.ingestSelected.mock.calls[0][0].candidates).toHaveLength(1);
    expect(await screen.findByText('服务器确认新增 1 篇。')).toBeInTheDocument();
    await waitFor(() => {
      expect(queryClient.getQueryState(paperKeys.list())?.isInvalidated).toBe(true);
    });
    expect(firstCandidate).toBeInTheDocument();
  });

  it('reports local PDF TOTAL/PARSED/ADDED/DUP/SKIP outcomes without hiding partial success', async () => {
    const user = userEvent.setup();
    apiMocks.importPdfs.mockImplementation(async (_paths, _enrich, options) => {
      options.onEvent?.({ type: 'progress', line: 'TOTAL::2' });
      options.onEvent?.({ type: 'progress', line: 'PARSED::1::2::Paper One' });
      options.onEvent?.({ type: 'progress', line: 'PARSED::2::2::Paper Two' });
      return {
        type: 'result', ok: true, added: 1, dup: 1, failed: 0, total: 2,
      };
    });
    renderAcquire();

    const panel = screen.getByRole('region', { name: '本地 PDF' });
    await user.type(within(panel).getByRole('textbox', { name: 'PDF 文件夹' }), 'C:/papers');
    await user.click(within(panel).getByRole('button', { name: '扫描文件夹' }));
    expect(await within(panel).findByText('TOTAL 2')).toBeInTheDocument();

    await user.click(within(panel).getByRole('button', { name: '导入选中 PDF' }));
    expect(await within(panel).findByText('PARSED 2 · ADDED 1 · DUP 1 · SKIP 0')).toBeInTheDocument();

    await user.click(within(panel).getByRole('button', { name: '补齐馆藏 PDF' }));
    expect(await within(panel).findByText('TOTAL 3 · DOWNLOADED 2 · SKIP 1 · FAILED 0')).toBeInTheDocument();

    await user.clear(within(panel).getByRole('textbox', { name: 'PDF 文件夹' }));
    await user.click(within(panel).getByRole('button', { name: '扫描文件夹' }));
    expect(within(panel).getByRole('alert')).toHaveTextContent('请输入 PDF 文件夹');
    expect(within(panel).queryByText('TOTAL 3 · DOWNLOADED 2 · SKIP 1 · FAILED 0')).not.toBeInTheDocument();
  });

  it('aborts the live stream on route unmount without publishing a failure', async () => {
    const user = userEvent.setup();
    const pending = deferred<never>();
    let signal: AbortSignal | undefined;
    apiMocks.search.mockImplementationOnce((_request, options) => {
      signal = options.signal;
      options.signal.addEventListener('abort', () => {
        pending.reject(new DOMException('Aborted', 'AbortError'));
      }, { once: true });
      return pending.promise;
    });
    const view = renderAcquire();

    await user.type(screen.getByRole('textbox', { name: '研究方向' }), 'unmount owner');
    await user.click(screen.getByRole('button', { name: '开始检索' }));
    await waitFor(() => expect(apiMocks.search).toHaveBeenCalledOnce());
    view.unmount();

    expect(signal?.aborted).toBe(true);
  });
});
