import { api, type ApiClient } from './client';
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

export function createInsightsGateway(client: ApiClient = api) {
  return {
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
