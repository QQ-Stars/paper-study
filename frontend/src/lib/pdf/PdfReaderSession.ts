import { isAbortError } from '../api/errors';
import { paperApi } from '../api/paperApi';
import type { PageViewportAnchor } from './PageViewportAnchor';
import { createPdfJsLoadingTask } from './pdfJsAdapter';

export interface PdfPageSurface {
  pageNumber: number;
  target: Element;
  canvas: HTMLCanvasElement;
  textLayer: HTMLElement;
}

export interface PdfPageRenderHandle {
  width: number;
  height: number;
  completed: Promise<void>;
  cancel(): void;
  dispose(): void;
}

export interface PdfDocumentHandle {
  pageCount: number;
  renderPage(
    surface: PdfPageSurface,
    zoom: number,
    signal: AbortSignal,
  ): Promise<PdfPageRenderHandle>;
  destroy(): Promise<void>;
}

export interface PdfDocumentLoadingHandle {
  promise: Promise<PdfDocumentHandle>;
  /**
   * Owns the loading task and any document it may eventually resolve.
   * After this is called, callers must not separately destroy a late document.
   */
  destroy(): Promise<void>;
}

export interface PdfIntersectionObserverPort {
  observe(target: Element): void;
  unobserve(target: Element): void;
  disconnect(): void;
}

export interface PdfResizeObserverPort {
  observe(target: Element): void;
  disconnect(): void;
}

export interface PdfViewportAnchorPort {
  capture(): PageViewportAnchor | null;
  restore(anchor: PageViewportAnchor): void;
}

export interface PdfReaderSessionDependencies {
  fetchBytes(paperId: string, signal: AbortSignal): Promise<ArrayBuffer>;
  createLoadingTask(
    bytes: ArrayBuffer,
  ): PdfDocumentLoadingHandle | Promise<PdfDocumentLoadingHandle>;
  createIntersectionObserver?: (
    notify: (target: Element, isIntersecting: boolean) => void,
    root: Element,
  ) => PdfIntersectionObserverPort;
  createResizeObserver?: (
    notify: () => void,
  ) => PdfResizeObserverPort;
}

export type PdfSessionStatus =
  | 'idle'
  | 'loading'
  | 'ready'
  | 'error'
  | 'cancelled'
  | 'disposed';

export interface PdfReaderSessionSnapshot {
  status: PdfSessionStatus;
  paperId: string | null;
  generation: number;
  zoom: number;
  pageCount: number;
  error: unknown | null;
  pages: Readonly<Record<number, PdfPageSnapshot>>;
}

export interface PdfPageSnapshot {
  pageNumber: number;
  status: 'idle' | 'loading' | 'ready' | 'error';
  width: number;
  height: number;
  error: unknown | null;
}

interface SessionOwner {
  paperId: string;
  generation: number;
  abortController: AbortController;
  detachExternalAbort: () => void;
  loadingTask: PdfDocumentLoadingHandle | null;
  loadingTaskDestroyed: boolean;
  document: PdfDocumentHandle | null;
  destroyed: boolean;
  cleanupPromise: Promise<void> | null;
}

interface MountedPageOwner {
  surface: PdfPageSurface;
  visible: boolean;
  revision: number;
  renderGeneration: number | null;
  renderZoom: number | null;
  renderPromise: Promise<void> | null;
  abortController: AbortController | null;
  renderHandle: PdfPageRenderHandle | null;
  released: boolean;
}

interface ViewportOwner {
  element: Element;
  anchor: PdfViewportAnchorPort | null;
  intersectionObserver: PdfIntersectionObserverPort | null;
  resizeObserver: PdfResizeObserverPort | null;
}

const initialSnapshot: PdfReaderSessionSnapshot = {
  status: 'idle',
  paperId: null,
  generation: 0,
  zoom: 1,
  pageCount: 0,
  error: null,
  pages: {},
};

