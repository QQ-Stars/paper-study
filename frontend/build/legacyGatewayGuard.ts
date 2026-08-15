import { readFile } from 'node:fs/promises';
import path from 'node:path';

import { createAcquisitionGateway } from '../src/lib/api/acquisitionGateway';
import { createArtifactGateway } from '../src/lib/api/artifactGateway';
import type { ApiClient, TransportOptions } from '../src/lib/api/client';
import {
  decodeCitationGraph,
  decodeCommandChanges,
  decodeCommandId,
  decodeExplainerPending,
  decodeExpandCommand,
  decodeJobDetail,
  decodeJobList,
  decodeLlmTestCommand,
  decodeOkCommand,
  decodeOutputCommand,
  decodePaperDetail,
  decodePaperList,
  decodePdfScanCommand,
  decodePdfStatus,
  decodeReviewCompleteCommand,
  decodeReviewSnapshotCommand,
  decodeReviewStartCommand,
  decodeScheduleList,
  decodeSettingsView,
  decodeTitleTranslationStatus,
  decodeTranslateTextCommand,
} from '../src/lib/api/decoders';
import { createInsightsGateway } from '../src/lib/api/insightsGateway';
import { createJobsGateway } from '../src/lib/api/jobsGateway';
import { createPaperApi } from '../src/lib/api/paperApi';
import { createPdfGateway } from '../src/lib/api/pdfGateway';
import { createSchedulesGateway } from '../src/lib/api/schedulesGateway';
import { createSettingsGateway } from '../src/lib/api/settingsGateway';
import type { Candidate, Decoder } from '../src/lib/api/types';
import {
  citationBuildContract,
  downloadPdfsContract,
  embedContract,
  explainBatchContract,
  explainContract,
  importPdfsContract,
  ingestSelectedContract,
  jobsConfirmContract,
  normalizeVenuesContract,
  recommendContract,
  searchContract,
  semanticSearchContract,
  titleTranslationsContract,
  translateContract,
  verifyVenueContract,
} from '../src/lib/streaming/contracts';
import type { StreamContract } from '../src/lib/streaming/contracts';

interface LedgerEndpoint {
  method: 'GET' | 'POST';
  path: string;
  requestKind: 'none' | 'query' | 'json';
  request: { requiredKeys: string[]; optionalKeys: string[] };
  responseKind: 'json' | 'ndjson' | 'text' | 'bytes';
  decoder?: string;
  streamContract?: string;
}

interface LegacyLedger {
  version: string;
  endpoints: LedgerEndpoint[];
}

interface RecordedCall {
  method: string;
  path: string;
  queryKeys: string[];
  bodyKeys: string[];
  adapter: string;
}

type GatewayMethodInventory = Map<string, string[]>;

const p3GatewayCalls = new Map<string, Omit<RecordedCall, 'method' | 'path'>>([
  ['POST /api/v2/search/chunks', {
    queryKeys: [],
    bodyKeys: ['limit', 'mode', 'paperIds', 'query'],
    adapter: 'strictJson',
  }],
  ['POST /api/v2/papers/paper%2Fone/index', {
    queryKeys: [],
    bodyKeys: ['includeEmbeddings', 'sourceDocumentId', 'sourceMode'],
    adapter: 'strictJson',
  }],
  ['GET /api/v2/papers/paper%2Fone/index-status', {
    queryKeys: ['sourceDocumentId'],
    bodyKeys: [],
    adapter: 'strictJson',
  }],
]);

export function assertCompleteGatewayMethodCoverage(
  inventory: GatewayMethodInventory,
  invokedMethods: Set<string>,
): number {
  const declaredMethods = new Set<string>();
  for (const [gatewayName, methodNames] of inventory) {
    for (const methodName of methodNames) declaredMethods.add(`${gatewayName}.${methodName}`);
  }
  const missing = [...declaredMethods].filter((method) => !invokedMethods.has(method)).sort();
  if (missing.length) throw new Error(`unrecorded Gateway methods: ${missing.join(', ')}`);
  const unexpected = [...invokedMethods].filter((method) => !declaredMethods.has(method)).sort();
  if (unexpected.length) throw new Error(`unknown recorded Gateway methods: ${unexpected.join(', ')}`);
  return declaredMethods.size;
}

