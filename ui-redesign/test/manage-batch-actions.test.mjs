import assert from 'node:assert/strict';
import test from 'node:test';

import {
  assertMutationOk,
  recoverBatchFailure,
} from '../src/components/manageBatchActions.ts';

test('assertMutationOk accepts only an explicit successful mutation response', () => {
  assert.doesNotThrow(() => assertMutationOk({ ok: true }));
  assert.throws(
    () => assertMutationOk({ ok: false, error: 'permission denied' }),
    /permission denied/,
  );
  assert.throws(() => assertMutationOk({ ok: false }), /操作失败/);
  assert.throws(() => assertMutationOk({}), /操作失败/);
});

test('recoverBatchFailure refreshes papers and status independently', async () => {
  const calls = [];
  await recoverBatchFailure(
    async () => {
      calls.push('papers');
      throw new Error('stale reload failed');
    },
    async () => {
      calls.push('status');
      throw new Error('status refresh failed');
    },
  );
  assert.deepEqual(calls, ['papers', 'status']);
});
