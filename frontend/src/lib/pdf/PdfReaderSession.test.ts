import {
  PdfReaderSession,
  type PdfDocumentHandle,
  type PdfDocumentLoadingHandle,
  type PdfIntersectionObserverPort,
  type PdfPageRenderHandle,
  type PdfResizeObserverPort,
} from './PdfReaderSession';

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return { promise, resolve, reject };
}

function documentHandle(pageCount = 3): PdfDocumentHandle {
  return {
    pageCount,
    destroy: vi.fn(async () => undefined),
    renderPage: vi.fn(),
  };
}

function loadingHandle(
  promise: Promise<PdfDocumentHandle>,
): PdfDocumentLoadingHandle {
  return {
    promise,
    destroy: vi.fn(async () => undefined),
  };
}

function pageRenderHandle(): PdfPageRenderHandle {
  return {
    width: 640,
    height: 900,
    completed: Promise.resolve(),
    cancel: vi.fn(),
    dispose: vi.fn(),
  };
}

class IntersectionObserverFake implements PdfIntersectionObserverPort {
  readonly observed = new Set<Element>();
  readonly disconnect = vi.fn(() => this.observed.clear());
  readonly unobserve = vi.fn((target: Element) => this.observed.delete(target));

  constructor(
    private readonly notify: (target: Element, isIntersecting: boolean) => void,
  ) {}

  observe = vi.fn((target: Element) => this.observed.add(target));

  emit(target: Element, isIntersecting: boolean) {
    this.notify(target, isIntersecting);
  }
}

class ResizeObserverFake implements PdfResizeObserverPort {
  readonly observed = new Set<Element>();
  readonly disconnect = vi.fn(() => this.observed.clear());
  readonly observe = vi.fn((target: Element) => this.observed.add(target));
}

it('switches an unresolved loading owner without destroying a late document twice', async () => {
  const firstDocument = documentHandle();
  const secondDocument = documentHandle(7);
  const firstDocumentDeferred = deferred<PdfDocumentHandle>();
  const firstLoadingTask = loadingHandle(firstDocumentDeferred.promise);
  const secondLoadingTask = loadingHandle(Promise.resolve(secondDocument));
  const createLoadingTask = vi
    .fn()
    .mockReturnValueOnce(firstLoadingTask)
    .mockReturnValueOnce(secondLoadingTask);
  const session = new PdfReaderSession({
    fetchBytes: vi.fn(async () => new ArrayBuffer(8)),
    createLoadingTask,
  });

  const firstOpen = session.open('paper-a');
  await vi.waitFor(() => expect(createLoadingTask).toHaveBeenCalledTimes(1));

  const secondOpen = session.open('paper-b');
  await vi.waitFor(() => expect(firstLoadingTask.destroy).toHaveBeenCalledOnce());
  await secondOpen;

  firstDocumentDeferred.resolve(firstDocument);
  await firstOpen;

  expect(firstDocument.destroy).not.toHaveBeenCalled();
  expect(session.getSnapshot()).toMatchObject({
    status: 'ready',
    paperId: 'paper-b',
    generation: 2,
    pageCount: 7,
  });

  await session.dispose();
  expect(secondDocument.destroy).toHaveBeenCalledOnce();
  expect(secondLoadingTask.destroy).not.toHaveBeenCalled();
});

