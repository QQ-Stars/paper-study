import assert from 'node:assert/strict';
import test from 'node:test';

import { filterLibraryPapers, paginateLibraryPapers } from '../src/components/libraryQuery.ts';

const papers = [
  {
    id: 'old-favorite',
    title: 'Older Methods',
    title_zh: '旧方法',
    venue: 'NeurIPS',
    topic: '方法',
    status: '学习中',
    favorite: 1,
    created_at: '2025-01-01T00:00:00Z',
    year: '2024',
    citations: 20,
    relevance: 0.2,
  },
  {
    id: 'new-favorite',
    title: 'New Methods',
    title_zh: '新方法',
    venue: 'ICML',
    topic: '方法',
    status: '学习中',
    favorite: 1,
    created_at: '2025-03-01T00:00:00Z',
    year: '2025',
    citations: 4,
    relevance: 0.9,
  },
  {
    id: 'unfavorite',
    title: 'New Analysis',
    title_zh: '新分析',
    venue: 'ICML',
    topic: '分析',
    status: '未开始',
    favorite: 0,
    created_at: '2025-04-01T00:00:00Z',
    year: '2025',
    citations: 80,
    relevance: 0.8,
  },
];

test('favorite view composes keyword, status, and topic filters', () => {
  const result = filterLibraryPapers(papers, {
    mode: 'keyword',
    query: '方法',
    status: '学习中',
    topic: '方法',
    view: 'favorites',
  });

  assert.deepEqual(result.map((paper) => paper.id), ['new-favorite', 'old-favorite']);
});

test('favorite view always uses created_at descending order', () => {
  const result = filterLibraryPapers(papers, {
    mode: 'keyword',
    query: '',
    status: 'all',
    topic: 'all',
    view: 'favorites',
  });

  assert.deepEqual(result.map((paper) => paper.id), ['new-favorite', 'old-favorite']);
});

test('reading queue view composes with the existing filters without using favorite state', () => {
  const result = filterLibraryPapers(papers, {
    mode: 'keyword',
    query: '',
    status: 'all',
    topic: 'all',
    view: 'queue',
    readingQueueIds: ['unfavorite', 'old-favorite'],
  });

  assert.deepEqual(result.map((paper) => paper.id), ['unfavorite', 'old-favorite']);
});

test('pagination reports final result bounds and empty pages safely', () => {
  const secondPage = paginateLibraryPapers(papers, 2, 2);
  assert.deepEqual(secondPage, {
    papers: [papers[2]],
    page: 2,
    pageCount: 2,
    pageStart: 3,
    pageEnd: 3,
  });

  const empty = paginateLibraryPapers([], 4, 20);
  assert.deepEqual(empty, {
    papers: [],
    page: 1,
    pageCount: 1,
    pageStart: 0,
    pageEnd: 0,
  });
});
