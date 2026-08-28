/* FastAPI 后端（backend/app，端口 5173）的领域类型，字段与真实响应一一对应 */

export type StudyStatus = '未开始' | '学习中' | '已理解';

export interface Paper {
  id: string;
  file: string;
  title: string;
  title_zh: string;
  venue: string;
  year: string;
  type: string;
  topic: string;
  pdf_url: string | null;
  pdf_path: string | null;
  url: string | null;
  tldr: string | null;
  contribution: string | null;
  citations: number | null;
  created_at: string;
  source: string;
  arxiv_id: string | null;
  doi: string | null;
  s2_id: string | null;
  openalex_id: string | null;
  relevance: number | null;
  order: number | null;
  status: StudyStatus;
  hasNote: 0 | 1;
  favorite: 0 | 1;
  ccf: string | null;
  hasPdf: boolean;
}

export interface BatchRun {
  id: number;
  kind: 'ocr' | 'explain';
  finishedAt: string;
  total: number;
  done: number;
  failed: number;
  skipped: number;
  detail: unknown;
}

export interface DuplicatePaper {
  id: string;
  title: string;
  year: string | null;
  venue: string | null;
}

export interface DuplicatePair {
  left: DuplicatePaper;
  right: DuplicatePaper;
  similarity: number;
}

export interface EnrichStatus {
  ok: boolean;
  total: number;
  missingYear: number;
  missingVenue: number;
  missingMetadata: number;
  withAuthors: number;
  missingAuthors: number;
  pending: number;
}

export interface ReviewItem {
  paper_id: string;
  started_at: string;
  current_step: number;
  completed_steps: number;
  next_due_at: string;
  completed_at: string | null;
  updated_at: string;
  title: string;
  title_zh: string;
  venue: string;
  year: string;
  status: StudyStatus;
  review_state: 'overdue' | 'due' | 'upcoming' | 'completed';
  total_steps: number;
}

export interface ReviewSnapshot {
  ok: boolean;
  today: string;
  counts: { overdue: number; dueToday: number; upcoming: number; completed: number };
  overdue: ReviewItem[];
  dueToday: ReviewItem[];
  upcoming: ReviewItem[];
  completed: ReviewItem[];
}

export interface LegacyJob {
  id: number;
  query: string;
  status: string;
  created_at: string;
  updated_at: string;
  candidates?: number;
  selected?: number;
  [key: string]: unknown;
}

export interface Schedule {
  id: number;
  query: string;
  sources: string;
  enabled: number | boolean;
  years: string;
  max: number;
  last_run: string | null;
  next_run: string | null;
  added: number;
  [key: string]: unknown;
}

export interface CiteNode {
  id: string;
  title: string;
  venue: string;
  year: string;
  type: string;
  topic: string;
  citations: number | null;
  indeg: number;
  outdeg: number;
}

export interface CiteEdge {
  source: string;
  target: string;
  [key: string]: unknown;
}

export interface CiteGraph {
  nodes: CiteNode[];
  links: CiteEdge[];
  edgeCount: number;
}

export interface Candidate {
  title: string;
  title_zh?: string;
  venue?: string;
  year?: string;
  type?: string;
  topic?: string;
  url?: string;
  pdf_url?: string;
  arxiv_id?: string;
  doi?: string;
  citations?: number;
  tldr?: string;
  contribution?: string;
  relevance?: number;
  [key: string]: unknown;
}

export interface Settings {
  provider: string;
  baseUrl: string;
  model: string;
  timeout: number;
  llmTimeout: number;
  ocrProvider: string;
  ocrBaseUrl: string;
  ocrModel: string;
  pdfTextProvider: string;
  ocrTimeout: number;
  ocrEnabled: boolean;
  ocrPageBatchSize: number;
  ocrMaxConcurrency: number;
  embedProvider: string;
  embedApiBase: string;
  embedApiModel: string;
  s2Provider: string;
  s2Endpoint: string;
  explainMaxChars: number;
  translateMode: string;
  translateChunkSize: number;
  translateMaxChars: number;
  translateWorkers: number;
  translateSkipReferences: boolean;
  pdfDir: string;
  defaultPdfDir: string;
  resolvedPdfDir: string;
  explainerDir: string;
  defaultExplainerDir: string;
  resolvedExplainerDir: string;
  translationDir: string;
  defaultTranslationDir: string;
  resolvedTranslationDir: string;
  ocrMarkdownDir: string;
  defaultOcrMarkdownDir: string;
  resolvedOcrMarkdownDir: string;
  reproductionDir: string;
  defaultReproductionDir: string;
  resolvedReproductionDir: string;
  researchTheme: string;
  obsidianEnabled: boolean;
  obsidianVaultPath: string;
  obsidianRootFolder: string;
  obsidianPdfMode: string;
  obsidianExportSource: boolean;
  obsidianExportExplainer: boolean;
  obsidianExportTranslation: boolean;
  obsidianAutoExport: boolean;
  hasApiKey: boolean;
  apiKeyTail: string;
  hasOcrKey: boolean;
  ocrKeyTail: string;
  hasEmbedKey: boolean;
  embedKeyTail: string;
  hasS2Key: boolean;
  s2KeyTail: string;
  credentialStatus?: Record<
    string,
    {
      kind: string;
      hasKey: boolean;
      keyTail: string | null;
      environmentManaged: boolean;
    }
  >;
}

