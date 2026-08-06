import type {
  PDFDocumentLoadingTask,
  PDFDocumentProxy,
  PDFPageProxy,
} from 'pdfjs-dist';

import type {
  PdfDocumentHandle,
  PdfDocumentLoadingHandle,
  PdfPageRenderHandle,
  PdfPageSurface,
} from './PdfReaderSession';

export const PDFJS_WORKER_URL = new URL(
  '../../../node_modules/pdfjs-dist/build/pdf.worker.min.mjs',
  import.meta.url,
).toString();

type PdfJsRuntime = typeof import('pdfjs-dist');
type PdfJsDocumentSource = NonNullable<
  Parameters<PdfJsRuntime['getDocument']>[0]
>;
type HardenedDocumentInit = PdfJsDocumentSource & {
  isEvalSupported: false;
};

let runtimePromise: Promise<PdfJsRuntime> | null = null;

function loadPdfJs(): Promise<PdfJsRuntime> {
  runtimePromise ??= import('pdfjs-dist');
  return runtimePromise;
}

function abortError(signal: AbortSignal): Error {
  return signal.reason instanceof Error
    ? signal.reason
    : new DOMException('The PDF page operation was aborted', 'AbortError');
}

function cancelSafely(cancel: () => void): void {
  try {
    cancel();
  } catch {
    // Cancellation is best-effort after a task has already settled.
  }
}

function createPageRenderHandle(
  runtime: PdfJsRuntime,
  page: PDFPageProxy,
  surface: PdfPageSurface,
  zoom: number,
  signal: AbortSignal,
): PdfPageRenderHandle {
  const viewport = page.getViewport({ scale: zoom });
  const outputScale = Math.max(1, globalThis.devicePixelRatio || 1);
  if (surface.target instanceof HTMLElement) {
    surface.target.style.setProperty('--scale-factor', String(viewport.scale));
    surface.target.style.setProperty('--user-unit', String(viewport.userUnit));
    surface.target.style.setProperty(
      '--total-scale-factor',
      'calc(var(--scale-factor) * var(--user-unit))',
    );
    surface.target.style.setProperty('--scale-round-x', '1px');
    surface.target.style.setProperty('--scale-round-y', '1px');
  }
  surface.canvas.width = Math.max(1, Math.floor(viewport.width * outputScale));
  surface.canvas.height = Math.max(1, Math.floor(viewport.height * outputScale));
  surface.canvas.style.width = `${viewport.width}px`;
  surface.canvas.style.height = `${viewport.height}px`;
  surface.textLayer.replaceChildren();

  const renderTask = page.render({
    canvas: surface.canvas,
    viewport,
    transform:
      outputScale === 1
        ? undefined
        : [outputScale, 0, 0, outputScale, 0, 0],
  });
  const textLayer = new runtime.TextLayer({
    textContentSource: page.streamTextContent(),
    container: surface.textLayer,
    viewport,
  });
  const textPromise = textLayer.render();
  let cancelled = false;
  let disposed = false;

  const cancel = () => {
    if (cancelled) return;
    cancelled = true;
    cancelSafely(() => renderTask.cancel());
    cancelSafely(() => textLayer.cancel());
  };
  const abort = () => cancel();
  signal.addEventListener('abort', abort, { once: true });

  return {
    width: viewport.width,
    height: viewport.height,
    completed: Promise.all([renderTask.promise, textPromise]).then(
      () => undefined,
    ),
    cancel,
    dispose() {
      if (disposed) return;
      disposed = true;
      signal.removeEventListener('abort', abort);
      cancel();
      page.cleanup();
    },
  };
}

function wrapDocument(
  runtime: PdfJsRuntime,
  proxy: PDFDocumentProxy,
  destroy: () => Promise<void>,
): PdfDocumentHandle {
  return {
    pageCount: proxy.numPages,
    destroy,
    async renderPage(surface, zoom, signal) {
      if (signal.aborted) throw abortError(signal);
      const page = await proxy.getPage(surface.pageNumber);
      if (signal.aborted) {
        page.cleanup();
        throw abortError(signal);
      }
      try {
        return createPageRenderHandle(runtime, page, surface, zoom, signal);
      } catch (error) {
        page.cleanup();
        throw error;
      }
    },
  };
}

function onceAsync(operation: () => Promise<void>): () => Promise<void> {
  let result: Promise<void> | null = null;
  return () => {
    result ??= Promise.resolve(operation());
    return result;
  };
}

export async function createPdfJsLoadingTask(
  bytes: ArrayBuffer,
): Promise<PdfDocumentLoadingHandle> {
  const runtime = await loadPdfJs();
  runtime.GlobalWorkerOptions.workerSrc = PDFJS_WORKER_URL;
  const options: HardenedDocumentInit = {
    data: new Uint8Array(bytes),
    isEvalSupported: false,
  };
  const loadingTask: PDFDocumentLoadingTask = runtime.getDocument(options);
  const destroy = onceAsync(() => loadingTask.destroy());

  return {
    destroy,
    promise: loadingTask.promise.then((proxy) =>
      wrapDocument(runtime, proxy, destroy),
    ),
  };
}
