import type {
  Candidate,
  SearchRequest,
  Verification,
} from '../../lib/api/types';

export const ACADEMIC_SOURCES = [
  'semanticscholar',
  'arxiv',
  'openalex',
  'dblp',
] as const;

export type AcademicSource = (typeof ACADEMIC_SOURCES)[number];
export type AcquireOperation = 'search' | 'verify' | 'ingest';
export type AcquireStatus =
  | 'idle'
  | 'running'
  | 'ready'
  | 'success'
  | 'failure'
  | 'stopped';

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

export interface AcquireState {
  status: AcquireStatus;
  operation: AcquireOperation | null;
  runId: number | null;
  progress: string[];
  candidates: Candidate[];
  selectedKeys: string[];
  verifications: Verification[];
  added: number | null;
  error: string | null;
}

export type AcquireAction =
  | { type: 'start'; operation: AcquireOperation; runId: number }
  | { type: 'progress'; runId: number; line: string }
  | { type: 'search-complete'; runId: number; candidates: Candidate[] }
  | { type: 'verify-complete'; runId: number; verifications: Verification[] }
  | { type: 'ingest-complete'; runId: number; added: number }
  | { type: 'failure'; runId: number; error: string }
  | { type: 'stop'; runId: number; reason?: string }
  | { type: 'toggle-candidate'; key: string; selected: boolean }
  | { type: 'select-all'; selected: boolean }
  | { type: 'reset' };

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

export function candidateKey(candidate: Candidate): string {
  if (candidate.candidateId !== null) return `job:${candidate.candidateId}`;
  const sourceIdentity = candidate.sourceId || candidate.arxivId || candidate.doi || candidate.title;
  return `${candidate.source}:${sourceIdentity}`;
}

export function createAcquireState(): AcquireState {
  return {
    status: 'idle',
    operation: null,
    runId: null,
    progress: [],
    candidates: [],
    selectedKeys: [],
    verifications: [],
    added: null,
    error: null,
  };
}

function isCurrentRun(state: AcquireState, runId: number): boolean {
  return state.status === 'running' && state.runId === runId;
}

function applyVerifications(
  candidates: Candidate[],
  verifications: Verification[],
): Candidate[] {
  return candidates.map((candidate, index) => {
    const verification = verifications[index];
    if (!verification || verification.error) return candidate;
    return {
      ...candidate,
      venue: verification.venue ?? candidate.venue,
      year: verification.year ?? candidate.year,
      ccf: verification.ccf ?? candidate.ccf,
    };
  });
}

export function acquireReducer(
  state: AcquireState,
  action: AcquireAction,
): AcquireState {
  if (action.type === 'reset') return createAcquireState();

  if (action.type === 'start') {
    if (action.operation === 'search') {
      return {
        ...createAcquireState(),
        status: 'running',
        operation: action.operation,
        runId: action.runId,
      };
    }
    return {
      ...state,
      status: 'running',
      operation: action.operation,
      runId: action.runId,
      progress: [],
      added: null,
      error: null,
    };
  }

  if (action.type === 'toggle-candidate') {
    const candidate = state.candidates.find((item) => candidateKey(item) === action.key);
    if (!candidate || candidate.inLibrary) return state;
    const selected = new Set(state.selectedKeys);
    if (action.selected) selected.add(action.key);
    else selected.delete(action.key);
    return { ...state, selectedKeys: [...selected] };
  }

  if (action.type === 'select-all') {
    return {
      ...state,
      selectedKeys: action.selected
        ? state.candidates.filter((candidate) => !candidate.inLibrary).map(candidateKey)
        : [],
    };
  }

  if (!isCurrentRun(state, action.runId)) return state;

  if (action.type === 'progress') {
    return {
      ...state,
      progress: action.line.trim()
        ? [...state.progress, action.line]
        : state.progress,
    };
  }
  if (action.type === 'search-complete') {
    return {
      ...state,
      status: 'ready',
      operation: null,
      candidates: action.candidates,
      selectedKeys: action.candidates
        .filter((candidate) => !candidate.inLibrary)
        .map(candidateKey),
      verifications: [],
      error: null,
    };
  }
  if (action.type === 'verify-complete') {
    return {
      ...state,
      status: 'ready',
      operation: null,
      candidates: applyVerifications(state.candidates, action.verifications),
      verifications: action.verifications,
      error: null,
    };
  }
  if (action.type === 'ingest-complete') {
    return {
      ...state,
      status: 'success',
      operation: null,
      added: action.added,
      error: null,
    };
  }
  if (action.type === 'failure') {
    return {
      ...state,
      status: 'failure',
      operation: null,
      error: action.error,
    };
  }
  return {
    ...state,
    status: 'stopped',
    operation: null,
    error: action.reason ?? null,
  };
}
