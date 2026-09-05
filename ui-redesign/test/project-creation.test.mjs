import assert from 'node:assert/strict';
import test from 'node:test';

import {
  filterProjectPapers,
  findReproductionProjectForPaper,
} from '../src/components/projectCreation.ts';

const papers = [
  {
    id: 'paper-cn',
    title: 'Scaling Laws for Vision Models',
    title_zh: '视觉模型的缩放规律',
    venue: 'NeurIPS',
    year: '2024',
    type: 'survey',
    topic: '视觉',
    source: 'arxiv',
    status: '学习中',
    favorite: 0,
    relevance: 0.7,
    created_at: '2025-02-01T00:00:00Z',
  },
  {
    id: 'paper-en',
    title: 'Efficient Transformers for Long Context',
    title_zh: '长上下文高效 Transformer',
    venue: 'ICML',
    year: '2025',
    type: 'method',
    topic: '语言模型',
    source: 'semantic scholar',
    status: '已理解',
    favorite: 1,
    relevance: 0.5,
    created_at: '2025-03-01T00:00:00Z',
  },
  {
    id: 'vision-baseline-2024',
    title: 'A Practical Vision Baseline',
    title_zh: '视觉基线实践',
    venue: 'CVPR',
    year: '2024',
    type: 'benchmark',
    topic: '视觉',
    source: 'local',
    status: '未开始',
    favorite: 0,
    relevance: 0.95,
    created_at: '2025-04-01T00:00:00Z',
  },
];

test('empty search returns papers in the default relevance order', () => {
  const result = filterProjectPapers(papers, '');
  assert.deepEqual(result.items.map((paper) => paper.id), [
    'paper-en',
    'paper-cn',
    'vision-baseline-2024',
  ]);
  assert.equal(result.total, 3);
});

test('Chinese and English title queries match the corresponding paper', () => {
  assert.deepEqual(filterProjectPapers(papers, '视觉模型').items.map((paper) => paper.id), ['paper-cn']);
  assert.deepEqual(filterProjectPapers(papers, 'long context').items.map((paper) => paper.id), ['paper-en']);
});

test('exact id query outranks title and metadata matches', () => {
  const result = filterProjectPapers(papers, 'vision-baseline-2024');
  assert.equal(result.items[0].id, 'vision-baseline-2024');
  assert.equal(result.total, 1);
});

test('venue and year are searchable', () => {
  assert.deepEqual(filterProjectPapers(papers, 'ICML').items.map((paper) => paper.id), ['paper-en']);
  assert.deepEqual(filterProjectPapers(papers, '2024').items.map((paper) => paper.id), [
    'paper-cn',
    'vision-baseline-2024',
  ]);
});

test('multi-token queries require every token and can cross fields', () => {
  const result = filterProjectPapers(papers, '视觉 2024');
  assert.deepEqual(result.items.map((paper) => paper.id), ['paper-cn', 'vision-baseline-2024']);
  assert.equal(filterProjectPapers(papers, '视觉 2025').total, 0);
});

test('result limit caps visible items without hiding the total', () => {
  const result = filterProjectPapers(papers, '', 2);
  assert.equal(result.items.length, 2);
  assert.equal(result.total, 3);
});

test('no-result queries return an empty list and zero total', () => {
  assert.deepEqual(filterProjectPapers(papers, '不存在的论文'), { items: [], total: 0 });
});

test('favorite and relevance scores influence empty-search ranking', () => {
  const result = filterProjectPapers([papers[0], papers[1], papers[2]], '');
  assert.equal(result.items[0].favorite, 1);
  assert.equal(result.items[1].relevance, 0.7);
});

test('opening a paper reuses only its linked reproduction, never a linked article', () => {
  const projects = [
    { id: 'article-1', projectKind: 'article', paperId: 'paper-cn' },
    { id: 'reproduction-1', projectKind: 'reproduction', paperId: 'paper-cn' },
    { id: 'reproduction-2', projectKind: 'reproduction', paperId: 'paper-en' },
  ];

  assert.equal(
    findReproductionProjectForPaper(projects, 'paper-cn')?.id,
    'reproduction-1',
  );
  assert.equal(findReproductionProjectForPaper(projects, 'missing'), undefined);
  assert.equal(findReproductionProjectForPaper(projects, '   '), undefined);
});
