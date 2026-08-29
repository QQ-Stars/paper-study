import { useCallback, useEffect, useMemo, useRef, useState } from 'react';

import {
  getDocument,
  GlobalWorkerOptions,
  type PDFDocumentProxy,
} from 'pdfjs-dist';
import workerUrl from 'pdfjs-dist/build/pdf.worker.min.mjs?url';

import { PdfPageCanvas, type PdfPageSize } from './PdfPageCanvas';
import {
  PDF_DEFAULT_SCALE,
  clampPdfPage,
  clampPdfScale,
  parseSavedPdfPosition,
  pdfRenderWindow,
} from './pdfViewerState';

/* PDF.js 连续阅读视图：所有页面纵向排列，只渲染当前页附近的 canvas。
 * textLayer 的选中事件会冒泡到外层，供 SelectionTranslate 触发划词翻译。 */

interface PdfViewerProps {
  url: string;
  /* 提供后按论文记忆上次阅读位置（页码/缩放，localStorage） */
  storageKey?: string;
  onConvert?: () => void;
  converting?: boolean;
}

const PDF_POS_PREFIX = 'paper-study:pdf-pos:';
const DEFAULT_PAGE_SIZE: PdfPageSize = { width: 612, height: 792 };

function readSavedPosition(key: string | undefined) {
  if (!key) return null;
  try {
    return parseSavedPdfPosition(localStorage.getItem(PDF_POS_PREFIX + key));
  } catch {
    return null;
  }
}

