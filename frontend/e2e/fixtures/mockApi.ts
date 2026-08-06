import { Buffer } from 'node:buffer';

import {
  expect,
  test as base,
  type Page,
  type Route,
} from '@playwright/test';

type PaperMode = 'empty' | 'one' | 'many';
type JobMode = 'empty' | 'active' | 'review';

export interface MockApiRequest {
  readonly method: string;
  readonly pathname: string;
  readonly search: string;
  readonly body: unknown;
}

export interface MockStreamOverride {
  readonly frames: readonly Record<string, unknown>[];
  readonly delayMs?: number;
}

export interface MockFailure {
  readonly status: number;
  readonly body: unknown;
}

export interface MockApiController {
  readonly requests: MockApiRequest[];
  readonly unhandled: MockApiRequest[];
  usePapers(mode: PaperMode): void;
  useJobs(mode: JobMode): void;
  failNext(pathname: string, failure?: MockFailure): void;
  streamNext(pathname: string, override: MockStreamOverride): void;
  requestCount(pathname: string, method?: string): number;
  lastRequest(pathname: string, method?: string): MockApiRequest | undefined;
}

interface WirePaper {
  id: string;
  file: string;
  title: string;
  title_zh: string | null;
  venue: string | null;
  year: string | null;
  type: string | null;
  topic: string | null;
  pdf_url: string | null;
  pdf_path: string | null;
  url: string | null;
  tldr: string | null;
  contribution: string | null;
  citations: number | null;
  created_at: string | null;
  source: string | null;
  arxiv_id: string | null;
  doi: string | null;
  s2_id: string | null;
  openalex_id: string | null;
  relevance: number | null;
  order: number | null;
  ccf: string | null;
  status: '未开始' | '学习中' | '已理解';
  hasNote: boolean;
  favorite: boolean;
  hasPdf: boolean;
}

interface MockState {
  paperMode: PaperMode;
  jobMode: JobMode;
  papers: WirePaper[];
  jobs: Record<string, unknown>[];
  schedules: Record<string, unknown>[];
  notes: Map<string, string>;
  failures: Map<string, MockFailure[]>;
  streams: Map<string, MockStreamOverride[]>;
  unhandled: MockApiRequest[];
  nextPaperId: number;
  nextJobId: number;
  nextScheduleId: number;
}

