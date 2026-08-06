import {
  capturePageViewportAnchor,
  createPageRectAnchor,
  resolvePageRectAnchor,
  resolvePageViewportAnchor,
} from './PageViewportAnchor';

it('restores the same relative page point after page dimensions change', () => {
  const anchor = capturePageViewportAnchor({
    scrollTop: 1_100,
    viewportHeight: 800,
    pages: [
      { pageNumber: 1, top: 0, height: 1_000 },
      { pageNumber: 2, top: 1_020, height: 1_000 },
    ],
  });

  expect(anchor).toEqual({
    pageNumber: 2,
    relativePageOffset: 0.08,
    viewportRatio: 0,
  });
  expect(
    resolvePageViewportAnchor(anchor, {
      viewportHeight: 800,
      pages: [
        { pageNumber: 1, top: 0, height: 1_500 },
        { pageNumber: 2, top: 1_530, height: 1_500 },
      ],
    }),
  ).toBe(1_650);
});

it('keeps a selection rectangle inside its normalized page bounds', () => {
  const anchor = createPageRectAnchor(
    1,
    { left: 90, top: 80, right: 130, bottom: 120 },
    { left: 0, top: 0, right: 100, bottom: 100 },
  );

  expect(anchor).toMatchObject({ pageNumber: 1, x: 0.9, y: 0.8 });
  expect(anchor?.width).toBeCloseTo(0.1);
  expect(anchor?.height).toBeCloseTo(0.2);
  expect(
    anchor && resolvePageRectAnchor(
      anchor,
      { left: 0, top: 0, right: 100, bottom: 100 },
    ),
  ).toEqual({ left: 90, top: 80, right: 100, bottom: 100 });
});
