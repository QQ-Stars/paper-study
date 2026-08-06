import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, useLocation } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import type { Candidate, CitationGraph, PaperListItem } from '../../lib/api/types';
import { Component } from './InsightsRoute';

const apiMocks = vi.hoisted(() => ({
  listPapers: vi.fn(),
  getCitationGraph: vi.fn(),
  buildCitationGraph: vi.fn(),
  normalizeVenues: vi.fn(),
  recommend: vi.fn(),
  embed: vi.fn(),
  semanticSearch: vi.fn(),
}));

vi.mock('../../lib/api/paperApi', () => ({
  paperApi: { listPapers: apiMocks.listPapers },
}));
vi.mock('../../lib/api/insightsGateway', () => ({
  insightsGateway: {
    getCitationGraph: apiMocks.getCitationGraph,
    buildCitationGraph: apiMocks.buildCitationGraph,
    normalizeVenues: apiMocks.normalizeVenues,
    recommend: apiMocks.recommend,
    embed: apiMocks.embed,
    semanticSearch: apiMocks.semanticSearch,
  },
}));

const paper: PaperListItem = {
  id: 'p1',
  file: 'p1.pdf',
  title: 'Graph Paper',
  titleZh: '图谱论文',
  venue: 'CSCW',
  year: '2026',
  type: '研究',
  topic: '图谱',
  pdfUrl: null,
  pdfPath: null,
  url: null,
  tldr: null,
  contribution: null,
  citations: 12,
  createdAt: '2026-08-01T00:00:00.000Z',
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
  favorite: true,
  hasPdf: true,
};

const graph: CitationGraph = {
  nodes: [{
    id: 'p1',
    title: 'Graph Paper',
    venue: 'CSCW',
    year: '2026',
    type: '研究',
    topic: '图谱',
    citations: 12,
    indeg: 3,
    outdeg: 1,
  }],
  links: [],
  edgeCount: 0,
};

const recommendation: Candidate = {
  source: 'semanticscholar',
  sourceId: 'recommended-1',
  title: 'Lifecycle-safe Graph Systems',
  authors: ['Ada Researcher'],
  venue: 'CHI',
  year: '2025',
  abstract: null,
  tldr: 'A lifecycle-safe graph workspace.',
  fields: ['Human-Computer Interaction'],
  citations: 8,
  url: 'https://example.test/recommended-1',
  pdfUrl: null,
  arxivId: null,
  doi: null,
  s2Id: 'recommended-1',
  ccf: 'A',
  type: '研究',
  topic: '图谱',
  task: null,
  models: [],
  datasets: [],
  contribution: null,
  llmTldr: null,
  tags: [],
  relevance: 0.87,
  inLibrary: false,
  candidateId: null,
};

function LocationProbe() {
  return <output data-testid="location">{useLocation().pathname}</output>;
}

function renderInsights() {
  const client = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={['/insights']}>
        <Component />
        <LocationProbe />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  apiMocks.listPapers.mockReset().mockResolvedValue([paper]);
  apiMocks.getCitationGraph.mockReset().mockResolvedValue(graph);
  apiMocks.buildCitationGraph.mockReset().mockImplementation(async (options) => {
    options.onEvent?.({ type: 'progress', line: '[DONE] graph ready' });
    return { type: 'result', ok: true, edges: 0, nodes: 1 };
  });
  apiMocks.normalizeVenues.mockReset().mockResolvedValue({
    type: 'result',
    ok: true,
    changed: 0,
    mapping: {},
  });
  apiMocks.recommend.mockReset().mockImplementation(async (_paperId, _limit, options) => {
    options.onEvent?.({ type: 'progress', line: '[1/1] matching papers' });
    return { type: 'result', ok: true, candidates: [recommendation] };
  });
  apiMocks.embed.mockReset().mockImplementation(async (_scope, options) => {
    options.onEvent?.({ type: 'progress', line: '[2/2] vectors indexed' });
    return { type: 'result', ok: true, indexed: 2, total: 2 };
  });
  apiMocks.semanticSearch.mockReset().mockImplementation(async (_query, _limit, options) => {
    options.onEvent?.({ type: 'progress', line: '[1/1] semantic match ready' });
    return { type: 'result', ok: true, results: [{ id: 'p1', score: 0.934 }] };
  });
});

