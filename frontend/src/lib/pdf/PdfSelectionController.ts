import type { PageRectAnchor, PdfGeometryRect } from './PageViewportAnchor';
import {
  applyPdfSelectionPolicy,
  type PdfSelectionFragment,
  type PdfSelectionPolicyInput,
} from './selectionPolicy';

export interface PdfBufferedSelection {
  text: string;
  pageNumber: number;
  anchor: PageRectAnchor | null;
}

export interface PdfSelectionControllerError {
  reason: 'too-long';
  length: number;
  maxCharacters: number;
}

export interface PdfSelectionControllerSnapshot {
  status: 'idle' | 'selected' | 'error';
  paperId: string | null;
  generation: number;
  fragments: readonly PdfBufferedSelection[];
  text: string;
  anchor: PageRectAnchor | null;
  popoverRect: PdfGeometryRect | null;
  popoverOpen: boolean;
  error: PdfSelectionControllerError | null;
}

export interface PdfSelectionControllerDependencies {
  getSelection?: () => Selection | null;
  resolveSelection?: (
    selection: Selection,
    root: HTMLElement,
  ) => PdfSelectionPolicyInput | null;
  delayMs?: number;
  maxCharacters?: number;
}

function initialSnapshot(): PdfSelectionControllerSnapshot {
  return {
    status: 'idle',
    paperId: null,
    generation: 0,
    fragments: [],
    text: '',
    anchor: null,
    popoverRect: null,
    popoverOpen: false,
    error: null,
  };
}

function elementForNode(node: Node | null): Element | null {
  if (!node) return null;
  return node.nodeType === 1 ? node as Element : node.parentElement;
}

function geometryRect(rect: Pick<DOMRect, 'left' | 'top' | 'right' | 'bottom'>): PdfGeometryRect {
  return {
    left: rect.left,
    top: rect.top,
    right: rect.right,
    bottom: rect.bottom,
  };
}

function boundaryTextOffset(
  span: HTMLElement,
  container: Node,
  offset: number,
): number | null {
  if (!span.contains(container)) return null;
  const probe = span.ownerDocument.createRange();
  probe.selectNodeContents(span);
  try {
    probe.setEnd(container, offset);
    return probe.toString().length;
  } catch {
    return null;
  }
}

function clippedSpanText(span: HTMLElement, range: Range): string {
  const fullText = span.textContent ?? '';
  const startOffset = boundaryTextOffset(
    span,
    range.startContainer,
    range.startOffset,
  );
  const endOffset = boundaryTextOffset(
    span,
    range.endContainer,
    range.endOffset,
  );
  return fullText.slice(startOffset ?? 0, endOffset ?? fullText.length);
}

function inferredBreak(
  previous: PdfSelectionFragment | undefined,
  currentRect: PdfGeometryRect,
  fontSize: number | undefined,
): PdfSelectionFragment['breakBefore'] {
  if (!previous) return 'none';
  const previousHeight = previous.rect.bottom - previous.rect.top;
  const baseline = Math.max(1, fontSize ?? previous.fontSize ?? previousHeight);
  const verticalDelta = currentRect.top - previous.rect.top;
  if (verticalDelta > baseline * 1.7) return 'paragraph';
  if (verticalDelta > baseline * 0.45) return 'line';
  return 'space';
}

function defaultResolveSelection(
  selection: Selection,
  root: HTMLElement,
): PdfSelectionPolicyInput | null {
  if (selection.isCollapsed || selection.rangeCount < 1) return null;
  const anchorElement = elementForNode(selection.anchorNode);
  const focusElement = elementForNode(selection.focusNode);
  if (
    !anchorElement ||
    !focusElement ||
    !root.contains(anchorElement) ||
    !root.contains(focusElement)
  ) {
    return null;
  }
  const page = anchorElement.closest<HTMLElement>('[data-pdf-page-number]');
  if (!page || !root.contains(page)) return null;
  const pageNumber = Number(page.dataset.pdfPageNumber);
  if (!Number.isInteger(pageNumber) || pageNumber < 1) return null;

  const range = selection.getRangeAt(0);
  const pageRect = geometryRect(page.getBoundingClientRect());
  const spans = [...page.querySelectorAll<HTMLElement>('[data-pdf-text], .textLayer span')];
  const selectedSpans = spans.filter((span) => {
    try {
      return range.intersectsNode(span);
    } catch {
      return false;
    }
  });
  const view = page.ownerDocument.defaultView;
  const fragments: PdfSelectionFragment[] = [];
  const anchorSpan = anchorElement.closest<HTMLElement>('[data-pdf-text], .textLayer span');
  const focusSpan = focusElement.closest<HTMLElement>('[data-pdf-text], .textLayer span');
  let startFragmentIndex = -1;
  let endFragmentIndex = -1;
  for (const span of selectedSpans) {
    const text = clippedSpanText(span, range);
    if (!text.trim()) continue;
    const rect = geometryRect(span.getBoundingClientRect());
    const parsedFontSize = Number.parseFloat(view?.getComputedStyle(span).fontSize ?? '');
    const fontSize = Number.isFinite(parsedFontSize) && parsedFontSize > 0
      ? parsedFontSize
      : undefined;
    const previous = fragments.at(-1);
    const fragmentIndex = fragments.length;
    if (span === anchorSpan) startFragmentIndex = fragmentIndex;
    if (span === focusSpan) endFragmentIndex = fragmentIndex;
    fragments.push({
      text,
      rect,
      ...(fontSize === undefined ? {} : { fontSize }),
      breakBefore: inferredBreak(previous, rect, fontSize),
    });
  }
  return {
    pageNumber,
    pageRect,
    fragments,
    startFragmentIndex: Math.max(0, startFragmentIndex),
    ...(endFragmentIndex < 0 ? {} : { endFragmentIndex }),
    nativeText: selection.toString(),
  };
}

