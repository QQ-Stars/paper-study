import { api, jsonRequest } from './client';
import type { ApiClient, TransportOptions } from './client';
import {
  decodeCitationGraph,
  decodeCommandId,
  decodeExplainerPending,
  decodeExpandCommand,
  decodeJobDetail,
  decodeJobList,
  decodeLlmTestCommand,
  decodeOkCommand,
  decodeOutputCommand,
  decodePdfScanCommand,
  decodePdfStatus,
  decodeScheduleList,
  decodeSettingsView,
  decodeTitleTranslationStatus,
  decodeTranslateTextCommand,
} from './decoders';
import { BusinessError, DecodeError } from './errors';
import type {
  Candidate,
  IngestCandidatesRequest,
  SearchRequest,
  SettingsUpdate,
} from './types';
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
} from '../streaming/contracts';
import type {
  CitationBuildTerminal,
  DoneAddedTerminal,
  DownloadPdfsTerminal,
  EmbedTerminal,
  ExplainBatchTerminal,
  ImportPdfsTerminal,
  LineProgressEvent,
  MarkdownTerminal,
  NormalizeVenuesTerminal,
  RecommendTerminal,
  SearchTerminal,
  SemanticSearchTerminal,
  TitleTranslationProgress,
  TitleTranslationsTerminal,
  VerifyVenueTerminal,
} from '../streaming/contracts';

export interface StreamCommandOptions<E, T> {
  signal?: AbortSignal;
  onEvent?: (event: E | T) => void;
}

export interface CreateJobInput {
  query: string;
  sources: string[];
  years?: string;
  max?: number;
  minRelevance?: number;
  onlyA?: boolean;
}

export interface CreateScheduleInput extends CreateJobInput {
  everyDays?: number;
}

export const streamSideEffectPolicies = {
  titleTranslations: { reconcileOn: 'settled', facts: ['papers', 'title-translation-status'] },
  explain: { reconcileOn: 'settled', facts: ['explainer', 'papers'] },
  explainBatch: { reconcileOn: 'settled', facts: ['explainers', 'papers'] },
  translate: { reconcileOn: 'settled', facts: ['translation', 'papers'] },
  embed: { reconcileOn: 'settled', facts: ['semantic-index'] },
  importPdfs: { reconcileOn: 'settled', facts: ['papers'] },
  downloadPdfs: { reconcileOn: 'settled', facts: ['papers', 'pdf-status'] },
  normalizeVenues: { reconcileOn: 'settled', facts: ['papers'] },
  citationBuild: { reconcileOn: 'settled', facts: ['citation-graph'] },
  ingestSelected: { reconcileOn: 'settled', facts: ['papers'] },
  jobsConfirm: { reconcileOn: 'settled', facts: ['papers', 'jobs', 'job-detail'] },
} as const;

function signalOptions(signal?: AbortSignal): RequestInit {
  return signal ? { signal } : {};
}

function streamOptions<E, T>(
  body: unknown,
  options: StreamCommandOptions<E, T> = {},
): TransportOptions<E, T> {
  return {
    ...jsonRequest(body, { method: 'POST', ...signalOptions(options.signal) }),
    onEvent: options.onEvent,
  };
}

function businessFailure(terminal: { ok: false; error?: string }): never {
  throw new BusinessError(terminal.error || '服务端任务失败', terminal);
}

function encodeCandidate(candidate: Candidate): Record<string, unknown> {
  return {
    source: candidate.source,
    source_id: candidate.sourceId,
    title: candidate.title,
    authors: candidate.authors,
    venue: candidate.venue,
    year: candidate.year,
    abstract: candidate.abstract,
    tldr: candidate.tldr,
    fields: candidate.fields,
    citations: candidate.citations,
    url: candidate.url,
    pdf_url: candidate.pdfUrl,
    arxiv_id: candidate.arxivId,
    doi: candidate.doi,
    s2_id: candidate.s2Id,
    ccf: candidate.ccf,
    type: candidate.type,
    topic: candidate.topic,
    task: candidate.task,
    models: candidate.models,
    datasets: candidate.datasets,
    contribution: candidate.contribution,
    llm_tldr: candidate.llmTldr,
    tags: candidate.tags,
    relevance: candidate.relevance,
    in_library: candidate.inLibrary,
    _cid: candidate.candidateId,
  };
}

export function createWorkspaceApi(client: ApiClient = api) {
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

    scanPdfs(directory: string, signal?: AbortSignal) {
      const dir = String(directory);
      const url = `/api/scan-pdfs?${new URLSearchParams({ dir }).toString()}`;
      return client.json(url, decodePdfScanCommand, signalOptions(signal));
    },

    async importPdfs(
      paths: string[],
      enrich = true,
      options: StreamCommandOptions<LineProgressEvent, ImportPdfsTerminal> = {},
    ) {
      const terminal = await client.ndjson('/api/import-pdfs', importPdfsContract, streamOptions({ paths, enrich }, options));
      if (!terminal.ok) return businessFailure(terminal);
      return terminal;
    },

    async downloadPdfs(
      input: { ids?: string[]; limit?: number } = {},
      options: StreamCommandOptions<LineProgressEvent, DownloadPdfsTerminal> = {},
    ) {
      const terminal = await client.ndjson('/api/download-pdfs', downloadPdfsContract, streamOptions(input, options));
      if (!terminal.ok) return businessFailure(terminal);
      return terminal;
    },

    getPdfStatus(id: string, signal?: AbortSignal) {
      const paperId = String(id);
      const url = `/api/pdf/status?${new URLSearchParams({ id: paperId }).toString()}`;
      return client.json(url, decodePdfStatus, signalOptions(signal));
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

    getSettings(signal?: AbortSignal) {
      return client.json('/api/settings', decodeSettingsView, signalOptions(signal));
    },

    async saveSettings(update: SettingsUpdate, signal?: AbortSignal): Promise<void> {
      await client.json('/api/settings', decodeOkCommand, jsonRequest(
        update, { method: 'POST', ...signalOptions(signal) },
      ));
    },

    testLlm(signal?: AbortSignal) {
      return client.json('/api/test-llm', decodeLlmTestCommand, jsonRequest(
        {}, { method: 'POST', ...signalOptions(signal) },
      ));
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

    listSchedules(signal?: AbortSignal) {
      return client.json('/api/schedules', decodeScheduleList, signalOptions(signal));
    },

    async createSchedule(input: CreateScheduleInput, signal?: AbortSignal): Promise<number> {
      const value = await client.json('/api/schedules', decodeCommandId, jsonRequest(
        input, { method: 'POST', ...signalOptions(signal) },
      ));
      if (typeof value !== 'number') throw new DecodeError('$.id', 'schedule id number', value);
      return value;
    },

    async toggleSchedule(id: number, enabled: boolean, signal?: AbortSignal): Promise<void> {
      const scheduleId = Number(id);
      await client.json('/api/schedules/toggle', decodeOkCommand, jsonRequest(
        { id: scheduleId, enabled }, { method: 'POST', ...signalOptions(signal) },
      ));
    },

    async deleteSchedule(id: number, signal?: AbortSignal): Promise<void> {
      const scheduleId = Number(id);
      await client.json('/api/schedules/delete', decodeOkCommand, jsonRequest(
        { id: scheduleId }, { method: 'POST', ...signalOptions(signal) },
      ));
    },
  };
}

export const workspaceApi = createWorkspaceApi();
export type WorkspaceApi = ReturnType<typeof createWorkspaceApi>;
