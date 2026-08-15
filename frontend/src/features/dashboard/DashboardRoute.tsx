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
import {
  artifactKeys,
  jobKeys,
  paperKeys,
  reviewKeys,
  settingsKeys,
  titleTranslationKeys,
} from '../../lib/api/keys';
import { paperApi } from '../../lib/api/paperApi';
import type {
  JobSummary,
  ReviewItem,
  ReviewSnapshot,
  ReviewState,
} from '../../lib/api/types';
import { artifactGateway } from '../../lib/api/artifactGateway';
import { jobsGateway } from '../../lib/api/jobsGateway';
import { settingsGateway } from '../../lib/api/settingsGateway';
import { useObsidianProjection } from '../../lib/api/useObsidianProjection';
import { DashboardQueue } from './DashboardQueue';
import { selectDashboardPapers } from './dashboardSelection';
import { PaperDeck, type PaperDeckItem } from './PaperDeck';
import {
  PaperInspector,
  type ArtifactAvailability,
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
    DashboardPaperEvidence {
  readonly relevance?: number | null;
}

export interface DashboardReview extends DashboardReviewEvidence {
  readonly reviewState?: ReviewState;
}

export interface DashboardAiEvidence {
  readonly status: 'pending' | 'ready' | 'error';
  readonly titleTranslationPending: number;
  readonly titleTranslationRunning: boolean;
  readonly explainerPending: number;
}

const EMPTY_AI_EVIDENCE: DashboardAiEvidence = {
  status: 'ready',
  titleTranslationPending: 0,
  titleTranslationRunning: false,
  explainerPending: 0,
};

const dashboardAiKeys = {
  explainerPending: () => ['dashboard', 'ai', 'explainer-pending'] as const,
};

export type DashboardLoadStatus = 'pending' | 'success' | 'error';

export interface DashboardViewProps {
  readonly papers: readonly DashboardPaper[];
  readonly reviews: readonly DashboardReview[];
  readonly jobs: readonly DashboardJobEvidence[];
  readonly aiEvidence?: DashboardAiEvidence;
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
  pageHeader: 'route',
  queue: DashboardQueueSlot,
  inspector: DashboardInspectorSlot,
  timeline: DashboardTimelineSlot,
} satisfies WorkspaceRouteHandle;

export function ErrorBoundary() {
  return (
    <>
      <h1 id="workspace-page-title" className="dashboard-route__page-title" tabIndex={-1}>
        研究概览
      </h1>
      <RouteErrorBoundary />
    </>
  );
}

export function DashboardView({
  papers,
  reviews,
  jobs,
  aiEvidence = EMPTY_AI_EVIDENCE,
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
  const paperIds = useMemo(
    () => [...new Set(papers.map((paper) => paper.id))],
    [papers],
  );
  const deckIsReconciled = deckState.ids.length === paperIds.length
    && deckState.ids.every((paperId, index) => paperId === paperIds[index]);

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
    if (status === 'pending' || !deckIsReconciled) return;
    if (lastReportedSelection.current === deckState.selectedId) return;
    lastReportedSelection.current = deckState.selectedId;
    onSelectionChange(deckState.selectedId);
  }, [deckIsReconciled, deckState.selectedId, onSelectionChange, status]);

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
        <h1 id="workspace-page-title" className="dashboard-route__page-title" tabIndex={-1}>
          研究概览
        </h1>
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
        <h1 id="workspace-page-title" className="dashboard-route__page-title" tabIndex={-1}>
          研究概览
        </h1>
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
  const todayPapers = reviews.filter((review) => review.reviewState === 'dueToday').length;
  const learningPapers = papers.filter((paper) => paper.status === '学习中').length;
  const completedPapers = papers.filter((paper) => paper.status === '已理解').length;
  const overdueReviews = reviews.filter((review) => review.reviewState === 'overdue').length;
  const activeAcquisitions = jobs.filter(
    (job) => job.status === 'pending' || job.status === 'running' || job.status === 'review',
  ).length;
  const recentAiTasks = aiEvidence.titleTranslationPending + aiEvidence.explainerPending;
  const aiValue = aiEvidence.status === 'pending'
    ? '…'
    : aiEvidence.status === 'error' ? '—' : String(recentAiTasks);
  const titleTaskLabel = aiEvidence.titleTranslationRunning
    ? `${aiEvidence.titleTranslationPending}（运行中）`
    : String(aiEvidence.titleTranslationPending);

  return (
    <section className="dashboard-route" aria-label="研究概览">
      <header className="dashboard-route__intro">
        <div>
          <h1 id="workspace-page-title" className="dashboard-route__page-title" tabIndex={-1}>
            研究概览
          </h1>
          <p className="dashboard-route__kicker">TODAY / RESEARCH CONTROL</p>
          <h2>今天回到研究现场</h2>
          <p>从当前论文继续，或处理已经到期的复习。</p>
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

      <section className="dashboard-summary" aria-label="今日研究摘要">
        <dl>
          <div>
            <dt>今日论文</dt>
            <dd>{todayPapers}</dd>
            <small>今日复习节点</small>
          </div>
          <div>
            <dt>学习中</dt>
            <dd>{learningPapers}</dd>
            <small>当前阅读状态</small>
          </div>
          <div>
            <dt>已完成论文</dt>
            <dd>{completedPapers}</dd>
            <small>状态为已理解</small>
          </div>
          <div>
            <dt>逾期复习</dt>
            <dd>{overdueReviews}</dd>
            <small>需要优先恢复</small>
          </div>
          <div>
            <dt>活跃采集</dt>
            <dd>{activeAcquisitions}</dd>
            <small>待命 / 运行 / 待确认</small>
          </div>
          <div>
            <dt>最近 AI 任务</dt>
            <dd>{aiValue}</dd>
            <small>
              {aiEvidence.status === 'error'
                ? 'AI 队列状态暂不可用'
                : `题名 ${titleTaskLabel} · 讲解 ${aiEvidence.explainerPending}`}
            </small>
          </div>
        </dl>
      </section>

      {status === 'pending' ? (
        <p className="dashboard-route__refresh" role="status">正在刷新研究事实…</p>
      ) : null}

      <div
        className={`dashboard-route__workspace ${showInspector
          ? 'dashboard-route__workspace--with-inline-inspector'
          : 'dashboard-route__workspace--stage-only'}`}
      >
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

function artifactAvailability(
  value: string | undefined,
  pending: boolean,
  failed: boolean,
): ArtifactAvailability {
  if (pending) return 'pending';
  if (failed) return 'error';
  return value?.trim() ? 'available' : 'empty';
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
    queryFn: ({ signal }) => jobsGateway.listJobs(signal),
  });
  const titleTranslationQuery = useQuery({
    queryKey: titleTranslationKeys.status(),
    queryFn: ({ signal }) => artifactGateway.getTitleTranslationStatus(signal),
  });
  const explainerPendingQuery = useQuery({
    queryKey: dashboardAiKeys.explainerPending(),
    queryFn: ({ signal }) => artifactGateway.getExplainerPending(signal),
  });

  const reviews = useMemo<DashboardReview[]>(() => (
    flattenReviewItems(reviewsQuery.data).map((review) => ({
      paperId: review.paperId,
      paperTitle: review.title,
      startedAt: review.startedAt,
      dueAt: review.nextDueAt,
      completedAt: review.completedAt,
      currentStep: review.currentStep,
      totalSteps: review.totalSteps,
      reviewState: review.reviewState,
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
    aiEvidence: {
      status: titleTranslationQuery.isPending || explainerPendingQuery.isPending
        ? 'pending' as const
        : titleTranslationQuery.isError && explainerPendingQuery.isError
          ? 'error' as const
          : 'ready' as const,
      titleTranslationPending: titleTranslationQuery.data?.pending ?? 0,
      titleTranslationRunning: titleTranslationQuery.data?.running ?? false,
      explainerPending: explainerPendingQuery.data?.pending ?? 0,
    },
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
  const selectedPaperKey = selectedPaperId ?? '';
  const obsidian = useObsidianProjection(selectedPaperId);
  const paperDetailQuery = useQuery({
    queryKey: paperKeys.detail(selectedPaperKey),
    queryFn: ({ signal }) => paperApi.getPaper(selectedPaperKey, signal),
    enabled: selectedPaperId != null,
  });
  const noteQuery = useQuery({
    queryKey: artifactKeys.note(selectedPaperKey),
    queryFn: ({ signal }) => paperApi.getNote(selectedPaperKey, signal),
    enabled: selectedPaperId != null,
  });
  const explainerQuery = useQuery({
    queryKey: artifactKeys.explainer(selectedPaperKey),
    queryFn: ({ signal }) => paperApi.getExplainer(selectedPaperKey, signal),
    enabled: selectedPaperId != null,
  });
  const translationQuery = useQuery({
    queryKey: artifactKeys.translation(selectedPaperKey),
    queryFn: ({ signal }) => paperApi.getTranslation(selectedPaperKey, signal),
    enabled: selectedPaperId != null,
  });
  const settingsQuery = useQuery({
    queryKey: settingsKeys.view(),
    queryFn: ({ signal }) => settingsGateway.getSettings(signal),
  });
  const paper = selectedPaperId == null
    ? null
    : papers.find((candidate) => candidate.id === selectedPaperId) ?? null;
  const detail = paperDetailQuery.data;
  const contextualPaper = paper == null || detail == null
    ? paper
    : {
        ...paper,
        titleZh: paper.titleZh ?? detail.titleZh,
        venue: paper.venue ?? detail.venue,
        year: paper.year ?? detail.year,
        type: paper.type ?? detail.type,
        topic: paper.topic ?? detail.topic,
        tldr: paper.tldr ?? detail.tldr,
        contribution: paper.contribution ?? detail.contribution,
        abstract: detail.abstract,
        authors: detail.authors,
        source: detail.source || paper.source,
      };
  const review = paper == null
    ? null
    : reviews.find((candidate) => candidate.paperId === paper.id) ?? null;
  const researchDirection = settingsQuery.isPending
    ? '正在读取…'
    : settingsQuery.isError
      ? '暂时不可用'
      : settingsQuery.data?.researchTheme.trim() || '尚未设置';

  if (status === 'pending') {
    return <div className="paper-inspector__empty" role="status">正在载入论文上下文…</div>;
  }

  return (
    <PaperInspector
      paper={contextualPaper}
      review={review}
      researchDirection={researchDirection}
      artifacts={{
        note: artifactAvailability(noteQuery.data, noteQuery.isPending, noteQuery.isError),
        explainer: artifactAvailability(
          explainerQuery.data,
          explainerQuery.isPending,
          explainerQuery.isError,
        ),
        translation: artifactAvailability(
          translationQuery.data,
          translationQuery.isPending,
          translationQuery.isError,
        ),
      }}
      mode="rail"
      open
      embedded
      onClose={closePanel}
      onExportObsidian={(paperId) => {
        if (paperId === selectedPaperId) obsidian.exportPaper.mutate({ dryRun: false });
      }}
      obsidianExportPending={obsidian.exportPaper.isPending}
      obsidianExportError={obsidian.exportPaper.isError
        ? errorMessage(obsidian.exportPaper.error)
        : null}
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

export function DashboardQueueSlot() {
  const papersQuery = useQuery({
    queryKey: paperKeys.list(),
    queryFn: ({ signal }) => paperApi.listPapers(signal),
  });
  const filters = useWorkspaceStore((state) => state.filters.dashboard);
  const selectedPaperId = useWorkspaceStore((state) => state.workspaceSelectionId);
  const setSurfaceFilters = useWorkspaceStore((state) => state.setSurfaceFilters);
  const setWorkspaceSelectionId = useWorkspaceStore(
    (state) => state.setWorkspaceSelectionId,
  );
  const closePanel = useWorkspaceStore((state) => state.closePanel);
  const papers = useMemo(
    () => papersQuery.data ?? [],
    [papersQuery.data],
  );
  const filteredPapers = useMemo(
    () => selectDashboardPapers(papers, filters),
    [filters, papers],
  );

  return (
    <DashboardQueue
      papers={papers}
      filteredPapers={filteredPapers}
      filters={filters}
      selectedPaperId={selectedPaperId}
      status={papersQuery.isPending
        ? 'pending'
        : papersQuery.isError ? 'error' : 'success'}
      errorMessage={errorMessage(papersQuery.error)}
      onFiltersChange={(patch) => setSurfaceFilters('dashboard', patch)}
      onSelect={(paperId) => {
        setWorkspaceSelectionId(paperId);
        closePanel();
      }}
    />
  );
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
  const filters = useWorkspaceStore((state) => state.filters.dashboard);
  const filteredPapers = useMemo(
    () => selectDashboardPapers(data.papers, filters),
    [data.papers, filters],
  );

  return (
    <DashboardView
      papers={filteredPapers}
      reviews={data.reviews}
      jobs={data.jobs}
      aiEvidence={data.aiEvidence}
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
