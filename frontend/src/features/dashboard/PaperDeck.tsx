import { useEffect, useMemo, useRef, type KeyboardEvent } from 'react';

import { useDeckFlip } from '../../lib/motion/useDeckFlip';
import type { DeckIdentity, DeckState } from './deckReducer';

export interface PaperDeckItem extends DeckIdentity {
  readonly title: string;
  readonly titleZh?: string | null;
  readonly venue?: string | null;
  readonly year?: string | null;
  readonly type?: string | null;
  readonly status?: string | null;
}

export interface PaperDeckProps {
  readonly papers: readonly PaperDeckItem[];
  readonly state: DeckState;
  readonly onSelect: (paperId: string) => void;
  readonly onMove: (delta: -1 | 1) => void;
  readonly onOpen: (paperId: string) => void;
}

function optionLabel(paper: PaperDeckItem): string {
  return [paper.title, paper.titleZh, paper.venue, paper.year, paper.status]
    .filter((value): value is string => Boolean(value))
    .join('，');
}

function positionText(state: DeckState): string {
  const width = Math.max(2, String(state.total).length);
  const position = state.selectedIndex < 0 ? 0 : state.selectedIndex + 1;
  return `${String(position).padStart(width, '0')} / ${String(state.total).padStart(width, '0')}`;
}

function layoutOffset(
  visibleIndex: number,
  selectedVisibleIndex: number,
  visibleCount: number,
): number {
  const offset = visibleIndex - selectedVisibleIndex;
  const radius = Math.floor(visibleCount / 2);
  if (offset > radius) return offset - visibleCount;
  if (offset < -radius) return offset + visibleCount;
  return offset;
}

export function PaperDeck({
  papers,
  state,
  onSelect,
  onMove,
  onOpen,
}: PaperDeckProps) {
  const paperById = useMemo(
    () => new Map(papers.map((paper) => [paper.id, paper])),
    [papers],
  );
  const optionRefs = useRef(new Map<string, HTMLDivElement>());
  const focusAfterMove = useRef(false);
  const deckScope = useRef<HTMLElement>(null);
  const layoutKey = `${state.visible.join('|')}::${state.selectedId ?? 'empty'}`;

  useDeckFlip({ scope: deckScope, layoutKey });

  useEffect(() => {
    if (!focusAfterMove.current || state.selectedId == null) return;
    focusAfterMove.current = false;
    optionRefs.current.get(state.selectedId)?.focus();
  }, [state.selectedId]);

  const moveSelection = (delta: -1 | 1, focusCard: boolean) => {
    const canMove = delta < 0 ? state.canPrevious : state.canNext;
    if (!canMove) return;
    focusAfterMove.current = focusCard;
    onMove(delta);
  };

  const handleKeyDown = (
    event: KeyboardEvent<HTMLDivElement>,
    paperId: string,
  ) => {
    if (event.key === 'ArrowLeft') {
      event.preventDefault();
      moveSelection(-1, true);
      return;
    }
    if (event.key === 'ArrowRight') {
      event.preventDefault();
      moveSelection(1, true);
      return;
    }
    if (event.key === 'Enter') {
      event.preventDefault();
      onOpen(paperId);
    }
  };

  const selectedPaper = state.selectedId == null
    ? undefined
    : paperById.get(state.selectedId);
  const selectedVisibleIndex = state.selectedId == null
    ? -1
    : state.visible.indexOf(state.selectedId);

  return (
    <section
      ref={deckScope}
      className="paper-deck"
      aria-labelledby="paper-deck-heading"
    >
      <header className="paper-deck__header">
        <div>
          <p className="paper-deck__eyebrow">RESEARCH QUEUE</p>
          <h2 id="paper-deck-heading">论文甲板</h2>
        </div>
        <div className="paper-deck__count" aria-label={`当前位置 ${positionText(state)}`}>
          <span>{positionText(state)}</span>
          <span>共 {state.total} 篇</span>
        </div>
      </header>

      {state.total === 0 ? (
        <div className="paper-deck__empty" role="status">
          <strong>当前没有论文</strong>
          <span>导入论文或调整筛选后，真实结果会出现在这里。</span>
        </div>
      ) : (
        <div
          className="paper-deck__stage"
          role="listbox"
          aria-label="论文甲板"
          aria-activedescendant={state.selectedId == null ? undefined : `deck-paper-${state.selectedId}`}
        >
          {state.visible.map((paperId, visibleIndex) => {
            const paper = paperById.get(paperId);
            if (paper == null) return null;
            const selected = paperId === state.selectedId;
            const sourceIndex = state.ids.indexOf(paperId);
            const offset = sourceIndex - state.selectedIndex;
            const cardLayoutOffset = layoutOffset(
              visibleIndex,
              selectedVisibleIndex,
              state.visible.length,
            );

            return (
              <div
                key={paperId}
                ref={(node) => {
                  if (node == null) optionRefs.current.delete(paperId);
                  else optionRefs.current.set(paperId, node);
                }}
                id={`deck-paper-${paperId}`}
                className="paper-deck__card"
                data-deck-card=""
                data-offset={offset}
                data-layout-offset={cardLayoutOffset}
                data-paper-id={paperId}
                role="option"
                aria-label={optionLabel(paper)}
                aria-selected={selected}
                aria-posinset={sourceIndex + 1}
                aria-setsize={state.total}
                tabIndex={selected ? 0 : -1}
                onMouseDown={(event) => event.preventDefault()}
                onClick={() => onSelect(paperId)}
                onDoubleClick={() => onOpen(paperId)}
                onKeyDown={(event) => handleKeyDown(event, paperId)}
              >
                <span className="paper-deck__card-index">
                  {String(sourceIndex + 1).padStart(2, '0')}
                </span>
                <span className="paper-deck__card-copy">
                  <strong>{paper.title}</strong>
                  {paper.titleZh ? <span lang="zh-CN">{paper.titleZh}</span> : null}
                </span>
                <span className="paper-deck__card-meta">
                  {[paper.venue, paper.year, paper.type].filter(Boolean).join(' · ')}
                </span>
                {paper.status ? (
                  <span className="paper-deck__card-status">{paper.status}</span>
                ) : null}
              </div>
            );
          })}
        </div>
      )}

      <footer className="paper-deck__actions">
        <button
          type="button"
          aria-label="上一篇论文"
          disabled={!state.canPrevious}
          onClick={() => moveSelection(-1, false)}
        >
          ←
        </button>
        <button
          type="button"
          className="paper-deck__open"
          disabled={selectedPaper == null}
          onClick={() => {
            if (selectedPaper != null) onOpen(selectedPaper.id);
          }}
        >
          打开阅读
        </button>
        <button
          type="button"
          aria-label="下一篇论文"
          disabled={!state.canNext}
          onClick={() => moveSelection(1, false)}
        >
          →
        </button>
      </footer>
    </section>
  );
}
