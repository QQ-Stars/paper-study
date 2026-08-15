import { api, jsonRequest, type ApiClient } from './client';
import { DecodeError } from './errors';
import {
  decodeProcessingJobSummary,
  type ProcessingJobSummary,
} from './processingGateway';
import { decodeCitationGraph } from './decoders';
import {
  businessFailure,
  signalOptions,
  streamOptions,
  type StreamCommandOptions,
} from './gatewayTransport';
import {
  citationBuildContract,
  embedContract,
  normalizeVenuesContract,
  recommendContract,
  semanticSearchContract,
} from '../streaming/contracts';
import type {
  CitationBuildTerminal,
  EmbedTerminal,
  LineProgressEvent,
  NormalizeVenuesTerminal,
  RecommendTerminal,
  SemanticSearchTerminal,
} from '../streaming/contracts';

export type SearchMode = 'lexical' | 'semantic' | 'hybrid';
export type IndexCoverage = 'empty' | 'partial' | 'complete';

export interface SearchChunksRequest {
  query: string;
  mode: SearchMode;
  paperIds: readonly string[];
  limit: number;
}

export interface SearchChunk {
  paperId: string;
  sourceDocumentId: string;
  chunkId: string;
  sequence: number;
  headingPath: string[];
  pageStart: number | null;
  pageEnd: number | null;
  excerpt: string;
  score: number | null;
  lexicalScore: number | null;
  semanticScore: number | null;
}

export interface SearchCoverage {
  readyChunks: number;
  embeddedChunks: number;
  staleChunks: number;
  failedEmbeddings: number;
}

export interface SearchChunksResponse {
  items: SearchChunk[];
  coverage: SearchCoverage;
}

export interface IndexEnqueueRequest {
  sourceMode: 'native' | 'ocr';
  sourceDocumentId: string;
  includeEmbeddings: boolean;
}

export interface IndexEnqueueResponse {
  job: ProcessingJobSummary;
  deduplicated: boolean;
}

export interface IndexStatus {
  totalChunks: number;
  readyChunks: number;
  embeddedChunks: number;
  staleChunks: number;
  failedEmbeddings: number;
  provider: string | null;
  model: string | null;
  version: string | null;
  coverage: IndexCoverage;
}

function inputObject(value: unknown, path: string): Record<string, unknown> {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) {
    throw new DecodeError(path, 'object', value);
  }
  return value as Record<string, unknown>;
}

function exactKeys(value: Record<string, unknown>, keys: readonly string[], path: string): void {
  const allowed = new Set(keys);
  for (const key of Object.keys(value)) {
    if (!allowed.has(key)) throw new DecodeError(`${path}.${key}`, 'no unknown field', value[key]);
  }
  for (const key of keys) {
    if (!Object.prototype.hasOwnProperty.call(value, key)) {
      throw new DecodeError(`${path}.${key}`, 'required field', undefined);
    }
  }
}

function text(value: unknown, path: string): string {
  if (typeof value !== 'string') throw new DecodeError(path, 'string', value);
  return value;
}

function finiteNumber(value: unknown, path: string): number {
  if (typeof value !== 'number' || !Number.isFinite(value)) {
    throw new DecodeError(path, 'finite number', value);
  }
  return value;
}

function nonNegativeInteger(value: unknown, path: string): number {
  const result = finiteNumber(value, path);
  if (!Number.isInteger(result) || result < 0) {
    throw new DecodeError(path, 'non-negative integer', value);
  }
  return result;
}

function nullableNumber(value: unknown, path: string): number | null {
  return value === null ? null : finiteNumber(value, path);
}

function nullableText(value: unknown, path: string): string | null {
  return value === null ? null : text(value, path);
}

function oneOf<const T extends readonly string[]>(values: T, value: unknown, path: string): T[number] {
  if (typeof value !== 'string' || !values.includes(value)) {
    throw new DecodeError(path, values.join(' | '), value);
  }
  return value;
}

function stringArray(value: unknown, path: string): string[] {
  if (!Array.isArray(value)) throw new DecodeError(path, 'string array', value);
  return value.map((item, index) => text(item, `${path}[${index}]`));
}