const paperFixtures: readonly WirePaper[] = [
  {
    id: 'paper-lifecycle',
    file: 'paper-lifecycle.pdf',
    title: 'Lifecycle-Safe Research Readers',
    title_zh: '生命周期安全的研究阅读器',
    venue: 'CHI',
    year: '2026',
    type: 'Systems',
    topic: 'Research tooling',
    pdf_url: 'https://example.test/paper-lifecycle.pdf',
    pdf_path: 'data/pdfs/paper-lifecycle.pdf',
    url: 'https://example.test/paper-lifecycle',
    tldr: 'A deterministic lifecycle model keeps readers responsive under rapid navigation.',
    contribution: 'Defines one owner for every asynchronous reading resource.',
    citations: 42,
    created_at: '2026-08-06T08:00:00.000Z',
    source: 'semanticscholar',
    arxiv_id: '2608.00001',
    doi: '10.1000/lifecycle',
    s2_id: 'S2-LIFECYCLE',
    openalex_id: 'W-LIFECYCLE',
    relevance: 0.97,
    order: 1,
    ccf: 'A',
    status: '学习中',
    hasNote: true,
    favorite: true,
    hasPdf: true,
  },
  {
    id: 'paper-workers',
    file: 'paper-workers.pdf',
    title: 'Bounded Workers for Scholarly Documents',
    title_zh: '面向学术文档的有界 Worker',
    venue: 'CSCW',
    year: '2025',
    type: 'Research',
    topic: 'Concurrency',
    pdf_url: 'https://example.test/paper-workers.pdf',
    pdf_path: 'data/pdfs/paper-workers.pdf',
    url: 'https://example.test/paper-workers',
    tldr: 'Generation gates prevent late worker messages from corrupting the active paper.',
    contribution: 'A protocol for timeout, termination, and late-message rejection.',
    citations: 17,
    created_at: '2026-08-05T08:00:00.000Z',
    source: 'arxiv',
    arxiv_id: '2508.00002',
    doi: null,
    s2_id: 'S2-WORKERS',
    openalex_id: null,
    relevance: 0.92,
    order: 2,
    ccf: 'B',
    status: '未开始',
    hasNote: false,
    favorite: false,
    hasPdf: true,
  },
  {
    id: 'paper-evidence',
    file: 'paper-evidence.pdf',
    title: 'Evidence-First Interfaces for Literature Review',
    title_zh: '证据优先的文献综述界面',
    venue: 'UIST',
    year: '2024',
    type: 'Methods',
    topic: 'Evidence',
    pdf_url: null,
    pdf_path: null,
    url: 'https://example.test/paper-evidence',
    tldr: 'Research interfaces should expose authoritative facts and explicit uncertainty.',
    contribution: 'Separates server facts from presentation state.',
    citations: 63,
    created_at: '2026-08-04T08:00:00.000Z',
    source: 'openalex',
    arxiv_id: null,
    doi: '10.1000/evidence',
    s2_id: null,
    openalex_id: 'W-EVIDENCE',
    relevance: 0.88,
    order: 3,
    ccf: 'A',
    status: '已理解',
    hasNote: true,
    favorite: false,
    hasPdf: false,
  },
  {
    id: 'paper-reviews',
    file: 'paper-reviews.pdf',
    title: 'Spaced Review in Personal Research Archives',
    title_zh: '个人研究档案中的间隔复习',
    venue: 'IUI',
    year: '2023',
    type: 'Study',
    topic: 'Memory',
    pdf_url: 'https://example.test/paper-reviews.pdf',
    pdf_path: 'data/pdfs/paper-reviews.pdf',
    url: 'https://example.test/paper-reviews',
    tldr: 'Authority-preserving review queues make the next action unambiguous.',
    contribution: 'Connects reading progress with an explicit seven-step schedule.',
    citations: 28,
    created_at: '2026-08-03T08:00:00.000Z',
    source: 'dblp',
    arxiv_id: null,
    doi: '10.1000/reviews',
    s2_id: 'S2-REVIEWS',
    openalex_id: null,
    relevance: 0.81,
    order: 4,
    ccf: 'A',
    status: '已理解',
    hasNote: false,
    favorite: true,
    hasPdf: true,
  },
  {
    id: 'paper-graphs',
    file: 'paper-graphs.pdf',
    title: 'Citation Graphs as Research Memory',
    title_zh: '作为研究记忆的引用图',
    venue: 'VIS',
    year: '2022',
    type: 'Visualization',
    topic: 'Citation graphs',
    pdf_url: 'https://example.test/paper-graphs.pdf',
    pdf_path: 'data/pdfs/paper-graphs.pdf',
    url: 'https://example.test/paper-graphs',
    tldr: 'Compact citation maps reveal clusters without inventing missing evidence.',
    contribution: 'Pairs a chart with an equivalent keyboard-accessible node list.',
    citations: 104,
    created_at: '2026-08-02T08:00:00.000Z',
    source: 'semanticscholar',
    arxiv_id: '2208.00005',
    doi: '10.1000/graphs',
    s2_id: 'S2-GRAPHS',
    openalex_id: 'W-GRAPHS',
    relevance: 0.78,
    order: 5,
    ccf: 'B',
    status: '学习中',
    hasNote: true,
    favorite: false,
    hasPdf: true,
  },
];

const candidateFixtures = [
  {
    source: 'semanticscholar',
    source_id: 'candidate-001',
    title: 'Deterministic Async Ownership in React',
    authors: ['Ada Lin', 'Rui Chen'],
    venue: 'CHI',
    year: '2026',
    abstract: 'A study of lifecycle ownership in research interfaces.',
    tldr: 'One live owner per asynchronous resource.',
    fields: ['Human-computer interaction'],
    citations: 12,
    url: 'https://example.test/candidate-001',
    pdf_url: 'https://example.test/candidate-001.pdf',
    arxiv_id: '2608.01001',
    doi: null,
    s2_id: 'S2-CANDIDATE-001',
    ccf: 'A',
    type: 'Research',
    topic: 'Lifecycle',
    task: 'Reader safety',
    models: [],
    datasets: [],
    contribution: 'A deterministic owner protocol.',
    llm_tldr: null,
    tags: ['react', 'lifecycle'],
    relevance: 0.95,
    in_library: false,
    _cid: 11,
  },
  {
    source: 'arxiv',
    source_id: 'candidate-002',
    title: 'Clean-Room Research Workspaces',
    authors: ['Mina Zhou'],
    venue: 'arXiv',
    year: '2025',
    abstract: 'A clean-room approach to research software interfaces.',
    tldr: 'Separate contract discovery from visual implementation.',
    fields: ['Software engineering'],
    citations: 4,
    url: 'https://example.test/candidate-002',
    pdf_url: 'https://example.test/candidate-002.pdf',
    arxiv_id: '2508.01002',
    doi: null,
    s2_id: null,
    ccf: null,
    type: 'Position',
    topic: 'Clean room',
    task: null,
    models: [],
    datasets: [],
    contribution: 'A clean separation between legacy behavior and new presentation.',
    llm_tldr: null,
    tags: ['clean-room'],
    relevance: 0.86,
    in_library: true,
    _cid: 12,
  },
] as const;

