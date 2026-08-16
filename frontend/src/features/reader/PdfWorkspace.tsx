import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  useSyncExternalStore,
  type CSSProperties,
  type KeyboardEvent,
} from 'react';

import {
  focusFirstWithin,
  focusSafely,
} from '../../lib/accessibility/focus';
import {
  capturePageViewportAnchor,
  resolvePageViewportAnchor,
  type PageRectAnchor,
  type PageViewportLayout,
} from '../../lib/pdf/PageViewportAnchor';
import {
  PdfReaderSession,
  type PdfReaderSessionSnapshot,
  type PdfViewportAnchorPort,
} from '../../lib/pdf/PdfReaderSession';
import {
  PdfSelectionController,
  type PdfSelectionControllerSnapshot,
} from '../../lib/pdf/PdfSelectionController';
import {
  SelectionTranslator,
  type SelectionTranslationCommit,
} from '../../lib/pdf/SelectionTranslator';
import { PdfPage } from './PdfPage';
import './pdf-workspace.css';

type PdfSelectionTranslator = SelectionTranslator<PageRectAnchor | null>;

interface TranslationView {
  paperId: string;
  generation: number;
  sourceText: string;
  status: 'idle' | 'loading' | 'ready' | 'error';
  translatedText: string;
  error: unknown | null;
}

interface PageTarget {
  paperId: string;
  pageNumber: number;
}

export interface PdfWorkspaceProps {
  paperId: string;
  className?: string;
  onGenerationChange?: (generation: number) => void;
  createSession?: () => PdfReaderSession;
  createSelectionController?: () => PdfSelectionController;
  createTranslator?: () => PdfSelectionTranslator;
}

const emptyTranslation: TranslationView = {
  paperId: '',
  generation: 0,
  sourceText: '',
  status: 'idle',
  translatedText: '',
  error: null,
};

function errorMessage(error: unknown): string {
  return error instanceof Error && error.message.trim()
    ? error.message.trim()
    : '未知错误';
}

function viewportPages(viewport: HTMLElement) {
  const viewportRect = viewport.getBoundingClientRect();
  return [...viewport.querySelectorAll<HTMLElement>('[data-pdf-page-number]')]
    .map((page) => {
      const pageNumber = Number(page.dataset.pdfPageNumber);
      const rect = page.getBoundingClientRect();
      return {
        pageNumber,
        top: viewport.scrollTop + rect.top - viewportRect.top,
        height: rect.height || page.offsetHeight,
      };
    });
}

function mostRelevantVisiblePage(
  layout: PageViewportLayout,
): number | null {
  const viewportTop = Number.isFinite(layout.scrollTop)
    ? Math.max(0, layout.scrollTop)
    : 0;
  const viewportHeight = Number.isFinite(layout.viewportHeight)
    ? Math.max(0, layout.viewportHeight)
    : 0;
  if (viewportHeight === 0) return null;
  const viewportBottom = viewportTop + viewportHeight;
  const viewportCenter = viewportTop + viewportHeight / 2;
  let best: {
    pageNumber: number;
    visibleHeight: number;
    centerDistance: number;
  } | null = null;

  for (const page of layout.pages) {
    if (
      !Number.isInteger(page.pageNumber) ||
      page.pageNumber < 1 ||
      !Number.isFinite(page.top) ||
      !Number.isFinite(page.height) ||
      page.height <= 0
    ) {
      continue;
    }
    const pageBottom = page.top + page.height;
    const visibleHeight = Math.max(
      0,
      Math.min(viewportBottom, pageBottom) - Math.max(viewportTop, page.top),
    );
    if (visibleHeight === 0) continue;
    const centerDistance = Math.abs(
      page.top + page.height / 2 - viewportCenter,
    );
    if (
      best === null ||
      visibleHeight > best.visibleHeight ||
      (visibleHeight === best.visibleHeight &&
        centerDistance < best.centerDistance) ||
      (visibleHeight === best.visibleHeight &&
        centerDistance === best.centerDistance &&
        page.pageNumber < best.pageNumber)
    ) {
      best = { pageNumber: page.pageNumber, visibleHeight, centerDistance };
    }
  }

  return best?.pageNumber ?? null;
}

