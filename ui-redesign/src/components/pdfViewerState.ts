export const PDF_DEFAULT_SCALE = 1.15;
export const PDF_MIN_SCALE = 0.6;
export const PDF_MAX_SCALE = 2.2;
export const PDF_RENDER_RADIUS = 2;

export type SavedPdfPosition = {
  page: number;
  scale: number;
};

export function clampPdfScale(value: number): number {
  if (!Number.isFinite(value)) return PDF_DEFAULT_SCALE;
  return Math.min(PDF_MAX_SCALE, Math.max(PDF_MIN_SCALE, value));
}

export function clampPdfPage(value: number, pageCount: number): number {
  if (!Number.isFinite(value) || pageCount < 1) return 1;
  return Math.min(pageCount, Math.max(1, Math.round(value)));
}

export function parseSavedPdfPosition(raw: string | null): SavedPdfPosition | null {
  if (!raw) return null;
  try {
    const parsed = JSON.parse(raw) as Partial<SavedPdfPosition>;
    if (!Number.isFinite(parsed.page) || !Number.isFinite(parsed.scale)) return null;
    return {
      page: Math.max(1, Math.round(parsed.page as number)),
      scale: clampPdfScale(parsed.scale as number),
    };
  } catch {
    return null;
  }
}

export function pdfRenderWindow(
  currentPage: number,
  pageCount: number,
  radius = PDF_RENDER_RADIUS,
): number[] {
  if (pageCount < 1) return [];
  const current = clampPdfPage(currentPage, pageCount);
  const safeRadius = Math.max(0, Math.floor(radius));
  const start = Math.max(1, current - safeRadius);
  const end = Math.min(pageCount, current + safeRadius);
  return Array.from({ length: end - start + 1 }, (_, index) => start + index);
}