it('rebuilds only visible page resources on zoom and releases every owner once', async () => {
  const firstRender = pageRenderHandle();
  const secondRender = pageRenderHandle();
  const pdfDocument = documentHandle();
  vi.mocked(pdfDocument.renderPage)
    .mockResolvedValueOnce(firstRender)
    .mockImplementationOnce(async (surface, zoom) => {
      expect(surface.canvas.width).toBe(0);
      expect(surface.canvas.height).toBe(0);
      expect(surface.textLayer).toBeEmptyDOMElement();
      expect(zoom).toBe(2);
      return secondRender;
    });
  const intersections: IntersectionObserverFake[] = [];
  const resizes: ResizeObserverFake[] = [];
  const anchor = {
    pageNumber: 1,
    relativePageOffset: 0.25,
    viewportRatio: 0,
  };
  const anchorPort = {
    capture: vi.fn(() => anchor),
    restore: vi.fn(),
  };
  const session = new PdfReaderSession({
    fetchBytes: vi.fn(async () => new ArrayBuffer(8)),
    createLoadingTask: vi.fn(() => loadingHandle(Promise.resolve(pdfDocument))),
    createIntersectionObserver: (notify) => {
      const observer = new IntersectionObserverFake(notify);
      intersections.push(observer);
      return observer;
    },
    createResizeObserver: () => {
      const observer = new ResizeObserverFake();
      resizes.push(observer);
      return observer;
    },
  });
  await session.open('paper-a');

  const viewport = document.createElement('div');
  const target = document.createElement('article');
  const canvas = document.createElement('canvas');
  canvas.width = 640;
  canvas.height = 900;
  const textLayer = document.createElement('div');
  textLayer.append(document.createElement('span'));
  session.attachViewport(viewport, anchorPort);
  session.mountPage({ pageNumber: 1, target, canvas, textLayer });

  expect(intersections).toHaveLength(1);
  expect(resizes).toHaveLength(1);
  expect(pdfDocument.renderPage).not.toHaveBeenCalled();

  intersections[0]?.emit(target, true);
  await vi.waitFor(() =>
    expect(session.getSnapshot().pages[1]).toMatchObject({ status: 'ready' }),
  );

  await session.setZoom(2);

  expect(pdfDocument.destroy).not.toHaveBeenCalled();
  expect(firstRender.cancel).toHaveBeenCalledOnce();
  expect(firstRender.dispose).toHaveBeenCalledOnce();
  expect(intersections[0]?.disconnect).toHaveBeenCalledOnce();
  expect(intersections).toHaveLength(2);
  expect(intersections[1]?.observed.has(target)).toBe(true);
  expect(anchorPort.capture).toHaveBeenCalledOnce();
  expect(anchorPort.restore).toHaveBeenCalledWith(anchor);
  expect(session.getSnapshot()).toMatchObject({ zoom: 2, pageCount: 3 });

  await session.dispose();
  await session.dispose();

  expect(secondRender.cancel).toHaveBeenCalledOnce();
  expect(secondRender.dispose).toHaveBeenCalledOnce();
  expect(intersections[1]?.disconnect).toHaveBeenCalledOnce();
  expect(resizes[0]?.disconnect).toHaveBeenCalledOnce();
  expect(pdfDocument.destroy).toHaveBeenCalledOnce();
  expect(canvas.width).toBe(0);
  expect(canvas.height).toBe(0);
});

it('supports a StrictMode-style dispose and reopen without retaining an owner', async () => {
  const firstDocument = documentHandle();
  const secondDocument = documentHandle();
  const createLoadingTask = vi
    .fn()
    .mockReturnValueOnce(loadingHandle(Promise.resolve(firstDocument)))
    .mockReturnValueOnce(loadingHandle(Promise.resolve(secondDocument)));
  const session = new PdfReaderSession({
    fetchBytes: vi.fn(async () => new ArrayBuffer(8)),
    createLoadingTask,
  });

  await session.open('paper-a');
  await session.dispose();
  await session.open('paper-a');

  expect(session.getSnapshot()).toMatchObject({
    status: 'ready',
    paperId: 'paper-a',
    generation: 2,
  });
  expect(firstDocument.destroy).toHaveBeenCalledOnce();
  expect(secondDocument.destroy).not.toHaveBeenCalled();

  await session.dispose();
  expect(secondDocument.destroy).toHaveBeenCalledOnce();
});

it('deduplicates concurrent activation for the same page owner', async () => {
  const pendingRender = deferred<PdfPageRenderHandle>();
  const render = pageRenderHandle();
  const pdfDocument = documentHandle(1);
  vi.mocked(pdfDocument.renderPage).mockReturnValue(pendingRender.promise);
  const intersections: IntersectionObserverFake[] = [];
  const session = new PdfReaderSession({
    fetchBytes: vi.fn(async () => new ArrayBuffer(8)),
    createLoadingTask: vi.fn(() => loadingHandle(Promise.resolve(pdfDocument))),
    createIntersectionObserver: (notify) => {
      const observer = new IntersectionObserverFake(notify);
      intersections.push(observer);
      return observer;
    },
  });
  await session.open('paper-a');
  const viewport = document.createElement('div');
  const target = document.createElement('article');
  session.attachViewport(viewport);
  const unmount = session.mountPage({
    pageNumber: 1,
    target,
    canvas: document.createElement('canvas'),
    textLayer: document.createElement('div'),
  });

  intersections[0]?.emit(target, true);
  intersections[0]?.emit(target, true);
  expect(pdfDocument.renderPage).toHaveBeenCalledOnce();

  pendingRender.resolve(render);
  await vi.waitFor(() =>
    expect(session.getSnapshot().pages[1]).toMatchObject({ status: 'ready' }),
  );
  intersections[0]?.emit(target, true);
  expect(pdfDocument.renderPage).toHaveBeenCalledOnce();

  unmount();
  await session.dispose();
  expect(render.cancel).toHaveBeenCalledOnce();
  expect(render.dispose).toHaveBeenCalledOnce();
});

