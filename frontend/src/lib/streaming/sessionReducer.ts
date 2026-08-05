import { BusinessError, isAbortError } from '../api/errors';

export interface StreamTerminal {
  ok: boolean;
  error?: string;
}

export type StreamSessionState<E, R extends StreamTerminal> =
  | { status: 'idle'; runId: null; events: E[] }
  | { status: 'running'; runId: number; events: E[] }
  | { status: 'success'; runId: number; events: E[]; result: R }
  | { status: 'failure'; runId: number; events: E[]; error: Error; result?: R }
  | { status: 'cancelled'; runId: number; events: E[]; reason?: unknown };

export type StreamSessionAction<E, R extends StreamTerminal> =
  | { type: 'start'; runId: number }
  | { type: 'event'; runId: number; event: E }
  | { type: 'terminal'; runId: number; terminal: R }
  | { type: 'error'; runId: number; error: Error }
  | { type: 'cancel'; runId: number; reason?: unknown }
  | { type: 'reset' };

export function createIdleStreamState<E, R extends StreamTerminal>(): StreamSessionState<E, R> {
  return { status: 'idle', runId: null, events: [] };
}

function ownsRunningRun<E, R extends StreamTerminal>(
  state: StreamSessionState<E, R>,
  runId: number,
): state is Extract<StreamSessionState<E, R>, { status: 'running' }> {
  return state.status === 'running' && state.runId === runId;
}

export function reduceStreamSession<E, R extends StreamTerminal>(
  state: StreamSessionState<E, R>,
  action: StreamSessionAction<E, R>,
): StreamSessionState<E, R> {
  if (action.type === 'reset') return createIdleStreamState<E, R>();
  if (action.type === 'start') return { status: 'running', runId: action.runId, events: [] };
  if (!ownsRunningRun(state, action.runId)) return state;

  if (action.type === 'event') {
    return { ...state, events: [...state.events, action.event] };
  }
  if (action.type === 'cancel') {
    return action.reason === undefined
      ? { status: 'cancelled', runId: action.runId, events: state.events }
      : { status: 'cancelled', runId: action.runId, events: state.events, reason: action.reason };
  }
  if (action.type === 'error') {
    if (isAbortError(action.error)) {
      return { status: 'cancelled', runId: action.runId, events: state.events, reason: action.error };
    }
    return { status: 'failure', runId: action.runId, events: state.events, error: action.error };
  }
  if (action.terminal.ok) {
    return { status: 'success', runId: action.runId, events: state.events, result: action.terminal };
  }
  return {
    status: 'failure',
    runId: action.runId,
    events: state.events,
    result: action.terminal,
    error: new BusinessError(action.terminal.error || '流式任务失败', action.terminal),
  };
}
