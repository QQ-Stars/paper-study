import { DecodeError } from '../api/errors';
import {
  arrayOf,
  boolean,
  decodeCandidate,
  decodeSemanticHit,
  decodeVerification,
  integer,
  string,
} from '../api/decoders';
import type { Candidate, Decoder, SemanticHit, Verification } from '../api/types';

export interface StreamEvent {
  type: string;
}

export interface LineProgressEvent extends StreamEvent {
  type: 'progress';
  line: string;
}

export interface GenericProgressEvent extends StreamEvent {
  type: 'progress';
  line?: string;
}

export interface GenericTerminal extends StreamEvent {
  type: 'result' | 'done';
  ok: boolean;
  error?: string;
  [key: string]: unknown;
}

export interface FailureTerminal<K extends 'result' | 'done'> {
  type: K;
  ok: false;
  error?: string;
  [key: string]: unknown;
}

export type StreamFrame<E, T> =
  | { kind: 'event'; value: E }
  | { kind: 'terminal'; value: T };

export interface StreamContract<E, T> {
  readonly terminalType: string;
  decode(value: unknown, path: string): StreamFrame<E, T>;
}

function inputObject(value: unknown, path: string): object {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) {
    throw new DecodeError(path, 'NDJSON event object', value);
  }
  return value;
}

function field(value: object, key: string): unknown {
  return Reflect.get(value, key);
}

function copyRecord(value: object): Record<string, unknown> {
  const result: Record<string, unknown> = {};
  for (const key of Object.keys(value)) result[key] = field(value, key);
  return result;
}

function optionalError(input: object, path: string): string | undefined {
  const value = field(input, 'error');
  if (value === undefined) return undefined;
  return string(value, `${path}.error`);
}

function withOptionalError<T extends object>(value: T, error: string | undefined): T & { error?: string } {
  return error === undefined ? value : Object.assign(value, { error });
}

function lineProgress(value: object, path: string): LineProgressEvent {
  return { type: 'progress', line: string(field(value, 'line'), `${path}.line`) };
}

function endpointContract<E, S extends { type: K; ok: true }, K extends 'result' | 'done'>(
  terminalType: K,
  decodeProgress: (value: object, path: string) => E,
  decodeSuccess: (value: object, path: string) => S,
): StreamContract<E, S | FailureTerminal<K>> {
  return {
    terminalType,
    decode(value, path) {
      const input = inputObject(value, path);
      const type = field(input, 'type');
      if (type === 'progress') return { kind: 'event', value: decodeProgress(input, path) };
      if (type !== terminalType) throw new DecodeError(`${path}.type`, `progress | ${terminalType}`, type);
      const ok = boolean(field(input, 'ok'), `${path}.ok`);
      const error = optionalError(input, path);
      if (!ok) {
        const failure: FailureTerminal<K> = { type: terminalType, ok: false };
        for (const key of Object.keys(input)) failure[key] = field(input, key);
        failure.type = terminalType;
        failure.ok = false;
        if (error !== undefined) failure.error = error;
        return { kind: 'terminal', value: failure };
      }
      return { kind: 'terminal', value: withOptionalError(decodeSuccess(input, path), error) };
    },
  };
}

export function createTerminalContract(
  terminalType: 'result' | 'done',
): StreamContract<GenericProgressEvent, GenericTerminal> {
  return {
    terminalType,
    decode(value, path) {
      const input = inputObject(value, path);
      const type = field(input, 'type');
      if (type === terminalType) {
        const ok = boolean(field(input, 'ok'), `${path}.ok`);
        const error = optionalError(input, path);
        const copied = copyRecord(input);
        const terminal = Object.assign(copied, { type: terminalType, ok });
        if (error !== undefined) terminal.error = error;
        return { kind: 'terminal', value: terminal };
      }
      if (type !== 'progress') throw new DecodeError(`${path}.type`, `progress | ${terminalType}`, type);
      const line = field(input, 'line');
      if (line !== undefined && typeof line !== 'string') throw new DecodeError(`${path}.line`, 'string', line);
      return { kind: 'event', value: line === undefined ? { type: 'progress' } : { type: 'progress', line } };
    },
  };
}

export const resultContract = createTerminalContract('result');
export const doneContract = createTerminalContract('done');

export type TitleTranslationProgress =
  | { type: 'progress'; stage: 'batch'; total: number }
  | {
    type: 'progress'; stage: 'item'; state: 'start' | 'done' | 'skipped' | 'failed';
    index: number; total: number; id: string; title?: string; titleZh?: string; error?: string;
  };

export interface TitleTranslationFailure {
  id: string;
  title: string;
  error: string;
}

export interface TitleTranslationSummary {
  total: number;
  done: number;
  failed: TitleTranslationFailure[];
  cancelled: boolean;
}

export type TitleTranslationsTerminal =
  | { type: 'result'; ok: true; summary: TitleTranslationSummary; error?: string }
  | FailureTerminal<'result'>;

