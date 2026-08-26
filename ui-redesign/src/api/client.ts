/* FastAPI 全量接口客户端：/api（legacy）+ /api/v2（durable）+ /health + PDF 服务。
 * NDJSON 流式接口统一通过 streamNdjson 消费（后端逐行推送事件，终态事件 type === 'done'）。 */

import type {
  BatchRun,
  Candidate,
  CiteGraph,
  DuplicatePair,
  EnrichStatus,
  LegacyJob,
  Paper,
  ReviewSnapshot,
  Schedule,
  Settings,
  StreamEvent,
  StudyStatus,
  V2JobDetail,
  V2JobEventsPage,
  V2JobSummary,
  V2RetryJobResult,
  ExperimentRun,
  ReproductionArtifact,
  ReproductionDocument,
  ReproductionListResponse,
  ReproductionNote,
  ReproductionProject,
  ReproductionResult,
} from './types';

async function json<T>(response: Response): Promise<T> {
  if (!response.ok) {
    let detail = '';
    try {
      detail = (await response.text()).slice(0, 300);
    } catch {
      /* ignore */
    }
    throw new Error(`HTTP ${response.status}${detail ? `: ${detail}` : ''}`);
  }
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

function get<T>(url: string): Promise<T> {
  return fetch(url).then((response) => json<T>(response));
}

async function text(url: string): Promise<string> {
  const response = await fetch(url);
  if (!response.ok) {
    let detail = '';
    try {
      detail = (await response.text()).slice(0, 300);
    } catch {
      /* ignore */
    }
    throw new Error(`HTTP ${response.status}${detail ? `: ${detail}` : ''}`);
  }
  return response.text();
}

function post<T>(url: string, body: unknown = {}): Promise<T> {
  return fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  }).then((response) => json<T>(response));
}

function mutate<T>(method: 'PATCH' | 'PUT' | 'DELETE', url: string, body?: unknown): Promise<T> {
  return fetch(url, {
    method,
    headers: body === undefined ? undefined : { 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
  }).then((response) => json<T>(response));
}

/* ── NDJSON 流式接口 ─────────────────────────────── */

export async function streamNdjson(
  url: string,
  body: unknown,
  onEvent: (event: StreamEvent) => void,
): Promise<void> {
  const response = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  if (!response.ok || !response.body) {
    let detail = '';
    try {
      detail = (await response.text()).slice(0, 300);
    } catch {
      /* ignore */
    }
    throw new Error(`HTTP ${response.status}${detail ? `: ${detail}` : ''}`);
  }
  const contentType = response.headers.get('content-type')?.toLowerCase() ?? '';
  if (!contentType.startsWith('application/x-ndjson')) {
    throw new Error(
      `流式任务 Content-Type 应为 application/x-ndjson，实际为 ${contentType || '缺失'}`,
    );
  }
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  let lineNumber = 0;
  let terminal: StreamEvent | undefined;
  const parseEvent = (line: string, number: number): StreamEvent => {
    let parsed: unknown;
    try {
      parsed = JSON.parse(line);
    } catch {
      throw new Error(`流式任务第 ${number} 行不是有效 JSON`);
    }
    if (
      parsed === null ||
      typeof parsed !== 'object' ||
      Array.isArray(parsed) ||
      typeof (parsed as { type?: unknown }).type !== 'string' ||
      !(parsed as { type: string }).type
    ) {
      throw new Error(`流式任务第 ${number} 行缺少有效事件类型`);
    }
    return parsed as StreamEvent;
  };
  const accept = (event: StreamEvent) => {
    if (terminal) {
      if (event.type === 'done' || event.type === 'result') {
        throw new Error('流式任务返回多个完成状态');
      }
      throw new Error('流式任务完成后仍收到事件');
    }
    if (event.type === 'done' || event.type === 'result') terminal = event;
    onEvent(event);
  };
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let newline = buffer.indexOf('\n');
    while (newline >= 0) {
      const line = buffer.slice(0, newline).trim();
      buffer = buffer.slice(newline + 1);
      lineNumber += 1;
      if (line) {
        accept(parseEvent(line, lineNumber));
      }
      newline = buffer.indexOf('\n');
    }
  }
  buffer += decoder.decode();
  const tail = buffer.trim();
  if (tail) {
    lineNumber += 1;
    accept(parseEvent(tail, lineNumber));
  }
  if (!terminal) throw new Error('流式任务未返回完成状态');
  if (typeof terminal.ok !== 'boolean') {
    throw new Error('流式任务完成状态缺少有效 ok 字段');
  }
  if (terminal.ok === false) {
    throw new Error(String(terminal.error ?? '任务执行失败'));
  }
}

