import { describe, expect, it } from 'vitest';

import { BusinessError } from './errors';
import {
  decodeCandidate,
  decodeCitationGraph,
  decodeExplainerPending,
  decodeExpandCommand,
  decodeJobDetail,
  decodeJobSummary,
  decodeLlmTestCommand,
  decodePaperDetail,
  decodePaperListItem,
  decodePdfScanCommand,
  decodePdfStatus,
  decodeReviewSnapshot,
  decodeSchedule,
  decodeSemanticHit,
  decodeSettingsView,
  decodeTitleTranslationStatus,
  decodeTranslateTextCommand,
  decodeVerification,
} from './decoders';

describe('wire DTO decoders', () => {
  it('normalizes the paper list wire shape without leaking SQLite values', () => {
    const paper = decodePaperListItem({
      id: 'p1', file: 'p1.pdf', title: 'Paper', title_zh: null, venue: 'CVPR', year: 2025,
      type: null, topic: null, pdf_url: null, pdf_path: null, url: null, tldr: null,
      contribution: null, citations: null, created_at: null, source: 'seed', arxiv_id: null,
      doi: null, s2_id: null, openalex_id: null, relevance: null, order: null,
      hasNote: 1, favorite: 0, hasPdf: true,
    });

    expect(paper).toMatchObject({
      id: 'p1', year: '2025', status: '未开始', hasNote: true, favorite: false, hasPdf: true,
    });
  });

  it('keeps paper detail distinct and safely parses its JSON-string arrays', () => {
    const paper = decodePaperDetail({
      id: 'p1', source: 'semanticscholar', title: 'Paper', year: 2024,
      authors: '["Ada","Lin"]', s2_fields: '["Computer Vision"]',
      models: '[]', datasets: null, tags: '["survey"]',
    });

    expect(paper).toMatchObject({
      id: 'p1', year: '2024', authors: ['Ada', 'Lin'], s2Fields: ['Computer Vision'], tags: ['survey'],
    });
    if (paper === null) throw new Error('Expected a paper detail record');
    expect(Object.hasOwn(paper, 'status')).toBe(false);
    expect(decodePaperDetail(null)).toBeNull();
  });

  it('reports the exact detail field path for malformed JSON arrays', () => {
    expect(() => decodePaperDetail({
      id: 'p1', source: 'seed', title: 'Paper', authors: '{"name":"Ada"}',
    })).toThrowError(expect.objectContaining({ kind: 'decode', path: '$.authors' }));
  });

  it('decodes review groups and their paper metadata as one snapshot', () => {
    const snapshot = decodeReviewSnapshot({
      today: '2026-08-05',
      counts: { overdue: 1, dueToday: 0, upcoming: 0, completed: 0 },
      overdue: [{
        paper_id: 'p1', started_at: '2026-08-01', current_step: 2, completed_steps: 1,
        next_due_at: '2026-08-04', completed_at: null, updated_at: '2026-08-04',
        title: 'Paper', title_zh: null, venue: 'CVPR', year: '2025', status: '已理解',
        review_state: 'overdue', total_steps: 7,
      }],
      dueToday: [], upcoming: [], completed: [],
    });

    expect(snapshot.overdue[0]).toMatchObject({
      paperId: 'p1', currentStep: 2, totalSteps: 7, reviewState: 'overdue', status: '已理解',
    });
  });

  it('normalizes candidate and venue verification wire values', () => {
    expect(decodeCandidate({
      source: 'semanticscholar', source_id: 's1', title: 'Candidate', authors: ['Ada'],
      year: 2025, citations: null, fields: [], models: [], datasets: [], tags: [], in_library: 1,
    })).toMatchObject({ year: '2025', inLibrary: true, citations: null });

    expect(decodeVerification({
      venue: 'CVPR', year: 2025, matched: 1, skipped: 0, source_of_truth: 'dblp',
      changed: true, orig_venue: 'CVPR 2025', ccf: 'A', note: '',
    })).toMatchObject({ year: '2025', matched: true, skipped: false, sourceOfTruth: 'dblp' });
  });

  it('keeps job years numeric while schedule years stay a string', () => {
    const summary = decodeJobSummary({
      id: 3, query: 'vision', venues: 'dblp,semanticscholar', year_from: 2024, year_to: 2026,
      max_papers: 12, min_relevance: 0.5, only_a: 1, schedule_id: null, status: 'review',
      found: 4, added: 1, skipped: 0, pending: 3, created_at: 'now', finished_at: null,
    });
    const detail = decodeJobDetail({ ok: true, job: {
      id: 3, query: 'vision', venues: 'dblp', year_from: 2024, year_to: 2026,
      max_papers: 12, min_relevance: 0.5, only_a: 0, schedule_id: null, status: 'review',
      found: 4, added: 1, skipped: 0, log: null, created_at: 'now', finished_at: null,
    }, candidates: [] });
    const schedule = decodeSchedule({
      id: 7, query: 'vision', sources: 'dblp,openalex', years: '2024-2026', max_papers: 12,
      min_relevance: 0.5, only_a: 1, every_days: 7, enabled: 0,
      last_run: null, next_run: 'later', created_at: 'now',
    });

    expect(summary).toMatchObject({ yearFrom: 2024, yearTo: 2026, onlyA: true, sources: ['dblp', 'semanticscholar'] });
    expect(detail.job.onlyA).toBe(false);
    expect(schedule).toMatchObject({ years: '2024-2026', enabled: false, sources: ['dblp', 'openalex'] });
  });

  it('decodes the remaining read and command DTO families', () => {
    expect(decodeCitationGraph({
      nodes: [{ id: 'p1', title: 'Paper', venue: null, year: 2025, type: null, topic: null, citations: null, indeg: 2, outdeg: 1 }],
      links: [{ source: 'p1', target: 'p2' }], edgeCount: 1,
    }).nodes[0]?.year).toBe('2025');
    expect(decodeSettingsView({
      provider: 'deepseek', baseUrl: '', model: '', apiKeyTail: '****1234', hasApiKey: 1,
      s2KeyTail: '', hasS2Key: false, pdfDir: '', explainerDir: '', translationDir: '',
      defaultPdfDir: 'data/pdfs', defaultExplainerDir: 'data/explainers', defaultTranslationDir: 'data/translations',
      resolvedPdfDir: 'F:/pdfs', resolvedExplainerDir: 'F:/explainers', resolvedTranslationDir: 'F:/translations',
      researchTheme: '', embedProvider: 'local', embedApiBase: '', embedApiModel: '',
      embedKeyTail: '', hasEmbedKey: 0,
    })).toMatchObject({ provider: 'deepseek', hasApiKey: true, hasEmbedKey: false });
    expect(decodeTitleTranslationStatus({ ok: true, pending: 4, running: 0 })).toEqual({ pending: 4, running: false });
    expect(decodeExplainerPending({ pending: 3, withPdf: 2, noPdf: 1 })).toEqual({ pending: 3, withPdf: 2, noPdf: 1 });
    expect(decodePdfStatus({ ok: true, id: 'p1', hasPdf: 1, size: 42, path: 'p1.pdf', canDownload: 0 })).toMatchObject({ hasPdf: true, canDownload: false });
    expect(decodePdfScanCommand({ ok: true, dir: 'F:/papers', count: 1, files: [{ path: 'F:/papers/a.pdf', name: 'a.pdf', size: 42 }] }).files).toHaveLength(1);
    expect(decodeTranslateTextCommand({ ok: true, text: '译文' })).toEqual({ text: '译文' });
    expect(decodeLlmTestCommand({ ok: true, output: 'pong' })).toEqual({ output: 'pong' });
    expect(decodeExpandCommand({ ok: true, queries: ['one', 'two'] })).toEqual({ queries: ['one', 'two'] });
    expect(decodeSemanticHit({ id: 'p1', score: 0.98 })).toEqual({ id: 'p1', score: 0.98 });
  });

  it('turns an HTTP 200 command failure into BusinessError', () => {
    expect(() => decodeLlmTestCommand({ ok: false, output: 'missing key' })).toThrow(BusinessError);
  });
});
