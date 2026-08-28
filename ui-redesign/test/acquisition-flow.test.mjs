import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

import {
  buildAcquireSearchParams,
  buildAcquireJobParams,
  decideIngestTerminal,
  decideSearchTerminal,
  chooseAcquireQuery,
} from '../src/components/acquisitionFlow.ts';

const acquirePageSource = readFileSync(
  new URL('../src/components/AcquirePage.tsx', import.meta.url),
  'utf8',
);
const appSource = readFileSync(new URL('../src/App.tsx', import.meta.url), 'utf8');

test('the visible acquisition controls become the exact search request payload', () => {
  assert.deepEqual(
    buildAcquireSearchParams({
      query: '  retrieval augmented generation  ',
      sources: ['arxiv', 'dblp'],
      years: '2024-2027',
      max: 40,
      minRelevance: 0.85,
      expand: true,
      onlyA: true,
      selectedQueries: new Set([' large language model ', 'RAG evaluation']),
    }),
    {
      query: 'retrieval augmented generation',
      sources: ['arxiv', 'dblp'],
      years: '2024-2027',
      max: 40,
      minRelevance: 0.85,
      expand: true,
      onlyA: true,
      queries: ['large language model', 'RAG evaluation'],
    },
  );
});

test('an empty relevance value and no custom terms preserve backend defaults', () => {
  assert.deepEqual(
    buildAcquireSearchParams({
      query: 'topic',
      sources: ['semanticscholar'],
      years: '2024-2026',
      max: 10,
      minRelevance: 0,
      expand: false,
      onlyA: false,
      selectedQueries: new Set(),
    }),
    {
      query: 'topic',
      sources: ['semanticscholar'],
      years: '2024-2026',
      max: 10,
      minRelevance: 0,
      expand: false,
      onlyA: false,
      queries: undefined,
    },
  );
});

test('background jobs receive the same visible filters and expansion intent', () => {
  assert.deepEqual(
    buildAcquireJobParams({
      query: 'topic',
      sources: ['semanticscholar'],
      years: '2024-2027',
      max: 20,
      minRelevance: 0,
      expand: true,
      onlyA: true,
      selectedQueries: new Set(),
    }),
    {
      query: 'topic',
      sources: ['semanticscholar'],
      years: '2024-2027',
      max: 20,
      minRelevance: 0,
      onlyA: true,
      queries: ['topic'],
    },
  );
});

test('the default-theme shortcut never overwrites a non-empty user query', () => {
  assert.equal(
    chooseAcquireQuery('LLM interpretability'),
    'LLM interpretability',
  );
  assert.equal(
    chooseAcquireQuery('   '),
    'LLM hallucination detection and mitigation',
  );
});

test('the topbar can start a fresh acquisition session after a completed run', () => {
  assert.match(appSource, /acquireSession/);
  assert.match(appSource, /<AcquirePage\s+key=\{`acquire-\$\{acquireSession\}`\}/);
  assert.match(acquirePageSource, /开始采集/);
  assert.match(
    acquirePageSource,
    /onClick=\{\(\) => void runSearch\(undefined, new Set\(\), false\)\}/,
    'the root chip must really bypass expanded terms for a direct search',
  );
});

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
