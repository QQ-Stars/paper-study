import { describe, expect, it } from 'vitest';

import { createIdleStreamState, reduceStreamSession } from './sessionReducer';

describe('stream session ownership', () => {
  it('ignores events and terminals from a stale run', () => {
    let state = reduceStreamSession(createIdleStreamState<string, { ok: boolean; value?: number }>(), {
      type: 'start', runId: 1,
    });
    state = reduceStreamSession(state, { type: 'start', runId: 2 });
    state = reduceStreamSession(state, { type: 'event', runId: 1, event: 'stale' });
    state = reduceStreamSession(state, { type: 'terminal', runId: 1, terminal: { ok: true, value: 1 } });

    expect(state).toMatchObject({ status: 'running', runId: 2, events: [] });
  });

  it('ignores late events after cancellation', () => {
    let state = reduceStreamSession(createIdleStreamState<string, { ok: boolean }>(), { type: 'start', runId: 4 });
    state = reduceStreamSession(state, { type: 'cancel', runId: 4 });
    state = reduceStreamSession(state, { type: 'event', runId: 4, event: 'late' });

    expect(state).toMatchObject({ status: 'cancelled', runId: 4, events: [] });
  });

  it('records an ok:false terminal as an explicit business failure', () => {
    let state = reduceStreamSession(createIdleStreamState<string, { ok: boolean; error?: string }>(), {
      type: 'start', runId: 8,
    });
    state = reduceStreamSession(state, {
      type: 'terminal', runId: 8, terminal: { ok: false, error: 'agent failed' },
    });

    expect(state).toMatchObject({
      status: 'failure', runId: 8, error: { kind: 'business', message: 'agent failed' },
    });
  });

  it('records AbortError as cancellation rather than task failure', () => {
    let state = reduceStreamSession(createIdleStreamState<string, { ok: boolean }>(), { type: 'start', runId: 9 });
    state = reduceStreamSession(state, {
      type: 'error', runId: 9, error: new DOMException('cancelled', 'AbortError'),
    });

    expect(state).toMatchObject({ status: 'cancelled', runId: 9 });
  });
});
