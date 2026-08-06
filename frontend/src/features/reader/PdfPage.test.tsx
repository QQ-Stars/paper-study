import { render } from '@testing-library/react';

import type {
  PdfPageSnapshot,
  PdfPageSurface,
} from '../../lib/pdf/PdfReaderSession';
import { PdfPage, type PdfPageSession } from './PdfPage';

function pageSnapshot(
  overrides: Partial<PdfPageSnapshot> = {},
): PdfPageSnapshot {
  return {
    pageNumber: 2,
    status: 'ready',
    width: 640,
    height: 900,
    error: null,
    ...overrides,
  };
}

it('mounts one canvas/text-layer owner and releases it on unmount', () => {
  let surface: PdfPageSurface | undefined;
  const release = vi.fn();
  const session: PdfPageSession = {
    mountPage: vi.fn((nextSurface) => {
      surface = nextSurface;
      return release;
    }),
  };

  const view = render(
    <PdfPage
      generation={4}
      pageNumber={2}
      paperId="paper-a"
      session={session}
      snapshot={pageSnapshot()}
    />,
  );

  const page = view.getByRole('article', { name: '第 2 页' });
  expect(page).toHaveAttribute('data-pdf-page-number', '2');
  expect(page).toHaveAttribute('data-status', 'ready');
  expect(surface).toMatchObject({ pageNumber: 2, target: page });
  expect(surface?.canvas).toBeInstanceOf(HTMLCanvasElement);
  expect(surface?.textLayer).toHaveClass('textLayer');
  expect(surface?.textLayer).not.toHaveAttribute('aria-hidden');
  expect(surface?.textLayer).toHaveAttribute('role', 'document');

  view.unmount();
  expect(release).toHaveBeenCalledOnce();
});

it('exposes loading and error states without remounting the page owner', () => {
  const release = vi.fn();
  const session: PdfPageSession = {
    mountPage: vi.fn(() => release),
  };
  const view = render(
    <PdfPage
      generation={1}
      pageNumber={2}
      paperId="paper-a"
      session={session}
      snapshot={pageSnapshot({ status: 'loading', width: 0, height: 0 })}
    />,
  );

  expect(view.getByRole('status')).toHaveTextContent('正在渲染第 2 页');
  view.rerender(
    <PdfPage
      generation={1}
      pageNumber={2}
      paperId="paper-a"
      session={session}
      snapshot={pageSnapshot({
        status: 'error',
        width: 0,
        height: 0,
        error: new Error('page failed'),
      })}
    />,
  );

  expect(view.getByRole('alert')).toHaveTextContent('page failed');
  expect(session.mountPage).toHaveBeenCalledOnce();
});