/* ── 文献库（legacy CRUD） ───────────────────────── */

export const libraryApi = {
  listPapers: () => get<Paper[]>('/api/papers'),
  getPaper: (id: string) => get<Paper>(`/api/paper/get?id=${encodeURIComponent(id)}`),
  addPaper: (fields: Record<string, unknown>) =>
    post<{ ok: boolean; id?: string; error?: string }>('/api/paper/add', fields),
  updatePaper: (id: string, fields: Record<string, unknown>) =>
    post<{ ok: boolean; changes?: number; error?: string }>('/api/paper/update', {
      id,
      ...fields,
    }),
  setProgress: (id: string, status: StudyStatus) =>
    post<{ ok: boolean }>('/api/progress', { id, status }),
  setFavorite: (id: string, favorite: boolean) =>
    post<{ ok: boolean }>('/api/favorite', { id, favorite }),
  deletePaper: (id: string) => post<{ ok: boolean; error?: string }>('/api/delete', { id }),
  pdfStatus: (id: string) =>
    get<{ ok: boolean; hasPdf: boolean; size: number; path: string; canDownload: boolean }>(
      `/api/pdf/status?id=${encodeURIComponent(id)}`,
    ),
  pdfUrl: (id: string) => `/pdfbytes?id=${encodeURIComponent(id)}`,
  /* 库内引用上下文（cite_edges）：它引用了谁 / 谁引用了它。 */
  citeContext: (id: string) =>
    get<{
      ok: boolean;
      cites?: Array<{ id: string; title: string; titleZh: string; year: string; venue: string; tldr: string }>;
      citedBy?: Array<{ id: string; title: string; titleZh: string; year: string; venue: string; tldr: string }>;
      error?: string;
    }>(`/api/cite-context?id=${encodeURIComponent(id)}`),
  paperAuthors: (id: string) =>
    get<{ ok: boolean; id: string; authors: string[]; error?: string }>(
      `/api/paper-authors?id=${encodeURIComponent(id)}`,
    ),
};

/* ── 论文复现工作区（v2） ────────────────────────── */

