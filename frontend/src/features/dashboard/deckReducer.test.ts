import { describe, expect, it } from 'vitest';

import {
  MAX_VISIBLE_CARDS,
  deckReducer,
  move,
  reconcile,
  select,
  type DeckIdentity,
} from './deckReducer';

const paper = (id: string): DeckIdentity => ({ id });
const papers = (...ids: string[]) => ids.map(paper);

describe('Paper Deck reducer', () => {
  it('represents an empty result with no selected item', () => {
    expect(reconcile([], 'p1')).toEqual({
      ids: [],
      visible: [],
      selectedId: null,
      selectedIndex: -1,
      visibleStart: 0,
      total: 0,
      canPrevious: false,
      canNext: false,
    });
  });

  it('selects a valid preferred paper and otherwise selects the first paper', () => {
    expect(reconcile(papers('p1', 'p2', 'p3'), 'p2').selectedId).toBe('p2');
    expect(reconcile(papers('p1', 'p2', 'p3'), 'missing').selectedId).toBe('p1');
    expect(reconcile([paper('p1')], undefined).visible).toEqual(['p1']);
  });

  it('renders at most five adjacent real papers while retaining the exact total', () => {
    const state = reconcile(papers('p1', 'p2', 'p3', 'p4', 'p5', 'p6', 'p7', 'p8'), 'p4');

    expect(MAX_VISIBLE_CARDS).toBe(5);
    expect(state.total).toBe(8);
    expect(state.visible).toEqual(['p2', 'p3', 'p4', 'p5', 'p6']);
    expect(new Set(state.visible).size).toBe(state.visible.length);
    expect(state.visible.every((id) => state.ids.includes(id))).toBe(true);
  });

  it('preserves a selected id after filtering when that paper still exists', () => {
    const selected = reconcile(papers('p1', 'p2', 'p3'), 'p2');
    const filtered = reconcile(papers('p2', 'p3'), selected.selectedId);

    expect(filtered.selectedId).toBe('p2');
    expect(filtered.selectedIndex).toBe(0);
  });

  it('falls back to the first filtered paper when the prior selection disappears', () => {
    const filteredWithoutSelection = papers('p3', 'p4');
    const filtered = reconcile(filteredWithoutSelection, 'p2');

    expect(filtered.selectedId).toBe(filteredWithoutSelection[0]?.id);
    expect(filtered.selectedIndex).toBe(0);
  });

  it('clamps previous and next movement at the ends without wrapping', () => {
    const atStart = reconcile(papers('p1', 'p2', 'p3'));
    const atEnd = reconcile(papers('p1', 'p2', 'p3'), 'p3');

    expect(move(atStart, -1).selectedIndex).toBe(0);
    expect(move(atEnd, 1).selectedIndex).toBe(2);
    expect(move(atStart, 1).selectedId).toBe('p2');
    expect(move(atEnd, -1).selectedId).toBe('p2');
  });

  it('selects only ids in the current result set', () => {
    const initial = reconcile(papers('p1', 'p2'));

    expect(select(initial, 'p2').selectedId).toBe('p2');
    expect(select(initial, 'missing')).toBe(initial);
  });

  it('reconciles new results in the reducer without mutating the prior state', () => {
    const initial = Object.freeze(reconcile(papers('p1', 'p2', 'p3'), 'p2'));
    const preserved = deckReducer(initial, {
      type: 'papers-reconciled',
      papers: papers('p2', 'p3'),
    });
    const replaced = deckReducer(preserved, {
      type: 'papers-reconciled',
      papers: papers('p4', 'p5'),
    });

    expect(preserved.selectedId).toBe('p2');
    expect(replaced.selectedId).toBe('p4');
    expect(initial.selectedId).toBe('p2');
  });
});
