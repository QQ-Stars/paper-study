import { api, jsonRequest, type ApiClient } from './client';
import { BusinessError } from './errors';
import type { ProcessingGateway } from './processingGateway';
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

export interface ArtifactGatewayConfiguration {
  processingGateway?: ProcessingGateway;
  explainerReader?: (paperId: string, signal?: AbortSignal) => Promise<string>;
  translationReader?: (paperId: string, signal?: AbortSignal) => Promise<string>;
}

export function createArtifactGateway(
  client: ApiClient = api,
  configuration: ArtifactGatewayConfiguration = {},
) {
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
      if (configuration.processingGateway) {
        const processing = configuration.processingGateway;
        const sourceEnqueue = await processing.enqueueSource(
          paperId,
          { sourceMode: 'native' },
          options.signal,
        );
        await processing.waitForTerminal(sourceEnqueue.job.id, options.signal);
        const sourcePage = await processing.listSources(
          paperId,
          { limit: 100 },
          options.signal,
        );
        const source = sourcePage.items.find((item) => (
          item.id === sourceEnqueue.source.id
          && item.paperId === paperId
          && item.mode === 'native'
        ));
        if (source?.status !== 'ready') {
          throw new BusinessError(
            'Source document is not ready.',
            { paperId, sourceDocumentId: sourceEnqueue.source.id },
            'SOURCE_DOCUMENT_NOT_READY',
          );
        }
        const artifactEnqueue = await processing.enqueueExplainer(paperId, {
          sourceMode: 'native',
          sourceDocumentId: source.id,
          profile: deep ? 'deep' : 'standard',
        }, options.signal);
        await processing.waitForTerminal(artifactEnqueue.job.id, options.signal);
        const readExplainer = configuration.explainerReader ?? ((fixedPaperId, signal) => (
          client.text(
            `/api/explainer?id=${encodeURIComponent(fixedPaperId)}`,
            signalOptions(signal),
          )
        ));
        const markdown = await readExplainer(paperId, options.signal);
        const normalizedMarkdown = markdown.trim();
        if (!normalizedMarkdown || normalizedMarkdown === '*(暂无讲解)*') {
          throw new BusinessError(
            'Explainer projection is empty.',
            { paperId },
            'EXPLAINER_PROJECTION_EMPTY',
          );
        }
        return { type: 'result', ok: true, markdown } as const;
      }
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
      if (configuration.processingGateway) {
        const processing = configuration.processingGateway;
        const sourceEnqueue = await processing.enqueueSource(
          paperId,
          { sourceMode: 'native' },
          options.signal,
        );
        await processing.waitForTerminal(sourceEnqueue.job.id, options.signal);
        const sourcePage = await processing.listSources(
          paperId,
          { limit: 100 },
          options.signal,
        );
        const source = sourcePage.items.find((item) => (
          item.id === sourceEnqueue.source.id
          && item.paperId === paperId
          && item.mode === 'native'
        ));
        if (source?.status !== 'ready') {
          throw new BusinessError(
            'Source document is not ready.',
            { paperId, sourceDocumentId: sourceEnqueue.source.id },
            'SOURCE_DOCUMENT_NOT_READY',
          );
        }
        const artifactEnqueue = await processing.enqueueTranslation(paperId, {
          sourceMode: 'native',
          sourceDocumentId: source.id,
        }, options.signal);
        await processing.waitForTerminal(artifactEnqueue.job.id, options.signal);
        const readTranslation = configuration.translationReader ?? ((fixedPaperId, signal) => (
          client.text(
            `/api/translation?id=${encodeURIComponent(fixedPaperId)}`,
            signalOptions(signal),
          )
        ));
        const markdown = await readTranslation(paperId, options.signal);
        if (!markdown.trim()) {
          throw new BusinessError(
            'Translation projection is empty.',
            { paperId },
            'TRANSLATION_PROJECTION_EMPTY',
          );
        }
        return { type: 'result', ok: true, markdown } as const;
      }
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
