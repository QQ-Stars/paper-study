import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { artifactKeys, paperKeys } from '../../lib/api/keys';
import { ArtifactPanel } from './ArtifactPanel';

const apiMocks = vi.hoisted(() => ({
  getNote: vi.fn(),
  getExplainer: vi.fn(),
  getTranslation: vi.fn(),
  saveNote: vi.fn(),
  explainPaper: vi.fn(),
  explainBatch: vi.fn(),
  translatePaper: vi.fn(),
}));

vi.mock('../../lib/api/paperApi', () => ({
  paperApi: {
    getNote: apiMocks.getNote,
    getExplainer: apiMocks.getExplainer,
    getTranslation: apiMocks.getTranslation,
    saveNote: apiMocks.saveNote,
  },
}));

vi.mock('../../lib/api/artifactGateway', () => ({
  artifactGateway: {
    explainPaper: apiMocks.explainPaper,
    explainBatch: apiMocks.explainBatch,
    translatePaper: apiMocks.translatePaper,
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

function createClient() {
  return new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  });
}

function renderPanel(paperId = 'paper-a', generation = 1) {
  const client = createClient();
  const view = render(
    <QueryClientProvider client={client}>
      <ArtifactPanel paperId={paperId} generation={generation} />
    </QueryClientProvider>,
  );
  return {
    client,
    ...view,
    rerenderPanel(nextPaperId: string, nextGeneration: number) {
      view.rerender(
        <QueryClientProvider client={client}>
          <ArtifactPanel paperId={nextPaperId} generation={nextGeneration} />
        </QueryClientProvider>,
      );
    },
  };
}

beforeEach(() => {
  apiMocks.getNote.mockReset().mockResolvedValue('');
  apiMocks.getExplainer.mockReset().mockResolvedValue('');
  apiMocks.getTranslation.mockReset().mockResolvedValue('');
  apiMocks.saveNote.mockReset().mockResolvedValue(undefined);
  apiMocks.explainPaper.mockReset().mockResolvedValue({
    type: 'result', ok: true, markdown: '# Explanation',
  });
  apiMocks.explainBatch.mockReset().mockResolvedValue({
    type: 'result',
    ok: true,
    summary: { total: 2, done: 2, failed: [], skippedNoPdf: [] },
  });
  apiMocks.translatePaper.mockReset().mockResolvedValue({
    type: 'result', ok: true, markdown: '# Translation',
  });
});

describe('ArtifactPanel', () => {
  it('keeps single-paper generation in Reader and excludes the library batch command', async () => {
    const user = userEvent.setup();
    apiMocks.explainPaper.mockImplementation(async (_id, _deep, options) => {
      options.onEvent?.({ type: 'progress', line: '正在生成当前论文讲解' });
      return { type: 'result', ok: true, markdown: '# Explanation' };
    });
    renderPanel();

    await user.click(await screen.findByRole('tab', { name: '讲解' }));
    expect(screen.getByRole('button', { name: '生成讲解' })).toBeEnabled();
    expect(screen.queryByRole('button', { name: '批量生成缺失讲解' })).not.toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: '生成讲解' }));

    expect(await screen.findByText('讲解已完成，正在同步服务端内容。')).toBeInTheDocument();
    expect(apiMocks.explainPaper).toHaveBeenCalledOnce();
    expect(apiMocks.explainBatch).not.toHaveBeenCalled();
  });

  it('integrates paper context into the same keyboard-accessible tab layer', async () => {
    const user = userEvent.setup();
    const client = createClient();
    render(
      <QueryClientProvider client={client}>
        <ArtifactPanel
          context={<div>上下文详情</div>}
          generation={1}
          paperId="paper-a"
        />
      </QueryClientProvider>,
    );

    const contextTab = await screen.findByRole('tab', { name: '上下文' });
    expect(contextTab).toHaveAttribute('aria-selected', 'true');
    const tabPanel = screen.getByRole('tabpanel');
    expect(tabPanel).toHaveTextContent('上下文详情');

    contextTab.focus();
    await user.tab();
    expect(tabPanel).toHaveFocus();

    contextTab.focus();
    await user.keyboard('{ArrowRight}');
    expect(screen.getByRole('tab', { name: '笔记' })).toHaveFocus();
    expect(screen.queryByText('上下文详情')).not.toBeInTheDocument();
  });

  it('moves tab focus and selection with arrow keys', async () => {
    const user = userEvent.setup();
    renderPanel();

    const noteTab = await screen.findByRole('tab', { name: '笔记' });
    noteTab.focus();
    await user.keyboard('{ArrowRight}');

    const explainerTab = screen.getByRole('tab', { name: '讲解' });
    expect(explainerTab).toHaveFocus();
    expect(explainerTab).toHaveAttribute('aria-selected', 'true');

    await user.keyboard('{ArrowLeft}');
    expect(noteTab).toHaveFocus();
    expect(noteTab).toHaveAttribute('aria-selected', 'true');
  });

  it('shows real empty states for blank payloads and the legacy explainer sentinel', async () => {
    const user = userEvent.setup();
    apiMocks.getExplainer.mockResolvedValue('*(暂无讲解)*');
    renderPanel();

    expect(await screen.findByText('暂无笔记')).toBeInTheDocument();
    await user.click(screen.getByRole('tab', { name: '讲解' }));
    expect(await screen.findByText('暂无讲解')).toBeInTheDocument();
    expect(screen.queryByText('*(暂无讲解)*')).not.toBeInTheDocument();
    await user.click(screen.getByRole('tab', { name: '翻译' }));
    expect(await screen.findByText('暂无翻译')).toBeInTheDocument();
  });

  it('aborts a stale explainer owner and drops its late progress and terminal commit', async () => {
    const user = userEvent.setup();
    const pending = deferred<{ type: 'result'; ok: true; markdown: string }>();
    let oldOptions: {
      signal?: AbortSignal;
      onEvent?: (event: { type: 'progress'; line: string }) => void;
    } | undefined;
    apiMocks.getExplainer.mockImplementation(async (id: string) => (
      id === 'paper-b' ? '# Current paper' : ''
    ));
    apiMocks.explainPaper.mockImplementation((id, deep, options) => {
      expect(id).toBe('paper-a');
      expect(deep).toBe(false);
      oldOptions = options;
      return pending.promise;
    });
    const view = renderPanel('paper-a', 1);
    const invalidate = vi.spyOn(view.client, 'invalidateQueries');

    await user.click(await screen.findByRole('tab', { name: '讲解' }));
    await user.click(screen.getByRole('button', { name: '生成讲解' }));
    act(() => oldOptions?.onEvent?.({ type: 'progress', line: 'paper-a progress' }));
    expect(await screen.findByText('paper-a progress')).toBeInTheDocument();

    view.rerenderPanel('paper-b', 2);
    expect(oldOptions?.signal?.aborted).toBe(true);
    act(() => oldOptions?.onEvent?.({ type: 'progress', line: 'late paper-a progress' }));
    await act(async () => {
      pending.resolve({ type: 'result', ok: true, markdown: '# stale result' });
      await pending.promise;
    });

    await waitFor(() => expect(screen.getByRole('tabpanel')).toHaveTextContent('# Current paper'));
    expect(screen.queryByText('late paper-a progress')).not.toBeInTheDocument();
    expect(screen.queryByText('讲解已完成，正在同步服务端内容。')).not.toBeInTheDocument();
    await waitFor(() => expect(invalidate).toHaveBeenCalledWith({
      queryKey: artifactKeys.explainer('paper-a'), exact: true,
    }));
    expect(invalidate).not.toHaveBeenCalledWith({
      queryKey: artifactKeys.explainer('paper-b'), exact: true,
    });
  });

  it('reconciles the fixed explainer and paper facts after a successful settle', async () => {
    const user = userEvent.setup();
    apiMocks.explainPaper.mockImplementation(async (_id, _deep, options) => {
      options.onEvent?.({ type: 'progress', line: '正在读取 PDF' });
      return { type: 'result', ok: true, markdown: '# Generated' };
    });
    const view = renderPanel();
    const invalidate = vi.spyOn(view.client, 'invalidateQueries');

    await user.click(await screen.findByRole('tab', { name: '讲解' }));
    await user.click(screen.getByRole('button', { name: '生成讲解' }));

    expect(await screen.findByText('讲解已完成，正在同步服务端内容。')).toBeInTheDocument();
    await waitFor(() => {
      expect(invalidate).toHaveBeenCalledWith({
        queryKey: artifactKeys.explainer('paper-a'), exact: true,
      });
      expect(invalidate).toHaveBeenCalledWith({
        queryKey: paperKeys.list(), exact: true,
      });
      expect(invalidate).toHaveBeenCalledWith({
        queryKey: paperKeys.detail('paper-a'), exact: true,
      });
    });
  });

  it('reports stream failure honestly and still reconciles translation on settle', async () => {
    const user = userEvent.setup();
    apiMocks.translatePaper.mockRejectedValue(new Error('模型不可用'));
    const view = renderPanel();
    const invalidate = vi.spyOn(view.client, 'invalidateQueries');

    await user.click(await screen.findByRole('tab', { name: '翻译' }));
    await user.click(screen.getByRole('button', { name: '生成翻译' }));

    expect(await screen.findByRole('alert')).toHaveTextContent('翻译失败：模型不可用');
    await waitFor(() => expect(invalidate).toHaveBeenCalledWith({
      queryKey: artifactKeys.translation('paper-a'), exact: true,
    }));
  });

  it('stops receiving without claiming that the server task was cancelled', async () => {
    const user = userEvent.setup();
    let signal: AbortSignal | undefined;
    apiMocks.translatePaper.mockImplementation((_id, options) => {
      signal = options.signal;
      return new Promise((_resolve, reject) => {
        signal?.addEventListener('abort', () => {
          reject(new DOMException('stopped', 'AbortError'));
        }, { once: true });
      });
    });
    renderPanel();

    await user.click(await screen.findByRole('tab', { name: '翻译' }));
    await user.click(screen.getByRole('button', { name: '生成翻译' }));
    await user.click(screen.getByRole('button', { name: '停止接收翻译' }));

    expect(signal?.aborted).toBe(true);
    expect(await screen.findByText('已停止接收响应；服务端可能仍在处理。')).toBeInTheDocument();
    expect(screen.queryByText(/任务已取消/)).not.toBeInTheDocument();
  });

  it('captures note identity, aborts on paper change, and never commits the late save', async () => {
    const user = userEvent.setup();
    const pending = deferred<void>();
    let saveSignal: AbortSignal | undefined;
    apiMocks.getNote.mockImplementation(async (id: string) => (
      id === 'paper-b' ? 'paper-b note' : 'paper-a note'
    ));
    apiMocks.saveNote.mockImplementation((id, content, signal) => {
      expect(id).toBe('paper-a');
      expect(content).toBe('edited paper-a note');
      saveSignal = signal;
      return pending.promise;
    });
    const view = renderPanel('paper-a', 1);
    const invalidate = vi.spyOn(view.client, 'invalidateQueries');

    const editor = await screen.findByLabelText('笔记内容');
    await user.clear(editor);
    await user.type(editor, 'edited paper-a note');
    await user.click(screen.getByRole('button', { name: '保存笔记' }));

    view.rerenderPanel('paper-b', 2);
    expect(saveSignal?.aborted).toBe(true);
    await act(async () => {
      pending.resolve();
      await pending.promise;
    });

    expect(await screen.findByDisplayValue('paper-b note')).toBeInTheDocument();
    expect(screen.queryByText('笔记已保存，正在同步服务端内容。')).not.toBeInTheDocument();
    await waitFor(() => expect(invalidate).toHaveBeenCalledWith({
      queryKey: artifactKeys.note('paper-a'), exact: true,
    }));
  });

  it('aborts an in-flight stream when the panel unmounts', async () => {
    const user = userEvent.setup();
    let signal: AbortSignal | undefined;
    apiMocks.translatePaper.mockImplementation((_id, options) => {
      signal = options.signal;
      return new Promise(() => undefined);
    });
    const view = renderPanel();

    await user.click(await screen.findByRole('tab', { name: '翻译' }));
    await user.click(screen.getByRole('button', { name: '生成翻译' }));
    view.unmount();

    expect(signal?.aborted).toBe(true);
  });
});
