import assert from 'node:assert/strict';
import test from 'node:test';

import { formatTerminalSummary } from '../src/components/streamSummary.ts';

test('formatTerminalSummary exposes stable batch counts from a nested summary', () => {
  assert.equal(
    formatTerminalSummary({
      type: 'result',
      ok: true,
      summary: { total: 4, done: 2, failed: [{ id: 'p3' }], skipped_no_pdf: ['p4'] },
    }),
    '共 4 项 · 完成 2 项 · 失败 1 项 · 跳过 1 项',
  );
});

test('formatTerminalSummary accepts top-level batch counts and preserves zeroes', () => {
  assert.equal(
    formatTerminalSummary({ type: 'result', ok: true, total: 0, done: 0, failed: [], skipped: [] }),
    '共 0 项 · 完成 0 项 · 失败 0 项 · 跳过 0 项',
  );
});

test('formatTerminalSummary reports terminal errors before any counts', () => {
  assert.equal(
    formatTerminalSummary({
      type: 'result',
      ok: false,
      error: 'provider unavailable',
      summary: { total: 2, done: 0, failed: ['p1', 'p2'], skipped_no_pdf: [] },
    }),
    '失败：provider unavailable',
  );
});

test('formatTerminalSummary treats unmatched citation records as a completed graph rebuild', () => {
  assert.equal(
    formatTerminalSummary({
      type: 'result',
      ok: true,
      edges: 486,
      nodes: 250,
      processed: 245,
      failed: 5,
    }),
    '486 条引用边 / 250 个节点 · 5 篇未匹配',
  );
});
