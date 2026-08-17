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
  edges: CiteEdge[];
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
}

export interface V2JobSummary {
  id: string;
  type: string;
  status: string;
  createdAt: string;
  updatedAt: string;
  [key: string]: unknown;
}

export interface StreamEvent {
  type: string;
  ok?: boolean;
  error?: string;
  message?: string;
  [key: string]: unknown;
}