function createViewportAnchorPort(
  viewport: HTMLElement,
): PdfViewportAnchorPort {
  return {
    capture() {
      return capturePageViewportAnchor({
        scrollTop: viewport.scrollTop,
        viewportHeight: viewport.clientHeight,
        pages: viewportPages(viewport),
      });
    },
    restore(anchor) {
      const scrollTop = resolvePageViewportAnchor(anchor, {
        viewportHeight: viewport.clientHeight,
        pages: viewportPages(viewport),
      });
      if (scrollTop !== null) viewport.scrollTop = scrollTop;
    },
  };
}

function useSessionSnapshot(
  session: PdfReaderSession,
): PdfReaderSessionSnapshot {
  const subscribe = useCallback(
    (notify: () => void) => session.subscribe(notify),
    [session],
  );
  const getSnapshot = useCallback(() => session.getSnapshot(), [session]);
  return useSyncExternalStore(subscribe, getSnapshot, getSnapshot);
}

function useSelectionSnapshot(
  controller: PdfSelectionController,
): PdfSelectionControllerSnapshot {
  const subscribe = useCallback(
    (notify: () => void) => controller.subscribe(notify),
    [controller],
  );
  const getSnapshot = useCallback(
    () => controller.getSnapshot(),
    [controller],
  );
  return useSyncExternalStore(subscribe, getSnapshot, getSnapshot);
}

function displayStatus(
  snapshot: PdfReaderSessionSnapshot,
  ownsPaper: boolean,
): string {
  if (!ownsPaper || snapshot.status === 'idle' || snapshot.status === 'loading') {
    return '正在载入 PDF…';
  }
  if (snapshot.status === 'error') {
    return `PDF 载入失败：${errorMessage(snapshot.error)}`;
  }
  if (snapshot.status === 'cancelled') return 'PDF 载入已取消';
  if (snapshot.status === 'disposed') return 'PDF 阅读器已释放';
  return `共 ${snapshot.pageCount} 页`;
}

