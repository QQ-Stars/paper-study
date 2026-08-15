export type Decoder<T> = (value: unknown, path?: string) => T;

export type StudyStatus = '未开始' | '学习中' | '已理解';
export type ReviewState = 'overdue' | 'dueToday' | 'upcoming' | 'completed';
export type JobStatus = 'pending' | 'running' | 'review' | 'done' | 'failed';

export interface PaperListItem {
  id: string;
  file: string;
  title: string;
  titleZh: string | null;
  venue: string | null;
  year: string | null;
  type: string | null;
  topic: string | null;
  pdfUrl: string | null;
  pdfPath: string | null;
  url: string | null;
  tldr: string | null;
  contribution: string | null;
  citations: number | null;
  createdAt: string | null;
  source: string | null;
  arxivId: string | null;
  doi: string | null;
  s2Id: string | null;
  openalexId: string | null;
  relevance: number | null;
  order: number | null;
  ccf: string | null;
  status: StudyStatus;
  hasNote: boolean;
  favorite: boolean;
  hasPdf: boolean;
}

export interface PaperRecord {
  id: string;
  source: string;
  sourceId: string | null;
  arxivId: string | null;
  doi: string | null;
  s2Id: string | null;
  openalexId: string | null;
  title: string;
  titleZh: string | null;
  titleNorm: string | null;
  authors: string[];
  venue: string | null;
  year: string | null;
  abstract: string | null;
  tldr: string | null;
  citations: number | null;
  s2Fields: string[];
  url: string | null;
  pdfUrl: string | null;
  pdfPath: string | null;
  type: string | null;
  topic: string | null;
  task: string | null;
  models: string[];
  datasets: string[];
  contribution: string | null;
  tags: string[];
  relevance: number | null;
  explainer: string | null;
  extractedBy: string | null;
  orderNo: number | null;
  createdAt: string | null;
  updatedAt: string | null;
}

export interface ReviewPlan {
  paperId: string;
  startedAt: string;
  currentStep: number;
  completedSteps: number;
  nextDueAt: string;
  completedAt: string | null;
  updatedAt: string;
}

export interface ReviewItem extends ReviewPlan {
  title: string;
  titleZh: string | null;
  venue: string | null;
  year: string | null;
  status: StudyStatus;
  reviewState: ReviewState;
  totalSteps: number;
}

export interface ReviewCounts {
  overdue: number;
  dueToday: number;
  upcoming: number;
  completed: number;
}

export interface ReviewSnapshot {
  today: string;
  counts: ReviewCounts;
  overdue: ReviewItem[];
  dueToday: ReviewItem[];
  upcoming: ReviewItem[];
  completed: ReviewItem[];
}

export interface Candidate {
  source: string;
  sourceId: string;
  title: string;
  authors: string[];
  venue: string | null;
  year: string | null;
  abstract: string | null;
  tldr: string | null;
  fields: string[];
  citations: number | null;
  url: string | null;
  pdfUrl: string | null;
  arxivId: string | null;
  doi: string | null;
  s2Id: string | null;
  ccf: string | null;
  type: string | null;
  topic: string | null;
  task: string | null;
  models: string[];
  datasets: string[];
  contribution: string | null;
  llmTldr: string | null;
  tags: string[];
  relevance: number | null;
  inLibrary: boolean;
  candidateId: number | null;
}

export interface Verification {
  venue: string | null;
  year: string | null;
  matched: boolean;
  skipped: boolean;
  sourceOfTruth: string;
  changed: boolean;
  originalVenue: string | null;
  ccf: string | null;
  note: string;
  error: boolean;
}

export interface JobSummary {
  id: number;
  query: string | null;
  sources: string[];
  yearFrom: number | null;
  yearTo: number | null;
  maxPapers: number | null;
  minRelevance: number | null;
  onlyA: boolean;
  scheduleId: number | null;
  status: JobStatus;
  found: number;
  added: number;
  skipped: number;
  pending: number;
  createdAt: string | null;
  finishedAt: string | null;
}

export interface JobRecord extends Omit<JobSummary, 'pending'> {
  log: string | null;
  queries: string[];
}

export interface JobDetail {
  job: JobRecord;
  candidates: Candidate[];
}

