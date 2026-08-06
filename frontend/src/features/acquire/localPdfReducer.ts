export type LocalPdfOperation = 'scan' | 'import' | 'download';

export type LocalPdfPhase =
  | 'idle'
  | 'running'
  | 'ready'
  | 'success'
  | 'failure'
  | 'stopped';

export interface LocalPdfImportProgress {
  readonly total: number | null;
  readonly parsed: number;
  readonly skipped: number;
  readonly prepErrors: readonly string[];
  readonly classificationErrors: readonly string[];
}

export interface LocalPdfImportSummary {
  readonly total: number;
  readonly parsed: number;
  readonly added: number;
  readonly dup: number;
  readonly skipped: number;
  readonly prepErrors: readonly string[];
  readonly classificationErrors: readonly string[];
  readonly classificationFailures: number;
}

export interface LocalPdfSessionState {
  readonly runId: number | null;
  readonly phase: LocalPdfPhase;
  readonly operation: LocalPdfOperation | null;
  readonly progress: string[];
  readonly terminal: string | null;
  readonly error: string | null;
  readonly importProgress: LocalPdfImportProgress | null;
  readonly importSummary: LocalPdfImportSummary | null;
}

export type LocalPdfSessionAction =
  | { readonly type: 'start'; readonly runId: number; readonly operation: LocalPdfOperation }
  | { readonly type: 'progress'; readonly runId: number; readonly line: string }
  | { readonly type: 'ready'; readonly runId: number; readonly terminal: string }
  | { readonly type: 'success'; readonly runId: number; readonly terminal: string }
  | {
    readonly type: 'import-success';
    readonly runId: number;
    readonly added: number;
    readonly dup: number;
    readonly failed: number;
    readonly total: number;
  }
  | { readonly type: 'failure'; readonly runId: number; readonly error: string }
  | { readonly type: 'stop'; readonly runId: number; readonly terminal: string }
  | { readonly type: 'validation-failure'; readonly error: string };

export function createLocalPdfSessionState(): LocalPdfSessionState {
  return {
    runId: null,
    phase: 'idle',
    operation: null,
    progress: [],
    terminal: null,
    error: null,
    importProgress: null,
    importSummary: null,
  };
}

function createImportProgress(): LocalPdfImportProgress {
  return {
    total: null,
    parsed: 0,
    skipped: 0,
    prepErrors: [],
    classificationErrors: [],
  };
}

function detailAfter(line: string, prefix: string): string {
  return line.slice(prefix.length).trim() || line;
}

function reduceImportProgress(
  progress: LocalPdfImportProgress,
  line: string,
): LocalPdfImportProgress {
  if (line.startsWith('TOTAL::')) {
    const total = Number(line.slice('TOTAL::'.length).split('::', 1)[0]);
    return Number.isSafeInteger(total) && total >= 0
      ? { ...progress, total }
      : progress;
  }
  if (line.startsWith('PARSED::')) {
    return { ...progress, parsed: progress.parsed + 1 };
  }
  if (line.startsWith('SKIP::')) {
    return { ...progress, skipped: progress.skipped + 1 };
  }
  if (line.startsWith('PREPERR::')) {
    return {
      ...progress,
      prepErrors: [...progress.prepErrors, detailAfter(line, 'PREPERR::')],
    };
  }
  if (line.startsWith('CLSERR::')) {
    return {
      ...progress,
      classificationErrors: [
        ...progress.classificationErrors,
        detailAfter(line, 'CLSERR::'),
      ],
    };
  }
  return progress;
}

function isCurrentRun(state: LocalPdfSessionState, runId: number): boolean {
  return state.phase === 'running' && state.runId === runId;
}

export function localPdfSessionReducer(
  state: LocalPdfSessionState,
  action: LocalPdfSessionAction,
): LocalPdfSessionState {
  if (action.type === 'start') {
    return {
      runId: action.runId,
      phase: 'running',
      operation: action.operation,
      progress: [],
      terminal: null,
      error: null,
      importProgress: action.operation === 'import' ? createImportProgress() : null,
      importSummary: null,
    };
  }

  if (action.type === 'validation-failure') {
    return {
      ...createLocalPdfSessionState(),
      phase: 'failure',
      error: action.error,
    };
  }

  if (!isCurrentRun(state, action.runId)) return state;

  if (action.type === 'progress') {
    const line = action.line.trim();
    if (!line) return state;
    return {
      ...state,
      progress: [...state.progress, line],
      importProgress: state.importProgress
        ? reduceImportProgress(state.importProgress, line)
        : null,
    };
  }

  if (action.type === 'import-success') {
    const progress = state.importProgress ?? createImportProgress();
    const parsed = progress.parsed;
    const importSummary: LocalPdfImportSummary = {
      total: progress.total ?? action.total,
      parsed,
      added: action.added,
      dup: action.dup,
      skipped: progress.skipped,
      prepErrors: progress.prepErrors,
      classificationErrors: progress.classificationErrors,
      classificationFailures: action.failed,
    };
    return {
      ...state,
      phase: 'success',
      operation: null,
      terminal: `PARSED ${parsed} · ADDED ${action.added} · DUP ${action.dup} · SKIP ${progress.skipped}`,
      error: null,
      importSummary,
    };
  }

  if (action.type === 'ready' || action.type === 'success') {
    return {
      ...state,
      phase: action.type,
      operation: null,
      terminal: action.terminal,
      error: null,
    };
  }

  if (action.type === 'failure') {
    return {
      ...state,
      phase: 'failure',
      operation: null,
      terminal: null,
      error: action.error,
    };
  }

  return {
    ...state,
    phase: 'stopped',
    operation: null,
    terminal: action.terminal,
    error: null,
  };
}