function defaultIntersectionObserver(
  notify: (target: Element, isIntersecting: boolean) => void,
  root: Element,
): PdfIntersectionObserverPort {
  if (typeof IntersectionObserver === 'function') {
    return new IntersectionObserver(
      (entries) => {
        for (const entry of entries) {
          notify(entry.target, entry.isIntersecting);
        }
      },
      { root },
    );
  }

  const observed = new Set<Element>();
  return {
    observe(target) {
      observed.add(target);
      queueMicrotask(() => {
        if (observed.has(target)) notify(target, true);
      });
    },
    unobserve(target) {
      observed.delete(target);
    },
    disconnect() {
      observed.clear();
    },
  };
}

function defaultResizeObserver(notify: () => void): PdfResizeObserverPort {
  if (typeof ResizeObserver === 'function') {
    return new ResizeObserver(() => notify());
  }
  return {
    observe() {},
    disconnect() {},
  };
}

function linkAbortSignal(
  controller: AbortController,
  externalSignal?: AbortSignal,
): () => void {
  if (!externalSignal) return () => undefined;
  if (externalSignal.aborted) {
    controller.abort(externalSignal.reason);
    return () => undefined;
  }

  const abort = () => controller.abort(externalSignal.reason);
  externalSignal.addEventListener('abort', abort, { once: true });
  return () => externalSignal.removeEventListener('abort', abort);
}

const defaultDependencies: PdfReaderSessionDependencies = {
  fetchBytes: (paperId, signal) => paperApi.getPdfBytes(paperId, signal),
  createLoadingTask: createPdfJsLoadingTask,
};

export class PdfReaderSession {
  readonly #dependencies: PdfReaderSessionDependencies;
  #generation = 0;
  #owner: SessionOwner | null = null;
  #snapshot: PdfReaderSessionSnapshot = initialSnapshot;
  #listeners = new Set<() => void>();
  #mountedPages = new Map<number, MountedPageOwner>();
  #viewport: ViewportOwner | null = null;

  constructor(dependencies: PdfReaderSessionDependencies = defaultDependencies) {
    this.#dependencies = dependencies;
  }

  getSnapshot(): PdfReaderSessionSnapshot {
    return this.#snapshot;
  }

  subscribe(listener: () => void): () => void {
    this.#listeners.add(listener);
    return () => this.#listeners.delete(listener);
  }

  attachViewport(
    element: Element,
    anchor: PdfViewportAnchorPort | null = null,
  ): () => void {
    this.#disconnectObservers();
    this.#cancelAllPages();
    const viewport: ViewportOwner = {
      element,
      anchor,
      intersectionObserver: null,
      resizeObserver: null,
    };
    this.#viewport = viewport;
    this.#ensureObservers();

    return () => {
      if (this.#viewport !== viewport) return;
      this.#disconnectObservers();
      this.#cancelAllPages(true);
      this.#viewport = null;
    };
  }

  mountPage(surface: PdfPageSurface): () => void {
    if (!Number.isInteger(surface.pageNumber) || surface.pageNumber < 1) {
      throw new RangeError('pageNumber must be a positive integer');
    }
    const existing = this.#mountedPages.get(surface.pageNumber);
    if (existing) {
      this.#viewport?.intersectionObserver?.unobserve(existing.surface.target);
      this.#releasePage(existing);
    }

    const page: MountedPageOwner = {
      surface,
      visible: false,
      revision: 0,
      renderGeneration: null,
      renderZoom: null,
      renderPromise: null,
      abortController: null,
      renderHandle: null,
      released: false,
    };
    this.#mountedPages.set(surface.pageNumber, page);
    this.#publishPage(page, {
      pageNumber: surface.pageNumber,
      status: 'idle',
      width: 0,
      height: 0,
      error: null,
    });
    this.#viewport?.intersectionObserver?.observe(surface.target);

    return () => {
      if (this.#mountedPages.get(surface.pageNumber) !== page) return;
      this.#viewport?.intersectionObserver?.unobserve(surface.target);
      this.#mountedPages.delete(surface.pageNumber);
      this.#releasePage(page);
      this.#removePageSnapshot(surface.pageNumber);
    };
  }