export const reproductionApi = {
  list: (params: { q?: string; status?: string; tag?: string; sort?: 'updated' | 'created' | 'name'; page?: number; limit?: number } = {}) => {
    const query = new URLSearchParams();
    if (params.q) query.set('q', params.q);
    if (params.status) query.set('status', params.status);
    if (params.tag) query.set('tag', params.tag);
    if (params.sort) query.set('sort', params.sort);
    if (params.limit !== undefined) query.set('limit', String(params.limit));
    if (params.page !== undefined) {
      const limit = params.limit ?? 25;
      query.set('offset', String(Math.max(0, params.page - 1) * limit));
    }
    const suffix = query.toString() ? `?${query.toString()}` : '';
    return get<ReproductionListResponse>(`/api/v2/reproductions${suffix}`);
  },
  get: (id: string) => get<ReproductionProject>(`/api/v2/reproductions/${encodeURIComponent(id)}`),
  create: (body: { paperId: string; name: string; tags?: string[] }) =>
    post<ReproductionProject>('/api/v2/reproductions', body),
  update: (id: string, body: { name?: string; status?: string; tags?: string[]; expectedRevision: number }) =>
    mutate<ReproductionProject>('PATCH', `/api/v2/reproductions/${encodeURIComponent(id)}`, body),
  archive: (id: string, expectedRevision: number) =>
    post<ReproductionProject>(`/api/v2/reproductions/${encodeURIComponent(id)}/archive`, { expectedRevision }),
  remove: (id: string, expectedRevision: number) =>
    mutate<void>('DELETE', `/api/v2/reproductions/${encodeURIComponent(id)}`, { expectedRevision }),
  saveDocument: (id: string, body: { content: string; expectedRevision: number }) =>
    mutate<ReproductionDocument>('PUT', `/api/v2/reproductions/${encodeURIComponent(id)}/document`, body),
  listRuns: (id: string) =>
    get<ExperimentRun[] | { items: ExperimentRun[] }>(`/api/v2/reproductions/${encodeURIComponent(id)}/runs`),
  createRun: (id: string, body: Partial<Omit<ExperimentRun, 'id' | 'projectId' | 'createdAt' | 'updatedAt'>>) =>
    post<ExperimentRun>(`/api/v2/reproductions/${encodeURIComponent(id)}/runs`, body),
  updateRun: (projectId: string, runId: string, body: Partial<Omit<ExperimentRun, 'id' | 'projectId' | 'createdAt' | 'updatedAt'>>) =>
    mutate<ExperimentRun>('PATCH', `/api/v2/reproductions/${encodeURIComponent(projectId)}/runs/${encodeURIComponent(runId)}`, body),
  deleteRun: (projectId: string, runId: string) =>
    mutate<void>('DELETE', `/api/v2/reproductions/${encodeURIComponent(projectId)}/runs/${encodeURIComponent(runId)}`),
  createResult: (id: string, body: Omit<ReproductionResult, 'id' | 'projectId' | 'createdAt' | 'updatedAt'>) =>
    post<ReproductionResult>(`/api/v2/reproductions/${encodeURIComponent(id)}/results`, body),
  updateResult: (projectId: string, resultId: string, body: Partial<Omit<ReproductionResult, 'id' | 'projectId' | 'createdAt' | 'updatedAt'>>) =>
    mutate<ReproductionResult>('PATCH', `/api/v2/reproductions/${encodeURIComponent(projectId)}/results/${encodeURIComponent(resultId)}`, body),
  deleteResult: (projectId: string, resultId: string) =>
    mutate<void>('DELETE', `/api/v2/reproductions/${encodeURIComponent(projectId)}/results/${encodeURIComponent(resultId)}`),
  listArtifacts: (id: string) =>
    get<ReproductionArtifact[] | { items: ReproductionArtifact[] }>(`/api/v2/reproductions/${encodeURIComponent(id)}/artifacts`),
  uploadArtifact: (id: string, body: FormData) =>
    fetch(`/api/v2/reproductions/${encodeURIComponent(id)}/artifacts`, { method: 'POST', body })
      .then((response) => json<ReproductionArtifact>(response)),
  artifactUrl: (projectId: string, artifactId: string) =>
    `/api/v2/reproductions/${encodeURIComponent(projectId)}/artifacts/${encodeURIComponent(artifactId)}/download`,
  listNotes: (id: string) =>
    get<ReproductionNote[]>(`/api/v2/reproductions/${encodeURIComponent(id)}/notes`),
  addNote: (id: string, content: string) =>
    post<ReproductionNote>(`/api/v2/reproductions/${encodeURIComponent(id)}/notes`, { content }),
};

/* ── 笔记与 AI 工件（legacy） ────────────────────── */