describe('Insights route', () => {
  it('renders only query-backed metrics and provides keyboard-accessible graph nodes', async () => {
    const user = userEvent.setup();
    renderInsights();

    expect(await screen.findByText('年度轨迹')).toBeInTheDocument();
    expect(screen.getByText('主题结构')).toBeInTheDocument();
    expect(screen.getByText('引用网络')).toBeInTheDocument();
    expect(screen.getByText('可访问节点列表')).toBeInTheDocument();
    expect(screen.getByText('图节点').nextSibling).toHaveTextContent('1');

    await user.click(screen.getByRole('button', { name: /Graph Paper/ }));
    expect(screen.getByTestId('location')).toHaveTextContent('/reader/p1');
  });

  it('runs citation build explicitly, exposes progress, and refetches the graph', async () => {
    const user = userEvent.setup();
    renderInsights();

    await user.click(await screen.findByRole('button', { name: '重建引用图' }));

    expect(apiMocks.buildCitationGraph).toHaveBeenCalledOnce();
    expect(await screen.findByText('[DONE] graph ready')).toBeInTheDocument();
    await waitFor(() => expect(apiMocks.getCitationGraph.mock.calls.length).toBeGreaterThan(1));
  });

  it('lets the user explicitly request recommendations from a real paper', async () => {
    const user = userEvent.setup();
    renderInsights();

    await user.click(await screen.findByRole('button', { name: '推荐相似论文' }));

    expect(await screen.findByText('[1/1] matching papers')).toBeInTheDocument();
    expect(await screen.findByText('Lifecycle-safe Graph Systems')).toBeInTheDocument();
    expect(apiMocks.recommend).toHaveBeenCalledWith(
      'p1',
      14,
      expect.objectContaining({ signal: expect.any(AbortSignal) }),
    );
  });

  it('lets the user explicitly update missing embeddings', async () => {
    const user = userEvent.setup();
    renderInsights();

    await user.click(await screen.findByRole('button', { name: '更新缺失向量' }));

    expect(await screen.findByText('[2/2] vectors indexed')).toBeInTheDocument();
    expect(await screen.findByText('向量索引完成：2 / 2。')).toBeInTheDocument();
    expect(apiMocks.embed).toHaveBeenCalledWith(
      'missing',
      expect.objectContaining({ signal: expect.any(AbortSignal) }),
    );
  });

  it('runs semantic search only on explicit request and presents real paper scores', async () => {
    const user = userEvent.setup();
    renderInsights();

    await screen.findByText('年度轨迹');
    expect(apiMocks.semanticSearch).not.toHaveBeenCalled();

    await user.type(screen.getByRole('searchbox', { name: '语义查询' }), 'graph lifecycle');
    await user.click(screen.getByRole('button', { name: '语义搜索' }));

    expect(await screen.findByText('[1/1] semantic match ready')).toBeInTheDocument();
    expect(await screen.findByRole('button', { name: 'Graph Paper' })).toBeInTheDocument();
    expect(screen.getByText('语义得分 0.934')).toBeInTheDocument();
    expect(apiMocks.semanticSearch).toHaveBeenCalledWith(
      'graph lifecycle',
      60,
      expect.objectContaining({ signal: expect.any(AbortSignal) }),
    );
  });

  it('keeps a restarted command authoritative after stopping an older run', async () => {
    const user = userEvent.setup();
    let finishFirst: ((value: { type: 'result'; ok: true; edges: number; nodes: number }) => void) | undefined;
    let firstOptions: { signal: AbortSignal; onEvent?: (event: unknown) => void } | undefined;
    apiMocks.buildCitationGraph
      .mockReset()
      .mockImplementationOnce((options) => {
        firstOptions = options;
        return new Promise((resolve) => {
          finishFirst = resolve;
        });
      })
      .mockImplementationOnce(async (options) => {
        options.onEvent?.({ type: 'progress', line: 'new run is authoritative' });
        return { type: 'result', ok: true, edges: 2, nodes: 3 };
      });
    renderInsights();

    await user.click(await screen.findByRole('button', { name: '重建引用图' }));
    await user.click(await screen.findByRole('button', { name: '停止接收' }));
    expect(firstOptions?.signal.aborted).toBe(true);
    await user.click(screen.getByRole('button', { name: '重建引用图' }));

    expect(await screen.findByText('new run is authoritative')).toBeInTheDocument();
    expect(await screen.findByText('引用图已更新：3 个节点，2 条边。')).toBeInTheDocument();

    await act(async () => {
      firstOptions?.onEvent?.({ type: 'progress', line: 'stale run leaked' });
      finishFirst?.({ type: 'result', ok: true, edges: 99, nodes: 99 });
      await Promise.resolve();
    });
    expect(screen.queryByText('stale run leaked')).not.toBeInTheDocument();
    expect(screen.queryByText('引用图已更新：99 个节点，99 条边。')).not.toBeInTheDocument();
    expect(screen.getByText('引用图已更新：3 个节点，2 条边。')).toBeInTheDocument();
  });

  it('aborts the active command when the route unmounts', async () => {
    const user = userEvent.setup();
    let activeSignal: AbortSignal | undefined;
    apiMocks.embed.mockReset().mockImplementation((_scope, options) => {
      activeSignal = options.signal;
      return new Promise(() => undefined);
    });
    const view = renderInsights();

    await user.click(await screen.findByRole('button', { name: '更新缺失向量' }));
    expect(activeSignal?.aborted).toBe(false);
    view.unmount();

    expect(activeSignal?.aborted).toBe(true);
  });

  it('reports command failures without replacing query-backed insight data', async () => {
    const user = userEvent.setup();
    apiMocks.embed.mockReset().mockRejectedValue(new Error('向量服务暂不可用'));
    renderInsights();

    await user.click(await screen.findByRole('button', { name: '更新缺失向量' }));

    expect(await screen.findByText('向量服务暂不可用')).toBeInTheDocument();
    expect(screen.getByText('年度轨迹')).toBeInTheDocument();
  });

  it('shows explanatory chart empty states instead of synthetic values', async () => {
    apiMocks.listPapers.mockResolvedValue([]);
    apiMocks.getCitationGraph.mockResolvedValue({ nodes: [], links: [], edgeCount: 0 });
    renderInsights();

    expect(await screen.findByText('年度轨迹')).toBeInTheDocument();
    expect(screen.getAllByText('没有足够的真实数据生成此图表。')).toHaveLength(5);
  });
});
