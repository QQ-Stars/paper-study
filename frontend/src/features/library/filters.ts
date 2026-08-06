import type { PaperListItem } from '../../lib/api/types';
import type {
  LibrarySort,
  LibrarySurfaceFilters,
} from '../../lib/workspace';

export type { LibrarySort, LibrarySourceFilter } from '../../lib/workspace';

export interface LibraryFilters extends Readonly<LibrarySurfaceFilters> {
  readonly semanticScores: ReadonlyMap<string, number> | null;
}

export const defaultLibraryFilters: LibraryFilters = {
  query: '',
  venue: 'all',
  type: 'all',
  topic: 'all',
  status: 'all',
  favorite: false,
  year: 'all',
  source: 'all',
  sort: 'added',
  semanticScores: null,
};

function searchableText(paper: PaperListItem): string {
  return [
    paper.title,
    paper.titleZh,
    paper.venue,
    paper.type,
    paper.topic,
  ]
    .filter((value): value is string => Boolean(value))
    .join('\n')
    .toLocaleLowerCase();
}

function numeric(value: number | string | null): number {
  if (value == null || value === '') return Number.NEGATIVE_INFINITY;
  const parsed = typeof value === 'number' ? value : Number(value);
  return Number.isFinite(parsed) ? parsed : Number.NEGATIVE_INFINITY;
}

function addedAt(value: string | null): number {
  if (value == null) return Number.NEGATIVE_INFINITY;
  const timestamp = Date.parse(value);
  return Number.isFinite(timestamp) ? timestamp : Number.NEGATIVE_INFINITY;
}

function compareBySort(
  left: PaperListItem,
  right: PaperListItem,
  sort: LibrarySort,
  semanticScores: ReadonlyMap<string, number> | null,
): number {
  let comparison = 0;
  if (semanticScores != null) {
    comparison = numeric(semanticScores.get(right.id) ?? null)
      - numeric(semanticScores.get(left.id) ?? null);
  }
  if (semanticScores == null && sort === 'added') comparison = addedAt(right.createdAt) - addedAt(left.createdAt);
  if (semanticScores == null && sort === 'relevance') comparison = numeric(right.relevance) - numeric(left.relevance);
  if (semanticScores == null && sort === 'year') comparison = numeric(right.year) - numeric(left.year);
  if (semanticScores == null && sort === 'citations') comparison = numeric(right.citations) - numeric(left.citations);
  if (semanticScores == null && sort === 'title') {
    comparison = left.title.localeCompare(right.title, undefined, { sensitivity: 'base' });
  }
  return comparison || left.id.localeCompare(right.id);
}

export function applyLibraryFilters(
  papers: readonly PaperListItem[],
  filters: LibraryFilters,
): PaperListItem[] {
  const query = filters.query.trim().toLocaleLowerCase();
  return papers.filter((paper) => {
    if (filters.semanticScores != null) {
      if (!filters.semanticScores.has(paper.id)) return false;
    } else if (query && !searchableText(paper).includes(query)) return false;
    if (filters.venue !== 'all' && paper.venue !== filters.venue) return false;
    if (filters.type !== 'all' && paper.type !== filters.type) return false;
    if (filters.topic !== 'all' && paper.topic !== filters.topic) return false;
    if (filters.status !== 'all' && paper.status !== filters.status) return false;
    if (filters.favorite && !paper.favorite) return false;
    if (filters.year !== 'all' && paper.year !== filters.year) return false;
    if (filters.source === 'seed' && paper.source !== 'seed') return false;
    if (filters.source === 'collected' && paper.source === 'seed') return false;
    return true;
  }).sort((left, right) => compareBySort(
    left,
    right,
    filters.sort,
    filters.semanticScores,
  ));
}

export function reconcileLibrarySelection(
  papers: readonly PaperListItem[],
  selectedId: string | null | undefined,
): string | null {
  if (selectedId != null && papers.some((paper) => paper.id === selectedId)) {
    return selectedId;
  }
  return papers[0]?.id ?? null;
}
