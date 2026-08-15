import { api, jsonRequest, type ApiClient } from './client';
import { BusinessError, DecodeError } from './errors';
import { signalOptions } from './gatewayTransport';
import type { Decoder } from './types';

export type ProcessingJobStatus = 'queued' | 'running' | 'succeeded' | 'failed' | 'cancelled';
export type SourceDocumentStatus = 'queued' | 'running' | 'ready' | 'failed' | 'stale' | 'cancelled';
export type ProcessingJobType =
  | 'source_materialize'
  | 'ocr'
  | 'explain'
  | 'translate'
  | 'embed'
  | 'obsidian_export'
  | 'obsidian_sync';

export interface SourceDocumentSummary {
  id: string;
  paperId: string;
  mode: 'native' | 'ocr';
  status: SourceDocumentStatus;
}

export interface ProcessingJobSummary {
  id: string;
  paperId: string;
  jobType: ProcessingJobType;
  sourceMode: 'native' | 'ocr' | null;
  status: ProcessingJobStatus;
}

export interface SourceEnqueueResponse {
  source: SourceDocumentSummary;
  job: ProcessingJobSummary;
  deduplicated: boolean;
}

export interface SourceListQuery {
  limit?: number;
  cursor?: string;
}

export interface SourceDocumentPage {
  items: SourceDocumentSummary[];
  nextCursor: string | null;
}

export type SourceEnqueueRequest = {
  sourceMode: 'native';
} | {
  sourceMode: 'ocr';
  ocrProvider: string;
  ocrModel: string;
  options: {
    pageBatchSize: number;
    maxConcurrency: number;
  };
};

export interface ExplainerEnqueueRequest {
  sourceMode: 'native' | 'ocr';
  sourceDocumentId: string;
  profile: 'standard' | 'deep';
}

export interface TranslationEnqueueRequest {
  sourceMode: 'native' | 'ocr';
  sourceDocumentId: string;
}

export interface GeneratedArtifactSummary {
  id: string;
  paperId: string;
  kind: 'explainer' | 'translation';
  sourceDocumentId: string;
  status: SourceDocumentStatus;
}

export interface ArtifactEnqueueResponse {
  artifact: GeneratedArtifactSummary;
  job: ProcessingJobSummary;
  deduplicated: boolean;
}

export interface ProcessingJobError {
  code: string;
  message: string;
}

export interface ProcessingJobDetail extends ProcessingJobSummary {
  progress: Record<string, unknown>;
  attempt: number;
  maxAttempts: number;
  error: ProcessingJobError | null;
  createdAt: string;
  startedAt: string | null;
  finishedAt: string | null;
  cancelledAt: string | null;
}

export interface ProcessingClock {
  wait(signal?: AbortSignal): Promise<void>;
}

function inputObject(value: unknown, path: string): object {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) {
    throw new DecodeError(path, 'object', value);
  }
  return value;
}

function field(value: object, key: string): unknown {
  return Reflect.get(value, key);
}

function assertExactKeys(value: object, keys: readonly string[], path: string): void {
  const allowed = new Set(keys);
  const unexpected = Object.keys(value).find((key) => !allowed.has(key));
  if (unexpected !== undefined) {
    throw new DecodeError(`${path}.${unexpected}`, 'no unknown field', field(value, unexpected));
  }
  const missing = keys.find((key) => !Object.prototype.hasOwnProperty.call(value, key));
  if (missing !== undefined) {
    throw new DecodeError(`${path}.${missing}`, 'required field', undefined);
  }
}

function string(value: unknown, path: string): string {
  if (typeof value !== 'string') throw new DecodeError(path, 'string', value);
  return value;
}

function boolean(value: unknown, path: string): boolean {
  if (typeof value !== 'boolean') throw new DecodeError(path, 'boolean', value);
  return value;
}

function array(value: unknown, path: string): unknown[] {
  if (!Array.isArray(value)) throw new DecodeError(path, 'array', value);
  return value;
}

