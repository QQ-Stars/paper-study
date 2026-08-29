import { useEffect, useRef, useState } from 'react';

import {
  TextLayer,
  type PDFDocumentProxy,
  type PDFPageProxy,
  type RenderTask,
} from 'pdfjs-dist';

export type PdfPageSize = {
  width: number;
  height: number;
};

interface PdfPageCanvasProps {
  document: PDFDocumentProxy;
  pageNumber: number;
  pageCount: number;
  scale: number;
  estimatedSize: PdfPageSize;
  shouldRender: boolean;
  active: boolean;
  searchQuery: string;
  onElement: (pageNumber: number, element: HTMLElement | null) => void;
}

function highlightTextLayer(container: HTMLDivElement | null, query: string) {
  if (!container) return;
  const needle = query.trim().toLocaleLowerCase();
  container.querySelectorAll('span').forEach((span) => {
    const matched = !!needle && (span.textContent ?? '').toLocaleLowerCase().includes(needle);
    span.classList.toggle('pdfviewer__hl', matched);
  });
}

function releaseCanvas(canvas: HTMLCanvasElement | null, textLayer: HTMLDivElement | null) {
  if (canvas) {
    canvas.width = 1;
    canvas.height = 1;
    canvas.style.width = '1px';
    canvas.style.height = '1px';
  }
  if (textLayer) textLayer.replaceChildren();
}

export function PdfPageCanvas({
  document,
  pageNumber,
  pageCount,
  scale,
  estimatedSize,
  shouldRender,
  active,
  searchQuery,
  onElement,
}: PdfPageCanvasProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const textLayerRef = useRef<HTMLDivElement>(null);
  const searchQueryRef = useRef(searchQuery);
  const [nativeSize, setNativeSize] = useState(estimatedSize);
  const [status, setStatus] = useState<'idle' | 'loading' | 'ready' | 'error'>('idle');
  const [error, setError] = useState('');

  searchQueryRef.current = searchQuery;

  useEffect(() => {
    highlightTextLayer(textLayerRef.current, searchQuery);
  }, [searchQuery]);

  useEffect(() => {
    const canvas = canvasRef.current;
    const textLayerElement = textLayerRef.current;
    if (!shouldRender) {
      releaseCanvas(canvas, textLayerElement);
      setStatus('idle');
      setError('');
      return;
    }

    let cancelled = false;
    let renderTask: RenderTask | null = null;
    let page: PDFPageProxy | null = null;
    let textLayer: TextLayer | null = null;
    setStatus('loading');
    setError('');

    void (async () => {
      page = await document.getPage(pageNumber);
      if (cancelled || !canvas || !textLayerElement) return;

      const nativeViewport = page.getViewport({ scale: 1 });
      setNativeSize({ width: nativeViewport.width, height: nativeViewport.height });
      const viewport = page.getViewport({ scale });
      const outputScale = Math.min(2, window.devicePixelRatio || 1);
      const context = canvas.getContext('2d');
      if (!context) throw new Error('无法创建 PDF 画布');

      canvas.width = Math.max(1, Math.floor(viewport.width * outputScale));
      canvas.height = Math.max(1, Math.floor(viewport.height * outputScale));
      canvas.style.width = `${Math.floor(viewport.width)}px`;
      canvas.style.height = `${Math.floor(viewport.height)}px`;
      renderTask = page.render({
        canvas,
        canvasContext: context,
        viewport,
        transform: outputScale !== 1 ? [outputScale, 0, 0, outputScale, 0, 0] : undefined,
      });
      await renderTask.promise;
      if (cancelled) return;

      textLayerElement.replaceChildren();
      textLayerElement.style.width = `${Math.floor(viewport.width)}px`;
      textLayerElement.style.height = `${Math.floor(viewport.height)}px`;
      textLayer = new TextLayer({
        textContentSource: page.streamTextContent(),
        container: textLayerElement,
        viewport,
      });
      await textLayer.render();
      if (cancelled) return;

      highlightTextLayer(textLayerElement, searchQueryRef.current);
      setStatus('ready');
    })().catch((renderError: unknown) => {
      const name = (renderError as { name?: string })?.name;
      if (cancelled || name === 'RenderingCancelledException') return;
      setStatus('error');
      setError(renderError instanceof Error ? renderError.message : String(renderError));
    });

    return () => {
      cancelled = true;
      renderTask?.cancel();
      textLayer?.cancel();
      page?.cleanup();
    };
  }, [document, pageNumber, scale, shouldRender]);

  const width = Math.max(1, Math.floor(nativeSize.width * scale));
  const height = Math.max(1, Math.floor(nativeSize.height * scale));

  return (
    <section
      ref={(element) => onElement(pageNumber, element)}
      className={`pdfviewer__page-shell${active ? ' pdfviewer__page-shell--active' : ''}`}
      data-page-number={pageNumber}
      aria-label={`PDF 第 ${pageNumber} 页，共 ${pageCount} 页`}
      aria-current={active ? 'page' : undefined}
    >
      <span className="pdfviewer__page-label">第 {pageNumber} 页</span>
      <div
        className="pdfviewer__page"
        style={{ width: `${width}px`, height: `${height}px` }}
        aria-busy={status === 'loading'}
      >
        <canvas ref={canvasRef} aria-hidden="true" />
        <div ref={textLayerRef} className="pdfviewer__textlayer" aria-hidden="false" />
        {status !== 'ready' && (
          <div className={`pdfviewer__page-placeholder${status === 'error' ? ' pdfviewer__page-placeholder--error' : ''}`}>
            {status === 'error' ? `第 ${pageNumber} 页加载失败：${error}` : shouldRender ? `正在渲染第 ${pageNumber} 页…` : `第 ${pageNumber} 页`}
          </div>
        )}
      </div>
    </section>
  );
}