export function PdfViewer({ url, storageKey, onConvert, converting }: PdfViewerProps) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const pageElementsRef = useRef<Array<HTMLElement | null>>([]);
  const textCacheRef = useRef<Map<number, string>>(new Map());
  const pendingRestoreRef = useRef<number | null>(null);
  const previousScaleRef = useRef(PDF_DEFAULT_SCALE);
  const [pdfDocument, setPdfDocument] = useState<PDFDocumentProxy | null>(null);
  const [pageSize, setPageSize] = useState<PdfPageSize>(DEFAULT_PAGE_SIZE);
  const [pageCount, setPageCount] = useState(0);
  const [pageNum, setPageNum] = useState(1);
  const [scale, setScale] = useState(PDF_DEFAULT_SCALE);
  const [error, setError] = useState('');
  const [searchQuery, setSearchQuery] = useState('');
  const [searchMatches, setSearchMatches] = useState<number[]>([]);
  const [searchIndex, setSearchIndex] = useState(-1);
  const [searching, setSearching] = useState(false);
  const [searchError, setSearchError] = useState('');

  /* 加载文档并读取第一页尺寸，其他页面先以同尺寸占位、进入视口附近后再渲染。 */
  useEffect(() => {
    let cancelled = false;
    let task: ReturnType<typeof getDocument> | null = null;
    setError('');
    setPdfDocument(null);
    setPageCount(0);
    setPageNum(1);
    setScale(PDF_DEFAULT_SCALE);
    setPageSize(DEFAULT_PAGE_SIZE);
    setSearchQuery('');
    setSearchMatches([]);
    setSearchIndex(-1);
    setSearching(false);
    setSearchError('');
    pageElementsRef.current = [];
    textCacheRef.current.clear();

    void (async () => {
      try {
        const response = await fetch(url);
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        const bytes = new Uint8Array(await response.arrayBuffer());
        GlobalWorkerOptions.workerSrc = workerUrl;
        task = getDocument({ data: bytes });
        const doc = await task.promise;
        if (cancelled) {
          await task.destroy();
          return;
        }

        const firstPage = await doc.getPage(1);
        const firstViewport = firstPage.getViewport({ scale: 1 });
        firstPage.cleanup();
        const saved = readSavedPosition(storageKey);
        const initialPage = clampPdfPage(saved?.page ?? 1, doc.numPages);
        const initialScale = clampPdfScale(saved?.scale ?? PDF_DEFAULT_SCALE);
        pendingRestoreRef.current = initialPage;
        previousScaleRef.current = initialScale;
        setPageSize({ width: firstViewport.width, height: firstViewport.height });
        setScale(initialScale);
        setPageNum(initialPage);
        setPdfDocument(doc);
        setPageCount(doc.numPages);
      } catch (loadError) {
        if (!cancelled) setError(loadError instanceof Error ? loadError.message : String(loadError));
      }
    })();

    return () => {
      cancelled = true;
      if (task) void task.destroy();
      pageElementsRef.current = [];
      textCacheRef.current.clear();
    };
  }, [url, storageKey]);

  const pageNumbers = useMemo(
    () => Array.from({ length: pageCount }, (_, index) => index + 1),
    [pageCount],
  );
  const renderedPages = useMemo(
    () => new Set(pdfRenderWindow(pageNum, pageCount)),
    [pageNum, pageCount],
  );

  const goToPage = useCallback((requestedPage: number, behavior: ScrollBehavior = 'smooth') => {
    const nextPage = clampPdfPage(requestedPage, pageCount);
    setPageNum(nextPage);
    window.requestAnimationFrame(() => {
      const root = scrollRef.current;
      const pageElement = pageElementsRef.current[nextPage];
      if (!root || !pageElement) return;
      const rootRect = root.getBoundingClientRect();
      const pageRect = pageElement.getBoundingClientRect();
      const top = Math.max(0, root.scrollTop + pageRect.top - rootRect.top - 12);
      root.scrollTo({ top, behavior });
    });
  }, [pageCount]);

  /* 页面占位节点全部存在；IntersectionObserver 只更新当前页，canvas 始终限制在附近五页。 */
  useEffect(() => {
    const root = scrollRef.current;
    if (!root || pageCount === 0) return;
    const visible = new Map<number, IntersectionObserverEntry>();
    const observer = new IntersectionObserver((entries) => {
      entries.forEach((entry) => {
        const number = Number((entry.target as HTMLElement).dataset.pageNumber);
        if (!Number.isFinite(number)) return;
        if (entry.isIntersecting) visible.set(number, entry);
        else visible.delete(number);
      });
      if (visible.size === 0) return;
      const rootRect = root.getBoundingClientRect();
      const focusLine = rootRect.top + Math.min(160, rootRect.height * 0.24);
      let bestPage = 1;
      let bestDistance = Number.POSITIVE_INFINITY;
      visible.forEach((entry, number) => {
        const rect = entry.boundingClientRect;
        const distance = rect.top <= focusLine && rect.bottom >= focusLine
          ? 0
          : Math.min(Math.abs(rect.top - focusLine), Math.abs(rect.bottom - focusLine));
        if (distance < bestDistance || (distance === bestDistance && number < bestPage)) {
          bestDistance = distance;
          bestPage = number;
        }
      });
      setPageNum((current) => current === bestPage ? current : bestPage);
    }, {
      root,
      threshold: [0, 0.01, 0.1, 0.25, 0.5, 0.75],
    });

    pageElementsRef.current.slice(1, pageCount + 1).forEach((element) => {
      if (element) observer.observe(element);
    });
    return () => observer.disconnect();
  }, [pdfDocument, pageCount, scale]);

  /* 恢复阅读页；缩放后把当前页重新对齐到滚动容器顶部。 */
  useEffect(() => {
    if (!pdfDocument || pageCount === 0 || pendingRestoreRef.current === null) return;
    const target = pendingRestoreRef.current;
    pendingRestoreRef.current = null;
    const frame = window.requestAnimationFrame(() => goToPage(target, 'auto'));
    return () => window.cancelAnimationFrame(frame);
  }, [pdfDocument, pageCount, goToPage]);

  useEffect(() => {
    if (!pdfDocument || pageCount === 0 || previousScaleRef.current === scale) return;
    previousScaleRef.current = scale;
    const frame = window.requestAnimationFrame(() => goToPage(pageNum, 'auto'));
    return () => window.cancelAnimationFrame(frame);
  }, [pdfDocument, pageCount, pageNum, scale, goToPage]);

  /* 位置记忆：滚动产生的当前页与缩放值都会持久化。 */
  useEffect(() => {
    if (!storageKey || pageCount === 0) return;
    try {
      localStorage.setItem(PDF_POS_PREFIX + storageKey, JSON.stringify({ page: pageNum, scale }));
    } catch {
      /* 存储不可用（隐私模式等）静默降级 */
    }
  }, [storageKey, pageNum, scale, pageCount]);

  /* 每页 textContent 只提取一次；查询命中后滚动到对应页面。 */
  useEffect(() => {
    const needle = searchQuery.trim().toLocaleLowerCase();
    setSearchMatches([]);
    setSearchIndex(-1);
    setSearchError('');
    if (!pdfDocument || pageCount === 0 || !needle) {
      setSearching(false);
      return;
    }

    let cancelled = false;
    setSearching(true);
    const timer = window.setTimeout(() => {
      void (async () => {
        const matches: number[] = [];
        for (let number = 1; number <= pageCount; number += 1) {
          if (cancelled) return;
          let text = textCacheRef.current.get(number);
          if (text === undefined) {
            const page = await pdfDocument.getPage(number);
            const textContent = await page.getTextContent();
            text = textContent.items.map((item) => ('str' in item ? item.str : '')).join(' ');
            textCacheRef.current.set(number, text);
          }
          if (text.toLocaleLowerCase().includes(needle)) matches.push(number);
        }
        if (cancelled) return;
        setSearchMatches(matches);
        setSearchIndex(matches.length > 0 ? 0 : -1);
        setSearching(false);
        if (matches.length > 0) goToPage(matches[0], 'auto');
      })().catch(() => {
        if (!cancelled) {
          setSearchMatches([]);
          setSearchIndex(-1);
          setSearching(false);
          setSearchError('PDF 正文检索失败');
        }
      });
    }, 180);

    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [searchQuery, pdfDocument, pageCount, goToPage]);

  useEffect(() => {
    const index = searchMatches.indexOf(pageNum);
    if (index >= 0) setSearchIndex(index);
  }, [pageNum, searchMatches]);

  const moveSearch = (direction: -1 | 1) => {
    if (searchMatches.length === 0) return;
    const current = searchIndex >= 0 ? searchIndex : 0;
    const next = (current + direction + searchMatches.length) % searchMatches.length;
    setSearchIndex(next);
    goToPage(searchMatches[next]);
  };

  if (error) {
    return <p className="reader__empty reader__empty--error">PDF 渲染失败：{error}</p>;
  }

  return (
    <div className="pdfviewer">
      <header className="pdfviewer__bar">
        <div className="pdfviewer__pager">
          <button
            type="button"
            className="btn btn--sm"
            disabled={pageNum <= 1}
            onClick={() => goToPage(pageNum - 1)}
          >
            上一页
          </button>
          <span className="pdfviewer__pageno">{pageNum} / {pageCount || '…'}</span>
          <button
            type="button"
            className="btn btn--sm"
            disabled={pageNum >= pageCount}
            onClick={() => goToPage(pageNum + 1)}
          >
            下一页
          </button>
          <span className="pdfviewer__flow">连续滚动</span>
        </div>
        <div className="pdfviewer__search" role="search" aria-busy={searching}>
          <input
            className="input pdfviewer__search-input"
            type="search"
            aria-label="搜索 PDF 正文"
            placeholder="搜索 PDF 正文…"
            value={searchQuery}
            disabled={!pdfDocument}
            onChange={(event) => setSearchQuery(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === 'Enter') {
                event.preventDefault();
                if (!searching) moveSearch(event.shiftKey ? -1 : 1);
              } else if (event.key === 'Escape') {
                setSearchQuery('');
              }
            }}
          />
          <button
            type="button"
            className="btn btn--sm"
            aria-label="上一个 PDF 搜索结果"
            disabled={searching || searchMatches.length === 0}
            onClick={() => moveSearch(-1)}
          >
            ↑
          </button>
          <button
            type="button"
            className="btn btn--sm"
            aria-label="下一个 PDF 搜索结果"
            disabled={searching || searchMatches.length === 0}
            onClick={() => moveSearch(1)}
          >
            ↓
          </button>
          <output
            className={`pdfviewer__search-count${searchError ? ' pdfviewer__search-count--error' : ''}`}
            aria-live="polite"
          >
            {searchError
              ? searchError
              : searching
              ? '检索中…'
              : `${searchIndex >= 0 ? searchIndex + 1 : 0} / ${searchMatches.length}`}
          </output>
        </div>
        <div className="pdfviewer__zoom">
          <button
            type="button"
            className="btn btn--sm"
            aria-label="缩小"
            onClick={() => setScale((value) => clampPdfScale(Number((value - 0.15).toFixed(2))))}
          >
            −
          </button>
          <span className="pdfviewer__pageno">{Math.round(scale * 100)}%</span>
          <button
            type="button"
            className="btn btn--sm"
            aria-label="放大"
            onClick={() => setScale((value) => clampPdfScale(Number((value + 0.15).toFixed(2))))}
          >
            +
          </button>
        </div>
        {onConvert && (
          <button
            type="button"
            className="btn btn--sm pdfviewer__convert"
            onClick={onConvert}
            disabled={converting}
          >
            {converting ? 'OCR 进行中…' : 'PDF 转 Markdown'}
          </button>
        )}
        <span className="pdfviewer__hint">按需渲染附近页面 · 选中文字即可划词翻译</span>
      </header>
      <div
        ref={scrollRef}
        className="pdfviewer__scroll"
        role="region"
        aria-label="PDF 连续阅读区域"
        aria-busy={!pdfDocument}
        tabIndex={0}
      >
        {!pdfDocument ? (
          <p className="pdfviewer__loading" role="status">正在解析 PDF…</p>
        ) : (
          <div className="pdfviewer__pages">
            {pageNumbers.map((number) => (
              <PdfPageCanvas
                key={`${url}-${number}`}
                document={pdfDocument}
                pageNumber={number}
                pageCount={pageCount}
                scale={scale}
                estimatedSize={pageSize}
                shouldRender={renderedPages.has(number)}
                active={pageNum === number}
                searchQuery={searchQuery}
                onElement={(page, element) => {
                  pageElementsRef.current[page] = element;
                }}
              />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
