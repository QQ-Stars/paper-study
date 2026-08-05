import { describe, expect, it, vi } from 'vitest';

import { api } from '../api/client';
import {
  citationBuildContract,
  doneContract,
  downloadPdfsContract,
  embedContract,
  explainBatchContract,
  explainContract,
  importPdfsContract,
  ingestSelectedContract,
  jobsConfirmContract,
  normalizeVenuesContract,
  recommendContract,
  resultContract,
  searchContract,
  semanticSearchContract,
  titleTranslationsContract,
  translateContract,
  verifyVenueContract,
} from './contracts';
import type { StreamContract } from './contracts';

const streamResponse = (...chunks: string[]) => new Response(new ReadableStream<Uint8Array>({
  start(controller) {
    const encoder = new TextEncoder();
    chunks.forEach((chunk) => controller.enqueue(encoder.encode(chunk)));
    controller.close();
  },
}), { status: 200 });

const fetchWith = (response: Response) => vi.fn(async () => response);

describe('NDJSON protocol reader', () => {
  it('parses events split across arbitrary chunks and returns the terminal result', async () => {
    const events: unknown[] = [];
    const response = streamResponse('{"type":"pro', 'gress","line":"one"}\n{"type":"result","ok":true,"candidates":[]');
    const fetchImpl = fetchWith(new Response(new ReadableStream<Uint8Array>({
      start(controller) {
        const encoder = new TextEncoder();
        controller.enqueue(encoder.encode('{"type":"pro'));
        controller.enqueue(encoder.encode('gress","line":"one"}\n{"type":"result","ok":true,"candidates":[]'));
        controller.enqueue(encoder.encode('}'));
        controller.close();
      },
    }), { status: 200 }));

    const terminal = await api.ndjson('/api/search', resultContract, {
      fetchImpl,
      onEvent: (event) => events.push(event),
    });

    expect(terminal).toEqual({ type: 'result', ok: true, candidates: [] });
    expect(events).toEqual([
      { type: 'progress', line: 'one' },
      { type: 'result', ok: true, candidates: [] },
    ]);
    expect(response.body).toBeTruthy();
  });

  it('consumes a final residual line without a newline', async () => {
    const fetchImpl = fetchWith(streamResponse('{"type":"done","ok":true,"added":2}'));

    await expect(api.ndjson('/api/ingest-selected', doneContract, { fetchImpl })).resolves.toEqual({
      type: 'done', ok: true, added: 2,
    });
  });

  it('accepts the server JSON fallback when no readable stream body is available', async () => {
    const fallback = new Response(null, { status: 200 });
    Object.defineProperty(fallback, 'body', { value: null });
    fallback.json = vi.fn(async () => ({ type: 'result', ok: true, candidates: [] }));
    const fetchImpl = fetchWith(fallback);

    await expect(api.ndjson('/api/search', resultContract, { fetchImpl })).resolves.toEqual({
      type: 'result', ok: true, candidates: [],
    });
  });

  it('does not synthesize a terminal for a no-body nonterminal JSON event', async () => {
    const fallback = new Response(null, { status: 200 });
    Object.defineProperty(fallback, 'body', { value: null });
    fallback.json = vi.fn(async () => ({ type: 'progress', line: 'working' }));
    const fetchImpl = fetchWith(fallback);

    await expect(api.ndjson('/api/search', resultContract, { fetchImpl })).rejects.toMatchObject({
      kind: 'protocol', code: 'missing-terminal',
    });
  });

  it('rejects malformed JSON with its one-based physical line', async () => {
    const fetchImpl = fetchWith(streamResponse('\n{"type":"progress","line":}\n'));

    await expect(api.ndjson('/api/search', resultContract, { fetchImpl })).rejects.toMatchObject({
      kind: 'protocol', code: 'invalid-json', line: 2,
    });
  });

  it('types a streaming read failure as a network error', async () => {
    const response = new Response(new ReadableStream<Uint8Array>({
      start(controller) {
        controller.error(new TypeError('stream reset'));
      },
    }), { status: 200 });

    await expect(api.ndjson('/api/search', resultContract, {
      fetchImpl: fetchWith(response),
    })).rejects.toMatchObject({ kind: 'network', message: expect.stringContaining('stream reset') });
  });

  it('defers terminal observation and promise resolution until EOF is drained', async () => {
    let closeStream: () => void = () => {};
    const allowClose = new Promise<void>((resolve) => { closeStream = resolve; });
    const observed: unknown[] = [];
    const encoder = new TextEncoder();
    const response = new Response(new ReadableStream<Uint8Array>({
      async start(controller) {
        controller.enqueue(encoder.encode('{"type":"result","ok":true}\n'));
        await allowClose;
        controller.close();
      },
    }), { status: 200 });
    const promise = api.ndjson('/api/search', resultContract, {
      fetchImpl: fetchWith(response), onEvent: (event) => observed.push(event),
    });

    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(observed).toEqual([]);
    closeStream();
    await expect(promise).resolves.toMatchObject({ type: 'result', ok: true });
    expect(observed).toEqual([{ type: 'result', ok: true }]);
  });

  it('rejects a stream that ends before its terminal event', async () => {
    const fetchImpl = fetchWith(streamResponse('{"type":"progress","line":"working"}\n'));

    await expect(api.ndjson('/api/search', resultContract, { fetchImpl })).rejects.toMatchObject({
      kind: 'protocol',
      code: 'missing-terminal',
    });
  });

  it('rejects duplicate terminal events', async () => {
    const fetchImpl = fetchWith(streamResponse(
      '{"type":"result","ok":true}\n{"type":"result","ok":true}\n',
    ));

    await expect(api.ndjson('/api/search', resultContract, { fetchImpl })).rejects.toMatchObject({
      kind: 'protocol',
      code: 'duplicate-terminal',
    });
  });

  it('rejects any event after the terminal event', async () => {
    const fetchImpl = fetchWith(streamResponse(
      '{"type":"result","ok":true}\n{"type":"progress","line":"late"}\n',
    ));

    await expect(api.ndjson('/api/search', resultContract, { fetchImpl })).rejects.toMatchObject({
      kind: 'protocol',
      code: 'post-terminal-event',
    });
  });

  it('does not retry a side-effecting stream request', async () => {
    const fetchImpl = vi.fn(async () => { throw new TypeError('network down'); });

    await expect(api.ndjson('/api/jobs/confirm', doneContract, {
      method: 'POST',
      body: JSON.stringify({ jobId: 1 }),
      fetchImpl,
    })).rejects.toMatchObject({ kind: 'network' });
    expect(fetchImpl).toHaveBeenCalledTimes(1);
  });

  it('decodes the structured title-translation progress protocol', async () => {
    const events: unknown[] = [];
    const fetchImpl = fetchWith(streamResponse(
      '{"type":"progress","stage":"batch","total":1}\n',
      '{"type":"progress","stage":"item","state":"done","index":1,"total":1,"id":"p1","title_zh":"论文"}\n',
      '{"type":"result","ok":true,"summary":{"total":1,"done":1,"failed":[],"cancelled":false}}\n',
    ));

    const terminal = await api.ndjson('/api/title-translations', titleTranslationsContract, {
      fetchImpl, onEvent: (event) => events.push(event),
    });
    expect(terminal.ok).toBe(true);
    expect(events[1]).toMatchObject({ stage: 'item', state: 'done', id: 'p1' });
  });

  it('accepts endpoint-specific failure terminals without success-only fields', async () => {
    const fetchImpl = fetchWith(streamResponse('{"type":"result","ok":false,"error":"failed"}\n'));

    await expect(api.ndjson('/api/search', searchContract, { fetchImpl })).resolves.toEqual({
      type: 'result', ok: false, error: 'failed',
    });
  });

  it('decodes every remaining endpoint-specific terminal contract', async () => {
    const check = async <E, T>(contract: StreamContract<E, T>, terminal: T) => {
      const fetchImpl = fetchWith(streamResponse(`${JSON.stringify(terminal)}\n`));
      await expect(api.ndjson('/api/stream', contract, { fetchImpl })).resolves.toEqual(terminal);
    };
    await check(verifyVenueContract, { type: 'result', ok: true, verifications: [] });
    await check(explainContract, { type: 'result', ok: true, markdown: '# Paper', error: '' });
    await check(translateContract, { type: 'result', ok: true, markdown: '# 译文', error: '' });
    await check(recommendContract, { type: 'result', ok: true, candidates: [], error: '' });
    await check(embedContract, { type: 'result', ok: true, indexed: 2, total: 3, error: '' });
    await check(semanticSearchContract, { type: 'result', ok: true, results: [{ id: 'p1', score: 0.9 }], error: '' });
    await check(importPdfsContract, { type: 'result', ok: true, added: 1, dup: 0, failed: 0, error: '' });
    await check(downloadPdfsContract, { type: 'result', ok: true, downloaded: 1, skipped: 0, failed: 0, total: 1, error: '' });
    await check(normalizeVenuesContract, { type: 'result', ok: true, changed: 1, mapping: { cvpr: 'CVPR' }, error: '' });
    await check(citationBuildContract, { type: 'result', ok: true, edges: 2, nodes: 3, error: '' });
    await check(ingestSelectedContract, { type: 'done', ok: true, added: 1 });
    await check(jobsConfirmContract, { type: 'done', ok: true, added: 1 });
  });

  it('normalizes the explain-batch summary fields', async () => {
    const fetchImpl = fetchWith(streamResponse(`${JSON.stringify({
      type: 'result', ok: true,
      summary: { total: 1, done: 1, failed: [], skipped_no_pdf: [] }, error: '',
    })}\n`));
    await expect(api.ndjson('/api/explain-batch', explainBatchContract, { fetchImpl })).resolves.toEqual({
      type: 'result', ok: true,
      summary: { total: 1, done: 1, failed: [], skippedNoPdf: [] }, error: '',
    });
  });
});
