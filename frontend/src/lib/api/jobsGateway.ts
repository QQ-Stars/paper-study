import { api, jsonRequest, type ApiClient } from './client';
import {
  decodeCommandId,
  decodeJobDetail,
  decodeJobList,
  decodeOkCommand,
} from './decoders';
import { DecodeError } from './errors';
import {
  businessFailure,
  encodeCandidate,
  signalOptions,
  streamOptions,
  type StreamCommandOptions,
} from './gatewayTransport';
import type { IngestCandidatesRequest } from './types';
import { jobsConfirmContract } from '../streaming/contracts';
import type {
  DoneAddedTerminal,
  LineProgressEvent,
} from '../streaming/contracts';

export interface CreateJobInput {
  query: string;
  sources: string[];
  years?: string;
  max?: number;
  minRelevance?: number;
  onlyA?: boolean;
  /** 用户在采集页生成/编辑的检索词；后端以 JSON 字符串存入 ingest_jobs.queries。 */
  queries?: string[];
}

export function createJobsGateway(client: ApiClient = api) {
  return {
    listJobs(signal?: AbortSignal) {
      return client.json('/api/jobs', decodeJobList, signalOptions(signal));
    },

    getJob(id: number, signal?: AbortSignal) {
      const jobId = Number(id);
      const url = `/api/jobs/detail?${new URLSearchParams({ id: String(jobId) }).toString()}`;
      return client.json(url, decodeJobDetail, signalOptions(signal));
    },

    async createJob(input: CreateJobInput, signal?: AbortSignal): Promise<number> {
      const { queries, ...rest } = input;
      const cleanedQueries = queries?.map((item) => item.trim()).filter(Boolean);
      const body = cleanedQueries && cleanedQueries.length > 0
        ? { ...rest, queries: JSON.stringify(cleanedQueries) }
        : rest;
      const value = await client.json('/api/jobs', decodeCommandId, jsonRequest(
        body, { method: 'POST', ...signalOptions(signal) },
      ));
      if (typeof value !== 'number') throw new DecodeError('$.id', 'job id number', value);
      return value;
    },

    async deleteJob(id: number, signal?: AbortSignal): Promise<void> {
      const jobId = Number(id);
      await client.json('/api/jobs/delete', decodeOkCommand, jsonRequest(
        { id: jobId }, { method: 'POST', ...signalOptions(signal) },
      ));
    },

    async confirmJob(
      id: number,
      input: IngestCandidatesRequest,
      options: StreamCommandOptions<LineProgressEvent, DoneAddedTerminal> = {},
    ) {
      const jobId = Number(id);
      const body = { jobId, ...input, candidates: input.candidates.map(encodeCandidate) };
      const terminal = await client.ndjson('/api/jobs/confirm', jobsConfirmContract, streamOptions(body, options));
      if (!terminal.ok) return businessFailure(terminal);
      return terminal;
    },
  };
}

export const jobsGateway = createJobsGateway();
export type JobsGateway = ReturnType<typeof createJobsGateway>;
