import type { Candidate, StreamEvent } from '../api/types';

export const DEFAULT_ACQUIRE_QUERY = 'LLM hallucination detection and mitigation';

export interface AcquireSearchDraft {
  query: string;
  sources: readonly string[];
  years: string;
  max: number;
  minRelevance: number;
  expand: boolean;
  onlyA: boolean;
  selectedQueries: ReadonlySet<string>;
}

export interface AcquireSearchParams {
  query: string;
  sources: string[];
  years: string;
  max: number;
  minRelevance: number | undefined;
  expand: boolean;
  onlyA: boolean;
  queries: string[] | undefined;
}

export interface AcquireJobParams {
  [key: string]: unknown;
  query: string;
  sources: string[];
  years: string;
  max: number;
  minRelevance: number | undefined;
  onlyA: boolean;
  queries: string[];
}

/**
 * Convert the values shown on the acquisition page into the legacy search
 * request contract.  Keeping this at one seam prevents a control from being
 * rendered as if it were active while silently disappearing from the request.
 */
export function buildAcquireSearchParams(draft: AcquireSearchDraft): AcquireSearchParams {
  const queries = [...draft.selectedQueries]
    .map((value) => value.trim())
    .filter(Boolean);
  const minRelevance = Number(draft.minRelevance);

  return {
    query: draft.query.trim(),
    sources: [...draft.sources],
    years: draft.years.trim(),
    max: draft.max,
    // Send zero explicitly.  The collection page displays zero as "no
    // threshold"; omitting it would make background jobs fall back to their
    // historical 0.5 default instead of honoring what the user sees.
    minRelevance:
      Number.isFinite(minRelevance) && minRelevance >= 0 ? minRelevance : undefined,
    expand: draft.expand,
    onlyA: draft.onlyA,
    queries: queries.length > 0 ? queries : undefined,
  };
}

/**
 * Build the legacy background-job payload from the same draft.  Jobs predate
 * the explicit `expand` boolean, so an empty selection is represented by the
 * historical query-array convention: `[query]` means automatic expansion and
 * `[]` means use only the main direction.
 */
export function buildAcquireJobParams(draft: AcquireSearchDraft): AcquireJobParams {
  const search = buildAcquireSearchParams(draft);
  return {
    query: search.query,
    sources: search.sources,
    years: search.years,
    max: search.max,
    minRelevance: search.minRelevance,
    onlyA: search.onlyA,
    queries: search.queries ?? (search.expand ? [search.query] : []),
  };
}

/** Choose the shortcut query without replacing text the user already typed. */
export function chooseAcquireQuery(
  current: string,
  fallback = DEFAULT_ACQUIRE_QUERY,
): string {
  const trimmed = current.trim();
  return trimmed || fallback;
}

export type SearchTerminalDecision =
  | { kind: 'pending' }
  | { kind: 'stale' }
  | { kind: 'failed'; error: string; notification: string }
  | {
      kind: 'succeeded';
      candidates: Candidate[];
      phase: 'searched';
      notification: string;
    };

export function decideSearchTerminal(
  event: StreamEvent,
  requestIsCurrent: boolean,
): SearchTerminalDecision {
  if (!requestIsCurrent) return { kind: 'stale' };
  if (event.type !== 'done' && event.type !== 'result') return { kind: 'pending' };
  if (event.ok === false) {
    const error = String(event.error ?? '任务执行失败');
    return { kind: 'failed', error, notification: `检索失败：${error}` };
  }
  const candidates = (Array.isArray(event.candidates) ? event.candidates : []).filter(
    (candidate): candidate is Candidate =>
      candidate !== null &&
      typeof candidate === 'object' &&
      typeof (candidate as Candidate).title === 'string',
  );
  return {
    kind: 'succeeded',
    candidates,
    phase: 'searched',
    notification: `检索完成，命中 ${candidates.length} 篇候选`,
  };
}

export type IngestTerminalDecision =
  | { kind: 'pending'; reloadPapers: false }
  | {
      kind: 'failed';
      error: string;
      reloadPapers: false;
      notification: string;
    }
  | {
      kind: 'succeeded';
      reloadPapers: true;
      notification: string;
    };

export function decideIngestTerminal(
  event: StreamEvent,
  selectedCount: number,
): IngestTerminalDecision {
  if (event.type !== 'done' && event.type !== 'result') {
    return { kind: 'pending', reloadPapers: false };
  }
  if (event.ok === false) {
    const error = String(event.error ?? '任务执行失败');
    return {
      kind: 'failed',
      error,
      reloadPapers: false,
      notification: `导入失败：${error}`,
    };
  }
  const added =
    typeof event.added === 'number' && Number.isSafeInteger(event.added) && event.added >= 0
      ? event.added
      : selectedCount;
  return {
    kind: 'succeeded',
    reloadPapers: true,
    notification: `已导入 ${added} 篇到文献库`,
  };
}