function selectionRect(selection: Selection): PdfGeometryRect | null {
  if (selection.rangeCount < 1) return null;
  const range = selection.getRangeAt(0);
  if (typeof range.getBoundingClientRect !== 'function') return null;
  const rect = range.getBoundingClientRect();
  if (![rect.left, rect.top, rect.right, rect.bottom].every(Number.isFinite)) {
    return null;
  }
  return geometryRect(rect);
}

export class PdfSelectionController {
  readonly #dependencies: PdfSelectionControllerDependencies;
  readonly #delayMs: number;
  readonly #maxCharacters: number;
  #snapshot = initialSnapshot();
  #listeners = new Set<() => void>();
  #root: HTMLElement | null = null;
  #document: Document | null = null;
  #popoverElement: HTMLElement | null = null;
  #detachListeners: (() => void) | null = null;
  #timer: ReturnType<typeof setTimeout> | null = null;
  #pendingAppend = false;
  #appendNext = false;
  #disposed = false;

  constructor(dependencies: PdfSelectionControllerDependencies = {}) {
    this.#dependencies = dependencies;
    this.#delayMs = Math.max(0, Math.trunc(dependencies.delayMs ?? 24));
    this.#maxCharacters = Math.max(1, Math.trunc(dependencies.maxCharacters ?? 6_000));
  }

  getSnapshot(): PdfSelectionControllerSnapshot {
    return this.#snapshot;
  }

  subscribe(listener: () => void): () => void {
    this.#listeners.add(listener);
    return () => this.#listeners.delete(listener);
  }

  mount(root: HTMLElement): () => void {
    if (this.#disposed) throw new Error('PdfSelectionController is disposed');
    this.#unmount(true);
    this.#root = root;
    this.#document = root.ownerDocument;
    let active = true;

    const mouseup = (event: Event) => {
      this.#scheduleSelection(Boolean((event as MouseEvent).altKey));
    };
    const selectionchange = () => {
      const selection = this.#getSelection();
      if (this.#selectionBelongsToRoot(selection)) this.#scheduleSelection(false);
    };
    const pointerdown = (event: Event) => {
      const target = event.target;
      if (!(target instanceof Node)) return;
      if (this.#root?.contains(target) || this.#popoverElement?.contains(target)) return;
      this.#clearTransient(true);
    };
    root.addEventListener('mouseup', mouseup);
    this.#document.addEventListener('selectionchange', selectionchange);
    this.#document.addEventListener('pointerdown', pointerdown, true);
    this.#detachListeners = () => {
      root.removeEventListener('mouseup', mouseup);
      root.ownerDocument.removeEventListener('selectionchange', selectionchange);
      root.ownerDocument.removeEventListener('pointerdown', pointerdown, true);
    };

    return () => {
      if (!active) return;
      active = false;
      if (this.#root === root) this.#unmount(true);
    };
  }

  switchPaper(paperId: string, generation: number): void {
    const normalizedPaperId = String(paperId).trim();
    if (!normalizedPaperId) throw new TypeError('paperId must not be empty');
    if (!Number.isInteger(generation) || generation < 1) {
      throw new RangeError('generation must be a positive integer');
    }
    if (
      this.#snapshot.paperId === normalizedPaperId &&
      this.#snapshot.generation === generation
    ) {
      return;
    }
    this.#cancelTimer();
    this.#appendNext = false;
    this.#pendingAppend = false;
    this.#clearNativeSelection();
    this.#publish({
      ...initialSnapshot(),
      paperId: normalizedPaperId,
      generation,
    });
  }

  onZoom(): void {
    this.#cancelTimer();
    this.#appendNext = false;
    this.#pendingAppend = false;
    this.#clearTransient(true);
  }

  beginContinuation(): void {
    this.#cancelTimer();
    this.#appendNext = true;
    this.#pendingAppend = false;
    this.#clearTransient(true, false);
  }

  clearBuffer(): void {
    const { paperId, generation } = this.#snapshot;
    this.#cancelTimer();
    this.#appendNext = false;
    this.#pendingAppend = false;
    this.#clearNativeSelection();
    this.#publish({ ...initialSnapshot(), paperId, generation });
  }

  setPopoverElement(element: HTMLElement | null): void {
    this.#popoverElement = element;
  }

  closePopover(): void {
    this.#clearTransient(true);
  }

  dispose(): void {
    if (this.#disposed) return;
    this.#unmount(true);
    this.#disposed = true;
    this.#listeners.clear();
  }

  #publish(snapshot: PdfSelectionControllerSnapshot): void {
    this.#snapshot = snapshot;
    for (const listener of this.#listeners) listener();
  }

  #getSelection(): Selection | null {
    return this.#dependencies.getSelection?.()
      ?? this.#document?.getSelection()
      ?? null;
  }

  #selectionBelongsToRoot(selection: Selection | null): selection is Selection {
    if (!selection || selection.isCollapsed || !this.#root) return false;
    return Boolean(
      selection.anchorNode &&
      selection.focusNode &&
      this.#root.contains(selection.anchorNode) &&
      this.#root.contains(selection.focusNode),
    );
  }

  #scheduleSelection(append: boolean): void {
    if (!this.#selectionBelongsToRoot(this.#getSelection())) return;
    this.#pendingAppend ||= append;
    this.#cancelTimer();
    this.#timer = setTimeout(() => {
      this.#timer = null;
      this.#commitSelection();
    }, this.#delayMs);
  }

