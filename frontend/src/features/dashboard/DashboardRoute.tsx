/* eslint-disable react-refresh/only-export-components -- React Router lazy modules must export Component, ErrorBoundary, and handle together. */
import {
  useEffect,
  useMemo,
  useReducer,
  useRef,
  useState,
  useSyncExternalStore,
} from 'react';
import { useQuery } from '@tanstack/react-query';
import { useNavigate } from 'react-router-dom';

import {
  type WorkspaceRouteHandle,
  useWorkspaceStore,
} from '../../lib/workspace';
import { RouteErrorBoundary } from '../../components/feedback/RouteErrorBoundary';
import { jobKeys, paperKeys, reviewKeys } from '../../lib/api/keys';
import { paperApi } from '../../lib/api/paperApi';
import type {
  JobSummary,
  ReviewItem,
  ReviewSnapshot,
} from '../../lib/api/types';
import { workspaceApi } from '../../lib/api/workspaceApi';
import { PaperDeck, type PaperDeckItem } from './PaperDeck';
import {
  PaperInspector,
  type InspectorMode,
  type PaperInspectorPaper,
} from './PaperInspector';
import {
  ResearchTimeline,
} from './ResearchTimeline';
import { deckReducer, reconcile } from './deckReducer';
import type {
  DashboardJobEvidence,
  DashboardPaperEvidence,
  DashboardReviewEvidence,
} from './evidence';
import './dashboard.css';

export interface DashboardPaper
  extends PaperDeckItem,
    PaperInspectorPaper,
    DashboardPaperEvidence {}

export type DashboardLoadStatus = 'pending' | 'success' | 'error';

export interface DashboardViewProps {
  readonly papers: readonly DashboardPaper[];
  readonly reviews: readonly DashboardReviewEvidence[];
  readonly jobs: readonly DashboardJobEvidence[];
  readonly preferredPaperId?: string | null;
  readonly status?: DashboardLoadStatus;
  readonly errorMessage?: string;
  readonly onRetry?: () => void;
  readonly inspectorMode: InspectorMode;
  readonly inspectorOpen?: boolean;
  readonly onInspectorOpenChange?: (open: boolean) => void;
  readonly showInspector?: boolean;
  readonly showTimeline?: boolean;
  readonly onSelectionChange: (paperId: string | null) => void;
  readonly onOpenPaper: (paperId: string) => void;
}

export const handle = {
  title: '研究概览',
  layout: 'inspector-timeline',
  inspector: DashboardInspectorSlot,
  timeline: DashboardTimelineSlot,
} satisfies WorkspaceRouteHandle;

export const ErrorBoundary = RouteErrorBoundary;

