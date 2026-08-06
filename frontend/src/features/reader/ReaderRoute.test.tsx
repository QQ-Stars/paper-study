import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { useEffect } from 'react';
import {
  createMemoryRouter,
  MemoryRouter,
  RouterProvider,
} from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import type { PaperRecord, PdfStatus } from '../../lib/api/types';
import { resetWorkspaceStore, useWorkspaceStore } from '../../lib/workspace';
import { Component } from './ReaderRoute';

const apiMocks = vi.hoisted(() => ({
  getPaper: vi.fn(),
  getPdfStatus: vi.fn(),
}));

vi.mock('../../lib/api/paperApi', () => ({
  paperApi: { getPaper: apiMocks.getPaper },
}));

vi.mock('../../lib/api/workspaceApi', () => ({
  workspaceApi: { getPdfStatus: apiMocks.getPdfStatus },
}));

vi.mock('./PdfWorkspace', () => ({
  PdfWorkspace: ({
    paperId,
    onGenerationChange,
  }: {
    paperId: string;
    onGenerationChange?(generation: number): void;
  }) => {
    useEffect(() => {
      onGenerationChange?.(paperId === 'paper-a' ? 4 : 7);
    }, [onGenerationChange, paperId]);
    return <div data-testid="pdf-workspace" data-paper-id={paperId} />;
  },
}));

vi.mock('./ArtifactPanel', () => ({
  ArtifactPanel: ({
    paperId,
    generation,
  }: {
    paperId: string;
    generation: number;
  }) => (
    <div
      data-testid="artifact-panel"
      data-paper-id={paperId}
      data-generation={generation}
    />
  ),
}));

function paper(id: string): PaperRecord {
  return {
    id,
    source: 'seed',
    sourceId: null,
    arxivId: null,
    doi: null,
    s2Id: null,
    openalexId: null,
    title: `Paper ${id}`,
    titleZh: id === 'paper-a' ? '论文甲' : '论文乙',
    titleNorm: null,
    authors: ['Ada Lovelace', 'Edsger Dijkstra'],
    venue: 'CHI',
    year: '2026',
    abstract: 'A research abstract.',
    tldr: 'A concise finding.',
    citations: 12,
    s2Fields: ['Human-Computer Interaction'],
    url: null,
    pdfUrl: null,
    pdfPath: `${id}.pdf`,
    type: 'Research',
    topic: 'Research tools',
    task: null,
    models: [],
    datasets: [],
    contribution: 'A verified contribution.',
    tags: [],
    relevance: 0.91,
    explainer: null,
    extractedBy: null,
    orderNo: 1,
    createdAt: '2026-08-01T08:00:00.000Z',
    updatedAt: '2026-08-02T08:00:00.000Z',
  };
}

function pdfStatus(id: string, hasPdf = true): PdfStatus {
  return {
    id,
    hasPdf,
    size: hasPdf ? 2048 : 0,
    path: hasPdf ? `${id}.pdf` : '',
    canDownload: !hasPdf,
  };
}

function createClient() {
  return new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  });
}

