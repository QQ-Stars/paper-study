const workerOptions = { workerSrc: '' };
const getDocument = vi.fn();
const textLayerCancel = vi.fn();
const textLayerRender = vi.fn(async () => undefined);
const TextLayer = vi.fn(function TextLayerMock() {
  return {
    cancel: textLayerCancel,
    render: textLayerRender,
  };
});

vi.mock('pdfjs-dist', () => ({
  getDocument,
  GlobalWorkerOptions: workerOptions,
  TextLayer,
}));

import {
  PDFJS_WORKER_URL,
  createPdfJsLoadingTask,
} from './pdfJsAdapter';

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return { promise, resolve, reject };
}

it('configures the Vite worker URL and disables PDF evaluation', async () => {
  const destroy = vi.fn(async () => undefined);
  const documentProxy = {
    numPages: 2,
    getPage: vi.fn(),
    loadingTask: { destroy },
  };
  getDocument.mockReturnValue({
    promise: Promise.resolve(documentProxy),
    destroy,
  });

  const loadingTask = await createPdfJsLoadingTask(new ArrayBuffer(4));
  const document = await loadingTask.promise;

  expect(PDFJS_WORKER_URL).toMatch(/pdf\.worker\.min\.mjs/);
  expect(PDFJS_WORKER_URL).not.toMatch(/(?:public|legacy)/i);
  expect(workerOptions.workerSrc).toBe(PDFJS_WORKER_URL);
  expect(getDocument).toHaveBeenCalledWith(
    expect.objectContaining({
      data: expect.any(Uint8Array),
      isEvalSupported: false,
    }),
  );
  expect(document.pageCount).toBe(2);

  await document.destroy();
  expect(destroy).toHaveBeenCalledOnce();
});

it('uses one viewport for canvas and text and releases both page tasks', async () => {
  const renderCancel = vi.fn();
  const renderPromise = Promise.resolve();
  const viewport = { width: 320, height: 480, scale: 1.5, userUnit: 2 };
  const page = {
    cleanup: vi.fn(),
    getViewport: vi.fn(() => viewport),
    render: vi.fn(() => ({ cancel: renderCancel, promise: renderPromise })),
    streamTextContent: vi.fn(() => new ReadableStream()),
  };
  const destroy = vi.fn(async () => undefined);
  getDocument.mockReturnValue({
    promise: Promise.resolve({
      numPages: 1,
      getPage: vi.fn(async () => page),
      loadingTask: { destroy },
    }),
    destroy,
  });
  const document = await (
    await createPdfJsLoadingTask(new ArrayBuffer(4))
  ).promise;
  const surface = {
    pageNumber: 1,
    target: globalThis.document.createElement('article'),
    canvas: globalThis.document.createElement('canvas'),
    textLayer: globalThis.document.createElement('div'),
  };

  const render = await document.renderPage(
    surface,
    1.5,
    new AbortController().signal,
  );
  await render.completed;

  expect(page.getViewport).toHaveBeenCalledWith({ scale: 1.5 });
  expect(page.render).toHaveBeenCalledWith(
    expect.objectContaining({ canvas: surface.canvas, viewport }),
  );
  expect(TextLayer).toHaveBeenCalledWith(
    expect.objectContaining({ container: surface.textLayer, viewport }),
  );
  expect(surface.canvas.width).toBe(320);
  expect(surface.canvas.height).toBe(480);
  expect(surface.target.style.getPropertyValue('--scale-factor')).toBe('1.5');
  expect(surface.target.style.getPropertyValue('--user-unit')).toBe('2');
  expect(surface.target.style.getPropertyValue('--total-scale-factor')).toBe(
    'calc(var(--scale-factor) * var(--user-unit))',
  );

  render.cancel();
  render.dispose();
  expect(renderCancel).toHaveBeenCalledOnce();
  expect(textLayerCancel).toHaveBeenCalledOnce();
  expect(page.cleanup).toHaveBeenCalledOnce();
});

it('lets a destroyed loading owner absorb a late document without double destroy', async () => {
  const proxy = deferred<{
    numPages: number;
    getPage: ReturnType<typeof vi.fn>;
  }>();
  const destroy = vi.fn(async () => undefined);
  getDocument.mockReturnValue({ promise: proxy.promise, destroy });

  const loading = await createPdfJsLoadingTask(new ArrayBuffer(4));
  await loading.destroy();
  proxy.resolve({ numPages: 1, getPage: vi.fn() });
  const document = await loading.promise;
  await document.destroy();
  await loading.destroy();

  expect(destroy).toHaveBeenCalledOnce();
});