export const artifactApi = {
  getNote: (id: string) =>
    text(`/api/note?id=${encodeURIComponent(id)}`),
  saveNote: (id: string, content: string) =>
    post<{ ok: boolean }>('/api/note', { id, content }),
  getExplainer: (id: string) =>
    text(`/api/explainer?id=${encodeURIComponent(id)}`),
  getTranslation: (id: string) =>
    text(`/api/translation?id=${encodeURIComponent(id)}`),
  explain: (id: string, onEvent: (event: StreamEvent) => void) =>
    streamNdjson('/api/explain', { id }, onEvent),
  translate: (id: string, onEvent: (event: StreamEvent) => void) =>
    streamNdjson('/api/translate', { id }, onEvent),
  explainBatchStatus: () =>
    get<{
      ok: boolean;
      pending?: number;
      running?: boolean;
      withPdf?: number;
      lastRun?: BatchRun | null;
      [key: string]: unknown;
    }>('/api/explain-batch'),
  explainBatch: (
    params: { limit?: number; onlyMissingPdf?: boolean },
    onEvent: (event: StreamEvent) => void,
  ) => streamNdjson('/api/explain-batch', params, onEvent),
  titleTranslationStatus: () =>
    get<{ ok: boolean; pending: number; running: boolean }>('/api/title-translations'),
  runTitleTranslations: (
    params: { limit?: number },
    onEvent: (event: StreamEvent) => void,
  ) => streamNdjson('/api/title-translations', params, onEvent),
  translateText: (text: string) =>
    post<{ ok: boolean; text?: string; translation?: string; error?: string }>('/api/translate-text', {
      text,
    }),
  /* PDF → Markdown（OCR，方案 A 官方提示词）：NDJSON 流，进度 OCRPG::i/n → progress 事件，
   * 终态 result 事件携带 markdown 字段；转换成功后后端自动落库 ocr_markdown 表。 */
  ocrMarkdown: (id: string, onEvent: (event: StreamEvent) => void) =>
    streamNdjson('/api/ocr-md', { id }, onEvent),
  /* 读已保存的 OCR Markdown（GET /api/ocr-md?id=）；无记录返回空字符串。 */
  getOcrMarkdown: (id: string) =>
    text(`/api/ocr-md?id=${encodeURIComponent(id)}`),
  /* 批量 OCR 统计：待转换/已有 OCR/缺 PDF 篇数。 */
  ocrBatchStats: () =>
    get<{
      ok: boolean;
      total?: number;
      hasOcr?: number;
      withPdf?: number;
      pending?: number;
      noPdf?: number;
      lastRun?: BatchRun | null;
      error?: string;
    }>('/api/ocr-md-batch'),
  /* 批量 PDF→Markdown(OCR)：NDJSON 流，逐篇 ITEM:: 进度 → progress 事件，
   * 终态 result 事件 summary 字段（与 explainBatch 同契约）。 */
  ocrBatch: (options: { limit?: number }, onEvent: (event: StreamEvent) => void) =>
    streamNdjson('/api/ocr-md-batch', options, onEvent),
};

/* ── 元数据与库维护（只读扫描 + NDJSON 补全） ─────── */

export const maintenanceApi = {
  duplicateScan: () =>
    get<{ ok: boolean; count: number; pairs: DuplicatePair[]; error?: string }>('/api/dup-scan'),
  enrichStatus: () => get<EnrichStatus>('/api/enrich-status'),
  enrich: (options: { limit?: number }, onEvent: (event: StreamEvent) => void) =>
    streamNdjson('/api/enrich', options, onEvent),
};

/* ── 复习 ───────────────────────────────────────── */

export const reviewApi = {
  snapshot: () => get<ReviewSnapshot>('/api/reviews'),
  start: (id: string) =>
    post<{ ok: boolean; plan?: unknown; error?: string }>('/api/reviews/start', { id }),
  complete: (id: string) =>
    post<{ ok: boolean; plan?: unknown; reviews?: ReviewSnapshot; error?: string }>(
      '/api/reviews/complete',
      { id },
    ),
};

/* ── 采集（legacy 检索 / 导入） ──────────────────── */

