export const REPRODUCTION_LIST_STORAGE_KEY = 'paper-study:reproduction-list';

export type ReproductionListState = {
  query: string;
  status: string;
  tag: string;
  sort: 'updated' | 'created' | 'name';
  scrollTop: number;
};

export const DEFAULT_REPRODUCTION_LIST_STATE: ReproductionListState = {
  query: '',
  status: '',
  tag: '',
  sort: 'updated',
  scrollTop: 0,
};

function isSort(value: unknown): value is ReproductionListState['sort'] {
  return value === 'updated' || value === 'created' || value === 'name';
}

export function readReproductionListState(storage: Pick<Storage, 'getItem'> | null | undefined): ReproductionListState {
  if (!storage) return { ...DEFAULT_REPRODUCTION_LIST_STATE };
  try {
    const raw = storage.getItem(REPRODUCTION_LIST_STORAGE_KEY);
    const parsed: unknown = raw ? JSON.parse(raw) : null;
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
      return { ...DEFAULT_REPRODUCTION_LIST_STATE };
    }
    const value = parsed as Record<string, unknown>;
    const scrollTop = typeof value.scrollTop === 'number' && Number.isFinite(value.scrollTop) && value.scrollTop >= 0
      ? Math.round(value.scrollTop)
      : 0;
    return {
      query: typeof value.query === 'string' ? value.query : '',
      status: typeof value.status === 'string' ? value.status : '',
      tag: typeof value.tag === 'string' ? value.tag : '',
      sort: isSort(value.sort) ? value.sort : 'updated',
      scrollTop,
    };
  } catch {
    return { ...DEFAULT_REPRODUCTION_LIST_STATE };
  }
}

export function writeReproductionListState(
  storage: Pick<Storage, 'setItem'> | null | undefined,
  state: ReproductionListState,
): void {
  if (!storage) return;
  const normalized: ReproductionListState = {
    query: state.query.trim(),
    status: state.status,
    tag: state.tag,
    sort: isSort(state.sort) ? state.sort : 'updated',
    scrollTop: Number.isFinite(state.scrollTop) && state.scrollTop > 0 ? Math.round(state.scrollTop) : 0,
  };
  try {
    storage.setItem(REPRODUCTION_LIST_STORAGE_KEY, JSON.stringify(normalized));
  } catch {
    /* Storage is optional; the page remains usable when it is unavailable. */
  }
}
