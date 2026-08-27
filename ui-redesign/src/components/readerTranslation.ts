import type { StreamEvent } from '../api/types';

export type TranslationMode = 'chunked' | 'full';

export function normalizeTranslationMode(value: unknown): TranslationMode {
  return value === 'full' ? 'full' : 'chunked';
}

export function translationModeHint(mode: TranslationMode): string {
  return mode === 'full'
    ? '全文一次翻译，可能需要较长时间，请勿离开'
    : '全文分块翻译，可能需要较长时间，请勿离开';
}

export function translationProgressMode(event: StreamEvent): TranslationMode | null {
  const progress = event.progress;
  if (!progress || typeof progress !== 'object') return null;
  const stage = (progress as { stage?: unknown }).stage;
  if (stage === 'translation_full') return 'full';
  if (stage === 'translation_chunked') return 'chunked';
  return null;
}

export function formatRegenerationLine(
  event: StreamEvent,
  kind: 'explainer' | 'translation',
  mode: TranslationMode,
): string {
  const eventName = String(event.event ?? '');
  const progress =
    event.progress && typeof event.progress === 'object'
      ? (event.progress as Record<string, unknown>)
      : null;
  const stage = typeof progress?.stage === 'string' ? progress.stage : '';
  const completed = typeof progress?.completed === 'number' ? progress.completed : null;
  const total = typeof progress?.total === 'number' ? progress.total : null;

  if (kind === 'translation') {
    if (stage === 'translation_full') {
      return completed !== null && total !== null
        ? `全文一次翻译中（${completed}/${total}）`
        : '全文一次翻译中';
    }
    if (stage === 'translation_chunked') {
      return completed !== null && total !== null
        ? `全文分块翻译中（${completed}/${total}）`
        : '全文分块翻译中';
    }
    if (eventName === 'enqueued') {
      return mode === 'full' ? '已加入全文翻译队列' : '已加入分块翻译队列';
    }
    if (eventName === 'claimed') return '翻译任务已开始';
    if (eventName === 'succeeded') return '全文翻译完成';
    if (eventName === 'failed' || eventName === 'cancelled') {
      return event.errorCode ? `翻译失败：${event.errorCode}` : '全文翻译失败';
    }
  }

  if (stage) return stage;
  if (eventName === 'enqueued') return '已加入队列';
  if (eventName === 'claimed') return '任务已开始';
  if (eventName === 'succeeded') return '生成完成';
  if (eventName === 'failed' || eventName === 'cancelled') {
    return event.errorCode ? `生成失败：${event.errorCode}` : '生成失败';
  }
  const raw = String(event.line ?? event.message ?? '');
  return raw.startsWith('STAGE::') ? raw.split('::').slice(2).join('::') : raw;
}