function nonNegativeInteger(value: unknown, path: string): number {
  if (typeof value !== 'number' || !Number.isInteger(value) || value < 0) {
    throw new DecodeError(path, 'non-negative integer', value);
  }
  return value;
}

function nullableTimestamp(value: unknown, path: string): string | null {
  if (value === null) return null;
  const decoded = string(value, path);
  if (!decoded.endsWith('Z') || Number.isNaN(Date.parse(decoded))) {
    throw new DecodeError(path, 'UTC timestamp', value);
  }
  return decoded;
}

function record(value: unknown, path: string): Record<string, unknown> {
  const input = inputObject(value, path);
  const result: Record<string, unknown> = {};
  for (const key of Object.keys(input)) result[key] = field(input, key);
  return result;
}

function oneOf<const T extends readonly string[]>(
  values: T,
  value: unknown,
  path: string,
): T[number] {
  if (typeof value !== 'string' || !values.includes(value)) {
    throw new DecodeError(path, values.join(' | '), value);
  }
  return value;
}

const sourceStatuses = ['queued', 'running', 'ready', 'failed', 'stale', 'cancelled'] as const;
const jobStatuses = ['queued', 'running', 'succeeded', 'failed', 'cancelled'] as const;
const jobTypes = [
  'source_materialize',
  'ocr',
  'explain',
  'translate',
  'embed',
  'obsidian_export',
  'obsidian_sync',
] as const;
const sourceModes = ['native', 'ocr'] as const;
const artifactKinds = ['explainer', 'translation'] as const;

const decodeSourceSummary: Decoder<SourceDocumentSummary> = (value, path = '$') => {
  const input = inputObject(value, path);
  assertExactKeys(input, ['id', 'paperId', 'mode', 'status'], path);
  return {
    id: string(field(input, 'id'), `${path}.id`),
    paperId: string(field(input, 'paperId'), `${path}.paperId`),
    mode: oneOf(sourceModes, field(input, 'mode'), `${path}.mode`),
    status: oneOf(sourceStatuses, field(input, 'status'), `${path}.status`),
  };
};

export const decodeProcessingJobSummary: Decoder<ProcessingJobSummary> = (value, path = '$') => {
  const input = inputObject(value, path);
  assertExactKeys(input, ['id', 'paperId', 'jobType', 'sourceMode', 'status'], path);
  const sourceMode = field(input, 'sourceMode');
  return {
    id: string(field(input, 'id'), `${path}.id`),
    paperId: string(field(input, 'paperId'), `${path}.paperId`),
    jobType: oneOf(jobTypes, field(input, 'jobType'), `${path}.jobType`),
    sourceMode: sourceMode === null
      ? null
      : oneOf(sourceModes, sourceMode, `${path}.sourceMode`),
    status: oneOf(jobStatuses, field(input, 'status'), `${path}.status`),
  };
};

const decodeSourceEnqueue: Decoder<SourceEnqueueResponse> = (value, path = '$') => {
  const input = inputObject(value, path);
  assertExactKeys(input, ['source', 'job', 'deduplicated'], path);
  return {
    source: decodeSourceSummary(field(input, 'source'), `${path}.source`),
    job: decodeProcessingJobSummary(field(input, 'job'), `${path}.job`),
    deduplicated: boolean(field(input, 'deduplicated'), `${path}.deduplicated`),
  };
};

const decodeSourcePage: Decoder<SourceDocumentPage> = (value, path = '$') => {
  const input = inputObject(value, path);
  assertExactKeys(input, ['items', 'nextCursor'], path);
  const nextCursor = field(input, 'nextCursor');
  return {
    items: array(field(input, 'items'), `${path}.items`).map((item, index) => (
      decodeSourceSummary(item, `${path}.items[${index}]`)
    )),
    nextCursor: nextCursor === null
      ? null
      : string(nextCursor, `${path}.nextCursor`),
  };
};

