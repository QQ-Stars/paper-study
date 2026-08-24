import assert from 'node:assert/strict';
import test from 'node:test';

import {
  BATCH_TASKS,
  buildBatchTaskRequest,
  createBatchLimitDraft,
  createBatchLimitDrafts,
} from '../src/components/batchTasks.ts';

test('batch task registry exposes the existing defaults and accessible controls', () => {
  assert.deepEqual(
    Object.fromEntries(Object.entries(BATCH_TASKS).map(([key, task]) => [key, task.defaultValue])),
    {
      titleTranslations: '10',
      pdfDownloads: '20',
      explanations: '3',
      ocrMarkdown: '3',
      metadataEnrichment: '10',
    },
  );
  assert.equal(BATCH_TASKS.titleTranslations.accessibleName, '本次处理篇数：标题翻译');
  assert.equal(BATCH_TASKS.pdfDownloads.inputId, 'pdf-download-limit');
});

test('each registered task builds its public request from the current draft', () => {
  assert.deepEqual(buildBatchTaskRequest('titleTranslations', '7'), {
    valid: true,
    request: { limit: 7 },
  });
  assert.deepEqual(buildBatchTaskRequest('pdfDownloads', '5', false, ['a', 'b']), {
    valid: true,
    request: { ids: ['a', 'b'], limit: 5 },
  });
  assert.deepEqual(buildBatchTaskRequest('explanations', '4'), {
    valid: true,
    request: { limit: 4 },
  });
  assert.deepEqual(buildBatchTaskRequest('ocrMarkdown', '6'), {
    valid: true,
    request: { limit: 6 },
  });
  assert.deepEqual(buildBatchTaskRequest('metadataEnrichment', ''), {
    valid: true,
    request: { limit: 0 },
  });
});

test('invalid values stay invalid for every registered task', () => {
  for (const key of Object.keys(BATCH_TASKS)) {
    const result = buildBatchTaskRequest(key, '1e3', false, ['paper']);
    assert.equal(result.valid, false, `${key} should reject exponent input`);
    assert.equal('request' in result, false);
  }
});

test('draft defaults are independent objects', () => {
  const first = createBatchLimitDraft('titleTranslations');
  const second = createBatchLimitDraft('titleTranslations');
  first.value = '99';
  assert.deepEqual(second, { value: '10', inputInvalid: false });
});

test('all task drafts start with independent configured defaults', () => {
  const drafts = createBatchLimitDrafts();
  assert.deepEqual(drafts, {
    titleTranslations: { value: '10', inputInvalid: false },
    pdfDownloads: { value: '20', inputInvalid: false },
    explanations: { value: '3', inputInvalid: false },
    ocrMarkdown: { value: '3', inputInvalid: false },
    metadataEnrichment: { value: '10', inputInvalid: false },
  });
  drafts.pdfDownloads.value = '1';
  assert.equal(drafts.titleTranslations.value, '10');
});