function trackGatewayMethods<T extends object>(
  gatewayName: string,
  gateway: T,
  inventory: GatewayMethodInventory,
  invokedMethods: Set<string>,
): T {
  const methodNames = Object.keys(gateway)
    .filter((key) => typeof Reflect.get(gateway, key) === 'function')
    .sort();
  inventory.set(gatewayName, methodNames);
  return new Proxy(gateway, {
    get(target, property, receiver) {
      const value = Reflect.get(target, property, receiver);
      if (typeof property !== 'string' || typeof value !== 'function') return value;
      return (...args: unknown[]) => {
        invokedMethods.add(`${gatewayName}.${property}`);
        return Reflect.apply(value, target, args);
      };
    },
  });
}

const decoderNames = new Map<unknown, string>([
  [decodeCitationGraph, 'decodeCitationGraph'],
  [decodeCommandChanges, 'decodeCommandChanges'],
  [decodeCommandId, 'decodeCommandId'],
  [decodeExplainerPending, 'decodeExplainerPending'],
  [decodeExpandCommand, 'decodeExpandCommand'],
  [decodeJobDetail, 'decodeJobDetail'],
  [decodeJobList, 'decodeJobList'],
  [decodeLlmTestCommand, 'decodeLlmTestCommand'],
  [decodeOkCommand, 'decodeOkCommand'],
  [decodeOutputCommand, 'decodeOutputCommand'],
  [decodePaperDetail, 'decodePaperDetail'],
  [decodePaperList, 'decodePaperList'],
  [decodePdfScanCommand, 'decodePdfScanCommand'],
  [decodePdfStatus, 'decodePdfStatus'],
  [decodeReviewCompleteCommand, 'decodeReviewCompleteCommand'],
  [decodeReviewSnapshotCommand, 'decodeReviewSnapshotCommand'],
  [decodeReviewStartCommand, 'decodeReviewStartCommand'],
  [decodeScheduleList, 'decodeScheduleList'],
  [decodeSettingsView, 'decodeSettingsView'],
  [decodeTitleTranslationStatus, 'decodeTitleTranslationStatus'],
  [decodeTranslateTextCommand, 'decodeTranslateTextCommand'],
]);

const streamContractNames = new Map<unknown, string>([
  [citationBuildContract, 'citationBuildContract'],
  [downloadPdfsContract, 'downloadPdfsContract'],
  [embedContract, 'embedContract'],
  [explainBatchContract, 'explainBatchContract'],
  [explainContract, 'explainContract'],
  [importPdfsContract, 'importPdfsContract'],
  [ingestSelectedContract, 'ingestSelectedContract'],
  [jobsConfirmContract, 'jobsConfirmContract'],
  [normalizeVenuesContract, 'normalizeVenuesContract'],
  [recommendContract, 'recommendContract'],
  [searchContract, 'searchContract'],
  [semanticSearchContract, 'semanticSearchContract'],
  [titleTranslationsContract, 'titleTranslationsContract'],
  [translateContract, 'translateContract'],
  [verifyVenueContract, 'verifyVenueContract'],
]);

function requestParts(input: RequestInfo | URL, options: RequestInit = {}) {
  const raw = typeof input === 'string' ? input : input.toString();
  const url = new URL(raw, 'http://gateway-guard.invalid');
  const method = (options.method ?? 'GET').toUpperCase();
  let bodyKeys: string[] = [];
  if (typeof options.body === 'string' && options.body.trim()) {
    const body = JSON.parse(options.body) as unknown;
    if (!body || typeof body !== 'object' || Array.isArray(body)) {
      throw new Error(`${method} ${url.pathname} did not send a JSON object`);
    }
    bodyKeys = Object.keys(body).sort();
  }
  return {
    method,
    path: url.pathname,
    queryKeys: [...url.searchParams.keys()].sort(),
    bodyKeys,
  };
}

