import type { PaperListItem, PaperRecord } from '../../lib/api/types';

export interface PaperPreviewProps {
  readonly paper: PaperListItem | null;
  readonly detail?: PaperRecord | null;
  readonly semanticScore?: number | null;
  readonly mutationPending?: boolean;
  readonly onOpen: (paperId: string) => void;
  readonly onEdit?: (paper: PaperListItem) => void;
  readonly onDelete?: (paperId: string) => void;
}

function fact(value: string | number | null | undefined): string {
  return value == null || value === '' ? '-' : String(value);
}

export function PaperPreview({
  paper,
  detail = null,
  semanticScore = null,
  mutationPending = false,
  onOpen,
  onEdit,
  onDelete,
}: PaperPreviewProps) {
  if (paper == null) {
    return (
      <aside className="paper-preview paper-preview--empty" aria-label="论文预览">
        <p className="paper-preview__eyebrow">PAPER CONTEXT</p>
        <strong>选择一篇论文</strong>
        <span>台账中的当前选择会在这里保持为唯一预览。</span>
      </aside>
    );
  }

  return (
    <aside className="paper-preview" aria-label="论文预览">
      <header>
        <p className="paper-preview__eyebrow">PAPER CONTEXT</p>
        <span className="paper-preview__status">{paper.status}</span>
      </header>

      <div className="paper-preview__identity">
        <h2>{paper.title}</h2>
        {paper.titleZh ? <p lang="zh-CN">{paper.titleZh}</p> : null}
        <span>{[paper.venue, paper.year].filter(Boolean).join(' · ') || '未标注来源'}</span>
        {detail?.authors.length ? <small>{detail.authors.join(', ')}</small> : null}
      </div>

      <dl className="paper-preview__facts">
        <div><dt>类型</dt><dd>{fact(paper.type)}</dd></div>
        <div><dt>主题</dt><dd>{fact(paper.topic)}</dd></div>
        <div><dt>来源</dt><dd>{fact(paper.source)}</dd></div>
        <div><dt>相关度</dt><dd>{paper.relevance == null ? '-' : `${Math.round(paper.relevance * 100)}%`}</dd></div>
        <div><dt>引用</dt><dd>{fact(paper.citations)}</dd></div>
        <div><dt>语义分</dt><dd>{semanticScore == null ? '-' : `${Math.round(semanticScore * 100)}%`}</dd></div>
      </dl>

      <section className="paper-preview__summary">
        <h3>研究摘要</h3>
        <p>{paper.tldr || paper.contribution || detail?.abstract || '当前论文还没有可核验的摘要或贡献说明。'}</p>
      </section>

      <div className="paper-preview__actions">
        <button
          type="button"
          className="paper-preview__open"
          onClick={() => onOpen(paper.id)}
        >
          打开阅读
        </button>
        {onEdit ? (
          <button type="button" disabled={mutationPending} onClick={() => onEdit(paper)}>
            编辑论文
          </button>
        ) : null}
        {onDelete ? (
          <button
            type="button"
            className="paper-preview__delete"
            disabled={mutationPending}
            onClick={() => onDelete(paper.id)}
          >
            删除论文
          </button>
        ) : null}
      </div>
    </aside>
  );
}
