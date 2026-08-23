import { useEffect, useRef, useState } from 'react';

import {
  getDocument,
  GlobalWorkerOptions,
  TextLayer,
  type PDFDocumentLoadingTask,
  type PDFDocumentProxy,
  type RenderTask,
} from 'pdfjs-dist';
import workerUrl from 'pdfjs-dist/build/pdf.worker.min.mjs?url';

/* PDF.js 阅读视图：canvas 渲染 + textLayer 可选中文本层。
 * textLayer 的选中事件会冒泡到外层，供 SelectionTranslate 触发划词翻译。 */

interface PdfViewerProps {
  url: string;
  /* 提供后按论文记忆上次阅读位置（页码/缩放，localStorage） */
  storageKey?: string;
  onConvert?: () => void;
  converting?: boolean;
}

const PDF_POS_PREFIX = 'paper-study:pdf-pos:';

function readSavedPosition(key: string | undefined): { page: number; scale: number } | null {
  if (!key) return null;
  try {
    const raw = localStorage.getItem(PDF_POS_PREFIX + key);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as { page?: number; scale?: number };
    if (typeof parsed.page !== 'number' || typeof parsed.scale !== 'number') return null;
    return { page: parsed.page, scale: Math.min(2.2, Math.max(0.6, parsed.scale)) };
  } catch {
    return null;
  }
}

function highlightTextLayer(container: HTMLDivElement | null, query: string) {
  if (!container) return;
  const needle = query.trim().toLocaleLowerCase();
  container.querySelectorAll('span').forEach((span) => {
    const matched = !!needle && (span.textContent ?? '').toLocaleLowerCase().includes(needle);
    span.classList.toggle('pdfviewer__hl', matched);
  });
}