export const acquireApi = {
  search: (
    params: {
      query: string;
      sources: string[];
      years?: string;
      max?: number;
      minRelevance?: number;
      expand?: boolean;
      onlyA?: boolean;
      queries?: string[];
    },
    onEvent: (event: StreamEvent) => void,
  ) => streamNdjson('/api/search', params, onEvent),
  expand: (query: string, expandN = 6) =>
    post<{
      ok?: boolean;
      queries?: string[];
      fallback?: boolean;
      source?: string;
      warning?: string;
      error?: string;
      [key: string]: unknown;
    }>('/api/expand', {
      query,
      expandN,
    }),
  ingestSelected: (
    params: { candidates: Candidate[]; deep?: boolean; downloadPdf?: boolean },
    onEvent: (event: StreamEvent) => void,
  ) => streamNdjson('/api/ingest-selected', params, onEvent),
  semsearch: (query: string, k = 8, onEvent: (event: StreamEvent) => void) =>
    streamNdjson('/api/semsearch', { query, k }, onEvent),
  recommend: (id: string, limit = 6, onEvent: (event: StreamEvent) => void) =>
    streamNdjson('/api/recommend', { id, limit }, onEvent),
  scanPdfs: (dir: string) =>
    get<{ ok?: boolean; files?: Array<{ path: string; size: number }>; [key: string]: unknown }>(
      `/api/scan-pdfs?dir=${encodeURIComponent(dir)}`,
    ),
  importPdfs: (
    params: { paths: string[]; enrich?: boolean },
    onEvent: (event: StreamEvent) => void,
  ) => streamNdjson('/api/import-pdfs', params, onEvent),
  downloadPdfs: (
    params: { ids?: string[]; limit?: number },
    onEvent: (event: StreamEvent) => void,
  ) => streamNdjson('/api/download-pdfs', params, onEvent),
  verifyVenue: (
    candidates: Array<Record<string, unknown>>,
    onEvent: (event: StreamEvent) => void,
    sources?: string[],
  ) => streamNdjson('/api/verify-venue', { candidates, sources }, onEvent),
  normVenues: (onEvent: (event: StreamEvent) => void) =>
    streamNdjson('/api/norm-venues', {}, onEvent),
  buildCiteGraph: (onEvent: (event: StreamEvent) => void) =>
    streamNdjson('/api/cite-build', {}, onEvent),
  citeGraph: () => get<CiteGraph>('/api/citegraph'),
  embed: (scope: 'all' | 'missing', onEvent: (event: StreamEvent) => void) =>
    streamNdjson('/api/embed', { scope }, onEvent),
};

/* ── 后台任务与定时调度（legacy） ────────────────── */

export const jobApi = {
  list: () => get<LegacyJob[]>('/api/jobs'),
  create: (body: Record<string, unknown>) =>
    post<{ ok: boolean; id?: number; error?: string }>('/api/jobs', body),
  detail: (id: number) => get<Record<string, unknown>>(`/api/jobs/detail?id=${id}`),
  confirm: (
    params: { jobId: number; candidates: Candidate[]; deep?: boolean; downloadPdf?: boolean },
    onEvent: (event: StreamEvent) => void,
  ) => streamNdjson('/api/jobs/confirm', params, onEvent),
  remove: (id: number) => post<{ ok: boolean }>('/api/jobs/delete', { id }),
};

export const scheduleApi = {
  list: () => get<Schedule[]>('/api/schedules'),
  create: (body: Record<string, unknown>) =>
    post<{ ok: boolean; id?: number; error?: string }>('/api/schedules', body),
  toggle: (id: number, enabled: boolean) =>
    post<{ ok: boolean }>('/api/schedules/toggle', { id, enabled }),
  remove: (id: number) => post<{ ok: boolean }>('/api/schedules/delete', { id }),
};

/* ── 设置 ───────────────────────────────────────── */

export const settingsApi = {
  get: () => get<Settings>('/api/settings'),
  update: (fields: Record<string, unknown>) =>
    post<{ ok: boolean; error?: string }>('/api/settings', fields),
  testLlm: () =>
    post<{ ok: boolean; latencyMs?: number; output?: string; error?: string }>('/api/test-llm'),
};

/* ── v2 durable 处理管线 ─────────────────────────── */

