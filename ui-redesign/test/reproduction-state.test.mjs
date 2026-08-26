import test from 'node:test';
import assert from 'node:assert/strict';

import {
  DEFAULT_REPRODUCTION_LIST_STATE,
  readReproductionListState,
  writeReproductionListState,
} from '../src/components/reproductionState.ts';

function memoryStorage(initial = {}) {
  const values = new Map(Object.entries(initial));
  return {
    getItem(key) {
      return values.has(key) ? values.get(key) : null;
    },
    setItem(key, value) {
      values.set(key, String(value));
    },
  };
}

test('reproduction list state restores filters and scroll position', () => {
  const storage = memoryStorage({
    'paper-study:reproduction-list': JSON.stringify({
      query: 'vision',
      status: 'running',
      tag: 'baseline',
      sort: 'created',
      scrollTop: 388,
    }),
  });

  assert.deepEqual(readReproductionListState(storage), {
    query: 'vision',
    status: 'running',
    tag: 'baseline',
    sort: 'created',
    scrollTop: 388,
  });
});

test('reproduction list state rejects malformed values and writes a stable snapshot', () => {
  const storage = memoryStorage({
    'paper-study:reproduction-list': '{not-json',
  });

  assert.deepEqual(readReproductionListState(storage), DEFAULT_REPRODUCTION_LIST_STATE);
  writeReproductionListState(storage, {
    query: '  ',
    status: '',
    tag: '',
    sort: 'updated',
    scrollTop: -20,
  });
  assert.equal(
    storage.getItem('paper-study:reproduction-list'),
    JSON.stringify(DEFAULT_REPRODUCTION_LIST_STATE),
  );
});
