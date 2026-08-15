import { describe, expect, it, vi } from 'vitest';

import type { ApiClient, TransportOptions } from './client';
import { createArtifactGateway } from './artifactGateway';
import { createInsightsGateway } from './insightsGateway';
import { createProcessingGateway } from './processingGateway';
import type { Decoder } from './types';

interface JsonCall {
  input: string;
  options: RequestInit | undefined;
}

class ScriptedClient implements ApiClient {
  readonly calls: JsonCall[] = [];
  readonly ndjsonCalls: JsonCall[] = [];
  readonly textCalls: JsonCall[] = [];

  constructor(
    private readonly responses: unknown[] = [],
    private readonly textResponses: string[] = [],
  ) {}

  async json<T>(
    input: RequestInfo | URL,
    decoder: Decoder<T>,
    options?: TransportOptions<unknown, unknown>,
  ): Promise<T> {
    this.calls.push({ input: String(input), options });
    if (this.responses.length === 0) throw new Error('unexpected JSON request');
    return decoder(this.responses.shift(), '$');
  }

  async text(
    input: RequestInfo | URL,
    options?: TransportOptions<unknown, unknown>,
  ): Promise<string> {
    this.textCalls.push({ input: String(input), options });
    const response = this.textResponses.shift();
    if (response === undefined) throw new Error('unexpected text request');
    return response;
  }

  async bytes(): Promise<ArrayBuffer> {
    throw new Error('unexpected bytes request');
  }

  async ndjson<E, T>(
    input: RequestInfo | URL,
    _contract: unknown,
    options?: TransportOptions<E, T>,
  ): Promise<T> {
    this.ndjsonCalls.push({ input: String(input), options });
    throw new Error('unexpected NDJSON request');
  }
}

const searchResponse = {
  items: [{
    paperId: 'paper/one',
    sourceDocumentId: 'source-1',
    chunkId: 'chunk-1',
    sequence: 0,
    headingPath: ['Methods', 'Evaluation'],
    pageStart: null,
    pageEnd: 4,
    excerpt: 'evaluation protocol',
    score: null,
    lexicalScore: 0.8,
    semanticScore: null,
  }],
  coverage: {
    readyChunks: 2,
    embeddedChunks: 1,
    staleChunks: 0,
    failedEmbeddings: 1,
  },
};

const indexEnqueue = {
  job: {
    id: 'job-index-1',
    paperId: 'paper/one',
    jobType: 'embed',
    sourceMode: 'native',
    status: 'queued',
  },
  deduplicated: false,
};

const indexStatus = {
  totalChunks: 2,
  readyChunks: 2,
  embeddedChunks: 1,
  staleChunks: 0,
  failedEmbeddings: 1,
  provider: 'fixture-provider',
  model: 'fixture-model',
  version: 'fixture-v1',
  coverage: 'partial',
};

const sourceEnqueue = {
  source: {
    id: 'source-1',
    paperId: 'paper-1',
    mode: 'native' as const,
    status: 'queued' as const,
  },
  job: {
    id: 'job-source-1',
    paperId: 'paper-1',
    jobType: 'source_materialize' as const,
    sourceMode: 'native' as const,
    status: 'queued' as const,
  },
  deduplicated: false,
};

const translationEnqueue = {
  artifact: {
    id: 'artifact-translation-1',
    paperId: 'paper-1',
    kind: 'translation' as const,
    sourceDocumentId: 'source-1',
    status: 'queued' as const,
  },
  job: {
    id: 'job-translation-1',
    paperId: 'paper-1',
    jobType: 'translate' as const,
    sourceMode: 'native' as const,
    status: 'queued' as const,
  },
  deduplicated: false,
};

const readySource = { ...sourceEnqueue.source, status: 'ready' as const };

