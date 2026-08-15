import { describe, expect, it, vi } from 'vitest';

import type { ApiClient, TransportOptions } from './client';
import { createArtifactGateway } from './artifactGateway';
import { createProcessingGateway } from './processingGateway';
import type { Decoder } from './types';

interface JsonCall {
  input: string;
  options: RequestInit | undefined;
}

class ScriptedClient implements ApiClient {
  readonly calls: JsonCall[] = [];
  readonly textCalls: JsonCall[] = [];
  readonly ndjsonCalls: JsonCall[] = [];

  constructor(
    private readonly responses: unknown[],
    private readonly textResponses: string[] = [],
  ) {}

  async json<T>(
    input: RequestInfo | URL,
    decoder: Decoder<T>,
    options?: TransportOptions<unknown, unknown>,
  ): Promise<T> {
    this.calls.push({ input: String(input), options });
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

const sourceEnqueue = {
  source: {
    id: 'source-1',
    paperId: 'paper-1',
    mode: 'native',
    status: 'queued',
  },
  job: {
    id: 'job-source-1',
    paperId: 'paper-1',
    jobType: 'source_materialize',
    sourceMode: 'native',
    status: 'queued',
  },
  deduplicated: false,
};

const artifactEnqueue = {
  artifact: {
    id: 'artifact-1',
    paperId: 'paper-1',
    kind: 'explainer',
    sourceDocumentId: 'source-1',
    status: 'queued',
  },
  job: {
    id: 'job-explainer-1',
    paperId: 'paper-1',
    jobType: 'explain',
    sourceMode: 'native',
    status: 'queued',
  },
  deduplicated: false,
};

const translationEnqueue = {
  artifact: {
    id: 'artifact-translation-1',
    paperId: 'paper-1',
    kind: 'translation',
    sourceDocumentId: 'source-1',
    status: 'queued',
  },
  job: {
    id: 'job-translation-1',
    paperId: 'paper-1',
    jobType: 'translate',
    sourceMode: 'native',
    status: 'queued',
  },
  deduplicated: false,
};

function jobDetail(status: 'queued' | 'running' | 'succeeded' | 'failed' | 'cancelled') {
  return {
    id: 'job-source-1',
    paperId: 'paper-1',
    jobType: 'source_materialize',
    sourceMode: 'native',
    status,
    progress: {},
    attempt: status === 'queued' ? 0 : 1,
    maxAttempts: 3,
    error: null,
    createdAt: '2026-08-11T01:00:00Z',
    startedAt: status === 'queued' ? null : '2026-08-11T01:00:01Z',
    finishedAt: status === 'succeeded' ? '2026-08-11T01:00:02Z' : null,
    cancelledAt: null,
  };
}

describe('ProcessingGateway', () => {
  it('enqueues an exact native source request and strictly decodes its safe response', async () => {
    const client = new ScriptedClient([sourceEnqueue]);
    const wait = vi.fn(async () => undefined);
    const gateway = createProcessingGateway(client, { wait });

    await expect(gateway.enqueueSource('paper-1')).resolves.toEqual(sourceEnqueue);
    expect(client.calls).toHaveLength(1);
    expect(client.calls[0]?.input).toBe('/api/v2/papers/paper-1/sources');
    expect(client.calls[0]?.options?.method).toBe('POST');
    expect(client.calls[0]?.options?.body).toBe(JSON.stringify({ sourceMode: 'native' }));
    expect(wait).not.toHaveBeenCalled();
  });

  it('rejects unknown fields instead of silently widening a safe DTO', async () => {
    const client = new ScriptedClient([{
      ...sourceEnqueue,
      authorization: 'must-not-cross-the-wire',
    }]);
    const gateway = createProcessingGateway(client);

    await expect(gateway.enqueueSource('paper-1')).rejects.toMatchObject({
      kind: 'decode',
      path: '$.authorization',
    });
  });

  it('enqueues an exact OCR source request without native fallback fields', async () => {
    const response = {
      source: { ...sourceEnqueue.source, mode: 'ocr' },
      job: { ...sourceEnqueue.job, jobType: 'ocr', sourceMode: 'ocr' },
      deduplicated: false,
    };
    const client = new ScriptedClient([response]);
    const gateway = createProcessingGateway(client);

    await expect(gateway.enqueueSource('paper-1', {
      sourceMode: 'ocr',
      ocrProvider: 'verified-provider',
      ocrModel: 'ocr-v1',
      options: { pageBatchSize: 2, maxConcurrency: 3 },
    })).resolves.toEqual(response);
    expect(client.calls[0]?.options?.body).toBe(JSON.stringify({
      sourceMode: 'ocr',
      ocrProvider: 'verified-provider',
      ocrModel: 'ocr-v1',
      options: { pageBatchSize: 2, maxConcurrency: 3 },
    }));
  });

  it('does not serialize structurally compatible extra fields into a source request', async () => {
    const client = new ScriptedClient([sourceEnqueue]);
    const gateway = createProcessingGateway(client);
    const request = {
      sourceMode: 'native' as const,
      authorization: 'Bearer must-not-cross-the-wire',
    };

    await gateway.enqueueSource('paper-1', request);
    expect(client.calls[0]?.options?.body).toBe(JSON.stringify({
      sourceMode: 'native',
    }));
  });

  it('enqueues an explainer with the exact source binding and profile body', async () => {
    const client = new ScriptedClient([artifactEnqueue]);
    const gateway = createProcessingGateway(client);

    await expect(gateway.enqueueExplainer('paper-1', {
      sourceMode: 'native',
      sourceDocumentId: 'source-1',
      profile: 'deep',
    })).resolves.toEqual(artifactEnqueue);
    expect(client.calls).toHaveLength(1);
    expect(client.calls[0]?.input).toBe('/api/v2/papers/paper-1/artifacts/explainer');
    expect(client.calls[0]?.options?.method).toBe('POST');
    expect(client.calls[0]?.options?.body).toBe(JSON.stringify({
      sourceMode: 'native',
      sourceDocumentId: 'source-1',
      profile: 'deep',
    }));
  });

  it('does not serialize structurally compatible extra fields into an artifact request', async () => {
    const client = new ScriptedClient([artifactEnqueue]);
    const gateway = createProcessingGateway(client);
    const request = {
      sourceMode: 'native' as const,
      sourceDocumentId: 'source-1',
      profile: 'standard' as const,
      authorization: 'Bearer must-not-cross-the-wire',
    };

    await gateway.enqueueExplainer('paper-1', request);
    expect(client.calls[0]?.options?.body).toBe(JSON.stringify({
      sourceMode: 'native',
      sourceDocumentId: 'source-1',
      profile: 'standard',
    }));
  });

  it('enqueues a translation with the exact source binding', async () => {
    const client = new ScriptedClient([translationEnqueue]);
    const gateway = createProcessingGateway(client);

    await expect(gateway.enqueueTranslation('paper-1', {
      sourceMode: 'native',
      sourceDocumentId: 'source-1',
    })).resolves.toEqual(translationEnqueue);
    expect(client.calls[0]?.input).toBe('/api/v2/papers/paper-1/artifacts/translation');
    expect(client.calls[0]?.options?.body).toBe(JSON.stringify({
      sourceMode: 'native',
      sourceDocumentId: 'source-1',
    }));
  });

  it('does not serialize structurally compatible extra translation fields', async () => {
    const client = new ScriptedClient([translationEnqueue]);
    const gateway = createProcessingGateway(client);
    const request = {
      sourceMode: 'native' as const,
      sourceDocumentId: 'source-1',
      authorization: 'Bearer must-not-cross-the-wire',
    };

    await gateway.enqueueTranslation('paper-1', request);
    expect(client.calls[0]?.options?.body).toBe(JSON.stringify({
      sourceMode: 'native',
      sourceDocumentId: 'source-1',
    }));
  });

  it('lists a strictly decoded source page with an encoded cursor', async () => {
    const page = {
      items: [{
        ...sourceEnqueue.source,
        status: 'ready',
      }],
      nextCursor: 'next/cursor',
    };
    const client = new ScriptedClient([page]);
    const gateway = createProcessingGateway(client);

    await expect(gateway.listSources('paper/1', {
      limit: 100,
      cursor: 'after+cursor',
    })).resolves.toEqual(page);
    expect(client.calls).toHaveLength(1);
    expect(client.calls[0]?.input).toBe(
      '/api/v2/papers/paper%2F1/sources?limit=100&cursor=after%2Bcursor',
    );
    expect(client.calls[0]?.options?.method).toBeUndefined();
  });

  it('polls queued and running jobs with the injected clock until a terminal status', async () => {
    const client = new ScriptedClient([
      jobDetail('queued'),
      jobDetail('running'),
      jobDetail('succeeded'),
    ]);
    const wait = vi.fn(async () => undefined);
    const gateway = createProcessingGateway(client, { wait });

    await expect(gateway.waitForTerminal('job-source-1')).resolves.toEqual(jobDetail('succeeded'));
    expect(wait).toHaveBeenCalledTimes(2);
    expect(client.calls.map((call) => call.input)).toEqual([
      '/api/v2/jobs/job-source-1',
      '/api/v2/jobs/job-source-1',
      '/api/v2/jobs/job-source-1',
    ]);
  });

  it('fails closed when a terminal job is failed or cancelled', async () => {
    const failed = {
      ...jobDetail('failed'),
      error: { code: 'OCR_TIMEOUT', message: 'Processing failed.' },
    };
    const cancelled = {
      ...jobDetail('cancelled'),
      cancelledAt: '2026-08-11T01:00:02Z',
    };
    const client = new ScriptedClient([failed, cancelled]);
    const gateway = createProcessingGateway(client);

    await expect(gateway.waitForTerminal('failed-job')).rejects.toMatchObject({
      kind: 'business',
      code: 'OCR_TIMEOUT',
    });
    await expect(gateway.waitForTerminal('cancelled-job')).rejects.toMatchObject({
      kind: 'business',
      code: 'PROCESSING_JOB_CANCELLED',
    });
  });

  it('treats detach as a local polling stop without posting server cancellation', async () => {
    const client = new ScriptedClient([]);
    const gateway = createProcessingGateway(client);
    const controller = new AbortController();
    controller.abort();

    await expect(gateway.waitForTerminal('job-source-1', controller.signal)).rejects.toMatchObject({
      name: 'AbortError',
    });
    expect(client.calls).toEqual([]);
  });

  it('posts cancellation only through the explicit cancel command', async () => {
    const cancelled = {
      ...jobDetail('cancelled'),
      cancelledAt: '2026-08-11T01:00:02Z',
    };
    const client = new ScriptedClient([cancelled]);
    const gateway = createProcessingGateway(client);

    await expect(gateway.cancelJob('job-source-1')).resolves.toEqual(cancelled);
    expect(client.calls).toHaveLength(1);
    expect(client.calls[0]?.input).toBe('/api/v2/jobs/job-source-1/cancel');
    expect(client.calls[0]?.options?.method).toBe('POST');
    expect(client.calls[0]?.options?.body).toBeUndefined();
  });

  it('uses an explicit P2 adapter and returns the real projected explainer markdown', async () => {
    const readySource = { ...sourceEnqueue.source, status: 'ready' };
    const succeededArtifactJob = {
      ...jobDetail('succeeded'),
      id: 'job-explainer-1',
      jobType: 'explain',
    };
    const client = new ScriptedClient([
      sourceEnqueue,
      jobDetail('succeeded'),
      { items: [readySource], nextCursor: null },
      artifactEnqueue,
      succeededArtifactJob,
    ], ['# Real projected explainer']);
    const processing = createProcessingGateway(client, { wait: vi.fn(async () => undefined) });
    const gateway = createArtifactGateway(client, { processingGateway: processing });
    const controller = new AbortController();

    await expect(gateway.explainPaper('paper-1', true, {
      signal: controller.signal,
    })).resolves.toEqual({
      type: 'result',
      ok: true,
      markdown: '# Real projected explainer',
    });
    expect(client.calls.map((call) => call.input)).toEqual([
      '/api/v2/papers/paper-1/sources',
      '/api/v2/jobs/job-source-1',
      '/api/v2/papers/paper-1/sources?limit=100',
      '/api/v2/papers/paper-1/artifacts/explainer',
      '/api/v2/jobs/job-explainer-1',
    ]);
    expect(client.calls[3]?.options?.body).toBe(JSON.stringify({
      sourceMode: 'native',
      sourceDocumentId: 'source-1',
      profile: 'deep',
    }));
    expect(client.textCalls.map((call) => call.input)).toEqual([
      '/api/explainer?id=paper-1',
    ]);
    expect(client.ndjsonCalls).toEqual([]);
  });

  it('fails closed instead of binding an explainer to a different ready source', async () => {
    const client = new ScriptedClient([
      sourceEnqueue,
      jobDetail('succeeded'),
      {
        items: [{
          ...sourceEnqueue.source,
          id: 'older-ready-source',
          status: 'ready',
        }],
        nextCursor: null,
      },
    ]);
    const processing = createProcessingGateway(client, { wait: vi.fn(async () => undefined) });
    const gateway = createArtifactGateway(client, { processingGateway: processing });

    await expect(gateway.explainPaper('paper-1')).rejects.toMatchObject({
      kind: 'business',
      code: 'SOURCE_DOCUMENT_NOT_READY',
    });
    expect(client.calls.map((call) => call.input)).toEqual([
      '/api/v2/papers/paper-1/sources',
      '/api/v2/jobs/job-source-1',
      '/api/v2/papers/paper-1/sources?limit=100',
    ]);
    expect(client.textCalls).toEqual([]);
    expect(client.ndjsonCalls).toEqual([]);
  });

  it('rejects the legacy missing-explainer sentinel instead of returning fake markdown success', async () => {
    const client = new ScriptedClient([
      sourceEnqueue,
      jobDetail('succeeded'),
      {
        items: [{ ...sourceEnqueue.source, status: 'ready' }],
        nextCursor: null,
      },
      artifactEnqueue,
      {
        ...jobDetail('succeeded'),
        id: 'job-explainer-1',
        jobType: 'explain',
      },
    ], ['*(暂无讲解)*']);
    const processing = createProcessingGateway(client, { wait: vi.fn(async () => undefined) });
    const gateway = createArtifactGateway(client, { processingGateway: processing });

    await expect(gateway.explainPaper('paper-1')).rejects.toMatchObject({
      kind: 'business',
      code: 'EXPLAINER_PROJECTION_EMPTY',
    });
  });

  it('keeps an uninjected artifact gateway on the legacy NDJSON endpoint', async () => {
    const client = new ScriptedClient([]);
    const gateway = createArtifactGateway(client);

    await expect(gateway.explainPaper('paper-1', true)).rejects.toThrow(
      'unexpected NDJSON request',
    );
    expect(client.calls).toEqual([]);
    expect(client.textCalls).toEqual([]);
    expect(client.ndjsonCalls).toHaveLength(1);
    expect(client.ndjsonCalls[0]?.input).toBe('/api/explain');
    expect(client.ndjsonCalls[0]?.options?.body).toBe(JSON.stringify({
      id: 'paper-1',
      deep: true,
    }));
  });

  it('maps the default legacy deep flag to the standard P2 profile', async () => {
    const client = new ScriptedClient([
      sourceEnqueue,
      jobDetail('succeeded'),
      {
        items: [{ ...sourceEnqueue.source, status: 'ready' }],
        nextCursor: null,
      },
      artifactEnqueue,
      {
        ...jobDetail('succeeded'),
        id: 'job-explainer-1',
        jobType: 'explain',
      },
    ], ['# Standard explainer']);
    const processing = createProcessingGateway(client, { wait: vi.fn(async () => undefined) });
    const gateway = createArtifactGateway(client, { processingGateway: processing });

    await expect(gateway.explainPaper('paper-1')).resolves.toMatchObject({
      ok: true,
      markdown: '# Standard explainer',
    });
    expect(client.calls[3]?.options?.body).toBe(JSON.stringify({
      sourceMode: 'native',
      sourceDocumentId: 'source-1',
      profile: 'standard',
    }));
  });
});
