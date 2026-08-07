import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, render, screen, within } from '@testing-library/react';
import { waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { resetWorkspaceStore, useWorkspaceStore } from '../../lib/workspace';
import { artifactKeys, paperKeys } from '../../lib/api/keys';
import type { PaperListItem, PaperRecord } from '../../lib/api/types';
import type { ExplainBatchTerminal } from '../../lib/streaming/contracts';
import { Component, LibraryInspectorSlot } from './LibraryRoute';

type SuccessfulExplainBatch = Extract<ExplainBatchTerminal, { ok: true }>;

const apiMocks = vi.hoisted(() => ({
  listPapers: vi.fn(),
  getPaper: vi.fn(),
  setFavorite: vi.fn(),
  setStatus: vi.fn(),
  addPaper: vi.fn(),
  updatePaper: vi.fn(),
  deletePaper: vi.fn(),
  getExplainerPending: vi.fn(),
  explainBatch: vi.fn(),
  semanticSearch: vi.fn(),
}));

vi.mock('../../lib/api/paperApi', () => ({
  paperApi: {
    listPapers: apiMocks.listPapers,
    getPaper: apiMocks.getPaper,
    setFavorite: apiMocks.setFavorite,
    setStatus: apiMocks.setStatus,
    addPaper: apiMocks.addPaper,
    updatePaper: apiMocks.updatePaper,
    deletePaper: apiMocks.deletePaper,
  },
}));

vi.mock('../../lib/api/insightsGateway', () => ({
  insightsGateway: { semanticSearch: apiMocks.semanticSearch },
}));

vi.mock('../../lib/api/artifactGateway', () => ({
  artifactGateway: {
    getExplainerPending: apiMocks.getExplainerPending,
    explainBatch: apiMocks.explainBatch,
  },
}));

function paper(
  id: string,
  overrides: Partial<PaperListItem> = {},
): PaperListItem {
  return {
    id,
    file: `${id}.pdf`,
    title: `Paper ${id}`,
    titleZh: null,
    venue: 'CSCW',
    year: '2026',
    type: 'Research',
    topic: 'Knowledge work',
    pdfUrl: null,
    pdfPath: null,
    url: null,
    tldr: null,
    contribution: null,
    citations: 0,
    createdAt: '2026-08-01T08:00:00.000Z',
    source: 'seed',
    arxivId: null,
    doi: null,
    s2Id: null,
    openalexId: null,
    relevance: 0.5,
    order: null,
    ccf: null,
    status: '未开始',
    hasNote: false,
    favorite: false,
    hasPdf: false,
    ...overrides,
  };
}

const papers = [
  paper('one', { title: 'Paper One', ccf: 'A', hasPdf: true }),
  paper('two', {
    title: 'Paper Two',
    titleZh: '第二篇论文',
    source: 'manual',
    favorite: true,
    status: '学习中',
  }),
];

function paperRecord(
  id: string,
  overrides: Partial<PaperRecord> = {},
): PaperRecord {
  const listItem = papers.find((candidate) => candidate.id === id) ?? paper(id);
  return {
    id,
    source: listItem.source ?? 'manual',
    sourceId: null,
    arxivId: listItem.arxivId,
    doi: listItem.doi,
    s2Id: listItem.s2Id,
    openalexId: listItem.openalexId,
    title: listItem.title,
    titleZh: listItem.titleZh,
    titleNorm: null,
    authors: ['Ada Lovelace', '林研'],
    venue: listItem.venue,
    year: listItem.year,
    abstract: 'Detailed abstract from the paper detail endpoint.',
    tldr: listItem.tldr,
    citations: listItem.citations,
    s2Fields: [],
    url: listItem.url,
    pdfUrl: listItem.pdfUrl,
    pdfPath: listItem.pdfPath,
    type: listItem.type,
    topic: listItem.topic,
    task: null,
    models: [],
    datasets: [],
    contribution: listItem.contribution,
    tags: [],
    relevance: listItem.relevance,
    explainer: null,
    extractedBy: null,
    orderNo: listItem.order,
    createdAt: listItem.createdAt,
    updatedAt: listItem.createdAt,
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

function renderLibrary(options: { reactStrictMode?: boolean } = {}) {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  });
  const view = render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={['/library']}>
        <Component />
        <LibraryInspectorSlot />
      </MemoryRouter>
    </QueryClientProvider>,
    options,
  );
  return { ...view, queryClient };
}

