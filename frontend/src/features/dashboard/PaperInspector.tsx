import { useEffect, useRef } from 'react';

import { useResponsivePanelPlacement } from '../../components/overlays/panelPlacement';
import type { DashboardReviewEvidence } from './evidence';
import type { PaperDeckItem } from './PaperDeck';

export interface PaperInspectorPaper extends PaperDeckItem {
  readonly topic?: string | null;
  readonly hasNote?: boolean;
  readonly authors?: readonly string[];
  readonly source?: string | null;
  readonly favorite?: boolean;
  readonly tldr?: string | null;
  readonly contribution?: string | null;
  readonly abstract?: string | null;
}

export type ArtifactAvailability = 'pending' | 'available' | 'empty' | 'error';

export interface PaperArtifactAvailability {
  readonly note: ArtifactAvailability;
  readonly explainer: ArtifactAvailability;
  readonly translation: ArtifactAvailability;
}

export type InspectorMode = 'rail' | 'drawer' | 'sheet';
export type InspectorCloseReason = 'button' | 'escape' | 'scrim' | 'breakpoint';

export interface PaperInspectorProps {
  readonly paper: PaperInspectorPaper | null;
  readonly review?: DashboardReviewEvidence | null;
  readonly researchDirection?: string;
  readonly artifacts?: PaperArtifactAvailability;
  readonly mode: InspectorMode;
  readonly open: boolean;
  readonly embedded?: boolean;
  readonly onClose: (reason: InspectorCloseReason) => void;
  readonly onOpenPaper: (paperId: string) => void;
}

function formatDueAt(value: string): string {
  const parsed = Date.parse(value);
  if (!Number.isFinite(parsed)) return value;
  return new Intl.DateTimeFormat('zh-CN', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  }).format(new Date(parsed));
}

function availabilityLabel(value: ArtifactAvailability): string {
  switch (value) {
    case 'pending':
      return '读取中';
    case 'available':
      return '已有';
    case 'error':
      return '不可用';
    default:
      return '暂无';
  }
}

export function PaperInspector({
  paper,
  review,
  researchDirection,
  artifacts,
  mode,
  open,
  embedded = false,
  onClose,
  onOpenPaper,
}: PaperInspectorProps) {
  const panelPlacement = useResponsivePanelPlacement();
  const previousMode = useRef(mode);
  const modal = mode !== 'rail';
  const visible = !modal || open;
  const hostedByOverlay = embedded
    && (panelPlacement === 'drawer' || panelPlacement === 'sheet');

  useEffect(() => {
    const priorMode = previousMode.current;
    previousMode.current = mode;
    if (open && priorMode !== mode && (priorMode !== 'rail' || mode !== 'rail')) {
      onClose('breakpoint');
    }
  }, [mode, onClose, open]);

  useEffect(() => {
    if (!modal || !open) return;
    const handleKeyDown = (event: globalThis.KeyboardEvent) => {
      if (event.key !== 'Escape') return;
      event.preventDefault();
      onClose('escape');
    };
    document.addEventListener('keydown', handleKeyDown);
    return () => document.removeEventListener('keydown', handleKeyDown);
  }, [modal, onClose, open]);

  if (!visible) return null;

  const matchingReview = review?.paperId === paper?.id ? review : null;
  const summary = paper?.tldr || paper?.contribution || paper?.abstract;

  const content = paper == null ? (
    <div className="paper-inspector__empty" role="status">
      选择一篇论文以查看其真实元数据与复习上下文。
    </div>
  ) : (
    <div className="paper-inspector__content">
      {researchDirection !== undefined ? (
        <div className="paper-inspector__direction">
          <span>当前研究方向</span>
          <strong>{researchDirection}</strong>
        </div>
      ) : null}

      <div className="paper-inspector__identity">
        <span className="paper-inspector__status">{paper.status || '状态未提供'}</span>
        <h3>{paper.title}</h3>
        {paper.titleZh ? <p lang="zh-CN">{paper.titleZh}</p> : null}
        <p>
          {[paper.venue, paper.year, paper.type, paper.topic]
            .filter(Boolean)
            .join(' · ')}
        </p>
        {paper.authors && paper.authors.length > 0 ? (
          <p className="paper-inspector__authors">{paper.authors.join(', ')}</p>
        ) : null}
      </div>

      <dl className="paper-inspector__facts">
        <div>
          <dt>复习</dt>
          <dd>
            {matchingReview?.currentStep != null && matchingReview.totalSteps != null
              ? `第 ${matchingReview.currentStep} / ${matchingReview.totalSteps} 轮`
              : '尚无复习轮次'}
          </dd>
        </div>
        <div>
          <dt>下次节点</dt>
          <dd>{matchingReview?.dueAt ? formatDueAt(matchingReview.dueAt) : '尚未安排'}</dd>
        </div>
        <div>
          <dt>笔记</dt>
          <dd>{paper.hasNote ? '已有笔记' : '暂无笔记'}</dd>
        </div>
        <div>
          <dt>来源</dt>
          <dd>{paper.source || '未提供'}</dd>
        </div>
        <div>
          <dt>收藏</dt>
          <dd>{paper.favorite ? '已收藏' : '未收藏'}</dd>
        </div>
      </dl>

      {artifacts ? (
        <div className="paper-inspector__artifacts" aria-label="论文资料状态">
          <h4>资料状态</h4>
          <ul>
            <li>笔记：{availabilityLabel(artifacts.note)}</li>
            <li>讲解：{availabilityLabel(artifacts.explainer)}</li>
            <li>翻译：{availabilityLabel(artifacts.translation)}</li>
          </ul>
        </div>
      ) : null}

      <div className="paper-inspector__summary">
        <h4>摘要证据</h4>
        <p>{summary || '此论文记录未提供摘要；打开阅读器查看原始内容。'}</p>
      </div>

      <button
        type="button"
        className="paper-inspector__open"
        aria-label={`打开 ${paper.title}`}
        onClick={() => {
          const paperId = paper.id;
          onOpenPaper(paperId);
        }}
      >
        打开阅读
      </button>
    </div>
  );

  if (hostedByOverlay) {
    return (
      <div
        className="paper-inspector paper-inspector--panel-content"
        data-inspector-mode={mode}
      >
        {content}
      </div>
    );
  }

  const inspector = (
    <aside
      className="paper-inspector"
      data-inspector-mode={mode}
      role={embedded ? 'region' : modal ? 'dialog' : 'complementary'}
      aria-modal={!embedded && modal ? true : undefined}
      aria-label="论文上下文"
    >
      <header className="paper-inspector__header">
        <div>
          <p>PAPER CONTEXT</p>
          <h2>论文上下文</h2>
        </div>
        {modal ? (
          <button
            type="button"
            aria-label="关闭论文上下文"
            onClick={() => onClose('button')}
          >
            ×
          </button>
        ) : null}
      </header>

      {content}
    </aside>
  );

  if (!modal) return inspector;

  return (
    <div className="paper-inspector-host" data-inspector-mode={mode}>
      <button
        type="button"
        className="paper-inspector-host__scrim"
        aria-label="关闭论文上下文遮罩"
        onClick={() => onClose('scrim')}
      />
      {inspector}
    </div>
  );
}
