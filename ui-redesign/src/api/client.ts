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
  return (await response.json()) as T;
}

function get<T>(url: string): Promise<T> {
  return fetch(url).then((response) => json<T>(response));
}

function post<T>(url: string, body: unknown = {}): Promise<T> {
  return fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
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
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let newline = buffer.indexOf('\n');
    while (newline >= 0) {
      const line = buffer.slice(0, newline).trim();
      buffer = buffer.slice(newline + 1);
      if (line) {
        try {
          onEvent(JSON.parse(line) as StreamEvent);
        } catch {
          /* 忽略不完整行 */
        }
      }
      newline = buffer.indexOf('\n');
    }
  }
  const tail = buffer.trim();
  if (tail) {
    try {
      onEvent(JSON.parse(tail) as StreamEvent);
    } catch {
      /* ignore */
    }
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
  deletePaper: (id: string) => post<{ ok: boolean }>('/api/delete', { id }),
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

/* ── 笔记与 AI 工件（legacy） ────────────────────── */

export const artifactApi = {
  getNote: (id: string) =>
    fetch(`/api/note?id=${encodeURIComponent(id)}`).then((response) =>
      response.ok ? response.text() : '',
    ),
  saveNote: (id: string, content: string) =>
    post<{ ok: boolean }>('/api/note', { id, content }),
  getExplainer: (id: string) =>
    fetch(`/api/explainer?id=${encodeURIComponent(id)}`).then((response) =>
      response.ok ? response.text() : '',
    ),
  getTranslation: (id: string) =>
    fetch(`/api/translation?id=${encodeURIComponent(id)}`).then((response) =>
      response.ok ? response.text() : '',
    ),
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
    fetch(`/api/ocr-md?id=${encodeURIComponent(id)}`).then((response) =>
      response.ok ? response.text() : '',
    ),
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
  expand: (query: string, expandN = 4) =>
    post<{ ok?: boolean; queries?: string[]; [key: string]: unknown }>('/api/expand', {
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
  verifyVenue: (candidates: Array<Record<string, unknown>>) =>
    post<Record<string, unknown>>('/api/verify-venue', { candidates }),
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
  testLlm: () => post<{ ok: boolean; latencyMs?: number; error?: string }>('/api/test-llm'),
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
