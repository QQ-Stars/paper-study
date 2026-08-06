import type { SearchRequest } from '../api/types';

export const ACADEMIC_SOURCES = [
  'semanticscholar',
  'arxiv',
  'openalex',
  'dblp',
] as const;

export type AcademicSource = (typeof ACADEMIC_SOURCES)[number];

export const SOURCE_LABELS = {
  semanticscholar: 'Semantic Scholar',
  arxiv: 'arXiv',
  openalex: 'OpenAlex',
  dblp: 'DBLP',
} as const satisfies Record<AcademicSource, string>;

export interface SearchDraft {
  query?: string;
  sources?: string[];
  years?: string;
  max?: number;
  minRelevance?: number;
  expand?: boolean;
  onlyA?: boolean;
  queries?: string[];
}

export type NormalizedSearchDraft =
  | { ok: true; request: SearchRequest }
  | { ok: false; errors: string[] };

const sourceSet = new Set<string>(ACADEMIC_SOURCES);

function finiteOr(value: number | undefined, fallback: number): number {
  return typeof value === 'number' && Number.isFinite(value) ? value : fallback;
}

function clamp(value: number, minimum: number, maximum: number): number {
  return Math.min(maximum, Math.max(minimum, value));
}

function uniqueTrimmed(values: string[] | undefined): string[] {
  const result: string[] = [];
  const seen = new Set<string>();
  for (const value of values ?? []) {
    const clean = String(value).trim();
    if (!clean || seen.has(clean)) continue;
    seen.add(clean);
    result.push(clean);
  }
  return result;
}

export function normalizeSearchDraft(draft: SearchDraft): NormalizedSearchDraft {
  const query = String(draft.query ?? '').trim();
  const sources = uniqueTrimmed(draft.sources).filter((source) => sourceSet.has(source));
  const errors: string[] = [];
  if (!query) errors.push('请输入研究方向');
  if (sources.length === 0) errors.push('至少选择一个学术来源');
  if (errors.length > 0) return { ok: false, errors };

  const queries = uniqueTrimmed(draft.queries);
  const request: SearchRequest = {
    query,
    sources,
    years: String(draft.years ?? '').trim() || '2024-2026',
    max: Math.trunc(clamp(finiteOr(draft.max, 10), 1, 60)),
    minRelevance: clamp(finiteOr(draft.minRelevance, 0), 0, 1),
    expand: Boolean(draft.expand),
    onlyA: Boolean(draft.onlyA),
  };
  if (queries.length > 0) request.queries = queries;
  return { ok: true, request };
}
