import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
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
}));

vi.mock('../../lib/api/acquisitionGateway', () => ({
  acquisitionGateway: {
    expand: apiMocks.expand,
    search: apiMocks.search,
    verifyVenue: apiMocks.verifyVenue,
    ingestSelected: apiMocks.ingestSelected,
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
      </MemoryRouter>
    </QueryClientProvider>,
  );
  return { ...view, queryClient };
}

beforeEach(() => {
  localStorage.clear();
  for (const mock of Object.values(apiMocks)) mock.mockReset();
  apiMocks.expand.mockResolvedValue({ queries: ['expanded query'] });
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
