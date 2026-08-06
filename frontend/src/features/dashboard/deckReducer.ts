export const MAX_VISIBLE_CARDS = 5;

export interface DeckIdentity {
  readonly id: string;
}

export interface DeckState {
  readonly ids: readonly string[];
  readonly visible: readonly string[];
  readonly selectedId: string | null;
  readonly selectedIndex: number;
  readonly visibleStart: number;
  readonly total: number;
  readonly canPrevious: boolean;
  readonly canNext: boolean;
}

export type DeckAction =
  | {
      readonly type: 'papers-reconciled';
      readonly papers: readonly DeckIdentity[];
      readonly preferredId?: string | null;
    }
  | { readonly type: 'paper-selected'; readonly paperId: string }
  | { readonly type: 'selection-moved'; readonly delta: number };

function uniqueIds(papers: readonly DeckIdentity[]): string[] {
  const seen = new Set<string>();
  const ids: string[] = [];

  for (const paper of papers) {
    if (seen.has(paper.id)) continue;
    seen.add(paper.id);
    ids.push(paper.id);
  }

  return ids;
}

function stateFrom(ids: readonly string[], selectedIndex: number): DeckState {
  if (ids.length === 0) {
    return {
      ids: [],
      visible: [],
      selectedId: null,
      selectedIndex: -1,
      visibleStart: 0,
      total: 0,
      canPrevious: false,
      canNext: false,
    };
  }

  const safeIndex = Math.min(Math.max(selectedIndex, 0), ids.length - 1);
  const visibleCount = Math.min(MAX_VISIBLE_CARDS, ids.length);
  const centeredStart = safeIndex - Math.floor(visibleCount / 2);
  const visibleStart = Math.min(
    Math.max(centeredStart, 0),
    ids.length - visibleCount,
  );

  return {
    ids,
    visible: ids.slice(visibleStart, visibleStart + visibleCount),
    selectedId: ids[safeIndex] ?? null,
    selectedIndex: safeIndex,
    visibleStart,
    total: ids.length,
    canPrevious: safeIndex > 0,
    canNext: safeIndex < ids.length - 1,
  };
}

export function reconcile(
  papers: readonly DeckIdentity[],
  preferredId?: string | null,
): DeckState {
  const ids = uniqueIds(papers);
  const preferredIndex = preferredId == null ? -1 : ids.indexOf(preferredId);
  return stateFrom(ids, preferredIndex >= 0 ? preferredIndex : 0);
}

export function select(state: DeckState, paperId: string): DeckState {
  const selectedIndex = state.ids.indexOf(paperId);
  if (selectedIndex < 0 || selectedIndex === state.selectedIndex) return state;
  return stateFrom(state.ids, selectedIndex);
}

export function move(state: DeckState, delta: number): DeckState {
  if (state.selectedIndex < 0 || !Number.isFinite(delta) || delta === 0) return state;
  const direction = delta < 0 ? -1 : 1;
  const nextIndex = Math.min(
    Math.max(state.selectedIndex + direction, 0),
    state.ids.length - 1,
  );
  if (nextIndex === state.selectedIndex) return state;
  return stateFrom(state.ids, nextIndex);
}

export function deckReducer(state: DeckState, action: DeckAction): DeckState {
  switch (action.type) {
    case 'papers-reconciled': {
      const preservedId = state.selectedId != null && action.papers.some(
        (paper) => paper.id === state.selectedId,
      )
        ? state.selectedId
        : action.preferredId;
      return reconcile(action.papers, preservedId);
    }
    case 'paper-selected':
      return select(state, action.paperId);
    case 'selection-moved':
      return move(state, action.delta);
  }
}