export function PdfViewer({ url, storageKey, onConvert, converting }: PdfViewerProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const textLayerRef = useRef<HTMLDivElement>(null);
  const docRef = useRef<PDFDocumentProxy | null>(null);
  const taskRef = useRef<PDFDocumentLoadingTask | null>(null);
  const textCacheRef = useRef<Map<number, string>>(new Map());
  const searchQueryRef = useRef('');
  const [pageCount, setPageCount] = useState(0);
  const [pageNum, setPageNum] = useState(1);
  const [scale, setScale] = useState(1.15);
  const [error, setError] = useState('');
  const [searchQuery, setSearchQuery] = useState('');
  const [searchMatches, setSearchMatches] = useState<number[]>([]);
  const [searchIndex, setSearchIndex] = useState(-1);
  const [searching, setSearching] = useState(false);
  const [searchError, setSearchError] = useState('');

  /* 加载文档 */
  useEffect(() => {
    let cancelled = false;
    setError('');
    setPageNum(1);
    setSearchQuery('');
    setSearchMatches([]);
    setSearchIndex(-1);
    setSearching(false);
    setSearchError('');
    textCacheRef.current.clear();
    (async () => {
      try {
        const response = await fetch(url);
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        const bytes = new Uint8Array(await response.arrayBuffer());
        GlobalWorkerOptions.workerSrc = workerUrl;
        const task = getDocument({ data: bytes });
        const doc = await task.promise;
        if (cancelled) {
          await task.destroy();
          return;
        }
        taskRef.current = task;
        docRef.current = doc;
        setPageCount(doc.numPages);
        /* 恢复上次阅读位置（限幅在有效页码内） */
        const saved = readSavedPosition(storageKey);
        if (saved) {
          setPageNum(Math.min(doc.numPages, Math.max(1, saved.page)));
          setScale(saved.scale);
        }
      } catch (loadError) {
        if (!cancelled) {
          setError(loadError instanceof Error ? loadError.message : String(loadError));
        }
      }
    })();
    return () => {
      cancelled = true;
      void taskRef.current?.destroy();
      taskRef.current = null;
      docRef.current = null;
      textCacheRef.current.clear();
    };
  }, [url]); // eslint-disable-line react-hooks/exhaustive-deps

  /* 位置记忆：页码/缩放变化时持久化（pageCount=0 即文档未加载时不写） */
  useEffect(() => {
    if (!storageKey || pageCount === 0) return;
    try {
      localStorage.setItem(PDF_POS_PREFIX + storageKey, JSON.stringify({ page: pageNum, scale }));
    } catch {
      /* 存储不可用（隐私模式等）静默降级 */
    }
  }, [storageKey, pageNum, scale, pageCount]);

  /* 渲染当前页（canvas + textLayer） */
  useEffect(() => {
    const doc = docRef.current;
    if (!doc || pageCount === 0) return;
    let cancelled = false;
    let renderTask: RenderTask | null = null;
    (async () => {
      const page = await doc.getPage(pageNum);
      if (cancelled) return;
      const viewport = page.getViewport({ scale });
      const canvas = canvasRef.current;
      const textLayerEl = textLayerRef.current;
      if (!canvas || !textLayerEl) return;
      const outputScale = window.devicePixelRatio || 1;
      canvas.width = Math.floor(viewport.width * outputScale);
      canvas.height = Math.floor(viewport.height * outputScale);
      canvas.style.width = `${Math.floor(viewport.width)}px`;
      canvas.style.height = `${Math.floor(viewport.height)}px`;
      renderTask = page.render({
        canvas,
        canvasContext: canvas.getContext('2d') as CanvasRenderingContext2D,
        viewport,
        transform: outputScale !== 1 ? [outputScale, 0, 0, outputScale, 0, 0] : undefined,
      });
      await renderTask.promise;
      if (cancelled) return;
      textLayerEl.innerHTML = '';
      textLayerEl.style.width = `${Math.floor(viewport.width)}px`;
      textLayerEl.style.height = `${Math.floor(viewport.height)}px`;
      const textLayer = new TextLayer({
        textContentSource: page.streamTextContent(),
        container: textLayerEl,
        viewport,
      });
      await textLayer.render();
      if (!cancelled) highlightTextLayer(textLayerEl, searchQueryRef.current);
    })().catch((renderError: unknown) => {
      const name = (renderError as { name?: string })?.name;
      if (!cancelled && name !== 'RenderingCancelledException') {
        setError(renderError instanceof Error ? renderError.message : String(renderError));
      }
    });
    return () => {
      cancelled = true;
      renderTask?.cancel();
    };
  }, [pageNum, scale, pageCount]);

  /* 每页 textContent 只提取一次；查询按页命中并跳转，避免重复解析 PDF。 */
  useEffect(() => {
    searchQueryRef.current = searchQuery;
    highlightTextLayer(textLayerRef.current, searchQuery);

    const doc = docRef.current;
    const needle = searchQuery.trim().toLocaleLowerCase();
    setSearchMatches([]);
    setSearchIndex(-1);
    setSearchError('');
    if (!doc || pageCount === 0 || !needle) {
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
            const page = await doc.getPage(number);
            const textContent = await page.getTextContent();
            text = textContent.items
              .map((item) => ('str' in item ? item.str : ''))
              .join(' ');
            textCacheRef.current.set(number, text);
          }
          if (text.toLocaleLowerCase().includes(needle)) matches.push(number);
        }
        if (cancelled) return;
        setSearchMatches(matches);
        setSearchIndex(matches.length > 0 ? 0 : -1);
        setSearching(false);
        if (matches.length > 0) setPageNum(matches[0]);
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
  }, [searchQuery, pageCount]);

  useEffect(() => {
    const index = searchMatches.indexOf(pageNum);
    if (index >= 0) setSearchIndex(index);
  }, [pageNum, searchMatches]);

  const moveSearch = (direction: -1 | 1) => {
    if (searchMatches.length === 0) return;
    const current = searchIndex >= 0 ? searchIndex : 0;
    const next = (current + direction + searchMatches.length) % searchMatches.length;
    setSearchIndex(next);
    setPageNum(searchMatches[next]);
  };

  if (error) {
    return (
      <p className="reader__empty reader__empty--error">PDF 渲染失败：{error}</p>
    );
  }

  return (
    <div className="pdfviewer">
      <header className="pdfviewer__bar">
        <div className="pdfviewer__pager">
          <button
            type="button"
            className="btn btn--sm"
            disabled={pageNum <= 1}
            onClick={() => setPageNum((value) => Math.max(1, value - 1))}
          >
            上一页
          </button>
          <span className="pdfviewer__pageno">
            {pageNum} / {pageCount || '…'}
          </span>
          <button
            type="button"
            className="btn btn--sm"
            disabled={pageNum >= pageCount}
            onClick={() => setPageNum((value) => Math.min(pageCount, value + 1))}
          >
            下一页
          </button>
        </div>
        <div className="pdfviewer__search" role="search" aria-busy={searching}>
          <input
            className="input pdfviewer__search-input"
            type="search"
            aria-label="搜索 PDF 正文"
            placeholder="搜索 PDF 正文…"
            value={searchQuery}
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
            onClick={() => setScale((value) => Math.max(0.6, Number((value - 0.15).toFixed(2))))}
          >
            −
          </button>
          <span className="pdfviewer__pageno">{Math.round(scale * 100)}%</span>
          <button
            type="button"
            className="btn btn--sm"
            aria-label="放大"
            onClick={() => setScale((value) => Math.min(2.2, Number((value + 0.15).toFixed(2))))}
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
        <span className="pdfviewer__hint">选中正文文字即可划词翻译</span>
      </header>
      <div className="pdfviewer__scroll">
        <div className="pdfviewer__page">
          <canvas ref={canvasRef} />
          <div ref={textLayerRef} className="pdfviewer__textlayer" aria-hidden="false" />
        </div>
      </div>
    </div>
  );
}