function jobDetail(
  id: string,
  jobType: 'source_materialize' | 'translate',
  status: 'queued' | 'running' | 'succeeded',
) {
  return {
    id,
    paperId: 'paper-1',
    jobType,
    sourceMode: 'native',
    status,
    progress: {},
    attempt: status === 'queued' ? 0 : 1,
    maxAttempts: 3,
    error: null,
    createdAt: '2026-08-14T00:00:00Z',
    startedAt: status === 'queued' ? null : '2026-08-14T00:00:01Z',
    finishedAt: status === 'succeeded' ? '2026-08-14T00:00:02Z' : null,
    cancelledAt: null,
  };
}

describe('InsightsGateway P3 contracts', () => {
  it('sends an exact search request and decodes nullable provenance and scores', async () => {
    const client = new ScriptedClient([searchResponse]);
    const gateway = createInsightsGateway(client);

    await expect(gateway.searchChunks({
      query: 'evaluation protocol',
      mode: 'hybrid',
      paperIds: ['paper/one'],
      limit: 20,
    })).resolves.toEqual(searchResponse);

    expect(client.calls).toHaveLength(1);
    expect(client.calls[0]?.input).toBe('/api/v2/search/chunks');
    expect(client.calls[0]?.options?.method).toBe('POST');
    expect(client.calls[0]?.options?.body).toBe(JSON.stringify({
      query: 'evaluation protocol',
      mode: 'hybrid',
      paperIds: ['paper/one'],
      limit: 20,
    }));
  });

  it('fails closed for missing and unknown search response fields', async () => {
    const missing = {
      ...searchResponse,
      items: [{ ...searchResponse.items[0], headingPath: undefined }],
    };
    const unknown = {
      ...searchResponse,
      extra: 'must-not-cross-the-wire',
    };

    await expect(createInsightsGateway(new ScriptedClient([missing])).searchChunks({
      query: 'abc', mode: 'lexical', paperIds: [], limit: 20,
    })).rejects.toMatchObject({ kind: 'decode' });
    await expect(createInsightsGateway(new ScriptedClient([unknown])).searchChunks({
      query: 'abc', mode: 'lexical', paperIds: [], limit: 20,
    })).rejects.toMatchObject({ kind: 'decode' });
  });

  it('rejects unknown search modes before issuing a request', async () => {
    const client = new ScriptedClient();
    const gateway = createInsightsGateway(client);

    await expect(gateway.searchChunks({
      query: 'abc',
      mode: 'fallback' as 'lexical',
      paperIds: [],
      limit: 20,
    })).rejects.toMatchObject({ kind: 'decode', path: '$.mode' });
    expect(client.calls).toEqual([]);
  });

  it('does not serialize structurally compatible extra search request fields', async () => {
    const client = new ScriptedClient();
    const gateway = createInsightsGateway(client);

    await expect(gateway.searchChunks({
      query: 'abc',
      mode: 'lexical',
      paperIds: [],
      limit: 20,
      authorization: 'Bearer must-not-cross-the-wire',
    } as never)).rejects.toMatchObject({ kind: 'decode', path: '$.authorization' });
    expect(client.calls).toEqual([]);
  });

  it('sends exact index enqueue fields and strictly decodes status coverage enums', async () => {
    const client = new ScriptedClient([indexEnqueue, indexStatus]);
    const gateway = createInsightsGateway(client);

    await expect(gateway.enqueueIndex('paper/one', {
      sourceMode: 'native',
      sourceDocumentId: 'source-1',
      includeEmbeddings: true,
    })).resolves.toEqual(indexEnqueue);
    await expect(gateway.getIndexStatus('paper/one', 'source-1')).resolves.toEqual(indexStatus);

    expect(client.calls.map((call) => call.input)).toEqual([
      '/api/v2/papers/paper%2Fone/index',
      '/api/v2/papers/paper%2Fone/index-status?sourceDocumentId=source-1',
    ]);
    expect(client.calls[0]?.options?.body).toBe(JSON.stringify({
      sourceMode: 'native',
      sourceDocumentId: 'source-1',
      includeEmbeddings: true,
    }));
  });

  it('rejects unknown index status coverage values', async () => {
    const client = new ScriptedClient([{ ...indexStatus, coverage: 'unknown' }]);
    const gateway = createInsightsGateway(client);

    await expect(gateway.getIndexStatus('paper-1', 'source-1'))
      .rejects.toMatchObject({ kind: 'decode' });
  });

  it('rejects an index enqueue response for a non-embed job', async () => {
    const client = new ScriptedClient([{
      ...indexEnqueue,
      job: { ...indexEnqueue.job, jobType: 'explain' },
    }]);
    const gateway = createInsightsGateway(client);

    await expect(gateway.enqueueIndex('paper-1', {
      sourceMode: 'native',
      sourceDocumentId: 'source-1',
      includeEmbeddings: true,
    })).rejects.toMatchObject({ kind: 'decode', path: '$.job.jobType' });
  });

  it('rejects missing and extra index status fields', async () => {
    const missing = { ...indexStatus, provider: undefined };
    const extra = { ...indexStatus, secret: 'not-public' };

    await expect(createInsightsGateway(new ScriptedClient([missing]))
      .getIndexStatus('paper-1', 'source-1')).rejects.toMatchObject({ kind: 'decode' });
    await expect(createInsightsGateway(new ScriptedClient([extra]))
      .getIndexStatus('paper-1', 'source-1')).rejects.toMatchObject({ kind: 'decode' });
  });
});