function clonePapers(mode: PaperMode): WirePaper[] {
  const source = mode === 'empty'
    ? []
    : mode === 'one'
      ? paperFixtures.slice(0, 1)
      : paperFixtures;
  return source.map((paper) => ({ ...paper }));
}

function jobFixtures(mode: JobMode): Record<string, unknown>[] {
  if (mode === 'empty') return [];
  if (mode === 'review') {
    return [{
      id: 2,
      query: 'lifecycle safe readers',
      venues: 'semanticscholar,arxiv',
      year_from: 2024,
      year_to: 2026,
      max_papers: 12,
      min_relevance: 0.55,
      only_a: false,
      schedule_id: null,
      status: 'review',
      found: 2,
      added: 0,
      skipped: 0,
      pending: 2,
      created_at: '2026-08-06T08:30:00.000Z',
      finished_at: null,
    }];
  }
  return [{
    id: 1,
    query: 'evidence first interfaces',
    venues: 'dblp,openalex',
    year_from: 2023,
    year_to: 2026,
    max_papers: 10,
    min_relevance: 0.5,
    only_a: false,
    schedule_id: null,
    status: 'running',
    found: 3,
    added: 0,
    skipped: 0,
    pending: 3,
    created_at: '2026-08-06T08:45:00.000Z',
    finished_at: null,
  }];
}

function scheduleFixtures(): Record<string, unknown>[] {
  return [{
    id: 7,
    query: 'research reader lifecycle',
    sources: 'semanticscholar,arxiv',
    years: '2024-2026',
    max_papers: 10,
    min_relevance: 0.5,
    only_a: false,
    every_days: 7,
    enabled: false,
    last_run: '2026-08-01T08:00:00.000Z',
    next_run: '2026-08-08T08:00:00.000Z',
    created_at: '2026-07-25T08:00:00.000Z',
  }];
}

function paperDetail(paper: WirePaper): Record<string, unknown> {
  return {
    id: paper.id,
    source: paper.source ?? 'seed',
    source_id: paper.s2_id ?? paper.arxiv_id,
    arxiv_id: paper.arxiv_id,
    doi: paper.doi,
    s2_id: paper.s2_id,
    openalex_id: paper.openalex_id,
    title: paper.title,
    title_zh: paper.title_zh,
    title_norm: paper.title.toLocaleLowerCase(),
    authors: ['Ada Lin', 'Rui Chen'],
    venue: paper.venue,
    year: paper.year,
    abstract: `${paper.tldr ?? ''} The fixture is deterministic and never reads the user database.`,
    tldr: paper.tldr,
    citations: paper.citations,
    s2_fields: ['Human-computer interaction'],
    url: paper.url,
    pdf_url: paper.pdf_url,
    pdf_path: paper.pdf_path,
    type: paper.type,
    topic: paper.topic,
    task: 'Literature synthesis',
    models: [],
    datasets: [],
    contribution: paper.contribution,
    tags: ['research-workspace', 'evidence'],
    relevance: paper.relevance,
    explainer: null,
    extracted_by: 'playwright-fixture',
    order_no: paper.order,
    created_at: paper.created_at,
    updated_at: '2026-08-06T09:00:00.000Z',
  };
}

function reviewItem(
  paper: WirePaper,
  reviewState: 'overdue' | 'dueToday' | 'upcoming' | 'completed',
  currentStep: number,
): Record<string, unknown> {
  const completed = reviewState === 'completed';
  return {
    paper_id: paper.id,
    started_at: '2026-07-20',
    current_step: currentStep,
    completed_steps: completed ? 7 : Math.max(0, currentStep - 1),
    next_due_at: reviewState === 'overdue' ? '2026-08-05' : '2026-08-06',
    completed_at: completed ? '2026-08-05' : null,
    updated_at: '2026-08-06',
    title: paper.title,
    title_zh: paper.title_zh,
    venue: paper.venue,
    year: paper.year,
    status: paper.status,
    review_state: reviewState,
    total_steps: 7,
  };
}

