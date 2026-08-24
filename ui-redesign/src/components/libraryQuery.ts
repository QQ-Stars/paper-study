import type { Paper, StudyStatus } from '../api/types';

export type LibrarySearchMode = 'keyword' | 'semantic' | 'chunks';
export type LibrarySortKey = 'recent' | 'year' | 'citations' | 'relevance';
export type LibraryView = LibrarySortKey | 'favorites' | 'queue';

export interface LibraryFilterQuery {
  mode: LibrarySearchMode;
  query: string;
  status: 'all' | StudyStatus;
  topic: string;
  view: LibraryView;
  readingQueueIds?: readonly string[];
}

export interface LibraryPageResult {
  papers: Paper[];
  page: number;
  pageCount: number;
  pageStart: number;
  pageEnd: number;
}

function sortKeyForView(view: LibraryView): LibrarySortKey {
  return view === 'favorites' || view === 'queue' ? 'recent' : view;
}

function matchesKeyword(paper: Paper, query: string): boolean {
  const trimmed = query.trim();
  if (!trimmed) return true;
  const lowered = trimmed.toLowerCase();
  return (
    (paper.title ?? '').toLowerCase().includes(lowered) ||
    (paper.title_zh ?? '').includes(trimmed) ||
    (paper.venue ?? '').toLowerCase().includes(lowered) ||
    (paper.topic ?? '').includes(trimmed) ||
    (paper.id ?? '').toLowerCase().includes(lowered)
  );
}

export function filterLibraryPapers(
  papers: readonly Paper[],
  filter: LibraryFilterQuery,
): Paper[] {
  const favoritesOnly = filter.view === 'favorites';
  const queueOnly = filter.view === 'queue';
  const readingQueue = queueOnly ? new Set(filter.readingQueueIds ?? []) : null;
  const sortKey = sortKeyForView(filter.view);
  const filtered = papers.filter(
    (paper) =>
      (filter.mode !== 'keyword' || matchesKeyword(paper, filter.query)) &&
      (filter.status === 'all' || paper.status === filter.status) &&
      (filter.topic === 'all' || paper.topic === filter.topic) &&
      (!favoritesOnly || paper.favorite === 1) &&
      (!queueOnly || readingQueue?.has(paper.id)),
  );

  const byKey = (a: Paper, b: Paper): number => {
    if (sortKey === 'year') return Number(b.year || 0) - Number(a.year || 0);
    if (sortKey === 'citations') return (b.citations ?? 0) - (a.citations ?? 0);
    if (sortKey === 'relevance') return (b.relevance ?? 0) - (a.relevance ?? 0);
    return (b.created_at ?? '').localeCompare(a.created_at ?? '');
  };

  return [...filtered].sort(byKey);
}

export function paginateLibraryPapers(
  papers: readonly Paper[],
  requestedPage: number,
  requestedPageSize: number,
): LibraryPageResult {
  const pageSize = Number.isFinite(requestedPageSize) && requestedPageSize > 0
    ? Math.floor(requestedPageSize)
    : 20;
  const pageCount = Math.max(1, Math.ceil(papers.length / pageSize));
  const page = Math.min(Math.max(Math.floor(requestedPage) || 1, 1), pageCount);
  const pageStart = papers.length === 0 ? 0 : (page - 1) * pageSize + 1;
  const pageEnd = Math.min(page * pageSize, papers.length);

  return {
    papers: papers.slice((page - 1) * pageSize, page * pageSize),
    page,
    pageCount,
    pageStart,
    pageEnd,
  };
}
