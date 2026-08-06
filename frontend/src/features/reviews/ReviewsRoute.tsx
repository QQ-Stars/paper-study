/* eslint-disable react-refresh/only-export-components -- React Router lazy modules export route metadata with their component. */
import { useState } from 'react';

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useNavigate } from 'react-router-dom';

import {
  type WorkspaceRouteHandle,
  useWorkspaceStore,
} from '../../lib/workspace';
import { RouteErrorBoundary } from '../../components/feedback/RouteErrorBoundary';
import { reviewKeys } from '../../lib/api/keys';
import { paperApi } from '../../lib/api/paperApi';
import { ReviewGroup } from './ReviewGroup';
import './reviews.css';

const reviewMutationScope = { id: 'review-completion' } as const;

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
  const [audit, setAudit] = useState<ReviewAudit>({ phase: 'idle', message: '' });
  const query = useQuery({
    queryKey: reviewKeys.list(),
    queryFn: ({ signal }) => paperApi.getReviews(signal),
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

  return (
    <section className="reviews-route" aria-label="复习队列">
      <header className="reviews-route__intro">
        <div>
          <p>REVIEW / AUTHORITY SNAPSHOT</p>
          <h2>复习队列</h2>
          <span>{reviews.today} · {dueCount} 篇当前需要处理</span>
        </div>
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