function decodeSearchChunk(value: unknown, path: string): SearchChunk {
  const input = inputObject(value, path);
  exactKeys(input, [
    'paperId', 'sourceDocumentId', 'chunkId', 'sequence', 'headingPath',
    'pageStart', 'pageEnd', 'excerpt', 'score', 'lexicalScore', 'semanticScore',
  ], path);
  return {
    paperId: text(input.paperId, `${path}.paperId`),
    sourceDocumentId: text(input.sourceDocumentId, `${path}.sourceDocumentId`),
    chunkId: text(input.chunkId, `${path}.chunkId`),
    sequence: nonNegativeInteger(input.sequence, `${path}.sequence`),
    headingPath: stringArray(input.headingPath, `${path}.headingPath`),
    pageStart: input.pageStart === null ? null : nonNegativeInteger(input.pageStart, `${path}.pageStart`),
    pageEnd: input.pageEnd === null ? null : nonNegativeInteger(input.pageEnd, `${path}.pageEnd`),
    excerpt: text(input.excerpt, `${path}.excerpt`),
    score: nullableNumber(input.score, `${path}.score`),
    lexicalScore: nullableNumber(input.lexicalScore, `${path}.lexicalScore`),
    semanticScore: nullableNumber(input.semanticScore, `${path}.semanticScore`),
  };
}

function decodeCoverage(value: unknown, path: string): SearchCoverage {
  const input = inputObject(value, path);
  exactKeys(input, ['readyChunks', 'embeddedChunks', 'staleChunks', 'failedEmbeddings'], path);
  return {
    readyChunks: nonNegativeInteger(input.readyChunks, `${path}.readyChunks`),
    embeddedChunks: nonNegativeInteger(input.embeddedChunks, `${path}.embeddedChunks`),
    staleChunks: nonNegativeInteger(input.staleChunks, `${path}.staleChunks`),
    failedEmbeddings: nonNegativeInteger(input.failedEmbeddings, `${path}.failedEmbeddings`),
  };
}

function decodeSearchResponse(value: unknown, path = '$'): SearchChunksResponse {
  const input = inputObject(value, path);
  exactKeys(input, ['items', 'coverage'], path);
  if (!Array.isArray(input.items)) throw new DecodeError(`${path}.items`, 'array', input.items);
  return {
    items: input.items.map((item, index) => decodeSearchChunk(item, `${path}.items[${index}]`)),
    coverage: decodeCoverage(input.coverage, `${path}.coverage`),
  };
}

function decodeIndexEnqueue(value: unknown, path = '$'): IndexEnqueueResponse {
  const input = inputObject(value, path);
  exactKeys(input, ['job', 'deduplicated'], path);
  if (typeof input.deduplicated !== 'boolean') throw new DecodeError(`${path}.deduplicated`, 'boolean', input.deduplicated);
  const job = decodeProcessingJobSummary(input.job, `${path}.job`);
  if (job.jobType !== 'embed') {
    throw new DecodeError(`${path}.job.jobType`, 'embed', job.jobType);
  }
  return { job, deduplicated: input.deduplicated };
}

function decodeIndexStatus(value: unknown, path = '$'): IndexStatus {
  const input = inputObject(value, path);
  exactKeys(input, [
    'totalChunks', 'readyChunks', 'embeddedChunks', 'staleChunks', 'failedEmbeddings',
    'provider', 'model', 'version', 'coverage',
  ], path);
  return {
    totalChunks: nonNegativeInteger(input.totalChunks, `${path}.totalChunks`),
    readyChunks: nonNegativeInteger(input.readyChunks, `${path}.readyChunks`),
    embeddedChunks: nonNegativeInteger(input.embeddedChunks, `${path}.embeddedChunks`),
    staleChunks: nonNegativeInteger(input.staleChunks, `${path}.staleChunks`),
    failedEmbeddings: nonNegativeInteger(input.failedEmbeddings, `${path}.failedEmbeddings`),
    provider: nullableText(input.provider, `${path}.provider`),
    model: nullableText(input.model, `${path}.model`),
    version: nullableText(input.version, `${path}.version`),
    coverage: oneOf(['empty', 'partial', 'complete'] as const, input.coverage, `${path}.coverage`),
  };
}