function reviewSnapshot(papers: readonly WirePaper[]): Record<string, unknown> {
  const overdue = papers[0] ? [reviewItem(papers[0], 'overdue', 2)] : [];
  const dueToday = papers[1] ? [reviewItem(papers[1], 'dueToday', 3)] : [];
  const upcoming = papers[2] ? [reviewItem(papers[2], 'upcoming', 4)] : [];
  const completed = papers[3] ? [reviewItem(papers[3], 'completed', 7)] : [];
  return {
    ok: true,
    today: '2026-08-06',
    counts: {
      overdue: overdue.length,
      dueToday: dueToday.length,
      upcoming: upcoming.length,
      completed: completed.length,
    },
    overdue,
    dueToday,
    upcoming,
    completed,
  };
}

function settingsFixture(): Record<string, unknown> {
  return {
    provider: 'deepseek',
    baseUrl: 'https://api.example.test/v1',
    model: 'research-model',
    apiKeyTail: '••••1234',
    hasApiKey: true,
    s2KeyTail: '••••5678',
    hasS2Key: true,
    pdfDir: 'data/pdfs',
    explainerDir: 'data/explainers',
    translationDir: 'data/translations',
    defaultPdfDir: 'data/pdfs',
    defaultExplainerDir: 'data/explainers',
    defaultTranslationDir: 'data/translations',
    resolvedPdfDir: 'F:/fixture/pdfs',
    resolvedExplainerDir: 'F:/fixture/explainers',
    resolvedTranslationDir: 'F:/fixture/translations',
    researchTheme: 'Lifecycle-safe research workspaces',
    embedProvider: 'openai-compatible',
    embedApiBase: 'https://embed.example.test/v1',
    embedApiModel: 'fixture-embedding',
    embedKeyTail: '••••9012',
    hasEmbedKey: true,
  };
}

function citationGraph(papers: readonly WirePaper[]): Record<string, unknown> {
  const nodes = papers.slice(0, 4).map((paper, index) => ({
    id: paper.id,
    title: paper.title,
    venue: paper.venue,
    year: paper.year,
    type: paper.type,
    topic: paper.topic,
    citations: paper.citations,
    indeg: index === 0 ? 2 : 1,
    outdeg: index < 3 ? 1 : 0,
  }));
  const links = nodes.slice(1).map((node, index) => ({
    source: nodes[index]?.id ?? nodes[0]?.id,
    target: node.id,
  }));
  return { nodes, links, edgeCount: links.length };
}

function buildPdfFixture(): Buffer {
  const content = [
    'BT',
    '/F1 20 Tf',
    '72 720 Td',
    '(Paper Study deterministic PDF fixture) Tj',
    'ET',
  ].join('\n');
  const objects = [
    '<< /Type /Catalog /Pages 2 0 R >>',
    '<< /Type /Pages /Kids [3 0 R] /Count 1 >>',
    '<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>',
    `<< /Length ${Buffer.byteLength(content)} >>\nstream\n${content}\nendstream`,
    '<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>',
  ];
  let source = '%PDF-1.4\n%fixture\n';
  const offsets = [0];
  for (const [index, object] of objects.entries()) {
    offsets.push(Buffer.byteLength(source));
    source += `${index + 1} 0 obj\n${object}\nendobj\n`;
  }
  const xrefOffset = Buffer.byteLength(source);
  source += `xref\n0 ${objects.length + 1}\n`;
  source += '0000000000 65535 f \n';
  source += offsets.slice(1)
    .map((offset) => `${String(offset).padStart(10, '0')} 00000 n \n`)
    .join('');
  source += `trailer\n<< /Size ${objects.length + 1} /Root 1 0 R >>\n`;
  source += `startxref\n${xrefOffset}\n%%EOF\n`;
  return Buffer.from(source, 'ascii');
}

const pdfFixture = buildPdfFixture();

function readBody(route: Route): unknown {
  const raw = route.request().postData();
  if (!raw) return null;
  try {
    return JSON.parse(raw) as unknown;
  } catch {
    return raw;
  }
}

function bodyRecord(body: unknown): Record<string, unknown> {
  return typeof body === 'object' && body !== null && !Array.isArray(body)
    ? body as Record<string, unknown>
    : {};
}

function idFrom(url: URL, body: unknown): string {
  const record = bodyRecord(body);
  return String(url.searchParams.get('id') ?? record.id ?? '');
}

function ndjson(frames: readonly Record<string, unknown>[]): string {
  return `${frames.map((frame) => JSON.stringify(frame)).join('\n')}\n`;
}

async function fulfillJson(route: Route, value: unknown, status = 200): Promise<void> {
  await route.fulfill({
    status,
    contentType: 'application/json; charset=utf-8',
    json: value,
  });
}

async function fulfillText(
  route: Route,
  value: string,
  contentType = 'text/plain; charset=utf-8',
): Promise<void> {
  await route.fulfill({ status: 200, contentType, body: value });
}