export function DashboardView({
  papers,
  reviews,
  jobs,
  preferredPaperId = null,
  status = 'success',
  errorMessage = '研究概览暂时不可用。',
  onRetry,
  inspectorMode,
  inspectorOpen: controlledInspectorOpen,
  onInspectorOpenChange,
  showInspector = true,
  showTimeline = true,
  onSelectionChange,
  onOpenPaper,
}: DashboardViewProps) {
  const [deckState, dispatch] = useReducer(
    deckReducer,
    { papers, preferredPaperId },
    ({ papers: initialPapers, preferredPaperId: initialPreferredId }) => (
      reconcile(initialPapers, initialPreferredId)
    ),
  );
  const [localInspectorOpen, setLocalInspectorOpen] = useState(false);
  const previousPreferredId = useRef(preferredPaperId);
  const lastReportedSelection = useRef<string | null | undefined>(undefined);

  useEffect(() => {
    dispatch({
      type: 'papers-reconciled',
      papers,
      preferredId: preferredPaperId,
    });
  }, [papers, preferredPaperId]);

  useEffect(() => {
    if (previousPreferredId.current === preferredPaperId) return;
    previousPreferredId.current = preferredPaperId;
    if (preferredPaperId != null) {
      dispatch({ type: 'paper-selected', paperId: preferredPaperId });
    }
  }, [preferredPaperId]);

  useEffect(() => {
    if (lastReportedSelection.current === deckState.selectedId) return;
    lastReportedSelection.current = deckState.selectedId;
    onSelectionChange(deckState.selectedId);
  }, [deckState.selectedId, onSelectionChange]);

  const selectedPaper = deckState.selectedId == null
    ? null
    : papers.find((paper) => paper.id === deckState.selectedId) ?? null;
  const selectedReview = selectedPaper == null
    ? null
    : reviews.find((review) => review.paperId === selectedPaper.id) ?? null;
  const inspectorOpen = controlledInspectorOpen ?? localInspectorOpen;
  const setInspectorOpen = (nextOpen: boolean) => {
    if (controlledInspectorOpen === undefined) setLocalInspectorOpen(nextOpen);
    onInspectorOpenChange?.(nextOpen);
  };

  if (status === 'pending' && papers.length === 0) {
    return (
      <section className="dashboard-route dashboard-route--pending" aria-label="研究概览">
        <div className="dashboard-route__state" role="status">
          <strong>正在载入真实论文…</strong>
          <span>甲板会在论文列表通过数据契约后显示。</span>
        </div>
      </section>
    );
  }

  if (status === 'error' && papers.length === 0) {
    return (
      <section className="dashboard-route dashboard-route--error" aria-label="研究概览">
        <div className="dashboard-route__state" role="alert">
          <strong>无法载入研究概览</strong>
          <span>{errorMessage}</span>
          {onRetry ? (
            <button type="button" onClick={onRetry}>重试载入概览</button>
          ) : null}
        </div>
      </section>
    );
  }

  const openPaper = (paperId: string) => {
    const requestedPaperId = paperId;
    setInspectorOpen(false);
    onOpenPaper(requestedPaperId);
  };

  return (
    <section className="dashboard-route" aria-label="研究概览">
      <header className="dashboard-route__intro">
        <div>
          <p className="dashboard-route__kicker">TODAY / RESEARCH CONTROL</p>
          <h2>从当前论文继续</h2>
          <p>论文选择、复习节点与后台任务均来自当前工作区事实。</p>
        </div>
        {inspectorMode !== 'rail' ? (
          <button
            id="dashboard-inspector-trigger"
            type="button"
            className="dashboard-route__context-trigger"
            aria-expanded={inspectorOpen}
            onClick={() => setInspectorOpen(true)}
          >
            显示论文上下文
          </button>
        ) : null}
      </header>

      {status === 'pending' ? (
        <p className="dashboard-route__refresh" role="status">正在刷新研究事实…</p>
      ) : null}

      <div className="dashboard-route__workspace">
        <PaperDeck
          papers={papers}
          state={deckState}
          onSelect={(paperId) => dispatch({ type: 'paper-selected', paperId })}
          onMove={(delta) => dispatch({ type: 'selection-moved', delta })}
          onOpen={openPaper}
        />

        {showInspector ? (
          <PaperInspector
            paper={selectedPaper}
            review={selectedReview}
            mode={inspectorMode}
            open={inspectorMode === 'rail' || inspectorOpen}
            onClose={() => setInspectorOpen(false)}
            onOpenPaper={openPaper}
          />
        ) : null}
      </div>

      {showTimeline ? (
        <ResearchTimeline papers={papers} reviews={reviews} jobs={jobs} />
      ) : null}
    </section>
  );
}

const desktopQuery = '(min-width: 1100px)';
const mobileQuery = '(max-width: 760px)';

function subscribeToInspectorMode(onStoreChange: () => void): () => void {
  if (typeof window.matchMedia !== 'function') return () => undefined;
  const desktop = window.matchMedia(desktopQuery);
  const mobile = window.matchMedia(mobileQuery);
  desktop.addEventListener('change', onStoreChange);
  mobile.addEventListener('change', onStoreChange);
  return () => {
    desktop.removeEventListener('change', onStoreChange);
    mobile.removeEventListener('change', onStoreChange);
  };
}

function getInspectorMode(): InspectorMode {
  if (typeof window.matchMedia !== 'function') return 'rail';
  if (window.matchMedia(mobileQuery).matches) return 'sheet';
  if (window.matchMedia(desktopQuery).matches) return 'rail';
  return 'drawer';
}

function useInspectorMode(): InspectorMode {
  return useSyncExternalStore(
    subscribeToInspectorMode,
    getInspectorMode,
    () => 'rail',
  );
}

function flattenReviewItems(snapshot: ReviewSnapshot | undefined): ReviewItem[] {
  if (snapshot == null) return [];
  return [
    ...snapshot.overdue,
    ...snapshot.dueToday,
    ...snapshot.upcoming,
    ...snapshot.completed,
  ];
}

function errorMessage(error: unknown): string {
  return error instanceof Error && error.message.trim()
    ? error.message
    : '论文列表暂时不可用。';
}

function jobLabel(job: JobSummary): string {
  const query = job.query?.trim();
  return query ? query : `任务 #${job.id}`;
}

