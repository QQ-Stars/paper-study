import { describe, expect, it } from 'vitest';

import type { Candidate } from '../../lib/api/types';
import {
  acquireReducer,
  candidateKey,
  createAcquireState,
  normalizeSearchDraft,
} from './acquireReducer';

function candidate(
  sourceId: string,
  overrides: Partial<Candidate> = {},
): Candidate {
  return {
    source: 'arxiv',
    sourceId,
    title: `Candidate ${sourceId}`,
    authors: [],
    venue: null,
    year: '2026',
    abstract: null,
    tldr: null,
    fields: [],
    citations: null,
    url: null,
    pdfUrl: null,
    arxivId: sourceId,
    doi: null,
    s2Id: null,
    ccf: null,
    type: null,
    topic: null,
    task: null,
    models: [],
    datasets: [],
    contribution: null,
    llmTldr: null,
    tags: [],
    relevance: 0.8,
    inLibrary: false,
    candidateId: null,
    ...overrides,
  };
}

describe('acquireReducer', () => {
  it('lets only the current run publish candidates and selects only new papers', () => {
    const available = candidate('2401.00001');
    const existing = candidate('2401.00002', { inLibrary: true });
    let state = createAcquireState();

    state = acquireReducer(state, { type: 'start', operation: 'search', runId: 1 });
    state = acquireReducer(state, { type: 'start', operation: 'search', runId: 2 });
    state = acquireReducer(state, {
      type: 'search-complete',
      runId: 1,
      candidates: [candidate('late')],
    });
    expect(state.candidates).toEqual([]);

    state = acquireReducer(state, {
      type: 'search-complete',
      runId: 2,
      candidates: [available, existing],
    });

    expect(state.status).toBe('ready');
    expect(state.candidates).toEqual([available, existing]);
    expect(state.selectedKeys).toEqual([candidateKey(available)]);
  });

  it('preserves candidates across verification and applies the authoritative result by position', () => {
    const original = candidate('2401.00001', { venue: 'CoRR', ccf: null });
    let state = createAcquireState();
    state = acquireReducer(state, { type: 'start', operation: 'search', runId: 1 });
    state = acquireReducer(state, {
      type: 'search-complete',
      runId: 1,
      candidates: [original],
    });
    state = acquireReducer(state, { type: 'start', operation: 'verify', runId: 2 });
    state = acquireReducer(state, {
      type: 'verify-complete',
      runId: 2,
      verifications: [{
        venue: 'CHI',
        year: '2025',
        matched: true,
        skipped: false,
        sourceOfTruth: 'dblp',
        changed: true,
        originalVenue: 'CoRR',
        ccf: 'A',
        note: 'corrected',
        error: false,
      }],
    });

    expect(state.candidates[0]).toMatchObject({ venue: 'CHI', year: '2025', ccf: 'A' });
    expect(state.verifications[0]?.sourceOfTruth).toBe('dblp');
    expect(state.selectedKeys).toEqual([candidateKey(original)]);
  });

  it('validates the required fields, filters sources, and clamps the server request', () => {
    expect(normalizeSearchDraft({ query: ' ', sources: [] })).toEqual({
      ok: false,
      errors: ['请输入研究方向', '至少选择一个学术来源'],
    });

    expect(normalizeSearchDraft({
      query: '  lifecycle-safe readers  ',
      sources: ['arxiv', 'unknown', 'arxiv', 'dblp'],
      years: '',
      max: 999,
      minRelevance: -2,
      expand: true,
      onlyA: true,
      queries: ['  first ', '', 'first', 'second'],
    })).toEqual({
      ok: true,
      request: {
        query: 'lifecycle-safe readers',
        sources: ['arxiv', 'dblp'],
        years: '2024-2026',
        max: 60,
        minRelevance: 0,
        expand: true,
        onlyA: true,
        queries: ['first', 'second'],
      },
    });
  });
});
