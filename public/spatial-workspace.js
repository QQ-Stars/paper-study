(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  if (root) root.SpatialWorkspace = api;
})(typeof window !== 'undefined' ? window : undefined, function () {
  const MAX_VISIBLE_LAYERS = 5;

  function paperList(value) {
    return Array.isArray(value) ? value.filter(paper => paper && paper.id != null) : [];
  }

  function stateFrom(items, selectedIndex, preservedStart = null) {
    const count = Math.min(MAX_VISIBLE_LAYERS, items.length);
    const centeredStart = selectedIndex < 0
      ? 0
      : Math.min(Math.max(selectedIndex - Math.floor(count / 2), 0), items.length - count);
    const maxStart = Math.max(0, items.length - count);
    const candidateStart = Number.isInteger(preservedStart)
      ? Math.min(Math.max(preservedStart, 0), maxStart)
      : centeredStart;
    const preservesSelected = selectedIndex >= candidateStart && selectedIndex < candidateStart + count;
    const start = preservesSelected ? candidateStart : centeredStart;
    return {
      papers: items,
      selectedId: selectedIndex < 0 ? null : items[selectedIndex].id,
      selectedIndex,
      total: items.length,
      visibleStart: start,
      visiblePapers: items.slice(start, start + count),
      canPrevious: selectedIndex > 0,
      canNext: selectedIndex >= 0 && selectedIndex < items.length - 1,
    };
  }

  function createWorkspaceState(papers, preferredId = null) {
    const items = paperList(papers);
    let selectedIndex = -1;
    if (preferredId != null) {
      selectedIndex = items.findIndex(paper => String(paper.id) === String(preferredId));
    }
    if (selectedIndex < 0) selectedIndex = items.length ? 0 : -1;
    return stateFrom(items, selectedIndex);
  }

  function reconcilePapers(state, papers) {
    return createWorkspaceState(papers, state && state.selectedId);
  }

  function selectPaper(state, paperId) {
    if (!state || paperId == null) return state;
    const nextIndex = state.papers.findIndex(paper => String(paper.id) === String(paperId));
    if (nextIndex < 0 || nextIndex === state.selectedIndex) return state;
    return stateFrom(state.papers, nextIndex, state.visibleStart);
  }

  function moveSelection(state, delta) {
    if (!state || state.selectedIndex < 0) return state;
    const nextIndex = Math.min(
      state.papers.length - 1,
      Math.max(0, state.selectedIndex + Math.sign(Number(delta) || 0)),
    );
    return selectPaper(state, state.papers[nextIndex].id);
  }

  function selectedPaper(state) {
    return state && state.selectedIndex >= 0 ? state.papers[state.selectedIndex] : null;
  }

  return {
    MAX_VISIBLE_LAYERS,
    createWorkspaceState,
    moveSelection,
    reconcilePapers,
    selectPaper,
    selectedPaper,
  };
});
