import { readFile } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

import { describe, expect, it } from 'vitest';

import {
  assertCompleteGatewayMethodCoverage,
  auditLegacyGateways,
} from './legacyGatewayGuard';

const FRONTEND_ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const REPOSITORY_ROOT = path.resolve(FRONTEND_ROOT, '..');
const GATEWAY_SOURCES = [
  'src/lib/api/paperApi.ts',
  'src/lib/api/acquisitionGateway.ts',
  'src/lib/api/artifactGateway.ts',
  'src/lib/api/pdfGateway.ts',
  'src/lib/api/insightsGateway.ts',
  'src/lib/api/jobsGateway.ts',
  'src/lib/api/schedulesGateway.ts',
  'src/lib/api/settingsGateway.ts',
].map((relativePath) => path.join(FRONTEND_ROOT, relativePath));

describe('legacy Gateway guard', () => {
  it('matches legacy methods to the ledger and records P3 Gateway extensions', async () => {
    const ledger = JSON.parse(await readFile(
      path.join(REPOSITORY_ROOT, 'contracts', 'legacy-api-v1.json'),
      'utf8',
    ));
    const report = await auditLegacyGateways({ ledger, gatewaySourcePaths: GATEWAY_SOURCES });
    expect(report).toEqual({
      endpointCount: 49,
      gatewayCallCount: 49,
      gatewayMethodCount: 52,
      p3GatewayCallCount: 3,
      sourceCount: 8,
    });
  });

  it('rejects a factory method that the recorder did not invoke', () => {
    expect(() => assertCompleteGatewayMethodCoverage(
      new Map([['settings', ['getSettings', 'saveSettings', 'testLlm', 'newMethod']]]),
      new Set(['settings.getSettings', 'settings.saveSettings', 'settings.testLlm']),
    )).toThrow(/unrecorded Gateway methods: settings\.newMethod/);
  });
});