function fakeJsonValue(method: string, endpointPath: string): unknown {
  if (method === 'GET' && endpointPath === '/api/paper/get') return null;
  if (method === 'POST' && endpointPath === '/api/paper/add') return 'fixture-paper';
  if (method === 'POST' && endpointPath === '/api/paper/update') return 1;
  if (method === 'POST' && (endpointPath === '/api/jobs' || endpointPath === '/api/schedules')) return 1;
  return {};
}

function createRecordingClient(calls: RecordedCall[]): ApiClient {
  return {
    async json<T>(input: RequestInfo | URL, decoder: Decoder<T>, options: TransportOptions = {}) {
      const parts = requestParts(input, options);
      const adapter = decoderNames.get(decoder)
        ?? (parts.path.startsWith('/api/v2/') ? 'strictJson' : undefined);
      if (!adapter) throw new Error(`unknown decoder for ${parts.method} ${parts.path}`);
      calls.push({ ...parts, adapter });
      return fakeJsonValue(parts.method, parts.path) as T;
    },
    async text(input: RequestInfo | URL, options: TransportOptions = {}) {
      calls.push({ ...requestParts(input, options), adapter: 'text' });
      return '';
    },
    async bytes(input: RequestInfo | URL, options: TransportOptions = {}) {
      calls.push({ ...requestParts(input, options), adapter: 'bytes' });
      return new ArrayBuffer(0);
    },
    async ndjson<E, T>(
      input: RequestInfo | URL,
      contract: StreamContract<E, T>,
      options: TransportOptions<E, T> = {},
    ) {
      const parts = requestParts(input, options);
      const adapter = streamContractNames.get(contract);
      if (!adapter) throw new Error(`unknown stream contract for ${parts.method} ${parts.path}`);
      calls.push({ ...parts, adapter });
      return { type: contract.terminalType, ok: true } as T;
    },
  };
}

const representativeCandidate: Candidate = {
  source: 'semanticscholar', sourceId: 'source-id', title: 'Candidate', authors: ['Ada'],
  venue: 'CVPR', year: '2026', abstract: null, tldr: null, fields: [], citations: 1,
  url: null, pdfUrl: null, arxivId: null, doi: null, s2Id: 's2-id', ccf: 'A', type: null,
  topic: null, task: null, models: [], datasets: [], contribution: null, llmTldr: null,
  tags: [], relevance: 0.9, inLibrary: false, candidateId: null,
};