export const v2Api = {
  health: () => get<{ ok: boolean; schemaRevision: string }>('/api/v2/health'),
  readiness: () => get<{ status: string; schemaRevision?: string }>('/health/ready'),
  listJobs: (params: {
    paperId?: string;
    status?: string;
    jobType?: string;
    limit?: number;
    cursor?: string;
  } = {}) => {
    const query = new URLSearchParams();
    if (params.paperId) query.set('paperId', params.paperId);
    if (params.status) query.set('status', params.status);
    if (params.jobType) query.set('jobType', params.jobType);
    if (params.limit !== undefined) query.set('limit', String(params.limit));
    if (params.cursor) query.set('cursor', params.cursor);
    const suffix = query.toString() ? `?${query.toString()}` : '';
    return get<{ items: V2JobDetail[]; nextCursor: string | null }>(`/api/v2/jobs${suffix}`);
  },
  getJob: (id: string) => get<V2JobDetail>(`/api/v2/jobs/${encodeURIComponent(id)}`),
  jobEvents: (id: string, afterSequence = 0) => {
    const query = afterSequence > 0 ? `?afterSequence=${afterSequence}` : '';
    return get<V2JobEventsPage>(
      `/api/v2/jobs/${encodeURIComponent(id)}/events${query}`,
    );
  },
  cancelJob: (id: string) => post<V2JobDetail>(`/api/v2/jobs/${encodeURIComponent(id)}/cancel`),
  retryJob: (id: string) => post<V2RetryJobResult>(`/api/v2/jobs/${encodeURIComponent(id)}/retry`),
  listSources: (paperId: string) =>
    get<{ items: Array<Record<string, unknown>>; nextCursor: string | null }>(
      `/api/v2/papers/${encodeURIComponent(paperId)}/sources`,
    ),
  enqueueSource: (paperId: string, sourceMode: 'native' | 'ocr') =>
    post<{ source: Record<string, unknown>; job: V2JobSummary; deduplicated: boolean }>(
      `/api/v2/papers/${encodeURIComponent(paperId)}/sources`,
      { sourceMode },
    ),
  listArtifacts: (paperId: string) =>
    get<{ items: Array<Record<string, unknown>>; nextCursor: string | null }>(
      `/api/v2/papers/${encodeURIComponent(paperId)}/artifacts`,
    ),
  enqueueExplainer: (
    paperId: string,
    sourceDocumentId: string,
    sourceMode: 'native' | 'ocr',
    profile: 'standard' | 'deep' = 'standard',
  ) =>
    post<{ artifact: Record<string, unknown>; job: V2JobSummary; deduplicated: boolean }>(
      `/api/v2/papers/${encodeURIComponent(paperId)}/artifacts/explainer`,
      { sourceDocumentId, sourceMode, profile },
    ),
  enqueueArtifact: (
    paperId: string,
    kind: 'translation' | 'classification' | 'metadata' | 'summary',
    sourceDocumentId: string,
    sourceMode: 'native' | 'ocr',
  ) =>
    post<{ artifact: Record<string, unknown>; job: V2JobSummary; deduplicated: boolean }>(
      `/api/v2/papers/${encodeURIComponent(paperId)}/artifacts/${kind}`,
      { sourceDocumentId, sourceMode },
    ),
  enqueueIndex: (
    paperId: string,
    sourceDocumentId: string,
    sourceMode: 'native' | 'ocr' = 'native',
    includeEmbeddings = true,
  ) =>
    post<{ job: V2JobSummary; deduplicated: boolean }>(
      `/api/v2/papers/${encodeURIComponent(paperId)}/index`,
      { sourceDocumentId, sourceMode, includeEmbeddings },
    ),
  indexStatus: (paperId: string, sourceDocumentId: string) =>
    get<Record<string, unknown>>(
      `/api/v2/papers/${encodeURIComponent(paperId)}/index-status?sourceDocumentId=${encodeURIComponent(sourceDocumentId)}`,
    ),
  searchChunks: (params: {
    query: string;
    mode: 'lexical' | 'semantic' | 'hybrid';
    paperIds?: string[];
    limit?: number;
  }) =>
    post<{
      items: Array<{
        paperId: string;
        chunkId: string;
        sequence: number;
        headingPath: string[];
        pageStart: number | null;
        pageEnd: number | null;
        excerpt: string;
        score: number;
        lexicalScore: number;
        semanticScore: number;
      }>;
      coverage: {
        readyChunks: number;
        embeddedChunks: number;
        staleChunks: number;
        failedEmbeddings: number;
      };
    }>('/api/v2/search/chunks', params),
  obsidianStatus: () =>
    get<{
      enabled: boolean;
      vaultConfigured: boolean;
      writable: boolean;
      rootFolder: string;
      pdfMode: string;
      lastJob: Record<string, unknown> | null;
      aggregate: Record<string, number>;
    }>('/api/v2/obsidian/status'),
  obsidianSync: (dryRun = false) =>
    post<{ job: Record<string, unknown>; deduplicated: boolean }>('/api/v2/obsidian/sync', {
      dryRun,
    }),
  obsidianTest: () => post<{ ok: boolean }>('/api/v2/obsidian/test'),
  obsidianExport: (paperId: string, dryRun = false) =>
    post<{ job: Record<string, unknown>; deduplicated: boolean }>(
      `/api/v2/papers/${encodeURIComponent(paperId)}/exports/obsidian`,
      { dryRun },
    ),
};