function streamDefaults(pathname: string): readonly Record<string, unknown>[] {
  switch (pathname) {
    case '/api/search':
      return [
        { type: 'progress', line: 'STAGE::search' },
        { type: 'progress', line: 'SOURCE::semanticscholar::2' },
        { type: 'result', ok: true, candidates: candidateFixtures },
      ];
    case '/api/verify-venue':
      return [
        { type: 'progress', line: 'VERIFY::dblp' },
        {
          type: 'result',
          ok: true,
          verifications: candidateFixtures.map((candidate, index) => ({
            venue: candidate.venue,
            year: candidate.year,
            matched: index === 0,
            skipped: index !== 0,
            source_of_truth: index === 0 ? 'dblp' : 'arxiv',
            changed: false,
            orig_venue: candidate.venue,
            ccf: candidate.ccf,
            note: index === 0 ? 'venue confirmed' : 'preprint only',
            error: false,
          })),
        },
      ];
    case '/api/ingest-selected':
    case '/api/jobs/confirm':
      return [
        { type: 'progress', line: 'INGEST::candidate-001' },
        { type: 'done', ok: true, added: 1 },
      ];
    case '/api/explain':
      return [
        { type: 'progress', line: 'EXPLAIN::outline' },
        { type: 'result', ok: true, markdown: '# 研究讲解\n\n生命周期 owner 阻止迟到结果污染当前论文。' },
      ];
    case '/api/title-translations':
      return [
        { type: 'progress', stage: 'batch', total: 1 },
        {
          type: 'progress', stage: 'item', state: 'done', index: 1, total: 1,
          id: 'paper-workers', title: 'Bounded Workers for Scholarly Documents',
          title_zh: '面向学术文档的有界 Worker',
        },
        { type: 'result', ok: true, summary: { total: 1, done: 1, failed: [], cancelled: false } },
      ];
    case '/api/explain-batch':
      return [
        { type: 'progress', line: 'EXPLAIN::paper-workers' },
        {
          type: 'result', ok: true,
          summary: { total: 2, done: 1, failed: [], skipped_no_pdf: ['paper-evidence'] },
        },
      ];
    case '/api/recommend':
      return [
        { type: 'progress', line: 'RECOMMEND::citation-neighbors' },
        { type: 'result', ok: true, candidates: candidateFixtures },
      ];
    case '/api/embed':
      return [
        { type: 'progress', line: 'EMBED::paper-lifecycle' },
        { type: 'result', ok: true, indexed: 4, total: 5 },
      ];
    case '/api/translate':
      return [
        { type: 'progress', line: 'TRANSLATE::page-1' },
        { type: 'result', ok: true, markdown: '# 全文翻译\n\n这是确定性的测试译文。' },
      ];
    case '/api/semsearch':
      return [
        { type: 'progress', line: 'EMBED::query' },
        { type: 'result', ok: true, results: [
          { id: 'paper-workers', score: 0.94 },
          { id: 'paper-lifecycle', score: 0.82 },
        ] },
      ];
    case '/api/import-pdfs':
      return [
        { type: 'progress', line: 'TOTAL::2' },
        { type: 'progress', line: 'PARSED::1::2::Fixture One' },
        { type: 'progress', line: 'PARSED::2::2::Fixture Two' },
        { type: 'result', ok: true, added: 1, dup: 1, failed: 0, total: 2 },
      ];
    case '/api/download-pdfs':
      return [
        { type: 'progress', line: 'DOWNLOAD::paper-evidence' },
        { type: 'result', ok: true, downloaded: 2, skipped: 1, failed: 0, total: 3 },
      ];
    case '/api/cite-build':
      return [
        { type: 'progress', line: '[BUILD] citation edges' },
        { type: 'result', ok: true, edges: 3, nodes: 4 },
      ];
    case '/api/norm-venues':
      return [
        { type: 'progress', line: '[NORMALIZE] venues' },
        { type: 'result', ok: true, changed: 1, mapping: { 'CHI Conference': 'CHI' } },
      ];
    default:
      return [
        { type: 'progress', line: 'FIXTURE::progress' },
        { type: 'result', ok: true },
      ];
  }
}

function detailJob(state: MockState, id: number): Record<string, unknown> | null {
  const summary = state.jobs.find((job) => Number(job.id) === id);
  if (!summary) return null;
  return {
    ok: true,
    job: {
      ...summary,
      log: 'Fixture job reached candidate review.',
      queries: ['lifecycle safe readers', 'bounded PDF workers'],
    },
    candidates: candidateFixtures,
  };
}

function popMapValue<T>(map: Map<string, T[]>, key: string): T | undefined {
  const queue = map.get(key);
  const value = queue?.shift();
  if (queue?.length === 0) map.delete(key);
  return value;
}

