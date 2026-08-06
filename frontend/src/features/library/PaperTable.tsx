import type { KeyboardEvent, MouseEvent } from 'react';

import type { PaperListItem, StudyStatus } from '../../lib/api/types';

const nextStudyStatus: Readonly<Record<StudyStatus, StudyStatus>> = {
  未开始: '学习中',
  学习中: '已理解',
  已理解: '未开始',
};

export interface PaperTableProps {
  readonly papers: readonly PaperListItem[];
  readonly selectedId: string | null;
  readonly batchSelection: ReadonlySet<string>;
  readonly semanticScores?: ReadonlyMap<string, number> | null;
  readonly pendingPaperIds?: ReadonlySet<string>;
  readonly onSelect: (paperId: string) => void;
  readonly onToggleBatch: (paperId: string) => void;
  readonly onToggleFavorite: (paperId: string, favorite: boolean) => void;
  readonly onStatusChange: (paperId: string, status: StudyStatus) => void;
  readonly onOpen: (paperId: string) => void;
}

function stopControlClick(event: MouseEvent<HTMLElement>): void {
  event.stopPropagation();
}

function formatPercent(value: number | null): string {
  return value == null ? '-' : `${Math.round(value * 100)}%`;
}

function formatDate(value: string | null): string {
  return value ? value.slice(0, 10) : '-';
}

export function PaperTable({
  papers,
  selectedId,
  batchSelection,
  semanticScores = null,
  pendingPaperIds = new Set<string>(),
  onSelect,
  onToggleBatch,
  onToggleFavorite,
  onStatusChange,
  onOpen,
}: PaperTableProps) {
  const onRowKeyDown = (
    event: KeyboardEvent<HTMLTableRowElement>,
    paperId: string,
  ) => {
    if (event.key !== 'Enter') return;
    event.preventDefault();
    onOpen(paperId);
  };

  return (
    <div className="paper-table-shell">
      <table className="paper-table" aria-label="文献台账">
        <thead>
          <tr>
            <th scope="col"><span className="sr-only">批量选择</span></th>
            <th scope="col">论文</th>
            <th scope="col">研究事实</th>
            <th scope="col">状态</th>
            <th scope="col">质量</th>
            <th scope="col">来源</th>
            <th scope="col">加入日期</th>
            <th scope="col"><span className="sr-only">收藏</span></th>
          </tr>
        </thead>
        <tbody>
          {papers.map((paper) => {
            const selected = paper.id === selectedId;
            const pending = pendingPaperIds.has(paper.id);
            const semanticScore = semanticScores?.get(paper.id);
            const nextStatus = nextStudyStatus[paper.status];
            return (
              <tr
                key={paper.id}
                className="paper-table__row"
                aria-selected={selected}
                data-paper-id={paper.id}
                tabIndex={selected ? 0 : -1}
                onClick={() => onSelect(paper.id)}
                onDoubleClick={() => onOpen(paper.id)}
                onKeyDown={(event) => onRowKeyDown(event, paper.id)}
              >
                <td className="paper-table__select">
                  <input
                    type="checkbox"
                    aria-label={`选择 ${paper.title}`}
                    checked={batchSelection.has(paper.id)}
                    onClick={stopControlClick}
                    onChange={() => onToggleBatch(paper.id)}
                  />
                </td>
                <td className="paper-table__identity">
                  <strong>{paper.title}</strong>
                  {paper.titleZh ? <span lang="zh-CN">{paper.titleZh}</span> : null}
                  <small>{[paper.venue, paper.year, paper.type, paper.topic].filter(Boolean).join(' · ') || '未标注'}</small>
                </td>
                <td>
                  <div className="paper-table__badges">
                    {paper.hasPdf ? <span>PDF</span> : null}
                    {paper.hasNote ? <span>NOTE</span> : null}
                    {paper.ccf ? <span>{paper.ccf}</span> : null}
                  </div>
                </td>
                <td>
                  <button
                    type="button"
                    className="paper-table__status"
                    aria-label={`${paper.title} 当前学习状态 ${paper.status}，切换到 ${nextStatus}`}
                    disabled={pending}
                    onClick={(event) => {
                      stopControlClick(event);
                      onStatusChange(paper.id, nextStatus);
                    }}
                  >
                    <span>{paper.status}</span>
                    <small aria-hidden="true">→ {nextStatus}</small>
                  </button>
                </td>
                <td className="paper-table__numeric">
                  <span>{semanticScore == null ? formatPercent(paper.relevance) : `语义 ${formatPercent(semanticScore)}`}</span>
                  <small>{paper.citations == null ? '-' : `${paper.citations} 引用`}</small>
                </td>
                <td><span className="paper-table__source">{paper.source || 'unknown'}</span></td>
                <td className="paper-table__date">{formatDate(paper.createdAt)}</td>
                <td>
                  <button
                    type="button"
                    className="paper-table__favorite"
                    aria-label={`${paper.favorite ? '取消收藏' : '收藏'} ${paper.title}`}
                    aria-pressed={paper.favorite}
                    disabled={pending}
                    onClick={(event) => {
                      stopControlClick(event);
                      onToggleFavorite(paper.id, !paper.favorite);
                    }}
                  >
                    {paper.favorite ? '★' : '☆'}
                  </button>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
