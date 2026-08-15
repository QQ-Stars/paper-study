import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, renderHook, waitFor } from '@testing-library/react';
import type { PropsWithChildren } from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { BusinessError } from '../../lib/api/errors';
import { obsidianKeys } from '../../lib/api/keys';
import {
  obsidianJobResponseFixture,
  obsidianStatusFixture,
  obsidianTestFixture,
} from '../../test/fixtures/obsidian';
import { useObsidianProjection } from './useObsidianProjection';

const apiMocks = vi.hoisted(() => ({
  getStatus: vi.fn(),
  testAccess: vi.fn(),
  exportPaper: vi.fn(),
  sync: vi.fn(),
  waitForTerminal: vi.fn(),
  cancelJob: vi.fn(),
}));

vi.mock('../../lib/api/obsidianGateway', () => ({
  obsidianGateway: {
    getStatus: apiMocks.getStatus,
    testAccess: apiMocks.testAccess,
    exportPaper: apiMocks.exportPaper,
    sync: apiMocks.sync,
  },
}));

vi.mock('../../lib/api/processingGateway', () => ({
  processingGateway: {
    waitForTerminal: apiMocks.waitForTerminal,
    cancelJob: apiMocks.cancelJob,
  },
}));

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((accept, decline) => {
    resolve = accept;
    reject = decline;
  });
  return { promise, reject, resolve };
}

function harness(initialPaperId: string | null = 'paper-fixture-1') {
  const client = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  });
  const invalidate = vi.spyOn(client, 'invalidateQueries').mockResolvedValue();
  const wrapper = ({ children }: PropsWithChildren) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
  const view = renderHook(
    ({ paperId }: { paperId: string | null }) => useObsidianProjection(paperId),
    { initialProps: { paperId: initialPaperId }, wrapper },
  );
  return { ...view, client, invalidate };
}

beforeEach(() => {
  for (const mock of Object.values(apiMocks)) mock.mockReset();
  apiMocks.getStatus.mockResolvedValue(obsidianStatusFixture);
  apiMocks.testAccess.mockResolvedValue(obsidianTestFixture);
  apiMocks.exportPaper.mockResolvedValue(obsidianJobResponseFixture);
  apiMocks.sync.mockResolvedValue({
    ...obsidianJobResponseFixture,
    job: {
      ...obsidianJobResponseFixture.job,
      id: 'job-obsidian-sync',
      paperId: null,
      jobType: 'obsidian_sync',
    },
  });
  apiMocks.waitForTerminal.mockResolvedValue({
    ...obsidianJobResponseFixture.job,
    status: 'succeeded',
  });
});

describe('useObsidianProjection', () => {
  it('queries status and hands export/sync jobs to P2 polling with exact invalidation', async () => {
    const { result, invalidate } = harness();

    await waitFor(() => expect(result.current.status.isSuccess).toBe(true));
    expect(apiMocks.getStatus).toHaveBeenCalledWith(expect.any(AbortSignal));

    await act(async () => {
      await result.current.testAccess.mutateAsync();
    });
    expect(apiMocks.testAccess).toHaveBeenCalledWith();
    expect(invalidate).toHaveBeenNthCalledWith(1, { queryKey: obsidianKeys.status() });

    await act(async () => {
      await result.current.exportPaper.mutateAsync({ dryRun: true });
    });
    expect(apiMocks.exportPaper).toHaveBeenCalledWith(
      'paper-fixture-1',
      { dryRun: true },
    );
    expect(apiMocks.waitForTerminal).toHaveBeenNthCalledWith(1, 'job-obsidian-1');
    expect(invalidate).toHaveBeenNthCalledWith(2, {
      queryKey: obsidianKeys.paper('paper-fixture-1'),
    });
    expect(invalidate).toHaveBeenNthCalledWith(3, { queryKey: obsidianKeys.status() });

    await act(async () => {
      await result.current.sync.mutateAsync({
        dryRun: false,
        applyCleanup: false,
        cleanupPlanSha: null,
      });
    });
    expect(apiMocks.sync).toHaveBeenCalledWith({
      dryRun: false,
      applyCleanup: false,
      cleanupPlanSha: null,
    });
    expect(apiMocks.waitForTerminal).toHaveBeenNthCalledWith(2, 'job-obsidian-sync');
    expect(invalidate).toHaveBeenNthCalledWith(4, { queryKey: obsidianKeys.global() });
    expect(invalidate).toHaveBeenNthCalledWith(5, { queryKey: obsidianKeys.status() });
  });

  it('deduplicates concurrent equivalent export mutations before the provider boundary', async () => {
    const pending = deferred<typeof obsidianJobResponseFixture>();
    apiMocks.exportPaper.mockReturnValue(pending.promise);
    const { result, invalidate } = harness();
    await waitFor(() => expect(result.current.status.isSuccess).toBe(true));

    let first!: Promise<unknown>;
    let second!: Promise<unknown>;
    act(() => {
      first = result.current.exportPaper.mutateAsync({ dryRun: false });
      second = result.current.exportPaper.mutateAsync({ dryRun: false });
    });
    await waitFor(() => expect(apiMocks.exportPaper).toHaveBeenCalledOnce());

    pending.resolve(obsidianJobResponseFixture);
    await act(async () => {
      await Promise.all([first, second]);
    });
    expect(apiMocks.exportPaper).toHaveBeenCalledOnce();
    expect(apiMocks.waitForTerminal).toHaveBeenCalledOnce();
    expect(invalidate).toHaveBeenCalledTimes(2);
    expect(invalidate).toHaveBeenNthCalledWith(1, {
      queryKey: obsidianKeys.paper('paper-fixture-1'),
    });
    expect(invalidate).toHaveBeenNthCalledWith(2, { queryKey: obsidianKeys.status() });
  });

  it('preserves the API error envelope and can recover on a later mutation', async () => {
    const failure = new BusinessError('Vault probe failed.', { safe: true }, 'OBSIDIAN_TEST_FAILED');
    apiMocks.testAccess.mockRejectedValueOnce(failure);
    const { result } = harness(null);
    await waitFor(() => expect(result.current.status.isSuccess).toBe(true));

    await act(async () => {
      await expect(result.current.testAccess.mutateAsync()).rejects.toBe(failure);
    });
    await waitFor(() => expect(result.current.testAccess.error).toBe(failure));

    await act(async () => {
      await expect(result.current.testAccess.mutateAsync()).resolves.toEqual(
        obsidianTestFixture,
      );
    });
    await waitFor(() => expect(result.current.testAccess.isSuccess).toBe(true));
  });

  it('does not cancel a server job when the paper changes or the observer unmounts', async () => {
    const terminal = deferred<unknown>();
    apiMocks.waitForTerminal.mockReturnValue(terminal.promise);
    const { result, rerender, unmount } = harness('paper-fixture-1');
    await waitFor(() => expect(result.current.status.isSuccess).toBe(true));

    let mutation!: Promise<unknown>;
    act(() => {
      mutation = result.current.exportPaper.mutateAsync({ dryRun: false });
    });
    await waitFor(() => expect(apiMocks.waitForTerminal).toHaveBeenCalledWith('job-obsidian-1'));

    rerender({ paperId: 'paper-fixture-2' });
    unmount();
    expect(apiMocks.cancelJob).not.toHaveBeenCalled();
    expect(apiMocks.waitForTerminal.mock.calls[0]).toEqual(['job-obsidian-1']);

    terminal.resolve({ status: 'succeeded' });
    await mutation;
    expect(apiMocks.cancelJob).not.toHaveBeenCalled();
  });
});
