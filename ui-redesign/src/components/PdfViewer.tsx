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
  onConvert?: () => void;
  converting?: boolean;
}

export function PdfViewer({ url, onConvert, converting }: PdfViewerProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const textLayerRef = useRef<HTMLDivElement>(null);
  const docRef = useRef<PDFDocumentProxy | null>(null);
  const taskRef = useRef<PDFDocumentLoadingTask | null>(null);
  const [pageCount, setPageCount] = useState(0);
  const [pageNum, setPageNum] = useState(1);
  const [scale, setScale] = useState(1.15);
  const [error, setError] = useState('');

  /* 加载文档 */
  useEffect(() => {
    let cancelled = false;
    setError('');
    setPageNum(1);
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
    };
  }, [url]);

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