const decodeTitleProgress = (value: object, path: string): TitleTranslationProgress => {
  const stage = field(value, 'stage');
  if (stage === 'batch') {
    return { type: 'progress', stage, total: integer(field(value, 'total'), `${path}.total`) };
  }
  if (stage !== 'item') throw new DecodeError(`${path}.stage`, 'batch | item', stage);
  const state = field(value, 'state');
  if (state !== 'start' && state !== 'done' && state !== 'skipped' && state !== 'failed') {
    throw new DecodeError(`${path}.state`, 'start | done | skipped | failed', state);
  }
  const event: {
    type: 'progress'; stage: 'item'; state: 'start' | 'done' | 'skipped' | 'failed';
    index: number; total: number; id: string; title?: string; titleZh?: string; error?: string;
  } = {
    type: 'progress', stage, state,
    index: integer(field(value, 'index'), `${path}.index`),
    total: integer(field(value, 'total'), `${path}.total`),
    id: string(field(value, 'id'), `${path}.id`),
  };
  const title = field(value, 'title');
  const titleZh = field(value, 'title_zh');
  const error = field(value, 'error');
  if (title !== undefined) event.title = string(title, `${path}.title`);
  if (titleZh !== undefined) event.titleZh = string(titleZh, `${path}.title_zh`);
  if (error !== undefined) event.error = string(error, `${path}.error`);
  return event;
};

const decodeTitleFailure: Decoder<TitleTranslationFailure> = (value, path = '$') => {
  const input = inputObject(value, path);
  return {
    id: string(field(input, 'id'), `${path}.id`),
    title: string(field(input, 'title'), `${path}.title`),
    error: string(field(input, 'error'), `${path}.error`),
  };
};

export const titleTranslationsContract: StreamContract<TitleTranslationProgress, TitleTranslationsTerminal> = endpointContract(
  'result',
  decodeTitleProgress,
  (value, path) => {
    const summary = inputObject(field(value, 'summary'), `${path}.summary`);
    return {
      type: 'result', ok: true,
      summary: {
        total: integer(field(summary, 'total'), `${path}.summary.total`),
        done: integer(field(summary, 'done'), `${path}.summary.done`),
        failed: arrayOf(decodeTitleFailure)(field(summary, 'failed'), `${path}.summary.failed`),
        cancelled: boolean(field(summary, 'cancelled'), `${path}.summary.cancelled`),
      },
    };
  },
);

export type SearchTerminal =
  | { type: 'result'; ok: true; candidates: Candidate[]; error?: string }
  | FailureTerminal<'result'>;
export const searchContract: StreamContract<LineProgressEvent, SearchTerminal> = endpointContract(
  'result', lineProgress,
  (value, path) => ({ type: 'result', ok: true, candidates: arrayOf(decodeCandidate)(field(value, 'candidates'), `${path}.candidates`) }),
);

export type VerifyVenueTerminal =
  | { type: 'result'; ok: true; verifications: Verification[]; error?: string }
  | FailureTerminal<'result'>;
export const verifyVenueContract: StreamContract<LineProgressEvent, VerifyVenueTerminal> = endpointContract(
  'result', lineProgress,
  (value, path) => ({ type: 'result', ok: true, verifications: arrayOf(decodeVerification)(field(value, 'verifications'), `${path}.verifications`) }),
);

export type MarkdownTerminal =
  | { type: 'result'; ok: true; markdown: string; error?: string }
  | FailureTerminal<'result'>;
const markdownContract = (): StreamContract<LineProgressEvent, MarkdownTerminal> => endpointContract(
  'result', lineProgress,
  (value, path) => ({ type: 'result', ok: true, markdown: string(field(value, 'markdown'), `${path}.markdown`) }),
);
export const explainContract = markdownContract();
export const translateContract = markdownContract();

export interface ExplainBatchSummary {
  total: number;
  done: number;
  failed: string[];
  skippedNoPdf: string[];
}
export type ExplainBatchTerminal =
  | { type: 'result'; ok: true; summary: ExplainBatchSummary; error?: string }
  | FailureTerminal<'result'>;
export const explainBatchContract: StreamContract<LineProgressEvent, ExplainBatchTerminal> = endpointContract(
  'result', lineProgress,
  (value, path) => {
    const summary = inputObject(field(value, 'summary'), `${path}.summary`);
    return {
      type: 'result', ok: true,
      summary: {
        total: integer(field(summary, 'total'), `${path}.summary.total`),
        done: integer(field(summary, 'done'), `${path}.summary.done`),
        failed: arrayOf(string)(field(summary, 'failed'), `${path}.summary.failed`),
        skippedNoPdf: arrayOf(string)(field(summary, 'skipped_no_pdf'), `${path}.summary.skipped_no_pdf`),
      },
    };
  },
);