async function recordEveryGatewayCall(client: ApiClient): Promise<number> {
  const inventory: GatewayMethodInventory = new Map();
  const invokedMethods = new Set<string>();
  const track = <T extends object>(name: string, gateway: T) => (
    trackGatewayMethods(name, gateway, inventory, invokedMethods)
  );

  const paper = track('paper', createPaperApi(client));
  await paper.listPapers();
  await paper.getPaper('paper/one');
  await paper.getNote('paper/one');
  await paper.getExplainer('paper/one');
  await paper.getTranslation('paper/one');
  await paper.getPdfBytes('paper/one');
  await paper.getReviews();
  await paper.startReview('paper/one');
  await paper.completeReview('paper/one');
  await paper.saveNote('paper/one', 'note');
  await paper.setStatus('paper/one', '学习中');
  await paper.setFavorite('paper/one', true);
  await paper.deletePaper('paper/one');
  const allPaperFields = {
    title: 'Paper', titleZh: '论文', venue: 'CVPR', year: '2026', type: 'method', topic: 'test',
    url: 'https://example.test', pdfUrl: 'https://example.test/p.pdf', pdfPath: 'p.pdf', tldr: 'tldr',
    abstract: 'abstract', contribution: 'contribution', authors: ['Ada'], relevance: 0.9, order: 1,
  };
  await paper.addPaper(allPaperFields);
  await paper.updatePaper('paper/one', allPaperFields);

  const acquisition = track('acquisition', createAcquisitionGateway(client));
  await acquisition.expand('query', 6);
  const searchRequest = {
    query: 'query', sources: ['dblp'], years: '2024-2026', max: 10, minRelevance: 0.5,
    expand: true, onlyA: true, queries: ['query'],
  };
  await acquisition.ingest({ ...searchRequest, deep: true, downloadPdf: false });
  await acquisition.search(searchRequest);
  await acquisition.verifyVenue([representativeCandidate], ['dblp']);
  await acquisition.ingestSelected({ candidates: [representativeCandidate], deep: true, downloadPdf: false });

  const artifact = track('artifact', createArtifactGateway(client));
  await artifact.getTitleTranslationStatus();
  await artifact.translateTitles(1);
  await artifact.explainPaper('paper/one', true);
  await artifact.getExplainerPending();
  await artifact.explainBatch(1);
  await artifact.translatePaper('paper/one');
  await artifact.translateText('text');

  const pdf = track('pdf', createPdfGateway(client));
  await pdf.scanPdfs('C:/fixture');
  await pdf.importPdfs(['C:/fixture/a.pdf'], false);
  await pdf.downloadPdfs({ ids: ['paper/one'], limit: 1 });
  await pdf.getPdfStatus('paper/one');

  const insights = track('insights', createInsightsGateway(client));
  await insights.searchChunks({
    query: 'query', mode: 'hybrid', paperIds: ['paper/one'], limit: 10,
  });
  await insights.enqueueIndex('paper/one', {
    sourceMode: 'native', sourceDocumentId: 'source/one', includeEmbeddings: true,
  });
  await insights.getIndexStatus('paper/one', 'source/one');
  await insights.recommend('paper/one', 14);
  await insights.embed('all');
  await insights.semanticSearch('query', 60);
  await insights.getCitationGraph();
  await insights.normalizeVenues();
  await insights.buildCitationGraph();

  const jobs = track('jobs', createJobsGateway(client));
  await jobs.listJobs();
  await jobs.createJob({
    query: 'query', sources: ['dblp'], years: '2024-2026', max: 10, minRelevance: 0.5, onlyA: true,
  });
  await jobs.getJob(1);
  await jobs.deleteJob(1);
  await jobs.confirmJob(1, { candidates: [representativeCandidate], deep: true, downloadPdf: false });

  const schedules = track('schedules', createSchedulesGateway(client));
  await schedules.listSchedules();
  await schedules.createSchedule({
    query: 'query', sources: ['dblp'], years: '2024-2026', max: 10, minRelevance: 0.5,
    onlyA: true, everyDays: 7,
  });
  await schedules.toggleSchedule(1, true);
  await schedules.deleteSchedule(1);

  const settings = track('settings', createSettingsGateway(client));
  await settings.getSettings();
  await settings.saveSettings({
    provider: 'openai', baseUrl: 'https://example.test', model: 'model', apiKey: 'key',
    s2ApiKey: 's2', pdfDir: 'pdfs', explainerDir: 'explainers', translationDir: 'translations',
    researchTheme: 'theme', embedProvider: 'api', embedApiBase: 'https://embed.test',
    embedApiModel: 'embed-model', embedApiKey: 'embed-key',
  });
  await settings.testLlm();
  return assertCompleteGatewayMethodCoverage(inventory, invokedMethods);
}

function expectedAdapter(record: LedgerEndpoint): string {
  const adapter = record.responseKind === 'ndjson' ? record.streamContract : record.decoder;
  if (!adapter) throw new Error(`${record.method} ${record.path} has no decoder/streamContract`);
  return adapter;
}

