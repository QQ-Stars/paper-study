import type { V2JobEvent } from '../api/types';

export function canCancelJob(status: string): boolean {
  return status === 'queued' || status === 'running';
}

export function canRetryJob(status: string): boolean {
  return status === 'failed' || status === 'cancelled';
}

export function appendJobEvents(
  existing: V2JobEvent[],
  incoming: V2JobEvent[],
): V2JobEvent[] {
  const bySequence = new Map(existing.map((event) => [event.sequence, event]));
  for (const event of incoming) bySequence.set(event.sequence, event);
  return [...bySequence.values()].sort((left, right) => left.sequence - right.sequence);
}
