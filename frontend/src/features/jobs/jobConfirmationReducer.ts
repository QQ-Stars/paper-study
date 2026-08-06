export type JobConfirmationPhase =
  | 'idle'
  | 'running'
  | 'success'
  | 'failure'
  | 'stopped';

export interface JobConfirmationState {
  readonly jobId: number;
  readonly runId: number | null;
  readonly phase: JobConfirmationPhase;
  readonly progress: string[];
  readonly terminal: string | null;
  readonly error: string | null;
}

export type JobConfirmationAction =
  | { readonly type: 'reset'; readonly jobId: number }
  | { readonly type: 'start'; readonly jobId: number; readonly runId: number }
  | { readonly type: 'progress'; readonly jobId: number; readonly runId: number; readonly line: string }
  | { readonly type: 'success'; readonly jobId: number; readonly runId: number; readonly terminal: string }
  | { readonly type: 'failure'; readonly jobId: number; readonly runId: number; readonly error: string }
  | { readonly type: 'stop'; readonly jobId: number; readonly runId: number; readonly terminal: string }
  | { readonly type: 'validation-failure'; readonly jobId: number; readonly error: string };

export function createJobConfirmationState(jobId: number): JobConfirmationState {
  return {
    jobId,
    runId: null,
    phase: 'idle',
    progress: [],
    terminal: null,
    error: null,
  };
}

function isCurrentRun(
  state: JobConfirmationState,
  action: { readonly jobId: number; readonly runId: number },
): boolean {
  return state.jobId === action.jobId
    && state.runId === action.runId
    && state.phase === 'running';
}

export function jobConfirmationReducer(
  state: JobConfirmationState,
  action: JobConfirmationAction,
): JobConfirmationState {
  if (action.type === 'reset') return createJobConfirmationState(action.jobId);

  if (action.type === 'start') {
    return {
      ...createJobConfirmationState(action.jobId),
      runId: action.runId,
      phase: 'running',
    };
  }

  if (action.type === 'validation-failure') {
    return {
      ...createJobConfirmationState(action.jobId),
      phase: 'failure',
      error: action.error,
    };
  }

  if (!isCurrentRun(state, action)) return state;

  if (action.type === 'progress') {
    const line = action.line.trim();
    return line ? { ...state, progress: [...state.progress, line] } : state;
  }

  if (action.type === 'success') {
    return {
      ...state,
      phase: 'success',
      terminal: action.terminal,
      error: null,
    };
  }

  if (action.type === 'failure') {
    return {
      ...state,
      phase: 'failure',
      terminal: null,
      error: action.error,
    };
  }

  return {
    ...state,
    phase: 'stopped',
    terminal: action.terminal,
    error: null,
  };
}
