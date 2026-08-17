import type { KeyboardEvent, MouseEvent } from 'react';

import { Button, Table } from '@cloudflare/kumo';

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
      <Table className="paper-table" layout="fixed" aria-label="文献台账">
        <colgroup>
          <col className="paper-table__col-select" />
          <col className="paper-table__col-identity" />
          <col className="paper-table__col-facts" />
          <col className="paper-table__col-status" />
          <col className="paper-table__col-quality" />
          <col className="paper-table__col-source" />
          <col className="paper-table__col-date" />
          <col className="paper-table__col-favorite" />
        </colgroup>
        <Table.Header>
          <Table.Row>
            <Table.Head><span className="sr-only">批量选择</span></Table.Head>
            <Table.Head>论文</Table.Head>
            <Table.Head>研究事实</Table.Head>
            <Table.Head>状态</Table.Head>
            <Table.Head>质量</Table.Head>
            <Table.Head>来源</Table.Head>
            <Table.Head>加入日期</Table.Head>
            <Table.Head><span className="sr-only">收藏</span></Table.Head>
          </Table.Row>
        </Table.Header>
        <Table.Body>
          {papers.map((paper) => {
            const selected = paper.id === selectedId;
            const pending = pendingPaperIds.has(paper.id);
            const semanticScore = semanticScores?.get(paper.id);
            const nextStatus = nextStudyStatus[paper.status];
            return (
              <Table.Row
                key={paper.id}
                className="paper-table__row"
                aria-selected={selected}
                data-paper-id={paper.id}
                tabIndex={selected ? 0 : -1}
                onClick={() => onSelect(paper.id)}
                onDoubleClick={() => onOpen(paper.id)}
                onKeyDown={(event) => onRowKeyDown(event, paper.id)}
              >
                <Table.CheckCell
                  className="paper-table__select"
                  label={`选择 ${paper.title}`}
                  checked={batchSelection.has(paper.id)}
                  onClick={stopControlClick}
                  onValueChange={() => onToggleBatch(paper.id)}
                />
                <Table.Cell className="paper-table__identity">
                  <strong>{paper.title}</strong>
                  {paper.titleZh ? <span lang="zh-CN">{paper.titleZh}</span> : null}
                  <small>{[paper.venue, paper.year, paper.type, paper.topic].filter(Boolean).join(' · ') || '未标注'}</small>
                </Table.Cell>
                <Table.Cell className="paper-table__facts">
                  <span aria-hidden="true" className="paper-table__field-label">研究事实</span>
                  <div className="paper-table__badges">
                    {paper.hasPdf ? <span>PDF</span> : null}
                    {paper.hasNote ? <span>NOTE</span> : null}
                    {paper.ccf ? <span>{paper.ccf}</span> : null}
                  </div>
                </Table.Cell>
                <Table.Cell className="paper-table__status-cell">
                  <span aria-hidden="true" className="paper-table__field-label">状态</span>
                  <Button
                    type="button"
                    variant="outline"
                    size="sm"
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
                  </Button>
                </Table.Cell>
                <Table.Cell className="paper-table__numeric">
                  <span aria-hidden="true" className="paper-table__field-label">质量</span>
                  <span>{semanticScore == null ? formatPercent(paper.relevance) : `语义 ${formatPercent(semanticScore)}`}</span>
                  <small>{paper.citations == null ? '-' : `${paper.citations} 引用`}</small>
                </Table.Cell>
                <Table.Cell className="paper-table__source-cell">
                  <span aria-hidden="true" className="paper-table__field-label">来源</span>
                  <span className="paper-table__source">{paper.source || 'unknown'}</span>
                </Table.Cell>
                <Table.Cell className="paper-table__date">
                  <span aria-hidden="true" className="paper-table__field-label">加入日期</span>
                  {formatDate(paper.createdAt)}
                </Table.Cell>
                <Table.Cell>
                  <Button
                    type="button"
                    variant="ghost"
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
                  </Button>
                </Table.Cell>
              </Table.Row>
            );
          })}
        </Table.Body>
      </Table>
    </div>
  );
}
