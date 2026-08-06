import { api, jsonRequest, type ApiClient } from './client';
import { decodeExpandCommand, decodeOutputCommand } from './decoders';
import type {
  Candidate,
  IngestCandidatesRequest,
  SearchRequest,
} from './types';
import {
  businessFailure,
  encodeCandidate,
  signalOptions,
  streamOptions,
  type StreamCommandOptions,
} from './gatewayTransport';
import {
  ingestSelectedContract,
  searchContract,
  verifyVenueContract,
} from '../streaming/contracts';
import type {
  DoneAddedTerminal,
  LineProgressEvent,
  SearchTerminal,
  VerifyVenueTerminal,
} from '../streaming/contracts';

export function createAcquisitionGateway(client: ApiClient = api) {
  return {
    expand(query: string, expandN = 6, signal?: AbortSignal) {
      return client.json('/api/expand', decodeExpandCommand, jsonRequest(
        { query, expandN }, { method: 'POST', ...signalOptions(signal) },
      ));
    },

    ingest(request: SearchRequest & { deep?: boolean; downloadPdf?: boolean }, signal?: AbortSignal) {
      return client.json('/api/ingest', decodeOutputCommand, jsonRequest(
        request, { method: 'POST', ...signalOptions(signal) },
      ));
    },

    async search(
      request: SearchRequest,
      options: StreamCommandOptions<LineProgressEvent, SearchTerminal> = {},
    ) {
      const terminal = await client.ndjson('/api/search', searchContract, streamOptions(request, options));
      if (!terminal.ok) return businessFailure(terminal);
      return terminal;
    },

    async verifyVenue(
      candidates: Candidate[],
      sources: string[] = ['dblp', 'semanticscholar'],
      options: StreamCommandOptions<LineProgressEvent, VerifyVenueTerminal> = {},
    ) {
      const body = { candidates: candidates.map(encodeCandidate), sources };
      const terminal = await client.ndjson('/api/verify-venue', verifyVenueContract, streamOptions(body, options));
      if (!terminal.ok) return businessFailure(terminal);
      return terminal;
    },

    async ingestSelected(
      input: IngestCandidatesRequest,
      options: StreamCommandOptions<LineProgressEvent, DoneAddedTerminal> = {},
    ) {
      const body = { ...input, candidates: input.candidates.map(encodeCandidate) };
      const terminal = await client.ndjson('/api/ingest-selected', ingestSelectedContract, streamOptions(body, options));
      if (!terminal.ok) return businessFailure(terminal);
      return terminal;
    },
  };
}

export const acquisitionGateway = createAcquisitionGateway();
export type AcquisitionGateway = ReturnType<typeof createAcquisitionGateway>;
