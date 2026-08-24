import assert from 'node:assert/strict';
import test from 'node:test';

import {
  READING_QUEUE_STORAGE_KEY,
  readReadingQueue,
  removeReadingQueueIds,
  updateReadingQueueIds,
} from '../src/components/readingQueue.ts';

function memoryStorage(initial = null) {
  const values = new Map();
  if (initial !== null) values.set(READING_QUEUE_STORAGE_KEY, initial);
  return {
    getItem: (key) => values.get(key) ?? null,
    setItem: (key, value) => values.set(key, value),
    value: () => values.get(READING_QUEUE_STORAGE_KEY) ?? null,
  };
}

test('reading queue storage accepts only unique non-empty paper ids', () => {
  const storage = memoryStorage(JSON.stringify(['paper-1', '', 3, 'paper-1', 'paper-2']));

  assert.deepEqual(readReadingQueue(storage), ['paper-1', 'paper-2']);
});

test('reading queue updates persist additions and removals', () => {
  const storage = memoryStorage();
  const queued = updateReadingQueueIds(['paper-1'], 'paper-2', true, storage);
  const removed = updateReadingQueueIds(queued, 'paper-1', false, storage);

  assert.deepEqual(queued, ['paper-1', 'paper-2']);
  assert.deepEqual(removed, ['paper-2']);
  assert.equal(storage.value(), JSON.stringify(['paper-2']));
});

test('successfully deleted papers are removed from the persisted reading queue', () => {
  const storage = memoryStorage(JSON.stringify(['paper-1', 'paper-2', 'paper-3']));

  const remaining = removeReadingQueueIds(
    ['paper-1', 'paper-2', 'paper-3'],
    ['paper-1', 'paper-3'],
    storage,
  );

  assert.deepEqual(remaining, ['paper-2']);
  assert.equal(storage.value(), JSON.stringify(['paper-2']));
});

test('invalid or unavailable storage safely yields an empty queue', () => {
  const invalid = memoryStorage('{invalid json');
  const unavailable = {
    getItem: () => { throw new Error('blocked'); },
    setItem: () => { throw new Error('blocked'); },
  };

  assert.deepEqual(readReadingQueue(invalid), []);
  assert.deepEqual(readReadingQueue(unavailable), []);
  assert.deepEqual(updateReadingQueueIds([], 'paper-1', true, unavailable), ['paper-1']);
});