const decodeArtifactSummary: Decoder<GeneratedArtifactSummary> = (value, path = '$') => {
  const input = inputObject(value, path);
  assertExactKeys(input, ['id', 'paperId', 'kind', 'sourceDocumentId', 'status'], path);
  return {
    id: string(field(input, 'id'), `${path}.id`),
    paperId: string(field(input, 'paperId'), `${path}.paperId`),
    kind: oneOf(artifactKinds, field(input, 'kind'), `${path}.kind`),
    sourceDocumentId: string(field(input, 'sourceDocumentId'), `${path}.sourceDocumentId`),
    status: oneOf(sourceStatuses, field(input, 'status'), `${path}.status`),
  };
};

const decodeArtifactEnqueue: Decoder<ArtifactEnqueueResponse> = (value, path = '$') => {
  const input = inputObject(value, path);
  assertExactKeys(input, ['artifact', 'job', 'deduplicated'], path);
  return {
    artifact: decodeArtifactSummary(field(input, 'artifact'), `${path}.artifact`),
    job: decodeProcessingJobSummary(field(input, 'job'), `${path}.job`),
    deduplicated: boolean(field(input, 'deduplicated'), `${path}.deduplicated`),
  };
};

function decodeJobError(value: unknown, path: string): ProcessingJobError | null {
  if (value === null) return null;
  const input = inputObject(value, path);
  assertExactKeys(input, ['code', 'message'], path);
  return {
    code: string(field(input, 'code'), `${path}.code`),
    message: string(field(input, 'message'), `${path}.message`),
  };
}

const decodeJobDetail: Decoder<ProcessingJobDetail> = (value, path = '$') => {
  const input = inputObject(value, path);
  assertExactKeys(input, [
    'id', 'paperId', 'jobType', 'sourceMode', 'status', 'progress',
    'attempt', 'maxAttempts', 'error', 'createdAt', 'startedAt',
    'finishedAt', 'cancelledAt',
  ], path);
  const summary = decodeProcessingJobSummary({
    id: field(input, 'id'),
    paperId: field(input, 'paperId'),
    jobType: field(input, 'jobType'),
    sourceMode: field(input, 'sourceMode'),
    status: field(input, 'status'),
  }, path);
  const createdAt = nullableTimestamp(field(input, 'createdAt'), `${path}.createdAt`);
  if (createdAt === null) throw new DecodeError(`${path}.createdAt`, 'UTC timestamp', null);
  return {
    ...summary,
    progress: record(field(input, 'progress'), `${path}.progress`),
    attempt: nonNegativeInteger(field(input, 'attempt'), `${path}.attempt`),
    maxAttempts: nonNegativeInteger(field(input, 'maxAttempts'), `${path}.maxAttempts`),
    error: decodeJobError(field(input, 'error'), `${path}.error`),
    createdAt,
    startedAt: nullableTimestamp(field(input, 'startedAt'), `${path}.startedAt`),
    finishedAt: nullableTimestamp(field(input, 'finishedAt'), `${path}.finishedAt`),
    cancelledAt: nullableTimestamp(field(input, 'cancelledAt'), `${path}.cancelledAt`),
  };
};

const defaultClock: ProcessingClock = {
  wait(signal) {
    return new Promise((resolve, reject) => {
      if (signal?.aborted) {
        reject(new DOMException('stopped', 'AbortError'));
        return;
      }
      const timeout = globalThis.setTimeout(resolve, 500);
      signal?.addEventListener('abort', () => {
        globalThis.clearTimeout(timeout);
        reject(new DOMException('stopped', 'AbortError'));
      }, { once: true });
    });
  },
};

function throwIfDetached(signal?: AbortSignal): void {
  if (signal?.aborted) throw new DOMException('stopped', 'AbortError');
}

