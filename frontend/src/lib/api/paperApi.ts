import { api, jsonRequest } from './client';
import type { ApiClient } from './client';
import {
  decodeCommandChanges,
  decodeCommandId,
  decodeOkCommand,
  decodePaperDetail,
  decodePaperList,
  decodeReviewCompleteCommand,
  decodeReviewSnapshotCommand,
  decodeReviewStartCommand,
} from './decoders';
import { DecodeError } from './errors';
import type { StudyStatus } from './types';

export interface PaperDraft {
  title: string;
  titleZh?: string | null;
  venue?: string | null;
  year?: string | null;
  type?: string | null;
  topic?: string | null;
  url?: string | null;
  pdfUrl?: string | null;
  pdfPath?: string | null;
  tldr?: string | null;
  abstract?: string | null;
  contribution?: string | null;
  authors?: string[];
  relevance?: number | null;
  order?: number | null;
}

export type PaperPatch = Partial<PaperDraft>;

function signalOptions(signal?: AbortSignal): RequestInit {
  return signal ? { signal } : {};
}

function paperQuery(path: string, paperId: string): string {
  return `${path}?${new URLSearchParams({ id: paperId }).toString()}`;
}

function encodePaperFields(fields: PaperPatch): Record<string, unknown> {
  const body: Record<string, unknown> = {};
  if (fields.title !== undefined) body.title = fields.title;
  if (fields.titleZh !== undefined) body.title_zh = fields.titleZh;
  if (fields.venue !== undefined) body.venue = fields.venue;
  if (fields.year !== undefined) body.year = fields.year;
  if (fields.type !== undefined) body.type = fields.type;
  if (fields.topic !== undefined) body.topic = fields.topic;
  if (fields.url !== undefined) body.url = fields.url;
  if (fields.pdfUrl !== undefined) body.pdf_url = fields.pdfUrl;
  if (fields.pdfPath !== undefined) body.pdf_path = fields.pdfPath;
  if (fields.tldr !== undefined) body.tldr = fields.tldr;
  if (fields.abstract !== undefined) body.abstract = fields.abstract;
  if (fields.contribution !== undefined) body.contribution = fields.contribution;
  if (fields.authors !== undefined) body.authors = fields.authors;
  if (fields.relevance !== undefined) body.relevance = fields.relevance;
  if (fields.order !== undefined) body.order_no = fields.order;
  return body;
}

export function createPaperApi(client: ApiClient = api) {
  return {
    listPapers(signal?: AbortSignal) {
      return client.json('/api/papers', decodePaperList, signalOptions(signal));
    },

    getPaper(id: string, signal?: AbortSignal) {
      const paperId = String(id);
      return client.json(paperQuery('/api/paper/get', paperId), decodePaperDetail, signalOptions(signal));
    },

    getNote(id: string, signal?: AbortSignal) {
      const paperId = String(id);
      return client.text(paperQuery('/api/note', paperId), signalOptions(signal));
    },

    getExplainer(id: string, signal?: AbortSignal) {
      const paperId = String(id);
      return client.text(paperQuery('/api/explainer', paperId), signalOptions(signal));
    },

    getTranslation(id: string, signal?: AbortSignal) {
      const paperId = String(id);
      return client.text(paperQuery('/api/translation', paperId), signalOptions(signal));
    },

    getPdfBytes(id: string, signal?: AbortSignal) {
      const paperId = String(id);
      return client.bytes(paperQuery('/pdfbytes', paperId), signalOptions(signal));
    },

    getReviews(signal?: AbortSignal) {
      return client.json('/api/reviews', decodeReviewSnapshotCommand, signalOptions(signal));
    },

    startReview(id: string, signal?: AbortSignal) {
      const paperId = String(id);
      return client.json('/api/reviews/start', decodeReviewStartCommand, jsonRequest(
        { id: paperId }, { method: 'POST', ...signalOptions(signal) },
      ));
    },

    completeReview(id: string, signal?: AbortSignal) {
      const paperId = String(id);
      return client.json('/api/reviews/complete', decodeReviewCompleteCommand, jsonRequest(
        { id: paperId }, { method: 'POST', ...signalOptions(signal) },
      ));
    },

    async saveNote(id: string, content: string, signal?: AbortSignal): Promise<void> {
      const paperId = String(id);
      await client.json('/api/note', decodeOkCommand, jsonRequest(
        { id: paperId, content }, { method: 'POST', ...signalOptions(signal) },
      ));
    },

    async setStatus(id: string, status: StudyStatus, signal?: AbortSignal): Promise<void> {
      const paperId = String(id);
      await client.json('/api/progress', decodeOkCommand, jsonRequest(
        { id: paperId, status }, { method: 'POST', ...signalOptions(signal) },
      ));
    },

    async setFavorite(id: string, favorite: boolean, signal?: AbortSignal): Promise<void> {
      const paperId = String(id);
      await client.json('/api/favorite', decodeOkCommand, jsonRequest(
        { id: paperId, favorite }, { method: 'POST', ...signalOptions(signal) },
      ));
    },

    async deletePaper(id: string, signal?: AbortSignal): Promise<void> {
      const paperId = String(id);
      await client.json('/api/delete', decodeOkCommand, jsonRequest(
        { id: paperId }, { method: 'POST', ...signalOptions(signal) },
      ));
    },

    async addPaper(draft: PaperDraft, signal?: AbortSignal): Promise<string> {
      const value = await client.json('/api/paper/add', decodeCommandId, jsonRequest(
        encodePaperFields(draft), { method: 'POST', ...signalOptions(signal) },
      ));
      if (typeof value !== 'string') throw new DecodeError('$.id', 'paper id string', value);
      return value;
    },

    updatePaper(id: string, patch: PaperPatch, signal?: AbortSignal): Promise<number> {
      const paperId = String(id);
      return client.json('/api/paper/update', decodeCommandChanges, jsonRequest(
        { id: paperId, ...encodePaperFields(patch) }, { method: 'POST', ...signalOptions(signal) },
      ));
    },
  };
}

export const paperApi = createPaperApi();
export type PaperApi = ReturnType<typeof createPaperApi>;
