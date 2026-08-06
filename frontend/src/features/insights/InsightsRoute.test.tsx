import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, useLocation } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import type { CitationGraph, PaperListItem } from '../../lib/api/types';
import { Component } from './InsightsRoute';

const apiMocks = vi.hoisted(() => ({
  listPapers: vi.fn(),
  getCitationGraph: vi.fn(),
  buildCitationGraph: vi.fn(),
  normalizeVenues: vi.fn(),
}));

vi.mock('../../lib/api/paperApi', () => ({
  paperApi: { listPapers: apiMocks.listPapers },
}));
vi.mock('../../lib/api/workspaceApi', () => ({
  workspaceApi: {
    getCitationGraph: apiMocks.getCitationGraph,
    buildCitationGraph: apiMocks.buildCitationGraph,
    normalizeVenues: apiMocks.normalizeVenues,
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

  it('shows explanatory chart empty states instead of synthetic values', async () => {
    apiMocks.listPapers.mockResolvedValue([]);
    apiMocks.getCitationGraph.mockResolvedValue({ nodes: [], links: [], edgeCount: 0 });
    renderInsights();

    expect(await screen.findByText('年度轨迹')).toBeInTheDocument();
    expect(screen.getAllByText('没有足够的真实数据生成此图表。')).toHaveLength(5);
  });
});
