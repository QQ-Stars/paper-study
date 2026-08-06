import {
  createPageRectAnchor,
  type PageRectAnchor,
  type PdfGeometryRect,
} from './PageViewportAnchor';

export type SelectionBreak = 'none' | 'space' | 'line' | 'paragraph';

export interface PdfSelectionFragment {
  text: string;
  rect: PdfGeometryRect;
  fontSize?: number;
  breakBefore?: SelectionBreak;
}

export interface PdfSelectionPolicyInput {
  pageNumber: number;
  pageRect: PdfGeometryRect;
  fragments: readonly PdfSelectionFragment[];
  startFragmentIndex: number;
  endFragmentIndex?: number;
  nativeText: string;
  maxCharacters?: number;
}

export type PdfSelectionPolicyResult =
  | {
      kind: 'accepted';
      text: string;
      source: 'geometry' | 'native';
      anchor: PageRectAnchor | null;
    }
  | { kind: 'empty'; text: ''; source: 'geometry' | 'native'; anchor: null }
  | {
      kind: 'rejected';
      reason: 'too-long';
      length: number;
      maxCharacters: number;
      anchor: PageRectAnchor | null;
    };

interface IndexedFragment extends PdfSelectionFragment {
  originalIndex: number;
}

function finiteRect(rect: PdfGeometryRect): boolean {
  return (
    [rect.left, rect.top, rect.right, rect.bottom].every(Number.isFinite) &&
    rect.right >= rect.left &&
    rect.bottom >= rect.top
  );
}

function median(values: readonly number[]): number | null {
  if (values.length === 0) return null;
  const sorted = [...values].sort((left, right) => left - right);
  const middle = Math.floor(sorted.length / 2);
  return sorted.length % 2 === 1
    ? (sorted[middle] ?? null)
    : ((sorted[middle - 1] ?? 0) + (sorted[middle] ?? 0)) / 2;
}

function columnFor(
  fragment: PdfSelectionFragment,
  center: number,
): 'left' | 'right' {
  return (fragment.rect.left + fragment.rect.right) / 2 < center
    ? 'left'
    : 'right';
}

function lockToStartingColumn(
  fragments: readonly IndexedFragment[],
  pageRect: PdfGeometryRect,
  startFragmentIndex: number,
  endFragmentIndex?: number,
): IndexedFragment[] {
  const pageWidth = pageRect.right - pageRect.left;
  if (!(pageWidth > 0)) return [];
  const center = pageRect.left + pageWidth / 2;
  const bodyFragments = fragments.filter(
    (fragment) => fragment.rect.right - fragment.rect.left < pageWidth * 0.72,
  );
  const leftEdge = Math.max(
    ...bodyFragments
      .filter((fragment) => fragment.rect.right <= center)
      .map((fragment) => fragment.rect.right),
    Number.NEGATIVE_INFINITY,
  );
  const rightEdge = Math.min(
    ...bodyFragments
      .filter((fragment) => fragment.rect.left >= center)
      .map((fragment) => fragment.rect.left),
    Number.POSITIVE_INFINITY,
  );
  const hasGutter =
    Number.isFinite(leftEdge) &&
    Number.isFinite(rightEdge) &&
    rightEdge - leftEdge >= Math.max(20, pageWidth * 0.035);
  if (!hasGutter) return [...fragments];

  const start =
    fragments.find((fragment) => fragment.originalIndex === startFragmentIndex) ??
    fragments[0];
  if (!start) return [];
  const startColumn = columnFor(start, center);
  const end = Number.isInteger(endFragmentIndex)
    ? fragments.find((fragment) => fragment.originalIndex === endFragmentIndex)
    : undefined;
  const verticalStart = end
    ? Math.min(start.rect.top, end.rect.top)
    : Number.NEGATIVE_INFINITY;
  const verticalEnd = end
    ? Math.max(start.rect.bottom, end.rect.bottom)
    : Number.POSITIVE_INFINITY;
  return fragments.filter(
    (fragment) =>
      columnFor(fragment, center) === startColumn &&
      fragment.rect.bottom >= verticalStart &&
      fragment.rect.top <= verticalEnd,
  );
}

