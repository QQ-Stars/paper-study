import type { DashboardSurfaceFilters } from '../../lib/workspace';

export interface DashboardSelectablePaper {
  readonly title: string;
  readonly titleZh?: string | null;
  readonly venue?: string | null;
  readonly year?: string | null;
  readonly type?: string | null;
  readonly topic?: string | null;
  readonly status?: string | null;
  readonly relevance?: number | null;
  readonly createdAt?: string | null;
}

function normalizedText(value: string | null | undefined): string {
  return value?.trim().toLocaleLowerCase() ?? '';
}

function numericYear(value: string | null | undefined): number {
  const parsed = Number.parseInt(value ?? '', 10);
  return Number.isFinite(parsed) ? parsed : Number.NEGATIVE_INFINITY;
}

function createdTime(value: string | null | undefined): number {
  const parsed = Date.parse(value ?? '');
  return Number.isFinite(parsed) ? parsed : Number.NEGATIVE_INFINITY;
}

export function selectDashboardPapers<T extends DashboardSelectablePaper>(
  papers: readonly T[],
  filters: DashboardSurfaceFilters,
): T[] {
  const query = normalizedText(filters.query);
  const filtered = papers.filter((paper) => {
    if (filters.status !== 'all' && paper.status !== filters.status) return false;
    if (!query) return true;
    return [paper.title, paper.titleZh, paper.venue, paper.type, paper.topic]
      .some((value) => normalizedText(value).includes(query));
  });

  return [...filtered].sort((left, right) => {
    switch (filters.sort) {
      case 'title':
        return (left.titleZh || left.title).localeCompare(
          right.titleZh || right.title,
          'zh-CN',
          { sensitivity: 'base' },
        );
      case 'year':
        return numericYear(right.year) - numericYear(left.year);
      case 'relevance':
        return (right.relevance ?? Number.NEGATIVE_INFINITY)
          - (left.relevance ?? Number.NEGATIVE_INFINITY);
      default:
        return createdTime(right.createdAt) - createdTime(left.createdAt);
    }
  });
}
