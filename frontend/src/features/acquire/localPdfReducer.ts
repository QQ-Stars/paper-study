export type LocalPdfOperation = 'scan' | 'import' | 'download';

export type LocalPdfPhase =
  | 'idle'
  | 'running'
  | 'ready'
  | 'success'
  | 'failure'
  | 'stopped';

export interface LocalPdfSessionState {
  readonly runId: number | null;
  readonly phase: LocalPdfPhase;
  readonly operation: LocalPdfOperation | null;
  readonly progress: string[];
  readonly terminal: string | null;
  readonly error: string | null;
}

export type LocalPdfSessionAction =
  | { readonly type: 'start'; readonly runId: number; readonly operation: LocalPdfOperation }
  | { readonly type: 'progress'; readonly runId: number; readonly line: string }
  | { readonly type: 'ready'; readonly runId: number; readonly terminal: string }
  | { readonly type: 'success'; readonly runId: number; readonly terminal: string }
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
  };
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
    return line ? { ...state, progress: [...state.progress, line] } : state;
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