describe('ArtifactGateway P3 translation adapter', () => {
  it('uses an explicitly injected adapter for source readiness, translation enqueue, and polling', async () => {
    const client = new ScriptedClient([
      sourceEnqueue,
      jobDetail('job-source-1', 'source_materialize', 'succeeded'),
      { items: [readySource], nextCursor: null },
      translationEnqueue,
      jobDetail('job-translation-1', 'translate', 'succeeded'),
    ], ['# translated projection']);
    const adapter = createProcessingGateway(client, { wait: vi.fn(async () => undefined) });
    const gateway = createArtifactGateway(client, {
      processingGateway: adapter,
    });

    await expect(gateway.translatePaper('paper-1')).resolves.toEqual({
      type: 'result',
      ok: true,
      markdown: '# translated projection',
    });
    expect(client.calls.map((call) => call.input)).toEqual([
      '/api/v2/papers/paper-1/sources',
      '/api/v2/jobs/job-source-1',
      '/api/v2/papers/paper-1/sources?limit=100',
      '/api/v2/papers/paper-1/artifacts/translation',
      '/api/v2/jobs/job-translation-1',
    ]);
    expect(client.calls[3]?.options?.body).toBe(JSON.stringify({
      sourceMode: 'native',
      sourceDocumentId: 'source-1',
    }));
    expect(client.ndjsonCalls).toEqual([]);
  });

  it('detaches an injected polling run locally without posting cancellation', async () => {
    const controller = new AbortController();
    let waitSignal: AbortSignal | undefined;
    const wait = vi.fn((signal?: AbortSignal) => {
      waitSignal = signal;
      return new Promise<void>((_resolve, reject) => {
        signal?.addEventListener('abort', () => {
          reject(new DOMException('detached', 'AbortError'));
        }, { once: true });
      });
    });
    const client = new ScriptedClient([
      sourceEnqueue,
      jobDetail('job-source-1', 'source_materialize', 'running'),
    ]);
    const adapter = createProcessingGateway(client, { wait });
    const gateway = createArtifactGateway(client, {
      processingGateway: adapter,
    });
    const pending = gateway.translatePaper('paper-1', { signal: controller.signal });

    await vi.waitFor(() => expect(waitSignal).toBe(controller.signal));
    controller.abort();
    await expect(pending).rejects.toMatchObject({ name: 'AbortError' });
    expect(client.calls).toHaveLength(2);
    expect(client.calls.some((call) => call.input.endsWith('/cancel'))).toBe(false);
  });

  it('keeps the exported default gateway on the legacy NDJSON path', async () => {
    const client = new ScriptedClient();
    const gateway = createArtifactGateway(client);

    await expect(gateway.translatePaper('paper-1')).rejects.toThrow('unexpected NDJSON request');
    expect(client.ndjsonCalls).toHaveLength(1);
    expect(client.ndjsonCalls[0]?.input).toBe('/api/translate');
  });
});
