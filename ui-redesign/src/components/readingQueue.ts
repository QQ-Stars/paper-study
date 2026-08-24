export const READING_QUEUE_STORAGE_KEY = 'paper-study:reading-queue';

interface ReadingQueueStorage {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
}

function normalizeReadingQueue(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  return Array.from(
    new Set(
      value.filter(
        (id): id is string => typeof id === 'string' && id.trim().length > 0,
      ),
    ),
  );
}

function persistReadingQueue(ids: readonly string[], storage: ReadingQueueStorage): void {
  try {
    storage.setItem(READING_QUEUE_STORAGE_KEY, JSON.stringify(ids));
  } catch {
    /* Keep the current-session state when browser storage is unavailable. */
  }
}

export function readReadingQueue(storage: ReadingQueueStorage = window.localStorage): string[] {
  try {
    const raw = storage.getItem(READING_QUEUE_STORAGE_KEY);
    return normalizeReadingQueue(raw ? JSON.parse(raw) : []);
  } catch {
    return [];
  }
}

export function updateReadingQueueIds(
  current: readonly string[],
  paperId: string,
  queued: boolean,
  storage: ReadingQueueStorage = window.localStorage,
): string[] {
  const next = queued
    ? normalizeReadingQueue([...current, paperId])
    : current.filter((id) => id !== paperId);
  persistReadingQueue(next, storage);
  return next;
}

export function removeReadingQueueIds(
  current: readonly string[],
  paperIds: readonly string[],
  storage: ReadingQueueStorage = window.localStorage,
): string[] {
  const removed = new Set(paperIds);
  const next = current.filter((id) => !removed.has(id));
  persistReadingQueue(next, storage);
  return next;
}