it('unobserves a replaced page target before transferring ownership', async () => {
  const pdfDocument = documentHandle(1);
  const intersections: IntersectionObserverFake[] = [];
  const session = new PdfReaderSession({
    fetchBytes: vi.fn(async () => new ArrayBuffer(8)),
    createLoadingTask: vi.fn(() => loadingHandle(Promise.resolve(pdfDocument))),
    createIntersectionObserver: (notify) => {
      const observer = new IntersectionObserverFake(notify);
      intersections.push(observer);
      return observer;
    },
  });
  await session.open('paper-a');
  session.attachViewport(document.createElement('div'));
  const firstTarget = document.createElement('article');
  const secondTarget = document.createElement('article');
  session.mountPage({
    pageNumber: 1,
    target: firstTarget,
    canvas: document.createElement('canvas'),
    textLayer: document.createElement('div'),
  });

  session.mountPage({
    pageNumber: 1,
    target: secondTarget,
    canvas: document.createElement('canvas'),
    textLayer: document.createElement('div'),
  });

  expect(intersections[0]?.unobserve).toHaveBeenCalledWith(firstTarget);
  expect(intersections[0]?.observed.has(firstTarget)).toBe(false);
  expect(intersections[0]?.observed.has(secondTarget)).toBe(true);
  await session.dispose();
});

it('aborts fetch on paper switch, destroys resolved documents, and clamps zoom', async () => {
  const firstFetch = deferred<ArrayBuffer>();
  let firstSignal: AbortSignal | undefined;
  const firstDocument = documentHandle();
  const secondDocument = documentHandle();
  const fetchBytes = vi
    .fn()
    .mockImplementationOnce((_paperId: string, signal: AbortSignal) => {
      firstSignal = signal;
      signal.addEventListener('abort', () => {
        firstFetch.reject(new DOMException('Aborted', 'AbortError'));
      }, { once: true });
      return firstFetch.promise;
    })
    .mockResolvedValue(new ArrayBuffer(8));
  const createLoadingTask = vi
    .fn()
    .mockReturnValueOnce(loadingHandle(Promise.resolve(secondDocument)))
    .mockReturnValueOnce(loadingHandle(Promise.resolve(firstDocument)));
  const session = new PdfReaderSession({ fetchBytes, createLoadingTask });

  const firstOpen = session.open('paper-a');
  await vi.waitFor(() => expect(firstSignal).toBeDefined());
  await session.open('paper-b');
  await firstOpen;

  expect(firstSignal?.aborted).toBe(true);
  expect(session.getSnapshot()).toMatchObject({ paperId: 'paper-b', generation: 2 });
  await session.setZoom(0.1);
  expect(session.getSnapshot().zoom).toBe(0.5);
  await session.setZoom(99);
  expect(session.getSnapshot().zoom).toBe(3);

  await session.open('paper-c');
  await vi.waitFor(() => expect(secondDocument.destroy).toHaveBeenCalledOnce());
  expect(firstDocument.destroy).not.toHaveBeenCalled();
  await session.dispose();
  expect(firstDocument.destroy).toHaveBeenCalledOnce();
});

it('does not notify an unsubscribed listener after teardown', async () => {
  const pdfDocument = documentHandle();
  const session = new PdfReaderSession({
    fetchBytes: vi.fn(async () => new ArrayBuffer(8)),
    createLoadingTask: vi.fn(() => loadingHandle(Promise.resolve(pdfDocument))),
  });
  const listener = vi.fn();
  const unsubscribe = session.subscribe(listener);

  await session.open('paper-a');
  expect(listener).toHaveBeenCalled();
  unsubscribe();
  listener.mockClear();
  await session.setZoom(1.5);
  await session.dispose();

  expect(listener).not.toHaveBeenCalled();
});
