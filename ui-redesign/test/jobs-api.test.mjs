import assert from 'node:assert/strict';
import test from 'node:test';

import { v2Api } from '../src/api/client.ts';
import {
  appendJobEvents,
  buildJobListParams,
  canCancelJob,
  canRetryJob,
  createJobHistoryPage,
  moveToNextJobPage,
  moveToPreviousJobPage,
} from '../src/components/jobHistory.ts';

const originalFetch = globalThis.fetch;

function mockJson(value, status = 200) {
  globalThis.fetch = async (input, init) => {
    mockJson.lastRequest = { input, init };
    return {
      ok: status >= 200 && status < 300,
      status,
      json: async () => value,
      text: async () => JSON.stringify(value),
    };
  };
}

test.afterEach(() => {
  globalThis.fetch = originalFetch;
});

test('job events expose the server page and continue from its sequence cursor', async () => {
  mockJson({
    items: [{ sequence: 5, type: 'progress', progress: { completed: 1 } }],
    nextAfterSequence: 5,
  });

  const page = await v2Api.jobEvents('job/1', 4);

  assert.deepEqual(page, {
    items: [{ sequence: 5, type: 'progress', progress: { completed: 1 } }],
    nextAfterSequence: 5,
  });
  assert.equal(mockJson.lastRequest.input, '/api/v2/jobs/job%2F1/events?afterSequence=4');
});

test('job listing forwards the durable filters without changing the response envelope', async () => {
  mockJson({ items: [], nextCursor: null });

  const result = await v2Api.listJobs({
    paperId: 'paper 1',
    status: 'failed',
    jobType: 'ocr',
    limit: 20,
    cursor: 'next/page',
  });

  assert.deepEqual(result, { items: [], nextCursor: null });
  const url = new URL(`http://localhost${mockJson.lastRequest.input}`);
  assert.equal(url.pathname, '/api/v2/jobs');
  assert.deepEqual(Object.fromEntries(url.searchParams), {
    paperId: 'paper 1',
    status: 'failed',
    jobType: 'ocr',
    limit: '20',
    cursor: 'next/page',
  });
});

test('retry responses preserve the new job envelope', async () => {
  mockJson({
    job: { id: 'new-job', paperId: 'paper-1', jobType: 'ocr', sourceMode: 'ocr', status: 'queued' },
    retriedFromJobId: 'old-job',
    deduplicated: false,
  });

  const result = await v2Api.retryJob('old-job');

  assert.equal(result.job.id, 'new-job');
  assert.equal(result.retriedFromJobId, 'old-job');
  assert.equal(result.deduplicated, false);
});

test('durable job actions follow the public status contract', () => {
  assert.equal(canCancelJob('queued'), true);
  assert.equal(canCancelJob('running'), true);
  assert.equal(canCancelJob('failed'), false);
  assert.equal(canRetryJob('failed'), true);
  assert.equal(canRetryJob('cancelled'), true);
  assert.equal(canRetryJob('running'), false);
});

test('job history filters omit the all options and reset their cursor on filter changes', () => {
  assert.deepEqual(buildJobListParams({ status: 'all', jobType: 'all', paperId: '  ' }, null), {});
  assert.deepEqual(
    buildJobListParams({ status: 'failed', jobType: 'ocr', paperId: ' paper-1 ' }, 'cursor-2'),
    { status: 'failed', jobType: 'ocr', paperId: 'paper-1', cursor: 'cursor-2' },
  );
  assert.deepEqual(buildJobListParams({ status: 'queued', jobType: 'all', paperId: '' }, null), {
    status: 'queued',
  });
});

test('job history pagination follows next cursors and returns to the prior cursor', () => {
  const initial = createJobHistoryPage();
  const second = moveToNextJobPage(initial, 'cursor-2');
  const third = moveToNextJobPage(second, 'cursor-3');

  assert.deepEqual(initial, { index: 0, cursors: [null] });
  assert.deepEqual(second, { index: 1, cursors: [null, 'cursor-2'] });
  assert.deepEqual(third, { index: 2, cursors: [null, 'cursor-2', 'cursor-3'] });
  assert.deepEqual(moveToPreviousJobPage(third), { index: 1, cursors: [null, 'cursor-2', 'cursor-3'] });
  assert.deepEqual(moveToPreviousJobPage(initial), initial);
});

test('event pages append only newer sequences when a detail is refreshed', () => {
  const first = [{ sequence: 1, type: 'started' }, { sequence: 2, type: 'progress' }];
  const second = [
    { sequence: 2, type: 'progress' },
    { sequence: 3, type: 'failed', error: { code: 'PROVIDER_TIMEOUT', message: 'timeout' } },
  ];

  assert.deepEqual(appendJobEvents(first, second), [
    { sequence: 1, type: 'started' },
    { sequence: 2, type: 'progress' },
    { sequence: 3, type: 'failed', error: { code: 'PROVIDER_TIMEOUT', message: 'timeout' } },
  ]);
});
