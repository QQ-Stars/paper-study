import assert from 'node:assert/strict';
import test from 'node:test';

import {
  batchLimitInsertionText,
  batchLimitLabel,
  buildBatchLimitRequest,
  buildDownloadBatchRequest,
  isBatchLimitText,
} from '../src/components/batchLimits.ts';

test('positive per-task limits are passed through unchanged', () => {
  assert.deepEqual(buildBatchLimitRequest('7'), { valid: true, request: { limit: 7 } });
  assert.deepEqual(buildBatchLimitRequest('5'), { valid: true, request: { limit: 5 } });
  assert.deepEqual(buildBatchLimitRequest('4'), { valid: true, request: { limit: 4 } });
  assert.deepEqual(buildBatchLimitRequest('6'), { valid: true, request: { limit: 6 } });
});

test('empty and zero limits explicitly request all matching papers', () => {
  assert.deepEqual(buildBatchLimitRequest(''), { valid: true, request: { limit: 0 } });
  assert.deepEqual(buildBatchLimitRequest('0'), { valid: true, request: { limit: 0 } });
  assert.equal(batchLimitLabel(''), '全部');
  assert.equal(batchLimitLabel('0'), '全部');
});

test('PDF download request keeps the complete eligible ID list', () => {
  const ids = Array.from({ length: 27 }, (_, index) => `paper-${index + 1}`);

  assert.deepEqual(buildDownloadBatchRequest('5', ids), {
    valid: true,
    request: { ids, limit: 5 },
  });
});

test('invalid values never produce a batch request', () => {
  for (const value of ['-1', '1.5', '1e3', 'abc', '9007199254740992']) {
    const result = buildBatchLimitRequest(value);
    assert.equal(result.valid, false, `${value} should be rejected`);
    assert.equal('request' in result, false, `${value} must not produce a request`);
  }
});

test('only empty text or decimal digits are accepted as editable input text', () => {
  for (const value of ['', '0', '7', '0012']) assert.equal(isBatchLimitText(value), true);
  for (const value of ['-1', '1.5', '1e3', 'abc', '+2']) {
    assert.equal(isBatchLimitText(value), false, `${value} should be rejected while editing`);
  }
});

test('beforeinput text prefers React data and safely handles native-event fallbacks', () => {
  assert.equal(batchLimitInsertionText('7', undefined), '7');
  assert.equal(batchLimitInsertionText(undefined, '8'), '8');
  assert.equal(batchLimitInsertionText(undefined, undefined), null);
  assert.equal(batchLimitInsertionText(undefined, null), null);
});

test('a rejected native input attempt cannot produce a request from its displayed value', () => {
  for (const value of ['', '10']) {
    const result = buildBatchLimitRequest(value, true);
    assert.equal(result.valid, false);
    assert.equal('request' in result, false);
  }
});