async function handleBackend(
  route: Route,
  request: MockApiRequest,
  url: URL,
  state: MockState,
): Promise<void> {
  const failure = popMapValue(state.failures, request.pathname);
  if (failure) {
    await fulfillJson(route, failure.body, failure.status);
    return;
  }

  const { method, pathname, body } = request;
  const record = bodyRecord(body);

  if (pathname === '/pdfbytes' || pathname.startsWith('/papers/')) {
    await route.fulfill({
      status: 200,
      contentType: 'application/pdf',
      headers: { 'content-length': String(pdfFixture.byteLength) },
      body: pdfFixture,
    });
    return;
  }

  if (method === 'GET' && pathname === '/api/papers') {
    await fulfillJson(route, state.papers);
    return;
  }
  if (method === 'GET' && pathname === '/api/paper/get') {
    const paper = state.papers.find((item) => item.id === idFrom(url, body));
    await fulfillJson(route, paper ? paperDetail(paper) : null);
    return;
  }
  if (method === 'GET' && pathname === '/api/reviews') {
    await fulfillJson(route, reviewSnapshot(state.papers));
    return;
  }
  if (method === 'GET' && pathname === '/api/title-translations') {
    await fulfillJson(route, { ok: true, pending: 1, running: false });
    return;
  }
  if (method === 'GET' && pathname === '/api/explain-batch') {
    await fulfillJson(route, { pending: 1, withPdf: 1, noPdf: 1 });
    return;
  }
  if (method === 'POST' && pathname === '/api/reviews/start') {
    const paper = state.papers.find((item) => item.id === idFrom(url, body)) ?? state.papers[0];
    await fulfillJson(route, {
      ok: true,
      plan: paper ? reviewItem(paper, 'upcoming', 1) : reviewItem(paperFixtures[0], 'upcoming', 1),
    });
    return;
  }
  if (method === 'POST' && pathname === '/api/reviews/complete') {
    const paper = state.papers.find((item) => item.id === idFrom(url, body)) ?? state.papers[0] ?? paperFixtures[0];
    await fulfillJson(route, {
      ok: true,
      plan: reviewItem(paper, 'upcoming', 3),
      reviews: reviewSnapshot(state.papers),
    });
    return;
  }
  if (pathname === '/api/note') {
    const id = idFrom(url, body);
    if (method === 'GET') {
      await fulfillText(route, state.notes.get(id) ?? '');
    } else {
      state.notes.set(id, String(record.content ?? ''));
      await fulfillJson(route, { ok: true });
    }
    return;
  }
  if (method === 'GET' && pathname === '/api/explainer') {
    await fulfillText(route, '# 研究讲解\n\n- 资源 owner 与 URL 论文绑定。\n- 迟到结果会被 generation 闸门丢弃。\n\n$E = mc^2$');
    return;
  }
  if (method === 'GET' && pathname === '/api/translation') {
    await fulfillText(route, '# 全文翻译\n\n阅读器中的译文来自服务端产物。');
    return;
  }
  if (method === 'GET' && pathname === '/api/pdf/status') {
    const id = idFrom(url, body);
    const paper = state.papers.find((item) => item.id === id);
    await fulfillJson(route, {
      ok: true,
      id,
      hasPdf: Boolean(paper?.hasPdf),
      size: paper?.hasPdf ? pdfFixture.byteLength : 0,
      path: paper?.pdf_path ?? '',
      canDownload: !paper?.hasPdf && Boolean(paper?.pdf_url),
    });
    return;
  }
  if (method === 'POST' && pathname === '/api/progress') {
    const paper = state.papers.find((item) => item.id === String(record.id ?? ''));
    if (paper) paper.status = String(record.status) as WirePaper['status'];
    await fulfillJson(route, { ok: true });
    return;
  }
  if (method === 'POST' && pathname === '/api/favorite') {
    const paper = state.papers.find((item) => item.id === String(record.id ?? ''));
    if (paper) paper.favorite = Boolean(record.favorite);
    await fulfillJson(route, { ok: true });
    return;
  }
  if (method === 'POST' && pathname === '/api/delete') {
    state.papers = state.papers.filter((item) => item.id !== String(record.id ?? ''));
    await fulfillJson(route, { ok: true });
    return;
  }
  if (method === 'POST' && pathname === '/api/paper/add') {
    const id = `manual-${state.nextPaperId++}`;
    const created: WirePaper = {
      ...paperFixtures[0],
      id,
      file: `${id}.pdf`,
      title: String(record.title ?? 'Untitled fixture paper'),
      title_zh: typeof record.title_zh === 'string' ? record.title_zh : null,
      venue: typeof record.venue === 'string' ? record.venue : null,
      year: typeof record.year === 'string' ? record.year : null,
      source: 'manual',
      created_at: '2026-08-06T10:00:00.000Z',
      hasPdf: false,
      hasNote: false,
      favorite: false,
      status: '未开始',
    };
    state.papers = [...state.papers, created];
    await fulfillJson(route, { ok: true, id });
    return;
  }
  if (method === 'POST' && pathname === '/api/paper/update') {
    const paper = state.papers.find((item) => item.id === String(record.id ?? ''));
    if (paper) {
      if (typeof record.title === 'string') paper.title = record.title;
      if (typeof record.title_zh === 'string' || record.title_zh === null) paper.title_zh = record.title_zh;
      if (typeof record.venue === 'string' || record.venue === null) paper.venue = record.venue;
      if (typeof record.year === 'string' || record.year === null) paper.year = record.year;
    }
    await fulfillJson(route, { ok: true, changes: paper ? 1 : 0 });
    return;
  }
  if (method === 'POST' && pathname === '/api/translate-text') {
    await fulfillJson(route, { ok: true, text: `译文：${String(record.text ?? '')}` });
    return;
  }
  if (method === 'POST' && pathname === '/api/expand') {
    const query = String(record.query ?? 'research workspace');
    await fulfillJson(route, { ok: true, queries: [query, `${query} lifecycle`, `${query} evidence`] });
    return;
  }
  if (method === 'POST' && pathname === '/api/ingest') {
    await fulfillJson(route, { ok: true, output: 'fixture ingest completed', code: 0 });
    return;
  }
  if (method === 'GET' && pathname === '/api/scan-pdfs') {
    const directory = url.searchParams.get('dir') ?? 'F:/fixture/papers';
    await fulfillJson(route, {
      ok: true,
      dir: directory,
      count: 2,
      files: [
        { path: `${directory}/fixture-one.pdf`, name: 'fixture-one.pdf', size: 4096 },
        { path: `${directory}/fixture-two.pdf`, name: 'fixture-two.pdf', size: 8192 },
      ],
    });
    return;
  }
  if (method === 'GET' && pathname === '/api/citegraph') {
    await fulfillJson(route, citationGraph(state.papers));
    return;
  }
  if (method === 'GET' && pathname === '/api/settings') {
    await fulfillJson(route, settingsFixture());
    return;
  }
  if (method === 'POST' && pathname === '/api/settings') {
    await fulfillJson(route, { ok: true });
    return;
  }
  if (method === 'POST' && pathname === '/api/test-llm') {
    await fulfillJson(route, { ok: true, output: 'fixture model connection verified' });
    return;
  }
  if (method === 'GET' && pathname === '/api/jobs') {
    await fulfillJson(route, state.jobs);
    return;
  }
  if (method === 'POST' && pathname === '/api/jobs') {
    const id = state.nextJobId++;
    state.jobs = [...state.jobs, {
      id,
      query: String(record.query ?? ''),
      venues: Array.isArray(record.sources) ? record.sources.join(',') : 'semanticscholar',
      year_from: 2024,
      year_to: 2026,
      max_papers: Number(record.max ?? 10),
      min_relevance: Number(record.minRelevance ?? 0),
      only_a: Boolean(record.onlyA),
      schedule_id: null,
      status: 'pending',
      found: 0,
      added: 0,
      skipped: 0,
      pending: 0,
      created_at: '2026-08-06T10:15:00.000Z',
      finished_at: null,
    }];
    await fulfillJson(route, { ok: true, id });
    return;
  }
  if (method === 'GET' && pathname === '/api/jobs/detail') {
    const id = Number(url.searchParams.get('id'));
    const detail = detailJob(state, id);
    await fulfillJson(route, detail ?? { ok: false, error: '任务不存在' }, detail ? 200 : 404);
    return;
  }
  if (method === 'POST' && pathname === '/api/jobs/delete') {
    const id = Number(record.id);
    state.jobs = state.jobs.filter((job) => Number(job.id) !== id);
    await fulfillJson(route, { ok: true });
    return;
  }
  if (method === 'GET' && pathname === '/api/schedules') {
    await fulfillJson(route, state.schedules);
    return;
  }
  if (method === 'POST' && pathname === '/api/schedules') {
    const id = state.nextScheduleId++;
    state.schedules = [...state.schedules, {
      id,
      query: String(record.query ?? ''),
      sources: Array.isArray(record.sources) ? record.sources.join(',') : 'semanticscholar',
      years: String(record.years ?? ''),
      max_papers: Number(record.max ?? 10),
      min_relevance: Number(record.minRelevance ?? 0),
      only_a: Boolean(record.onlyA),
      every_days: Number(record.everyDays ?? 7),
      enabled: true,
      last_run: null,
      next_run: '2026-08-13T08:00:00.000Z',
      created_at: '2026-08-06T10:30:00.000Z',
    }];
    await fulfillJson(route, { ok: true, id });
    return;
  }
  if (method === 'POST' && pathname === '/api/schedules/toggle') {
    const schedule = state.schedules.find((item) => Number(item.id) === Number(record.id));
    if (schedule) schedule.enabled = Boolean(record.enabled);
    await fulfillJson(route, { ok: true });
    return;
  }
  if (method === 'POST' && pathname === '/api/schedules/delete') {
    state.schedules = state.schedules.filter((item) => Number(item.id) !== Number(record.id));
    await fulfillJson(route, { ok: true });
    return;
  }

  const streamPaths = new Set([
    '/api/search',
    '/api/title-translations',
    '/api/verify-venue',
    '/api/ingest-selected',
    '/api/explain',
    '/api/explain-batch',
    '/api/translate',
    '/api/recommend',
    '/api/embed',
    '/api/semsearch',
    '/api/import-pdfs',
    '/api/download-pdfs',
    '/api/jobs/confirm',
    '/api/cite-build',
    '/api/norm-venues',
  ]);
  if (method === 'POST' && streamPaths.has(pathname)) {
    const override = popMapValue(state.streams, pathname);
    if (override?.delayMs) {
      await new Promise((resolve) => setTimeout(resolve, override.delayMs));
    }
    await fulfillText(
      route,
      ndjson(override?.frames ?? streamDefaults(pathname)),
      'application/x-ndjson; charset=utf-8',
    );
    return;
  }

  state.unhandled.push(request);
  await fulfillJson(route, { ok: false, error: `Unhandled mock API request: ${method} ${pathname}` }, 599);
}