  async open(
    paperId: string,
    externalSignal?: AbortSignal,
  ): Promise<PdfReaderSessionSnapshot> {
    const normalizedPaperId = String(paperId).trim();
    if (!normalizedPaperId) throw new TypeError('paperId must not be empty');

    const generation = ++this.#generation;
    const previousOwner = this.#owner;
    this.#resetPagesForDocumentChange();
    const abortController = new AbortController();
    const owner: SessionOwner = {
      paperId: normalizedPaperId,
      generation,
      abortController,
      detachExternalAbort: linkAbortSignal(
        abortController,
        externalSignal,
      ),
      loadingTask: null,
      loadingTaskDestroyed: false,
      document: null,
      destroyed: false,
      cleanupPromise: null,
    };
    this.#owner = owner;
    this.#setSnapshot({
      status: 'loading',
      paperId: normalizedPaperId,
      generation,
      zoom: this.#snapshot.zoom,
      pageCount: 0,
      error: null,
      pages: {},
    });
    void this.#destroyOwner(previousOwner).catch(() => undefined);

    try {
      const bytes = await this.#dependencies.fetchBytes(
        normalizedPaperId,
        abortController.signal,
      );
      if (!this.#isCurrent(owner)) return this.#snapshot;

      const loadingTask = await this.#dependencies.createLoadingTask(bytes);
      owner.loadingTask = loadingTask;
      if (owner.destroyed || !this.#isCurrent(owner)) {
        await this.#destroyLoadingTask(owner);
        return this.#snapshot;
      }

      const document = await loadingTask.promise;
      if (owner.destroyed || !this.#isCurrent(owner)) {
        return this.#snapshot;
      }

      owner.document = document;
      this.#setSnapshot({
        status: 'ready',
        paperId: normalizedPaperId,
        generation,
        zoom: this.#snapshot.zoom,
        pageCount: document.pageCount,
        error: null,
        pages: this.#snapshot.pages,
      });
      this.#ensureObservers();
      return this.#snapshot;
    } catch (error) {
      if (!this.#isCurrent(owner)) return this.#snapshot;
      this.#setSnapshot({
        ...this.#snapshot,
        status: isAbortError(error) ? 'cancelled' : 'error',
        error: isAbortError(error) ? null : error,
      });
      if (isAbortError(error)) return this.#snapshot;
      throw error;
    }
  }

  async setZoom(zoom: number): Promise<PdfReaderSessionSnapshot> {
    if (!Number.isFinite(zoom)) throw new TypeError('zoom must be finite');
    const nextZoom = Math.min(3, Math.max(0.5, zoom));
    if (nextZoom === this.#snapshot.zoom) return this.#snapshot;

    const generation = this.#snapshot.generation;
    const anchor = this.#viewport?.anchor?.capture() ?? null;
    const visiblePages = [...this.#mountedPages.values()].filter(
      (page) => page.visible && !page.released,
    );
    this.#cancelAllPages();
    this.#setSnapshot({ ...this.#snapshot, zoom: nextZoom });
    this.#rebuildIntersectionObserver();

    await Promise.all(visiblePages.map((page) => this.#startPageRender(page)));
    if (
      anchor &&
      this.#snapshot.generation === generation &&
      this.#snapshot.zoom === nextZoom
    ) {
      this.#viewport?.anchor?.restore(anchor);
    }
    return this.#snapshot;
  }

  async dispose(): Promise<void> {
    const owner = this.#owner;
    this.#owner = null;
    this.#disconnectObservers();
    this.#cancelAllPages(true);
    this.#viewport = null;
    this.#setSnapshot({
      status: 'disposed',
      paperId: null,
      generation: this.#generation,
      zoom: this.#snapshot.zoom,
      pageCount: 0,
      error: null,
      pages: {},
    });
    this.#listeners.clear();
    await this.#destroyOwner(owner);
  }

  #setSnapshot(snapshot: PdfReaderSessionSnapshot): void {
    this.#snapshot = snapshot;
    for (const listener of this.#listeners) listener();
  }

  #publishPage(page: MountedPageOwner, snapshot: PdfPageSnapshot): void {
    if (page.released || this.#mountedPages.get(snapshot.pageNumber) !== page) {
      return;
    }
    this.#setSnapshot({
      ...this.#snapshot,
      pages: { ...this.#snapshot.pages, [snapshot.pageNumber]: snapshot },
    });
  }

  #removePageSnapshot(pageNumber: number): void {
    const pages = { ...this.#snapshot.pages };
    delete pages[pageNumber];
    this.#setSnapshot({ ...this.#snapshot, pages });
  }

  #ensureObservers(): void {
    const viewport = this.#viewport;
    if (!viewport) return;
    if (!viewport.intersectionObserver) {
      const create =
        this.#dependencies.createIntersectionObserver ??
        defaultIntersectionObserver;
      viewport.intersectionObserver = create(
        (target, isIntersecting) =>
          this.#handleIntersection(target, isIntersecting),
        viewport.element,
      );
      for (const page of this.#mountedPages.values()) {
        viewport.intersectionObserver.observe(page.surface.target);
      }
    }
    if (!viewport.resizeObserver) {
      const create =
        this.#dependencies.createResizeObserver ?? defaultResizeObserver;
      viewport.resizeObserver = create(() => {
        this.#setSnapshot({ ...this.#snapshot });
      });
      viewport.resizeObserver.observe(viewport.element);
    }
  }

  #rebuildIntersectionObserver(): void {
    const viewport = this.#viewport;
    if (!viewport) return;
    viewport.intersectionObserver?.disconnect();
    viewport.intersectionObserver = null;
    this.#ensureObservers();
  }

  #disconnectObservers(): void {
    if (!this.#viewport) return;
    this.#viewport.intersectionObserver?.disconnect();
    this.#viewport.resizeObserver?.disconnect();
    this.#viewport.intersectionObserver = null;
    this.#viewport.resizeObserver = null;
  }

  #handleIntersection(target: Element, isIntersecting: boolean): void {
    const page = [...this.#mountedPages.values()].find(
      (candidate) => candidate.surface.target === target,
    );
    if (!page || page.released) return;
    page.visible = isIntersecting;
    if (isIntersecting) void this.#startPageRender(page);
    else this.#cancelPage(page);
  }

  #startPageRender(page: MountedPageOwner): Promise<void> {
    const document = this.#owner?.document;
    if (!document || page.released || !page.visible) return Promise.resolve();
    if (page.surface.pageNumber > document.pageCount) {
      this.#publishPage(page, {
        pageNumber: page.surface.pageNumber,
        status: 'error',
        width: 0,
        height: 0,
        error: new RangeError('pageNumber exceeds the loaded document'),
      });
      return Promise.resolve();
    }

    const generation = this.#snapshot.generation;
    const zoom = this.#snapshot.zoom;
    const currentPageSnapshot = this.#snapshot.pages[page.surface.pageNumber];
    if (
      page.renderGeneration === generation &&
      page.renderZoom === zoom
    ) {
      if (page.renderPromise) return page.renderPromise;
      if (page.renderHandle && currentPageSnapshot?.status === 'ready') {
        return Promise.resolve();
      }
    }

    this.#cancelPage(page);
    const revision = ++page.revision;
    const controller = new AbortController();
    page.renderGeneration = generation;
    page.renderZoom = zoom;
    page.abortController = controller;
    this.#publishPage(page, {
      pageNumber: page.surface.pageNumber,
      status: 'loading',
      width: 0,
      height: 0,
      error: null,
    });

    const renderPromise = this.#performPageRender(
      page,
      document,
      revision,
      generation,
      zoom,
      controller,
    );
    page.renderPromise = renderPromise;
    const clearRenderPromise = () => {
      if (page.renderPromise === renderPromise) page.renderPromise = null;
    };
    void renderPromise.then(clearRenderPromise, clearRenderPromise);
    return renderPromise;
  }

  async #performPageRender(
    page: MountedPageOwner,
    document: PdfDocumentHandle,
    revision: number,
    generation: number,
    zoom: number,
    controller: AbortController,
  ): Promise<void> {
    try {
      const renderHandle = await document.renderPage(
        page.surface,
        zoom,
        controller.signal,
      );
      if (!this.#isPageCurrent(page, revision, generation)) {
        renderHandle.cancel();
        renderHandle.dispose();
        return;
      }
      page.renderHandle = renderHandle;
      await renderHandle.completed;
      if (!this.#isPageCurrent(page, revision, generation)) return;
      this.#publishPage(page, {
        pageNumber: page.surface.pageNumber,
        status: 'ready',
        width: renderHandle.width,
        height: renderHandle.height,
        error: null,
      });
    } catch (error) {
      if (!this.#isPageCurrent(page, revision, generation)) return;
      if (isAbortError(error)) {
        this.#publishPage(page, {
          pageNumber: page.surface.pageNumber,
          status: 'idle',
          width: 0,
          height: 0,
          error: null,
        });
        return;
      }
      page.renderHandle?.cancel();
      page.renderHandle?.dispose();
      page.renderHandle = null;
      page.renderGeneration = null;
      page.renderZoom = null;
      this.#publishPage(page, {
        pageNumber: page.surface.pageNumber,
        status: 'error',
        width: 0,
        height: 0,
        error,
      });
    }
  }

  #isPageCurrent(
    page: MountedPageOwner,
    revision: number,
    generation: number,
  ): boolean {
    return (
      !page.released &&
      page.revision === revision &&
      this.#mountedPages.get(page.surface.pageNumber) === page &&
      this.#snapshot.generation === generation
    );
  }

  #resetSurface(surface: PdfPageSurface): void {
    surface.canvas.width = 0;
    surface.canvas.height = 0;
    surface.canvas.style.width = '';
    surface.canvas.style.height = '';
    surface.textLayer.replaceChildren();
  }

  #cancelPage(page: MountedPageOwner): void {
    page.revision += 1;
    page.renderGeneration = null;
    page.renderZoom = null;
    page.renderPromise = null;
    page.abortController?.abort();
    page.abortController = null;
    page.renderHandle?.cancel();
    page.renderHandle?.dispose();
    page.renderHandle = null;
    this.#resetSurface(page.surface);
    this.#publishPage(page, {
      pageNumber: page.surface.pageNumber,
      status: 'idle',
      width: 0,
      height: 0,
      error: null,
    });
  }

  #releasePage(page: MountedPageOwner): void {
    if (page.released) return;
    this.#cancelPage(page);
    page.released = true;
  }

  #cancelAllPages(clear = false): void {
    for (const page of this.#mountedPages.values()) {
      if (clear) this.#releasePage(page);
      else this.#cancelPage(page);
    }
    if (clear) this.#mountedPages.clear();
  }

  #resetPagesForDocumentChange(): void {
    this.#disconnectObservers();
    this.#cancelAllPages(true);
    this.#ensureObservers();
  }

  #isCurrent(owner: SessionOwner): boolean {
    return this.#owner === owner && !owner.destroyed;
  }

  async #destroyLoadingTask(owner: SessionOwner): Promise<void> {
    if (!owner.loadingTask || owner.loadingTaskDestroyed) return;
    owner.loadingTaskDestroyed = true;
    await owner.loadingTask.destroy();
  }

  #destroyOwner(owner: SessionOwner | null): Promise<void> {
    if (!owner) return Promise.resolve();
    if (owner.cleanupPromise) return owner.cleanupPromise;

    owner.destroyed = true;
    owner.abortController.abort();
    owner.detachExternalAbort();
    owner.cleanupPromise = (async () => {
      if (owner.document) {
        await owner.document.destroy();
      } else {
        await this.#destroyLoadingTask(owner);
      }
    })();
    return owner.cleanupPromise;
  }
}