function useDashboardData() {
  const papersQuery = useQuery({
    queryKey: paperKeys.list(),
    queryFn: ({ signal }) => paperApi.listPapers(signal),
  });
  const reviewsQuery = useQuery({
    queryKey: reviewKeys.list(),
    queryFn: ({ signal }) => paperApi.getReviews(signal),
  });
  const jobsQuery = useQuery({
    queryKey: jobKeys.list(),
    queryFn: ({ signal }) => workspaceApi.listJobs(signal),
  });

  const reviews = useMemo<DashboardReviewEvidence[]>(() => (
    flattenReviewItems(reviewsQuery.data).map((review) => ({
      paperId: review.paperId,
      paperTitle: review.title,
      startedAt: review.startedAt,
      dueAt: review.nextDueAt,
      completedAt: review.completedAt,
      currentStep: review.currentStep,
      totalSteps: review.totalSteps,
    }))
  ), [reviewsQuery.data]);

  const jobs = useMemo<DashboardJobEvidence[]>(() => (
    (jobsQuery.data ?? []).map((job) => ({
      id: String(job.id),
      label: jobLabel(job),
      status: job.status,
      createdAt: job.createdAt,
      finishedAt: job.finishedAt,
    }))
  ), [jobsQuery.data]);

  return {
    papers: (papersQuery.data ?? []) as readonly DashboardPaper[],
    reviews,
    jobs,
    status: papersQuery.isPending
      ? 'pending' as const
      : papersQuery.isError
        ? 'error' as const
        : 'success' as const,
    errorMessage: errorMessage(papersQuery.error),
    retry: () => {
      void Promise.all([
        papersQuery.refetch(),
        reviewsQuery.refetch(),
        jobsQuery.refetch(),
      ]);
    },
  };
}

export function DashboardInspectorSlot() {
  const navigate = useNavigate();
  const { papers, reviews, status } = useDashboardData();
  const selectedPaperId = useWorkspaceStore((state) => state.workspaceSelectionId);
  const closePanel = useWorkspaceStore((state) => state.closePanel);
  const paper = selectedPaperId == null
    ? null
    : papers.find((candidate) => candidate.id === selectedPaperId) ?? null;
  const review = paper == null
    ? null
    : reviews.find((candidate) => candidate.paperId === paper.id) ?? null;

  if (status === 'pending') {
    return <div className="paper-inspector__empty" role="status">正在载入论文上下文…</div>;
  }

  return (
    <PaperInspector
      paper={paper}
      review={review}
      mode="rail"
      open
      embedded
      onClose={closePanel}
      onOpenPaper={(paperId) => {
        const requestedPaperId = paperId;
        closePanel();
        void navigate(`/reader/${encodeURIComponent(requestedPaperId)}`);
      }}
    />
  );
}

export function DashboardTimelineSlot() {
  const { papers, reviews, jobs, status } = useDashboardData();

  if (status === 'pending') {
    return <div className="research-timeline__empty" role="status">正在载入真实时间线…</div>;
  }

  return <ResearchTimeline papers={papers} reviews={reviews} jobs={jobs} />;
}

export function Component() {
  const navigate = useNavigate();
  const inspectorMode = useInspectorMode();
  const data = useDashboardData();
  const preferredPaperId = useWorkspaceStore((state) => state.workspaceSelectionId);
  const setWorkspaceSelectionId = useWorkspaceStore(
    (state) => state.setWorkspaceSelectionId,
  );
  const activePanel = useWorkspaceStore((state) => state.panel.active);
  const openPanel = useWorkspaceStore((state) => state.openPanel);
  const closePanel = useWorkspaceStore((state) => state.closePanel);

  return (
    <DashboardView
      papers={data.papers}
      reviews={data.reviews}
      jobs={data.jobs}
      preferredPaperId={preferredPaperId}
      status={data.status}
      errorMessage={data.errorMessage}
      onRetry={data.retry}
      inspectorMode={inspectorMode}
      inspectorOpen={activePanel === 'inspector'}
      onInspectorOpenChange={(open) => {
        if (open) openPanel('inspector', 'dashboard-inspector-trigger');
        else closePanel();
      }}
      showInspector={false}
      showTimeline={false}
      onSelectionChange={setWorkspaceSelectionId}
      onOpenPaper={(paperId) => {
        const requestedPaperId = paperId;
        closePanel();
        void navigate(`/reader/${encodeURIComponent(requestedPaperId)}`);
      }}
    />
  );
}