export async function installMockApi(page: Page): Promise<MockApiController> {
  const requests: MockApiRequest[] = [];
  const unhandled: MockApiRequest[] = [];
  const state: MockState = {
    paperMode: 'many',
    jobMode: 'active',
    papers: clonePapers('many'),
    jobs: jobFixtures('active'),
    schedules: scheduleFixtures(),
    notes: new Map([
      ['paper-lifecycle', '# 阅读笔记\n\n- 当前论文由 URL 决定。\n- 资源清理必须幂等。\n\n$E = mc^2$'],
      ['paper-evidence', '证据与推断应分开呈现。'],
    ]),
    failures: new Map(),
    streams: new Map(),
    unhandled,
    nextPaperId: 100,
    nextJobId: 20,
    nextScheduleId: 30,
  };

  await page.route('**/*', async (route) => {
    const requestObject = route.request();
    const url = new URL(requestObject.url());
    const isBackendBoundary = url.pathname.startsWith('/api/')
      || url.pathname === '/pdfbytes'
      || url.pathname.startsWith('/papers/');
    if (!isBackendBoundary) {
      await route.continue();
      return;
    }
    const request: MockApiRequest = {
      method: requestObject.method(),
      pathname: url.pathname,
      search: url.search,
      body: readBody(route),
    };
    requests.push(request);
    await handleBackend(route, request, url, state);
  });

  return {
    requests,
    unhandled,
    usePapers(mode) {
      state.paperMode = mode;
      state.papers = clonePapers(mode);
    },
    useJobs(mode) {
      state.jobMode = mode;
      state.jobs = jobFixtures(mode);
    },
    failNext(pathname, failure = { status: 500, body: { ok: false, error: 'fixture failure' } }) {
      state.failures.set(pathname, [...(state.failures.get(pathname) ?? []), failure]);
    },
    streamNext(pathname, override) {
      state.streams.set(pathname, [...(state.streams.get(pathname) ?? []), override]);
    },
    requestCount(pathname, method) {
      return requests.filter((request) => (
        request.pathname === pathname && (!method || request.method === method)
      )).length;
    },
    lastRequest(pathname, method) {
      for (let index = requests.length - 1; index >= 0; index -= 1) {
        const request = requests[index];
        if (
          request
          && request.pathname === pathname
          && (!method || request.method === method)
        ) {
          return request;
        }
      }
      return undefined;
    },
  };
}

export const test = base.extend<{ mockApi: MockApiController }>({
  mockApi: [async ({ page }, use) => {
    const mockApi = await installMockApi(page);
    await use(mockApi);
    expect(mockApi.unhandled, 'mock API must handle every backend request').toEqual([]);
  }, { auto: true }],
});

export { expect };
