/* eslint-disable react-refresh/only-export-components -- React Router lazy modules export route metadata with their component. */
import { useState } from 'react';

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useNavigate } from 'react-router-dom';

import {
  type WorkspaceRouteHandle,
  useWorkspaceStore,
} from '../../lib/workspace';
import { RouteErrorBoundary } from '../../components/feedback/RouteErrorBoundary';
import { paperKeys, reviewKeys } from '../../lib/api/keys';
import { paperApi } from '../../lib/api/paperApi';
import { ReviewGroup } from './ReviewGroup';
import './reviews.css';

const reviewMutationScope = { id: 'review-write' } as const;

interface ReviewAudit {
  readonly phase: 'idle' | 'pending' | 'success' | 'error';
  readonly message: string;
}

export const handle = {
  title: '复习',
  layout: 'standard',
} satisfies WorkspaceRouteHandle;

export const ErrorBoundary = RouteErrorBoundary;

function errorText(error: unknown): string {
  return error instanceof Error && error.message.trim()
    ? error.message
    : '服务器拒绝了这次复习提交';
}

export function Component() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const setSelectedId = useWorkspaceStore((state) => state.setWorkspaceSelectionId);
  const [pendingPaperIds, setPendingPaperIds] = useState<ReadonlySet<string>>(
    () => new Set(),
  );
  const [startPaperId, setStartPaperId] = useState('');
  const [audit, setAudit] = useState<ReviewAudit>({ phase: 'idle', message: '' });
  const query = useQuery({
    queryKey: reviewKeys.list(),
    queryFn: ({ signal }) => paperApi.getReviews(signal),
  });
  const papersQuery = useQuery({
    queryKey: paperKeys.list(),
    queryFn: ({ signal }) => paperApi.listPapers(signal),
  });
  const start = useMutation({
    mutationKey: ['reviews', 'start'],
    scope: reviewMutationScope,
    mutationFn: ({ paperId }: { paperId: string }) => paperApi.startReview(paperId),
    onMutate: async ({ paperId }) => {
      setAudit({ phase: 'pending', message: `正在开始 ${paperId} 的复习计划` });
      await queryClient.cancelQueries({ queryKey: reviewKeys.list(), exact: true });
    },
    onSuccess: async (_plan, { paperId }) => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: reviewKeys.list(), exact: true }),
        queryClient.invalidateQueries({ queryKey: paperKeys.all() }),
      ]);
      setStartPaperId('');
      setAudit({ phase: 'success', message: `已开始 ${paperId} 的复习计划` });
    },
    onError: (error) => {
      setAudit({ phase: 'error', message: `开始复习计划失败：${errorText(error)}` });
    },
  });
  const complete = useMutation({
    mutationKey: ['reviews', 'complete'],
    scope: reviewMutationScope,
    mutationFn: ({ paperId }: { paperId: string }) => paperApi.completeReview(paperId),
    onMutate: async ({ paperId }) => {
      setAudit({ phase: 'pending', message: `正在提交 ${paperId} 的当前复习轮次` });
      await queryClient.cancelQueries({ queryKey: reviewKeys.list(), exact: true });
    },
    onSuccess: (result, { paperId }) => {
      queryClient.setQueryData(reviewKeys.list(), result.reviews);
      setAudit({ phase: 'success', message: `已完成 ${paperId} 的当前复习轮次` });
    },
    onError: (error) => {
      setAudit({ phase: 'error', message: `复习提交失败：${errorText(error)}` });
    },
  });
  const submitCompletion = (paperId: string) => {
    if (pendingPaperIds.has(paperId)) return;
    setPendingPaperIds((current) => new Set(current).add(paperId));
    complete.mutate({ paperId }, {
      onSettled: () => {
        setPendingPaperIds((current) => {
          const next = new Set(current);
          next.delete(paperId);
          return next;
        });
      },
    });
  };
  const openPaper = (paperId: string) => {
    setSelectedId(paperId);
    void navigate(`/reader/${encodeURIComponent(paperId)}`);
  };

  if (query.isPending) {
    return <div className="reviews-route__state" role="status">正在载入权威复习快照…</div>;
  }
  if (query.isError || !query.data) {
    return (
      <div className="reviews-route__state" role="alert">
        <strong>无法载入复习快照</strong>
        <span>{query.isError ? errorText(query.error) : '快照内容为空'}</span>
        <button type="button" onClick={() => void query.refetch()}>重试</button>
      </div>
    );
  }

  const reviews = query.data;
  const dueCount = reviews.counts.overdue + reviews.counts.dueToday;
  const plannedPaperIds = new Set([
    ...reviews.overdue,
    ...reviews.dueToday,
    ...reviews.upcoming,
    ...reviews.completed,
  ].map((item) => item.paperId));
  const eligiblePapers = (papersQuery.data ?? []).filter(
    (paper) => !plannedPaperIds.has(paper.id),
  );

  return (
    <section className="reviews-route" aria-label="复习队列">
      {/* 页标题已在顶部命令栏展示；intro 压缩为单行状态栏，把纵向空间让给队列。 */}
      <header className="reviews-route__intro">
        <span>{reviews.today} · {dueCount} 篇当前需要处理</span>
        <button
          type="button"
          disabled={query.isFetching}
          onClick={() => void query.refetch()}
        >
          {query.isFetching ? '同步中…' : '刷新快照'}
        </button>
      </header>

      {audit.phase !== 'idle' ? (
        <p
          className={`reviews-route__audit reviews-route__audit--${audit.phase}`}
          role={audit.phase === 'error' ? 'alert' : 'status'}
        >
          {audit.message}
        </p>
      ) : null}

      <section className="reviews-route__start" aria-labelledby="reviews-start-title">
        <div>
          <p>NEW PLAN</p>
          <h2 id="reviews-start-title">开始复习计划</h2>
          <span>选择尚未进入四组权威快照的论文，由服务端创建固定七轮计划。</span>
        </div>
        <label>
          <span>未安排论文</span>
          <select
            aria-label="选择未安排复习的论文"
            value={startPaperId}
            disabled={papersQuery.isPending || start.isPending}
            onChange={(event) => setStartPaperId(event.currentTarget.value)}
          >
            <option value="">请选择论文</option>
            {eligiblePapers.map((paper) => (
              <option key={paper.id} value={paper.id}>
                {paper.titleZh || paper.title} · {paper.id}
              </option>
            ))}
          </select>
        </label>
        <button
          type="button"
          disabled={!startPaperId || papersQuery.isPending || start.isPending || complete.isPending}
          onClick={() => start.mutate({ paperId: startPaperId })}
        >
          {start.isPending ? '开始中…' : '开始计划'}
        </button>
        {papersQuery.isError ? (
          <p className="reviews-route__start-error" role="alert">
            无法读取可安排论文：{errorText(papersQuery.error)}
            <button type="button" onClick={() => void papersQuery.refetch()}>重试</button>
          </p>
        ) : null}
        {!papersQuery.isPending && !papersQuery.isError && eligiblePapers.length === 0 ? (
          <p className="reviews-route__start-empty">当前所有论文都已安排复习计划。</p>
        ) : null}
      </section>

      <div className="reviews-route__groups">
        <ReviewGroup
          title="逾期复习"
          description="已经越过计划节点，优先恢复研究记忆。"
          tone="overdue"
          items={reviews.overdue}
          actionable
          pendingPaperIds={pendingPaperIds}
          onComplete={submitCompletion}
          onOpen={openPaper}
        />
        <ReviewGroup
          title="今日复习"
          description="计划在今天完成的当前轮次。"
          tone="today"
          items={reviews.dueToday}
          actionable
          pendingPaperIds={pendingPaperIds}
          onComplete={submitCompletion}
          onOpen={openPaper}
        />
        <ReviewGroup
          title="后续复习"
          description="未来节点只读展示，不允许提前推进计划。"
          tone="upcoming"
          items={reviews.upcoming}
          pendingPaperIds={pendingPaperIds}
          onComplete={submitCompletion}
          onOpen={openPaper}
        />
        <ReviewGroup
          title="已完成复习"
          description="七轮计划已经由服务端确认完成。"
          tone="completed"
          items={reviews.completed}
          pendingPaperIds={pendingPaperIds}
          onComplete={submitCompletion}
          onOpen={openPaper}
        />
      </div>
    </section>
  );
}
