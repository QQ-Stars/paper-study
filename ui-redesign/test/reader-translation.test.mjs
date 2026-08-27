import assert from 'node:assert/strict';
import test from 'node:test';

import {
  formatRegenerationLine,
  normalizeTranslationMode,
  translationModeHint,
  translationProgressMode,
} from '../src/components/readerTranslation.ts';

test('translation mode normalizes only the persisted full value', () => {
  assert.equal(normalizeTranslationMode('full'), 'full');
  assert.equal(normalizeTranslationMode('chunked'), 'chunked');
  assert.equal(normalizeTranslationMode('FULL'), 'chunked');
  assert.equal(normalizeTranslationMode(undefined), 'chunked');
});

test('translation hints distinguish one-shot and chunked work', () => {
  assert.match(translationModeHint('full'), /全文一次翻译/);
  assert.match(translationModeHint('chunked'), /全文分块翻译/);
  assert.notEqual(translationModeHint('full'), translationModeHint('chunked'));
});

test('progress stages reveal the mode used by the durable job', () => {
  assert.equal(
    translationProgressMode({ type: 'progress', progress: { stage: 'translation_full' } }),
    'full',
  );
  assert.equal(
    translationProgressMode({ type: 'progress', progress: { stage: 'translation_chunked' } }),
    'chunked',
  );
  assert.equal(translationProgressMode({ type: 'progress', progress: { stage: 'other' } }), null);
});

test('regeneration status renders durable queue, progress, and failure states', () => {
  assert.equal(
    formatRegenerationLine(
      { type: 'progress', event: 'enqueued' },
      'translation',
      'full',
    ),
    '已加入全文翻译队列',
  );
  assert.equal(
    formatRegenerationLine(
      {
        type: 'progress',
        event: 'progress',
        progress: { stage: 'translation_full', completed: 0, total: 1 },
      },
      'translation',
      'chunked',
    ),
    '全文一次翻译中（0/1）',
  );
  assert.equal(
    formatRegenerationLine(
      { type: 'progress', event: 'failed', errorCode: 'TRANSLATION_PROVIDER_REQUEST' },
      'translation',
      'full',
    ),
    '翻译失败：TRANSLATION_PROVIDER_REQUEST',
  );
  assert.equal(
    formatRegenerationLine(
      { type: 'progress', line: 'STAGE::load::paper' },
      'explainer',
      'chunked',
    ),
    'paper',
  );
});