export interface Schedule {
  id: number;
  query: string;
  sources: string[];
  years: string;
  maxPapers: number;
  minRelevance: number;
  onlyA: boolean;
  everyDays: number;
  enabled: boolean;
  lastRun: string | null;
  nextRun: string | null;
  createdAt: string | null;
}

export interface CitationNode {
  id: string;
  title: string;
  venue: string | null;
  year: string | null;
  type: string | null;
  topic: string | null;
  citations: number | null;
  indeg: number;
  outdeg: number;
}

export interface CitationLink {
  source: string;
  target: string;
}

export interface CitationGraph {
  nodes: CitationNode[];
  links: CitationLink[];
  edgeCount: number;
}

export interface SettingsView {
  provider: string;
  baseUrl: string;
  model: string;
  apiKeyTail: string;
  hasApiKey: boolean;
  s2KeyTail: string;
  hasS2Key: boolean;
  pdfDir: string;
  explainerDir: string;
  translationDir: string;
  defaultPdfDir: string;
  defaultExplainerDir: string;
  defaultTranslationDir: string;
  resolvedPdfDir: string;
  resolvedExplainerDir: string;
  resolvedTranslationDir: string;
  researchTheme: string;
  embedProvider: string;
  embedApiBase: string;
  embedApiModel: string;
  embedKeyTail: string;
  hasEmbedKey: boolean;
  obsidianEnabled?: boolean;
  obsidianVaultPath?: string;
  obsidianRootFolder?: string;
  obsidianPdfMode?: ObsidianPdfMode;
  obsidianExportSource?: boolean;
  obsidianExportExplainer?: boolean;
  obsidianExportTranslation?: boolean;
  obsidianAutoExport?: boolean;
}

export type ObsidianPdfMode = 'none' | 'reference' | 'copy';

export interface ObsidianResultCounts {
  exported: number;
  unchanged: number;
  conflicts: number;
  errors: number;
  skipped: number;
  userManaged: number;
  orphaned: number;
  deleted: number;
}

export interface ObsidianLastJob {
  id: string;
  paperId: string | null;
  jobType: 'obsidian_export' | 'obsidian_sync';
  status: 'queued' | 'running' | 'succeeded' | 'failed' | 'cancelled';
}

export interface ObsidianStatus {
  enabled: boolean;
  vaultConfigured: boolean;
  writable: boolean;
  rootFolder: string;
  pdfMode: ObsidianPdfMode;
  lastJob: ObsidianLastJob | null;
  aggregate: ObsidianResultCounts;
}

export interface ObsidianTestResult {
  ok: boolean;
}

export interface ObsidianExportRequest {
  dryRun: boolean;
}

export interface ObsidianCleanupRequest {
  dryRun: boolean;
  applyCleanup: boolean;
  cleanupPlanSha: string | null;
}

export interface TitleTranslationStatus {
  pending: number;
  running: boolean;
}

export interface ExplainerPending {
  pending: number;
  withPdf: number;
  noPdf: number;
}

export interface PdfStatus {
  id: string;
  hasPdf: boolean;
  size: number;
  path: string;
  canDownload: boolean;
}

export interface PdfScanFile {
  path: string;
  name: string;
  size: number;
}

export interface PdfScan {
  dir: string;
  count: number;
  files: PdfScanFile[];
}

export interface SemanticHit {
  id: string;
  score: number;
}

export interface SettingsUpdate {
  provider?: string;
  baseUrl?: string;
  model?: string;
  apiKey?: string;
  s2ApiKey?: string;
  pdfDir?: string;
  explainerDir?: string;
  translationDir?: string;
  researchTheme?: string;
  embedProvider?: string;
  embedApiBase?: string;
  embedApiModel?: string;
  embedApiKey?: string;
  obsidianEnabled?: boolean;
  obsidianVaultPath?: string;
  obsidianRootFolder?: string;
  obsidianPdfMode?: ObsidianPdfMode;
  obsidianExportSource?: boolean;
  obsidianExportExplainer?: boolean;
  obsidianExportTranslation?: boolean;
  obsidianAutoExport?: boolean;
}

export interface SearchRequest {
  query: string;
  sources: string[];
  years?: string;
  max?: number;
  minRelevance?: number;
  expand?: boolean;
  onlyA?: boolean;
  queries?: string[];
}

export interface IngestCandidatesRequest {
  candidates: Candidate[];
  deep?: boolean;
  downloadPdf?: boolean;
}
