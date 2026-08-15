import { useRef } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import { BusinessError } from './errors';
import { obsidianKeys } from './keys';
import { obsidianGateway } from './obsidianGateway';
import { processingGateway } from './processingGateway';
import type {
  ObsidianCleanupRequest,
  ObsidianExportRequest,
} from './types';

export function useObsidianProjection(paperId: string | null = null) {
  const queryClient = useQueryClient();
  const inFlight = useRef(new Map<string, Promise<unknown>>());

  const once = <T,>(key: string, operation: () => Promise<T>): Promise<T> => {
    const existing = inFlight.current.get(key);
    if (existing !== undefined) return existing as Promise<T>;
    const pending = Promise.resolve()
      .then(operation)
      .finally(() => {
        if (inFlight.current.get(key) === pending) inFlight.current.delete(key);
      });
    inFlight.current.set(key, pending);
    return pending;
  };

  const invalidateStatus = () => queryClient.invalidateQueries({
    queryKey: obsidianKeys.status(),
  });

  const status = useQuery({
    queryKey: obsidianKeys.status(),
    queryFn: ({ signal }) => obsidianGateway.getStatus(signal),
  });

  const testAccess = useMutation({
    mutationKey: [...obsidianKeys.global(), 'test'],
    mutationFn: () => once('test', async () => {
      const result = await obsidianGateway.testAccess();
      await invalidateStatus();
      return result;
    }),
  });

  const exportPaper = useMutation({
    mutationKey: paperId === null
      ? [...obsidianKeys.global(), 'export']
      : [...obsidianKeys.paper(paperId), 'export'],
    mutationFn: (request: ObsidianExportRequest) => once(
      `export:${paperId ?? ''}:${request.dryRun ? 'dry' : 'write'}`,
      async () => {
        if (paperId === null) {
          throw new BusinessError(
            'Select a paper before exporting to Obsidian.',
            undefined,
            'OBSIDIAN_PAPER_REQUIRED',
          );
        }
        const response = await obsidianGateway.exportPaper(paperId, {
          dryRun: request.dryRun,
        });
        await processingGateway.waitForTerminal(response.job.id);
        await queryClient.invalidateQueries({ queryKey: obsidianKeys.paper(paperId) });
        await invalidateStatus();
        return response;
      },
    ),
  });

  const sync = useMutation({
    mutationKey: [...obsidianKeys.global(), 'sync'],
    mutationFn: (request: ObsidianCleanupRequest) => once(
      `sync:${request.dryRun ? 'dry' : 'write'}:${request.applyCleanup
        ? request.cleanupPlanSha ?? ''
        : 'preview'}`,
      async () => {
        const response = await obsidianGateway.sync({
          dryRun: request.dryRun,
          applyCleanup: request.applyCleanup,
          cleanupPlanSha: request.cleanupPlanSha,
        });
        await processingGateway.waitForTerminal(response.job.id);
        await queryClient.invalidateQueries({ queryKey: obsidianKeys.global() });
        await invalidateStatus();
        return response;
      },
    ),
  });

  return { status, testAccess, exportPaper, sync };
}