export async function auditLegacyGateways({
  ledger,
  gatewaySourcePaths,
}: {
  ledger: LegacyLedger;
  gatewaySourcePaths: string[];
}): Promise<{
  endpointCount: number;
  gatewayCallCount: number;
  gatewayMethodCount: number;
  p3GatewayCallCount: number;
  sourceCount: number;
}> {
  if (ledger.version !== 'legacy-api-v1') throw new Error(`unsupported ledger: ${ledger.version}`);
  const expectedSources = new Set([
    'paperApi.ts', 'acquisitionGateway.ts', 'artifactGateway.ts', 'pdfGateway.ts',
    'insightsGateway.ts', 'jobsGateway.ts', 'schedulesGateway.ts', 'settingsGateway.ts',
  ]);
  if (gatewaySourcePaths.length !== expectedSources.size) throw new Error('Gateway source inventory is incomplete');
  for (const sourcePath of gatewaySourcePaths) {
    if (!expectedSources.delete(path.basename(sourcePath))) throw new Error(`unexpected Gateway source: ${sourcePath}`);
    const source = await readFile(sourcePath, 'utf8');
    if (!source.includes('Gateway') && !source.includes('createPaperApi')) {
      throw new Error(`Gateway factory missing from source: ${sourcePath}`);
    }
  }
  if (expectedSources.size) throw new Error(`missing Gateway sources: ${[...expectedSources].join(', ')}`);

  const calls: RecordedCall[] = [];
  const gatewayMethodCount = await recordEveryGatewayCall(createRecordingClient(calls));
  const legacyCalls = calls.filter((call) => !call.path.startsWith('/api/v2/'));
  const observedP3Calls = calls.filter((call) => call.path.startsWith('/api/v2/'));
  if (observedP3Calls.length !== p3GatewayCalls.size) {
    throw new Error(`P3 Gateway call count mismatch: ${observedP3Calls.length}/${p3GatewayCalls.size}`);
  }
  for (const call of observedP3Calls) {
    const pair = `${call.method} ${call.path}`;
    const expected = p3GatewayCalls.get(pair);
    if (!expected) throw new Error(`unrecorded P3 Gateway call: ${pair}`);
    if (JSON.stringify(call.queryKeys) !== JSON.stringify(expected.queryKeys)
      || JSON.stringify(call.bodyKeys) !== JSON.stringify(expected.bodyKeys)
      || call.adapter !== expected.adapter) {
      throw new Error(`P3 Gateway call differs: ${pair}`);
    }
  }
  const callByPair = new Map<string, RecordedCall>();
  for (const call of legacyCalls) {
    const pair = `${call.method} ${call.path}`;
    if (callByPair.has(pair)) throw new Error(`duplicate Gateway call: ${pair}`);
    callByPair.set(pair, call);
  }
  if (callByPair.size !== ledger.endpoints.length) {
    throw new Error(`Gateway/ledger count mismatch: ${callByPair.size}/${ledger.endpoints.length}`);
  }

  for (const record of ledger.endpoints) {
    const pair = `${record.method} ${record.path}`;
    const call = callByPair.get(pair);
    if (!call) throw new Error(`ledger endpoint has no Gateway call: ${pair}`);
    const expectedKeys = [...record.request.requiredKeys, ...record.request.optionalKeys].sort();
    const observedKeys = record.requestKind === 'query' ? call.queryKeys : call.bodyKeys;
    if (JSON.stringify(observedKeys) !== JSON.stringify(expectedKeys)) {
      throw new Error(`${pair} request keys differ: ${JSON.stringify(observedKeys)} != ${JSON.stringify(expectedKeys)}`);
    }
    if (call.adapter !== expectedAdapter(record)) {
      throw new Error(`${pair} adapter differs: ${call.adapter} != ${expectedAdapter(record)}`);
    }
    callByPair.delete(pair);
  }
  if (callByPair.size) throw new Error(`unledgered Gateway calls: ${[...callByPair.keys()].join(', ')}`);
  return {
    endpointCount: ledger.endpoints.length,
    gatewayCallCount: legacyCalls.length,
    gatewayMethodCount,
    p3GatewayCallCount: observedP3Calls.length,
    sourceCount: gatewaySourcePaths.length,
  };
}