function validateSearchRequest(request: SearchChunksRequest): {
  query: string;
  mode: SearchMode;
  paperIds: string[];
  limit: number;
} {
  if (typeof request !== 'object' || request === null) throw new TypeError('search request must be an object');
  exactKeys(request as unknown as Record<string, unknown>, ['query', 'mode', 'paperIds', 'limit'], '$');
  const query = text(request.query, '$.query');
  const mode = oneOf(['lexical', 'semantic', 'hybrid'] as const, request.mode, '$.mode');
  const paperIds = stringArray(request.paperIds, '$.paperIds');
  const limit = nonNegativeInteger(request.limit, '$.limit');
  if (limit < 1 || limit > 50) throw new DecodeError('$.limit', 'integer between 1 and 50', request.limit);
  return { query, mode, paperIds, limit };
}

export function createInsightsGateway(client: ApiClient = api) {
  return {
    async searchChunks(request: SearchChunksRequest, signal?: AbortSignal) {
      const body = validateSearchRequest(request);
      return client.json(
        '/api/v2/search/chunks',
        decodeSearchResponse,
        jsonRequest(body, { method: 'POST', ...signalOptions(signal) }),
      );
    },

    async enqueueIndex(
      paperId: string,
      request: IndexEnqueueRequest,
      signal?: AbortSignal,
    ) {
      if (typeof request !== 'object' || request === null) throw new TypeError('index request must be an object');
      exactKeys(
        request as unknown as Record<string, unknown>,
        ['sourceMode', 'sourceDocumentId', 'includeEmbeddings'],
        '$',
      );
      const sourceMode = oneOf(['native', 'ocr'] as const, request.sourceMode, '$.sourceMode');
      const sourceDocumentId = text(request.sourceDocumentId, '$.sourceDocumentId');
      if (typeof request.includeEmbeddings !== 'boolean') {
        throw new DecodeError('$.includeEmbeddings', 'boolean', request.includeEmbeddings);
      }
      return client.json(
        `/api/v2/papers/${encodeURIComponent(String(paperId))}/index`,
        decodeIndexEnqueue,
        jsonRequest({ sourceMode, sourceDocumentId, includeEmbeddings: request.includeEmbeddings }, {
          method: 'POST',
          ...signalOptions(signal),
        }),
      );
    },

    getIndexStatus(paperId: string, sourceDocumentId: string, signal?: AbortSignal) {
      const params = new URLSearchParams({ sourceDocumentId: text(sourceDocumentId, '$.sourceDocumentId') });
      return client.json(
        `/api/v2/papers/${encodeURIComponent(String(paperId))}/index-status?${params.toString()}`,
        decodeIndexStatus,
        signalOptions(signal),
      );
    },

    async recommend(
      id: string,
      limit = 14,
      options: StreamCommandOptions<LineProgressEvent, RecommendTerminal> = {},
    ) {
      const paperId = String(id);
      const terminal = await client.ndjson('/api/recommend', recommendContract, streamOptions({ id: paperId, limit }, options));
      if (!terminal.ok) return businessFailure(terminal);
      return terminal;
    },

    async embed(
      scope: 'all' | 'missing' = 'missing',
      options: StreamCommandOptions<LineProgressEvent, EmbedTerminal> = {},
    ) {
      const terminal = await client.ndjson('/api/embed', embedContract, streamOptions({ scope }, options));
      if (!terminal.ok) return businessFailure(terminal);
      return terminal;
    },

    async semanticSearch(
      query: string,
      k = 60,
      options: StreamCommandOptions<LineProgressEvent, SemanticSearchTerminal> = {},
    ) {
      const terminal = await client.ndjson('/api/semsearch', semanticSearchContract, streamOptions({ query, k }, options));
      if (!terminal.ok) return businessFailure(terminal);
      return terminal;
    },

    getCitationGraph(signal?: AbortSignal) {
      return client.json('/api/citegraph', decodeCitationGraph, signalOptions(signal));
    },

    async normalizeVenues(
      options: StreamCommandOptions<LineProgressEvent, NormalizeVenuesTerminal> = {},
    ) {
      const terminal = await client.ndjson('/api/norm-venues', normalizeVenuesContract, streamOptions({}, options));
      if (!terminal.ok) return businessFailure(terminal);
      return terminal;
    },

    async buildCitationGraph(
      options: StreamCommandOptions<LineProgressEvent, CitationBuildTerminal> = {},
    ) {
      const terminal = await client.ndjson('/api/cite-build', citationBuildContract, streamOptions({}, options));
      if (!terminal.ok) return businessFailure(terminal);
      return terminal;
    },
  };
}

export const insightsGateway = createInsightsGateway();
export type InsightsGateway = ReturnType<typeof createInsightsGateway>;
