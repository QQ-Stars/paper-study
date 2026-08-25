import type { Candidate, StreamEvent } from '../api/types';

export type SearchTerminalDecision =
  | { kind: 'pending' }
  | { kind: 'stale' }
  | { kind: 'failed'; error: string; notification: string }
  | {
      kind: 'succeeded';
      candidates: Candidate[];
      phase: 'searched';
      notification: string;
    };

export function decideSearchTerminal(
  event: StreamEvent,
  requestIsCurrent: boolean,
): SearchTerminalDecision {
  if (!requestIsCurrent) return { kind: 'stale' };
  if (event.type !== 'done' && event.type !== 'result') return { kind: 'pending' };
  if (event.ok === false) {
    const error = String(event.error ?? '任务执行失败');
    return { kind: 'failed', error, notification: `检索失败：${error}` };
  }
  const candidates = (Array.isArray(event.candidates) ? event.candidates : []).filter(
    (candidate): candidate is Candidate =>
      candidate !== null &&
      typeof candidate === 'object' &&
      typeof (candidate as Candidate).title === 'string',
  );
  return {
    kind: 'succeeded',
    candidates,
    phase: 'searched',
    notification: `检索完成，命中 ${candidates.length} 篇候选`,
  };
}

export type IngestTerminalDecision =
  | { kind: 'pending'; reloadPapers: false }
  | {
      kind: 'failed';
      error: string;
      reloadPapers: false;
      notification: string;
    }
  | {
      kind: 'succeeded';
      reloadPapers: true;
      notification: string;
    };

export function decideIngestTerminal(
  event: StreamEvent,
  selectedCount: number,
): IngestTerminalDecision {
  if (event.type !== 'done' && event.type !== 'result') {
    return { kind: 'pending', reloadPapers: false };
  }
  if (event.ok === false) {
    const error = String(event.error ?? '任务执行失败');
    return {
      kind: 'failed',
      error,
      reloadPapers: false,
      notification: `导入失败：${error}`,
    };
  }
  const added =
    typeof event.added === 'number' && Number.isSafeInteger(event.added) && event.added >= 0
      ? event.added
      : selectedCount;
  return {
    kind: 'succeeded',
    reloadPapers: true,
    notification: `已导入 ${added} 篇到文献库`,
  };
}
