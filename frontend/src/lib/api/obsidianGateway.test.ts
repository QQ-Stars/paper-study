import { describe, expect, it } from 'vitest';

import {
  obsidianCountsFixture,
  obsidianJobResponseFixture,
  obsidianStatusFixture,
  obsidianSyncStatusFixture,
  obsidianTestFixture,
} from '../../test/fixtures/obsidian';
import type { ApiClient, TransportOptions } from './client';
import { createObsidianGateway } from './obsidianGateway';
import type { Decoder } from './types';

interface JsonCall {
  input: string;
  options: RequestInit | undefined;
}

class ScriptedClient implements ApiClient {
  readonly calls: JsonCall[] = [];

  constructor(private readonly responses: unknown[]) {}

  async json<T>(
    input: RequestInfo | URL,
    decoder: Decoder<T>,
    options?: TransportOptions<unknown, unknown>,
  ): Promise<T> {
    this.calls.push({ input: String(input), options });
    return decoder(this.responses.shift(), '$');
  }

  async text(): Promise<string> {
    throw new Error('unexpected text request');
  }

  async bytes(): Promise<ArrayBuffer> {
    throw new Error('unexpected bytes request');
  }

  async ndjson<E, T>(
    input: RequestInfo | URL,
    contract: unknown,
    options?: TransportOptions<E, T>,
  ): Promise<T> {
    void input;
    void contract;
    void options;
    throw new Error('unexpected NDJSON request');
  }
}

describe('ObsidianGateway', () => {
  it('uses only the four fixed v2 routes with exact methods and safe request bodies', async () => {
    const client = new ScriptedClient([
      obsidianStatusFixture,
      obsidianTestFixture,
      obsidianJobResponseFixture,
      {
        ...obsidianJobResponseFixture,
        job: {
          ...obsidianJobResponseFixture.job,
          id: 'job-obsidian-sync',
          paperId: null,
          jobType: 'obsidian_sync',
        },
      },
    ]);
    const gateway = createObsidianGateway(client);
    const cleanupPlanSha = 'a'.repeat(64);

    await expect(gateway.getStatus()).resolves.toEqual(obsidianStatusFixture);
    await expect(gateway.testAccess()).resolves.toEqual(obsidianTestFixture);
    await expect(gateway.exportPaper('paper /?#', { dryRun: true })).resolves.toEqual(
      obsidianJobResponseFixture,
    );
    await expect(gateway.sync({
      dryRun: false,
      applyCleanup: true,
      cleanupPlanSha,
    })).resolves.toMatchObject({ job: { jobType: 'obsidian_sync' } });

    expect(client.calls.map((call) => call.input)).toEqual([
      '/api/v2/obsidian/status',
      '/api/v2/obsidian/test',
      '/api/v2/papers/paper%20%2F%3F%23/exports/obsidian',
      '/api/v2/obsidian/sync',
    ]);
    expect(client.calls.map((call) => call.options?.method ?? 'GET')).toEqual([
      'GET',
      'POST',
      'POST',
      'POST',
    ]);
    expect(client.calls[1]?.options?.body).toBe(JSON.stringify({}));
    expect(client.calls[2]?.options?.body).toBe(JSON.stringify({ dryRun: true }));
    expect(client.calls[3]?.options?.body).toBe(JSON.stringify({
      dryRun: false,
      applyCleanup: true,
      cleanupPlanSha,
    }));
    expect(client.calls.some((call) => call.input.includes('/api/settings'))).toBe(false);
    expect(client.calls.map((call) => call.options?.body ?? '').join('')).not.toContain('vaultPath');
  });

  it('accepts the frozen null paper identity for a global sync status', async () => {
    const gateway = createObsidianGateway(new ScriptedClient([obsidianSyncStatusFixture]));

    await expect(gateway.getStatus()).resolves.toEqual(obsidianSyncStatusFixture);
  });

  it.each([
    [{ ...obsidianStatusFixture, vaultPath: 'must-not-cross-the-wire' }, '$.vaultPath'],
    [{ ...obsidianStatusFixture, enabled: 1 }, '$.enabled'],
    [{ ...obsidianStatusFixture, aggregate: { ...obsidianCountsFixture, errors: -1 } }, '$.aggregate.errors'],
    [{
      ...obsidianStatusFixture,
      aggregate: {
        exported: 0,
        unchanged: 0,
        conflicts: 0,
        errors: 0,
        skipped: 0,
        userManaged: 0,
        orphaned: 0,
      },
    }, '$.aggregate.deleted'],
    [{
      ...obsidianStatusFixture,
      lastJob: { ...obsidianStatusFixture.lastJob, sourceMode: null },
    }, '$.lastJob.sourceMode'],
  ])('rejects unsafe or malformed status wire %#', async (wire, path) => {
    const gateway = createObsidianGateway(new ScriptedClient([wire]));

    await expect(gateway.getStatus()).rejects.toMatchObject({ kind: 'decode', path });
  });

  it.each([
    [{ ...obsidianJobResponseFixture, vaultPath: 'must-not-cross-the-wire' }, '$.vaultPath'],
    [{ ...obsidianJobResponseFixture, deduplicated: 0 }, '$.deduplicated'],
    [{
      ...obsidianJobResponseFixture,
      job: { ...obsidianJobResponseFixture.job, status: 'done' },
    }, '$.job.status'],
    [{
      ...obsidianJobResponseFixture,
      job: { ...obsidianJobResponseFixture.job, authorization: 'secret' },
    }, '$.job.authorization'],
    [{
      ...obsidianJobResponseFixture,
      job: {
        ...obsidianJobResponseFixture.job,
        paperId: null,
        jobType: 'obsidian_sync',
      },
    }, '$.job.jobType'],
  ])('reuses the strict P2 job response contract %#', async (wire, path) => {
    const gateway = createObsidianGateway(new ScriptedClient([wire]));

    await expect(gateway.exportPaper('paper-fixture-1', { dryRun: false }))
      .rejects.toMatchObject({ kind: 'decode', path });
  });

  it.each([
    [{}, '$.ok'],
    [{ ok: 1 }, '$.ok'],
    [{ ok: true, detail: 'absolute path must stay private' }, '$.detail'],
  ])('strictly decodes the write-capability probe %#', async (wire, path) => {
    const gateway = createObsidianGateway(new ScriptedClient([wire]));

    await expect(gateway.testAccess()).rejects.toMatchObject({ kind: 'decode', path });
  });
});