  #commitSelection(): void {
    const root = this.#root;
    const selection = this.#getSelection();
    if (!root || !this.#selectionBelongsToRoot(selection)) return;
    const resolve = this.#dependencies.resolveSelection ?? defaultResolveSelection;
    const input = resolve(selection, root);
    if (!input) return;
    const result = applyPdfSelectionPolicy({
      ...input,
      maxCharacters: this.#maxCharacters,
    });
    if (result.kind === 'empty') {
      this.#clearTransient(false);
      return;
    }
    if (result.kind === 'rejected') {
      this.#appendNext = false;
      this.#pendingAppend = false;
      this.#publish({
        ...this.#snapshot,
        status: 'error',
        anchor: result.anchor,
        popoverRect: selectionRect(selection),
        popoverOpen: false,
        error: {
          reason: result.reason,
          length: result.length,
          maxCharacters: result.maxCharacters,
        },
      });
      return;
    }

    const append = this.#appendNext || this.#pendingAppend;
    const fragment: PdfBufferedSelection = {
      text: result.text,
      pageNumber: input.pageNumber,
      anchor: result.anchor,
    };
    const fragments = append
      ? [...this.#snapshot.fragments, fragment]
      : [fragment];
    const text = fragments.map((item) => item.text).join('\n\n');
    this.#appendNext = false;
    this.#pendingAppend = false;
    if (text.length > this.#maxCharacters) {
      this.#publish({
        ...this.#snapshot,
        status: 'error',
        anchor: result.anchor,
        popoverRect: selectionRect(selection),
        popoverOpen: false,
        error: {
          reason: 'too-long',
          length: text.length,
          maxCharacters: this.#maxCharacters,
        },
      });
      return;
    }
    this.#publish({
      ...this.#snapshot,
      status: 'selected',
      fragments,
      text,
      anchor: result.anchor,
      popoverRect: selectionRect(selection),
      popoverOpen: true,
      error: null,
    });
  }

  #clearTransient(
    clearNative: boolean,
    resetContinuation = true,
  ): void {
    this.#cancelTimer();
    if (resetContinuation) {
      this.#appendNext = false;
      this.#pendingAppend = false;
    }
    if (clearNative) this.#clearNativeSelection();
    this.#publish({
      ...this.#snapshot,
      status: this.#snapshot.fragments.length > 0 ? 'selected' : 'idle',
      anchor: null,
      popoverRect: null,
      popoverOpen: false,
      error: null,
    });
  }

  #clearNativeSelection(): void {
    try {
      this.#getSelection()?.removeAllRanges();
    } catch {
      // Native Selection can already be detached while the page is unmounting.
    }
  }

  #cancelTimer(): void {
    if (this.#timer === null) return;
    clearTimeout(this.#timer);
    this.#timer = null;
  }

  #unmount(clearAll: boolean): void {
    this.#cancelTimer();
    this.#detachListeners?.();
    this.#detachListeners = null;
    this.#clearNativeSelection();
    this.#root = null;
    this.#document = null;
    this.#popoverElement = null;
    this.#appendNext = false;
    this.#pendingAppend = false;
    if (clearAll) {
      const { paperId, generation } = this.#snapshot;
      this.#publish({ ...initialSnapshot(), paperId, generation });
    }
  }
}