export type RecommendTerminal = SearchTerminal;
export const recommendContract: StreamContract<LineProgressEvent, RecommendTerminal> = endpointContract(
  'result', lineProgress,
  (value, path) => ({ type: 'result', ok: true, candidates: arrayOf(decodeCandidate)(field(value, 'candidates'), `${path}.candidates`) }),
);

export type EmbedTerminal =
  | { type: 'result'; ok: true; indexed: number; total: number; error?: string }
  | FailureTerminal<'result'>;
export const embedContract: StreamContract<LineProgressEvent, EmbedTerminal> = endpointContract(
  'result', lineProgress,
  (value, path) => ({
    type: 'result', ok: true,
    indexed: integer(field(value, 'indexed'), `${path}.indexed`),
    total: integer(field(value, 'total'), `${path}.total`),
  }),
);

export type SemanticSearchTerminal =
  | { type: 'result'; ok: true; results: SemanticHit[]; error?: string }
  | FailureTerminal<'result'>;
export const semanticSearchContract: StreamContract<LineProgressEvent, SemanticSearchTerminal> = endpointContract(
  'result', lineProgress,
  (value, path) => ({ type: 'result', ok: true, results: arrayOf(decodeSemanticHit)(field(value, 'results'), `${path}.results`) }),
);

export type ImportPdfsTerminal =
  | { type: 'result'; ok: true; added: number; dup: number; failed: number; error?: string }
  | FailureTerminal<'result'>;
export const importPdfsContract: StreamContract<LineProgressEvent, ImportPdfsTerminal> = endpointContract(
  'result', lineProgress,
  (value, path) => ({
    type: 'result', ok: true,
    added: integer(field(value, 'added'), `${path}.added`),
    dup: integer(field(value, 'dup'), `${path}.dup`),
    failed: integer(field(value, 'failed'), `${path}.failed`),
  }),
);

export type DownloadPdfsTerminal =
  | { type: 'result'; ok: true; downloaded: number; skipped: number; failed: number; total: number; error?: string }
  | FailureTerminal<'result'>;
export const downloadPdfsContract: StreamContract<LineProgressEvent, DownloadPdfsTerminal> = endpointContract(
  'result', lineProgress,
  (value, path) => ({
    type: 'result', ok: true,
    downloaded: integer(field(value, 'downloaded'), `${path}.downloaded`),
    skipped: integer(field(value, 'skipped'), `${path}.skipped`),
    failed: integer(field(value, 'failed'), `${path}.failed`),
    total: integer(field(value, 'total'), `${path}.total`),
  }),
);

function stringMapping(value: unknown, path: string): Record<string, string> {
  const input = inputObject(value, path);
  const result: Record<string, string> = {};
  for (const key of Object.keys(input)) result[key] = string(field(input, key), `${path}.${key}`);
  return result;
}

export type NormalizeVenuesTerminal =
  | { type: 'result'; ok: true; changed: number; mapping: Record<string, string>; error?: string }
  | FailureTerminal<'result'>;
export const normalizeVenuesContract: StreamContract<LineProgressEvent, NormalizeVenuesTerminal> = endpointContract(
  'result', lineProgress,
  (value, path) => ({
    type: 'result', ok: true,
    changed: integer(field(value, 'changed'), `${path}.changed`),
    mapping: stringMapping(field(value, 'mapping'), `${path}.mapping`),
  }),
);

export type CitationBuildTerminal =
  | { type: 'result'; ok: true; edges: number; nodes: number; error?: string }
  | FailureTerminal<'result'>;
export const citationBuildContract: StreamContract<LineProgressEvent, CitationBuildTerminal> = endpointContract(
  'result', lineProgress,
  (value, path) => ({
    type: 'result', ok: true,
    edges: integer(field(value, 'edges'), `${path}.edges`),
    nodes: integer(field(value, 'nodes'), `${path}.nodes`),
  }),
);

export type DoneAddedTerminal =
  | { type: 'done'; ok: true; added: number; error?: string }
  | FailureTerminal<'done'>;
const doneAddedContract = (): StreamContract<LineProgressEvent, DoneAddedTerminal> => endpointContract(
  'done', lineProgress,
  (value, path) => ({ type: 'done', ok: true, added: integer(field(value, 'added'), `${path}.added`) }),
);
export const ingestSelectedContract = doneAddedContract();
export const jobsConfirmContract = doneAddedContract();

export const endpointStreamContracts = {
  titleTranslations: titleTranslationsContract,
  search: searchContract,
  verifyVenue: verifyVenueContract,
  explain: explainContract,
  explainBatch: explainBatchContract,
  translate: translateContract,
  recommend: recommendContract,
  embed: embedContract,
  semanticSearch: semanticSearchContract,
  importPdfs: importPdfsContract,
  downloadPdfs: downloadPdfsContract,
  normalizeVenues: normalizeVenuesContract,
  citationBuild: citationBuildContract,
  ingestSelected: ingestSelectedContract,
  jobsConfirm: jobsConfirmContract,
};
