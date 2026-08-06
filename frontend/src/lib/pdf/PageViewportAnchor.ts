export interface PdfPageBox {
  pageNumber: number;
  top: number;
  height: number;
}

export interface PageViewportAnchor {
  pageNumber: number;
  relativePageOffset: number;
  viewportRatio: number;
}

export interface PdfGeometryRect {
  left: number;
  top: number;
  right: number;
  bottom: number;
}

export interface PageRectAnchor {
  pageNumber: number;
  x: number;
  y: number;
  width: number;
  height: number;
}

export interface PageViewportLayout {
  scrollTop: number;
  viewportHeight: number;
  pages: readonly PdfPageBox[];
}

export interface RestoredPageViewportLayout {
  viewportHeight: number;
  pages: readonly PdfPageBox[];
}

function clamp(value: number, minimum: number, maximum: number): number {
  return Math.min(maximum, Math.max(minimum, value));
}

function usablePages(pages: readonly PdfPageBox[]): PdfPageBox[] {
  return [...pages]
    .filter(
      (page) =>
        Number.isInteger(page.pageNumber) &&
        page.pageNumber > 0 &&
        Number.isFinite(page.top) &&
        Number.isFinite(page.height) &&
        page.height > 0,
    )
    .sort((left, right) => left.top - right.top);
}

export function capturePageViewportAnchor(
  layout: PageViewportLayout,
  viewportRatio = 0,
): PageViewportAnchor | null {
  const pages = usablePages(layout.pages);
  if (pages.length === 0) return null;

  const safeViewportRatio = clamp(viewportRatio, 0, 1);
  const anchorY =
    Math.max(0, layout.scrollTop) +
    Math.max(0, layout.viewportHeight) * safeViewportRatio;
  const page =
    pages.find(
      (candidate) =>
        anchorY >= candidate.top && anchorY <= candidate.top + candidate.height,
    ) ??
    pages.reduce((nearest, candidate) => {
      const distance =
        anchorY < candidate.top
          ? candidate.top - anchorY
          : anchorY - (candidate.top + candidate.height);
      const nearestDistance =
        anchorY < nearest.top
          ? nearest.top - anchorY
          : anchorY - (nearest.top + nearest.height);
      return distance < nearestDistance ? candidate : nearest;
    });

  return {
    pageNumber: page.pageNumber,
    relativePageOffset: clamp((anchorY - page.top) / page.height, 0, 1),
    viewportRatio: safeViewportRatio,
  };
}

export function resolvePageViewportAnchor(
  anchor: PageViewportAnchor | null,
  layout: RestoredPageViewportLayout,
): number | null {
  if (!anchor) return null;
  const page = usablePages(layout.pages).find(
    (candidate) => candidate.pageNumber === anchor.pageNumber,
  );
  if (!page) return null;

  return Math.max(
    0,
    page.top +
      clamp(anchor.relativePageOffset, 0, 1) * page.height -
      Math.max(0, layout.viewportHeight) * clamp(anchor.viewportRatio, 0, 1),
  );
}

export function createPageRectAnchor(
  pageNumber: number,
  rect: PdfGeometryRect,
  pageRect: PdfGeometryRect,
): PageRectAnchor | null {
  const pageWidth = pageRect.right - pageRect.left;
  const pageHeight = pageRect.bottom - pageRect.top;
  const width = rect.right - rect.left;
  const height = rect.bottom - rect.top;
  if (
    !Number.isInteger(pageNumber) ||
    pageNumber < 1 ||
    !Number.isFinite(pageWidth) ||
    !Number.isFinite(pageHeight) ||
    !Number.isFinite(width) ||
    !Number.isFinite(height) ||
    pageWidth <= 0 ||
    pageHeight <= 0 ||
    width < 0 ||
    height < 0
  ) {
    return null;
  }

  const x = clamp((rect.left - pageRect.left) / pageWidth, 0, 1);
  const y = clamp((rect.top - pageRect.top) / pageHeight, 0, 1);
  return {
    pageNumber,
    x,
    y,
    width: clamp(width / pageWidth, 0, 1 - x),
    height: clamp(height / pageHeight, 0, 1 - y),
  };
}

export function resolvePageRectAnchor(
  anchor: PageRectAnchor,
  pageRect: PdfGeometryRect,
): PdfGeometryRect | null {
  const pageWidth = pageRect.right - pageRect.left;
  const pageHeight = pageRect.bottom - pageRect.top;
  if (pageWidth <= 0 || pageHeight <= 0) return null;
  const left = pageRect.left + clamp(anchor.x, 0, 1) * pageWidth;
  const top = pageRect.top + clamp(anchor.y, 0, 1) * pageHeight;
  return {
    left,
    top,
    right: left + clamp(anchor.width, 0, 1) * pageWidth,
    bottom: top + clamp(anchor.height, 0, 1) * pageHeight,
  };
}