export function PdfWorkspace({
  paperId,
  className,
  onGenerationChange,
  createSession,
  createSelectionController,
  createTranslator,
}: PdfWorkspaceProps) {
  const [session] = useState(
    () => createSession?.() ?? new PdfReaderSession(),
  );
  const [selectionController] = useState(
    () => createSelectionController?.() ?? new PdfSelectionController(),
  );
  const [translator] = useState<PdfSelectionTranslator>(
    () => createTranslator?.() ?? new SelectionTranslator(),
  );
  const [translation, setTranslation] = useState(emptyTranslation);
  const [pageTarget, setPageTarget] = useState<PageTarget>({
    paperId,
    pageNumber: 1,
  });
  const viewportRef = useRef<HTMLDivElement>(null);
  const popoverElementRef = useRef<HTMLDivElement>(null);
  const snapshot = useSessionSnapshot(session);
  const selection = useSelectionSnapshot(selectionController);
  const ownsPaper = snapshot.paperId === paperId && snapshot.generation > 0;
  const generation = ownsPaper ? snapshot.generation : 0;
  const status = ownsPaper ? snapshot.status : 'loading';
  const pageCount = ownsPaper ? snapshot.pageCount : 0;
  const currentPage = pageTarget.paperId === paperId
    ? Math.min(Math.max(pageTarget.pageNumber, 1), Math.max(pageCount, 1))
    : 1;
  const ownsSelection =
    generation > 0 &&
    selection.paperId === paperId &&
    selection.generation === generation;
  const bufferedSelection = ownsSelection ? selection.text : '';
  const ownsTranslation =
    translation.paperId === paperId &&
    translation.generation === generation &&
    translation.sourceText === bufferedSelection;
  const activeTranslation = ownsTranslation ? translation : emptyTranslation;

  useEffect(() => {
    const abortController = new AbortController();
    void session.open(paperId, abortController.signal).catch(() => undefined);
    return () => abortController.abort();
  }, [paperId, session]);

  useEffect(() => {
    const viewport = viewportRef.current;
    if (!viewport) return undefined;
    return session.attachViewport(
      viewport,
      createViewportAnchorPort(viewport),
    );
  }, [session]);

  useEffect(() => {
    const viewport = viewportRef.current;
    if (!viewport) return undefined;
    return selectionController.mount(viewport);
  }, [selectionController]);

  useEffect(() => {
    const viewport = viewportRef.current;
    if (!viewport) return undefined;
    let animationFrame: number | null = null;
    const updateCurrentPage = () => {
      animationFrame = null;
      const pageNumber = mostRelevantVisiblePage({
        scrollTop: viewport.scrollTop,
        viewportHeight: viewport.clientHeight,
        pages: viewportPages(viewport),
      });
      if (pageNumber === null) return;
      setPageTarget((current) =>
        current.paperId === paperId && current.pageNumber === pageNumber
          ? current
          : { paperId, pageNumber },
      );
    };
    const handleScroll = () => {
      if (animationFrame !== null) return;
      animationFrame = window.requestAnimationFrame(updateCurrentPage);
    };
    viewport.addEventListener('scroll', handleScroll, { passive: true });
    return () => {
      viewport.removeEventListener('scroll', handleScroll);
      if (animationFrame !== null) {
        window.cancelAnimationFrame(animationFrame);
      }
    };
  }, [paperId]);

  useEffect(() => {
    if (generation < 1) return;
    selectionController.switchPaper(paperId, generation);
    translator.updateContext({ paperId, generation });
    onGenerationChange?.(generation);
  }, [generation, onGenerationChange, paperId, selectionController, translator]);

  useEffect(() => () => {
    translator.dispose();
    void session.dispose().catch(() => undefined);
  }, [session, translator]);

  useEffect(() => {
    if (!ownsSelection || !selection.popoverOpen) return undefined;
    const popover = popoverElementRef.current;
    if (!popover) return undefined;
    const fallbackFocus = viewportRef.current;
    const { activeElement, body, documentElement } = popover.ownerDocument;
    const previousFocus =
      activeElement instanceof HTMLElement &&
      activeElement !== body &&
      activeElement !== documentElement
        ? activeElement
        : fallbackFocus;
    focusFirstWithin(popover);
    return () => {
      const currentFocus = popover.ownerDocument.activeElement;
      const lostFocus = currentFocus === body || currentFocus === documentElement;
      if (!popover.contains(currentFocus) && !lostFocus) return;
      if (!focusSafely(previousFocus)) focusSafely(fallbackFocus);
    };
  }, [ownsSelection, selection.popoverOpen]);

  const popoverRef = useCallback(
    (element: HTMLDivElement | null) => {
      popoverElementRef.current = element;
      selectionController.setPopoverElement(element);
    },
    [selectionController],
  );

  const changeZoom = (nextZoom: number) => {
    selectionController.onZoom();
    translator.abort();
    setTranslation(emptyTranslation);
    void session.setZoom(nextZoom).catch((error: unknown) => {
      setTranslation({
        paperId,
        generation,
        sourceText: bufferedSelection,
        status: 'error',
        translatedText: '',
        error,
      });
    });
  };

  const scrollToPage = (requestedPage: number) => {
    if (pageCount < 1) return;
    const nextPage = Math.min(Math.max(Math.trunc(requestedPage), 1), pageCount);
    setPageTarget({ paperId, pageNumber: nextPage });
    const page = viewportRef.current?.querySelector<HTMLElement>(
      `[data-pdf-page-number="${nextPage}"]`,
    );
    page?.scrollIntoView({ block: 'start' });
  };

  const translateSelection = () => {
    if (activeTranslation.status === 'loading') return;
    const fixedSelection = selectionController.getSnapshot();
    if (
      generation < 1 ||
      fixedSelection.paperId !== paperId ||
      fixedSelection.generation !== generation ||
      !fixedSelection.text.trim()
    ) {
      return;
    }
    const sourceText = fixedSelection.text;
    const anchor = fixedSelection.anchor;
    translator.updateContext({ paperId, generation });
    setTranslation({
      paperId,
      generation,
      sourceText,
      status: 'loading',
      translatedText: '',
      error: null,
    });
    void translator.translate(
      { text: sourceText, anchor },
      (commit: SelectionTranslationCommit<PageRectAnchor | null>) => {
        setTranslation({
          paperId: commit.paperId,
          generation: commit.generation,
          sourceText: commit.sourceText,
          status: 'ready',
          translatedText: commit.translatedText,
          error: null,
        });
      },
    ).catch((error: unknown) => {
      setTranslation((current) => {
        if (
          current.paperId !== paperId ||
          current.generation !== generation ||
          current.sourceText !== sourceText
        ) {
          return current;
        }
        return { ...current, status: 'error', error };
      });
    });
  };

  const clearSelection = () => {
    translator.abort();
    selectionController.clearBuffer();
    setTranslation(emptyTranslation);
  };

  const closePopover = () => {
    translator.abort();
    selectionController.closePopover();
    setTranslation(emptyTranslation);
  };

  const handlePopoverKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    if (event.key !== 'Escape') return;
    event.preventDefault();
    event.stopPropagation();
    closePopover();
  };

  // Clamp the translation popover into the visible window: selections near the
  // bottom/right edge used to push it fully off-screen ("invisible bubble").
  // Sizes are bounded by css (width min(26rem,…), max-height min(24rem,…)), so
  // the bounds alone are enough to decide placement without measuring the DOM.
  const popoverStyle = useMemo<CSSProperties | undefined>(() => {
    if (!ownsSelection) return undefined;
    const margin = 12;
    const gap = 10;
    const innerWidth = window.innerWidth;
    const innerHeight = window.innerHeight;
    const width = Math.min(416, innerWidth - margin * 2);
    const height = Math.min(384, innerHeight - margin * 2);
    const clampLeft = (value: number) =>
      Math.round(
        Math.min(Math.max(margin, value), Math.max(margin, innerWidth - width - margin)),
      );
    const rect = selection.popoverRect;
    if (!rect) {
      return {
        position: 'fixed',
        left: clampLeft(innerWidth - width - margin),
        top: Math.round(Math.max(margin, innerHeight - height - margin)),
      };
    }
    const belowTop = rect.bottom + gap;
    const fitsBelow = belowTop + height <= innerHeight - margin;
    const top = fitsBelow ? belowTop : Math.max(margin, rect.top - height - gap);
    return {
      position: 'fixed',
      left: clampLeft(rect.left),
      top: Math.round(top),
    };
  }, [ownsSelection, selection.popoverRect]);

  return (
    <section
      aria-label="PDF 阅读工作区"
      className={['pdf-workspace', className].filter(Boolean).join(' ')}
      data-generation={generation}
      data-paper-id={paperId}
      data-status={status}
    >
      <header className="pdf-workspace__toolbar">
        <div className="pdf-workspace__identity">
          <p>PDF READER</p>
          <strong>{paperId}</strong>
        </div>
        <p
          className="pdf-workspace__status"
          role={status === 'error' ? 'alert' : 'status'}
        >
          {displayStatus(snapshot, ownsPaper)}
        </p>
        <div aria-label="PDF 页码" className="pdf-workspace__pagination" role="group">
          <button
            aria-label="上一页"
            disabled={pageCount < 1 || currentPage <= 1}
            onClick={() => scrollToPage(currentPage - 1)}
            type="button"
          >
            ←
          </button>
          <label>
            <span className="visually-hidden">当前页</span>
            <input
              aria-label="当前页"
              disabled={pageCount < 1}
              max={Math.max(pageCount, 1)}
              min={1}
              onChange={(event) => scrollToPage(Number(event.currentTarget.value))}
              type="number"
              value={currentPage}
            />
          </label>
          <span aria-hidden="true">/ {pageCount}</span>
          <button
            aria-label="下一页"
            disabled={pageCount < 1 || currentPage >= pageCount}
            onClick={() => scrollToPage(currentPage + 1)}
            type="button"
          >
            →
          </button>
        </div>
        <div aria-label="PDF 缩放" className="pdf-workspace__zoom" role="group">
          <button
            aria-label="缩小 PDF"
            disabled={snapshot.zoom <= 0.5}
            onClick={() => changeZoom(Math.round((snapshot.zoom - 0.1) * 10) / 10)}
            type="button"
          >
            −
          </button>
          <output aria-label="当前缩放比例">{Math.round(snapshot.zoom * 100)}%</output>
          <button
            aria-label="放大 PDF"
            disabled={snapshot.zoom >= 3}
            onClick={() => changeZoom(Math.round((snapshot.zoom + 0.1) * 10) / 10)}
            type="button"
          >
            +
          </button>
        </div>
      </header>

      <div
        className="pdf-workspace__viewport"
        data-testid="pdf-viewport"
        ref={viewportRef}
        tabIndex={0}
      >
        {!ownsPaper || status === 'loading' ? (
          <p className="pdf-workspace__empty" role="status">正在载入 PDF…</p>
        ) : null}
        {status === 'error' ? (
          <p className="pdf-workspace__empty" role="alert">
            无法载入 PDF：{errorMessage(snapshot.error)}
          </p>
        ) : null}
        {ownsPaper && status === 'ready' && pageCount === 0 ? (
          <p className="pdf-workspace__empty">PDF 没有可渲染页面。</p>
        ) : null}
        {ownsPaper && status === 'ready' ? (
          <div
            className="pdf-workspace__pages"
          >
            {Array.from({ length: pageCount }, (_, index) => {
              const pageNumber = index + 1;
              return (
                <PdfPage
                  generation={generation}
                  key={`${paperId}:${generation}:${pageNumber}`}
                  pageNumber={pageNumber}
                  paperId={paperId}
                  session={session}
                  snapshot={snapshot.pages[pageNumber]}
                />
              );
            })}
          </div>
        ) : null}
      </div>

      {ownsSelection && selection.status === 'error' && selection.error ? (
        <p className="pdf-workspace__selection-error" role="alert">
          选文共 {selection.error.length} 字，超过 {selection.error.maxCharacters} 字上限；未截断已有选文。
        </p>
      ) : null}

      {bufferedSelection ? (
        <aside
          aria-label="选文缓冲"
          className="pdf-workspace__selection-buffer floating-material"
        >
          <div>
            <strong>已选 {bufferedSelection.length} 字</strong>
            <span>{selection.fragments.length} 段</span>
          </div>
          <blockquote>{bufferedSelection}</blockquote>
          <div>
            <button
              onClick={() => selectionController.beginContinuation()}
              type="button"
            >
              续选
            </button>
            <button onClick={clearSelection} type="button">清空选文</button>
          </div>
        </aside>
      ) : null}

      {ownsSelection && selection.popoverOpen ? (
        <div
          aria-label="选文翻译"
          className="pdf-workspace__translation-popover floating-material"
          onKeyDown={handlePopoverKeyDown}
          ref={popoverRef}
          role="dialog"
          style={popoverStyle}
        >
          <header>
            <strong>选文翻译</strong>
            <button aria-label="关闭选文翻译" onClick={closePopover} type="button">×</button>
          </header>
          <button
            aria-disabled={activeTranslation.status === 'loading'}
            data-panel-autofocus="true"
            onClick={translateSelection}
            type="button"
          >
            {activeTranslation.status === 'loading'
              ? '正在翻译…'
              : activeTranslation.status === 'ready'
                ? '重新翻译'
                : activeTranslation.status === 'error'
                  ? '重试翻译'
                  : '翻译选文'}
          </button>
          {activeTranslation.status === 'loading' ? (
            <p role="status">正在翻译选文…</p>
          ) : null}
          {activeTranslation.status === 'ready' ? (
            <p>{activeTranslation.translatedText}</p>
          ) : null}
          {activeTranslation.status === 'error' ? (
            <p role="alert">翻译失败：{errorMessage(activeTranslation.error)}</p>
          ) : null}
        </div>
      ) : null}
    </section>
  );
}
