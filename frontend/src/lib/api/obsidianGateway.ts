import { api, jsonRequest, type ApiClient } from './client';
import {
  decodeObsidianExportJobResponse,
  decodeObsidianStatus,
  decodeObsidianSyncJobResponse,
  decodeObsidianTestResult,
} from './decoders';
import { signalOptions } from './gatewayTransport';
import type {
  ObsidianCleanupRequest,
  ObsidianExportRequest,
} from './types';

export function createObsidianGateway(client: ApiClient = api) {
  return {
    getStatus(signal?: AbortSignal) {
      return client.json(
        '/api/v2/obsidian/status',
        decodeObsidianStatus,
        signalOptions(signal),
      );
    },

    testAccess(signal?: AbortSignal) {
      return client.json(
        '/api/v2/obsidian/test',
        decodeObsidianTestResult,
        jsonRequest({}, { method: 'POST', ...signalOptions(signal) }),
      );
    },

    exportPaper(
      paperId: string,
      request: ObsidianExportRequest,
      signal?: AbortSignal,
    ) {
      return client.json(
        `/api/v2/papers/${encodeURIComponent(paperId)}/exports/obsidian`,
        decodeObsidianExportJobResponse,
        jsonRequest(
          { dryRun: request.dryRun },
          { method: 'POST', ...signalOptions(signal) },
        ),
      );
    },

    sync(request: ObsidianCleanupRequest, signal?: AbortSignal) {
      return client.json(
        '/api/v2/obsidian/sync',
        decodeObsidianSyncJobResponse,
        jsonRequest({
          dryRun: request.dryRun,
          applyCleanup: request.applyCleanup,
          cleanupPlanSha: request.cleanupPlanSha,
        }, { method: 'POST', ...signalOptions(signal) }),
      );
    },
  };
}

export const obsidianGateway = createObsidianGateway();
export type ObsidianGateway = ReturnType<typeof createObsidianGateway>;
