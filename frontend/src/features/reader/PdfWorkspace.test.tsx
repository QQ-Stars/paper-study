import { StrictMode } from 'react';
import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import type { PageRectAnchor } from '../../lib/pdf/PageViewportAnchor';
import {
  PdfReaderSession,
  type PdfDocumentHandle,
  type PdfDocumentLoadingHandle,
  type PdfPageRenderHandle,
  type PdfPageSurface,
} from '../../lib/pdf/PdfReaderSession';
import { PdfSelectionController } from '../../lib/pdf/PdfSelectionController';
import type { PdfSelectionPolicyInput } from '../../lib/pdf/selectionPolicy';
import { SelectionTranslator } from '../../lib/pdf/SelectionTranslator';
import { PdfWorkspace } from './PdfWorkspace';

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return { promise, resolve, reject };
}

function renderHandle(
  surface?: PdfPageSurface,
): PdfPageRenderHandle {
  if (surface) {
    surface.canvas.width = 640;
    surface.canvas.height = 900;
  }
  return {
    width: 640,
    height: 900,
    completed: Promise.resolve(),
    cancel: vi.fn(),
    dispose: vi.fn(),
  };
}

function documentHandle(pageCount = 1): PdfDocumentHandle {
  return {
    pageCount,
    destroy: vi.fn(async () => undefined),
    renderPage: vi.fn(async (surface) => renderHandle(surface)),
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

function policyInput(text: string): PdfSelectionPolicyInput {
  return {
    pageNumber: 1,
    pageRect: { left: 0, top: 0, right: 0, bottom: 0 },
    fragments: [],
    startFragmentIndex: 0,
    nativeText: text,
  };
}

function nativeSelection(root: HTMLElement): Selection {
  return {
    anchorNode: root,
    focusNode: root,
    isCollapsed: false,
    rangeCount: 0,
    removeAllRanges: vi.fn(),
    toString: () => 'native selection',
  } as unknown as Selection;
}

it('destroys an unresolved loading task when the route paper changes', async () => {
  const firstDocument = deferred<PdfDocumentHandle>();
  const firstLoading = loadingHandle(firstDocument.promise);
  const secondPdf = documentHandle(2);
  const secondLoading = loadingHandle(Promise.resolve(secondPdf));
  const createLoadingTask = vi
    .fn()
    .mockReturnValueOnce(firstLoading)
    .mockReturnValueOnce(secondLoading);
  const session = new PdfReaderSession({
    fetchBytes: vi.fn(async () => new ArrayBuffer(8)),
    createLoadingTask,
  });
  const onGenerationChange = vi.fn();
  const view = render(
    <PdfWorkspace
      createSession={() => session}
      onGenerationChange={onGenerationChange}
      paperId="paper-a"
    />,
  );
  await waitFor(() => expect(createLoadingTask).toHaveBeenCalledOnce());

  view.rerender(
    <PdfWorkspace
      createSession={() => session}
      onGenerationChange={onGenerationChange}
      paperId="paper-b"
    />,
  );

  await waitFor(() => expect(firstLoading.destroy).toHaveBeenCalledOnce());
  await screen.findByText('共 2 页');
  expect(screen.getByLabelText('PDF 阅读工作区')).toHaveAttribute(
    'data-paper-id',
    'paper-b',
  );
  expect(onGenerationChange).toHaveBeenLastCalledWith(2);

  firstDocument.resolve(documentHandle());
  view.unmount();
  await waitFor(() => expect(secondPdf.destroy).toHaveBeenCalledOnce());
});

it('keeps buffered text while zoom cancels the old page owner', async () => {
  const pdf = documentHandle();
  const renders: PdfPageRenderHandle[] = [];
  vi.mocked(pdf.renderPage).mockImplementation(async (surface) => {
    const handle = renderHandle(surface);
    renders.push(handle);
    return handle;
  });
  const session = new PdfReaderSession({
    fetchBytes: vi.fn(async () => new ArrayBuffer(8)),
    createLoadingTask: vi.fn(() => loadingHandle(Promise.resolve(pdf))),
  });
  let selection: Selection | null = null;
  const controller = new PdfSelectionController({
    getSelection: () => selection,
    resolveSelection: () => policyInput('kept across zoom'),
    delayMs: 0,
  });
  const view = render(
    <PdfWorkspace
      createSelectionController={() => controller}
      createSession={() => session}
      paperId="paper-a"
    />,
  );
  await screen.findByRole('article', { name: '第 1 页' });
  await waitFor(() => expect(renders).toHaveLength(1));
  const viewport = screen.getByTestId('pdf-viewport');
  selection = nativeSelection(viewport);
  fireEvent.mouseUp(viewport);
  await screen.findByText('kept across zoom');

  await userEvent.click(screen.getByRole('button', { name: '放大 PDF' }));

  await waitFor(() => expect(renders).toHaveLength(2));
  expect(renders[0]?.cancel).toHaveBeenCalledOnce();
  expect(renders[0]?.dispose).toHaveBeenCalledOnce();
  expect(screen.getByText('kept across zoom')).toBeVisible();
  expect(screen.getByText('110%')).toBeVisible();
  view.unmount();
});

it('drops a late translation after the paper generation changes', async () => {
  const firstTranslation = deferred<string>();
  const signals: AbortSignal[] = [];
  const translator = new SelectionTranslator<PageRectAnchor | null>({
    translate: vi.fn((_text, signal) => {
      signals.push(signal);
      return firstTranslation.promise;
    }),
  });
  const documents = [documentHandle(), documentHandle()];
  const session = new PdfReaderSession({
    fetchBytes: vi.fn(async () => new ArrayBuffer(8)),
    createLoadingTask: vi
      .fn()
      .mockReturnValueOnce(loadingHandle(Promise.resolve(documents[0]!)))
      .mockReturnValueOnce(loadingHandle(Promise.resolve(documents[1]!))),
  });
  let selection: Selection | null = null;
  const controller = new PdfSelectionController({
    getSelection: () => selection,
    resolveSelection: () => policyInput('translate me'),
    delayMs: 0,
  });
  const view = render(
    <PdfWorkspace
      createSelectionController={() => controller}
      createSession={() => session}
      createTranslator={() => translator}
      paperId="paper-a"
    />,
  );
  await screen.findByRole('article', { name: '第 1 页' });
  const viewport = screen.getByTestId('pdf-viewport');
  selection = nativeSelection(viewport);
  fireEvent.mouseUp(viewport);
  await screen.findByText('translate me');
  await userEvent.click(screen.getByRole('button', { name: '翻译选文' }));
  await waitFor(() => expect(signals).toHaveLength(1));

  view.rerender(
    <PdfWorkspace
      createSelectionController={() => controller}
      createSession={() => session}
      createTranslator={() => translator}
      paperId="paper-b"
    />,
  );
  await waitFor(() => expect(signals[0]?.aborted).toBe(true));
  await act(async () => {
    firstTranslation.resolve('不应出现的迟到翻译');
    await firstTranslation.promise;
  });

  expect(screen.queryByText('不应出现的迟到翻译')).not.toBeInTheDocument();
  expect(screen.queryByText('translate me')).not.toBeInTheDocument();
  view.unmount();
});

it('survives a StrictMode probe and releases the final document owner', async () => {
  const documents: PdfDocumentHandle[] = [];
  const fetchSignals: AbortSignal[] = [];
  const session = new PdfReaderSession({
    fetchBytes: vi.fn(async (_paperId, signal) => {
      fetchSignals.push(signal);
      return new ArrayBuffer(8);
    }),
    createLoadingTask: vi.fn(() => {
      const pdf = documentHandle();
      documents.push(pdf);
      return loadingHandle(Promise.resolve(pdf));
    }),
  });
  const generations: number[] = [];
  const view = render(
    <StrictMode>
      <PdfWorkspace
        createSession={() => session}
        onGenerationChange={(generation) => generations.push(generation)}
        paperId="paper-a"
      />
    </StrictMode>,
  );

  await waitFor(() => expect(fetchSignals).toHaveLength(2));
  await waitFor(() => expect(documents).toHaveLength(1));
  await waitFor(() => expect(generations.at(-1)).toBe(2));
  expect(fetchSignals[0]?.aborted).toBe(true);
  expect(fetchSignals[1]?.aborted).toBe(false);
  expect(documents[0]?.destroy).not.toHaveBeenCalled();

  view.unmount();
  await waitFor(() => expect(documents[0]?.destroy).toHaveBeenCalledOnce());
});
