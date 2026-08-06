import type { Candidate, SemanticHit } from '../../lib/api/types';

export type InsightsCommand =
  | 'citation-build'
  | 'normalize-venues'
  | 'recommend'
  | 'embed'
  | 'semantic-search';

export type InsightsCommandPhase =
  | 'idle'
  | 'running'
  | 'success'
  | 'failure'
  | 'stopped';

export type InsightsCommandTerminal =
  | {
      readonly command: 'citation-build' | 'normalize-venues' | 'embed';
      readonly summary: string;
    }
  | {
      readonly command: 'recommend';
      readonly summary: string;
      readonly candidates: readonly Candidate[];
    }
  | {
      readonly command: 'semantic-search';
      readonly summary: string;
      readonly results: readonly SemanticHit[];
    };

export interface InsightsCommandSession {
  readonly runId: number;
  readonly command: InsightsCommand | null;
  readonly phase: InsightsCommandPhase;
  readonly progress: readonly string[];
  readonly terminal: InsightsCommandTerminal | null;
  readonly error: string | null;
}

export type InsightsCommandAction =
  | {
      readonly type: 'started';
      readonly runId: number;
      readonly command: InsightsCommand;
    }
  | {
      readonly type: 'progressed';
      readonly runId: number;
      readonly line: string;
    }
  | {
      readonly type: 'completed';
      readonly runId: number;
      readonly terminal: InsightsCommandTerminal;
    }
  | {
      readonly type: 'failed';
      readonly runId: number;
      readonly error: string;
    }
  | {
      readonly type: 'stopped';
      readonly runId: number;
    };

export function createInsightsCommandSession(): InsightsCommandSession {
  return {
    runId: 0,
    command: null,
    phase: 'idle',
    progress: [],
    terminal: null,
    error: null,
  };
}

export function insightsCommandReducer(
  state: InsightsCommandSession,
  action: InsightsCommandAction,
): InsightsCommandSession {
  if (action.type === 'started') {
    return {
      runId: action.runId,
      command: action.command,
      phase: 'running',
      progress: [],
      terminal: null,
      error: null,
    };
  }
  if (action.runId !== state.runId || state.phase !== 'running') return state;

  switch (action.type) {
    case 'progressed': {
      const line = action.line.trim();
      if (!line) return state;
      return {
        ...state,
        progress: [...state.progress, line].slice(-8),
      };
    }
    case 'completed':
      return {
        ...state,
        phase: 'success',
        terminal: action.terminal,
        error: null,
      };
    case 'failed':
      return {
        ...state,
        phase: 'failure',
        terminal: null,
        error: action.error,
      };
    case 'stopped':
      return {
        ...state,
        phase: 'stopped',
        terminal: null,
        error: null,
      };
  }
}