function filterSmallText(
  fragments: readonly IndexedFragment[],
): IndexedFragment[] {
  const medianFontSize = median(
    fragments
      .map((fragment) => fragment.fontSize)
      .filter(
        (fontSize): fontSize is number =>
          typeof fontSize === 'number' &&
          Number.isFinite(fontSize) &&
          fontSize > 0,
      ),
  );
  if (medianFontSize === null) return [...fragments];
  const minimumFontSize = medianFontSize * 0.7;
  return fragments.filter(
    (fragment) =>
      fragment.fontSize === undefined || fragment.fontSize >= minimumFontSize,
  );
}

function separatorFor(
  previous: IndexedFragment | undefined,
  current: IndexedFragment,
): string {
  if (!previous) return '';
  const requestedBreak = current.breakBefore ?? 'space';
  if (requestedBreak === 'none') return '';
  if (requestedBreak === 'paragraph') return '\n\n';
  if (requestedBreak === 'line') {
    return previous.text.trimEnd().endsWith('-') ? '' : ' ';
  }
  return ' ';
}

function mergeFragments(fragments: readonly IndexedFragment[]): string {
  let text = '';
  let previous: IndexedFragment | undefined;
  for (const fragment of fragments) {
    const value = fragment.text.trim();
    if (!value) continue;
    const separator = separatorFor(previous, fragment);
    if (
      currentBreakIsHyphenContinuation(previous, fragment) &&
      text.endsWith('-')
    ) {
      text = text.slice(0, -1);
    }
    text += `${separator}${value}`;
    previous = fragment;
  }
  return text.trim();
}

function currentBreakIsHyphenContinuation(
  previous: IndexedFragment | undefined,
  current: IndexedFragment,
): boolean {
  return Boolean(
    previous?.text.trimEnd().endsWith('-') && current.breakBefore === 'line',
  );
}

function unionRect(
  fragments: readonly IndexedFragment[],
): PdfGeometryRect | null {
  if (fragments.length === 0) return null;
  return {
    left: Math.min(...fragments.map((fragment) => fragment.rect.left)),
    top: Math.min(...fragments.map((fragment) => fragment.rect.top)),
    right: Math.max(...fragments.map((fragment) => fragment.rect.right)),
    bottom: Math.max(...fragments.map((fragment) => fragment.rect.bottom)),
  };
}

function normalizeNativeText(text: string): string {
  return text
    .replace(/-\s*\r?\n\s*/g, '')
    .replace(/\r?\n\s*\r?\n+/g, '\n\n')
    .replace(/(?<!\n)\r?\n(?!\n)/g, ' ')
    .replace(/[ \t]+/g, ' ')
    .trim();
}

export function applyPdfSelectionPolicy(
  input: PdfSelectionPolicyInput,
): PdfSelectionPolicyResult {
  const maximum = input.maxCharacters ?? 6_000;
  const geometryAvailable =
    finiteRect(input.pageRect) &&
    input.pageRect.right > input.pageRect.left &&
    input.pageRect.bottom > input.pageRect.top;
  const usableFragments: IndexedFragment[] = geometryAvailable
    ? input.fragments
        .map((fragment, originalIndex) => ({ ...fragment, originalIndex }))
        .filter(
          (fragment) => fragment.text.trim() && finiteRect(fragment.rect),
        )
    : [];
  const source = usableFragments.length > 0 ? 'geometry' : 'native';
  const selectedFragments =
    source === 'geometry'
      ? filterSmallText(
          lockToStartingColumn(
            usableFragments,
            input.pageRect,
            input.startFragmentIndex,
            input.endFragmentIndex,
          ),
        )
      : [];
  const text =
    source === 'geometry'
      ? mergeFragments(selectedFragments)
      : normalizeNativeText(input.nativeText);
  const selectedRect = unionRect(selectedFragments);
  const anchor = selectedRect
    ? createPageRectAnchor(input.pageNumber, selectedRect, input.pageRect)
    : null;

  if (!text) return { kind: 'empty', text: '', source, anchor: null };
  if (text.length > maximum) {
    return {
      kind: 'rejected',
      reason: 'too-long',
      length: text.length,
      maxCharacters: maximum,
      anchor,
    };
  }
  return { kind: 'accepted', text, source, anchor };
}
