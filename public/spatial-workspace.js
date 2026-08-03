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

  function createWorkspaceController({
    root,
    document,
    scrollContainer,
    onOpen,
    onClearFilters,
    getDetails,
    desktopMedia,
    mobileMedia,
  } = {}) {
    const media = desktopMedia || (
      typeof window !== 'undefined' && typeof window.matchMedia === 'function'
        ? window.matchMedia('(min-width: 1101px)')
        : null
    );
    const mobile = mobileMedia || (
      typeof window !== 'undefined' && typeof window.matchMedia === 'function'
        ? window.matchMedia('(max-width: 760px)')
        : null
    );
    let state = createWorkspaceState([]);
    let initialized = false;
    let bound = false;
    let emptyMessage = '';
    let lastPreferredKey = null;
    let pendingPreferredId = null;

    const byId = id => document.getElementById(id);
    const elements = {
      layers: byId('spatialLayers'),
      count: byId('spatialCount'),
      position: byId('spatialPosition'),
      queueTotal: byId('spatialQueueTotal'),
      queueLearning: byId('spatialQueueLearning'),
      queueDone: byId('spatialQueueDone'),
      directions: byId('spatialDirections'),
      context: byId('spatialContext'),
      empty: byId('spatialEmpty'),
      emptyText: byId('spatialEmptyText'),
      clear: byId('spatialClearFilters'),
      inspector: byId('spatialInspector'),
      title: byId('spatialInspectorTitle'),
      titleZh: byId('spatialInspectorTitleZh'),
      meta: byId('spatialInspectorMeta'),
      status: byId('spatialInspectorStatus'),
      review: byId('spatialInspectorReview'),
      note: byId('spatialInspectorNote'),
      summary: byId('spatialInspectorSummary'),
      previous: byId('spatialPrev'),
      next: byId('spatialNext'),
      open: byId('spatialOpen'),
      queue: byId('spatialQueue'),
      queueToggle: byId('spatialQueueToggle'),
      queueClose: byId('spatialQueueClose'),
      inspectorClose: byId('spatialInspectorClose'),
      scrim: byId('spatialScrim'),
      filterActions: byId('homeFilterActions'),
      filterHome: byId('topFilters'),
      filterSlot: byId('spatialFilterSlot'),
      inspectorToggle: byId('spatialInspectorToggle'),
    };

    let panelTrigger = null;
    let savedScrollTop = 0;
    let activePanel = null;

    function resetPanelPresentation() {
      root.classList.remove('is-queue-open', 'is-inspector-open');
      document.documentElement.classList.remove('spatial-queue-open', 'spatial-inspector-open');
      elements.queueToggle.setAttribute('aria-expanded', 'false');
      elements.inspectorToggle.setAttribute('aria-expanded', 'false');
      elements.scrim.hidden = true;
      elements.filterHome.append(elements.filterActions);
    }

    function closePanels({ restoreFocus = true } = {}) {
      const wasActive = Boolean(activePanel);
      const trigger = panelTrigger;
      const scrollTop = savedScrollTop;
      resetPanelPresentation();
      activePanel = null;
      panelTrigger = null;
      if (!wasActive) return;
      if (scrollContainer) scrollContainer.scrollTop = scrollTop;
      if (restoreFocus && trigger && typeof trigger.focus === 'function') trigger.focus();
    }

    function closePanelsForBreakpoint() {
      const activeElement = document.activeElement;
      const panel = activePanel === 'inspector'
        ? elements.inspector
        : activePanel === 'queue' ? elements.queue : null;
      const shouldMoveFocus = Boolean(activeElement && (
        activeElement === panelTrigger
        || (panel && typeof panel.contains === 'function' && panel.contains(activeElement))
      ));
      closePanels({ restoreFocus: false });
      if (!shouldMoveFocus) return;
      const selectedLayer = Array.from(elements.layers.children || [])
        .find(layer => layer.getAttribute('aria-selected') === 'true');
      const target = selectedLayer || (!elements.clear.disabled ? elements.clear : null);
      if (target && typeof target.focus === 'function') target.focus();
    }

    function openPanel(name) {
      const nextPanel = name === 'inspector' ? 'inspector' : 'queue';
      const inspector = nextPanel === 'inspector';
      if (inspector && (!selectedPaper(state) || elements.inspectorToggle.disabled)) {
        closePanels({ restoreFocus: false });
        return;
      }
      if (!activePanel) savedScrollTop = Number(scrollContainer && scrollContainer.scrollTop) || 0;
      resetPanelPresentation();
      activePanel = nextPanel;
      panelTrigger = inspector ? elements.inspectorToggle : elements.queueToggle;
      if (!inspector) elements.filterSlot.append(elements.filterActions);
      root.classList.add(inspector ? 'is-inspector-open' : 'is-queue-open');
      document.documentElement.classList.add(inspector ? 'spatial-inspector-open' : 'spatial-queue-open');
      panelTrigger.setAttribute('aria-expanded', 'true');
      elements.scrim.hidden = false;
      const panel = inspector ? elements.inspector : elements.queue;
      const firstFocusable = panel.querySelector('button, a, input, select, textarea, [tabindex="0"]');
      if (firstFocusable) firstFocusable.focus();
    }

    function createTextElement(tag, className, text) {
      const element = document.createElement(tag);
      element.className = className;
      element.textContent = text == null ? '' : String(text);
      return element;
    }

    function layerNode(paper) {
      const sourceIndex = state.papers.findIndex(item => String(item.id) === String(paper.id));
      const offset = Math.max(-4, Math.min(4, sourceIndex - state.selectedIndex));
      const button = document.createElement('button');
      button.type = 'button';
      button.className = `spatial-layer layer-offset-${offset}`;
      button.dataset.spatialPaperId = paper.id;
      button.setAttribute('role', 'option');
      button.setAttribute('aria-selected', String(String(paper.id) === String(state.selectedId)));
      button.tabIndex = String(paper.id) === String(state.selectedId) ? 0 : -1;
      button.append(
        createTextElement('span', 'spatial-layer-index', String(sourceIndex + 1).padStart(2, '0')),
        createTextElement('strong', 'spatial-layer-title', paper.title || paper.id),
        createTextElement('span', 'spatial-layer-meta', [paper.venue, paper.year, paper.type].filter(Boolean).join(' · ')),
        createTextElement('span', 'spatial-layer-status', paper.status || '未开始'),
      );
      return button;
    }

    function detailsFor(paper) {
      if (typeof getDetails !== 'function') {
        return { reviewText: '复习数据载入中…', noteText: paper.hasNote ? '已有笔记' : '暂无笔记' };
      }
      try {
        return getDetails(paper) || { reviewText: '尚未安排', noteText: '暂无笔记' };
      } catch (error) {
        return { reviewText: '复习信息暂不可用', noteText: paper.hasNote ? '已有笔记' : '暂无笔记' };
      }
    }

    function renderDirections() {
      const counts = new Map();
      for (const paper of state.papers) {
        const direction = String(paper.type || '').split('·')[0].trim() || '其他';
        counts.set(direction, (counts.get(direction) || 0) + 1);
      }
      const nodes = [...counts.entries()]
        .sort((left, right) => right[1] - left[1] || left[0].localeCompare(right[0]))
        .slice(0, 5)
        .map(([name, count]) => createTextElement('li', 'spatial-direction', `${name} ${count}`));
      elements.directions.replaceChildren(...nodes);
    }

    function renderInspector() {
      const paper = selectedPaper(state);
      elements.inspector.hidden = !paper;
      elements.inspectorToggle.disabled = !paper;
      if (!paper && activePanel === 'inspector') {
        closePanels({ restoreFocus: false });
        elements.clear.focus();
      }
      elements.open.disabled = !paper;
      if (!paper) {
        for (const element of [
          elements.title, elements.titleZh, elements.meta, elements.status,
          elements.review, elements.note, elements.summary,
        ]) element.textContent = '';
        return;
      }
      const details = detailsFor(paper);
      elements.title.textContent = paper.title || paper.id;
      elements.titleZh.textContent = paper.title_zh || '';
      elements.titleZh.hidden = !paper.title_zh;
      elements.meta.textContent = [paper.venue, paper.year, paper.type, paper.topic].filter(Boolean).join(' · ');
      elements.status.textContent = paper.status || '未开始';
      elements.review.textContent = details.reviewText || '尚未安排';
      elements.note.textContent = details.noteText || (paper.hasNote ? '已有笔记' : '暂无笔记');
      elements.summary.textContent = paper.tldr || paper.contribution || paper.abstract || '打开阅读查看论文、讲解与笔记。';
    }

    function sameLayerWindow() {
      const renderedIds = Array.from(elements.layers.children)
        .map(node => String(node.dataset.spatialPaperId));
      const desiredIds = state.visiblePapers.map(paper => String(paper.id));
      return renderedIds.length === desiredIds.length
        && renderedIds.every((id, index) => id === desiredIds[index]);
    }

    function syncLayerPresentation() {
      for (const node of Array.from(elements.layers.children)) {
        const sourceIndex = state.papers.findIndex(
          paper => String(paper.id) === String(node.dataset.spatialPaperId),
        );
        const offset = Math.max(-4, Math.min(4, sourceIndex - state.selectedIndex));
        const selected = String(node.dataset.spatialPaperId) === String(state.selectedId);
        node.className = `spatial-layer layer-offset-${offset}`;
        node.setAttribute('aria-selected', String(selected));
        node.tabIndex = selected ? 0 : -1;
      }
    }

    function render({ focusLayer = false, preserveLayerNodes = false } = {}) {
      const done = state.papers.filter(paper => paper.status === '已理解').length;
      const learning = state.papers.filter(paper => paper.status === '学习中').length;
      const position = state.selectedIndex < 0 ? 0 : state.selectedIndex + 1;
      if (!preserveLayerNodes || !sameLayerWindow()) {
        elements.layers.replaceChildren(...state.visiblePapers.map(layerNode));
      }
      else syncLayerPresentation();
      elements.count.textContent = `共 ${state.total} 篇`;
      elements.position.textContent = `${position} / ${state.total}`;
      elements.queueTotal.textContent = String(state.total);
      elements.queueLearning.textContent = String(learning);
      elements.queueDone.textContent = String(done);
      elements.context.textContent = `已理解 ${done} · 学习中 ${learning} · 当前 ${position} / ${state.total}`;
      elements.empty.hidden = state.total > 0;
      elements.emptyText.textContent = state.total > 0 ? '' : emptyMessage;
      elements.previous.disabled = !state.canPrevious;
      elements.next.disabled = !state.canNext;
      renderDirections();
      renderInspector();
      if (focusLayer) {
        const selected = Array.from(elements.layers.children).find(node => node.getAttribute('aria-selected') === 'true');
        if (selected && typeof selected.focus === 'function') selected.focus();
      }
    }

    function getState() {
      return {
        ...state,
        papers: state.papers.slice(),
        visiblePapers: state.visiblePapers.slice(),
      };
    }

    function preferredKey(value) {
      return value == null ? null : String(value);
    }

    function hasPaper(papers, paperId) {
      return paperId != null
        && papers.some(paper => String(paper.id) === String(paperId));
    }

    function update(papers, options = {}) {
      emptyMessage = String(options.emptyMessage || '没有匹配的论文。');
      const nextPreferredKey = preferredKey(options.preferredId);
      if (!initialized) {
        state = createWorkspaceState(papers, options.preferredId);
        initialized = true;
        lastPreferredKey = nextPreferredKey;
        pendingPreferredId = hasPaper(state.papers, options.preferredId)
          ? null
          : options.preferredId;
      } else {
        state = reconcilePapers(state, papers);
        if (nextPreferredKey !== lastPreferredKey) {
          lastPreferredKey = nextPreferredKey;
          pendingPreferredId = options.preferredId;
        }
        if (hasPaper(state.papers, pendingPreferredId)) {
          state = selectPaper(state, pendingPreferredId);
          pendingPreferredId = null;
        }
      }
      render();
      return getState();
    }

    function select(paperId, options = {}) {
      const next = selectPaper(state, paperId);
      if (next === state) return getState();
      state = next;
      render({
        focusLayer: Boolean(options.focus),
        preserveLayerNodes: Boolean(options.preserveLayerNodes),
      });
      return getState();
    }

    function move(delta) {
      const next = moveSelection(state, delta);
      if (next === state || next.selectedId === state.selectedId) return getState();
      state = next;
      render({ focusLayer: true });
      return getState();
    }

    function openSelected() {
      const paper = selectedPaper(state);
      if (paper && typeof onOpen === 'function') onOpen(paper);
    }

    function paperButton(event) {
      const button = event && event.target && typeof event.target.closest === 'function'
        ? event.target.closest('[data-spatial-paper-id]')
        : null;
      return button && root.contains(button) ? button : null;
    }

    function bind() {
      if (bound) return;
      bound = true;
      root.addEventListener('click', event => {
        const button = paperButton(event);
        if (button) select(button.dataset.spatialPaperId, { focus: true, preserveLayerNodes: true });
      });
      root.addEventListener('dblclick', event => {
        const button = paperButton(event);
        if (!button) return;
        select(button.dataset.spatialPaperId, { preserveLayerNodes: true });
        openSelected();
      });
      root.addEventListener('keydown', event => {
        const button = paperButton(event);
        if (!button) return;
        if (event.key === 'Enter') {
          event.preventDefault();
          select(button.dataset.spatialPaperId, { preserveLayerNodes: true });
          openSelected();
        } else if (event.key === 'ArrowLeft') {
          event.preventDefault();
          select(button.dataset.spatialPaperId);
          move(-1);
        } else if (event.key === 'ArrowRight') {
          event.preventDefault();
          select(button.dataset.spatialPaperId);
          move(1);
        }
      });
      elements.previous.addEventListener('click', () => move(-1));
      elements.next.addEventListener('click', () => move(1));
      elements.open.addEventListener('click', openSelected);
      elements.clear.addEventListener('click', () => {
        if (typeof onClearFilters === 'function') onClearFilters();
      });
      elements.queueToggle.addEventListener('click', () => openPanel('queue'));
      elements.inspectorToggle.addEventListener('click', () => openPanel('inspector'));
      elements.queueClose.addEventListener('click', () => closePanels());
      elements.inspectorClose.addEventListener('click', () => closePanels());
      elements.scrim.addEventListener('click', () => closePanels());
      document.addEventListener('keydown', event => {
        if (event.key !== 'Escape' || !activePanel) return;
        if (typeof event.preventDefault === 'function') event.preventDefault();
        closePanels();
      });
      if (media) {
        const onDesktopChange = event => {
          if (event.matches) closePanelsForBreakpoint();
        };
        if (typeof media.addEventListener === 'function') media.addEventListener('change', onDesktopChange);
        else if (typeof media.addListener === 'function') media.addListener(onDesktopChange);
      }
      if (mobile) {
        const onMobileChange = event => {
          if (!event.matches && activePanel === 'queue') closePanelsForBreakpoint();
        };
        if (typeof mobile.addEventListener === 'function') mobile.addEventListener('change', onMobileChange);
        else if (typeof mobile.addListener === 'function') mobile.addListener(onMobileChange);
      }
    }

    function refreshDetails() { renderInspector(); }

    return {
      bind,
      closePanels,
      getState,
      move,
      openPanel,
      openSelected,
      refreshDetails,
      select,
      update,
    };
  }

  return {
    MAX_VISIBLE_LAYERS,
    createWorkspaceController,
    createWorkspaceState,
    moveSelection,
    reconcilePapers,
    selectPaper,
    selectedPaper,
  };
});
