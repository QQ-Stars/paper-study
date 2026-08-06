import { jsonRequest, type TransportOptions } from './client';
import { BusinessError } from './errors';
import type { Candidate } from './types';

export interface StreamCommandOptions<E, T> {
  signal?: AbortSignal;
  onEvent?: (event: E | T) => void;
}

export function signalOptions(signal?: AbortSignal): RequestInit {
  return signal ? { signal } : {};
}

export function streamOptions<E, T>(
  body: unknown,
  options: StreamCommandOptions<E, T> = {},
): TransportOptions<E, T> {
  return {
    ...jsonRequest(body, { method: 'POST', ...signalOptions(options.signal) }),
    onEvent: options.onEvent,
  };
}

export function businessFailure(terminal: { ok: false; error?: string }): never {
  throw new BusinessError(terminal.error || '服务端任务失败', terminal);
}

export function encodeCandidate(candidate: Candidate): Record<string, unknown> {
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
