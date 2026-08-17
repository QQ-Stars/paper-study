import { useEffect, useReducer, useRef } from 'react';

import { useQuery, useQueryClient } from '@tanstack/react-query';

import { artifactGateway } from '../../lib/api/artifactGateway';
import { isAbortError } from '../../lib/api/errors';
import { paperKeys } from '../../lib/api/keys';
import type { ExplainBatchSummary } from '../../lib/streaming/contracts';

const explainerBatchPendingKey = ['library', 'explainer-batch', 'pending'] as const;

interface BatchOwner {
  readonly runId: number;
  readonly controller: AbortController;
}

interface BatchSession {
  readonly runId: number;
  readonly phase: 'idle' | 'running' | 'success' | 'failure' | 'stopped';
  readonly reconciling: boolean;
  readonly progress: string;
  readonly summary: ExplainBatchSummary | null;
  readonly error: string;
}

type BatchSessionAction =
  | { readonly type: 'started'; readonly runId: number }
  | { readonly type: 'progressed'; readonly runId: number; readonly progress: string }
  | { readonly type: 'succeeded'; readonly runId: number; readonly summary: ExplainBatchSummary }
  | { readonly type: 'failed'; readonly runId: number; readonly error: string }
  | { readonly type: 'stopped'; readonly runId: number }
  | { readonly type: 'reconciled'; readonly runId: number };

const idleSession: BatchSession = {
  runId: 0,
  phase: 'idle',
  reconciling: false,
  progress: '',
  summary: null,
  error: '',
};

function batchSessionReducer(state: BatchSession, action: BatchSessionAction): BatchSession {
  if (action.type === 'started') {
    return {
      runId: action.runId,
      phase: 'running',
      reconciling: false,
      progress: '',
      summary: null,
      error: '',
    };
  }
  if (action.runId !== state.runId) return state;
  if (action.type === 'reconciled') {
    return state.reconciling ? { ...state, reconciling: false } : state;
  }
  if (state.phase !== 'running') return state;

  switch (action.type) {
    case 'progressed':
      return { ...state, progress: action.progress };
    case 'succeeded':
      return {
        ...state,
        phase: 'success',
        reconciling: true,
        progress: '',
        summary: action.summary,
      };
    case 'failed':
      return {
        ...state,
        phase: 'failure',
        reconciling: true,
        progress: '',
        error: action.error,
      };
    case 'stopped':
      return { ...state, phase: 'stopped', reconciling: true, progress: '' };
  }
}

function errorMessage(error: unknown): string {
  return error instanceof Error && error.message.trim()
    ? error.message
    : '批量讲解暂时无法启动。';
}

function completionText(summary: ExplainBatchSummary): string {
  return `已完成 ${summary.done} / ${summary.total} 篇 · 失败 ${summary.failed.length} · 跳过无 PDF ${summary.skippedNoPdf.length}`;
}