export type V2JobStatus = 'queued' | 'running' | 'succeeded' | 'failed' | 'cancelled';

export interface V2JobError {
  code: string;
  message?: string;
}

export interface V2JobSummary {
  id: string;
  paperId: string | null;
  jobType: string;
  sourceMode: string | null;
  status: V2JobStatus | string;
  [key: string]: unknown;
}

export interface V2JobDetail extends V2JobSummary {
  progress: Record<string, unknown>;
  attempt: number;
  maxAttempts: number;
  error: V2JobError | null;
  createdAt: string;
  startedAt: string | null;
  finishedAt: string | null;
  cancelledAt: string | null;
}

export interface V2JobEvent {
  sequence: number;
  type: string;
  progress: Record<string, unknown>;
  error: V2JobError | null;
  createdAt: string;
}

export interface V2JobEventsPage {
  items: V2JobEvent[];
  nextAfterSequence: number;
}

export interface V2RetryJobResult {
  job: V2JobSummary;
  retriedFromJobId: string;
  deduplicated: boolean;
}

export interface StreamEvent {
  type: string;
  ok?: boolean;
  error?: string;
  message?: string;
  [key: string]: unknown;
}

/* ── 论文复现工作区 ──────────────────────────────── */

export type ReproductionStatus =
  | 'planned'
  | 'preparing'
  | 'running'
  | 'completed'
  | 'blocked'
  | 'archived';

export type ReproductionDocumentStatus = 'unsaved' | 'saving' | 'saved' | 'failed';

export interface ReproductionDocument {
  id: string;
  projectId?: string;
  content: string;
  revision: number;
  projectRevision?: number;
  saveStatus?: ReproductionDocumentStatus;
  status?: ReproductionDocumentStatus;
  updatedAt: string;
  createdAt?: string | null;
}

export interface ReproductionProject {
  id: string;
  paperId: string | null;
  paperTitle: string;
  name: string;
  status: ReproductionStatus;
  tags: string[];
  revision: number;
  createdAt: string;
  updatedAt: string;
  document?: ReproductionDocument;
  runs?: ExperimentRun[];
  artifacts?: ReproductionArtifact[];
  notes?: ReproductionNote[];
  results?: ReproductionResult[];
  runCount?: number;
  lastRunSummary?: string | null;
  lastRunStatus?: ExperimentRunStatus | null;
  latestMetrics?: Record<string, unknown>;
  hasFailedTask?: boolean;
  hasUnsavedContent?: boolean;
}

export interface ReproductionListResponse {
  items: ReproductionProject[];
  total: number;
  offset?: number;
  page?: number;
  limit: number;
}

export type ExperimentRunStatus = 'planned' | 'running' | 'completed' | 'failed' | 'blocked';

export interface ExperimentRun {
  id: string;
  projectId: string;
  name?: string;
  environment: string;
  command: string;
  parameters: Record<string, unknown>;
  dataVersion: string;
  codeRevision: string;
  seed: number | null;
  status: ExperimentRunStatus;
  metrics: Record<string, unknown>;
  resultSummary: string;
  startedAt?: string | null;
  finishedAt?: string | null;
  runtimeVersions?: string | null;
  dataset?: string | null;
  preprocessing?: string | null;
  repositoryUrl?: string | null;
  config?: string | null;
  issues?: string | null;
  createdAt: string;
  updatedAt?: string;
}

export interface ReproductionArtifact {
  id: string;
  projectId: string;
  runId: string | null;
  kind: string;
  filename: string;
  storageKey: string;
  mimeType: string;
  sizeBytes: number;
  sha256: string;
  createdAt: string;
}

export interface ReproductionNote {
  id: string;
  projectId: string;
  content: string;
  createdAt: string;
  updatedAt: string;
}

export type ReproductionResultStatus = 'reproduced' | 'partial' | 'not_reproduced' | 'inconsistent';

export interface ReproductionResult {
  id: string;
  projectId: string;
  metricName: string;
  paperValue?: string | null;
  reproductionValue?: string | null;
  difference?: string | null;
  differencePercent?: string | null;
  datasetSettings?: string | null;
  source?: string | null;
  status: ReproductionResultStatus;
  notes?: string | null;
  createdAt: string;
  updatedAt: string;
}
