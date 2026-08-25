import assert from 'node:assert/strict';
import test from 'node:test';

import {
  decideIngestTerminal,
  decideSearchTerminal,
} from '../src/components/acquisitionFlow.ts';

test('failed import terminal blocks paper reload and reports the terminal error', () => {
  const decision = decideIngestTerminal(
    { type: 'done', ok: false, error: 'provider unavailable' },
    2,
  );

  assert.deepEqual(decision, {
    kind: 'failed',
    error: 'provider unavailable',
    reloadPapers: false,
    notification: '导入失败：provider unavailable',
  });
});

test('successful import notification uses the terminal added count', () => {
  assert.deepEqual(decideIngestTerminal({ type: 'done', ok: true, added: 1 }, 2), {
    kind: 'succeeded',
    reloadPapers: true,
    notification: '已导入 1 篇到文献库',
  });
});

test('a terminal from an obsolete search cannot replace candidates, phase, or notifications', () => {
  const decision = decideSearchTerminal(
    {
      type: 'done',
      ok: true,
      candidates: [{ title: 'Slower result' }],
    },
    false,
  );

  assert.deepEqual(decision, { kind: 'stale' });
});

test('a failed current search reports its error without entering the searched phase', () => {
  const decision = decideSearchTerminal(
    { type: 'result', ok: false, error: 'provider unavailable' },
    true,
  );

  assert.deepEqual(decision, {
    kind: 'failed',
    error: 'provider unavailable',
    notification: '检索失败：provider unavailable',
  });
});

test('a successful current search replaces candidates and enters the searched phase', () => {
  const decision = decideSearchTerminal(
    {
      type: 'done',
      ok: true,
      candidates: [{ title: 'Current result' }, null, { title: 42 }],
    },
    true,
  );

  assert.deepEqual(decision, {
    kind: 'succeeded',
    candidates: [{ title: 'Current result' }],
    phase: 'searched',
    notification: '检索完成，命中 1 篇候选',
  });
});
