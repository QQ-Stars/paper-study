import { describe, expect, it } from 'vitest';

import type { PaperListItem } from '../../lib/api/types';
import {
  applyLibraryFilters,
  defaultLibraryFilters,
  reconcileLibrarySelection,
} from './filters';

function paper(
  id: string,
  overrides: Partial<PaperListItem> = {},
): PaperListItem {
  return {
    id,
    file: `${id}.pdf`,
    title: `Paper ${id}`,
    titleZh: null,
    venue: null,
    year: null,
    type: null,
    topic: null,
    pdfUrl: null,
    pdfPath: null,
    url: null,
    tldr: null,
    contribution: null,
    citations: null,
    createdAt: null,
    source: null,
    arxivId: null,
    doi: null,
    s2Id: null,
    openalexId: null,
    relevance: null,
    order: null,
    ccf: null,
    status: '未开始',
    hasNote: false,
    favorite: false,
    hasPdf: false,
    ...overrides,
  };
}

describe('applyLibraryFilters', () => {
  it('searches English and Chinese titles plus venue, type, and topic', () => {
    const papers = [
      paper('one', {
        title: 'Interfaces for Collective Sensemaking',
        titleZh: '协作式意义建构界面',
        venue: 'CSCW',
        type: 'Research',
        topic: 'Knowledge work',
      }),
      paper('two', {
        title: 'Visual Foundation Models',
        titleZh: '视觉基础模型',
        venue: 'CVPR',
        type: 'Survey',
        topic: 'Computer vision',
      }),
    ];

    for (const query of ['collective', '协作式', 'cscw', 'research', 'knowledge']) {
      expect(applyLibraryFilters(papers, {
        ...defaultLibraryFilters,
        query,
      }).map(({ id }) => id)).toEqual(['one']);
    }
  });

  it('combines venue, type, topic, status, favorite, year, and source filters', () => {
    const papers = [
      paper('match', {
        venue: 'CSCW',
        type: 'Research',
        topic: 'Knowledge work',
        status: '学习中',
        favorite: true,
        year: '2026',
        source: 'seed',
      }),
      paper('wrong-status', {
        venue: 'CSCW',
        type: 'Research',
        topic: 'Knowledge work',
        status: '已理解',
        favorite: true,
        year: '2026',
        source: 'seed',
      }),
      paper('collected', {
        venue: 'CSCW',
        type: 'Research',
        topic: 'Knowledge work',
        status: '学习中',
        favorite: true,
        year: '2026',
        source: 'semanticscholar',
      }),
    ];

    expect(applyLibraryFilters(papers, {
      ...defaultLibraryFilters,
      venue: 'CSCW',
      type: 'Research',
      topic: 'Knowledge work',
      status: '学习中',
      favorite: true,
      year: '2026',
      source: 'seed',
    }).map(({ id }) => id)).toEqual(['match']);

    expect(applyLibraryFilters(papers, {
      ...defaultLibraryFilters,
      source: 'collected',
    }).map(({ id }) => id)).toEqual(['collected']);
  });

  it('implements added, relevance, year, citations, and title sort modes', () => {
    const papers = [
      paper('one', {
        title: 'Gamma',
        createdAt: '2024-01-01T00:00:00.000Z',
        relevance: 0.9,
        year: '2020',
        citations: 5,
      }),
      paper('two', {
        title: 'Alpha',
        createdAt: '2026-01-01T00:00:00.000Z',
        relevance: 0.2,
        year: '2022',
        citations: 100,
      }),
      paper('three', {
        title: 'Beta',
        createdAt: '2025-01-01T00:00:00.000Z',
        relevance: 0.5,
        year: '2024',
        citations: 20,
      }),
    ];
    const expected: Record<string, string[]> = {
      added: ['two', 'three', 'one'],
      relevance: ['one', 'three', 'two'],
      year: ['three', 'two', 'one'],
      citations: ['two', 'three', 'one'],
      title: ['two', 'three', 'one'],
    };

    for (const sort of ['added', 'relevance', 'year', 'citations', 'title'] as const) {
      expect(applyLibraryFilters(papers, {
        ...defaultLibraryFilters,
        sort,
      }).map(({ id }) => id)).toEqual(expected[sort]);
    }
  });

  it('limits semantic results to scored papers and orders them by score', () => {
    const papers = [paper('one'), paper('two'), paper('three')];

    expect(applyLibraryFilters(papers, {
      ...defaultLibraryFilters,
      query: 'words do not need to occur in a semantic result',
      semanticScores: new Map([
        ['one', 0.61],
        ['two', 0.94],
      ]),
    }).map(({ id }) => id)).toEqual(['two', 'one']);
  });
});

describe('reconcileLibrarySelection', () => {
  it('preserves a visible id, otherwise selects the first result or null', () => {
    const papers = [paper('one'), paper('two')];

    expect(reconcileLibrarySelection(papers, 'two')).toBe('two');
    expect(reconcileLibrarySelection(papers, 'missing')).toBe('one');
    expect(reconcileLibrarySelection([], 'one')).toBeNull();
  });
});
