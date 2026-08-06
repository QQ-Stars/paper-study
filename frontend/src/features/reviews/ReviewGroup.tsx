import { useId } from 'react';

import type { ReviewItem } from '../../lib/api/types';

export interface ReviewGroupProps {
  readonly title: string;
  readonly description: string;
  readonly tone: 'overdue' | 'today' | 'upcoming' | 'completed';
  readonly items: readonly ReviewItem[];
  readonly actionable?: boolean;
  readonly pendingPaperIds?: ReadonlySet<string>;
  readonly onComplete: (paperId: string) => void;
  readonly onOpen: (paperId: string) => void;
}

function dueLabel(item: ReviewItem): string {
  if (item.completedAt) return `完成于 ${item.completedAt}`;
  return `下次节点 ${item.nextDueAt}`;
}

export function ReviewGroup({
  title,
  description,
  tone,
  items,
  actionable = false,
  pendingPaperIds = new Set<string>(),
  onComplete,
  onOpen,
}: ReviewGroupProps) {
  const titleId = useId();
  const descriptionId = useId();

  return (
    <section
      className="review-group"
      data-tone={tone}
      aria-labelledby={titleId}
      aria-describedby={descriptionId}
    >
      <header className="review-group__header">
        <div>
          <h2 id={titleId}>{title}</h2>
          <p id={descriptionId}>{description}</p>
        </div>
        <strong aria-label={`${items.length} 篇`}>{items.length}</strong>
      </header>

      {items.length === 0 ? (
        <div className="review-group__empty" role="status">此组暂无论文</div>
      ) : (
        <ul className="review-group__list">
          {items.map((item) => {
            const pending = pendingPaperIds.has(item.paperId);
            return (
              <li key={item.paperId} className="review-item">
                <div className="review-item__identity">
                  <span>{[item.venue, item.year].filter(Boolean).join(' · ') || '来源未标注'}</span>
                  <h3>{item.title}</h3>
                  {item.titleZh ? <p lang="zh-CN">{item.titleZh}</p> : null}
                </div>

                <dl className="review-item__facts">
                  <div>
                    <dt>轮次</dt>
                    <dd>第 {item.currentStep} / {item.totalSteps} 轮</dd>
                  </div>
                  <div>
                    <dt>进度</dt>
                    <dd>{item.completedSteps} / {item.totalSteps} 已完成</dd>
                  </div>
                  <div>
                    <dt>节点</dt>
                    <dd>{dueLabel(item)}</dd>
                  </div>
                </dl>

                <div className="review-item__actions">
                  <button type="button" onClick={() => onOpen(item.paperId)}>打开阅读</button>
                  {actionable ? (
                    <button
                      type="button"
                      className="review-item__complete"
                      disabled={pending}
                      onClick={() => onComplete(item.paperId)}
                    >
                      {pending ? '提交中…' : '完成本轮'}
                    </button>
                  ) : null}
                </div>
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}
