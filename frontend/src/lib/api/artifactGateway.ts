import { api, jsonRequest, type ApiClient } from './client';
import {
  decodeExplainerPending,
  decodeTitleTranslationStatus,
  decodeTranslateTextCommand,
} from './decoders';
import {
  businessFailure,
  signalOptions,
  streamOptions,
  type StreamCommandOptions,
} from './gatewayTransport';
import {
  explainBatchContract,
  explainContract,
  titleTranslationsContract,
  translateContract,
} from '../streaming/contracts';
import type {
  ExplainBatchTerminal,
  LineProgressEvent,
  MarkdownTerminal,
  TitleTranslationProgress,
  TitleTranslationsTerminal,
} from '../streaming/contracts';

export function createArtifactGateway(client: ApiClient = api) {
  return {
    getTitleTranslationStatus(signal?: AbortSignal) {
      return client.json('/api/title-translations', decodeTitleTranslationStatus, signalOptions(signal));
    },

    async translateTitles(
      limit = 0,
      options: StreamCommandOptions<TitleTranslationProgress, TitleTranslationsTerminal> = {},
    ) {
      const terminal = await client.ndjson(
        '/api/title-translations', titleTranslationsContract, streamOptions({ limit }, options),
      );
      if (!terminal.ok) return businessFailure(terminal);
      return terminal;
    },

    async explainPaper(
      id: string,
      deep = false,
      options: StreamCommandOptions<LineProgressEvent, MarkdownTerminal> = {},
    ) {
      const paperId = String(id);
      const terminal = await client.ndjson('/api/explain', explainContract, streamOptions({ id: paperId, deep }, options));
      if (!terminal.ok) return businessFailure(terminal);
      return terminal;
    },

    getExplainerPending(signal?: AbortSignal) {
      return client.json('/api/explain-batch', decodeExplainerPending, signalOptions(signal));
    },

    async explainBatch(
      limit = 0,
      options: StreamCommandOptions<LineProgressEvent, ExplainBatchTerminal> = {},
    ) {
      const terminal = await client.ndjson('/api/explain-batch', explainBatchContract, streamOptions({ limit }, options));
      if (!terminal.ok) return businessFailure(terminal);
      return terminal;
    },

    async translatePaper(
      id: string,
      options: StreamCommandOptions<LineProgressEvent, MarkdownTerminal> = {},
    ) {
      const paperId = String(id);
      const terminal = await client.ndjson('/api/translate', translateContract, streamOptions({ id: paperId }, options));
      if (!terminal.ok) return businessFailure(terminal);
      return terminal;
    },

    translateText(text: string, signal?: AbortSignal) {
      return client.json('/api/translate-text', decodeTranslateTextCommand, jsonRequest(
        { text }, { method: 'POST', ...signalOptions(signal) },
      ));
    },
  };
}

export const artifactGateway = createArtifactGateway();
export type ArtifactGateway = ReturnType<typeof createArtifactGateway>;