export function ExplainerBatchManager() {
  const queryClient = useQueryClient();
  const ownerRef = useRef<BatchOwner | null>(null);
  const runSequence = useRef(0);
  const [session, dispatch] = useReducer(batchSessionReducer, idleSession);
  const pendingQuery = useQuery({
    queryKey: explainerBatchPendingKey,
    queryFn: ({ signal }) => artifactGateway.getExplainerPending(signal),
  });

  useEffect(() => () => {
    const owner = ownerRef.current;
    ownerRef.current = null;
    owner?.controller.abort();
  }, []);

  const reconcile = async () => {
    await Promise.allSettled([
      queryClient.invalidateQueries({ queryKey: explainerBatchPendingKey, exact: true }),
      queryClient.invalidateQueries({ queryKey: paperKeys.all() }),
    ]);
  };

  const start = async () => {
    if (ownerRef.current || session.reconciling) return;
    const owner: BatchOwner = {
      runId: ++runSequence.current,
      controller: new AbortController(),
    };
    ownerRef.current = owner;
    dispatch({ type: 'started', runId: owner.runId });

    try {
      const terminal = await artifactGateway.explainBatch(0, {
        signal: owner.controller.signal,
        onEvent(event) {
          if (ownerRef.current !== owner || event.type !== 'progress') return;
          dispatch({ type: 'progressed', runId: owner.runId, progress: event.line });
        },
      });
      if (ownerRef.current !== owner) return;
      dispatch({ type: 'succeeded', runId: owner.runId, summary: terminal.summary });
    } catch (error) {
      if (ownerRef.current !== owner) return;
      dispatch(isAbortError(error)
        ? { type: 'stopped', runId: owner.runId }
        : { type: 'failed', runId: owner.runId, error: errorMessage(error) });
    } finally {
      await reconcile();
      if (ownerRef.current === owner) ownerRef.current = null;
      dispatch({ type: 'reconciled', runId: owner.runId });
    }
  };

  const stop = () => {
    const owner = ownerRef.current;
    if (!owner) return;
    ownerRef.current = null;
    owner.controller.abort();
    dispatch({ type: 'stopped', runId: owner.runId });
  };

  const pending = pendingQuery.data;
  const metricPlaceholder = pendingQuery.isPending
    ? '…'
    : pendingQuery.isError ? '—' : null;
  const canStart = session.phase !== 'running'
    && !session.reconciling
    && !pendingQuery.isPending
    && !pendingQuery.isFetching
    && !pendingQuery.isError
    && (pending?.withPdf ?? 0) > 0;

  return (
    <section className="library-explainer-batch" aria-label="批量讲解管理">
      <div className="library-explainer-batch__identity">
        <p>AI ARTIFACTS</p>
        <div>
          <h3>批量讲解</h3>
          <span>统一补齐已有 PDF 的缺失讲解</span>
        </div>
      </div>

      <div className="library-explainer-batch__metrics" aria-live="polite">
        <span>待生成 {metricPlaceholder ?? pending?.pending ?? 0} 篇</span>
        <span>可直接处理 {metricPlaceholder ?? pending?.withPdf ?? 0} 篇</span>
        <span>缺少 PDF {metricPlaceholder ?? pending?.noPdf ?? 0} 篇</span>
      </div>

      <div className="library-explainer-batch__actions">
        {session.phase === 'running' ? (
          <button
            type="button"
            className="library-explainer-batch__stop"
            onClick={stop}
          >
            停止接收
          </button>
        ) : pendingQuery.isError ? (
          <button
            type="button"
            className="library-explainer-batch__start"
            disabled={pendingQuery.isFetching}
            onClick={() => { void pendingQuery.refetch(); }}
          >
            {pendingQuery.isFetching ? '正在重读统计…' : '重新读取统计'}
          </button>
        ) : (
          <button
            type="button"
            className="library-explainer-batch__start"
            disabled={!canStart}
            onClick={() => { void start(); }}
          >
            批量生成缺失讲解
          </button>
        )}
      </div>

      {pendingQuery.isError ? (
        <p className="library-explainer-batch__feedback library-explainer-batch__feedback--error" role="alert">
          待生成统计读取失败：{errorMessage(pendingQuery.error)}
        </p>
      ) : null}
      {/* 禁用原因明示：待讲解论文全部缺 PDF 时按钮不可点，
          若不解释用户会以为“点了没反应”。 */}
      {session.phase === 'idle' && !pendingQuery.isPending && !pendingQuery.isError
        && (pending?.pending ?? 0) > 0 && (pending?.withPdf ?? 0) === 0 ? (
        <p className="library-explainer-batch__feedback" role="status">
          暂无法开始：待生成讲解的论文都缺少本地 PDF（批量讲解需通读 PDF 全文），请先在「采集 → 本地 PDF」补齐 PDF。
        </p>
      ) : null}
      {session.phase === 'running' && session.progress ? (
        <p className="library-explainer-batch__feedback">{session.progress}</p>
      ) : null}
      {session.phase === 'success' && session.summary ? (
        <p className="library-explainer-batch__feedback" role="status">
          {completionText(session.summary)}
        </p>
      ) : null}
      {session.phase === 'failure' ? (
        <p className="library-explainer-batch__feedback library-explainer-batch__feedback--error" role="alert">
          {session.error}
        </p>
      ) : null}
      {session.phase === 'stopped' ? (
        <p className="library-explainer-batch__feedback" role="status">
          已停止接收；服务端可能仍在运行。
        </p>
      ) : null}
    </section>
  );
}
