import type { V2JobEvent } from '../api/types';

export interface JobHistoryFilters {
  status: string;
  jobType: string;
  paperId: string;
}

export interface JobHistoryPage {
  index: number;
  cursors: Array<string | null>;
}

export function buildJobListParams(
  filters: JobHistoryFilters,
  cursor: string | null,
): Record<string, string> {
  const params: Record<string, string> = {};
  if (filters.status !== 'all') params.status = filters.status;
  if (filters.jobType !== 'all') params.jobType = filters.jobType;
  const paperId = filters.paperId.trim();
  if (paperId) params.paperId = paperId;
  if (cursor) params.cursor = cursor;
  return params;
}

export function createJobHistoryPage(): JobHistoryPage {
  return { index: 0, cursors: [null] };
}

export function moveToNextJobPage(page: JobHistoryPage, nextCursor: string | null): JobHistoryPage {
  if (!nextCursor) return page;
  return {
    index: page.index + 1,
    cursors: [...page.cursors.slice(0, page.index + 1), nextCursor],
  };
}

export function moveToPreviousJobPage(page: JobHistoryPage): JobHistoryPage {
  return page.index > 0 ? { ...page, index: page.index - 1 } : page;
}

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