function renderReader(initialEntry = '/reader/paper-a') {
  const router = createMemoryRouter(
    [{ path: '/reader/:paperId', Component }],
    { initialEntries: [initialEntry] },
  );
  const client = createClient();
  const view = render(
    <QueryClientProvider client={client}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
  return { client, router, ...view };
}

beforeEach(() => {
  resetWorkspaceStore();
  apiMocks.getPaper.mockReset().mockImplementation(async (id: string) => paper(id));
  apiMocks.getPdfStatus.mockReset().mockImplementation(async (id: string) => pdfStatus(id));
});

describe('Reader route', () => {
  it('uses the URL paper id for every server fact and synchronizes only toward the workspace store', async () => {
    useWorkspaceStore.getState().setWorkspaceSelectionId('stale-store-paper');
    renderReader();

    expect(await screen.findByRole('heading', { name: '论文甲' })).toBeInTheDocument();
    expect(apiMocks.getPaper).toHaveBeenCalledWith('paper-a', expect.any(AbortSignal));
    expect(apiMocks.getPdfStatus).toHaveBeenCalledWith('paper-a', expect.any(AbortSignal));
    expect(screen.getByTestId('pdf-workspace')).toHaveAttribute('data-paper-id', 'paper-a');
    expect(screen.getByTestId('artifact-panel')).toHaveAttribute('data-paper-id', 'paper-a');
    await waitFor(() => {
      expect(screen.getByTestId('artifact-panel')).toHaveAttribute('data-generation', '4');
    });
    expect(useWorkspaceStore.getState().workspaceSelectionId).toBe('paper-a');
  });

  it('aborts stale route queries and drops the old identity when the URL changes', async () => {
    const signals = new Map<string, AbortSignal>();
    apiMocks.getPaper.mockImplementation((id: string, signal: AbortSignal) => {
      signals.set(`paper:${id}`, signal);
      if (id === 'paper-a') {
        return new Promise<PaperRecord | null>((_resolve, reject) => {
          signal.addEventListener('abort', () => {
            reject(new DOMException('route changed', 'AbortError'));
          }, { once: true });
        });
      }
      return Promise.resolve(paper(id));
    });
    apiMocks.getPdfStatus.mockImplementation((id: string, signal: AbortSignal) => {
      signals.set(`pdf:${id}`, signal);
      if (id === 'paper-a') {
        return new Promise<PdfStatus>((_resolve, reject) => {
          signal.addEventListener('abort', () => {
            reject(new DOMException('route changed', 'AbortError'));
          }, { once: true });
        });
      }
      return Promise.resolve(pdfStatus(id));
    });
    const view = renderReader();

    expect(await screen.findByText('正在读取论文与 PDF 状态…')).toBeInTheDocument();
    await act(async () => {
      await view.router.navigate('/reader/paper-b');
    });

    expect(await screen.findByRole('heading', { name: '论文乙' })).toBeInTheDocument();
    await waitFor(() => {
      expect(signals.get('paper:paper-a')?.aborted).toBe(true);
      expect(signals.get('pdf:paper-a')?.aborted).toBe(true);
    });
    expect(screen.getByTestId('pdf-workspace')).toHaveAttribute('data-paper-id', 'paper-b');
    expect(screen.getByTestId('artifact-panel')).toHaveAttribute('data-paper-id', 'paper-b');
    expect(screen.queryByText('论文甲')).not.toBeInTheDocument();
    expect(useWorkspaceStore.getState().workspaceSelectionId).toBe('paper-b');
  });

  it('shows a truthful missing-paper state without mounting entity-scoped tools', async () => {
    apiMocks.getPaper.mockResolvedValue(null);
    renderReader('/reader/missing-paper');

    expect(await screen.findByRole('heading', { name: '找不到这篇论文' })).toBeInTheDocument();
    expect(screen.getByText('论文可能已删除，或链接中的标识无效。')).toBeInTheDocument();
    expect(screen.queryByTestId('pdf-workspace')).not.toBeInTheDocument();
    expect(screen.queryByTestId('artifact-panel')).not.toBeInTheDocument();
  });

  it('keeps research artifacts available when the paper has no local PDF', async () => {
    apiMocks.getPdfStatus.mockImplementation(async (id: string) => pdfStatus(id, false));
    renderReader();

    expect(await screen.findByRole('heading', { name: '论文甲' })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: '尚未关联 PDF' })).toBeInTheDocument();
    expect(screen.getByText('可以继续编辑笔记，PDF 下载或补齐后即可开始阅读。')).toBeInTheDocument();
    expect(screen.queryByTestId('pdf-workspace')).not.toBeInTheDocument();
    expect(screen.getByTestId('artifact-panel')).toHaveAttribute('data-paper-id', 'paper-a');
  });

  it('offers an explicit retry after a route fact fails', async () => {
    const user = userEvent.setup();
    apiMocks.getPaper
      .mockRejectedValueOnce(new Error('论文服务不可用'))
      .mockResolvedValueOnce(paper('paper-a'));
    renderReader();

    expect(await screen.findByRole('alert')).toHaveTextContent('论文服务不可用');
    await user.click(screen.getByRole('button', { name: '重新读取' }));

    expect(await screen.findByRole('heading', { name: '论文甲' })).toBeInTheDocument();
    expect(apiMocks.getPaper).toHaveBeenCalledTimes(2);
  });

  it('handles a missing route parameter without starting requests', () => {
    render(
      <QueryClientProvider client={createClient()}>
        <MemoryRouter>
          <Component />
        </MemoryRouter>
      </QueryClientProvider>,
    );

    expect(screen.getByRole('heading', { name: '缺少论文标识' })).toBeInTheDocument();
    expect(apiMocks.getPaper).not.toHaveBeenCalled();
    expect(apiMocks.getPdfStatus).not.toHaveBeenCalled();
  });
});
