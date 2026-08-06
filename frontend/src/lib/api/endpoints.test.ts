import { describe, expect, it, vi } from 'vitest';

import { BusinessError } from './errors';
import { createApiClient } from './client';
import { createAcquisitionGateway } from './acquisitionGateway';
import { createPaperApi } from './paperApi';
import { createSettingsGateway } from './settingsGateway';

describe('typed endpoint commands', () => {
  it('uses origin-relative URLs and captures the supplied paper id in JSON mutations', async () => {
    const fetchImpl = vi.fn(async () => new Response('{"ok":true}', {
      status: 200, headers: { 'Content-Type': 'application/json' },
    }));
    const papers = createPaperApi(createApiClient(fetchImpl));

    await papers.saveNote('paper/one', 'fixed note');

    expect(fetchImpl).toHaveBeenCalledWith('/api/note', expect.objectContaining({
      method: 'POST',
      body: JSON.stringify({ id: 'paper/one', content: 'fixed note' }),
    }));
  });

  it('decodes the nullable paper-detail response without fabricating status', async () => {
    const fetchImpl = vi.fn(async () => new Response('null', { status: 200 }));
    const papers = createPaperApi(createApiClient(fetchImpl));

    await expect(papers.getPaper('missing')).resolves.toBeNull();
    expect(fetchImpl).toHaveBeenCalledWith('/api/paper/get?id=missing', expect.any(Object));
  });

  it('turns a 200 LLM command failure into BusinessError', async () => {
    const fetchImpl = vi.fn(async () => new Response('{"ok":false,"output":"missing key"}', {
      status: 200, headers: { 'Content-Type': 'application/json' },
    }));
    const settings = createSettingsGateway(createApiClient(fetchImpl));

    await expect(settings.testLlm()).rejects.toEqual(expect.objectContaining({
      kind: 'business', message: 'missing key',
    }));
    expect(fetchImpl).toHaveBeenCalledWith('/api/test-llm', expect.objectContaining({ method: 'POST' }));
  });

  it('fully reads a failed stream terminal then surfaces its business failure', async () => {
    const encoder = new TextEncoder();
    const fetchImpl = vi.fn(async () => new Response(new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(encoder.encode('{"type":"progress","line":"working"}\n'));
        controller.enqueue(encoder.encode('{"type":"result","ok":false,"error":"agent failed"}\n'));
        controller.close();
      },
    }), { status: 200 }));
    const acquisition = createAcquisitionGateway(createApiClient(fetchImpl));

    await expect(acquisition.search({ query: 'vision', sources: ['dblp'] })).rejects.toBeInstanceOf(BusinessError);
    expect(fetchImpl).toHaveBeenCalledTimes(1);
  });
});