export function createProcessingGateway(
  client: ApiClient = api,
  clock: ProcessingClock = defaultClock,
) {
  const getJob = (jobId: string, signal?: AbortSignal) => {
    throwIfDetached(signal);
    return client.json(
      `/api/v2/jobs/${encodeURIComponent(jobId)}`,
      decodeJobDetail,
      signalOptions(signal),
    );
  };

  return {
    enqueueSource(
      paperId: string,
      request: SourceEnqueueRequest = { sourceMode: 'native' },
      signal?: AbortSignal,
    ) {
      const body = request.sourceMode === 'native'
        ? { sourceMode: request.sourceMode }
        : {
            sourceMode: request.sourceMode,
            ocrProvider: request.ocrProvider,
            ocrModel: request.ocrModel,
            options: {
              pageBatchSize: request.options.pageBatchSize,
              maxConcurrency: request.options.maxConcurrency,
            },
          };
      return client.json(
        `/api/v2/papers/${encodeURIComponent(paperId)}/sources`,
        decodeSourceEnqueue,
        jsonRequest(body, {
          method: 'POST',
          ...signalOptions(signal),
        }),
      );
    },

    enqueueExplainer(
      paperId: string,
      request: ExplainerEnqueueRequest,
      signal?: AbortSignal,
    ) {
      return client.json(
        `/api/v2/papers/${encodeURIComponent(paperId)}/artifacts/explainer`,
        decodeArtifactEnqueue,
        jsonRequest({
          sourceMode: request.sourceMode,
          sourceDocumentId: request.sourceDocumentId,
          profile: request.profile,
        }, {
          method: 'POST',
          ...signalOptions(signal),
        }),
      );
    },

    enqueueTranslation(
      paperId: string,
      request: TranslationEnqueueRequest,
      signal?: AbortSignal,
    ) {
      return client.json(
        `/api/v2/papers/${encodeURIComponent(paperId)}/artifacts/translation`,
        decodeArtifactEnqueue,
        jsonRequest({
          sourceMode: request.sourceMode,
          sourceDocumentId: request.sourceDocumentId,
        }, {
          method: 'POST',
          ...signalOptions(signal),
        }),
      );
    },

    listSources(paperId: string, query: SourceListQuery = {}, signal?: AbortSignal) {
      const parameters = new URLSearchParams();
      if (query.limit !== undefined) parameters.set('limit', String(query.limit));
      if (query.cursor !== undefined) parameters.set('cursor', query.cursor);
      const suffix = parameters.size > 0 ? `?${parameters.toString()}` : '';
      return client.json(
        `/api/v2/papers/${encodeURIComponent(paperId)}/sources${suffix}`,
        decodeSourcePage,
        signalOptions(signal),
      );
    },

    getJob,

    async waitForTerminal(jobId: string, signal?: AbortSignal): Promise<ProcessingJobDetail> {
      let job = await getJob(jobId, signal);
      while (job.status === 'queued' || job.status === 'running') {
        await clock.wait(signal);
        throwIfDetached(signal);
        job = await getJob(jobId, signal);
      }
      if (job.status === 'failed') {
        throw new BusinessError(
          job.error?.message ?? 'Processing job failed.',
          { jobId: job.id, status: job.status },
          job.error?.code ?? 'PROCESSING_JOB_FAILED',
        );
      }
      if (job.status === 'cancelled') {
        throw new BusinessError(
          'Processing job was cancelled.',
          { jobId: job.id, status: job.status },
          'PROCESSING_JOB_CANCELLED',
        );
      }
      return job;
    },

    cancelJob(jobId: string, signal?: AbortSignal) {
      return client.json(
        `/api/v2/jobs/${encodeURIComponent(jobId)}/cancel`,
        decodeJobDetail,
        { method: 'POST', ...signalOptions(signal) },
      );
    },
  };
}

export const processingGateway = createProcessingGateway();
export type ProcessingGateway = ReturnType<typeof createProcessingGateway>;
