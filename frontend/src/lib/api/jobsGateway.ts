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
      const value = await client.json('/api/jobs', decodeCommandId, jsonRequest(
        input, { method: 'POST', ...signalOptions(signal) },
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
