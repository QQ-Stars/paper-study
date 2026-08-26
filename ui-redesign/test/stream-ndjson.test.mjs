import assert from 'node:assert/strict';
import test from 'node:test';

import { artifactApi, streamNdjson } from '../src/api/client.ts';

const originalFetch = globalThis.fetch;

function streamResponse(chunks, { status = 200, failAfter } = {}) {
  const encoder = new TextEncoder();
  let index = 0;
  return new Response(
    new ReadableStream({
      pull(controller) {
        if (failAfter === index) {
          controller.error(new Error('fixture stream interrupted'));
          return;
        }
        if (index >= chunks.length) {
          controller.close();
          return;
        }
        controller.enqueue(encoder.encode(chunks[index]));
        index += 1;
      },
    }),
    { status, headers: { 'Content-Type': 'application/x-ndjson' } },
  );
}

test.afterEach(() => {
  globalThis.fetch = originalFetch;
});

test('streamNdjson emits split events and accepts a final result event', async () => {
  const events = [];
  globalThis.fetch = async () =>
    streamResponse([
      '{"type":"progress","line":"STAGE::',
      'search"}\n{"type":"result","ok":true,"candidates":[{"title":"fixture"}]}\n',
    ]);

  await streamNdjson('/api/search', { query: 'fixture' }, (event) => events.push(event));

  assert.deepEqual(events, [
    { type: 'progress', line: 'STAGE::search' },
    { type: 'result', ok: true, candidates: [{ title: 'fixture' }] },
  ]);
});

test('streamNdjson reports the HTTP status and bounded response detail', async () => {
  globalThis.fetch = async () =>
    new Response('provider unavailable', { status: 503, statusText: 'Service Unavailable' });

  await assert.rejects(
    streamNdjson('/api/search', {}, () => {}),
    /HTTP 503: provider unavailable/,
  );
});

test('streamNdjson rejects an interrupted response body', async () => {
  globalThis.fetch = async () =>
    streamResponse(['{"type":"progress","line":"searching"}\n'], { failAfter: 1 });

  await assert.rejects(
    streamNdjson('/api/search', {}, () => {}),
    /fixture stream interrupted/,
  );
});

test('streamNdjson rejects a completed response without a terminal event', async () => {
  globalThis.fetch = async () =>
    streamResponse(['{"type":"progress","line":"searching"}\n']);

  await assert.rejects(
    streamNdjson('/api/search', {}, () => {}),
    /\u6d41\u5f0f\u4efb\u52a1\u672a\u8fd4\u56de\u5b8c\u6210\u72b6\u6001/,
  );
});

test('streamNdjson surfaces a failed terminal event after delivering it', async () => {
  const events = [];
  globalThis.fetch = async () =>
    streamResponse(['{"type":"done","ok":false,"error":"provider unavailable"}\n']);

  await assert.rejects(
    streamNdjson('/api/ingest-selected', {}, (event) => events.push(event)),
    /provider unavailable/,
  );
  assert.deepEqual(events, [{ type: 'done', ok: false, error: 'provider unavailable' }]);
});

test('streamNdjson rejects an event received after the terminal event', async () => {
  globalThis.fetch = async () =>
    streamResponse([
      '{"type":"done","ok":true}\n{"type":"progress","line":"late"}\n',
    ]);

  await assert.rejects(
    streamNdjson('/api/search', {}, () => {}),
    /流式任务完成后仍收到事件/,
  );
});

test('streamNdjson rejects duplicate terminal events', async () => {
  globalThis.fetch = async () =>
    streamResponse(['{"type":"result","ok":true}\n{"type":"done","ok":true}\n']);

  await assert.rejects(
    streamNdjson('/api/search', {}, () => {}),
    /流式任务返回多个完成状态/,
  );
});

test('streamNdjson reports malformed NDJSON instead of silently dropping it', async () => {
  globalThis.fetch = async () =>
    streamResponse(['{"type":"progress","line":"searching"}\nnot-json\n']);

  await assert.rejects(
    streamNdjson('/api/search', {}, () => {}),
    /第 2 行不是有效 JSON/,
  );
});

test('streamNdjson rejects a successful response with a non-NDJSON content type', async () => {
  globalThis.fetch = async () =>
    new Response('{"type":"result","ok":true}\n', {
      status: 200,
      headers: { 'Content-Type': 'application/json; charset=utf-8' },
    });

  await assert.rejects(
    streamNdjson('/api/search', {}, () => {}),
    /Content-Type.*application\/x-ndjson/,
  );
});

test('artifact text reads reject HTTP errors instead of presenting an empty artifact', async () => {
  globalThis.fetch = async () =>
    new Response('artifact store unavailable', { status: 503 });

  await assert.rejects(
    artifactApi.getExplainer('paper-1'),
    /HTTP 503: artifact store unavailable/,
  );
});

test('streamNdjson rejects a terminal event without a boolean ok field', async () => {
  globalThis.fetch = async () =>
    streamResponse(['{"type":"result","candidates":[] }\n']);

  await assert.rejects(
    streamNdjson('/api/search', {}, () => {}),
    /\u5b8c\u6210\u72b6\u6001\u7f3a\u5c11\u6709\u6548 ok \u5b57\u6bb5/,
  );
});
