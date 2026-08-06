import { describe, expect, it, vi } from 'vitest';

import { ApiError } from './errors';
import { api } from './client';
import { arrayOf, decodePaper, object, string } from './decoders';

const fetchWith = (response: Response) => vi.fn(async () => response);

describe('typed HTTP transport', () => {
  it('decodes a valid JSON response through an unknown-input decoder', async () => {
    const fetchImpl = fetchWith(new Response(JSON.stringify([{
      id: 'p1', file: 'p1.pdf', title: 'Paper', hasNote: 0, favorite: 0, hasPdf: false,
    }]), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    }));

    await expect(api.json('/api/papers', arrayOf(decodePaper), { fetchImpl })).resolves.toEqual([
      expect.objectContaining({ id: 'p1', title: 'Paper', status: '未开始' }),
    ]);
  });

  it('reports a JSON protocol error when the payload is not JSON', async () => {
    const fetchImpl = fetchWith(new Response('not-json', { status: 200 }));

    await expect(api.json('/api/papers', arrayOf(decodePaper), { fetchImpl })).rejects.toMatchObject({
      kind: 'protocol',
      message: expect.stringContaining('JSON'),
    });
  });

  it('reports a decoder error with its value path', async () => {
    const fetchImpl = fetchWith(new Response(JSON.stringify({ ok: 3 }), { status: 200 }));

    await expect(api.json('/api/example', object({ ok: string }), { fetchImpl })).rejects.toMatchObject({
      kind: 'decode',
      message: expect.stringContaining('$.ok'),
    });
  });

  it('keeps an empty text body as a valid value', async () => {
    const fetchImpl = fetchWith(new Response('', { status: 200 }));

    await expect(api.text('/api/note?id=p1', { fetchImpl })).resolves.toBe('');
  });

  it('returns response bytes unchanged', async () => {
    const fetchImpl = fetchWith(new Response(new Uint8Array([37, 80, 68, 70]), { status: 200 }));

    const value = await api.bytes('/pdfbytes?id=p1', { fetchImpl });
    expect([...new Uint8Array(value)]).toEqual([37, 80, 68, 70]);
  });

  it('includes a non-2xx response body in the structured HTTP error', async () => {
    const fetchImpl = fetchWith(new Response('论文不存在', { status: 404, statusText: 'Not Found' }));

    await expect(api.text('/api/explainer?id=missing', { fetchImpl })).rejects.toEqual(
      expect.objectContaining<Partial<ApiError>>({
        kind: 'http',
        status: 404,
        body: '论文不存在',
      }),
    );
  });

  it('consumes a non-2xx JSON body once and preserves its structured detail', async () => {
    const response = new Response('{"ok":false,"error":"bad request","code":"invalid"}', {
      status: 400, statusText: 'Bad Request', headers: { 'Content-Type': 'application/json' },
    });
    const textSpy = vi.spyOn(response, 'text');
    const fetchImpl = fetchWith(response);

    await expect(api.text('/api/example', { fetchImpl })).rejects.toMatchObject({
      kind: 'http', status: 400,
      body: { ok: false, error: 'bad request', code: 'invalid' },
    });
    expect(textSpy).toHaveBeenCalledTimes(1);
  });

  it('wraps a transport failure as a typed network error', async () => {
    const fetchImpl = vi.fn(async () => { throw new TypeError('connection reset'); });

    await expect(api.text('/api/papers', { fetchImpl })).rejects.toMatchObject({
      kind: 'network',
      message: expect.stringContaining('connection reset'),
      requestMethod: 'GET',
    });
  });

  it('records the normalized method on transport and HTTP failures', async () => {
    const offline = vi.fn(async () => {
      throw new TypeError('offline');
    });

    await expect(api.text('/api/paper/update', {
      method: 'post',
      fetchImpl: offline,
    })).rejects.toMatchObject({
      kind: 'network',
      requestMethod: 'POST',
    });

    await expect(api.text('/api/paper/update', {
      method: 'PATCH',
      fetchImpl: fetchWith(new Response('', { status: 503 })),
    })).rejects.toMatchObject({
      kind: 'http',
      status: 503,
      requestMethod: 'PATCH',
    });
  });

  it('preserves AbortError identity instead of wrapping cancellation', async () => {
    const aborted = new DOMException('cancelled', 'AbortError');
    const fetchImpl = vi.fn(async () => { throw aborted; });

    await expect(api.text('/api/note?id=p1', { fetchImpl })).rejects.toBe(aborted);
  });
});