beforeEach(() => {
  resetWorkspaceStore();
  for (const mock of Object.values(apiMocks)) mock.mockReset();
  apiMocks.listPapers.mockResolvedValue(papers);
  apiMocks.getPaper.mockImplementation((id: string) => Promise.resolve(paperRecord(id)));
  apiMocks.setFavorite.mockResolvedValue(undefined);
  apiMocks.setStatus.mockResolvedValue(undefined);
  apiMocks.addPaper.mockResolvedValue('new-paper');
  apiMocks.updatePaper.mockResolvedValue(1);
  apiMocks.deletePaper.mockResolvedValue(undefined);
  apiMocks.getExplainerPending.mockResolvedValue({
    pending: 3,
    withPdf: 2,
    noPdf: 1,
  });
  apiMocks.explainBatch.mockResolvedValue({
    type: 'result',
    ok: true,
    summary: { total: 2, done: 2, failed: [], skippedNoPdf: [] },
  });
  apiMocks.semanticSearch.mockResolvedValue({ type: 'result', ok: true, results: [] });
});

describe('LibraryRoute', () => {
  it('manages missing explainers with authoritative counts and streamed progress', async () => {
    const user = userEvent.setup();
    const request = deferred<SuccessfulExplainBatch>();
    const reconciliation = deferred<{ pending: number; withPdf: number; noPdf: number }>();
    apiMocks.getExplainerPending
      .mockResolvedValueOnce({ pending: 3, withPdf: 2, noPdf: 1 })
      .mockImplementationOnce(() => reconciliation.promise)
      .mockResolvedValue({ pending: 1, withPdf: 1, noPdf: 0 });
    apiMocks.explainBatch.mockImplementation((limit, options) => {
      expect(limit).toBe(0);
      options.onEvent?.({ type: 'progress', line: '批量讲解 1 / 2' });
      return request.promise;
    });

    renderLibrary();

    const manager = await screen.findByRole('region', { name: '批量讲解管理' });
    expect(await within(manager).findByText('待生成 3 篇')).toBeInTheDocument();
    expect(within(manager).getByText('可直接处理 2 篇')).toBeInTheDocument();
    expect(within(manager).getByText('缺少 PDF 1 篇')).toBeInTheDocument();

    await user.click(within(manager).getByRole('button', { name: '批量生成缺失讲解' }));

    expect(await within(manager).findByText('批量讲解 1 / 2')).toBeInTheDocument();
    expect(apiMocks.explainBatch).toHaveBeenCalledOnce();

    await act(async () => {
      request.resolve({
        type: 'result',
        ok: true,
        summary: { total: 2, done: 2, failed: [], skippedNoPdf: [] },
      });
      await request.promise;
    });

    expect(await within(manager).findByText('已完成 2 / 2 篇 · 失败 0 · 跳过无 PDF 0')).toBeInTheDocument();
    await waitFor(() => expect(apiMocks.getExplainerPending).toHaveBeenCalledTimes(2));
    const restart = within(manager).getByRole('button', { name: '批量生成缺失讲解' });
    expect(restart).toBeDisabled();
    await user.click(restart);
    expect(apiMocks.explainBatch).toHaveBeenCalledOnce();

    reconciliation.resolve({ pending: 1, withPdf: 1, noPdf: 0 });
    await waitFor(() => expect(apiMocks.listPapers).toHaveBeenCalledTimes(2));
    expect(within(manager).getByText('待生成 1 篇')).toBeInTheDocument();
    await waitFor(() => expect(restart).toBeEnabled());
  });

  it('stops receiving a batch without accepting late progress or claiming server cancellation', async () => {
    const user = userEvent.setup();
    const request = deferred<SuccessfulExplainBatch>();
    const reconciliation = deferred<{ pending: number; withPdf: number; noPdf: number }>();
    let commandOptions: {
      signal?: AbortSignal;
      onEvent?: (event: { type: 'progress'; line: string }) => void;
    } | undefined;
    apiMocks.explainBatch.mockImplementation((_limit, options) => {
      commandOptions = options;
      return request.promise;
    });
    apiMocks.getExplainerPending
      .mockResolvedValueOnce({ pending: 3, withPdf: 2, noPdf: 1 })
      .mockImplementationOnce(() => reconciliation.promise)
      .mockResolvedValue({ pending: 3, withPdf: 2, noPdf: 1 });

    renderLibrary();

    const manager = await screen.findByRole('region', { name: '批量讲解管理' });
    await within(manager).findByText('待生成 3 篇');
    await user.click(within(manager).getByRole('button', { name: '批量生成缺失讲解' }));
    await user.click(await within(manager).findByRole('button', { name: '停止接收' }));

    expect(commandOptions?.signal?.aborted).toBe(true);
    expect(within(manager).getByText('已停止接收；服务端可能仍在运行。')).toBeInTheDocument();
    const restart = within(manager).getByRole('button', { name: '批量生成缺失讲解' });
    expect(restart).toBeDisabled();
    await user.click(restart);
    expect(apiMocks.explainBatch).toHaveBeenCalledOnce();

    await act(async () => {
      commandOptions?.onEvent?.({ type: 'progress', line: '不应出现的晚到进度' });
      request.resolve({
        type: 'result',
        ok: true,
        summary: { total: 2, done: 2, failed: [], skippedNoPdf: [] },
      });
      await request.promise;
    });

    expect(within(manager).queryByText('不应出现的晚到进度')).not.toBeInTheDocument();
    expect(within(manager).queryByText('已完成 2 / 2 篇 · 失败 0 · 跳过无 PDF 0')).not.toBeInTheDocument();
    expect(within(manager).getByText('已停止接收；服务端可能仍在运行。')).toBeInTheDocument();
    await waitFor(() => expect(apiMocks.getExplainerPending).toHaveBeenCalledTimes(2));
    expect(restart).toBeDisabled();

    reconciliation.resolve({ pending: 3, withPdf: 2, noPdf: 1 });
    await waitFor(() => expect(apiMocks.listPapers).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(restart).toBeEnabled());
  });

  it('aborts an active batch when the StrictMode route unmounts', async () => {
    const user = userEvent.setup();
    const request = deferred<SuccessfulExplainBatch>();
    let signal: AbortSignal | undefined;
    apiMocks.explainBatch.mockImplementation((_limit, options) => {
      signal = options.signal;
      return request.promise;
    });

    const view = renderLibrary({ reactStrictMode: true });
    const manager = await screen.findByRole('region', { name: '批量讲解管理' });
    await within(manager).findByText('待生成 3 篇');
    await user.click(within(manager).getByRole('button', { name: '批量生成缺失讲解' }));
    expect(signal?.aborted).toBe(false);

    view.unmount();

    expect(signal?.aborted).toBe(true);
    request.resolve({
      type: 'result',
      ok: true,
      summary: { total: 2, done: 2, failed: [], skippedNoPdf: [] },
    });
    await request.promise;
  });

  it('disables batch generation when no pending explainer has a PDF', async () => {
    apiMocks.getExplainerPending.mockResolvedValue({
      pending: 2,
      withPdf: 0,
      noPdf: 2,
    });

    renderLibrary();

    const manager = await screen.findByRole('region', { name: '批量讲解管理' });
    expect(await within(manager).findByText('可直接处理 0 篇')).toBeInTheDocument();
    expect(within(manager).getByRole('button', { name: '批量生成缺失讲解' })).toBeDisabled();
    expect(apiMocks.explainBatch).not.toHaveBeenCalled();
  });

  it('keeps failed pending counts unknown and lets the user retry them', async () => {
    const user = userEvent.setup();
    apiMocks.getExplainerPending
      .mockRejectedValueOnce(new Error('统计服务不可用'))
      .mockResolvedValueOnce({ pending: 3, withPdf: 2, noPdf: 1 });

    renderLibrary();

    const manager = await screen.findByRole('region', { name: '批量讲解管理' });
    expect(await within(manager).findByText('待生成统计读取失败：统计服务不可用')).toBeInTheDocument();
    expect(within(manager).getByText('待生成 — 篇')).toBeInTheDocument();
    expect(within(manager).getByText('可直接处理 — 篇')).toBeInTheDocument();
    expect(within(manager).getByText('缺少 PDF — 篇')).toBeInTheDocument();

    await user.click(within(manager).getByRole('button', { name: '重新读取统计' }));

    expect(await within(manager).findByText('待生成 3 篇')).toBeInTheDocument();
    expect(within(manager).getByRole('button', { name: '批量生成缺失讲解' })).toBeEnabled();
  });

  it('renders a dense ledger, client-only batch selection, fixed preview, and selection reconciliation', async () => {
    const user = userEvent.setup();
    renderLibrary();

    const firstRow = await screen.findByRole('row', { name: /Paper One/ });
    const secondRow = screen.getByRole('row', { name: /Paper Two/ });
    expect(within(firstRow).getByText('A')).toBeInTheDocument();
    expect(within(firstRow).getByText('PDF')).toBeInTheDocument();

    await user.click(secondRow);
    expect(screen.getByRole('complementary', { name: '论文预览' })).toHaveTextContent('第二篇论文');

    await user.click(within(secondRow).getByRole('checkbox', { name: '选择 Paper Two' }));
    expect(screen.getByText('已选择 1 篇')).toBeInTheDocument();

    await user.type(screen.getByRole('searchbox', { name: '搜索文献' }), '第二篇');
    expect(screen.queryByRole('row', { name: /Paper One/ })).not.toBeInTheDocument();
    expect(screen.getByRole('row', { name: /Paper Two/ })).toBeInTheDocument();

    await user.clear(screen.getByRole('searchbox', { name: '搜索文献' }));
    await user.selectOptions(screen.getByRole('combobox', { name: '来源' }), 'seed');
    expect(screen.queryByRole('row', { name: /Paper Two/ })).not.toBeInTheDocument();
    expect(screen.getByRole('complementary', { name: '论文预览' })).toHaveTextContent('Paper One');
  });

  it('optimistically toggles favorite and restores the exact cache after failure', async () => {
    const user = userEvent.setup();
    const request = deferred<void>();
    apiMocks.setFavorite.mockReturnValueOnce(request.promise);
    renderLibrary();

    const favorite = await screen.findByRole('button', { name: '收藏 Paper One' });
    await user.click(favorite);

    expect(screen.getByRole('button', { name: '取消收藏 Paper One' })).toBeInTheDocument();
    expect(apiMocks.setFavorite).toHaveBeenCalledWith('one', true);

    request.reject(new Error('favorite write failed'));

    await waitFor(() => {
      expect(screen.getByRole('button', { name: '收藏 Paper One' })).toBeInTheDocument();
    });
    expect(screen.getByRole('alert')).toHaveTextContent('favorite write failed');
  });

  it('advances only through the fixed study-status cycle and rolls back without inventing a review step', async () => {
    const user = userEvent.setup();
    const request = deferred<void>();
    apiMocks.setStatus.mockReturnValueOnce(request.promise);
    renderLibrary();

    const status = await screen.findByRole('button', {
      name: 'Paper One 当前学习状态 未开始，切换到 学习中',
    });
    expect(screen.queryByRole('combobox', { name: 'Paper One 的学习状态' })).not.toBeInTheDocument();
    await user.click(status);
    expect(status).toHaveAccessibleName('Paper One 当前学习状态 学习中，切换到 已理解');
    expect(apiMocks.setStatus).toHaveBeenCalledWith('one', '学习中');

    request.reject(new Error('status write failed'));

    await waitFor(() => expect(status).toHaveAccessibleName(
      'Paper One 当前学习状态 未开始，切换到 学习中',
    ));
    expect(screen.getByRole('alert')).toHaveTextContent('status write failed');
  });

  it('serializes paper writes while keeping every handler bound to its original paper id', async () => {
    const user = userEvent.setup();
    const firstWrite = deferred<void>();
    apiMocks.setFavorite.mockReturnValueOnce(firstWrite.promise);
    renderLibrary();

    await user.click(await screen.findByRole('button', { name: '收藏 Paper One' }));
    await user.click(screen.getByRole('button', {
      name: 'Paper Two 当前学习状态 学习中，切换到 已理解',
    }));
    await user.click(screen.getByRole('row', { name: /Paper Two/ }));

    expect(apiMocks.setFavorite).toHaveBeenCalledWith('one', true);
    expect(apiMocks.setStatus).not.toHaveBeenCalled();

    firstWrite.resolve(undefined);

    await waitFor(() => {
      expect(apiMocks.setStatus).toHaveBeenCalledWith('two', '已理解');
    });
    expect(screen.getByRole('complementary', { name: '论文预览' })).toHaveTextContent('Paper Two');
  });

  it('orders semantic hits by server score and distinguishes every empty-result reason', async () => {
    const user = userEvent.setup();
    apiMocks.semanticSearch
      .mockResolvedValueOnce({
        type: 'result',
        ok: true,
        results: [
          { id: 'one', score: 0.61 },
          { id: 'two', score: 0.94 },
        ],
      })
      .mockResolvedValueOnce({ type: 'result', ok: true, results: [] });
    renderLibrary();

    const search = screen.getByRole('searchbox', { name: '搜索文献' });
    await screen.findByRole('row', { name: /Paper One/ });
    await user.type(search, 'research question');
    await user.click(screen.getByRole('button', { name: '语义检索' }));

    const semanticRows = await screen.findAllByRole('row');
    expect(semanticRows[1]).toHaveTextContent('Paper Two');
    expect(semanticRows[1]).toHaveTextContent('语义 94%');
    expect(semanticRows[2]).toHaveTextContent('Paper One');

    await user.click(screen.getByRole('button', { name: '返回普通检索' }));
    await user.clear(search);
    await user.type(search, 'definitely missing');
    expect(screen.getByText('没有匹配当前筛选的论文')).toBeInTheDocument();

    await user.clear(search);
    await user.click(screen.getByRole('checkbox', { name: '仅看收藏' }));
    await user.type(search, 'Paper One');
    expect(screen.getByText('收藏夹还是空的')).toBeInTheDocument();

    await user.click(screen.getByRole('checkbox', { name: '仅看收藏' }));
    await user.clear(search);
    await user.type(search, 'another question');
    await user.click(screen.getByRole('button', { name: '语义检索' }));
    expect(await screen.findByText('语义检索没有命中')).toBeInTheDocument();
  });

  it('keeps the add editor and current ledger selection recoverable after a failed save', async () => {
    const user = userEvent.setup();
    apiMocks.addPaper.mockRejectedValueOnce(new Error('add write failed'));
    renderLibrary();

    await screen.findByRole('row', { name: /Paper One/ });
    await user.click(screen.getByRole('button', { name: '添加论文' }));
    const editor = screen.getByRole('dialog', { name: '添加论文' });
    await user.type(within(editor).getByRole('textbox', { name: '英文题名' }), 'A recoverable paper');
    await user.click(within(editor).getByRole('button', { name: '保存论文' }));

    expect(await within(editor).findByRole('alert')).toHaveTextContent('add write failed');
    expect(within(editor).getByRole('textbox', { name: '英文题名' })).toHaveValue('A recoverable paper');
    expect(screen.getByRole('row', { name: /Paper One/ })).toBeInTheDocument();
    expect(screen.getByRole('row', { name: /Paper Two/ })).toBeInTheDocument();
    expect(screen.getByRole('complementary', { name: '论文预览' })).toHaveTextContent('Paper One');
  });

  it('reloads the authoritative ledger before selecting a newly added paper', async () => {
    const user = userEvent.setup();
    const added = paper('new-paper', {
      title: 'A newly indexed paper',
      source: 'manual',
      createdAt: '2026-08-06T09:00:00.000Z',
    });
    apiMocks.listPapers
      .mockResolvedValueOnce(papers)
      .mockResolvedValueOnce([...papers, added]);
    apiMocks.addPaper.mockResolvedValueOnce('new-paper');
    renderLibrary();

    await screen.findByRole('row', { name: /Paper One/ });
    await user.click(screen.getByRole('button', { name: '添加论文' }));
    const editor = screen.getByRole('dialog', { name: '添加论文' });
    await user.type(within(editor).getByRole('textbox', { name: '英文题名' }), 'A newly indexed paper');
    await user.click(within(editor).getByRole('button', { name: '保存论文' }));

    expect(apiMocks.addPaper).toHaveBeenCalledWith(expect.objectContaining({
      title: 'A newly indexed paper',
    }));
    expect(await screen.findByRole('row', { name: /A newly indexed paper/ })).toBeInTheDocument();
    expect(screen.queryByRole('dialog', { name: '添加论文' })).not.toBeInTheDocument();
    expect(screen.getByRole('complementary', { name: '论文预览' })).toHaveTextContent('A newly indexed paper');
  });

  it('keeps detail metadata, cache, and selection recoverable after an edit fails', async () => {
    const user = userEvent.setup();
    apiMocks.updatePaper.mockRejectedValueOnce(new Error('update write failed'));
    const { queryClient } = renderLibrary();

    const preview = await screen.findByRole('complementary', { name: '论文预览' });
    expect(await within(preview).findByText('Ada Lovelace, 林研')).toBeInTheDocument();
    await user.click(within(preview).getByRole('button', { name: '编辑论文' }));
    const editor = screen.getByRole('dialog', { name: '编辑论文' });
    const title = within(editor).getByRole('textbox', { name: '英文题名' });
    expect(within(editor).getByRole('textbox', { name: '作者' })).toHaveValue('Ada Lovelace, 林研');
    await user.clear(title);
    await user.type(title, 'Edited Paper One');
    await user.click(within(editor).getByRole('button', { name: '保存论文' }));

    expect(await within(editor).findByRole('alert')).toHaveTextContent('update write failed');
    expect(title).toHaveValue('Edited Paper One');
    expect(apiMocks.updatePaper).toHaveBeenCalledWith('one', expect.objectContaining({
      title: 'Edited Paper One',
      authors: ['Ada Lovelace', '林研'],
    }));
    expect(screen.getByRole('row', { name: /Paper One/ })).toBeInTheDocument();
    expect(screen.queryByRole('row', { name: /Edited Paper One/ })).not.toBeInTheDocument();
    expect(queryClient.getQueryData(paperKeys.detail('one'))).toEqual(paperRecord('one'));
    expect(screen.getByRole('complementary', { name: '论文预览' })).toHaveTextContent('Paper One');
  });

  it('requires explicit delete confirmation and preserves every entity cache after failure', async () => {
    const user = userEvent.setup();
    apiMocks.deletePaper.mockRejectedValueOnce(new Error('delete write failed'));
    const { queryClient } = renderLibrary();

    const preview = await screen.findByRole('complementary', { name: '论文预览' });
    await within(preview).findByText('Ada Lovelace, 林研');
    queryClient.setQueryData(artifactKeys.note('one'), 'note cache');
    queryClient.setQueryData(artifactKeys.explainer('one'), 'explainer cache');
    queryClient.setQueryData(artifactKeys.translation('one'), 'translation cache');
    await user.click(within(preview).getByRole('button', { name: '删除论文' }));

    const confirmation = screen.getByRole('dialog', { name: '删除 Paper One' });
    expect(within(confirmation).getByText(/提交前可以取消/)).toBeInTheDocument();
    await user.click(within(confirmation).getByRole('button', { name: '确认删除' }));

    expect(await within(confirmation).findByRole('alert')).toHaveTextContent('delete write failed');
    expect(apiMocks.deletePaper).toHaveBeenCalledWith('one');
    expect(screen.getByRole('row', { name: /Paper One/ })).toBeInTheDocument();
    expect(queryClient.getQueryData(paperKeys.detail('one'))).toEqual(paperRecord('one'));
    expect(queryClient.getQueryData(artifactKeys.note('one'))).toBe('note cache');
    expect(queryClient.getQueryData(artifactKeys.explainer('one'))).toBe('explainer cache');
    expect(queryClient.getQueryData(artifactKeys.translation('one'))).toBe('translation cache');
    expect(useWorkspaceStore.getState().workspaceSelectionId).toBe('one');
    expect(within(confirmation).getByRole('button', { name: '确认删除' })).toBeEnabled();
  });

  it('patches list and detail caches for the fixed paper after an edit succeeds', async () => {
    const user = userEvent.setup();
    const { queryClient } = renderLibrary();

    const preview = await screen.findByRole('complementary', { name: '论文预览' });
    await within(preview).findByText('Ada Lovelace, 林研');
    await user.click(within(preview).getByRole('button', { name: '编辑论文' }));
    const editor = screen.getByRole('dialog', { name: '编辑论文' });
    const title = within(editor).getByRole('textbox', { name: '英文题名' });
    await user.clear(title);
    await user.type(title, 'Edited Paper One');
    await user.click(within(editor).getByRole('button', { name: '保存论文' }));

    await waitFor(() => {
      expect(screen.queryByRole('dialog', { name: '编辑论文' })).not.toBeInTheDocument();
    });
    expect(screen.getByRole('row', { name: /Edited Paper One/ })).toBeInTheDocument();
    expect(screen.getByRole('complementary', { name: '论文预览' })).toHaveTextContent('Edited Paper One');
    expect(queryClient.getQueryData<PaperRecord>(paperKeys.detail('one'))?.title).toBe('Edited Paper One');
    expect(apiMocks.updatePaper).toHaveBeenCalledWith('one', expect.objectContaining({
      title: 'Edited Paper One',
    }));
  });

  it('removes the confirmed entity caches and reconciles both selections after delete succeeds', async () => {
    const user = userEvent.setup();
    const { queryClient } = renderLibrary();

    const firstRow = await screen.findByRole('row', { name: /Paper One/ });
    const preview = screen.getByRole('complementary', { name: '论文预览' });
    await within(preview).findByText('Ada Lovelace, 林研');
    await user.click(within(firstRow).getByRole('checkbox', { name: '选择 Paper One' }));
    queryClient.setQueryData(artifactKeys.note('one'), 'note cache');
    await user.click(within(preview).getByRole('button', { name: '删除论文' }));
    await user.click(within(
      screen.getByRole('dialog', { name: '删除 Paper One' }),
    ).getByRole('button', { name: '确认删除' }));

    await waitFor(() => expect(screen.queryByRole('row', { name: /Paper One/ })).not.toBeInTheDocument());
    expect(screen.queryByRole('dialog', { name: '删除 Paper One' })).not.toBeInTheDocument();
    expect(screen.getByRole('row', { name: /Paper Two/ })).toBeInTheDocument();
    expect(screen.getByText('已选择 0 篇')).toBeInTheDocument();
    expect(useWorkspaceStore.getState().workspaceSelectionId).toBe('two');
    expect(screen.getByRole('complementary', { name: '论文预览' })).toHaveTextContent('Paper Two');
    expect(queryClient.getQueryState(paperKeys.detail('one'))).toBeUndefined();
    expect(queryClient.getQueryState(artifactKeys.note('one'))).toBeUndefined();
    expect(apiMocks.deletePaper).toHaveBeenCalledWith('one');
  });
});
